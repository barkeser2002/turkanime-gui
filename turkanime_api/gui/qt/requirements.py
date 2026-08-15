"""Gereksinim sihirbazı (mpv / ffmpeg / aria2c / yt-dlp).

Eski GUI'de `check_requirements_on_startup` kontrolü tamamen atlıyordu ("Embed
edilmiş araçlar kullanılıyor" yazıp geçiyordu), yani sihirbaz hiç açılmıyordu.
Burada kontrol gerçekten yapılır; ama:

- gömülü araç varsa (paketlenmiş EXE'de `sys._MEIPASS/bin`) hiç sorulmaz,
- kullanıcı "Atla" derse tercih `ayarlar.json`'a yazılır ve bir daha açılmaz
  (Ayarlar sayfasındaki "Gereksinimleri Denetle" bu tercihi geri alır).

Tespit ve kurulum `common.requirements`'ta; burası thread → sinyal köprüsü.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
    QWidget,
)

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


class RequirementsDialog(QDialog):
    """Eksik araçları listeler, indirip kurar, "Atla"yı hatırlar."""

    def __init__(self, servis: RequirementsService, eksikler: Sequence[str],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Eksik Gereksinimler")
        self.setModal(True)
        self.setMinimumWidth(440)

        self.servis = servis
        self.eksikler = list(eksikler)
        self.atlandi = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        head = QLabel("Bazı araçlar bulunamadı")
        head.setObjectName("Subtitle")
        layout.addWidget(head)

        self.lblList = QLabel("\n".join(f"• {ad}" for ad in self.eksikler))
        layout.addWidget(self.lblList)

        note = QLabel("Bunlar olmadan oynatma, birleştirme veya indirme "
                      "çalışmayabilir. Otomatik kurulumu şimdi yapabilirsiniz.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        self.lblStatus = QLabel("")
        self.lblStatus.setObjectName("Muted")
        self.lblStatus.setWordWrap(True)
        layout.addWidget(self.lblStatus)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btnInstall = QPushButton("İndir ve Kur")
        self.btnInstall.setObjectName("Primary")
        self.btnInstall.clicked.connect(self._kur)
        row.addWidget(self.btnInstall)
        self.btnSkip = QPushButton("Atla")
        self.btnSkip.clicked.connect(self._atla)
        row.addWidget(self.btnSkip)
        layout.addLayout(row)

        servis.progress.connect(self._on_progress)
        servis.install_done.connect(self._on_done)

    # ── Davranış ────────────────────────────────────────────────────────────
    def _kur(self) -> None:
        if not self.servis.kur(self.eksikler):
            return
        self.btnInstall.setEnabled(False)
        self.btnInstall.setText("Kuruluyor…")
        self.bar.setVisible(True)
        self.lblStatus.setText("Gereksinimler indiriliyor…")

    def _atla(self) -> None:
        """Bir daha sorma tercihini yaz ve kapat."""
        self.atlandi = self.servis.atlandi_yaz(True)
        self.reject()

    def _on_progress(self, yuzde: int, ayrinti: str) -> None:
        self.bar.setValue(max(0, min(100, yuzde)))
        self.lblStatus.setText(ayrinti)

    def _on_done(self, sonuclar) -> None:
        basarisiz = [(ad, hata) for ad, ok, hata in (sonuclar or []) if not ok]
        if not basarisiz:
            self.lblStatus.setText("Tüm gereksinimler kuruldu.")
            self.btnInstall.setText("Tamamlandı")
            self.btnSkip.setText("Kapat")
            return
        self.lblStatus.setText("Kurulamayanlar:\n" + "\n".join(
            f"• {ad}: {hata}" for ad, hata in basarisiz))
        self.lblStatus.setStyleSheet("color: #d63031;")
        self.btnInstall.setEnabled(True)
        self.btnInstall.setText("Tekrar Dene")


__all__ = ["RequirementsService", "RequirementsDialog"]
