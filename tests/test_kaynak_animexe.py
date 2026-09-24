"""Animexe kaynağı (`sources/animexe.py`): arama, bölümler, akışlar.

Testler ağa çıkmaz. `conftest.py`'deki ağ mandalı yalnızca Python soketlerini
kesiyor; curl_cffi kendi (libcurl) soketlerini açtığı için mandalı atlatır. Bu
yüzden modülün TEK oturum fabrikası (`_yeni_oturum`) her testte sahteyle
değiştiriliyor; sahte, yönlendirilmemiş bir adrese istek gelirse testi düşürür.

Fikstürler (`tests/fixtures/animexe/`) gerçek sayfaların kırpılmış hâli: kart
ve betik bölümleri olduğu gibi, geri kalan (menü, reklam, sohbet) atılmış.

Sınanan davranışlar:
* Arama kartları, boş sonuç ("Toplam 0 anime bulundu"), JSON öneri yedeği.
* Bölüm listesi izleme sırasında (sezon, bölüm sayısal), jenerik başlık
  tekrarlanmıyor, başlıktaki HTML kaçışları çözülüyor.
* Akışlar yalnızca fansub (tau-video) girdileri; vekil (`/stream/proxy?u=`)
  base64'ü çözülüp CDN adresi dönüyor, referer dolu. Anizium girdileri ve
  embed sayfası dönmüyor.
* Ölü CDN'ler yoklamayla ayıklanıyor; hiçbiri yaşamıyorsa açık hata.
* Hata yolları (404, engel, ağ hatası, tanınmayan sayfa, geçersiz kimlik)
  sessizce boş liste değil, Türkçe mesajlı istisna.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from turkanime_api.sources import animexe

FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "animexe"
SITE = animexe.BASE_URL


def fikstur(ad: str) -> str:
    return (FIKSTUR / ad).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sahte HTTP katmanı
# ─────────────────────────────────────────────────────────────────────────────
class SahteYanit:
    def __init__(self, status_code: int = 200, text: str = "",
                 headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"Content-Type": "text/html; charset=UTF-8"}
        self.kapandi = False

    def json(self):
        return json.loads(self.text)

    def close(self):
        self.kapandi = True


def html(ad: str, kod: int = 200) -> SahteYanit:
    return SahteYanit(kod, fikstur(ad))


def video(kod: int = 206) -> SahteYanit:
    return SahteYanit(kod, "", {"Content-Type": "video/mp4",
                                "Content-Range": "bytes 0-1/441343477"})


class SahteOturum:
    """URL → yanıt eşlemesiyle çalışan `Session` yerine geçen nesne.

    Değer bir `SahteYanit`, bir istisna (fırlatılır), bir liste (sırayla
    tüketilir; yeniden deneme testleri için) ya da `(params) -> yanıt`
    çağrılabilir olabilir. Her çağrı `cagrilar`'a yazılır. Zaman aşımı
    verilmeyen istek testi düşürür: timeout'suz istek sonsuza dek asılabilir.
    """

    def __init__(self):
        self.yollar: Dict[str, Any] = {}
        self.cagrilar: List[Dict[str, Any]] = []

    def get(self, url, params=None, headers=None, timeout=None, stream=False, **_kw):
        assert timeout, f"timeout'suz istek: {url}"
        self.cagrilar.append({"url": url, "params": dict(params or {}),
                              "headers": dict(headers or {}), "timeout": timeout,
                              "stream": stream})
        if url not in self.yollar:
            pytest.fail(f"beklenmeyen istek (ağa çıkılacaktı): {url}")
        deger = self.yollar[url]
        if isinstance(deger, list):
            deger = deger.pop(0) if len(deger) > 1 else deger[0]
        if callable(deger) and not isinstance(deger, SahteYanit):
            deger = deger(params or {})
        if isinstance(deger, BaseException):
            raise deger
        return deger

    def close(self):
        pass

    def istenen(self, onek: str) -> List[Dict[str, Any]]:
        return [c for c in self.cagrilar if c["url"].startswith(onek)]


@pytest.fixture(autouse=True)
def oturum(request, monkeypatch):
    """Modülün oturum fabrikasını sahteyle değiştir (ağ testi hariç)."""
    if request.node.get_closest_marker("network"):
        yield None
        return
    sahte = SahteOturum()
    monkeypatch.setattr(animexe, "_yeni_oturum", lambda: sahte)
    monkeypatch.setattr(animexe, "_paylasilan_oturum", None)
    yield sahte


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_kartlari_site_sirasiyla_donuyor(oturum):
    oturum.yollar[f"{SITE}/search"] = html("search-naruto.html")

    sonuc = animexe.search_animexe("naruto", limit=20)

    assert len(sonuc) == 16
    assert sonuc[:3] == [("naruto", "Naruto"),
                         ("naruto-shippuden-1739", "Naruto Shippuden"),
                         ("boruto-naruto-next-generations-10986",
                          "Boruto: Naruto Next Generations")]
    # Başlıktaki HTML kaçışı çözülüyor (&amp; → &).
    assert ("naruto-spin-off-rock-lee-his-ninja-pals-11011",
            "NARUTO Spin-Off: Rock Lee & His Ninja Pals") in sonuc
    assert len({s for s, _ in sonuc}) == 16
    # 16 < 24: ikinci sayfa istenmiyor.
    assert [c["params"] for c in oturum.cagrilar] == [{"q": "naruto"}]


def test_arama_limit_uygulaniyor(oturum):
    oturum.yollar[f"{SITE}/search"] = html("search-naruto.html")
    assert [s for s, _ in animexe.search_animexe("naruto", limit=2)] == \
        ["naruto", "naruto-shippuden-1739"]


def test_zengin_arama_kapak_gorselini_veriyor(oturum):
    oturum.yollar[f"{SITE}/search"] = html("search-naruto.html")
    ilk = animexe.search_animexe_zengin("naruto", limit=1)[0]
    assert ilk == {"slug": "naruto", "title": "Naruto",
                   "image": "https://image.tmdb.org/t/p/original/vauCEnR7CiyBDzRCeElKkCaXIYu.jpg"}


def test_arama_sonuc_yoksa_bos_liste(oturum):
    """Sitenin "Toplam 0 anime bulundu" cevabı boş sonuçtur, hata değil;
    öneri ucuna da gidilmez (aynı eşleştirmeyi kullanıyor)."""
    oturum.yollar[f"{SITE}/search"] = html("search-empty.html")

    assert animexe.search_animexe("kimetsu") == []
    assert len(oturum.cagrilar) == 1


def test_kisa_sorgu_istek_atmadan_bos(oturum):
    assert animexe.search_animexe(" a ") == []
    assert animexe.search_animexe("") == []
    assert oturum.cagrilar == []


def test_arama_sayfasi_taninmazsa_json_oneri_ucuna_dusuyor(oturum):
    """Kart da sayaç da yoksa tasarım değişmiş olabilir: JSON uca düş."""
    oturum.yollar[f"{SITE}/search"] = SahteYanit(200, "<html><body>yeni tasarım</body></html>")
    oturum.yollar[f"{SITE}/search/suggest"] = SahteYanit(200, fikstur("suggest-frieren.json"))

    sonuc = animexe.search_animexe_zengin("frieren")

    assert [(k["slug"], k["title"]) for k in sonuc] == [
        ("sousou-no-frieren", "Sousou no Frieren"),
        ("sousou-no-frieren-no-mahou", "Sousou no Frieren: ●● no Mahou")]
    assert sonuc[0]["image"].startswith("https://image.tmdb.org/")
    assert oturum.istenen(f"{SITE}/search/suggest")[0]["params"] == {"q": "frieren"}


def test_json_oneri_ucu_bossa_bos_liste(oturum):
    oturum.yollar[f"{SITE}/search"] = SahteYanit(200, "<html></html>")
    oturum.yollar[f"{SITE}/search/suggest"] = SahteYanit(200, fikstur("suggest-empty.json"))
    assert animexe.search_animexe("zzzz") == []


def test_arama_sonraki_sayfayi_istiyor(oturum, monkeypatch):
    """Sayfa dolu geldiyse (sayfa başına kart sayısı kadar) sonraki sayfa istenir."""
    monkeypatch.setattr(animexe, "SAYFA_BASINA", 16)
    sayfalar = {1: html("search-naruto.html"), 2: html("search-empty.html")}
    oturum.yollar[f"{SITE}/search"] = lambda p: sayfalar[int(p.get("page", 1))]

    assert len(animexe.search_animexe("naruto", limit=40)) == 16
    assert [c["params"] for c in oturum.cagrilar] == [{"q": "naruto"},
                                                      {"q": "naruto", "page": 2}]


@pytest.mark.parametrize("yanit", [
    SahteYanit(403, "Forbidden"),
    SahteYanit(429, "Too Many Requests"),
    SahteYanit(200, "<html><title>Just a moment...</title></html>"),
], ids=["403", "429", "challenge"])
def test_engel_bos_sonuc_sayilmiyor(oturum, yanit):
    oturum.yollar[f"{SITE}/search"] = yanit
    with pytest.raises(ConnectionError, match="engelledi"):
        animexe.search_animexe("naruto")
    assert len(oturum.cagrilar) == 1, "engel yeniden denenmemeli"


def test_gecici_5xx_bir_kez_yeniden_deneniyor(oturum):
    """Ölçülen: aramada tek seferlik 503, hemen ardından 200."""
    oturum.yollar[f"{SITE}/search"] = [SahteYanit(503, ""), html("search-naruto.html")]
    assert len(animexe.search_animexe("naruto")) == 16
    assert len(oturum.cagrilar) == 2


def test_ag_hatasi_surerse_acik_hata(oturum):
    oturum.yollar[f"{SITE}/anime/naruto"] = OSError("bağlantı sıfırlandı")
    with pytest.raises(ConnectionError, match="Animexe'ye ulaşılamadı.*bağlantı sıfırlandı"):
        animexe.get_anime_episodes("naruto")
    assert len(oturum.cagrilar) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_izleme_sirasinda(oturum):
    oturum.yollar[f"{SITE}/anime/jujutsu-kaisen-1992"] = html("anime-jujutsu-kaisen-1992.html")

    bolumler = animexe.get_anime_episodes("jujutsu-kaisen-1992")

    assert len(bolumler) == 35
    assert bolumler[0] == ("jujutsu-kaisen-1992/2/1", "2. Sezon 1. Bölüm - Gizli Envanter [No. 1]")
    assert bolumler[-1] == ("jujutsu-kaisen-1992/3/12", "3. Sezon 12. Bölüm - Sendai Kolonisi")
    kimlikler = [b for b, _ in bolumler]
    # Sayısal sıra: dizgi sıralaması "3/10"u "3/9"un önüne koyardı.
    assert kimlikler[-4:] == [f"jujutsu-kaisen-1992/3/{n}" for n in (9, 10, 11, 12)]
    assert len(set(kimlikler)) == 35


def test_bolum_sirasi_sayfa_sirasindan_bagimsiz(oturum):
    """Site kartları ters (ya da karışık) sırayla verse de izleme sırası aynı."""
    sayfa = fikstur("anime-jujutsu-kaisen-1992.html")
    parcalar = re.split(r'(?=<a href="https://animexe\.com/watch/)', sayfa)
    ters = parcalar[0] + "".join(reversed(parcalar[1:]))
    oturum.yollar[f"{SITE}/anime/jujutsu-kaisen-1992"] = SahteYanit(200, ters)

    duz = animexe.anime_sayfasini_ayristir("jujutsu-kaisen-1992", sayfa)
    assert animexe.get_anime_episodes("jujutsu-kaisen-1992") == duz


def test_tek_sezonda_sezon_yazilmiyor_kacislar_cozuluyor(oturum):
    oturum.yollar[f"{SITE}/anime/one-piece-13928"] = html("anime-one-piece-13928.html")

    bolumler = animexe.get_anime_episodes("one-piece-13928")

    # "İzlemeye Başla" düğmesi (class="ah-play") de /watch/…/1/1'e gidiyor;
    # bölüm sayılmamalı.
    assert [b for b, _ in bolumler] == ["one-piece-13928/1/1", "one-piece-13928/1/2",
                                        "one-piece-13928/1/1164", "one-piece-13928/1/1165"]
    assert bolumler[0][1] == "1. Bölüm - Ben Luffy! Korsan Kral Olacağım!"
    assert bolumler[-1][1] == \
        "1165. Bölüm - Dost Kadehleriyle Karşılama ve Loki'nin Peşindeki İstilacılar"


def test_jenerik_bolum_basligi_tekrarlanmiyor(oturum):
    """Naruto: başlıklar "N. Bölüm"; 220. bölümden sonra tek başına bir S2E3."""
    oturum.yollar[f"{SITE}/anime/naruto"] = html("anime-naruto.html")

    bolumler = animexe.get_anime_episodes("naruto")

    assert bolumler == [
        ("naruto/1/1", "1. Sezon 1. Bölüm"), ("naruto/1/2", "1. Sezon 2. Bölüm"),
        ("naruto/1/3", "1. Sezon 3. Bölüm"), ("naruto/1/218", "1. Sezon 218. Bölüm"),
        ("naruto/1/219", "1. Sezon 219. Bölüm"), ("naruto/1/220", "1. Sezon 220. Bölüm"),
        ("naruto/2/3", "2. Sezon 3. Bölüm"),
    ]


def test_bolumu_olmayan_anime_bos_liste(oturum):
    oturum.yollar[f"{SITE}/anime/yeni-seri"] = SahteYanit(
        200, '<h1 class="ah-title">Yeni Seri</h1><div class="ep-grid"></div>')
    assert animexe.get_anime_episodes("yeni-seri") == []


def test_olmayan_anime_acik_hata(oturum):
    oturum.yollar[f"{SITE}/anime/yok-boyle"] = SahteYanit(404, "<html>404</html>")
    with pytest.raises(LookupError, match="bulunamadı"):
        animexe.get_anime_episodes("yok-boyle")


def test_taninmayan_anime_sayfasi_acik_hata(oturum):
    oturum.yollar[f"{SITE}/anime/naruto"] = SahteYanit(200, "<html><body>bakım modu</body></html>")
    with pytest.raises(ValueError, match="çözümlenemedi"):
        animexe.get_anime_episodes("naruto")


@pytest.mark.parametrize("kimlik", ["../etc", "Naruto", "naruto/1", "", "naruto?x=1"])
def test_gecersiz_anime_kimligi_istek_atmiyor(oturum, kimlik):
    with pytest.raises(ValueError, match="Geçersiz"):
        animexe.get_anime_episodes(kimlik)
    assert oturum.cagrilar == []


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
FRIEREN_FANSUB = [
    ("HolySubs (480p)", "HolySubs",
     "https://misakina.asia/file/tau-video/45d215c7-76a2-4bcc-bada-5d3b63fb5484.mp4"),
    ("Anime Diyarı (480p)", "Anime Diyarı",
     "https://tsuriko-2.asia/file/tau-video/b7ab18f8-4cf4-47ce-8b25-cc1845b506df.mp4"),
    ("Anime Diyarı (720p)", "Anime Diyarı",
     "https://tsuriko-3.asia/file/tau-video/bdc479a1-ebae-4cbf-b3f7-97b4cc5143d6.mp4"),
    ("Anime Diyarı (1080p)", "Anime Diyarı",
     "https://tsuriko-1.asia/file/tau-video/74825fd4-3b42-40e9-a3f3-8c58a6cadfe7.mp4"),
    ("ShiroSubs (480p)", "ShiroSubs",
     "https://rhyzoku-2.asia/file/tau-video/4dbb9a7a-0acf-4179-ae8d-9b744bad1840.mp4"),
    ("GachaFlex Fansub (480p)", "GachaFlex Fansub",
     "https://uryuishida.asia/file/tau-video/1c6da435-7fe8-4a01-83ca-45592bf4e168.mp4"),
    ("YukiSubs (480p)", "YukiSubs",
     "https://zappy-net.store/file/tau-video/a1722fb9-0d2e-448c-a7fc-d1aad685966f.mp4"),
]
FRIEREN_1_1 = f"{SITE}/watch/sousou-no-frieren/1/1"


def test_akislar_yalnizca_fansub_ve_cozulmus_cdn_adresi(oturum):
    oturum.yollar[FRIEREN_1_1] = html("watch-sousou-no-frieren_1_1.html")

    akislar = animexe.get_episode_streams("sousou-no-frieren/1/1", yokla=False)

    assert [(a["label"], a["fansub"], a["url"]) for a in akislar] == FRIEREN_FANSUB
    for akis in akislar:
        assert akis["type"] == "direct"
        assert akis["referer"] == "https://animexe.com/"
        # Vekil değil, CDN'in kendisi: sitenin vekili Range'i yok sayıyor.
        assert "animexe.com" not in akis["url"]
    # Sayfadaki 6 Anizium girdisi (aniziumserver.*) dönmüyor.
    assert not any("aniziumserver" in a["url"] for a in akislar)


def test_anizium_girdileri_donmuyor(oturum):
    """One Piece 1165'te yalnızca Anizium aktarımı var: fansub akışı yok → []."""
    oturum.yollar[f"{SITE}/watch/one-piece-13928/1/1165"] = \
        html("watch-one-piece-13928_1_1165.html")
    sayfa = fikstur("watch-one-piece-13928_1_1165.html")
    assert '"source":"anizium"' in sayfa, "fikstür Anizium girdisi taşımalı"

    assert animexe.get_episode_streams("one-piece-13928/1/1165", yokla=False) == []
    assert animexe.get_episode_streams("one-piece-13928/1/1165") == []


