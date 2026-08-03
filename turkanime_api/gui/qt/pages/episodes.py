"""Bölüm listesi sayfası — çok kaynaklı birleşik liste, seçim, sayfalama.

Eski `common/ui.py::AccordionSourceEpisodeList` davranışının Qt karşılığı:

- Kaynak başına ayrı liste YOK; bütün kaynaklar `(sezon, bölüm)` anahtarıyla
  tek listede birleşir ve her satır o bölümün bulunduğu kaynakların ▶/⬇
  düğmelerini taşır.
- Tembel sayfalama (30'ar), arama filtresi, filtreli toplu seçim.
- Seçim satır widget'ında değil, bölüm anahtarında tutulur: kullanıcı 200
  bölümü seçip "Daha fazla yükle"ye hiç basmadan indirebilmeli (eski GUI'nin
  `get_selected_episodes` davranışı).

Oynatma/indirme mevcut `best_video()` → yt-dlp/mpv boru hattını kullanır, yani
kaynak tarafında hiçbir değişiklik gerekmez.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ....common.episode_parser import merge_episodes
from .. import prefs
from ..sources_bridge import UnsupportedSource, fetch_episodes
from ..widgets import StatusLabel
from ..workers import WorkerSignals, run_bg

PAGE_SIZE = 30

# İzlendi/indirildi rozetleri — eski GUI'deki "izlendi ikonu" ayarı bu ikilinin
# görünürlüğünü yönetiyordu ama hiçbir widget'a bağlanmamıştı.
IKON_IZLENDI = "✓"
IKON_INDIRILDI = "⬇"

# Kaynak kimliği: rozet rengi + iki harfli kısaltma. Satırda kaynak adının
# tamamı sığmıyor; renk + kısaltma ikilisi eski GUI'den birebir taşındı ki
# kullanıcı alışkanlığı bozulmasın.
SOURCE_COLORS = {
    "TürkAnime": "#ffd93d",
    "AnimeciX": "#ff6b6b",
    "Anizle": "#9b59b6",
    "TRAnimeİzle": "#e84393",
    "OpenAnime": "#4ecdc4",
    "Tranimaci": "#0984e3",
    "AnimeDepo": "#e67e22",
}
SOURCE_SHORT = {
    "TürkAnime": "TA",
    "AnimeciX": "CX",
    "Anizle": "AZ",
    "TRAnimeİzle": "TR",
    "OpenAnime": "OA",
    "Tranimaci": "TC",
    "AnimeDepo": "AD",
}
DEFAULT_SOURCE_COLOR = "#7f8c8d"


# ── Saf yardımcılar (Qt'siz; doğrudan test edilebilir) ──────────────────────
def source_short(name: str) -> str:
    """Kaynağın iki harfli rozet metni (bilinmeyen kaynak → ilk iki harf)."""
    return SOURCE_SHORT.get(name) or (name or "?")[:2].upper()


def source_color(name: str) -> str:
    return SOURCE_COLORS.get(name, DEFAULT_SOURCE_COLOR)


def as_sources_data(source: str, episodes: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Gelen yükü daima ``{kaynak: [bölüm, ...]}`` şekline getir.

    Detay sayfası çok kaynaklı yüklemede sözlük, tek kaynaklı eski akışta
    (ve testlerde) düz liste veriyor. İki şekli burada tek noktada
    normalleştirmek, listenin geri kalanını şekilden bağımsız kılıyor.
    """
    if isinstance(episodes, dict):
        return {
            str(name): [e for e in (items or []) if isinstance(e, dict)]
            for name, items in episodes.items()
        }
    items = [e for e in (episodes or []) if isinstance(e, dict)]
    return {source or "Kaynak": items}


def active_sources(sources_data: Dict[str, Sequence[Dict[str, Any]]]) -> List[str]:
    """Gerçekten bölüm döndüren kaynaklar (boş dönenler listede yer tutmaz)."""
    return [name for name, items in (sources_data or {}).items() if items]


