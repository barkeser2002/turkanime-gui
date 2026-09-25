"""One Pace TR kaynağı (`sources/onepacetr.py`) — ağsız.

Site bir React SPA'sı; veri Heroku'daki Strapi arka ucundan, sitenin JS
paketinde duran herkese açık bir jetonla okunuyor. Burada sınananlar:

1. **Yapılandırma.** Jeton KODDA YOK; ana sayfa → güncel `index-*.js` →
   jeton ve API kökü çalışma anında çıkarılıyor, süreç içinde önbellekleniyor,
   401/403'te ve arka ucun taşındığını gösteren JSON olmayan 404'te bir kez
   tazeleniyor. Paket değişirse açık hata.
2. **Arama.** Sunucu tarafı arama yok; ark listesi yerelde süzülüyor. "one
   pace"/"one piece" bütün seriyi + arkları, ark adı yalnız o arkı getiriyor.
3. **Bölümler.** API sırasız döndürüyor; izleme sırası (ark, bölüm). Başlıklar
   uygulamanın bölüm birleştiricisinde (ark = sezon) çakışmıyor.
4. **Akışlar.** Yalnızca yt-dlp'nin oynatabildiği gdrive + sibnet; sibnet
   kendi referer'ıyla (onepacetr.net referer'ı 403 alıyor).
5. **Hatalar.** Ağ/arka uç/biçim sorunları ve bulunamayan kimlik açık Türkçe
   `OnePaceTRHatasi`; sonuçsuz arama ve bölümsüz ark boş liste. Sunucu
   tarayıcısı (kaynak taranabilir) bu hataları doğru sınıflıyor: bulunamayan
   kayıt kalıcı, sunucu/ağ sorunu geçici, reddedilen jeton engellenme.

HTTP tamamen sahte: curl_cffi kendi (libcurl) soketlerini açtığı için
`conftest`'in ağ mandalı onu YAKALAMAZ; bu yüzden her test `_oturum`'u
sahteleyen otomatik fixture'la koşuyor. Fixture'lar gerçek yanıtların
kırpılmış hâli (`tests/fixtures/onepacetr/`, 2026-09-24); JS parçasındaki
jeton sahte bir 256-hex değerle değiştirildi.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from turkanime_api.sources import kayit, onepacetr
from turkanime_api.sources.onepacetr import OnePaceTRHatasi

FIX = Path(__file__).resolve().parent / "fixtures" / "onepacetr"
KAYNAK_DOSYASI = Path(onepacetr.__file__)

ANA_SAYFA = "https://www.onepacetr.net/"
PAKET = "https://www.onepacetr.net/assets/index-CtAt_sKf.js"
API = "https://onepacetradmin-v3-4f0db9f5d700.herokuapp.com/api"
YENI_API = "https://onepacetradmin-v4-0123abcd.herokuapp.com/api"
SAHTE_JETON = "0123456789abcdef" * 16          # fixture'daki (sahte) jeton
YENI_JETON = "fedcba9876543210" * 16
# Heroku'nun kaldırılmış uygulama için döndürdüğü sayfa (JSON değil)
HEROKU_404 = "<html><head><title>Heroku | No such app</title></head></html>"


def _fx(ad: str) -> str:
    return (FIX / ad).read_text(encoding="utf-8")


def _fx_json(ad: str) -> Any:
    return json.loads(_fx(ad))


class Yanit:
    """curl_cffi yanıtının kullanılan yüzü."""

    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)


def _json_yanit(govde: Any, durum: int = 200) -> Yanit:
    return Yanit(durum, json.dumps(govde, ensure_ascii=False))


class SahteOturum:
    """Adrese göre fixture döndüren, her isteği kaydeden sahte HTTP oturumu.

    Arka uç gerçeği gibi davranıyor: jetonsuz istek 403, yanlış jeton 401.
    ``ozel``: adres → Yanit ya da fırlatılacak istisna (öncelikli).
    ``api_koku``: arka ucun o anki adresi; ``olu_kok``: kaldırılmış eski
    adres (Heroku'nun JSON olmayan 404 sayfası).
    """

    def __init__(self) -> None:
        self.istekler: List[Dict[str, Any]] = []
        self.gecerli_jeton = SAHTE_JETON
        self.paket_metni = _fx("index-parca.js")
        self.ozel: Dict[str, Any] = {}
        self.api_koku = API
        self.olu_kok: Optional[str] = None

    def get(self, url: str, headers: Optional[Dict[str, str]] = None,
            timeout: Any = None) -> Yanit:
        basliklar = dict(headers or {})
        self.istekler.append({"url": url, "headers": basliklar, "timeout": timeout})
        if url in self.ozel:
            deger = self.ozel[url]
            if isinstance(deger, BaseException):
                raise deger
            return deger
        if url == ANA_SAYFA:
            return Yanit(200, _fx("anasayfa.html"))
        if url == PAKET:
            return Yanit(200, self.paket_metni)
        if self.olu_kok and url.startswith(self.olu_kok + "/"):
            return Yanit(404, HEROKU_404)
        if url.startswith(self.api_koku + "/"):
            return self._api(url[len(self.api_koku) + 1:], basliklar)
        raise AssertionError(f"beklenmeyen istek: {url}")

    def _api(self, yol: str, basliklar: Dict[str, str]) -> Yanit:
        yetki = basliklar.get("Authorization")
        if yetki is None:
            return Yanit(403, _fx("api-403.json"))
        if yetki != f"Bearer {self.gecerli_jeton}":
            return Yanit(401, _fx("api-401.json"))
        if yol.startswith("seasons?pagination[pageSize]=100") and "populate[image]" in yol:
            return Yanit(200, _fx("katalog.json"))
        if yol.startswith("seasons?pagination[pageSize]=100") and "populate[episodes]" in yol:
            return Yanit(200, _fx("seri.json"))
        m = re.match(r"seasons\?filters\[slug\]\[\$eq\]=([a-z0-9-]+)&", yol)
        if m:
            dosya = FIX / f"ark-{m.group(1)}.json"
            return Yanit(200, dosya.read_text(encoding="utf-8") if dosya.exists()
                         else _fx("ark-yok.json"))
        m = re.fullmatch(r"episodes/([a-z0-9-]+)", yol)
        if m:
            dosya = FIX / f"bolum-{m.group(1)}.json"
            if dosya.exists():
                return Yanit(200, dosya.read_text(encoding="utf-8"))
            return Yanit(404, _fx("bolum-yok-404.json"))
        raise AssertionError(f"beklenmeyen API yolu: {yol}")

    # Kolaylıklar
    def adresler(self) -> List[str]:
        return [i["url"] for i in self.istekler]

    def api_istekleri(self) -> List[Dict[str, Any]]:
        return [i for i in self.istekler if i["url"].startswith(API + "/")]


@pytest.fixture(autouse=True)
def oturum(request, monkeypatch):
    """Her testte önbellek sıfır ve HTTP sahte (ağ testi hariç)."""
    onepacetr.sifirla()
    if request.node.get_closest_marker("network"):
        yield None
        onepacetr.sifirla()
        return
    sahte = SahteOturum()
    monkeypatch.setattr(onepacetr, "_oturum", lambda: sahte)
    yield sahte
    onepacetr.sifirla()


def _api_govdesi(oturum: SahteOturum, bolum: str, govde: Any, durum: int = 200) -> None:
    oturum.ozel[f"{API}/episodes/{bolum}"] = _json_yanit(govde, durum)


# ─────────────────────────────────────────────────────────────────────────────
# 1) Yapılandırma: jeton ve API kökü JS paketinden
# ─────────────────────────────────────────────────────────────────────────────
def test_jeton_calisma_aninda_paketten_okunuyor(oturum):
    onepacetr.search_onepacetr("wano")

    assert oturum.adresler()[:2] == [ANA_SAYFA, PAKET]
    api = oturum.api_istekleri()
    assert len(api) == 1
    assert api[0]["headers"]["Authorization"] == f"Bearer {SAHTE_JETON}"
    assert api[0]["headers"]["Accept"] == "application/json"


def test_her_istek_zaman_asimiyla_gidiyor(oturum):
    onepacetr.search_onepacetr("one piece")
    onepacetr.get_anime_episodes("romance-dawn")
    onepacetr.get_episode_streams("romance-dawn/maceranin-baslangici")

    assert oturum.istekler
    assert all(i["timeout"] == onepacetr.HTTP_TIMEOUT for i in oturum.istekler)


def test_yapilandirma_surec_icinde_onbellekte(oturum):
    onepacetr.search_onepacetr("wano")
    onepacetr.get_anime_episodes("wano")
    onepacetr.get_episode_streams("wano/hasir-sapkali-luffy-1")

    assert oturum.adresler().count(ANA_SAYFA) == 1
    assert oturum.adresler().count(PAKET) == 1


def test_jeton_kodda_gomulu_degil():
    """Görev kuralı: jeton koda yazılmaz, paketten okunur."""
    for yol in (KAYNAK_DOSYASI, Path(kayit.__file__)):
        metin = yol.read_text(encoding="utf-8")
        assert not re.search(r"[0-9a-f]{32,}", metin), f"{yol.name} jeton benzeri dizge taşıyor"


def test_paketten_yapilandirma_ayristiriliyor():
    api, jeton = onepacetr.paketten_yapilandirma(_fx("index-parca.js"))
    assert api == API
    assert jeton == SAHTE_JETON and len(jeton) == 256


def test_paketten_yapilandirma_tirnak_degisse_de_okunuyor():
    js = ("x=async t=>fetch('https://yeni-arka-uc.example.com/api/episodes/'+t,"
          "{headers:{'Authorization':'Bearer " + YENI_JETON + "'}})")
    assert onepacetr.paketten_yapilandirma(js) == ("https://yeni-arka-uc.example.com/api",
                                                   YENI_JETON)
    assert onepacetr.paketten_yapilandirma("") == (None, None)


def test_paketten_yapilandirma_en_sik_adresi_ve_jetonu_seciyor():
    """Pakette unutulmuş tek bir deneme adresi/jetonu asıl değerleri ezmesin."""
    def cagri(kok: str, jeton: str) -> str:
        return (f'fetch("{kok}/seasons",{{headers:{{Authorization:"Bearer {jeton}"}}}});')

    js = (cagri("https://deneme.example.com/api", YENI_JETON)
          + cagri(API, SAHTE_JETON) * 3)
    assert onepacetr.paketten_yapilandirma(js) == (API, SAHTE_JETON)


def test_protokolsuz_paket_adresi_https_ile_isteniyor(oturum):
    cdn_paket = "https://cdn.onepacetr.example/assets/index-Yeni1.js"
    oturum.ozel[ANA_SAYFA] = Yanit(200, _fx("anasayfa.html").replace(
        'src="/assets/index-CtAt_sKf.js"', 'src="//cdn.onepacetr.example/assets/index-Yeni1.js"'))
    oturum.ozel[cdn_paket] = Yanit(200, _fx("index-parca.js"))

    assert onepacetr.search_onepacetr("wano") == [("wano", "One Pace 35: Wano")]
    assert oturum.adresler()[:2] == [ANA_SAYFA, cdn_paket]


def test_401_gelince_jeton_tazelenip_bir_kez_yeniden_deneniyor(oturum):
    """Site jetonu döndürdü: eski jeton 401 alıyor, yeni paket yenisini taşıyor."""
    onepacetr.search_onepacetr("wano")
    onepacetr._yapilandirma["zaman"] -= 3600          # saatler önce alınmış olsun
    oturum.gecerli_jeton = YENI_JETON
    oturum.paket_metni = _fx("index-parca.js").replace(SAHTE_JETON, YENI_JETON)

    bolumler = onepacetr.get_anime_episodes("wano")

    assert len(bolumler) == 55
    assert oturum.adresler().count(PAKET) == 2
    son_iki = oturum.api_istekleri()[-2:]
    assert [i["headers"]["Authorization"] for i in son_iki] == [
        f"Bearer {SAHTE_JETON}", f"Bearer {YENI_JETON}"]


def test_arka_uc_tasininca_yeni_adres_paketten_aliniyor(oturum):
    """Eski Heroku uygulaması kaldırıldı (JSON olmayan 404), paket yeni adresi taşıyor.

    Önbellekteki yapılandırma saatlerce eski adreste kalıp her şeye "bulunamadı"
    demesin: JSON olmayan 404 bir kez tazelemeyi tetikliyor.
    """
    onepacetr.search_onepacetr("wano")
    onepacetr._yapilandirma["zaman"] -= 3600
    oturum.olu_kok, oturum.api_koku = API, YENI_API
    oturum.paket_metni = _fx("index-parca.js").replace(API, YENI_API)

    bolumler = onepacetr.get_anime_episodes("wano")

    assert len(bolumler) == 55
    son_iki = [u for u in oturum.adresler() if "herokuapp" in u][-2:]
    assert son_iki[0].startswith(API + "/seasons?filters")
    assert son_iki[1].startswith(YENI_API + "/seasons?filters")
    assert oturum.adresler().count(PAKET) == 2


def test_arka_uc_yoksa_acik_hata_ve_tek_tazeleme(oturum):
    oturum.olu_kok = API                  # paket de hâlâ ölü adresi gösteriyor
    with pytest.raises(OnePaceTRHatasi, match="adresinde yok") as hata:
        onepacetr.get_anime_episodes("wano")
    assert hata.value.status_code == 503  # kayıt değil sunucu yok: kalıcı değil
    # Taze yapılandırmayla alınan 404'te paket yeniden indirilmez
    assert oturum.adresler().count(PAKET) == 1
    assert len([u for u in oturum.adresler() if u.startswith(API + "/")]) == 2


def test_strapi_bulunamadi_yaniti_yapilandirmayi_tazelemiyor(oturum):
    """Bilinmeyen bölümün JSON 404'ü "kayıt yok"; jeton/adres sorunu değil."""
    onepacetr.search_onepacetr("wano")
    onepacetr._yapilandirma["zaman"] -= 3600
    with pytest.raises(OnePaceTRHatasi, match="bölümü bulunamadı"):
        onepacetr.get_episode_streams("wano/yok-boyle-bir-bolum")
    assert oturum.adresler().count(PAKET) == 1
    assert oturum.adresler().count(f"{API}/episodes/yok-boyle-bir-bolum") == 1


def test_kalici_red_acik_hata_veriyor(oturum):
    oturum.gecerli_jeton = YENI_JETON                 # paketteki jeton hiç geçmiyor
    with pytest.raises(OnePaceTRHatasi, match="erişimi reddetti"):
        onepacetr.get_anime_episodes("wano")
    # Taze yapılandırmayla alınan 401'de paket yeniden indirilmez (fırtına yok)
    assert oturum.adresler().count(PAKET) == 1


def test_pakette_jeton_yoksa_acik_hata(oturum):
    oturum.paket_metni = "console.log('jeton yok')"
    with pytest.raises(OnePaceTRHatasi, match="API anahtarı bulunamadı"):
        onepacetr.search_onepacetr("wano")


def test_pakette_api_adresi_yoksa_bilinen_koke_dusuluyor(oturum):
    oturum.paket_metni = ('f=()=>fetch(import.meta.env.VITE_API+"/seasons",'
                          '{headers:{Authorization:"Bearer ' + SAHTE_JETON + '"}})')
    assert onepacetr.search_onepacetr("wano") == [("wano", "One Pace 35: Wano")]
    assert oturum.api_istekleri()[0]["url"].startswith(onepacetr.API_BASE_URL + "/")


def test_ana_sayfada_paket_yoksa_acik_hata(oturum):
    oturum.ozel[ANA_SAYFA] = Yanit(200, "<html><body>bakımdayız</body></html>")
    with pytest.raises(OnePaceTRHatasi, match="uygulama paketi"):
        onepacetr.search_onepacetr("wano")


def test_ag_hatasi_acik_turkce_hata(oturum):
    oturum.ozel[ANA_SAYFA] = ConnectionError("bağlantı reddedildi")
    with pytest.raises(OnePaceTRHatasi, match="ulaşılamadı"):
        onepacetr.get_anime_episodes("wano")


def test_arka_uc_5xx_acik_hata(oturum):
    oturum.ozel[f"{API}/episodes/maceranin-baslangici"] = Yanit(503, "Application error")
    with pytest.raises(OnePaceTRHatasi, match="HTTP 503"):
        onepacetr.get_episode_streams("romance-dawn/maceranin-baslangici")


def test_json_olmayan_yanit_acik_hata(oturum):
    oturum.ozel[f"{API}/episodes/maceranin-baslangici"] = Yanit(200, "<html>")
    with pytest.raises(OnePaceTRHatasi, match="JSON olmayan"):
        onepacetr.get_episode_streams("romance-dawn/maceranin-baslangici")


def test_429_acik_hata(oturum):
    oturum.ozel[f"{API}/episodes/maceranin-baslangici"] = Yanit(429, "")
    with pytest.raises(OnePaceTRHatasi, match="HTTP 429") as hata:
        onepacetr.get_episode_streams("romance-dawn/maceranin-baslangici")
    assert hata.value.status_code == 429


# ─────────────────────────────────────────────────────────────────────────────
# 1b) Sunucu tarayıcısı hataları doğru sınıflıyor
# ─────────────────────────────────────────────────────────────────────────────
def _tur(cagri) -> str:
    from turkanime_server.crawler.nezaket import hata_turu

    with pytest.raises(OnePaceTRHatasi) as hata:
        cagri()
    return hata_turu(hata.value)


def test_bulunamayan_ve_gecersiz_kayit_kalici_sayiliyor():
    """Kaldırılmış ark/bölüm her turda yeniden denenmesin (görev kapanır).

    Sınıf yalnızca metinden çıkarılsaydı bu hatalar "geçici" sayılırdı: art
    arda beş deneme kaynağın tamamını o tur dinlenmeye alıyordu.
    """
    from turkanime_server.crawler.nezaket import KALICI

    assert _tur(lambda: onepacetr.get_anime_episodes("yok-boyle")) == KALICI
    assert _tur(lambda: onepacetr.get_episode_streams("wano/yok-boyle")) == KALICI
    assert _tur(lambda: onepacetr.get_anime_episodes("../x")) == KALICI
    assert _tur(lambda: onepacetr.get_episode_streams("wano/a?b")) == KALICI


def test_sunucu_sorunlari_kalici_sayilmiyor(oturum):
    """Arka uç/ağ sorunu görevi kalıcı kapatmamalı: düzelince tarama sürmeli."""
    from turkanime_server.crawler.nezaket import ENGELLENME, GECICI

    oturum.ozel[f"{API}/episodes/maceranin-baslangici"] = Yanit(503, "Application error")
    assert _tur(lambda: onepacetr.get_episode_streams("x/maceranin-baslangici")) == GECICI

    oturum.olu_kok = API                  # Heroku uygulaması kaldırılmış
    assert _tur(lambda: onepacetr.get_anime_episodes("wano")) == GECICI

    oturum.olu_kok = None
    oturum.ozel[f"{API}/seasons?filters[slug][$eq]=wano{onepacetr._BOLUM_ALANLARI}"] = (
        _json_yanit(_fx_json("bolum-yok-404.json"), 404))   # uç kaldırılmış
    assert _tur(lambda: onepacetr.get_anime_episodes("wano")) == GECICI

    oturum.gecerli_jeton = YENI_JETON     # jeton geçmiyor
    assert _tur(lambda: onepacetr.get_episode_streams("wano/hasir-sapkali-luffy-1")) == ENGELLENME


def test_ag_ve_paket_sorunlari_gecici(oturum):
    from turkanime_server.crawler.nezaket import GECICI

    oturum.ozel[PAKET] = Yanit(404, "Not Found")   # yayın anında silinen paket
    assert _tur(lambda: onepacetr.search_onepacetr("wano")) == GECICI
    oturum.ozel[ANA_SAYFA] = ConnectionError("zaman aşımı")
    assert _tur(lambda: onepacetr.search_onepacetr("wano")) == GECICI


# ─────────────────────────────────────────────────────────────────────────────
# 2) Arama
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sorgu, beklenen", [
    ("wano", [("wano", "One Pace 35: Wano")]),
    # Sitede ad "Alabasta", slug "arabasta": ikisi de bulmalı
    ("Alabasta", [("arabasta", "One Pace 14: Alabasta")]),
    ("ARABASTA", [("arabasta", "One Pace 14: Alabasta")]),
    ("Eniès lobby", [("enies-lobby", "One Pace 19: Enies Lobby"),
                     ("post-enies-lobby", "One Pace 20: Post-Enies Lobby")]),
    ("one pace dressrosa", [("dressrosa", "One Pace 31: Dressrosa")]),
    ("One Piece Wano", [("wano", "One Pace 35: Wano")]),
    ("one pace 35", [("wano", "One Pace 35: Wano")]),
    ("island", [("drum-island", "One Pace 13: Drum Island"),
                ("fishman-island", "One Pace 29: Fishman Island"),
                ("whole-cake-island", "One Pace 33: Whole Cake Island")]),
])
def test_ark_aramasi(sorgu, beklenen):
    assert onepacetr.search_onepacetr(sorgu) == beklenen


