"""Animeler.pw kaynağı (`sources/animeler.py`): ayrıştırma, akış çözümü, kayıt.

Hiçbir test ağa çıkmaz. DİKKAT: `conftest.py`'nin ağ mandalı curl_cffi'yi
YAKALAMIYOR (libcurl kendi soketini açıyor); bu yüzden modülün oturum fabrikası
(`_yeni_oturum`) ve FirePlayer istemcisinin POST'u (`anizle._http_post`) her
testte sahteleniyor — sahtesi olmayan istek testi düşürür.

Fikstürler `tests/fixtures/animeler/` altında: sitenin 2026-09'daki GERÇEK
yanıtlarından kırpılmış parçalar (baş etiketleri + ilgili betik/HTML; uzun
listelerin ortası atıldı, yapı korundu).

Sınananlar:
* Arama: ajax JSON'u, boş sonuç, "Comic" ayıklama, 8'den fazlası için /filter
  ile tamamlama, yetişkin türlerin süzülmesi, hata/engel/yeniden deneme.
* Bölümler: izleme sırası, birleşik bölüm kimliği ("bolum-215-216"), 404,
  yapısı değişmiş sayfa.
* Akışlar: CSRF'li POST'un sayfayla AYNI oturumdan ve sayfanın jetonuyla
  gitmesi, embed → barındırıcı, FirePlayer'ın Anizle koduyla çözülmesi (HLS +
  referer), CryptoJS aynaları, olmayan bölümün kanonik adresle yakalanması,
  sıralama ve hata yolları.
* Kayıt: kaynak kayıtta, türetilen listelerde ve köprüde.
"""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import pytest

from turkanime_api.sources import anizle, animeler, kayit
from turkanime_api.sources.animeler import AnimelerHatasi

FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "animeler"
TABAN = animeler.BASE_URL


def fikstur(ad: str) -> str:
    return (FIKSTUR / ad).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Sahte site
# ─────────────────────────────────────────────────────────────────────────────
class Yanit:
    """curl_cffi/requests yanıtının kullanılan yüzü."""

    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def json(self) -> Any:
        return json.loads(self.text)


# Tek sayfalık, kartsız /filter yanıtı (yetişkin türlerinde eşleşme yok).
BOS_FILTRE = ('<div class="filter-results"><p class="filter-results-count">0 anime bulundu</p>'
              '<div class="anime-grid-modern"></div><div class="pagination-wrapper"></div></div>')

Isleyici = Callable[[Dict[str, Any]], Any]


class Site:
    """Adres → yanıt tablosu; gelen her isteği (oturum kimliğiyle) kaydeder.

    Tabloda olmayan istek AssertionError: test beklemediği bir ağ çağrısını
    fark etsin. Her istekte `timeout` zorunlu (modülün sözleşmesi).
    """

    def __init__(self):
        self.yollar: Dict[tuple, Any] = {}
        self.istekler: List[tuple] = []
        self.yetiskin_sayfalari: Dict[str, List[str]] = {}
        self.arama_sayfalari: List[str] = []
        self._sayac = itertools.count(1)

    def ekle(self, yontem: str, adres: str, yanit: Any) -> None:
        self.yollar[(yontem, adres)] = yanit

    def cevapla(self, oturum_no: int, yontem: str, adres: str, kw: Dict[str, Any]) -> Any:
        assert kw.get("timeout"), f"zaman aşımsız istek: {yontem} {adres}"
        self.istekler.append((oturum_no, yontem, adres, kw))
        if yontem == "GET" and adres == f"{TABAN}/filter":
            return self._filtre(kw.get("params") or {})
        yanit = self.yollar.get((yontem, adres))
        if yanit is None:
            raise AssertionError(f"beklenmeyen istek: {yontem} {adres}")
        if isinstance(yanit, Exception):
            raise yanit
        return yanit(kw) if callable(yanit) else yanit

    def _filtre(self, params: Dict[str, Any]) -> Yanit:
        sayfa = int(params.get("page") or 1)
        if "genre[]" in params:
            sayfalar = self.yetiskin_sayfalari.get(params["genre[]"], [BOS_FILTRE])
        else:
            sayfalar = self.arama_sayfalari
        if sayfa > len(sayfalar):
            return Yanit(BOS_FILTRE)
        icerik = sayfalar[sayfa - 1]
        return icerik if isinstance(icerik, Yanit) else Yanit(icerik)

    def oturum(self) -> "Oturum":
        return Oturum(self, next(self._sayac))

    def cagrilar(self, yontem: Optional[str] = None, adres_icerir: str = "") -> List[tuple]:
        return [i for i in self.istekler
                if (yontem is None or i[1] == yontem) and adres_icerir in i[2]]


class Oturum:
    def __init__(self, site: Site, no: int):
        self.site, self.no = site, no

    def get(self, adres: str, **kw: Any) -> Any:
        return self.site.cevapla(self.no, "GET", adres, kw)

    def post(self, adres: str, **kw: Any) -> Any:
        return self.site.cevapla(self.no, "POST", adres, kw)


