"""SeiCode kaynağı (`turkanime_api/sources/seicode.py`).

Hiçbir test ağa çıkmaz: modülün HTTP oturumu sahte bir oturumla değiştiriliyor.
Bu şart — conftest'in ağ mandalı `socket`'i kesiyor ama curl_cffi kendi
libcurl'ünü kullandığı için mandalın yanından geçer.

Fikstürler (`tests/fixtures/seicode/`) 2026-09-24'te alınan gerçek yanıtlar.
JSON API yanıtları olduğu gibi. ok.ru gömme sayfaları kırpıldı: <title> ve
oynatıcı kartının (`data-options` taşıyan) açılış etiketleri aynen, aradaki
ilgisiz ~30 KB betik/stil çıkarıldı; okcdn adreslerindeki `srcIp=` araştırma
makinesinin IP'si, belgelendirme aralığına (203.0.113.0/24) çevrildi.
`tauvideo-api-cf403.html` tau-video'nun veri merkezi IP'lerine verdiği
Cloudflare engel sayfası (aynı işlem). `katalog-tum.json` `/anime?page=1..6`
yanıtlarının `animes` listelerinin sırayla birleşimi (180 dizi); testler onu
yeniden 32'lik sayfalara bölüyor. tau-video'nun BAŞARILI yanıtı bu
makineden alınamadı; o yanıt testte AnimeciX'in ayrıştırdığı biçimle
(`{"urls": [{"label", "url"}]}`) elle kuruluyor.

`--network` ile ayrıca canlı bir duman testi koşar (arama → bölümler → akış).
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from turkanime_api.sources import kayit
from turkanime_api.sources import seicode as sc

FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "seicode"
API = sc.API_URL


def oku(ad: str) -> str:
    return (FIKSTUR / ad).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sahte site
# ─────────────────────────────────────────────────────────────────────────────
class Yanit:
    """curl_cffi yanıtının kullandığımız kadarı."""

    def __init__(self, status_code: int = 200, text: str = "",
                 headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self.text = text
        self.headers = dict(headers or {})
        self.kapandi = False

    def json(self):
        return json.loads(self.text)

    def close(self):
        self.kapandi = True


def sayfa(ad: str) -> Yanit:
    tur = "application/json" if ad.endswith(".json") else "text/html"
    return Yanit(200, oku(ad), {"Content-Type": tur})


def api(ad: str, kod: int = 200) -> Yanit:
    return Yanit(kod, oku(ad), {"Content-Type": "application/json; charset=utf-8"})


Cevap = Any   # Yanit | Exception | Callable[[params], Yanit] | list (sırayla)


class SahteOturum:
    """Adres (sorgusuz) → cevap tablosu; bütün oturumlar aynı tabloyu paylaşıyor."""

    def __init__(self, tablo: Dict[str, Cevap], kayit_: List[Dict[str, Any]]):
        self.tablo = tablo
        self.kayit = kayit_
        self.kapandi = False

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.kayit.append({"url": url, "params": dict(params or {}),
                           "headers": dict(headers or {}), "timeout": timeout,
                           "stream": stream})
        cevap = self.tablo.get(url)
        if isinstance(cevap, list):
            cevap = cevap.pop(0) if len(cevap) > 1 else cevap[0]
        if callable(cevap) and not isinstance(cevap, Yanit):
            cevap = cevap(dict(params or {}))
        if cevap is None:
            return Yanit(404, "Not Found")
        if isinstance(cevap, BaseException):
            raise cevap
        return cevap

    def close(self):
        self.kapandi = True


class _AgYasak:
    """Gerçek oturum kurulursa test ağa çıkıyor demektir."""

    class Session:
        def __init__(self, *a, **k):
            raise AssertionError("seicode testi gerçek HTTP oturumu kurmaya çalıştı")


@pytest.fixture
def site(monkeypatch):
    """Modülü sahte siteye bağla; bekleme yok, önbellek temiz."""
    tablo: Dict[str, Cevap] = {}
    cagrilar: List[Dict[str, Any]] = []
    oturumlar: List[SahteOturum] = []

    def yeni_oturum():
        oturum = SahteOturum(tablo, cagrilar)
        oturumlar.append(oturum)
        return oturum

    monkeypatch.setattr(sc, "_http", _AgYasak)
    monkeypatch.setattr(sc, "_yeni_oturum", yeni_oturum)
    monkeypatch.setattr(sc, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(sc, "_YENIDEN_DENEME_BEKLEMESI", 0.0)
    monkeypatch.setattr(sc, "_oturum_nesnesi", None)
    monkeypatch.setattr(sc, "_son_istek", 0.0)
    monkeypatch.setattr(sc, "_detay_onbellek", {})
    monkeypatch.setattr(sc, "_katalog_onbellek", (0.0, []))
    monkeypatch.setattr(sc, "_okru_onbellek", {})
    return types.SimpleNamespace(tablo=tablo, cagrilar=cagrilar, oturumlar=oturumlar)


SAYFA_BOYU = 32


def _katalog(site) -> None:
    """`/anime?page=N` → gerçek kataloğun N. 32'lik dilimi (sitenin sayfalaması)."""
    tum = json.loads(oku("katalog-tum.json"))
    toplam = -(-len(tum) // SAYFA_BOYU)

    def cevap(params):
        n = int(params["page"])
        return Yanit(200, json.dumps({
            "animes": tum[(n - 1) * SAYFA_BOYU:n * SAYFA_BOYU], "page": n,
            "totalPages": toplam}), {"Content-Type": "application/json"})
    site.tablo[f"{API}/anime"] = cevap


def _adresler(cagrilar) -> List[str]:
    return [c["url"] for c in cagrilar]


def _anime(site, slug: str, ad: Optional[str] = None) -> None:
    site.tablo[f"{API}/anime/{slug}"] = api(ad or f"anime-{slug}.json")


def _okru(site, kimlik: str, ad: str) -> None:
    site.tablo[f"https://ok.ru/videoembed/{kimlik}"] = sayfa(ad)


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_ayristiriliyor_ve_istek_dogru(site):
    site.tablo[f"{API}/anime/search"] = api("search-jujutsu.json")

    assert sc.search_seicode("  jujutsu ") == [("jujutsu-kaisen", "JUJUTSU KAISEN")]

    istek = site.cagrilar[0]
    assert istek["params"] == {"q": "jujutsu"}
    assert istek["headers"]["Referer"] == "https://seicode.net/"
    assert istek["headers"]["Accept"] == "application/json"
    assert istek["timeout"] == sc.HTTP_TIMEOUT


def test_zengin_arama_kapagi_kart_boyutunda(site):
    """Site TMDB "original" (MB'larca) veriyor; kart için w342, çift / tekil."""
    site.tablo[f"{API}/anime/search"] = api("search-jujutsu.json")

    assert sc.search_seicode_zengin("jujutsu") == [{
        "slug": "jujutsu-kaisen", "title": "JUJUTSU KAISEN",
        "image": "https://image.tmdb.org/t/p/w342/fHpKWq9ayzSk8nSwqRuaAUemRKh.jpg",
    }]


def test_arama_site_sirasi_ve_limit(site):
    site.tablo[f"{API}/anime/search"] = api("search-demon-slayer.json")

    sonuc = sc.search_seicode("demon slayer")
    assert [s for s, _ in sonuc] == ["demon-slayer-kimetsu-no-yaiba", "demon-lord-2099",
                                     "welcome-to-demon-school-iruma-kun"]
    assert sonuc[0][1] == "Demon Slayer: Kimetsu no Yaiba"
    assert len(sc.search_seicode("demon slayer", limit=2)) == 2


def test_arama_donghua(site):
    site.tablo[f"{API}/anime/search"] = api("search-link.json")
    assert sc.search_seicode("link") == [
        ("link-click", "LINK CLICK"),
        ("link-click-the-daily-life-in-lightime", "Link Click: The Daily Life in Lightime"),
    ]


def test_arama_sonuc_yoksa_bos(site):
    """Katalogda olmayan dizi (One Piece) ya da romaji ad: API de katalog da boş."""
    site.tablo[f"{API}/anime/search"] = api("search-empty.json")
    _katalog(site)
    assert sc.search_seicode("one piece") == []
    assert sc.search_seicode_zengin("kusuriya") == []


def test_arama_bossa_katalogda_alt_dizge_araniyor(site):
    """Sitenin araması kelime tabanlı: "iruma" → [] (dizi "…Iruma-kun")."""
    site.tablo[f"{API}/anime/search"] = api("search-empty.json")
    _katalog(site)

    assert sc.search_seicode_zengin("iruma") == [{
        "slug": "welcome-to-demon-school-iruma-kun",
        "title": "Welcome to Demon School! Iruma-kun",
        "image": "https://image.tmdb.org/t/p/w342/aed6I1EMR4Lbk8bdikWrndbn5Og.jpg",
    }]
    sayfalar = [c["params"]["page"] for c in site.cagrilar if c["url"] == f"{API}/anime"]
    assert sayfalar == [1, 2, 3, 4, 5, 6]
    # Boşluksuz yazım ve birden çok kelime (hepsi aranıyor, sıra katalogdaki).
    assert [s for s, _ in sc.search_seicode("rezero")] == [
        "re-zero-kara-hajimeru-break-time", "re-zero-starting-life-in-another-world-"]
    assert sc.search_seicode("JUJUTSU kai") == [("jujutsu-kaisen", "JUJUTSU KAISEN")]
    assert sc.search_seicode("spy x family") == [("spy-x-family", "SPY x FAMILY")]
    # Tek harf ya da yalnız kısa parçalar bütün kataloğa uyardı: yedek çalışmaz.
    assert sc.search_seicode("x!") == [] and sc.search_seicode("no") == []
    # Katalog 6 saat önbellekte: ikinci ve üçüncü yedek arama sayfa istemedi.
    assert len([c for c in site.cagrilar if c["url"] == f"{API}/anime"]) == 6
    # Yedek de yalnızca SeiCode'a gidiyor (sunucu tarayıcısının garantisi).
    assert all(c["url"].startswith(API + "/") for c in site.cagrilar)


def test_katalog_alinamazsa_arama_bos_ama_hatasiz(site):
    """Arama cevap verdi ("yok"); yedeğin düşmesi aramayı hataya çevirmemeli."""
    site.tablo[f"{API}/anime/search"] = api("search-empty.json")
    site.tablo[f"{API}/anime"] = Yanit(403, "blocked")
    assert sc.search_seicode("iruma") == []


def test_arama_kisa_sorgu_aga_cikmiyor(site):
    """Site 2 karakterden kısa sorguya 400 veriyor; istek hiç atılmamalı."""
    assert sc.search_seicode("a") == []
    assert sc.search_seicode("   ") == []
    assert sc.search_seicode("") == []
    assert sc.search_seicode("naruto", limit=0) == []
    assert site.cagrilar == []


def test_arama_400_bos_liste(site):
    site.tablo[f"{API}/anime/search"] = Yanit(400, "[]")
    _katalog(site)
    assert sc.search_seicode("x!") == []


def test_arama_gecersiz_ve_yinelenen_slug_ayiklaniyor(site):
    site.tablo[f"{API}/anime/search"] = Yanit(200, json.dumps([
        {"slug": "solo-leveling", "english": "Solo Leveling"},
        {"slug": "../../admin", "english": "Kötü"},
        {"slug": "solo-leveling", "english": "Solo Leveling (tekrar)"},
        {"slug": "Buyuk-Harf", "english": "Büyük harf slug"},
        {"slug": "adsiz"},
        "çöp",
    ]))
    assert sc.search_seicode("solo") == [("solo-leveling", "Solo Leveling"),
                                         ("adsiz", "adsiz")]


def test_arama_ag_hatasi_bir_kez_yeniden_denenip_sebeple_yukseliyor(site):
    """Arama sayfası "sonuç yok" değil sebebi göstermeli (AramaSonuclari.hatalar)."""
    site.tablo[f"{API}/anime/search"] = ConnectionError("ağ yok")

    with pytest.raises(sc.SeiCodeHatasi, match="bağlanılamadı"):
        sc.search_seicode("naruto")
    assert len(site.cagrilar) == 2


def test_zaman_asimi_sebebi_kullaniciya_ulasiyor(site):
    from turkanime_api.common.hatalar import KaynakYanitVermedi, kaynak_hatasi

    site.tablo[f"{API}/anime/jujutsu-kaisen"] = TimeoutError("timed out")
    with pytest.raises(sc.SeiCodeHatasi) as hata:
        sc.get_anime_episodes("jujutsu-kaisen")
    cevrilen = kaynak_hatasi(hata.value, "SeiCode")
    assert isinstance(cevrilen, KaynakYanitVermedi) and "zaman aşımı" in str(cevrilen)


def test_arama_gecici_5xx_sonrasi_basariyor(site):
    site.tablo[f"{API}/anime/search"] = [Yanit(502, "Bad Gateway"),
                                         api("search-jujutsu.json")]
    assert sc.search_seicode("jujutsu") == [("jujutsu-kaisen", "JUJUTSU KAISEN")]
    assert len(site.cagrilar) == 2


def test_engel_yeniden_denenmiyor_ve_engellenme_sayiliyor(site):
    from turkanime_api.common.hatalar import KaynakEngellendi, kaynak_hatasi
    from turkanime_server.crawler.nezaket import ENGELLENME, hata_turu

    site.tablo[f"{API}/anime/search"] = Yanit(403, "<html>error code: 1020</html>")

    with pytest.raises(sc.SeiCodeHatasi, match="engelledi") as hata:
        sc.search_seicode("naruto")
    assert hata.value.status_code == 403
    assert len(site.cagrilar) == 1, "engelde üstelemek engeli uzatır"
    # Sunucu tarayıcısı kaynağı dinlendirsin; arayüz "erişim reddedildi" desin.
    assert hata_turu(hata.value) == ENGELLENME
    assert isinstance(kaynak_hatasi(hata.value, "SeiCode"), KaynakEngellendi)


def test_cloudflare_challenge_503_engel(site):
    site.tablo[f"{API}/anime/search"] = Yanit(
        503, "<title>Just a moment...</title><div>challenge-platform</div>")
    with pytest.raises(sc.SeiCodeHatasi, match="engelledi"):
        sc.search_seicode("naruto")
    assert len(site.cagrilar) == 1


def test_json_yerine_html_gelirse_hata(site):
    site.tablo[f"{API}/anime/search"] = Yanit(200, "<!DOCTYPE html><html>bakım</html>")
    with pytest.raises(sc.SeiCodeHatasi, match="beklenmeyen"):
        sc.search_seicode("naruto")


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_tek_sezon_ama_1_degil_sezon_yaziliyor(site):
    """JJK'de sitede yalnız 3. sezon var: "1. Bölüm" 1. sezon sanılırdı."""
    _anime(site, "jujutsu-kaisen")

    bolumler = sc.get_anime_episodes("jujutsu-kaisen")

    assert _adresler(site.cagrilar) == [f"{API}/anime/jujutsu-kaisen"]
    assert len(bolumler) == 12
    assert bolumler[0] == ("jujutsu-kaisen/3/1", "3. Sezon 1. Bölüm")
    assert bolumler[-1] == ("jujutsu-kaisen/3/12", "3. Sezon 12. Bölüm")


def test_bolumler_cok_sezon_izleme_sirasiyla(site):
    _anime(site, "link-click")
    bolumler = sc.get_anime_episodes("link-click")
    assert len(bolumler) == 31
    assert bolumler[11][0] == "link-click/1/12" and bolumler[12][0] == "link-click/2/1"
    assert bolumler[-1] == ("link-click/3/7", "3. Sezon 7. Bölüm")


def test_bolumler_mutlak_numarali_sezonlar(site):
    """Bleach: 1. sezonun yalnız 46. bölümü, 2. sezon (TYBW) 27-48."""
    _anime(site, "bleach")
    bolumler = sc.get_anime_episodes("bleach")
    assert len(bolumler) == 22
    assert bolumler[:2] == [("bleach/1/46", "1. Sezon 46. Bölüm"),
                           ("bleach/2/27", "2. Sezon 27. Bölüm")]


def test_bolumler_yalniz_birinci_sezonda_sezon_yazilmiyor(site):
    _anime(site, "witch-hat-atelier")
    bolumler = sc.get_anime_episodes("witch-hat-atelier")
    assert bolumler[0] == ("witch-hat-atelier/1/1", "1. Bölüm")
    assert all(b.endswith(". Bölüm") and "Sezon" not in b for _, b in bolumler)


def test_bolumu_olmayan_dizi_bos_liste(site):
    _anime(site, "moriarty-the-patriot")
    assert sc.get_anime_episodes("moriarty-the-patriot") == []


def test_bilinmeyen_anime_kalici_hata(site):
    from turkanime_server.crawler.nezaket import KALICI, hata_turu

    site.tablo[f"{API}/anime/yok-boyle"] = api("anime-404.json", kod=404)

    with pytest.raises(sc.SeiCodeHatasi, match="bulunamadı") as hata:
        sc.get_anime_episodes("yok-boyle")
    assert hata.value.status_code == 404
    assert hata_turu(hata.value) == KALICI


@pytest.mark.parametrize("kimlik", ["../etc/passwd", "jujutsu kaisen", "", "-abc", "a/b"])
def test_bolumler_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(sc.SeiCodeHatasi, match="geçersiz") as hata:
        sc.get_anime_episodes(kimlik)
    assert hata.value.status_code == 400
    assert site.cagrilar == []


def test_bolumler_buyuk_harf_kimlik_kabul(site):
    _anime(site, "jujutsu-kaisen")
    assert len(sc.get_anime_episodes(" Jujutsu-Kaisen/ ")) == 12


def test_bolumler_sirasiz_ve_bozuk_veride_kararli():
    veri = {"seasons": [
        {"season_number": 2, "episodes": [{"episode_number": 10}, {"episode_number": 2},
                                          {"episode_number": "x"}, {"episode_number": 2}]},
        {"season_number": 1, "episodes": [{"episode_number": 1}]},
        {"season_number": None, "episodes": [{"episode_number": 5}]},
        {"season_number": True, "episodes": [{"episode_number": 6}]},
        "çöp",
    ]}
    assert sc.bolumleri_ayristir("x", veri) == [
        ("x/1/1", "1. Sezon 1. Bölüm"),
        ("x/2/2", "2. Sezon 2. Bölüm"),
        ("x/2/10", "2. Sezon 10. Bölüm"),
    ]
    assert sc.bolumleri_ayristir("x", {"seasons": None}) == []


def test_detay_bes_dakika_onbellekte(site, monkeypatch):
    """Bölüm listesini açıp hemen oynatan kullanıcı detayı ikinci kez indirmesin."""
    _anime(site, "jujutsu-kaisen")
    _okru(site, "14299278608896", "okru-embed-14299278608896-blocked.html")
    saat = [1000.0]
    monkeypatch.setattr(sc, "time", types.SimpleNamespace(
        monotonic=lambda: saat[0], sleep=lambda _s: None))

    sc.get_anime_episodes("jujutsu-kaisen")
    sc.get_episode_streams("jujutsu-kaisen/3/1")
    assert _adresler(site.cagrilar).count(f"{API}/anime/jujutsu-kaisen") == 1

    saat[0] += sc._DETAY_TTL + 1          # site yeni bölüm eklemiş olabilir
    sc.get_anime_episodes("jujutsu-kaisen")
    assert _adresler(site.cagrilar).count(f"{API}/anime/jujutsu-kaisen") == 2


def test_api_istekleri_arasinda_aralik_var_cozuculerde_yok(site, monkeypatch):
    """API'ye kısa aralık; ok.ru gibi başka konaklara giden çözücüler beklemez."""
    site.tablo[f"{API}/anime/search"] = api("search-jujutsu.json")
    saat, uykular = [50.0], []

    def uyu(sn):
        uykular.append(round(sn, 3))
        saat[0] += sn
    monkeypatch.setattr(sc, "time", types.SimpleNamespace(monotonic=lambda: saat[0], sleep=uyu))
    monkeypatch.setattr(sc, "_MIN_INTERVAL", 0.5)

    sc.search_seicode("jujutsu")
    saat[0] += 0.2
    sc.search_seicode("jujutsu")
    _okru(site, "1", "okru-embed-18756723345920.html")
    sc._okru_coz("https://ok.ru/videoembed/1")

    assert uykular == [0.3]


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def test_akislar_jjk_sirali_ve_normalize(site):
    """ok.ru telif engelli, short.ink/HDVid/Voe/rpmvip atlanıyor; kalanlar
    yt-dlp'nin açtığı biçimde ve güvenilirlik sırasıyla."""
    _anime(site, "jujutsu-kaisen")
    _okru(site, "14299278608896", "okru-embed-14299278608896-blocked.html")

    akislar = sc.get_episode_streams("jujutsu-kaisen/3/1")

    assert [(a["player"], a["url"]) for a in akislar] == [
        ("SIBNET", "https://video.sibnet.ru/shell.php?videoid=6094910"),
        ("SENDVID", "https://sendvid.com/t18tas4f"),
        ("VIDMOLY", "https://vidmoly.biz/embed-jbukyhndpe3m.html"),   # http://vidmoly.me/e/…
        ("GDRIVE", "https://drive.google.com/file/d/1icga0BzM_NrpU-f533LxZ7RjMVlXHX8k/view"),
    ]
    assert [a["label"] for a in akislar] == ["Sibnet", "SendVid", "Vidmoly", "GDrive"]
    assert all(a["fansub"] == "SeiCode" for a in akislar)
    assert "https://ok.ru/videoembed/14299278608896" in _adresler(site.cagrilar)
    konaklar = " ".join(_adresler(site.cagrilar))
    for atlanan in ("short.icu", "hdvid", "voe.sx", "rpmvip"):
        assert atlanan not in konaklar, "atlanan konağa istek gitmemeli"


def test_akislar_okru_mp4e_cozulup_one_geciyor(site):
    _anime(site, "witch-hat-atelier")
    _okru(site, "14838475655711", "okru-embed-14838475655711.html")

    akislar = sc.get_episode_streams("witch-hat-atelier/1/8")

    assert [a["player"] for a in akislar] == ["ODNOKLASSNIKI", "ODNOKLASSNIKI", "SIBNET",
                                              "SENDVID", "GDRIVE"]
    assert [a["label"] for a in akislar[:2]] == ["OK.ru 1080p", "OK.ru 720p"]
    assert all(a["url"].startswith("https://vd346.okcdn.ru/?expires=") and
               a["type"] == "direct" for a in akislar[:2])
    assert akislar[0]["url"] != akislar[1]["url"]
    # Adres bu UA'ya bağlı: yt-dlp de mpv de onunla istemeli.
    assert all(a["user_agent"] == sc.OKRU_UA for a in akislar[:2])
    assert all("user_agent" not in a for a in akislar[2:])
    okru_istegi = next(c for c in site.cagrilar if "ok.ru" in c["url"])
    assert okru_istegi["headers"]["Referer"] == "https://seicode.net/"
    # okcdn adresi isteyenin tarayıcı ailesine bağlı (srcAg): yt-dlp'nin UA'sı
    # Windows Chrome; sayfa da öyle istenmeli yoksa yt-dlp 400 alıyor.
    assert "Windows NT" in okru_istegi["headers"]["User-Agent"]
    assert "Chrome/" in okru_istegi["headers"]["User-Agent"]


def test_akislar_bosluklu_degerler_ve_vidmoly_to(site):
    """Solo Leveling: değerler 8 boşlukla başlıyor; vidmoly.to → .biz; tau-video
    bu makineye Cloudflare engeli veriyor → sessizce yok, diğerleri kalıyor."""
    _anime(site, "solo-leveling")
    _okru(site, "9083051838200", "okru-embed-18756723345920.html")
    site.tablo["https://tau-video.xyz/api/video/68c7d0c34283ea0794d21a84"] = Yanit(
        403, oku("tauvideo-api-cf403.html"), {"Content-Type": "text/html"})

    akislar = sc.get_episode_streams("solo-leveling/2/1")

    assert [a["player"] for a in akislar] == ["ODNOKLASSNIKI", "SIBNET", "VIDMOLY", "GDRIVE"]
    assert akislar[2]["url"] == "https://vidmoly.biz/embed-8t2x00ru5zuv.html"
    assert akislar[1]["url"] == "https://video.sibnet.ru/shell.php?videoid=5790523"
    tau = next(c for c in site.cagrilar if "tau-video" in c["url"])
    assert tau["params"] == {}, "SeiCode'un tau adreslerinde ?vid= yok"
    assert not any("doodstream" in u for u in _adresler(site.cagrilar))


def test_akislar_cop_deger_yok_sayiliyor(site):
    """Kill Blue 6: video_links'te "Encoder: Mercury" diye bir değer var."""
    _anime(site, "kill-blue")
    _okru(site, "18574775486976", "okru-embed-14299278608896-blocked.html")

    akislar = sc.get_episode_streams("kill-blue/1/6")

    assert [a["player"] for a in akislar] == ["SIBNET", "SENDVID", "GDRIVE"]
    assert akislar[2]["url"] == \
        "https://drive.google.com/file/d/1OnXTBcLSNlKE-gGNpMN-tzqpfky7AuZj/view"


def test_akislar_bolum_yoksa_bos(site):
    _anime(site, "jujutsu-kaisen")
    assert sc.get_episode_streams("jujutsu-kaisen/9/9") == []
    assert sc.get_episode_streams("jujutsu-kaisen/3/99") == []


@pytest.mark.parametrize("kimlik", ["../etc/passwd", "jujutsu-kaisen-3-1",
                                    "jujutsu-kaisen/3/1?x=1", "jujutsu-kaisen/3",
                                    "jujutsu-kaisen/1234/1", ""])
def test_akislar_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(sc.SeiCodeHatasi, match="bölüm kimliği"):
        sc.get_episode_streams(kimlik)
    assert site.cagrilar == []


def test_cozucu_hatasi_diger_kopyalari_dusurmuyor(site):
    _anime(site, "witch-hat-atelier")
    site.tablo["https://ok.ru/videoembed/14838475655711"] = TimeoutError("zaman aşımı")

    akislar = sc.get_episode_streams("witch-hat-atelier/1/8")

    assert [a["player"] for a in akislar] == ["SIBNET", "SENDVID", "GDRIVE"]


def test_tek_kopya_tau_ve_engelliyse_bos_liste_ve_sebep(site):
    """Witch Hat Atelier 1: yalnız tau-video (+ atlanan dood/uqload). tau'nun CF
    engeli SeiCode'un engeli değil: sunucu tarayıcısı kaynağı kapatmasın diye
    hata değil boş liste; kullanıcı sebebi `bos_akis_mesaji`'ndan görüyor."""
    from turkanime_api.common.hatalar import VideoYok
    from turkanime_api.sources import adapter as adapter_mod

    _anime(site, "witch-hat-atelier")
    site.tablo["https://tau-video.xyz/api/video/69d3d7da76acb7a32c3537ac"] = Yanit(
        403, oku("tauvideo-api-cf403.html"), {"Content-Type": "text/html"})

    assert sc.get_episode_streams("witch-hat-atelier/1/1") == []

    bolum = adapter_mod.kayittan_bolumler(kayit.bul("SeiCode"), "witch-hat-atelier",
                                          "Witch Hat Atelier")[0]
    with pytest.raises(VideoYok, match="tau-video bu ağı engelliyor"):
        bolum.best_video()


def test_tek_kopya_cozulemezse_gecici_hata(site):
    """Tek kopya ok.ru ve zaman aşımı: "video yok" değil, geçici arıza."""
    from turkanime_api.common.hatalar import KaynakYanitVermedi, kaynak_hatasi
    from turkanime_server.crawler.nezaket import GECICI, hata_turu

    site.tablo["https://ok.ru/videoembed/7"] = TimeoutError("timed out")
    with pytest.raises(sc.SeiCodeHatasi, match="alınamadı") as hata:
        sc.akislari_kur({"OkRu": "https://ok.ru/videoembed/7",
                         "Voe": "https://voe.sx/e/abc"})
    assert hata_turu(hata.value) == GECICI
    assert isinstance(kaynak_hatasi(hata.value, "SeiCode"), KaynakYanitVermedi)


def test_okru_http_hatasi_bos(site):
    site.tablo["https://ok.ru/videoembed/5"] = Yanit(500, "oops")
    assert sc._okru_coz("https://ok.ru/video/5") == []


def test_okru_cozumu_bes_dakika_ayni_adres(site, monkeypatch):
    """okcdn adresi her istekte değişiyor (expires/sig). `yedekli_oynat` mpv'de
    düşen adresi `atla` ile eliyor; adres her `best_video`'da yeni olsaydı aynı
    ok.ru kopyası her denemede yeniden seçilirdi."""
    _anime(site, "witch-hat-atelier")
    sayac = []

    def okru(_params):
        sayac.append(1)
        metin = oku("okru-embed-14838475655711.html").replace("sig=", f"sig=v{len(sayac)}")
        return Yanit(200, metin, {"Content-Type": "text/html"})
    site.tablo["https://ok.ru/videoembed/14838475655711"] = okru
    saat = [500.0]
    monkeypatch.setattr(sc, "time", types.SimpleNamespace(
        monotonic=lambda: saat[0], sleep=lambda _s: None))

    ilk = sc.get_episode_streams("witch-hat-atelier/1/8")
    ikinci = sc.get_episode_streams("witch-hat-atelier/1/8")
    assert ilk == ikinci and len(sayac) == 1

    ikinci[0]["url"] = "değişti"                 # önbellek dışarıdan bozulamasın
    assert sc.get_episode_streams("witch-hat-atelier/1/8")[0]["url"] == ilk[0]["url"]

    saat[0] += sc._OKRU_TTL + 1
    ucuncu = sc.get_episode_streams("witch-hat-atelier/1/8")
    assert len(sayac) == 2 and ucuncu[0]["url"] != ilk[0]["url"]


# ── tau-video ───────────────────────────────────────────────────────────────
TAU_ID = "673251c4dadcfea584d826b2"


def _tau_api(urls: List[Dict[str, str]]) -> Yanit:
    # Elle kurulan yanıt: bu makineden tau-video'ya ulaşılamıyor (CF IP engeli);
    # biçim `sources/animecix.py::_video_streams`'in ayrıştırdığıyla aynı.
    return Yanit(200, json.dumps({"urls": urls}), {"Content-Type": "application/json"})


def test_tau_dogrudan_mp4_ve_olu_cdn_eleniyor(site):
    site.tablo[f"https://tau-video.xyz/api/video/{TAU_ID}"] = _tau_api([
        {"label": "1080p", "url": "https://cdn-a.example/file/tau-video/a.mp4"},
        {"label": "720p", "url": "https://cdn-b.example/file/tau-video/b.mp4"},
        {"label": "480p", "url": "https://cdn-c.example/file/tau-video/c.mp4"},
        {"label": "360p", "url": "javascript:alert(1)"},
    ])
    site.tablo["https://cdn-a.example/file/tau-video/a.mp4"] = Yanit(
        206, "", {"Content-Type": "video/mp4"})
    site.tablo["https://cdn-b.example/file/tau-video/b.mp4"] = TimeoutError("ölü CDN")
    site.tablo["https://cdn-c.example/file/tau-video/c.mp4"] = Yanit(
        200, "<html>404</html>", {"Content-Type": "text/html"})

    akislar = sc.akislari_kur({"tau-video.xyz": f"https://tau-video.xyz/embed/{TAU_ID}"})

    assert akislar == [{
        "url": "https://cdn-a.example/file/tau-video/a.mp4", "label": "TauVideo 1080p",
        "player": "TAUVIDEO", "fansub": "SeiCode", "type": "direct",
        "referer": "https://tau-video.xyz/",
    }]
    yoklamalar = [c for c in site.cagrilar if c["url"].startswith("https://cdn-")]
    assert all(c["headers"]["Range"] == "bytes=0-1" and c["stream"] for c in yoklamalar)
    assert all(c["headers"]["Referer"] == "https://tau-video.xyz/" for c in yoklamalar)
    assert all(c["timeout"] == sc.YOKLAMA_TIMEOUT for c in yoklamalar)
    # Yoklama oturumları kapatılıyor (curl tutamacı sızmasın).
    assert all(o.kapandi for o in site.oturumlar[1:])


def test_tau_vid_parametresi_iletiliyor(site):
    site.tablo[f"https://tau-video.xyz/api/video/{TAU_ID}"] = _tau_api([])
    assert sc._tau_coz(f"https://tau-video.xyz/embed/{TAU_ID}?vid=691338") == []
    assert site.cagrilar[0]["params"] == {"vid": "691338"}


def test_tau_cloudflare_engeli_bos(site):
    site.tablo[f"https://tau-video.xyz/api/video/{TAU_ID}"] = Yanit(
        403, oku("tauvideo-api-cf403.html"), {"Content-Type": "text/html"})
    assert sc._tau_coz(f"https://tau-video.xyz/embed/{TAU_ID}") == []


def test_tau_json_degilse_bos(site):
    site.tablo[f"https://tau-video.xyz/api/video/{TAU_ID}"] = Yanit(200, "<html></html>")
    assert sc._tau_coz(f"https://tau-video.xyz/embed/{TAU_ID}") == []


def test_tau_m3u8_hls_ve_etiketsiz():
    assert sc.tau_akislari({"urls": [{"url": "https://x.example/master.m3u8"}]}) == [{
        "url": "https://x.example/master.m3u8", "label": "TauVideo", "player": "TAUVIDEO",
        "fansub": "SeiCode", "type": "hls", "referer": "https://tau-video.xyz/"}]
    assert sc.tau_akislari(None) == [] and sc.tau_akislari({"urls": "x"}) == []


def test_sira_tau_okru_sibnet_ve_tekrar_yok(site, monkeypatch):
    monkeypatch.setattr(sc, "_tau_coz", lambda _a: [sc._akis("https://t/1.mp4",
                                                             "TauVideo 1080p", "TAUVIDEO")])
    monkeypatch.setattr(sc, "_okru_coz", lambda _a: [sc._akis("https://o/1", "OK.ru 720p",
                                                              "ODNOKLASSNIKI")])
    akislar = sc.akislari_kur({
        "Dailymotion": "https://dai.ly/xa5wbee",
        "GDrive": "https://drive.google.com/file/d/1icga0BzM_NrpU-f533LxZ7RjMVlXHX8k/preview",
        "Mail": "https://my.mail.ru/mail/uploader/video/_myvideo/63.html",
        "Vidmoly": "https://vidmoly.me/v/0n0tybzb29wj",
        "vidmoly.to": "http://vidmoly.to/embed-0n0tybzb29wj.html",     # aynı video
        "SendVid": "https://sendvid.com/fywpqmhn",
        "Sibnet": "https://video.sibnet.ru/shell.php?videoid=6175239",
        "OkRu": "https://ok.ru/videoembed/1",
        "tau-video.xyz": f"https://tau-video.xyz/embed/{TAU_ID}",
    })
    assert [a["player"] for a in akislar] == list(sc.OYNATICI_SIRASI)
    assert site.cagrilar == []


# ── ok.ru ayrıştırıcısı ─────────────────────────────────────────────────────
def test_okru_en_iyi_iki_kalite():
    akislar = sc.okru_akislari(oku("okru-embed-14838475655711.html"))
    assert [a["label"] for a in akislar] == ["OK.ru 1080p", "OK.ru 720p"]
    assert all(a["player"] == "ODNOKLASSNIKI" and a["fansub"] == "SeiCode" for a in akislar)


def test_okru_tek_kalite():
    akislar = sc.okru_akislari(oku("okru-embed-18756723345920.html"))
    assert [a["label"] for a in akislar] == ["OK.ru 1080p"]
    assert akislar[0]["url"].startswith("https://vd329.okcdn.ru/?expires=")


def test_okru_telif_engelli_bos():
    assert sc.okru_akislari(oku("okru-embed-14299278608896-blocked.html")) == []


def test_okru_metadata_dizge_olarak_da_okunuyor():
    """yt-dlp'nin beklediği ESKİ biçim (JSON dizgesi) de çalışmalı: ok.ru iki
    biçimi de kullandı; yeni biçim (nesne) yt-dlp 2026.08.19'u çökertiyor."""
    import html as html_mod
    import re

    sayfa_ = oku("okru-embed-14838475655711.html")
    ham = re.search(r'data-options="([^"]+)"', sayfa_).group(1)
    secenekler = json.loads(html_mod.unescape(ham))
    assert isinstance(secenekler["flashvars"]["metadata"], dict)
    secenekler["flashvars"]["metadata"] = json.dumps(secenekler["flashvars"]["metadata"])
    eski = ('<div data-options="{}"></div><div data-options="'
            + html_mod.escape(json.dumps(secenekler)) + '"></div>')

    assert [a["label"] for a in sc.okru_akislari(eski)] == ["OK.ru 1080p", "OK.ru 720p"]


# ── Adres normalizasyonu (ağsız) ────────────────────────────────────────────
@pytest.mark.parametrize("ham,beklenen", [
    ("https://video.sibnet.ru/shell.php?videoid=6094910",
     ("SIBNET", "https://video.sibnet.ru/shell.php?videoid=6094910")),
    ("https://video.sibnet.ru/video6094910-bolum", ("SIBNET",
     "https://video.sibnet.ru/shell.php?videoid=6094910")),
    ("https://vidmoly.me/v/0n0tybzb29wj", ("VIDMOLY", "https://vidmoly.biz/embed-0n0tybzb29wj.html")),
    ("http://vidmoly.me/e/nahf4ty75upw", ("VIDMOLY", "https://vidmoly.biz/embed-nahf4ty75upw.html")),
    ("http://vidmoly.to/embed-0wrke2e6o0e4.html",
     ("VIDMOLY", "https://vidmoly.biz/embed-0wrke2e6o0e4.html")),
    ("http://vidmoly.to/embed-qxleu17eipk2.htm",
     ("VIDMOLY", "https://vidmoly.biz/embed-qxleu17eipk2.html")),
    ("https://vidmoly.net/embed-wlnxyvgofoaz.html",
     ("VIDMOLY", "https://vidmoly.biz/embed-wlnxyvgofoaz.html")),
    ("https://vidmoly.biz/embed-4mpi48l1hajd.html",
     ("VIDMOLY", "https://vidmoly.biz/embed-4mpi48l1hajd.html")),
    ("https://sendvid.com/fywpqmhn", ("SENDVID", "https://sendvid.com/fywpqmhn")),
    ("https://sendvid.com/embed/fywpqmhn", ("SENDVID", "https://sendvid.com/fywpqmhn")),
    ("https://drive.google.com/file/d/1OjLyMq3Gxb4X9-fzGAtny2XIkxwGO5t8/view?usp=drive_link",
     ("GDRIVE", "https://drive.google.com/file/d/1OjLyMq3Gxb4X9-fzGAtny2XIkxwGO5t8/view")),
    ("https://drive.google.com/file/d/15VSy1Ah8ZzWLfs3wsub-8TNrLGe33tv1/preview?usp=drive_link",
     ("GDRIVE", "https://drive.google.com/file/d/15VSy1Ah8ZzWLfs3wsub-8TNrLGe33tv1/view")),
    ("https://drive.google.com/open?id=1Dbd_qhWKTf-A44v5NXXb7w-zPPDTRyb5&usp=drive_copy",
     ("GDRIVE", "https://drive.google.com/file/d/1Dbd_qhWKTf-A44v5NXXb7w-zPPDTRyb5/view")),
    ("https://dai.ly/xa5wbee", ("DAILYMOTION", "https://www.dailymotion.com/video/xa5wbee")),
    ("https://www.dailymotion.com/embed/video/x9ie7wm",
     ("DAILYMOTION", "https://www.dailymotion.com/video/x9ie7wm")),
    ("https://geo.dailymotion.com/player.html?video=x9etx12&mute=true",
     ("DAILYMOTION", "https://www.dailymotion.com/video/x9etx12")),
    ("https://my.mail.ru/xmail.ru/seicodesubs/video/_myvideo/70.html",
     ("MAIL", "https://my.mail.ru/xmail.ru/seicodesubs/video/_myvideo/70.html")),
])
def test_normalize(ham, beklenen):
    akis = sc.normalize_et(ham)
    assert akis is not None and (akis["player"], akis["url"]) == beklenen
    assert akis["fansub"] == "SeiCode" and "type" not in akis


@pytest.mark.parametrize("ham", [
    "https://doodstream.com/e/qolj513xgcy4", "https://dood.li/d/0u51mnrh2ik2",
    "https://uqload.io/embed-amcwody0i2jc.html", "https://voe.sx/e/kgdrvhtocw3p",
    "https://filemoon.sx/d/d3dqqll38kd4", "https://www.mp4upload.com/ycv8u0votb2p",
    "https://luluvdo.com/e/on1qa7goapdz", "https://lulu.st/5mukusurl6k2",
    "https://short.icu/8x5mmK3yF", "https://short.ink/AmN0TXjZD",
    "https://abyssplayer.com/z0n2MX9Dl", "https://player.abyssplayer.com/WjghtbyAv",
    "https://seicode.rpmvip.com/#aswn85&dl=1", "https://hdvid.tv/nmipe3uij6ux.html",
    "https://streamtape.com/v/JkRrApWjVJTeq3/", "https://files.fm/f/ntkdrscswv",
    "https://mega.nz/embed/qIVjFDDb#eIGi", "https://embedrise.com/v/66bf7019c2f22",
    "https://vidhdthe.online/embed-st1ej2nvf89e.html", "https://vudeo.io/xt3qgx5t2idk.html",
    "https://drive.google.com/drive/folders/1IeM3AhXc9NT0uFVtL4FaoF707pNeo6Re",
    "https://vidmoly.me/", "https://sendvid.com/", "https://example.com/video.mp4",
    "https://book.ru/video/123",
])
def test_atlanan_ve_taninmayan_adresler(ham):
    assert sc.normalize_et(ham) is None
    assert sc.cozulecek_mi(ham) is None


@pytest.mark.parametrize("ham,kimlik", [
    ("https://ok.ru/videoembed/14838475655711", "14838475655711"),
    ("http://ok.ru/video/8381970188911", "8381970188911"),
    ("https://ok.ru/videoembed/8537567857397/", "8537567857397"),
    ("https://m.ok.ru/videoembed/1", "1"),
    ("https://odnoklassniki.ru/videoembed/2", "2"),
])
def test_okru_adresleri_cozucuye_gidiyor(ham, kimlik):
    assert sc.cozulecek_mi(ham) is sc._okru_coz
    assert sc._okru_kimligi(ham) == kimlik


@pytest.mark.parametrize("ham,beklenen", [
    ("        https://video.sibnet.ru/shell.php?videoid=1",
     "https://video.sibnet.ru/shell.php?videoid=1"),
    ("//ok.ru/videoembed/1", "https://ok.ru/videoembed/1"),
    ("Encoder: Mercury", None),
    ("javascript:alert(1)", None),
    ("ftp://files.example/a.mp4", None),
    ("https://", None),
    (None, None),
    (123, None),
])
def test_gomme_adresi_temizle(ham, beklenen):
    assert sc.gomme_adresi_temizle(ham) == beklenen


def test_izleme_adresi():
    assert sc.izleme_adresi("jujutsu-kaisen/3/1") == \
        "https://seicode.net/anime/jujutsu-kaisen/3/1"
    # Tanınmayan kimlikler aynı adrese çökmesin (adres bölüm nesnesinin kimliği).
    assert sc.izleme_adresi("a b?c") == "https://seicode.net/anime/a%20b%3Fc"


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt ve uygulama boru hattı
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_seicode_kaynagini_sunuyor():
    kaynak = kayit.bul("seicode")
    assert kaynak is not None and kaynak is kayit.bul("SeiCode")
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("SeiCode", "SeiCode", "SC", "#badc58", "SEICODE")
    assert kaynak.modul == "seicode" and kaynak.cli_kodu == "seicode"
    assert kaynak.deneysel and kaynak.cli_etiketi == "SeiCode (deneysel)"
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    # Rozet rengi/kısaltması başka bir kaynağınkiyle karışmasın.
    digerleri = [k for k in kayit.KAYNAKLAR if k is not kaynak]
    assert kaynak.renk not in {k.renk for k in digerleri}
    assert kaynak.kisaltma not in {k.kisaltma for k in digerleri}
    uclar = kaynak.uclar()
    assert uclar.ara is sc.search_seicode
    assert uclar.zengin_ara is sc.search_seicode_zengin
    assert uclar.bolumler is sc.get_anime_episodes
    assert uclar.akislar is sc.get_episode_streams
    assert kaynak.bolum_adresi("jujutsu-kaisen/3/1") == \
        "https://seicode.net/anime/jujutsu-kaisen/3/1"
    assert kaynak.bolum_slugu("jujutsu-kaisen/3/1") == "seicode-jujutsu-kaisen-3-1"


def test_kayittan_bolumler_best_video_ilk_calisani_seciyor(site, monkeypatch):
    """Köprü/CLI yolu: bölüm nesnesi izleme adresini ve kimlikten slug'ı taşıyor;
    `best_video` ok.ru'nun 1080p MP4'ünü önce deniyor, olmazsa sıradakine geçiyor."""
    from turkanime_api.sources import adapter as adapter_mod

    _anime(site, "witch-hat-atelier")
    _okru(site, "14838475655711", "okru-embed-14838475655711.html")
    denenen = []

    def bilgi(url, _secenekler):
        denenen.append(url)
        return {} if "okcdn.ru" in url else {"url": url, "ext": "mp4"}
    monkeypatch.setattr(adapter_mod, "extract_video_info", bilgi)

    bolumler = adapter_mod.kayittan_bolumler(kayit.bul("SeiCode"), "witch-hat-atelier",
                                             "Witch Hat Atelier")
    bolum = bolumler[7]
    assert bolum.title == "8. Bölüm"
    assert bolum.url == "https://seicode.net/anime/witch-hat-atelier/1/8"
    assert bolum.slug == "seicode-witch-hat-atelier-1-8"
    assert bolum.fansubs == ["SeiCode"]

    video = bolum.best_video()

    assert video is not None and video.player == "SIBNET"
    assert video.url == "https://video.sibnet.ru/shell.php?videoid=6195307"
    assert "okcdn.ru" in denenen[0] and "okcdn.ru" in denenen[1]


def test_okru_ua_yt_dlp_ve_mpv_ye_ulasiyor(site, monkeypatch):
    """ok.ru adresi isteyenin tarayıcı ailesine bağlı (`srcAg`): uygulamanın
    mpv'ye verdiği kısa UA ile 400 dönüyor (2026-09-30, mpv 0.37 ile ölçüldü).
    Akışın UA'sı yt-dlp seçeneklerine ve mpv komutuna aynen gitmeli."""
    from turkanime_api.common import mpv_oynatici
    from turkanime_api.sources import adapter as adapter_mod

    _anime(site, "witch-hat-atelier")
    _okru(site, "14838475655711", "okru-embed-14838475655711.html")
    gorulen = []

    def bilgi(url, secenekler):
        gorulen.append((secenekler.get("http_headers") or {}).get("User-Agent"))
        return {"url": url, "ext": "mp4"}
    monkeypatch.setattr(adapter_mod, "extract_video_info", bilgi)
    komutlar = []
    monkeypatch.setattr(mpv_oynatici, "_mpv", lambda: "mpv")
    monkeypatch.setattr(mpv_oynatici, "calistir", lambda cmd: komutlar.append(cmd))

    bolum = adapter_mod.kayittan_bolumler(kayit.bul("SeiCode"), "witch-hat-atelier",
                                          "Witch Hat Atelier")[7]
    video = bolum.best_video()
    assert video.player == "ODNOKLASSNIKI" and video.label == "OK.ru 1080p"
    assert video.user_agent == sc.OKRU_UA and gorulen == [sc.OKRU_UA]

    mpv_oynatici.video_oynat(video)
    assert f"--user-agent={sc.OKRU_UA}" in komutlar[0]

    # UA bildirmeyen akış eski davranışta: yt-dlp kendi UA'sı, mpv varsayılanı.
    sibnet = adapter_mod.AdapterVideo(bolum, "https://video.sibnet.ru/shell.php?videoid=1",
                                      "Sibnet", player="SIBNET")
    assert sibnet.user_agent is None
    assert "User-Agent" not in (sibnet.ydl_opts.get("http_headers") or {})
    mpv_oynatici.video_oynat(sibnet)
    assert f"--user-agent={mpv_oynatici.USER_AGENT}" in komutlar[1]


def test_akis_saglayici_engeli_turkce_sebeple_iletiyor(site):
    from turkanime_api.common.hatalar import KaynakEngellendi

    site.tablo[f"{API}/anime/jujutsu-kaisen"] = Yanit(429, "Too Many Requests")
    kaynak = kayit.bul("SeiCode")
    saglayici = kayit.akis_saglayici(kaynak.uclar().akislar, "jujutsu-kaisen/3/1",
                                     kaynak.etiket)
    with pytest.raises(KaynakEngellendi, match="SeiCode"):
        saglayici("yok sayılır")


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (--network)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = sc.search_seicode("jujutsu", limit=5)
    assert ("jujutsu-kaisen", "JUJUTSU KAISEN") in sonuc
    bolumler = sc.get_anime_episodes("jujutsu-kaisen")
    assert bolumler and bolumler[0][0] == "jujutsu-kaisen/3/1"
    akislar = sc.get_episode_streams(bolumler[0][0])
    assert any(a["player"] == "SIBNET" for a in akislar)
