"""Animezer kaynağı (`turkanime_api/sources/animezer.py`).

Hiçbir test ağa çıkmaz: modülün HTTP oturumu (`_yeni_oturum`) ve anizmplayer
`getVideo` POST'u (`anizle._http_post`, FirePlayer istemcisi Anizle'yle
ortak) sahteleniyor. Bu şart — conftest'in ağ mandalı `socket`'i kesiyor ama
curl_cffi kendi libcurl'ünü kullandığı için mandalın yanından geçer.

Fikstürler (`tests/fixtures/animezer/`) sitenin gerçek yanıtları
(2026-09-25 araştırması ve 2026-09-30 canlı kontrolü). JSON'lar olduğu gibi,
yalnızca büyük detay yanıtları kırpıldı: anime detayında `content` birkaç alana
indi, yorum/istatistik atıldı, ilk 3 bölüm aynen, diğer bölümlerde ayrıştırıcının
okuduğu alanlar (numara, ara, etiket, ad) kaldı. `detail-one-piece-kirpik.json`
1179 bölümün yalnızca 1-3, 213-229 ve 1178-1179'unu taşıyor (etiketli ikili
bölümler "215-216", "227-228" o aralıkta). Çizgi dizi detayında bölümlerin içi
boşaltıldı. `proxy-anizmplayer-frieren-s2e10.json` vekilin 2026-09-30 biçimi
(masterUrl + kalite başına sources), `proxy-anizmplayer-naruto-s1e1.json`
2026-09-25 biçimi (tek "Auto").

`--network` ile ayrıca canlı bir duman testi koşar (arama → bölümler → akış).
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from turkanime_api.common.hatalar import KaynakEngellendi, KaynakHatasi, KaynakYanitVermedi
from turkanime_api.sources import animezer as az
from turkanime_api.sources import anizle, kayit

FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "animezer"
CF_SAYFASI = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
              "<body><script src=\"/cdn-cgi/challenge-platform/h/b/orchestrate/"
              "chl_page/v1\"></script></body></html>")


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

    def json(self):
        return json.loads(self.text)


def sayfa(ad: str, kod: int = 200) -> Yanit:
    return Yanit(kod, oku(ad))


Cevap = Any   # Yanit | Exception | Callable[[params], Yanit] | list (sırayla)


class SahteOturum:
    """Yol → cevap tablosu. Tabloda olmayan yol 404 (test onu da görür)."""

    def __init__(self, tablo: Dict[str, Cevap], cagrilar: List[Dict[str, Any]]):
        self.tablo = tablo
        self.cagrilar = cagrilar

    def get(self, url, params=None, headers=None, timeout=None):
        self.cagrilar.append({"url": url, "params": dict(params or {}),
                              "headers": dict(headers or {}), "timeout": timeout})
        yol = url[len(az.BASE_URL):] if url.startswith(az.BASE_URL) else url
        cevap = self.tablo.get(yol)
        if isinstance(cevap, list):
            cevap = cevap.pop(0) if len(cevap) > 1 else cevap[0]
        if callable(cevap) and not isinstance(cevap, Yanit):
            cevap = cevap(dict(params or {}))
        if cevap is None:
            return Yanit(404, '{"success":false,"error":"Not Found"}')
        if isinstance(cevap, BaseException):
            raise cevap
        return cevap


class FirePlayer:
    """`anizle._http_post` sahtesi: anizmplayer `do=getVideo` yanıtları.

    Bilinmeyen kimlik 404 alır ve kayda geçer: POST iş parçacığı havuzunda
    koşuyor, orada yükselen bir AssertionError "çözülemedi" diye yutulurdu.
    """

    def __init__(self):
        self.yanitlar: Dict[str, Cevap] = {}
        self.cagrilar: List[Dict[str, Any]] = []

    def __call__(self, url: str, timeout: int = 60, headers: Optional[Dict[str, str]] = None,
                 data: Optional[Dict] = None) -> Optional[Yanit]:
        self.cagrilar.append({"url": url, "timeout": timeout, "headers": dict(headers or {}),
                              "data": data})
        kimlik = url.split("data=")[1].split("&")[0]
        cevap = self.yanitlar.get(kimlik)
        if cevap is None:
            return Yanit(404, "Not Found")
        return cevap

    def imzali(self, *kimlikler: str) -> None:
        """Her kimliğe kendi imzalı master adresini veren gerçek getVideo yanıtı."""
        sablon = json.loads(oku("anizmplayer-getvideo-naruto-s1e1.json"))
        for kimlik in kimlikler:
            veri = dict(sablon, securedLink=master(kimlik), videoSource=master(kimlik))
            self.yanitlar[kimlik] = Yanit(200, json.dumps(veri))


def master(kimlik: str) -> str:
    return f"https://anizmplayer.com/cdn/hls/{kimlik}/master.m3u8?md5=x&expires=1"


class _AgYasak:
    """Gerçek oturum kurulursa test ağa çıkıyor demektir."""

    class Session:
        def __init__(self, *a, **k):
            raise AssertionError("animezer testi gerçek HTTP oturumu kurmaya çalıştı")


@pytest.fixture
def site(monkeypatch):
    """Modülü sahte siteye bağla; bekleme yok, vekil kipi varsayılan."""
    tablo: Dict[str, Cevap] = {}
    cagrilar: List[Dict[str, Any]] = []
    oynatici = FirePlayer()

    monkeypatch.setattr(az, "_http", _AgYasak)
    monkeypatch.setattr(az, "_yeni_oturum", lambda: SahteOturum(tablo, cagrilar))
    monkeypatch.setattr(az, "_MIN_ARALIK", 0.0)
    monkeypatch.setattr(az, "_oturum", None)
    monkeypatch.setattr(az, "_son_istek", 0.0)
    monkeypatch.setattr(anizle, "_http_post", oynatici)
    monkeypatch.delenv(az.VEKIL_ORTAM_ANAHTARI, raising=False)
    return types.SimpleNamespace(tablo=tablo, cagrilar=cagrilar, oynatici=oynatici)


def _yollar(cagrilar) -> List[str]:
    return [c["url"][len(az.BASE_URL):] for c in cagrilar]


def _vekil_sitesi(site, ad: str = "proxy-anizmplayer-frieren-s2e10.json") -> None:
    """Her gömme için ayrı jetonlu vekil adresi (gerçek yanıtın biçiminde)."""
    sablon = json.loads(oku(ad))

    def cevap(params):
        kimlik = params["url"].rsplit("/", 1)[-1]
        return Yanit(200, json.dumps(dict(sablon, masterUrl=f"/api/proxy/stream?token={kimlik}")))
    site.tablo["/api/proxy/anizmplayer"] = cevap


def vekil(kimlik: str) -> str:
    return f"https://animezer.com/api/proxy/stream?token={kimlik}"


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_video_olmayan_turler_atiliyor_kimlik_tur_onekli(site):
    """Site "naruto" için manga da veriyor; sayısal kimlik türler arasında çakışıyor."""
    site.tablo["/api/search"] = sayfa("search-naruto.json")

    sonuc = az.search_animezer("  Naruto ", limit=20)

    assert sonuc[:3] == [("anime/naruto", "Naruto"),
                         ("anime/naruto-shipp-den", "Naruto Shippūden"),
                         ("anime/boruto-naruto-next-generations",
                          "Boruto: Naruto Next Generations")]
    assert len(sonuc) == 16, "17 sonucun biri manga"
    assert all(k.split("/")[0] in ("anime", "film") for k, _ in sonuc)
    # "Movie" geçen film adına ek yok; geçmeyene "(Film)".
    assert ("film/boruto-naruto-the-movie", "Boruto: Naruto the Movie") in sonuc
    assert ("film/live-spectacle-naruto", "Live Spectacle Naruto (Film)") in sonuc
    istek = site.cagrilar[0]
    assert _yollar(site.cagrilar) == ["/api/search"]
    assert istek["params"] == {"q": "Naruto", "limit": 50}
    assert istek["headers"]["Referer"] == "https://animezer.com/"
    assert istek["timeout"] == az.HTTP_TIMEOUT


def test_arama_donghua_ve_cizgi_dizi_yollari(site):
    site.tablo["/api/search"] = sayfa("search-battle_through_the_heavens.json")
    assert az.search_animezer("battle through the heavens")[0] == \
        ("donghua/fights-break-sphere", "Battle Through the Heavens (Donghua)")

    # Site aynı çizgi diziyi iki kez veriyor; aramada "cizgi_dizi", yolda "cizgi-dizi".
    site.tablo["/api/search"] = sayfa("search-avatar.json")
    assert az.search_animezer("avatar") == [
        ("cizgi-dizi/avatar-the-last-airbender", "Avatar: The Last Airbender (Çizgi Dizi)")]


def test_zengin_arama_kapak_gorselini_tasiyor(site):
    site.tablo["/api/search"] = sayfa("search-frieren.json")
    assert az.search_animezer_zengin("frieren") == [{
        "slug": "anime/frieren-beyond-journey-s-end",
        "title": "Frieren: Beyond Journey's End",
        "image": "https://image.tmdb.org/t/p/w500/dqZENchTd7lp5zht7BdlqM7RBhD.jpg",
    }]


def test_arama_limit_uygulaniyor(site):
    site.tablo["/api/search"] = sayfa("search-naruto.json")
    assert len(az.search_animezer("naruto", limit=3)) == 3


def test_arama_kisa_sorgu_aga_cikmiyor(site):
    """Site 2 karakterden kısa sorguya HTTP 400 veriyor."""
    assert az.search_animezer("a") == []
    assert az.search_animezer("   ") == []
    assert az.search_animezer("naruto", limit=0) == []
    assert site.cagrilar == []


def test_arama_400_ve_sonucsuz_yanit_bos_liste(site):
    site.tablo["/api/search"] = sayfa("search-empty.json", 400)
    assert az.search_animezer("!!") == []
    site.tablo["/api/search"] = sayfa("search-douluo.json")
    assert az.search_animezer("douluo") == []


def test_arama_cloudflare_engeli_sebebiyle_yukseliyor(site):
    from turkanime_server.crawler.nezaket import ENGELLENME, hata_turu

    site.tablo["/api/search"] = Yanit(403, CF_SAYFASI)

    with pytest.raises(az.AnimezerEngellendi, match="geri çevirdi") as hata:
        az.search_animezer("naruto")
    assert len(site.cagrilar) == 1, "engel yeniden denenmez"
    assert hata.value.status_code == 403
    # Arayüz mesajı aynen gösteriyor; tarayıcı kaynağı dinlendiriyor.
    assert isinstance(hata.value, KaynakEngellendi)
    assert hata_turu(hata.value) == ENGELLENME


def test_ag_hatasi_ve_5xx_bir_kez_yeniden_deneniyor(site):
    site.tablo["/api/search"] = [ConnectionError("koptu"), sayfa("search-frieren.json")]
    assert az.search_animezer("frieren")[0][0] == "anime/frieren-beyond-journey-s-end"
    assert len(site.cagrilar) == 2

    site.cagrilar.clear()
    site.tablo["/api/search"] = [Yanit(502, "Bad Gateway"), sayfa("search-frieren.json")]
    assert az.search_animezer("frieren")
    assert len(site.cagrilar) == 2


def test_siteye_ulasilamazsa_yanit_vermedi_hatasi(site):
    site.tablo["/api/search"] = TimeoutError("zaman aşımı")

    with pytest.raises(az.AnimezerYanitVermedi, match="ulaşılamadı") as hata:
        az.search_animezer("frieren")
    assert isinstance(hata.value, KaynakYanitVermedi)
    assert isinstance(hata.value.__cause__, TimeoutError)
    assert len(site.cagrilar) == 2


def test_duz_503_gecici_cloudflare_503_engel():
    assert not az._engel_mi(Yanit(503, "Service Unavailable"))
    assert az._engel_mi(Yanit(503, CF_SAYFASI))
    assert az._engel_mi(Yanit(429, ""))
    # Normal sayfa CF betiği taşıyabilir: 200 engel değil.
    assert not az._engel_mi(Yanit(200, CF_SAYFASI))


def test_istekler_arasinda_en_az_bir_saniye(site, monkeypatch):
    site.tablo["/api/search"] = sayfa("search-frieren.json")
    saat, uykular = [50.0], []

    def uyu(sn):
        uykular.append(round(sn, 3))
        saat[0] += sn
    monkeypatch.setattr(az, "time", types.SimpleNamespace(monotonic=lambda: saat[0], sleep=uyu))
    monkeypatch.setattr(az, "_MIN_ARALIK", 1.0)

    az.search_animezer("frieren")
    saat[0] += 0.25
    az.search_animezer("frieren")

    assert uykular == [0.75]


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_tek_sezonda_sezon_yazilmiyor(site):
    site.tablo["/api/anime-detail/naruto"] = sayfa("detail-naruto.json")

    bolumler = az.get_anime_episodes("anime/naruto")

    assert _yollar(site.cagrilar) == ["/api/anime-detail/naruto"]
    assert len(bolumler) == 220
    assert bolumler[0] == ("anime/naruto/1/1/0", "1. Bölüm")
    assert bolumler[-1] == ("anime/naruto/1/220/0", "220. Bölüm")


def test_bolumler_cok_sezonlu_gercek_bolum_adlariyla(site):
    """Başlıklar ayrıştırıcıdan aynı (sezon, bölüm)la geri çıkmalı: kaynaklar
    arası birleştirme bu anahtarla yapılıyor ve JJK'nin adları rakam taşıyor
    ("Tokyo Colony No. 1 (5)")."""
    from turkanime_api.common.episode_parser import parse_episode

    site.tablo["/api/anime-detail/jujutsu-kaisen"] = sayfa("detail-jujutsu-kaisen.json")

    bolumler = az.get_anime_episodes("anime/jujutsu-kaisen")

    assert len(bolumler) == 58
    assert bolumler[0] == ("anime/jujutsu-kaisen/1/1/0", "1. Sezon 1. Bölüm - Ryomen Sukuna")
    assert bolumler[-1] == ("anime/jujutsu-kaisen/3/11/0",
                            "3. Sezon 11. Bölüm - Tokyo Colony No. 1 (5)")
    for bolum_id, baslik in bolumler:
        _y, _s, sezon, bolum, _a = bolum_id.split("/")
        bilgi = parse_episode(baslik)
        assert (bilgi.season, bilgi.episode) == (int(sezon), int(bolum)), baslik


def test_bolumler_frieren_iki_sezon(site):
    site.tablo["/api/anime-detail/frieren-beyond-journey-s-end"] = sayfa("detail-frieren.json")
    bolumler = az.get_anime_episodes("anime/frieren-beyond-journey-s-end")
    assert len(bolumler) == 38
    assert bolumler[27] == ("anime/frieren-beyond-journey-s-end/1/28/0", "1. Sezon 28. Bölüm")
    assert bolumler[28] == ("anime/frieren-beyond-journey-s-end/2/1/0", "2. Sezon 1. Bölüm")


def test_one_piece_ikili_bolum_etiketi_parantezde(site):
    """Site "215-216"yı tek video olarak 215'in etiketine yazıyor; kimlik 215."""
    from turkanime_api.common.episode_parser import parse_episode

    site.tablo["/api/anime-detail/one-piece"] = sayfa("detail-one-piece-kirpik.json")

    bolumler = dict(az.get_anime_episodes("anime/one-piece"))

    assert bolumler["anime/one-piece/1/215/0"] == "215. Bölüm (215-216)"
    assert bolumler["anime/one-piece/1/216/0"] == "216. Bölüm"
    assert bolumler["anime/one-piece/1/227/0"] == "227. Bölüm (227-228)"
    assert parse_episode("215. Bölüm (215-216)").episode == 215