def test_embed_atiliyor_sayisal_fansub_gosterilmiyor(oturum):
    """Naruto 100: "7 (480p)" adsız grup; tau-video.xyz embed'i CF 403 veriyor."""
    oturum.yollar[f"{SITE}/watch/naruto/1/100"] = html("watch-naruto_1_100.html")

    akislar = animexe.get_episode_streams("naruto/1/100", yokla=False)

    assert akislar == [
        {"url": "https://gojousatoru.icu/file/tau-video/80_1_100_480p.mp4",
         "label": "480p", "type": "direct", "referer": "https://animexe.com/"},
        {"url": "https://kiryuuin.icu/file/tau-video/80_1_100_720p.mp4",
         "label": "720p", "type": "direct", "referer": "https://animexe.com/"},
    ]


def test_yolsuz_tau_video_adresi(oturum):
    """irtau1.online `/file/tau-video/` yolu olmadan veriyor; konak sabit değil."""
    oturum.yollar[f"{SITE}/watch/shingeki-no-kyojin-movie-2-jiyuu-no-tsubasa/1/1"] = \
        html("watch-shingeki-no-kyojin-movie-2-jiyuu-no-tsubasa_1_1.html")

    akislar = animexe.get_episode_streams(
        "shingeki-no-kyojin-movie-2-jiyuu-no-tsubasa/1/1", yokla=False)

    assert [(a["fansub"], a["url"]) for a in akislar] == [
        ("AnimeciX", "https://irtau1.online/cd7765fa-55ba-47a1-bb32-e77f9e8348f3.mp4"),
        ("SeiCode", "https://irtau1.online/23386d73-c1d4-44df-8fba-78003d1990f4.mp4"),
    ]


