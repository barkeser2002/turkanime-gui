"""Yeniden kullanılabilir Qt widget'ları (eski CTk kart/ızgara mantığının karşılığı)."""
from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .theme import ACCENT, BG_ELEV, TEXT_MUTED

CARD_MIN_WIDTH = 210
CARD_HEIGHT = 116
GRID_SPACING = 12
THUMB_W, THUMB_H = 68, 96


class AnimeCard(QFrame):
    """Tek bir anime sonucunu temsil eden tıklanabilir kart.

    Eski GUI'deki `create_anime_card` karşılığı. Küçük resim boru hattı A2
    fazında eklenecek; şu an başlık + kaynak rozeti gösterir.
    """

    clicked = Signal(object)

    def __init__(self, title: str, source: str, payload: Any = None,
                 image_url: Optional[str] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.payload = payload
        self.image_url = image_url
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(CARD_HEIGHT)
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 8, 12, 8)
        outer.setSpacing(10)

        # Kapak görseli (yalnızca sağlayan kaynaklarda; yoksa yer kaplamaz)
        self.lblThumb = QLabel()
        self.lblThumb.setFixedSize(THUMB_W, THUMB_H)
        self.lblThumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblThumb.setStyleSheet(
            f"background: {BG_ELEV}; border-radius: 4px; color: {TEXT_MUTED}; font-size: 10px;")
        self.lblThumb.setVisible(bool(image_url))
        outer.addWidget(self.lblThumb, 0)

        # Sütun layout'u öznitelikte tutuluyor: alt sınıflar (ör. izleme listesi
        # kartı) başlığın altına satır ekleyebilsin. Layout'u `layout().itemAt`
        # ile kazımak, kart iç yapısı değişince sessizce bozulurdu.
        self.col = col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self.lblTitle = QLabel(title)
        self.lblTitle.setWordWrap(True)
        self.lblTitle.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.lblTitle.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        col.addWidget(self.lblTitle, 1)

        self.lblSource = QLabel(source)
        self.lblSource.setStyleSheet(f"color: {ACCENT}; font-size: 11px; font-weight: 600;")
        col.addWidget(self.lblSource, 0)
        outer.addLayout(col, 1)

        self.setToolTip(f"{title}\nKaynak: {source}")

    def set_thumbnail(self, data: bytes) -> None:
        """İndirilen görsel baytlarını karta yerleştir (GUI thread'inden çağrılmalı)."""
        if not data:
            return
        pix = QPixmap()
        if not pix.loadFromData(data):
            return
        self.lblThumb.setPixmap(pix.scaled(
            THUMB_W, THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))
        self.lblThumb.setVisible(True)

    def mousePressEvent(self, event):  # noqa: N802 (Qt imzası)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.payload)
        super().mousePressEvent(event)


class ResponsiveGrid(QWidget):
    """Genişliğe göre sütun sayısını yeniden hesaplayan ızgara.

    Eski GUI'deki `_calculate_columns` + `_update_*_grid` davranışının Qt
    karşılığı; yeniden yerleşim yalnızca sütun sayısı değiştiğinde yapılır.
    """

    def __init__(self, min_item_width: int = CARD_MIN_WIDTH,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._min_item_width = min_item_width
        self._items: List[QWidget] = []
        self._columns = 0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(GRID_SPACING)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)

    # ── Genel API ───────────────────────────────────────────────────────────
    def set_items(self, items: List[QWidget]) -> None:
        self.clear()
        self._items = items
        for item in items:
            item.setParent(self)
        self._columns = 0          # yeniden yerleşimi zorla
        self._relayout()

    def clear(self) -> None:
        while self._grid.count():
            entry = self._grid.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._items = []
        self._columns = 0

    def count(self) -> int:
        return len(self._items)

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def _calc_columns(self) -> int:
        usable = max(self.width(), self._min_item_width)
        cols = max(1, (usable + GRID_SPACING) // (self._min_item_width + GRID_SPACING))
        return int(cols)

    def _relayout(self) -> None:
        cols = self._calc_columns()
        if cols == self._columns or not self._items:
            return
        self._columns = cols

        while self._grid.count():
            self._grid.takeAt(0)

        for index, item in enumerate(self._items):
            self._grid.addWidget(item, index // cols, index % cols)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

    def resizeEvent(self, event):  # noqa: N802 (Qt imzası)
        super().resizeEvent(event)
        self._relayout()


class ScrollableGrid(QScrollArea):
    """`ResponsiveGrid`'i dikey kaydırma içinde sunan sarmalayıcı.

    CTk'deki `CTkScrollableFrame` idyomunun Qt karşılığı.
    """

    def __init__(self, min_item_width: int = CARD_MIN_WIDTH,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.grid = ResponsiveGrid(min_item_width)
        outer.addWidget(self.grid)
        outer.addStretch(1)

        self.setWidget(container)

    def set_items(self, items: List[QWidget]) -> None:
        self.grid.set_items(items)

    def clear(self) -> None:
        self.grid.clear()


class StatusLabel(QLabel):
    """Sayfa üstü durum satırı (arıyor / sonuç yok / hata)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Muted")
        self.setWordWrap(True)

    def info(self, text: str) -> None:
        self.setStyleSheet(f"color: {TEXT_MUTED};")
        self.setText(text)

    def ok(self, text: str) -> None:
        self.setStyleSheet(f"color: {ACCENT};")
        self.setText(text)

    def error(self, text: str) -> None:
        self.setStyleSheet("color: #d63031;")
        self.setText(text)


__all__ = ["AnimeCard", "ResponsiveGrid", "ScrollableGrid", "StatusLabel", "CARD_MIN_WIDTH"]