def test_ara_bolum_ayri_satir_ve_site_yazimiyla_adres():
    """episode_sub > 0: site "5A" diyor ve adresi bolum-5a; 5. bölümü ezmemeli."""
    from turkanime_api.common.episode_parser import parse_episode

    veri = {"success": True, "data": {"seasons": [{"season_number": 1, "episodes": [
        {"season_number": 1, "episode_number": 6, "episode_sub": 0, "title": "Bölüm 6"},
        {"season_number": 1, "episode_number": 5, "episode_sub": 1, "title": "Bölüm 5",
         "episode_label": None},
        {"season_number": 1, "episode_number": 5, "episode_sub": 0, "title": "Bölüm 5"},
    ]}]}}

    bolumler = az.bolumleri_ayristir("anime", "x", veri)

    assert bolumler == [("anime/x/1/5/0", "5. Bölüm"), ("anime/x/1/5/1", "5.1. Bölüm"),
                        ("anime/x/1/6/0", "6. Bölüm")]
    bilgi = parse_episode("5.1. Bölüm")
    assert (bilgi.episode, bilgi.sub) == (5, 1)
    assert az.watch_url("anime/x/1/5/1") == "https://animezer.com/anime/x/sezon-1/bolum-5a"


def test_donghua_seyrek_ve_birden_baslamayan_sezonlar(site):
    site.tablo["/api/donghua/fights-break-sphere"] = sayfa("donghua-fights-break-sphere.json")

    bolumler = az.get_anime_episodes("donghua/fights-break-sphere")

    assert _yollar(site.cagrilar) == ["/api/donghua/fights-break-sphere"]
    assert len(bolumler) == 2 + 24 + 15
    assert bolumler[:2] == [("donghua/fights-break-sphere/3/1/0", "3. Sezon 1. Bölüm"),
                            ("donghua/fights-break-sphere/3/7/0", "3. Sezon 7. Bölüm")]
    assert ("donghua/fights-break-sphere/5/1/0",
            "5. Sezon 1. Bölüm - City of Black Seals") in bolumler
    assert bolumler[-1][0] == "donghua/fights-break-sphere/5/207/0"


