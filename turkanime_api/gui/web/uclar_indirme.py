"""İndirilenler sayfasının köprü uçları.

İş `gui.qt.indirme.DownloadManager`'da (kuyruk, duraklat/sürdür, kalıcı
kuyruk dosyası). Buradaki `IndirmeUclari` yöneticinin sinyallerini dinleyip
satırların son hâlini tutuyor — sayfa her açıldığında o anki tabloyu tek
çağrıyla alsın (`indirmeler`) ve sonrası olaylarla gelsin:

``indirme_satir``     bir satır eklendi/durumu değişti/bitti (hemen)
``indirme_ilerleme``  ilerleme yüzdeleri, TOPLU ve seyrek (bkz. ARALIK_MS)
``indirme_silindi``   "Tamamlananları Temizle" ile düşen satırlar

QObject: yönetici sinyalleri işçi thread'lerinden yayıyor; alıcı GUI
thread'inde yaşayan bir QObject olunca Qt çağrıyı kuyruklayıp GUI thread'ine
taşıyor, satır tablosuna iki thread aynı anda dokunmuyor.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from PySide6.QtCore import QObject, QTimer, Slot

from .kopru import Kopru, UcHatasi, uc

# yt-dlp ilerleme kancası saniyede onlarca kez çağrılabiliyor; sayfaya en
# çok bu aralıkla, biriktirilmiş olarak gidiyor.
ARALIK_MS = 250


class IndirmeUclari(QObject):
    """``indirmeler``, ``indirme_eylem``, ``indirme_toplu``."""

    def __init__(self, kopru: Kopru, yonetici, *,
                 oynat: Callable[[Dict[str, Any]], Any],
                 klasor_ac: Callable[[str], bool] = None,
                 indirme_dizini: Callable[[], str] = None):
        super().__init__()
        from ..qt import prefs
        from ..qt.indirme import klasoru_ac
        self._kopru = kopru
        self._yonetici = yonetici
        self._oynat = oynat
        self._klasor_ac = klasor_ac or klasoru_ac
        self._dizin = indirme_dizini or prefs.indirme_dizini
        self._satirlar: Dict[str, Dict[str, Any]] = {}
        self._kirli: set = set()
        self._zamanlayici = QTimer(self)
        self._zamanlayici.setInterval(ARALIK_MS)
        self._zamanlayici.timeout.connect(self._ilerleme_gonder)
        yonetici.added.connect(self._eklendi)
        yonetici.progress.connect(self._ilerledi)
        yonetici.state.connect(self._durum_degisti)
        yonetici.finished.connect(self._bitti)

    # ── Yönetici sinyalleri (GUI thread'i) ──────────────────────────────────
    def _gonder(self, tid: str) -> None:
        satir = self._satirlar.get(tid)
        if satir is not None:
            self._kopru.yay("indirme_satir", {"satir": satir})

    @Slot(str, str)
    def _eklendi(self, tid: str, baslik: str) -> None:
        self._satirlar[tid] = {"id": tid, "baslik": baslik, "durum": "bekliyor",
                               "yuzde": 0, "detay": "", "mesaj": "", "ok": False,
                               "ayrinti": ""}
        self._gonder(tid)

    @Slot(str, int, str)
    def _ilerledi(self, tid: str, yuzde: int, detay: str) -> None:
        satir = self._satirlar.get(tid)
        if satir is None:
            return
        if yuzde >= 0:                  # -1: yalnızca metin ("duraklatılıyor…")
            satir["yuzde"] = min(100, int(yuzde))
        satir["detay"] = detay
        self._kirli.add(tid)
        if not self._zamanlayici.isActive():
            self._zamanlayici.start()

    @Slot()
    def _ilerleme_gonder(self) -> None:
        if not self._kirli:
            self._zamanlayici.stop()
            return
        satirlar = [{"id": t, "yuzde": self._satirlar[t]["yuzde"],
                     "detay": self._satirlar[t]["detay"]}
                    for t in self._kirli if t in self._satirlar]
        self._kirli.clear()
        self._kopru.yay("indirme_ilerleme", {"satirlar": satirlar})

    @Slot(str, str)
    def _durum_degisti(self, tid: str, durum: str) -> None:
        from ..qt.indirme import DURUM_BEKLIYOR
        satir = self._satirlar.get(tid)
        if satir is None:
            return
        satir["durum"] = durum
        if durum == DURUM_BEKLIYOR:     # yeniden deneme / sürdürme
            satir.update(yuzde=0, detay="", mesaj="", ok=False, ayrinti="")
        self._gonder(tid)

    @Slot(str, bool, str)
    def _bitti(self, tid: str, ok: bool, mesaj: str) -> None:
        satir = self._satirlar.get(tid)
        if satir is None:
            return
        satir.update(ok=bool(ok), mesaj=mesaj,
                     ayrinti="" if ok else self._yonetici.ayrinti(tid))
        if ok:
            satir["yuzde"] = 100
        self._kirli.discard(tid)
        self._gonder(tid)

    # ── Sayfanın uçları ─────────────────────────────────────────────────────
    @uc()
    def indirmeler(self) -> Dict[str, Any]:
        return {"satirlar": list(self._satirlar.values()), "klasor": self._dizin()}

    @uc()
    def indirme_eylem(self, id: str, eylem: str) -> bool:  # noqa: A002 (JS adı)
        y = self._yonetici
        if id not in self._satirlar:
            raise UcHatasi("indirme bulunamadı")
        if eylem == "duraklat":
            return y.pause(id)
        if eylem == "devam":
            return y.resume(id)
        if eylem == "iptal":
            return y.cancel(id)
        if eylem == "tekrar":
            if y.retry(id) is None:
                raise UcHatasi("bu bölüm zaten yeniden kuyrukta")
            return True
        if eylem == "oynat":
            kayit = y.kayit(id)
            if not kayit:
                raise UcHatasi("bölüm kaydı yok")
            self._oynat(kayit)
            return True
        if eylem == "klasor":
            return self._klasor(y.klasor(id))
        raise UcHatasi(f"bilinmeyen eylem: {eylem}")

    def _klasor(self, yol: str) -> bool:
        import os
        if not (yol and os.path.isdir(yol)):
            raise UcHatasi("klasör bulunamadı (taşınmış ya da silinmiş olabilir)")
        if not self._klasor_ac(yol):
            raise UcHatasi(f"klasör açılamadı: {yol}")
        return True

    @uc()
    def indirme_toplu(self, eylem: str) -> Dict[str, Any]:
        from ..qt.indirme import BITMIS_DURUMLAR
        y = self._yonetici
        if eylem == "surdur":
            return {"adet": y.resume_all()}
        if eylem == "duraklat":
            return {"adet": y.pause_all()}
        if eylem == "iptal":
            return {"adet": y.cancel_all()}
        if eylem == "temizle":
            idler: List[str] = [t for t, s in self._satirlar.items()
                                if s["durum"] in BITMIS_DURUMLAR]
            for tid in idler:
                self._satirlar.pop(tid, None)
            self._kopru.yay("indirme_silindi", {"idler": idler})
            return {"adet": len(idler)}
        if eylem == "klasor":
            self._klasor(self._dizin())
            return {"adet": 1}
        raise UcHatasi(f"bilinmeyen eylem: {eylem}")


__all__ = ["IndirmeUclari", "ARALIK_MS"]
