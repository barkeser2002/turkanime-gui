"""Qt threading altyapısı — CustomTkinter'daki elle yazılmış desenlerin karşılığı.

Eski GUI'de arka plan işi `threading.Thread(daemon=True)` ile çalışıp sonucu
`self.after(0, ...)` ile UI thread'ine taşıyordu. Qt'de bu iki mekanizmanın
karşılığı:

- `WorkerSignals`  : elle yazılmış callback registry yerine gerçek Qt sinyalleri.
                     Alıcı GUI thread'inde yaşadığı için bağlantı otomatik olarak
                     *queued* olur; yani ayrıca marshalling gerekmez.
- `run_bg(fn)`     : `threading.Thread(...).start()` yerine QThreadPool.
- `UiBridge`       : elden geçirilmemiş `self.after(0, cb)` çağrıları için köprü.

Eski `connect_*` / `emit_*` metod isimleri korunur ki mevcut worker sınıfları
(DownloadWorker, VideoFindWorker, SearchWorker, EpisodesWorker) minimum
değişiklikle taşınabilsin.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    """Arka plan işlerinin UI'ya haber verdiği sinyaller.

    Eski saf-Python `WorkerSignals` sınıfıyla aynı yüzeyi sunar; farkı, altta
    gerçek Qt sinyallerinin olması ve thread güvenliğinin Qt tarafından
    sağlanmasıdır.
    """

    progress = Signal(str)
    progress_item = Signal(object)
    error = Signal(str)
    error_item = Signal(object)
    success = Signal()
    found = Signal(object)
    finished = Signal()

    # ── Geriye dönük uyumlu bağlama yardımcıları ────────────────────────────
    def connect_progress(self, cb: Callable[[str], None]) -> None:
        self.progress.connect(cb)

    def connect_progress_item(self, cb: Callable[[Any], None]) -> None:
        self.progress_item.connect(cb)

    def connect_error(self, cb: Callable[[str], None]) -> None:
        self.error.connect(cb)

    def connect_error_item(self, cb: Callable[[Any], None]) -> None:
        self.error_item.connect(cb)

    def connect_success(self, cb: Callable[[], None]) -> None:
        self.success.connect(cb)

    def connect_found(self, cb: Callable[[Any], None]) -> None:
        self.found.connect(cb)

    # ── Geriye dönük uyumlu yayma yardımcıları ──────────────────────────────
    def emit_progress(self, msg: str) -> None:
        self.progress.emit(msg)

    def emit_progress_item(self, item: Any) -> None:
        self.progress_item.emit(item)

    def emit_error(self, msg: str) -> None:
        self.error.emit(str(msg))

    def emit_error_item(self, item: Any) -> None:
        self.error_item.emit(item)

    def emit_success(self) -> None:
        self.success.emit()

    def emit_found(self, item: Any) -> None:
        self.found.emit(item)


class _Task(QRunnable):
    """Bir callable'ı thread havuzunda çalıştıran QRunnable sarmalayıcı."""

    def __init__(self, fn: Callable[..., Any], args: tuple, kwargs: dict,
                 signals: WorkerSignals | None):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._signals = signals

    @Slot()
    def run(self) -> None:  # pragma: no cover - thread gövdesi
        try:
            self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # arka plan hatası UI'yı düşürmemeli
            self._yay_hata(exc)
        finally:
            self._yay_bitti()

    # ── Sinyal yayma (alıcı silinmiş olabilir) ──────────────────────────────
    #
    # Kullanıcı ağ isteği sürerken pencereyi kapatırsa sinyal nesnesi C++
    # tarafında yıkılır ama arka plan işi hâlâ koşuyordur; `emit` o noktada
    # `RuntimeError: Signal source has been deleted` fırlatır. Korumasız hâlde
    # bu üç kez zincirleme patlıyordu: önce asıl emit, sonra hata bildirimi,
    # sonra `finally`'deki `finished` — kapanışta konsola yığınla traceback.
    # Alıcı yoksa yayacak kimse de yok; sessizce geçmek doğru davranış.
    def _yay_hata(self, exc: Exception) -> None:
        if self._signals is None:
            traceback.print_exc()
            return
        try:
            self._signals.emit_error(f"{exc}")
        except RuntimeError:
            pass

    def _yay_bitti(self) -> None:
        if self._signals is None:
            return
        try:
            self._signals.finished.emit()
        except RuntimeError:
            pass


# Uzun süren işler (indirme) için AYRI havuz.
#
# Neden: `video.oynat()` mpv süreci bitene kadar, `video.indir()` de indirme
# bitene kadar çalıştığı thread'i tutar. Bunlar global havuza konursa 30 bölüm
# seçip indirmeye basmak havuzu tamamen doldurur; arama/bölüm-listesi görevleri
# kuyrukta bekler, `_busy` bayrakları hiç sıfırlanmaz ve arayüz kalıcı olarak
# "aranıyor…" durumunda kilitlenir.
_long_pool: QThreadPool | None = None

