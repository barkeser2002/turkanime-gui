"""Arayüzde fansub seçimi ("Fansub'u kendim seçeyim").

ESKİ HATA: ayar kaydediliyordu ama yalnızca CLI soruyordu; arayüz her bölümde
oynatıcı önceliğiyle seçilen akışı açıyor, seri içinde çeviri grubu bölümden
bölüme değişebiliyordu. Ağ yok, mpv yok: sahte bölümün `best_video`'su video
döndürmüyor (oynatma/indirme "video yok" ile biter, diske bir şey yazılmaz).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from turkanime_api.gui.qt.fansub import FansubDialog, FansubSecici, fansub_ozeti
from turkanime_api.gui.qt.pages.downloads import BITMIS_DURUMLAR


class FansubluBolum:
    """`fansubs` okunduğunu sayar, `best_video` argümanlarını saklar."""

    def __init__(self, no: int = 1, fansubs=("A", "B"), seri="fansub-seri"):
        self.slug = f"{seri}-{no}-bolum"
        self.anime = SimpleNamespace(slug=seri, title="Fansub Seri")
        self._fansubs = list(fansubs)
        self.fansub_okuma = 0
        self.cagrilar: list = []
        self._bekleyen_akislar = [
            {"url": f"u{i}", "fansub": f, "player": "SIBNET", "label": "1080p"}
            for i, f in enumerate(self._fansubs)]

    @property
    def fansubs(self):
        self.fansub_okuma += 1
        return list(self._fansubs)

    def best_video(self, **kwargs):
        self.cagrilar.append(kwargs)
        return None


def _entry(bolum, no=1):
    return {"title": f"Fansub Seri {no}. Bölüm", "obj": bolum,
            "kaynak": "TürkAnime", "kimlik": "fansub-seri"}


@pytest.fixture
def sorulan(main_window, monkeypatch):
    kayit: list = []

    def sor(baslik, fansubs, ozet):
        kayit.append((baslik, list(fansubs), ozet))
        return ("B", True)

    monkeypatch.setattr(main_window.fansub, "sor", sor)
    return kayit


def _oynat(main_window, qtbot, bolum, no=1):
    main_window._on_play(_entry(bolum, no))
    qtbot.waitUntil(lambda: bool(bolum.cagrilar) and not main_window._playing,
                    timeout=10000)


def test_secilen_fansub_best_videoya_gidiyor_ve_seride_hatirlaniyor(
        main_window, qtbot, ayarla, tmp_path, sorulan):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    bolum = FansubluBolum()
    _oynat(main_window, qtbot, bolum)
    assert bolum.cagrilar[0]["by_fansub"] == "B"
    assert len(sorulan) == 1
    assert sorulan[0][1] == ["A", "B"]
    assert sorulan[0][2]["A"] == ["SIBNET 1080p"], "oynatıcı/kalite özeti gösterilmeli"

    ikinci = FansubluBolum(no=2)
    _oynat(main_window, qtbot, ikinci, no=2)
    assert ikinci.cagrilar[0]["by_fansub"] == "B"
    assert len(sorulan) == 1, "aynı seride ikinci kez sorulmamalı"
    assert ikinci.fansub_okuma == 0, "hatırlanan seçimde liste bile okunmamalı"


def test_tek_fansubda_ve_ayar_kapaliyken_sorulmuyor(main_window, qtbot, ayarla,
                                                    tmp_path, sorulan):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    tek = FansubluBolum(fansubs=("A",))
    _oynat(main_window, qtbot, tek)
    assert sorulan == [] and "by_fansub" not in tek.cagrilar[0]

    ayarla(**{"manuel fansub": False})
    kapali = FansubluBolum(seri="baska-seri")
    _oynat(main_window, qtbot, kapali)
    assert sorulan == [] and kapali.fansub_okuma == 0
    assert "by_fansub" not in kapali.cagrilar[0]


def test_iptal_oynatmayi_baslatmiyor(main_window, qtbot, ayarla, tmp_path, monkeypatch):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    monkeypatch.setattr(main_window.fansub, "sor", lambda *a: None)
    bolum = FansubluBolum()
    main_window._on_play(_entry(bolum))
    qtbot.waitUntil(lambda: not main_window._playing, timeout=10000)
    assert bolum.cagrilar == []
    assert "fansub seçilmedi" in main_window.statusBar().currentMessage()


def test_toplu_indirme_tek_soru_hepsine_ayni_fansub(main_window, qtbot, ayarla,
                                                    tmp_path, sorulan):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    bolumler = [FansubluBolum(no=i) for i in range(1, 4)]
    for i, b in enumerate(bolumler, start=1):
        main_window._on_download(_entry(b, i))
    qtbot.waitUntil(lambda: all(b.cagrilar for b in bolumler), timeout=10000)
    qtbot.waitUntil(lambda: all(main_window.downloads.durum(t) in BITMIS_DURUMLAR
                                for t in list(main_window.downloads._jobs)),
                    timeout=10000)
    assert len(sorulan) == 1, "12 bölümlük toplu indirme 12 kez sormamalı"
    assert [b.cagrilar[0].get("by_fansub") for b in bolumler] == ["B", "B", "B"]
    assert sum(b.fansub_okuma for b in bolumler) == 1


def test_diyalog_ozetle_listeliyor(qtbot):
    ozet = fansub_ozeti(FansubluBolum())
    assert ozet == {"A": ["SIBNET 1080p"], "B": ["SIBNET 1080p"]}
    dialog = FansubDialog("Seri", ["A", "B"], ozet)
    qtbot.addWidget(dialog)
    metinler = [dialog.liste.item(i).text() for i in range(dialog.liste.count())]
    assert metinler[0].startswith("Otomatik")
    assert metinler[1] == "A — SIBNET 1080p"
    assert dialog.secim == "A"
    dialog.liste.setCurrentRow(0)
    assert dialog.secim == "", "Otomatik = fansub süzülmez"


def test_otomatik_secimi_de_hatirlaniyor(qtbot, monkeypatch):
    secici = FansubSecici()
    monkeypatch.setattr(secici, "sor", lambda *a: ("", True))
    sonuclar: list = []
    secici.iste(_entry(FansubluBolum()), lambda t, f: sonuclar.append((t, f)))
    qtbot.waitUntil(lambda: bool(sonuclar), timeout=5000)
    secici.iste(_entry(FansubluBolum(no=2)), lambda t, f: sonuclar.append((t, f)))
    assert sonuclar == [(True, None), (True, None)]
