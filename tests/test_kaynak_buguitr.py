"""BuguiTR kaynağı (`turkanime_api/sources/buguitr.py`).

Hiçbir test ağa çıkmaz: modülün HTTP oturumu sahte bir oturumla değiştiriliyor.
Bu şart — conftest'in ağ mandalı `socket`'i kesiyor ama curl_cffi kendi
libcurl'ünü kullandığı için mandalın yanından geçer.

Fikstürler (`tests/fixtures/buguitr/`) 2026-09-30'da sitenin WordPress REST
API'sinden alınan gerçek yanıtlar, kırpılmadan (her biri birkaç KB).
`403-barindirici.html` barındırıcının tarayıcı olmayan User-Agent'a verdiği
sayfa (düz curl ile alındı).

`--network` ile ayrıca canlı bir duman testi koşar (arama → bölümler → akış →
gömülü oynatıcı sayfası).
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from turkanime_api.common.episode_parser import parse_episode
from turkanime_api.sources import buguitr as bg
from turkanime_api.sources import kayit

FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "buguitr"


def oku(ad: str) -> str:
    return (FIKSTUR / ad).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sahte site
# ─────────────────────────────────────────────────────────────────────────────
class Yanit:
    """curl_cffi yanıtının kullandığımız kadarı."""

    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text


def json_yanit(ad: str) -> Yanit:
    return Yanit(200, oku(ad))


class SahteOturum:
    """"<tür>:<anahtar>=<değer>" → cevap tablosu.

    REST'te arama da yazı okuma da aynı `/posts` ucu; ayıran sorgu
    parametresi (search / slug). Tabloda olmayan slug için WordPress'in
    gerçek davranışı: 200 ve boş liste.
    """

    def __init__(self, tablo: Dict[str, Any], kayit_: List[Dict[str, Any]]):
        self.tablo = tablo
        self.kayit = kayit_

    def get(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.kayit.append({"url": url, "params": params, "headers": dict(headers or {}),
                           "timeout": timeout})
        tur = url[len(bg.API) + 1:]
        anahtar = "search" if "search" in params else "slug"
        # WordPress araması büyük/küçük harfe duyarsız; sahte site de öyle.
        cevap = self.tablo.get(f"{tur}:{anahtar}={str(params.get(anahtar)).casefold()}")
        if cevap is None:
            return Yanit(200, "[]")
        if isinstance(cevap, BaseException):
            raise cevap
        return cevap


class _AgYasak:
    """Gerçek oturum kurulursa test ağa çıkıyor demektir."""

    class Session:
        def __init__(self, *a, **k):
            raise AssertionError("buguitr testi gerçek HTTP oturumu kurmaya çalıştı")


@pytest.fixture
def site(monkeypatch):
    """Modülü sahte siteye bağla; bekleme yok."""
    tablo: Dict[str, Any] = {}
    cagrilar: List[Dict[str, Any]] = []
    monkeypatch.setattr(bg, "_http", _AgYasak)
    monkeypatch.setattr(bg, "_yeni_oturum", lambda: SahteOturum(tablo, cagrilar))
    monkeypatch.setattr(bg, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(bg, "_oturum", None)
    monkeypatch.setattr(bg, "_son_istek", 0.0)
    return types.SimpleNamespace(tablo=tablo, cagrilar=cagrilar)


def _yazi(site, slug: str) -> None:
    site.tablo[f"posts:slug={slug}"] = json_yanit(f"post-{slug}.json")


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_yalnizca_animasyon_kategorilerinde(site):
    site.tablo["posts:search=sasaki"] = json_yanit("search-sasaki.json")

    sonuc = bg.search_buguitr("  sasaki ", limit=5)

    # Site filmi önce veriyor; kısa ad (ana seri) öne alınıyor.
    assert sonuc == [("sasaki-to-miyano", "Sasaki To Miyano"),
                     ("sasaki-to-miyano-sotsugyou-hen", "Sasaki to Miyano – Sotsugyou-hen")]
    istek = site.cagrilar[0]
    assert istek["url"] == "https://buguitr.com/wp-json/wp/v2/posts"
    assert istek["params"]["search"] == "sasaki"
    assert istek["params"]["categories"] == "676,1072,282"   # ANİME, DONGHUA, Anime
    assert istek["timeout"] == bg.HTTP_TIMEOUT


def test_arama_bolum_yazilarini_ayikliyor(site):
    """"tadaima": sitenin 14 sonucunun 12'si bölüm yazısı ("... 3. Bölüm")."""
    site.tablo["posts:search=tadaima"] = json_yanit("search-tadaima.json")

    sonuc = bg.search_buguitr("Tadaima")

    assert sonuc == [("tadaima-okaeri", "Tadaima, Okaeri"),
                     # İçerikte "Tadaima Okaeri animesini de beğenebilirsiniz" geçiyor.
                     ("tasogare-outfocus", "Tasogare Out Focus")]
    assert bg.search_buguitr("tadaima", limit=1) == [("tadaima-okaeri", "Tadaima, Okaeri")]


