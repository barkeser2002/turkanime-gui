"""``ta://`` URL şeması — arayüz dosyaları ve kapak görselleri.

İki ev sahibi (host):

``ta://uygulama/<yol>``
    ``statik/`` altındaki HTML/CSS/JS. ``file://`` yerine özel şema: sayfanın
    kökeni sabit (``ta://uygulama``), PyInstaller açılım dizini gibi yollar
    sayfaya sızmıyor, ``file://``'ın Chromium kısıtları (modül betikleri,
    ``fetch``) devreye girmiyor.

``ta://gorsel/?u=<adres>``
    Kapak görseli. Chromium doğrudan ``https://`` posterini de yükleyebilirdi
    ama o zaman `gui.qt.gorsel`'in disk önbelleği (çevrimdışıyken posterler),
    ölü konak listesi (turkanime.tv) ve görsel imzası denetimi devre dışı
    kalırdı. İndirme arka plan havuzunda (`workers.gorsel_havuzu`), yanıt
    GUI thread'inde veriliyor.

Şema `semayi_kaydet` ile QApplication kurulmadan ÖNCE kaydedilmeli
(`gui.qt.app.prepare_qt_env` çağırıyor); işleyici profil başına bir kez
kuruluyor (`profile_kur`).
"""
from __future__ import annotations

import mimetypes
import posixpath
from pathlib import Path
from typing import Dict, Optional, Tuple

SEMA = b"ta"
UYGULAMA = "uygulama"
GORSEL = "gorsel"

# Arayüz dosyaları. PyInstaller'da `turkanime_api/gui/web/statik` olarak
# pakete giriyor (bkz. turkanime-gui.spec); geliştirmede modülün yanında.
STATIK = Path(__file__).resolve().with_name("statik")

BASLANGIC = "ta://uygulama/index.html"

# Uzantı → içerik türü. `mimetypes` Windows'ta kayıt defterinden okuyor ve
# `.js` için "text/plain" dönebiliyor (bilinen Python sorunu); Chromium o
# zaman betiği çalıştırmıyor. Bildiğimiz türler buradan, gerisi `mimetypes`.
_TURLER = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

# Görsel imzaları → içerik türü (`gorsel.gorsel_mi` ile aynı aile + WEBP).
_IMZALAR: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def icerik_turu(yol: str) -> str:
    """Dosya uzantısından içerik türü."""
    uzanti = posixpath.splitext(yol)[1].lower()
    return (_TURLER.get(uzanti) or mimetypes.guess_type(yol)[0]
            or "application/octet-stream")


def gorsel_turu(veri: Optional[bytes]) -> Optional[str]:
    """Görsel baytlarının içerik türü; tanınmıyorsa ``None``."""
    if not veri:
        return None
    if veri[:4] == b"RIFF" and veri[8:12] == b"WEBP":
        return "image/webp"
    for imza, tur in _IMZALAR:
        if veri.startswith(imza):
            return tur
    return None


def statik_yol(url_yolu: str, kok: Path = STATIK) -> Optional[Path]:
    """``/css/tema.css`` → diskteki dosya; kökün dışına çıkan yol ``None``.

    ``..`` ve mutlak yol reddediliyor: sayfa (ya da içine sızan bir bağlantı)
    ``ta://uygulama/../../ayarlar.json`` ile diskte gezemesin.
    """
    parcalar = [p for p in str(url_yolu or "").split("/") if p]
    if not parcalar:
        parcalar = ["index.html"]
    if any(p in ("..", ".") or "\\" in p or ":" in p for p in parcalar):
        return None
    kok = Path(kok).resolve()
    hedef = kok.joinpath(*parcalar).resolve()
    try:
        hedef.relative_to(kok)
    except ValueError:
        return None
    return hedef if hedef.is_file() else None


def gorsel_adresi(url: str) -> str:
    """Kapak adresinin şema karşılığı (JS'teki `TA.gorsel` ile aynı biçim)."""
    from urllib.parse import quote
    return f"ta://{GORSEL}/?u={quote(url, safe='')}"


def semayi_kaydet() -> None:
    """``ta`` şemasını Chromium'a tanıt. QApplication'dan ÖNCE çağrılmalı.

    İkinci çağrı zararsız (Qt aynı adı ikinci kez kaydetmeyi reddedip uyarı
    basıyor; önceden bakılıyor).
    """
    try:
        from PySide6.QtWebEngineCore import QWebEngineUrlScheme
    except ImportError:          # WebEngine'siz kurulum: arayüz yine açılsın
        return
    if QWebEngineUrlScheme.schemeByName(SEMA).name():
        return
    sema = QWebEngineUrlScheme(SEMA)
    sema.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    # SecureScheme: sayfa "güvenli bağlam" sayılıyor (http uyarıları yok).
    # CorsEnabled: aynı kökenden `fetch`/modül yüklemeye izin.
    sema.setFlags(QWebEngineUrlScheme.Flag.SecureScheme
                  | QWebEngineUrlScheme.Flag.CorsEnabled)
    QWebEngineUrlScheme.registerScheme(sema)