def test_cizgi_dizi_semasi(site):
    site.tablo["/api/cizgi-dizi/the-tom-and-jerry-show"] = sayfa(
        "cizgi-dizi-the-tom-and-jerry-show.json")

    bolumler = az.get_anime_episodes("cizgi-dizi/the-tom-and-jerry-show")

    assert bolumler[0] == ("cizgi-dizi/the-tom-and-jerry-show/1/1/0",
                           "1. Sezon 1. Bölüm - Spike Gets Skooled")
    sezonlar = {b[0].split("/")[2] for b in bolumler}
    assert sezonlar == {"1", "2", "3", "4", "5"}


def test_film_tek_bolum_ve_adi(site):
    site.tablo["/api/film/jujutsu-kaisen-0"] = sayfa("film-jujutsu-kaisen-0.json")
    assert az.get_anime_episodes("film/jujutsu-kaisen-0") == [
        ("film/jujutsu-kaisen-0/1/1/0", "Jujutsu Kaisen 0")]


def test_ciplak_slug_anime_sayiliyor(site):
    site.tablo["/api/anime-detail/naruto"] = sayfa("detail-naruto.json")
    assert az.get_anime_episodes("naruto")[0][0] == "anime/naruto/1/1/0"
    # Tireyle başlayan gerçek slug (".hack" → "-hack").
    site.tablo["/api/anime-detail/-hack"] = Yanit(200, '{"success":true,"data":{"seasons":[]}}')
    assert az.get_anime_episodes("anime/-hack") == []


