""""Erişimi aç": köprü uçları (`gui/web/uclar_erisim.py`) ve sayfalardaki düğme.

Gömülü tarayıcı penceresi burada sahte (`_isci_kur`): pencerenin kendisi
`test_erisim_penceresi.py`'de gerçek QtWebEngine'le sınanıyor. Burada ölçülen
akış: bot doğrulamasına takılan kaynağın hatasının yanında düğme çıkıyor
(arama sayfası, detaydaki kaynak akordiyonu, oynatma bildirimi), düğme
pencereyi açıyor, erişim açılınca sayfa YALNIZCA o kaynağı yeniden deniyor;
Ayarlar kayıtlı oturumları listeliyor ve "Temizle" siliyor. Düz hata
(zaman aşımı) düğme göstermiyor.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from turkanime_api.common import oturumlar
from turkanime_api.gui.web.kopru import UcHatasi
from turkanime_api.gui.web.uclar_erisim import (
    ErisimUclari, gecerlilik_metni, oturum_satirlari, yas_metni,
)
from turkanime_api.sources.deokwave import DeokwaveDogrulamasi

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "QtWebEngine/6.11.2 Chrome/140.0.0.0 Safari/537.36")


def cf_cerezi(deger="PENCERE"):
    return {"name": "cf_clearance", "value": deger, "domain": ".deokwave.com",
            "path": "/", "expiry": int(time.time()) + 3600, "secure": True}


@pytest.fixture(autouse=True)
def _konak_durumu_temiz():
    oturumlar._konak_durumu.clear()
    yield
    oturumlar._konak_durumu.clear()


class SahteIsci:
    """Pencere yerine: `start` sonucu kısa süre sonra bildirir (kullanıcı çözdü)."""

    def __init__(self, sonuc):
        self.sonuc = sonuc
        self.baslatma = 0
        self.one = 0

    def start(self):
        from PySide6.QtCore import QTimer
        self.baslatma += 1
        if self.sonuc is not None:
            QTimer.singleShot(30, self.sonuc)
        return True

    def one_getir(self):
        self.one += 1


def pencereyi_sahtele(uclar, cerezler=None):
    """`uclar._isci_kur`'u değiştir; kurulan işçiler listesi döner."""
    kurulan = []

    def kur(hedef):
        isci = SahteIsci(lambda: uclar._kaydet(hedef, {
            "cerezler": [cf_cerezi()] if cerezler is None else cerezler,
            "user_agent": UA, "basliklar": {}}))
        kurulan.append(isci)
        return isci
    uclar._isci_kur = kur
    return kurulan


# ─────────────────────────────────────────────────────────────────────────────
# Uçlar (sayfasız)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def uclar(qtbot):
    from conftest import SahteKopru
    kopru = SahteKopru()
    u = ErisimUclari(kopru)
    u.kopru = kopru
    return u


def test_penceresi_olmayan_kaynak_reddediliyor(uclar):
    for ad in ("TürkAnime", "AniList", "YokBöyleKaynak"):
        with pytest.raises(UcHatasi, match="erişim penceresi yok"):
            uclar.erisim_ac(ad, istek=1)


def test_basari_kaydediyor_ve_bekleyen_her_istege_bildiriyor(uclar, qtbot):
    kurulan = []

    def kur(hedef):
        isci = SahteIsci(None)
        isci.hedef = hedef
        kurulan.append(isci)
        return isci
    uclar._isci_kur = kur

    assert uclar.erisim_ac("deokwave", istek=7) == {"istek": 7, "kaynak": "Deokwave"}
    # Pencere açıkken ikinci tık yeni pencere açmıyor, öne getiriyor.
    assert uclar.erisim_ac("Deokwave", istek=8)["zaten_acik"] is True
    assert len(kurulan) == 1 and kurulan[0].one == 1 and uclar.acik_mi("Deokwave")

    uclar._kaydet(kurulan[0].hedef, {"cerezler": [cf_cerezi()], "user_agent": UA,
                                     "basliklar": {"sec-ch-ua-mobile": "?0"}})

    kayit = oturumlar.kayit("Deokwave")
    assert kayit["user_agent"] == UA and kayit["alanlar"] == ["deokwave.com"]
    assert kayit["basliklar"] == {"sec-ch-ua-mobile": "?0"}
    sonuclar = uclar.kopru.hepsi("erisim_sonuc")
    assert [s["istek"] for s in sonuclar] == [7, 8]
    assert all(s["basarili"] and not s["iptal"] and s["kaynak"] == "Deokwave" for s in sonuclar)
    assert uclar.kopru.son("erisim_degisti") == {"kaynak": "Deokwave"}
    assert not uclar.acik_mi("Deokwave")


