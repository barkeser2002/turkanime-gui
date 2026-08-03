"""Yeniden kullanılabilir Qt widget'ları."""
from __future__ import annotations

import pytest

from turkanime_api.gui.qt.widgets import (
    CARD_MIN_WIDTH, AnimeCard, ResponsiveGrid, ScrollableGrid, StatusLabel,
)


@pytest.mark.parametrize("width,expected_min", [(400, 1), (900, 3), (1400, 5)])
def test_grid_columns_scale_with_width(qtbot, width, expected_min):
    grid = ResponsiveGrid()
    qtbot.addWidget(grid)
    grid.resize(width, 600)
    cols = grid._calc_columns()
    assert cols >= expected_min
    assert cols * CARD_MIN_WIDTH <= width + CARD_MIN_WIDTH


def test_grid_never_returns_zero_columns(qtbot):
    """Sıfır sütun ZeroDivision/boş ızgara demek; daralt da olsa en az 1."""
    grid = ResponsiveGrid()
    qtbot.addWidget(grid)
    grid.resize(10, 100)
    assert grid._calc_columns() >= 1


def test_grid_set_items_and_clear(qtbot):
    grid = ScrollableGrid()
    qtbot.addWidget(grid)
    cards = [AnimeCard(f"Anime {i}", "AnimeDepo") for i in range(7)]
    grid.set_items(cards)
    assert grid.grid.count() == 7
    grid.clear()
    assert grid.grid.count() == 0


def test_card_click_emits_payload(qtbot):
    from PySide6.QtCore import Qt

    payload = ("AnimeDepo", "naruto", "Naruto")
    card = AnimeCard("Naruto", "AnimeDepo", payload=payload)
    qtbot.addWidget(card)
    with qtbot.waitSignal(card.clicked, timeout=2000) as blocker:
        qtbot.mouseClick(card, Qt.MouseButton.LeftButton)
    assert blocker.args[0] == payload


def test_card_hides_thumb_area_without_image(qtbot):
    """Görseli olmayan kaynaklarda kapak alanı yer kaplamamalı."""
    card = AnimeCard("Bir Anime", "AnimeDepo", image_url=None)
    qtbot.addWidget(card)
    assert not card.lblThumb.isVisibleTo(card)


@pytest.fixture
def png_bytes(qtbot):
    """Geçerli PNG üret (elle yazılmış hex kırılgan; Qt'nin kendi kodlayıcısı kesin)."""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QPixmap

    pix = QPixmap(4, 4)
    pix.fill()
    # `QBuffer(QByteArray())` YAZMA: QBuffer arabelleğe yalnızca referans tutar,
    # geçici QByteArray ise Python tarafında hemen toplanır ve `save()` serbest
    # bırakılmış belleğe yazarak süreci düşürür (access violation).
    storage = QByteArray()
    buf = QBuffer(storage)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    assert pix.save(buf, "PNG")
    return bytes(buf.data())


def test_card_set_thumbnail_renders_pixmap(qtbot, png_bytes):
    card = AnimeCard("Naruto", "AniList", image_url="http://x/y.png")
    qtbot.addWidget(card)
    assert card.lblThumb.pixmap().isNull()
    card.set_thumbnail(png_bytes)
    assert not card.lblThumb.pixmap().isNull()


def test_card_ignores_corrupt_image(qtbot):
    """Bozuk bayt kartı düşürmemeli (ağdan her şey gelebilir)."""
    card = AnimeCard("Naruto", "AniList", image_url="http://x/y.png")
    qtbot.addWidget(card)
    card.set_thumbnail(b"bu-bir-png-degil")
    assert card.lblThumb.pixmap().isNull()


def test_status_label_states(qtbot):
    lbl = StatusLabel()
    qtbot.addWidget(lbl)
    lbl.info("arıyor")
    assert lbl.text() == "arıyor"
    lbl.ok("bulundu")
    assert lbl.text() == "bulundu"
    lbl.error("hata")
    assert lbl.text() == "hata"
