"""AnimeTR kaynağı (`turkanime_api/sources/animetr.py`).

Hiçbir test ağa çıkmaz: modülün HTTP oturumu sahte bir oturumla değiştiriliyor.
Bu şart — conftest'in ağ mandalı `socket`'i kesiyor ama curl_cffi kendi
libcurl'ünü kullandığı için mandalın yanından geçer. Cloudflare yedeği
(`_cf_yedegi`) de sahte: gerçeği Qt/FlareSolverr zincirine uzanırdı.

Fikstürler (`tests/fixtures/animetr/`) sitenin 2026-09-24 ve 2026-09-30
tarihli gerçek yanıtlarından kırpıldı; işaretleme AYNEN, yalnızca
ayrıştırıcının okumadığı bölgeler ve boş satırlar atıldı:

* `seriler-*.html`: başlık + <head>'deki `rel="next"` + üst menüdeki
  "Rastgele Seri" bağlantısı + sonuç başlığından sayfalamaya kadar olan
  bölge. Kartların üzerine gelince açılan bilgi kutusu (özet, türler,
  sayaçlar) atıldı; kart bağlantısı ve <img> aynen.
* `seri-*.html`: başlık + "Bölümler (…)" başlığından "Benzer Seriler"e
  kadar. One Piece'te 1.150 satırın ilk 7'si (yinelenen bolum-5 dahil),
  bütün aralık satırları ve son 5'i tutuldu.
* `izle-*.html`: başlık + `<script id="epspage-data">` aynen.
* `sibnet-shell-*.html`: canlı ve silinmiş ("Ошибка обработки видео") video.
* `sendvid-embed.html`: Sendvid gömme sayfasının başlığı + <video> öğesi.
* `search-series-*.json`: yanıtlar olduğu gibi.

`--network` ile ayrıca canlı bir duman testi koşar (arama → bölümler → akış).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from turkanime_api.sources import animetr as at
from turkanime_api.sources import kayit

KOK = Path(__file__).resolve().parent.parent
FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "animetr"
TABAN = "https://animetr.co"


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
        self.headers = headers or {}


def sayfa(ad: str, kod: int = 200) -> Yanit:
    return Yanit(kod, oku(ad))


class BeklenmeyenIstek(BaseException):
    """Tablada olmayan adres. `Exception` DEĞİL: modül ağ hatalarını (ayna
    denetiminde) `Exception` ile yutuyor; bu, yutulmadan teste kadar çıksın."""


class SahteOturum:
    """Adres → cevap tablosu (bütün iş parçacıklarının oturumları paylaşıyor).

    Anahtar sitenin yolu ("/seri/one-piece") ya da tam adres (Sibnet);
    HEAD istekleri "HEAD <adres>" anahtarıyla. Cevap bir `Yanit`, bir istisna,
    `(params) -> Yanit` ya da sırayla verilecek liste. Tabloda olmayan adres
    testi düşürür: sessizce 404 dönmek, beklenmeyen bir isteği "bulunamadı"
    diye gizlerdi.
    """

    def __init__(self, tablo: Dict[str, Any], cagrilar: List[Dict[str, Any]]):
        self.tablo = tablo
        self.cagrilar = cagrilar

    def head(self, url, headers=None, timeout=None, allow_redirects=None):
        return self.get(f"HEAD {url}", headers=headers, timeout=timeout)

    def get(self, url, params=None, headers=None, timeout=None):
        self.cagrilar.append({"url": url, "params": dict(params or {}),
                              "headers": dict(headers or {}), "timeout": timeout})
        anahtar = url[len(TABAN):] if url.startswith(TABAN) else url
        if anahtar not in self.tablo:
            raise BeklenmeyenIstek(f"beklenmeyen istek: {url} {params}")
        cevap = self.tablo[anahtar]
        if isinstance(cevap, list):
            cevap = cevap.pop(0) if len(cevap) > 1 else cevap[0]
        if callable(cevap) and not isinstance(cevap, Yanit):
            cevap = cevap(dict(params or {}))
        if isinstance(cevap, BaseException):
            raise cevap
        return cevap


class _AgYasak:
    """Gerçek oturum kurulursa test ağa çıkıyor demektir."""

    class Session:
        def __init__(self, *a, **k):
            raise AssertionError("animetr testi gerçek HTTP oturumu kurmaya çalıştı")


@pytest.fixture
def site(monkeypatch):
    """Modülü sahte siteye bağla; bekleme yok, CF yedeği kayıtlı ve kapalı."""
    tablo: Dict[str, Any] = {}
    cagrilar: List[Dict[str, Any]] = []
    cf: List[str] = []
    cf_cevap: List[Optional[Yanit]] = [None]

    def cf_yedegi(url, basliklar):
        cf.append(url)
        return cf_cevap[0]

    monkeypatch.setattr(at, "_http", _AgYasak)
    monkeypatch.setattr(at, "_yeni_oturum", lambda: SahteOturum(tablo, cagrilar))
    monkeypatch.setattr(at, "_yerel", threading.local())
    monkeypatch.setattr(at, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(at, "_son_istek", 0.0)
    monkeypatch.setattr(at, "_cf_yedegi", cf_yedegi)
    return types.SimpleNamespace(tablo=tablo, cagrilar=cagrilar, cf=cf, cf_cevap=cf_cevap)


def _yollar(cagrilar) -> List[str]:
    return [c["url"][len(TABAN):] if c["url"].startswith(TABAN) else c["url"]
            for c in cagrilar]


def _sayfalar(**sayfa_no_ad):
    """/seriler: `page` parametresine göre fikstür (1 → parametresiz istek)."""
    def cevap(params):
        return sayfa(sayfa_no_ad[f"s{params.get('page', 1)}"])
    return cevap


SIBNET_CANLI = "https://video.sibnet.ru/shell.php?videoid=6100508"
SIBNET_OLU = "https://video.sibnet.ru/shell.php?videoid=6293724"
SENDVID_JJK = "https://sendvid.com/embed/tt3y3jtz"
# `sendvid-embed.html`'deki <source> (başka bir videonun sayfası; biçim aynı).
SENDVID_MP4 = ("https://videos2.sendvid.com/09/cf/s5jusokd.mp4?validfrom=1790741172"
               "&validto=1790755572&rate=250k&hash=vNxRdwxpSggp%2BZ3YchxuBdpJFro%3D")


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_liste_ve_json_birlesip_alakaya_gore_siralaniyor(site):
    """Liste sayfası yeni→eski: 2. sezon asıl diziden önce geliyor."""
    site.tablo["/seriler"] = sayfa("seriler-frieren.html")
    site.tablo["/search-series"] = sayfa("search-series-frieren.json")

    sonuc = at.search_animetr("  frieren ", limit=10)

    assert sonuc == [("sousou-no-frieren", "Sousou no Frieren"),
                     ("sousou-no-frieren-2nd-season", "Sousou no Frieren 2nd Season")]
    liste, js = site.cagrilar
    assert (liste["url"], liste["params"]) == (f"{TABAN}/seriler", {"title": "frieren"})
    assert (js["url"], js["params"]) == (f"{TABAN}/search-series", {"q": "frieren"})
    assert all(c["headers"]["Referer"] == TABAN + "/" for c in site.cagrilar)
    assert all(c["timeout"] == at.HTTP_TIMEOUT for c in site.cagrilar)


def test_arama_rel_next_ile_sayfalaniyor_limit_dolunca_json_istenmiyor(site):
    """"one piece": 20 sonuç, 12 + 8. JSON ucu 15 FİLM veriyor, asıl diziyi
    atlıyor; liste sayfası doluysa ona hiç gidilmiyor."""
    site.tablo["/seriler"] = _sayfalar(s1="seriler-one-piece-1.html",
                                       s2="seriler-one-piece-2.html")

    sonuc = at.search_animetr("one piece", limit=20)

    assert [c["params"] for c in site.cagrilar] == [
        {"title": "one piece"}, {"title": "one piece", "page": 2}]
    assert len(sonuc) == 20 and len({s for s, _ in sonuc}) == 20
    assert sonuc[0] == ("one-piece", "One Piece"), "tam eşleşme en önde"
    assert ("one-piece-film-red", "One Piece Film: Red") in sonuc, "2. sayfadan"


def test_arama_limit_ilk_sayfada_dolarsa_tek_istek(site):
    site.tablo["/seriler"] = _sayfalar(s1="seriler-one-piece-1.html")

    sonuc = at.search_animetr("one piece", limit=5)

    assert len(site.cagrilar) == 1
    assert len(sonuc) == 5 and sonuc[0][0] == "one-piece"


def test_arama_son_sayfada_durur(site):
    """Son sayfada `rel="next"` yok: üçüncü sayfa istenmiyor."""
    site.tablo["/seriler"] = _sayfalar(s1="seriler-one-piece-1.html",
                                       s2="seriler-one-piece-2.html")
    site.tablo["/search-series"] = sayfa("search-series-one-piece.json")

    sonuc = at.search_animetr("one piece", limit=50)

    assert _yollar(site.cagrilar) == ["/seriler", "/seriler", "/search-series"]
    assert len(sonuc) == 20, "JSON'daki 15 film listede zaten var"


def test_arama_json_ingilizce_adla_tamamliyor_ve_url_title_tercih_ediliyor(site):
    """"soul land" bir İngilizce ad; iki Douluo Dalu sezonu aynı `url_en`'i
    ("soul-land") paylaşıyor — kimlik olarak kullanılsaydı ikisi tek kayda
    düşerdi."""
    site.tablo["/seriler"] = sayfa("seriler-bos.html")
    site.tablo["/search-series"] = sayfa("search-series-soul-land.json")

    sonuc = dict(at.search_animetr("soul land"))

    assert set(sonuc) == {"douluo-dalu", "douluo-dalu-2nd-season",
                          "douluo-dalu-jiandao-chen-xin", "douluo-dalu-ii-jueshi-tangmen"}
    assert "soul-land" not in sonuc


def test_arama_sonuc_yoksa_bos_liste(site):
    site.tablo["/seriler"] = sayfa("seriler-bos.html")
    site.tablo["/search-series"] = sayfa("search-series-bos.json")
    assert at.search_animetr("zzzqqq") == []


def test_arama_kisa_sorgu_aga_cikmiyor(site):
    """Site 2 karakterden kısa sorguya boş dönüyor (kendi JS'i de öyle)."""
    assert at.search_animetr("a") == []
    assert at.search_animetr("   ") == []
    assert at.search_animetr("naruto", limit=0) == []
    assert site.cagrilar == []


def _filtresiz_liste() -> Yanit:
    """Site `title` parametresini yok saysaydı: filtresiz liste en yeni serileri
    veriyor ve başlığı "Anime ve Donghua Listesi" (arama sonucu değil)."""
    return Yanit(200, oku("seriler-one-piece-1.html").replace(
        "&quot;one piece&quot; Arama Sonuçları", "Anime ve Donghua Listesi"))


@pytest.mark.parametrize("liste_cevabi", [
    Yanit(500, "Server Error"),
    Yanit(200, "<html><title>Bakım</title><body>Arama Sonuçları yok</body></html>"),
    ConnectionError("bağlantı koptu"),
    _filtresiz_liste(),
])
def test_arama_liste_sayfasi_okunamazsa_json_yetiyor(site, liste_cevabi):
    site.tablo["/seriler"] = liste_cevabi
    site.tablo["/search-series"] = sayfa("search-series-frieren.json")

    assert [s for s, _ in at.search_animetr("frieren")] == [
        "sousou-no-frieren", "sousou-no-frieren-2nd-season"]


def test_arama_iki_uc_da_okunamazsa_sebep_yukseliyor(site):
    """Arama sayfası "sonuç yok" değil sebebi göstermeli (AramaSonuclari.hatalar)."""
    site.tablo["/seriler"] = ConnectionError("DNS çözülemedi")
    site.tablo["/search-series"] = ConnectionError("DNS çözülemedi")

    with pytest.raises(at.AnimeTRHatasi, match="ulaşılamadı"):
        at.search_animetr("naruto")


def test_arama_json_bozuksa_liste_sonucu_yine_donuyor(site):
    site.tablo["/seriler"] = sayfa("seriler-frieren.html")
    site.tablo["/search-series"] = Yanit(200, "<!DOCTYPE html><html>hata</html>")

    assert len(at.search_animetr("frieren")) == 2


def test_zengin_arama_kapaklari_mutlak_adres(site):
    site.tablo["/seriler"] = sayfa("seriler-bos.html")
    site.tablo["/search-series"] = sayfa("search-series-frieren.json")

    sonuc = {k["slug"]: k["image"] for k in at.zengin_ara("frieren")}

    # JSON göreli yol veriyor ("images/series/…"): /storage/ altında.
    assert sonuc["sousou-no-frieren"] == f"{TABAN}/storage/images/series/1456_poster.webp"
    assert sonuc["sousou-no-frieren-2nd-season"] == \
        "https://myanimelist.net/images/anime/1921/154528.jpg"


def test_liste_kartlari_ve_kapaklari():
    kartlar, sonraki = at.liste_ayristir(oku("seriler-one-piece-1.html"))
    assert sonraki
    assert len(kartlar) == 12
    assert kartlar[0] == {"slug": "one-piece", "title": "One Piece",
                          "image": f"{TABAN}/storage/images/series/1718_poster.webp?v=1778423137"}
    # Her kart kendi kapağını almalı (bir sonrakinin <img>'ine taşmamalı).
    assert len({k["image"] for k in kartlar}) == 12
    # Üst menüdeki "Rastgele Seri" bağlantısı da /seri/'ye gidiyor ama kart değil.
    assert "doupo-cangqiong-special-1" not in {k["slug"] for k in kartlar}


def test_liste_basliklarinda_html_kacislari_cozuluyor():
    kart = ('<a href="https://animetr.co/seri/x" class="anime-card" data-category="anime" '
            'aria-label="Kaguya-sama wa Kokurasetai: Tensai-tachi no Ren&#039;ai Zunousen">'
            '<figure class="anime-card__image"><img src="images/series/1_poster.webp"></figure></a>')
    kartlar, sonraki = at.liste_ayristir(kart)
    assert kartlar == [{"slug": "x", "image": f"{TABAN}/storage/images/series/1_poster.webp",
                        "title": "Kaguya-sama wa Kokurasetai: Tensai-tachi no Ren'ai Zunousen"}]
    assert not sonraki


def test_liste_sayfasi_taninmazsa_hata():
    """Kart da "N sonuç bulundu" da yoksa bu bir liste sayfası değil."""
    with pytest.raises(at.AnimeTRHatasi, match="düzeni değişmiş"):
        at.liste_ayristir("<html><body>Bakımdayız</body></html>")
    assert at.liste_ayristir(oku("seriler-bos.html")) == ([], False)


def test_json_liste_degilse_hata():
    with pytest.raises(at.AnimeTRHatasi, match="beklenen biçimde değil"):
        at.json_ayristir({"message": "Server Error"})


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_one_piece_araliklar_ve_yinelenen_satir(site):
    site.tablo["/seri/one-piece"] = sayfa("seri-one-piece-kirpik.html")

    bolumler = at.get_anime_episodes("one-piece")

    kimlikler = [k for k, _ in bolumler]
    assert _yollar(site.cagrilar) == ["/seri/one-piece"]
    assert len(kimlikler) == len(set(kimlikler)) == 18, "yinelenen bolum-5 ayıklanmalı"
    assert bolumler[:3] == [("one-piece/bolum-1", "1. Bölüm"), ("one-piece/bolum-2", "2. Bölüm"),
                            ("one-piece/bolum-3", "3. Bölüm")]
    # Aralık satırı tek video: tek kayıt, Asya Animeleri'yle aynı etiket biçimi.
    assert ("one-piece/bolum-215-216", "215-216. Bölüm") in bolumler
    assert ("one-piece/bolum-1146-1150", "1146-1150. Bölüm") in bolumler
    assert bolumler[-1] == ("one-piece/bolum-1162", "1162. Bölüm")


def test_bolumler_donghua_aralik_satirlari(site):
    site.tablo["/seri/douluo-dalu"] = sayfa("seri-douluo-dalu.html")

    bolumler = at.get_anime_episodes("douluo-dalu")

    assert len(bolumler) == 25
    assert bolumler[0] == ("douluo-dalu/bolum-1-5", "1-5. Bölüm")
    assert bolumler[-1] == ("douluo-dalu/bolum-241-250", "241-250. Bölüm")


def test_bolum_adi_etikete_ekleniyor_ve_ayristirici_numarayi_okuyor(site):
    from turkanime_api.common.episode_parser import parse_episode

    site.tablo["/seri/sousou-no-frieren-2nd-season"] = sayfa(
        "seri-sousou-no-frieren-2nd-season.html")

    bolumler = at.get_anime_episodes("sousou-no-frieren-2nd-season")

    assert len(bolumler) == 10
    assert bolumler[-1] == ("sousou-no-frieren-2nd-season/bolum-10", "10. Bölüm - Final")
    assert parse_episode(bolumler[-1][1]).episode == 10
    assert parse_episode("1146-1150. Bölüm").episode == 1150


def test_bolumler_takma_adla_acilsa_da_kanonik_slug(site):
    """`url_en` takma adı da açılıyor; bölüm kimlikleri satırdaki kanonik slug'ı taşır."""
    site.tablo["/seri/frieren-beyond-journey-s-end-season-2"] = sayfa(
        "seri-sousou-no-frieren-2nd-season.html")

    bolumler = at.get_anime_episodes("frieren-beyond-journey-s-end-season-2")

    assert bolumler[0][0] == "sousou-no-frieren-2nd-season/bolum-1"


def test_bolumler_tam_adres_de_kabul_ediliyor(site):
    site.tablo["/seri/douluo-dalu"] = sayfa("seri-douluo-dalu.html")
    assert len(at.get_anime_episodes("https://animetr.co/seri/douluo-dalu/")) == 25


def test_bolumler_ters_sirali_gelirse_duzeltiliyor():
    metin = oku("seri-sousou-no-frieren-2nd-season.html")
    satirlar = re.findall(r'<div class="list-group-item d-flex.*?(?=<div class="list-group-item'
                          r' d-flex|</div>\n</div>\n</div>\n</div>)', metin, re.S)
    assert len(satirlar) == 10
    ters = metin.replace("".join(satirlar), "".join(reversed(satirlar)))
    kimlikler = [k for k, _ in at.bolumleri_ayristir(ters)]
    assert kimlikler[0].endswith("/bolum-1") and kimlikler[-1].endswith("/bolum-10")


def test_bolumu_olmayan_seri_bos_liste():
    """"Bölümler ( 0 / 0)" başlığı var, satır yok: gerçek bir "0 bölüm"."""
    metin = ('<h2 class="mb-4 text-center fw-bold fs-2">Bölümler (  0 / 12)</h2>'
             '<div class="list-group shadow-sm"><div class="list-group-scrollable"></div></div>')
    assert at.bolumleri_ayristir(metin) == []


def test_bolumler_404_kalici_hata(site):
    from turkanime_server.crawler.nezaket import KALICI, hata_turu

    site.tablo["/seri/naruto-shippuuden"] = sayfa("seri-404.html", 404)

    with pytest.raises(at.AnimeTRHatasi, match="bulunamadı") as hata:
        at.get_anime_episodes("naruto-shippuuden")
    assert hata.value.status_code == 404
    assert hata_turu(hata.value) == KALICI


def test_bolumler_sayfa_yapisi_degismisse_hata(site):
    site.tablo["/seri/one-piece"] = Yanit(200, "<html><title>One Piece</title></html>")
    with pytest.raises(at.AnimeTRHatasi, match="bölüm listesi bulunamadı"):
        at.get_anime_episodes("one-piece")


@pytest.mark.parametrize("kimlik", [
    "", "../../etc/passwd", "one piece", "seri/a/b", "https://kotu.example/seri/one-piece",
])
def test_bolumler_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(at.AnimeTRHatasi) as hata:
        at.get_anime_episodes(kimlik)
    assert hata.value.status_code == 400
    assert site.cagrilar == []


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def test_akislar_sibnet_basligi_kirpiliyor_mail_orijinal_adresle(site):
    site.tablo["/izle/one-piece/bolum-1"] = sayfa("izle-one-piece-bolum-1.html")
    site.tablo["https://video.sibnet.ru/shell.php?videoid=4850279"] = \
        sayfa("sibnet-shell-canli.html")

    akislar = at.get_episode_streams("one-piece/bolum-1")

    assert akislar == [
        {"url": "https://video.sibnet.ru/shell.php?videoid=4850279",
         "label": "Asyaanimeleri - Sibnet", "player": "SIBNET", "type": "iframe",
         "fansub": "Asyaanimeleri"},
        # Gömme biçimi (/video/embed/347) yt-dlp'de 404; sayfa adresi çalışıyor.
        {"url": "https://my.mail.ru/mail/depsan/video/_myvideo/347.html",
         "label": "Asyaanimeleri - Mail.ru", "player": "MAIL", "type": "iframe",
         "fansub": "Asyaanimeleri"},
    ]
    # referer BİLEREK yok: Sibnet MP4'ü Referer = shell adresi istiyor.
    assert all("referer" not in a for a in akislar)


def test_akislar_vidmoly_site_hatasi_duzeltiliyor_oynatilamayanlar_atiliyor(site):
    """Doupo 210: Vidmoly iki kez ve embed'i "embed-v.html" (404) diye kayıtlı;
    VK gömme adresi yt-dlp'de açılmıyor; VIP/abyss/upns oynatılamıyor."""
    site.tablo["/izle/doupo-cangqiong-nian-fan/bolum-210"] = sayfa(
        "izle-doupo-cangqiong-nian-fan-bolum-210.html")

    akislar = at.get_episode_streams("doupo-cangqiong-nian-fan/bolum-210")

    assert [(a["player"], a["url"]) for a in akislar] == [
        ("VK", "https://vkvideo.ru/video-229950337_456239423"),
        ("GDRIVE", "https://drive.google.com/file/d/1Bi-40-WQjGf8J7gQnyhCZ6OFwdCkeIvX/view"),
        ("VIDMOLY", "https://vidmoly.net/embed-c6iu2in8dfm3.html"),
        ("ODNOKLASSNIKI", "https://ok.ru/videoembed/15860137134803"),
    ]
    assert akislar[0]["label"] == "Asyaanimeleri - VK Video"


def _jjk_sitesi(site, sendvid_head: Any) -> None:
    site.tablo["/izle/jujutsu-kaisen/bolum-1"] = sayfa("izle-jujutsu-kaisen-bolum-1.html")
    site.tablo["https://video.sibnet.ru/shell.php?videoid=4410965"] = \
        sayfa("sibnet-shell-canli.html")
    site.tablo[SENDVID_JJK] = sayfa("sendvid-embed.html")
    site.tablo[f"HEAD {SENDVID_MP4}"] = sendvid_head


def test_akislar_drive_u0_yolu_ve_sendvid(site):
    _jjk_sitesi(site, Yanit(200, "", {"content-length": "367001600"}))

    akislar = at.get_episode_streams("jujutsu-kaisen/bolum-1")

    assert [(a["player"], a["url"]) for a in akislar] == [
        ("SIBNET", "https://video.sibnet.ru/shell.php?videoid=4410965"),
        # "/file/u/0/d/<id>/view" yt-dlp'de "Unsupported URL".
        ("GDRIVE", "https://drive.google.com/file/d/1IAFodXxRulRJqAdGo-e-SBs0F2RFC5jW/view"),
        ("VIDMOLY", "https://vidmoly.net/embed-jmqyxsm35hm2.html"),
        ("SENDVID", "https://sendvid.com/embed/tt3y3jtz"),
        ("ODNOKLASSNIKI", "https://ok.ru/videoembed/3130504710757"),
    ]
    # vidoza (404), voe (403), hdvid/vidthehd (502) atıldı.
    assert not any(k in a["url"] for a in akislar for k in ("vidoza", "voe.sx", "vidthehd"))
    head = next(c for c in site.cagrilar if c["url"].startswith("HEAD "))
    assert head["headers"]["Referer"] == SENDVID_JJK


def test_sendvid_yer_tutucusu_atiliyor(site):
    """Sendvid erişilemeyen videoyu 200 ile 5 sn'lik "This video is temporarily
    unavailable" MP4'ü olarak veriyor (36.789 bayt); yt-dlp "çalışıyor" diyor."""
    _jjk_sitesi(site, Yanit(200, "", {"content-length": "36789"}))

    akislar = at.get_episode_streams("jujutsu-kaisen/bolum-1")

    assert "SENDVID" not in [a["player"] for a in akislar]
    assert len(akislar) == 4


@pytest.mark.parametrize("head", [
    Yanit(405, ""),                                  # HEAD desteklenmiyor
    Yanit(200, "", {}),                              # boyut yok
    ConnectionError("bağlantı koptu"),
])
def test_sendvid_denetlenemezse_karari_yt_dlp_veriyor(site, head):
    _jjk_sitesi(site, head)
    assert "SENDVID" in [a["player"] for a in at.get_episode_streams("jujutsu-kaisen/bolum-1")]


def test_akislar_vk_com_ve_dailymotion_sayfa_adresine(site):
    site.tablo["/izle/sousou-no-frieren-2nd-season/bolum-1"] = sayfa(
        "izle-sousou-no-frieren-2nd-season-bolum-1.html")
    site.tablo[SIBNET_CANLI] = sayfa("sibnet-shell-canli.html")

    akislar = at.get_episode_streams("sousou-no-frieren-2nd-season/bolum-1")

    assert [(a["player"], a["url"]) for a in akislar] == [
        ("SIBNET", SIBNET_CANLI),
        ("VK", "https://vkvideo.ru/video883756997_456240487"),
        ("GDRIVE", "https://drive.google.com/file/d/1Fay410QUFyvEDQowjK2I3wdcDxbdoFjd/view"),
        ("VIDMOLY", "https://vidmoly.net/embed-lq8htmrjvqpj.html"),
        ("DAILYMOTION", "https://www.dailymotion.com/video/x9xwqlw"),
    ]


def test_silinmis_sibnet_videosu_atiliyor(site):
    """Silinen videonun shell sayfası da 200; yt-dlp'nin genel çıkarıcısı onda
    ext=php bir "video" bulup is_working'i kandırıyor."""
    site.tablo["/izle/grand-blue-3-sezon/bolum-12"] = sayfa(
        "izle-grand-blue-3-sezon-bolum-12.html")
    site.tablo[SIBNET_OLU] = sayfa("sibnet-shell-olu.html")

    akislar = at.get_episode_streams("grand-blue-3-sezon/bolum-12")

    assert [a["player"] for a in akislar] == ["GDRIVE", "VIDMOLY"]
    sibnet = next(c for c in site.cagrilar if c["url"] == SIBNET_OLU)
    assert sibnet["timeout"] == at.DENETIM_TIMEOUT


@pytest.mark.parametrize("cevap", [ConnectionError("zaman aşımı"), Yanit(503, "")])
def test_sibnet_denetlenemezse_karari_yt_dlp_veriyor(site, cevap):
    """Yalnızca KESİN ölüm (200 + MP4 yolu yok) atılır; denetlenemeyen aday
    kalır — yt-dlp de açamazsa zaten "çalışmıyor" sayılır."""
    site.tablo["/izle/grand-blue-3-sezon/bolum-12"] = sayfa(
        "izle-grand-blue-3-sezon-bolum-12.html")
    site.tablo[SIBNET_OLU] = cevap

    assert at.get_episode_streams("grand-blue-3-sezon/bolum-12")[0]["url"] == SIBNET_OLU


def test_ayna_denetimi_kapatilabilir(site):
    site.tablo["/izle/grand-blue-3-sezon/bolum-12"] = sayfa(
        "izle-grand-blue-3-sezon-bolum-12.html")

    akislar = at.get_episode_streams("grand-blue-3-sezon/bolum-12", dogrula=False)

    assert akislar[0]["player"] == "SIBNET"
    assert _yollar(site.cagrilar) == ["/izle/grand-blue-3-sezon/bolum-12"]


def test_akislar_404_video_yok_olarak_raporlaniyor(site):
    """Aralığın içindeki numara ("bolum-1150", aralık 1146-1150) 404 veriyor."""
    from turkanime_api.common.hatalar import VideoYok

    site.tablo["/izle/one-piece/bolum-1150"] = sayfa("izle-404.html", 404)

    with pytest.raises(at.AnimeTRHatasi, match="bulunamadı") as hata:
        at.get_episode_streams("one-piece/bolum-1150")
    assert hata.value.status_code == 404
    # Oynatma yolu: akış sağlayıcısı bunu "video yok" diye kullanıcıya söyler.
    saglayici = kayit.akis_saglayici(at.get_episode_streams, "one-piece/bolum-1150",
                                     etiket="AnimeTR")
    with pytest.raises(VideoYok, match="AnimeTR"):
        saglayici("yok sayılır")


@pytest.mark.parametrize("govde,iz", [
    ("<html><title>One Piece 1. Bölüm</title><body>oynatıcı yok</body></html>",
     "epspage-data"),
    ('<script type="application/json" id="epspage-data">{"episodeId": 1, </script>',
     "bozuk JSON"),
])
def test_akislar_sayfa_taninmazsa_hata(site, govde, iz):
    site.tablo["/izle/one-piece/bolum-1"] = Yanit(200, govde)
    with pytest.raises(at.AnimeTRHatasi, match=iz):
        at.get_episode_streams("one-piece/bolum-1")


def test_fansub_yoksa_bos_liste():
    assert at.akislari_ayristir(
        '<script type="application/json" id="epspage-data">{"episodeId":1,"fansubs":[]}'
        "</script>") == []


@pytest.mark.parametrize("kimlik", [
    "one-piece", "one-piece/1162", "one-piece/bolum-1/fazla", "../x/bolum-1",
    "https://kotu.example/izle/one-piece/bolum-1",
])
def test_akislar_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(at.AnimeTRHatasi, match="bölüm kimliği|AnimeTR adresi değil"):
        at.get_episode_streams(kimlik)
    assert site.cagrilar == []


@pytest.mark.parametrize("saglayici,embed,orijinal,beklenen", [
    # Sibnet: yalnızca orijinal adres (başlık yapışık) de yeter.
    ("Sibnet", "", "https://video.sibnet.ru/video4850279-_One_Piece_/",
     ("https://video.sibnet.ru/shell.php?videoid=4850279", "SIBNET")),
    # Drive: open?id= biçimi.
    ("Google Drive", "https://drive.google.com/open?id=1Bi-40-WQjGf8J7gQnyhCZ6OFwdCkeIvX", "",
     ("https://drive.google.com/file/d/1Bi-40-WQjGf8J7gQnyhCZ6OFwdCkeIvX/view", "GDRIVE")),
    # Vidmoly: embed-v.html ve orijinal yok → kimlik kurulamaz.
    ("Vidmoly", "https://vidmoly.net/embed-v.html", "", None),
    ("Vidmoly", "https://vidmoly.to/embed-abcdef123456.html", "",
     ("https://vidmoly.net/embed-abcdef123456.html", "VIDMOLY")),
    # VK: orijinal yoksa gömme adresinin oid/id'si.
    ("VK", "https://vk.com/video_ext.php?oid=-1234&id=5678&hd=1", "",
     ("https://vkvideo.ru/video-1234_5678", "VK")),
    ("OK.ru", "//ok.ru/videoembed/123456", "",
     ("https://ok.ru/videoembed/123456", "ODNOKLASSNIKI")),
    ("Dailymotion", "https://geo.dailymotion.com/player.html?video=x9abc", "",
     ("https://www.dailymotion.com/video/x9abc", "DAILYMOTION")),
    ("Sendvid", "", "https://sendvid.com/s5jusokd",
     ("https://sendvid.com/embed/s5jusokd", "SENDVID")),
    # Mail.ru: yalnızca gömme biçimi varsa (yt-dlp'de 404) atılır.
    ("Mail.ru", "https://my.mail.ru/video/embed/347", "", None),
    ("Mail.ru", "", "http://my.mail.ru/mail/depsan/video/_myvideo/347.html",
     ("https://my.mail.ru/mail/depsan/video/_myvideo/347.html", "MAIL")),
    # Sitenin kendi/oynatılamayan oynatıcıları.
    ("Vip", "https://asyaanimeleri.pw/video/ac0b236e", "https://asyaanimeleri.pw/video/ac0b236e",
     None),
    ("AnimeTR VIP", "https://azelvid.com/player/index.php?data=ab12", "", None),
    ("AsyaAnim", "https://asyaanim.upns.one/#hde1dt", "", None),
    ("GDrive Player", "https://gdplayer.to/x/?aE9W", "https://gdplayer.to/x/?aE9W", None),
    ("Filemoon", "https://filemoon.sx/e/abc", "", None),
    ("Short", "https://short.icu/CX1stdbQD", "", None),
])
def test_embed_duzelt(saglayici, embed, orijinal, beklenen):
    assert at.embed_duzelt(saglayici, embed, orijinal) == beklenen


# ─────────────────────────────────────────────────────────────────────────────
# HTTP katmanı: engel, CF yedeği, aralık
# ─────────────────────────────────────────────────────────────────────────────
CF_SAYFASI = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
              "<body><script src=\"/cdn-cgi/challenge-platform/h/g/orchestrate/jsch/v1\">"
              "</script></body></html>")


