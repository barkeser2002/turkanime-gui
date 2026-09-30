"""`WebGorunum` — köprüsü ve ``ta://`` şeması kurulu QWebEngineView.

Uygulamada TEK web görünümü var; sayfalar onun içinde rota (``#/home``,
``#/trending``). Her sayfa için ayrı görünüm, ayrı Chromium render süreci
demekti (sayfa başına onlarca MB).

Python rotayı `git` ile değiştiriyor; sayfa henüz yüklenmediyse istek
bekletilip yükleme bitince uygulanıyor.

Dış bağlantılar (AniList sayfası, fansub sitesi) görünümde AÇILMAZ:
``ta://`` dışındaki her gezinti sistem tarayıcısına devrediliyor. Arayüz
bir tarayıcı değil; kullanıcı içeride bir siteye düşerse geri dönüş yolu yok.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from PySide6.QtCore import QFile, QIODevice, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineScript,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from . import sema
from .kopru import Kopru

# Sayfanın zemin rengi (tema.css'teki --zemin). Görünüm ilk kareyi çizene
# kadar beyaz parlamasın.
ZEMIN = "#0b0d12"

_profil: Optional[QWebEngineProfile] = None


def ortak_profil() -> QWebEngineProfile:
    """Web arayüzünün profili: diske yazmayan (off-the-record), şemalı.

    Varsayılan profil kullanılmıyor: çerez penceresi ve CF çözücü kendi
    profillerini kuruyor; arayüzün önbelleği/çerezleriyle karışmasınlar.
    Profilin ebeveyni QApplication: sayfalardan SONRA yıkılmalı.
    """
    global _profil
    if _profil is None:
        from PySide6.QtWidgets import QApplication
        _profil = QWebEngineProfile(QApplication.instance())
        sema.profili_kur(_profil)
    return _profil


def _qwebchannel_betigi() -> QWebEngineScript:
    """Qt'nin gömülü ``qwebchannel.js``'i, belge oluşurken enjekte."""
    dosya = QFile(":/qtwebchannel/qwebchannel.js")
    kaynak = ""
    if dosya.open(QIODevice.OpenModeFlag.ReadOnly):
        kaynak = bytes(dosya.readAll()).decode("utf-8")
        dosya.close()
    betik = QWebEngineScript()
    betik.setName("qwebchannel")
    betik.setSourceCode(kaynak)
    betik.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    betik.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    betik.setRunsOnSubFrames(False)
    return betik


class _Sayfa(QWebEnginePage):
    """``ta://`` dışına gezinmeyi sistem tarayıcısına devreden sayfa."""

    def acceptNavigationRequest(self, url, tur, ana_cerceve):  # noqa: N802
        if url.scheme() == sema.SEMA.decode():
            return True
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        return False

    # `createWindow` bilerek yazılmadı (None → yeni pencere açılmaz): arayüz
    # `target=_blank` kullanmıyor, dış bağlantılar düz `<a href>` ve yukarıdaki
    # yol onları tarayıcıya veriyor.

    def javaScriptConsoleMessage(self, seviye, mesaj, satir, kaynak):  # noqa: N802
        if seviye == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"[Web JS] {kaynak}:{satir}: {mesaj}")


class WebGorunum(QWebEngineView):
    """Arayüzün web sayfalarını taşıyan görünüm."""

    # Sayfa yüklendi ve köprü bağlandı (JS `TA.hazir` çağırdı değil; yükleme).
    yuklendi = Signal(bool)

    def __init__(self, kopru: Kopru, parent=None, *, kabuk: str = "qt",
                 profil: Optional[QWebEngineProfile] = None):
        super().__init__(parent)
        self.kopru = kopru
        self._hazir = False
        self._bekleyen: Optional[str] = None
        self.rota = ""

        sayfa = _Sayfa(profil or ortak_profil(), self)
        sayfa.setBackgroundColor(QColor(ZEMIN))
        self.setPage(sayfa)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        sayfa.scripts().insert(_qwebchannel_betigi())

        self._kanal = QWebChannel(sayfa)
        self._kanal.registerObject("kopru", kopru)
        sayfa.setWebChannel(self._kanal)

        self.loadFinished.connect(self._yukleme_bitti)
        # `kabuk=qt`: üst çubuk ve menü Qt'de; sayfa yalnızca içeriği çiziyor.
        self.load(QUrl(f"{sema.BASLANGIC}?kabuk={kabuk}"))

    # ── Gezinti ─────────────────────────────────────────────────────────────
    def git(self, rota: str, parametreler: Optional[Dict[str, Any]] = None) -> None:
        """Sayfayı ``rota``ya götür (yükleme sürüyorsa bitince)."""
        self.rota = rota
        betik = (f"window.TA && TA.git({json.dumps(rota)}, "
                 f"{json.dumps(parametreler or {}, ensure_ascii=False)})")
        if self._hazir:
            self.page().runJavaScript(betik)
        else:
            self._bekleyen = betik

    def calistir(self, betik: str, geri=None) -> None:
        """Sayfada JS çalıştır (testler ve ileri kullanım için)."""
        if geri is None:
            self.page().runJavaScript(betik)
        else:
            self.page().runJavaScript(betik, 0, geri)

    @property
    def hazir(self) -> bool:
        return self._hazir

    def _yukleme_bitti(self, basarili: bool) -> None:
        self._hazir = bool(basarili)
        if basarili and self._bekleyen:
            betik, self._bekleyen = self._bekleyen, None
            self.page().runJavaScript(betik)
        self.yuklendi.emit(bool(basarili))


__all__ = ["WebGorunum", "ortak_profil", "ZEMIN"]
