"""Güncelleme servisi ve diyaloğu (eski CTk `UpdateManager`'ın Qt karşılığı).

Ağ ve dosya işleri `common.updater`'da; burada yalnızca thread → sinyal köprüsü
ve arayüz var. Diyalog hiçbir zaman ağ görmez, servis hiçbir zaman widget'a
dokunmaz — indirme ilerlemesi arka plan thread'inden sinyalle taşınır.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

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
        """Çalışan sürüm (diyalogda gösterilir)."""
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


class UpdateDialog(QDialog):
    """Sürüm bilgisi, changelog, indirme ilerlemesi ve "Daha Sonra"."""

    def __init__(self, servis: UpdateService, version_data: Dict[str, Any],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Güncelleme Mevcut")
        self.setModal(True)
        self.setMinimumWidth(460)

        self.servis = servis
        self.version_data = version_data or {}
        self.indirilen: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        head = QLabel("Yeni sürüm yayımlandı")
        head.setObjectName("Subtitle")
        layout.addWidget(head)

        tarih = str(self.version_data.get("release_date") or "")[:10]
        bilgi = (f"Mevcut sürüm: {servis.mevcut_surum}\n"
                 f"Yeni sürüm: {self.version_data.get('version', '?')}")
        if tarih:
            bilgi += f"\nYayın tarihi: {tarih}"
        self.lblInfo = QLabel(bilgi)
        self.lblInfo.setObjectName("Muted")
        layout.addWidget(self.lblInfo)

        layout.addWidget(QLabel("Değişiklikler"))
        self.txtChangelog = QPlainTextEdit(
            str(self.version_data.get("changelog") or "Değişiklik bilgisi yok."))
        self.txtChangelog.setReadOnly(True)
        self.txtChangelog.setFixedHeight(120)
        layout.addWidget(self.txtChangelog)

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
        self.btnOpen = QPushButton("Klasörü Aç")
        self.btnOpen.setVisible(False)
        self.btnOpen.clicked.connect(self._klasoru_ac)
        row.addWidget(self.btnOpen)
        self.btnDownload = QPushButton("Güncellemeyi İndir")
        self.btnDownload.setObjectName("Primary")
        self.btnDownload.clicked.connect(self._indir)
        row.addWidget(self.btnDownload)
        self.btnLater = QPushButton("Daha Sonra")
        self.btnLater.clicked.connect(self.reject)
        row.addWidget(self.btnLater)
        layout.addLayout(row)

        servis.progress.connect(self._on_progress)
        servis.download_ready.connect(self._on_ready)
        servis.download_failed.connect(self._on_failed)

    # ── Davranış ────────────────────────────────────────────────────────────
    def _indir(self) -> None:
        if not self.servis.indir(self.version_data):
            return
        self.btnDownload.setEnabled(False)
        self.btnDownload.setText("İndiriliyor…")
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.lblStatus.setText("Güncelleme indiriliyor…")

    def _on_progress(self, yuzde: int, ayrinti: str) -> None:
        self.bar.setValue(max(0, min(100, yuzde)))
        self.lblStatus.setText(f"İndiriliyor… {ayrinti}")

    def _on_ready(self, yol: str) -> None:
        """İndirme + SHA-256 doğrulaması geçti; kurulum talimatını göster."""
        self.indirilen = yol
        self.bar.setValue(100)
        self.lblStatus.setText("İndirildi ve doğrulandı.\n\n"
                               + updater.kurulum_talimati(yol))
        self.btnDownload.setVisible(False)
        self.btnOpen.setVisible(True)
        self.btnLater.setText("Kapat")

    def _on_failed(self, mesaj: str) -> None:
        self.bar.setVisible(False)
        self.lblStatus.setText(f"Güncelleme indirilemedi: {mesaj}")
        self.lblStatus.setStyleSheet("color: #d63031;")
        self.btnDownload.setEnabled(True)
        self.btnDownload.setText("Tekrar Dene")

    def _klasoru_ac(self) -> None:
        if not self.servis.konumu_ac(self.indirilen):
            self.lblStatus.setText(
                f"Klasör açılamadı. Dosya: {self.indirilen}")


__all__ = ["UpdateService", "UpdateDialog"]