@pytest.mark.parametrize("yol,ad", [("/api/anime-detail/yok", "detail-404.json"),
                                     ("/api/film/yok", "film-404.json")])
def test_bilinmeyen_kimlik_kalici_hata(site, yol, ad):
    from turkanime_server.crawler.nezaket import KALICI, hata_turu

    site.tablo[yol] = sayfa(ad, 404)
    kimlik = yol.replace("/api/anime-detail/", "anime/").replace("/api/film/", "film/")

    with pytest.raises(az.AnimezerHatasi, match="bulunamadı") as hata:
        az.get_anime_episodes(kimlik)
    assert hata.value.status_code == 404
    assert hata_turu(hata.value) == KALICI


@pytest.mark.parametrize("kimlik", ["manga/naruto", "anime/../etc", "..", "anime/a b", ""])
def test_gecersiz_kaynak_kimligi_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(az.AnimezerHatasi, match="kaynak kimliği"):
        az.get_anime_episodes(kimlik)
    assert site.cagrilar == []


def test_detay_json_degil_ya_da_sezonsuzsa_hata(site):
    site.tablo["/api/anime-detail/naruto"] = Yanit(200, "<!DOCTYPE html><html>bakım</html>")
    with pytest.raises(az.AnimezerHatasi, match="JSON değil"):
        az.get_anime_episodes("anime/naruto")

    site.tablo["/api/anime-detail/naruto"] = Yanit(200, '{"success":true,"data":{"content":{}}}')
    with pytest.raises(az.AnimezerHatasi, match="sezon listesi yok"):
        az.get_anime_episodes("anime/naruto")