def test_betik_tasinirsa_kaynak_sekmeleri_okunuyor(oturum):
    """`const VIDEO_SOURCES` kalkarsa aynı veri sekmelerin data-* özniteliklerinde."""
    sayfa = re.sub(r"const VIDEO_SOURCES\s*=.*?\n", "",
                   fikstur("watch-sousou-no-frieren_1_1.html"), flags=re.S)
    assert "VIDEO_SOURCES" not in sayfa
    oturum.yollar[FRIEREN_1_1] = SahteYanit(200, sayfa)

    akislar = animexe.get_episode_streams("sousou-no-frieren/1/1", yokla=False)

    # Sekmelerde "source" alanı yok; Anizium sekmeleri (hls, aniziumserver)
    # yine de ayıklanıyor.
    assert [(a["label"], a["fansub"], a["url"]) for a in akislar] == FRIEREN_FANSUB


def test_vekil_adresi_cozuluyor():
    assert animexe.vekili_coz(
        "https://animexe.com/stream/proxy?u=aHR0cHM6Ly94LmFuaXppdW1zZXJ2ZXIuc2l0ZS8y"
        "MDk4NjcvMi8xMC8xMDgwcC1vcmlnaW5hbC9tYXN0ZXIubTN1OA%3D%3D"
    ) == "https://x.aniziumserver.site/209867/2/10/1080p-original/master.m3u8"
    # Dolgu ("=") atılmış olsa da çözülür.
    assert animexe.vekili_coz(
        "https://animexe.com/cf-proxy?u=aHR0cHM6Ly9pcnRhdTEub25saW5lL2EubXA0"
    ) == "https://irtau1.online/a.mp4"
    assert animexe.vekili_coz(
        "https://animexe.com/hls-proxy.php?url=https%3A%2F%2Fcdn.example%2Fa.m3u8"
    ) == "https://cdn.example/a.m3u8"
    assert animexe.vekili_coz("https://tsuriko-1.asia/x.mp4") == "https://tsuriko-1.asia/x.mp4"
    with pytest.raises(ValueError, match="çözülemedi"):
        animexe.vekili_coz("https://animexe.com/stream/proxy?u=%%%bozuk")


