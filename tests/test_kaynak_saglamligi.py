"""Kaynak modüllerinin sağlamlık korumaları: timeout, kimlik, yapılandırma.

Buradaki testler ağa çıkmaz; istek katmanı (urlopen / oturum) sahtelenir.

Kapsanan eski hatalar:

* AnimeciX stream isteği `timeout` almadan atılıyordu ve `setdefaulttimeout`
  hiçbir yerde çağrılmadığı için soket varsayılanı SONSUZ'du.
* AnimeciX sayısal olmayan kimliklerde `hash(...) % 1000000` uyduruyordu;
  PYTHONHASHSEED yüzünden her süreçte farklı, üstelik alakasız bir animenin
  kimliği olabilecek bir sayı.
* OpenAnime'ın üç dışa açık fonksiyonunun `timeout` parametresi gövdelerde hiç
  kullanılmıyordu (`PROVIDER_CONFIG["timeout"]` de hiç okunmuyordu).
* OpenAnime CDN kökü sabit kodluydu; CDN taşındığında tek çare sürüm çıkmaktı.
"""
import ast
import inspect

import pytest

import turkanime_api.sources.animecix as ac
import turkanime_api.sources.openani as oa


# ═════════════════════════════════════════════════════════════════════════════
# AnimeciX — stream isteğinde timeout
# ═════════════════════════════════════════════════════════════════════════════

class SahteAcilis:
    """`urlopen` yanıtı: yalnızca `geturl()` ve context manager gerekiyor."""

    def __init__(self, url: str):
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.fixture
def cix_akis(monkeypatch):
    """`urlopen` + `_http_get`'i sahtele; urlopen'a geçen timeout'u kaydet."""
    kayit = {}

    def sahte_urlopen(_istek, *a, **kw):
        # timeout hem konumsal (url, data, timeout) hem anahtarlı gelebilir
        kayit["timeout"] = kw.get("timeout", a[1] if len(a) > 1 else None)
        return SahteAcilis("https://tau-video.xyz/embed/ABC?vid=99")

    monkeypatch.setattr(ac.urllib.request, "urlopen", sahte_urlopen)
    monkeypatch.setattr(
        ac, "_http_get",
        lambda url, timeout=None: b'{"urls": [{"label": "720p", "url": "https://v/1.mp4"}]}')
    return kayit


def test_animecix_stream_istegi_timeout_ile_gidiyor(cix_akis):
    """ESKİ HATA: urlopen timeout'suz çağrılıyordu; soket varsayılanı sonsuz."""
    sonuc = ac._video_streams("/embed/xyz")

    assert sonuc == [{"label": "720p", "url": "https://v/1.mp4"}]
    assert cix_akis["timeout"], \
        "urlopen timeout'suz çağrıldı: yanıtsız sunucu akışı süresiz askıda bırakır"


def test_animecix_stream_timeout_u_cagirana_uyuyor(cix_akis):
    """Çağıranın verdiği süre gerçekten sokete gitsin, sabite düşülmesin."""
    ac._video_streams("/embed/xyz", timeout=3)

    assert cix_akis["timeout"] == 3


# ═════════════════════════════════════════════════════════════════════════════
# AnimeciX — hash tabanlı uydurma kimlik
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def cix_istekler(monkeypatch):
    """`_http_get`'i sahtele; istenen URL'leri kaydet (ağa çıkış yok)."""
    istekler = []

    def sahte(url, timeout=10):
        istekler.append(url)
        return b"{}"

    monkeypatch.setattr(ac, "_http_get", sahte)
    return istekler


def test_sayisal_olmayan_kimlik_bolum_sormuyor(cix_istekler):
    """ESKİ HATA: hash(slug)%1000000 ile ALAKASIZ bir animenin bölümleri geliyordu."""
    assert ac.CixAnime(id="one-piece", title="One Piece").episodes == []
    assert cix_istekler == [], \
        f"uydurma kimlikle AnimeciX'e istek atıldı: {cix_istekler}"