def test_henuz_bolumu_olmayan_anime_bos_liste(site):
    site.tablo["/api/anime-detail/yeni"] = Yanit(200, '{"success":true,"data":{"seasons":[]}}')
    assert az.get_anime_episodes("anime/yeni") == []


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
NARUTO_ANIZM = ("a7971abb4134fc0cfcec7d589e1ebcf6", "763a42da6c38cd64adb4ffcbebfa4292",
                "df7e148cabfd9b608090fa5ee3348bfe")


def _naruto_sitesi(site) -> None:
    site.tablo["/api/anime/naruto/embeds"] = sayfa("embeds-naruto-s1e1.json")
    site.oynatici.imzali(*NARUTO_ANIZM)
    _vekil_sitesi(site)


def test_akislar_siralama_suzme_ve_etiketler(site):
    """anizm HLS → sibnet → mail.ru → (yedek) vekil → ok.ru; voe ve abyss yok."""
    _naruto_sitesi(site)

    akislar = az.get_episode_streams("anime/naruto/1/1/0")

    embeds = site.cagrilar[0]
    assert _yollar([embeds]) == ["/api/anime/naruto/embeds"]
    assert embeds["params"] == {"season": 1, "episode": 1, "sub": 0, "grouped": "false"}
    assert [(a["player"], a["label"]) for a in akislar] == [
        ("ANIZM", "Bilinmeyen - Anizm (HLS)"),
        ("ANIZM", "Akagami - Anizm (HLS)"),
        ("ANIZM", "Akagami - Anizm (HLS)"),
        ("SIBNET", "Bilinmeyen - Sibnet"),
        ("MAIL", "Akagami - Mail.ru"),
        ("ANIMEZER", "Bilinmeyen - Anizm (Animezer vekili)"),
        ("ANIMEZER", "Akagami - Anizm (Animezer vekili)"),
        ("ODNOKLASSNIKI", "Akagami - OK.ru"),
    ]
    assert [a["url"] for a in akislar[:3]] == [master(k) for k in NARUTO_ANIZM]
    assert akislar[0]["type"] == "hls" and "referer" not in akislar[0]
    assert akislar[3] == {
        "url": "https://video.sibnet.ru/shell.php?videoid=4374024",
        "label": "Bilinmeyen - Sibnet", "type": "iframe", "referer": "https://animezer.com/",
        "fansub": "Bilinmeyen", "player": "SIBNET",
    }
    assert [a["url"] for a in akislar[5:7]] == [vekil(k) for k in NARUTO_ANIZM[:2]]
    assert not any(h in a["url"] for a in akislar for h in ("voe.sx", "abyssplayer"))
    # Etiket çözünürlük taşımıyor: best_video'nun çözünürlük sıralaması
    # (`(\d{3,4})p`) bu sırayı bozmasın.
    assert not any(re.search(r"\d{3,4}p", a["label"]) for a in akislar)


def test_getvideo_istegi_anizle_istemcisiyle_gidiyor(site):
    _naruto_sitesi(site)

    az.get_episode_streams("anime/naruto/1/1/0")

    assert sorted(c["url"] for c in site.oynatici.cagrilar) == sorted(
        f"https://anizmplayer.com/player/index.php?data={k}&do=getVideo" for k in NARUTO_ANIZM)
    ilk = next(c for c in site.oynatici.cagrilar if NARUTO_ANIZM[0] in c["url"])
    # Tarayıcıda POST'u gömme sayfası atıyor: Referer o, Origin oynatıcı.
    assert ilk["headers"] == {"Referer": f"https://anizmplayer.com/video/{NARUTO_ANIZM[0]}",
                              "Origin": "https://anizmplayer.com"}
    vekiller = [c for c in site.cagrilar if c["url"].endswith("/api/proxy/anizmplayer")]
    assert [c["params"] for c in vekiller] == [
        {"url": f"https://anizmplayer.com/video/{k}"} for k in NARUTO_ANIZM[:2]]


def test_vekil_kapaliyken_istenmiyor(site, monkeypatch):
    _naruto_sitesi(site)
    monkeypatch.setenv(az.VEKIL_ORTAM_ANAHTARI, "kapalı")

    akislar = az.get_episode_streams("anime/naruto/1/1/0")

    assert "/api/proxy/anizmplayer" not in _yollar(site.cagrilar)
    assert [a["player"] for a in akislar] == ["ANIZM"] * 3 + ["SIBNET", "MAIL", "ODNOKLASSNIKI"]