class FirePlayer:
    """`anizle._http_post` sahtesi: FirePlayer `do=getVideo` yanıtları."""

    def __init__(self):
        self.yanitlar: Dict[str, Callable[[Dict[str, Any]], Optional[Yanit]]] = {}
        self.cagrilar: List[Dict[str, Any]] = []

    def __call__(self, url: str, timeout: int = 60, headers: Optional[Dict[str, str]] = None,
                 data: Optional[Dict] = None) -> Optional[Yanit]:
        self.cagrilar.append({"url": url, "timeout": timeout, "headers": headers or {},
                              "data": data})
        sayfa = url.split("?do=getVideo")[0].split("&do=getVideo")[0]
        isleyici = self.yanitlar.get(sayfa)
        if isleyici is None:
            raise AssertionError(f"beklenmeyen FirePlayer isteği: {url}")
        return isleyici(data or {})


@pytest.fixture
def site(monkeypatch) -> Site:
    """Modülün bütün HTTP'sini sahte siteye bağla; önbellekleri sıfırla."""
    sahte = Site()
    monkeypatch.setattr(animeler, "_yeni_oturum", sahte.oturum)
    animeler.onbellegi_sifirla()
    yield sahte
    animeler.onbellegi_sifirla()


@pytest.fixture
def fireplayer(monkeypatch) -> FirePlayer:
    sahte = FirePlayer()
    monkeypatch.setattr(anizle, "_http_post", sahte)
    return sahte


def ajax_ekle(site: Site, sorgu_yaniti: Dict[str, str]) -> None:
    """`/ajax/search`: sorgu → JSON gövdesi."""
    def isleyici(kw):
        q = (kw.get("params") or {}).get("q")
        if q not in sorgu_yaniti:
            raise AssertionError(f"beklenmeyen arama: {q!r}")
        return Yanit(sorgu_yaniti[q])
    site.ekle("GET", f"{TABAN}/ajax/search", isleyici)


