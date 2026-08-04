"""Oynatma sonrası "kaçıncı bölümü tamamladınız?" diyaloğu.

Eski GUI'deki `show_progress_dialog` karşılığı. Bir fark var: eski sürüm girilen
numarayı doğrudan AniList'e yazıyordu ve AniList kimliği yoksa hiçbir yere
kaydetmiyordu — yani AniList'e bağlı olmayan kullanıcı için diyalog tamamen boşa
çalışıyordu. Burada ilerleme önce YEREL geçmişe yazılır; AniList yazımı Faz
7'de `progress_saved` sinyaline bağlandı (bkz. `app.MainWindow._ask_progress`).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QSpinBox, QPushButton, QVBoxLayout, QWidget,
)

from . import prefs
from ...common.episode_parser import extract_episode_info


def anime_adi(bolum, yedek: str = "") -> str:
    """Seri adı — `Bolum.title` gibi ağa çıkabilecek alanlara dokunmadan.

    Diyalog dışında da kullanılıyor (Discord durumu), bu yüzden genel.
    """
    anime = getattr(bolum, "anime", None)
    for alan in ("_title", "title", "slug"):
        deger = getattr(anime, alan, None)
        if isinstance(deger, str) and deger:
            return deger
    return yedek or "Bilinmeyen anime"


class ProgressDialog(QDialog):
    """İzleme ilerlemesini soran modal diyalog."""

    progress_saved = Signal(str, int)   # seri slug, bölüm no

    def __init__(self, bolum, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("İzleme İlerlemesi")
        self.setModal(True)

        self.seri, bolum_slug = prefs.bolum_kimligi(bolum)
        bolum_adi = title or bolum_slug
        # Okunabilir seri adı öznitelikte: AniList araması slug ("naruto-test")
        # yerine bununla yapılır, aksi hâlde eşleşme çok daha zayıf olur.
        self.anime_adi = anime_adi(bolum, self.seri)
        # Numara başlıktan çıkarılır; kullanıcı yine de düzeltebilsin diye
        # alan salt-okunur değil (kaynaklar bölümü sık sık yanlış adlandırıyor).
        # Anime adı da veriliyor: başlık adı içerdiği için ("86 2nd Season 5.
        # Bölüm") addaki rakam bölüm sanılıyor ve AniList'e 86 yazılıyordu.
        _, self.bolum_no = extract_episode_info(bolum_adi, self.anime_adi)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        head = QLabel("İzleme İlerlemesi Kaydet")
        head.setObjectName("Subtitle")
        layout.addWidget(head)

        info = QLabel(f"{self.anime_adi}\n{bolum_adi}")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel("Kaçıncı bölümü tamamladınız?"))
        self.spnEpisode = QSpinBox()
        self.spnEpisode.setRange(1, 9999)
        self.spnEpisode.setValue(max(1, int(self.bolum_no or 1)))
        layout.addWidget(self.spnEpisode)

        self.lblNote = QLabel("")
        self.lblNote.setObjectName("Muted")
        self.lblNote.setWordWrap(True)
        layout.addWidget(self.lblNote)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btnSave = QPushButton("Kaydet")
        self.btnSave.setObjectName("Primary")
        self.btnSave.clicked.connect(self.save)
        row.addWidget(self.btnSave)
        self.btnSkip = QPushButton("Atla")
        self.btnSkip.clicked.connect(self.reject)
        row.addWidget(self.btnSkip)
        layout.addLayout(row)

        if not self.seri:
            # Seri kimliği yoksa ilerlemeyi bağlayacağımız anahtar da yok;
            # kaydediyormuş gibi yapıp sessizce kaybetmektense söylüyoruz.
            self.btnSave.setEnabled(False)
            self.lblNote.setText("Seri kimliği bulunamadı, ilerleme kaydedilemiyor.")

    def save(self) -> None:
        """Yerel ilerlemeyi yaz ve sinyali yay (AniList: Faz 7)."""
        no = int(self.spnEpisode.value())
        if not prefs.ilerleme_kaydet(self.seri, no):
            self.lblNote.setText("İlerleme kaydedilemedi.")
            return
        self.progress_saved.emit(self.seri, no)
        self.accept()


__all__ = ["ProgressDialog", "anime_adi"]
