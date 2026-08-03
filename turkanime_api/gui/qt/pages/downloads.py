"""İndirme kuyruğu ve ilerleme paneli.

Eski GUI'deki `DownloadWorker` + indirme paneli davranışının Qt karşılığı:
kullanıcı ayarlarına uyan paralellik, başarısızlıkta otomatik tekrar, elle
"Yeniden Dene" ve gerçekten çalışan iptal.

İndirme işi yt-dlp'ye `progress_hooks` ile bağlanır; hook arka plan thread'inde
çalıştığı için ilerleme UI'ya **sinyalle** taşınır (kuyruklu bağlantı sayesinde
slot GUI thread'inde çalışır).
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .. import prefs
from ..widgets import StatusLabel
from ..workers import run_bg, set_long_task_limit

# Kullanıcıya gösterilen durumlar. Metinler tek yerde: satır etiketi, durum
# çubuğu ve testler aynı sözlüğü konuşsun.
DURUM_BEKLIYOR = "bekliyor"
DURUM_INDIRILIYOR = "indiriliyor"
DURUM_TAMAMLANDI = "tamamlandı"
DURUM_HATA = "hata"
DURUM_IPTAL = "iptal edildi"

# Bitmiş sayılan durumlar: satır temizlenebilir, yeniden denenebilir.
BITMIS_DURUMLAR = (DURUM_TAMAMLANDI, DURUM_HATA, DURUM_IPTAL)

# Eski GUI ile aynı: bir otomatik tekrar hakkı. Kaynak sunucuları sık sık
# geçici 5xx/timeout veriyor; ikinci deneme çoğu zaman tutuyor.
MAX_DENEME = 2

DURUM_RENK = {
    DURUM_TAMAMLANDI: "#00b894",
    DURUM_HATA: "#d63031",
    DURUM_IPTAL: "#e17055",
}


class IndirmeIptal(Exception):
    """Hook'tan fırlatılan iptal sinyali.

    yt-dlp'yi durdurmanın tek yolu bu: eski GUI hook'ta `return` ediyordu, o da
    yalnızca ilerleme raporunu susturuyor, indirme arka planda sonuna kadar
    devam ediyordu.
    """


def _fmt_size(num: Optional[float]) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


class _Is:
    """Tek bir indirme işinin paylaşılan durumu (GUI + arka plan)."""

    def __init__(self, task_id: str, entry: Dict[str, Any], title: str, output: str):
        self.task_id = task_id
        self.entry = entry
        self.title = title
        self.output = output
        self.iptal = threading.Event()
        self.durum = DURUM_BEKLIYOR
        # Bitişi iki thread de yazabiliyor (iptal GUI'den, sonuç işçiden);
        # kilit olmadan aynı iş iki kez "bitti" diye raporlanabilir.
        self.kilit = threading.Lock()


class DownloadManager(QObject):
    """İndirmeleri sıraya alır, arka planda çalıştırır, ilerlemeyi yayar."""

    added = Signal(str, str)            # task_id, başlık
    progress = Signal(str, int, str)    # task_id, yüzde, ayrıntı
    state = Signal(str, str)            # task_id, durum
    finished = Signal(str, bool, str)   # task_id, başarılı mı, mesaj

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._seq = 0
        self._jobs: Dict[str, _Is] = {}

    # ── Kuyruk ──────────────────────────────────────────────────────────────
    def enqueue(self, entry: Dict[str, Any], output: str = "") -> Optional[str]:
        bolum = (entry or {}).get("obj")
        if bolum is None:
            return None
        tercih = prefs.oku()
        # Eşzamanlılık "paralel indirme sayisi" ayarından; her kuyruğa girişte
        # tazeleniyor ki kullanıcı ayarı değiştirince yeniden başlatmak gerekmesin.
        set_long_task_limit(tercih.paralel)

        self._seq += 1
        task_id = f"dl{self._seq}"
        title = entry.get("title") or "Bölüm"
        job = _Is(task_id, entry, title, output or prefs.indirme_dizini(tercih))
        self._jobs[task_id] = job
        self.added.emit(task_id, title)
        self._basla(job)
        return task_id

    def retry(self, task_id: str) -> Optional[str]:
        """Başarısız/iptal edilmiş işi aynı satırda yeniden kuyruğa al."""
        job = self._jobs.get(task_id)
        if job is None or job.durum not in (DURUM_HATA, DURUM_IPTAL):
            return None
        set_long_task_limit(prefs.oku().paralel)
        self._basla(job)
        return task_id

    def cancel(self, task_id: str) -> bool:
        job = self._jobs.get(task_id)
        if job is None or job.durum in BITMIS_DURUMLAR:
            return False
        job.iptal.set()
        if job.durum == DURUM_BEKLIYOR:
            # Havuz doluysa bu iş dakikalarca başlamayabilir; kullanıcıya
            # "iptal edildi"yi o zamana kadar beklettirmenin anlamı yok.
            self._bitir(job, False, DURUM_IPTAL)
        return True

    def cancel_all(self) -> int:
        """Bekleyen ve çalışan tüm işleri iptal et; iptal edilen sayısını döndür."""
        return sum(1 for task_id in list(self._jobs) if self.cancel(task_id))

    # ── Sorgular (test ve UI için) ──────────────────────────────────────────
    def durum(self, task_id: str) -> Optional[str]:
        job = self._jobs.get(task_id)
        return job.durum if job else None

    def active_ids(self) -> List[str]:
        return [tid for tid, job in self._jobs.items()
                if job.durum not in BITMIS_DURUMLAR]

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def _basla(self, job: _Is) -> None:
        job.iptal.clear()
        with job.kilit:
            job.durum = DURUM_BEKLIYOR
        self.state.emit(job.task_id, DURUM_BEKLIYOR)
        # long_running: indirme, işi bitene kadar thread'i tutar; UI görevlerinin
        # (arama, bölüm listesi) havuzunu tüketmemesi için ayrı havuza gider.
        run_bg(self._run, job.task_id, long_running=True)

    def _bitir(self, job: _Is, ok: bool, durum: str,
               mesaj: Optional[str] = None) -> bool:
        """İşi sonlandır. Aynı iş için ikinci çağrı yok sayılır."""
        with job.kilit:
            if job.durum in BITMIS_DURUMLAR:
                return False
            job.durum = durum
        self.state.emit(job.task_id, durum)
        self.finished.emit(job.task_id, ok, mesaj or durum)
        return True

    def _hook_uret(self, job: _Is):
        """`progress_hooks` için iptal farkındalıklı ilerleme hook'u."""
        task_id = job.task_id

        def hook(d: Dict[str, Any]) -> None:
            if job.iptal.is_set():
                raise IndirmeIptal(task_id)
            durum = d.get("status")
            if durum == "downloading":
                done = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = int(done * 100 / total) if total else 0
                speed = d.get("speed")
                detail = f"{_fmt_size(done)} / {_fmt_size(total)}"
                if speed:
                    detail += f" · {_fmt_size(speed)}/s"
                self.progress.emit(task_id, pct, detail)
            elif durum == "finished":
                self.progress.emit(task_id, 100, "birleştiriliyor…")
            elif durum == "error":
                # aria2c fallback'i bu yolla haber veriyor; iş henüz bitmedi.
                mesaj = d.get("message")
                if mesaj:
                    self.progress.emit(task_id, 0, f"hata: {mesaj}")

        return hook

    def _run(self, task_id: str) -> None:
        job = self._jobs.get(task_id)
        if job is None:
            return
        if job.iptal.is_set():
            self._bitir(job, False, DURUM_IPTAL)
            return

        # Ayarlar iş BAŞLARKEN okunur: kuyruk uzunsa kullanıcı arada ayarı
        # değiştirmiş olabilir, sıradaki işler yenisine uymalı.
        tercih = prefs.oku()
        bolum = (job.entry or {}).get("obj")

        with job.kilit:
            job.durum = DURUM_INDIRILIYOR
        self.state.emit(task_id, DURUM_INDIRILIYOR)
        self.progress.emit(task_id, 0, "video aranıyor…")

        try:
            video = bolum.best_video(by_res=tercih.max_res,
                                     early_subset=tercih.aday_sayisi)
        except Exception as exc:
            self._bitir(job, False, DURUM_HATA, f"video hatası: {exc}")
            return
        if video is None:
            self._bitir(job, False, DURUM_HATA, "çalışan video bulunamadı")
            return

        hook = self._hook_uret(job)
        son_hata: Optional[Exception] = None
        for deneme in range(1, MAX_DENEME + 1):
            if job.iptal.is_set():
                break
            if deneme > 1:
                self.progress.emit(task_id, 0, f"{deneme}. deneme…")
            try:
                prefs.indir(video, hook, job.output, tercih)
            except IndirmeIptal:
                break
            except Exception as exc:
                son_hata = exc
                continue
            if job.iptal.is_set():
                # aria2c'de hook yalnızca sonda ateşleniyor: iş iptal istendikten
                # sonra bitmiş olabilir. "Tamamlandı" demek kullanıcıyı yanıltır.
                break
            # İndirme geçmişi burada yazılır; eski GUI'de yazılıyordu, Qt'de
            # hiç çağrılmıyordu (bölüm satırındaki ⬇ rozeti bu kayda bakıyor).
            prefs.gecmis_kaydet(bolum, "indirildi")
            self._bitir(job, True, DURUM_TAMAMLANDI)
            return

        if job.iptal.is_set():
            self._bitir(job, False, DURUM_IPTAL)
        else:
            self._bitir(job, False, DURUM_HATA, f"hata: {son_hata}")


