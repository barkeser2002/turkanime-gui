"""Gerçek tarayıcı erişim motoru (`common/tarayici_oturum.py`), Qt'siz.

selenium/undetected-chromedriver sandbox'ta yok; sürücü ENJEKTE ediliyor
(sahte). Saat ve uyku da enjekte: zaman aşımı gerçekten beklemeden sınanıyor.
İlke sınanıyor: pencere hiçbir şeye tıklamıyor, yalnızca kullanıcının çözdüğü
oturumu okuyor.
"""
from __future__ import annotations

import pytest

from turkanime_api.common import tarayici_oturum as to
from turkanime_api.common.oturumlar import ErisimHedefi


def hedef(gerekli=frozenset(), alan="ornek.test"):
    return ErisimHedefi(kaynak="Deokwave", etiket="Deokwave",
                        adres=f"https://{alan}/", alanlar=(alan,),
                        profil_adi="deokwave", gerekli_cerezler=gerekli)


DOGRULAMA = "<html><head><title>Just a moment...</title></head>" \
            "<body><script>window._cf_chl_opt={}</script></body></html>"
TEMIZ = "<html><head><title>Anime</title></head><body>icerik</body></html>"
HINTS = {"platform": "Windows", "mobile": False,
         "fullVersionList": [{"brand": "Chromium", "version": "140.0.0.0"},
                             {"brand": "Not=A?Brand", "version": "24.0.0.0"}]}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0.0.0 Safari/537.36"


class Saat:
    def __init__(self):
        self.t = 0.0

    def simdi(self):
        return self.t


class SahteSurucu:
    """page_source/current_url/get_cookies kareden kareye ilerler; `uyu` ile."""

    def __init__(self, kareler):
        self.kareler = kareler
        self.i = 0
        self.acildi = None
        self.kapandi = False

    @property
    def _kare(self):
        return self.kareler[min(self.i, len(self.kareler) - 1)]

    def get(self, url):
        self.acildi = url

    @property
    def page_source(self):
        return self._kare.get("html", "")

    @property
    def current_url(self):
        return self._kare.get("url", "")

    def get_cookies(self):
        return list(self._kare.get("cerezler", []))

    def execute_script(self, _s):
        return self._kare.get("ua", UA)

    def execute_async_script(self, _s):
        return self._kare.get("hints", HINTS)

    def quit(self):
        self.kapandi = True


def kosturucu(surucu, saat, adim=1.0):
    def uyu(n):
        saat.t += n
        surucu.i += 1
    return uyu


def test_dogrulama_gecince_cerez_ua_ipucu_yakalaniyor():
    cz = [{"name": "cf_clearance", "value": "X", "domain": "ornek.test",
           "path": "/", "expiry": 0, "secure": True, "httpOnly": True}]
    s = SahteSurucu([
        {"html": DOGRULAMA, "url": "https://ornek.test/"},
        {"html": TEMIZ, "url": "https://ornek.test/", "cerezler": cz},
        {"html": TEMIZ, "url": "https://ornek.test/", "cerezler": cz},
    ])
    saat = Saat()
    sonuc = to.oturum_yakala(hedef(), surucu=s, uyu=kosturucu(s, saat),
                             simdi=saat.simdi)
    assert sonuc is not None
    assert sonuc["user_agent"] == UA
    c = sonuc["cerezler"][0]
    assert c["name"] == "cf_clearance" and c["httponly"] is True and c["secure"] is True
    # Yüksek-entropili ipuçlarından sec-ch-ua başlıkları:
    assert '"Chromium";v="140.0.0.0"' in sonuc["basliklar"]["sec-ch-ua"]
    assert sonuc["basliklar"]["sec-ch-ua-platform"] == '"Windows"'
    assert sonuc["basliklar"]["sec-ch-ua-mobile"] == "?0"
    assert s.acildi == "https://ornek.test/"


def test_tek_temiz_ankette_donmuyor_ardisik_gerekiyor():
    """Geçici bir ara sayfa (bir ankette temiz) tek başına geçildi saymasın."""
    s = SahteSurucu([
        {"html": TEMIZ, "url": "https://ornek.test/"},     # 1 temiz
        {"html": DOGRULAMA, "url": "https://ornek.test/"}, # sayaç sıfırlanır
        {"html": TEMIZ, "url": "https://ornek.test/"},
        {"html": TEMIZ, "url": "https://ornek.test/"},     # burada 2 ardışık
    ])
    saat = Saat()
    sonuc = to.oturum_yakala(hedef(), surucu=s, uyu=kosturucu(s, saat),
                             simdi=saat.simdi, zaman_asimi=100)
    assert sonuc is not None and s.i >= 3


def test_gerekli_cerez_gelince_doğrulama_sayfasinda_bile_donuyor():
    cz = [{"name": ".AitrWeb.Session", "value": "S", "domain": "ornek.test", "path": "/"}]
    s = SahteSurucu([
        {"html": DOGRULAMA, "url": "https://ornek.test/giris"},
        {"html": DOGRULAMA, "url": "https://ornek.test/giris", "cerezler": cz},
    ])
    saat = Saat()
    sonuc = to.oturum_yakala(hedef(gerekli=frozenset({".AitrWeb.Session"})),
                             surucu=s, uyu=kosturucu(s, saat), simdi=saat.simdi)
    assert sonuc is not None
    assert sonuc["cerezler"][0]["name"] == ".AitrWeb.Session"