def test_sayisal_olmayan_kimlik_sezon_sormuyor(cix_istekler):
    """ESKİ HATA: sezon listesi de hash'lenmiş kimlikle sorgulanıyordu."""
    assert ac._seasons_for_title("one-piece") == []
    assert cix_istekler == []


def test_sayisal_kimlik_hala_sorguya_gidiyor(cix_istekler):
    """Koruma geçerli kimliği elemesin: 12345 hâlâ sorgulanmalı."""
    ac._seasons_for_title(12345)

    assert any("12345" in url for url in cix_istekler), \
        f"geçerli sayısal kimlik sorgulanmadı: {cix_istekler}"


def test_kimlik_yedeginde_hash_cagrisi_kalmadi():
    """ESKİ HATA: hash() PYTHONHASHSEED'e bağlı — kimlik süreçler arası tutarsızdı."""
    cagrilar = [
        dugum for dugum in ast.walk(ast.parse(inspect.getsource(ac)))
        if isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id == "hash"
    ]

    assert not cagrilar, \
        f"animecix.py'de {len(cagrilar)} adet hash() çağrısı duruyor (satır: " \
        f"{[d.lineno for d in cagrilar]})"


# ═════════════════════════════════════════════════════════════════════════════
# OpenAnime — timeout gerçekten isteklere gidiyor mu
# ═════════════════════════════════════════════════════════════════════════════

class SahteYanit:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class SahteOturum:
    """`get` çağrılarını (url + kwargs) kaydeden sahte oturum."""

    def __init__(self, yanit):
        self._yanit = yanit
        self.cagrilar = []

    def get(self, url, **kw):
        self.cagrilar.append(dict(kw, url=url))
        return self._yanit


@pytest.fixture
def openani_oturum(monkeypatch):
    """Verilen süre için sahte oturumlu bir adaptörü `_ozel_adaptorler`'e koy.

    Böylece `search/episodes/streams` fonksiyonlarının o süreyi gerçekten
    adaptöre taşıyıp taşımadığı ölçülebiliyor — taşımıyorsa paylaşılan tekil
    adaptör kullanılır ve sahte oturuma hiç uğranmaz.
    """
    def kur(sure: int, yanit=None):
        ada = oa.OpenAniAdapter(timeout=sure)
        oturum = SahteOturum(yanit or SahteYanit(200, "<html></html>"))
        ada.session = oturum
        ada._light_session = oturum      # `_light_get` bu oturumu yeniden kullanır
        monkeypatch.setitem(oa._ozel_adaptorler, sure, ada)
        return oturum
    return kur


def test_saglayici_yapilandirmasindaki_timeout_okunuyor():
    """ESKİ HATA: PROVIDER_CONFIG["timeout"] tanımlıydı ama hiçbir yerde okunmuyordu."""
    assert oa.OpenAniAdapter().timeout == oa.OpenAniAdapter.PROVIDER_CONFIG["timeout"]


def test_arama_timeout_u_gercek_istege_gidiyor(openani_oturum):
    """ESKİ HATA: search_openani(timeout=...) gövdede hiç kullanılmıyordu (sabit 15)."""
    oturum = openani_oturum(7, SahteYanit(200, "<html><title>Naruto | OpenAnime</title></html>"))

    oa.search_openani("naruto", timeout=7)

    assert oturum.cagrilar, "arama isteği verilen timeout'lu adaptöre hiç uğramadı"
    assert all(c.get("timeout") == 7 for c in oturum.cagrilar), \
        f"istek(ler) yanlış süreyle gitti: {[c.get('timeout') for c in oturum.cagrilar]}"


def test_bolum_listesi_timeout_u_gercek_istege_gidiyor(openani_oturum):
    """ESKİ HATA: get_anime_episodes(timeout=...) yok sayılıyordu; istekte süre HİÇ yoktu."""
    oturum = openani_oturum(7)

    oa.get_anime_episodes("one-piece", timeout=7)

    assert oturum.cagrilar, "bölüm isteği verilen timeout'lu adaptöre hiç uğramadı"
    assert oturum.cagrilar[0].get("timeout") == 7