def test_iptal_ve_cerezsiz_basari(uclar):
    # Sonucu test veriyor: kendiliğinden bildiren sahte işçinin zamanlayıcısı
    # sonraki testin olay döngüsünde patlayıp onun veri köküne yazıyordu.
    kurulan = []
    uclar._isci_kur = lambda hedef: kurulan.append(SahteIsci(None)) or kurulan[-1]
    uclar.erisim_ac("Deokwave", istek=1)
    uclar._iptal(oturumlar.erisim_hedefi("Deokwave"))
    son = uclar.kopru.son("erisim_sonuc")
    assert son["iptal"] and not son["basarili"] and oturumlar.kayit("Deokwave") is None
    assert len(kurulan) == 1

    # Tarayıcı doğrulamasız girdiyse: başarı, ama kaydedilecek oturum yok.
    uclar.erisim_ac("Deokwave", istek=2)
    uclar._kaydet(oturumlar.erisim_hedefi("Deokwave"), {"cerezler": [], "user_agent": UA})
    son = uclar.kopru.son("erisim_sonuc")
    assert son["basarili"] and "doğrulama istemedi" in son["mesaj"]
    assert oturumlar.kayit("Deokwave") is None


def test_tranime_cerezi_ayarlarin_yolundan_kaydediliyor(uclar):
    """TRAnimeİzle: eski çerez akışı; Netscape metni Ayarlar'ın yoluna gider."""
    kaydedilen = []
    uclar._tranime_cerez = kaydedilen.append
    hedef = oturumlar.erisim_hedefi("TRAnimeİzle")
    isci = uclar._isci_kur(hedef)
    from turkanime_api.gui.qt.cookie_browser import CookieBrowserWorker
    assert isinstance(isci, CookieBrowserWorker)
    uclar._acik["TRAnimeİzle"] = {"isci": isci, "istekler": [3]}
    uclar._tranime_geldi(hedef, "# Netscape\n.tranimeizle.io\tTRUE\t/\tTRUE\t0\t.AitrWeb.Session\tX\n")
    assert len(kaydedilen) == 1 and ".AitrWeb.Session" in kaydedilen[0]
    son = uclar.kopru.son("erisim_sonuc")
    assert son["istek"] == 3 and son["basarili"]
    assert oturumlar.kayit("TRAnimeİzle") is None           # oturumlar.json'a değil


def test_temizle_kaydi_ve_profili_siliyor(uclar, monkeypatch):
    from turkanime_api.gui.qt import erisim_penceresi
    sifirlanan = []
    monkeypatch.setattr(erisim_penceresi, "profili_sifirla",
                        lambda hedef: sifirlanan.append(hedef.kaynak))
    oturumlar.kaydet("Deokwave", cerezler=[cf_cerezi()], user_agent=UA,
                     alanlar=["deokwave.com"])
    assert [s["kaynak"] for s in uclar.erisim_oturumlari()] == ["Deokwave"]
    assert uclar.erisim_temizle("Deokwave") == []
    assert oturumlar.kayit("Deokwave") is None and sifirlanan == ["Deokwave"]
    assert uclar.kopru.son("erisim_degisti") == {"kaynak": "Deokwave"}


def test_engel_bildir_ve_yeniden_dene(uclar):
    calisan = []
    assert uclar.engel_bildir(DeokwaveDogrulamasi(), "Deokwave", baslik="Frieren 1",
                              yeniden=lambda: calisan.append(1)) is True
    olay = uclar.kopru.son("erisim_gerekli")
    assert olay["kaynak"] == "Deokwave" and olay["anahtar"]
    assert olay["mesaj"].startswith("Frieren 1 — Deokwave") and "Erişimi aç" in olay["mesaj"]
    assert uclar.erisim_yeniden(olay["anahtar"]) is True and calisan == [1]
    with pytest.raises(UcHatasi):
        uclar.erisim_yeniden(olay["anahtar"])                 # bir kez
    # Doğrulama olmayan hata, penceresi olmayan kaynak: bildirim yok.
    assert uclar.engel_bildir(TimeoutError("zaman aşımı"), "Deokwave") is False
    assert uclar.engel_bildir(DeokwaveDogrulamasi(), "TürkAnime") is False
    assert len(uclar.kopru.hepsi("erisim_gerekli")) == 1


