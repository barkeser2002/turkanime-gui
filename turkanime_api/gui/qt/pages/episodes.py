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

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ....common.episode_parser import merge_episodes
from ....sources import kayit
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
# kullanıcı alışkanlığı bozulmasın. Değerler `sources/kayit.py`'de: eskiden
# burada elle tutuluyordu ve yeni kaynak eklendiğinde unutuluyordu (rozet gri
# kalıyordu). "TürkAnime" artık arşiv; eski "AnimeDepo" adıyla gelen satır da
# aynı rozeti alır (bkz. `source_short`).
SOURCE_COLORS = {k.ad: k.renk for k in kayit.kaynaklar()}
SOURCE_SHORT = {k.ad: k.kisaltma for k in kayit.kaynaklar()}
DEFAULT_SOURCE_COLOR = "#7f8c8d"


# ── Saf yardımcılar (Qt'siz; doğrudan test edilebilir) ──────────────────────
def source_short(name: str) -> str:
    """Kaynağın iki harfli rozet metni (bilinmeyen kaynak → ilk iki harf)."""
    return (SOURCE_SHORT.get(name) or SOURCE_SHORT.get(kayit.kanonik_ad(name))
            or (name or "?")[:2].upper())


def source_color(name: str) -> str:
    return (SOURCE_COLORS.get(name) or SOURCE_COLORS.get(kayit.kanonik_ad(name))
            or DEFAULT_SOURCE_COLOR)


def source_label(name: str) -> str:
    """Kaynağın kullanıcıya görünen adı ("TürkAnime" → "TürkAnime (arşiv)").

    Sayfa içinde kaynaklar KANONİK adla anahtarlı (satır düğmeleri, seçim,
    indirme sırası); yalnızca ekrana basılan metin etikete çevrilir. Arama
    kartı ve detay sayfası da aynı `kayit.gorunen_ad`'ı kullanıyor: kullanıcı
    turkanime.tv'nin kapandığını ve verinin arşivden geldiğini her yerde aynı
    adla görsün.
    """
    return kayit.gorunen_ad(name)


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


class AralikSonucu(set):
    """`aralik_coz`'un dönüşü: seçilen numaralar (set) + ``hatali`` parçalar.

    Set alt sınıfı: çağıran sonucu doğrudan küme olarak kullanır; tanınmayan
    parçalar ("abc", "5-2") istisna değil ``hatali``'da, kullanıcıya
    "şunları anlayamadım" denebilsin diye.
    """

    def __init__(self, *args: Any, hatali: Optional[List[str]] = None):
        super().__init__(*args)
        self.hatali: List[str] = list(hatali or [])


_ARALIK_PARCASI = re.compile(r"^(\d+)?\s*(-)?\s*(\d+)?$")


def aralik_coz(metin: str, mevcut: Iterable[int]) -> AralikSonucu:
    """"1-12, 15, 20-" → mevcut bölüm numaralarından seçilenler.

    Sözdizimi: virgül/boşlukla ayrılmış parçalar; "N" tek bölüm, "A-B"
    kapalı aralık, "A-" A'dan sona, "-B" baştan B'ye. Numara listede yoksa
    (sayfalanmış seri, eksik bölüm) sessizce atlanır: kullanıcı "1-12" der,
    3. bölüm hiç yayınlanmadıysa gerisi yine seçilmeli.

    Filtre kutusu bunu yapamıyordu: numara eşleşmesi ÖNEK ("1" → 1, 10-19,
    100…), 1-12'yi seçmek 12 tıklama demekti.
    """
    numaralar = sorted({int(n) for n in mevcut})
    secilen: Set[int] = set()
    hatali: List[str] = []
    # "1 - 12" de yazılıyor: rakamlar arasındaki tireye yapışık boşluklar
    # silinir, sonra virgül/boşlukla bölünür.
    duz = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", (metin or "").replace("–", "-"))
    duz = re.sub(r"(\d)\s+-(?=[,;\s]|$)", r"\1-", duz)          # "20 -"
    for parca in re.split(r"[,;\s]+", duz):
        if not parca:
            continue
        eslesme = _ARALIK_PARCASI.match(parca)
        if not eslesme or not (eslesme.group(1) or eslesme.group(3)):
            hatali.append(parca)
            continue
        bas, tire, son = eslesme.groups()
        if not tire:
            alt = ust = int(bas or son)
        else:
            alt = int(bas) if bas else (numaralar[0] if numaralar else 0)
            ust = int(son) if son else (numaralar[-1] if numaralar else alt)
        if alt > ust:
            hatali.append(parca)
            continue
        secilen.update(n for n in numaralar if alt <= n <= ust)
    return AralikSonucu(secilen, hatali=hatali)


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


