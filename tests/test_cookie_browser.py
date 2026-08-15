"""QtWebEngine cookie akışı.

En kritik güvence: üretilen Netscape metni **eski Selenium sürümüyle birebir
aynı** olmalı. `tranime.set_session_cookie()` bu metni tab ile ayırıp 7 alan
bekliyor; biçim kayarsa TRAnimeİzle sessizce çalışmaz hâle gelir.
"""
from __future__ import annotations

import pytest

from turkanime_api.gui.qt import cookie_browser as qtcb

SAMPLE = [
    {"domain": ".tranimeizle.io", "path": "/", "secure": True,
     "expiry": 1900000000, "name": ".AitrWeb.Session", "value": "abc123"},
    {"domain": "www.tranimeizle.io", "path": "/anime", "secure": False,
     "expiry": 0, "name": "age_verified", "value": "true"},
]


def test_is_available():
    assert qtcb.is_available() is True


# Eski Selenium modülünün (`gui/cookie_browser.py`) SAMPLE için ürettiği çıktı,
# Faz 9'da o modül silinmeden hemen önce çalıştırılıp donduruldu. Karşılaştırma
# artık canlı modüle değil bu sabite karşı yapılıyor — biçim güvencesi aynen
# duruyor, ölü Selenium bağımlılığı olmadan.
GOLDEN_SATIRLAR = [
    ".tranimeizle.io\tTRUE\t/\tTRUE\t1900000000\t.AitrWeb.Session\tabc123",
    "www.tranimeizle.io\tFALSE\t/anime\tFALSE\t0\tage_verified\ttrue",
]


def test_netscape_format_matches_frozen_legacy_output():
    """Eski Selenium sürümüyle bayt bayt aynı veri satırları."""
    uretilen = qtcb._cookies_to_netscape(SAMPLE)
    veri = [s for s in uretilen.splitlines() if s and not s.startswith("#")]
    assert veri == GOLDEN_SATIRLAR
    # `tranime.set_session_cookie()` her satırda tam 7 alan bekliyor.
    assert all(len(s.split("\t")) == 7 for s in veri)


def test_netscape_roundtrips_through_set_session_cookie():
    """Uçtan uca: üretilen metin gerçekten tüketilebiliyor mu?"""
    from turkanime_api.sources import tranime

    tranime.set_session_cookie(qtcb._cookies_to_netscape(SAMPLE))
    assert tranime.SESSION_COOKIE == "abc123"


def test_qcookie_to_dict():
    from PySide6.QtCore import QDateTime
    from PySide6.QtNetwork import QNetworkCookie

    c = QNetworkCookie(b".AitrWeb.Session", b"abc123")
    c.setDomain(".tranimeizle.io")
    c.setPath("/")
    c.setSecure(True)
    c.setExpirationDate(QDateTime.fromSecsSinceEpoch(1900000000))

    d = qtcb._qcookie_to_dict(c)
    assert d["name"] == ".AitrWeb.Session"
    assert d["value"] == "abc123"
    assert d["domain"] == ".tranimeizle.io"
    assert d["secure"] is True


def test_required_cookie_gate():
    assert qtcb._has_required_cookies(SAMPLE) is True
    assert qtcb._has_required_cookies([SAMPLE[1]]) is False


def test_foreign_domain_cookies_are_filtered():
    """Başka sitelerin çerezleri TRAnime oturumuna karışmamalı."""
    mixed = SAMPLE + [{"domain": "google.com", "name": "x", "value": "y"}]
    assert len(qtcb._filter_tranime_cookies(mixed)) == 2


def test_dialog_captures_cookie_from_local_server(qtbot, local_server, monkeypatch):
    """Olay tabanlı yakalama: sunucu çerez set edince `cookies_ready` çıkmalı.

    Gerçek siteye gitmiyoruz — yerel sunucu deterministik.
    """
    url = local_server(set_cookie="AitrSession=YEREL-TOKEN; Path=/")
    monkeypatch.setattr(qtcb, "COOKIE_DOMAIN", "127.0.0.1")
    monkeypatch.setattr(qtcb, "REQUIRED_COOKIES", {"AitrSession"})
    monkeypatch.setattr(qtcb, "TARGET_ANIME", url)

    dlg = qtcb.CookieBrowserDialog()
    qtbot.addWidget(dlg)
    dlg.show()          # şart: gösterilmemiş view yükleme yapmaz

    with qtbot.waitSignal(dlg.cookies_ready, timeout=20000) as blocker:
        dlg.begin()

    netscape = blocker.args[0]
    assert "AitrSession" in netscape
    assert "YEREL-TOKEN" in netscape


def test_repeat_runs_still_capture(qtbot, local_server, monkeypatch):
    """Aynı çerez ikinci kez de yakalanmalı.

    Kalıcı profil kullanılsaydı Chromium değişmeyen çerez için `cookieAdded`
    yaymaz ve çerezi daha önce almış kullanıcı sonsuza dek 'bot kontrolünü
    çözün' ekranında kalırdı. Off-the-record profil bunu engelliyor.
    """
    monkeypatch.setattr(qtcb, "COOKIE_DOMAIN", "127.0.0.1")
    monkeypatch.setattr(qtcb, "REQUIRED_COOKIES", {"AitrSession"})

    for _ in range(2):
        url = local_server(set_cookie="AitrSession=AYNI-TOKEN; Path=/")
        monkeypatch.setattr(qtcb, "TARGET_ANIME", url)
        dlg = qtcb.CookieBrowserDialog()
        qtbot.addWidget(dlg)
        dlg.show()
        with qtbot.waitSignal(dlg.cookies_ready, timeout=20000):
            dlg.begin()
        dlg.close()