def test_arama_film_tanitimi_yalniz_anime_kategorisinde_de_kaliyor():
    veri = json.loads(oku("search-tadaima.json")) + [
        {"id": 4045, "slug": "umibe-no-etranger", "title": {"rendered": "Umibe no &#201;tranger"},
         "categories": [282]},
        {"id": 9, "slug": "mignon-episode-1", "title": {"rendered": "Mignon &#8211; Episode 1"},
         "categories": [282]},
    ]
    sonuc = bg.arama_ayristir(veri)
    assert ("umibe-no-etranger", "Umibe no Étranger") in sonuc
    assert all(slug != "mignon-episode-1" for slug, _ in sonuc)
    assert [slug for slug, _ in sonuc] == ["tasogare-outfocus", "tadaima-okaeri",
                                           "umibe-no-etranger"]


def test_arama_sonuc_yoksa_bos(site):
    site.tablo["posts:search=frieren"] = json_yanit("search-frieren.json")
    assert bg.search_buguitr("frieren") == []


def test_arama_kisa_sorgu_aga_cikmiyor(site):
    assert bg.search_buguitr("a") == []
    assert bg.search_buguitr("   ") == []
    assert bg.search_buguitr("naruto", limit=0) == []
    assert site.cagrilar == []


def test_arama_siteye_ulasilamazsa_sebep_yukseliyor(site):
    site.tablo["posts:search=naruto"] = ConnectionError("Could not resolve host")
    with pytest.raises(bg.BuguiTRHatasi, match="bağlanılamadı"):
        bg.search_buguitr("naruto")


def test_barindirici_403_engellenme_sayiliyor(site):
    from turkanime_server.crawler.nezaket import ENGELLENME, hata_turu

    site.tablo["posts:search=naruto"] = Yanit(403, oku("403-barindirici.html"))

    with pytest.raises(bg.BuguiTRHatasi, match="engelledi") as hata:
        bg.search_buguitr("naruto")
    assert hata.value.status_code == 403
    # Sunucu tarayıcısı kaynağı dinlendirsin, "geçici hata" deyip üstelemesin.
    assert hata_turu(hata.value) == ENGELLENME


def test_engel_tanima():
    assert bg._engellendi_mi(Yanit(403, oku("403-barindirici.html")))
    assert bg._engellendi_mi(Yanit(429, ""))
    assert bg._engellendi_mi(Yanit(503, "<title>Just a moment...</title>"))
    # WordPress'in kendi JSON hatası engel sayfası değil (ör. yetkisiz uç).
    assert not bg._engellendi_mi(Yanit(403, '{"code":"rest_forbidden","data":{"status":403}}'))
    assert not bg._engellendi_mi(Yanit(200, "403 Forbidden"))


