"""Asya Animeleri kaynağı (`sources/asyaanimeleri.py`).

Hiçbir test ağa çıkmaz: HTTP oturumu sahte. Bu ŞART — conftest soket
mandalı kuruyor ama curl_cffi kendi libcurl'ünü kullandığı için mandalın
yanından geçer; sahtelenmeyen bir istek gerçekten siteye giderdi.

Fixture'lar `tests/fixtures/asyaanimeleri/` altında: sitenin 2026-09-24
tarihli gerçek yanıtlarından kırpıldı (işaretleme aynen, yalnızca
ayrıştırıcının okumadığı kısımlar atıldı). Sınananlar:

* arama: kart ayrıştırma, kenar çubuğu sızıntısı, sayfalama, boş sonuç,
  Türkçe sorgunun kodlanması;
* bölümler: izleme sırası, aralıklı bölümler ("1-3"), sitenin numara yazım
  hataları, boş seri;
* akışlar: base64 aynaların çözülmesi, tırnaksız/protokolsüz iframe adresleri,
  oynatılamayan konakların atılması, ortak oynatıcı önceliği, referer;
* hata yolları: ağ hatası, engel sayfası, 404, tanınmayan sayfa → AÇIK hata
  (boş liste değil), testcookie kapısının çözülmesi;
* kayıt: kaynak kayıtta, köprü/CLI/arama motoru üzerinden uçtan uca.

Canlı duman testi `@pytest.mark.network` ile işaretli; `--network` ile koşar.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus

import pytest

from turkanime_api.common.oynatici_onceligi import oncelik_anahtari
from turkanime_api.sources import asyaanimeleri as aa
from turkanime_api.sources import kayit

KOK = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "asyaanimeleri"
TABAN = "https://asyaanimeleri.top"
REFERER = TABAN + "/"


def fx(ad: str) -> str:
    return (FIXTURE / ad).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sahte HTTP oturumu
# ─────────────────────────────────────────────────────────────────────────────
class BeklenmeyenIstek(BaseException):
    """Rota tablosunda olmayan adres. `Exception` DEĞİL: modül ağ hatalarını
    `Exception` ile sarıyor; bu, sarılmadan teste kadar çıksın."""


class SahteYanit:
    def __init__(self, durum: int, govde: str):
        self.status_code = durum
        self.text = govde


class SahteCerezler:
    def __init__(self):
        self.kayitlar: List[Tuple[str, str, str]] = []

    def set(self, ad, deger, domain=""):
        self.kayitlar.append((ad, deger, domain))


class SahteOturum:
    """`url → yanıt listesi` tablosu. Liste sırayla tüketilir (son eleman
    tekrarlanır); eleman bir istisna ise fırlatılır. Her çağrı kaydedilir."""

    def __init__(self, rotalar: Dict[str, Any]):
        self.rotalar = {u: (v if isinstance(v, list) else [v]) for u, v in rotalar.items()}
        self.cagrilar: List[Dict[str, Any]] = []
        self.cookies = SahteCerezler()

    def get(self, url, headers=None, timeout=None, **_kw):
        self.cagrilar.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        if url not in self.rotalar:
            raise BeklenmeyenIstek(url)
        sira = self.rotalar[url]
        cevap = sira.pop(0) if len(sira) > 1 else sira[0]
        if isinstance(cevap, BaseException):
            raise cevap
        return cevap


@pytest.fixture
def site(monkeypatch):
    """Sahte site kur: ``site({url: yanit, ...})`` oturumu döndürür.

    CF yedeği de kapatılıyor (gerçeği ortak CF zincirine, yani ağa gider);
    çağrıldığı `yedek_cagrilari`'ndan okunur.
    """
    monkeypatch.setattr(aa, "BASE_URL", TABAN)
    yedek_cagrilari: List[str] = []

    def _cf_yedegi(url, _basliklar):
        yedek_cagrilari.append(url)
        return None
    monkeypatch.setattr(aa, "_cf_yedegi", _cf_yedegi)

    def kur(rotalar: Dict[str, Any]) -> SahteOturum:
        oturum = SahteOturum(rotalar)
        monkeypatch.setattr(aa, "_oturum_al", lambda: oturum)
        return oturum

    kur.yedek_cagrilari = yedek_cagrilari  # type: ignore[attr-defined]
    return kur


def ok(ad: str) -> SahteYanit:
    return SahteYanit(200, fx(ad))


def arama_adresi(sorgu: str, sayfa: int = 1) -> str:
    if sayfa == 1:
        return f"{TABAN}/?s={quote_plus(sorgu)}"
    return f"{TABAN}/page/{sayfa}/?s={quote_plus(sorgu)}"


def iframe_b64(src: str, tirnak: str = '"') -> str:
    etiket = f'<iframe width="640" height="360" src={tirnak}{src}{tirnak} allowfullscreen></iframe>'
    return base64.b64encode(etiket.encode()).decode()


def bolum_sayfasi(secenekler: List[Tuple[str, str]], ek: str = "", pembed: str = "") -> str:
    """Sitenin bölüm sayfası iskeleti (gerçek fixture'lardaki işaretlemeyle)."""
    opts = "\n".join(f'<option value="{v}" data-index="{i}"> {e} </option>'
                     for i, (v, e) in enumerate(secenekler, 1))
    return (f'{ek}<div class="video-content"><div id="embed_holder" class="lowvid">'
            f'<div class="player-embed" id="pembed">{pembed}</div></div></div>'
            f'<div class="item video-nav"><div class="mobius">'
            f'<select class="mirror" name="mirror" onchange="loadMi(this);">'
            f'<option value="">Player Seç</option>{opts}</select></div></div>')


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_kartlari_ayristiriyor(site):
    oturum = site({arama_adresi("one piece"): ok("arama-one-piece.html")})

    sonuc = aa.search_asyaanimeleri("one piece", limit=10)

    assert sonuc == [("one-piece-live-action", "One Piece Live Action"),
                     ("one-piece", "One Piece"),
                     ("one-piece-film-red", "One Piece Film: Red")]
    # Tek sayfa ("next" yok): ikinci sayfa istenmemeli.
    assert [c["url"] for c in oturum.cagrilar] == [arama_adresi("one piece")]
    assert oturum.cagrilar[0]["timeout"] == aa.HTTP_TIMEOUT
    assert oturum.cagrilar[0]["headers"]["Referer"] == REFERER


def test_arama_kenar_cubugunu_sonuclara_karistirmiyor(site):
    """Kenar çubuğundaki "popüler seriler" de /series/ bağlantısı taşıyor."""
    govde = fx("arama-one-piece.html")
    assert "/series/black-clover-2-sezon/" in govde, "fixture kenar çubuğunu içermeli"
    site({arama_adresi("one piece"): SahteYanit(200, govde)})

    sluglar = {s for s, _ in aa.search_asyaanimeleri("one piece")}

    assert not sluglar & {"black-clover-2-sezon", "da-wang-rao-ming-3-sezon",
                          "xian-ni-movie-shi-xian-zhi-zhan"}


def test_zengin_arama_kapak_gorselini_veriyor(site):
    site({arama_adresi("one piece"): ok("arama-one-piece.html")})

    kayitlar = aa.zengin_ara("one piece")

    assert kayitlar[1] == {
        "slug": "one-piece", "title": "One Piece",
        "image": f"{TABAN}/wp-content/uploads/2023/08/139965-212x300.jpg"}
    assert all(k["image"].startswith(TABAN + "/wp-content/") for k in kayitlar)


def test_arama_sonraki_sayfaya_geciyor(site):
    oturum = site({arama_adresi("xian"): ok("arama-xian-1.html"),
                   arama_adresi("xian", 2): ok("arama-xian-2.html")})

    sonuc = aa.search_asyaanimeleri("xian", limit=15)

    assert len(sonuc) == 15
    assert len({s for s, _ in sonuc}) == 15, "iki sayfa aynı seriyi tekrar vermemeli"
    assert [c["url"] for c in oturum.cagrilar] == [arama_adresi("xian"),
                                                   arama_adresi("xian", 2)]


def test_arama_limit_dolunca_sonraki_sayfayi_istemiyor(site):
    oturum = site({arama_adresi("xian"): ok("arama-xian-1.html")})

    sonuc = aa.search_asyaanimeleri("xian", limit=10)

    assert len(sonuc) == 10
    assert len(oturum.cagrilar) == 1
    assert len(aa.search_asyaanimeleri("xian", limit=3)) == 3


def test_arama_sayfa_sayisi_sinirli(site, monkeypatch):
    """Genel bir sorgu 10 sayfa tutuyor; toplam arama bütçesini tek kaynak yememeli."""
    monkeypatch.setattr(aa, "AZAMI_ARAMA_SAYFASI", 2)
    oturum = site({arama_adresi("xian"): ok("arama-xian-1.html"),
                   arama_adresi("xian", 2): ok("arama-xian-1.html")})   # "next" hep var

    aa.search_asyaanimeleri("xian", limit=100)

    assert len(oturum.cagrilar) == 2


def test_sonucsuz_arama_bos_liste(site):
    site({arama_adresi("zzzzqqqq"): ok("arama-bos.html")})
    assert aa.search_asyaanimeleri("zzzzqqqq") == []


@pytest.mark.parametrize("sorgu", ["", "   ", None])
def test_bos_sorgu_istek_atmiyor(site, sorgu):
    oturum = site({})
    assert aa.search_asyaanimeleri(sorgu) == []
    assert oturum.cagrilar == []


def test_turkce_sorgu_kodlanarak_gidiyor(site):
    oturum = site({arama_adresi("şeytan avcısı"): ok("arama-bos.html")})

    aa.search_asyaanimeleri("  şeytan avcısı ")

    assert oturum.cagrilar[0]["url"] == \
        f"{TABAN}/?s=%C5%9Feytan+avc%C4%B1s%C4%B1"


def test_arama_ag_hatasi_acik_hata_veriyor(site):
    """Ağ hatası "sonuç yok" DEĞİL: arama motoru bunu `hatalar`'a yazmalı."""
    site({arama_adresi("naruto"): ConnectionError("bağlantı reddedildi")})

    with pytest.raises(aa.AsyaAnimeleriHatasi, match="ulaşılamadı.*bağlantı reddedildi"):
        aa.search_asyaanimeleri("naruto")


def test_arama_engel_sayfasi_acik_hata_veriyor(site):
    site({arama_adresi("naruto"): SahteYanit(403, "<title>Just a moment...</title>")})

    with pytest.raises(aa.AsyaAnimeleriHatasi, match="engelledi.*403"):
        aa.search_asyaanimeleri("naruto")
    assert site.yedek_cagrilari == [arama_adresi("naruto")], "önce CF zinciri denenmeli"


def test_engelde_cf_yedegi_kurtarirsa_sonuc_geliyor(site, monkeypatch):
    site({arama_adresi("one piece"): SahteYanit(503, "Just a moment...")})
    monkeypatch.setattr(aa, "_cf_yedegi", lambda url, _b: ok("arama-one-piece.html"))

    assert len(aa.search_asyaanimeleri("one piece")) == 3


def test_200_donen_challenge_sayfasi_da_engel(site):
    """Cloudflare challenge sayfası HTTP 200 ile de gelebiliyor."""
    site({arama_adresi("naruto"): SahteYanit(200, "<html>Checking your browser</html>")})

    with pytest.raises(aa.AsyaAnimeleriHatasi, match="engelledi"):
        aa.search_asyaanimeleri("naruto")


def test_arama_sayfasi_tanınmazsa_acik_hata(site):
    site({arama_adresi("naruto"): SahteYanit(200, "<html><body>bakım</body></html>")})

    with pytest.raises(aa.AsyaAnimeleriHatasi, match="beklenen biçimde değil"):
        aa.search_asyaanimeleri("naruto")


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def seri_adresi(slug: str) -> str:
    return f"{TABAN}/series/{slug}/"


def test_bolumler_izleme_sirasinda(site):
    oturum = site({seri_adresi("sousou-no-frieren"): ok("seri-sousou-no-frieren.html")})

    bolumler = aa.get_anime_episodes("sousou-no-frieren")

    assert len(bolumler) == 28
    # Site en yeniyi üstte veriyor; izleme sırası eskiden yeniye.
    assert bolumler[0] == ("sousou-no-frieren-1-bolum-turkce-altyazili", "1. Bölüm")
    assert bolumler[-1] == ("sousou-no-frieren-28-bolum", "28. Bölüm")
    assert [b for _, b in bolumler] == [f"{i}. Bölüm" for i in range(1, 29)]
    assert oturum.cagrilar[0]["timeout"] == aa.HTTP_TIMEOUT


def test_aralikli_bolumler_ve_duzensiz_sluglar(site):
    site({seri_adresi("xian-ni"): ok("seri-xian-ni.html")})

    bolumler = aa.get_anime_episodes("xian-ni")

    assert len(bolumler) == 75
    assert bolumler[0] == ("xian-ni-1-3-bolum", "1-3. Bölüm")
    assert bolumler[-1] == ("xian-ni-159-bolum", "159. Bölüm")
    ilkler = [int(b.split(".")[0].split("-")[0]) for _, b in bolumler]
    assert ilkler == sorted(ilkler)
    # Slug'lar sayfadan aynen: numaradan üretilseydi bunlar tutmazdı.
    kimlikler = {k for k, _ in bolumler}
    assert {"xian-ni-138-bolum-4k", "xian-ni-144-bolum-2"} <= kimlikler


def test_sitenin_numara_yazim_hatalari_duzeltiliyor(site):
    """epl-num "146-1150"/başlık "1146-1150"; epl-num "1125"/başlık "125"."""
    site({seri_adresi("one-piece"): ok("seri-one-piece.html")})

    bolumler = aa.get_anime_episodes("one-piece")
    etiket = dict(bolumler)

    assert len(bolumler) == 45
    assert etiket["one-piece-125-bolum-izle"] == "1125. Bölüm"
    assert etiket["one-piece-1146-1150-bolum-izle"] == "1146-1150. Bölüm"
    assert bolumler[0] == ("one-piece-1071-bolum", "1071. Bölüm")
    assert bolumler[-1] == ("one-piece-1161-bolum-izle", "1161. Bölüm")


def test_bolumu_olmayan_seri_bos_liste(site):
    site({seri_adresi("black-clover-2-sezon"): ok("seri-black-clover-2-sezon-bos.html")})
    assert aa.get_anime_episodes("black-clover-2-sezon") == []


def test_tek_bolumluk_film(site):
    site({seri_adresi("one-piece-film-red"): ok("seri-one-piece-film-red.html")})
    assert aa.get_anime_episodes("one-piece-film-red") == \
        [("one-piece-film-red-turkce-altyazili", "1. Bölüm")]


def _li(yol: str, num: str, baslik: str) -> str:
    return (f'<li data-index="0">\n    <a href="{TABAN}/{yol}/">\n'
            f'        <div class="epl-num">{num}</div>\n'
            f'        <div class="epl-title">{baslik}</div>\n'
            f'                <div class="epl-date">Mart 1, 2024</div>\n    </a>\n</li>')


def _seri(*satirlar: str) -> str:
    return '<div class="eplister"><ul>' + "".join(satirlar) + "</ul></div>"


def test_sayisal_olmayan_bolum_basligiyla_ve_sonda(site):
    site({seri_adresi("x"): SahteYanit(200, _seri(
        _li("x-movie", "Movie", "Jujutsu Kaisen 0 Movie"),
        _li("x-2-bolum", "2", "X 2.Bölüm"),
        _li("x-1-bolum", "1", "X 1.Bölüm")))})

    assert aa.get_anime_episodes("x") == [
        ("x-1-bolum", "1. Bölüm"), ("x-2-bolum", "2. Bölüm"),
        ("x-movie", "Jujutsu Kaisen 0 Movie")]


def test_numara_tekrarlaniyorsa_sitenin_sirasi_korunuyor(site):
    """Aynı sayfada iki sezon 1'den başlıyorsa numaraya göre dizmek sezonları
    birbirine geçirirdi (1,1,2,2); sitenin (ters çevrilmiş) sırası doğru."""
    site({seri_adresi("x"): SahteYanit(200, _seri(
        _li("x-2-sezon-2-bolum", "2", "X 2.Sezon 2.Bölüm"),
        _li("x-2-sezon-1-bolum", "1", "X 2.Sezon 1.Bölüm"),
        _li("x-2-bolum", "2", "X 2.Bölüm"),
        _li("x-1-bolum", "1", "X 1.Bölüm")))})

    assert [k for k, _ in aa.get_anime_episodes("x")] == [
        "x-1-bolum", "x-2-bolum", "x-2-sezon-1-bolum", "x-2-sezon-2-bolum"]


def test_yinelenen_bolum_bir_kez(site):
    site({seri_adresi("x"): SahteYanit(200, _seri(
        _li("x-1-bolum", "1", "X 1.Bölüm"), _li("x-1-bolum", "1", "X 1.Bölüm")))})
    assert aa.get_anime_episodes("x") == [("x-1-bolum", "1. Bölüm")]


@pytest.mark.parametrize("kimlik", [
    "sousou-no-frieren",
    "/sousou-no-frieren/",
    "series/sousou-no-frieren",
    f"{TABAN}/series/sousou-no-frieren/",
])
def test_seri_kimligi_bicimleri(site, kimlik):
    oturum = site({seri_adresi("sousou-no-frieren"): ok("seri-sousou-no-frieren.html")})
    assert len(aa.get_anime_episodes(kimlik)) == 28
    assert oturum.cagrilar[0]["url"] == seri_adresi("sousou-no-frieren")


def test_yuzde_kodlu_slug_aynen_gidiyor(site):
    """Yeniden kodlanırsa ("%25ef…") sayfa 404 döner."""
    slug = "attack-on-titan%ef%bc%9arequiem"
    oturum = site({seri_adresi(slug): ok("seri-one-piece-film-red.html")})
    aa.get_anime_episodes(slug)
    assert oturum.cagrilar[0]["url"] == f"{TABAN}/series/{slug}/"


def test_baska_konagin_adresi_kimlik_olamaz(site):
    oturum = site({})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="Asya Animeleri adresi değil"):
        aa.get_anime_episodes("https://kotu.example/series/x/")
    assert oturum.cagrilar == []