def test_vekil_once_kipinde_her_anizm_icin_ve_onde(site, monkeypatch):
    _naruto_sitesi(site)
    monkeypatch.setenv(az.VEKIL_ORTAM_ANAHTARI, "ÖNCE")

    akislar = az.get_episode_streams("anime/naruto/1/1/0")

    assert [a["url"] for a in akislar[:3]] == [vekil(k) for k in NARUTO_ANIZM]
    assert [a["player"] for a in akislar[3:6]] == ["ANIZM"] * 3


def test_dogrudan_cozulemeyen_gomme_vekille_tutuluyor(site):
    """Yedek kipte vekil ilk 2 gömme İLE doğrudan yolu çözülemeyenler için."""
    _naruto_sitesi(site)
    site.oynatici.yanitlar[NARUTO_ANIZM[2]] = Yanit(500, "oops")

    akislar = az.get_episode_streams("anime/naruto/1/1/0")

    anizm = [a["url"] for a in akislar if a["player"] == "ANIZM"]
    vekiller = [a["url"] for a in akislar if a["player"] == "ANIMEZER"]
    assert anizm == [master(k) for k in NARUTO_ANIZM[:2]]
    assert vekiller == [vekil(k) for k in NARUTO_ANIZM]
    assert next(a for a in akislar if a["url"] == vekil(NARUTO_ANIZM[2]))["fansub"] == "Akagami"


def test_hicbir_anizm_cozulemezse_sebepli_hata(site):
    """Bölümün tek kaydı anizm ve iki yol da düştü: "video yok" değil, geçici hata."""
    from turkanime_server.crawler.nezaket import GECICI, hata_turu

    site.tablo["/api/film/jujutsu-kaisen-0/embeds"] = sayfa("embeds-jjk0-film.json")
    site.tablo["/api/proxy/anizmplayer"] = Yanit(502, "upstream request failed")

    with pytest.raises(az.AnimezerYanitVermedi, match="çözülemedi") as hata:
        az.get_episode_streams("film/jujutsu-kaisen-0/1/1/0")
    assert isinstance(hata.value, KaynakHatasi)
    assert hata_turu(hata.value) == GECICI


def test_film_akislari_ve_vekil_eski_bicimi(site):
    """Vekilin 2026-09-25 biçimi (tek "Auto") da okunuyor; film sezonu yok sayıyor."""
    site.tablo["/api/film/jujutsu-kaisen-0/embeds"] = sayfa("embeds-jjk0-film.json")
    site.oynatici.imzali("88223ade11c861267dccbc28768b5003")
    site.tablo["/api/proxy/anizmplayer"] = sayfa("proxy-anizmplayer-naruto-s1e1.json")

    akislar = az.get_episode_streams("film/jujutsu-kaisen-0/1/1/0")

    assert site.cagrilar[0]["params"]["season"] == 1
    assert [(a["player"], a["fansub"]) for a in akislar] == [("ANIZM", "AnimeWho"),
                                                             ("ANIMEZER", "AnimeWho")]
    assert akislar[1]["url"] == (
        "https://animezer.com" + json.loads(oku("proxy-anizmplayer-naruto-s1e1.json"))["masterUrl"])


def test_vekil_master_yoksa_stream_adresi(site):
    """masterUrl yoksa streamUrl; ikisi de yoksa hata (aday uydurulmaz)."""
    veri = json.loads(oku("proxy-anizmplayer-frieren-s2e10.json"))
    veri.pop("masterUrl")
    site.tablo["/api/proxy/anizmplayer"] = Yanit(200, json.dumps(veri))
    assert az._vekil_coz("https://anizmplayer.com/video/x") == \
        "https://animezer.com" + veri["streamUrl"]

    veri.pop("streamUrl")
    site.tablo["/api/proxy/anizmplayer"] = Yanit(200, json.dumps(veri))
    with pytest.raises(az.AnimezerHatasi, match="adres vermedi"):
        az._vekil_coz("https://anizmplayer.com/video/x")


def test_jjk_kalabalik_bolum_barindirici_sirasi(site):
    kimlikler = [json.loads(oku("embeds-jjk-s1e1.json"))["embeds"][i]["url"].rsplit("/", 1)[-1]
                 for i in (0, 1, 3, 4, 5, 6, 7)]
    site.tablo["/api/anime/jujutsu-kaisen/embeds"] = sayfa("embeds-jjk-s1e1.json")
    site.oynatici.imzali(*kimlikler)
    _vekil_sitesi(site)

    akislar = az.get_episode_streams("anime/jujutsu-kaisen/1/1/0")

    assert [a["player"] for a in akislar] == (
        ["ANIZM"] * 7 + ["SIBNET", "MAIL", "ANIMEZER", "ANIMEZER", "DAILYMOTION", "DAILYMOTION"])
    # mail.ru'nun kullanıcı videosu biçimi de tanınıyor.
    assert akislar[8]["url"].startswith("https://my.mail.ru/mail/alperdemiroglu/video/embed/")
    assert [a["fansub"] for a in akislar[9:11]] == ["PuzzleSubs", "Adonis"]