def test_stream_timeout_u_gercek_istege_gidiyor(openani_oturum):
    """ESKİ HATA: get_episode_streams(timeout=...) yok sayılıyordu."""
    oturum = openani_oturum(7)

    oa.get_episode_streams("one-piece/1/1", timeout=7)

    assert oturum.cagrilar, "stream isteği verilen timeout'lu adaptöre hiç uğramadı"
    assert oturum.cagrilar[0].get("timeout") == 7


def test_ayni_sure_icin_adaptor_yeniden_kullaniliyor():
    """Her çağrıda yeni CFSession kurmak (ayar okuma + oturum) israf olurdu."""
    try:
        assert oa._adaptor(9) is oa._adaptor(9)
        assert oa._adaptor(oa.adapter.timeout) is oa.adapter
    finally:
        oa._ozel_adaptorler.pop(9, None)


# ═════════════════════════════════════════════════════════════════════════════
# OpenAnime — CDN kökü ortam değişkeniyle ezilebilsin
# ═════════════════════════════════════════════════════════════════════════════

VIDEO_SAYFASI = (
    '<html><body>const data = '
    '[{"videoUrl":"%CDN_HOST%/animes/one-piece/1.mp4","fansubName":"AoiSubs"}];'
    '</body></html>'
)

CDN_LINK_SAYFASI = (
    '<html><body>const data = '
    '[{CDN_LINK:"%CDN_HOST%/animes/",files:[{resolution:1080,file:"one-piece/1.mp4"}]}];'
    '</body></html>'
)


def test_cdn_host_ortam_degiskeniyle_eziliyor(monkeypatch):
    """ESKİ HATA: CDN kökü sabit kodluydu; CDN taşınınca tek çare sürüm çıkmaktı."""
    monkeypatch.setenv(oa.CDN_ORTAM_ANAHTARI, "https://kendi-cdn.example/")

    # Sondaki `/` atılmalı: yer tutucu `%CDN_HOST%/animes/...` biçiminde
    # dolduruluyor, çift eğik çizgi bazı CDN'lerde 404 demek.
    assert oa.cdn_host() == "https://kendi-cdn.example"


def test_cdn_host_bos_ortam_degiskeninde_varsayilana_donuyor(monkeypatch):
    """Boş/boşluklu değer varsayılanı bozmasın (yanlışlıkla tanımlanmış değişken)."""
    monkeypatch.setenv(oa.CDN_ORTAM_ANAHTARI, "   ")

    assert oa.cdn_host() == oa.CDN_HOST


def test_yer_tutucu_ortamdaki_cdn_ile_dolduruluyor(monkeypatch, openani_oturum):
    """ESKİ HATA: %CDN_HOST% yer tutucusunun tek karşılığı sabit CDN_HOST'tu."""
    monkeypatch.setenv(oa.CDN_ORTAM_ANAHTARI, "https://kendi-cdn.example")
    openani_oturum(7, SahteYanit(200, VIDEO_SAYFASI))

    videolar = oa._adaptor(7).get_video_urls({"url": "https://openani.me/anime/one-piece/1/1"})

    assert [v["url"] for v in videolar] == \
        ["https://kendi-cdn.example/animes/one-piece/1.mp4"]


def test_sayfadaki_cdn_link_ortam_cdn_si_ile_kuruluyor(monkeypatch, openani_oturum):
    """Sayfa CDN_LINK verse bile içindeki %CDN_HOST% ortam değişkenini görmeli."""
    monkeypatch.setenv(oa.CDN_ORTAM_ANAHTARI, "https://kendi-cdn.example")
    openani_oturum(7, SahteYanit(200, CDN_LINK_SAYFASI))

    videolar = oa._adaptor(7).get_video_urls({"url": "https://openani.me/anime/one-piece/1/1"})

    assert [v["url"] for v in videolar] == \
        ["https://kendi-cdn.example/animes/one-piece/1.mp4"]
