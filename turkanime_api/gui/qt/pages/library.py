"""Kitaplığım — AniList'siz yerel kitaplık: izlemeye devam, favoriler, geçmiş.

Veri `common.kutuphane`'den (``kutuphane.json``); her kayıt kaynağın KENDİ
anime kimliğini taşıyor. Karta tıklamak detay sayfasını o kaynağa BAĞLI açar
ve bölümleri hemen getirir (bkz. `DetailPage.kitaplik_ac`): eşleştirme
diyaloğu yok, "Bölümleri Getir"e ikinci kez basmak yok.

Okuma arka planda: dosya küçük ama `kutuphane_yolu` `Dosyalar`'ı kuruyor
(ayar dosyasını okuyor, eksik anahtarı yazabiliyor) ve sayfa her
gösterimde tazeleniyor.

Ana sayfanın üstündeki "İzlemeye devam et" şeridi de burada (`DevamSeridi`):
yerel olduğu için Jikan/AniList düştüğünde ya da çevrimdışıyken de dolu.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from ....common import kutuphane
from ....sources.kayit import gorunen_ad
from ..gorsel import gorsel_getir
from ..widgets import AnimeCard, StatusLabel
from ..workers import run_bg
from ._grid import CardGrid

# Ana sayfa şeridinde en fazla kaç seri: şerit bir kısayol, kitaplığın
# tamamı "Kitaplığım"da.
SERIT_SINIRI = 8

BOS_DEVAM = "Henüz izlenen bir şey yok. İzlediğiniz bölümler burada görünür."
BOS_FAVORI = "Favori yok. Detay sayfasındaki “♡ Kitaplığa ekle” ile ekleyin."
BOS_GECMIS = "İzleme geçmişi boş."


def devam_kayitlari(veri: Optional[Dict[str, Any]] = None,
                    sinir: Optional[int] = None) -> List[Dict[str, Any]]:
    """İzlemeye devam listesi, son bölümün kayıtlı konumuyla birlikte.

    Konum ayrı bir bölümde duruyor (bkz. `kutuphane.konum_kaydet`); kart
    "5. Bölüm · 12:14" diyebilsin diye burada birleştiriliyor.
    """
    veri = veri if veri is not None else kutuphane.oku()
    sonuc = []
    for seri in kutuphane.devam_listesi(veri, sinir):
        kayit = dict(seri)
        son = seri.get("son") or {}
        kayit["konum"] = kutuphane.konum_getir(
            seri["kaynak"], seri["kimlik"], str(son.get("bolum_slug") or ""), veri)
        sonuc.append(kayit)
    return sonuc


def son_bolum_metni(kayit: Dict[str, Any]) -> str:
    """``"5. Bölüm · 12:14"`` — konum yoksa yalnızca bölüm adı."""
    son = kayit.get("son") or {}
    metin = str(son.get("bolum_baslik") or son.get("bolum_slug") or "")
    konum = kayit.get("konum")
    if isinstance(konum, dict) and konum.get("konum"):
        metin += f" · {kutuphane.sure_metni(konum['konum'])}"
    return metin


def tarih_metni(zaman: Any) -> str:
    try:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(float(zaman)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


class DevamSeridi(QWidget):
    """Ana sayfanın üstündeki yatay "İzlemeye devam et" şeridi.

    Poster kartı değil kompakt düğme: ana sayfanın asıl içeriği keşif ızgarası,
    şerit onu ekranın altına itmemeli.
    """

    secildi = Signal(object)          # kitaplık seri kaydı

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        baslik = QLabel("İzlemeye devam et")
        baslik.setObjectName("Subtitle")
        col.addWidget(baslik)

        self._alan = QScrollArea()
        self._alan.setWidgetResizable(True)
        self._alan.setFrameShape(QScrollArea.Shape.NoFrame)
        self._alan.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._alan.setFixedHeight(64)
        tasiyici = QWidget()
        self._satir = QHBoxLayout(tasiyici)
        self._satir.setContentsMargins(0, 0, 0, 0)
        self._satir.setSpacing(8)
        self._satir.addStretch(1)
        self._alan.setWidget(tasiyici)
        col.addWidget(self._alan)
        self.dugmeler: List[QPushButton] = []
        self.setVisible(False)

    def doldur(self, kayitlar: List[Dict[str, Any]]) -> None:
        for dugme in self.dugmeler:
            dugme.setParent(None)
            dugme.deleteLater()
        self.dugmeler = []
        for kayit in kayitlar or []:
            dugme = QPushButton(f"▶ {kayit.get('baslik') or kayit.get('kimlik')}\n"
                                f"{son_bolum_metni(kayit)}")
            dugme.setObjectName("Card")
            dugme.setToolTip(f"{kayit.get('baslik')} — {gorunen_ad(kayit.get('kaynak') or '')}")
            dugme.clicked.connect(lambda _=False, k=kayit: self.secildi.emit(k))
            self._satir.insertWidget(self._satir.count() - 1, dugme)
            self.dugmeler.append(dugme)
        self.setVisible(bool(self.dugmeler))


class LibraryPage(QWidget):
    """Yerel kitaplık: üç sekme, hepsi `kutuphane.json`'dan."""

    kitaplik_secildi = Signal(object)     # kitaplık seri kaydı
    veri_hazir = Signal(object)           # arka plandan: kutuphane.oku() çıktısı
    thumb_ready = Signal(object, object)  # (AnimeCard, görsel baytları)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._kartlar: Dict[str, List[AnimeCard]] = {"devam": [], "favori": []}
        self.veri_hazir.connect(self._uygula)
        self.thumb_ready.connect(self._kapak_uygula)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        head = QHBoxLayout()
        baslik = QLabel("Kitaplığım")
        baslik.setObjectName("Title")
        head.addWidget(baslik)
        head.addStretch(1)
        self.lblStatus = StatusLabel()
        head.addWidget(self.lblStatus)
        layout.addLayout(head)

        self.tabs = QTabWidget()
        self.gridDevam = CardGrid()
        self.gridFavori = CardGrid()
        self.lstGecmis = QListWidget()
        self.lstGecmis.itemClicked.connect(self._gecmis_tiklandi)
        self.lblBosDevam = self._bos_etiket(BOS_DEVAM)
        self.lblBosFavori = self._bos_etiket(BOS_FAVORI)
        self.lblBosGecmis = self._bos_etiket(BOS_GECMIS)
        for ad, govde, bos in (("İzlemeye devam et", self.gridDevam, self.lblBosDevam),
                               ("Favoriler", self.gridFavori, self.lblBosFavori),
                               ("Geçmiş", self.lstGecmis, self.lblBosGecmis)):
            sayfa = QWidget()
            col = QVBoxLayout(sayfa)
            col.setContentsMargins(0, 8, 0, 0)
            col.addWidget(bos)
            col.addWidget(govde, 1)
            self.tabs.addTab(sayfa, ad)
        layout.addWidget(self.tabs, 1)
        self.lblStatus.info("Yükleniyor…")

    @staticmethod
    def _bos_etiket(metin: str) -> QLabel:
        etiket = QLabel(metin)
        etiket.setObjectName("Muted")
        etiket.setWordWrap(True)
        etiket.setVisible(False)
        return etiket

    # ── Yükleme ─────────────────────────────────────────────────────────────
    def showEvent(self, event):  # noqa: N802 (Qt imzası)
        """Her gösterimde tazele: az önce izlenen bölüm hemen görünmeli."""
        super().showEvent(event)
        self.yenile()

    def yenile(self) -> None:
        run_bg(self._oku)

    def _oku(self) -> None:
        """Arka plan: kitaplığı oku, sonucu sinyalle GUI thread'ine taşı."""
        try:
            self.veri_hazir.emit(kutuphane.oku())
        except RuntimeError:
            pass              # sayfa bu arada yok edildi (kapanış)

    # ── Sonuç (GUI thread'i) ────────────────────────────────────────────────
    def _uygula(self, veri: Dict[str, Any]) -> None:
        devam = devam_kayitlari(veri)
        favori = kutuphane.favoriler(veri)
        gecmis = kutuphane.gecmis_listesi(veri)

        self._kartlar["devam"] = self._kartlari_kur(
            self.gridDevam, devam, son_bolum_metni)
        self._kartlar["favori"] = self._kartlari_kur(
            self.gridFavori, favori, lambda k: gorunen_ad(k.get("kaynak") or ""))
        self.lblBosDevam.setVisible(not devam)
        self.lblBosFavori.setVisible(not favori)

        self.lstGecmis.clear()
        for kayit in gecmis:
            metin = (f"{kayit.get('baslik')} — {kayit.get('bolum_baslik')}"
                     f"   ·   {tarih_metni(kayit.get('zaman'))}"
                     f"   ·   {gorunen_ad(kayit.get('kaynak') or '')}")
            oge = QListWidgetItem(metin)
            oge.setData(Qt.ItemDataRole.UserRole, kayit)
            self.lstGecmis.addItem(oge)
        self.lblBosGecmis.setVisible(not gecmis)
        self.lblStatus.ok(f"{len(devam)} seri izleniyor • {len(favori)} favori")

    def _kartlari_kur(self, grid: CardGrid, kayitlar: List[Dict[str, Any]],
                      rozet) -> List[AnimeCard]:
        kartlar = []
        for kayit in kayitlar:
            kart = AnimeCard(str(kayit.get("baslik") or kayit.get("kimlik")),
                             rozet(kayit), payload=kayit,
                             image_url=kayit.get("kapak") or None)
            kart.clicked.connect(self._kart_tiklandi)
            kartlar.append(kart)
        grid.set_items(list(kartlar))
        for kart in kartlar:
            if kart.image_url:
                run_bg(self._kapak_getir, kart, kart.image_url, gorsel=True)
        return kartlar

    def kartlar(self, sekme: str = "devam") -> List[AnimeCard]:
        """Ekrandaki kartlar ("devam" | "favori") — test ve köprüleme için."""
        return list(self._kartlar.get(sekme) or [])

    def _kart_tiklandi(self, kayit) -> None:
        if isinstance(kayit, dict) and kayit.get("kaynak") and kayit.get("kimlik"):
            self.kitaplik_secildi.emit(kayit)

    def _gecmis_tiklandi(self, oge: QListWidgetItem) -> None:
        self._kart_tiklandi(oge.data(Qt.ItemDataRole.UserRole))

    # ── Kapaklar ────────────────────────────────────────────────────────────
    def _kapak_getir(self, kart, url: str) -> None:
        data = gorsel_getir(url)
        if data:
            try:
                self.thumb_ready.emit(kart, data)
            except RuntimeError:
                pass

    @staticmethod
    def _kapak_uygula(kart, data: bytes) -> None:
        try:
            kart.set_thumbnail(data)
        except RuntimeError:
            pass          # kart bu arada silinmiş (yenileme)


__all__ = ["LibraryPage", "DevamSeridi", "devam_kayitlari", "son_bolum_metni",
           "tarih_metni", "SERIT_SINIRI"]