def episode_matches(episode: Dict[str, Any], needle: str) -> bool:
    """Arama filtresi — hem başlık hem bölüm numarası üzerinden.

    Kullanıcı çoğunlukla numara yazıyor ("12"); yalnız başlığa bakmak
    "S02E12" gibi normalize edilmiş satırlarda da çalışır ama "Bölüm 12"
    yazan kaynaklarda numarayı ayrıca aramak daha isabetli sonuç veriyor.
    """
    if not needle:
        return True
    title = str(episode.get("title") or "").lower()
    number = str(episode.get("number") or "")
    if needle in title:
        return True
    return number.startswith(needle) if needle.isdigit() else needle in number


def primary_entry(episode: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Bölümün "varsayılan" kaynak kaydı — ilk yüklenen kaynak kazanır."""
    for entry in (episode.get("sources") or {}).values():
        if entry:
            return entry
    return None


def source_counts(episodes: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Verilen bölümlerde kaynak başına kaç bölüm bulunduğu."""
    counts: Dict[str, int] = {}
    for episode in episodes:
        for name, entry in (episode.get("sources") or {}).items():
            if entry:
                counts[name] = counts.get(name, 0) + 1
    return counts


# ── Kaynak seçim diyaloğu ───────────────────────────────────────────────────
class SourceSelectDialog(QDialog):
    """Toplu indirmede "hangi kaynaktan?" sorusu.

    Eski GUI'deki `show_source_selection_dialog` karşılığı. Tek kaynak varsa
    diyalog hiç açılmaz (bkz. `EpisodePage._pick_download_source`); sormak
    kullanıcıya seçenek değil, fazladan tık demek olurdu.
    """

    def __init__(self, counts: Dict[str, int], total: int,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Kaynak Seçimi")
        self.selection: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        head = QLabel("Hangi kaynaktan indirilsin?")
        head.setObjectName("Subtitle")
        layout.addWidget(head)

        info = QLabel(f"{total} bölüm seçili")
        info.setObjectName("Muted")
        layout.addWidget(info)

        self.buttons: Dict[str, QPushButton] = {}
        for name in sorted(counts, key=lambda n: (-counts[n], n)):
            btn = QPushButton(f"{name} — {counts[name]}/{total} bölüm")
            btn.setStyleSheet(
                f"text-align: left; border-left: 4px solid {source_color(name)};")
            btn.clicked.connect(lambda _=False, n=name: self._choose(n))
            layout.addWidget(btn)
            self.buttons[name] = btn

        cancel = QPushButton("İptal")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

    def _choose(self, name: str) -> None:
        self.selection = name
        self.accept()


# ── Satır ───────────────────────────────────────────────────────────────────
class EpisodeRow(QFrame):
    """Tek bölüm satırı: seçim kutusu + başlık + kaynak başına aksiyonlar."""

    play_requested = Signal(object)
    download_requested = Signal(object)
    toggled = Signal(object, bool)          # (bölüm anahtarı, seçili mi)

    def __init__(self, episode: Dict[str, Any], multi: bool = False,
                 gecmis: Optional[Any] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.episode = episode
        self.sources: Dict[str, Dict[str, Any]] = dict(episode.get("sources") or {})
        self.key: Tuple[int, int] = (int(episode.get("season") or 1),
                                     int(episode.get("number") or 0))
        # Filtre durumunu AÇIKÇA tut: `isVisible()` ata widget'ların görünürlüğüne
        # bağlı olduğu için seçim/filtre mantığının kaynağı olamaz.
        self.filtered_out = False
        self.izlendi = False
        self.indirildi = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        self.chk = QCheckBox()
        self.chk.toggled.connect(lambda state: self.toggled.emit(self.key, state))
        layout.addWidget(self.chk)

        self.lblHistory = QLabel()
        self.lblHistory.setFixedWidth(28)
        self.lblHistory.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lblHistory.setVisible(False)
        layout.addWidget(self.lblHistory)

        self.lbl = QLabel(str(episode.get("title") or ""))
        self.lbl.setWordWrap(False)
        layout.addWidget(self.lbl, 1)

        self.btnPlay: Optional[QPushButton] = None
        self.btnDl: Optional[QPushButton] = None
        self.source_buttons: Dict[str, Tuple[QPushButton, QPushButton]] = {}

        # `multi` listenin tamamına bakar, satıra değil: çok kaynaklı bir listede
        # tek kaynakta bulunan bölüm de rozet göstermeli, yoksa kullanıcı o
        # satırın hangi kaynaktan geldiğini bilemez.
        if multi or len(self.sources) > 1:
            self._build_multi(layout)
        else:
            self._build_single(layout)

        self.setToolTip(self._tooltip())
        self.apply_history(gecmis)

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_single(self, layout: QHBoxLayout) -> None:
        """Tek kaynak: rozet/kısaltma gürültüsü olmadan sade görünüm."""
        name = next(iter(self.sources), "")
        entry = self.sources.get(name)

        self.btnPlay = QPushButton("Oynat")
        self.btnPlay.clicked.connect(lambda: self._emit_play(name))
        layout.addWidget(self.btnPlay)

        self.btnDl = QPushButton("İndir")
        self.btnDl.setObjectName("Primary")
        self.btnDl.clicked.connect(lambda: self._emit_download(name))
        layout.addWidget(self.btnDl)

        enabled = bool(entry)
        self.btnPlay.setEnabled(enabled)
        self.btnDl.setEnabled(enabled)
        if name:
            self.source_buttons[name] = (self.btnPlay, self.btnDl)

    def _build_multi(self, layout: QHBoxLayout) -> None:
        """Çok kaynak: kaynak başına renkli kısaltma + ▶/⬇ ikilisi."""
        for name in sorted(self.sources):
            badge = QLabel(source_short(name))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedWidth(26)
            badge.setToolTip(name)
            badge.setStyleSheet(
                f"background: {source_color(name)}; color: #111111;"
                "border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 0;"
            )
            layout.addWidget(badge)

            play = QPushButton("▶")
            play.setFixedWidth(30)
            play.setToolTip(f"{name} — oynat")
            play.clicked.connect(lambda _=False, n=name: self._emit_play(n))
            layout.addWidget(play)

            download = QPushButton("⬇")
            download.setFixedWidth(30)
            download.setToolTip(f"{name} — indir")
            download.clicked.connect(lambda _=False, n=name: self._emit_download(n))
            layout.addWidget(download)

            self.source_buttons[name] = (play, download)

    def _tooltip(self) -> str:
        names = ", ".join(sorted(self.sources)) or "kaynak yok"
        return f"{self.episode.get('title') or ''}\nKaynaklar: {names}"

    # ── Geçmiş rozeti ───────────────────────────────────────────────────────
    def apply_history(self, gecmis: Optional[Any]) -> None:
        """İzlendi/indirildi rozetini tazele.

        Bir bölüm birden çok kaynakta olabiliyor; herhangi birinden izlendiyse
        satır izlenmiş sayılır (kullanıcı hangi kaynaktan açtığını hatırlamaz).
        `gecmis` None ise ("izlendi ikonu" kapalı) rozet hiç çizilmez.
        """
        if gecmis is None:
            self.izlendi = self.indirildi = False
            self.lblHistory.setVisible(False)
            return
        izlendi = indirildi = False
        for entry in self.sources.values():
            watched, downloaded = gecmis.durum((entry or {}).get("obj"))
            izlendi = izlendi or watched
            indirildi = indirildi or downloaded
        self.izlendi, self.indirildi = izlendi, indirildi

        parcalar = []
        if izlendi:
            parcalar.append(IKON_IZLENDI)
        if indirildi:
            parcalar.append(IKON_INDIRILDI)
        self.lblHistory.setText("".join(parcalar))
        self.lblHistory.setToolTip(
            " · ".join(n for n, v in (("izlendi", izlendi), ("indirildi", indirildi)) if v))
        self.lblHistory.setStyleSheet(
            "color: #00b894; font-weight: 700;" if izlendi else "color: #74b9ff;")
        self.lblHistory.setVisible(bool(parcalar))

    # ── Aksiyonlar ──────────────────────────────────────────────────────────
    def _emit_play(self, name: str) -> None:
        entry = self.sources.get(name)
        if entry:
            self.play_requested.emit(entry)

    def _emit_download(self, name: str) -> None:
        entry = self.sources.get(name)
        if entry:
            self.download_requested.emit(entry)

    # ── Sorgular ────────────────────────────────────────────────────────────
    @property
    def entry(self) -> Optional[Dict[str, Any]]:
        """Varsayılan kaynak kaydı (tek kaynaklı akışlarla uyum için)."""
        return primary_entry(self.episode)

    def entry_for(self, source: Optional[str]) -> Optional[Dict[str, Any]]:
        """İstenen kaynağın kaydı; o kaynakta yoksa varsayılana düş."""
        if source:
            entry = self.sources.get(source)
            if entry:
                return entry
        return self.entry

    @property
    def checked(self) -> bool:
        return self.chk.isChecked()

    def set_checked(self, value: bool) -> None:
        self.chk.setChecked(value)

    def matches(self, needle: str) -> bool:
        return episode_matches(self.episode, needle)


# ── Sayfa ───────────────────────────────────────────────────────────────────
class EpisodePage(QWidget):
    """Seçilen animenin bölümlerini (tüm kaynaklar birleşik) listeler."""

    play_requested = Signal(object)
    download_requested = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sources: Dict[str, List[Dict[str, Any]]] = {}
        self._all: List[Dict[str, Any]] = []
        self._rows: List[EpisodeRow] = []
        # Seçim satırda değil ANAHTARDA: yüklenmemiş sayfalardaki bölümler de
        # "Tümünü Seç" ile seçilebilmeli (eski GUI davranışı).
        self._selected: set = set()
        self._needle = ""
        self._shown = 0
        self._busy = False
        self._context = ("", "", "")
        # Geçmiş tek seferde okunur; satır başına dosya açmak birkaç yüz
        # bölümlük listede gözle görülür gecikme demek.
        self._gecmis: Optional[prefs.Gecmis] = None

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

        self.lblSources = QLabel("")
        self.lblSources.setObjectName("Muted")
        self.lblSources.setVisible(False)
        layout.addWidget(self.lblSources)

        tools = QHBoxLayout()
        self.txtFilter = QLineEdit()
        self.txtFilter.setPlaceholderText("Bölüm ara…")
        self.txtFilter.setClearButtonEnabled(True)
        self.txtFilter.textChanged.connect(self._apply_filter)
        tools.addWidget(self.txtFilter, 1)

        self.lblSelected = QLabel("0 seçili")
        self.lblSelected.setObjectName("Muted")
        tools.addWidget(self.lblSelected)

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
    def load(self, source: str, slug: str, title: str,
             episodes: Optional[Any] = None) -> None:
        """Bölüm listesini göster.

        `episodes` verilirse ağa ÇIKILMAZ: detay sayfası listeyi zaten çekmiş
        olur ve ikinci çağrı hem gereksiz beklemedir hem de bazı kaynaklarda
        (TRAnimeİzle, Tranimaci) yeniden bot-koruma turu tetikler. Yük hem düz
        liste (tek kaynak) hem `{kaynak: liste}` sözlüğü (çok kaynak) olabilir.
        """
        if episodes is None and self._busy:
            self.lblStatus.info("Önceki istek sürüyor, lütfen bekleyin…")
            return
        self._busy = episodes is None
        # Yeni anime: seçim durumunu ve "Tümünü Seç" etiketini sıfırla, aksi
        # hâlde buton "Seçimi Kaldır" derken hiçbir satır seçili olmaz.
        self.btnAll.setChecked(False)
        self.txtFilter.clear()
        self._selected.clear()
        self._needle = ""
        self._context = (source, slug, title)
        self._reload_gecmis()
        self.lblTitle.setText(f"{title} — {source}")
        self.lblSources.setVisible(False)
        self._clear_rows()
        self.btnMore.hide()
        if episodes is not None:
            self._on_episodes(episodes)
            return
        self.lblStatus.info("Bölümler getiriliyor…")
        run_bg(self._do_load, source, slug, title, signals=self.signals)

    def _do_load(self, source: str, slug: str, title: str) -> None:
        try:
            episodes = fetch_episodes(source, slug, title)
        except UnsupportedSource as exc:
            self.signals.emit_error(str(exc))
            return
        self.signals.emit_found(episodes)

    def _on_episodes(self, episodes: Any) -> None:
        self._busy = False
        source, _slug, title = self._context
        self._sources = as_sources_data(source, episodes)
        self._all = merge_episodes(self._sources)
        self._selected.clear()

        names = active_sources(self._sources)
        if len(names) > 1:
            self.lblTitle.setText(f"{title} — {len(names)} kaynak")
            self.lblSources.setText("Kaynaklar: " + ", ".join(names))
            self.lblSources.setVisible(True)
        else:
            self.lblSources.setVisible(False)

        if not self._all:
            self.lblStatus.error("Bu kaynakta bölüm bulunamadı.")
            self._update_selection_label()
            return
        self._shown = 0
        self._load_more()
        suffix = f" • {len(names)} kaynak" if len(names) > 1 else ""
        self.lblStatus.ok(f"{len(self._all)} bölüm{suffix}")
        self._update_selection_label()

    def _on_error(self, message: str) -> None:
        self._busy = False
        self.lblStatus.error(message)

    # ── Geçmiş ──────────────────────────────────────────────────────────────
    def _reload_gecmis(self) -> None:
        """Geçmişi diskten oku (ayar kapalıysa rozet hiç çizilmesin diye None)."""
        self._gecmis = prefs.Gecmis.yukle() if prefs.oku().izlendi_ikonu else None

    def refresh_history(self) -> None:
        """Oynatma/indirme bitince rozetleri tazele (GUI thread'inden)."""
        self._reload_gecmis()
        for row in self._rows:
            row.apply_history(self._gecmis)

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
        multi = len(active_sources(self._sources)) > 1
        for episode in chunk:
            row = EpisodeRow(episode, multi=multi, gecmis=self._gecmis)
            row.play_requested.connect(self.play_requested.emit)
            row.download_requested.connect(self.download_requested.emit)
            row.toggled.connect(self._on_row_toggled)
            # Satır sonradan yüklendiyse seçim zaten anahtarda olabilir.
            if row.key in self._selected:
                row.set_checked(True)
            self._list.addWidget(row)
            self._rows.append(row)
        self._shown += len(chunk)
        self.btnMore.setVisible(self._shown < len(self._all))
        self.btnMore.setText(
            f"Daha fazla yükle ({len(self._all) - self._shown} kaldı)"
        )
        self._apply_filter(self.txtFilter.text())

    def _apply_filter(self, text: str) -> None:
        self._needle = (text or "").strip().lower()
        for row in self._rows:
            row.filtered_out = bool(self._needle) and not row.matches(self._needle)
            row.setVisible(not row.filtered_out)
        self._update_selection_label()

    def _toggle_all(self, checked: bool) -> None:
        """Filtreden geçen TÜM bölümleri seç/bırak (yüklenmemişler dahil)."""
        self.btnAll.setText("Seçimi Kaldır" if checked else "Tümünü Seç")
        for episode in self.filtered_episodes():
            key = _key_of(episode)
            if checked:
                self._selected.add(key)
            else:
                self._selected.discard(key)
        for row in self._rows:
            if not row.filtered_out:
                row.set_checked(checked)
        self._update_selection_label()

    def _on_row_toggled(self, key, checked: bool) -> None:
        if checked:
            self._selected.add(tuple(key))
        else:
            self._selected.discard(tuple(key))
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        self.lblSelected.setText(f"{len(self.selected_episodes())} seçili")

    # ── Sorgular ────────────────────────────────────────────────────────────
    def visible_rows(self) -> List[EpisodeRow]:
        """Filtreden geçen satırlar (Qt görünürlüğünden bağımsız)."""
        return [r for r in self._rows if not r.filtered_out]

    def filtered_episodes(self) -> List[Dict[str, Any]]:
        """Filtreden geçen birleşik bölümler — henüz yüklenmemişler dahil."""
        if not self._needle:
            return list(self._all)
        return [e for e in self._all if episode_matches(e, self._needle)]

    def selected_episodes(self) -> List[Dict[str, Any]]:
        """Seçili VE filtreden geçen birleşik bölümler.

        Filtreyi de dikkate almak şart: `_toggle_all` yalnızca filtreden geçen
        bölümleri işaretliyor; burada filtreyi yok sayarsak kullanıcı 200 bölümü
        seçip sonra "12" diye filtreleyince yine 200 bölüm indirilir.
        """
        return [e for e in self.filtered_episodes() if _key_of(e) in self._selected]

    def selected_entries(self, source: Optional[str] = None) -> List[Dict[str, Any]]:
        """Seçili bölümlerin kaynak kayıtları (istenirse belirli kaynaktan)."""
        out: List[Dict[str, Any]] = []
        for episode in self.selected_episodes():
            entry = (episode.get("sources") or {}).get(source) if source else None
            entry = entry or primary_entry(episode)
            if entry:
                out.append(entry)
        return out

    def available_sources(self) -> List[str]:
        """Seçili bölümlerde bulunan kaynaklar."""
        return sorted(source_counts(self.selected_episodes()))

    # ── Toplu indirme ───────────────────────────────────────────────────────
    def _download_selected(self) -> None:
        picked = self.selected_episodes()
        if not picked:
            self.lblStatus.error("Önce en az bir bölüm seçin.")
            return

        counts = source_counts(picked)
        if not counts:
            self.lblStatus.error("Seçili bölümlerin indirilebilir kaynağı yok.")
            return

        source = self._pick_download_source(counts, len(picked))
        if source is None:
            self.lblStatus.info("İndirme iptal edildi.")
            return

        # Seçilen kaynakta olmayan bölümler SESSİZCE başka kaynaktan indirilmez:
        # kullanıcı bilerek bir kaynak seçti, kaçını alamadığımızı söylüyoruz.
        entries = [e["sources"][source] for e in picked
                   if (e.get("sources") or {}).get(source)]
        missing = len(picked) - len(entries)
        message = f"{len(entries)} bölüm indirme sırasına alındı ({source})."
        if missing:
            message += f" {missing} bölüm bu kaynakta yok, atlandı."
        self.lblStatus.info(message)
        for entry in entries:
            self.download_requested.emit(entry)

    def _pick_download_source(self, counts: Dict[str, int],
                              total: int) -> Optional[str]:
        """Tek kaynak varsa sorma; çok kaynakta kullanıcıya seçtir."""
        if len(counts) == 1:
            return next(iter(counts))
        return self._ask_source(counts, total)

    def _ask_source(self, counts: Dict[str, int], total: int) -> Optional[str]:
        """Kaynak seçim diyaloğunu aç (testlerde sahtelenir)."""
        dialog = SourceSelectDialog(counts, total, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.selection
        return None


def _key_of(episode: Dict[str, Any]) -> Tuple[int, int]:
    return (int(episode.get("season") or 1), int(episode.get("number") or 0))


__all__ = ["EpisodePage", "EpisodeRow", "SourceSelectDialog", "as_sources_data",
           "active_sources", "episode_matches", "primary_entry", "source_counts",
           "source_short", "source_color"]