# Oynatma da uzun bir iş ama indirmeyle AYNI havuzu paylaşamaz: havuzun sınırı
# kullanıcının "paralel indirme sayisi" ayarı ve 30 bölüm kuyruğa alındığında
# havuz baştan sona doludur. Oynatma o kuyruğun sonuna eklenince mpv dakikalarca
# açılmaz; üstelik `MainWindow._playing` bayrağı iş başlayana kadar açık kaldığı
# için kullanıcının ikinci denemesi de "zaten bir bölüm açılıyor" diye reddedilir
# — yani indirme kuyruğu bitene kadar oynatma tamamen ölür.
_play_pool: QThreadPool | None = None

# Yalnızca YEDEK değer: gerçek sınır "paralel indirme sayisi" ayarından gelir
# (bkz. `set_long_task_limit`). Sabit tutulduğu sürece kullanıcının ayarı
# hiçbir işe yaramıyordu.
VARSAYILAN_UZUN_IS = 3

# `MainWindow._playing` zaten tek oynatmaya izin veriyor; ikinci slot yalnızca
# biten mpv'nin thread'i havuza dönerken yeni isteğin beklememesi için.
ES_ZAMANLI_OYNATMA = 2


def long_task_pool() -> QThreadPool:
    """İndirme gibi uzun işler için ayrılmış havuz."""
    global _long_pool
    if _long_pool is None:
        _long_pool = QThreadPool()
        _long_pool.setMaxThreadCount(VARSAYILAN_UZUN_IS)
    return _long_pool


def playback_pool() -> QThreadPool:
    """Oynatma (mpv) için ayrılmış havuz — indirme kuyruğundan bağımsız."""
    global _play_pool
    if _play_pool is None:
        _play_pool = QThreadPool()
        _play_pool.setMaxThreadCount(ES_ZAMANLI_OYNATMA)
    return _play_pool


def set_long_task_limit(sayi: int | None) -> int:
    """Uzun iş havuzunun eşzamanlılığını çalışma anında ayarla.

    Kullanıcı ayarı iş kuyruğa girerken uygulanır; QThreadPool küçültmeyi
    çalışan işleri kesmeden yapar, yani indirme ortasında ayar değiştirmek
    güvenlidir.
    """
    pool = long_task_pool()
    try:
        limit = int(sayi)
    except (TypeError, ValueError):
        limit = VARSAYILAN_UZUN_IS
    limit = max(1, limit)
    if pool.maxThreadCount() != limit:
        pool.setMaxThreadCount(limit)
    return limit


def run_bg(fn: Callable[..., Any], *args, signals: WorkerSignals | None = None,
           long_running: bool = False, playback: bool = False, **kwargs) -> None:
    """`fn`'i arka planda çalıştır (eski `threading.Thread(daemon=True)` yerine).

    `long_running=True` verilirse iş, kısa UI görevlerini aç bırakmamak için
    indirme havuzuna gönderilir. `playback=True` ise oynatmaya ayrılmış havuza:
    mpv, kullanıcının indirme kuyruğu yüzünden beklemek zorunda kalmamalı.

    Hata olursa `signals.error` yayılır; sinyal verilmemişse traceback basılır.
    """
    if playback:
        pool = playback_pool()
    elif long_running:
        pool = long_task_pool()
    else:
        pool = QThreadPool.globalInstance()
    pool.start(_Task(fn, args, kwargs, signals))


def shutdown_pools(msecs: int = 3000) -> bool:
    """Kapanışta havuzları boşalt; çalışanların bitmesini kısa süre bekle.

    Dönüş: hepsi durduysa `True`. Çağıranın bunu YOK SAYMAMASI gerekir —
    `~QThreadPool` yıkıcısı zaman aşımsız `waitForDone()` çağırdığı için,
    burada `False` dönüldüğünde süreç normal yoldan çıkmaya çalışırsa iş
    (ör. yarım kalmış bir indirme) bitene kadar askıda kalır.
    """
    tamam = True
    for pool in (QThreadPool.globalInstance(), _long_pool, _play_pool):
        if pool is None:
            continue
        pool.clear()                      # henüz başlamamışları at
        if not pool.waitForDone(msecs):   # çalışanlara kısa mühlet
            tamam = False
    return tamam


class UiBridge(QObject):
    """`self.after(0, cb)` çağrılarının Qt karşılığı.

    Arka plan thread'inden `bridge.call.emit(fn)` denince `fn`, GUI thread'inde
    çalıştırılır (queued connection). Böylece eski `after(0, ...)` çağrıları
    mekanik olarak dönüştürülebilir.
    """

    call = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.call.connect(self._invoke)

    @Slot(object)
    def _invoke(self, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception:  # UI callback'i uygulamayı düşürmemeli
            traceback.print_exc()

    def post(self, fn: Callable[[], Any]) -> None:
        """`after(0, fn)` ile birebir aynı anlam."""
        self.call.emit(fn)


__all__ = ["WorkerSignals", "run_bg", "UiBridge", "long_task_pool",
           "playback_pool", "set_long_task_limit", "shutdown_pools",
           "VARSAYILAN_UZUN_IS", "ES_ZAMANLI_OYNATMA"]