class _SecimKutusu(QCheckBox):
    """Tıklamanın/tuşun Shift ile yapılıp yapılmadığını hatırlayan onay kutusu.

    Değer olayın KENDİSİNDEN okunuyor, `QApplication.keyboardModifiers()`'tan
    değil: o, olay kuyruğu işlenirken güncelleniyor ve sentetik (QTest) ya
    da uzak masaüstü tıklamalarında Shift'i kaçırabiliyor. `toggled` sinyali
    bırakma olayının içinde yayıldığı için yayın anında değer doğru.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.shift_basili = False

    def mouseReleaseEvent(self, event) -> None:          # noqa: N802 (Qt adı)
        self.shift_basili = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        try:
            super().mouseReleaseEvent(event)
        finally:
            self.shift_basili = False

    def keyReleaseEvent(self, event) -> None:            # noqa: N802 (Qt adı)
        self.shift_basili = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        try:
            super().keyReleaseEvent(event)
        finally:
            self.shift_basili = False


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
            btn = QPushButton(f"{source_label(name)} — {counts[name]}/{total} bölüm")
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
    # Shift ile işaretlendi: sayfa son işaretlenen satırdan buraya kadar
    # hepsini aynı duruma getirir (dosya yöneticilerindeki aralık seçimi).
    aralik_toggled = Signal(object, bool)

    def __init__(self, episode: Dict[str, Any], multi: bool = False,
                 gecmis: Optional[Any] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.episode = episode
        self.sources: Dict[str, Dict[str, Any]] = dict(episode.get("sources") or {})
        self.key: Tuple[int, int, int] = _key_of(episode)
        # Filtre durumunu AÇIKÇA tut: `isVisible()` ata widget'ların görünürlüğüne
        # bağlı olduğu için seçim/filtre mantığının kaynağı olamaz.
        self.filtered_out = False
        self.izlendi = False
        self.indirildi = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        self.chk = _SecimKutusu()
        self.chk.toggled.connect(self._on_chk)
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
            badge.setToolTip(source_label(name))
            badge.setStyleSheet(
                f"background: {source_color(name)}; color: #111111;"
                "border-radius: 4px; font-size: 10px; font-weight: 700; padding: 2px 0;"
            )
            layout.addWidget(badge)

            play = QPushButton("▶")
            play.setFixedWidth(30)
            play.setToolTip(f"{source_label(name)} — oynat")
            play.clicked.connect(lambda _=False, n=name: self._emit_play(n))
            layout.addWidget(play)

            download = QPushButton("⬇")
            download.setFixedWidth(30)
            download.setToolTip(f"{source_label(name)} — indir")
            download.clicked.connect(lambda _=False, n=name: self._emit_download(n))
            layout.addWidget(download)

            self.source_buttons[name] = (play, download)

    def _tooltip(self) -> str:
        names = ", ".join(source_label(n) for n in sorted(self.sources)) or "kaynak yok"
        return f"{self.episode.get('title') or ''}\nKaynaklar: {names}"

    def _on_chk(self, state: bool) -> None:
        if self.chk.shift_basili:
            self.aralik_toggled.emit(self.key, state)
        else:
            self.toggled.emit(self.key, state)

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

    def sessiz_isaretle(self, value: bool) -> None:
        """Seçimi sinyal yaymadan göster (sayfa kümeyi zaten güncelledi)."""
        self.chk.blockSignals(True)
        try:
            self.chk.setChecked(value)
        finally:
            self.chk.blockSignals(False)

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
        # Shift aralığının başlangıcı: son işaretlenen/bırakılan satırın anahtarı.
        self._son_anahtar: Optional[Tuple[int, int, int]] = None
        self._needle = ""
        self._shown = 0
        self._busy = False
        self._context = ("", "", "")
        # Kaynak → kaynağın KENDİ anime kimliği ve kapak (kitaplık kaydı için;
        # bkz. `_kimlik_damgala`).
        self._baglar: Dict[str, str] = {}
        self._kapak = ""
        # Geçmiş tek seferde okunur; satır başına dosya açmak birkaç yüz
        # bölümlük listede gözle görülür gecikme demek.
        self._gecmis: Optional[prefs.Gecmis] = None
        # "Bu bölüm zaten indirme kuyruğunda mı?" — kuyruğu ana pencere
        # tutuyor ve buraya bağlıyor. Sayfa artık indirmeden sonra açık
        # kaldığı için ikinci "Seçilenleri İndir" aynı bölümleri yeniden
        # gönderebiliyor; kaçının atlandığını kullanıcı burada görmeli.
        self.kuyrukta_mi: Callable[[Dict[str, Any]], bool] = lambda _entry: False

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
        # "▶ Devam et: 5. Bölüm (12:14)" / "▶ Sıradaki: 6. Bölüm" — hedefi
        # `devam_hedefi` belirler; hedef yoksa düğme gizli.
        self.btnDevam = QPushButton("")
        self.btnDevam.setObjectName("Primary")
        self.btnDevam.clicked.connect(self._devam_oynat)
        self.btnDevam.hide()
        head.addWidget(self.btnDevam)
        layout.addLayout(head)
        self._devam_kaydi: Optional[Dict[str, Any]] = None

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

        # Toplu seçim: aralık + hızlı süzgeçler. 1-12'yi seçmek 12 tıklama,
        # "izlemediklerim"i indirmek satır satır bakmak demekti.
        secim = QHBoxLayout()
        self.txtAralik = QLineEdit()
        self.txtAralik.setPlaceholderText("Aralık seç: 1-12, 15, 20-")
        self.txtAralik.setToolTip(
            "Bölüm numaraları: “1-12” aralık, “20-” 20'den sona, “-5” baştan 5'e; "
            "virgülle birleştirin. Enter seçer. Satırda Shift+tık da aralık seçer.")
        self.txtAralik.returnPressed.connect(
            lambda: self.select_range(self.txtAralik.text()))
        secim.addWidget(self.txtAralik, 1)
        self.btnAralik = QPushButton("Aralığı Seç")
        self.btnAralik.clicked.connect(
            lambda: self.select_range(self.txtAralik.text()))
        secim.addWidget(self.btnAralik)
        self.btnIzlenmemis = QPushButton("İzlenmemişler")
        self.btnIzlenmemis.setToolTip("İzlendi işareti olmayan bölümleri seç.")
        self.btnIzlenmemis.clicked.connect(self.select_unwatched)
        secim.addWidget(self.btnIzlenmemis)
        self.btnIndirilmemis = QPushButton("İndirilmemişler")
        self.btnIndirilmemis.setToolTip(
            "İndirilmemiş ve indirme kuyruğunda olmayan bölümleri seç.")
        self.btnIndirilmemis.clicked.connect(self.select_not_downloaded)
        secim.addWidget(self.btnIndirilmemis)
        layout.addLayout(secim)

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
             episodes: Optional[Any] = None,
             baglar: Optional[Dict[str, str]] = None, kapak: str = "") -> None:
        """Bölüm listesini göster.

        `episodes` verilirse ağa ÇIKILMAZ: detay sayfası listeyi zaten çekmiş
        olur ve ikinci çağrı hem gereksiz beklemedir hem de bazı kaynaklarda
        (TRAnimeİzle, Tranimaci) yeniden bot-koruma turu tetikler. Yük hem düz
        liste (tek kaynak) hem `{kaynak: liste}` sözlüğü (çok kaynak) olabilir.

        ``baglar`` çok kaynaklı listede her kaynağın kendi kimliği (detay
        sayfasının bağları); ``kapak`` kitaplık kartının posteri.
        """
        if episodes is None and self._busy:
            self.lblStatus.info("Önceki istek sürüyor, lütfen bekleyin…")
            return
        self._busy = episodes is None
        # Yeni anime: seçim durumunu ve "Tümünü Seç" etiketini sıfırla, aksi
        # hâlde buton "Seçimi Kaldır" derken hiçbir satır seçili olmaz.
        self.btnAll.setChecked(False)
        self.txtFilter.clear()
        self.txtAralik.clear()
        self._selected.clear()
        self._son_anahtar = None
        self._needle = ""
        self._context = (source, slug, title)
        self._baglar = {str(k): str(v) for k, v in (baglar or {}).items() if v}
        self._kapak = str(kapak or "")
        self._reload_gecmis()
        self.lblTitle.setText(f"{title} — {source_label(source)}")
        self.lblSources.setVisible(False)
        self._clear_rows()
        self.btnMore.hide()
        self._devam_kaydi = None          # önceki animenin hedefi kalmasın
        self.btnDevam.hide()
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
        source, slug, title = self._context
        self._sources = as_sources_data(source, episodes)
        self._kimlik_damgala(source, slug, title)
        # Anime adı birleştiriciye veriliyor: kaynaklar başlığa adı da yazıyor
        # ("86 2nd Season 5. Bölüm") ve addaki rakamlar bölüm/sezon sanılırsa
        # aynı bölüm kaynak başına ayrı satır olur.
        self._all = merge_episodes(self._sources, title)
        self._selected.clear()

        names = active_sources(self._sources)
        if len(names) > 1:
            self.lblTitle.setText(f"{title} — {len(names)} kaynak")
            self.lblSources.setText("Kaynaklar: " + ", ".join(source_label(n) for n in names))
            self.lblSources.setVisible(True)
        else:
            self.lblSources.setVisible(False)

        if not self._all:
            self.lblStatus.error("Bu kaynakta bölüm bulunamadı.")
            self._update_selection_label()
            return
        self._shown = 0
        self._load_more()
        self._devam_guncelle()
        suffix = f" • {len(names)} kaynak" if len(names) > 1 else ""
        self.lblStatus.ok(f"{len(self._all)} bölüm{suffix}")
        self._update_selection_label()

    def _on_error(self, message: str) -> None:
        self._busy = False
        self.lblStatus.error(message)

    def _kimlik_damgala(self, source: str, slug: str, title: str) -> None:
        """Her kaynak kaydına kitaplığın anahtarını iliştir.

        Oynatma/indirme yalnızca bu kaydı (`entry`) taşıyor. Kimlik bölüm
        nesnesinden TÜRETİLEMEZ: `AdapterAnime` sayısal kimliği (AnimeciX
        "1234") başlık slug'ına çeviriyor ve kitaplık o slug'la seriyi bir
        daha açamazdı. Çok kaynaklı listede her satır tıklanan KAYNAĞIN
        kimliğini alır; birincil kaynağın slug'ı yalnızca kendisine aittir.
        """
        for name, items in self._sources.items():
            kimlik = self._baglar.get(name) or (slug if name == source else "")
            for entry in items:
                entry["kaynak"] = name
                entry["kimlik"] = kimlik
                entry["seri_adi"] = title
                entry["kapak"] = self._kapak

    # ── Geçmiş ──────────────────────────────────────────────────────────────
    def _reload_gecmis(self) -> None:
        """Geçmişi diskten oku (ayar kapalıysa rozet hiç çizilmesin diye None)."""
        self._gecmis = prefs.Gecmis.yukle() if prefs.oku().izlendi_ikonu else None

    def refresh_history(self) -> None:
        """Oynatma/indirme bitince rozetleri tazele (GUI thread'inden)."""
        self._reload_gecmis()
        for row in self._rows:
            row.apply_history(self._gecmis)
        self._devam_guncelle()

    # ── Devam et / Sıradaki ─────────────────────────────────────────────────
    def devam_hedefi(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """``(düğme metni, oynatılacak kayıt)`` ya da None.

        Önce yarıda bırakılan bölüm (kitaplıkta konumu olan, en son izlenen):
        kullanıcı büyük olasılıkla onu bitirmek istiyor. Yoksa en ilerideki
        izlenen bölümün ardı (`kutuphane.sonraki_bolum`); hiç izlenmemiş ya
        da bitmiş seride düğme yok — "Sıradaki: 1. Bölüm" gürültü olurdu.
        Çok kaynaklı satırda bölüm herhangi bir kaynaktan izlendiyse izlenmiş
        sayılır (rozetle aynı kural).
        """
        from ....common import kutuphane
        if not self._all:
            return None
        veri = kutuphane.oku()
        yarimlar: List[Tuple[float, Dict[str, Any], Dict[str, Any], Any]] = []
        for episode in self._all:
            for name, entry in (episode.get("sources") or {}).items():
                if not entry or not entry.get("kimlik"):
                    continue
                _seri, slug = prefs.bolum_kimligi(entry.get("obj"))
                konum = kutuphane.konum_getir(name, entry["kimlik"], slug, veri)
                if konum:
                    yarimlar.append((float(konum.get("zaman") or 0), episode,
                                     entry, konum.get("konum")))
        if yarimlar:
            _zaman, episode, entry, saniye = max(yarimlar, key=lambda y: y[0])
            return (f"▶ Devam et: {episode.get('title') or 'bölüm'} "
                    f"({kutuphane.sure_metni(saniye)})", entry)

        gecmis = self._gecmis or prefs.Gecmis.yukle()
        izlenen = [i for i, episode in enumerate(self._all)
                   if any(gecmis.durum((e or {}).get("obj"))[0]
                          for e in (episode.get("sources") or {}).values() if e)]
        if not izlenen:
            return None
        sira = kutuphane.sonraki_bolum(range(len(self._all)), izlenen)
        if sira is None:
            return None
        episode = self._all[sira]
        entry = primary_entry(episode)
        if entry is None:
            return None
        return f"▶ Sıradaki: {episode.get('title') or 'bölüm'}", entry

    def _devam_guncelle(self) -> None:
        hedef = self.devam_hedefi()
        self._devam_kaydi = hedef[1] if hedef else None
        self.btnDevam.setText(hedef[0] if hedef else "")
        self.btnDevam.setVisible(hedef is not None)

    def _devam_oynat(self) -> None:
        if self._devam_kaydi:
            self.play_requested.emit(self._devam_kaydi)

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
            row.aralik_toggled.connect(self._on_row_range_toggled)
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
        self._son_anahtar = tuple(key)
        self._update_selection_label()

    def _on_row_range_toggled(self, key, checked: bool) -> None:
        """Shift+tık: son işaretlenen satırdan bu satıra kadar aynı duruma getir.

        Aralık FİLTREDEN GEÇEN sırada: kullanıcı ekranda gördüğü iki satırın
        arasını kastediyor. Önceki satır filtrede yoksa (ya da ilk tık Shift'li)
        yalnızca bu satır değişir.
        """
        key = tuple(key)
        sira = [_key_of(e) for e in self.filtered_episodes()]
        if self._son_anahtar in sira and key in sira:
            i, j = sorted((sira.index(self._son_anahtar), sira.index(key)))
            aralik = sira[i:j + 1]
        else:
            aralik = [key]
        for anahtar in aralik:
            if checked:
                self._selected.add(anahtar)
            else:
                self._selected.discard(anahtar)
        self._son_anahtar = key
        self._satirlari_esitle()

    def _satirlari_esitle(self) -> None:
        """Çizilmiş satırların kutularını seçim kümesine eşitle (sinyalsiz)."""
        for row in self._rows:
            row.sessiz_isaretle(row.key in self._selected)
        self._update_selection_label()

    def _secimi_kur(self, secilecek: Iterable[Dict[str, Any]], aciklama: str) -> int:
        """Seçimi verilen bölümlerle DEĞİŞTİR; kaç bölüm seçildiğini söyle.

        Ekleme değil değiştirme: "İzlenmemişler"e basan kullanıcı önceki elle
        seçimin üstüne eklenmesini değil, tam o kümeyi bekliyor.
        """
        self._selected = {_key_of(e) for e in secilecek}
        self._son_anahtar = None
        self._satirlari_esitle()
        adet = len(self.selected_episodes())
        self.lblStatus.info(f"{adet} bölüm seçildi ({aciklama}).")
        return adet

    def select_range(self, metin: str) -> int:
        """"1-12, 15, 20-" — filtreden geçen bölümlerde numarayla seç.

        Ara bölümler (5.5) numaralarıyla birlikte gelir: "5" yazan 5.5'i de
        alır; o bölümün numarası 5 ve çoğu kaynak onu ayrı numaralamıyor.
        """
        adaylar = self.filtered_episodes()
        sonuc = aralik_coz(metin, (int(e.get("number") or 0) for e in adaylar))
        if not (metin or "").strip() or (not sonuc and sonuc.hatali):
            self.lblStatus.error("Aralık anlaşılamadı; örnek: 1-12, 15, 20-")
            return 0
        adet = self._secimi_kur(
            [e for e in adaylar if int(e.get("number") or 0) in sonuc],
            f"aralık {metin.strip()}")
        if sonuc.hatali:
            self.lblStatus.info(f"{adet} bölüm seçildi; anlaşılamayan: "
                                + ", ".join(sonuc.hatali))
        return adet

    @staticmethod
    def _gecmis_durumu(episode: Dict[str, Any], gecmis: Any) -> Tuple[bool, bool]:
        """Bölümün (izlendi, indirildi) durumu — satırı çizilmemiş olsa da.

        Satırdaki rozetle AYNI kural (bkz. `EpisodeRow.apply_history`):
        herhangi bir kaynaktan izlendiyse izlenmiş sayılır.
        """
        izlendi = indirildi = False
        for entry in (episode.get("sources") or {}).values():
            watched, downloaded = gecmis.durum((entry or {}).get("obj"))
            izlendi = izlendi or watched
            indirildi = indirildi or downloaded
        return izlendi, indirildi

    def _gecmis_oku(self) -> Any:
        # Rozetler kapalıyken `_gecmis` None; seçim yine geçmişe bakmalı.
        return self._gecmis if self._gecmis is not None else prefs.Gecmis.yukle()

    def select_unwatched(self) -> int:
        gecmis = self._gecmis_oku()
        return self._secimi_kur(
            [e for e in self.filtered_episodes()
             if not self._gecmis_durumu(e, gecmis)[0]], "izlenmemişler")

    def select_not_downloaded(self) -> int:
        """İndirilmemiş VE kuyrukta olmayanlar: "Seçilenleri İndir" zaten
        kuyruktakileri atlıyor, seçili görünmeleri yanıltırdı."""
        gecmis = self._gecmis_oku()
        secilecek = []
        for e in self.filtered_episodes():
            if self._gecmis_durumu(e, gecmis)[1]:
                continue
            if any(self.kuyrukta_mi(k) for k in (e.get("sources") or {}).values() if k):
                continue
            secilecek.append(e)
        return self._secimi_kur(secilecek, "indirilmemişler")

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
        yeni = [e for e in entries if not self.kuyrukta_mi(e)]
        kuyrukta = len(entries) - len(yeni)
        message = f"{len(yeni)} bölüm indirme sırasına alındı ({source_label(source)})."
        if kuyrukta:
            message += f" {kuyrukta} bölüm zaten kuyrukta."
        if missing:
            message += f" {missing} bölüm bu kaynakta yok, atlandı."
        self.lblStatus.info(message)
        for entry in yeni:
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


def _key_of(episode: Dict[str, Any]) -> Tuple[int, int, int]:
    """Satır kimliği: ``(sezon, bölüm, ara bölüm)``.

    Ara bölüm anahtarın parçası; "5. Bölüm" ile "5.5. Bölüm" iki ayrı satır ve
    biri işaretlenince diğeri de işaretlenmiş sayılmamalı.
    """
    return (int(episode.get("season") or 1), int(episode.get("number") or 0),
            int(episode.get("sub") or 0))


__all__ = ["EpisodePage", "EpisodeRow", "SourceSelectDialog", "as_sources_data",
           "aralik_coz", "AralikSonucu",
           "active_sources", "episode_matches", "primary_entry", "source_counts",
           "source_short", "source_color", "source_label"]