def test_bekleyen_isler_sinirli(uclar):
    from turkanime_api.gui.web import uclar_erisim
    for _ in range(uclar_erisim.AZAMI_BEKLEYEN + 5):
        uclar.engel_bildir(DeokwaveDogrulamasi(), "Deokwave", yeniden=lambda: None)
    assert len(uclar._bekleyen) == uclar_erisim.AZAMI_BEKLEYEN


def test_satir_metinleri():
    assert yas_metni(5) == "az önce" and yas_metni(125) == "2 dk önce"
    assert yas_metni(3 * 3600 + 5) == "3 sa önce" and yas_metni(2 * 86400) == "2 gün önce"
    assert gecerlilik_metni({"cerezler": [{"name": "PHPSESSID", "expiry": 0}]}).startswith("süresiz")
    metin = gecerlilik_metni({"cerezler": [
        {"name": "cf_clearance", "expiry": 1900000000},
        {"name": "__cf_bm", "expiry": 1999999999}]})
    assert metin.endswith("'e kadar") and "2030" in metin      # cf_clearance'ın bitişi
    oturumlar.kaydet("Deokwave", cerezler=[cf_cerezi()], user_agent=UA,
                     zaman=time.time() - 7200)
    satir = oturum_satirlari()[0]
    assert (satir["kaynak"], satir["etiket"], satir["yas"], satir["cerez"]) == \
        ("Deokwave", "Deokwave", "2 sa önce", 1)
    assert satir["renk"]


# ─────────────────────────────────────────────────────────────────────────────
# Sayfalar (gerçek QtWebEngine)
# ─────────────────────────────────────────────────────────────────────────────
def kayit_(slug, baslik):
    return {"slug": slug, "title": baslik, "image": None}


@pytest.fixture
def engelli_motor(monkeypatch):
    """Deokwave oturum yokken bot doğrulamasına takılan, AnimeTR hep zaman aşımına
    uğrayan arama motoru. Çağrılan kaynak kümeleri kaydediliyor."""
    import turkanime_api.common.adapters as adapters_mod
    cagrilar: list = []

    class Motor:
        def __init__(self):
            self.adapters = {"TürkAnime": None, "Deokwave": None, "AnimeTR": None}

        def artimli_ara(self, query, kaynak_bitti, limit_per_source=10):
            cagrilar.append(sorted(self.adapters))
            sonuc, hatalar = {}, {}
            for ad in self.adapters:
                if ad == "Deokwave" and oturumlar.kayit("Deokwave") is None:
                    hatalar[ad] = str(DeokwaveDogrulamasi())
                elif ad == "AnimeTR":
                    hatalar[ad] = "zaman aşımı: sunucu zamanında yanıt vermedi"
                else:
                    sonuc[ad] = [kayit_(f"{ad.lower()}-1", f"{ad} Frieren")]
                kaynak_bitti(ad, sonuc.get(ad, []), hatalar.get(ad))
            return adapters_mod.AramaSonuclari(sonuc, hatalar=hatalar)

    monkeypatch.setattr(adapters_mod, "SearchEngine", Motor)
    return cagrilar


DEOKWAVE_DUGMESI = ".arama-uyari .erisim-dugme[data-erisim='Deokwave']"


def test_arama_dugmesi_yalniz_dogrulamada_ve_yalniz_o_kaynagi_yeniden_ariyor(
        main_window, web, engelli_motor):
    kurulan = pencereyi_sahtele(main_window.erisim)
    main_window.ara("frieren")
    web.bekle(f"!!document.querySelector(\"{DEOKWAVE_DUGMESI}\")")
    # Zaman aşımına uğrayan AnimeTR'nin sebebi var ama düğmesi yok.
    assert "zaman aşımı" in web.js("document.querySelector('.arama-uyari').innerText")
    assert web.js("document.querySelectorAll('.arama-uyari .erisim-dugme').length") == 1
    assert web.js("Array.from(document.querySelectorAll('.sonuc-grubu')).map(g => g.dataset.kaynak)") \
        == ["TürkAnime"]

    web.js(f"document.querySelector(\"{DEOKWAVE_DUGMESI}\").click()")
    web.bekle("!!document.querySelector('.sonuc-grubu[data-kaynak=\"Deokwave\"]')")
    assert len(kurulan) == 1
    assert engelli_motor == [["AnimeTR", "Deokwave", "TürkAnime"], ["Deokwave"]]
    # Sayfa sıfırlanmadı: TürkAnime kartı yerinde, Deokwave artık hatalı değil.
    gruplar = web.js("Array.from(document.querySelectorAll('.sonuc-grubu')).map(g => g.dataset.kaynak)")
    assert gruplar == ["TürkAnime", "Deokwave"]
    web.bekle("!document.querySelector('.arama-uyari .erisim-dugme')")
    assert "Deokwave" not in web.js("document.querySelector('.arama-uyari').innerText")
    assert "zaman aşımı" in web.js("document.querySelector('.arama-uyari').innerText")
    assert oturumlar.kayit("Deokwave")["cerezler"][0]["value"] == "PENCERE"