# ─────────────────────────────────────────────────────────────────────────────
# 1) Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_ajax_sonuclarini_alaka_sirasiyla_donduruyor(site):
    ajax_ekle(site, {"frieren": fikstur("ajax-search-frieren.json")})

    sonuc = animeler.search_animeler("frieren", limit=10)

    assert sonuc == [
        ("sousou-no-frieren", "Sousou no Frieren"),
        ("sousou-no-frieren-2nd-season", "Sousou no Frieren 2nd Season"),
        ("sousou-no-frieren-no-mahou", "Sousou no Frieren: ●● no Mahou"),
    ]
    arama = site.cagrilar("GET", "/ajax/search")[0][3]
    assert arama["params"] == {"q": "frieren"}
    assert arama["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert arama["timeout"] == animeler.SEARCH_TIMEOUT
    # total == dönen sayı: /filter ile tamamlamaya gerek yok.
    assert not [c for c in site.cagrilar("GET", "/filter") if "search" in c[3]["params"]]


def test_arama_sonuc_yoksa_bos_liste_ve_ek_istek_yok(site):
    ajax_ekle(site, {"zzqxnotexist": fikstur("ajax-search-empty.json")})

    assert animeler.search_animeler("zzqxnotexist") == []
    assert len(site.istekler) == 1, "boş sonuçta yetişkin listesi/filtre istenmemeli"


def test_kisa_sorgu_istek_atmiyor(site):
    assert animeler.search_animeler("a") == []
    assert animeler.search_animeler("   ") == []
    assert site.istekler == []


def test_arama_comic_atiliyor_donghua_kaliyor(site):
    naruto = json.loads(fikstur("ajax-search-naruto.json"))
    naruto["results"][1]["category"] = "Comic"
    naruto["total"] = len(naruto["results"])
    ajax_ekle(site, {"naruto": json.dumps(naruto),
                     "xian ni": fikstur("ajax-search-xianni-donghua.json")})

    sluglar = [s for s, _ in animeler.search_animeler("naruto", limit=20)]
    assert naruto["results"][1]["slug"] not in sluglar
    assert len(sluglar) == len(naruto["results"]) - 1

    donghua = animeler.search_animeler("xian ni")
    assert donghua and all(slug for slug, _ in donghua)


def test_arama_sekizden_fazlasini_filtre_sayfasiyla_tamamliyor(site):
    ajax = json.loads(fikstur("ajax-search-naruto.json"))
    assert ajax["total"] > len(ajax["results"]) == 8
    ajax_ekle(site, {"naruto": fikstur("ajax-search-naruto.json")})
    site.arama_sayfalari = [fikstur("filter-naruto.html")]

    sonuc = animeler.search_animeler("naruto", limit=12)

    assert len(sonuc) == 12
    # İlk 8 sitenin alaka sırasıyla ajax'tan; tamamlama yinelenen içermiyor.
    assert [s for s, _ in sonuc[:8]] == [r["slug"] for r in ajax["results"]]
    assert len({s for s, _ in sonuc}) == 12
    filtre = [c for c in site.cagrilar("GET", "/filter") if "search" in c[3]["params"]]
    assert filtre[0][3]["params"] == {"search": "naruto", "page": 1}


def test_filtre_kartlari_ve_sayfalama_ayristiriliyor():
    kartlar = animeler.filtre_kartlarini_ayristir(fikstur("filter-onepiece-page1.html"))
    assert len(kartlar) == 8
    assert kartlar[1] == ("one-piece-adventure-of-nebulandia", "One Piece: Adventure of Nebulandia",
                          ["Aksiyon", "Macera", "Fantezi"])
    assert all(slug and baslik and turler for slug, baslik, turler in kartlar)
    assert animeler.sonraki_sayfa_var(fikstur("filter-onepiece-page1.html"))
    assert not animeler.sonraki_sayfa_var(fikstur("filter-naruto.html"))


def _kartin_turlerini_degistir(sayfa: str, slug: str, turler: str) -> str:
    """Filtre sayfasında tek bir kartın tür satırını değiştir (gerçek yapı korunur)."""
    bas = sayfa.index(f'/{slug}" class="anime-card-modern">')
    tur = re.compile(r'<div class="tooltip-tags">[^<]*</div>').search(sayfa, bas)
    return sayfa[:tur.start()] + f'<div class="tooltip-tags">{turler}</div>' + sayfa[tur.end():]


def _tur_istekleri(site: Site) -> int:
    return len([c for c in site.cagrilar("GET", "/filter") if "genre[]" in c[3]["params"]])


def test_arama_yetiskin_turleri_suzuyor_ve_liste_onbellekte(site):
    """Yetişkin türündeki anime ajax sonucundan da, filtre kartından da atılır.

    Ajax araması tür vermiyor: tür sayfalarındaki slug'lar bir kez toplanıyor.
    Tamamlama kartları türünü kendisi taşıyor, o da ayrıca denetleniyor.
    """
    ajax = json.loads(fikstur("ajax-search-naruto.json"))
    ajax_ekle(site, {"naruto": fikstur("ajax-search-naruto.json")})
    yasakli = ajax["results"][2]["slug"]
    # "Hentai" tür sayfası: gerçek kart yapısı, ilk kartın slug'ı yasaklıya çevrilmiş.
    tur_sayfasi = fikstur("filter-onepiece-page1.html").replace('rel="next"', "")
    ilk = animeler.filtre_kartlarini_ayristir(tur_sayfasi)[0][0]
    site.yetiskin_sayfalari["Hentai"] = [tur_sayfasi.replace(f"/{ilk}\"", f"/{yasakli}\"")]
    # Tamamlama sayfasında ajax'ta olmayan bir kartın türü "Erotica".
    filtre = fikstur("filter-naruto.html")
    ajax_sluglari = {r["slug"] for r in ajax["results"]}
    erotik = next(s for s, _b, _t in animeler.filtre_kartlarini_ayristir(filtre)
                  if s not in ajax_sluglari)
    site.arama_sayfalari = [_kartin_turlerini_degistir(filtre, erotik, "Dram, Erotica")]

    sonuc = [s for s, _ in animeler.search_animeler("naruto", limit=30)]

    assert yasakli not in sonuc
    assert erotik not in sonuc
    assert len(sonuc) == len(ajax_sluglari | {s for s, _b, _t in
                                             animeler.filtre_kartlarini_ayristir(filtre)}) - 2
    istek = _tur_istekleri(site)
    assert istek == len(animeler.YETISKIN_TURLERI)
    animeler.search_animeler("naruto", limit=30)
    assert _tur_istekleri(site) == istek, "yetişkin listesi önbellekten gelmeli"
    assert all(c[3]["timeout"] <= animeler.SEARCH_TIMEOUT for c in site.cagrilar("GET", "/filter"))


def test_yetiskin_listesi_alinamazsa_arama_yine_donuyor(site):
    ajax_ekle(site, {"frieren": fikstur("ajax-search-frieren.json")})
    site.yetiskin_sayfalari["Hentai"] = [Yanit("bakım", 500)]

    assert len(animeler.search_animeler("frieren")) == 3


def test_arama_ag_hatasi_aciklamali_hata_verir(site):
    site.ekle("GET", f"{TABAN}/ajax/search", ConnectionError("bağlantı reddedildi"))

    with pytest.raises(AnimelerHatasi, match="ulaşılamadı.*arama"):
        animeler.search_animeler("naruto")
    assert len(site.istekler) == 2, "hızlı düşen istek bir kez yeniden denenmeli"


def test_arama_hizli_502_bir_kez_yeniden_deneniyor(site):
    yanitlar = iter([Yanit("<html>502 Bad Gateway</html>", 502),
                     Yanit(fikstur("ajax-search-frieren.json"))])
    site.ekle("GET", f"{TABAN}/ajax/search", lambda kw: next(yanitlar))

    assert len(animeler.search_animeler("frieren")) == 3


def test_arama_http_hatasi_durum_koduyla_yukselir(site):
    site.ekle("GET", f"{TABAN}/ajax/search", Yanit("hata", 500))

    with pytest.raises(AnimelerHatasi) as bilgi:
        animeler.search_animeler("frieren")
    assert bilgi.value.status_code == 500 and "HTTP 500" in str(bilgi.value)


def test_cloudflare_engeli_bos_sonuc_sayilmiyor(site):
    site.ekle("GET", f"{TABAN}/ajax/search",
              Yanit("<html><title>Just a moment...</title></html>", 403))

    with pytest.raises(AnimelerHatasi, match="engelledi") as bilgi:
        animeler.search_animeler("frieren")
    assert bilgi.value.status_code == 403


def test_arama_ek_sayfa_duserse_ilk_sonuclar_donuyor(site):
    ajax_ekle(site, {"naruto": fikstur("ajax-search-naruto.json")})
    site.arama_sayfalari = [Yanit("bakımda", 500)]

    assert len(animeler.search_animeler("naruto", limit=12)) == 8


def test_bozuk_arama_yaniti_yapi_hatasi_verir(site):
    site.ekle("GET", f"{TABAN}/ajax/search", Yanit("<html>bakım</html>"))
    with pytest.raises(AnimelerHatasi, match="JSON"):
        animeler.search_animeler("frieren")


# ─────────────────────────────────────────────────────────────────────────────
# 2) Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_izleme_sirasiyla(site):
    site.ekle("GET", f"{TABAN}/sousou-no-frieren", Yanit(fikstur("anime-sousou-no-frieren.html")))

    bolumler = animeler.get_anime_episodes("sousou-no-frieren")

    assert len(bolumler) == 28
    assert bolumler[0] == ("sousou-no-frieren/bolum-1", "1. Bölüm")
    assert bolumler[-1] == ("sousou-no-frieren/bolum-28", "28. Bölüm")
    assert [b[0] for b in bolumler] == [f"sousou-no-frieren/bolum-{i}" for i in range(1, 29)]
    assert site.istekler[0][3]["timeout"] == animeler.PAGE_TIMEOUT


def test_bolumler_birlesik_bolum_kimligi_ve_tam_liste(site):
    # Görünen ızgara 100'de kesiliyor; JS dizisi tamamını taşıyor.
    site.ekle("GET", f"{TABAN}/naruto", Yanit(fikstur("anime-naruto.html")))
    site.ekle("GET", f"{TABAN}/one-piece", Yanit(fikstur("anime-one-piece.html")))

    assert len(animeler.get_anime_episodes("naruto")) == 220

    op = animeler.get_anime_episodes("one-piece")
    kimlikler = [k for k, _ in op]
    i = kimlikler.index("one-piece/bolum-215-216")
    assert kimlikler[i - 1] == "one-piece/bolum-214"
    assert kimlikler[i + 1] == "one-piece/bolum-217"
    assert op[i][1] == "215-216. Bölüm"
    assert kimlikler[-1] == "one-piece/bolum-1161"


def test_bolumler_sayfa_sirasi_karisiksa_siralaniyor(site):
    sayfa = fikstur("anime-sousou-no-frieren.html")
    m = re.search(r"(var\s+allEpisodesData\s*=\s*\[)(.*?)(\];)", sayfa, re.S)
    girdiler = re.findall(r"\{[^{}]*\}", m.group(2))
    ters = sayfa[:m.start(2)] + ",\n".join(reversed(girdiler)) + sayfa[m.end(2):]
    site.ekle("GET", f"{TABAN}/sousou-no-frieren", Yanit(ters))

    bolumler = animeler.get_anime_episodes("sousou-no-frieren")
    assert [b[0] for b in bolumler][:3] == [f"sousou-no-frieren/bolum-{i}" for i in (1, 2, 3)]


def test_bolumler_tam_adresle_de_calisiyor(site):
    site.ekle("GET", f"{TABAN}/one-piece-film-red", Yanit(fikstur("anime-one-piece-film-red.html")))
    assert animeler.get_anime_episodes(f"{TABAN}/one-piece-film-red/") == [
        ("one-piece-film-red/bolum-1", "1. Bölüm")]


def test_olmayan_anime_404_hatasi(site):
    site.ekle("GET", f"{TABAN}/boyle-bir-anime-yok", Yanit(fikstur("anime-404.html"), 404))

    with pytest.raises(AnimelerHatasi, match="boyle-bir-anime-yok.*404") as bilgi:
        animeler.get_anime_episodes("boyle-bir-anime-yok")
    assert bilgi.value.status_code == 404


def test_yapisi_degismis_anime_sayfasi_hata_verir(site):
    site.ekle("GET", f"{TABAN}/naruto", Yanit("<html><body>yeni tasarım</body></html>"))
    with pytest.raises(AnimelerHatasi, match="allEpisodesData"):
        animeler.get_anime_episodes("naruto")


def test_bicimi_degismis_bolum_dizisi_bos_liste_sayilmiyor():
    sayfa = fikstur("anime-sousou-no-frieren.html")
    # Anahtarlar tırnaklı olsa (biçim değişikliği) kalıp tutmaz: sessizce "0 bölüm" değil.
    tirnakli = re.sub(r"\{ id: (\d+), url:", r'{ "id": \1, "url":', sayfa)
    with pytest.raises(AnimelerHatasi, match="biçim"):
        animeler.bolum_listesini_ayristir(tirnakli)
    # Adres biçimi değişirse de.
    with pytest.raises(AnimelerHatasi, match="biçim"):
        animeler.bolum_listesini_ayristir(sayfa.replace("/bolum-", "/izle-"))
    # Boş dizi: henüz bölümü olmayan anime.
    bos = re.sub(r"(var\s+allEpisodesData\s*=\s*\[).*?(\];)", r"\1\2", sayfa, flags=re.S)
    assert animeler.bolum_listesini_ayristir(bos) == []


def test_bolum_listesi_ag_hatasi_yukselir(site):
    site.ekle("GET", f"{TABAN}/naruto", TimeoutError("okuma zaman aşımı"))
    with pytest.raises(AnimelerHatasi, match="ulaşılamadı"):
        animeler.get_anime_episodes("naruto")


# ─────────────────────────────────────────────────────────────────────────────
# 3) Akışlar
# ─────────────────────────────────────────────────────────────────────────────
FRIEREN_E1 = f"{TABAN}/sousou-no-frieren/bolum-1"
FRIEREN_EMBEDLER = {           # kaynak id → embed fikstürü
    18637: "embed-frieren-e1-default.html",
    271742: "embed-frieren-e1-okru.html",
    271739: "embed-frieren-e1-mailru1.html",
    271743: "embed-frieren-e1-mailru2.html",
    271740: "embed-frieren-e1-gdrive1.html",
    271741: "embed-frieren-e1-gdrive2.html",
}
FRIEREN_FIREPLAYER = "https://play.animeler.pw/fireplayer/video/8db66f2fc805310b86589bd1aeaa2cd7"


def frieren_kur(site: Site, fireplayer: FirePlayer, sayfa: Optional[str] = None,
                kaynak_yaniti: Optional[Isleyici] = None) -> None:
    """Frieren 1. bölümün bütün uçlarını sahte siteye yerleştir."""
    site.ekle("GET", FRIEREN_E1, Yanit(sayfa or fikstur("episode-sousou-no-frieren-bolum-1.html")))
    adresler = json.loads(fikstur("get-source-url-frieren-e1.json"))

    def kaynak_adresi(kw):
        return Yanit(json.dumps(adresler[str(kw["data"]["source_id"])]))

    site.ekle("POST", f"{TABAN}/ajax/get-source-url", kaynak_yaniti or kaynak_adresi)
    for kimlik, dosya in FRIEREN_EMBEDLER.items():
        site.ekle("GET", adresler[str(kimlik)]["url"], Yanit(fikstur(dosya)))
    tum = json.loads(fikstur("fireplayer-getvideo-frieren-e1-all.json"))
    fireplayer.yanitlar[FRIEREN_FIREPLAYER] = lambda data: Yanit(tum[data.get("s") or "s0"])


def test_frieren_akislari_cozuluyor_ve_siralaniyor(site, fireplayer):
    frieren_kur(site, fireplayer)

    akislar = animeler.get_episode_streams("sousou-no-frieren/bolum-1")

    assert [a["url"] for a in akislar] == [
        "https://video.sibnet.ru/shell.php?videoid=5263894",
        "https://my.mail.ru/video/embed/7571497515682370823",
        "https://my.mail.ru/video/embed/7571497515682370821",
        "https://drive.google.com/file/d/1949KZBubCLsI_hwq77a1uFK6f4Utqvaz/preview",
        "https://drive.google.com/file/d/1twpqdSXABY3NmlbmVTTsowHGDrEsF4od/preview",
        # ok.ru yt-dlp'de düşüyor (ölçüldü): en sonda; eski alan adı düzeltildi.
        "https://ok.ru/videoembed/6661536942827",
        "https://ok.ru/videoembed/7856258943574",
    ], "voe atılmalı, sıra ölçülmüş güvenilirliğe göre olmalı"
    assert akislar[0] == {
        "url": "https://video.sibnet.ru/shell.php?videoid=5263894",
        "label": "Varsayılan - Varsayılan(Toplu) - Sibnet",
        "type": "iframe", "fansub": "Varsayılan", "player": "SIBNET",
    }
    assert [a["player"] for a in akislar] == ["SIBNET", "MAIL", "MAIL", "GDRIVE", "GDRIVE",
                                              "ODNOKLASSNIKI", "ODNOKLASSNIKI"]
    assert all("referer" not in a for a in akislar), "yt-dlp barındırıcıları referer istemiyor"
    assert all(a["fansub"] == "Varsayılan" for a in akislar)


def test_csrf_postu_sayfanin_jetonuyla_ayni_oturumdan_gidiyor(site, fireplayer):
    frieren_kur(site, fireplayer)

    animeler.get_episode_streams("sousou-no-frieren/bolum-1")

    sayfa_istegi = site.cagrilar("GET", "/sousou-no-frieren/bolum-1")[0]
    postlar = site.cagrilar("POST", "/ajax/get-source-url")
    token = animeler.bolum_sayfasini_ayristir(fikstur("episode-sousou-no-frieren-bolum-1.html")).token
    assert token
    # Varsayılan kaynağın (18637) embed'i sayfada hazır: POST'u yok.
    assert sorted(p[3]["data"]["source_id"] for p in postlar) == \
        sorted(str(k) for k in FRIEREN_EMBEDLER if k != 18637)
    assert {p[3]["data"]["_token"] for p in postlar} == {token}
    assert {p[0] for p in postlar} == {sayfa_istegi[0]}, "POST sayfayı çeken oturumdan gitmeli"
    assert all(p[3]["headers"]["Referer"] == FRIEREN_E1 for p in postlar)
    # Yavaş bölüm sayfası uzun süre alıyor; küçük uçlar kısa.
    assert sayfa_istegi[3]["timeout"] == animeler.EPISODE_TIMEOUT == 90
    assert all(i[3]["timeout"] == animeler.PLAYER_TIMEOUT for i in site.istekler
               if i is not sayfa_istegi)


def test_fireplayer_anizle_koduyla_cozuluyor(site, fireplayer):
    frieren_kur(site, fireplayer)

    animeler.get_episode_streams("sousou-no-frieren/bolum-1")

    md5 = FRIEREN_FIREPLAYER.rsplit("/", 1)[-1]
    assert [c["url"] for c in fireplayer.cagrilar] == [f"{FRIEREN_FIREPLAYER}?do=getVideo"] * 3
    assert [c["data"]["s"] for c in fireplayer.cagrilar] == ["", "s2", "s3"]
    assert all(c["data"]["hash"] == md5 and c["data"]["r"] == TABAN + "/"
               for c in fireplayer.cagrilar)
    assert fireplayer.cagrilar[0]["headers"]["Origin"] == "https://play.animeler.pw"


ONE_PIECE_1161 = f"{TABAN}/one-piece/bolum-1161"
MUGEN = "https://anizmplayer.com/video/0e5cea59ae769ec1186d3625fdcf249b"


def embed_sayfasi(hedef: str) -> str:
    """Gerçek embed sayfasının iskeleti, iframe'i `hedef`e çevrilmiş."""
    return fikstur("embed-frieren-e1-okru.html").replace(
        "https://odnoklassniki.ru/videoembed/7856258943574", hedef)


def one_piece_kur(site: Site, fireplayer: FirePlayer) -> None:
    """One Piece 1161: "Mugen" (anizmplayer FirePlayer) + loveulikeido aynası."""
    cozum = json.loads(fikstur("resolved-onepiece-e1161.json"))
    site.ekle("GET", ONE_PIECE_1161, Yanit(fikstur("episode-one-piece-bolum-1161.html")))
    site.ekle("POST", f"{TABAN}/ajax/get-source-url", lambda kw: Yanit(json.dumps(
        {"success": True, "url": cozum[1]["embed"], "type": "iframe"})))
    for kayit_ in cozum:
        site.ekle("GET", kayit_["embed"], Yanit(embed_sayfasi(kayit_["iframe"])))
    site.ekle("GET", cozum[1]["iframe"], Yanit(fikstur("loveulikeido-e-onepiece-1161.html")))
    fireplayer.yanitlar[MUGEN] = lambda data: Yanit(fikstur("anizmplayer-getvideo-naruto-e1.json"))


def test_one_piece_mugen_hls_referer_ile_ve_aynalar_suzuluyor(site, fireplayer):
    one_piece_kur(site, fireplayer)

    akislar = animeler.get_episode_streams("one-piece/bolum-1161")

    guvenli = json.loads(fikstur("anizmplayer-getvideo-naruto-e1.json"))["securedLink"]
    assert akislar == [{
        "url": guvenli,
        "label": "Yapay Zeka - Mugen - HLS",
        "type": "hls",
        "fansub": "Yapay Zeka",
        "player": "MUGEN",
        "referer": "https://anizmplayer.com/",
    }], "loveulikeido aynalarının hiçbiri yt-dlp'de açılmıyor: atılmalı"
    # Ayna sayfası gerçekten açıldı (şifre çözüldü, sonra süzüldü).
    assert site.cagrilar("GET", "loveulikeido.site/e/")


def test_pycryptodome_yoksa_ayna_sayfasi_istenmiyor(site, fireplayer, monkeypatch):
    """Sunucu imajında pycryptodome yok: ayna adımı atlanır, gerisi çalışır."""
    monkeypatch.setattr(animeler, "_aes", lambda: None)
    one_piece_kur(site, fireplayer)

    akislar = animeler.get_episode_streams("one-piece/bolum-1161")

    assert [a["player"] for a in akislar] == ["MUGEN"]
    assert not site.cagrilar("GET", "loveulikeido.site")
    with pytest.raises(ValueError, match="pycryptodome"):
        animeler.cryptojs_coz("U2FsdGVkX18=", "parola")


def test_cryptojs_aynalari_cozuluyor():
    beklenen = json.loads(fikstur("loveulikeido-onepiece-1161-decrypted.json"))
    aynalar = animeler.sifreli_aynalari_coz(fikstur("loveulikeido-e-onepiece-1161.html"))
    assert aynalar == [(b["servername"], b["type"], b["url"]) for b in beklenen]


def test_cryptojs_yanlis_parola_hata():
    ilk = json.loads(fikstur("loveulikeido-onepiece-1161-decrypted.json"))[0]
    with pytest.raises(ValueError):
        animeler.cryptojs_coz(ilk["link"], "yanlis-parola-yanlis-parola-1234")
    assert animeler.cryptojs_coz(ilk["link"], "Ak7qrvvH4WKYxV2OgaeHAEg2a5eh16vE") == ilk["url"]


def test_olmayan_bolum_kanonik_adresle_yakalaniyor(site):
    """Site olmayan bölüm için 200 ile 1. bölümü döndürüyor; sessizce yanlış
    bölüm oynatmak yerine hata."""
    site.ekle("GET", f"{TABAN}/sousou-no-frieren/bolum-999",
              Yanit(fikstur("episode-sousou-no-frieren-bolum-999.html")))

    with pytest.raises(AnimelerHatasi, match="bolum-999") as bilgi:
        animeler.get_episode_streams("sousou-no-frieren/bolum-999")
    assert bilgi.value.status_code == 404
    assert not site.cagrilar("POST"), "yanlış bölümün kaynakları çözülmemeli"


def test_birlesik_bolum_sayfasi_kabul_ediliyor():
    sayfa = animeler.bolum_sayfasini_ayristir(fikstur("episode-naruto-shippuuden-bolum-1-2.html"))
    assert sayfa.kanonik == "naruto-shippuuden/bolum-1-2"
    assert len(sayfa.kaynaklar) == 8 and sayfa.gruplar == {1: "Varsayılan"}
    assert sayfa.varsayilan_embed.startswith(f"{TABAN}/embed/")


def test_csrf_419_diger_kaynaklari_dusurmuyor(site, fireplayer):
    frieren_kur(site, fireplayer, kaynak_yaniti=lambda kw: Yanit("Session has expired", 419))

    akislar = animeler.get_episode_streams("sousou-no-frieren/bolum-1")

    # POST'suz varsayılan kaynak (FirePlayer) yine çözülür.
    assert [a["player"] for a in akislar] == ["SIBNET", "ODNOKLASSNIKI"]


def test_hicbir_kaynak_cozulemezse_sebebiyle_hata(site, fireplayer):
    frieren_kur(site, fireplayer, kaynak_yaniti=lambda kw: Yanit("Session has expired", 419))
    fireplayer.yanitlar[FRIEREN_FIREPLAYER] = lambda data: Yanit("oops", 500)

    with pytest.raises(AnimelerHatasi, match="oynatılabilir akış bulunamadı"):
        animeler.get_episode_streams("sousou-no-frieren/bolum-1")


def test_kaldirilmis_kaynak_atlaniyor_hata_sayilmiyor(site, fireplayer):
    frieren_kur(site, fireplayer, kaynak_yaniti=lambda kw: Yanit(
        '{"success":false,"message":"Kaynak bulunamad\\u0131"}'))

    akislar = animeler.get_episode_streams("sousou-no-frieren/bolum-1")
    assert [a["player"] for a in akislar] == ["SIBNET", "ODNOKLASSNIKI"]


def test_yapay_zeka_cevirisi_insan_cevirisinden_sonra(site, fireplayer):
    sayfa = fikstur("episode-sousou-no-frieren-bolum-1.html")
    # Mail.ru kaynakları ikinci bir gruba ("Yapay Zeka") taşınıyor.
    for kimlik in (271739, 271743):
        sayfa = sayfa.replace(f'{{"id":{kimlik},"fansub_group_id":1', f'{{"id":{kimlik},"fansub_group_id":4')
    dugme = re.search(r"<button type=\"button\"\s+class=\"fansub-group-btn active\".*?</button>", sayfa, re.S)
    ikinci = dugme.group(0).replace('data-fansub-group-id="1"', 'data-fansub-group-id="4"') \
        .replace(">Varsayılan<", ">Yapay Zeka<")
    sayfa = sayfa.replace(dugme.group(0), dugme.group(0) + ikinci)
    frieren_kur(site, fireplayer, sayfa=sayfa)

    akislar = animeler.get_episode_streams("sousou-no-frieren/bolum-1")

    assert [a["fansub"] for a in akislar][-2:] == ["Yapay Zeka", "Yapay Zeka"]
    assert akislar[-3]["player"] == "ODNOKLASSNIKI"


def test_ayni_sunucudan_en_cok_uc_kaynak_cozuluyor(site, fireplayer):
    sayfa = fikstur("episode-sousou-no-frieren-bolum-1.html")
    kaynaklar = [{"id": 900 + i, "fansub_group_id": 1, "source_type": "iframe",
                  "quality": "auto", "server_name": "Sibnet"} for i in range(6)]
    sayfa = re.sub(r"var allEpisodeSources = \[.*?\];",
                   "var allEpisodeSources = " + json.dumps(kaynaklar) + ";", sayfa)
    site.ekle("GET", FRIEREN_E1, Yanit(sayfa))
    site.ekle("POST", f"{TABAN}/ajax/get-source-url", lambda kw: Yanit(json.dumps(
        {"success": True, "url": f"{TABAN}/embed/{kw['data']['source_id']}/abc", "type": "iframe"})))
    for i in range(6):
        site.ekle("GET", f"{TABAN}/embed/{900 + i}/abc",
                  Yanit(embed_sayfasi(f"https://video.sibnet.ru/shell.php?videoid={i}")))

    akislar = animeler.get_episode_streams("sousou-no-frieren/bolum-1")

    assert len(site.cagrilar("POST")) == animeler.SUNUCU_BASINA_AZAMI == 3
    assert [a["url"][-1] for a in akislar] == ["0", "1", "2"]


def test_bolum_sayfasi_alinamazsa_hata(site):
    site.ekle("GET", FRIEREN_E1, ConnectionError("bağlantı koptu"))
    with pytest.raises(AnimelerHatasi, match="ulaşılamadı.*bölüm sayfası"):
        animeler.get_episode_streams("sousou-no-frieren/bolum-1")


def test_kaynak_listesi_olmayan_bolum_sayfasi_hata(site):
    site.ekle("GET", FRIEREN_E1, Yanit('<link rel="canonical" href="https://animeler.pw/'
                                       'sousou-no-frieren/bolum-1"><p>bakım</p>'))
    with pytest.raises(AnimelerHatasi, match="allEpisodeSources"):
        animeler.get_episode_streams("sousou-no-frieren/bolum-1")


@pytest.mark.parametrize("kimlik", ["", "sousou-no-frieren", "a b/bolum-1", "../etc/passwd"])
def test_gecersiz_bolum_kimligi_istek_atmadan_reddediliyor(site, kimlik):
    with pytest.raises(AnimelerHatasi, match="Geçersiz"):
        animeler.get_episode_streams(kimlik)
    assert site.istekler == []


def test_embed_iframe_data_src_ile_karismiyor():
    html = ('<iframe data-src="https://yanlis.example/x" id="innerPlayer" '
            'src="//video.sibnet.ru/shell.php?videoid=7"></iframe>')
    assert animeler.embed_adresi(html) == ("iframe", "https://video.sibnet.ru/shell.php?videoid=7")


def test_anizle_fireplayer_yolu_degismedi(fireplayer):
    """Ortak koda taşınan Anizle çağrısı aynı ucu aynı başlıklarla çağırıyor."""
    fireplayer.yanitlar[f"{anizle.PLAYER_BASE_URL}/player/index.php?data=abc"] = \
        lambda data: Yanit(fikstur("anizmplayer-getvideo-naruto-e1.json"))

    akis = anizle._get_video_stream_from_player("abc", "Grup - Oynatıcı")

    guvenli = json.loads(fikstur("anizmplayer-getvideo-naruto-e1.json"))["securedLink"]
    assert akis == {"url": guvenli, "label": "Grup - Oynatıcı (HLS)", "type": "hls"}
    cagri = fireplayer.cagrilar[0]
    assert cagri["url"] == f"{anizle.PLAYER_BASE_URL}/player/index.php?data=abc&do=getVideo"
    assert cagri["headers"] == {"Referer": f"{anizle.PLAYER_BASE_URL}/player/abc",
                                "Origin": anizle.PLAYER_BASE_URL}
    assert cagri["data"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 4) Kayıt
# ─────────────────────────────────────────────────────────────────────────────
def test_kayitta_animeler_var():
    kaynak = kayit.bul("Animeler")
    assert kaynak is not None and kaynak is kayit.KAYNAKLAR[-1]
    assert kayit.bul("animeler") is kayit.bul("Animeler.pw") is kaynak
    assert (kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("Animeler.pw", "AN", "#00cec9", "ANIMELER")
    assert kaynak.modul == "animeler" and kaynak.cli_kodu == "animeler"
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari() and kaynak in kayit.tarayici_kaynaklari()
    uclar = kaynak.uclar()
    assert uclar.ara is animeler.search_animeler
    assert uclar.bolumler is animeler.get_anime_episodes
    assert uclar.akislar is animeler.get_episode_streams
    assert kaynak.bolum_adresi("one-piece/bolum-215-216") == f"{TABAN}/one-piece/bolum-215-216"
    assert kaynak.bolum_slugu("one-piece/bolum-215-216") == "one-piece-bolum-215-216"


def test_kopru_animeler_bolumlerini_kuruyor(site):
    from turkanime_api.gui.qt import sources_bridge as sb

    site.ekle("GET", f"{TABAN}/sousou-no-frieren", Yanit(fikstur("anime-sousou-no-frieren.html")))

    bolumler = sb.fetch_episodes("Animeler", "sousou-no-frieren", "Frieren: Beyond Journey's End")

    assert len(bolumler) == 28
    nesne = bolumler[0]["obj"]
    assert nesne.url == FRIEREN_E1
    # Slug sitenin kimliğinden: verilen başlık ne olursa olsun aynı.
    assert nesne.slug == "sousou-no-frieren-bolum-1"
    assert nesne._player_name == "ANIMELER"


# ─────────────────────────────────────────────────────────────────────────────
# 5) Canlı duman testi (varsayılan olarak atlanır; `--network` ile koşar)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    animeler.onbellegi_sifirla()
    sonuc = animeler.search_animeler("frieren", limit=5)
    assert ("sousou-no-frieren", "Sousou no Frieren") in sonuc

    bolumler = animeler.get_anime_episodes("sousou-no-frieren")
    assert len(bolumler) >= 28 and bolumler[0][0] == "sousou-no-frieren/bolum-1"

    akislar = animeler.get_episode_streams(bolumler[0][0])
    assert akislar, "canlı sitede Frieren 1. bölüm için akış çıkmadı"
    for akis in akislar:
        assert urlparse(akis["url"]).scheme == "https" and akis["label"] and akis["player"]