def test_baska_alandayken_gecildi_saymiyor():
    """Kullanıcı başka bir alana (giriş sağlayıcısı) gidince bitmiş sayılmamalı."""
    s = SahteSurucu([{"html": TEMIZ, "url": "https://baska.test/"}] * 5)
    saat = Saat()
    sonuc = to.oturum_yakala(hedef(), surucu=s, uyu=kosturucu(s, saat),
                             simdi=saat.simdi, zaman_asimi=3)
    assert sonuc is None            # zaman aşımı; alan tutmadı


def test_zaman_asiminda_none():
    s = SahteSurucu([{"html": DOGRULAMA, "url": "https://ornek.test/"}])
    saat = Saat()
    sonuc = to.oturum_yakala(hedef(), surucu=s, uyu=kosturucu(s, saat),
                             simdi=saat.simdi, zaman_asimi=3, anket=1)
    assert sonuc is None


def test_iptal_edilince_none():
    s = SahteSurucu([{"html": DOGRULAMA, "url": "https://ornek.test/"}])
    saat = Saat()
    sayac = {"n": 0}

    def iptal():
        sayac["n"] += 1
        return sayac["n"] >= 2

    sonuc = to.oturum_yakala(hedef(), surucu=s, iptal=iptal,
                             uyu=kosturucu(s, saat), simdi=saat.simdi)
    assert sonuc is None


def test_pencere_kapatilinca_none():
    class Kapali(SahteSurucu):
        @property
        def page_source(self):
            raise RuntimeError("no such window")

    s = Kapali([{"html": DOGRULAMA, "url": "https://ornek.test/"}])
    saat = Saat()
    sonuc = to.oturum_yakala(hedef(), surucu=s, uyu=kosturucu(s, saat),
                             simdi=saat.simdi)
    assert sonuc is None


def test_ipucu_okunamazsa_ua_dan_turetiliyor():
    s = SahteSurucu([
        {"html": TEMIZ, "url": "https://ornek.test/", "hints": None},
        {"html": TEMIZ, "url": "https://ornek.test/", "hints": None},
    ])
    saat = Saat()
    sonuc = to.oturum_yakala(hedef(), surucu=s, uyu=kosturucu(s, saat),
                             simdi=saat.simdi)
    # userAgentData okunamadı → UA'dan türetildi (sec-ch-ua-platform Windows).
    assert sonuc["basliklar"]["sec-ch-ua-platform"] == '"Windows"'


def test_fabrika_cagriliyor_ve_kapaniyor():
    s = SahteSurucu([{"html": TEMIZ, "url": "https://ornek.test/"}] * 2)
    cagrildi = {}

    def fabrika(hedef_):
        cagrildi["hedef"] = hedef_
        return s

    saat = Saat()
    to.oturum_yakala(hedef(), surucu_fabrikasi=fabrika,
                     uyu=kosturucu(s, saat), simdi=saat.simdi)
    assert cagrildi["hedef"].kaynak == "Deokwave" and s.kapandi is True


# ── Motor seçimi / tarayıcı bulma ─────────────────────────────────────────────
def test_erisim_motoru_hazir_degilse_gomulu(monkeypatch):
    monkeypatch.setattr(to, "motor_hazir", lambda: (False, "yok"))
    assert to.erisim_motoru({}) == to.MOTOR_GOMULU
    assert to.erisim_motoru({to.AYAR_ANAHTARI: "oto"}) == to.MOTOR_GOMULU
    assert to.erisim_motoru({to.AYAR_ANAHTARI: "chrome"}) == to.MOTOR_GOMULU
    assert to.erisim_motoru({to.AYAR_ANAHTARI: "gomulu"}) == to.MOTOR_GOMULU


def test_erisim_motoru_hazirsa_oto_ve_chrome_gercek(monkeypatch):
    monkeypatch.setattr(to, "motor_hazir", lambda: (True, ""))
    assert to.erisim_motoru({}) == to.MOTOR_CHROME
    assert to.erisim_motoru({to.AYAR_ANAHTARI: "chrome"}) == to.MOTOR_CHROME
    # Kullanıcı açıkça gömülü istediyse hazır olsa bile gömülü:
    assert to.erisim_motoru({to.AYAR_ANAHTARI: "gomulu"}) == to.MOTOR_GOMULU


def test_tarayici_bul_ortam_degiskeni(monkeypatch, tmp_path):
    sahte = tmp_path / "chrome"
    sahte.write_text("")
    monkeypatch.setenv(to.TARAYICI_ORTAM, str(sahte))
    assert to.tarayici_bul() == str(sahte)


def test_tarayici_bul_pathten(monkeypatch):
    monkeypatch.delenv(to.TARAYICI_ORTAM, raising=False)
    monkeypatch.setattr(to.shutil, "which",
                        lambda ad: "/usr/bin/chromium" if ad == "chromium" else None)
    assert to.tarayici_bul() == "/usr/bin/chromium"


def test_motor_hazir_uc_yoksa(monkeypatch):
    monkeypatch.setattr(to, "_uc_var", lambda: False)
    hazir, sebep = to.motor_hazir()
    assert hazir is False and "undetected-chromedriver" in sebep


def test_motor_hazir_tarayici_yoksa(monkeypatch):
    monkeypatch.setattr(to, "_uc_var", lambda: True)
    monkeypatch.setattr(to, "tarayici_bul", lambda: None)
    hazir, sebep = to.motor_hazir()
    assert hazir is False and "tarayıcı" in sebep.lower()
