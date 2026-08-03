"""PySide6 GUI giriş noktası — CustomTkinter `MainWindow` yerine geçecek iskelet.

Bu modül A0 fazının çıktısıdır: Qt event loop'u, tema, pencere iskeleti ve
threading köprüleri. İçerik sayfaları (arama, bölüm listesi, indirmeler) sonraki
fazlarda `QStackedWidget` içine doldurulur.

Eski akış:  ctk.set_appearance_mode -> MainWindow(ctk.CTk) -> app.mainloop()
Yeni akış:  prepare_qt_env() -> QApplication -> MainWindow(QMainWindow) -> exec()
"""
from __future__ import annotations

import os
import sys
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .pages.detail import DetailPage
from .pages.discover import DiscoverPage
from .pages.downloads import DownloadManager, DownloadsPage
from .pages.episodes import EpisodePage
from .pages.search import SearchPage
from .pages.settings import SettingsPage
from .theme import apply_theme
from .workers import UiBridge, run_bg

APP_TITLE = "TürkAnime İndirici"

# Sol menü: (anahtar, etiket). Sonraki fazlarda her biri gerçek sayfayla dolacak.
NAV_ITEMS = [
    ("home", "Ana Sayfa"),
    ("search", "Arama"),
    ("season", "Bu Sezon"),
    ("trending", "Trend"),
    ("watchlist", "İzleme Listesi"),
    ("downloads", "İndirilenler"),
    ("settings", "Ayarlar"),
]


