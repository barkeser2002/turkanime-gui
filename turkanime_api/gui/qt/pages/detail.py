"""Anime detay sayfası — keşif/arama ile bölüm listesi arasındaki köprü.

Eski GUI'de bu iş `gui/main.py::show_anime_details` içindeydi (~740 satır). O
hacmin neredeyse tamamı her kaynak için ayrı yazılmış bölüm-yükleme `elif`
bloklarıydı; aynı iş artık `sources_bridge.fetch_episodes` tablosunda tek yerde
durduğu için burada yalnızca sunum + tek bir çağrı kalıyor.

Sayfa **kendisine verilen sözlüğü** render eder, kendiliğinden metadata
aramaz: keşif kartı zaten tam kaydı taşır, arama sonucu ise yalnızca
(kaynak, slug, başlık) bilir ve bu hâliyle de bütün aksiyonlar çalışır.
Kullanıcının istemediği bir ağ isteği atmamak, birkaç boş alanı doldurmaktan
önemli.

Bölümler burada çekilir ama burada GÖSTERİLMEZ: sayfalama, filtre, toplu seçim
ve oynat/indir kablolaması `EpisodePage`'de zaten var; ikinci bir liste
arayüzü yazmak eski GUI'nin kopyala-yapıştır hatasını tekrarlamak olurdu.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from ..sources_bridge import (
    METADATA_ONLY, UnsupportedSource, fetch_episodes, supported_sources,
)
from ..theme import ACCENT, BG_ELEV, BG_ELEV_2, BORDER, TEXT_MUTED, score_color
from ..widgets import StatusLabel
from ..workers import WorkerSignals, run_bg
from .discover import anime_title, cover_url, score_of

COVER_W, COVER_H = 220, 310

# Rozet satırı tek sıra; çok uzun listeler kartı taşırıyor. AniList'te tür
# sayısı zaten ~5, kalanı araç ipucunda gösteriliyor.
MAX_BADGES = 8

# Eşleşme diyaloğunda kaynak başına gösterilecek aday sayısı. Amaç doğru kaydı
# bulmak, tam listeyi taramak değil.
MATCH_LIMIT_PER_SOURCE = 8

# AniList/Jikan büyük harfli sabitler döndürür; kullanıcıya Türkçe gösteriyoruz.
SEASON_LABELS = {"WINTER": "Kış", "SPRING": "İlkbahar", "SUMMER": "Yaz",
                 "FALL": "Sonbahar"}
STATUS_LABELS = {"RELEASING": "Yayında", "FINISHED": "Tamamlandı",
                 "NOT_YET_RELEASED": "Henüz yayınlanmadı",
                 "CANCELLED": "İptal edildi", "HIATUS": "Ara verildi"}

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLANKS_RE = re.compile(r"\n{3,}")


# ── Veri yardımcıları (Qt'siz; doğrudan test edilebilir) ────────────────────
def clean_html(text: Any) -> str:
    """Özeti düz metne indir.

    Hem AniList hem Jikan özeti HTML taşır (`<br>`, `<i>`, `&quot;`). Ham
    hâliyle basılırsa kullanıcı etiketleri okur.
    """
    if not text:
        return ""
    plain = _BR_RE.sub("\n", str(text))
    plain = _TAG_RE.sub("", plain)
    return _BLANKS_RE.sub("\n\n", html.unescape(plain)).strip()


def studio_names(item: Dict[str, Any]) -> List[str]:
    """Stüdyo adları — iki farklı kaynak şekli de desteklenir.

    Jikan `to_anilist_format` düz `List[str]` üretir, AniList GraphQL ise
    `{"nodes": [{"name": ...}]}` döndürür. Birini varsayan kod diğerinde
    sessizce boş liste gösterir; eski GUI de bu yüzden iki dalı taşıyordu.
    """
    raw = item.get("studios")
    out: List[str] = []
    if isinstance(raw, dict):
        for node in (raw.get("nodes") or []):
            if isinstance(node, dict) and node.get("name"):
                out.append(str(node["name"]).strip())
        for edge in (raw.get("edges") or []):
            node = (edge or {}).get("node") if isinstance(edge, dict) else None
            if isinstance(node, dict) and node.get("name"):
                out.append(str(node["name"]).strip())
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
            elif isinstance(entry, dict) and entry.get("name"):
                # Ham Jikan kaydı (to_anilist_format'tan geçmemiş)
                out.append(str(entry["name"]).strip())
    return [s for s in out if s]


def genre_names(item: Dict[str, Any]) -> List[str]:
    """Tür adları — düz metin listesi ya da `{"name": ...}` sözlükleri."""
    raw = item.get("genres")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif isinstance(entry, dict) and entry.get("name"):
            out.append(str(entry["name"]).strip())
    return out


def meta_line(item: Dict[str, Any]) -> str:
    """"12 bölüm • 24 dk • Yaz 2026 • Tamamlandı" biçimindeki özet satır."""
    parts: List[str] = []
    if item.get("episodes"):
        parts.append(f"{item['episodes']} bölüm")
    if item.get("duration"):
        parts.append(f"{item['duration']} dk")

    season = SEASON_LABELS.get(str(item.get("season") or "").upper())
    year = item.get("seasonYear")
    if season and year:
        parts.append(f"{season} {year}")
    elif season:
        parts.append(season)
    elif year:
        parts.append(str(year))

    status = STATUS_LABELS.get(str(item.get("status") or "").upper())
    if status:
        parts.append(status)
    return " • ".join(parts)


def save_match(source: str, slug: str, title: str) -> bool:
    """Kullanıcının seçtiği eşleşmeyi API'ye kaydet.

    Kaydetmek "nice to have": API kapalıysa ya da kullanıcı çevrimdışıysa
    detay sayfası çalışmaya devam etmeli, bu yüzden her hata yutulur.
    """
    try:
        from ....common.db import APIManager

        return bool(APIManager().save_anime_match(source, str(slug), title))
    except Exception as exc:            # ağ/import hatası akışı kesmemeli
        print(f"[Detay] Eşleşme kaydedilemedi: {exc}")
        return False


def _badge(text: str) -> QLabel:
    """Tür/stüdyo rozeti (tema QSS'i QLabel'a kutu vermiyor, elle veriyoruz)."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background: {BG_ELEV_2}; border: 1px solid {BORDER};"
        f"border-radius: 10px; padding: 3px 10px; color: {TEXT_MUTED}; font-size: 11px;"
    )
    return lbl