@pytest.mark.parametrize("sorgu", ["one piece", "One Pace", "onepiece", "ONE PİECE",
                                   "one-pace", "one p"])
def test_seri_adi_butun_seriyi_ve_arklari_getiriyor(sorgu):
    sonuc = onepacetr.search_onepacetr(sorgu, limit=100)

    assert sonuc[0] == (onepacetr.SERI_KIMLIGI, "One Pace")
    arklar = sonuc[1:]
    assert len(arklar) == 35
    # API sırasız döndürüyor; arama ark numarasına göre diziyor
    assert arklar[0] == ("romance-dawn", "One Pace 01: Romance Dawn")
    assert arklar[-1] == ("wano", "One Pace 35: Wano")
    numaralar = [int(re.search(r"One Pace (\d+):", b).group(1)) for _, b in arklar]
    assert numaralar == list(range(1, 36))


@pytest.mark.parametrize("sorgu", ["", "   ", "naruto", "frieren", "jujutsu kaisen",
                                   "one piece film red", "one", "one pace 99"])
def test_sonucsuz_arama_bos_liste(sorgu):
    assert onepacetr.search_onepacetr(sorgu) == []


def test_bos_sorgu_aga_cikmiyor(oturum):
    onepacetr.search_onepacetr("  ")
    assert oturum.istekler == []