def test_arama_sonuc_yokken_listede_dugme(main_window, web, monkeypatch):
    import turkanime_api.common.adapters as adapters_mod

    class Motor:
        adapters = {"Deokwave": None}

        def artimli_ara(self, query, kaynak_bitti, limit_per_source=10):
            sebep = str(DeokwaveDogrulamasi())
            kaynak_bitti("Deokwave", [], sebep)
            return adapters_mod.AramaSonuclari({}, hatalar={"Deokwave": sebep})

    monkeypatch.setattr(adapters_mod, "SearchEngine", Motor)
    # Kullanıcı pencereyi doğrulamayı geçmeden kapatıyor.
    main_window.erisim._isci_kur = lambda hedef: SahteIsci(
        lambda: main_window.erisim._iptal(hedef))
    main_window.ara("frieren")
    dugme = ".arama-bos .hata-listesi li[data-kaynak='Deokwave'] .erisim-dugme"
    web.bekle(f"!!document.querySelector(\"{dugme}\")")
    web.js(f"document.querySelector(\"{dugme}\").click()")
    # Kullanıcı pencereyi kapattı: düğme yeniden kullanılabilir, bilgi bildirimi.
    web.bekle(f"document.querySelector(\"{dugme}\").disabled === false")
    web.bekle("Array.from(document.querySelectorAll('.bildirim')).some("
              "b => b.innerText.includes('Erişim penceresi kapatıldı'))")


def test_detay_akordiyonunda_dugme_ve_bolumler_yeniden(main_window, web, sahte_bolumler):
    kurulan = pencereyi_sahtele(main_window.erisim)

    def bolumler(_kimlik):
        if oturumlar.kayit("Deokwave") is None:
            raise DeokwaveDogrulamasi()
        return [{"title": "1. Bölüm", "obj": object()}, {"title": "2. Bölüm", "obj": object()}]
    cagrilar = sahte_bolumler({"Deokwave": bolumler})

    main_window._on_anime_selected("Deokwave", "0C61BB4", "Frieren")
    dugme = ".akordiyon[data-kaynak='Deokwave'] .bolum-hata .erisim-dugme"
    web.bekle(f"!!document.querySelector(\"{dugme}\")", timeout=8000)
    assert "Erişimi aç" in web.js(
        "document.querySelector(\".akordiyon[data-kaynak='Deokwave'] .bolum-hata\").innerText")
    web.js(f"document.querySelector(\"{dugme}\").click()")
    web.detay_bekle("Deokwave", 2)
    assert len(kurulan) == 1 and len(cagrilar) == 2


def test_detay_duz_hatada_dugme_yok(main_window, web, sahte_bolumler):
    sahte_bolumler({"Deokwave": TimeoutError("read timed out")})
    main_window._on_anime_selected("Deokwave", "0C61BB4", "Frieren")
    web.bekle("!!document.querySelector(\".akordiyon[data-kaynak='Deokwave'] .bolum-hata\")",
              timeout=8000)
    assert web.js("document.querySelectorAll('.bolum-hata .erisim-dugme').length") == 0


class _EngelliBolum:
    slug = "frieren-1"
    anime = SimpleNamespace(slug="frieren", title="Frieren")
    url = "https://deokwave.com/watch/0C61BB4/season/1/episode/1"

    def __init__(self):
        self.deneme = 0

    def best_video(self, **_k):
        self.deneme += 1
        raise DeokwaveDogrulamasi()


