"""AniMOM kaynağı (`turkanime_api/sources/animom.py`).

Hiçbir test ağa çıkmaz: modülün HTTP oturumları sahte oturumlarla
değiştiriliyor. Bu şart — conftest'in ağ mandalı `socket`'i kesiyor ama
curl_cffi kendi libcurl'ünü kullandığı için mandalın yanından geçer.

Fikstürler (`tests/fixtures/animom/`) 2026-09-30'da siteden alınan gerçek
yanıtlar. HTML sayfaları kırpıldı: `<title>`, başlık bloğu, bölüm listesi,
video sekmeleri ve bölüm şeridi (`episodes-slide`) aynen duruyor; yorumlar,
kenar çubuğu ve betikler çıkarıldı. Arama yanıtındaki üye profillerinin
adları anonimleştirildi ("uye1"…). Film listesinin 1. sayfasında 20 kartın
4'ü tutuldu. JSON ve m3u8 yanıtları olduğu gibi.

`--network` ile ayrıca canlı bir duman testi koşar (arama → bölümler → akış →
varyant listesinin ilk parçası).
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from turkanime_api.sources import animom as am
from turkanime_api.sources import kayit

FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "animom"
HD = "https://hdplayersystem.com"
MASTER = f"{HD}/cdn/hls/b38cc07127da35c2274f8c50322a3106/master.m3u8"
MASTER_AYRI_SES = f"{HD}/cdn/hls/b3cf32565c535121e6d3e19b2e9f9bac/master.m3u8"


def oku(ad: str) -> str:
    return (FIKSTUR / ad).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sahte site
# ─────────────────────────────────────────────────────────────────────────────
class Yanit:
    """curl_cffi yanıtının kullandığımız kadarı."""

    def __init__(self, status_code: int = 200, text: str = "", url: str = "",
                 headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


def sayfa(ad: str) -> Yanit:
    return Yanit(200, oku(ad))


Cevap = Any   # Yanit | Exception | Callable[[kw], Yanit] | list (sırayla)


class SahteSite:
    """(yöntem, yol) → cevap tablosu; bütün oturumlar aynı siteyi görüyor.

    Yol: animom.org için "/anime/…", başka konaklar için sorgusuz tam adres.
    """

    def __init__(self):
        self.tablo: Dict[Any, Cevap] = {}
        self.cagrilar: List[Dict[str, Any]] = []
        self.oturum_sayisi = 0

    def cevapla(self, yontem: str, url: str, oturum: int, kw: Dict[str, Any]) -> Yanit:
        yol = url.split("?", 1)[0]
        if yol.startswith(am.BASE_URL):
            yol = yol[len(am.BASE_URL):]
        self.cagrilar.append({"yontem": yontem, "url": url, "yol": yol, "oturum": oturum,
                              "data": kw.get("data"), "headers": dict(kw.get("headers") or {}),
                              "timeout": kw.get("timeout"),
                              "allow_redirects": kw.get("allow_redirects")})
        cevap = self.tablo.get((yontem, yol))
        if isinstance(cevap, list):
            cevap = cevap.pop(0) if len(cevap) > 1 else cevap[0]
        if callable(cevap) and not isinstance(cevap, Yanit):
            cevap = cevap(kw)
        if cevap is None:
            return Yanit(404, "Not Found", url)
        if isinstance(cevap, BaseException):
            raise cevap
        return cevap

    def yollar(self, yontem: Optional[str] = None) -> List[str]:
        return [c["yol"] for c in self.cagrilar if yontem in (None, c["yontem"])]


class SahteOturum:
    def __init__(self, site: SahteSite, no: int):
        self.site, self.no = site, no

    def get(self, url, **kw):
        return self.site.cevapla("GET", url, self.no, kw)

    def post(self, url, **kw):
        return self.site.cevapla("POST", url, self.no, kw)


class _AgYasak:
    """Gerçek oturum kurulursa test ağa çıkıyor demektir."""

    class Session:
        def __init__(self, *a, **k):
            raise AssertionError("animom testi gerçek HTTP oturumu kurmaya çalıştı")


@pytest.fixture
def site(monkeypatch):
    """Modülü sahte siteye bağla; bekleme yok, önbellekler temiz."""
    s = SahteSite()

    def yeni_oturum():
        s.oturum_sayisi += 1
        return SahteOturum(s, s.oturum_sayisi)

    monkeypatch.setattr(am, "_http", _AgYasak)
    monkeypatch.setattr(am, "_yeni_oturum", yeni_oturum)
    monkeypatch.setattr(am, "_MIN_INTERVAL", 0.0)
    am.onbellegi_sifirla()
    yield s
    am.onbellegi_sifirla()


def _filmler(site: SahteSite) -> None:
    site.tablo[("GET", "/anime-filmleri")] = sayfa("anime-filmleri-1.html")
    site.tablo[("GET", "/anime-filmleri/2")] = sayfa("anime-filmleri-2.html")


def _grup_tablosu(**hash_bas_dosya: str):
    """POST /get/video/group: gövdedeki hash'in başına göre fikstür; bilinmeyen boş."""
    def cevap(kw):
        kimlik = (kw.get("data") or {}).get("hash", "")
        for bas, dosya in hash_bas_dosya.items():
            if kimlik.startswith(bas):
                return sayfa(dosya)
        return Yanit(200, '{"success":true,"videos":[]}')
    return cevap