def test_json_yerine_html_gelirse_hata(site):
    site.tablo["posts:search=naruto"] = Yanit(200, "<html>bakım</html>")
    with pytest.raises(bg.BuguiTRHatasi, match="beklenmeyen"):
        bg.search_buguitr("naruto")


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_tanitimdaki_baglantilardan(site):
    _yazi(site, "tadaima-okaeri")

    bolumler = bg.get_anime_episodes("tadaima-okaeri")

    assert len(bolumler) == 12
    assert bolumler[0] == ("tadaima-okaeri-1-bolum", "1. Bölüm")
    assert bolumler[-1] == ("tadaima-okaeri-12-bolum-final", "12. Bölüm")
    istek = site.cagrilar[0]
    assert istek["params"]["slug"] == "tadaima-okaeri"
    assert "content" in istek["params"]["_fields"]


def test_bolumler_iki_sezon_ozel_bolum_ve_ayristirici(site):
    """Düğmeler iki sezonda da "1. Bölüm"; sezon slug'dan ("-2-sezon-")."""
    _yazi(site, "heaven-officials-blessing")

    bolumler = bg.get_anime_episodes("heaven-officials-blessing")

    assert len(bolumler) == 24
    assert bolumler[11] == ("heaven-officials-blessing-ozel-bolum", "Özel Bölüm")
    assert bolumler[12] == ("heaven-officials-blessing-2-sezon-1-bolum", "2. Sezon 1. Bölüm")
    assert bolumler[-1][1] == "2. Sezon 12. Bölüm"
    bilgi = parse_episode(bolumler[12][1])
    assert (bilgi.season, bilgi.episode) == (2, 1)
    assert len({b for b, _ in bolumler}) == 24


def test_bolumler_roma_rakamlari_ve_tek_video(site):
    """Düğmeler "I", "I V", "V II" (harfler ayrı <span>'larda)."""
    _yazi(site, "hyperventilation")

    bolumler = bg.get_anime_episodes("hyperventilation")

    assert [b for _, b in bolumler] == [
        "1. Bölüm", "2. Bölüm", "3. Bölüm", "4. Bölüm", "5. Bölüm", "6. Bölüm",
        "Özel Bölüm", "Tüm Bölümler (tek video)"]


def test_bolumler_ara_bolum_ve_ikinci_sezon(site):
    _yazi(site, "link-click")

    bolumler = dict(bg.get_anime_episodes("link-click"))

    assert bolumler["link-click-55-bolum"] == "5.5. Bölüm"
    bilgi = parse_episode("5.5. Bölüm")
    assert (bilgi.episode, bilgi.sub) == (5, 5)       # 5 ile aynı satıra düşmüyor
    assert bolumler["link-click-2-sezon-3-bolum"] == "2. Sezon 3. Bölüm"


def test_iki_bolumluk_yazi_ikiye_ayriliyor(site):
    """"1. & 2. BÖLÜM" tek yazı; birleştirme (sezon, bölüm) üzerinden yapıldığı
    için iki ayrı bölüm olmalı, yoksa 2. bölüm hiç görünmez."""
    _yazi(site, "no-love-zone")

    bolumler = bg.get_anime_episodes("no-love-zone")

    assert len(bolumler) == 10
    assert bolumler[:2] == [("no-love-zone-1-2-bolum#1", "1. Bölüm"),
                            ("no-love-zone-1-2-bolum#2", "2. Bölüm")]
    assert bolumler[-1] == ("no-love-zone-9-10-bolum-final#10", "10. Bölüm")
    # Tanıtımdaki "Mignon animesini de beğenebilirsiniz" bağlantısı bölüm değil.
    assert all(not b.startswith("mignon") for b, _ in bolumler)


def test_film_tanitimi_capraz_baglantiyi_almiyor(site):
    _yazi(site, "sasaki-to-miyano-sotsugyou-hen")

    assert bg.get_anime_episodes("sasaki-to-miyano-sotsugyou-hen") == [
        ("sasaki-to-miyano-sotsugyou-hen-2", "Film")]


def test_videoyu_kendisi_gomen_tanitim_tek_bolum(site):
    _yazi(site, "mo-dao-zu-shi-q")
    assert bg.get_anime_episodes("mo-dao-zu-shi-q") == [("mo-dao-zu-shi-q", "Mo Dao Zu Shi Q")]