def test_arama_limite_uyuyor():
    sonuc = onepacetr.search_onepacetr("one piece", limit=5)
    assert len(sonuc) == 5
    assert sonuc[0][0] == onepacetr.SERI_KIMLIGI


def test_ark_listesi_onbellekte(oturum):
    onepacetr.search_onepacetr("wano")
    onepacetr.search_onepacetr("alabasta")
    onepacetr.search_onepacetr("one piece")
    assert len(oturum.api_istekleri()) == 1


def test_arama_ag_hatasini_yutmuyor(oturum):
    """"Aranamadı" ≠ "0 sonuç": arama motoru hatayı kaynağın adıyla gösterir."""
    oturum.ozel[ANA_SAYFA] = ConnectionError("zaman aşımı")
    with pytest.raises(OnePaceTRHatasi):
        onepacetr.search_onepacetr("wano")


def test_zengin_arama_kapak_veriyor():
    sonuc = onepacetr.zengin_ara("wano")
    assert sonuc == [{
        "slug": "wano", "title": "One Pace 35: Wano",
        "image": "https://onepacetr.s3.eu-west-2.amazonaws.com/"
                 "small_cover_wano_arc_26ad69216d.webp",
    }]
    seri = onepacetr.zengin_ara("one pace", limit=1)[0]
    assert seri["slug"] == onepacetr.SERI_KIMLIGI and seri["image"].startswith("https://")


