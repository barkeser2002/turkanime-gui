"""Deokwave kaynağı (`turkanime_api/sources/deokwave.py`).

Hiçbir test ağa çıkmaz: modülün HTTP oturumu sahte bir oturumla değiştiriliyor.
Bu şart — conftest'in ağ mandalı `socket`'i kesiyor ama curl_cffi kendi
libcurl'ünü kullandığı için mandalın yanından geçer.

Fikstürler (`tests/fixtures/deokwave/`) 2026-09-24'te siteden alınan gerçek
yanıtlar. Büyük HTML sayfaları kırpıldı: `<head>`in başı (başlık, JSON-LD)
ve `var allSezonlar = ...;` değişmezini taşıyan `<script>` parçası aynen
duruyor, aradaki ilgisiz yüzlerce KB çıkarıldı. One Piece sayfasında 1179
bölümün anahtarları aynen; boyut için ilk 3 bölüm dışındakilerin içi
(küçük resim, fansub notları) boşaltıldı — ayrıştırıcı içeriğe bakmıyor.
`video_info-33D1627-s1e1.json` canlı kontrol sırasında alındı (site kendi
Sibnet vekilini "main" yapmış). JSON yanıtları olduğu gibi.

`--network` ile ayrıca canlı bir duman testi koşar (arama → bölümler → akış →
videonun ilk KB'ı).
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from turkanime_api.sources import deokwave as dw
from turkanime_api.sources import kayit

FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "deokwave"
TOKEN = "6f55c621eef78d3dab84dfc3a5219086bfd3d1d2e308bd4ffe7f7b23d6767c3d"


def oku(ad: str) -> str:
    return (FIKSTUR / ad).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sahte site
# ─────────────────────────────────────────────────────────────────────────────
class Yanit:
    """curl_cffi yanıtının kullandığımız kadarı."""

    def __init__(self, status_code: int = 200, text: str = "", url: str = ""):
        self.status_code = status_code
        self.text = text
        self.url = url

    def json(self):
        return json.loads(self.text)


def sayfa(ad: str, url: str = "") -> Yanit:
    return Yanit(200, oku(ad), url)


Cevap = Any   # Yanit | Exception | Callable[[params, profil], Yanit] | list (sırayla)


class SahteOturum:
    """Yol → cevap tablosu (bütün oturumlar aynı tabloyu paylaşıyor)."""

    def __init__(self, tablo: Dict[str, Cevap], profil: str, kayit_: List[Dict[str, Any]]):
        self.tablo = tablo
        self.profil = profil
        self.kayit = kayit_

    def get(self, url, params=None, headers=None, timeout=None):
        self.kayit.append({"url": url, "params": dict(params or {}),
                           "headers": dict(headers or {}), "timeout": timeout,
                           "profil": self.profil})
        yol = url[len(dw.BASE_URL):] if url.startswith(dw.BASE_URL) else url
        cevap = self.tablo.get(yol)
        if isinstance(cevap, list):
            cevap = cevap.pop(0) if len(cevap) > 1 else cevap[0]
        if callable(cevap) and not isinstance(cevap, Yanit):
            cevap = cevap(dict(params or {}), self.profil)
        if cevap is None:
            return Yanit(404, "Not Found", url)
        if isinstance(cevap, BaseException):
            raise cevap
        if not cevap.url:
            cevap.url = url
        return cevap


class _AgYasak:
    """Gerçek oturum kurulursa test ağa çıkıyor demektir."""

    class Session:
        def __init__(self, *a, **k):
            raise AssertionError("deokwave testi gerçek HTTP oturumu kurmaya çalıştı")


@pytest.fixture
def site(monkeypatch):
    """Modülü sahte siteye bağla; bekleme yok, anahtar önbelleği temiz."""
    tablo: Dict[str, Cevap] = {}
    cagrilar: List[Dict[str, Any]] = []
    profiller: List[str] = []

    def yeni_oturum(profil):
        profiller.append(profil)
        return SahteOturum(tablo, profil, cagrilar)

    monkeypatch.setattr(dw, "_http", _AgYasak)
    monkeypatch.setattr(dw, "_yeni_oturum", yeni_oturum)
    monkeypatch.setattr(dw, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(dw, "_oturum", None)
    monkeypatch.setattr(dw, "_profil_sirasi", 0)
    monkeypatch.setattr(dw, "_son_istek", 0.0)
    monkeypatch.setattr(dw, "_token", None)
    monkeypatch.setattr(dw, "_token_zamani", 0.0)
    return types.SimpleNamespace(tablo=tablo, cagrilar=cagrilar, profiller=profiller)


def _yollar(cagrilar) -> List[str]:
    return [c["url"][len(dw.BASE_URL):] for c in cagrilar]


def _video_info(ad: str) -> Callable[[Dict[str, Any], str], Yanit]:
    return lambda params, _p: Yanit(200, oku(ad))


def _akis_sitesi(site, video_info: str) -> None:
    site.tablo["/watch/video-info/"] = _video_info(video_info)
    site.tablo["/api/v1/video/token/"] = sayfa("video_token.json")


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_yeni_api_ayristirilip_siralaniyor(site):
    """Site "naruto" için 9 filmi "Naruto" dizisinden önce veriyor."""
    site.tablo["/api/v1/animes/search/"] = sayfa("search_v1-naruto.json")

    sonuc = dw.search_deokwave("  Naruto ", limit=5)

    assert sonuc[0] == ("E781094", "Naruto")              # tam eşleşme
    assert sonuc[1] == ("B2B63BE", "Naruto Shippūden")    # önek + dizi
    assert len(sonuc) == 5
    assert all(dw._ID_RE.match(k) for k, _ in sonuc)
    istek = site.cagrilar[0]
    assert istek["params"] == {"q": "Naruto", "page": 1}
    assert istek["headers"]["Referer"] == "https://deokwave.com/"
    assert istek["timeout"] == dw.HTTP_TIMEOUT
    assert len(site.cagrilar) == 1, "tek sayfa: ikinci sayfa istenmemeli"


def test_arama_ana_dizi_mini_animeden_once(site):
    site.tablo["/api/v1/animes/search/"] = sayfa("search_v1-frieren.json")
    assert dw.search_deokwave("frieren") == [
        ("0C61BB4", "Frieren: Beyond Journey's End"),
        ("9F35EFC", "Frieren: Beyond Journey's End Mini Anime"),
    ]


def test_arama_sonuc_yoksa_yedek_uca_bakip_bos_donuyor(site):
    site.tablo["/api/v1/animes/search/"] = Yanit(
        200, '{"success":true,"animes":[],"total":0,"page":1,"total_pages":0}')
    site.tablo["/search_api.php"] = sayfa("search_api-empty.json")

    assert dw.search_deokwave("yokboyleanime") == []
    assert _yollar(site.cagrilar) == ["/api/v1/animes/search/", "/search_api.php"]


def test_arama_yeni_api_bozuksa_otomatik_tamamlama_kullaniliyor(site):
    site.tablo["/api/v1/animes/search/"] = Yanit(500, "Internal Server Error")
    site.tablo["/search_api.php"] = sayfa("search_api-one_piece.json")

    sonuc = dw.search_deokwave("one piece", limit=3)

    assert sonuc[0] == ("11922FA", "One Piece")
    assert ("43F262A", "ONE PIECE") in sonuc
    assert len(sonuc) == 3
    assert site.cagrilar[-1]["params"] == {"q": "one piece"}


def test_arama_sayfalari_limit_dolana_kadar_geziyor(site):
    veri = json.loads(oku("search_v1-naruto.json"))
    sayfalar = {1: veri["animes"][:9], 2: veri["animes"][9:]}

    def cevap(params, _p):
        return Yanit(200, json.dumps({"success": True, "animes": sayfalar[params["page"]],
                                      "total": 17, "page": params["page"], "total_pages": 2}))
    site.tablo["/api/v1/animes/search/"] = cevap

    sonuc = dw.search_deokwave("naruto", limit=20)

    assert [c["params"]["page"] for c in site.cagrilar] == [1, 2]
    assert len(sonuc) == 17 and sonuc[0][0] == "E781094"


def test_arama_kisa_sorgu_aga_cikmiyor(site):
    assert dw.search_deokwave("a") == []
    assert dw.search_deokwave("   ") == []
    assert site.cagrilar == []


def test_arama_siteye_ulasilamazsa_sebep_yukseliyor(site):
    """Arama sayfası "sonuç yok" değil sebebi göstermeli (AramaSonuclari.hatalar)."""
    site.tablo["/api/v1/animes/search/"] = ConnectionError("ağ yok")
    site.tablo["/search_api.php"] = ConnectionError("ağ yok")

    with pytest.raises(dw.DeokwaveHatasi, match="bağlanılamadı"):
        dw.search_deokwave("naruto")


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_cok_sezonlu_izleme_sirasiyla(site):
    site.tablo["/anime/33D1627/"] = sayfa("anime-33D1627.html")

    bolumler = dw.get_anime_episodes("33d1627")      # küçük harf de kabul

    assert _yollar(site.cagrilar) == ["/anime/33D1627/"], "sondaki / şart (yoksa 404)"
    assert len(bolumler) == 59
    assert bolumler[0] == ("33D1627/1/1", "1. Sezon 1. Bölüm")
    assert bolumler[24] == ("33D1627/2/1", "2. Sezon 1. Bölüm")
    assert bolumler[-1] == ("33D1627/3/12", "3. Sezon 12. Bölüm")
    sezonlar: Dict[str, int] = {}
    for bolum_id, _ in bolumler:
        sezonlar[bolum_id.split("/")[1]] = sezonlar.get(bolum_id.split("/")[1], 0) + 1
    assert sezonlar == {"1": 24, "2": 23, "3": 12}


def test_bolumler_frieren_iki_sezon(site):
    site.tablo["/anime/0C61BB4/"] = sayfa("anime-0C61BB4.html")
    bolumler = dw.get_anime_episodes("0C61BB4")
    assert len(bolumler) == 38
    assert bolumler[27][0] == "0C61BB4/1/28" and bolumler[28][0] == "0C61BB4/2/1"


def test_bolumler_tek_sezonda_sezon_yazilmiyor_ve_sayisal_sirali(site):
    site.tablo["/anime/11922FA/"] = sayfa("anime-11922FA.html")

    bolumler = dw.get_anime_episodes("11922FA")

    assert len(bolumler) == 1179
    assert [b[0] for b in bolumler] == [f"11922FA/1/{n}" for n in range(1, 1180)]
    assert bolumler[0][1] == "1. Bölüm" and bolumler[-1][1] == "1179. Bölüm"


def test_bolumler_anahtarlar_sayisal_siralaniyor():
    """JSON anahtarları dizge: "10" < "2" olmamalı. PHP 0..n-1 anahtarlı sezonu
    listeye çeviriyor; indeksler bölüm numarası."""
    sayfa_ = ('<script>var allSezonlar = {"10":{"10":{},"2":{},"1":{}},"2":{"1":{}},'
              '"3":[{},{}]};</script>')
    assert dw.bolumleri_ayristir("ABCDEF0", sayfa_) == [
        ("ABCDEF0/2/1", "2. Sezon 1. Bölüm"),
        ("ABCDEF0/3/0", "3. Sezon 0. Bölüm"),
        ("ABCDEF0/3/1", "3. Sezon 1. Bölüm"),
        ("ABCDEF0/10/1", "10. Sezon 1. Bölüm"),
        ("ABCDEF0/10/2", "10. Sezon 2. Bölüm"),
        ("ABCDEF0/10/10", "10. Sezon 10. Bölüm"),
    ]


def test_film_tek_bolum_ve_basligi_sayfadan(site):
    site.tablo["/anime/B96C51E/"] = sayfa("anime-B96C51E-movie.html")
    assert dw.get_anime_episodes("B96C51E") == [("B96C51E/0/0", "Naruto Shippuden the Movie")]


def test_henuz_bolumu_olmayan_anime_bos_liste():
    assert dw.bolumleri_ayristir("ABCDEF0", "<script>var allSezonlar = [];</script>") == []


def test_anime_basligi_html_kacislari_cozuluyor():
    assert dw.anime_basligi(oku("anime-0C61BB4.html")) == "Frieren: Beyond Journey's End"


def test_bolumler_gecersiz_kimlik_aga_cikmadan_hata(site):
    with pytest.raises(dw.DeokwaveHatasi, match="7 haneli"):
        dw.get_anime_episodes("naruto")
    assert site.cagrilar == []


def test_bolumler_bilinmeyen_kimlik_ana_sayfaya_yonleniyor(site):
    """Site bilinmeyen kimliği 302 ile ana sayfaya atıyor; "0 bölüm" değil hata."""
    site.tablo["/anime/FFFFFF0/"] = sayfa("homepage.html", url="https://deokwave.com/")

    with pytest.raises(dw.DeokwaveHatasi, match="bulunamadı") as hata:
        dw.get_anime_episodes("FFFFFF0")
    assert hata.value.status_code == 404


def test_bolumler_sayfa_yapisi_degismisse_hata(site):
    site.tablo["/anime/0C61BB4/"] = Yanit(200, "<html><title>Deokwave</title></html>")
    with pytest.raises(dw.DeokwaveHatasi, match="bölüm listesi bulunamadı"):
        dw.get_anime_episodes("0C61BB4")


@pytest.mark.parametrize("cevap,iz", [
    (Yanit(500, "Internal Server Error"), "HTTP 500"),
    (TimeoutError("zaman aşımı"), "bağlanılamadı"),
])
def test_bolumler_ag_hatasi_turkce_mesajla_yukseliyor(site, cevap, iz):
    site.tablo["/anime/0C61BB4/"] = cevap
    with pytest.raises(dw.DeokwaveHatasi, match=iz):
        dw.get_anime_episodes("0C61BB4")


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def test_akislar_her_fansub_ayri_ve_referer_ile(site):
    _akis_sitesi(site, "video_info-33D1627-s2e5.json")

    akislar = dw.get_episode_streams("33D1627/2/5")

    info = next(c for c in site.cagrilar if c["url"].endswith("/watch/video-info/"))
    assert info["params"] == {"animeid": "33D1627", "season": 2, "episode": 5}
    assert len(akislar) == 21
    assert akislar[0] == {
        "url": f"https://sw2.deokwave.com/v/9a7ddb9a11a38a2c43cafcedf279a677/1080/?vt={TOKEN}",
        "label": "1080p HolySubs",
        "type": "direct",
        "referer": "https://deokwave.com/",
        "fansub": "HolySubs",
        "player": "DEOKWAVE",
    }
    assert all(a["referer"] == "https://deokwave.com/" for a in akislar)
    assert all(a["url"].startswith("https://sw2.deokwave.com/v/") for a in akislar)
    fansublar = list(dict.fromkeys(a["fansub"] for a in akislar))
    assert fansublar == ["HolySubs", "Chiharu Ceviri", "YukiSubs", "Kirigana Fairies",
                         "Çeviri-TR", "Adonis Fansub", "AnimeWho", "Deokwave"]
    # Sibnet vekili (isSibnet) sw2 üzerinden MP4, ama en sonda.
    assert akislar[-1]["url"].startswith(
        "https://sw2.deokwave.com/v/850d645498ba5c3a9813b271120bd4b5/480/")
    # Etiket çözünürlükle başlıyor: best_video'nun sıralaması onu okuyor.
    assert [a["label"] for a in akislar[:3]] == [
        "1080p HolySubs", "720p HolySubs", "480p HolySubs"]


def test_akislar_sibnet_vekili_main_olsa_da_sona(site):
    _akis_sitesi(site, "video_info-33D1627-s1e1.json")

    akislar = dw.get_episode_streams("33D1627/1/1")

    assert akislar[0]["fansub"] == "Tempest Fansub"
    assert akislar[-1]["fansub"] == "Deokwave" and "/480/" in akislar[-1]["url"]
    assert len(akislar) == 28


def test_akislar_harici_altyazili_kayit_atlaniyor(site):
    """hasSub: altyazı ayrı dosyada ve girişe bağlı; ham (altyazısız) oynardı."""
    _akis_sitesi(site, "video_info-E867981-s1e1-hassub.json")

    akislar = dw.get_episode_streams("E867981/1/1")

    assert akislar and not any("/2160/" in a["url"] for a in akislar)
    assert "Deokwave 4K" not in {a["fansub"] for a in akislar}
    assert akislar[0]["fansub"] == "FaichiSubs"          # sitenin varsayılanı


def test_akislar_fansub_listesi_bossa_ust_duzey_video(site):
    _akis_sitesi(site, "video_info-B96C51E-movie.json")

    akislar = dw.get_episode_streams("B96C51E/0/0")

    assert akislar == [{
        "url": f"https://sw2.deokwave.com/v/4360d01774c49d8d5accf90e563bcba2/480/?vt={TOKEN}",
        "label": "480p Deokwave", "type": "direct", "referer": "https://deokwave.com/",
        "fansub": "Deokwave", "player": "DEOKWAVE",
    }]


def test_akislar_kaliteler_yuksekten_dusuge(site):
    """Site kaliteleri bazen artan sırayla veriyor (850BA25: 480, 720, 1080)."""
    _akis_sitesi(site, "video_info-850BA25-empty_fansubs.json")
    assert [a["label"] for a in dw.get_episode_streams("850BA25/0/0")] == [
        "1080p Deokwave", "720p Deokwave", "480p Deokwave"]


def test_akislar_turkce_dublaj_etiketleniyor():
    bilgi = json.loads(oku("video_info-33D1627-s2e5.json"))
    yuki = next(f for f in bilgi["fansubs"] if f["name"] == "YukiSubs")
    yuki["extra"] = "Türkçe Dublaj"
    akislar = dw.akislari_kur(bilgi, "T" * 64)
    etiketler = {a["label"] for a in akislar}
    assert "1080p YukiSubs (TR Dublaj)" in etiketler
    assert "1080p HolySubs" in etiketler
    # fansub alanı sade ad kalıyor: kullanıcının seçtiği grup buna göre süzülüyor.
    assert {a["fansub"] for a in akislar if "Dublaj" in a["label"]} == {"YukiSubs"}


def test_bolum_yoksa_bos_liste_ve_anahtar_istenmiyor(site):
    _akis_sitesi(site, "video_info-notfound.json")
    assert dw.get_episode_streams("33D1627/9/99") == []
    assert "/api/v1/video/token/" not in _yollar(site.cagrilar)


def test_oynatma_anahtari_bes_dakika_onbellekte(site, monkeypatch):
    _akis_sitesi(site, "video_info-33D1627-s2e5.json")
    saat = [1000.0]
    monkeypatch.setattr(dw, "time", types.SimpleNamespace(
        monotonic=lambda: saat[0], sleep=lambda _s: None))

    dw.get_episode_streams("33D1627/2/5")
    dw.get_episode_streams("33D1627/2/5")
    assert _yollar(site.cagrilar).count("/api/v1/video/token/") == 1

    saat[0] += dw._TOKEN_TTL + 1          # anahtar dönüyor; eskisi 401 verir
    dw.get_episode_streams("33D1627/2/5")
    assert _yollar(site.cagrilar).count("/api/v1/video/token/") == 2


def test_oynatma_anahtari_uc_bozuksa_izleme_sayfasindan(site):
    site.tablo["/watch/video-info/"] = _video_info("video_info-33D1627-s2e5.json")
    site.tablo["/api/v1/video/token/"] = Yanit(200, '{"token": null}')
    site.tablo["/watch/33D1627/season/2/episode/5"] = sayfa("watch-0C61BB4-s1e1.html")

    akislar = dw.get_episode_streams("33D1627/2/5")

    assert akislar[0]["url"].endswith(f"?vt={TOKEN}")
    assert dw.token_from_watch_html(oku("watch-0C61BB4-s1e1.html")) == TOKEN


def test_oynatma_anahtari_hic_alinamazsa_hata(site):
    site.tablo["/watch/video-info/"] = _video_info("video_info-33D1627-s2e5.json")
    site.tablo["/api/v1/video/token/"] = Yanit(503, "Service Unavailable")
    site.tablo["/watch/33D1627/season/2/episode/5"] = Yanit(200, "<html></html>")

    with pytest.raises(dw.DeokwaveHatasi, match="oynatma anahtarı alınamadı"):
        dw.get_episode_streams("33D1627/2/5")


def test_akislar_gecersiz_kimlik_aga_cikmadan_hata(site):
    with pytest.raises(dw.DeokwaveHatasi, match="bölüm kimliği"):
        dw.get_episode_streams("33D1627-1-1")
    assert site.cagrilar == []


def test_akislar_json_yerine_html_gelirse_hata(site):
    site.tablo["/watch/video-info/"] = Yanit(200, "<!DOCTYPE html><html>bakım</html>")
    with pytest.raises(dw.DeokwaveHatasi, match="beklenmeyen"):
        dw.get_episode_streams("33D1627/2/5")


def test_izleme_adresi():
    assert dw.watch_url("0C61BB4/1/1") == "https://deokwave.com/watch/0C61BB4/season/1/episode/1"
    assert dw.watch_url("b96c51e/0/0") == "https://deokwave.com/watch/B96C51E/"
    # Tanınmayan kimlikler aynı adrese çökmesin (adres bölüm nesnesinin kimliği).
    assert dw.watch_url("17/b1") != dw.watch_url("17/b2")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP katmanı: kısıt, engel, aralık
# ─────────────────────────────────────────────────────────────────────────────
def _engelli_profil(icerik: Yanit, ad: str = "chrome131"):
    def cevap(_params, profil):
        return icerik if profil == ad else sayfa("search_v1-frieren.json")
    return cevap


def test_litespeed_engelinde_profil_degisip_yeniden_deneniyor(site):
    site.tablo["/api/v1/animes/search/"] = _engelli_profil(
        Yanit(403, oku("403-litespeed.html")))

    assert dw.search_deokwave("frieren")[0][0] == "0C61BB4"
    assert [c["profil"] for c in site.cagrilar] == ["chrome131", "safari17_0"]


def test_butun_profiller_engelliyse_engellenme_hatasi(site):
    from turkanime_server.crawler.nezaket import ENGELLENME, hata_turu

    site.tablo["/anime/0C61BB4/"] = Yanit(403, oku("403-litespeed.html"))

    with pytest.raises(dw.DeokwaveHatasi, match="engelledi") as hata:
        dw.get_anime_episodes("0C61BB4")
    assert len(site.cagrilar) == len(dw._PROFILES)
    assert hata.value.status_code == 403
    # Sunucu tarayıcısı kaynağı dinlendirsin, "geçici hata" deyip üstelemesin.
    assert hata_turu(hata.value) == ENGELLENME


def test_bot_dogrulama_sayfasi_engel_sayiliyor():
    # Gözlenen LiteSpeed reCAPTCHA sayfasının ayırt edici parçaları (başlık +
    # form kimliği); tam sayfa fikstür olarak yakalanamadı, arada bir çıkıyor.
    bot = ("<!DOCTYPE html><html><head><title>Bot Verification</title><script>"
           "function onSubmit(){document.getElementById('lsrecaptcha-form').submit();}"
           "</script></head></html>")
    assert dw._engellendi_mi(Yanit(403, bot))
    assert dw._engellendi_mi(Yanit(429, ""))
    # Normal sayfa CF betiği ("challenge-platform") taşıyabilir: 200 engel değil.
    assert not dw._engellendi_mi(Yanit(200, "/cdn-cgi/challenge-platform/scripts/jsd/main.js"))
    assert not dw._engellendi_mi(Yanit(404, "Access to this resource on the server is denied"))


def test_istekler_arasinda_en_az_bir_saniye(site, monkeypatch):
    """Ölçülen güvenli hız 1 istek/sn; patlama 1-3 dk'lık 403 getiriyor."""
    site.tablo["/api/v1/animes/search/"] = sayfa("search_v1-frieren.json")
    saat, uykular = [50.0], []

    def uyu(sn):
        uykular.append(round(sn, 3))
        saat[0] += sn
    monkeypatch.setattr(dw, "time", types.SimpleNamespace(monotonic=lambda: saat[0], sleep=uyu))
    monkeypatch.setattr(dw, "_MIN_INTERVAL", 1.0)

    dw.search_deokwave("frieren")
    saat[0] += 0.25
    dw.search_deokwave("frieren")

    assert uykular == [0.75]


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt ve uygulama boru hattı
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_deokwave_kaynagini_sunuyor():
    kaynak = kayit.bul("deokwave")
    assert kaynak is not None and kaynak is kayit.KAYNAKLAR[-1]
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("Deokwave", "Deokwave", "DW", "#6c5ce7", "DEOKWAVE")
    assert kaynak.modul == "deokwave" and kaynak.cli_kodu == "deokwave"
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    uclar = kaynak.uclar()
    assert uclar.ara is dw.search_deokwave
    assert uclar.bolumler is dw.get_anime_episodes
    assert uclar.akislar is dw.get_episode_streams
    assert kaynak.bolum_adresi("33D1627/2/5") == \
        "https://deokwave.com/watch/33D1627/season/2/episode/5"


