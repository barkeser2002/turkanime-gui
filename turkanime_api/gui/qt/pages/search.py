"""Arama sayfası — tüm kaynaklarda paralel arama ve sonuç ızgarası.

Eski GUI'deki `SearchWorker` + `display_search_results` akışının Qt karşılığı.
Arama işi `common.adapters.SearchEngine` üzerinden yürür (kaynak listesi orada
tanımlı; AnimeDepo dahil), bu yüzden kaynak eklemek bu sayfada değişiklik
gerektirmez.
"""
from __future__ import annotations

from typing import Any, Dict, List

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
        self._cards: List[AnimeCard] = []

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
        self._cards = []
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

    def cards(self) -> List[AnimeCard]:
        """Ekrandaki kartlar (test ve köprüleme için) — `DiscoverPage` ile aynı."""
        return list(self._cards)

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
    def _on_results(self, results: Dict[str, List[Dict[str, Any]]]) -> None:
        """Kaynak → kayıt listesi eşlemesini karta çevir.

        Sözleşme `SearchEngine.search_all_sources_rich`'in döndürdüğüdür: kayıt
        bir SÖZLÜK (slug/title/image), `(slug, title)` çifti DEĞİL. İmza uzun
        süre eski `search_all_sources` sözleşmesini (`Tuple[str, str]`) iddia
        ediyordu; gövde en baştan sözlük okuduğu için çalışıyordu ama okuyan
        kişiyi gövdeyi "düzeltmeye" davet ediyordu.
        """
        self._busy = False
        if not isinstance(results, dict):
            self.lblStatus.error("Beklenmeyen arama sonucu.")
            return

        cards: List[AnimeCard] = []
        per_source: List[str] = []
        pending_thumbs = []
        for source, items in sorted(results.items()):
            if not items:
                continue
            eklenen = 0
            for item in items:
                # Tek bozuk kayıt (eski demet biçimi, None…) tüm sonuç ekranını
                # götürmemeli: slot içindeki istisna Qt sinyal yolunda yutulur,
                # geriye yalnızca "aranıyor…"da donmuş bir sayfa kalırdı.
                if not isinstance(item, dict):
                    continue
                slug = item.get("slug") or ""
                # Slug'sız kayıt tıklanabilir ama işe yaramaz: bölüm sayfası boş
                # slug'la sorgulanır ve kullanıcı sessiz bir hiçlikle karşılaşır.
                # Kaydı hiç göstermemek, ölü kart göstermekten dürüst.
                if not slug:
                    continue
                title = item.get("title") or slug
                image = item.get("image")
                card = AnimeCard(title, source, payload=(source, slug, title),
                                 image_url=image)
                card.clicked.connect(self._on_card_clicked)
                cards.append(card)
                eklenen += 1
                if image:
                    pending_thumbs.append((card, image))
            # Sayaç atılan kayıtları değil GÖSTERİLENLERİ saymalı; aksi hâlde
            # kaynak dökümünün toplamı üstteki toplamı tutmuyordu.
            if eklenen:
                per_source.append(f"{source}: {eklenen}")

        if not cards:
            # Önceki aramanın kartları ekranda kalmamalı: "sonuç bulunamadı"
            # yazarken altta eski sonuçları göstermek doğrudan yalan olurdu.
            self._cards = []
            self.results.clear()
            self.lblStatus.error(f"“{self._query}” için sonuç bulunamadı.")
            return

        self._cards = cards
        self.results.set_items(list(cards))
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
