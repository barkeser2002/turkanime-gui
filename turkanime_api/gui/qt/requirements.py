"""Gereksinim sihirbazı (mpv / ffmpeg / aria2c / yt-dlp).

Eski GUI'de `check_requirements_on_startup` kontrolü tamamen atlıyordu ("Embed
edilmiş araçlar kullanılıyor" yazıp geçiyordu), yani sihirbaz hiç açılmıyordu.
Burada kontrol gerçekten yapılır; ama:

- gömülü araç varsa (paketlenmiş EXE'de `sys._MEIPASS/bin`) hiç sorulmaz,
- kullanıcı "Atla" derse tercih `ayarlar.json`'a yazılır ve bir daha açılmaz
  (Ayarlar sayfasındaki "Gereksinimleri Denetle" bu tercihi geri alır).

Tespit ve kurulum `common.requirements`'ta; burası thread → sinyal köprüsü.
Sihirbaz penceresi web arayüzünde (`gui.web.pencereler.GereksinimPenceresi`).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal

from . import prefs
from .workers import run_bg
from ...common import requirements as core


class RequirementsService(QObject):
    """Eksik araçları arka planda tespit eder ve kurar."""

    missing_found = Signal(object)     # eksik araç adları (liste)
    all_present = Signal()
    progress = Signal(int, str)        # yüzde, ayrıntı
    install_done = Signal(object)      # [(ad, başarılı mı, hata), ...]

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._calisiyor = False

    # ── Tespit ──────────────────────────────────────────────────────────────
    def denetle(self, kullanici_istegi: bool = False) -> bool:
        """Eksik araç var mı? (`kullanici_istegi=False` → açılış denetimi)

        Açılış denetimi "Atla" tercihine saygı duyar; kullanıcı Ayarlar'dan
        kendisi istediyse tercih yok sayılır — aksi hâlde bir kez atlayan
        kullanıcı sihirbazı bir daha hiç açamazdı.
        """
        if not kullanici_istegi and prefs.oku().gereksinim_atlandi:
            return False
        # subprocess çağrıları (araç başına `--version`) saniyeler sürebiliyor;
        # açılışta arayüzü bekletmemek için arka planda.
        run_bg(self._denetle)
        return True

    def _denetle(self) -> None:
        eksikler = core.eksik_araclar()
        if eksikler:
            self.missing_found.emit(eksikler)
        else:
            self.all_present.emit()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def kur(self, eksikler: Sequence[str]) -> bool:
        """Eksik araçları indir ve kur (arka planda)."""
        if self._calisiyor or not eksikler:
            return False
        self._calisiyor = True
        run_bg(self._kur, list(eksikler), long_running=True)
        return True

    def _kur(self, eksikler: List[str]) -> None:
        sonuclar: List[Tuple[str, bool, str]] = []
        try:
            try:
                liste = core.gereksinim_listesi_getir()
            except Exception as exc:
                self.install_done.emit([(ad, False, f"liste alınamadı: {exc}")
                                        for ad in eksikler])
                return

            hedef = self._hedef_dizin()
            toplam = len(eksikler)
            for sira, ad in enumerate(eksikler, start=1):
                taban = int((sira - 1) * 100 / toplam)
                self.progress.emit(taban, f"{ad} indiriliyor…")

                def ilerleme(inen: int, boyut: int, _t=taban, _n=toplam) -> None:
                    pay = int(inen * 100 / boyut) if boyut else 0
                    self.progress.emit(_t + pay // _n, f"{inen // 1048576} MB")

                try:
                    core.indir_ve_kur(ad, core.paket_url(liste, ad), hedef,
                                      ilerleme)
                except Exception as exc:
                    sonuclar.append((ad, False, str(exc)))
                    continue
                sonuclar.append((ad, True, ""))
            # Yeni kurulan araçlar yeniden başlatmadan bulunabilsin.
            core.path_hazirla()
        finally:
            self._calisiyor = False
        self.progress.emit(100, "tamamlandı")
        self.install_done.emit(sonuclar)

    @staticmethod
    def _hedef_dizin() -> str:
        """Araçların kurulacağı klasör: uygulamanın kendi dizini.

        İndirilenler klasörü DEĞİL — oraya konan bir mpv.exe kullanıcının
        indirmelerine karışır ve PATH'e de girmez.
        """
        from ...cli.dosyalar import Dosyalar
        return Dosyalar().ta_path

    # ── Tercih ──────────────────────────────────────────────────────────────
    @staticmethod
    def atlandi_yaz(atlandi: bool) -> bool:
        """"Atla" tercihini kalıcılaştır."""
        return prefs.ayar_yaz(gereksinim_atlandi=bool(atlandi))


__all__ = ["RequirementsService"]