def _resource_path(rel: str) -> str:
    """PyInstaller tek-dosya ve geliştirme ortamında kaynak yolu çözer."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, rel)
    # turkanime_api/gui/qt/app.py -> proje kökü
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    return os.path.join(root, rel)


def prepare_qt_env() -> None:
    """QApplication kurulmadan **önce** çağrılmalı.

    QtWebEngine, OpenGL bağlamlarının paylaşılmasını şart koşar; bu attribute
    QApplication yaratıldıktan sonra ayarlanırsa etkisiz olur (ve WebEngine
    çalışmaz). Ayrıca WebEngine çekirdeğini erken import ederek başlatma
    sırasını garantiye alıyoruz.
    """
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    # Bazı sanal/başsız ortamlarda GPU yok; WebEngine'i yumuşak başlat.
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-gpu-compositing --disable-features=UseChromeOSDirectVideoDecoder",
    )

    try:  # WebEngine opsiyonel kalsın: yoksa GUI yine de açılmalı
        import PySide6.QtWebEngineCore  # noqa: F401
    except ImportError:
        pass


class PlaceholderPage(QWidget):
    """Henüz taşınmamış sayfalar için geçici içerik."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(8)

        heading = QLabel(title)
        heading.setObjectName("Title")
        note = QLabel("Bu bölüm Qt'ye taşınıyor.")
        note.setObjectName("Muted")

        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    """Uygulamanın ana penceresi (CustomTkinter `MainWindow`'un Qt karşılığı)."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1400, 900)
        self.setMinimumSize(1024, 640)

        icon_path = _resource_path(os.path.join("docs", "TurkAnime.ico"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # Arka plan işlerinden UI'ya güvenli geçiş köprüsü (eski `after(0, ...)`)
        self.ui = UiBridge(self)
        self.downloads = DownloadManager(self)
        self.downloads.finished.connect(
            lambda _id, ok, msg: self._status(("İndirme " if ok else "İndirme başarısız: ") + msg)
        )

        self._playing = False          # aynı anda tek oynatma denemesi
        # Detay sayfasındaki "← Geri" hangi sekmeden gelindiyse oraya dönmeli.
        self._detail_origin = "home"
        self.pages: Dict[str, QWidget] = {}
        self._build_ui()
        self.show_page("home")

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        for key, label in NAV_ITEMS:
            page = self._make_page(key, label)
            self.pages[key] = page
            self.stack.addWidget(page)

        # Detay ve bölüm listesi menüde yer almaz; keşif/arama sonucundan açılır.
        detail = DetailPage()
        detail.episodes_ready.connect(self._on_detail_episodes)
        detail.back_requested.connect(self._on_detail_back)
        self.pages["detail"] = detail
        self.stack.addWidget(detail)

        episodes = EpisodePage()
        episodes.play_requested.connect(self._on_play)
        episodes.download_requested.connect(self._on_download)
        self.pages["episodes"] = episodes
        self.stack.addWidget(episodes)

        body.addWidget(self.stack, 1)

        outer.addLayout(body, 1)

    def _make_page(self, key: str, label: str) -> QWidget:
        """İlgili sayfayı üret; henüz taşınmamışsa yer tutucu döndür."""
        if key == "search":
            page = SearchPage()
            page.anime_selected.connect(self._on_anime_selected)
            return page
        if key in ("home", "trending", "season"):
            page = DiscoverPage(key)
            page.anime_selected.connect(self._on_discover_selected)
            return page
        if key == "downloads":
            return DownloadsPage(self.downloads)
        if key == "settings":
            return SettingsPage()
        return PlaceholderPage(label)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(64)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(12)

        brand = QLabel(APP_TITLE)
        brand.setObjectName("Subtitle")
        layout.addWidget(brand)
        layout.addSpacing(16)

        self.txtSearch = QLineEdit()
        self.txtSearch.setPlaceholderText("Anime ara…")
        self.txtSearch.setClearButtonEnabled(True)
        self.txtSearch.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Fixed)
        self.txtSearch.returnPressed.connect(self._on_search)
        layout.addWidget(self.txtSearch, 1)

        self.btnSearch = QPushButton("Ara")
        self.btnSearch.setObjectName("Primary")
        self.btnSearch.clicked.connect(self._on_search)
        layout.addWidget(self.btnSearch)

        return header

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 14, 10, 14)
        layout.setSpacing(4)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: Dict[str, QPushButton] = {}

        for key, label in NAV_ITEMS:
            btn = QPushButton(label)
            btn.setObjectName("Nav")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self._nav_group.addButton(btn)
            self._nav_buttons[key] = btn
            layout.addWidget(btn)
            if key == "home":
                btn.setChecked(True)

        layout.addStretch(1)

        version = QLabel(self._version_text())
        version.setObjectName("Muted")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        return sidebar

    @staticmethod
    def _version_text() -> str:
        try:
            from ... import version as _v
            ver = getattr(_v, "__version__", None) or getattr(_v, "APP_VERSION", None)
            return f"v{ver}" if ver else ""
        except Exception:
            return ""

    # ── Davranış ────────────────────────────────────────────────────────────
    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)

    def _on_search(self) -> None:
        query = self.txtSearch.text().strip()
        if not query:
            return
        self.show_page("search")
        self._sync_nav("search")
        page = self.pages.get("search")
        if isinstance(page, SearchPage):
            page.start_search(query)

    def _sync_nav(self, key: str) -> None:
        """Sol menüdeki seçili düğmeyi programatik geçişlerle senkron tut."""
        btn = self._nav_buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    def _on_discover_selected(self, item) -> None:
        """Keşif kartına tıklandı: kaydın tamamıyla detay sayfasını aç.

        Kaynak/slug verilmiyor: MyAnimeList/AniList kimliğinin TürkAnime
        kaynaklarındaki karşılığı bilinmiyor. Kullanıcı detay sayfasında
        "Bölümleri Getir"e basınca eşleştirme diyaloğu devreye girer.
        """
        page = self.pages.get("detail")
        if isinstance(item, dict) and item and isinstance(page, DetailPage):
            self._open_detail(lambda: page.show_anime(item))

    def _on_anime_selected(self, source: str, slug: str, title: str) -> None:
        """Arama sonucundan anime seçildi: kaynağı bağlı detay sayfasını aç."""
        page = self.pages.get("detail")
        if isinstance(page, DetailPage):
            self._open_detail(lambda: page.show_match(source, slug, title))

    def _open_detail(self, populate) -> None:
        """Detay sayfasına geç ve dönüş noktasını hatırla.

        Detaya hem keşiften hem aramadan gelinebiliyor; sabit bir "Geri" hedefi
        (ör. ana sayfa) kullanıcıyı aramasından koparırdı.
        """
        current = self.stack.currentWidget()
        for key, page in self.pages.items():
            if page is current and key not in ("detail", "episodes"):
                self._detail_origin = key
                break
        self.show_page("detail")
        populate()

    def _on_detail_back(self) -> None:
        self.show_page(self._detail_origin)
        self._sync_nav(self._detail_origin)

    def _on_detail_episodes(self, source: str, slug: str, title: str,
                            episodes) -> None:
        """Detay sayfası bölümleri çekti: listeyi olduğu gibi devral.

        `EpisodePage.load` burada `episodes` ile çağrılır; parametresiz çağrı
        aynı listeyi ikinci kez ağdan indirirdi.
        """
        page = self.pages.get("episodes")
        if isinstance(page, EpisodePage):
            self.show_page("episodes")
            page.load(source, slug, title, episodes=episodes)

    # ── Oynatma / indirme ───────────────────────────────────────────────────
    def _status(self, msg: str, timeout: int = 6000) -> None:
        """Durum çubuğuna yaz (her thread'den güvenli)."""
        self.ui.post(lambda: self.statusBar().showMessage(msg, timeout))

    def _on_play(self, entry) -> None:
        bolum = (entry or {}).get("obj")
        if bolum is None:
            return
        if self._playing:
            self._status("Zaten bir bölüm açılıyor, lütfen bekleyin.")
            return
        self._playing = True
        self._status(f"{entry.get('title')} — video aranıyor…")
        # long_running: oynatma, mpv kapanana kadar thread'i tutar.
        run_bg(self._play_blocking, bolum, entry.get("title") or "",
               long_running=True)

    def _play_blocking(self, bolum, title: str) -> None:
        # NOT: Bu gövde arka plan thread'inde; hata yutulursa kullanıcı sonsuza
        # kadar "video aranıyor…" görür. Bu yüzden her çıkış yolu raporlanır.
        try:
            video = bolum.best_video()
            if video is None:
                self._status(f"{title} — çalışan video bulunamadı.")
                return
            self._status(f"{title} — oynatıcı açılıyor…")
            proc = video.oynat()
            if proc is None:
                # oynat() mpv bulunamazsa None döndürüp sessizce geçiyor.
                self._status(f"{title} — oynatıcı başlatılamadı (mpv kurulu mu?).")
        except Exception as exc:
            self._status(f"{title} — oynatma hatası: {exc}")
        finally:
            self._playing = False

    def _on_download(self, entry) -> None:
        """İndirmeyi kuyruğa al ve indirilenler panelini göster."""
        if not (entry or {}).get("obj"):
            return
        self.downloads.enqueue(entry, output=self._download_dir())
        self.show_page("downloads")
        self._sync_nav("downloads")

    @staticmethod
    def _download_dir() -> str:
        """İndirme klasörü.

        Boş string DÖNDÜRMÜYORUZ: yt-dlp onu çalışma dizini sayar; paketlenmiş
        uygulamada bu `Program Files` altı olur (yazma izni yok ya da dosyalar
        kaybolur). Ayar okunamazsa kullanıcının Downloads klasörüne düşüyoruz.
        """
        try:
            from ...cli.dosyalar import Dosyalar
            configured = (Dosyalar().ayarlar or {}).get("indirilenler")
            if configured and os.path.isdir(configured):
                return configured
            if configured:
                # Ayarlı ama yok: oluşturmayı dene, olmazsa yedeğe düş.
                try:
                    os.makedirs(configured, exist_ok=True)
                    return configured
                except OSError:
                    pass
        except Exception:
            pass
        fallback = os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            os.makedirs(fallback, exist_ok=True)
        except OSError:
            return os.path.expanduser("~")
        return fallback

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt imzası)
        """Kapanışta arka plan işlerini durdur.

        `clear()` tek başına yetmez: yalnızca HENÜZ BAŞLAMAMIŞ görevleri atar.
        Çalışan bir indirme, biz pencereyi yok ettikten sonra sinyal yaymaya
        devam eder ve silinmiş C++ nesnesine çarpar (çökme). Bu yüzden kısa bir
        süre bitmelerini bekliyoruz.
        """
        try:
            from .workers import shutdown_pools
            shutdown_pools(3000)
        except Exception:
            pass
        super().closeEvent(event)


def run() -> int:
    """GUI'yi başlat. `turkanime-gui` giriş noktası buraya bağlanacak."""
    prepare_qt_env()

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    app.setApplicationName(APP_TITLE)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