def test_bolum_yazisinin_sonraki_bolum_dugmesi_liste_sanilmiyor(site):
    """Kimlik bir bölüm yazısıysa (arşivden/elle) yazının kendisi döner."""
    _yazi(site, "tadaima-okaeri-3-bolum")
    assert bg.get_anime_episodes("tadaima-okaeri-3-bolum") == [
        ("tadaima-okaeri-3-bolum", "Tadaima, Okaeri 3. Bölüm")]


def test_bolumler_tam_adresi_de_kabul_ediyor(site):
    _yazi(site, "tadaima-okaeri")
    assert len(bg.get_anime_episodes("https://buguitr.com/tadaima-okaeri/")) == 12


def test_bolumler_bilinmeyen_yazi_404(site):
    """Yazı yoksa sayfalar da denenir, sonra kalıcı hata (404)."""
    # WordPress'in gerçek yanıtı: 200 ve boş liste.
    site.tablo["posts:slug=boyle-bir-yazi-yok-xyz"] = json_yanit("post-yok.json")
    site.tablo["pages:slug=boyle-bir-yazi-yok-xyz"] = json_yanit("post-yok.json")
    with pytest.raises(bg.BuguiTRHatasi, match="bulunamadı") as hata:
        bg.get_anime_episodes("boyle-bir-yazi-yok-xyz")
    assert hata.value.status_code == 404
    assert [c["url"].rsplit("/", 1)[-1] for c in site.cagrilar] == ["posts", "pages"]


@pytest.mark.parametrize("kimlik", ["", "../wp-admin", "tadaima okaeri", "a/b"])
def test_bolumler_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(bg.BuguiTRHatasi):
        bg.get_anime_episodes(kimlik)
    assert site.cagrilar == []


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def test_akislar_desteklenmeyen_aynalar_atiliyor_oncelikli_sirada(site):
    """Yazıda Drive, vidmoly.to, MEGA, ok.ru var; vidmoly.to park edilmiş alan
    adı, MEGA'yı yt-dlp desteklemiyor."""
    _yazi(site, "tasogare-out-focus-2-bolum")

    akislar = bg.get_episode_streams("tasogare-out-focus-2-bolum")

    assert [a["player"] for a in akislar] == ["GDRIVE", "ODNOKLASSNIKI"]
    assert akislar[0]["url"].startswith("https://drive.google.com/file/d/")
    assert akislar[1]["url"].startswith("https://ok.ru/videoembed/")   # "//ok.ru" düzeltildi
    for akis in akislar:
        assert akis["type"] == "iframe"
        assert akis["referer"] == "https://buguitr.com/"
        assert akis["fansub"] == "BU'GUI TR"


def test_akislar_vk_atiliyor_bilinmeyenler_desteklenenlerin_arkasinda(site):
    _yazi(site, "my-new-boss-is-goofy-3-bolum")

    akislar = bg.get_episode_streams("my-new-boss-is-goofy-3-bolum")

    # Sayfa sırası: vk, sibnet, krakenfiles, videa.
    assert [(a["player"], a["label"]) for a in akislar] == [
        ("SIBNET", "Sibnet"), ("KRAKENFILES", "Krakenfiles"), ("VIDEA", "Videa")]
    assert akislar[0]["url"] == "https://video.sibnet.ru/shell.php?videoid=5323084"
    assert akislar[2]["url"] == "https://videa.hu/player?v=O4rjYeDt1DaDKoHq"


def test_akislar_baslikta_baglanti_olan_yazi(site):
    """Başlık "<a>Lord of Mysteries</a> | OKRU"; ok.ru adresi protokolsüz ve
    sorgu dizgili. Başlıklar bölüm numarası taşımıyor: süzme yapılmıyor."""
    _yazi(site, "lord-of-mysteries-1-bolum")

    akislar = bg.get_episode_streams("lord-of-mysteries-1-bolum")

    assert [(a["player"], a["url"]) for a in akislar] == [
        ("ODNOKLASSNIKI", "https://ok.ru/videoembed/9828458891942?nochat=1"),
        ("VIDMOLY", "https://vidmoly.net/embed-kmmpkgsffjfn.html")]
    assert bg._aynalar(json.loads(oku("post-lord-of-mysteries-1-bolum.json"))[0]
                       ["content"]["rendered"])[1][0] == "Lord of Mysteries | VIDMOLY"