def test_hic_cozulemeyen_vekil_acik_hata(oturum):
    sayfa = ('<script>\nconst VIDEO_SOURCES = [{"label":"X (480p)","url":'
             '"https:\\/\\/animexe.com\\/stream\\/proxy?u=%%%","type":"mp4","source":"animecix"}];\n'
             "</script>")
    oturum.yollar[FRIEREN_1_1] = SahteYanit(200, sayfa)
    with pytest.raises(ValueError, match="hiçbiri çözülemedi"):
        animexe.get_episode_streams("sousou-no-frieren/1/1", yokla=False)


def _cdn_yanitlari(oturum, olu: Dict[str, Any]) -> None:
    """Frieren fansub adresleri: `olu`'dakiler verilen yanıtı/hatayı, diğerleri 206."""
    for _etiket, _fansub, url in FRIEREN_FANSUB:
        oturum.yollar[url] = olu.get(url.split("/")[2], video())


def test_yoklama_olu_cdnleri_ayikliyor(oturum):
    oturum.yollar[FRIEREN_1_1] = html("watch-sousou-no-frieren_1_1.html")
    _cdn_yanitlari(oturum, {
        "misakina.asia": SahteYanit(502, "Bad Gateway", {"Content-Type": "text/plain"}),
        "uryuishida.asia": SahteYanit(200, "<html>hata</html>"),     # 200 ama video değil
        "zappy-net.store": ConnectionError("DNS çözülemedi"),
    })

    akislar = animexe.get_episode_streams("sousou-no-frieren/1/1")

    assert [a["label"] for a in akislar] == [
        "Anime Diyarı (480p)", "Anime Diyarı (720p)", "Anime Diyarı (1080p)", "ShiroSubs (480p)"]
    yoklamalar = [c for c in oturum.cagrilar if not c["url"].startswith(SITE)]
    assert len(yoklamalar) == 7
    for cagri in yoklamalar:
        assert cagri["headers"]["Range"] == "bytes=0-1"
        assert cagri["headers"]["Referer"] == "https://animexe.com/"
        assert cagri["timeout"] == animexe.YOKLAMA_TIMEOUT
        assert cagri["stream"] is True, "Range'i yok sayan CDN bütün dosyayı yollar"