def test_kayittan_bolumler_referer_yt_dlpye_ulasiyor(site, monkeypatch):
    """Köprü/CLI yolu: bölüm nesnesi izleme adresini taşıyor, akışlar fansub
    adlarını veriyor, seçilen videonun yt-dlp seçeneklerinde Referer var (yoksa
    sw2 404 döner)."""
    from turkanime_api.sources import adapter as adapter_mod

    site.tablo["/anime/33D1627/"] = sayfa("anime-33D1627.html")
    _akis_sitesi(site, "video_info-33D1627-s2e5.json")
    gorulen = []

    def bilgi(url, secenekler):
        gorulen.append((url, (secenekler.get("http_headers") or {}).get("Referer")))
        return {"url": url, "ext": "mp4"}
    monkeypatch.setattr(adapter_mod, "extract_video_info", bilgi)

    bolumler = adapter_mod.kayittan_bolumler(kayit.bul("Deokwave"), "33D1627", "Jujutsu Kaisen")
    bolum = bolumler[24 + 4]                                # 2. sezon 5. bölüm
    assert bolum.title == "2. Sezon 5. Bölüm"
    assert bolum.url == "https://deokwave.com/watch/33D1627/season/2/episode/5"
    assert bolum.fansubs[:2] == ["HolySubs", "Chiharu Ceviri"]

    video = bolum.best_video(by_fansub="YukiSubs")

    assert video is not None and video.referer == "https://deokwave.com/"
    assert gorulen[0] == (
        f"https://sw2.deokwave.com/v/adaa689596d5863e3a47c27af942ea37/1080/?vt={TOKEN}",
        "https://deokwave.com/")


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (--network)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = dw.search_deokwave("frieren", limit=5)
    assert sonuc, "Deokwave aramada Frieren bulamadı"
    kimlik = sonuc[0][0]
    bolumler = dw.get_anime_episodes(kimlik)
    assert len(bolumler) >= 28
    akislar = dw.get_episode_streams(bolumler[0][0])
    assert akislar and all(a["referer"] == dw.REFERER for a in akislar)

    oturum = dw._yeni_oturum(dw._PROFILES[0])
    yanit: Optional[Any] = oturum.get(akislar[0]["url"], timeout=30, headers={
        "Referer": akislar[0]["referer"], "Range": "bytes=0-1023"})
    assert yanit.status_code in (200, 206)
    assert yanit.headers.get("content-type", "").startswith("video/")