def test_otomatik_eslestirme_tek_arki_degil_seriyi_baglar():
    """Detay sayfası kaynak başına YALNIZ ilk sonucu bağlıyor (limit=1).

    Tek bir ark "ONE PIECE"e bağlansaydı ark bölümleri asıl serinin 1., 2., …
    bölümleriyle aynı satıra düşerdi; seri girişi ark = sezon taşıyor.
    """
    kaynak = kayit.bul("One Pace TR")
    assert kaynak.ara("ONE PIECE", limit=1) == [(onepacetr.SERI_KIMLIGI, "One Pace")]


# ─────────────────────────────────────────────────────────────────────────────
# 3) Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def test_ark_bolumleri_izleme_sirasiyla():
    # API sırası 3, 1, 2, 4
    assert onepacetr.get_anime_episodes("romance-dawn") == [
        ("romance-dawn/maceranin-baslangici",
         "1. Sezon 1. Bölüm - Romance Dawn: Maceranın Başlangıcı"),
        ("romance-dawn/hasir-sapkali-luffy",
         "1. Sezon 2. Bölüm - Romance Dawn: Hasır Şapkalı Luffy"),
        ("romance-dawn/korsanlar-krali-ve-en-iyi-kilic-ustasi",
         "1. Sezon 3. Bölüm - Romance Dawn: Korsanlar Kralı ve En İyi Kılıç Ustası"),
        ("romance-dawn/bir-numara", "1. Sezon 4. Bölüm - Romance Dawn: Bir Numara"),
    ]


