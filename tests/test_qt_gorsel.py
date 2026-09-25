"""Kapak görselleri: ayrı havuz + bellek/disk önbelleği (`gui/qt/gorsel.py`).

Ağa çıkılmaz: `requests.get` her testte sahte. Önbellek klasörü ve bellek
önbelleği conftest'teki `_gorsel_onbellek_yalitimi` ile test başına boş.
"""
from __future__ import annotations

import base64
import threading
import time

import pytest
from PySide6.QtCore import QThreadPool

from turkanime_api.gui.qt import gorsel
from turkanime_api.gui.qt.workers import gorsel_havuzu, run_bg, shutdown_pools


# Gerçek, çözülebilir 1x1 PNG. Qt'siz üretiliyor: `QPixmap` QApplication
# olmadan kurulursa süreç düşer ve önbellek testlerinin Qt'ye ihtiyacı yok.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def png_baytlari(boyut: int = 0) -> bytes:
    """PNG imzalı baytlar; ``boyut`` verilirse o uzunluğa doldurulur."""
    return PNG_1X1 + b"\0" * max(0, boyut - len(PNG_1X1))


class Yanit:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


@pytest.fixture
def sahte_get(monkeypatch):
    """`requests.get`'i sahtele; istenen URL'leri kaydet."""
    import requests

    istekler: list = []

    def _kur(yanitla):
        def _get(url, **_kw):
            istekler.append(url)
            return yanitla(url)

        monkeypatch.setattr(requests, "get", _get)
        return istekler

    return _kur


# ── Önbellek ────────────────────────────────────────────────────────────────
def test_ikinci_istek_bellekten_geliyor(sahte_get):
    png = png_baytlari()
    istekler = sahte_get(lambda url: Yanit(png))

    assert gorsel.gorsel_getir("https://kapak/1.png") == png
    assert gorsel.gorsel_getir("https://kapak/1.png") == png
    assert istekler == ["https://kapak/1.png"]


def test_bellek_bosalsa_da_diskten_geliyor(sahte_get):
    """Uygulama yeniden açıldığında (bellek boş) ağa çıkılmamalı."""
    png = png_baytlari()
    istekler = sahte_get(lambda url: Yanit(png))
    gorsel.gorsel_getir("https://kapak/2.png")
    gorsel.bellegi_temizle()

    assert gorsel.gorsel_getir("https://kapak/2.png") == png
    assert len(istekler) == 1


@pytest.mark.parametrize("yanit", [
    Yanit(b"<html>404</html>" * 4, status_code=200),   # görsel değil
    Yanit(b"x" * 64, status_code=200),                  # imzasız
    Yanit(b"\x89PNG\r\n\x1a\n" + b"0" * 32, status_code=503),
])
def test_hatali_yanit_onbellege_girmiyor(sahte_get, yanit):
    istekler = sahte_get(lambda url: yanit)

    assert gorsel.gorsel_getir("https://kapak/bozuk") is None
    assert gorsel.gorsel_getir("https://kapak/bozuk") is None
    assert len(istekler) == 2, "hatalı yanıt önbelleğe alındı"
    assert list(gorsel.onbellek_dizini().glob("*")) == []


def test_olu_turkanime_adresine_istek_atilmiyor(sahte_get):
    """Arşivdeki "Resim" adresleri kapanan siteye gidiyor: hiç denenmemeli."""
    istekler = sahte_get(lambda url: Yanit(png_baytlari()))

    for url in ("http://www.turkanime.co/imajlar/serilerb/1.jpg",
                "https://www.turkanime.tv/x.jpg", "ftp://kapak/1.png", "", None):
        assert gorsel.gorsel_getir(url) is None
    assert istekler == []