def test_yalnizca_acilamayan_barindirici_varsa_bos_liste(site):
    """BTTH 5x202'nin tek kaydı vk (yt-dlp çözemiyor): "video yok", hata değil."""
    site.tablo["/api/donghua/fights-break-sphere/embeds"] = sayfa("embeds-btth-s5e202.json")
    assert az.get_episode_streams("donghua/fights-break-sphere/5/202/0") == []
    assert site.oynatici.cagrilar == []


def test_donghua_sibnet_akisi(site):
    site.tablo["/api/donghua/fights-break-sphere/embeds"] = sayfa("embeds-btth-s5e207.json")

    akislar = az.get_episode_streams("donghua/fights-break-sphere/5/207/0")

    assert site.cagrilar[0]["params"] == {"season": 5, "episode": 207, "sub": 0,
                                          "grouped": "false"}
    assert akislar == [{"url": "https://video.sibnet.ru/shell.php?videoid=6240144",
                        "label": "AsyaAnime - Sibnet", "type": "iframe",
                        "referer": "https://animezer.com/", "fansub": "AsyaAnime",
                        "player": "SIBNET"}]


def test_cizgi_dizi_akisi(site):
    site.tablo["/api/cizgi-dizi/the-tom-and-jerry-show/embeds"] = sayfa(
        "embeds-tom-and-jerry-s1e1.json")
    akislar = az.get_episode_streams("cizgi-dizi/the-tom-and-jerry-show/1/1/0")
    assert [(a["player"], a["fansub"]) for a in akislar] == [("SIBNET", "Altyazı")]


def test_bolumun_kaydi_yoksa_bos_liste(site):
    site.tablo["/api/anime/naruto/embeds"] = sayfa("embeds-empty.json")
    assert az.get_episode_streams("anime/naruto/1/9999/0") == []


@pytest.mark.parametrize("ad,beklenen", [
    ("embeds-onepiece-s1e1179.json", ["ANIZM", "VIDMOLY", "ANIMEZER", "ODNOKLASSNIKI"]),
    ("embeds-onepiece-s1e1.json", ["ANIZM", "ANIZM", "SIBNET", "SIBNET", "ANIMEZER",
                                   "ANIMEZER"]),
])
def test_dolgu_fansub_adlari_bilinmeyen_oluyor(site, ad, beklenen):
    """Site grup adı yerine "-", "Güncellenecek", "Bilinmiyor" yazabiliyor."""
    gommeler = json.loads(oku(ad))["embeds"]
    site.tablo["/api/anime/one-piece/embeds"] = sayfa(ad)
    site.oynatici.imzali(*[g["url"].rsplit("/", 1)[-1] for g in gommeler
                           if "anizmplayer" in g["url"]])
    _vekil_sitesi(site)

    akislar = az.get_episode_streams("anime/one-piece/1/1/0")

    assert [a["player"] for a in akislar] == beklenen
    ham = {g["fansub_name"] for g in gommeler}
    assert ham & {"-", "Güncellenecek"}
    assert {a["fansub"] for a in akislar} <= {"Bilinmeyen", "VictoriaSubs"}


def test_dublaj_etiketi_fansub_adi_sade():
    akislar = az.akislari_kur([
        {"url": "https://video.sibnet.ru/shell.php?videoid=1", "fansub_name": "HolySubs",
         "is_dubbed": True},
        {"url": "//ok.ru/videoembed/2", "fansub_name": "HolySubs", "is_dubbed": False},
        {"url": "r2://kova/dosya.mp4", "fansub_name": "HolySubs"},
        {"url": "https://video.sibnet.ru/shell.php?videoid=1", "fansub_name": "Kopya"},
    ])
    assert [(a["label"], a["fansub"], a["url"]) for a in akislar] == [
        ("HolySubs - Sibnet (Dublaj)", "HolySubs", "https://video.sibnet.ru/shell.php?videoid=1"),
        ("HolySubs - OK.ru", "HolySubs", "https://ok.ru/videoembed/2"),
    ]


def test_gomme_listesi_yoksa_hata(site):
    site.tablo["/api/anime/naruto/embeds"] = Yanit(200, '{"success":false,"error":"Sunucu hatası"}')
    with pytest.raises(az.AnimezerHatasi, match="Sunucu hatası"):
        az.get_episode_streams("anime/naruto/1/1/0")


@pytest.mark.parametrize("kimlik", ["anime/naruto/1", "anime/naruto", "17/b1",
                                    "anime/naruto/x/1/0", "manga/naruto/1/1/0"])
def test_gecersiz_bolum_kimligi_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(az.AnimezerHatasi, match="bölüm kimliği"):
        az.get_episode_streams(kimlik)
    assert site.cagrilar == []