def test_wano_extended_bolumu_isaretli():
    bolumler = onepacetr.get_anime_episodes("wano")
    assert len(bolumler) == 55
    assert bolumler[-1] == (
        "wano/hasir-sapkali-luffy-1",
        "35. Sezon 55. Bölüm - Wano: Hasır Şapkalı Luffy (Extended)")
    numaralar = [int(re.search(r"Sezon (\d+)\. Bölüm", b).group(1)) for _, b in bolumler]
    assert numaralar == list(range(1, 56))
    assert all(i.startswith("wano/") for i, _ in bolumler)
    assert all("  " not in b and b == b.strip() for _, b in bolumler)


def test_butun_seri_ark_ve_bolum_sirasiyla():
    govde = _fx_json("seri.json")
    beklenen_sayi = sum(len(a["episodes"]) for a in govde["data"])
    ark_sirasi = [a["slug"] for a in sorted(govde["data"], key=lambda a: a["number"])]

    bolumler = onepacetr.get_anime_episodes(onepacetr.SERI_KIMLIGI)

    assert len(bolumler) == beklenen_sayi
    assert bolumler[0] == ("romance-dawn/maceranin-baslangici",
                           "1. Sezon 1. Bölüm - Romance Dawn: Maceranın Başlangıcı")
    assert bolumler[-1][0] == "wano/hasir-sapkali-luffy-1"
    # Arklar numara sırasıyla, her ark bir kez ve bitişik
    gorulen = []
    for kimlik, _ in bolumler:
        ark = kimlik.split("/")[0]
        if not gorulen or gorulen[-1] != ark:
            gorulen.append(ark)
    assert gorulen == ark_sirasi
    assert len({i for i, _ in bolumler}) == len(bolumler)


