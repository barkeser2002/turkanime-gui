"""Erişim oturumları deposu (`common/oturumlar.py`) ve HTTP katmanlarına bağlanışı.

"Erişimi aç" penceresinin ürettiği oturum (çerezler + gömülü tarayıcının
kimliği) burada saklanıyor ve kaynakların istekleri onu taşıyor. Ölçülen
kurallar:

* Kayıt `<veri kökü>/oturumlar.json`'da (ayarlar.json'da DEĞİL); süresi dolan
  çerez okurken atılıyor, dosyadan ise bir sonraki yazımda.
* Çerez yalnızca kendi alanının (ve alt alanlarının) isteğine gidiyor;
  yalnızca-konak çerezi alt alana, secure çerez düz http'ye gitmiyor.
* Çerezle birlikte gömülü tarayıcının TAM User-Agent'ı ve istemci ipuçları
  gidiyor (Cloudflare `cf_clearance`'ı UA'ya bağlıyor); curl_cffi profili
  UA'daki Chrome sürümüne çekiliyor.
* Ortak CF zinciri (`CFSession`) ve Deokwave oturumu kullanıyor; Deokwave
  oturumla da doğrulamaya takılırsa "Erişimi aç ile yeniden" diyor.

Ağ yok: veri kökü conftest'in geçici klasörü, HTTP oturumları sahte.
"""
from __future__ import annotations

import json
import time
import types
from pathlib import Path

import pytest

