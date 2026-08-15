"""Yeniden kullanılabilir Qt widget'ları."""
from __future__ import annotations

import pytest

from turkanime_api.gui.qt.widgets import (
    CARD_PAD, POSTER_RATIO, AnimeCard, ElidedLabel, StatusLabel,
)








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


# ── Poster yerleşimi ────────────────────────────────────────────────────────
def _goster(qtbot, card, width=320):
    """Kartı gerçekten yerleştir: gizli widget resize olayı ALMAZ."""
    qtbot.addWidget(card)
    card.resize(width, 600)
    card.show()
    qtbot.waitExposed(card)
    return card


def test_poster_kartin_tam_genisligini_kapliyor(qtbot):
    """ESKİ HATA: poster 68px sabit genişlikte sola sıkışıyor, kartın kalanı
    boş siyah alan olarak kalıyordu."""
    card = _goster(qtbot, AnimeCard("Naruto", "★ 8.5", image_url="http://x/y.png"), 320)

    assert card.lblThumb.width() >= card.width() * 0.9
    assert card.lblThumb.maximumWidth() > 200, "postere sabit küçük genişlik verilmiş"


def test_poster_2_3_en_boy_oraninda(qtbot):
    card = _goster(qtbot, AnimeCard("Naruto", "★ 8.5", image_url="http://x/y.png"), 300)

    # Poster kartın içini kaplar (kenar boşluğu + olası kenarlık kadar içeride).
    assert card.lblThumb.width() >= 300 - 2 * CARD_PAD - 4
    beklenen = round(card.lblThumb.width() * POSTER_RATIO)
    assert abs(card.lblThumb.height() - beklenen) <= 1
    # Kart = poster + başlık alanı; poster kartı taşırmamalı.
    assert card.height() > card.lblThumb.height()
    assert card.height() - card.lblThumb.height() < 120


def test_poster_pencere_buyuyunce_yeniden_olcekleniyor(qtbot, png_bytes):
    """Pencere genişleyince poster küçük/bulanık kalmamalı."""
    card = _goster(qtbot, AnimeCard("Naruto", "★ 8.5", image_url="http://x/y.png"), 240)
    card.set_thumbnail(png_bytes)
    kucuk = card.lblThumb.pixmap().width()

    card.resize(480, 900)
    qtbot.waitUntil(lambda: card.lblThumb.pixmap().width() > kucuk, timeout=2000)

    assert card.lblThumb.pixmap().width() >= card.width() * 0.9
    assert card.lblThumb.pixmap().height() >= card.lblThumb.height() - 1


def test_rozet_posterin_sag_alt_kosesinde(qtbot):
    card = _goster(qtbot, AnimeCard("Naruto", "★ 8.5", image_url="http://x/y.png"), 320)

    assert card.badge.parent() is card.lblThumb, "rozet posterin üzerinde değil"
    # Sağ-alt köşe: rozetin merkezi posterin sağ-alt çeyreğinde olmalı.
    merkez = card.badge.geometry().center()
    assert merkez.x() > card.lblThumb.width() / 2
    assert merkez.y() > card.lblThumb.height() / 2
    assert card.badge.geometry().right() <= card.lblThumb.width()
    assert card.badge.geometry().bottom() <= card.lblThumb.height()


def test_uzun_baslik_iki_satiri_asinca_elide_ediliyor(qtbot):
    """Taşan başlık kırpılmalı (…), yarım harf satırı olarak kesilmemeli."""
    uzun = ("Jojo'nun Tuhaf Macerası Taş Okyanusu Altın Rüzgar Çelik Top Koşusu "
            "Ve Devamı Niteliğindeki Uzun Uzun Alt Başlık")
    card = _goster(qtbot, AnimeCard(uzun, "★ 8.5", image_url="http://x/y.png"), 220)

    satirlar = card.lblTitle.visible_lines()
    assert len(satirlar) == 2
    assert card.lblTitle.is_elided()
    assert satirlar[-1].endswith("…")
    # Tam başlık kaybolmamalı: tooltip ve çağıranlar hâlâ tamamını görür.
    assert card.lblTitle.text() == uzun


def test_kisa_baslik_elide_edilmiyor(qtbot):
    card = _goster(qtbot, AnimeCard("Naruto", "★ 8.5", image_url="http://x/y.png"), 320)

    assert card.lblTitle.visible_lines() == ["Naruto"]
    assert not card.lblTitle.is_elided()


def test_olculmemis_etiket_basligi_kirpmiyor(qtbot):
    """Genişlik 0 iken kırpmak, metni sebepsiz '…'e indirirdi."""
    lbl = ElidedLabel("Cowboy Bebop")
    qtbot.addWidget(lbl)
    assert lbl.visible_lines(0) == ["Cowboy Bebop"]


def test_gorselsiz_kart_cokmuyor(qtbot):
    """Kapak sağlamayan kaynakta kart boş siyah poster göstermemeli."""
    card = _goster(qtbot, AnimeCard("AnimeDepo Kaydı", "AnimeDepo", image_url=None), 320)

    assert not card.lblThumb.isVisibleTo(card)
    assert card.height() > 0 and card.width() == 320
    assert card.lblTitle.text() == "AnimeDepo Kaydı"
    assert card.lblSource.text() == "AnimeDepo"
    assert card.lblSource.isVisibleTo(card), "rozet poster ile birlikte kaybolmuş"


def test_kapak_inmeden_once_yer_tutucu_var(qtbot):
    """Görsel gelene kadar poster boş siyah dikdörtgen olmamalı."""
    card = _goster(qtbot, AnimeCard("Cowboy Bebop", "★ 8.7", image_url="http://x/y.png"), 320)

    assert card.lblThumb.pixmap().isNull()
    assert "Cowboy Bebop" in card.lblThumb.text()






def test_status_label_states(qtbot):
    lbl = StatusLabel()
    qtbot.addWidget(lbl)
    lbl.info("arıyor")
    assert lbl.text() == "arıyor"
    lbl.ok("bulundu")
    assert lbl.text() == "bulundu"
    lbl.error("hata")
    assert lbl.text() == "hata"