class DownloadRow(QFrame):
    """Tek bir indirme işinin satırı: ilerleme + iptal/yeniden dene."""

    cancel_requested = Signal(str)
    retry_requested = Signal(str)

    def __init__(self, task_id: str, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.task_id = task_id
        # Bitmişlik AÇIKÇA tutulur; ilerleme metninden ya da bar değerinden
        # çıkarmaya çalışmak başarısız işleri kaçırır.
        self.durum = DURUM_BEKLIYOR
        self.is_finished = False
        self.is_ok = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.lblTitle = QLabel(title)
        top.addWidget(self.lblTitle, 1)
        self.lblDetail = QLabel(DURUM_BEKLIYOR)
        self.lblDetail.setObjectName("Muted")
        top.addWidget(self.lblDetail)

        self.btnCancel = QPushButton("İptal")
        self.btnCancel.clicked.connect(
            lambda: self.cancel_requested.emit(self.task_id))
        top.addWidget(self.btnCancel)

        self.btnRetry = QPushButton("Yeniden Dene")
        self.btnRetry.clicked.connect(
            lambda: self.retry_requested.emit(self.task_id))
        self.btnRetry.setVisible(False)
        top.addWidget(self.btnRetry)
        layout.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)

    def set_progress(self, pct: int, detail: str) -> None:
        self.bar.setValue(max(0, min(100, pct)))
        self.lblDetail.setText(detail)

    def set_state(self, durum: str) -> None:
        """Durumu ve buton görünürlüklerini eşitle."""
        self.durum = durum
        self.is_finished = durum in BITMIS_DURUMLAR
        self.is_ok = durum == DURUM_TAMAMLANDI
        self.btnCancel.setVisible(not self.is_finished)
        self.btnRetry.setVisible(durum in (DURUM_HATA, DURUM_IPTAL))
        if durum == DURUM_BEKLIYOR:
            # Yeniden denemede eski hata metni/rengi kalmasın.
            self.bar.setValue(0)
            self.lblDetail.setStyleSheet("")
            self.lblDetail.setText(DURUM_BEKLIYOR)

    def set_done(self, ok: bool, message: str) -> None:
        self.bar.setValue(100 if ok else self.bar.value())
        self.lblDetail.setText(message)
        renk = DURUM_RENK.get(self.durum, DURUM_RENK[DURUM_HATA] if not ok
                              else DURUM_RENK[DURUM_TAMAMLANDI])
        self.lblDetail.setStyleSheet(f"color: {renk};")


