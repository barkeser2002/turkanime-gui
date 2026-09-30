"""Güncelleme servisi (eski CTk `UpdateManager`'ın Qt karşılığı).

Ağ ve dosya işleri `common.updater`'da; burada yalnızca thread → sinyal
köprüsü var. Pencere web arayüzünde (`gui.web.pencereler.GuncellemePenceresi`)
ve hiçbir zaman ağ görmez; servis hiçbir zaman pencereye dokunmaz — indirme
ilerlemesi arka plan thread'inden sinyalle taşınır.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal

from . import prefs
from .workers import run_bg
from ...common import updater


class UpdateService(QObject):
    """Sürüm denetimi ve paket indirme; her ikisi de arka planda."""

    update_available = Signal(object)   # version.json sözlüğü
    up_to_date = Signal()
    check_failed = Signal(str)
    progress = Signal(int, str)         # yüzde, ayrıntı
    download_ready = Signal(str)        # indirilen dosyanın yolu
    download_failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._mevcut = updater.mevcut_surum()
        self._calisiyor = False

    @property
    def mevcut_surum(self) -> str:
        """Çalışan sürüm (pencerede gösterilir)."""
        return self._mevcut

    # ── Denetim ─────────────────────────────────────────────────────────────
    def kontrol_et(self, sessiz: bool = True) -> None:
        """`version.json`'ı arka planda getir ve sürümü karşılaştır.

        `sessiz=True` (açılış denetimi) hata yaymaz: kullanıcı uygulamayı anime
        izlemek için açtı, GitHub'a erişilemedi diye uyarı görmesinin anlamı yok.
        """
        run_bg(self._kontrol, bool(sessiz))

    def _kontrol(self, sessiz: bool) -> None:
        try:
            veri = updater.surum_bilgisi_getir()
        except Exception as exc:
            if not sessiz:
                self.check_failed.emit(f"Güncelleme kontrolü yapılamadı: {exc}")
            return
        if updater.guncelleme_var_mi(veri, self._mevcut):
            self.update_available.emit(veri)
        else:
            self.up_to_date.emit()

    # ── İndirme ─────────────────────────────────────────────────────────────
    def indir(self, version_data: Dict[str, Any]) -> bool:
        """Paketi ayarlardaki indirme klasörüne indir (arka planda).

        Aynı anda ikinci indirme başlatılmaz: iki iş aynı dosyaya yazar ve
        ikisi de checksum'dan geçemez.
        """
        if self._calisiyor:
            return False
        paket = updater.platform_paketi(version_data or {})
        if paket is None:
            self.download_failed.emit("Bu platform için güncelleme paketi yok.")
            return False
        self._calisiyor = True
        # long_running: paket onlarca MB; kısa UI görevlerinin havuzunu tutmasın.
        run_bg(self._indir, paket["url"], paket["checksum"], long_running=True)
        return True

    def _indir(self, url: str, checksum: str) -> None:
        try:
            # Hedef klasör ayardan: eski sürüm dosyayı `indirilenler`e indirip
            # sonra hep `~/Downloads`'u açıyordu.
            yol = updater.indir_ve_dogrula(
                url, prefs.indirme_dizini(), checksum, self._ilerleme)
        except Exception as exc:
            self.download_failed.emit(str(exc))
            return
        finally:
            self._calisiyor = False
        self.download_ready.emit(yol)

    def _ilerleme(self, inen: int, toplam: int) -> None:
        yuzde = int(inen * 100 / toplam) if toplam else 0
        self.progress.emit(yuzde, f"{inen // 1048576} MB"
                           + (f" / {toplam // 1048576} MB" if toplam else ""))

    # ── Kurulum sonrası ─────────────────────────────────────────────────────
    @staticmethod
    def konumu_ac(dosya_yolu: str) -> bool:
        """İndirilen dosyanın klasörünü aç (sabit `~/Downloads` değil)."""
        return updater.konumu_ac(os.path.dirname(dosya_yolu)
                                 or prefs.indirme_dizini())


__all__ = ["UpdateService"]
