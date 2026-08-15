"""İzleme Listesi ("Listem") — AniList kullanıcı listesi.

Eski GUI'deki `show_watchlist` + `load_watchlist` + `create_watchlist_card`
üçlüsünün karşılığı. Kart ızgarası keşif sayfasıyla aynı bileşenleri kullanır
(`AnimeCard`, `CardGrid`); tek fark kartın altına eklenen ilerleme
çubuğu, kullanıcı skoru ve durum rozetidir.

Eski sürümde giriş yapılmamış kullanıcı yalnızca kırmızı bir durum satırı
görüp boş ekranda kalıyordu; burada nereye gideceğini söyleyen bir yönlendirme
paneli var.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from ..anilist import (
    DURUM_ETIKETI, DURUM_RENGI, DURUMLAR, AniListService,
)
from ..theme import ACCENT, TEXT_MUTED
from ..widgets import AnimeCard, StatusLabel
from ._grid import CardGrid
from ..workers import run_bg
from .discover import anime_title, cover_url

# Keşif kartından yüksek: ilerleme çubuğu + skor satırı ekleniyor.
KART_YUKSEKLIGI = 172


class WatchlistCard(AnimeCard):
    """Kapak + başlık + durum rozeti + "İzlenen: x/y" + kullanıcı skoru."""

    def __init__(self, media: Dict[str, Any], parent: Optional[QWidget] = None):
        durum = str(media.get("user_status") or "")
        super().__init__(anime_title(media),
                         DURUM_ETIKETI.get(durum, durum or "—"),
                         payload=media, image_url=cover_url(media),
                         parent=parent)
        self.setFixedHeight(KART_YUKSEKLIGI)
        self.durum = durum
        self.lblSource.setStyleSheet(
            f"color: {DURUM_RENGI.get(durum, TEXT_MUTED)};"
            " font-size: 11px; font-weight: 600;")

        self.izlenen = _sayi(media.get("user_progress"))
        self.toplam = _sayi(media.get("episodes"))
        self.skor = _sayi(media.get("user_score"))

        self.lblProgress = QLabel(
            f"İzlenen: {self.izlenen}/{self.toplam or '?'}")
        self.lblProgress.setStyleSheet(f"color: {ACCENT}; font-size: 11px;")
        self.col.addWidget(self.lblProgress)

        self.barProgress = QProgressBar()
        self.barProgress.setTextVisible(False)
        self.barProgress.setFixedHeight(6)
        if self.toplam > 0:
            self.barProgress.setRange(0, self.toplam)
            # Bölüm sayısı AniList'te yayın sürerken artabiliyor; izlenen sayı
            # toplamı geçerse çubuk taşar.
            self.barProgress.setValue(min(self.izlenen, self.toplam))
        else:
            # Toplam bilinmiyor (devam eden yayın): dolu çubuk yanıltıcı olur.
            self.barProgress.setRange(0, 1)
            self.barProgress.setValue(0)
        self.col.addWidget(self.barProgress)

        self.lblScore = QLabel(f"★ {self.skor:g}" if self.skor else "Puansız")
        self.lblScore.setStyleSheet(
            f"color: {'#ffd93d' if self.skor else TEXT_MUTED}; font-size: 11px;")
        self.col.addWidget(self.lblScore)

        self.setToolTip("\n".join([
            self.lblTitle.text(),
            self.lblProgress.text(),
            f"Durum: {self.lblSource.text()}",
        ]))


def _sayi(deger: Any) -> int:
    """AniList alanları ``None`` ya da string gelebiliyor; sayı değilse 0."""
    try:
        return int(deger or 0)
    except (TypeError, ValueError):
        return 0


class WatchlistPage(QWidget):
    """AniList izleme listesini durum filtresiyle gösteren sayfa."""

    # Keşif sayfasıyla aynı sözleşme: kartın TAM kaydı taşınır, detay sayfası
    # kapağı ve künyeyi yeniden çekmek zorunda kalmasın.
    anime_selected = Signal(object)
    settings_requested = Signal()
    thumb_ready = Signal(object, object)

    def __init__(self, servis: Optional[AniListService] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.servis = servis or AniListService(self)
        self._busy = False
        self._loaded = False
        self._sync_istendi = False     # senkronu kullanıcı mı başlattı?
        self._cards: List[WatchlistCard] = []

        self.thumb_ready.connect(self._apply_thumb)
        self.servis.list_ready.connect(self._on_list)
        self.servis.list_failed.connect(self._on_failed)
        self.servis.auth_changed.connect(self._on_auth)
        self.servis.sync_done.connect(self._on_sync)

        self._build_ui()
        self._apply_auth_state()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        lbl = QLabel("İzleme Listem")
        lbl.setObjectName("Title")
        titles.addWidget(lbl)
        self.lblSubtitle = QLabel("AniList hesabındaki listelerin")
        self.lblSubtitle.setObjectName("Muted")
        titles.addWidget(self.lblSubtitle)
        head.addLayout(titles)
        head.addStretch(1)

        self.lblStatus = StatusLabel()
        self.lblStatus.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.lblStatus)

        self.cmbStatus = QComboBox()
        for kod, etiket in DURUMLAR:
            self.cmbStatus.addItem(etiket, kod)
        self.cmbStatus.currentIndexChanged.connect(self._on_status_changed)
        head.addWidget(self.cmbStatus)

        self.btnSync = QPushButton("Senkronize Et")
        self.btnSync.setToolTip(
            "AniList'teki ilerlemeyi yerel geçmişe uygular.")
        self.btnSync.clicked.connect(self._on_sync_clicked)
        head.addWidget(self.btnSync)

        self.btnRefresh = QPushButton("Yenile")
        self.btnRefresh.clicked.connect(self.refresh)
        head.addWidget(self.btnRefresh)
        layout.addLayout(head)

        # ── Giriş yönlendirmesi ─────────────────────────────────────────────
        self.pnlLogin = QFrame()
        self.pnlLogin.setObjectName("Panel")
        pl = QVBoxLayout(self.pnlLogin)
        pl.setContentsMargins(20, 18, 20, 18)
        pl.setSpacing(8)
        baslik = QLabel("AniList girişi gerekli")
        baslik.setObjectName("Subtitle")
        pl.addWidget(baslik)
        self.lblLoginHint = QLabel(
            "İzleme listeni görebilmek için Ayarlar'dan AniList'e giriş yapın.")
        self.lblLoginHint.setObjectName("Muted")
        self.lblLoginHint.setWordWrap(True)
        pl.addWidget(self.lblLoginHint)
        row = QHBoxLayout()
        self.btnGoSettings = QPushButton("Ayarlar'a Git")
        self.btnGoSettings.setObjectName("Primary")
        self.btnGoSettings.clicked.connect(lambda: self.settings_requested.emit())
        row.addWidget(self.btnGoSettings)
        row.addStretch(1)
        pl.addLayout(row)
        layout.addWidget(self.pnlLogin)

        self.results = CardGrid()
        layout.addWidget(self.results, 1)

    # ── Durum ───────────────────────────────────────────────────────────────
    def durum(self) -> str:
        """Seçili AniList `MediaListStatus` kodu."""
        veri = self.cmbStatus.currentData()
        return str(veri or "CURRENT")

    def cards(self) -> List[WatchlistCard]:
        """Ekrandaki kartlar (test ve köprüleme için)."""
        return list(self._cards)

    def _apply_auth_state(self) -> bool:
        """Giriş durumuna göre paneli/ızgarayı göster; giriş var mı döndür."""
        girisli = self.servis.giris_var_mi()
        self.pnlLogin.setVisible(not girisli)
        self.results.setVisible(girisli)
        for btn in (self.btnRefresh, self.btnSync, self.cmbStatus):
            btn.setEnabled(girisli)
        if not girisli:
            self.lblStatus.info("Giriş yapılmamış")
        return girisli

    # ── Yükleme ─────────────────────────────────────────────────────────────
    def showEvent(self, event):  # noqa: N802 (Qt imzası)
        """İlk gösterimde listeyi çek (keşif sayfalarıyla aynı gerekçe)."""
        super().showEvent(event)
        if not self._loaded and self._apply_auth_state():
            self.refresh()

    def refresh(self) -> None:
        """Seçili durumun listesini (yeniden) getir."""
        if not self._apply_auth_state():
            return
        if self._busy:
            return
        self._busy = True
        self._loaded = True
        self.btnRefresh.setEnabled(False)
        self.results.clear()
        self._cards = []
        self.lblStatus.info("Yükleniyor…")
        self.servis.liste_getir(self.durum())

    def _on_status_changed(self, _index: int) -> None:
        """Filtre değişti: liste kesin yeniden çekilmeli."""
        self._busy = False          # önceki isteğin sonucu artık geçersiz
        self.refresh()

    def _on_sync_clicked(self) -> None:
        if self.servis.yereli_senkronla():
            self._sync_istendi = True
            self.lblStatus.info("Senkronize ediliyor…")

    # ── Sonuç işleme (GUI thread'i) ─────────────────────────────────────────
    def _on_list(self, durum: str, girisler: Any) -> None:
        if durum != self.durum():
            return                  # kullanıcı bu arada filtreyi değiştirdi
        self._busy = False
        self.btnRefresh.setEnabled(True)

        kayitlar = [g for g in (girisler or []) if isinstance(g, dict)]
        if not kayitlar:
            self.lblStatus.info("Bu listede anime yok.")
            return

        cards = [self._make_card(kayit) for kayit in kayitlar]
        self._cards = cards
        self.results.set_items(list(cards))
        self.lblStatus.ok(f"{len(cards)} anime")

        for card in cards:
            if card.image_url:
                run_bg(self._fetch_thumb, card, card.image_url)

    def _make_card(self, media: Dict[str, Any]) -> WatchlistCard:
        card = WatchlistCard(media)
        card.clicked.connect(self._on_card_clicked)
        return card

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.btnRefresh.setEnabled(True)
        if not self._apply_auth_state():
            return
        self.lblStatus.error(f"Liste alınamadı: {message}")

    def _on_auth(self, user: Any) -> None:
        """Giriş/çıkış oldu: paneli güncelle, girişte listeyi çek.

        `user is None` ama jeton duruyorsa liste SİLİNMEZ: bu "çıkış yapıldı"
        değil "kullanıcı bilgisi alınamadı" demek (ağ dalgalanması, açılışta
        henüz tamamlanmamış tazeleme). Kartları o yüzden temizlemek, ekrandaki
        listeyi sebepsiz boşaltırdı.
        """
        girisli = self._apply_auth_state()
        if girisli:
            if isinstance(user, dict):
                self._busy = False
                self.refresh()
            return
        self.results.clear()
        self._cards = []
        self._loaded = False

    def _on_sync(self, sayi: int) -> None:
        """Senkron bitti.

        Giriş sonrası senkron KENDİLİĞİNDEN çalışıyor; sonucu her seferinde
        yazmak, durum satırındaki kart sayısını hiçbir şey değişmemişken bile
        siliyordu. Sessiz kalma koşulu: kullanıcı istemedi ve değişen yok.
        """
        if not (self._sync_istendi or sayi):
            return
        self._sync_istendi = False
        self.lblStatus.ok(f"{sayi} serinin ilerlemesi yerele işlendi."
                          if sayi else "Yerel ilerleme zaten güncel.")

    def _on_card_clicked(self, payload: Any) -> None:
        if isinstance(payload, dict) and payload:
            self.anime_selected.emit(payload)

    # ── Kapak görselleri ────────────────────────────────────────────────────
    def _fetch_thumb(self, card: WatchlistCard, url: str) -> None:
        """Arka plan: görseli indir, baytları sinyalle taşı (widget'a dokunma)."""
        try:
            import requests
            data = requests.get(url, timeout=10).content
        except Exception:
            return
        if data:
            self.thumb_ready.emit(card, data)

    def _apply_thumb(self, card: Any, data: bytes) -> None:
        try:
            card.set_thumbnail(data)
        except RuntimeError:
            pass                    # kart bu arada silinmiş (yenileme)


__all__ = ["WatchlistPage", "WatchlistCard", "KART_YUKSEKLIGI"]
