"""Yeniden kullanılabilir Qt widget'ları (eski CTk kart/ızgara mantığının karşılığı)."""
from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QStyle, QStyleOption, QVBoxLayout, QWidget,
)

from .theme import ACCENT, BG_ELEV_2, TEXT_MUTED

CARD_MIN_WIDTH = 210
CARD_HEIGHT = 116          # yalnızca kapaksız (kompakt) kartlar için
GRID_SPACING = 12

# Poster geometrisi: kartın iç genişliğini tamamen kaplar, en-boy 2:3 korunur.
POSTER_RATIO = 3 / 2       # yükseklik / genişlik
CARD_PAD = 6               # kart kenar boşluğu (poster bu kadar içeride kalır)
BADGE_INSET = 6            # rozetin poster köşesine uzaklığı
TITLE_LINES = 2            # başlık en çok kaç satır


class ElidedLabel(QLabel):
    """En çok `max_lines` satıra sığdırılan, taşarsa `…` ile kırpılan etiket.

    `QLabel(wordWrap=True)` taşan metni KIRPMAZ, kutunun dışına taşırır ya da
    alttan keser (yarım harf satırı). Burada satırlar elle sarılır ve son satır
    `elidedText` ile kısaltılır.

    `text()` her zaman TAM başlığı döndürür — kırpma yalnızca çizimde yapılır,
    böylece başlığı okuyan çağıranlar (tooltip, testler, arama) bozulmaz.
    """

    def __init__(self, text: str = "", max_lines: int = TITLE_LINES,
                 parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._max_lines = max(1, int(max_lines))
        # Yatayda `Ignored`: uzun başlık kartın minimum genişliğini şişirmesin
        # (aksi hâlde tek uzun isim tüm ızgara sütununu genişletirdi).
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    # ── Ölçüler ─────────────────────────────────────────────────────────────
    def _line_height(self) -> int:
        return self.fontMetrics().lineSpacing()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt imzası)
        return QSize(0, self._line_height() * self._max_lines)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt imzası)
        return self.sizeHint()

    # ── Sarma / kırpma ──────────────────────────────────────────────────────
    def visible_lines(self, width: Optional[int] = None) -> List[str]:
        """Gerçekten çizilen satırlar (kırpılmış hâlleriyle)."""
        text = " ".join((self.text() or "").split())
        if not text:
            return []
        avail = self.width() if width is None else int(width)
        if avail <= 0:
            # Henüz yerleşmemiş widget: genişlik bilinmeden kırpmak, metni
            # sebepsiz "…"e indirirdi.
            return [text]

        fm = self.fontMetrics()
        lines: List[str] = []
        current = ""
        for word in text.split(" "):
            trial = f"{current} {word}" if current else word
            if not current or fm.horizontalAdvance(trial) <= avail:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)

        if len(lines) > self._max_lines:
            kalan = " ".join(lines[self._max_lines - 1:])
            lines = lines[:self._max_lines - 1] + [kalan]

        # Tek kelime satırdan uzun olabilir (uzun Japonca isim); o da kırpılır.
        return [
            line if fm.horizontalAdvance(line) <= avail
            else fm.elidedText(line, Qt.TextElideMode.ElideRight, avail)
            for line in lines
        ]

    def is_elided(self) -> bool:
        """Başlık sığmadığı için kısaltıldı mı?"""
        tam = " ".join((self.text() or "").split())
        return " ".join(self.visible_lines()) != tam

    def paintEvent(self, event):  # noqa: N802 (Qt imzası)
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        # Stil sayfasından gelen arka planı da çiz (özel paintEvent onu atlar).
        self.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        # `opt.palette` PySide6'da bir metot gibi de görünebiliyor; pylint onu
        # `palette` sınıfı sanıp `.color` yok diyor (Linux CI'da E1101).
        # Widget'ın kendi paletini okumak hem doğru hem taşınabilir — stil
        # sayfasından gelen renk zaten buraya yansıyor.
        painter.setPen(self.palette().color(self.foregroundRole()))
        fm = self.fontMetrics()
        y = fm.ascent()
        for line in self.visible_lines():
            painter.drawText(0, y, line)
            y += fm.lineSpacing()