def test_disk_onbellegi_sinirin_altinda_kaliyor(sahte_get, monkeypatch):
    png = png_baytlari(400)
    monkeypatch.setattr(gorsel, "DISK_SINIRI", len(png) * 5)
    sahte_get(lambda url: Yanit(png))

    for i in range(20):
        gorsel.gorsel_getir(f"https://kapak/{i}.png")
        time.sleep(0.002)                   # mtime sırası belirgin olsun

    dosyalar = list(gorsel.onbellek_dizini().glob("*"))
    assert sum(f.stat().st_size for f in dosyalar) <= gorsel.DISK_SINIRI
    assert dosyalar, "önbellek tamamen silinmemeli"
    # LRU: en son yazılan kalmalı, ilk yazılan gitmeli.
    assert (gorsel.onbellek_dizini() / gorsel._anahtar("https://kapak/19.png")).exists()
    assert not (gorsel.onbellek_dizini() / gorsel._anahtar("https://kapak/0.png")).exists()


def test_bozuk_disk_dosyasi_yeniden_indiriliyor(sahte_get):
    png = png_baytlari()
    istekler = sahte_get(lambda url: Yanit(png))
    kok = gorsel.onbellek_dizini()
    kok.mkdir(parents=True, exist_ok=True)
    (kok / gorsel._anahtar("https://kapak/3.png")).write_bytes(b"yarim")

    assert gorsel.gorsel_getir("https://kapak/3.png") == png
    assert len(istekler) == 1


# ── Havuz ───────────────────────────────────────────────────────────────────
def test_yavas_posterler_aramayi_bekletmiyor(qtbot, sahte_get):
    """Ölçüldü: 12 yavaş poster aramayı 4.5 sn geciktiriyordu (ortak havuz)."""
    birak = threading.Event()

    def _yavas(url):
        birak.wait(10)
        return Yanit(png_baytlari())

    sahte_get(_yavas)
    try:
        for i in range(3 * QThreadPool.globalInstance().maxThreadCount()):
            run_bg(gorsel.gorsel_getir, f"https://kapak/yavas{i}.png", gorsel=True)

        basladi = threading.Event()
        t0 = time.monotonic()
        run_bg(basladi.set)                 # "arama motoru" işi
        qtbot.waitUntil(basladi.is_set, timeout=2000)
        assert time.monotonic() - t0 < 0.5
    finally:
        birak.set()
        gorsel_havuzu().waitForDone(10000)


def test_shutdown_pools_gorsel_havuzunu_da_bosaltiyor(qtbot, sahte_get):
    birak = threading.Event()
    basladi = threading.Event()

    def _yavas(url):
        basladi.set()
        birak.wait(10)
        return Yanit(png_baytlari())

    sahte_get(_yavas)
    try:
        run_bg(gorsel.gorsel_getir, "https://kapak/kapanis.png", gorsel=True)
        qtbot.waitUntil(basladi.is_set, timeout=5000)
        assert shutdown_pools(200) is False, "görsel havuzu beklenmedi"
    finally:
        birak.set()
        gorsel_havuzu().waitForDone(5000)
    assert shutdown_pools(2000) is True


def test_kesif_yenilemesi_ikinci_kez_indirmiyor(qtbot, sahte_get, monkeypatch):
    """Aynı posterler ikinci yenilemede ağa çıkmadan karta basılmalı."""
    import turkanime_api.jikan_client as jikan_mod
    from turkanime_api.gui.qt.pages.discover import DiscoverPage

    kayitlar = [{"id": i, "title": {"romaji": f"Anime {i}"},
                 "coverImage": {"large": f"https://kapak/k{i}.png"}}
                for i in range(3)]
    monkeypatch.setattr(jikan_mod, "get_trending_anime_list",
                        lambda *a, **k: list(kayitlar))
    istekler = sahte_get(lambda url: Yanit(png_baytlari()))

    sayfa = DiscoverPage("trending")
    qtbot.addWidget(sayfa)

    def _kapaklar_geldi():
        kartlar = sayfa.cards()
        return len(kartlar) == 3 and all(k._src_pixmap is not None for k in kartlar)

    sayfa.refresh()
    qtbot.waitUntil(_kapaklar_geldi, timeout=5000)
    assert len(istekler) == 3

    sayfa.refresh()
    qtbot.waitUntil(lambda: sayfa._busy is False, timeout=5000)
    qtbot.waitUntil(_kapaklar_geldi, timeout=5000)
    assert len(istekler) == 3, "önbellekteki posterler yeniden indirildi"
