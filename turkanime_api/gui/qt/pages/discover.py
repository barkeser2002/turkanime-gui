"""Keşif sayfaları — Ana Sayfa / Trend / Bu Sezon.

Tek bileşen (`DiscoverPage`) üç nav öğesini de karşılar; aralarındaki tek fark
veri kaynağı, başlık ve kart sayısıdır. Eski GUI'deki `load_trending_anime` +
`show_season` akışlarının Qt karşılığı.

Eski ana sayfadaki hero istatistikleri ("10.000+ Anime" gibi sabit sayılar) ve
tıklanamayan kategori kartları **taşınmadı**: hiçbiri gerçek veriye bağlı
değildi.

Veri MyAnimeList'ten (Jikan) gelir; Jikan boş/hatalı dönerse AniList
trending'e düşülür. İki istemci de aynı sözlük şeklini üretir
(`title.romaji`, `coverImage.large`, `averageScore` 0-100), bu yüzden kart
üretimi tek koddur.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..theme import TEXT_MUTED, score_color
from ..widgets import AnimeCard, ScrollableGrid, StatusLabel
from ..workers import WorkerSignals, run_bg

MODES = ("home", "trending", "season")

# Ana sayfa bir "şerit": tek ekranda bitsin diye daha az kart gösterir.
HOME_LIMIT = 12
LIST_LIMIT = 24

# Jikan sezon adlarını İngilizce döndürür; başlık kullanıcıya gösterildiği için
# ay numarasından Türkçe etiketi burada üretiyoruz.
_SEASON_NAMES = {1: "Kış", 2: "İlkbahar", 3: "Yaz", 4: "Sonbahar"}

_HEADINGS = {
    "home": ("Ana Sayfa", "Şu anda trend olan animeler"),
    "trending": ("Trend", "MyAnimeList'te yayında ve en çok izlenenler"),
    "season": ("Bu Sezon", "MyAnimeList sezon listesi"),
}


# ── Veri yardımcıları (Qt'siz; doğrudan test edilebilir) ────────────────────
def season_label(now: Optional[datetime] = None) -> str:
    """Başlıkta gösterilen "Yaz 2026" biçimindeki etiket."""
    now = now or datetime.now()
    return f"{_SEASON_NAMES[(now.month - 1) // 3 + 1]} {now.year}"


def anime_title(item: Dict[str, Any]) -> str:
    """Kart başlığı. Jikan ve AniList'te `title` bir sözlüktür."""
    title = item.get("title")
    if isinstance(title, dict):
        return (title.get("romaji") or title.get("english")
                or title.get("native") or "İsimsiz")
    return str(title or "İsimsiz")


def cover_url(item: Dict[str, Any]) -> Optional[str]:
    """Kapak görseli URL'si (yoksa None)."""
    cover = item.get("coverImage")
    if isinstance(cover, dict):
        return cover.get("large") or cover.get("medium")
    return None


def score_of(item: Dict[str, Any]) -> Optional[float]:
    """0-100 aralığındaki puan. İki istemci de bu ölçeği kullanır."""
    raw = item.get("averageScore")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def fetch_discover(mode: str, limit: int = LIST_LIMIT) -> List[Dict[str, Any]]:
    """Seçilen kip için anime listesini getir (arka plan thread'inde çağrılır).

    Jikan tercih edilir; 5xx/timeout gibi geçici hatalarda istemci kendi içinde
    yeniden dener ve önbelleğe düşer. Yine de boş dönerse AniList trending
    devreye girer — eski GUI'deki davranış buydu ve keşif sayfasının tamamen
    boş kalmasını engelliyor.
    """
    items: List[Dict[str, Any]] = []
    try:
        # İçe aktarımlar gövdede: bu modül GUI açılışında yükleniyor, ağ
        # istemcilerini o anda import etmeye gerek yok.
        from ....jikan_client import get_seasonal_anime_list, get_trending_anime_list

        if mode == "season":
            # year/season BİLEREK verilmiyor: parametresiz çağrı Jikan'ın
            # `/seasons/now` ucuna gider; "şu anki sezon" tanımını yerel saatten
            # tahmin etmek yerine sunucuya bırakmak daha doğru.
            items = get_seasonal_anime_list() or []
        else:
            items = get_trending_anime_list(limit=limit) or []
    except Exception as exc:  # ağ/parse hatası sayfayı düşürmemeli
        print(f"[Keşif] Jikan hatası: {exc}")
        items = []

    if not items:
        try:
            from ....anilist_client import anilist_client

            items = anilist_client.get_trending_anime(page=1, per_page=limit) or []
        except Exception as exc:
            print(f"[Keşif] AniList hatası: {exc}")
            items = []

    return list(items)[:limit]


