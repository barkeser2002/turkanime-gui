"""Arama sayfası — tüm kaynaklarda paralel arama ve sonuç ızgarası.

Eski GUI'deki `SearchWorker` + `display_search_results` akışının Qt karşılığı.
Arama işi `common.adapters.SearchEngine` üzerinden yürür (kaynak listesi orada
tanımlı; AnimeDepo dahil), bu yüzden kaynak eklemek bu sayfada değişiklik
gerektirmez.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..widgets import AnimeCard, ScrollableGrid, StatusLabel
from ..workers import WorkerSignals, run_bg

# Kaynak başına gösterilecek azami sonuç
LIMIT_PER_SOURCE = 10


class SearchPage(QWidget):
    """Arama sonuçlarını kaynak rozetli kartlar hâlinde gösterir."""

    # (source_name, slug, title)
    anime_selected = Signal(str, str, str)
    # (AnimeCard, görsel baytları) — arka plandan UI thread'ine
    thumb_ready = Signal(object, object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._busy = False
        self._query = ""

        self.signals = WorkerSignals()
        self.signals.connect_found(self._on_results)
        self.signals.connect_error(self._on_error)
        self.thumb_ready.connect(self._apply_thumb)

        self._build_ui()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        head = QHBoxLayout()
        self.lblTitle = QLabel("Arama")
        self.lblTitle.setObjectName("Title")
        head.addWidget(self.lblTitle)
        head.addStretch(1)
        self.lblStatus = StatusLabel()
        self.lblStatus.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.lblStatus)
        layout.addLayout(head)

        self.results = ScrollableGrid()
        layout.addWidget(self.results, 1)

        self.lblStatus.info("Aramak için yukarıdaki kutuyu kullanın.")

    # ── Arama akışı ─────────────────────────────────────────────────────────
    def start_search(self, query: str) -> None:
        """Aramayı arka planda başlat (UI bloklanmaz)."""
        query = (query or "").strip()
        if not query:
            return
        if self._busy:
            self.lblStatus.info("Önceki arama sürüyor, lütfen bekleyin…")
            return
        self._busy = True
        self._query = query
        self.lblTitle.setText(f"Arama — “{query}”")
        self.results.clear()
        self.lblStatus.info("Kaynaklarda aranıyor…")

        run_bg(self._do_search, query, signals=self.signals)

    def _do_search(self, query: str) -> None:
        """Arka plan thread'i: tüm kaynaklarda paralel ara."""
        from ....common.adapters import SearchEngine

        engine = SearchEngine()
        # Zengin sözleşme: kapak görseli sağlayabilen kaynaklar (AniList) görsel
        # URL'si de döndürür; sağlamayanlar image=None ile sarılır.
        results = engine.search_all_sources_rich(query, limit_per_source=LIMIT_PER_SOURCE)
        # Sinyal kuyruklu bağlandığı için slot GUI thread'inde çalışır.
        self.signals.emit_found(results)

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
            pass          # kart bu arada silinmiş (yeni arama)

    # ── Sonuç işleme (GUI thread'i) ─────────────────────────────────────────
    def _on_results(self, results: Dict[str, List[Tuple[str, str]]]) -> None:
        self._busy = False
        if not isinstance(results, dict):
            self.lblStatus.error("Beklenmeyen arama sonucu.")
            return

        cards: List[QWidget] = []
        per_source: List[str] = []
        pending_thumbs = []
        for source, items in sorted(results.items()):
            if not items:
                continue
            per_source.append(f"{source}: {len(items)}")
            for item in items:
                slug = item.get("slug") or ""
                title = item.get("title") or slug
                image = item.get("image")
                card = AnimeCard(title, source, payload=(source, slug, title),
                                 image_url=image)
                card.clicked.connect(self._on_card_clicked)
                cards.append(card)
                if image:
                    pending_thumbs.append((card, image))

        if not cards:
            self.lblStatus.error(f"“{self._query}” için sonuç bulunamadı.")
            return

        self.results.set_items(cards)
        self.lblStatus.ok(f"{len(cards)} sonuç — " + ", ".join(per_source))

        # Görseller kartlar yerleştikten SONRA, arka planda indirilir.
        for card, url in pending_thumbs:
            run_bg(self._fetch_thumb, card, url)

    def _on_error(self, message: str) -> None:
        self._busy = False
        self.lblStatus.error(f"Arama hatası: {message}")

    def _on_card_clicked(self, payload) -> None:
        if not payload:
            return
        source, slug, title = payload
        self.anime_selected.emit(source, slug, title)


__all__ = ["SearchPage"]