from turkanime_api.common import oturumlar
from turkanime_api.common.hatalar import BotDogrulamasi, KaynakEngellendi, insanlastir

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "QtWebEngine/6.11.2 Chrome/140.0.0.0 Safari/537.36")
IPUCLARI = {"sec-ch-ua": '"Not=A?Brand";v="24", "Chromium";v="140"',
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Linux"'}
FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "deokwave"


def cerez(ad, deger, alan=".deokwave.com", yol="/", bitis=None, secure=True):
    return {"name": ad, "value": deger, "domain": alan, "path": yol,
            "expiry": int(time.time()) + 3600 if bitis is None else bitis,
            "secure": secure}


def deokwave_kaydi(**ek):
    ek.setdefault("cerezler", [cerez("cf_clearance", "TEMIZ"),
                               cerez("PHPSESSID", "S1", alan="deokwave.com", bitis=0)])
    return oturumlar.kaydet("Deokwave", user_agent=UA, alanlar=["deokwave.com"],
                            basliklar=IPUCLARI, **ek)


@pytest.fixture(autouse=True)
def _konak_durumu_temiz():
    """Konak durumu süreç genelinde; testler birbirine sızdırmasın."""
    oturumlar._konak_durumu.clear()
    yield
    oturumlar._konak_durumu.clear()


class Yanit:
    def __init__(self, status_code=200, text="<html>içerik</html>", headers=None, url=""):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = url

    def json(self):
        return json.loads(self.text)


# ─────────────────────────────────────────────────────────────────────────────
# Depo
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_veri_kokunde_ayri_dosyada_kalici():
    from turkanime_api.cli.dosyalar import Dosyalar, veri_koku
    deokwave_kaydi()
    yol = Path(veri_koku()) / "oturumlar.json"
    assert yol.is_file() and oturumlar.dosya_yolu() == yol
    veri = json.loads(yol.read_text(encoding="utf-8"))
    kayit = veri["kaynaklar"]["Deokwave"]
    assert kayit["user_agent"] == UA and kayit["alanlar"] == ["deokwave.com"]
    assert {c["name"] for c in kayit["cerezler"]} == {"cf_clearance", "PHPSESSID"}
    assert kayit["basliklar"] == IPUCLARI and kayit["kaydedildi"] > 0
    # Ayar dosyasına sızmıyor.
    assert "cf_clearance" not in json.dumps(Dosyalar().ayarlar)
    # Önbellek sıfırlansa da (yeni süreç gibi) okunuyor.
    oturumlar._onbellek.update(yol=None, damga=None, veri=None)
    assert oturumlar.kayit("Deokwave")["cerezler"][0]["value"] == "TEMIZ"


def test_takma_ad_kanonik_ada_yaziliyor():
    oturumlar.kaydet("deokwave", cerezler=[cerez("a", "1")], user_agent=UA)
    assert set(oturumlar.kayitlar()) == {"Deokwave"}
    assert oturumlar.kayit("DEOKWAVE") is not None


def test_suresi_dolan_cerez_okurken_atiliyor_dosyadan_yazarken():
    simdi = int(time.time())
    deokwave_kaydi(cerezler=[cerez("cf_clearance", "ESKI", bitis=simdi - 10),
                             cerez("__cf_bm", "TAZE", bitis=simdi + 600)])
    assert [c["name"] for c in oturumlar.kayit("Deokwave")["cerezler"]] == ["__cf_bm"]
    ham = json.loads(oturumlar.dosya_yolu().read_text(encoding="utf-8"))
    assert len(ham["kaynaklar"]["Deokwave"]["cerezler"]) == 2     # dosyada duruyor

    # Bütün çerezleri dolmuş kayıt hiç görünmüyor (istek yine doğrulamaya takılır).
    oturumlar.kaydet("AnimeTR", cerezler=[cerez("cf_clearance", "X", ".animetr.co",
                                                bitis=simdi - 1)], user_agent=UA)
    assert "AnimeTR" not in oturumlar.kayitlar()
    # Bir sonraki yazım dosyayı da temizliyor.
    oturumlar.kaydet("BuguiTR", cerezler=[cerez("a", "1", ".buguitr.com")], user_agent=UA)
    ham = json.loads(oturumlar.dosya_yolu().read_text(encoding="utf-8"))
    assert "AnimeTR" not in ham["kaynaklar"]
    assert [c["name"] for c in ham["kaynaklar"]["Deokwave"]["cerezler"]] == ["__cf_bm"]


def test_sil():
    deokwave_kaydi()
    assert oturumlar.sil("Deokwave") is True
    assert oturumlar.kayit("Deokwave") is None
    assert oturumlar.sil("Deokwave") is False


def test_baska_surecin_yazdigi_goruluyor():
    """Önbellek dosyanın damgasına bağlı: CLI başka süreçte yazarsa GUI görür."""
    deokwave_kaydi()
    assert oturumlar.kayit("Deokwave")["cerezler"][0]["value"] == "TEMIZ"
    yol = oturumlar.dosya_yolu()
    veri = json.loads(yol.read_text(encoding="utf-8"))
    veri["kaynaklar"]["Deokwave"]["cerezler"][0]["value"] = "YENI-DEGER-UZUN"
    yol.write_text(json.dumps(veri), encoding="utf-8")
    assert oturumlar.kayit("Deokwave")["cerezler"][0]["value"] == "YENI-DEGER-UZUN"


def test_bozuk_dosya_bos_sayiliyor():
    yol = oturumlar.dosya_yolu()
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text("{bozuk", encoding="utf-8")
    assert oturumlar.kayitlar() == {}
    assert oturumlar.istek_ekleri("https://deokwave.com/") == ({}, None)


# ─────────────────────────────────────────────────────────────────────────────
# Adrese göre çerez + kimlik
# ─────────────────────────────────────────────────────────────────────────────
def test_alan_eslestirme():
    deokwave_kaydi()
    cerezler, ua = oturumlar.istek_ekleri("https://deokwave.com/api/v1/animes/search/?q=x")
    assert cerezler == {"cf_clearance": "TEMIZ", "PHPSESSID": "S1"} and ua == UA
    # Alan çerezi alt alana gidiyor; yalnızca-konak çerezi gitmiyor.
    assert oturumlar.istek_ekleri("https://sw2.deokwave.com/v/x/1080/")[0] == {
        "cf_clearance": "TEMIZ"}
    # Başka site, benzer ad, düz http (secure çerez) → hiçbiri.
    assert oturumlar.istek_ekleri("https://animetr.co/") == ({}, None)
    assert oturumlar.istek_ekleri("https://evil-deokwave.com/") == ({}, None)
    assert oturumlar.istek_ekleri("http://deokwave.com/")[0] == {}


def test_yol_eslestirme():
    deokwave_kaydi(cerezler=[cerez("yol", "1", yol="/api")])
    assert oturumlar.istek_ekleri("https://deokwave.com/api/v1/x")[0] == {"yol": "1"}
    assert oturumlar.istek_ekleri("https://deokwave.com/api")[0] == {"yol": "1"}
    assert oturumlar.istek_ekleri("https://deokwave.com/apix")[0] == {}


def test_istege_ekle_ua_ipucu_cerez_ve_taklit():
    deokwave_kaydi()
    kw = {"headers": {"user-agent": "kaynagin-kendi-UA", "Referer": "https://deokwave.com/"},
          "cookies": {"PHPSESSID": "KAYNAGIN"}}
    assert oturumlar.istege_ekle("https://deokwave.com/anime/0C61BB4/", kw) is True
    basliklar = kw["headers"]
    # Kaynağın UA'sı (büyük/küçük harf farkıyla bile) gömülü tarayıcınınkiyle değişiyor.
    assert [k for k in basliklar if k.lower() == "user-agent"] == ["User-Agent"]
    assert basliklar["User-Agent"] == UA and basliklar["Referer"] == "https://deokwave.com/"
    assert basliklar["sec-ch-ua-platform"] == '"Linux"'
    assert basliklar["sec-ch-ua"] == IPUCLARI["sec-ch-ua"]
    # Kaynağın kendi verdiği aynı adlı çerez kazanır.
    assert kw["cookies"] == {"cf_clearance": "TEMIZ", "PHPSESSID": "KAYNAGIN"}
    assert kw["impersonate"] == "chrome136"

    duz = {"headers": {}}
    assert oturumlar.istege_ekle("https://deokwave.com/", duz, curl=False)
    assert "impersonate" not in duz                     # düz requests bilmiyor

    bos = {"headers": {"X": "1"}}
    assert oturumlar.istege_ekle("https://animetr.co/", bos) is False
    assert bos == {"headers": {"X": "1"}}                # kayıt yoksa dokunulmuyor


def test_taklit_hedefi_ve_istemci_ipuclari():
    assert oturumlar.taklit_hedefi(UA) == "chrome136"   # 140'ı geçmeyen en yeni
    assert oturumlar.taklit_hedefi("Mozilla/5.0 Chrome/131.0.0.0") == "chrome131"
    assert oturumlar.taklit_hedefi("Mozilla/5.0 Firefox/133.0") is None
    # QtWebEngine 6.11'in gönderdiği başlıkların aynısı (yerel sunucuda ölçüldü).
    assert oturumlar.istemci_ipuclari(UA) == IPUCLARI
    win = oturumlar.istemci_ipuclari(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
        "Gecko) QtWebEngine/6.8.1 Chrome/122.0.6261.171 Safari/537.36")
    assert win["sec-ch-ua-platform"] == '"Windows"' and '"Chromium";v="122"' in win["sec-ch-ua"]


def test_kayitta_ipucu_yoksa_uadan_turetiliyor():
    oturumlar.kaydet("Deokwave", cerezler=[cerez("cf_clearance", "X")], user_agent=UA)
    assert oturumlar.istek_basliklari("https://deokwave.com/") == {**IPUCLARI, "User-Agent": UA}


# ─────────────────────────────────────────────────────────────────────────────
# Doğrulama sayfası, konak durumu, "Erişimi aç" kararı
# ─────────────────────────────────────────────────────────────────────────────
def test_dogrulama_sayfasi_tanima():
    cf = (FIKSTUR / "cf-challenge.html").read_text(encoding="utf-8")
    assert oturumlar.dogrulama_sayfasi_mi(cf)
    bot = "<html><head><title>Bot Verification</title></head><form id='lsrecaptcha-form'>"
    assert oturumlar.dogrulama_sayfasi_mi(bot)
    # Normal sayfaya Cloudflare'ın eklediği betik doğrulama değil.
    assert not oturumlar.dogrulama_sayfasi_mi(
        '<html><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>')
    # Sayfaya gömülü Turnstile kutusu (form) da değil: nowsecure.nl'nin geçilmiş
    # sayfasında ölçüldü, pencere onu doğrulama sanıp kapanmıyordu.
    assert not oturumlar.dogrulama_sayfasi_mi(
        '<form><div class="cf-turnstile" data-sitekey="3x00000000000000000000FF"><div>'
        '<input type="hidden" name="cf-turnstile-response" id="cf-chl-widget-7mp58_response">'
        '</div></div></form>')
    litespeed = (FIKSTUR / "403-litespeed.html").read_text(encoding="utf-8")
    assert not oturumlar.dogrulama_yaniti_mi(Yanit(403, litespeed))
    assert oturumlar.dogrulama_yaniti_mi(Yanit(403, cf))
    assert oturumlar.dogrulama_yaniti_mi(Yanit(503, "", {"cf-mitigated": "challenge"}))
    assert not oturumlar.dogrulama_yaniti_mi(Yanit(200, cf))    # 200: içerik


def test_konak_durumu_dogrulama_sonra_acik():
    cf = (FIKSTUR / "cf-challenge.html").read_text(encoding="utf-8")
    assert not oturumlar.dogrulama_bekliyor("Deokwave")
    assert oturumlar.yanit_denetle("https://deokwave.com/api/v1/x", Yanit(403, cf)) is True
    assert oturumlar.dogrulama_bekliyor("Deokwave")
    assert not oturumlar.dogrulama_bekliyor("AnimeTR")
    # Aynı konaktan düzgün yanıt gelince bayrak iner.
    assert oturumlar.yanit_denetle("https://deokwave.com/", Yanit(200)) is False
    assert not oturumlar.dogrulama_bekliyor("Deokwave")
    # Pencere doğrulamayı geçince de.
    oturumlar.yanit_denetle("https://deokwave.com/", Yanit(403, cf))
    oturumlar.dogrulama_gecildi("Deokwave")
    assert not oturumlar.dogrulama_bekliyor("Deokwave")


def test_erisim_engeli_karari():
    from turkanime_api.common.cf_bypass import CFBypassError
    from turkanime_api.common.hatalar import OturumGerekli
    from turkanime_api.sources.deokwave import DeokwaveDogrulamasi

    assert oturumlar.erisim_engeli_mi(DeokwaveDogrulamasi(), "Deokwave")
    assert oturumlar.erisim_engeli_mi(BotDogrulamasi("x"), "AnimeTR")
    assert oturumlar.erisim_engeli_mi(CFBypassError("çözülemedi"), "AnimeciX")
    try:
        try:
            raise CFBypassError("iç")
        except CFBypassError as ic:
            raise RuntimeError("dış sarmalayıcı") from ic
    except RuntimeError as dis:
        assert oturumlar.erisim_engeli_mi(dis, "Anizle")      # zincirde
    # Metin (arama sayfası istisnayı değil metni görüyor).
    assert oturumlar.erisim_engeli_mi("Cloudflare engeli: site bot doğrulaması istiyor", "Deokwave")
    # Düz 403 ve zaman aşımı doğrulama değil…
    assert not oturumlar.erisim_engeli_mi(RuntimeError("HTTP 403 yasak"), "AnimeTR")
    assert not oturumlar.erisim_engeli_mi("zaman aşımı (25 sn)", "AnimeTR")
    # …ama az önce o konakta doğrulama sayfası görüldüyse evet.
    oturumlar.yanit_denetle("https://animetr.co/seriler", Yanit(403, "<title>Just a moment...</title>"))
    assert oturumlar.erisim_engeli_mi(RuntimeError("AnimeTR isteği engelledi (HTTP 403)"), "AnimeTR")
    # Çerez isteyen kaynakta (TRAnimeİzle) oturum hatası da pencereyle çözülür.
    assert oturumlar.erisim_engeli_mi(OturumGerekli("çerez gir"), "TRAnimeİzle")
    assert oturumlar.erisim_engeli_mi("TRAnimeİzle çerez istiyor: …", "TRAnimeİzle")
    assert not oturumlar.erisim_engeli_mi(OturumGerekli("jeton gir"), "OpenAnime")
    # Penceresi olmayan kaynakta düğme yok.
    assert not oturumlar.erisim_engeli_mi(BotDogrulamasi("x"), "TürkAnime")
    assert not oturumlar.erisim_engeli_mi(BotDogrulamasi("x"), "AniList")


def test_erisim_isaretle_koprunun_okudugu_alani_yaziyor():
    from turkanime_api.sources.deokwave import DeokwaveDogrulamasi
    hata = oturumlar.erisim_isaretle(DeokwaveDogrulamasi(), "deokwave")
    assert hata.erisim_kaynagi == "Deokwave"
    duz = oturumlar.erisim_isaretle(ValueError("x"), "Deokwave")
    assert not hasattr(duz, "erisim_kaynagi")


def test_erisim_hedefleri():
    from turkanime_api.sources import kayit
    for kaynak in kayit.kaynaklar():
        hedef = oturumlar.erisim_hedefi(kaynak.ad)
        if kaynak.ad in ("AniList", "TürkAnime"):
            assert hedef is None, kaynak.ad
            continue
        assert hedef is not None, kaynak.ad
        assert hedef.adres.startswith("https://") and hedef.alanlar, kaynak.ad
        assert "/" not in hedef.profil_adi
    dw = oturumlar.erisim_hedefi("Deokwave")
    assert (dw.adres, dw.alanlar, dw.cerez_akisi) == ("https://deokwave.com/", ("deokwave.com",), False)
    tr = oturumlar.erisim_hedefi("TRAnimeİzle")
    assert tr.cerez_akisi and tr.gerekli_cerezler == {".AitrWeb.Session"}
    assert tr.alanlar == ("tranimeizle.io",)


def test_siniflandirma_dogrulama_ile_duz_engeli_ayiriyor():
    import requests
    from turkanime_api.common.cf_bypass import CFBypassError
    from turkanime_api.common.hatalar import kaynak_hatasi

    def http_hatasi(kod, govde):
        yanit = requests.Response()
        yanit.status_code = kod
        yanit._content = govde.encode()
        return requests.exceptions.HTTPError(f"{kod} Client Error", response=yanit)

    assert isinstance(kaynak_hatasi(CFBypassError("x"), "X"), BotDogrulamasi)
    cf = kaynak_hatasi(http_hatasi(403, "<title>Just a moment...</title>"), "X")
    assert isinstance(cf, BotDogrulamasi) and "Erişimi aç" in str(cf)
    ls = kaynak_hatasi(http_hatasi(403, "<title>Bot Verification</title>"), "X")
    assert isinstance(ls, BotDogrulamasi) and "Cloudflare" not in str(ls)
    blok = kaynak_hatasi(http_hatasi(403, "Attention Required! | Cloudflare"), "X")
    assert isinstance(blok, KaynakEngellendi) and not isinstance(blok, BotDogrulamasi)
    assert "Erişimi aç" in insanlastir(CFBypassError("x"))[0]


# ─────────────────────────────────────────────────────────────────────────────
# Oturum sarmalayıcı (kaynak modüllerinin oturum fabrikaları)
# ─────────────────────────────────────────────────────────────────────────────
class KayitliOturum:
    """curl_cffi oturumunun kullandığımız kadarı; çağrıları kaydeder."""

    def __init__(self, yanit=None):
        self.cagrilar = []
        self.yanit = yanit or Yanit()
        self.headers = {"User-Agent": "yedek"}
        self.kapandi = False

    def get(self, url, **kw):
        self.cagrilar.append(("GET", url, kw))
        return self.yanit

    def post(self, url, **kw):
        self.cagrilar.append(("POST", url, kw))
        return self.yanit

    def close(self):
        self.kapandi = True


def test_sarmalayici_kayit_yoksa_dokunmuyor_varsa_ekliyor():
    ic = KayitliOturum()
    oturum = oturumlar.oturumlu(ic)
    assert oturumlar.oturumlu(oturum) is oturum            # iki kez sarılmıyor
    oturum.get("https://deokwave.com/", headers={"A": "1"}, timeout=5)
    assert ic.cagrilar[-1][2] == {"headers": {"A": "1"}, "timeout": 5}

    deokwave_kaydi()
    oturum.post("https://deokwave.com/api", data={"q": 1})
    kw = ic.cagrilar[-1][2]
    assert kw["cookies"]["cf_clearance"] == "TEMIZ" and kw["headers"]["User-Agent"] == UA
    assert kw["impersonate"] == "chrome136" and kw["data"] == {"q": 1}
    # Başka konağa (video CDN'i) oturum gitmiyor.
    oturum.get("https://video.sibnet.ru/x.mp4")
    assert "cookies" not in ic.cagrilar[-1][2]
    # Öznitelikler alttaki oturumun.
    assert oturum.headers == {"User-Agent": "yedek"}
    with oturum:
        pass
    assert ic.kapandi


def test_sarmalayici_dogrulamayi_not_ediyor_akista_govdeyi_okumuyor():
    cf = (FIKSTUR / "cf-challenge.html").read_text(encoding="utf-8")
    oturumlar.oturumlu(KayitliOturum(Yanit(403, cf))).get("https://animetr.co/x")
    assert oturumlar.dogrulama_bekliyor("AnimeTR")

    class AkisYaniti(Yanit):
        @property
        def text(self):
            raise AssertionError("akış yanıtının gövdesi okundu")

        @text.setter
        def text(self, _deger):
            pass

    oturumlar.oturumlu(KayitliOturum(AkisYaniti(403))).get(
        "https://buguitr.com/v.mp4", stream=True)          # patlamıyor


def test_kaynak_fabrikalari_sarili(monkeypatch):
    """Kendi HTTP kodu olan kaynaklar erişim oturumunu taşıyor (tek satırlık bağ)."""
    import importlib
    deokwave_kaydi()
    oturumlar.kaydet("AnimeTR", cerezler=[cerez("cf_clearance", "AT", ".animetr.co")],
                     user_agent=UA)
    for ad in ("animetr", "asyaanimeleri", "animeler", "animexe", "animezer", "animom",
               "animpow", "buguitr", "onepacetr", "seicode"):
        modul = importlib.import_module(f"turkanime_api.sources.{ad}")
        ic = KayitliOturum()
        monkeypatch.setattr(modul, "_http", types.SimpleNamespace(Session=lambda *a, _i=ic, **k: _i))
        oturum = modul._yeni_oturum()
        assert isinstance(oturum, oturumlar.OturumluIstemci), ad
    from turkanime_api.sources import animetr
    ic = KayitliOturum(Yanit(200, "<html>seri</html>"))
    monkeypatch.setattr(animetr, "_http", types.SimpleNamespace(Session=lambda *a, **k: ic))
    monkeypatch.setattr(animetr, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(animetr, "_yerel", types.SimpleNamespace())
    animetr._get("/seri/one-piece")
    kw = ic.cagrilar[-1][2]
    assert kw["cookies"] == {"cf_clearance": "AT"} and kw["headers"]["User-Agent"] == UA
    assert kw["headers"]["Referer"] == animetr.BASE_URL + "/"


# ─────────────────────────────────────────────────────────────────────────────
# Ortak CF zinciri
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def sahte_curl(monkeypatch):
    """`cf_bypass.curl_requests.Session` sahtesi: istekleri kaydeder."""
    from turkanime_api.common import cf_bypass as cf
    kayit = types.SimpleNamespace(istekler=[], yanit=None)

    class Oturum:
        def __init__(self, *a, **k):
            pass

        def request(self, yontem, url, **kw):
            kayit.istekler.append((yontem, url, kw))
            return kayit.yanit

    monkeypatch.setattr(cf, "curl_requests", types.SimpleNamespace(Session=Oturum))
    monkeypatch.setattr(cf, "HAS_CURL_CFFI", True)
    return kayit


def _zinciri_kaydet(monkeypatch, oturum):
    cagrilar = []
    for ad in ("_try_curl_cffi", "_try_cloudscraper", "_try_flaresolverr",
               "_try_qtwebengine", "_try_requests_fallback"):
        monkeypatch.setattr(oturum, ad, lambda *a, _ad=ad, **k: cagrilar.append(_ad))
    return cagrilar


def test_cfsession_once_erisim_oturumunu_deniyor(monkeypatch, sahte_curl):
    import requests
    from turkanime_api.common import cf_bypass as cf
    deokwave_kaydi()
    yanit = requests.Response()
    yanit.status_code = 200
    yanit._content = b'{"success": true}'
    sahte_curl.yanit = yanit
    oturum = cf.CFSession(flaresolverr_url="", retry_delay=0)
    zincir = _zinciri_kaydet(monkeypatch, oturum)

    assert oturum.get("https://deokwave.com/api/v1/x", headers={"X": "1"}, timeout=7) is yanit
    assert zincir == [] and oturum.last_method == "erisim_oturumu"
    yontem, _url, kw = sahte_curl.istekler[0]
    assert yontem == "GET" and kw["cookies"]["cf_clearance"] == "TEMIZ"
    assert kw["headers"]["User-Agent"] == UA and kw["headers"]["X"] == "1"
    assert kw["impersonate"] == "chrome136" and kw["timeout"] == 7

    sahte_curl.istekler.clear()
    oturum.post("https://deokwave.com/api", data={"a": 1})
    assert sahte_curl.istekler[0][0] == "POST" and sahte_curl.istekler[0][2]["data"] == {"a": 1}


def test_cfsession_kayit_yoksa_zincir_eskisi_gibi(monkeypatch, sahte_curl):
    from turkanime_api.common import cf_bypass as cf
    oturum = cf.CFSession(flaresolverr_url="", retry_delay=0, max_retries=1)
    zincir = _zinciri_kaydet(monkeypatch, oturum)
    with pytest.raises(cf.CFBypassError):
        oturum.get("https://deokwave.com/")
    assert sahte_curl.istekler == []
    assert zincir[0] == "_try_curl_cffi"


def test_cfsession_oturum_reddedilirse_zincir_suruyor(monkeypatch, sahte_curl):
    import requests
    from turkanime_api.common import cf_bypass as cf
    deokwave_kaydi()
    dogrulama = requests.Response()
    dogrulama.status_code = 403
    dogrulama._content = (FIKSTUR / "cf-challenge.html").read_bytes()
    dogrulama.headers["cf-mitigated"] = "challenge"
    sahte_curl.yanit = dogrulama
    oturum = cf.CFSession(flaresolverr_url="", retry_delay=0, max_retries=1)
    zincir = _zinciri_kaydet(monkeypatch, oturum)
    with pytest.raises(cf.CFBypassError):
        oturum.get("https://deokwave.com/")
    assert len(sahte_curl.istekler) == 1 and zincir[0] == "_try_curl_cffi"
    assert oturumlar.dogrulama_bekliyor("Deokwave")        # arayüz düğmeyi göstersin


# ─────────────────────────────────────────────────────────────────────────────
# Deokwave
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def deokwave_sitesi(monkeypatch):
    """Deokwave'in sahte sitesi: istekler (kwargs'larıyla) kaydediliyor."""
    from turkanime_api.sources import deokwave as dw
    kayit = types.SimpleNamespace(istekler=[], tablo={})

    class Oturum:
        def __init__(self, profil):
            self.profil = profil

        def get(self, url, params=None, **kw):
            kayit.istekler.append({"url": url, "params": params, "profil": self.profil, **kw})
            yol = url[len(dw.BASE_URL):]
            cevap = kayit.tablo.get(yol)
            return cevap(kw) if callable(cevap) else cevap or Yanit(404, "yok")

    monkeypatch.setattr(dw, "_yeni_oturum", Oturum)
    monkeypatch.setattr(dw, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(dw, "_oturum", None)
    monkeypatch.setattr(dw, "_profil_sirasi", 0)
    monkeypatch.setattr(dw, "_token", None)
    return kayit


def _cf_yaniti():
    return Yanit(403, (FIKSTUR / "cf-challenge.html").read_text(encoding="utf-8"),
                 {"cf-mitigated": "challenge"})


def test_deokwave_oturumla_istek_atiyor_ve_geciyor(deokwave_sitesi):
    from turkanime_api.sources import deokwave as dw
    frieren = (FIKSTUR / "search_v1-frieren.json").read_text(encoding="utf-8")

    def arama(kw):
        # Cloudflare'ın bakacağı üçlü: çerez + aynı UA (+ ipuçları).
        if (kw.get("cookies") or {}).get("cf_clearance") == "TEMIZ" \
                and kw["headers"].get("User-Agent") == UA:
            return Yanit(200, frieren)
        return _cf_yaniti()
    deokwave_sitesi.tablo["/api/v1/animes/search/"] = arama

    with pytest.raises(dw.DeokwaveDogrulamasi) as hata:     # oturum yok
        dw.search_deokwave("frieren")
    assert "Erişimi aç" in str(hata.value) and not hata.value.oturumlu
    assert isinstance(hata.value, BotDogrulamasi)
    assert oturumlar.dogrulama_bekliyor("Deokwave")

    deokwave_kaydi()
    sonuc = dw.search_deokwave("frieren")
    assert sonuc and sonuc[0][0] == "0C61BB4"
    son = deokwave_sitesi.istekler[-1]
    assert son["headers"]["Referer"] == dw.REFERER
    assert son["headers"]["sec-ch-ua"] == IPUCLARI["sec-ch-ua"]
    assert son["impersonate"] == "chrome136"
    assert not oturumlar.dogrulama_bekliyor("Deokwave")    # site artık açık


def test_deokwave_oturum_reddedilince_yeniden_erisim_istiyor(deokwave_sitesi):
    from turkanime_api.sources import deokwave as dw
    from turkanime_server.crawler.nezaket import ENGELLENME, hata_turu
    deokwave_kaydi()
    deokwave_sitesi.tablo["/anime/0C61BB4/"] = lambda kw: _cf_yaniti()

    with pytest.raises(dw.DeokwaveDogrulamasi) as hata:
        dw.get_anime_episodes("0C61BB4")

    assert hata.value.oturumlu and "kabul etmedi" in str(hata.value)
    assert "Erişimi aç" in str(hata.value)
    # Oturumla gitti, profil döndürülmedi, tek istek.
    assert len(deokwave_sitesi.istekler) == 1
    assert deokwave_sitesi.istekler[0]["cookies"]["cf_clearance"] == "TEMIZ"
    # Sunucu tarayıcısı ve arayüz sınıflandırması aynı.
    assert hata.value.status_code == 403 and hata_turu(hata.value) == ENGELLENME
    assert insanlastir(hata.value)[0] == str(hata.value)
    assert oturumlar.erisim_engeli_mi(hata.value, "Deokwave")
