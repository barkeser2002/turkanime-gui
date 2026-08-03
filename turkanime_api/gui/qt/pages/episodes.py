"""Bölüm listesi sayfası — seçim, sayfalama, oynat/indir.

Eski `common/ui.py::AccordionSourceEpisodeList` davranışının Qt karşılığı:
tembel sayfalama (30'ar), toplu seçim, arama filtresi. Oynatma/indirme mevcut
`best_video()` → yt-dlp/mpv boru hattını kullanır, yani kaynak tarafında hiçbir
değişiklik gerekmez.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..sources_bridge import UnsupportedSource, fetch_episodes
from ..widgets import StatusLabel
from ..workers import WorkerSignals, run_bg

PAGE_SIZE = 30


class EpisodeRow(QFrame):
    """Tek bölüm satırı: seçim kutusu + başlık + aksiyonlar."""

    play_requested = Signal(object)
    download_requested = Signal(object)

    def __init__(self, entry: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.entry = entry
        # Filtre durumunu AÇIKÇA tut: `isVisible()` ata widget'ların görünürlüğüne
        # bağlı olduğu için seçim/filtre mantığının kaynağı olamaz.
        self.filtered_out = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        self.chk = QCheckBox()
        layout.addWidget(self.chk)

        self.lbl = QLabel(entry.get("title") or "")
        self.lbl.setWordWrap(False)
        layout.addWidget(self.lbl, 1)

        self.btnPlay = QPushButton("Oynat")
        self.btnPlay.clicked.connect(lambda: self.play_requested.emit(self.entry))
        layout.addWidget(self.btnPlay)

        self.btnDl = QPushButton("İndir")
        self.btnDl.setObjectName("Primary")
        self.btnDl.clicked.connect(lambda: self.download_requested.emit(self.entry))
        layout.addWidget(self.btnDl)

    @property
    def checked(self) -> bool:
        return self.chk.isChecked()

    def set_checked(self, value: bool) -> None:
        self.chk.setChecked(value)

    def matches(self, needle: str) -> bool:
        return needle in (self.entry.get("title") or "").lower()


class EpisodePage(QWidget):
    """Seçilen animenin bölümlerini listeler."""

    play_requested = Signal(object)
    download_requested = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._all: List[Dict[str, Any]] = []
        self._rows: List[EpisodeRow] = []
        self._shown = 0
        self._busy = False
        self._context = ("", "", "")

        self.signals = WorkerSignals()
        self.signals.connect_found(self._on_episodes)
        self.signals.connect_error(self._on_error)

        self._build_ui()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        head = QHBoxLayout()
        self.lblTitle = QLabel("Bölümler")
        self.lblTitle.setObjectName("Title")
        head.addWidget(self.lblTitle)
        head.addStretch(1)
        self.lblStatus = StatusLabel()
        head.addWidget(self.lblStatus)
        layout.addLayout(head)

        tools = QHBoxLayout()
        self.txtFilter = QLineEdit()
        self.txtFilter.setPlaceholderText("Bölüm ara…")
        self.txtFilter.setClearButtonEnabled(True)
        self.txtFilter.textChanged.connect(self._apply_filter)
        tools.addWidget(self.txtFilter, 1)

        self.btnAll = QPushButton("Tümünü Seç")
        self.btnAll.setCheckable(True)
        self.btnAll.toggled.connect(self._toggle_all)
        tools.addWidget(self.btnAll)

        self.btnDlSel = QPushButton("Seçilenleri İndir")
        self.btnDlSel.setObjectName("Primary")
        self.btnDlSel.clicked.connect(self._download_selected)
        tools.addWidget(self.btnDlSel)
        layout.addLayout(tools)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self._list = QVBoxLayout(holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(6)
        self._list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(holder)
        layout.addWidget(self.scroll, 1)

        self.btnMore = QPushButton("Daha fazla yükle")
        self.btnMore.clicked.connect(self._load_more)
        self.btnMore.hide()
        layout.addWidget(self.btnMore)

        self.lblStatus.info("Arama sonucundan bir anime seçin.")

    # ── Yükleme ─────────────────────────────────────────────────────────────
    def load(self, source: str, slug: str, title: str) -> None:
        if self._busy:
            self.lblStatus.info("Önceki istek sürüyor, lütfen bekleyin…")
            return
        self._busy = True
        # Yeni anime: seçim durumunu ve "Tümünü Seç" etiketini sıfırla, aksi
        # hâlde buton "Seçimi Kaldır" derken hiçbir satır seçili olmaz.
        self.btnAll.setChecked(False)
        self.txtFilter.clear()
        self._context = (source, slug, title)
        self.lblTitle.setText(f"{title} — {source}")
        self._clear_rows()
        self.lblStatus.info("Bölümler getiriliyor…")
        self.btnMore.hide()
        run_bg(self._do_load, source, slug, title, signals=self.signals)

    def _do_load(self, source: str, slug: str, title: str) -> None:
        try:
            episodes = fetch_episodes(source, slug, title)
        except UnsupportedSource as exc:
            self.signals.emit_error(str(exc))
            return
        self.signals.emit_found(episodes)

    def _on_episodes(self, episodes: List[Dict[str, Any]]) -> None:
        self._busy = False
        self._all = episodes or []
        if not self._all:
            self.lblStatus.error("Bu kaynakta bölüm bulunamadı.")
            return
        self._shown = 0
        self._load_more()
        self.lblStatus.ok(f"{len(self._all)} bölüm")

    def _on_error(self, message: str) -> None:
        self._busy = False
        self.lblStatus.error(message)

    # ── Liste yönetimi ──────────────────────────────────────────────────────
    def _clear_rows(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows = []
        self._shown = 0

    def _load_more(self) -> None:
        chunk = self._all[self._shown:self._shown + PAGE_SIZE]
        for entry in chunk:
            row = EpisodeRow(entry)
            row.play_requested.connect(self.play_requested.emit)
            row.download_requested.connect(self.download_requested.emit)
            self._list.addWidget(row)
            self._rows.append(row)
        self._shown += len(chunk)
        self.btnMore.setVisible(self._shown < len(self._all))
        self.btnMore.setText(
            f"Daha fazla yükle ({len(self._all) - self._shown} kaldı)"
        )
        self._apply_filter(self.txtFilter.text())

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row in self._rows:
            row.filtered_out = bool(needle) and not row.matches(needle)
            row.setVisible(not row.filtered_out)

    def _toggle_all(self, checked: bool) -> None:
        """Yalnızca filtreden geçen satırları seç/bırak."""
        self.btnAll.setText("Seçimi Kaldır" if checked else "Tümünü Seç")
        for row in self._rows:
            if not row.filtered_out:
                row.set_checked(checked)

    def visible_rows(self) -> List[EpisodeRow]:
        """Filtreden geçen satırlar (Qt görünürlüğünden bağımsız)."""
        return [r for r in self._rows if not r.filtered_out]

    def selected_entries(self) -> List[Dict[str, Any]]:
        """Seçili VE filtreden geçen bölümler.

        Filtreyi de dikkate almak şart: `_toggle_all` yalnızca görünen satırları
        işaretliyor; burada filtreyi yok sayarsak kullanıcı 200 bölümü seçip
        sonra "12" diye filtreleyince yine 200 bölüm indirilir.
        """
        return [r.entry for r in self._rows if r.checked and not r.filtered_out]

    def _download_selected(self) -> None:
        picked = self.selected_entries()
        if not picked:
            self.lblStatus.error("Önce en az bir bölüm seçin.")
            return
        self.lblStatus.info(f"{len(picked)} bölüm indirme sırasına alındı.")
        for entry in picked:
            self.download_requested.emit(entry)


__all__ = ["EpisodePage", "EpisodeRow"]