def test_cf_sinamasinda_yedek_denenip_engel_hatasi(site):
    from turkanime_server.crawler.nezaket import ENGELLENME, hata_turu

    site.tablo["/seri/one-piece"] = Yanit(403, CF_SAYFASI)

    with pytest.raises(at.AnimeTRHatasi, match="engelledi") as hata:
        at.get_anime_episodes("one-piece")
    assert site.cf == [f"{TABAN}/seri/one-piece"]
    assert hata.value.status_code == 403
    # Sunucu tarayıcısı kaynağı dinlendirsin, "geçici hata" deyip üstelemesin.
    assert hata_turu(hata.value) == ENGELLENME


def test_cf_yedegi_gecerse_sayfasi_kullaniliyor(site):
    site.tablo["/seriler"] = Yanit(403, CF_SAYFASI)
    site.tablo["/search-series"] = sayfa("search-series-bos.json")
    site.cf_cevap[0] = sayfa("seriler-frieren.html")

    assert len(at.search_animetr("frieren")) == 2
    # FlareSolverr/Qt basamakları `params` almıyor: sorgu adresin içinde.
    assert site.cf == [f"{TABAN}/seriler?title=frieren"]


def test_429_engel_sayiliyor_bakim_503_sayilmiyor(site):
    from turkanime_server.crawler.nezaket import GECICI, hata_turu

    assert at._engellendi_mi(Yanit(429, ""))
    assert at._engellendi_mi(Yanit(503, CF_SAYFASI))
    # Laravel bakım modu da 503 veriyor: engel değil, geçici arıza.
    assert not at._engellendi_mi(Yanit(503, "<title>Service Unavailable</title>"))
    # Normal sayfa CF betiği ("challenge-platform") taşıyabilir: 200 engel değil.
    assert not at._engellendi_mi(Yanit(200, CF_SAYFASI))

    site.tablo["/seri/one-piece"] = Yanit(503, "<title>Service Unavailable</title>")
    with pytest.raises(at.AnimeTRHatasi, match="HTTP 503") as hata:
        at.get_anime_episodes("one-piece")
    assert site.cf == []
    assert hata_turu(hata.value) == GECICI


