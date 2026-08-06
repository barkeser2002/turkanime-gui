"""CF çözücü alt-sürecinin giriş noktaları ve yetim süreç sızıntısı.

Donmuş (PyInstaller) build'de `sys.executable -m paket.modul` çalışmaz, bu
yüzden `cf_bypass._get_qt_solver` **uygulamanın kendisini** `--cf-qt-solver`
bayrağıyla yeniden çağırır. Release iş akışı iki ayrı donmuş dosya üretiyor:

    turkanime-gui  ->  turkanime_api/gui/qt/__main__.py   (bayrağı tanıyordu)
    turkanime-cli  ->  turkanime_api/cli/__main__.py      (TANIMIYORDU)

CLI tarafında bayrak tanınmayınca `--cf-qt-solver`, etkileşimli menüyü açıyor;
el sıkışma bozuk çıkıyor ve açılan süreç yetim kalıyordu — her CF challenge'ında
bir tane.
"""
from __future__ import annotations

import sys

import pytest

from turkanime_api.common import cf_bypass as cf
from turkanime_api.common.cf_qt_solver import SOLVER_FLAG


def test_iki_giris_noktasi_da_ayni_bayragi_kullaniyor():
    """Bayrağın tek kaynağı olmalı; iki kopya ayrışırsa biri sessizce bozulur."""
    from turkanime_api.gui.qt.__main__ import SOLVER_FLAG as gui_flag

    assert gui_flag == SOLVER_FLAG == "--cf-qt-solver"


def test_cli_giris_noktasi_bayragi_cozucuye_yonlendiriyor(monkeypatch):
    """CLI, bayrağı görünce menüyü DEĞİL çözücüyü başlatmalı."""
    import turkanime_api.cli.__main__ as cli_main
    from turkanime_api.common import cf_qt_solver

    monkeypatch.setattr(sys, "argv", ["TurkAnime.exe", SOLVER_FLAG])
    monkeypatch.setattr(cf_qt_solver, "main", lambda: 42)
    monkeypatch.setattr(cli_main, "menu_loop",
                        lambda: pytest.fail("bayrak varken menü açılmamalı"))

    assert cli_main.main() == 42


def test_bozuk_el_sikismada_alt_surec_olduruluyor(monkeypatch):
    """Ölçüldü: `json.loads` patlayınca alt-süreç `poll() is None` kalıyordu.

    Çözücü yerine (bayrağı tanımayan bir giriş noktası yüzünden) menü açıldığında
    ilk satır JSON değildir; eski kod istisnayı yakalayıp `None` dönüyor ama
    süreci hiç öldürmüyordu.
    """
    oldurulen = []

    class SahteSurec:
        def __init__(self):
            self.pid = 4242
            self._canli = True
            self.stdin = None
            self.stdout = _SahteStdout()

        def kill(self):
            oldurulen.append(self.pid)
            self._canli = False

        def poll(self):
            return None if self._canli else -9

    class _SahteStdout:
        @staticmethod
        def readline():
            # Etkileşimli CLI'nin bastığı ilk satır — JSON değil.
            return "İşlemi seç\n"

    import subprocess

    sahte = SahteSurec()
    monkeypatch.setattr(cf, "HAS_QTWEBENGINE", True)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: sahte)

    ses = cf.CFSession(flaresolverr_url="")
    assert ses._get_qt_solver() is None
    assert oldurulen == [4242], "bozuk el sıkışmada alt-süreç yetim kaldı"


def test_donmus_modda_uygulamanin_kendisi_cagriliyor(monkeypatch):
    """Donmuş modda komut `[sys.executable, --cf-qt-solver]` olmalı."""
    komutlar = []

    class SahteSurec:
        pid = 1
        stdin = None

        class stdout:                     # noqa: N801 (sahte)
            @staticmethod
            def readline():
                return '{"ok": true}\n'

        @staticmethod
        def kill():
            pass

        @staticmethod
        def poll():
            return None

    import subprocess

    monkeypatch.setattr(cf, "HAS_QTWEBENGINE", True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\x\TurkAnime.exe")
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **k: komutlar.append(cmd) or SahteSurec())

    ses = cf.CFSession(flaresolverr_url="")
    assert ses._get_qt_solver() is not None
    assert komutlar == [[r"C:\x\TurkAnime.exe", SOLVER_FLAG]]