def _fireplayer_her_videoya(site: SahteSite, *kimlikler: str) -> None:
    """Her FirePlayer kimliğine ayrı master ve ayrı varyant (gerçekte de öyle)."""
    veri = json.loads(oku("hd-getvideo.json"))

    def getvideo(kw):
        kimlik = (kw.get("data") or {}).get("hash", "")
        return Yanit(200, json.dumps({**veri, "securedLink":
                                      f"{HD}/cdn/hls/{kimlik}/master.m3u8?md5=x&expires=1"}))
    site.tablo[("POST", f"{HD}/player/index.php")] = getvideo
    for kimlik in kimlikler:
        site.tablo[("GET", f"{HD}/cdn/hls/{kimlik}/master.m3u8")] = Yanit(
            200, oku("hd-master.m3u8").replace("/hls/", f"/hls/{kimlik}-", 1))


def _fireplayer(site: SahteSite, getvideo: str = "hd-getvideo.json",
                master: Cevap = None, master_adresi: str = MASTER) -> None:
    site.tablo[("POST", f"{HD}/player/index.php")] = sayfa(getvideo)
    site.tablo[("GET", master_adresi)] = master if master is not None else sayfa("hd-master.m3u8")


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_xhr_post_ve_uye_profilleri_alinmiyor(site):
    site.tablo[("POST", "/search")] = sayfa("search-naruto.json")
    _filmler(site)

    assert am.search_animom("  naruto ") == [
        ("boruto-naruto-next-generations-izle-hd", "Boruto: Naruto Next Generations")]
    istek = site.cagrilar[0]
    assert (istek["yontem"], istek["yol"], istek["data"]) == ("POST", "/search", {"query": "naruto"})
    # X-Requested-With olmadan uç JSON yerine 403 HTML veriyor (ölçüldü).
    assert istek["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert istek["timeout"] == am.HTTP_TIMEOUT


def test_arama_tam_eslesme_once_ve_zengin_sonuc_gorselli(site):
    site.tablo[("POST", "/search")] = sayfa("search-one_piece.json")
    _filmler(site)

    zengin = am.zengin_ara("one piece", limit=2)

    assert [k["slug"] for k in zengin] == ["one-piece-izle-hd-izle-hd11", "one-piece-fan-letter"]
    assert zengin[0] == {
        "slug": "one-piece-izle-hd-izle-hd11", "title": "One Piece",
        "image": "https://animom.org/uploads/animes/original/one-piece-5570.webp"}


def test_filmler_aramada_yok_yerel_film_listesinden_bulunuyor(site):
    """Sitenin araması yalnızca dizileri veriyor; "chainsaw" → "bulunamadı"."""
    site.tablo[("POST", "/search")] = sayfa("search-empty.json")
    _filmler(site)

    assert am.search_animom("Chainsaw") == [
        ("chainsaw-man-reze-hen-izle-hd", "Chainsaw Man: Reze-hen")]
    # Film listesi sayfalı: rel="next" izleniyor, 2. sayfa son.
    assert site.yollar("GET") == ["/anime-filmleri", "/anime-filmleri/2"]
    # İkinci sayfadaki film de bulunuyor (Türkçe karakterli başlık).
    assert am.search_animom("yüreğinin")[0][0] == "yureginin-sesi-izle"


def test_film_listesi_onbellekte(site):
    site.tablo[("POST", "/search")] = sayfa("search-frieren.json")
    _filmler(site)

    am.search_animom("frieren")
    am.search_animom("frieren")

    assert site.yollar("GET").count("/anime-filmleri") == 1
    assert site.yollar("POST").count("/search") == 2


def test_film_listesi_alinamazsa_arama_dizilerle_suruyor(site):
    site.tablo[("POST", "/search")] = sayfa("search-frieren.json")
    site.tablo[("GET", "/anime-filmleri")] = Yanit(500, "Internal Server Error")

    assert am.search_animom("frieren") == [
        ("sousou-no-frieren-izle", "Sousou no Frieren"),
        ("sousou-no-frieren-2-sezon", "Sousou no Frieren 2.Sezon")]


def test_kisa_sorgu_aga_cikmiyor(site):
    """Tek harfe site bütün katalogun başını döndürüyor: anlamsız sonuç."""
    assert am.search_animom("a") == []
    assert am.search_animom("   ") == []
    assert am.search_animom("frieren", limit=0) == []
    assert site.cagrilar == []


def test_arama_engelde_sebep_yukseliyor(site):
    from turkanime_server.crawler.nezaket import ENGELLENME, hata_turu

    site.tablo[("POST", "/search")] = Yanit(
        403, "<html><title>Just a moment...</title></html>")

    with pytest.raises(am.AnimomHatasi, match="engelledi") as hata:
        am.search_animom("frieren")
    assert hata.value.status_code == 403
    assert hata_turu(hata.value) == ENGELLENME
    assert len(site.yollar("POST")) == 1, "engel yeniden denenmemeli"


def test_arama_json_yerine_html_gelirse_hata(site):
    site.tablo[("POST", "/search")] = Yanit(200, "<!DOCTYPE html><html>bakım</html>")
    with pytest.raises(am.AnimomHatasi, match="JSON değil"):
        am.search_animom("frieren")


def test_arama_ag_hatasi_bir_kez_yeniden_deneniyor(site):
    _filmler(site)
    site.tablo[("POST", "/search")] = [ConnectionError("koptu"), sayfa("search-frieren.json")]
    assert am.search_animom("frieren")[0][0] == "sousou-no-frieren-izle"

    site.tablo[("POST", "/search")] = ConnectionError("ağ yok")
    with pytest.raises(am.AnimomHatasi, match="ulaşılamadı"):
        am.search_animom("frieren")


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_tam_liste_bolum_sayfasinin_seridinden(site):
    """Anime sayfası 20 bölüm gösteriyor; bölüm sayfasının şeridi 28'in hepsini."""
    site.tablo[("GET", "/anime/sousou-no-frieren-izle")] = sayfa("anime-sousou-no-frieren-izle.html")
    site.tablo[("GET", "/anime/sousou-no-frieren-1-bolum")] = sayfa("ep-sousou-no-frieren-1-bolum.html")

    bolumler = am.get_anime_episodes("sousou-no-frieren-izle")

    assert site.yollar() == ["/anime/sousou-no-frieren-izle", "/anime/sousou-no-frieren-1-bolum"]
    assert len(bolumler) == 28
    assert bolumler[0] == ("sousou-no-frieren-1-bolum", "1. Bölüm")
    assert bolumler[1] == ("sousou-no-frieren-2-bolum-izle", "2. Bölüm")   # site eki kimlikte kalır
    assert bolumler[-1] == ("sousou-no-frieren-28-bolum", "28. Bölüm")


def test_bolumler_cok_sezonlu_dizi(site):
    site.tablo[("GET", "/anime/blue-lock-2-sezon")] = sayfa("anime-blue-lock-2-sezon.html")
    site.tablo[("GET", "/anime/blue-lock-2-sezon/sezon-1/bolum-1")] = sayfa(
        "ep-blue-lock-2-sezon-sezon-1-bolum-1.html")

    bolumler = am.get_anime_episodes("https://animom.org/anime/blue-lock-2-sezon")

    assert len(bolumler) == 48
    assert bolumler[0] == ("blue-lock-2-sezon/sezon-1/bolum-1", "1. Sezon 1. Bölüm")
    assert bolumler[24] == ("blue-lock-2-sezon/sezon-2/bolum-1", "2. Sezon 1. Bölüm")
    assert bolumler[-1] == ("blue-lock-2-sezon/sezon-2/bolum-24", "2. Sezon 24. Bölüm")


def test_film_tek_bolum_kimligi_anime_slugu(site):
    site.tablo[("GET", "/anime/chainsaw-man-reze-hen-izle-hd")] = sayfa(
        "anime-chainsaw-man-reze-hen-izle-hd.html")

    assert am.get_anime_episodes("chainsaw-man-reze-hen-izle-hd") == [
        ("chainsaw-man-reze-hen-izle-hd", "Chainsaw Man: Reze-hen (2025)")]
    assert len(site.cagrilar) == 1


def test_serit_okunamazsa_daha_fazla_goster_yedegi(site):
    """Bölüm sayfası şeritsiz gelirse sitenin kendi "Daha fazla" ucu (20'şer)."""
    site.tablo[("GET", "/anime/sousou-no-frieren-izle")] = sayfa("anime-sousou-no-frieren-izle.html")
    site.tablo[("GET", "/anime/sousou-no-frieren-1-bolum")] = Yanit(200, "<html>tasarım değişti</html>")
    site.tablo[("POST", "/episode/item/load")] = [sayfa("itemload-frieren-p2.json"),
                                                  sayfa("itemload-frieren-p3.json")]

    bolumler = am.get_anime_episodes("sousou-no-frieren-izle")

    assert len(bolumler) == 28 and bolumler[-1] == ("sousou-no-frieren-28-bolum", "28. Bölüm")
    yuklemeler = [c["data"] for c in site.cagrilar if c["yol"] == "/episode/item/load"]
    # 2. sayfa 8 yeni bölüm getirdi, `last` 0; 3. sayfa boş ve `last` 1 → dur.
    assert yuklemeler == [{"page": 2, "type": 1, "id": "386"}, {"page": 3, "type": 1, "id": "386"}]


def test_bolumler_bilinmeyen_anime_404(site):
    site.tablo[("GET", "/anime/boyle-bir-anime-yok")] = Yanit(404, oku("anime-404.html"))

    with pytest.raises(am.AnimomHatasi, match="yok") as hata:
        am.get_anime_episodes("boyle-bir-anime-yok")
    assert hata.value.status_code == 404


def test_bolumler_sayfa_yapisi_degismisse_hata(site):
    site.tablo[("GET", "/anime/sousou-no-frieren-izle")] = Yanit(200, "<html><title>AniMOM</title></html>")
    with pytest.raises(am.AnimomHatasi, match="bölüm listesi bulunamadı"):
        am.get_anime_episodes("sousou-no-frieren-izle")


@pytest.mark.parametrize("kimlik", ["", "../etc", "Naruto Shippuden", "Frieren/1"])
def test_bolumler_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(am.AnimomHatasi, match="Geçersiz"):
        am.get_anime_episodes(kimlik)
    assert site.cagrilar == []


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _frieren_bolumu(site: SahteSite) -> None:
    site.tablo[("GET", "/anime/sousou-no-frieren-1-bolum")] = sayfa("ep-sousou-no-frieren-1-bolum.html")
    site.tablo[("POST", "/get/video/group")] = sayfa("group-sousou-no-frieren-1.json")


def test_akislar_animom_yuklemesi_varyant_olarak(site):
    _frieren_bolumu(site)
    _fireplayer(site)

    akislar = am.get_episode_streams("sousou-no-frieren-1-bolum")

    varyant = HD + next(s for s in oku("hd-master.m3u8").splitlines() if s.startswith("/hls/"))
    assert akislar == [{
        "url": varyant,
        "label": "720p Türkçe Altyazı AniMOM",
        "type": "hls",
        "fansub": "Türkçe Altyazı",
        "player": "ANIMOM",
        "referer": "https://hdplayersystem.com/",
    }]
    grup = next(c for c in site.cagrilar if c["yol"] == "/get/video/group")
    assert grup["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert grup["headers"]["Referer"] == "https://animom.org/anime/sousou-no-frieren-1-bolum"
    getvideo = next(c for c in site.cagrilar if c["yol"] == f"{HD}/player/index.php")
    assert getvideo["url"] == (f"{HD}/player/index.php?data=eb6de71b465f16507cadfb2347a9d98f"
                               "&do=getVideo")
    assert getvideo["data"] == {"hash": "eb6de71b465f16507cadfb2347a9d98f",
                                "r": "https://animom.org/"}
    master = next(c for c in site.cagrilar if c["yol"] == MASTER)
    # İmza getVideo'yu çağıran istemciye bağlı: master AYNI oturumdan okunmalı.
    assert master["oturum"] == getvideo["oturum"]
    assert master["url"].endswith("md5=at4QZ2Lj69rmYYkADlW7Og&expires=1790755000")


def test_akislar_ses_ayri_izdeyse_master_donuyor(site):
    """Varyant tek başına sessiz oynardı (TYPE=AUDIO): master verilmeli."""
    _frieren_bolumu(site)
    _fireplayer(site, "hd-getvideo-ayri-ses.json", sayfa("hd-master-ayri-ses.m3u8"),
                MASTER_AYRI_SES)

    akislar = am.get_episode_streams("sousou-no-frieren-1-bolum")

    assert [(a["url"].split("?")[0], a["label"], a["type"]) for a in akislar] == [
        (MASTER_AYRI_SES, "Türkçe Altyazı AniMOM HLS", "hls")]
    assert "md5=46v8sCowKWogm1Dk1ZcbuA" in akislar[0]["url"]


def test_akislar_master_403_ise_taze_getvideo(site):
    _frieren_bolumu(site)
    _fireplayer(site, master=[Yanit(403, "<html>403 Forbidden</html>"), sayfa("hd-master.m3u8")])

    akislar = am.get_episode_streams("sousou-no-frieren-1-bolum")

    assert akislar[0]["label"] == "720p Türkçe Altyazı AniMOM"
    assert site.yollar("POST").count(f"{HD}/player/index.php") == 2
    oturumlar = [c["oturum"] for c in site.cagrilar if c["yol"] in (f"{HD}/player/index.php", MASTER)]
    assert oturumlar[0] == oturumlar[1] != oturumlar[2] == oturumlar[3]


def test_akislar_master_hic_okunamazsa_imzali_adres(site):
    """Ev bağlantısında (sabit IP) oynatıcı imzalı adresi açabilir; boş dönmesin."""
    _frieren_bolumu(site)
    _fireplayer(site, master=Yanit(403, "<html>403 Forbidden</html>"))

    akislar = am.get_episode_streams("sousou-no-frieren-1-bolum")

    assert len(akislar) == 1 and akislar[0]["url"].startswith(MASTER + "?md5=")
    assert site.yollar("POST").count(f"{HD}/player/index.php") == am._FIREPLAYER_DENEME


def test_akislar_altyazi_dublajdan_once(site):
    """Filmde site "Türkçe Dublaj" sekmesini önce veriyor; uygulamanın amacı altyazı."""
    site.tablo[("GET", "/anime/chainsaw-man-reze-hen-izle-hd")] = sayfa(
        "anime-chainsaw-man-reze-hen-izle-hd.html")
    site.tablo[("POST", "/get/video/group")] = _grup_tablosu(
        JqIOzjuJiT="group-chainsaw-dublaj.json", xPrXQ="group-chainsaw-altyazi.json")
    _fireplayer_her_videoya(site, "b6b505ff2025d4dec937e9dfba52e4c2",
                            "432773124021b504983e853ed7588fa6")

    akislar = am.get_episode_streams("chainsaw-man-reze-hen-izle-hd")

    assert [a["fansub"] for a in akislar] == ["Türkçe Altyazı", "Türkçe Dublaj"]
    getvideo = [c["data"]["hash"] for c in site.cagrilar if c["yol"] == f"{HD}/player/index.php"]
    assert getvideo == ["b6b505ff2025d4dec937e9dfba52e4c2", "432773124021b504983e853ed7588fa6"]


def test_akislar_ucuncu_taraf_aynalar_yt_dlpye_aynen(site):
    """Fansub sekmesi: sibnet/ok.ru/gdrive… geçiyor, ölü barındırıcılar atılıyor,
    hiçbiri için ek istek yok."""
    site.tablo[("GET", "/anime/blue-lock-2-sezon/sezon-1/bolum-1")] = sayfa(
        "ep-blue-lock-2-sezon-sezon-1-bolum-1.html")
    site.tablo[("POST", "/get/video/group")] = _grup_tablosu(
        **{"": "group-blue-lock-s1e1-bct.json"})

    akislar = am.get_episode_streams("blue-lock-2-sezon/sezon-1/bolum-1")

    bct = [a for a in akislar if a["fansub"] == "BÇT"]
    assert [a["player"] for a in bct] == [
        "SIBNET", "GDRIVE", "MAIL", "SENDVID", "MYVI", "VIDMOLY", "ODNOKLASSNIKI", "YOURUPLOAD"]
    konaklar = " ".join(a["url"] for a in akislar)
    for olu in ("savefileway", "fembed", "filemoon", "embedgram", "uqload", "liiivideo", "dood"):
        assert olu not in konaklar
    assert all(a["type"] == "iframe" and "referer" not in a for a in bct)
    assert bct[0]["label"] == "BÇT SIBNET"
    # 5 sekme × 1 POST + sayfa; üçüncü taraf adresler için istek yok.
    assert site.yollar() == ["/anime/blue-lock-2-sezon/sezon-1/bolum-1"] + ["/get/video/group"] * 5


def test_fansub_sekmesindeki_aynalar_suzuluyor():
    """Kirigana Fairies (Zom 100, 1. bölüm): 16 ayna; ölüler ağa çıkmadan atılıyor,
    Drive'ın bozuk "view?usp=drive_link/prev" eki temizleniyor."""
    videolar = json.loads(oku("group-zom100-1-kirigana.json"))["videos"]

    adaylar = [a["url"] for v in videolar for a in am._konagi_ac(v["link"])]

    assert adaylar == [
        "https://ok.ru/videoembed/6525664365191",
        "https://video.sibnet.ru/shell.php?videoid=5192996",
        "https://video.sibnet.ru/shell.php?videoid=5193005",
        "https://sendvid.com/embed/tnige5ol",
        "https://drive.google.com/file/d/1IxIZZogNb28Kkx1V0QZssmwsrdJI9-dq/view",
        "https://drive.google.com/file/d/1xGiScAVClt5Bbh-nfYHdg_nmXC1bmH3f/preview",
        "https://www.yourupload.com/embed/k6rx0e3g1JGH",
        "https://hdvid.tv/embed-t0w6539ldmuk-950x480.html",
        "https://hdvid.tv/embed-s8pn9ty7flxs-950x480.html",
    ]


def test_drive_ve_odnoklassniki_adresleri_duzeltiliyor():
    assert am._konagi_ac(
        "https://drive.google.com/file/d/1IxIZZogNb28Kkx1V0QZssmwsrdJI9-dq/view?usp=drive_link/prev"
    ) == [{"url": "https://drive.google.com/file/d/1IxIZZogNb28Kkx1V0QZssmwsrdJI9-dq/view",
           "type": "iframe", "alt": ""}]
    assert am._konagi_ac("//odnoklassniki.ru/videoembed/1104323152605")[0]["url"] == \
        "https://ok.ru/videoembed/1104323152605"
    assert am._konagi_ac("javascript:alert(1)") == []


def test_akislar_anizm_oynaticisi_aynadan_cozuluyor(site, monkeypatch):
    """anizm.net CF sınaması veriyor; aynı kimlik anizle.co'da 302 ile gidiyor."""
    from turkanime_api.sources import anizle

    monkeypatch.setattr(anizle, "aday_kokler", lambda: ["https://anizle.co"])
    site.tablo[("GET", "/anime/ayaka-1-bolum")] = sayfa("ep-ayaka-1-bolum.html")
    site.tablo[("POST", "/get/video/group")] = _grup_tablosu(
        tXF1TyEX2r="group-ayaka-1-animom.json", YMrnFqhnIw="group-ayaka-1-holy-anizm.json")
    _fireplayer(site)
    yonlen = {
        "1573968": "https://ok.ru/videoembed/5935650245373",
        "1573962": "https://video.sibnet.ru/shell.php?videoid=5200719",
        "1573961": "https://drive.google.com/file/d/1lcECyIzYm2eBUoU0d0STVqPdmKbtyPa-/preview",
    }
    for kimlik, hedef in yonlen.items():
        site.tablo[("GET", f"https://anizle.co/player/{kimlik}")] = Yanit(
            302, "", headers={"location": hedef})

    akislar = am.get_episode_streams("ayaka-1-bolum")

    holy = [(a["player"], a["url"]) for a in akislar if a["fansub"] == "Holy Fansup"]
    assert holy == [("SIBNET", yonlen["1573962"]), ("GDRIVE", yonlen["1573961"]),
                    ("ODNOKLASSNIKI", yonlen["1573968"])]
    assert akislar[0]["player"] == "ANIMOM"
    anizm = [c for c in site.cagrilar if c["yol"].startswith("https://anizle.co/")]
    # Adı ölü barındırıcı olanlar (STREAMSB, FILEMOON, CLOUDVIDEO, VOE) hiç sorulmuyor.
    assert sorted(c["yol"].rsplit("/", 1)[-1] for c in anizm) == sorted(yonlen)
    assert all(c["allow_redirects"] is False for c in anizm)
    assert not any("anizm.net" in c["url"] for c in site.cagrilar)


def test_akislar_kilitli_video_atlaniyor(site):
    """Üyelik katmanına kilitli kayıt: kapı aşılmaz, bağlantı uydurulmaz."""
    _frieren_bolumu(site)
    site.tablo[("POST", "/get/video/group")] = Yanit(200, json.dumps({"success": True, "videos": [
        {"name": "AniMOM", "link": "", "lock": True, "tiers": "PHA+w5x5ZWxlcmU8L3A+"},
        {"name": "AniMOM", "link": f"{HD}/video/eb6de71b465f16507cadfb2347a9d98f", "lock": 1},
    ]}))

    assert am.get_episode_streams("sousou-no-frieren-1-bolum") == []
    assert f"{HD}/player/index.php" not in site.yollar()


def test_cozum_butcesi_asilmiyor(site, monkeypatch):
    monkeypatch.setattr(am, "_AZAMI_COZUM", 2)
    _frieren_bolumu(site)
    site.tablo[("POST", "/get/video/group")] = Yanit(200, json.dumps({"success": True, "videos": [
        {"name": f"AniMOM {i}", "link": f"{HD}/video/{i:032x}"} for i in range(5)]}))
    _fireplayer(site)

    am.get_episode_streams("sousou-no-frieren-1-bolum")

    assert site.yollar("POST").count(f"{HD}/player/index.php") == 2


def test_videosu_olmayan_bolum_bos_liste(site):
    """Bölüm listede var ama videosu henüz yok (sekme de düğme de yok). Site
    bunu "Bu bölüm çok yakında yayınlanacak." diye gösteriyor (JJK 1. sezon,
    2026-09-30)."""
    site.tablo[("GET", "/anime/jujutsu-kaisen-1-bolum-izle-hd")] = Yanit(200, (
        '<div class="series-watch"><div class="series-watch-alternatives video-services">'
        '<ul class="flex flex-wrap mb-3"></ul></div><div class="series-watch-player relative">'
        '<div class="this-episode-not-ready"><div><h3>Jujutsu Kaisen <span>1. Bölüm</span></h3>'
        '<p>Bu bölüm çok yakında yayınlanacak.</p></div></div></div></div>'))
    assert am.get_episode_streams("jujutsu-kaisen-1-bolum-izle-hd") == []
    assert len(site.cagrilar) == 1


def test_sekme_ucu_duserse_sebep_yukseliyor(site):
    site.tablo[("GET", "/anime/sousou-no-frieren-1-bolum")] = sayfa("ep-sousou-no-frieren-1-bolum.html")
    site.tablo[("POST", "/get/video/group")] = Yanit(500, "Internal Server Error")

    with pytest.raises(am.AnimomHatasi, match="çözülemedi") as hata:
        am.get_episode_streams("sousou-no-frieren-1-bolum")
    assert hata.value.status_code == 500


def test_bolum_yoksa_404(site):
    site.tablo[("GET", "/anime/sousou-no-frieren-99-bolum")] = Yanit(404, oku("anime-404.html"))
    with pytest.raises(am.AnimomHatasi, match="böyle bir bölüm yok") as hata:
        am.get_episode_streams("sousou-no-frieren-99-bolum")
    assert hata.value.status_code == 404


@pytest.mark.parametrize("kimlik", ["", "../../etc/passwd", "a b", "x/sezon-1", "x/sezon-1/bolum-a"])
def test_akislar_gecersiz_kimlik_aga_cikmadan_hata(site, kimlik):
    with pytest.raises(am.AnimomHatasi, match="bölüm kimliği"):
        am.get_episode_streams(kimlik)
    assert site.cagrilar == []


def test_varyantlar_yuksekten_dusuge_ve_mutlak():
    master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n/hls/dusuk\n"
              "#EXT-X-STREAM-INF:BANDWIDTH=5800000,RESOLUTION=1920x1080\n/hls/yuksek\n")
    assert am.varyantlari_ayristir(master, MASTER + "?md5=x") == [
        (1080, f"{HD}/hls/yuksek"), (360, f"{HD}/hls/dusuk")]


# ─────────────────────────────────────────────────────────────────────────────
# Kimlikler, aralık
# ─────────────────────────────────────────────────────────────────────────────
def test_bolum_adresi_ve_slugu():
    assert am.bolum_adresi("blue-lock-2-sezon/sezon-2/bolum-1") == \
        "https://animom.org/anime/blue-lock-2-sezon/sezon-2/bolum-1"
    assert am.bolum_adresi("sousou-no-frieren-1-bolum") == \
        "https://animom.org/anime/sousou-no-frieren-1-bolum"
    # Tanınmayan kimlikler aynı adrese çökmesin (adres bölüm nesnesinin kimliği).
    assert am.bolum_adresi("A B") != am.bolum_adresi("A C")
    # SEO ekleri dosya adına/geçmiş anahtarına girmiyor.
    assert am.bolum_slugu("one-piece-1-bolum-izle-hd-izle-hd1") == "one-piece-1-bolum"
    assert am.bolum_slugu("one-piece-2-bolum-izle-hd111") == "one-piece-2-bolum"
    assert am.bolum_slugu("sousou-no-frieren-2-bolum-izle") == "sousou-no-frieren-2-bolum"
    assert am.bolum_slugu("blue-lock-2-sezon/sezon-2/bolum-1") == "blue-lock-2-sezon-sezon-2-bolum-1"


def test_istekler_konak_basina_aralikli(site, monkeypatch):
    """Site istekleri arasında `_MIN_INTERVAL`; oynatıcı konağı sitenin
    saatini beklemiyor, master ise getVideo'yu hiç beklemiyor."""
    _frieren_bolumu(site)
    _fireplayer(site)
    saat, uykular = [100.0], []

    def uyu(sn):
        uykular.append(round(sn, 3))
        saat[0] += sn
    monkeypatch.setattr(am, "time", types.SimpleNamespace(monotonic=lambda: saat[0], sleep=uyu))
    monkeypatch.setattr(am, "_MIN_INTERVAL", 1.0)

    am.get_episode_streams("sousou-no-frieren-1-bolum")

    # sayfa (0) → grup POST'u (1 sn bekler) → getVideo (başka konak: beklemez)
    # → master (beklemez).
    assert uykular == [1.0]
    assert [c["yol"] for c in site.cagrilar] == [
        "/anime/sousou-no-frieren-1-bolum", "/get/video/group", f"{HD}/player/index.php", MASTER]


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt ve uygulama boru hattı
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_animom_kaynagini_sunuyor():
    kaynak = kayit.bul("animom")
    assert kaynak is not None and kaynak is kayit.bul("AniMOM")
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("AniMOM", "AniMOM", "MO", "#4ff461", "ANIMOM")
    assert kaynak.modul == "animom" and kaynak.cli_kodu == "animom"
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    uclar = kaynak.uclar()
    assert uclar.ara is am.search_animom
    assert uclar.zengin_ara is am.zengin_ara
    assert uclar.bolumler is am.get_anime_episodes
    assert uclar.akislar is am.get_episode_streams
    assert kaynak.bolum_adresi("blue-lock-2-sezon/sezon-2/bolum-1") == \
        "https://animom.org/anime/blue-lock-2-sezon/sezon-2/bolum-1"
    assert kaynak.bolum_slugu("one-piece-1-bolum-izle-hd-izle-hd1") == "one-piece-1-bolum"
    kisaltmalar = [k.kisaltma for k in kayit.KAYNAKLAR]
    # "AM" Animezer'in; rozetler tekil olmalı.
    assert kisaltmalar.count("MO") == 1
    assert len(set(kisaltmalar)) == len(kisaltmalar)


def test_kayittan_bolumler_referer_yt_dlpye_ulasiyor(site, monkeypatch):
    """Köprü/CLI yolu: bölüm nesnesi izleme adresini taşıyor, seçilen videonun
    yt-dlp seçeneklerinde oynatıcının Referer'ı var."""
    from turkanime_api.sources import adapter as adapter_mod

    site.tablo[("GET", "/anime/sousou-no-frieren-izle")] = sayfa("anime-sousou-no-frieren-izle.html")
    _frieren_bolumu(site)
    _fireplayer(site)
    gorulen = []

    def bilgi(url, secenekler):
        gorulen.append((url, (secenekler.get("http_headers") or {}).get("Referer")))
        return {"url": url, "ext": "mp4"}
    monkeypatch.setattr(adapter_mod, "extract_video_info", bilgi)

    bolumler = adapter_mod.kayittan_bolumler(kayit.bul("AniMOM"), "sousou-no-frieren-izle",
                                            "Sousou no Frieren")
    bolum = bolumler[0]
    assert bolum.title == "1. Bölüm"
    assert bolum.url == "https://animom.org/anime/sousou-no-frieren-1-bolum"
    assert bolum.fansubs == ["Türkçe Altyazı"]

    video = bolum.best_video()

    assert video is not None and video.referer == "https://hdplayersystem.com/"
    assert gorulen[0][0].startswith("https://hdplayersystem.com/hls/")
    assert gorulen[0][1] == "https://hdplayersystem.com/"


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (--network)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    from urllib.parse import urljoin

    am.onbellegi_sifirla()
    sonuc = am.search_animom("frieren", limit=5)
    assert sonuc, "AniMOM aramada Frieren bulamadı"
    bolumler = am.get_anime_episodes(sonuc[0][0])
    assert len(bolumler) >= 28
    akislar = am.get_episode_streams(bolumler[0][0])
    hls = [a for a in akislar if a["type"] == "hls"]
    assert hls, f"AniMOM HLS akışı yok: {akislar}"

    oturum = am._yeni_oturum()
    liste = oturum.get(hls[0]["url"], timeout=30, headers={"Referer": hls[0]["referer"]})
    assert liste.status_code == 200 and "#EXTM3U" in liste.text
    parca = next(s for s in liste.text.splitlines() if s and not s.startswith("#"))
    if "#EXT-X-STREAM-INF" in liste.text:            # ses ayrı: master geldi
        liste = oturum.get(urljoin(hls[0]["url"], parca), timeout=30)
        parca = next(s for s in liste.text.splitlines() if s and not s.startswith("#"))
    ilk = oturum.get(urljoin(liste.url or hls[0]["url"], parca), timeout=30,
                     headers={"Referer": hls[0]["referer"]})
    assert ilk.status_code == 200 and ilk.content[:1] == b"\x47", "MPEG-TS parçası değil"