def test_istekler_arasinda_en_az_bir_saniye(site, monkeypatch):
    site.tablo["/seri/douluo-dalu"] = sayfa("seri-douluo-dalu.html")
    saat, uykular = [50.0], []

    def uyu(sn):
        uykular.append(round(sn, 3))
        saat[0] += sn
    monkeypatch.setattr(at, "time", types.SimpleNamespace(monotonic=lambda: saat[0], sleep=uyu))
    monkeypatch.setattr(at, "_MIN_INTERVAL", 1.0)

    at.get_anime_episodes("douluo-dalu")
    saat[0] += 0.25
    at.get_anime_episodes("douluo-dalu")

    assert uykular == [0.75]


def test_sibnet_denetimi_site_araligini_beklemiyor(site, monkeypatch):
    """Sibnet başka bir konak: AnimeTR'ye nezaket aralığı ona uygulanmıyor."""
    site.tablo["/izle/grand-blue-3-sezon/bolum-12"] = sayfa(
        "izle-grand-blue-3-sezon-bolum-12.html")
    site.tablo[SIBNET_OLU] = sayfa("sibnet-shell-olu.html")
    uykular: List[float] = []
    monkeypatch.setattr(at, "time", types.SimpleNamespace(
        monotonic=lambda: 100.0, sleep=uykular.append))
    monkeypatch.setattr(at, "_MIN_INTERVAL", 1.0)
    monkeypatch.setattr(at, "_son_istek", 0.0)

    at.get_episode_streams("grand-blue-3-sezon/bolum-12")

    assert uykular == []


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt ve uygulama boru hattı
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_animetr_kaynagini_sunuyor():
    kaynak = kayit.bul("animetr")
    assert kaynak is not None
    for ad in ("AnimeTR", "ANIMETR", "animetr"):
        assert kayit.bul(ad) is kaynak
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("AnimeTR", "AnimeTR", "AT", "#d63031", "ANIMETR")
    assert (kaynak.modul, kaynak.cli_kodu) == ("animetr", "animetr")
    assert kaynak.taranabilir and kaynak.oynatilabilir and not kaynak.cerez_gerekir
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    assert kayit.KAYNAKLAR.index(kaynak) > kayit.KAYNAKLAR.index(kayit.bul("Asya Animeleri"))
    uclar = kaynak.uclar()
    assert uclar.ara is at.search_animetr
    assert uclar.bolumler is at.get_anime_episodes
    assert uclar.akislar is at.get_episode_streams
    assert uclar.zengin_ara is at.zengin_ara