def test_izleme_adresi():
    assert az.watch_url("anime/jujutsu-kaisen/3/11/0") == \
        "https://animezer.com/anime/jujutsu-kaisen/sezon-3/bolum-11"
    assert az.watch_url("anime/jujutsu-kaisen/3/11") == \
        "https://animezer.com/anime/jujutsu-kaisen/sezon-3/bolum-11"
    assert az.watch_url("donghua/fights-break-sphere/5/207/0") == \
        "https://animezer.com/donghua/fights-break-sphere/sezon-5/bolum-207"
    assert az.watch_url("cizgi-dizi/the-simpsons/2/3/0") == \
        "https://animezer.com/cizgi-dizi/the-simpsons/sezon-2/bolum-3"
    assert az.watch_url("film/jujutsu-kaisen-0/1/1/0") == \
        "https://animezer.com/film/jujutsu-kaisen-0/izle"
    # Tanınmayan kimlikler aynı adrese çökmesin (adres bölüm nesnesinin kimliği).
    assert az.watch_url("17/b1") != az.watch_url("17/b2")


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt ve uygulama boru hattı
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_animezer_kaynagini_sunuyor():
    kaynak = kayit.bul("animezer")
    assert kaynak is not None and kaynak is kayit.bul("ANIMEZER")
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("Animezer", "Animezer", "AM", "#e67e22", "ANIMEZER")
    assert kaynak.modul == "animezer" and kaynak.cli_kodu == "animezer"
    assert kaynak.taranabilir and kaynak.oynatilabilir and kaynak.deneysel
    assert kaynak.cli_etiketi == "Animezer (deneysel)"
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    uclar = kaynak.uclar()
    assert uclar.ara is az.search_animezer
    assert uclar.zengin_ara is az.search_animezer_zengin
    assert uclar.bolumler is az.get_anime_episodes
    assert uclar.akislar is az.get_episode_streams
    assert kaynak.bolum_adresi("anime/naruto/1/5/0") == \
        "https://animezer.com/anime/naruto/sezon-1/bolum-5"
    kisaltmalar = [k.kisaltma for k in kayit.KAYNAKLAR]
    assert kisaltmalar.count("AM") == 1


def test_kayittan_bolumler_dogrudan_hls_403se_yedege_geciyor(site, monkeypatch):
    """Köprü/CLI yolu: imzalı master oynatıcının bağlantısında 403 verirse
    (IP'ye bağlı imza) `best_video` sıradaki adaya geçiyor; seçilen fansub'un
    doğrudan adresi düşerse aynı fansub'un vekil kopyası oynuyor."""
    from turkanime_api.sources import adapter as adapter_mod

    site.tablo["/api/anime-detail/frieren-beyond-journey-s-end"] = sayfa("detail-frieren.json")
    site.tablo["/api/anime/frieren-beyond-journey-s-end/embeds"] = sayfa(
        "embeds-frieren-s2e10.json")
    gommeler = json.loads(oku("embeds-frieren-s2e10.json"))["embeds"]
    site.oynatici.imzali(*[g["url"].rsplit("/", 1)[-1] for g in gommeler
                           if "anizmplayer" in g["url"]])
    _vekil_sitesi(site)
    denenen = []

    def bilgi(url, _secenekler):
        denenen.append(url)
        if url.startswith("https://anizmplayer.com/cdn/"):
            return None                     # yt-dlp: HTTP Error 403
        return {"url": url, "ext": "mp4"}
    monkeypatch.setattr(adapter_mod, "extract_video_info", bilgi)

    bolumler = adapter_mod.kayittan_bolumler(kayit.bul("Animezer"),
                                             "anime/frieren-beyond-journey-s-end", "Frieren")
    bolum = bolumler[-1]
    assert bolum.title == "2. Sezon 10. Bölüm"
    assert bolum.url == ("https://animezer.com/anime/frieren-beyond-journey-s-end/"
                         "sezon-2/bolum-10")
    assert bolum.fansubs == ["GachaFlex Fansub", "Anizm Çeviri Ekibi", "Yuki"]

    video = bolum.best_video(by_res=False)
    assert video is not None and video.player == "SIBNET"
    assert video.url == "https://video.sibnet.ru/shell.php?videoid=6177666"
    assert len(denenen) == 4, "3 doğrudan HLS denendi, 4. aday sibnet"

    video = bolum.best_video(by_res=False, by_fansub="GachaFlex Fansub")
    assert video is not None and video.player == "ANIMEZER"
    assert video.url.startswith("https://animezer.com/api/proxy/stream?token=")


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (--network)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = az.search_animezer("frieren", limit=5)
    assert ("anime/frieren-beyond-journey-s-end", "Frieren: Beyond Journey's End") in sonuc
    bolumler = az.get_anime_episodes("anime/frieren-beyond-journey-s-end")
    assert len(bolumler) >= 38
    akislar = az.get_episode_streams(bolumler[0][0])
    assert akislar, "Frieren 1. bölümün oynatılabilir akışı yok"
    vekiller = [a for a in akislar if a["player"] == "ANIMEZER"]
    if vekiller:
        yanit = az._oturum_al().get(vekiller[0]["url"], timeout=30)
        assert yanit.status_code == 200 and yanit.text.startswith("#EXTM3U")
