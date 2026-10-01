"""Gerçek tarayıcı erişim işçisi — `common.tarayici_oturum`'u bir thread'de koşturur.

`ErisimIsci` (gömülü QtWebEngine) ile AYNI arayüz: ``start()``, ``one_getir()``,
``stop()`` ve ``on_success/on_error/on_cancel`` geri çağrıları; tam olarak biri
bir kez çağrılır. Böylece `gui/web/uclar_erisim._isci_kur` ikisi arasında tek
satırla seçebiliyor.

selenium + undetected-chromedriver GERÇEK sürücüyü açar (bu işçi onu kendi
açıp kapatıyor, `one_getir`de öne getirebilmek için elinde tutuyor). Sürücü
ayrı bir OS penceresi; bu yüzden iş bir Python thread'inde dönüyor ve sonuç
Qt sinyalleriyle (thread'ler arası kuyruklanır) GUI thread'ine taşınıyor.

İlke değişmedi: doğrulamayı/girişi KULLANICI çözer; `oturum_yakala` yalnızca
sonucu okur. Otomatik tık/çözme yok.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ...common import oturumlar, tarayici_oturum


class TarayiciIsci(QObject):
    """Gerçek tarayıcıda erişim oturumunu bir thread'de yakalar."""

    # İç sinyaller: işçi thread'inden GUI thread'ine (kuyruklu bağlantı).
    _basari = Signal(object)
    _hata = Signal(str)
    _iptal = Signal()
    _durum = Signal(str)

    def __init__(self, hedef: Optional[oturumlar.ErisimHedefi] = None, *,
                 on_status: Optional[Callable[[str], None]] = None,
                 on_success: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.hedef = hedef
        self._basari.connect(lambda s: self._safe(on_success or (lambda _s: None), s))
        self._hata.connect(lambda m: self._safe(on_error or (lambda _m: None), m))
        self._iptal.connect(lambda: self._safe(on_cancel or (lambda: None)))
        self._durum.connect(lambda m: self._safe(on_status or (lambda _m: None), m))
        self._thread: Optional[threading.Thread] = None
        self._surucu: Any = None
        self._iptal_bayragi = False
        self._bitti = False
        self._kilit = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Yakalamayı başlat. Motor hazır değilse False (çağıran gömülüye düşsün).

        Motor hazır değilken hata YAYMIYORUZ: `_isci_kur` zaten `erisim_motoru`
        ile seçtiği için buraya normalde yalnızca motor hazırken geliniyor;
        yine de hazır değilse sessizce False dönüp çağıranın gömülü pencereye
        düşmesine izin veriyoruz (hata penceresi gösterip isteği düşürmek yerine).
        """
        hazir, _sebep = tarayici_oturum.motor_hazir()
        if not hazir:
            return False
        if self.is_running:
            self.one_getir()
            return True
        self._iptal_bayragi = False
        self._bitti = False
        self._durum.emit("Tarayıcı açılıyor; doğrulamayı tarayıcıda siz çözün…")
        self._thread = threading.Thread(target=self._calis, daemon=True)
        self._thread.start()
        return True

    def one_getir(self) -> None:
        surucu = self._surucu
        if surucu is None:
            return
        try:
            surucu.switch_to.window(surucu.current_window_handle)
        except Exception:
            pass

    def stop(self) -> None:
        self._iptal_bayragi = True

    def _calis(self) -> None:
        try:
            self._surucu = tarayici_oturum._surucu_kur(self.hedef)
        except Exception as exc:
            self._bitir_hata(f"Tarayıcı açılamadı: {exc}")
            return
        try:
            sonuc = tarayici_oturum.oturum_yakala(
                self.hedef, surucu=self._surucu,
                iptal=lambda: self._iptal_bayragi)
        except Exception as exc:
            self._bitir_hata(f"Tarayıcı oturumu alınamadı: {exc}")
            return
        finally:
            try:
                self._surucu.quit()
            except Exception:
                pass
            self._surucu = None
        if sonuc is not None:
            with self._kilit:
                if self._bitti:
                    return
                self._bitti = True
            self._basari.emit(sonuc)
        elif self._iptal_bayragi:
            with self._kilit:
                if self._bitti:
                    return
                self._bitti = True
            self._iptal.emit()
        else:
            self._bitir_hata("Doğrulama tamamlanmadı (süre doldu ya da pencere kapatıldı).")

    def _bitir_hata(self, mesaj: str) -> None:
        with self._kilit:
            if self._bitti:
                return
            self._bitti = True
        self._hata.emit(mesaj)

    @staticmethod
    def _safe(cb: Callable, *args) -> None:
        try:
            cb(*args)
        except Exception:
            import traceback
            traceback.print_exc()


__all__ = ["TarayiciIsci"]