def test_bolum_basliklari_birlestiricide_cakismiyor():
    """Uygulama bölümleri başlıktan (sezon, bölüm) anahtarıyla birleştiriyor.

    Arksız "1. Bölüm" 30 arkın ilk bölümünü tek satıra indirirdi. Ark = sezon.
    """
    from turkanime_api.common.episode_parser import merge_episodes

    bolumler = onepacetr.get_anime_episodes(onepacetr.SERI_KIMLIGI)
    satirlar = merge_episodes({"One Pace TR": [{"title": b} for _, b in bolumler]},
                              "One Pace")
    assert len(satirlar) == len(bolumler)

    beklenen = []
    for a in sorted(_fx_json("seri.json")["data"], key=lambda a: a["number"]):
        beklenen += [(a["number"], e["number"]) for e in sorted(a["episodes"],
                                                                key=lambda e: e["number"])]
    assert [(s["season"], s["number"]) for s in satirlar] == beklenen


def test_bilinmeyen_ark_acik_hata():
    with pytest.raises(OnePaceTRHatasi, match="'yok-boyle' arkı bulunamadı"):
        onepacetr.get_anime_episodes("yok-boyle")


@pytest.mark.parametrize("kimlik", ["", "../x", "Wano", "wano/", "a b", "wano?x=1"])
def test_gecersiz_ark_kimligi_istek_atmadan_hata(oturum, kimlik):
    with pytest.raises(OnePaceTRHatasi, match="Geçersiz"):
        onepacetr.get_anime_episodes(kimlik)
    assert oturum.istekler == []


