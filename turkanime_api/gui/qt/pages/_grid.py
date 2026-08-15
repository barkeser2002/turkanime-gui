"""Kart ızgarası — Keşif / Arama sayfalarının kartları yerleştirdiği düzen.

Bu modül **kartın içini** değil, kartların sayfaya nasıl DİZİLDİĞİNİ tanımlar:
sütun sayısı, sütun genişlikleri, aradaki boşluk ve dikey hizalama. Kartın
kendi görünümü (`AnimeCard`) `..widgets` içinde kalır; buradan yalnızca
`CARD_MIN_WIDTH` okunur, böylece kart tasarımı değiştiğinde (ör. poster
oranına geçildiğinde) ızgara kendiliğinden ona uyar.

`widgets.ScrollableGrid` neden yeterli değildi
----------------------------------------------
Eski ızgara sütun sayısını **kendi genişliğinden** hesaplıyordu::

    cols = (self.width() + GAP) // (MIN_W + GAP)

Ama bu widget bir `QScrollArea` içinde ve `setWidgetResizable(True)` altında
duruyor: kaydırma alanı, içeriğini asla kendi asgari genişliğinin altına
sıkıştırmaz. Izgara N sütuna çıktığı anda asgari genişliği
``N*MIN_W + (N-1)*GAP`` olur ve `self.width()` bir daha bu değerin altına
inemez. Yani ölçü, ölçtüğü şeyin kendisine geri besleniyordu:

* pencere genişlerken sütun sayısı artıyor,
* pencere daralırken **artık azalmıyordu** (mandal etkisi),
* içerik görünen alandan taşıyor, yatay kaydırma da kapalı olduğu için
  sağdaki sütun ekranın kenarında kırpılıyordu — kullanıcının "son sütun dar"
  diye gördüğü şey buydu. (Ölçüm: 1600px'te 7 sütuna çıkan ızgara, pencere
  900px'e indirildiğinde 1542px genişliğinde kalıyor; 5. sütundan yalnızca
  12 piksel görünüyordu.)

Doğru ölçü **viewport genişliğidir**: dikey kaydırma çubuğu çıktığında da
küçülür ve ızgaranın kendi asgarisinden etkilenmez. Sütun sayısı buradan
hesaplandığı sürece ``cols*MIN_W + (cols-1)*GAP <= viewport`` her zaman
sağlanır, yani içerik hiçbir genişlikte taşmaz.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QGridLayout, QScrollArea, QVBoxLayout, QWidget

from ..widgets import CARD_MIN_WIDTH

#: Sütunlar ve satırlar arasındaki sabit boşluk. Sütun genişliği pencereyle
#: birlikte değişir, bu boşluk değişmez.
GRID_GAP = 12


class CardGridBody(QWidget):
    """Kartları eşit genişlikte sütunlara dizen ızgara gövdesi.

    Yerleşim üç kurala indirgenir:

    1. **Sütunların hepsi TAM eşit genişliktedir** (bkz. `_apply_column_widths`).
    2. **Boşluk sabittir.** Fazla genişlik boşluğa değil sütunlara gider;
       sütunlara bölünemeyen birkaç piksel sağdaki yutucu sütuna kalır.
    3. **Kartlar üste hizalıdır.** Eksik satırlar ızgarayı esnetmez.

    Son satır eksik kalsa bile sütun sayısı değişmez: 12 kart 5 sütuna
    dizildiğinde son satırdaki 2 kart, üst satırdakilerle aynı genişliktedir.
    """

    def __init__(self, min_item_width: int = CARD_MIN_WIDTH,
                 gap: int = GRID_GAP, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._min_item_width = max(1, int(min_item_width))
        self._gap = max(0, int(gap))
        self._items: List[QWidget] = []
        self._columns = 0
        self._available = 0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(self._gap)
        # Kartlar dikeyde yayılmasın: eksik satırlar ızgarayı esnetmek yerine
        # altta boşluk bırakır (bkz. `CardGrid` içindeki alt stretch).
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)

    # ── Genel API ───────────────────────────────────────────────────────────
    def set_items(self, items: List[QWidget]) -> None:
        """Izgarayı verilen kartlarla doldur (öncekiler silinir)."""
        self.clear()
        self._items = list(items)
        for item in self._items:
            item.setParent(self)
        self._place(force=True)

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

    def columns(self) -> int:
        """Şu an kullanılan sütun sayısı (kart yokken 0)."""
        return self._columns

    def rows(self) -> int:
        if not self._items or self._columns <= 0:
            return 0
        return -(-len(self._items) // self._columns)   # yukarı yuvarlama

    def columns_for(self, available: int) -> int:
        """Verilen genişliğe kaç sütun sığar.

        ``cols*MIN_W + (cols-1)*GAP <= available`` eşitsizliğinin en büyük
        çözümü; `available` tek kart bile almıyorsa 1 (kırpılmış tek sütun,
        boş ekrandan iyidir).
        """
        usable = max(int(available), self._min_item_width)
        return max(1, int((usable + self._gap) // (self._min_item_width + self._gap)))

    def relayout(self, available: int) -> None:
        """Kullanılabilir genişlik değişti: gerekiyorsa yeniden diz."""
        self._available = max(0, int(available))
        self._place()

    def column_widths(self) -> List[int]:
        """Yerleşmiş sütunların piksel genişlikleri (ölçüm ve test için).

        Widget geometrisinden okunur, layout'un niyetinden değil: testin
        doğruladığı şey kullanıcının gördüğü genişlik olmalı.
        """
        widths: List[int] = []
        for column in range(self._columns):
            item = self._grid.itemAtPosition(0, column)
            widget = item.widget() if item is not None else None
            if widget is None:
                break
            widths.append(widget.width())
        return widths

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def column_width_for(self, available: int, cols: int) -> int:
        """Bir sütuna düşen TAM genişlik (kalan piksel dahil edilmez).

        `available` sütun sayısına tam bölünmediğinde artan 1-2 piksel
        sütunlara dağıtılmaz, sağdaki yutucu sütuna bırakılır — bkz.
        `_apply_column_widths`.
        """
        if cols <= 0:
            return self._min_item_width
        net = int(available) - (cols - 1) * self._gap
        return max(self._min_item_width, net // cols)

    def _apply_column_widths(self, cols: int) -> None:
        """Sütunları tam olarak eşit genişliğe sabitle.

        Sütunlara esneme payı verilseydi Qt artan pikselleri sütunlara TEK TEK
        dağıtırdı ve aralarında 1px fark kalırdı. Kompakt kartta bu görünmez;
        poster kartında görünür, çünkü kart yüksekliği genişliğinden türetiliyor
        (2:3) — 1px genişlik farkı 1-2px yükseklik farkına, o da aynı satırdaki
        kartların dikeyde kaymasına dönüşür. Ölçüldü: 1200px'te kart
        genişlikleri 227/228, kart y'leri 0/1 idi.

        Bunun yerine gerçek sütunlar sabit genişlikte (stretch 0) tutulur ve
        SAĞA, esneme payı olan boş bir "yutucu" sütun eklenir; bölünmeden artan
        birkaç piksel oraya gider.
        """
        genislik = self.column_width_for(self._available, cols)
        # Yutucu sütun. `setColumnStretch` layout'un sütun sayısını da büyütür,
        # bu yüzden önce o kuruluyor. Asgari genişliğinin SIFIRLANMASI şart:
        # sütun sayısı düştüğünde bu indeks daha önce gerçek bir sütundu ve
        # eski asgari genişliğini koruyordu — ızgara o kadar geniş kalıp
        # görünen alandan taşıyordu (ölçüldü: 320px pencerede gövde 531px).
        self._grid.setColumnStretch(cols, 1)
        self._grid.setColumnMinimumWidth(cols, 0)
        for column in range(cols):
            self._grid.setColumnStretch(column, 0)
            self._grid.setColumnMinimumWidth(column, genislik)
        # `QGridLayout.columnCount()` küçülmez: sütun sayısı 7'den 4'e
        # düştüğünde 5.-7. sütunlar layout'ta kalır. Esneme payları
        # sıfırlanmazsa bu hayalet sütunlar fazla genişliği paylaşmaya devam
        # eder ve gerçek kartlar olduğundan dar görünür.
        for column in range(cols + 1, self._grid.columnCount()):
            self._grid.setColumnStretch(column, 0)
            self._grid.setColumnMinimumWidth(column, 0)

    def _place(self, force: bool = False) -> None:
        cols = self.columns_for(self._available)
        if not self._items:
            self._columns = 0
            return

        if cols != self._columns or force:
            self._columns = cols
            while self._grid.count():
                self._grid.takeAt(0)
            for index, item in enumerate(self._items):
                self._grid.addWidget(item, index // cols, index % cols)

        # Sütun sayısı aynı kalsa da genişlik her piksellik değişimde yeniden
        # hesaplanmalı: sabit genişlikli sütunların pencereyi izlemesinin tek
        # yolu bu.
        self._apply_column_widths(cols)


class CardGrid(QScrollArea):
    """`CardGridBody`'yi dikey kaydırma içinde sunan sarmalayıcı.

    Sütun sayısını **viewport** genişliğinden hesaplar (modül başlığındaki
    mandal sorunu), kartları üste hizalar ve artan yeri altta toplar.
    """

    def __init__(self, min_item_width: int = CARD_MIN_WIDTH,
                 gap: int = GRID_GAP, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        # Yatay kaydırma yok: sütun sayısı zaten viewport'a sığacak şekilde
        # seçiliyor, dolayısıyla taşma olmuyor.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.grid = CardGridBody(min_item_width, gap)
        outer.addWidget(self.grid, 0)
        # Az kart varken kartlar sayfaya yayılmasın: boş yer altta toplanır.
        outer.addStretch(1)

        self.setWidget(container)

        # Dikey kaydırma çubuğunun görünmesi pencereyi değil yalnızca
        # viewport'u daraltır; `resizeEvent` o durumda gelmez, bu yüzden
        # viewport'un kendi resize olayını dinliyoruz.
        self.viewport().installEventFilter(self)

    # ── Genel API (eski `ScrollableGrid` ile uyumlu) ────────────────────────
    def available_width(self) -> int:
        """Kartların paylaşabileceği genişlik."""
        return self.viewport().width()

    def set_items(self, items: List[QWidget]) -> None:
        self.grid.set_items(items)
        self._sync()

    def clear(self) -> None:
        self.grid.clear()

    def count(self) -> int:
        return self.grid.count()

    def columns(self) -> int:
        return self.grid.columns()

    # ── Olaylar ─────────────────────────────────────────────────────────────
    def _sync(self) -> None:
        self.grid.relayout(self.available_width())

    def resizeEvent(self, event):  # noqa: N802 (Qt imzası)
        super().resizeEvent(event)
        self._sync()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt imzası)
        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            self._sync()
        return super().eventFilter(obj, event)


__all__ = ["CardGrid", "CardGridBody", "GRID_GAP"]
