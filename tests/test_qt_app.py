"""Ana pencere: sayfa yönlendirme, indirme klasörü, temiz kapanış."""
from __future__ import annotations

import os

import pytest

from turkanime_api.gui.qt.app import NAV_ITEMS, MainWindow


def test_window_builds_all_pages(main_window):
    for key, _label in NAV_ITEMS:
        assert key in main_window.pages, f"{key} sayfası kurulmadı"
    # Bölüm sayfası menüde yok ama stack'te olmalı (arama sonucundan açılır)
    assert "episodes" in main_window.pages
    assert main_window.stack.count() == len(main_window.pages)


def test_page_switching(main_window):
    for key in ("downloads", "settings", "search", "home"):
        main_window.show_page(key)
        assert main_window.stack.currentWidget() is main_window.pages[key]


def test_search_from_header_routes_to_search_page(main_window, monkeypatch):
    """Aramaya basınca arama sayfasına geçilmeli (ağa çıkmadan doğrula)."""
    started: list = []
    page = main_window.pages["search"]
    monkeypatch.setattr(page, "start_search", lambda q: started.append(q))

    main_window.txtSearch.setText("naruto")
    main_window._on_search()

    assert main_window.stack.currentWidget() is page
    assert started == ["naruto"]


def test_empty_search_is_ignored(main_window, monkeypatch):
    started: list = []
    monkeypatch.setattr(main_window.pages["search"], "start_search",
                        lambda q: started.append(q))
    main_window.txtSearch.setText("   ")
    main_window._on_search()
    assert started == []


def test_download_dir_never_empty_or_cwd():
    """Boş string yt-dlp'de çalışma dizini demek.

    Paketlenmiş uygulamada bu `Program Files` altı olur: ya yazma izni yok ya da
    indirilenler kaybolur. Ayar okunamasa bile gerçek bir dizin dönmeli.
    """
    d = MainWindow._download_dir()
    assert d, "boş string döndü"
    assert os.path.isabs(d)
    assert os.path.isdir(d)


def test_download_dir_falls_back_when_settings_broken(monkeypatch):
    import turkanime_api.cli.dosyalar as dosyalar_mod

    class Bozuk:
        def __init__(self):
            raise OSError("ayar dosyası bozuk")

    monkeypatch.setattr(dosyalar_mod, "Dosyalar", Bozuk)
    d = MainWindow._download_dir()
    assert os.path.isdir(d)
    assert os.path.abspath(d) != os.path.abspath(os.getcwd())


# ── Kapanış: süreç gerçekten çıkmalı ─────────────────────────────────────────
COCUK_KAPANIS = """
import os, sys, time
sys.path.insert(0, {repo!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from turkanime_api.gui.qt import app as app_mod
from turkanime_api.gui.qt.workers import run_bg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow


class SahtePencere(QMainWindow):
    # Gerçek MainWindow'un yerine: uzun bir indirme başlatıp kendini kapatır.
    def __init__(self):
        super().__init__()
        run_bg(lambda: time.sleep(25), long_running=True)
        QTimer.singleShot(600, self.close)


app_mod.MainWindow = SahtePencere
sys.exit(app_mod.run())
"""


def test_uzun_is_surerken_surec_asili_kalmiyor(tmp_path):
    """Ölçüldü: pencere kapansa da süreç indirme bitene kadar çıkmıyordu.

    `~QThreadPool` yıkıcısı zaman aşımsız `waitForDone()` çağırıyor; 20 sn'lik
    sahte bir indirmeyle süreç 20,7 sn sonra çıkıyordu (beklenen ~3,7 sn).
    """
    import subprocess
    import sys
    import time

    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    betik = tmp_path / "kapanis_cocugu.py"
    betik.write_text(COCUK_KAPANIS.format(repo=kok), encoding="utf-8")

    t0 = time.time()
    proc = subprocess.Popen([sys.executable, str(betik)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("süreç 20 sn içinde çıkmadı: uzun iş kapanışı kilitliyor")
    sure = time.time() - t0
    assert sure < 15, f"kapanış {sure:.1f} sn sürdü — uzun işi bekliyor"