def test_oynatma_engelinde_bildirim_ve_yeniden_oynatma(main_window, web, qtbot, monkeypatch):
    from turkanime_api.gui.qt import prefs
    monkeypatch.setattr(prefs, "oynat", lambda *a, **k: pytest.fail("mpv açılmamalı"))
    pencereyi_sahtele(main_window.erisim)
    bolum = _EngelliBolum()
    entry = {"title": "Frieren 1. Bölüm", "obj": bolum, "kaynak": "Deokwave",
             "kimlik": "0C61BB4"}

    main_window._on_play(entry)
    dugme = ".erisim-bildirimi[data-kaynak='Deokwave'] .erisim-dugme"
    web.bekle(f"!!document.querySelector(\"{dugme}\")", timeout=8000)
    assert "Frieren 1. Bölüm" in web.js("document.querySelector('.erisim-bildirimi').innerText")
    qtbot.waitUntil(lambda: not main_window._playing, timeout=5000)

    web.js(f"document.querySelector(\"{dugme}\").click()")
    qtbot.waitUntil(lambda: bolum.deneme == 2, timeout=8000)    # yeniden oynatıldı
    assert oturumlar.kayit("Deokwave") is not None
    # İkinci oynatmanın arka plan işi bitmeden pencere yıkılmasın: iş GUI
    # thread'ine mesaj gönderiyor, yıkılan pencereye giden mesaj süreci düşürür.
    qtbot.waitUntil(lambda: not main_window._playing, timeout=8000)
    web.bekle("document.querySelectorAll('.erisim-bildirimi').length === 1")
    qtbot.wait(100)


def test_indirme_engelinde_bildirim_ve_yeniden_kuyruk(main_window, web, qtbot, monkeypatch):
    """İndirme bot doğrulamasına takıldıysa aynı teklif; erişim açılınca iş
    aynı satırda yeniden kuyruğa girer (düz hata teklif üretmez)."""
    pencereyi_sahtele(main_window.erisim)
    yenilenen = []
    indirmeler = main_window.downloads
    monkeypatch.setattr(indirmeler, "kayit", lambda tid: {"title": "Frieren 2. Bölüm",
                                                          "kaynak": "Deokwave"})
    monkeypatch.setattr(indirmeler, "ayrinti", lambda tid: (
        "DeokwaveDogrulamasi: " + str(DeokwaveDogrulamasi()) if tid == "i1" else "TimeoutError: x"))
    monkeypatch.setattr(indirmeler, "retry", lambda tid: yenilenen.append(tid) or tid)

    main_window._on_download_finished("i2", False, "zaman aşımı")
    main_window._on_download_finished("i1", False, "Deokwave bot doğrulaması")
    dugme = ".erisim-bildirimi[data-kaynak='Deokwave'] .erisim-dugme"
    web.bekle(f"!!document.querySelector(\"{dugme}\")")
    assert web.js("document.querySelectorAll('.erisim-bildirimi').length") == 1
    web.js(f"document.querySelector(\"{dugme}\").click()")
    qtbot.waitUntil(lambda: yenilenen == ["i1"], timeout=5000)


def test_ayarlarda_erisim_oturumlari_ve_temizle(main_window, web, monkeypatch):
    from turkanime_api.gui.qt import erisim_penceresi
    monkeypatch.setattr(erisim_penceresi, "profili_sifirla", lambda hedef: None)
    oturumlar.kaydet("Deokwave", cerezler=[cf_cerezi()], user_agent=UA,
                     alanlar=["deokwave.com"], zaman=time.time() - 600)
    main_window.show_page("settings")
    satir = ".erisim-oturumlari .erisim-satiri[data-kaynak='Deokwave']"
    web.bekle(f"!!document.querySelector(\"{satir}\")")
    metin = web.js(f"document.querySelector(\"{satir}\").innerText")
    assert "Deokwave" in metin and "10 dk önce kaydedildi" in metin and "1 çerez" in metin

    web.js(f"document.querySelector(\"{satir} .erisim-temizle\").click()")
    web.bekle("!!document.querySelector('.erisim-oturumlari .erisim-bos')")
    assert oturumlar.kayit("Deokwave") is None


def test_ayarlar_listesi_erisim_acilinca_tazeleniyor(main_window, web):
    main_window.show_page("settings")
    web.bekle("!!document.querySelector('.erisim-oturumlari .erisim-bos')")
    oturumlar.kaydet("Deokwave", cerezler=[cf_cerezi()], user_agent=UA)
    main_window.kopru.yay("erisim_degisti", {"kaynak": "Deokwave"})
    web.bekle("!!document.querySelector(\".erisim-satiri[data-kaynak='Deokwave']\")")
