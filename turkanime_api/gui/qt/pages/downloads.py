"""İndirme kuyruğu ve ilerleme paneli.

Eski GUI'deki `DownloadWorker` + indirme paneli davranışının Qt karşılığı.
İndirme işi yt-dlp'ye `progress_hooks` ile bağlanır; hook arka plan thread'inde
çalıştığı için ilerleme UI'ya **sinyalle** taşınır (kuyruklu bağlantı sayesinde
slot GUI thread'inde çalışır).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..widgets import StatusLabel
from ..workers import run_bg


def _fmt_size(num: Optional[float]) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


class DownloadManager(QObject):
    """İndirmeleri sıraya alır, arka planda çalıştırır, ilerlemeyi yayar."""

    added = Signal(str, str)            # task_id, başlık
    progress = Signal(str, int, str)    # task_id, yüzde, ayrıntı
    finished = Signal(str, bool, str)   # task_id, başarılı mı, mesaj

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._seq = 0

    def enqueue(self, entry: Dict[str, Any], output: str = "") -> Optional[str]:
        bolum = (entry or {}).get("obj")
        if bolum is None:
            return None
        self._seq += 1
        task_id = f"dl{self._seq}"
        title = entry.get("title") or "Bölüm"
        self.added.emit(task_id, title)
        # long_running: indirme, işi bitene kadar thread'i tutar; UI görevlerinin
        # (arama, bölüm listesi) havuzunu tüketmemesi için ayrı havuza gider.
        run_bg(self._run, task_id, bolum, title, output, long_running=True)
        return task_id

    # ── Arka plan ───────────────────────────────────────────────────────────
    def _run(self, task_id: str, bolum, title: str, output: str) -> None:
        self.progress.emit(task_id, 0, "video aranıyor…")
        try:
            video = bolum.best_video()
        except Exception as exc:
            self.finished.emit(task_id, False, f"video hatası: {exc}")
            return
        if video is None:
            self.finished.emit(task_id, False, "çalışan video bulunamadı")
            return

        def hook(d: Dict[str, Any]) -> None:
            status = d.get("status")
            if status == "downloading":
                done = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = int(done * 100 / total) if total else 0
                speed = d.get("speed")
                detail = f"{_fmt_size(done)} / {_fmt_size(total)}"
                if speed:
                    detail += f" · {_fmt_size(speed)}/s"
                self.progress.emit(task_id, pct, detail)
            elif status == "finished":
                self.progress.emit(task_id, 100, "birleştiriliyor…")

        try:
            video.indir(callback=hook, output=output)
        except Exception as exc:
            self.finished.emit(task_id, False, f"indirme hatası: {exc}")
            return
        self.finished.emit(task_id, True, "tamamlandı")


class DownloadRow(QFrame):
    """Tek bir indirme işinin satırı."""

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        # Bitmişlik AÇIKÇA tutulur; ilerleme metninden ya da bar değerinden
        # çıkarmaya çalışmak başarısız işleri kaçırır.
        self.is_finished = False
        self.is_ok = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.lblTitle = QLabel(title)
        top.addWidget(self.lblTitle, 1)
        self.lblDetail = QLabel("sıraya alındı")
        self.lblDetail.setObjectName("Muted")
        top.addWidget(self.lblDetail)
        layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)

    def set_progress(self, pct: int, detail: str) -> None:
        self.bar.setValue(max(0, min(100, pct)))
        self.lblDetail.setText(detail)

    def set_done(self, ok: bool, message: str) -> None:
        self.is_finished = True
        self.is_ok = ok
        self.bar.setValue(100 if ok else self.bar.value())
        self.lblDetail.setText(message)
        color = "#00b894" if ok else "#d63031"
        self.lblDetail.setStyleSheet(f"color: {color};")


class DownloadsPage(QWidget):
    """Aktif ve tamamlanmış indirmeleri listeler."""

    def __init__(self, manager: DownloadManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: Dict[str, DownloadRow] = {}
        self._active = 0

        self.manager = manager
        manager.added.connect(self._on_added)
        manager.progress.connect(self._on_progress)
        manager.finished.connect(self._on_finished)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("İndirilenler")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)
        self.lblStatus = StatusLabel()
        head.addWidget(self.lblStatus)
        self.btnClear = QPushButton("Tamamlananları Temizle")
        self.btnClear.clicked.connect(self._clear_finished)
        head.addWidget(self.btnClear)
        layout.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self._list = QVBoxLayout(holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(6)
        self._list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(holder)
        layout.addWidget(self.scroll, 1)

        self.lblStatus.info("Henüz indirme yok.")

    # ── Sinyal alıcıları (GUI thread'i) ─────────────────────────────────────
    def _on_added(self, task_id: str, title: str) -> None:
        row = DownloadRow(title)
        self._rows[task_id] = row
        self._list.addWidget(row)
        self._active += 1
        self._refresh_status()

    def _on_progress(self, task_id: str, pct: int, detail: str) -> None:
        row = self._rows.get(task_id)
        if row is not None:
            row.set_progress(pct, detail)

    def _on_finished(self, task_id: str, ok: bool, message: str) -> None:
        row = self._rows.get(task_id)
        if row is not None:
            row.set_done(ok, message)
        self._active = max(0, self._active - 1)
        self._refresh_status()

    def _refresh_status(self) -> None:
        total = len(self._rows)
        if self._active:
            self.lblStatus.info(f"{self._active} aktif / {total} toplam")
        elif total:
            self.lblStatus.ok(f"{total} iş tamamlandı")
        else:
            self.lblStatus.info("Henüz indirme yok.")

    def _clear_finished(self) -> None:
        """Biten işleri (başarılı VE başarısız) listeden çıkar."""
        for task_id, row in list(self._rows.items()):
            if row.is_finished:
                row.setParent(None)
                row.deleteLater()
                del self._rows[task_id]
        self._refresh_status()


__all__ = ["DownloadsPage", "DownloadManager", "DownloadRow"]