def test_bilinmeyen_seri_acik_hata(site):
    site({seri_adresi("yok-boyle"): SahteYanit(404, "<html>404</html>")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="'yok-boyle' serisi bulunamadı"):
        aa.get_anime_episodes("yok-boyle")


def test_bolum_listesi_ag_hatasi_acik_hata(site):
    site({seri_adresi("x"): TimeoutError("zaman aşımı")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="ulaşılamadı"):
        aa.get_anime_episodes("x")


def test_bolum_listesi_sunucu_hatasi_acik_hata(site):
    site({seri_adresi("x"): SahteYanit(522, "origin down")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="HTTP 522"):
        aa.get_anime_episodes("x")


def test_bolum_listesi_yoksa_duzen_degisti_hatasi(site):
    site({seri_adresi("x"): SahteYanit(200, "<html><h1>X</h1></html>")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="bölüm listesi yok"):
        aa.get_anime_episodes("x")


def test_satirlar_taninmazsa_ayristirma_hatasi(site):
    """Liste dolu ama tek satır tanınmıyorsa "bölüm yok" demek yalan olur."""
    site({seri_adresi("x"): SahteYanit(
        200, '<div class="eplister"><ul><li><span>1</span></li><li><span>2</span></li></ul></div>')})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="2 satır tanınmadı"):
        aa.get_anime_episodes("x")


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def bolum_adresi_(yol: str) -> str:
    return f"{TABAN}/{yol}/"


def test_aynalar_cozuluyor_ve_oncelige_gore_siralaniyor(site):
    oturum = site({bolum_adresi_("sousou-no-frieren-28-bolum"):
                   ok("bolum-sousou-no-frieren-28.html")})

    akislar = aa.get_episode_streams("sousou-no-frieren-28-bolum")

    assert [(a["player"], a["url"]) for a in akislar] == [
        ("GDRIVE", "https://drive.google.com/file/d/1UN6RBd7W56GcrmOh-U365sAok7p81TuH/preview"),
        # Protokolsüz "//ok.ru/…" → https.
        ("ODNOKLASSNIKI", "https://ok.ru/videoembed/7365199596038"),
        ("SIBNET", "https://video.sibnet.ru/shell.php?videoid=5479211"),
    ]
    # VK (yt-dlp çıkarıcısı bozuk), vidmoly.to (park edilmiş alan adı),
    # filemoon (yt-dlp reddediyor) atıldı.
    adresler = " ".join(a["url"] for a in akislar)
    assert "vk.com" not in adresler and "vidmoly.to" not in adresler
    assert "filemoon" not in adresler
    assert [a["label"] for a in akislar] == ["Gdrive", "Ok.ru", "Sibnet"]
    assert all(a["type"] == "iframe" and a["referer"] == REFERER for a in akislar)
    assert oturum.cagrilar[0]["headers"]["Referer"] == REFERER
    assert oturum.cagrilar[0]["timeout"] == aa.HTTP_TIMEOUT


def test_sira_ortak_oynatici_onceligini_izliyor(site):
    """Sıra kaynağa özgü değil: `common/oynatici_onceligi` tek kaynak."""
    site({bolum_adresi_("sousou-no-frieren-28-bolum"): ok("bolum-sousou-no-frieren-28.html"),
          bolum_adresi_("xian-ni-159-bolum"): ok("bolum-xian-ni-159.html"),
          bolum_adresi_("one-piece-1161-bolum-izle"): ok("bolum-one-piece-1161.html")})
    for yol in ("sousou-no-frieren-28-bolum", "xian-ni-159-bolum",
                "one-piece-1161-bolum-izle"):
        oynaticilar = [a["player"] for a in aa.get_episode_streams(yol)]
        assert oynaticilar == sorted(oynaticilar, key=oncelik_anahtari), yol


def test_olu_varsayilan_oynatici_atiliyor(site):
    """"VİP" (asyaanimeleri.pw) varsayılan iframe ama her istekte 522 veriyor."""
    site({bolum_adresi_("xian-ni-159-bolum"): ok("bolum-xian-ni-159.html"),
          bolum_adresi_("one-piece-1161-bolum-izle"): ok("bolum-one-piece-1161.html")})

    xian = aa.get_episode_streams("xian-ni-159-bolum")
    one_piece = aa.get_episode_streams("one-piece-1161-bolum-izle")

    assert [(a["player"], a["label"]) for a in xian] == [("GDRIVE", "GDRİVE"),
                                                         ("VIDMOLY", "Moly")]
    assert xian[1]["url"] == "https://vidmoly.biz/embed-53qvqgwt4uq7.html"
    assert [a["player"] for a in one_piece] == ["GDRIVE", "SIBNET"]
    for akis in xian + one_piece:
        assert "asyaanimeleri.pw" not in akis["url"]
        for olu in ("rumble.com", "puterin", "vkvideo", "gdplayer"):
            assert olu not in akis["url"]


def test_tirnaksiz_iframe_adresi(site):
    """naruto-1-bolum: `<iframe … src=https://…&share=1>` — tırnak yok."""
    site({bolum_adresi_("naruto-1-bolum"): ok("bolum-naruto-1-tirnaksiz.html")})

    assert aa.get_episode_streams("naruto-1-bolum") == [{
        "url": "https://video.sibnet.ru/shell.php?videoid=3642169&share=1",
        "label": "Sibnet", "player": "SIBNET", "type": "iframe", "referer": REFERER}]


def test_ayna_menusu_yoksa_varsayilan_iframe(site):
    sayfa = ('<div class="player-embed" id="pembed">'
             '<iframe src="//vidmoly.net/embed-abc123.html" frameborder="0"></iframe></div>')
    site({bolum_adresi_("x-1-bolum"): SahteYanit(200, sayfa)})

    akislar = aa.get_episode_streams("x-1-bolum")

    assert [(a["player"], a["url"], a["label"]) for a in akislar] == [
        ("VIDMOLY", "https://vidmoly.net/embed-abc123.html", "Vidmoly")]


def test_ayna_disindaki_secenekler_okunmuyor(site):
    """Ayna menüsü dışındaki <option>'lar ayna değil; base64 gibi görünse de."""
    tuzak = ('<select name="genre[]"><option value="'
             + iframe_b64("https://video.sibnet.ru/shell.php?videoid=1") + '">Aksiyon</option></select>')
    sayfa = bolum_sayfasi([(iframe_b64("https://my.mail.ru/video/embed/42"), "Mail")], ek=tuzak)
    site({bolum_adresi_("x-1-bolum"): SahteYanit(200, sayfa)})

    assert [a["url"] for a in aa.get_episode_streams("x-1-bolum")] == \
        ["https://my.mail.ru/video/embed/42"]


def test_ayni_ayna_bir_kez_bozuk_ayna_atlaniyor(site):
    sibnet = iframe_b64("https://video.sibnet.ru/shell.php?videoid=7")
    sayfa = bolum_sayfasi([(sibnet, "Sibnet"), (sibnet, "Sibnet 2"),
                           ("!!!bozuk-base64!!!", "Bozuk"),
                           (base64.b64encode(b"<p>iframe yok</p>").decode(), "Bos"),
                           (iframe_b64("https://www.dailymotion.com/embed/video/x8", "'"), "Daily")])
    site({bolum_adresi_("x-1-bolum"): SahteYanit(200, sayfa)})

    akislar = aa.get_episode_streams("x-1-bolum")

    assert [(a["player"], a["label"]) for a in akislar] == [("DAILYMOTION", "Daily"),
                                                            ("SIBNET", "Sibnet")]


def test_hic_oynatilabilir_ayna_yoksa_bos_liste(site):
    sayfa = bolum_sayfasi([(iframe_b64("https://vk.com/video_ext.php?oid=1&id=2"), "VK"),
                           (iframe_b64("https://voe.sx/e/abc"), "Voe")])
    site({bolum_adresi_("x-1-bolum"): SahteYanit(200, sayfa)})
    assert aa.get_episode_streams("x-1-bolum") == []


def test_bilinmeyen_bolum_acik_hata(site):
    site({bolum_adresi_("yok-1-bolum"): SahteYanit(404, "yok")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="'yok-1-bolum' bölümü bulunamadı"):
        aa.get_episode_streams("yok-1-bolum")


def test_oynaticisiz_bolum_sayfasi_duzen_hatasi(site):
    site({bolum_adresi_("x-1-bolum"): SahteYanit(200, "<html><h1>X 1.Bölüm</h1></html>")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="oynatıcı bulunamadı"):
        aa.get_episode_streams("x-1-bolum")


def test_akis_ag_hatasi_acik_hata(site):
    site({bolum_adresi_("x-1-bolum"): OSError("ağ yok")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="ulaşılamadı.*ağ yok"):
        aa.get_episode_streams("x-1-bolum")


def test_akis_adapter_videoya_referer_ile_geciyor(site):
    """Akışın referer'ı yt-dlp'nin bütün isteklerine iliştirilmeli."""
    from turkanime_api.sources.adapter import AdapterVideo

    site({bolum_adresi_("naruto-1-bolum"): ok("bolum-naruto-1-tirnaksiz.html")})
    akis = aa.get_episode_streams("naruto-1-bolum")[0]

    video = AdapterVideo(None, akis["url"], akis["label"], player=akis["player"],
                         referer=akis["referer"])

    assert video.url == "https://video.sibnet.ru/shell.php?videoid=3642169&share=1"
    assert video.ydl_opts["http_headers"]["Referer"] == REFERER


# ─────────────────────────────────────────────────────────────────────────────
# testcookie kapısı (HTTP 202 + slowAES)
# ─────────────────────────────────────────────────────────────────────────────
def test_testcookie_kapisi_cozulup_istek_yineleniyor(site):
    AES = pytest.importorskip("Crypto.Cipher.AES")
    anahtar, iv = bytes(range(16)), bytes(range(16, 32))
    duz = bytes.fromhex("0123456789abcdeffedcba9876543210")
    sifreli = AES.new(anahtar, AES.MODE_CBC, iv).encrypt(duz)
    kapi = ('<html><body><script type="text/javascript" src="/aes.js"></script>'
            '<script>function toNumbers(d){var e=[];d.replace(/(..)/g,function(d)'
            '{e.push(parseInt(d,16))});return e} var a=toNumbers("%s"),b=toNumbers("%s"),'
            'c=toNumbers("%s");document.cookie="__test="+toHex(slowAES.decrypt(c,2,a,b))'
            '+"; path=/";location.href="%s/?s=naruto&i=1";</script></body></html>'
            % (anahtar.hex(), iv.hex(), sifreli.hex(), TABAN))
    oturum = site({arama_adresi("naruto"): [SahteYanit(202, kapi), ok("arama-bos.html")]})

    assert aa.search_asyaanimeleri("naruto") == []

    assert oturum.cookies.kayitlar == [("__test", duz.hex(), "asyaanimeleri.top")]
    assert len(oturum.cagrilar) == 2, "çerezle bir kez yeniden denenmeli"


def test_cozulemeyen_testcookie_acik_hata(site):
    site({arama_adresi("naruto"): SahteYanit(202, "<script>slowAES bozuk</script>")})
    with pytest.raises(aa.AsyaAnimeleriHatasi, match="testcookie"):
        aa.search_asyaanimeleri("naruto")


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt ve uçtan uca
# ─────────────────────────────────────────────────────────────────────────────
def test_kaynak_kayitta():
    kaynak = kayit.bul("Asya Animeleri")
    assert kaynak is not None
    for ad in ("asyaanimeleri", "ASYAANIMELERI", "Asya Animeleri"):
        assert kayit.bul(ad) is kaynak
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("Asya Animeleri", "Asya Animeleri", "AA", "#e17055", "ASYAANIMELERI")
    assert (kaynak.modul, kaynak.cli_kodu) == ("asyaanimeleri", "asyaanimeleri")
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari()
    assert kaynak in kayit.tarayici_kaynaklari()
    assert kayit.KAYNAKLAR.index(kaynak) > kayit.KAYNAKLAR.index(kayit.bul("Tranimaci")), "yeni kaynak eski kaynakların arkasına eklenmeli"


def test_kayit_uclari_modulun_fonksiyonlari():
    uclar = kayit.bul("asyaanimeleri").uclar()
    assert uclar.ara is aa.search_asyaanimeleri
    assert uclar.bolumler is aa.get_anime_episodes
    assert uclar.akislar is aa.get_episode_streams
    assert uclar.zengin_ara is aa.zengin_ara


def test_bolum_adresi_sayfanin_kendisi():
    kaynak = kayit.bul("asyaanimeleri")
    assert kaynak.bolum_adresi("one-piece-1161-bolum-izle") == \
        f"{TABAN}/one-piece-1161-bolum-izle/"


def test_alan_adi_ortam_degiskeniyle_degisiyor():
    """Site alan adı değiştirdi (.com park edildi); sürüm beklemeden taşınabilsin."""
    kod = ("import turkanime_api.sources.asyaanimeleri as a, turkanime_api.sources.kayit as k\n"
           "print(a.BASE_URL); print(k.bul('asyaanimeleri').bolum_adresi('x-1-bolum'))\n")
    ortam = {**os.environ, aa.ORTAM_ANAHTARI: "https://asyaanimeleri.yeni/ ",
             "PYTHONPATH": str(KOK)}
    r = subprocess.run([sys.executable, "-c", kod], cwd=KOK, env=ortam,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["https://asyaanimeleri.yeni",
                                "https://asyaanimeleri.yeni/x-1-bolum/"]


def test_kopru_bolum_nesneleri_kuruyor(site):
    """GUI köprüsü → kayıt → modül: bölüm nesneleri ve akış sağlayıcısı."""
    from turkanime_api.gui.qt import sources_bridge as sb

    site({seri_adresi("naruto"): SahteYanit(200, _seri(
              _li("naruto-2-bolum", "2", "Naruto 2.Bölüm"),
              _li("naruto-1-bolum", "1", "Naruto 1.Bölüm"))),
          bolum_adresi_("naruto-1-bolum"): ok("bolum-naruto-1-tirnaksiz.html")})

    bolumler = sb.fetch_episodes("Asya Animeleri", "naruto", "Naruto")

    assert [b["title"] for b in bolumler] == ["1. Bölüm", "2. Bölüm"]
    nesne = bolumler[0]["obj"]
    assert nesne.url == f"{TABAN}/naruto-1-bolum/"
    assert nesne._player_name == "ASYAANIMELERI"
    akislar = nesne._saglayici()(nesne.url)
    assert [(a["player"], a["referer"]) for a in akislar] == [("SIBNET", REFERER)]


def test_arama_motoru_gorselli_sonuc_ve_hata_metni(site):
    from turkanime_api.common.adapters import SearchEngine, ZenginKaynakAdaptoru

    site({arama_adresi("one piece"): ok("arama-one-piece.html"),
          arama_adresi("naruto"): ConnectionError("DNS çözülemedi")})
    motor = SearchEngine()
    # Diğer kaynaklar gerçek siteye giderdi; yalnız bu kaynak.
    motor.adapters = {"Asya Animeleri": motor.adapters["Asya Animeleri"]}
    assert isinstance(motor.adapters["Asya Animeleri"], ZenginKaynakAdaptoru)

    sonuc = motor.search_all_sources_rich("one piece")
    assert sonuc["Asya Animeleri"][0]["slug"] == "one-piece", "alakaya göre sıralı"
    assert sonuc["Asya Animeleri"][0]["image"]
    assert not sonuc.hatalar

    hatali = motor.search_all_sources_rich("naruto")
    assert hatali["Asya Animeleri"] == []
    assert "ulaşılamadı" in hatali.hatalar["Asya Animeleri"], \
        "ağ hatası 'sonuç yok' gibi görünmemeli"


def test_fixturelar_kucuk():
    """Fixture'lar kırpılmış kalsın (her biri ≤ 60 KB)."""
    for yol in FIXTURE.iterdir():
        assert yol.stat().st_size <= 60 * 1024, yol.name


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (varsayılan olarak atlanır; `pytest --network`)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = aa.search_asyaanimeleri("frieren", limit=10)
    assert ("sousou-no-frieren", "Sousou no Frieren") in sonuc, sonuc

    bolumler = aa.get_anime_episodes("sousou-no-frieren")
    assert len(bolumler) >= 28
    assert bolumler[0][1] == "1. Bölüm"

    akislar = aa.get_episode_streams(bolumler[-1][0])
    assert akislar, "son bölümün oynatılabilir aynası kalmamış"
    bilinen = {ad for _, ad in aa._OYNATICILAR}
    for akis in akislar:
        assert akis["url"].startswith("https://")
        assert akis["player"] in bilinen
        assert akis["referer"] == aa.BASE_URL + "/"