class DownloadsPage(QWidget):
    """Aktif ve tamamlanmış indirmeleri listeler."""

    def __init__(self, manager: DownloadManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: Dict[str, DownloadRow] = {}

        self.manager = manager
        manager.added.connect(self._on_added)
        manager.progress.connect(self._on_progress)
        manager.state.connect(self._on_state)
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
        self.btnCancelAll = QPushButton("Tümünü İptal")
        self.btnCancelAll.clicked.connect(self._cancel_all)
        head.addWidget(self.btnCancelAll)
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
        row = DownloadRow(task_id, title)
        row.cancel_requested.connect(self.manager.cancel)
        row.retry_requested.connect(self.manager.retry)
        self._rows[task_id] = row
        self._list.addWidget(row)
        self._refresh_status()

    def _on_progress(self, task_id: str, pct: int, detail: str) -> None:
        row = self._rows.get(task_id)
        if row is not None:
            row.set_progress(pct, detail)

    def _on_state(self, task_id: str, durum: str) -> None:
        row = self._rows.get(task_id)
        if row is not None:
            row.set_state(durum)
        self._refresh_status()

    def _on_finished(self, task_id: str, ok: bool, message: str) -> None:
        row = self._rows.get(task_id)
        if row is not None:
            row.set_done(ok, message)
        self._refresh_status()

    # ── Yardımcılar ─────────────────────────────────────────────────────────
    def _refresh_status(self) -> None:
        """Aktiflik satırlardan sayılır; ayrı sayaç tutmak yeniden denemede şaşar."""
        total = len(self._rows)
        active = sum(1 for row in self._rows.values() if not row.is_finished)
        if active:
            self.lblStatus.info(f"{active} aktif / {total} toplam")
        elif total:
            hatali = sum(1 for row in self._rows.values() if not row.is_ok)
            if hatali:
                self.lblStatus.error(f"{total - hatali} tamamlandı, {hatali} başarısız")
            else:
                self.lblStatus.ok(f"{total} iş tamamlandı")
        else:
            self.lblStatus.info("Henüz indirme yok.")

    def _cancel_all(self) -> None:
        adet = self.manager.cancel_all()
        if adet:
            self.lblStatus.info(f"{adet} indirme iptal ediliyor…")

    def _clear_finished(self) -> None:
        """Biten işleri (başarılı VE başarısız) listeden çıkar."""
        for task_id, row in list(self._rows.items()):
            if row.is_finished:
                row.setParent(None)
                row.deleteLater()
                del self._rows[task_id]
        self._refresh_status()


__all__ = ["DownloadsPage", "DownloadManager", "DownloadRow", "IndirmeIptal",
           "DURUM_BEKLIYOR", "DURUM_INDIRILIYOR", "DURUM_TAMAMLANDI",
           "DURUM_HATA", "DURUM_IPTAL", "BITMIS_DURUMLAR", "MAX_DENEME"]