def test_iki_bolumluk_yazida_yalnizca_o_bolumun_aynalari(site):
    _yazi(site, "no-love-zone-1-2-bolum")

    birinci = bg.get_episode_streams("no-love-zone-1-2-bolum#1")
    ikinci = bg.get_episode_streams("no-love-zone-1-2-bolum#2")
    hepsi = bg.get_episode_streams("no-love-zone-1-2-bolum")

    assert [a["url"] for a in birinci] == ["https://video.sibnet.ru/shell.php?videoid=5813021"]
    assert [a["url"] for a in ikinci] == ["https://video.sibnet.ru/shell.php?videoid=5813033"]
    # Parçasız kimlikte ikisi de; aynı etiket iki kez görünmüyor.
    assert [a["label"] for a in hepsi] == ["Sibnet", "Sibnet 2"]
    assert site.cagrilar[0]["params"]["slug"] == "no-love-zone-1-2-bolum"


def test_hic_oynatilabilir_ayna_yoksa_bos_liste(site):
    """Umibe no Étranger yalnızca MEGA + vidmoly.to gömüyor."""
    _yazi(site, "umibe-no-etranger")
    assert bg.get_episode_streams("umibe-no-etranger") == []


def test_akislar_bilinmeyen_yazi_hata(site):
    with pytest.raises(bg.BuguiTRHatasi, match="bulunamadı"):
        bg.get_episode_streams("boyle-bir-yazi-yok-xyz")


@pytest.mark.parametrize("kimlik", ["", "x#y", "../a", "a#1#2"])
def test_akislar_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(bg.BuguiTRHatasi):
        bg.get_episode_streams(kimlik)
    assert site.cagrilar == []


def test_iframe_adresi_tembel_yukleme_ve_paragraf_ici():
    """Klasik düzenleyici iframe'i <p> içine koyuyor; tembel yükleme eklentisi
    gerçek adresi data-src'ye taşıyıp src'ye boş sayfa koyuyor."""
    icerik = (
        '<p><strong>1. BÖLÜM | SIBNET</strong><br>'
        '<iframe src="about:blank" data-src="https://video.sibnet.ru/shell.php?videoid=1">'
        '</iframe></p>'
        '<p>2. BÖLÜM | SIBNET</p><p><iframe src=//video.sibnet.ru/shell.php?videoid=2></iframe></p>'
        '<iframe src="https://www.youtube.com/embed/fragman"></iframe>'
    )
    assert bg._aynalar(icerik) == [
        ("1. BÖLÜM | SIBNET", "https://video.sibnet.ru/shell.php?videoid=1"),
        ("2. BÖLÜM | SIBNET", "https://video.sibnet.ru/shell.php?videoid=2"),
        ("2. BÖLÜM | SIBNET", "https://www.youtube.com/embed/fragman"),
    ]
    assert [a["url"] for a in bg.akislari_ayristir(icerik, 2)] == [
        "https://video.sibnet.ru/shell.php?videoid=2"]
    # Fragman "video var" sayılmıyor: tanıtım yine bölüm bağlantılarına bakar.
    assert bg._video_iframeleri('<iframe src="https://www.youtube.com/embed/x"></iframe>') == []


def test_bolum_adresi():
    assert bg.bolum_adresi("tadaima-okaeri-3-bolum") == \
        "https://buguitr.com/tadaima-okaeri-3-bolum/"
    assert bg.bolum_adresi("no-love-zone-1-2-bolum#2") == \
        "https://buguitr.com/no-love-zone-1-2-bolum/#2"
    # Adres bölüm nesnesinin kimliği gibi de kullanılıyor: ayrık kalmalı.
    assert bg.bolum_adresi("no-love-zone-1-2-bolum#1") != bg.bolum_adresi("no-love-zone-1-2-bolum#2")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP katmanı
