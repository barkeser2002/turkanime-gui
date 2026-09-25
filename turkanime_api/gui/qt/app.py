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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSizePolicy, QStackedWidget, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from ...common import kutuphane, mpv_oynatici
from ...common.episode_parser import extract_episode_info
from ...common.oynatma import yedekli_oynat
from . import prefs
from .anilist import AniListService
from .discord import DiscordService
from .pages.detail import DetailPage
from .pages.discover import DiscoverPage
from .pages.downloads import DURUM_IPTAL, DownloadManager, DownloadsPage
from .pages.episodes import EpisodePage
from .pages.library import LibraryPage
from .pages.search import SearchPage
from .pages.settings import SettingsPage
from .pages.watchlist import WatchlistPage
from .progress_dialog import ProgressDialog, anime_adi
from .requirements import RequirementsDialog, RequirementsService
from .theme import ACCENT, apply_theme
from .updates import UpdateDialog, UpdateService
from .workers import UiBridge, run_bg

# Header'daki AniList avatarı (kare, köşeler tema tarafından yuvarlanmıyor).
AVATAR_BOYUTU = 28

APP_TITLE = "TürkAnime İndirici"

# Açılış denetimleri (güncelleme, gereksinim, Discord) pencere çizildikten sonra:
# ilk kareyi ağ isteği ve `--version` çağrılarıyla geciktirmenin anlamı yok.
ACILIS_DENETIM_GECIKMESI = 1500

# Kapanışta çalışan işlere tanınan mühlet. Bittiğinde bekleyen iş kalmışsa
# süreç zorla sonlandırılır (bkz. `run`).
KAPANIS_MUHLETI = 3000

