"""Ayarlar sayfası.

Ayarlar `cli.dosyalar.Dosyalar` üzerinden okunur/yazılır (CLI ile ortak dosya),
böylece iki arayüz aynı yapılandırmayı paylaşır.

Buradaki en önemli iş **TRAnimeİzle cookie'si**: o kaynak bot kontrolü nedeniyle
cookie olmadan hiç bölüm döndürmüyor. "Tarayıcıdan Al" düğmesi gömülü
QtWebEngine penceresini açar, kullanıcı kontrolü çözer, oturum çerezi otomatik
kaydedilir.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..widgets import StatusLabel


class SettingsPage(QWidget):
    """İndirme klasörü, TRAnime cookie'si ve bypass ayarları."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cookie_worker = None
        self._build_ui()
        self.reload()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Ayarlar")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)
        self.lblStatus = StatusLabel()
        head.addWidget(self.lblStatus)
        layout.addLayout(head)

        # ── İndirme ─────────────────────────────────────────────────────────
        box = QFrame(); box.setObjectName("Panel")
        form = QFormLayout(box)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        row = QHBoxLayout()
        self.txtDir = QLineEdit()
        self.txtDir.setPlaceholderText("İndirilenler klasörü")
        row.addWidget(self.txtDir, 1)
        btnBrowse = QPushButton("Gözat…")
        btnBrowse.clicked.connect(self._pick_dir)
        row.addWidget(btnBrowse)
        holder = QWidget(); holder.setLayout(row)
        form.addRow("İndirme klasörü", holder)

        self.spnParallel = QSpinBox()
        self.spnParallel.setRange(1, 10)
        form.addRow("Paralel indirme", self.spnParallel)

        self.chkMaxRes = QCheckBox("En yüksek çözünürlüğü tercih et")
        form.addRow("", self.chkMaxRes)
        self.chkRemember = QCheckBox("Kaldığım dakikayı hatırla")
        form.addRow("", self.chkRemember)
        self.chkAria = QCheckBox("aria2c ile indir")
        form.addRow("", self.chkAria)
        layout.addWidget(box)

        # ── TRAnimeİzle cookie ──────────────────────────────────────────────
        cbox = QFrame(); cbox.setObjectName("Panel")
        cl = QVBoxLayout(cbox)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(8)

        cl.addWidget(QLabel("TRAnimeİzle oturum çerezi"))
        self.lblCookie = QLabel()
        self.lblCookie.setObjectName("Muted")
        self.lblCookie.setWordWrap(True)
        cl.addWidget(self.lblCookie)

        crow = QHBoxLayout()
        self.btnCookie = QPushButton("Tarayıcıdan Al")
        self.btnCookie.setObjectName("Primary")
        self.btnCookie.clicked.connect(self._fetch_cookie)
        crow.addWidget(self.btnCookie)
        self.btnCookieClear = QPushButton("Temizle")
        self.btnCookieClear.clicked.connect(self._clear_cookie)
        crow.addWidget(self.btnCookieClear)
        crow.addStretch(1)
        cl.addLayout(crow)
        layout.addWidget(cbox)

        # ── Bypass ──────────────────────────────────────────────────────────
        bbox = QFrame(); bbox.setObjectName("Panel")
        bform = QFormLayout(bbox)
        bform.setContentsMargins(16, 14, 16, 14)
        self.txtFlare = QLineEdit()
        self.txtFlare.setPlaceholderText("http://host:8191 (boş bırakılabilir)")
        bform.addRow("FlareSolverr", self.txtFlare)
        hint = QLabel("Boş bırakılırsa yalnızca yerel QtWebEngine çözücü kullanılır.")
        hint.setObjectName("Muted")
        bform.addRow("", hint)
        layout.addWidget(bbox)

        actions = QHBoxLayout()
        actions.addStretch(1)
        btnSave = QPushButton("Kaydet")
        btnSave.setObjectName("Primary")
        btnSave.clicked.connect(self.save)
        actions.addWidget(btnSave)
        layout.addLayout(actions)

        layout.addStretch(1)

    # ── Ayar okuma/yazma ────────────────────────────────────────────────────
    @staticmethod
    def _dosya():
        from ....cli.dosyalar import Dosyalar
        return Dosyalar()

    def reload(self) -> None:
        """Ayarları diskten oku ve forma yerleştir."""
        try:
            ayarlar: Dict[str, Any] = self._dosya().ayarlar or {}
        except Exception as exc:
            self.lblStatus.error(f"Ayarlar okunamadı: {exc}")
            return
        self.txtDir.setText(str(ayarlar.get("indirilenler") or ""))
        self.spnParallel.setValue(int(ayarlar.get("paralel indirme sayisi") or 3))
        self.chkMaxRes.setChecked(bool(ayarlar.get("max resolution", True)))
        self.chkRemember.setChecked(bool(ayarlar.get("dakika hatirla", True)))
        self.chkAria.setChecked(bool(ayarlar.get("aria2c kullan", False)))
        self.txtFlare.setText(str(ayarlar.get("flaresolverr_url") or ""))
        self._show_cookie_state(str(ayarlar.get("tranime_cookie") or ""))

    def save(self) -> None:
        try:
            self._dosya().set_ayar(ayar_list={
                "indirilenler": self.txtDir.text().strip(),
                "paralel indirme sayisi": self.spnParallel.value(),
                "max resolution": self.chkMaxRes.isChecked(),
                "dakika hatirla": self.chkRemember.isChecked(),
                "aria2c kullan": self.chkAria.isChecked(),
                "flaresolverr_url": self.txtFlare.text().strip(),
            })
        except Exception as exc:
            self.lblStatus.error(f"Kaydedilemedi: {exc}")
            return
        self.lblStatus.ok("Ayarlar kaydedildi.")

    def _pick_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "İndirme klasörü seç", self.txtDir.text() or "")
        if chosen:
            self.txtDir.setText(chosen)

    # ── TRAnime cookie ──────────────────────────────────────────────────────
    def _show_cookie_state(self, netscape: str) -> None:
        if netscape and ".AitrWeb.Session" in netscape:
            self.lblCookie.setText("Oturum çerezi kayıtlı ✓")
            self.lblCookie.setStyleSheet("color: #00b894;")
        else:
            self.lblCookie.setText(
                "Çerez yok — TRAnimeİzle bot kontrolü nedeniyle bölüm döndürmez. "
                "“Tarayıcıdan Al” ile kontrolü çözün.")
            self.lblCookie.setStyleSheet("")

    def _fetch_cookie(self) -> None:
        from ..cookie_browser import CookieBrowserWorker, is_available
        if not is_available():
            self.lblStatus.error("QtWebEngine yok (PySide6-Addons kurulu mu?).")
            return
        if self._cookie_worker is not None and self._cookie_worker.is_running:
            self.lblStatus.info("Tarayıcı zaten açık.")
            return

        self.btnCookie.setEnabled(False)
        self.lblStatus.info("Tarayıcı açılıyor…")
        self._cookie_worker = CookieBrowserWorker(
            on_status=self.lblStatus.info,
            on_cookies=self._on_cookie_ready,
            on_error=self._on_cookie_error,
            parent=self,
        )
        self._cookie_worker.start()

    def _on_cookie_ready(self, netscape: str) -> None:
        try:
            from ....sources.tranime import set_session_cookie
            set_session_cookie(netscape)                       # süreç içi
            self._dosya().set_ayar("tranime_cookie", netscape)  # kalıcı
        except Exception as exc:
            self.lblStatus.error(f"Çerez kaydedilemedi: {exc}")
            self.btnCookie.setEnabled(True)
            return
        self._show_cookie_state(netscape)
        self.lblStatus.ok("TRAnimeİzle çerezi alındı ve kaydedildi.")
        self.btnCookie.setEnabled(True)

    def _on_cookie_error(self, message: str) -> None:
        self.lblStatus.error(message)
        self.btnCookie.setEnabled(True)

    def _clear_cookie(self) -> None:
        try:
            self._dosya().set_ayar("tranime_cookie", "")
        except Exception as exc:
            self.lblStatus.error(f"Temizlenemedi: {exc}")
            return
        self._show_cookie_state("")
        self.lblStatus.info("Çerez temizlendi.")


__all__ = ["SettingsPage"]