# ── "İstediğin anime değil mi?" diyaloğu ────────────────────────────────────
class AnimeMatchDialog(QDialog):
    """Çok kaynakta arayıp doğru kaydı elle seçtiren diyalog.

    Otomatik eşleştirme (kaynak başına `limit=1`) sık sık yanlış sezonu ya da
    OVA'yı seçiyor; kullanıcıya kaçış yolu bırakmak eski GUI'de de vardı.
    """

    def __init__(self, query: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("İstediğin anime değil mi?")
        self.resize(560, 480)
        self.selection: Optional[Tuple[str, str, str]] = None
        self._busy = False

        self.signals = WorkerSignals()
        self.signals.connect_found(self.set_results)
        self.signals.connect_error(self._on_error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QLabel("Doğru kaydı seçin")
        head.setObjectName("Subtitle")
        layout.addWidget(head)

        row = QHBoxLayout()
        self.txtQuery = QLineEdit(query)
        self.txtQuery.setPlaceholderText("Anime adı…")
        self.txtQuery.returnPressed.connect(self.search)
        row.addWidget(self.txtQuery, 1)

        self.btnSearch = QPushButton("Ara")
        self.btnSearch.setObjectName("Primary")
        self.btnSearch.clicked.connect(self.search)
        row.addWidget(self.btnSearch)
        layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept_selection())
        layout.addWidget(self.tree, 1)

        self.lblStatus = StatusLabel()
        layout.addWidget(self.lblStatus)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btnCancel = QPushButton("İptal")
        self.btnCancel.clicked.connect(self.reject)
        buttons.addWidget(self.btnCancel)

        self.btnPick = QPushButton("Bu Animeyi Kullan")
        self.btnPick.setObjectName("Primary")
        self.btnPick.clicked.connect(self.accept_selection)
        buttons.addWidget(self.btnPick)
        layout.addLayout(buttons)

        self.lblStatus.info("Aramak için “Ara”ya basın.")

    # ── Arama ───────────────────────────────────────────────────────────────
    def search(self) -> None:
        query = self.txtQuery.text().strip()
        if not query or self._busy:
            return
        self._busy = True
        self.btnSearch.setEnabled(False)
        self.tree.clear()
        self.lblStatus.info("Kaynaklarda aranıyor…")
        run_bg(self._do_search, query, signals=self.signals)

    def _do_search(self, query: str) -> None:
        """Arka plan thread'i — widget'a DOKUNMAZ, yalnızca sinyal yayar."""
        from ....common.adapters import SearchEngine

        results = SearchEngine().search_all_sources_rich(
            query, limit_per_source=MATCH_LIMIT_PER_SOURCE)
        self.signals.emit_found(results)

    def set_results(self, results: Dict[str, List[Dict[str, Any]]]) -> None:
        """GUI thread'i: sonuçları kaynağa göre gruplayıp ağaca yerleştir."""
        self._busy = False
        self.btnSearch.setEnabled(True)
        self.tree.clear()
        if not isinstance(results, dict):
            self.lblStatus.error("Beklenmeyen arama sonucu.")
            return

        total = 0
        for source in sorted(results):
            items = [i for i in (results[source] or []) if isinstance(i, dict)]
            if not items:
                continue
            parent = QTreeWidgetItem(self.tree, [f"{source} ({len(items)})"])
            parent.setFirstColumnSpanned(True)
            for item in items:
                slug = str(item.get("slug") or "")
                title = str(item.get("title") or slug)
                if not slug:
                    continue
                child = QTreeWidgetItem(parent, [title])
                child.setData(0, Qt.ItemDataRole.UserRole, (source, slug, title))
                total += 1
            parent.setExpanded(True)

        if not total:
            self.lblStatus.error("Hiçbir kaynakta sonuç bulunamadı.")
            return
        self.lblStatus.ok(f"{total} aday")

    def _on_error(self, message: str) -> None:
        self._busy = False
        self.btnSearch.setEnabled(True)
        self.lblStatus.error(f"Arama hatası: {message}")

    # ── Seçim ───────────────────────────────────────────────────────────────
    def accept_selection(self) -> None:
        """Seçili yaprağı kabul et (kaynak başlığına basmak seçim değildir)."""
        item = self.tree.currentItem()
        payload = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not payload:
            self.lblStatus.error("Önce listeden bir anime seçin.")
            return
        self.selection = payload
        self.accept()


# ── Detay sayfası ───────────────────────────────────────────────────────────
class DetailPage(QWidget):
    """Tek bir animenin künyesi + bölüm listesine geçiş noktası."""

    # (kaynak, slug, başlık, bölümler) — liste zaten çekildi, tekrar çekilmesin
    episodes_ready = Signal(str, str, str, object)
    back_requested = Signal()
    # (request_id, görsel baytları) — arka plandan UI thread'ine
    cover_ready = Signal(int, object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._anime: Dict[str, Any] = {}
        self._slug = ""
        self._match_title = ""
        self._busy = False
        # Yarış koruması: her yeni anime bu sayacı artırır, arka plandan dönen
        # her sonuç kendi kimliğiyle gelir ve eskiyse sessizce atılır.
        self._request_id = 0

        self.signals = WorkerSignals()
        self.signals.connect_found(self._on_episodes)
        self.signals.connect_error_item(self._on_failed)
        self.cover_ready.connect(self._apply_cover)

        self._build_ui()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(12)

        top = QHBoxLayout()
        self.btnBack = QPushButton("← Geri")
        self.btnBack.clicked.connect(lambda: self.back_requested.emit())
        top.addWidget(self.btnBack)
        top.addStretch(1)
        self.lblStatus = StatusLabel()
        self.lblStatus.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.lblStatus)
        outer.addLayout(top)

        # Küçük pencerelerde künye + özet sığmıyor; tamamı kaydırılabilir.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        col.addWidget(self._build_header())
        col.addWidget(self._build_summary())
        col.addWidget(self._build_tags())
        col.addStretch(1)

        self.lblStatus.info("Bir anime seçin.")

    def _build_header(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Panel")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 16, 16, 16)
        row.setSpacing(18)

        self.lblCover = QLabel("Kapak yok")
        self.lblCover.setFixedSize(COVER_W, COVER_H)
        self.lblCover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblCover.setStyleSheet(
            f"background: {BG_ELEV}; border-radius: 6px; color: {TEXT_MUTED};")
        row.addWidget(self.lblCover, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(8)

        self.lblTitle = QLabel("")
        self.lblTitle.setObjectName("Title")
        self.lblTitle.setWordWrap(True)
        info.addWidget(self.lblTitle)

        self.lblMeta = QLabel("")
        self.lblMeta.setObjectName("Muted")
        self.lblMeta.setWordWrap(True)
        info.addWidget(self.lblMeta)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.lblScore = QLabel("")
        self.lblPopularity = QLabel("")
        stats.addWidget(self.lblScore)
        stats.addWidget(self.lblPopularity)
        stats.addStretch(1)
        info.addLayout(stats)

        info.addStretch(1)
        info.addLayout(self._build_actions())
        row.addLayout(info, 1)
        return card

    def _build_actions(self):
        actions = QHBoxLayout()
        actions.setSpacing(8)

        lbl = QLabel("Kaynak:")
        lbl.setObjectName("Muted")
        actions.addWidget(lbl)

        self.cmbSource = QComboBox()
        self.cmbSource.addItems(supported_sources())
        self.cmbSource.currentTextChanged.connect(self._on_source_changed)
        actions.addWidget(self.cmbSource)

        self.btnEpisodes = QPushButton("Bölümleri Getir")
        self.btnEpisodes.setObjectName("Primary")
        self.btnEpisodes.clicked.connect(self.load_episodes)
        actions.addWidget(self.btnEpisodes)

        self.btnMatch = QPushButton("İstediğin anime değil mi?")
        self.btnMatch.clicked.connect(self.open_match_dialog)
        actions.addWidget(self.btnMatch)

        actions.addStretch(1)
        return actions

    def _build_summary(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Panel")
        col = QVBoxLayout(card)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(8)

        heading = QLabel("Özet")
        heading.setObjectName("Subtitle")
        col.addWidget(heading)

        # QTextBrowser: uzun özetler kaydırılabilir olmalı, ama düz metin
        # basıyoruz (bkz. clean_html) — zengin metin işleme istemiyoruz.
        self.txtSummary = QTextBrowser()
        self.txtSummary.setMinimumHeight(140)
        self.txtSummary.setOpenExternalLinks(False)
        col.addWidget(self.txtSummary)
        return card

    def _build_tags(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Panel")
        col = QVBoxLayout(card)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(8)

        self.genre_badges: List[QLabel] = []
        self.studio_badges: List[QLabel] = []

        self.lblGenresHead = QLabel("Türler")
        self.lblGenresHead.setObjectName("Subtitle")
        col.addWidget(self.lblGenresHead)
        self._genre_row = QHBoxLayout()
        self._genre_row.setSpacing(6)
        self._genre_row.addStretch(1)
        col.addLayout(self._genre_row)

        self.lblStudiosHead = QLabel("Stüdyo")
        self.lblStudiosHead.setObjectName("Subtitle")
        col.addWidget(self.lblStudiosHead)
        self._studio_row = QHBoxLayout()
        self._studio_row.setSpacing(6)
        self._studio_row.addStretch(1)
        col.addLayout(self._studio_row)
        return card

    # ── Giriş noktaları ─────────────────────────────────────────────────────
    @property
    def request_id(self) -> int:
        """Ekranda duran isteğin kimliği (yarış koruması testleri için)."""
        return self._request_id

    def show_anime(self, anime: Dict[str, Any],
                   source: Optional[str] = None, slug: str = "") -> int:
        """Keşif kartından gelen tam metadata kaydını göster."""
        self._request_id += 1
        self._busy = False           # önceki isteğin sonucu artık geçersiz
        self.btnEpisodes.setEnabled(True)
        self._anime = dict(anime or {})
        self._match_title = anime_title(self._anime)
        self._slug = str(slug or "")
        if source:
            self._select_source(source)
        self._render()
        self._load_cover(self._request_id)
        return self._request_id

    def show_match(self, source: str, slug: str, title: str) -> int:
        """Arama sonucundan gelen (kaynak, slug, başlık) üçlüsünü göster.

        Metadata yok; sayfa yine de künye iskeletini ve aksiyonları gösterir.
        """
        rid = self.show_anime({"title": {"romaji": title}}, source=source, slug=slug)
        self._match_title = title
        # METADATA_ONLY kaynaklarda `_select_source` zaten uyarı yazdı; onu
        # "bölümleri getirebilirsiniz" ile değiştirmek yanlış yönlendirme olur.
        if source not in METADATA_ONLY:
            self.lblStatus.info(f"{source} kaydı seçildi. Bölümleri getirebilirsiniz.")
        return rid

    def apply_match(self, source: str, slug: str, title: str) -> None:
        """Eşleşme diyaloğundan seçilen kaydı sayfaya bağla.

        `show_match` DEĞİL: kullanıcı aynı animenin doğru kaydını seçti,
        ekrandaki künyeyi (özet, tür, kapak) silmenin anlamı yok.
        """
        self._slug = str(slug or "")
        self._match_title = title or self._match_title
        self._select_source(source)
        self.lblStatus.ok(f"{source} → {title} eşleştirildi.")
        save_match(source, self._slug, self._match_title)

    # ── Render ──────────────────────────────────────────────────────────────
    def _render(self) -> None:
        item = self._anime
        self.lblTitle.setText(anime_title(item))
        self.lblMeta.setText(meta_line(item))

        score = score_of(item)
        if score:
            self.lblScore.setText(f"SKOR  {score:.0f}%")
            self.lblScore.setStyleSheet(
                f"color: {score_color(score / 100.0)}; font-weight: 600;")
        else:
            self.lblScore.setText("")
            self.lblScore.setStyleSheet("")

        popularity = item.get("popularity")
        self.lblPopularity.setText(f"POPÜLERLİK  #{popularity}" if popularity else "")
        self.lblPopularity.setStyleSheet(
            f"color: {ACCENT}; font-weight: 600;" if popularity else "")

        self.txtSummary.setPlainText(clean_html(item.get("description"))
                                     or "Özet bulunamadı.")

        self._fill_badges(self._genre_row, self.genre_badges, genre_names(item))
        self._fill_badges(self._studio_row, self.studio_badges, studio_names(item))
        self.lblGenresHead.setVisible(bool(self.genre_badges))
        self.lblStudiosHead.setVisible(bool(self.studio_badges))

        self.lblCover.clear()
        self.lblCover.setText("Kapak yok")

    def _fill_badges(self, row: QHBoxLayout, store: List[QLabel],
                     values: List[str]) -> None:
        """Rozet satırını sıfırla ve yeniden doldur (son eleman esneme payı)."""
        while row.count() > 1:
            entry = row.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        store.clear()

        for value in values[:MAX_BADGES]:
            badge = _badge(value)
            row.insertWidget(row.count() - 1, badge)   # esneme payından önce
            store.append(badge)
        if len(values) > MAX_BADGES:
            # Taşanları gizlemek yerine sayısını göster; tamamı araç ipucunda.
            more = _badge(f"+{len(values) - MAX_BADGES}")
            more.setToolTip(", ".join(values))
            row.insertWidget(row.count() - 1, more)

    # ── Kapak görseli ───────────────────────────────────────────────────────
    def _load_cover(self, rid: int) -> None:
        url = cover_url(self._anime)
        if url:
            run_bg(self._fetch_cover, rid, url)

    def _fetch_cover(self, rid: int, url: str) -> None:
        """Arka plan: görseli indir, baytları UI thread'ine taşı."""
        try:
            import requests
            data = requests.get(url, timeout=10).content
        except Exception:
            return
        if data:
            self.cover_ready.emit(rid, data)

    def _apply_cover(self, rid: int, data: bytes) -> None:
        """GUI thread'i: kapak hâlâ istenen animeye aitse yerleştir."""
        if rid != self._request_id:
            return               # kullanıcı başka animeye geçti
        pix = QPixmap()
        if not pix.loadFromData(data):
            return
        self.lblCover.setPixmap(pix.scaled(
            COVER_W, COVER_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    # ── Kaynak seçimi ───────────────────────────────────────────────────────
    def _select_source(self, source: str) -> None:
        """Kaynağı combo'da seç; listede yoksa ekle.

        `supported_sources()` yalnızca oynatma bağlanmış kaynakları verir; ama
        arama AniList gibi metadata kaynaklarını da döndürebiliyor. Gelen
        kaynağı gizlemek yerine gösterip seçildiğinde net hata veriyoruz.
        """
        if not source:
            return
        index = self.cmbSource.findText(source)
        if index < 0:
            self.cmbSource.addItem(source)
            index = self.cmbSource.findText(source)
        self.cmbSource.setCurrentIndex(index)

    def _on_source_changed(self, source: str) -> None:
        if source in METADATA_ONLY:
            self.lblStatus.info(
                f"{source} yalnızca metadata kaynağı; bölüm için başka kaynak seçin.")

    def current_source(self) -> str:
        return self.cmbSource.currentText()

    # ── Bölüm yükleme ───────────────────────────────────────────────────────
    def load_episodes(self) -> None:
        """Seçili kaynaktan bölümleri arka planda getir."""
        if not self._slug:
            # Keşiften gelen kayıtta kaynak/slug yok (MyAnimeList/AniList kimliği
            # TürkAnime kaynaklarına karşılık gelmiyor). Kullanıcıyı çıkmaza
            # sokmak yerine eşleştirme diyaloğunu başlık dolu açıyoruz.
            self.lblStatus.info("Bu anime bir kaynağa bağlı değil, eşleştirin.")
            self.open_match_dialog()
            if not self._slug:
                return
        source = self.current_source()
        if self._busy:
            self.lblStatus.info("Önceki istek sürüyor, lütfen bekleyin…")
            return
        self._busy = True
        self.btnEpisodes.setEnabled(False)
        self.lblStatus.info(f"{source} bölümleri getiriliyor…")
        run_bg(self._do_load, self._request_id, source, self._slug,
               self._match_title)

    def _do_load(self, rid: int, source: str, slug: str, title: str) -> None:
        """Arka plan thread'i — sonucu KENDİ istek kimliğiyle geri yollar.

        Hatalar burada yakalanır: `run_bg`'nin genel hata sinyali kimlik
        taşımaz, yani eski bir isteğin hatası yeni animenin ekranına düşerdi.
        """
        try:
            episodes = fetch_episodes(source, slug, title)
        except UnsupportedSource as exc:
            self.signals.emit_error_item((rid, str(exc)))
            return
        except Exception as exc:
            self.signals.emit_error_item((rid, f"Bölümler alınamadı: {exc}"))
            return
        self.signals.emit_found((rid, source, slug, title, episodes))

    def _on_episodes(self, payload) -> None:
        """GUI thread'i: sonuç güncel isteğe aitse bölüm sayfasına devret."""
        try:
            rid, source, slug, title, episodes = payload
        except (TypeError, ValueError):
            return
        if rid != self._request_id:
            return               # geç dönen eski istek — ekranı EZMESİN
        self._busy = False
        self.btnEpisodes.setEnabled(True)

        if not episodes:
            self.lblStatus.error(f"{source} kaynağında bölüm bulunamadı.")
            return
        self.lblStatus.ok(f"{len(episodes)} bölüm bulundu.")
        self.episodes_ready.emit(source, slug, title, episodes)

    def _on_failed(self, payload) -> None:
        try:
            rid, message = payload
        except (TypeError, ValueError):
            return
        if rid != self._request_id:
            return
        self._busy = False
        self.btnEpisodes.setEnabled(True)
        self.lblStatus.error(message)

    # ── Eşleşme diyaloğu ────────────────────────────────────────────────────
    def open_match_dialog(self) -> None:
        dialog = AnimeMatchDialog(self._match_title or anime_title(self._anime), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection:
            self.apply_match(*dialog.selection)


__all__ = ["DetailPage", "AnimeMatchDialog", "clean_html", "studio_names",
           "genre_names", "meta_line", "save_match", "MATCH_LIMIT_PER_SOURCE"]
