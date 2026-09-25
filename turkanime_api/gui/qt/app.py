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
from typing import Dict, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from ...common import kutuphane, mpv_oynatici
from ...common.episode_parser import extract_episode_info
from ...common.hatalar import insanlastir
from ...common.oynatma import yedekli_oynat
from . import prefs
from .anilist import AniListService
from .discord import DiscordService
from .pages.discover import DiscoverPage
from .fansub import FansubSecici
from .pages.downloads import DURUM_IPTAL, DownloadManager, DownloadsPage
from .pages.library import LibraryPage
from .pages.settings import SettingsPage
from .pages.watchlist import WatchlistPage
from .progress_dialog import ProgressDialog, anime_adi
from .requirements import RequirementsDialog, RequirementsService
from .theme import ACCENT, apply_theme
from .updates import UpdateDialog, UpdateService
from .workers import UiBridge, run_bg
from ..web.gorunum import WebGorunum
from ..web.kopru import Kopru
from ..web.uclar_arama import AramaUclari
from ..web.uclar_detay import DetayUclari
from ..web.uclar_genel import GenelUclar
from ..web.uclar_kesif import KesifUclari

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

# Web arayüzüne taşınmış sayfalar (bkz. `gui/web`). Hepsi TEK bir
# `WebGorunum`'da rota olarak açılıyor; listede olmayanlar hâlâ Qt sayfası.
# Geçiş sayfa sayfa: bir sayfa taşındığında buraya eklenir, eski Qt sınıfı
# geçiş bitince silinir.
WEB_SAYFALARI = ("home", "trending", "season", "search")
# Menüde olmayan web sayfaları (kartlardan açılıyor).
WEB_ALT_SAYFALAR = ("detail",)


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

    # Chromium'un GPU süreci kapalı: GPU'suz ortamda sayfa kapanırken çöküyordu
    # (ölçüm ve gerekçe: common/chromium.py). CF çözücü alt-süreci de aynısını
    # kullanıyor.
    from ...common.chromium import bayraklari_hazirla
    bayraklari_hazirla()

    # Web arayüzünün `ta://` şeması: Chromium şemaları ilk açılışta okuyor,
    # sonradan kaydedilen şema tanınmıyor.
    from ..web.sema import semayi_kaydet
    semayi_kaydet()

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
        # "Fansub'u kendim seçeyim": seri başına tek soru (bkz. `fansub`).
        self.fansub = FansubSecici(self)
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
        # Önceki oturumun bitmemiş indirmeleri (kapanışta ya da çökmede
        # kalanlar) "duraklatıldı" olarak geri gelir; ağa çıkılmaz, iş
        # başlatılmaz. Sayfalar kurulduktan SONRA: satırlar `added` ile doğuyor.
        geri = self.downloads.geri_yukle()
        if geri:
            self.statusBar().showMessage(
                f"Önceki oturumdan {geri} indirme duraklatılmış olarak geri "
                "yüklendi — İndirilenler'den “Tümünü Sürdür”.", 0)
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
        self._build_web()
        for key, label in NAV_ITEMS:
            page = self._make_page(key, label)
            self.pages[key] = page
            if self.stack.indexOf(page) < 0:      # web görünümü tek sefer
                self.stack.addWidget(page)

        # Detay (künye + kaynak akordiyonlarında bölümler) menüde yer almaz;
        # keşif/arama/kitaplık kartından açılır.
        for key in WEB_ALT_SAYFALAR:
            self.pages[key] = self.web

        body.addWidget(self.stack, 1)

        outer.addLayout(body, 1)

    def _build_web(self) -> None:
        """Web arayüzü: köprü + uçlar + tek görünüm (bkz. `WEB_SAYFALARI`)."""
        # Köprünün Qt EBEVEYNİ YOK (bilerek): arka plan uçları bitince köprüden
        # sinyal yayıyor. Ebeveyni pencere olsaydı, pencere yıkılırken süren
        # bir iş yıkılmakta olan nesneden `emit` edip süreci segfault'la
        # düşürüyordu (test paketinde 12 koşuda bir yakalandı). Ebeveynsiz
        # nesneyi Python referansı yaşatıyor; iş sürdükçe `self` referansı da
        # sürüyor, yani yayıcı işten önce ölemiyor. Alıcı (web kanalı)
        # silinirse Qt bağlantıyı güvenle koparıyor.
        self.kopru = Kopru()
        self.kopru.bagla(GenelUclar(ac=self._web_ac))
        self.kopru.bagla(KesifUclari())
        self.arama = self.kopru.bagla(AramaUclari(self.kopru))
        self.detay = self.kopru.bagla(DetayUclari(
            self.kopru, oynat=self._on_play, indir=self._on_download,
            kuyrukta=self._web_kuyrukta))
        self.web = WebGorunum(self.kopru)
        # Detay sayfasındaki "Kuyrukta" rozetleri: iş eklendi/bitti/durdu.
        self.downloads.state.connect(lambda *_a: self.kopru.yay("kuyruk_degisti"))

    def _web_ac(self, hedef: str, veri: Dict) -> None:
        """Web sayfasından gezinti isteği (GUI thread'i; `GenelUclar.ac`)."""
        if hedef == "sayfa":
            key = str(veri.get("ad") or "")
            if key in self.pages:
                self.show_page(key)
                self._sync_nav(key)
        elif hedef == "anime":
            self._on_discover_selected(veri.get("kayit"))
        elif hedef == "kitaplik":
            self._on_kitaplik_selected(veri.get("kayit"))
        elif hedef == "arama":
            sorgu = str(veri.get("sorgu") or "").strip()
            if sorgu:
                self.txtSearch.setText(sorgu)
                self._on_search()
        elif hedef == "geri":
            self._on_detail_back()
        elif hedef == "sonuc":
            kayit = veri.get("kayit") if isinstance(veri.get("kayit"), dict) else None
            self._on_anime_selected(str(veri.get("kaynak") or ""),
                                    str(veri.get("slug") or ""),
                                    str(veri.get("baslik") or ""), kayit)

    def _make_page(self, key: str, label: str) -> QWidget:
        """`NAV_ITEMS` anahtarına karşılık gelen sayfayı üret.

        Aşağıdaki dallar `NAV_ITEMS`'ın sekiz anahtarını da karşılıyor, yani
        sona düşmek mümkün değil. Yine de sessizce `None` dönüp çağıranın
        `addWidget`'ında anlamsız bir hatayla patlamak yerine burada
        bağırıyoruz: `NAV_ITEMS`'a dalı yazılmamış bir anahtar eklenirse
        hata, sebebini söyleyerek açılışta çıksın.
        """
        if key in WEB_SAYFALARI:
            return self.web
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
    def show_page(self, key: str, parametreler: Optional[Dict] = None) -> None:
        page = self.pages.get(key)
        if page is not None:
            if page is self.web:
                self.web.git(key, parametreler)
            self.stack.setCurrentWidget(page)
            self._current_page = key
            self.discord.sayfa(key)

    def _on_search(self) -> None:
        query = self.txtSearch.text().strip()
        if not query:
            return
        # Arama sayfası web'de: sorgu rotanın parametresi, sayfa aramayı
        # kendisi başlatıyor (`ara` ucu, sonuçlar olaylarla).
        self.show_page("search", {"sorgu": query})
        self._sync_nav("search")

    def _sync_nav(self, key: str) -> None:
        """Sol menüdeki seçili düğmeyi programatik geçişlerle senkron tut."""
        btn = self._nav_buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

    def _on_discover_selected(self, item) -> None:
        """Keşif/izleme listesi kartı: kaydın tamamıyla detay sayfasını aç.

        Kaynağa bağlı değil: MyAnimeList/AniList kimliğinin Türkçe
        kaynaklardaki karşılığı bilinmiyor; detay sayfası eşleşme arıyor
        (yerel arşivde kendiliğinden, diğer kaynaklarda kullanıcı isteyince).
        """
        if isinstance(item, dict) and item:
            self._open_detail(self.detay.ac_kesif(item))

    def _on_kitaplik_selected(self, kayit) -> None:
        """Kitaplık kartı: detayı kaynağa BAĞLI aç; bölümler hemen gelir.

        Keşif kartından farkı: kayıt kaynağın kendi kimliğini taşıyor, yani
        eşleştirme (ve yanlış eşleşme riski) yok.
        """
        if isinstance(kayit, dict) and kayit:
            self._open_detail(self.detay.ac_kitaplik(kayit))

    def _on_anime_selected(self, source: str, slug: str, title: str,
                           kayit: object = None) -> None:
        """Arama sonucundan anime seçildi: kaynağı bağlı detay sayfasını aç.

        ``kayit`` arama kaydının kendisi (kapak adresi dahil); detay sayfası
        kartta görünen posteri tekrar göstermek için kullanıyor.
        """
        ek = kayit if isinstance(kayit, dict) else None
        self._open_detail(self.detay.ac_sonuc(source, slug, title, ek))

    def _open_detail(self, rid: int) -> None:
        """Detay sayfasına (``rid`` oturumuyla) geç, dönüş noktasını hatırla.

        Detaya hem keşiften hem aramadan gelinebiliyor; sabit bir "Geri" hedefi
        (ör. ana sayfa) kullanıcıyı aramasından koparırdı.
        """
        # Anahtar `_current_page`'den: web sayfalarının hepsi aynı widget,
        # widget'tan anahtar çıkarmak hep ilk web sayfasını ("home") verirdi.
        if self._current_page != "detail":
            self._detail_origin = self._current_page
        self.show_page("detail", {"rid": rid})

    def _on_detail_back(self) -> None:
        self.show_page(self._detail_origin)
        self._sync_nav(self._detail_origin)

    # ── Oynatma / indirme ───────────────────────────────────────────────────
    def _status(self, msg: str, timeout: int = 6000) -> None:
        """Durum çubuğuna yaz (her thread'den güvenli)."""
        self.ui.post(lambda: self.statusBar().showMessage(msg, timeout))

    def _hata_durumu(self, msg: str) -> None:
        """Hatayı durum çubuğuna SÜRESİZ yaz; bir sonraki mesaj onu değiştirir.

        Bilgi mesajları 6 sn'de siliniyor, hatalar da öyleydi: mpv'nin
        açılmasını bekleyip başka pencereye bakan kullanıcı "neden
        oynamadı?"nın cevabını hiç görmüyordu.
        """
        self._status(msg, 0)

    def _on_play(self, entry) -> None:
        bolum = (entry or {}).get("obj")
        if bolum is None:
            return
        if self._playing:
            self._status("Zaten bir bölüm açılıyor, lütfen bekleyin.")
            return
        self._playing = True
        title = entry.get("title") or ""
        self._status(f"{entry.get('title')} — video aranıyor…")
        self.discord.izliyor(anime_adi(bolum, ""), title)

        def devam(tamam: bool, fansub: Optional[str]) -> None:
            if not tamam:
                self._playing = False
                self._status(f"{title} — oynatma iptal edildi (fansub seçilmedi).")
                return
            # playback: oynatma mpv kapanana kadar thread'i tutar ama İNDİRME
            # havuzuna girmemeli — kuyrukta 30 bölüm varsa mpv hiç açılmaz ve
            # `_playing` açık kaldığı için kullanıcı yeniden de deneyemez.
            run_bg(self._play_blocking, bolum, title, entry, fansub,
                   playback=True)

        tercih = prefs.oku()
        # İndirilmiş bölüm diskten oynuyor: fansub sormanın anlamı yok.
        if tercih.manuel_fansub and not prefs.yerel_dosya(
                bolum, tercih, str(entry.get("yerel_dosya") or "")):
            self.fansub.iste(entry, devam)
        else:
            devam(True, None)

    def _play_blocking(self, bolum, title: str, entry=None,
                       fansub: Optional[str] = None) -> None:
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
                ek = {"by_fansub": fansub} if fansub else {}
                return bolum.best_video(by_res=tercih.max_res,
                                        early_subset=tercih.aday_sayisi,
                                        callback=callback, atla=atla, **ek)

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
                self._hata_durumu(f"{title} — {sonuc.sebep}")
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
            # Kaynak hatası (`common.hatalar.KaynakHatasi`) kullanıcıya yazılmış
            # cümle, olduğu gibi; ham requests/yt-dlp metni Türkçe sebebe
            # çevrilir ("HTTPSConnectionPool(...) Max retries…" kimseye bir
            # şey anlatmıyordu). Ham metin konsolda kalır.
            kisa, ayrinti = insanlastir(exc)
            print(f"[Oynatma] {title}: {ayrinti}")
            self._hata_durumu(f"{title} — oynatılamadı: {kisa}")
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
        """Oynatma/indirme bitti: detay sayfası rozetleri ve "Devam et"i
        tazelesin (izlendi/indirildi/kaldığın yer)."""
        self.kopru.yay("gecmis_degisti")

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

        def devam(tamam: bool, fansub: Optional[str]) -> None:
            if not tamam:
                self._status(f"{baslik} — indirme iptal edildi (fansub seçilmedi).")
                return
            # Soru sürerken aynı bölüm başka yoldan kuyruğa girmiş olabilir;
            # `enqueue` o durumda mevcut işin kimliğini döndürüp yeni iş açmaz.
            self.downloads.enqueue(entry, output=output, fansub=fansub)
            self.statusBar().showMessage(
                f"{baslik} indirme sırasına alındı — ilerleme: İndirilenler.", 6000)

        # Toplu indirmede her bölüm buraya ayrı gelir; `FansubSecici` aynı
        # serinin isteklerini biriktirip TEK soru soruyor ve seçimi hepsine
        # uyguluyor.
        if prefs.oku().manuel_fansub:
            self.fansub.iste(entry, devam)
        else:
            devam(True, None)

    def _kuyrukta_mi(self, entry) -> bool:
        """Bölümün bitmemiş indirme işi var mı?"""
        return self.downloads.kuyruktaki_is(entry, self._download_dir()) is not None

    def _web_kuyrukta(self, entry) -> bool:
        """Detay sayfasının satır rozeti: kuyruk boşsa hedef yolu hiç hesaplama
        (1000 bölümlük seride satır başına ayar okuması olurdu)."""
        if not self.downloads.active_ids():
            return False
        return self._kuyrukta_mi(entry)

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
        if yuzde < 0:                   # yalnızca metin ("duraklatılıyor…")
            return
        self.discord.indiriyor(self._dl_titles.get(task_id, "Bölüm"), yuzde)

    def _on_download_finished(self, task_id: str, ok: bool, mesaj: str) -> None:
        # İptal hata değil (kullanıcı kesti); yalnızca gerçek hata kalıcı.
        if ok or self.downloads.durum(task_id) == DURUM_IPTAL:
            self._status("İndirme: " + mesaj)
        else:
            self._hata_durumu("İndirme başarısız: " + mesaj)
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

    def _kapanis_onayi(self, adet: int) -> bool:
        """Süren indirmeler varken kapanış sorusu (testler bunu sahteler).

        Eskiden kapatmak sormadan her şeyi iptal ediyordu ve kuyruk yalnızca
        bellekteydi: 40 bölümlük toplu indirme yanlış bir tıkla kayboluyordu.
        """
        cevap = QMessageBox.question(
            self, "İndirmeler sürüyor",
            f"{adet} indirme sürüyor. Duraklatılıp çıkılsın mı?\n\n"
            "Kuyruk kaydedilir; uygulamayı yeniden açınca İndirilenler'den "
            "“Devam et” ile kaldığı yerden sürdürebilirsiniz.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        return cevap == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt imzası)
        """Kapanışta arka plan işlerini durdur.

        `clear()` tek başına yetmez: yalnızca HENÜZ BAŞLAMAMIŞ görevleri atar.
        Çalışan bir indirme, biz pencereyi yok ettikten sonra sinyal yaymaya
        devam eder ve silinmiş C++ nesnesine çarpar (çökme). Bu yüzden önce
        işleri durduruyor, sonra kısa süre bitmelerini bekliyoruz.

        Süren indirme varsa ÖNCE sorulur ("Hayır": pencere açık kalır, hiçbir
        şeye dokunulmaz). İşler iptal değil DURAKLATILIR ve kuyruk diske
        yazılır; bir sonraki açılışta geri gelirler (`geri_yukle`).
        """
        # closeEvent ASLA fırlatmamalı: C++ sanal metodundan kaçan istisna
        # (ör. yıkılmakta olan yönetici) süreci segfault'la düşürüyor.
        try:
            calisan = self.downloads.active_ids()
        except Exception:
            calisan = []
        if calisan and not self._kapanis_onayi(len(calisan)):
            event.ignore()
            return
        try:
            self.discord.durdur()
        except Exception:
            pass
        try:
            # Durdurmak ŞART: yalnızca beklemek yetmez, yt-dlp indirmeyi
            # sonuna kadar sürdürür ve süreç dakikalarca kapanmaz.
            # `pause_all` iptal bayrağını kaldırır (hook bir sonraki parçada
            # görüp indirmeyi bırakır) ama işi "duraklatıldı" bitirir: `.part`
            # diskte kalır, kuyruk dosyası işi bir sonraki açılışa taşır.
            self.downloads.pause_all()
            self.downloads.kapanista_kaydet()
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
    # Pencere (ve içindeki web sayfası) QApplication'dan ÖNCE yıkılmalı: web
    # profilinin ebeveyni QApplication ve Qt, profil sayfadan önce giderse
    # "WebEnginePage still not deleted" deyip kapanışta çökebiliyor. Yerel
    # değişkenlerin yıkım sırası Python'da tanımsız; sırayı burada koyuyoruz.
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del window

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
