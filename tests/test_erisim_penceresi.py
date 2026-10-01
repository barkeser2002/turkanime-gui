"""Erişim penceresi (`gui/qt/erisim_penceresi.py`): gerçek QtWebEngine, yerel site.

Yerel sunucu Cloudflare'ın davranışını taklit ediyor: çerezsiz istek
`window._cf_chl_opt` taşıyan bir doğrulama sayfası (403) alıyor; sayfanın
KENDİ betiği kısa süre sonra "/dogrula"ya gidiyor, sunucu `cf_clearance`
yazıp ana sayfaya yönlendiriyor (etkileşimsiz geçen yönetilen doğrulama
gibi). Pencere hiçbir şeye tıklamıyor; yalnızca sayfanın doğrulama olmaktan
çıktığını izliyor. Ölçülenler: çerez + UA yakalanıyor, kalıcı profil oturumu
bir sonraki açılışa taşıyor, kaydı olmayan kaynağın profili sıfırlanıyor,
iptal ve süre dolması doğru bildiriliyor.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from turkanime_api.common import oturumlar
from turkanime_api.common.oturumlar import ErisimHedefi
from turkanime_api.gui.qt import erisim_penceresi as ep

DOGRULAMA = (b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
             b"<script>window._cf_chl_opt={cType:'managed'};"
             b"setTimeout(function(){location.href='/dogrula';},1200);</script>"
             b"<p>Performing security verification</p></body></html>")
ICERIK = (b"<!DOCTYPE html><html><head><title>Yerel Anime</title></head><body>"
          + b"<p>bolum listesi</p>" * 40 + b"</body></html>")


class Site:
    def __init__(self):
        self.istekler = []            # (yol, cf_clearance var mı)
        site = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                cerez = self.headers.get("Cookie") or ""
                site.istekler.append((self.path, "cf_clearance=" in cerez,
                                      self.headers.get("User-Agent") or ""))
                if self.path.startswith("/dogrula"):
                    self.send_response(302)
                    self.send_header("Set-Cookie",
                                     "cf_clearance=YEREL-TEMIZ; Path=/; Max-Age=3600; HttpOnly")
                    self.send_header("Location", "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                govde = ICERIK if "cf_clearance=" in cerez else DOGRULAMA
                self.send_response(200 if govde is ICERIK else 403)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(govde)))
                self.end_headers()
                self.wfile.write(govde)

            def log_message(self, *a):
                pass

        self.sunucu = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.sunucu.serve_forever, daemon=True).start()
        self.adres = f"http://127.0.0.1:{self.sunucu.server_address[1]}/"

    def kapat(self):
        self.sunucu.shutdown()


@pytest.fixture
def site():
    s = Site()
    yield s
    s.kapat()


@pytest.fixture(autouse=True)
def _konak_durumu_temiz():
    oturumlar._konak_durumu.clear()
    yield
    oturumlar._konak_durumu.clear()


def hedef_(site, ad="Deokwave"):
    """Kayıt adı gerçek bir kaynak (depo kanonik ada yazıyor); site yerel."""
    return ErisimHedefi(kaynak=ad, etiket="Yerel", adres=site.adres,
                        alanlar=("127.0.0.1",), profil_adi="yerel")


def ac(qtbot, hedef, **kw):
    dlg = ep.ErisimPenceresi(hedef, **kw)
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


def test_dogrulama_gecince_cerez_ve_kimlik_yakalaniyor(qtbot, site):
    dlg = ac(qtbot, hedef_(site))
    with qtbot.waitSignal(dlg.acildi, timeout=20000) as sinyal:
        dlg.begin()
    sonuc = sinyal.args[0]
    adlar = {c["name"]: c for c in sonuc["cerezler"]}
    assert adlar["cf_clearance"]["value"] == "YEREL-TEMIZ"
    assert adlar["cf_clearance"]["expiry"] > 0 and adlar["cf_clearance"]["httponly"]
    assert "QtWebEngine" in sonuc["user_agent"] and "Chrome/" in sonuc["user_agent"]
    assert sonuc["basliklar"]["sec-ch-ua-mobile"] == "?0"
    assert '"Chromium"' in sonuc["basliklar"]["sec-ch-ua"]
    assert sonuc["dogrulama_goruldu"] is True
    # Sayfaya giden UA ile yakalanan aynı (Cloudflare çerezi UA'ya bağlıyor).
    assert site.istekler[-1][2] == sonuc["user_agent"]
    qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=5000)
    assert dlg.basarili


def test_kalici_profil_ve_kayit_bir_sonraki_acilista_dogrulamasiz(qtbot, site):
    hedef = hedef_(site)
    dlg = ac(qtbot, hedef)
    with qtbot.waitSignal(dlg.acildi, timeout=20000) as sinyal:
        dlg.begin()
    oturumlar.kaydet(hedef.kaynak, cerezler=sinyal.args[0]["cerezler"],
                     user_agent=sinyal.args[0]["user_agent"], alanlar=hedef.alanlar)
    qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=5000)
    # Profil klasörü veri kökünün altında.
    assert ep.profil_dizini(hedef).is_dir()
    assert ep.profil_yuklu_mu(hedef)

    site.istekler.clear()
    ikinci = ac(qtbot, hedef)
    with qtbot.waitSignal(ikinci.acildi, timeout=20000) as sinyal:
        ikinci.begin()
    assert sinyal.args[0]["dogrulama_goruldu"] is False
    assert site.istekler[0][:2] == ("/", True)      # ilk istek zaten çerezli
    assert {c["name"] for c in sinyal.args[0]["cerezler"]} >= {"cf_clearance"}


def test_kaydi_olmayan_kaynagin_profili_sifirlaniyor(qtbot, site):
    """Profilde bilmediğimiz geçerli çerez kalırsa doğrulama görünmez ve
    çerez yakalanamaz; kayıt yoksa profil temiz başlamalı."""
    hedef = hedef_(site)
    dlg = ac(qtbot, hedef)
    with qtbot.waitSignal(dlg.acildi, timeout=20000):
        dlg.begin()
    qtbot.waitUntil(lambda: not dlg.isVisible(), timeout=5000)
    assert oturumlar.kayit(hedef.kaynak) is None            # kaydedilmedi

    site.istekler.clear()
    ikinci = ac(qtbot, hedef)
    with qtbot.waitSignal(ikinci.acildi, timeout=20000) as sinyal:
        ikinci.begin()
    assert site.istekler[0][:2] == ("/", False)             # çerezsiz başladı
    assert sinyal.args[0]["dogrulama_goruldu"] is True
    assert "cf_clearance" in {c["name"] for c in sinyal.args[0]["cerezler"]}


def test_profili_sifirla_yuklu_degilse_klasoru_siliyor(tmp_path):
    hedef = ErisimHedefi(kaynak="Deokwave", etiket="D", adres="https://deokwave.com/",
                         alanlar=("deokwave.com",), profil_adi="hic_yuklenmedi")
    dizin = ep.profil_dizini(hedef)
    (dizin / "Cookies").parent.mkdir(parents=True)
    (dizin / "Cookies").write_bytes(b"x")
    assert not ep.profil_yuklu_mu(hedef)
    ep.profili_sifirla(hedef)
    assert not dizin.exists()


def test_iptal_ve_zaman_asimi(qtbot, site):
    hedef = ErisimHedefi(kaynak="Deokwave", etiket="Yerel", adres=site.adres + "hep-dogrulama",
                         alanlar=("127.0.0.1",), profil_adi="yerel_iptal")
    sonuclar = []
    isci = ep.ErisimIsci(hedef, on_success=lambda s: sonuclar.append(("ok", s)),
                         on_error=lambda m: sonuclar.append(("hata", m)),
                         on_cancel=lambda: sonuclar.append(("iptal",)))
    assert isci.start()
    qtbot.waitUntil(lambda: isci.is_running, timeout=5000)
    isci.start()                                   # ikinci çağrı yeni pencere açmıyor
    isci.stop()
    qtbot.waitUntil(lambda: bool(sonuclar), timeout=5000)
    assert sonuclar == [("iptal",)]

    dlg = ep.ErisimPenceresi(hedef, azami_bekleme=1)
    qtbot.addWidget(dlg)
    dlg.show()
    with qtbot.waitSignal(dlg.failed, timeout=10000) as sinyal:
        dlg.begin()
    assert "Süre doldu" in sinyal.args[0]
    assert not dlg.basarili


def test_ipuclari_ve_alan_suzgeci():
    assert ep.ipuclari_basliklari(
        '{"b":[{"brand":"Not=A?Brand","version":"24"},{"brand":"Chromium","version":"140"}],'
        '"m":false,"p":"Linux"}') == {
            "sec-ch-ua": '"Not=A?Brand";v="24", "Chromium";v="140"',
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Linux"'}
    assert ep.ipuclari_basliklari("") == {} and ep.ipuclari_basliklari(None) == {}
    assert ep.alana_ait({"domain": ".deokwave.com"}, ("deokwave.com",))
    assert ep.alana_ait({"domain": "sw2.deokwave.com"}, ("deokwave.com",))
    assert not ep.alana_ait({"domain": "challenges.cloudflare.com"}, ("deokwave.com",))


def test_kayittaki_cerez_qt_cerezine_ve_geri():
    ilk = {"name": "cf_clearance", "value": "D", "domain": ".deokwave.com", "path": "/",
           "expiry": 1900000000, "secure": True, "httponly": True}
    qc, koken = ep.sozluk_qcookie(ilk)
    assert koken.toString() == "https://deokwave.com/"
    geri = ep.qcookie_sozluk(qc)
    assert geri == ilk
    # Noktasız alan: yalnızca-konak (alan boş bırakılıp köken veriliyor).
    qc, koken = ep.sozluk_qcookie(dict(ilk, domain="deokwave.com"))
    assert qc.domain() == "" and koken.host() == "deokwave.com"