def test_bolum_adresi_ve_slugu():
    kaynak = kayit.bul("AnimeTR")
    assert kaynak.bolum_adresi("one-piece/bolum-1162") == f"{TABAN}/izle/one-piece/bolum-1162"
    # Tanınmayan kimlikler aynı adrese çökmesin (adres bölüm nesnesinin kimliği).
    assert kaynak.bolum_adresi("x/y") != kaynak.bolum_adresi("x/z")
    # Başlıktan değil kimlikten: AniList adıyla açılsa da geçmiş anahtarı aynı;
    # "bolum-1" tek başına farklı serilerin dosyalarını çakıştırırdı.
    assert kaynak.bolum_slugu("douluo-dalu/bolum-1-5") == "douluo-dalu-bolum-1-5"


def test_alan_adi_ortam_degiskeniyle_degisiyor():
    """Site DMCA baskısı altında; taşınırsa sürüm beklemeden düzeltilebilsin.
    Kart deseni alan adına bağlı değil: yeni adresteki kartlar da okunuyor."""
    kod = ("import turkanime_api.sources.animetr as a, turkanime_api.sources.kayit as k\n"
           "print(a.BASE_URL); print(k.bul('animetr').bolum_adresi('x/bolum-1'))\n"
           "kart = ('<a href=\"https://animetr.yeni/seri/one-piece\" class=\"anime-card\" '\n"
           "        'data-category=\"anime\" aria-label=\"One Piece\">')\n"
           "print(a.liste_ayristir(kart)[0][0]['slug'])\n")
    ortam = {**os.environ, at.ORTAM_ANAHTARI: "https://animetr.yeni/ ", "PYTHONPATH": str(KOK)}
    r = subprocess.run([sys.executable, "-c", kod], cwd=KOK, env=ortam,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["https://animetr.yeni", "https://animetr.yeni/izle/x/bolum-1",
                                "one-piece"]


def test_kayittan_bolumler_ilk_calisan_ayna_seciliyor(site, monkeypatch):
    """Köprü/CLI yolu: bölüm nesnesi izleme adresini ve kimlikten slug'ı taşıyor;
    `best_video` sıradaki aynaları deniyor ve yt-dlp'ye Referer ZORLANMIYOR
    (Sibnet'in MP4'ü animetr.co Referer'ıyla 403 veriyor)."""
    from turkanime_api.sources import adapter as adapter_mod

    site.tablo["/seri/sousou-no-frieren-2nd-season"] = sayfa(
        "seri-sousou-no-frieren-2nd-season.html")
    site.tablo["/izle/sousou-no-frieren-2nd-season/bolum-1"] = sayfa(
        "izle-sousou-no-frieren-2nd-season-bolum-1.html")
    site.tablo[SIBNET_CANLI] = sayfa("sibnet-shell-canli.html")
    denenen = []

    def bilgi(url, secenekler):
        denenen.append((url, (secenekler.get("http_headers") or {}).get("Referer")))
        # Sibnet bu kez açılmıyor (ör. geçici hata): sıradaki VK seçilmeli.
        return {"url": "https://vkvd.okcdn.ru/v.mp4", "ext": "mp4"} if "vkvideo" in url else {}
    monkeypatch.setattr(adapter_mod, "extract_video_info", bilgi)

    bolumler = adapter_mod.kayittan_bolumler(kayit.bul("AnimeTR"),
                                             "sousou-no-frieren-2nd-season", "Frieren S2")
    bolum = bolumler[0]
    assert (bolum.title, bolum.url, bolum.slug) == (
        "1. Bölüm", f"{TABAN}/izle/sousou-no-frieren-2nd-season/bolum-1",
        "sousou-no-frieren-2nd-season-bolum-1")
    assert bolum.fansubs == ["Asyaanimeleri"]

    video = bolum.best_video()

    assert video is not None and video.player == "VK"
    assert video.url == "https://vkvideo.ru/video883756997_456240487"
    assert denenen == [(SIBNET_CANLI, None), ("https://vkvideo.ru/video883756997_456240487", None)]
    assert video.referer is None


def test_fiksturler_kucuk():
    """Fikstürler kırpılmış kalsın (her biri ≤ 60 KB)."""
    for yol in FIKSTUR.iterdir():
        assert yol.stat().st_size <= 60 * 1024, yol.name


def test_json_fiksturleri_gecerli():
    for yol in FIKSTUR.glob("*.json"):
        assert isinstance(json.loads(yol.read_text("utf-8")), list), yol.name


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (varsayılan olarak atlanır; `pytest --network`)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = at.search_animetr("frieren", limit=10)
    assert ("sousou-no-frieren", "Sousou no Frieren") in sonuc, sonuc

    bolumler = at.get_anime_episodes("sousou-no-frieren")
    assert len(bolumler) >= 28
    assert bolumler[0] == ("sousou-no-frieren/bolum-1", "1. Bölüm")

    akislar = at.get_episode_streams(bolumler[-1][0])
    assert akislar, "son bölümün oynatılabilir aynası kalmamış"
    bilinen = {ad for _, ad in at._KONAKLAR}
    for akis in akislar:
        assert akis["url"].startswith("https://")
        assert akis["player"] in bilinen
        assert "referer" not in akis