def _isleyici_sinifi():
    """`SemaIsleyici` sınıfını tembel kur (modül Qt'siz import edilebilsin)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QUrl, QUrlQuery, Signal
    from PySide6.QtWebEngineCore import (
        QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler,
    )

    class SemaIsleyici(QWebEngineUrlSchemeHandler):
        """``ta://`` isteklerini yanıtlayan işleyici."""

        # (iş anahtarı, (bayt, tür) | None) — arka plandan GUI thread'ine.
        _gorsel_hazir = Signal(int, object)

        def __init__(self, parent=None, kok: Path = STATIK):
            super().__init__(parent)
            self._kok = Path(kok)
            # Bekleyen görsel istekleri. İş nesnesinin sahibi WebEngine:
            # sayfa isteği iptal ederse (gezinti, yenileme) nesne silinir ve
            # ona yanıt vermek çöker. `destroyed` ile listeden düşülüyor.
            self._bekleyen: Dict[int, QWebEngineUrlRequestJob] = {}
            self._sayac = 0
            self._gorsel_hazir.connect(self._gorseli_yanitla)

        # ── Giriş ───────────────────────────────────────────────────────────
        def requestStarted(self, job) -> None:  # noqa: N802 (Qt adı)
            url = job.requestUrl()
            ev = url.host()
            if ev == UYGULAMA:
                self._dosya(job, url.path())
            elif ev == GORSEL:
                hedef = QUrlQuery(url).queryItemValue(
                    "u", QUrl.ComponentFormattingOption.FullyDecoded)
                self._gorsel(job, hedef)
            else:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)

        # ── Arayüz dosyaları ────────────────────────────────────────────────
        def _dosya(self, job, url_yolu: str) -> None:
            yol = statik_yol(url_yolu, self._kok)
            if yol is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            try:
                veri = yol.read_bytes()
            except OSError:
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                return
            self._yanitla(job, veri, icerik_turu(yol.name))

        @staticmethod
        def _yanitla(job, veri: bytes, tur: str) -> None:
            # Tampon işin çocuğu: iş silinince tampon da gider.
            tampon = QBuffer(job)
            tampon.setData(QByteArray(veri))
            tampon.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(tur.encode("ascii"), tampon)

        # ── Görseller ───────────────────────────────────────────────────────
        def _gorsel(self, job, hedef: str) -> None:
            if not hedef.startswith(("http://", "https://")):
                job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
                return
            self._sayac += 1
            anahtar = self._sayac
            self._bekleyen[anahtar] = job
            job.destroyed.connect(
                lambda *_a, k=anahtar: self._bekleyen.pop(k, None))
            from ..qt.workers import run_bg
            run_bg(self._gorsel_getir, anahtar, hedef, gorsel=True)

        def _gorsel_getir(self, anahtar: int, hedef: str) -> None:
            """Arka plan: önbellek/ağ; sonucu sinyalle GUI thread'ine taşı."""
            from ..qt.gorsel import gorsel_getir
            veri = gorsel_getir(hedef)
            tur = gorsel_turu(veri)
            try:
                self._gorsel_hazir.emit(anahtar, (veri, tur) if tur else None)
            except RuntimeError:
                pass              # işleyici bu arada silindi (kapanış)

        def _gorseli_yanitla(self, anahtar: int, sonuc) -> None:
            job = self._bekleyen.pop(anahtar, None)
            if job is None:
                return            # sayfa isteği bıraktı
            try:
                if sonuc:
                    self._yanitla(job, sonuc[0], sonuc[1])
                else:
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            except RuntimeError:
                pass              # iş tam bu arada silindi

        def bekleyen_sayisi(self) -> int:
            return len(self._bekleyen)

    return SemaIsleyici


_SINIF = None


def isleyici_sinifi():
    """`SemaIsleyici` sınıfı (ilk çağrıda kurulur)."""
    global _SINIF
    if _SINIF is None:
        _SINIF = _isleyici_sinifi()
    return _SINIF


def profili_kur(profil, kok: Path = STATIK):
    """Profile ``ta`` işleyicisini kur (kuruluysa olanı döndür)."""
    mevcut = profil.urlSchemeHandler(SEMA)
    if mevcut is not None:
        return mevcut
    isleyici = isleyici_sinifi()(profil, kok)
    profil.installUrlSchemeHandler(SEMA, isleyici)
    return isleyici


__all__ = ["SEMA", "UYGULAMA", "GORSEL", "STATIK", "BASLANGIC", "icerik_turu",
           "gorsel_turu", "statik_yol", "gorsel_adresi", "semayi_kaydet",
           "isleyici_sinifi", "profili_kur"]
