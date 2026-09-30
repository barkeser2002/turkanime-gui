"""Gereksinim sihirbazı (mpv / ffmpeg / aria2c / yt-dlp + önerilen FlareSolverr).

Eski GUI'de `check_requirements_on_startup` kontrolü tamamen atlıyordu ("Embed
edilmiş araçlar kullanılıyor" yazıp geçiyordu), yani sihirbaz hiç açılmıyordu.
Burada kontrol gerçekten yapılır; ama:

- gömülü araç varsa (paketlenmiş EXE'de `sys._MEIPASS/bin`) hiç sorulmaz,
- kullanıcı "Atla" derse tercih `ayarlar.json`'a yazılır ve bir daha açılmaz
  (Ayarlar sayfasındaki "Gereksinimleri Denetle" bu tercihi geri alır).

FlareSolverr isteğe bağlı: eksikse listeye "flaresolverr" adıyla ekleniyor,
pencere onu ayrı (seçilebilir) satırda gösteriyor. Kurulumu
`gereksinimler.json`'dan değil `common.flaresolverr`'dan (sabit sürüm +
SHA-256).

Tespit ve kurulum `common.requirements`'ta; burası thread → sinyal köprüsü.
Sihirbaz penceresi web arayüzünde (`gui.web.pencereler.GereksinimPenceresi`).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal

from . import prefs
from .workers import run_bg
from ...common import flaresolverr
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
        eksikler = core.eksik_araclar() + core.onerilen_eksikler()
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
            araclar = [ad for ad in eksikler if ad != flaresolverr.AD]
            liste: list = []
            liste_hatasi = ""
            if araclar:
                # Liste yalnızca `gereksinimler.json` araçları için gerekli;
                # alınamaması FlareSolverr kurulumunu (kendi sabit adresi var)
                # engellememeli.
                try:
                    liste = core.gereksinim_listesi_getir()
                except Exception as exc:
                    liste_hatasi = f"liste alınamadı: {exc}"

            hedef = self._hedef_dizin()
            toplam = len(eksikler)
            for sira, ad in enumerate(eksikler, start=1):
                taban = int((sira - 1) * 100 / toplam)
                gorunen = "FlareSolverr" if ad == flaresolverr.AD else ad
                self.progress.emit(taban, f"{gorunen} indiriliyor…")

                def ilerleme(inen: int, boyut: int, _t=taban, _n=toplam,
                             _ad=gorunen) -> None:
                    pay = int(inen * 100 / boyut) if boyut else 0
                    self.progress.emit(_t + pay // _n, f"{_ad}: {inen // 1048576}"
                                       + (f" / {boyut // 1048576} MB" if boyut else " MB"))

                try:
                    if ad == flaresolverr.AD:
                        # Yönetici üzerinden: veri kökündeki kopya çalışıyorsa
                        # önce durdurulur, Ayarlar sayfası da ilerlemeyi görür.
                        flaresolverr.yonetici().kur(ilerleme=ilerleme)
                    elif liste_hatasi:
                        raise RuntimeError(liste_hatasi)
                    else:
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
