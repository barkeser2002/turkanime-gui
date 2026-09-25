"""Arama sayfası — tüm kaynaklarda paralel arama ve sonuç ızgarası.

Eski GUI'deki `SearchWorker` + `display_search_results` akışının Qt karşılığı.
Arama işi `common.adapters.SearchEngine` üzerinden yürür (kaynak listesi
`sources/kayit.py`'de), bu yüzden kaynak eklemek bu sayfada değişiklik
gerektirmez.

Sonuçlar KAYNAK KAYNAK gelir (`SearchEngine.artimli_ara`): yerel arşivin
anlık sonuçları, en yavaş ağ kaynağını (25 sn'ye kadar) beklemeden ekrana
düşer; geç gelen kaynak mevcut kartları silmeden kendi yerine eklenir.
Her arama bir istek kimliği taşır: yeni sorgu süren aramayı beklemeden
başlar, eskisinin geç dönen sonuçları sessizce atılır.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ....sources import kayit
from ....sources.kayit import gorunen_ad
from ..gorsel import gorsel_getir
from ..widgets import AnimeCard, StatusLabel
from ..workers import WorkerSignals, run_bg
from ._grid import CardGrid

# Kaynak başına gösterilecek azami sonuç
LIMIT_PER_SOURCE = 10
# Durum satırında bir kaynağın hata sebebi en çok bu kadar karakter.
HATA_SEBEBI_SINIRI = 300


def _kisalt(metin: str, sinir: int = HATA_SEBEBI_SINIRI) -> str:
    metin = " ".join(str(metin).split())
    return metin if len(metin) <= sinir else metin[:sinir - 1].rstrip() + "…"


def kaynak_sirasi(ad: str) -> Tuple[int, int, str]:
    """Sonuç gruplarının sırası: kayıt sırası, metadata kaynakları EN SONDA.

    Eskiden gruplar ada göre alfabetikti: AniList (yalnızca metadata, oynatma
    yok) en başta, TürkAnime (arşiv, ağsız) en sonda duruyordu — ilk ve en
    göze batan kartlar oynatılamayan kayıtlardı. Kayıtta olmayan ad (eski
    sürüm/test) oynatılabilirlerden sonra, metadata'dan önce; eşitlikte ad.
    """
    kaynak = kayit.bul(ad)
    if kaynak is None:
        return (1, 0, str(ad))
    sira = [k.ad for k in kayit.kaynaklar()].index(kaynak.ad)
    return (2 if kaynak.yalnizca_metadata else 0, sira, str(ad))


class SearchPage(QWidget):
    """Arama sonuçlarını kaynak rozetli kartlar hâlinde gösterir."""

    # (kaynak, slug, başlık, arama kaydı). Son alan kaydın kendisi (slug,
    # title, image…): kartta görünen kapak detay sayfasında da görünsün diye
    # taşınıyor. Kart yükü üçlü kalıyor; kayıt `_kayitlar`'dan bulunuyor.
    anime_selected = Signal(str, str, str, object)
    # (AnimeCard, görsel baytları) — arka plandan UI thread'ine
    thumb_ready = Signal(object, object)
    # Arka plandan UI thread'ine, hepsi istek kimliğiyle:
    # (istek, [kaynak adları]) — "N kaynak bekleniyor" için
    kaynaklar_belli = Signal(object)
    # (istek, kaynak, kayıtlar, hata ya da None) — kaynak bittiği an
    kaynak_geldi = Signal(object)
    # (istek, AramaSonuclari) — arama bitti (zaman aşımları `hatalar`'da)
    arama_bitti = Signal(object)
    # (istek, hata metni) — arama hiç yürütülemedi
    arama_hatasi = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._busy = False
        self._query = ""
        # Yarış koruması: her `start_search` artırır; arka plandan gelen her
        # sinyal kendi kimliğini taşır, eskisi atılır.
        self._istek = 0
        self._cards: List[AnimeCard] = []
        # Kaynak → o kaynağın kartları (dizilişte `kaynak_sirasi`'na göre).
        self._gruplar: Dict[str, List[AnimeCard]] = {}
        self._gelen: Set[str] = set()          # sonucu (ya da hatası) gelenler
        self._bekleyen: Set[str] = set()
        self._hatalar: Dict[str, str] = {}
        # (kaynak, slug) → arama kaydı (bkz. `anime_selected`)
        self._kayitlar: Dict[tuple, Dict[str, Any]] = {}

        # Eski API: `found` tek seferde tam sonuç kümesi (`_on_results`).
        self.signals = WorkerSignals()
        self.signals.connect_found(self._on_results)
        self.signals.connect_error(self._on_error)
        self.thumb_ready.connect(self._apply_thumb)
        self.kaynaklar_belli.connect(self._kaynaklar_belli)
        self.kaynak_geldi.connect(self._kaynak_geldi)
        self.arama_bitti.connect(self._arama_bitti)
        self.arama_hatasi.connect(self._arama_hatasi)

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

        # Keşif sayfasıyla aynı ızgara: kartlar üste hizalı, sütunlar eşit
        # genişlikte, artan yer altta.
        self.results = CardGrid()
        layout.addWidget(self.results, 1)

        self.lblStatus.info("Aramak için yukarıdaki kutuyu kullanın.")

    # ── Arama akışı ─────────────────────────────────────────────────────────
    def start_search(self, query: str) -> None:
        """Aramayı arka planda başlat (UI bloklanmaz).

        Süren arama BEKLENMEZ: eskiden ikinci sorgu "önceki arama sürüyor"
        diye reddediliyordu ve bir yazım hatası 25 sn'ye mal oluyordu. Yeni
        istek kimliği eski aramanın bütün geç sinyallerini geçersiz kılar;
        eski arka plan işi kendi süresinde biter, sonucu okunmaz.
        """
        query = (query or "").strip()
        if not query:
            return
        self._istek += 1
        self._busy = True
        self._query = query
        self.lblTitle.setText(f"Arama — “{query}”")
        self._sifirla()
        self.lblStatus.info("Kaynaklarda aranıyor…")

        run_bg(self._do_search, self._istek, query)

    def _do_search(self, istek: int, query: str) -> None:
        """Arka plan thread'i: tüm kaynaklarda paralel ara, bittikçe bildir.

        Widget'a DOKUNMAZ; yalnızca kimlikli sinyal yayar. Hata burada
        yakalanıyor: `run_bg`'nin genel hata sinyali kimlik taşımaz, eski
        aramanın hatası yeni aramanın ekranına düşerdi.
        """
        from ....common.adapters import SearchEngine

        try:
            engine = SearchEngine()
            adlar = list(getattr(engine, "adapters", None) or [])
            if adlar:
                self._yay(self.kaynaklar_belli, (istek, adlar))
            if hasattr(engine, "artimli_ara"):
                results = engine.artimli_ara(
                    query,
                    lambda ad, kayitlar, hata: self._yay(
                        self.kaynak_geldi, (istek, ad, kayitlar, hata)),
                    limit_per_source=LIMIT_PER_SOURCE)
            else:
                # Eski sözleşmeli motor (testlerdeki sahteler): tek seferde.
                results = engine.search_all_sources_rich(
                    query, limit_per_source=LIMIT_PER_SOURCE)
        except Exception as exc:
            self._yay(self.arama_hatasi, (istek, str(exc) or type(exc).__name__))
            return
        self._yay(self.arama_bitti, (istek, results))

    @staticmethod
    def _yay(sinyal, yuk) -> None:
        """Sayfa kapanmışsa (C++ nesnesi silinmiş) sinyal sessizce düşer."""
        try:
            sinyal.emit(yuk)
        except RuntimeError:
            pass

    def cards(self) -> List[AnimeCard]:
        """Ekrandaki kartlar (test ve köprüleme için) — `DiscoverPage` ile aynı."""
        return list(self._cards)

    def _fetch_thumb(self, card, url: str) -> None:
        """Arka plan: görseli indir, baytları UI thread'ine taşı.

        Widget'a burada DOKUNULMAZ; yalnızca sinyal yayılır (kart bu arada
        silinmiş olabilir, o yüzden slot tarafında da kontrol var).
        """
        # Tek yol `gorsel_getir`: bellek/disk önbelleği, durum kodu ve
        # görsel imzası denetimi orada (bkz. `gorsel.py`).
        data = gorsel_getir(url)
        if data:
            self.thumb_ready.emit(card, data)

    def _apply_thumb(self, card, data: bytes) -> None:
        """GUI thread'i: görseli karta yerleştir (kart hâlâ yaşıyorsa)."""
        try:
            card.set_thumbnail(data)
        except RuntimeError:
            pass          # kart bu arada silinmiş (yeni arama)

    # ── Sonuç işleme (GUI thread'i) ─────────────────────────────────────────
    def _sifirla(self) -> None:
        """Ekrandaki sonuçları ve toplama durumunu temizle."""
        self._cards = []
        self._gruplar = {}
        self._gelen = set()
        self._bekleyen = set()
        self._hatalar = {}
        self._kayitlar = {}
        self.results.clear()

    def _kaynaklar_belli(self, payload) -> None:
        istek, adlar = payload
        if istek != self._istek:
            return
        self._bekleyen = set(adlar) - self._gelen
        self._ara_durum()

    def _kaynak_geldi(self, payload) -> None:
        """Bir kaynak bitti: kartlarını kendi sırasındaki yere ekle."""
        istek, kaynak, kayitlar, hata = payload
        if istek != self._istek:
            return               # eski aramanın geç sonucu — ekranı EZMESİN
        self._gelen.add(kaynak)
        self._bekleyen.discard(kaynak)
        if hata:
            self._hatalar[kaynak] = str(hata)
        if self._grup_ekle(kaynak, kayitlar):
            self._diz()
        self._ara_durum()

    def _arama_bitti(self, payload) -> None:
        """Arama bitti: gelmemiş grupları ekle (eski motor), son durumu yaz."""
        istek, results = payload
        if istek != self._istek:
            return
        self._busy = False
        self._bekleyen = set()
        if not isinstance(results, dict):
            self.lblStatus.error("Beklenmeyen arama sonucu.")
            return
        eklendi = False
        for kaynak, kayitlar in results.items():
            if kaynak not in self._gelen:
                self._gelen.add(kaynak)
                eklendi = self._grup_ekle(kaynak, kayitlar) or eklendi
        if eklendi:
            self._diz()
        # Hata veren ve süreye yetişemeyen kaynaklar (`AramaSonuclari.hatalar`).
        # Sahte motorlar düz dict döndürüyor.
        for kaynak, sebep in (getattr(results, "hatalar", None) or {}).items():
            self._hatalar.setdefault(kaynak, str(sebep))
        self._son_durum()

    def _arama_hatasi(self, payload) -> None:
        istek, mesaj = payload
        if istek != self._istek:
            return
        self._on_error(mesaj)

    def _on_results(self, results: Dict[str, List[Dict[str, Any]]]) -> None:
        """Tam sonuç kümesini TEK SEFERDE göster (öncekiler silinir).

        Sözleşme `SearchEngine.search_all_sources_rich`'in döndürdüğüdür: kayıt
        bir SÖZLÜK (slug/title/image), `(slug, title)` çifti DEĞİL. Artımlı
        akış da aynı gruplama yolundan geçiyor (`_grup_ekle`).
        """
        self._sifirla()
        self._arama_bitti((self._istek, results))

    def _grup_ekle(self, source: str, items: Any) -> bool:
        """Bir kaynağın kayıtlarını karta çevir; kart eklendiyse True."""
        cards: List[AnimeCard] = []
        for item in (items or []):
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
            # Kartta insana dönük etiket ("TürkAnime (arşiv)": kullanıcı
            # sonucun kapanan siteden değil arşivden geldiğini görsün);
            # yükte kanonik ad — köprü ve eşleşme kaydı onu bekliyor.
            card = AnimeCard(title, gorunen_ad(source),
                             payload=(source, slug, title), image_url=image)
            card.clicked.connect(self._on_card_clicked)
            self._kayitlar[(source, slug)] = dict(item)
            cards.append(card)
        if not cards:
            return False
        self._gruplar[source] = cards
        # Görseller ayrı havuzda. Kart yerleşmeden başlasa da sorun yok:
        # sinyal kuyruklu, kart bu arada silinirse `_apply_thumb` yutar.
        for card in cards:
            if card.image_url:
                run_bg(self._fetch_thumb, card, card.image_url, gorsel=True)
        return True

    def _diz(self) -> None:
        """Grupları `kaynak_sirasi`'na göre diz; mevcut kartlar SİLİNMEZ."""
        self._cards = [card for kaynak in sorted(self._gruplar, key=kaynak_sirasi)
                       for card in self._gruplar[kaynak]]
        self.results.set_items(list(self._cards))

    def _kaynak_dokumu(self) -> str:
        # Sayaç atılan kayıtları değil GÖSTERİLENLERİ sayar; aksi hâlde kaynak
        # dökümünün toplamı üstteki toplamı tutmuyordu.
        return ", ".join(f"{gorunen_ad(k)}: {len(self._gruplar[k])}"
                         for k in sorted(self._gruplar, key=kaynak_sirasi))

    def _ara_durum(self) -> None:
        """Arama sürerken: gelenler + kaç kaynağın beklendiği."""
        if not self._busy:
            return
        bekleyen = len(self._bekleyen)
        ek = f"{bekleyen} kaynak bekleniyor…" if bekleyen else "tamamlanıyor…"
        if self._cards:
            self.lblStatus.ok(f"{len(self._cards)} sonuç — {self._kaynak_dokumu()}"
                              f" · {ek}")
        else:
            self.lblStatus.info(f"Kaynaklarda aranıyor… ({ek})")

    def _son_durum(self) -> None:
        hatalar = self._hatalar
        if not self._cards:
            # Önceki aramanın kartları ekranda kalmamalı: "sonuç bulunamadı"
            # yazarken altta eski sonuçları göstermek doğrudan yalan olurdu.
            self._cards = []
            self.results.clear()
            metin = f"“{self._query}” için sonuç bulunamadı."
            if hatalar:
                # "Sonuç yok" ile "aranamadı" farklı: TürkAnime arşivi
                # okunamadığında (aynalar kapalı, önbellek boş) kullanıcı ağı
                # ya da arşivi düzeltebilir. Kırpılıyor: bazı ağ hataları
                # (curl) sayfa dolusu metin taşıyor.
                metin += " Aranamayan kaynak: " + "; ".join(
                    f"{gorunen_ad(ad)} — {_kisalt(sebep)}"
                    for ad, sebep in sorted(hatalar.items()))
            self.lblStatus.error(metin)
            return

        metin = f"{len(self._cards)} sonuç — {self._kaynak_dokumu()}"
        if hatalar:
            # Sonuç varken yalnızca adlar (zaman aşımı ayrıca): satırı
            # sebeplerle doldurmayalım.
            asan = sorted(ad for ad, sebep in hatalar.items()
                          if "zaman aşımı" in str(sebep))
            diger = sorted(ad for ad in hatalar if ad not in asan)
            if diger:
                metin += " · aranamayan: " + ", ".join(gorunen_ad(ad) for ad in diger)
            if asan:
                metin += " · zaman aşımı: " + ", ".join(gorunen_ad(ad) for ad in asan)
        self.lblStatus.ok(metin)

    def _on_error(self, message: str) -> None:
        self._busy = False
        self.lblStatus.error(f"Arama hatası: {message}")

    def _on_card_clicked(self, payload: Optional[tuple]) -> None:
        if not payload:
            return
        source, slug, title = payload
        secilen = dict(self._kayitlar.get((source, slug)) or {})
        self.anime_selected.emit(source, slug, title, secilen)


__all__ = ["SearchPage", "kaynak_sirasi", "LIMIT_PER_SOURCE"]
