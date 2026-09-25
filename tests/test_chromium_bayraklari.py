"""QtWebEngine'in Chromium bayrakları: GPU süreci kapalı olmalı.

Çerez penceresi testi (`test_cookie_browser.py`) eski bayraklarla ara ara
segfault veriyordu: 130 koşunun 5'inde, Chromium'un kendi thread'inde, pencere
kapanırken. `--disable-gpu` ile 130'da 0. Gerekçe `common/chromium.py`'de.
"""
from __future__ import annotations

import ast
import inspect

from turkanime_api.common import cf_qt_solver, chromium


def test_bayraklar_gpu_surecini_kapatiyor():
    parcalar = chromium.BAYRAKLAR.split()
    assert "--disable-gpu" in parcalar
    assert "--disable-gpu-compositing" in parcalar


def test_bos_ortamda_bayraklar_yaziliyor(monkeypatch):
    monkeypatch.delenv(chromium.ORTAM_ANAHTARI, raising=False)
    assert chromium.bayraklari_hazirla() == chromium.BAYRAKLAR
    import os
    assert os.environ[chromium.ORTAM_ANAHTARI] == chromium.BAYRAKLAR


def test_kullanicinin_verdigi_deger_ezilmiyor(monkeypatch):
    monkeypatch.setenv(chromium.ORTAM_ANAHTARI, "--kendi-bayragim")
    assert chromium.bayraklari_hazirla() == "--kendi-bayragim"


def test_root_ta_kum_havuzu_kapatiliyor(monkeypatch):
    """Chromium root olarak kum havuzuyla çalışmıyor, süreci öldürüyor.

    Gitea runner'ında iş root olarak koşuyor; test kapısı orada özetsiz
    "exit 1" ile bitiyordu.
    """
    monkeypatch.delenv(chromium.KUM_HAVUZU_ANAHTARI, raising=False)
    monkeypatch.setattr(chromium.os, "geteuid", lambda: 0, raising=False)
    chromium.bayraklari_hazirla()
    import os
    assert os.environ[chromium.KUM_HAVUZU_ANAHTARI] == "1"


def test_normal_kullanicida_kum_havuzuna_dokunulmuyor(monkeypatch):
    monkeypatch.delenv(chromium.KUM_HAVUZU_ANAHTARI, raising=False)
    monkeypatch.setattr(chromium.os, "geteuid", lambda: 1000, raising=False)
    chromium.bayraklari_hazirla()
    import os
    assert chromium.KUM_HAVUZU_ANAHTARI not in os.environ


def test_gui_ortak_bayraklari_kullaniyor(monkeypatch):
    """`prepare_qt_env` bayrakları ortak modülden almalı (ikinci kopya yok)."""
    monkeypatch.delenv(chromium.ORTAM_ANAHTARI, raising=False)
    from turkanime_api.gui.qt.app import prepare_qt_env
    prepare_qt_env()
    import os
    assert os.environ[chromium.ORTAM_ANAHTARI] == chromium.BAYRAKLAR


def test_cf_cozucu_qt_dan_once_bayraklari_hazirliyor():
    """Alt-süreç `main()` PySide6'yı import etmeden önce bayrakları koymalı.

    Süreç gerçekten başlatılmıyor (QApplication + WebEngine açar); AST ile
    çağrı sırası denetleniyor.
    """
    agac = ast.parse(inspect.getsource(cf_qt_solver.main))
    govde = agac.body[0].body
    hazirlik = qt = None
    for sira, dugum in enumerate(govde):
        kaynak = ast.unparse(dugum)
        if hazirlik is None and "bayraklari_hazirla()" in kaynak:
            hazirlik = sira
        if qt is None and "PySide6" in kaynak:
            qt = sira
    assert hazirlik is not None, "cf_qt_solver.main bayraklari_hazirla() çağırmıyor"
    assert qt is not None and hazirlik < qt
