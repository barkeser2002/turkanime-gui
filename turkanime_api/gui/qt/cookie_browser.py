"""TRAnimeİzle cookie toplayıcı — QtWebEngine tabanlı (Selenium'un yerine).

Eski akış harici bir Selenium tarayıcısı (Chrome→Edge→Firefox→Chromium) açıp
`driver.get_cookies()`'i 2 sn'de bir yokluyordu; bu hem ağır (gerekirse onlarca
MB Chromium indiriyordu) hem de paketlenmiş EXE'de kırılgandı (Chrome yolu
bulma, registry taraması).

Yeni akış: uygulamanın **içine gömülü** bir `QWebEngineView`. Kullanıcı bot
kontrolünü/captcha'yı aynı pencerede çözer; cookie'ler `QWebEngineCookieStore`
üzerinden **olay tabanlı** (`cookieAdded`) toplanır — yoklama yok.

Public kontrat eski modülle birebir aynıdır (`on_status` / `on_cookies` /
`on_error`, `start()` / `stop()` / `is_running`) ve üretilen Netscape metni de
aynı formatta olduğu için `tranime.set_session_cookie()` değişmeden çalışır.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

# ── Eski modülle aynı sabitler ──────────────────────────────────────────────
TRANIME_BASE = "https://www.tranimeizle.io"
TARGET_ANIME = f"{TRANIME_BASE}/anime/naruto-izle"
COOKIE_DOMAIN = "tranimeizle.io"
REQUIRED_COOKIES = {".AitrWeb.Session"}
MAX_WAIT_SECONDS = 300  # 5 dakika


def is_available() -> bool:
    """QtWebEngine kullanılabilir mi? (eski modülde: Selenium kurulu mu)"""
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except ImportError:
        return False


# ── Cookie dönüşümü ─────────────────────────────────────────────────────────
def _qcookie_to_dict(cookie: QNetworkCookie) -> Dict:
    """QNetworkCookie -> eski Selenium cookie sözlüğüyle aynı şekil."""
    expiry = 0
    exp = cookie.expirationDate()
    if exp.isValid():
        expiry = max(0, exp.toSecsSinceEpoch())
    return {
        "domain": cookie.domain(),
        "path": cookie.path() or "/",
        "secure": bool(cookie.isSecure()),
        "expiry": expiry,
        "name": bytes(cookie.name()).decode("utf-8", "replace"),
        "value": bytes(cookie.value()).decode("utf-8", "replace"),
    }


def _cookies_to_netscape(cookies: List[Dict]) -> str:
    """Cookie listesini Netscape HTTP Cookie File formatına çevir.

    Çıktı eski Selenium sürümüyle birebir aynı şekildedir; `set_session_cookie`
    bu metni tab ile ayırıp 7 alan bekler.
    """
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# TürkAnime GUI tarafından otomatik oluşturuldu.",
        "",
    ]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        expiry = str(int(c.get("expiry", 0)))
        name = c.get("name", "")
        value = c.get("value", "")
        if not name:
            continue
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def _has_required_cookies(cookies: List[Dict]) -> bool:
    names = {c.get("name", "") for c in cookies}
    return REQUIRED_COOKIES.issubset(names)


def _filter_tranime_cookies(cookies: List[Dict]) -> List[Dict]:
    return [c for c in cookies if COOKIE_DOMAIN in c.get("domain", "")]


# ── Gömülü tarayıcı diyaloğu ────────────────────────────────────────────────
class CookieBrowserDialog(QDialog):
    """İçinde gerçek bir tarayıcı olan cookie toplama penceresi."""

    cookies_ready = Signal(str)   # Netscape metni
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("TRAnimeİzle — Bot kontrolünü çözün")
        self.resize(1000, 760)
        self.setSizeGripEnabled(True)

        self._cookies: Dict[str, Dict] = {}
        self._finished = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.lblStatus = QLabel("Tarayıcı hazırlanıyor…")
        self.lblStatus.setObjectName("Muted")
        self.lblStatus.setWordWrap(True)
        layout.addWidget(self.lblStatus)

        self.view = self._build_view()
        layout.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btnCancel = QPushButton("İptal")
        self.btnCancel.clicked.connect(self.reject)
        row.addWidget(self.btnCancel)
        layout.addLayout(row)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._timeout.start(MAX_WAIT_SECONDS * 1000)

    def _build_view(self):
        from PySide6.QtWebEngineCore import QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        # GEÇİCİ (off-the-record) profil — bilinçli tercih.
        #
        # Kalıcı profil kullanmak gerçek bir hataya yol açıyordu: çerez diske
        # yazıldıktan sonra sunucu AYNI değeri gönderdiğinde Chromium çerezi
        # "değişmedi" sayıp `cookieAdded` yaymıyor. Qt6'da cookie store'un
        # okuma API'si de yok (`loadAllCookies()` mevcut çerezleri yeniden
        # yayınlamıyor), dolayısıyla çerezi daha önce almış kullanıcı sonsuza
        # dek "bot kontrolünü çözün" ekranında kalıyordu.
        #
        # Kalıcılığa zaten ihtiyacımız yok: çerez `ayarlar.json`'a
        # (`tranime_cookie`) bizim tarafımızdan kaydediliyor. Her oturumu temiz
        # başlatmak `cookieAdded`'in daima tetiklenmesini garantiler.
        #
        # YAŞAM SÜRESİ: Qt, profilin sayfadan **uzun yaşamasını** şart koşar.
        # Profil dialog'a çocuk yapılırsa yıkım sırası profili sayfadan önce
        # silebiliyor; ebeveynsiz bırakılıp yalnızca Python referansıyla
        # tutulursa da dialog GC edilince aynı yarış oluşuyor (test paketinde
        # Windows erişim ihlaliyle çökme olarak yakalandı).
        # Çözüm: profili QApplication'a bağla — uygulama boyunca yaşar, yani
        # her zaman sayfadan sonra yıkılır. Ebeveynli `QWebEngineProfile(parent)`
        # yine **off-the-record**'dur (kalıcı profil ancak isim verilince olur).
        from PySide6.QtWidgets import QApplication
        self._profile = QWebEngineProfile(QApplication.instance())

        store = self._profile.cookieStore()
        store.cookieAdded.connect(self._on_cookie_added)
        try:
            store.loadAllCookies()
        except Exception:
            pass

        self._seed_age_cookie(store)

        view = QWebEngineView(self)
        from PySide6.QtWebEngineCore import QWebEnginePage
        view.setPage(QWebEnginePage(self._profile, view))
        view.loadFinished.connect(self._on_load_finished)
        return view

    @staticmethod
    def _seed_age_cookie(store) -> None:
        """Yaş doğrulamasını önden set et (eski sürümdeki davranış)."""
        try:
            cookie = QNetworkCookie(b"age_verified", b"true")
            cookie.setDomain("." + COOKIE_DOMAIN)
            cookie.setPath("/")
            store.setCookie(cookie, QUrl(TRANIME_BASE))
        except Exception:
            pass

    # ── Akış ────────────────────────────────────────────────────────────────
    def begin(self) -> None:
        """Yüklemeyi başlat.

        ÖNEMLİ: Bu çağrıdan önce pencere `show()` edilmiş olmalı. Gösterilmemiş
        bir `QWebEngineView` render sürecini başlatmaz ve yükleme sessizce
        `loadFinished(False)` ile düşer (çerez de toplanmaz).
        `CookieBrowserWorker.start()` bu sırayı garanti eder.
        """
        self._set_status("Sayfa yükleniyor… Bot kontrolü çıkarsa pencerede çözün.")
        store = self._profile.cookieStore()
        # Yükleme başlamadan hemen önce tekrar tohumla: WebEngine çekirdeği ilk
        # yüklemeyle ayağa kalktığı için bu, çerezin kesinlikle uygulanmasını garantiler.
        self._seed_age_cookie(store)
        self.view.load(QUrl(TARGET_ANIME))

    def _set_status(self, msg: str) -> None:
        self.lblStatus.setText(msg)
        self.status.emit(msg)

    def _on_cookie_added(self, cookie: QNetworkCookie) -> None:
        data = _qcookie_to_dict(cookie)
        if COOKIE_DOMAIN not in data.get("domain", ""):
            return
        self._cookies[data["name"]] = data
        self._check_done()

    def _on_load_finished(self, ok: bool) -> None:
        if self._finished:
            return
        if not ok:
            self._set_status("Sayfa yüklenemedi; bağlantınızı kontrol edin.")
            return
        if not self._check_done():
            self._set_status(
                "Bot kontrolünü/captcha'yı bu pencerede çözün. "
                "Oturum çerezi alınınca pencere kendiliğinden kapanacak."
            )

    def _check_done(self) -> bool:
        cookies = _filter_tranime_cookies(list(self._cookies.values()))
        if not _has_required_cookies(cookies):
            return False
        self._finished = True
        self._timeout.stop()
        self._set_status("Oturum çerezi alındı.")
        self.cookies_ready.emit(_cookies_to_netscape(cookies))
        QTimer.singleShot(400, self.accept)
        return True

    def _on_timeout(self) -> None:
        if self._finished:
            return
        self.failed.emit(
            f"Süre doldu ({MAX_WAIT_SECONDS} sn): oturum çerezi alınamadı."
        )
        self.reject()

    def reject(self):  # noqa: D102 (Qt imzası)
        if not self._finished:
            self._finished = True
            self._timeout.stop()
        super().reject()


# ── Eski API ile uyumlu sarmalayıcı ─────────────────────────────────────────
class CookieBrowserWorker(QObject):
    """Selenium tabanlı `CookieBrowserWorker` ile aynı yüzey, QtWebEngine gövdesi.

    Fark: QtWebEngine ana (GUI) thread'inde çalışmak zorunda olduğu için artık
    ayrı bir thread yok; bu yüzden `dispatch` parametresine gerek kalmadı.
    `dispatch` ve `allow_download` yalnızca çağıran kodu bozmamak için kabul
    edilir ve yok sayılır (Chromium indirme mekanizması tamamen kalktı).
    """

    def __init__(
        self,
        on_status: Optional[Callable[[str], None]] = None,
        on_cookies: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        dispatch: Optional[Callable] = None,      # geriye dönük uyumluluk
        allow_download: Optional[Callable] = None,  # geriye dönük uyumluluk
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.on_status = on_status or (lambda m: None)
        self.on_error = on_error or (lambda m: None)
        self.on_cookies = on_cookies or (lambda m: None)
        self._parent_widget = parent
        self._dialog: Optional[CookieBrowserDialog] = None
        self._done = False

    @property
    def is_running(self) -> bool:
        return self._dialog is not None and self._dialog.isVisible()

    def start(self) -> None:
        """Gömülü tarayıcı penceresini aç."""
        if self.is_running:
            return
        if not is_available():
            self._safe(self.on_error, "QtWebEngine kullanılamıyor (PySide6-Addons kurulu mu?).")
            return

        self._done = False
        dlg = CookieBrowserDialog(self._parent_widget)
        dlg.status.connect(lambda m: self._safe(self.on_status, m))
        dlg.cookies_ready.connect(self._on_cookies_ready)
        dlg.failed.connect(lambda m: self._safe(self.on_error, m))
        dlg.finished.connect(self._on_dialog_finished)
        self._dialog = dlg

        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dlg.show()
        dlg.begin()

    def stop(self) -> None:
        """İptal et ve pencereyi kapat."""
        if self._dialog is not None:
            self._dialog.reject()

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def _on_cookies_ready(self, netscape: str) -> None:
        if self._done:
            return
        self._done = True
        self._safe(self.on_cookies, netscape)

    def _on_dialog_finished(self, _result: int) -> None:
        if not self._done:
            self._safe(self.on_status, "Cookie toplama iptal edildi.")
        # Referansı bu slot'un içinde DÜŞÜRMÜYORUZ: dialog hâlâ `finished`
        # sinyalini yayıyor; son referansı burada bırakmak nesneyi yayın
        # sırasında yok eder (use-after-free). Yıkımı olay döngüsüne bırakıyoruz.
        dlg, self._dialog = self._dialog, None
        if dlg is not None:
            dlg.deleteLater()

    @staticmethod
    def _safe(cb: Callable, *args) -> None:
        try:
            cb(*args)
        except Exception:
            pass


__all__ = [
    "CookieBrowserWorker",
    "CookieBrowserDialog",
    "is_available",
    "TRANIME_BASE",
    "TARGET_ANIME",
    "COOKIE_DOMAIN",
    "REQUIRED_COOKIES",
    "MAX_WAIT_SECONDS",
]