class DiscoverPage(QWidget):
    """Trend / sezon listelerini kart ızgarasında gösteren keşif sayfası."""

    # Seçilen animenin başlığı — detay sayfası gelene kadar aramaya köprülenir.
    anime_selected = Signal(str)
    # (AnimeCard, görsel baytları) — arka plandan UI thread'ine
    thumb_ready = Signal(object, object)

    def __init__(self, mode: str = "home", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.mode = mode if mode in MODES else "home"
        self.limit = HOME_LIMIT if self.mode == "home" else LIST_LIMIT
        self._busy = False
        self._loaded = False
        self._cards: List[AnimeCard] = []

        self.signals = WorkerSignals()
        self.signals.connect_found(self._on_items)
        self.signals.connect_error(self._on_error)
        self.thumb_ready.connect(self._apply_thumb)

        self._build_ui()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        heading, subtitle = _HEADINGS[self.mode]
        if self.mode == "season":
            subtitle = f"{season_label()} sezonu • MyAnimeList"

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)

        self.lblTitle = QLabel(heading)
        self.lblTitle.setObjectName("Title")
        titles.addWidget(self.lblTitle)

        self.lblSubtitle = QLabel(subtitle)
        self.lblSubtitle.setObjectName("Muted")
        titles.addWidget(self.lblSubtitle)
        head.addLayout(titles)

        head.addStretch(1)

        self.lblStatus = StatusLabel()
        self.lblStatus.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.lblStatus)

        self.btnRefresh = QPushButton("Yenile")
        self.btnRefresh.clicked.connect(self.refresh)
        head.addWidget(self.btnRefresh)
        layout.addLayout(head)

        self.results = ScrollableGrid()
        layout.addWidget(self.results, 1)

        self.lblStatus.info("Yükleniyor…")

    # ── Yükleme ─────────────────────────────────────────────────────────────
    def showEvent(self, event):  # noqa: N802 (Qt imzası)
        """İlk gösterimde veriyi çek.

        Yükleme kurucuda yapılmıyor: üç keşif sayfası da pencere kurulurken
        yaratılıyor; kurucuda çekseydik uygulama her açılışta kullanıcı hiç o
        sekmeye bakmasa bile üç ağ isteği atardı.
        """
        super().showEvent(event)
        if not self._loaded:
            self.refresh()

    def refresh(self) -> None:
        """Listeyi (yeniden) getir."""
        if self._busy:
            return
        self._busy = True
        self._loaded = True
        self.btnRefresh.setEnabled(False)
        self.results.clear()
        self._cards = []
        self.lblStatus.info("Yükleniyor…")
        run_bg(self._fetch, signals=self.signals)

    def _fetch(self) -> None:
        """Arka plan thread'i: veriyi çek, sinyalle UI'ya taşı."""
        self.signals.emit_found(fetch_discover(self.mode, self.limit))

    # ── Sonuç işleme (GUI thread'i) ─────────────────────────────────────────
    def _on_items(self, items: List[Dict[str, Any]]) -> None:
        self._busy = False
        self.btnRefresh.setEnabled(True)

        if not isinstance(items, list) or not items:
            self.lblStatus.error("İçerik alınamadı. Bağlantınızı kontrol edip yenileyin.")
            return

        cards: List[AnimeCard] = []
        pending_thumbs: List[tuple] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            card = self._make_card(item)
            cards.append(card)
            if card.image_url:
                pending_thumbs.append((card, card.image_url))

        if not cards:
            self.lblStatus.error("İçerik alınamadı. Bağlantınızı kontrol edip yenileyin.")
            return

        self._cards = cards
        self.results.set_items(list(cards))
        self.lblStatus.ok(f"{len(cards)} anime")

        # Görseller kartlar yerleştikten SONRA, arka planda indirilir.
        for card, url in pending_thumbs:
            run_bg(self._fetch_thumb, card, url)

    def _make_card(self, item: Dict[str, Any]) -> AnimeCard:
        """Tek bir anime sözlüğünden kart üret (rozet = puan)."""
        title = anime_title(item)
        score = score_of(item)
        # AnimeCard'ın ikinci alanı bir rozet; keşifte akış kaynağı henüz belli
        # olmadığı için oraya puanı yazıyoruz.
        badge = f"★ {score / 10:.1f}" if score else "Puansız"

        card = AnimeCard(title, badge, payload=title, image_url=cover_url(item))
        color = score_color(score / 100.0) if score else TEXT_MUTED
        card.lblSource.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600;")

        episodes = item.get("episodes")
        tip = [title]
        if score:
            tip.append(f"Puan: {score / 10:.1f}/10")
        if episodes:
            tip.append(f"{episodes} bölüm")
        card.setToolTip("\n".join(tip))

        card.clicked.connect(self._on_card_clicked)
        return card

    def _on_error(self, message: str) -> None:
        self._busy = False
        self.btnRefresh.setEnabled(True)
        self.lblStatus.error(f"Yüklenemedi: {message}")

    def _on_card_clicked(self, payload) -> None:
        if payload:
            self.anime_selected.emit(str(payload))

    def cards(self) -> List[AnimeCard]:
        """Ekrandaki kartlar (test ve köprüleme için)."""
        return list(self._cards)

    # ── Kapak görselleri ────────────────────────────────────────────────────
    def _fetch_thumb(self, card, url: str) -> None:
        """Arka plan: görseli indir, baytları UI thread'ine taşı.

        Widget'a burada DOKUNULMAZ; yalnızca sinyal yayılır (kart bu arada
        silinmiş olabilir, o yüzden slot tarafında da kontrol var).
        """
        try:
            import requests
            data = requests.get(url, timeout=10).content
        except Exception:
            return
        if data:
            self.thumb_ready.emit(card, data)

    def _apply_thumb(self, card, data: bytes) -> None:
        """GUI thread'i: görseli karta yerleştir (kart hâlâ yaşıyorsa)."""
        try:
            card.set_thumbnail(data)
        except RuntimeError:
            pass          # kart bu arada silinmiş (yenileme)


__all__ = ["DiscoverPage", "fetch_discover", "season_label", "anime_title",
           "cover_url", "score_of", "MODES"]
