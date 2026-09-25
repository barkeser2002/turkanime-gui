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
* TRAnimeİzle harf sayfalarını `hash(sorgu)` anahtarıyla önbelleğe alıyordu;
  anahtar süreçten sürece değişiyor, boş (bot kontrolü) sonuç da yazılıyordu.
* Tranimaci arama sorgusunu kodlamıyordu: "Tom & Jerry" `q=Tom ` gidiyordu.
"""
import ast
import json
import inspect
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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


@pytest.fixture
def cix_yanitlar(monkeypatch):
    """`_http_get`'i adrese göre yanıt veren sahteyle değiştir; istekleri kaydet."""
    istekler, yanitlar = [], {}

    def sahte(url, timeout=10):
        istekler.append(url)
        for parca, govde in yanitlar.items():
            if parca in url:
                if isinstance(govde, Exception):
                    raise govde
                return json.dumps(govde).encode()
        return b"{}"

    monkeypatch.setattr(ac, "_http_get", sahte)
    return istekler, yanitlar


def test_video_kimligi_yoksa_baska_animenin_kimligi_uydurulmuyor(cix_yanitlar):
    """ESKİ HATA: başlıkta video yoksa sabit bir video kimliğiyle (başka bir
    animenin) related-videos soruluyordu; o animenin bölümleri gelebiliyordu."""
    istekler, yanitlar = cix_yanitlar
    yanitlar["secure/titles/42"] = {"title": {"videos": [], "seasons": []}}

    assert ac.CixAnime(id="42", title="Videosuz").episodes == []
    assert ac._seasons_for_title(42) == []
    assert not [u for u in istekler if "related-videos" in u], istekler
    assert "637113" not in inspect.getsource(ac)


def test_baslik_bir_kez_isteniyor(cix_yanitlar):
    """Eskiden `_episodes_for_title` secure/titles'ı iki kez istiyordu."""
    istekler, yanitlar = cix_yanitlar
    yanitlar["secure/titles/42"] = {"title": {"videos": [{"id": 9}],
                                              "seasons": [{}, {}]}}
    yanitlar["related-videos"] = {"videos": [{"name": "1. Bölüm", "url": "u1"}]}

    bolumler = ac._episodes_for_title(42)

    assert bolumler == [{"name": "1. Bölüm", "url": "u1", "season_num": None}]
    assert len([u for u in istekler if "secure/titles/42" in u]) == 1
    sezon_istekleri = [u for u in istekler if "related-videos" in u]
    assert len(sezon_istekleri) == 2 and all("videoId=9" in u for u in sezon_istekleri)


def test_baslik_okunamazsa_tipli_hata(cix_yanitlar):
    """Ağ hatası "bu animenin bölümü yok" diye yutulmuyor."""
    from turkanime_api.common.hatalar import KaynakYanitVermedi

    _istekler, yanitlar = cix_yanitlar
    yanitlar["secure/titles/42"] = TimeoutError("timed out")

    with pytest.raises(KaynakYanitVermedi, match="AnimeciX"):
        ac._episodes_for_title(42)


def test_oynatici_sabiti_tek_ve_kullaniliyor():
    """`VIDEO_PLAYERS[1]` ("sibnet") hiçbir yolda okunmuyordu."""
    assert ac.VIDEO_PLAYER == "tau-video.xyz"
    assert not hasattr(ac, "VIDEO_PLAYERS")


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


# ═════════════════════════════════════════════════════════════════════════════
# TRAnimeİzle — harf sayfası önbelleği
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def tranime_harf(monkeypatch, tmp_path):
    """Önbelleği geçici klasöre al; harf sayfası isteklerini say (ağ yok)."""
    import turkanime_api.sources.tranime as tr

    cagrilar, sayfalar = [], {}

    def sahte_harf(harf, sayfa=1):
        cagrilar.append((harf, sayfa))
        return sayfalar.get(sayfa, [])

    monkeypatch.setattr(tr, "CACHE_DIR", tmp_path / "tranime_cache")
    monkeypatch.setattr(tr, "search_by_letter", sahte_harf)
    monkeypatch.setattr(tr, "_search_direct", lambda *a, **k: [])
    monkeypatch.setattr(tr.time, "sleep", lambda _s: None)
    return tr, cagrilar, sayfalar


def test_ayni_harfle_baslayan_sorgular_sayfalari_bir_kez_indiriyor(tranime_harf):
    tr, cagrilar, sayfalar = tranime_harf
    sayfalar[1] = [("one-piece", "One Piece"), ("overlord", "Overlord")]

    assert tr.search_anime("one piece")[0] == ("one-piece", "One Piece")
    ilk = len(cagrilar)
    assert tr.search_anime("overlord")[0] == ("overlord", "Overlord")

    assert len(cagrilar) == ilk == 2, cagrilar      # sayfa 1 + boş sayfa 2
    assert [p.name for p in tr.CACHE_DIR.iterdir()] == ["harf_o_s1-5.json"]


def test_bos_harf_sonucu_onbellege_yazilmiyor(tranime_harf):
    """Bot kontrolü boş liste döndürüyor; yazılsaydı 30 dk "sonuç yok" denirdi."""
    tr, cagrilar, _sayfalar = tranime_harf

    assert tr.search_anime("one piece") == []
    assert tr.search_anime("one piece") == []

    assert len(cagrilar) == 2, "boş sonuç önbellekten okundu"
    assert not tr.CACHE_DIR.exists() or not any(tr.CACHE_DIR.iterdir())


def test_onbellek_anahtari_surecten_bagimsiz(tmp_path):
    """Python `hash`'i süreç başına rastgele; anahtar iki süreçte aynı olmalı."""
    betik = (
        "import sys, pathlib\n"
        "import turkanime_api.sources.tranime as tr\n"
        "tr.CACHE_DIR = pathlib.Path(sys.argv[1])\n"
        "tr._search_direct = lambda *a, **k: []\n"
        "tr.search_by_letter = lambda h, s=1: [('one-piece', 'One Piece')] if s == 1 else []\n"
        "tr.time.sleep = lambda _s: None\n"
        "tr.search_anime('one piece')\n"
        "print(sorted(p.name for p in tr.CACHE_DIR.iterdir()))\n"
    )
    kok = Path(__file__).resolve().parent.parent
    ciktilar = []
    for tohum in ("1", "2"):
        ortam = dict(os.environ, PYTHONHASHSEED=tohum)
        cikti = subprocess.run(
            [sys.executable, "-c", betik, str(tmp_path / f"c{tohum}")],
            cwd=kok, env=ortam, capture_output=True, text=True, timeout=60, check=True)
        ciktilar.append(cikti.stdout.strip())

    assert ciktilar[0] == ciktilar[1] == "['harf_o_s1-5.json']"


# ═════════════════════════════════════════════════════════════════════════════
# Tranimaci — arama sorgusu kodlanıyor
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sorgu", ["Tom & Jerry", "Steins;Gate #0", "Çağ? 100%"])
def test_tranimaci_sorgusu_kesilmeden_gidiyor(monkeypatch, sorgu):
    import turkanime_api.sources.tranimaci as tm

    yollar = []

    class Yanit:
        status_code = 200
        text = "<html></html>"

    def sahte_istek(yontem, yol, **k):
        yollar.append(yol)
        return Yanit()

    monkeypatch.setattr(tm, "_request", sahte_istek)
    tm.search_tranimaci(sorgu)

    assert parse_qs(urlsplit(yollar[0]).query) == {"q": [sorgu]}