def test_hicbir_akis_yasamiyorsa_acik_hata(oturum):
    oturum.yollar[FRIEREN_1_1] = html("watch-sousou-no-frieren_1_1.html")
    _cdn_yanitlari(oturum, {u.split("/")[2]: SahteYanit(502, "", {"Content-Type": "text/plain"})
                            for _e, _f, u in FRIEREN_FANSUB})

    with pytest.raises(ConnectionError, match=r"7 fansub akışının hiçbiri yanıt vermiyor") as hata:
        animexe.get_episode_streams("sousou-no-frieren/1/1")
    assert "tsuriko-1.asia (HTTP 502)" in str(hata.value)


def test_olmayan_bolum_acik_hata(oturum):
    oturum.yollar[f"{SITE}/watch/naruto/1/999"] = SahteYanit(404, "<html>404</html>")
    with pytest.raises(LookupError, match="bu bölüm yok"):
        animexe.get_episode_streams("naruto/1/999")


def test_taninmayan_izleme_sayfasi_acik_hata(oturum):
    oturum.yollar[f"{SITE}/watch/naruto/1/1"] = SahteYanit(200, "<html><body>yeni oynatıcı</body></html>")
    with pytest.raises(ValueError, match="kaynak listesi"):
        animexe.get_episode_streams("naruto/1/1")