# Sol menü: (anahtar, etiket). Sonraki fazlarda her biri gerçek sayfayla dolacak.
NAV_ITEMS = [
    ("home", "Ana Sayfa"),
    ("search", "Arama"),
    ("season", "Bu Sezon"),
    ("trending", "Trend"),
    ("watchlist", "İzleme Listesi"),
    ("library", "Kitaplığım"),
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
        self.downloads.finished.connect(self._on_download_finished)

        # AniList tek bir servisten yürür: ayar sayfası girişi yapar, izleme
        # listesi ve ilerleme yazımı aynı jetonu/oturumu paylaşır.
        self.anilist = AniListService(self)
        self.anilist.auth_changed.connect(self._on_anilist_user)
        self.anilist.avatar_ready.connect(self._on_anilist_avatar)
        self.anilist.status_changed.connect(self._on_anilist_status)

        # Çevresel servisler. Sayfalardan ÖNCE kuruluyor: `show_page` Discord'a
        # haber veriyor ve ayar sayfası bu üçünü düğmelerine bağlıyor.
        self.updates = UpdateService(self)
        self.updates.update_available.connect(self._on_update_available)
        self.updates.check_failed.connect(lambda mesaj: self._status(mesaj))
        self.requirements = RequirementsService(self)
        self.requirements.missing_found.connect(self._on_requirements_missing)
        self.discord = DiscordService(self)
        self._update_dialog: QWidget | None = None
        self._req_dialog: QWidget | None = None
        self._dl_titles: Dict[str, str] = {}
        # Kuyruk boşalana kadar biten işlerin sayımı (bkz. `_toplu_indirme_bitti`).
        self._toplu_indirme = {"ok": 0, "hata": 0}
        self._tepsi: QSystemTrayIcon | None = None
        self.downloads.added.connect(self._on_download_added)
        self.downloads.progress.connect(self._on_download_progress)
        # Menüdeki "İndirilenler (N)": indirme artık sayfayı değiştirmiyor,
        # kuyruğa girdiğini ve kaç işin sürdüğünü kullanıcı buradan görüyor.
        self.downloads.state.connect(self._indirme_sayacini_guncelle)

        self._playing = False          # aynı anda tek oynatma denemesi
        # Detay sayfasındaki "← Geri" hangi sekmeden gelindiyse oraya dönmeli.
        self._detail_origin = "home"
        self._current_page = "home"    # Discord durumu buradan türetiliyor
        self.pages: Dict[str, QWidget] = {}
        self._build_ui()
        self.show_page("home")
        # Jeton diskte duruyor olabilir; kullanıcı adını/avatarı arka planda al.
        self.anilist.baslat()
        QTimer.singleShot(ACILIS_DENETIM_GECIKMESI, self._acilis_denetimleri)

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
        episodes.kuyrukta_mi = self._kuyrukta_mi
        self.pages["episodes"] = episodes
        self.stack.addWidget(episodes)

        body.addWidget(self.stack, 1)

        outer.addLayout(body, 1)

    def _make_page(self, key: str, label: str) -> QWidget:
        """`NAV_ITEMS` anahtarına karşılık gelen sayfayı üret.

        Aşağıdaki dallar `NAV_ITEMS`'ın sekiz anahtarını da karşılıyor, yani
        sona düşmek mümkün değil. Yine de sessizce `None` dönüp çağıranın
        `addWidget`'ında anlamsız bir hatayla patlamak yerine burada
        bağırıyoruz: `NAV_ITEMS`'a dalı yazılmamış bir anahtar eklenirse
        hata, sebebini söyleyerek açılışta çıksın.
        """
        if key == "search":
            page = SearchPage()
            page.anime_selected.connect(self._on_anime_selected)
            return page
        if key in ("home", "trending", "season"):
            page = DiscoverPage(key)
            page.anime_selected.connect(self._on_discover_selected)
            page.kitaplik_secildi.connect(self._on_kitaplik_selected)
            return page
        if key == "library":
            page = LibraryPage()
            page.kitaplik_secildi.connect(self._on_kitaplik_selected)
            return page
        if key == "downloads":
            page = DownloadsPage(self.downloads)
            # Biten indirmenin "Oynat"ı normal oynatma yolundan geçer: yerel
            # dosya orada ilk aday, geçmiş/kitaplık yazımı da aynı yerde.
            page.oynat_istendi.connect(self._on_play)
            return page
        if key == "watchlist":
            page = WatchlistPage(self.anilist)
            page.anime_selected.connect(self._on_discover_selected)
            page.settings_requested.connect(self._goto_settings)
            return page
        if key == "settings":
            return SettingsPage(self.anilist, discord=self.discord,
                                updates=self.updates,
                                requirements=self.requirements)
        raise ValueError(f"NAV_ITEMS anahtarı {key!r} ({label}) için sayfa dalı yok")

    def _goto_settings(self) -> None:
        """"Ayarlar'a Git" yönlendirmesi (sol menü de senkron kalmalı)."""
        self.show_page("settings")
        self._sync_nav("settings")

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

        # AniList giriş durumu: avatar yalnızca indirildiğinde görünür, aksi
        # hâlde 28px'lik boş bir kutu header'da delik gibi durur.
        layout.addSpacing(8)
        self.lblAvatar = QLabel()
        self.lblAvatar.setFixedSize(AVATAR_BOYUTU, AVATAR_BOYUTU)
        self.lblAvatar.setVisible(False)
        layout.addWidget(self.lblAvatar)

        self.lblAniList = QLabel()
        self.lblAniList.setObjectName("Muted")
        layout.addWidget(self.lblAniList)
        self._on_anilist_user(None)

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
            self._current_page = key
            self.discord.sayfa(key)

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

    def _on_kitaplik_selected(self, kayit) -> None:
        """Kitaplık kartı: detayı kaynağa BAĞLI aç, bölümleri hemen getir.

        Keşif kartından farkı: kayıt kaynağın kendi kimliğini taşıyor, yani
        eşleştirme (ve yanlış eşleşme riski) yok (bkz. `kitaplik_ac`).
        """
        page = self.pages.get("detail")
        if isinstance(kayit, dict) and isinstance(page, DetailPage):
            self._open_detail(lambda: page.kitaplik_ac(kayit))

    def _on_anime_selected(self, source: str, slug: str, title: str,
                           kayit: object = None) -> None:
        """Arama sonucundan anime seçildi: kaynağı bağlı detay sayfasını aç.

        ``kayit`` arama kaydının kendisi (kapak adresi dahil); detay sayfası
        kartta görünen posteri tekrar göstermek için kullanıyor.
        """
        page = self.pages.get("detail")
        if isinstance(page, DetailPage):
            ek = kayit if isinstance(kayit, dict) else None
            self._open_detail(lambda: page.show_match(source, slug, title, kayit=ek))

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
        detail = self.pages.get("detail")
        # Kaynak başına kimlikler + kapak: kitaplık kaydı satırın KENDİ
        # kaynağının kimliğiyle yazılsın (bkz. `EpisodePage._kimlik_damgala`).
        baglam = (detail.kitaplik_baglami() if isinstance(detail, DetailPage)
                  else {})
        if isinstance(page, EpisodePage):
            self.show_page("episodes")
            page.load(source, slug, title, episodes=episodes,
                      baglar=baglam.get("baglar"), kapak=baglam.get("kapak") or "")

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
        self.discord.izliyor(anime_adi(bolum, ""), entry.get("title") or "")
        # playback: oynatma mpv kapanana kadar thread'i tutar ama İNDİRME
        # havuzuna girmemeli — kuyrukta 30 bölüm varsa mpv hiç açılmaz ve
        # `_playing` açık kaldığı için kullanıcı yeniden de deneyemez.
        run_bg(self._play_blocking, bolum, entry.get("title") or "", entry,
               playback=True)

    def _play_blocking(self, bolum, title: str, entry=None) -> None:
        # NOT: Bu gövde arka plan thread'inde; hata yutulursa kullanıcı sonsuza
        # kadar "video aranıyor…" görür. Bu yüzden her çıkış yolu raporlanır.
        #
        # Aday döngüsü CLI ile ORTAK (`common.oynatma.yedekli_oynat`). Eskiden
        # `best_video` bir kez çağrılıyor, mpv nasıl kapanırsa kapansın bölüm
        # "izlendi" yazılıyor ve ilerleme diyaloğu açılıyordu — mpv 2 ile
        # (dosya oynatılamadı) çıksa bile. Yeniden "Oynat" da işe yaramıyordu:
        # `best_video` aynı bozuk ilk adayı yine seçiyordu. Artık oynatılamayan
        # adres `atla` ile geri veriliyor ve geçmiş yalnızca başarıda yazılıyor.
        try:
            tercih = prefs.oku()
            # İndirilmiş bölüm ağa çıkmadan, diskten oynatılır. Yerel dosya İLK
            # aday: bozuksa (mpv 2) yolu `atla`ya girer ve döngü kendiliğinden
            # akışa geçer (bkz. `prefs.YerelVideo`).
            yerel = prefs.yerel_dosya(bolum, tercih,
                                      str((entry or {}).get("yerel_dosya") or ""))

            def bul(atla, callback):
                if yerel and os.path.abspath(yerel) not in atla:
                    return prefs.YerelVideo(yerel)
                return bolum.best_video(by_res=tercih.max_res,
                                        early_subset=tercih.aday_sayisi,
                                        callback=callback, atla=atla)

            # Kaldığı yer bölümün KENDİ anahtarıyla (kaynak + kimlik + bölüm):
            # mpv'nin adrese bağlı kaydı token'lı adreslerde ve başka aday
            # seçildiğinde kayboluyordu. Rapor dosyasını mpv betiği yazar.
            kayit = entry or {"obj": bolum}
            eski = prefs.konum_getir(kayit)
            baslangic = (eski or {}).get("konum")
            rapor_yolu = mpv_oynatici.konum_dosyasi_ayir()
            kayit_yolu = []          # "İzlerken kaydet" hedefi (akışta)

            def oynat(video):
                yerelden = isinstance(video, prefs.YerelVideo)
                self._status(f"{title} — "
                             + ("indirilmiş dosya açılıyor…" if yerelden
                                else "oynatıcı açılıyor…"))
                if (not yerelden and tercih.izlerken_kaydet
                        and mpv_oynatici.kaynak_videosu_mu(video)):
                    kayit_yolu.append(mpv_oynatici.kayit_hedefi_kur(
                        prefs.indirme_dizini(tercih), bolum))
                return prefs.oynat(video, tercih, baslangic=baslangic,
                                   konum_dosyasi=rapor_yolu, bolum=bolum)

            try:
                sonuc = yedekli_oynat(
                    bul, oynat, bildir=lambda m: self._status(f"{title} — {m}"))
            finally:
                rapor = mpv_oynatici.konum_oku(rapor_yolu, sil=True)
            bitti = rapor is not None and kutuphane.bitti_mi(
                rapor["konum"], rapor["sure"], rapor["sebep"])
            if kayit_yolu:
                # Kayıt yalnızca baştan sona izlendiyse "indirilmiş" sayılır.
                mpv_oynatici.kaydi_sonlandir(
                    kayit_yolu[-1], tam=bool(sonuc.basarili and rapor
                                             and rapor["sebep"] == "eof"
                                             and not (tercih.dakika_hatirla
                                                      and baslangic)))
            if not sonuc.basarili:
                self._status(f"{title} — {sonuc.sebep}", 10000)
                return
            # Buraya gelindiyse mpv düzgün kapandı. Kitaplık: "izlemeye devam
            # et" + bölüm geçmişi (kaynaksız kayıt yazılmaz, bkz. prefs).
            prefs.kitapliga_yaz(kayit, title)
            if rapor is None:
                # mpv rapor vermedi (Lua'sız derleme, eski `oynat`): bölümün
                # bitip bitmediği bilinmiyor — eski davranış, izlendi + soru.
                prefs.gecmis_kaydet(bolum, "izlendi")
                self._status(f"{title} — oynatma bitti.")
                self.ui.post(lambda: self._on_play_finished(bolum, title, "sor"))
            elif bitti:
                prefs.gecmis_kaydet(bolum, "izlendi")
                prefs.konum_yaz(kayit, None)        # bir dahaki sefere baştan
                self._status(f"{title} — izlendi.")
                kip = "sor" if tercih.ilerlemeyi_sor else "otomatik"
                self.ui.post(lambda: self._on_play_finished(bolum, title, kip))
            else:
                # Yarıda kapatıldı: izlendi YAZILMAZ, ilerleme sorulmaz; yer
                # saklanır ve bölüm listesi "Devam et" gösterir.
                konum = rapor["konum"] or 0
                if konum >= kutuphane.ASGARI_KONUM:
                    prefs.konum_yaz(kayit, konum, rapor["sure"])
                    self._status(f"{title} — kaldığınız yer "
                                 f"({kutuphane.sure_metni(konum)}) kaydedildi; "
                                 "bir dahaki sefere oradan devam edilecek.")
                else:
                    self._status(f"{title} — oynatma kapatıldı.")
                self.ui.post(lambda: self._on_play_finished(bolum, title, ""))
        except Exception as exc:
            self._status(f"{title} — oynatma hatası: {exc}")
        finally:
            self._playing = False

    def _on_play_finished(self, bolum, title: str, ilerleme: str = "sor") -> None:
        """Oynatma bitti (GUI thread'i): rozetleri tazele, ilerlemeyi işle.

        ``ilerleme``: "sor" → diyalog (rapor yok ya da ayar açık), "otomatik"
        → bölüm numarası başlıktan yazılır, "" → dokunulmaz (yarıda kaldı).
        Her bölümden sonra açılan modal soru, bölümü sonuna kadar izleyen
        kullanıcıya her seferinde aynı cevabı yazdırıyordu.
        """
        self.discord.sayfa(self._current_page)
        self._refresh_episode_history()
        if ilerleme == "sor":
            self._ask_progress(bolum, title)
        elif ilerleme == "otomatik":
            self._otomatik_ilerleme(bolum, title)

    def _otomatik_ilerleme(self, bolum, title: str) -> None:
        """Bitmiş bölümün numarasını yerel ilerlemeye ve AniList'e yaz.

        Numara diyaloğunkiyle aynı yoldan (`extract_episode_info`, seri adı
        verilerek: "86 2nd Season 5. Bölüm"de 86 bölüm sanılmasın). İlerleme
        GERİ ALINMAZ: 10. bölümdeki kullanıcı 3'ü yeniden izlerse AniList'e 3
        yazmak yanlış olurdu.
        """
        seri, _slug = prefs.bolum_kimligi(bolum)
        ad = anime_adi(bolum, seri)
        _, no = extract_episode_info(title or _slug, ad)
        if not (seri and no) or no <= prefs.yerel_ilerleme().get(seri, 0):
            return
        if prefs.ilerleme_kaydet(seri, no):
            self._on_progress_saved(seri, no, ad)

    def _ask_progress(self, bolum, title: str) -> None:
        """İzleme ilerlemesi diyaloğunu aç (eski `show_progress_dialog`)."""
        dialog = ProgressDialog(bolum, title, self)
        # Okunabilir seri adını sinyale iliştiriyoruz: `progress_saved` yalnızca
        # slug taşıyor, AniList'te "naruto-test" diye aramak eşleşmez.
        ad = getattr(dialog, "anime_adi", "")
        dialog.progress_saved.connect(
            lambda seri, no, _ad=ad: self._on_progress_saved(seri, no, _ad))
        dialog.exec()

    def _on_progress_saved(self, seri: str, bolum_no: int,
                           anime_adi: str = "") -> None:
        """Yerel ilerleme yazıldı; AniList'e de yansıt.

        `ilerleme_yaz` giriş yoksa sessizce atlar — yerel kayıt zaten yapıldı,
        AniList kullanmayan kullanıcıyı her bölüm sonunda uyarmanın anlamı yok.
        """
        self._status(f"İlerleme kaydedildi: {seri or 'seri'} — {bolum_no}. bölüm")
        self.anilist.ilerleme_yaz(seri, bolum_no, anime_adi)

    # ── AniList ─────────────────────────────────────────────────────────────
    def _on_anilist_user(self, user) -> None:
        """Giriş durumu değişti (GUI thread'i): header'ı ve senkronu güncelle."""
        if isinstance(user, dict) and user.get("name"):
            self.lblAniList.setText(str(user["name"]))
            self.lblAniList.setStyleSheet(f"color: {ACCENT}; font-weight: 600;")
            # Giriş tazelendiğinde AniList → yerel ilerleme senkronu; kullanıcı
            # başka cihazda izlediyse rozetleri burada yakalıyoruz.
            self.anilist.yereli_senkronla()
        else:
            self.lblAniList.setText("AniList: giriş yok")
            self.lblAniList.setStyleSheet("")
            self.lblAvatar.clear()
            self.lblAvatar.setVisible(False)

    def _on_anilist_avatar(self, data) -> None:
        pix = QPixmap()
        if not pix.loadFromData(bytes(data or b"")):
            return
        self.lblAvatar.setPixmap(pix.scaled(
            AVATAR_BOYUTU, AVATAR_BOYUTU,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation))
        self.lblAvatar.setVisible(True)

    def _on_anilist_status(self, mesaj: str, _hata: bool) -> None:
        """Servis mesajları durum çubuğuna (sinyal zaten GUI thread'inde)."""
        self.statusBar().showMessage(mesaj, 8000)

    def _refresh_episode_history(self) -> None:
        page = self.pages.get("episodes")
        if isinstance(page, EpisodePage):
            page.refresh_history()

    def _on_download(self, entry) -> None:
        """İndirmeyi kuyruğa al; kullanıcı bulunduğu bölüm listesinde KALIR.

        Eskiden burada İndirilenler sayfasına geçiliyordu. Bölüm listesinin
        menüde düğmesi, kendisinin de "Geri"si yok: kullanıcı listeye ancak
        aramayı baştan yapıp "Bölümleri Getir"le (yeniden ağ isteği, keşif
        kayıtlarında elle eşleştirme diyaloğu) dönebiliyordu. Toplu indirmede
        sayfa bölüm başına bir kez değiştiriliyor, "N bölüm sıraya alındı"
        mesajı da gizlenmiş sayfaya yazılıyordu. Artık onay durum çubuğunda,
        sürenlerin sayısı menüdeki "İndirilenler (N)" düğmesinde.
        """
        if not (entry or {}).get("obj"):
            return
        baslik = entry.get("title") or "Bölüm"
        output = self._download_dir()
        if self.downloads.kuyruktaki_is(entry, output) is not None:
            self.statusBar().showMessage(f"{baslik} zaten kuyrukta.", 6000)
            return
        self.downloads.enqueue(entry, output=output)
        self.statusBar().showMessage(
            f"{baslik} indirme sırasına alındı — ilerleme: İndirilenler.", 6000)

    def _kuyrukta_mi(self, entry) -> bool:
        """`EpisodePage` toplu indirmesi için: bölümün bitmemiş işi var mı?"""
        return self.downloads.kuyruktaki_is(entry, self._download_dir()) is not None

    def _indirme_sayacini_guncelle(self, *_args) -> None:
        """Menüdeki İndirilenler düğmesine süren iş sayısını yaz."""
        btn = self._nav_buttons.get("downloads")
        if btn is None:
            return
        etiket = dict(NAV_ITEMS)["downloads"]
        sayi = len(self.downloads.active_ids())
        btn.setText(f"{etiket} ({sayi})" if sayi else etiket)

    def _on_download_added(self, task_id: str, title: str) -> None:
        """İş adlarını sakla: `progress` sinyali yalnızca kimlik taşıyor."""
        self._dl_titles[task_id] = title

    def _on_download_progress(self, task_id: str, yuzde: int, _detay: str) -> None:
        self.discord.indiriyor(self._dl_titles.get(task_id, "Bölüm"), yuzde)

    def _on_download_finished(self, task_id: str, ok: bool, mesaj: str) -> None:
        self._status(("İndirme: " if ok else "İndirme başarısız: ") + mesaj)
        self._dl_titles.pop(task_id, None)
        # Toplu indirmenin özeti: iptal hata sayılmaz, kullanıcı kendisi kesti.
        if ok:
            self._toplu_indirme["ok"] += 1
        elif self.downloads.durum(task_id) != DURUM_IPTAL:
            self._toplu_indirme["hata"] += 1
        if not self.downloads.active_ids():
            self.discord.sayfa(self._current_page)
            self._toplu_indirme_bitti()
        if ok:
            # Bölüm satırındaki ⬇ rozeti geçmişten okunuyor; liste açıksa tazele.
            self._refresh_episode_history()

    def _toplu_indirme_bitti(self) -> None:
        """Kuyruk boşaldı: pencere arka plandaysa masaüstü bildirimi.

        Durum çubuğu mesajı 6 saniye duruyor; 30 bölümlük kuyruğu başlatıp
        başka işe geçen kullanıcı indirmenin bittiğini hiç görmüyordu.
        Pencere öndeyse bildirim yok: kullanıcı zaten bakıyor.
        """
        ok, hata = self._toplu_indirme["ok"], self._toplu_indirme["hata"]
        self._toplu_indirme = {"ok": 0, "hata": 0}
        if not (ok or hata):
            return                      # hepsi iptal edildi
        mesaj = f"{ok} bölüm indirildi" + (f", {hata} hata" if hata else "")
        self.statusBar().showMessage(f"İndirmeler bitti: {mesaj}.", 10000)
        if not self.isActiveWindow():
            self._bildir("İndirmeler bitti", mesaj)

    def _bildir(self, baslik: str, mesaj: str) -> None:
        """Masaüstü bildirimi: sistem tepsisi varsa balon, yoksa görev çubuğu.

        Tepsi simgesi ilk bildirimde kuruluyor; bildirimsiz oturumda tepside
        boş yere simge durmasın.
        """
        if QSystemTrayIcon.isSystemTrayAvailable():
            if self._tepsi is None:
                self._tepsi = QSystemTrayIcon(self.windowIcon(), self)
                self._tepsi.setToolTip(APP_TITLE)
                self._tepsi.show()
            self._tepsi.showMessage(baslik, mesaj)
        else:
            QApplication.alert(self)

    @staticmethod
    def _download_dir() -> str:
        """İndirme klasörü (ayarlardan; bkz. `prefs.indirme_dizini`)."""
        return prefs.indirme_dizini()

    # ── Çevresel servisler ──────────────────────────────────────────────────
    def _acilis_denetimleri(self) -> None:
        """Açılıştaki sessiz denetimler (pencere çizildikten sonra)."""
        self.discord.baslat()
        self.updates.kontrol_et(sessiz=True)
        self.requirements.denetle()

    def _on_update_available(self, version_data) -> None:
        """Yeni sürüm bulundu: diyaloğu aç (GUI thread'i).

        `exec()` yerine `open()`: açılış denetimi kullanıcının önüne iç içe bir
        olay döngüsü koymamalı, pencereyi kullanmaya devam edebilmeli.
        """
        if self._update_dialog is not None:
            return
        self._status(f"Yeni sürüm mevcut: {(version_data or {}).get('version', '')}")
        dialog = UpdateDialog(self.updates, version_data, self)
        dialog.finished.connect(lambda _=0: self._dialog_kapandi("_update_dialog"))
        self._update_dialog = dialog
        dialog.open()

    def _on_requirements_missing(self, eksikler) -> None:
        """Eksik araç sihirbazını aç."""
        if self._req_dialog is not None or not eksikler:
            return
        self._status("Eksik araçlar bulundu: " + ", ".join(eksikler))
        dialog = RequirementsDialog(self.requirements, eksikler, self)
        dialog.finished.connect(lambda _=0: self._dialog_kapandi("_req_dialog"))
        self._req_dialog = dialog
        dialog.open()

    def _dialog_kapandi(self, alan: str) -> None:
        """Diyalog referansını bırak; `deleteLater` olmadan pencere sızar."""
        dialog = getattr(self, alan, None)
        setattr(self, alan, None)
        if dialog is not None:
            dialog.deleteLater()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt imzası)
        """Kapanışta arka plan işlerini durdur.

        `clear()` tek başına yetmez: yalnızca HENÜZ BAŞLAMAMIŞ görevleri atar.
        Çalışan bir indirme, biz pencereyi yok ettikten sonra sinyal yaymaya
        devam eder ve silinmiş C++ nesnesine çarpar (çökme). Bu yüzden önce
        işleri iptal ediyor, sonra kısa süre bitmelerini bekliyoruz.
        """
        try:
            self.discord.durdur()
        except Exception:
            pass
        try:
            # İptal ŞART: yalnızca beklemek yetmez, yt-dlp indirmeyi sonuna
            # kadar sürdürür ve süreç dakikalarca kapanmaz. `cancel_all` iptal
            # bayrağını kaldırır, ilerleme hook'u bir sonraki parçada görüp
            # indirmeyi bırakır.
            self.downloads.cancel_all()
        except Exception:
            pass
        try:
            # Aynı sebeple süren tam arşiv indirmesi (~230 MB) de iptal edilir;
            # yarım paket geçici klasörle birlikte silinir, eski arşiv yerinde.
            ayarlar = self.pages.get("settings")
            if isinstance(ayarlar, SettingsPage):
                ayarlar.arsiv_indirmeyi_durdur()
        except Exception:
            pass
        try:
            from .workers import shutdown_pools
            shutdown_pools(KAPANIS_MUHLETI)
        except Exception:
            pass
        super().closeEvent(event)


