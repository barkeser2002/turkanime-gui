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