def test_izleme_sayfasi_engeli_acik_hata(oturum):
    oturum.yollar[f"{SITE}/watch/naruto/1/1"] = SahteYanit(403, "Forbidden")
    with pytest.raises(ConnectionError, match="engelledi"):
        animexe.get_episode_streams("naruto/1/1")


@pytest.mark.parametrize("kimlik", ["naruto/1/x", "../etc/passwd", "naruto/1/1?x=1",
                                    "naruto", "naruto/1/1/2", "Naruto/1/1"])
def test_gecersiz_bolum_kimligi_istek_atmiyor(oturum, kimlik):
    with pytest.raises(ValueError, match="Geçersiz Animexe bölüm kimliği"):
        animexe.get_episode_streams(kimlik)
    assert oturum.cagrilar == []


# ─────────────────────────────────────────────────────────────────────────────
# Kayıt
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_animexe_kaynagini_sunuyor():
    kayit = pytest.importorskip("turkanime_api.sources.kayit")

    kaynak = kayit.bul("Animexe")
    assert kaynak is not None, "Animexe kaynak kaydında (KAYNAKLAR) yok"
    assert kayit.bul("animexe") is kaynak and kayit.bul("ANIMEXE") is kaynak
    assert (kaynak.etiket, kaynak.kisaltma, kaynak.oynatici, kaynak.modul, kaynak.cli_kodu) == \
        ("Animexe", "AX", "ANIMEXE", "animexe", "animexe")
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    # Bölüm kimliği "slug/sezon/bölüm"; nesnenin url'si izleme sayfası.
    assert kaynak.bolum_adresi("naruto/1/6") == "https://animexe.com/watch/naruto/1/6"

    uclar = kaynak.uclar()
    assert uclar.ara is animexe.search_animexe
    assert uclar.bolumler is animexe.get_anime_episodes
    assert uclar.akislar is animexe.get_episode_streams
    # Kapaklı arama isteğe bağlı; bağlandıysa modülün kendi ucu olmalı.
    assert uclar.zengin_ara in (None, animexe.search_animexe_zengin)


# ─────────────────────────────────────────────────────────────────────────────
# Canlı duman testi (yalnızca --network ile)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = animexe.search_animexe("frieren")
    assert "sousou-no-frieren" in [s for s, _ in sonuc]

    bolumler = animexe.get_anime_episodes("sousou-no-frieren")
    assert len(bolumler) >= 28
    assert bolumler[0][0] == "sousou-no-frieren/1/1"

    akislar = animexe.get_episode_streams(bolumler[0][0])
    assert akislar, "yoklamadan sağ çıkan fansub akışı yok"
    for akis in akislar:
        assert akis["url"].startswith("https://")
        assert "aniziumserver" not in akis["url"] and "animexe.com" not in akis["url"]
        assert akis["referer"] == "https://animexe.com/"