class AnimeCard(QFrame):
    """Tek bir anime sonucunu temsil eden tıklanabilir poster kartı.

    Yerleşim: kartın TAM genişliğini kaplayan 2:3 poster, altında en çok iki
    satırlık başlık, posterin sağ alt köşesinde rozet (puan / durum / kaynak).
    Kapak sağlamayan kaynaklarda (`image_url=None`) poster alanı hiç
    gösterilmez; kart eski kompakt hâlinde kalır.
    """

    clicked = Signal(object)

    def __init__(self, title: str, source: str, payload: Any = None,
                 image_url: Optional[str] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.payload = payload
        self.image_url = image_url
        self._poster_mode = bool(image_url)
        self._poster_size = (0, 0)
        self._src_pixmap: Optional[QPixmap] = None   # ham kapak (yeniden ölçek için)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(CARD_PAD, CARD_PAD, CARD_PAD, CARD_PAD)
        outer.setSpacing(CARD_PAD)

        # ── Poster ──────────────────────────────────────────────────────────
        self.lblThumb = QLabel()
        self.lblThumb.setObjectName("CardPoster")
        self.lblThumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblThumb.setWordWrap(True)
        # Yatayda `Ignored`: poster kartın verdiği genişliği alır, kendi pixmap
        # boyutunu dayatmaz. Yükseklik `_apply_geometry`'de 2:3'ten hesaplanır.
        self.lblThumb.setSizePolicy(QSizePolicy.Policy.Ignored,
                                    QSizePolicy.Policy.Fixed)
        # Seçici (`#CardPoster`) şart: seçicisiz stil sayfası ALT widget'lara da
        # sızar ve posterin çocuğu olan rozeti de boyardı.
        self.lblThumb.setStyleSheet(
            f"#CardPoster {{ background-color: {BG_ELEV_2}; border-radius: 6px;"
            f" color: {TEXT_MUTED}; font-size: 11px; }}")
        # Kapak inene kadar boş siyah dikdörtgen yerine adın kendisi durur.
        self.lblThumb.setText(title)
        self.lblThumb.setVisible(self._poster_mode)
        outer.addWidget(self.lblThumb, 0)

        # ── Rozet (poster üstünde köşe rozeti) ──────────────────────────────
        # Posterin ÇOCUĞU: böylece görselin üzerinde durur, yer kaplamaz.
        self.badge = QFrame(self.lblThumb if self._poster_mode else None)
        self.badge.setObjectName("CardBadge" if self._poster_mode else "CardBadgeFlat")
        badge_row = QHBoxLayout(self.badge)
        badge_row.setContentsMargins(6, 2, 6, 2)
        badge_row.setSpacing(0)
        self.lblSource = QLabel(source)
        self.lblSource.setStyleSheet(
            f"color: {ACCENT}; font-size: 11px; font-weight: 600;")
        badge_row.addWidget(self.lblSource)

        # ── Başlık bloğu ────────────────────────────────────────────────────
        # Sütun layout'u öznitelikte tutuluyor: alt sınıflar (ör. izleme listesi
        # kartı) başlığın altına satır ekleyebilsin. Layout'u `layout().itemAt`
        # ile kazımak, kart iç yapısı değişince sessizce bozulurdu.
        self._textPanel = QWidget()
        self.col = col = QVBoxLayout(self._textPanel)
        col.setContentsMargins(2, 0, 2, 0)
        col.setSpacing(4)

        self.lblTitle = ElidedLabel(title)
        col.addWidget(self.lblTitle, 0)
        if not self._poster_mode:
            # Poster yoksa rozet sarkacak yer bulamaz; başlığın altına iner.
            col.addWidget(self.badge, 0)
        outer.addWidget(self._textPanel, 0)

        if not self._poster_mode:
            self.setFixedHeight(CARD_HEIGHT)

        self.setToolTip(f"{title}\nKaynak: {source}")
        self._apply_geometry()

    # ── Geometri ────────────────────────────────────────────────────────────
    def _poster_width(self) -> int:
        """Posterin gerçek genişliği.

        Kart genişliğinden kenar boşluğunu çıkarmak YETMEZ: `QFrame#Card`'ın
        stil sayfasından gelen kenarlığı da içeriği daraltır, o da 2:3 oranını
        birkaç piksel kaydırırdı. Bu yüzden layout bir kez çalıştırılıp
        posterin kendi genişliği okunuyor.
        """
        lay = self.layout()
        if lay is not None:
            lay.activate()
        pw = self.lblThumb.width()
        if pw > 1:
            return pw
        # Kart henüz hiç yerleşmediyse (gizli widget resize olayı almaz) en az
        # kart minimum genişliği varsayılır; aksi hâlde 0'a ölçeklenmiş pixmap
        # çıkardı.
        return max(self.width(), CARD_MIN_WIDTH) - 2 * CARD_PAD

    def _apply_geometry(self) -> None:
        """Poster yüksekliğini ve kart yüksekliğini genişlikten türet."""
        if not self._poster_mode:
            return
        pw = self._poster_width()
        ph = int(round(pw * POSTER_RATIO))
        if (pw, ph) != self._poster_size:
            self._poster_size = (pw, ph)
            self.lblThumb.setFixedHeight(ph)
            self._rescale_poster()

        toplam = 2 * CARD_PAD + ph + CARD_PAD + self._textPanel.sizeHint().height()
        if self.minimumHeight() != toplam:
            self.setFixedHeight(toplam)
        self._place_badge(pw, ph)

    def _place_badge(self, pw: int, ph: int) -> None:
        if not self._poster_mode:
            return
        self.badge.adjustSize()
        self.badge.move(max(0, pw - self.badge.width() - BADGE_INSET),
                        max(0, ph - self.badge.height() - BADGE_INSET))
        self.badge.raise_()

    def _rescale_poster(self) -> None:
        """Ham pixmap'i güncel poster kutusuna göre yeniden ölçekle.

        `KeepAspectRatioByExpanding` kutuyu doldurur ama taşan kenarı bırakır;
        taşan kısım ortadan kırpılmazsa poster kartın dışına sarkar.
        """
        if self._src_pixmap is None or self._src_pixmap.isNull():
            return
        pw, ph = self._poster_size
        if pw <= 0 or ph <= 0:
            return
        scaled = self._src_pixmap.scaled(
            pw, ph,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - pw) // 2)
        y = max(0, (scaled.height() - ph) // 2)
        self.lblThumb.setPixmap(scaled.copy(x, y, min(pw, scaled.width()),
                                            min(ph, scaled.height())))

    def _enable_poster(self) -> None:
        """Kapaksız açılan kart sonradan görsel alırsa poster kipine geç."""
        if self._poster_mode:
            return
        self._poster_mode = True
        self.col.removeWidget(self.badge)
        self.badge.setParent(self.lblThumb)
        self.badge.setObjectName("CardBadge")
        self.badge.setVisible(True)       # reparent widget'ı gizler
        self.lblThumb.setVisible(True)
        self.setMaximumHeight(16777215)   # kompakt sabit yüksekliği çöz
        self.setMinimumHeight(0)
        self._apply_geometry()

    # ── Genel API ───────────────────────────────────────────────────────────
    def set_thumbnail(self, data: bytes) -> None:
        """İndirilen görsel baytlarını karta yerleştir (GUI thread'inden çağrılmalı)."""
        if not data:
            return
        pix = QPixmap()
        if not pix.loadFromData(data):
            return
        self._src_pixmap = pix
        self._enable_poster()
        self._poster_size = (0, 0)        # ölçeklemeyi zorla
        self._apply_geometry()

    def resizeEvent(self, event):  # noqa: N802 (Qt imzası)
        super().resizeEvent(event)
        self._apply_geometry()

    def showEvent(self, event):  # noqa: N802 (Qt imzası)
        super().showEvent(event)
        self._apply_geometry()

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
        self._avail = 0            # dışarıdan bildirilen kullanılabilir genişlik

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

    def set_available_width(self, width: int) -> None:
        """Izgaranın gerçekte kullanabileceği genişliği bildir.

        ESKİ HATA: sütun sayısı `self.width()`ten hesaplanıyordu. Kartların
        `minimumWidth`i yüzünden ızgaranın kendi minimum genişliği sütun
        sayısıyla birlikte büyür; pencere daralınca widget bu minimumun altına
        İNEMEZ, dolayısıyla `self.width()` küçülmez ve sütun sayısı bir daha
        asla azalmazdı. Sonuç: yatay kaydırma kapalı olduğu için en sağdaki
        sütun kırpılıyor ("son sütun dar") ve kartlar taşıyordu.
        """
        width = int(width)
        if width <= 0 or width == self._avail:
            return
        self._avail = width
        self._relayout()

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def _calc_columns(self) -> int:
        usable = max(self._avail or self.width(), self._min_item_width)
        cols = max(1, (usable + GRID_SPACING) // (self._min_item_width + GRID_SPACING))
        return int(cols)

    def _relayout(self) -> None:
        cols = self._calc_columns()
        if cols == self._columns or not self._items:
            return
        self._columns = cols

        while self._grid.count():
            self._grid.takeAt(0)

        # ESKİ HATA: `setColumnStretch` layout'ta KALICI. Sütun sayısı 5'ten
        # 3'e düşünce eski 4. ve 5. sütunlar esnemeye devam ediyor, boş oldukları
        # hâlde yer kapıyordu; kartlar daralıyor ve son sütun dar görünüyordu.
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)

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
        self.grid.set_available_width(self.viewport().width())

    def clear(self) -> None:
        self.grid.clear()

    def resizeEvent(self, event):  # noqa: N802 (Qt imzası)
        super().resizeEvent(event)
        # Izgara kendi genişliğine bakarak daralamaz (bkz. `set_available_width`);
        # ölçüt görünüm alanının genişliğidir — dikey kaydırma çubuğu hariç.
        self.grid.set_available_width(self.viewport().width())


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


__all__ = ["AnimeCard", "ElidedLabel", "ResponsiveGrid", "ScrollableGrid",
           "StatusLabel", "CARD_MIN_WIDTH", "CARD_PAD", "POSTER_RATIO"]