def run() -> int:
    """GUI'yi başlat. `turkanime-gui` giriş noktası buraya bağlanacak."""
    prepare_qt_env()
    # mpv/ffmpeg/aria2c uygulama dizininde ya da gömülü `bin/` altında olabilir;
    # eski CTk giriş noktası PATH'i böyle hazırlıyordu, Qt'ninki unutmuştu.
    try:
        from ...common.requirements import path_hazirla
        path_hazirla()
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    app.setApplicationName(APP_TITLE)

    window = MainWindow()
    window.show()
    kod = app.exec()

    # `~QThreadPool` yıkıcısı ZAMAN AŞIMSIZ `waitForDone()` çağırır: havuzda
    # hâlâ koşan bir iş varsa normal dönüş süreci bitirmez, yorumlayıcı kapanışta
    # o iş (ör. yarım kalmış bir indirme) tamamlanana kadar askıda kalır —
    # kullanıcı pencereyi kapatmış olmasına rağmen süreç dakikalarca ayakta
    # kalıyordu. `closeEvent` zaten iptal edip mühlet tanıdı; buraya gelindiğinde
    # beklenecek bir şey kalmamıştır.
    from .workers import shutdown_pools
    if not shutdown_pools(0):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(kod)
    return kod


if __name__ == "__main__":
    sys.exit(run())
