"""Anime detay sayfası — keşif/arama ile bölüm listesi arasındaki köprü.

Eski GUI'de bu iş `gui/main.py::show_anime_details` içindeydi (~740 satır). O
hacmin neredeyse tamamı her kaynak için ayrı yazılmış bölüm-yükleme `elif`
bloklarıydı; aynı iş artık `sources_bridge.fetch_episodes` tablosunda tek yerde
durduğu için burada yalnızca sunum + tek bir çağrı kalıyor.

Sayfa **kendisine verilen sözlüğü** render eder, kendiliğinden ağdan
metadata aramaz: keşif kartı zaten tam kaydı taşır, arama sonucu ise
(kaynak, slug, başlık) ve varsa kapak adresini bilir; bu hâliyle de bütün
aksiyonlar çalışır. Kullanıcının istemediği bir ağ isteği atmamak, birkaç boş
alanı doldurmaktan önemli. Tek istisna arşiv (TürkAnime) kaydı: künyesi
(`info.json`: özet, tür, stüdyo, puan…) arşivin kendisinde, yerel arşivle
ağsız okunuyor (bkz. `animedepo.anime_bilgisi`).

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
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ....common import kutuphane
from ....common.episode_parser import merge_episodes
from ....common.hatalar import ham_metin, sebep_metni
from ....common.title_match import baslik_normalize, siralama_skoru
from ....sources.kayit import gorunen_ad, kanonik_ad
from ..sources_bridge import (
    METADATA_ONLY, UnsupportedSource, fetch_episodes, supported_sources,
)
from ..theme import ACCENT, BG_ELEV, BG_ELEV_2, BORDER, TEXT_MUTED, score_color
from ..gorsel import gorsel_getir
from ..widgets import StatusLabel, yer_tutucu_poster
from ..workers import WorkerSignals, run_bg
from .discover import anime_title, cover_url, score_of

COVER_W, COVER_H = 220, 310

# Rozet satırı tek sıra; çok uzun listeler kartı taşırıyor. AniList'te tür
# sayısı zaten ~5, kalanı araç ipucunda gösteriliyor.
MAX_BADGES = 8

# Eşleşme diyaloğunda kaynak başına gösterilecek aday sayısı. Amaç doğru kaydı
# bulmak, tam listeyi taramak değil.
MATCH_LIMIT_PER_SOURCE = 8

# Otomatik eşleştirmede kaynak başına aday sayısı. ESKİDEN 1'di ve bu, kaynağın
# HAM ilk sonucunun bağlanması demekti: `Kaynak.ara` listeyi alaka sırasından
# (`adapters._alakaya_gore_sirala`) ÖNCE kesiyor, sıralama tek elemanlı listeyi
# sıralıyordu. "One Piece" araması AnimeciX'te "Koisuru One Piece"e bağlanıyordu.
# Diyalogla aynı sayı: kaynakların çoğu zaten tek sayfa çekiyor, fazlası yalnızca
# kesilmiyor; birebir başlık aday listesine girip sıralamayı kazanıyor.
AUTO_MATCH_LIMIT = MATCH_LIMIT_PER_SOURCE

# Otomatik bağlama eşiği (`siralama_skoru`, 0..1). Ölçümler (bkz. denetim):
#   birebir / "One Piece (TV)" / "One Piece İzle"    1.00 / 0.99 / 0.99
#   "[Oshi no Ko]" → arşivde "Hoshi no Koe"           0.91   ← başka anime
#   "One Piece Fan Letter" / "ONE PIECE (Live Action)" 0.87 / 0.85
#   "Koisuru One Piece" / "Naruto: Shippuuden"         0.74 / 0.77
# Yanlış ve doğru adaylar 0.79-0.91 bandında iç içe; o bantta sessizce bağlamak
# kullanıcıya YANLIŞ animenin bölümlerini oynatmak demek. 0.95 yalnızca
# birebir ve ek-almış ("(TV)", "İzle") başlıkları geçiriyor; geçmeyen kaynak
# bağlanmıyor, gerekirse diyalog açılıyor (bir tık, yanlış anime değil).
OTOMATIK_ESLESME_ESIGI = 0.95

# Eşleşme bulunamayınca sıradaki başlık varyantıyla (İngilizce, eş anlamlı…)
# en çok kaç sorgu atılır. Ağ kaynağında her tur 25 sn'ye kadar sürebilir.
ESLESME_SORGU_SINIRI = 3

# Künyesi arşivin kendisinde duran kaynak (bkz. `animedepo.anime_bilgisi`).
ARSIV_KAYNAGI = "TürkAnime"

# Kitaplık düğmesinin iki hâli. Düğme bağlı kaynağa göre anahtarlanıyor
# (bkz. `DetailPage._favori_guncelle`); bağsız kayıtta kitaplığa eklenecek
# bir kimlik yok, düğme kapalı durur.
FAVORI_EKLE = "♡ Kitaplığa ekle"
FAVORI_VAR = "♥ Kitaplıkta"

# AniList/Jikan büyük harfli sabitler döndürür; kullanıcıya Türkçe gösteriyoruz.
SEASON_LABELS = {"WINTER": "Kış", "SPRING": "İlkbahar", "SUMMER": "Yaz",
                 "FALL": "Sonbahar"}
STATUS_LABELS = {"RELEASING": "Yayında", "FINISHED": "Tamamlandı",
                 "NOT_YET_RELEASED": "Henüz yayınlanmadı",
                 "CANCELLED": "İptal edildi", "HIATUS": "Ara verildi"}
# AniList `format` sabitleri; arşivin "Kategori"si ("TV", "Film", "OVA")
# zaten Türkçe/okunur olduğu için tabloda olmayan değer olduğu gibi basılır.
FORMAT_LABELS = {"MOVIE": "Film", "TV_SHORT": "TV (kısa)", "SPECIAL": "Özel",
                 "MUSIC": "Müzik"}
AY_ADLARI = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")

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


def tarih_metni(tarih: Any) -> str:
    """AniList `{"year", "month", "day"}` → "7 Nisan 2009" (eksik alan atlanır)."""
    if not isinstance(tarih, dict) or not tarih.get("year"):
        return ""
    parcalar: List[str] = []
    ay = tarih.get("month")
    if isinstance(ay, int) and 1 <= ay <= 12:
        if tarih.get("day"):
            parcalar.append(str(int(tarih["day"])))
        parcalar.append(AY_ADLARI[ay - 1])
    parcalar.append(str(tarih["year"]))
    return " ".join(parcalar)


def meta_line(item: Dict[str, Any]) -> str:
    """"12 bölüm • 24 dk • Yaz 2026 • Tamamlandı" biçimindeki özet satır."""
    parts: List[str] = []
    if item.get("episodes"):
        parts.append(f"{item['episodes']} bölüm")
    if item.get("duration"):
        parts.append(f"{item['duration']} dk")
    fmt = str(item.get("format") or "").strip()
    if fmt:
        parts.append(FORMAT_LABELS.get(fmt.upper(), fmt))

    season = SEASON_LABELS.get(str(item.get("season") or "").upper())
    year = item.get("seasonYear")
    baslangic = tarih_metni(item.get("startDate"))
    bitis = tarih_metni(item.get("endDate"))
    if season and year:
        parts.append(f"{season} {year}")
    elif season:
        parts.append(season)
    elif baslangic:
        # Sezonu olmayan kayıt (arşiv künyesi) tarih taşıyor; yıl zaten içinde.
        # Sezon bilinen kayıtta tarih YAZILMIYOR: satır iki kez yıl söylerdi.
        parts.append(baslangic if bitis in ("", baslangic)
                     else f"{baslangic} – {bitis}")
    elif year:
        parts.append(str(year))

    status = STATUS_LABELS.get(str(item.get("status") or "").upper())
    if status:
        parts.append(status)
    return " • ".join(parts)


def episode_total(episodes: Any) -> int:
    """Toplam bölüm sayısı — yük düz liste de olabilir, `{kaynak: liste}` de."""
    if isinstance(episodes, dict):
        return sum(len(items or []) for items in episodes.values())
    return len(episodes or [])


def kunye_birlestir(anime: Dict[str, Any], ek: Dict[str, Any]) -> Dict[str, Any]:
    """``ek``'teki alanlarla YALNIZCA boş olanları doldur (kopya döner).

    Arşiv künyesi keşiften gelen tam AniList kaydının üstüne yazılmamalı
    (AniList türleri, puanı, özeti daha zengin); ama arama sonucundan gelen
    başlık-yalnızca kayıtta her alan boştur ve künye onları doldurur.
    Başlık sözlüğü alan alan birleşir: "native" eksikse eklenir, mevcut
    "romaji" korunur.
    """
    out = dict(anime or {})
    for anahtar, deger in (ek or {}).items():
        if anahtar == "title" and isinstance(deger, dict):
            basliklar = dict(out.get("title") or {}) if isinstance(
                out.get("title"), dict) else {}
            for alt, metin in deger.items():
                if metin and not basliklar.get(alt):
                    basliklar[alt] = metin
            out["title"] = basliklar
        elif deger not in (None, "", [], {}) and not out.get(anahtar):
            out[anahtar] = deger
    return out


def first_slug(items: Any) -> str:
    """Arama sonucundan ilk geçerli slug (skorsuz; eşleştirmede KULLANILMIYOR).

    Otomatik eşleştirme artık `en_iyi_slug` ile eşik uyguluyor; bu yardımcı
    geriye dönük uyumluluk için duruyor.
    """
    for item in (items or []):
        if isinstance(item, dict) and item.get("slug"):
            return str(item["slug"])
    return ""


def eslesme_basliklari(anime: Dict[str, Any], yedek: str = "") -> List[str]:
    """Otomatik eşleştirmede denenecek başlıklar (sıralı, tekrarsız).

    Sıra: ekrandaki başlık (arama sorgusu/`_match_title`), romaji, İngilizce,
    eş anlamlılar (AniList `synonyms`), Japonca. Arşiv başlıkları çoğunlukla
    MAL/AniList romajisi; diğer siteler İngilizce ya da Türkçe adı
    kullanabiliyor ("Frieren: Beyond Journey's End" romajiye karşı 0.31).
    Normalize biçimi aynı olanlar ("Dr. Stone" / "Dr Stone") bir kez sayılır;
    normalize edilince boş kalanlar (Japonca yazı: `siralama_skoru` onlara
    zaten 0 veriyor) hiç girmez — ağ kaynağında boşa bir sorgu turu olurdu.
    """
    adaylar: List[Any] = [yedek]
    basliklar = anime.get("title") if isinstance(anime, dict) else None
    if isinstance(basliklar, dict):
        adaylar += [basliklar.get("romaji"), basliklar.get("english")]
    elif isinstance(basliklar, str):
        adaylar.append(basliklar)
    esler = anime.get("synonyms") if isinstance(anime, dict) else None
    if isinstance(esler, list):
        adaylar += esler
    if isinstance(basliklar, dict):
        adaylar.append(basliklar.get("native"))

    out: List[str] = []
    gorulen = set()
    for aday in adaylar:
        if not isinstance(aday, str) or not aday.strip():
            continue
        anahtar = baslik_normalize(aday)
        if anahtar and anahtar not in gorulen:
            gorulen.add(anahtar)
            out.append(aday.strip())
    return out


def en_iyi_aday(items: Any, basliklar: List[str],
                esik: float = OTOMATIK_ESLESME_ESIGI) -> Tuple[str, str, float]:
    """Eşiği geçen en iyi aday: ``(slug, başlık, skor)``; yoksa ``("", "", skor)``.

    Aday skoru, başlık varyantlarının EN İYİSİ (romajiyle değil İngilizce
    adla birebir eşleşen de bağlanır). Eşit skorda kaynağın kendi sırası
    korunur (ilk gelen kazanır).
    """
    en_iyi: Tuple[str, str, float] = ("", "", 0.0)
    for item in (items or []):
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        baslik = str(item.get("title") or "")
        skor = max((siralama_skoru(b, baslik) for b in basliklar if b), default=0.0)
        if skor > en_iyi[2]:
            en_iyi = (str(item["slug"]), baslik, skor)
    if en_iyi[2] >= esik:
        return en_iyi
    return "", "", en_iyi[2]


def en_iyi_slug(items: Any, basliklar: List[str],
                esik: float = OTOMATIK_ESLESME_ESIGI) -> str:
    """`en_iyi_aday`'ın yalnızca slug'ı: eşiği geçen aday yoksa ``""``."""
    return en_iyi_aday(items, basliklar, esik)[0]


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

    Otomatik eşleştirme eşiği geçen aday bulamazsa ya da kullanıcı başka
    sezonu/OVA'yı istiyorsa kaçış yolu. ``ara=True``: diyalog açılır açılmaz
    aramaya başlar — eskiden boş bir listeyle açılıp "Ara"ya basılmasını
    bekliyordu, oysa sorgu (animenin adı) zaten dolu geliyordu.
    """

    def __init__(self, query: str = "", parent: Optional[QWidget] = None,
                 ara: bool = False):
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
        if ara:
            # Sonuçlar kuyruklu sinyalle gelir; `exec()` döngüsü başlayınca işlenir.
            self.search()

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
            # Etiket ("TürkAnime (arşiv)") ve sayı ayrı: "(arşiv) (1)" okunmuyordu.
            parent = QTreeWidgetItem(
                self.tree, [f"{gorunen_ad(source)} — {len(items)} sonuç"])
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

    # (kaynak, slug, başlık, bölümler) — liste zaten çekildi, tekrar çekilmesin.
    # Son alan tek kaynakta düz liste, çok kaynakta `{kaynak: liste}` sözlüğü.
    episodes_ready = Signal(str, str, str, object)
    back_requested = Signal()
    # (request_id, görsel baytları) — arka plandan UI thread'ine
    cover_ready = Signal(int, object)
    # (request_id, arşiv künyesi sözlüğü) — `animedepo.anime_bilgisi` çıktısı
    arsiv_bilgisi_hazir = Signal(int, object)
    # (request_id, {kaynak: slug}, tüm_kaynaklar_mı, istenen_kaynak)
    sources_resolved = Signal(object)
    # (request_id, kaynak, bölümler, hata) — kaynak başına tek iş
    source_loaded = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._anime: Dict[str, Any] = {}
        self._slug = ""
        self._match_title = ""
        self._busy = False
        # Kaynak → slug. Slug'lar kaynağa özgü ("cowboy-bebop" vs "17"), bu
        # yüzden çok kaynaklı yükleme tek bir slug'la yapılamaz.
        self._bindings: Dict[str, str] = {}
        # Kullanıcının eşleşme diyaloğunda ELLE seçtiği kaynak→slug. Ayrı
        # tutuluyor çünkü arka planda süren otomatik eşleştirme (`_do_resolve`)
        # işe başlarken çekilmiş bir kopyayla dönüyor; bu küme olmasa geç dönen
        # otomatik sonuç kullanıcının seçimini sessizce ezerdi.
        self._manual: Dict[str, str] = {}
        # Süren çok kaynaklı yüklemenin toplama durumu (bkz. `_dispatch`)
        self._pending: Dict[str, Any] = {}
        # Bu istek için otomatik eşleştirme yapıldı mı (aynı aramayı tekrarlama)
        self._resolved_for = -1
        # Otomatik bağlanan kaynak → eşleşen başlık ("Eşleşme: X → Y" bildirimi)
        self._oto_eslesme: Dict[str, str] = {}
        # Son "Tüm kaynaklar" eşleştirmesinde aday bulunamayan kaynaklar
        self._eslesmeyen: List[str] = []
        # Yarış koruması: her yeni anime bu sayacı artırır, arka plandan dönen
        # her sonuç kendi kimliğiyle gelir ve eskiyse sessizce atılır.
        self._request_id = 0

        self.signals = WorkerSignals()
        self.signals.connect_found(self._on_episodes)
        self.signals.connect_error_item(self._on_failed)
        self.cover_ready.connect(self._apply_cover)
        self.arsiv_bilgisi_hazir.connect(self._arsiv_bilgisini_uygula)
        self.sources_resolved.connect(self._on_sources_resolved)
        self.source_loaded.connect(self._on_source_loaded)

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

        self.lblCover = QLabel()
        # Gerçek kapak mı, çizilmiş yer tutucu mu duruyor (bkz. `_kapak_yer_tutucu`)
        self.kapak_yer_tutucuda = True
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

        # Kutuda insana dönük etiket ("TürkAnime (arşiv)"), öğe verisinde
        # kanonik ad: bağlantılar, köprü ve eşleşme kaydı kanonik adla çalışıyor
        # (bkz. `current_source`). Metin değişince değil İNDEKS değişince
        # dinleniyor — metin artık etiket, kaynak adı değil.
        self.cmbSource = QComboBox()
        for source in supported_sources():
            self.cmbSource.addItem(gorunen_ad(source), source)
        self.cmbSource.currentIndexChanged.connect(
            lambda _index: self._on_source_changed(self.current_source()))
        actions.addWidget(self.cmbSource)

        # Varsayılan kaynak arşiv: kayıtlı ilk kaynak alfabetik sırayla
        # AnimeciX'ti (deneysel) ve bağsız her anime oraya düşüyordu. Arşiv
        # yerel/önbellekli, eşleştirmesi ağsız ve kataloğu en geniş olanı.
        varsayilan = self.cmbSource.findData(ARSIV_KAYNAGI)
        if varsayilan >= 0:
            self.cmbSource.setCurrentIndex(varsayilan)

        # Varsayılan KAPALI: tek kaynak, tek istek. İşaretlenince bütün
        # kaynaklarda otomatik eşleşme aranır ve bölümler tek listede birleşir —
        # bu, kaynak sayısı kadar ağ isteği demek, kullanıcı istemeden olmamalı.
        self.chkAllSources = QCheckBox("Tüm kaynaklar")
        self.chkAllSources.setToolTip(
            "Bölümleri desteklenen bütün kaynaklardan çekip tek listede birleştir.")
        actions.addWidget(self.chkAllSources)

        self.btnEpisodes = QPushButton("Bölümleri Getir")
        self.btnEpisodes.setObjectName("Primary")
        self.btnEpisodes.clicked.connect(self.load_episodes)
        actions.addWidget(self.btnEpisodes)

        self.btnMatch = QPushButton("İstediğin anime değil mi?")
        self.btnMatch.clicked.connect(self.open_match_dialog)
        actions.addWidget(self.btnMatch)

        # Favori, kaynağın KENDİ kimliğiyle saklanıyor: kitaplıktan açılan kayıt
        # eşleştirme diyaloğuna uğramadan aynı kaynağa bağlanabilsin.
        self.btnFavori = QPushButton(FAVORI_EKLE)
        self.btnFavori.setCheckable(True)
        self.btnFavori.clicked.connect(self._favori_degistir)
        actions.addWidget(self.btnFavori)

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
        self._pending = {}           # yarıda kalan toplama durumu da geçersiz
        self._resolved_for = -1
        self._oto_eslesme = {}
        self._eslesmeyen = []
        self.btnEpisodes.setEnabled(True)
        self._anime = dict(anime or {})
        self._match_title = anime_title(self._anime)
        self._slug = str(slug or "")
        # Bağlantılar animeye özgü: yeni animede eskisinin slug'ları kalırsa
        # başka bir animenin bölümleri listeye karışır.
        self._bindings = {source: self._slug} if (source and self._slug) else {}
        self._manual = {}            # elle seçim de animeye özgü
        if source:
            self._select_source(source)
        self._render()
        self._favori_guncelle()
        self._kapak_yer_tutucu()
        self._load_cover(self._request_id)
        if source and self._slug and kanonik_ad(source) == ARSIV_KAYNAGI:
            # Künye diskten (yerel arşiv) ya da aynadan okunur: GUI thread'inde
            # değil. Sonuç kimlikli döner; kullanıcı bu arada başka animeye
            # geçtiyse eski künye yenisinin üstüne basılmaz.
            run_bg(self._arsiv_bilgisi_oku, self._request_id, self._slug)
        return self._request_id

    def show_match(self, source: str, slug: str, title: str,
                   kayit: Optional[Dict[str, Any]] = None) -> int:
        """Arama sonucundan gelen (kaynak, slug, başlık) üçlüsünü göster.

        ``kayit`` arama sonucunun kendisi (`search_all_sources_rich` kaydı);
        kartta görünen kapak (``image``) detayda da görünsün diye taşınıyor —
        eskiden yalnızca üçlü geliyordu ve AniList kartına tıklayan kullanıcı
        posteri az önce gördüğü animenin detayında "Kapak yok" görüyordu.
        Metadata yoksa sayfa yine de künye iskeletini ve aksiyonları gösterir.
        """
        anime: Dict[str, Any] = {"title": {"romaji": title}}
        gorsel = (kayit or {}).get("image") if isinstance(kayit, dict) else None
        if isinstance(gorsel, str) and gorsel:
            anime["coverImage"] = {"large": gorsel}
        if kanonik_ad(source) in METADATA_ONLY:
            # AniList kartı bir METADATA kaydı: oynatılacak bir kaynak değil.
            # Eskiden "AniList"e bağlanıyor, kutuya ekleniyor ve "Bölümleri
            # Getir" metadata hatasıyla bitiyordu. Artık keşif kartı gibi
            # açılıyor: bağ yok, bölümler istenince seçili oynatma kaynağında
            # (varsayılan arşiv) başlığa göre otomatik eşleşme aranıyor.
            rid = self.show_anime(anime)
            self._match_title = title
            self.lblStatus.info(
                f"{gorunen_ad(source)} kaydı. Bölümleri getirince "
                f"{gorunen_ad(self.current_source())} kaynağında eşleşme aranacak.")
            return rid
        rid = self.show_anime(anime, source=source, slug=slug)
        self._match_title = title
        self.lblStatus.info(
            f"{gorunen_ad(source)} kaydı seçildi. Bölümleri getirebilirsiniz.")
        return rid

    def apply_match(self, source: str, slug: str, title: str) -> None:
        """Eşleşme diyaloğundan seçilen kaydı sayfaya bağla.

        `show_match` DEĞİL: kullanıcı aynı animenin doğru kaydını seçti,
        ekrandaki künyeyi (özet, tür, kapak) silmenin anlamı yok.
        """
        self._slug = str(slug or "")
        self._match_title = title or self._match_title
        # Diğer kaynakların bağlantısı SİLİNMEZ: kullanıcı ikinci bir kaynağı
        # elle eşleştirdiyse "Tüm kaynaklar" o eşleşmeyi de kullanabilmeli.
        if self._slug:
            self._bindings[source] = self._slug
            # Açık seçim işaretlenir: arka planda süren otomatik eşleştirme
            # döndüğünde bu kaynağa dokunmasın (bkz. `_on_sources_resolved`).
            self._manual[source] = self._slug
        self._select_source(source)
        self._favori_guncelle()
        self.lblStatus.ok(f"{gorunen_ad(source)} → {title} eşleştirildi.")
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
        # Kapak BURADA sıfırlanmıyor: künye arka plandan gelip yeniden render
        # edildiğinde o sırada inmiş kapak silinirdi (bkz. `_kapak_yer_tutucu`).

    # ── Arşiv künyesi ───────────────────────────────────────────────────────
    def _arsiv_bilgisi_oku(self, rid: int, slug: str) -> None:
        """Arka plan: arşiv künyesini oku, kimliğiyle UI thread'ine taşı.

        Künye süs: okunamazsa (arşiv yok, ayna kapalı) sayfa başlıkla ve
        aksiyonlarla çalışmaya devam eder; hata bölüm yüklenirken zaten
        sebebiyle gösteriliyor, burada ikinci kez bağırmıyoruz.
        """
        try:
            from ....sources import animedepo
            bilgi = animedepo.anime_bilgisi(slug)
        except Exception as exc:
            print(f"[Detay] Arşiv künyesi okunamadı ({slug}): {exc}")
            return
        if bilgi:
            self.arsiv_bilgisi_hazir.emit(rid, bilgi)

    def _arsiv_bilgisini_uygula(self, rid: int, bilgi: Dict[str, Any]) -> None:
        """GUI thread'i: künye hâlâ ekrandaki animeye aitse boş alanları doldur."""
        if rid != self._request_id or not isinstance(bilgi, dict):
            return
        self._anime = kunye_birlestir(self._anime, bilgi)
        self._render()

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
    def _kapak_yer_tutucu(self) -> None:
        """Çizilmiş başlık kartı: kapak yoksa ya da inemezse bu kalır.

        Arşiv kayıtlarının kapağı yok (adresler kapanan siteye gidiyor);
        "Kapak yok" yazılı gri kutu yerine aramadaki kartla AYNI renkte,
        baş harfli bir poster gösteriliyor. Ağ yok, anında çizilir.
        """
        self.lblCover.setPixmap(yer_tutucu_poster(
            anime_title(self._anime), COVER_W, COVER_H))
        self.kapak_yer_tutucuda = True

    def _load_cover(self, rid: int) -> None:
        url = cover_url(self._anime)
        if url:
            run_bg(self._fetch_cover, rid, url, gorsel=True)

    def _fetch_cover(self, rid: int, url: str) -> None:
        """Arka plan: görseli (önbellekten ya da ağdan) al, UI thread'ine taşı."""
        data = gorsel_getir(url)
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
        self.kapak_yer_tutucuda = False

    # ── Kaynak seçimi ───────────────────────────────────────────────────────
    def _select_source(self, source: str) -> None:
        """Kaynağı combo'da seç; listede yoksa ekle.

        `supported_sources()` yalnızca oynatma bağlanmış kaynakları verir; ama
        arama AniList gibi metadata kaynaklarını da döndürebiliyor. Gelen
        kaynağı gizlemek yerine gösterip seçildiğinde net hata veriyoruz.
        """
        if not source:
            return
        index = self.cmbSource.findData(source)
        if index < 0:
            self.cmbSource.addItem(gorunen_ad(source), source)
            index = self.cmbSource.findData(source)
        self.cmbSource.setCurrentIndex(index)

    def _on_source_changed(self, source: str) -> None:
        self._favori_guncelle()
        if source in METADATA_ONLY:
            self.lblStatus.info(
                f"{gorunen_ad(source)} yalnızca metadata kaynağı; "
                "bölüm için başka kaynak seçin.")

    def current_source(self) -> str:
        """Seçili kaynağın kanonik adı (kutudaki etiket değil)."""
        veri = self.cmbSource.currentData()
        return veri if isinstance(veri, str) and veri else self.cmbSource.currentText()

    # ── Bölüm yükleme ───────────────────────────────────────────────────────
    def load_episodes(self) -> None:
        """Seçili kaynak(lar)dan bölümleri arka planda getir.

        Kaynağa bağlı olmayan kayıt (keşif/izleme listesi kartı, AniList arama
        sonucu) ÖNCE otomatik eşleştirmeden geçer: eskiden diyalog her seferinde
        açılıyor, boş listeyle "Ara"ya basılmasını bekliyordu. Diyalog artık
        yalnızca hiçbir kaynakta eşiği geçen aday çıkmazsa, dolu ve aramaya
        başlamış olarak açılıyor (bkz. `_elle_eslestir`).
        """
        if self._busy:
            self.lblStatus.info("Önceki istek sürüyor, lütfen bekleyin…")
            return

        rid = self._request_id
        source = self.current_source()
        want_all = self.chkAllSources.isChecked()
        # Kombo başka kaynağa çevrildiyse o kaynağın slug'ı bilinmiyor olabilir;
        # elimizdeki slug BAŞKA kaynağa ait, onunla istek atmak yanlış animenin
        # bölümlerini getirir. Bu yüzden önce eşleşme aranır.
        need_resolve = (want_all and self._resolved_for != rid) or (
            not want_all and source not in self._bindings
            and source not in METADATA_ONLY)

        self._busy = True
        self.btnEpisodes.setEnabled(False)
        if need_resolve:
            self.lblStatus.info(
                "Kaynaklarda eşleşme aranıyor…" if want_all
                else f"{gorunen_ad(source)} kaynağında eşleşme aranıyor…")
            # Başlık varyantları GUI thread'inde, `_anime`'nin o anki hâlinden;
            # arka plan işi sayfanın durumuna dokunmaz.
            run_bg(self._do_resolve, rid, self._match_title,
                   dict(self._bindings), want_all, source,
                   eslesme_basliklari(self._anime, self._match_title))
            return
        self._dispatch(rid, self._targets(want_all, source))

    def _targets(self, want_all: bool, source: str) -> Dict[str, str]:
        """Bu yüklemede hangi kaynak hangi slug ile çekilecek."""
        if want_all:
            return dict(self._bindings)
        slug = self._bindings.get(source, "")
        return {source: slug} if slug else {}

    def _dispatch(self, rid: int, targets: Dict[str, str]) -> None:
        """Kaynak başına bir arka plan işi başlat ve toplama durumunu kur."""
        if not targets:
            self._on_failed((rid, "Bu anime için kaynak eşleşmesi bulunamadı."))
            return
        primary = (self.current_source() if self.current_source() in targets
                   else next(iter(targets)))
        self._pending = {
            "rid": rid, "expected": len(targets), "primary": primary,
            "slug": targets[primary], "title": self._match_title,
            "results": {}, "errors": {},
        }
        if len(targets) > 1:
            self.lblStatus.info(
                f"{len(targets)} kaynaktan bölümler getiriliyor…")
        else:
            self.lblStatus.info(f"{gorunen_ad(primary)} bölümleri getiriliyor…")
        # Kaynak başına AYRI iş: biri kilitlenirse ya da patlarsa diğerleri
        # kendi hızında gelmeye devam eder.
        for source, slug in targets.items():
            run_bg(self._do_load, rid, source, slug, self._match_title)

    def _do_resolve(self, rid: int, title: str, known: Dict[str, str],
                    want_all: bool, wanted: str,
                    basliklar: Optional[List[str]] = None) -> None:
        """Arka plan: kaynak başına EŞİĞİ GEÇEN en iyi adayı bulup slug'a bağla.

        Yalnızca bağlanması istenen kaynaklar aranır (`arama_motoru`): tek
        kaynakta o kaynak, "Tüm kaynaklar"da henüz bağlı olmayan oynatılabilir
        kaynaklar — AniList (metadata) hiçbir zaman. Eşiği geçen aday yoksa
        kaynak BAĞLANMAZ (eskiden ham ilk sonuç bağlanıyordu); sıradaki başlık
        varyantıyla yalnızca cevap verip eşleşme çıkmayan kaynaklar yeniden
        aranır — hata veren/yanıtsız kaynağa ikinci tur yalnızca bekleme demek.
        """
        from ....common.adapters import arama_motoru

        basliklar = [b for b in (basliklar or [title]) if b] or [title]
        bindings = dict(known)
        supported = set(supported_sources())
        # Kanonik adla da bak: eski "AnimeDepo" bağlantısı varken aramadan
        # gelen "TürkAnime" aynı arşiv — ikinci kez bağlanırsa aynı bölümler
        # "Tüm kaynaklar" listesine iki kaynakmış gibi girer.
        bagli = {kanonik_ad(b) for b in bindings}
        if want_all:
            hedefler = sorted(s for s in supported if s not in METADATA_ONLY)
        else:
            hedefler = [wanted]
        hedefler = [s for s in hedefler if s in supported and s not in bindings
                    and s not in METADATA_ONLY and kanonik_ad(s) not in bagli]

        aranacak = list(hedefler)
        eslesen: Dict[str, str] = {}
        for tur, sorgu in enumerate(basliklar[:ESLESME_SORGU_SINIRI]):
            if not aranacak:
                break
            try:
                results = arama_motoru(aranacak).search_all_sources_rich(
                    sorgu, limit_per_source=AUTO_MATCH_LIMIT)
            except Exception as exc:
                if tur == 0:
                    self.signals.emit_error_item(
                        (rid, f"Kaynak araması başarısız: {sebep_metni(exc)}"))
                    return
                break                    # yedek sorgu patladı: eldekiyle dön
            hatalar = getattr(results, "hatalar", None) or {}
            cevaplayan = set()
            for source, items in (results or {}).items():
                if source not in aranacak or kanonik_ad(source) in bagli:
                    continue
                if source not in hatalar:
                    cevaplayan.add(source)
                slug, eslesen_baslik, _skor = en_iyi_aday(items, basliklar)
                if slug:
                    bindings[source] = slug
                    eslesen[source] = eslesen_baslik
                    bagli.add(kanonik_ad(source))
            aranacak = [s for s in aranacak
                        if s in cevaplayan and s not in bindings]

        eslesmeyen = [s for s in hedefler if s not in bindings]
        self.sources_resolved.emit((rid, bindings, want_all, wanted,
                                    {"eslesen": eslesen, "eslesmeyen": eslesmeyen}))

    def _on_sources_resolved(self, payload) -> None:
        """GUI thread'i: çözülen bağlantıları sakla ve yüklemeyi başlat.

        Otomatik eşleşme kullanıcının ELLE seçtiğini EZMEZ: arama saniyeler
        sürüyor ve kullanıcı bu sırada "İstediğin anime değil mi?"den doğru
        sezonu seçebiliyor. `request_id` yarışı animeyi değiştirmeyi korur,
        aynı anime içindeki bu yarışı `_manual` korur.
        """
        try:
            rid, bindings, want_all, wanted, *ek = payload
        except (TypeError, ValueError):
            return
        if rid != self._request_id:
            return
        rapor = ek[0] if ek and isinstance(ek[0], dict) else {}
        for source, slug in (bindings or {}).items():
            if source in self._manual:
                continue               # kullanıcının seçimi üstündür
            self._bindings[source] = slug
        for source, baslik in (rapor.get("eslesen") or {}).items():
            if source not in self._manual:
                self._oto_eslesme[source] = baslik
        self._eslesmeyen = [s for s in (rapor.get("eslesmeyen") or [])
                            if s not in self._bindings]
        if want_all:
            self._resolved_for = rid       # aynı anime için bir daha arama
        self._favori_guncelle()            # yeni bağ: kitaplık düğmesi açılabilir
        # Tek kaynak modunda elle eşleştirme kaynağı da değiştirmiş olabilir;
        # istek, isteği başlatan eski seçime değil kullanıcının seçtiğine gider.
        if not want_all and self.current_source() in self._manual:
            wanted = self.current_source()
        targets = self._targets(want_all, wanted)
        if not targets and not self._bindings:
            # Hiçbir kaynakta eşiği geçen aday yok ve anime hiçbir yere bağlı
            # değil: tahmin edip yanlış animeyi oynatmak yerine kullanıcıya sor.
            self._elle_eslestir(rid)
            return
        self._dispatch(rid, targets)

    def _elle_eslestir(self, rid: int) -> None:
        """Otomatik eşleşme yok: diyaloğu dolu ve aramaya başlamış aç.

        Kullanıcı bir kayıt seçerse yükleme kaldığı yerden sürer; `_manual`
        ve `_resolved_for` ayarlı olduğu için ikinci bir otomatik arama yok.
        """
        self._busy = False
        self.btnEpisodes.setEnabled(True)
        self.lblStatus.info("Otomatik eşleşme bulunamadı; doğru kaydı seçin.")
        self.open_match_dialog()
        if rid != self._request_id:
            return                     # diyalog açıkken başka animeye geçildi
        if not self._bindings:
            self.lblStatus.error(
                "Bu anime bir kaynağa bağlı değil: “İstediğin anime değil mi?” "
                "ile eşleştirin.")
            return
        self.load_episodes()

    def _do_load(self, rid: int, source: str, slug: str, title: str) -> None:
        """Arka plan thread'i — sonucu KENDİ istek kimliğiyle geri yollar.

        Hatalar burada yakalanır: `run_bg`'nin genel hata sinyali kimlik
        taşımaz, yani eski bir isteğin hatası yeni animenin ekranına düşerdi.
        Kaynak hatası da yutulmaz, kaynağın adıyla birlikte rapor edilir.
        """
        try:
            episodes = fetch_episodes(source, slug, title)
        except UnsupportedSource as exc:
            self.source_loaded.emit((rid, source, [], str(exc)))
            return
        except Exception as exc:
            # Ham metin ("HTTPSConnectionPool(host=…): Max retries exceeded …
            # (Caused by ProxyError(…))") Türkçe sebebe çevrilir; konsolda kalır.
            print(f"[Detay] {source} bölümleri alınamadı: {ham_metin(exc)}")
            self.source_loaded.emit(
                (rid, source, [], f"Bölümler alınamadı: {sebep_metni(exc)}"))
            return
        self.source_loaded.emit((rid, source, list(episodes or []), ""))

    def _on_source_loaded(self, payload) -> None:
        """GUI thread'i: kaynak sonuçlarını topla, hepsi gelince devret."""
        try:
            rid, source, episodes, error = payload
        except (TypeError, ValueError):
            return
        state = self._pending
        if rid != self._request_id or state.get("rid") != rid:
            return               # geç dönen eski istek — ekranı EZMESİN

        state["results"][source] = list(episodes or [])
        if error:
            state["errors"][source] = error
        if len(state["results"]) < state["expected"]:
            return               # diğer kaynaklar hâlâ yolda

        self._pending = {}
        results, errors = state["results"], state["errors"]
        if not episode_total(results) and errors:
            # Hiç bölüm yok ve nedeni belli: kullanıcıya "bulunamadı" değil,
            # kaynağın kendi hata mesajı gösterilmeli.
            self._on_failed((rid, " • ".join(errors.values())))
            return
        self._on_episodes((rid, state["primary"], state["slug"], state["title"],
                           results))

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

        total = episode_total(episodes)
        if not total:
            self.lblStatus.error(f"{gorunen_ad(source)} kaynağında bölüm bulunamadı.")
            return

        if isinstance(episodes, dict) and len(episodes) > 1:
            loaded = [s for s, items in episodes.items() if items]
            failed = [s for s, items in episodes.items() if not items]
            message = (f"{len(merge_episodes(episodes, title))} bölüm • "
                       f"{len(episodes)} kaynaktan {len(loaded)} tanesi yüklendi.")
            if failed:
                message += f" Yüklenemeyen: {', '.join(gorunen_ad(s) for s in failed)}."
        else:
            message = f"{total} bölüm bulundu."
        if self._eslesmeyen:
            # Sessizce atlanan kaynak "orada yok" mu "bulunamadı" mı belli olsun.
            message += (" Eşleşme bulunamayan: "
                        f"{', '.join(gorunen_ad(s) for s in self._eslesmeyen)}.")
        if source in self._oto_eslesme and source not in self._manual:
            # Otomatik bağlanan kaydı göster: yanlışsa kullanıcı "İstediğin
            # anime değil mi?" ile düzeltebileceğini bilsin.
            message += f" Eşleşme: {gorunen_ad(source)} → {self._oto_eslesme[source]}"
        self.lblStatus.ok(message)
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

    # ── Kitaplık ────────────────────────────────────────────────────────────
    def kitaplik_ac(self, kayit: Dict[str, Any]) -> int:
        """Kitaplık kaydını (kaynak + kaynağın kimliği) BAĞLI olarak aç ve
        bölümleri hemen getir.

        Kayıt kaynağın kendi kimliğini taşıdığı için eşleştirme yok: ne
        otomatik arama ne diyalog. "İzlemeye devam et"e basan kullanıcı
        künye sayfasında bir kez daha "Bölümleri Getir"e basmamalı.
        """
        kaynak = str((kayit or {}).get("kaynak") or "")
        kimlik = str((kayit or {}).get("kimlik") or "")
        baslik = str((kayit or {}).get("baslik") or kimlik)
        kapak = (kayit or {}).get("kapak") or ""
        rid = self.show_match(kaynak, kimlik, baslik,
                              kayit={"image": kapak} if kapak else None)
        if kaynak and kimlik:
            self.chkAllSources.setChecked(False)
            self.load_episodes()
        return rid

    def kitaplik_baglami(self) -> Dict[str, Any]:
        """Bölüm sayfasına devredilen kimlik bilgisi: kaynak→kimlik ve kapak.

        Çok kaynaklı listede her satır KENDİ kaynağının kimliğiyle kaydedilmeli
        (`episodes_ready` yalnızca birincil kaynağın slug'ını taşıyor).
        """
        return {"baglar": dict(self._bindings),
                "kapak": cover_url(self._anime) or ""}

    def _favori_hedefi(self) -> Tuple[str, str]:
        """Kitaplık düğmesinin işlediği (kaynak, kimlik); bağ yoksa boş."""
        kaynak = self.current_source()
        return kaynak, self._bindings.get(kaynak, "")

    def _favori_guncelle(self) -> None:
        """Düğmeyi bağlı kaynağın kitaplık durumuna eşitle.

        Okuma yerel ve küçük bir JSON; ayar okumaları gibi GUI thread'inde.
        """
        if not hasattr(self, "btnFavori"):
            return          # kombo kurulurken tetiklendi; düğme henüz yok
        kaynak, kimlik = self._favori_hedefi()
        var = bool(kimlik) and kutuphane.favori_mi(kaynak, kimlik)
        self.btnFavori.setEnabled(bool(kimlik))
        self.btnFavori.setToolTip(
            "" if kimlik else "Kitaplığa eklemek için önce bölümleri getirin "
                              "(anime bir kaynağa bağlanmalı).")
        self._favori_goster(var)

    def _favori_goster(self, var: bool) -> None:
        self.btnFavori.setChecked(var)
        self.btnFavori.setText(FAVORI_VAR if var else FAVORI_EKLE)

    def _favori_degistir(self, isaretli: bool) -> None:
        """Kitaplığa ekle/çıkar. Yazım arka planda (fsync'li atomik yazım)."""
        kaynak, kimlik = self._favori_hedefi()
        if not kimlik:
            self._favori_goster(False)
            return
        self._favori_goster(isaretli)
        run_bg(kutuphane.favori_ayarla, kaynak, kimlik, bool(isaretli),
               self._match_title or anime_title(self._anime),
               cover_url(self._anime) or "")
        self.lblStatus.ok("Kitaplığa eklendi." if isaretli
                          else "Kitaplıktan çıkarıldı.")

    # ── Eşleşme diyaloğu ────────────────────────────────────────────────────
    def open_match_dialog(self) -> None:
        # Sorgu dolu geliyor; diyalog aramaya kendiliğinden başlar (fazladan
        # "Ara" tıklaması yok). Kullanıcı sorguyu değiştirip yeniden arayabilir.
        dialog = AnimeMatchDialog(self._match_title or anime_title(self._anime),
                                  self, ara=True)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selection:
            self.apply_match(*dialog.selection)


__all__ = ["DetailPage", "AnimeMatchDialog", "FAVORI_EKLE", "FAVORI_VAR", "clean_html", "studio_names",
           "genre_names", "meta_line", "tarih_metni", "kunye_birlestir",
           "save_match", "episode_total", "first_slug", "en_iyi_slug",
           "en_iyi_aday", "eslesme_basliklari", "MATCH_LIMIT_PER_SOURCE",
           "AUTO_MATCH_LIMIT", "OTOMATIK_ESLESME_ESIGI", "ARSIV_KAYNAGI"]
