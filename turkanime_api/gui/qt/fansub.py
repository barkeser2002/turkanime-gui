"""Fansub seçimi — ayarlardaki "Fansub'u kendim seçeyim" arayüzde de çalışsın.

NEDEN VAR: Ayar kaydediliyordu ama yalnızca CLI soruyordu; arayüz her bölümde
`best_video`'nun oynatıcı önceliğiyle seçtiği akışı açıyordu. Arşivde her üç
bölümden biri birden çok fansub'la duruyor (tam taramada 71 bin bölümün
%31,7'si), yani bir seri içinde çeviri grubu bölümden bölüme değişebiliyordu.

Akış (hepsi GUI thread'inde, ağ işi arka planda):

1. `FansubSecici.iste(entry, devam)`: seri için hatırlanan seçim varsa
   ``devam(True, fansub)`` hemen çağrılır, soru yok.
2. Yoksa bölümün fansub listesi (`AdapterBolum.fansubs` — akışları getirir,
   yani ağ) ARKA PLANDA okunur. Aynı seri için gelen diğer istekler (toplu
   indirmede 12 bölüm) bekleyen listesine girer; liste bir kez getirilir,
   soru bir kez sorulur ve cevap hepsine uygulanır.
3. Birden çok fansub varsa kullanıcıya oynatıcı/kalite özetiyle sorulur;
   "Otomatik" de bir seçenek. Tek fansub (ya da fansub kavramı olmayan
   kaynak) sorulmaz. İptal: ``devam(False, None)``.

`fansubs`'un getirdiği akışlar `AdapterBolum`'da bekletiliyor ve ilk
`best_video` onları tüketiyor: soru ağ turunu iki katına çıkarmıyor.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget,
)

from . import prefs
from .workers import UiBridge, run_bg

# "Otomatik" seçimi: `best_video` fansub süzmeden, oynatıcı önceliğiyle seçer.
OTOMATIK = ""

Devam = Callable[[bool, Optional[str]], Any]


def fansub_ozeti(bolum: Any) -> Dict[str, List[str]]:
    """Fansub → ["SIBNET 1080p", "MAIL 720p", …] (akış sırasıyla, tekrarsız).

    `AdapterBolum.fansubs` akışları `_bekleyen_akislar`'da bırakıyor (ilk
    `best_video` onları kullanıyor); özet oradan okunur, ikinci istek yok.
    Kaynak modülüne dokunulmadığı için özel öznitelik okunuyor; yoksa (eski
    nesne, sahte) özet boş kalır ve diyalog yalnızca adları gösterir.
    """
    ozet: Dict[str, List[str]] = {}
    for akis in getattr(bolum, "_bekleyen_akislar", None) or []:
        if not isinstance(akis, dict):
            continue
        ad = str(akis.get("fansub") or "").strip()
        if not ad:
            continue
        parca = " ".join(str(x) for x in (akis.get("player"), akis.get("label")) if x)
        satir = ozet.setdefault(ad, [])
        if parca and parca not in satir:
            satir.append(parca)
    return ozet


class FansubDialog(QDialog):
    """Fansub listesi + "Otomatik" + "Bu seri için hatırla"."""

    def __init__(self, baslik: str, fansubs: List[str],
                 ozet: Optional[Dict[str, List[str]]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Fansub seç")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        aciklama = QLabel(f"“{baslik}” birden çok çeviri grubuyla var. "
                          "Hangisi oynatılsın/indirilsin?")
        aciklama.setWordWrap(True)
        layout.addWidget(aciklama)

        self.liste = QListWidget()
        otomatik = QListWidgetItem("Otomatik — en iyi çalışan video")
        otomatik.setData(Qt.ItemDataRole.UserRole, OTOMATIK)
        self.liste.addItem(otomatik)
        for ad in fansubs:
            ayrinti = ", ".join((ozet or {}).get(ad, [])[:4])
            oge = QListWidgetItem(f"{ad} — {ayrinti}" if ayrinti else ad)
            oge.setData(Qt.ItemDataRole.UserRole, ad)
            self.liste.addItem(oge)
        self.liste.setCurrentRow(1 if fansubs else 0)
        self.liste.itemDoubleClicked.connect(lambda _o: self.accept())
        layout.addWidget(self.liste)

        self.chkHatirla = QCheckBox("Bu seri için hatırla (bir daha sorma)")
        self.chkHatirla.setChecked(True)
        layout.addWidget(self.chkHatirla)

        dugmeler = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                    | QDialogButtonBox.StandardButton.Cancel)
        dugmeler.accepted.connect(self.accept)
        dugmeler.rejected.connect(self.reject)
        layout.addWidget(dugmeler)

    @property
    def secim(self) -> str:
        oge = self.liste.currentItem()
        return str(oge.data(Qt.ItemDataRole.UserRole)) if oge is not None else OTOMATIK


class FansubSecici(QObject):
    """Seri başına fansub tercihi + tek seferlik soru (bkz. modül belgesi)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pencere = parent
        self.ui = UiBridge(self)
        # Seri anahtarı → seçilen fansub (OTOMATIK dahil). Oturum boyunca:
        # kalıcı saklamak, kaynaktaki grup adı değişince eski tercihi
        # sessizce "otomatik"e düşürürdü; her açılışta bir soru ucuz.
        self.tercih: Dict[str, str] = {}
        self._bekleyen: Dict[str, List[Devam]] = {}

    @staticmethod
    def anahtar(entry: Dict[str, Any]) -> str:
        """Seri kimliği: kaynak + kaynağın anime kimliği (yoksa seri slug'ı)."""
        kimlik = str(entry.get("kimlik") or "")
        if not kimlik:
            kimlik = prefs.bolum_kimligi(entry.get("obj"))[0]
        return f"{entry.get('kaynak') or ''}:{kimlik}"

    def iste(self, entry: Dict[str, Any], devam: Devam) -> None:
        """Bu bölüm için fansub'u çöz, sonra ``devam(tamam, fansub)`` (GUI thread'i)."""
        anahtar = self.anahtar(entry)
        if anahtar in self.tercih:
            devam(True, self.tercih[anahtar] or None)
            return
        bekleyen = self._bekleyen.setdefault(anahtar, [])
        bekleyen.append(devam)
        if len(bekleyen) == 1:
            baslik = str(entry.get("seri_adi") or entry.get("title") or "Bölüm")
            run_bg(self._getir, anahtar, entry.get("obj"), baslik)

    def _getir(self, anahtar: str, bolum: Any, baslik: str) -> None:
        """Arka plan: fansub listesini (ağ) oku, cevabı GUI thread'ine yolla."""
        fansubs: List[str] = []
        ozet: Dict[str, List[str]] = {}
        try:
            fansubs = [str(f) for f in (getattr(bolum, "fansubs", None) or [])]
            ozet = fansub_ozeti(bolum)
        except Exception as exc:          # liste yüzünden oynatma düşmesin
            print(f"[Fansub] liste okunamadı: {exc}")
        finally:
            try:
                self.ui.post(lambda: self._cevapla(anahtar, baslik, fansubs, ozet))
            except RuntimeError:          # pencere kapandı
                pass

    def _cevapla(self, anahtar: str, baslik: str, fansubs: List[str],
                 ozet: Dict[str, List[str]]) -> None:
        bekleyenler = self._bekleyen.pop(anahtar, [])
        if len(fansubs) <= 1:
            # Soru yok ve HATIRLANMAZ: serinin sonraki bölümünde birden çok
            # grup olabilir.
            for devam in bekleyenler:
                devam(True, None)
            return
        cevap = self.sor(baslik, fansubs, ozet)
        if cevap is None:
            for devam in bekleyenler:
                devam(False, None)
            return
        secim, hatirla = cevap
        if hatirla:
            self.tercih[anahtar] = secim
        for devam in bekleyenler:
            devam(True, secim or None)

    def sor(self, baslik: str, fansubs: List[str],
            ozet: Dict[str, List[str]]) -> Optional[Tuple[str, bool]]:
        """Diyaloğu aç: ``(fansub ya da OTOMATIK, hatırla)``; iptalde None.

        Testler bu metodu sahteler (modal diyalog testi bekletir).
        """
        dialog = FansubDialog(baslik, fansubs, ozet, self._pencere)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.secim, dialog.chkHatirla.isChecked()


__all__ = ["FansubSecici", "FansubDialog", "fansub_ozeti", "OTOMATIK"]