# ─────────────────────────────────────────────────────────────────────────────
def test_istekler_arasinda_en_az_bir_saniye(site, monkeypatch):
    site.tablo["posts:search=sasaki"] = json_yanit("search-sasaki.json")
    saat, uykular = [50.0], []

    def uyu(sn):
        uykular.append(round(sn, 3))
        saat[0] += sn
    monkeypatch.setattr(bg, "time", types.SimpleNamespace(monotonic=lambda: saat[0], sleep=uyu))
    monkeypatch.setattr(bg, "_MIN_INTERVAL", 1.0)

    bg.search_buguitr("sasaki")
    saat[0] += 0.25
    bg.search_buguitr("sasaki")

    assert uykular == [0.75]


def test_curl_cffi_yoksa_tarayici_user_agenti(monkeypatch):
    """Barındırıcı python-requests'in kendi UA'sına 403 veriyor."""
    class Oturum:
        def __init__(self):
            self.headers = {}

    monkeypatch.setattr(bg, "_HAS_CURL", False)
    monkeypatch.setattr(bg, "_http", types.SimpleNamespace(Session=Oturum))
    assert bg._yeni_oturum().headers["User-Agent"].startswith("Mozilla/5.0")


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt ve uygulama boru hattı
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_buguitr_kaynagini_sunuyor():
    kaynak = kayit.bul("buguitr")
    assert kaynak is not None and kayit.bul("BU'GUI TR") is kaynak
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("BuguiTR", "BuguiTR", "BG", "#b33771", "BUGUITR")
    assert kaynak.modul == "buguitr" and kaynak.cli_kodu == "buguitr"
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    uclar = kaynak.uclar()
    assert uclar.ara is bg.search_buguitr
    assert uclar.bolumler is bg.get_anime_episodes
    assert uclar.akislar is bg.get_episode_streams
    assert kaynak.bolum_adresi("no-love-zone-1-2-bolum#2") == \
        "https://buguitr.com/no-love-zone-1-2-bolum/#2"


def test_rozet_rengi_ve_kisaltmasi_tekil():
    diger = [k for k in kayit.KAYNAKLAR if k.ad != "BuguiTR"]
    assert "BG" not in {k.kisaltma for k in diger}
    assert "#b33771" not in {k.renk.lower() for k in diger}


def test_kayittan_bolumler_referer_yt_dlpye_ulasiyor(site, monkeypatch):
    """Köprü/CLI yolu: bölüm nesnesi yazının adresini taşıyor, fansub adı
    görünüyor, seçilen videonun yt-dlp seçeneklerinde Referer var."""
    from turkanime_api.sources import adapter as adapter_mod

    _yazi(site, "no-love-zone")
    _yazi(site, "no-love-zone-1-2-bolum")
    gorulen = []

    def bilgi(url, secenekler):
        gorulen.append((url, (secenekler.get("http_headers") or {}).get("Referer")))
        return {"url": url, "ext": "mp4"}
    monkeypatch.setattr(adapter_mod, "extract_video_info", bilgi)

    bolumler = adapter_mod.kayittan_bolumler(kayit.bul("BuguiTR"), "no-love-zone", "No Love Zone")
    bolum = bolumler[1]
    assert bolum.title == "2. Bölüm"
    assert bolum.url == "https://buguitr.com/no-love-zone-1-2-bolum/#2"
    assert bolum.fansubs == ["BU'GUI TR"]

    video = bolum.best_video()

    assert video is not None and video.referer == "https://buguitr.com/"
    assert gorulen[0] == ("https://video.sibnet.ru/shell.php?videoid=5813033",
                          "https://buguitr.com/")


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (--network)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = bg.search_buguitr("tadaima", limit=5)
    assert sonuc and sonuc[0][0] == "tadaima-okaeri", sonuc
    bolumler = bg.get_anime_episodes(sonuc[0][0])
    assert len(bolumler) >= 12
    akislar = bg.get_episode_streams(bolumler[0][0])
    assert akislar and all(a["referer"] == bg.REFERER for a in akislar)

    oturum = bg._yeni_oturum()
    yanit = oturum.get(akislar[0]["url"], timeout=30, headers={"Referer": bg.REFERER})
    assert yanit.status_code == 200