def test_bolumsuz_ark_bos_liste(oturum):
    yol = ("seasons?filters[slug][$eq]=egghead" + onepacetr._BOLUM_ALANLARI)
    oturum.ozel[f"{API}/{yol}"] = _json_yanit(
        {"data": [{"id": 1, "name": "Egghead", "number": 36, "slug": "egghead",
                   "episodes": []}], "meta": {}})
    assert onepacetr.get_anime_episodes("egghead") == []


def test_bicimsiz_bolum_listesi_acik_hata(oturum):
    yol = ("seasons?filters[slug][$eq]=wano" + onepacetr._BOLUM_ALANLARI)
    oturum.ozel[f"{API}/{yol}"] = _json_yanit({"data": {"beklenmeyen": True}})
    with pytest.raises(OnePaceTRHatasi, match="beklenen biçimde değil"):
        onepacetr.get_anime_episodes("wano")


# ─────────────────────────────────────────────────────────────────────────────
# 4) Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def test_akislar_gdrive_sonra_sibnet_referer_ile():
    akislar = onepacetr.get_episode_streams("romance-dawn/maceranin-baslangici")
    assert akislar == [
        {"url": "https://drive.google.com/file/d/1nctaAzmAAQBqSFoCul-c4s-_2dtC1Ykr/preview",
         "label": "Google Drive 1080p", "type": "iframe", "player": "GDRIVE",
         "fansub": "One Pace TR"},
        {"url": "https://video.sibnet.ru/shell.php?videoid=5903315",
         "label": "Sibnet 1080p", "type": "iframe", "player": "SIBNET",
         "fansub": "One Pace TR", "referer": "https://video.sibnet.ru/"},
    ]


def test_hic_bir_akis_site_refererini_tasimiyor():
    """Sibnet mp4'ü onepacetr.net referer'ıyla 403 veriyor (ölçüldü)."""
    for kimlik in ("romance-dawn/maceranin-baslangici", "wano/hasir-sapkali-luffy-1"):
        for akis in onepacetr.get_episode_streams(kimlik):
            assert "onepacetr" not in (akis.get("referer") or "")


def test_oynatilamayan_oynaticilar_eleniyor():
    # Fixture'da clone, vidguard, gettsu, streamtape, mixdrop da var
    for kimlik in ("romance-dawn/maceranin-baslangici", "wano/hasir-sapkali-luffy-1"):
        adresler = [a["url"] for a in onepacetr.get_episode_streams(kimlik)]
        assert len(adresler) == 2
        assert not any(k in u for u in adresler
                       for k in ("short.icu", "listeamed", "gett.su", "streamtape", "mixdrop"))


def test_yalin_bolum_slugu_da_calisiyor():
    assert (onepacetr.get_episode_streams("maceranin-baslangici")
            == onepacetr.get_episode_streams("romance-dawn/maceranin-baslangici"))


def test_gizli_bos_ve_yabanci_konakli_kayitlar_atiliyor(oturum):
    _api_govdesi(oturum, "deneme", {"data": {"resolution": "720p", "players": [
        {"name": "gdrive", "url": "#", "hide": 0},
        {"name": "gdrive", "url": "https://drive.google.com/file/d/gizli/preview", "hide": 1},
        {"name": "sibnet", "url": "https://kotu.example/shell.php?videoid=1", "hide": 0},
        {"name": "sibnet", "url": "http://video.sibnet.ru/shell.php?videoid=2", "hide": 0},
        {"name": "Sibnet", "url": " https://video.sibnet.ru/shell.php?videoid=3 ", "hide": 0},
        {"name": "sibnet", "url": "https://video.sibnet.ru/shell.php?videoid=3", "hide": 0},
        {"name": "gdrive", "url": "https://drive.google.com/file/d/iki/preview"},
        "bozuk-kayit",
    ]}})
    assert [(a["player"], a["url"], a["label"]) for a in
            onepacetr.get_episode_streams("x/deneme")] == [
        ("GDRIVE", "https://drive.google.com/file/d/iki/preview", "Google Drive 720p"),
        ("SIBNET", "https://video.sibnet.ru/shell.php?videoid=3", "Sibnet 720p"),
    ]


def test_oynatilabilir_oynaticisi_yoksa_bos_liste(oturum):
    _api_govdesi(oturum, "yalniz-clone", {"data": {"players": [
        {"name": "clone", "url": "https://short.icu/abc", "hide": 0}]}})
    assert onepacetr.get_episode_streams("x/yalniz-clone") == []


def test_bilinmeyen_bolum_acik_hata():
    with pytest.raises(OnePaceTRHatasi, match="'yok-boyle-bir-bolum' bölümü bulunamadı"):
        onepacetr.get_episode_streams("wano/yok-boyle-bir-bolum")


@pytest.mark.parametrize("kimlik", ["", "wano/", "../../etc/passwd", "wano/Bolum",
                                    "x y/bolum", "wano/a?b"])
def test_gecersiz_bolum_kimligi_istek_atmadan_hata(oturum, kimlik):
    with pytest.raises(OnePaceTRHatasi, match="Geçersiz"):
        onepacetr.get_episode_streams(kimlik)
    assert oturum.istekler == []


def test_bicimsiz_bolum_yaniti_acik_hata(oturum):
    _api_govdesi(oturum, "bozuk", {"data": None})
    with pytest.raises(OnePaceTRHatasi, match="beklenen biçimde değil"):
        onepacetr.get_episode_streams("x/bozuk")


# ─────────────────────────────────────────────────────────────────────────────
# 5) Kayıt
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_kaynagi_aciyor():
    kaynak = kayit.bul("onepacetr")
    assert kaynak is not None
    assert kaynak is kayit.bul("One Pace TR")
    assert kayit.KAYNAKLAR.index(kaynak) > kayit.KAYNAKLAR.index(kayit.bul("Tranimaci"))
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == (
        "One Pace TR", "One Pace TR", "OP", "#fdcb6e", "ONEPACETR")
    assert (kaynak.modul, kaynak.cli_kodu) == ("onepacetr", "onepacetr")
    assert kaynak.taranabilir and kaynak.oynatilabilir
    assert kaynak in kayit.cli_kaynaklari()
    assert kaynak in kayit.tarayici_kaynaklari()


def test_kayit_uclari_modulun_fonksiyonlari():
    uclar = kayit.bul("One Pace TR").uclar()
    assert uclar.ara is onepacetr.search_onepacetr
    assert uclar.bolumler is onepacetr.get_anime_episodes
    assert uclar.akislar is onepacetr.get_episode_streams
    assert uclar.zengin_ara is onepacetr.zengin_ara


def test_bolum_adresi_ve_slugu_kimlikten():
    kaynak = kayit.bul("One Pace TR")
    kimlik = "wano/hasir-sapkali-luffy-1"
    assert kaynak.bolum_adresi(kimlik) == "https://www.onepacetr.net/wano#hasir-sapkali-luffy-1"
    assert kaynak.bolum_adresi(kimlik) == onepacetr.bolum_adresi(kimlik)
    assert kaynak.bolum_slugu(kimlik) == "one-pace-wano-hasir-sapkali-luffy-1"


def test_kayittan_bolumler_uctan_uca(oturum):
    """Köprü/CLI'ın kullandığı yol: bölüm nesnesi + kimliği kapatan akış sağlayıcı."""
    from turkanime_api.sources.adapter import kayittan_bolumler

    kaynak = kayit.bul("One Pace TR")
    bolumler = kayittan_bolumler(kaynak, "romance-dawn", "One Pace 01: Romance Dawn")

    assert [b.title for b in bolumler][:1] == [
        "1. Sezon 1. Bölüm - Romance Dawn: Maceranın Başlangıcı"]
    ilk = bolumler[0]
    assert ilk.url == "https://www.onepacetr.net/romance-dawn#maceranin-baslangici"
    assert ilk.slug == "one-pace-romance-dawn-maceranin-baslangici"
    akislar = ilk._saglayici()(ilk.url)
    assert [a["player"] for a in akislar] == ["GDRIVE", "SIBNET"]
    assert ilk.fansubs == ["One Pace TR"]


def test_ayni_bolum_seri_ve_ark_girisinde_ayni_slugu_aliyor():
    """İzleme geçmişi/indirme adı arama sonucunun adından bağımsız."""
    from turkanime_api.sources.adapter import kayittan_bolumler

    kaynak = kayit.bul("One Pace TR")
    ark = {b.url: b.slug for b in kayittan_bolumler(kaynak, "romance-dawn", "One Pace 01")}
    seri = {b.url: b.slug for b in kayittan_bolumler(kaynak, "one-pace", "One Pace")}
    assert ark and all(seri[u] == s for u, s in ark.items())


# ─────────────────────────────────────────────────────────────────────────────
# 6) Canlı duman testi (yalnızca --network)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = onepacetr.search_onepacetr("one piece", limit=50)
    assert sonuc and sonuc[0][0] == onepacetr.SERI_KIMLIGI
    assert ("wano", "One Pace 35: Wano") in sonuc

    bolumler = onepacetr.get_anime_episodes("wano")
    assert len(bolumler) >= 50
    akislar = onepacetr.get_episode_streams(bolumler[0][0])
    oynaticilar = [a["player"] for a in akislar]
    assert oynaticilar and set(oynaticilar) <= {"GDRIVE", "SIBNET"}
    for akis in akislar:
        if akis["player"] == "SIBNET":
            assert akis["referer"] == "https://video.sibnet.ru/"
