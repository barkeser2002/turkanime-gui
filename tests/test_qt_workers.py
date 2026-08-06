"""Qt threading altyapısı.

Buradaki asıl güvence: arka plan işleri UI'ya **her zaman GUI thread'inde**
teslim edilmeli. Bu bozulursa çökme rastgele ve teşhisi zor olur.
"""
from __future__ import annotations

import threading

import pytest

from turkanime_api.gui.qt.workers import (
    UiBridge, WorkerSignals, long_task_pool, run_bg,
)


def test_signals_deliver_on_gui_thread(qtbot):
    """`emit_*` arka plandan çağrılsa bile slot GUI thread'inde çalışmalı."""
    main_tid = threading.get_ident()
    got: dict = {}

    sig = WorkerSignals()
    sig.connect_progress(lambda m: got.setdefault("progress", (m, threading.get_ident())))
    sig.connect_found(lambda o: got.setdefault("found", (o, threading.get_ident())))

    def job():
        got["bg_tid"] = threading.get_ident()
        sig.emit_progress("indiriliyor")
        sig.emit_found({"slug": "naruto"})

    with qtbot.waitSignal(sig.found, timeout=5000):
        run_bg(job)

    assert got["bg_tid"] != main_tid, "iş gerçekten arka planda koşmalı"
    assert got["progress"] == ("indiriliyor", main_tid)
    assert got["found"][1] == main_tid


def test_background_exception_becomes_error_signal(qtbot):
    """Arka plan hatası uygulamayı düşürmemeli, `error` sinyaline dönmeli."""
    sig = WorkerSignals()

    def boom():
        raise RuntimeError("beklenen-hata")

    with qtbot.waitSignal(sig.error, timeout=5000) as blocker:
        run_bg(boom, signals=sig)
    assert "beklenen-hata" in blocker.args[0]


def test_ui_bridge_marshals_to_gui_thread(qtbot):
    """`UiBridge.post` eski `after(0, ...)` ile aynı anlamda olmalı."""
    main_tid = threading.get_ident()
    seen: list = []

    bridge = UiBridge()
    bridge.post(lambda: seen.append(threading.get_ident()))
    qtbot.waitUntil(lambda: len(seen) == 1, timeout=5000)
    assert seen[0] == main_tid


def test_ui_bridge_survives_callback_error(qtbot):
    """Bozuk bir UI callback'i köprüyü kilitlememeli."""
    bridge = UiBridge()
    ok: list = []

    bridge.post(lambda: (_ for _ in ()).throw(ValueError("bozuk")))
    bridge.post(lambda: ok.append(True))
    qtbot.waitUntil(lambda: ok == [True], timeout=5000)


def test_task_survives_deleted_signals(qtbot):
    """Pencere ağ isteği sürerken kapatılırsa `_Task` çökmemeli.

    Sinyal nesnesi C++ tarafında yıkıldıktan sonra arka plan işi hâlâ koşuyor
    olabilir; `emit` orada `RuntimeError: Signal source has been deleted`
    fırlatır. Korumasız hâlde bu üç kez zincirleniyordu (asıl emit → hata
    bildirimi → `finished`) ve kapanışta konsola yığınla traceback basıyordu.
    """
    from PySide6.QtCore import QObject
    from turkanime_api.gui.qt.workers import _Task

    sig = WorkerSignals()
    # Alıcıyı C++ tarafında yık (pencere kapanınca olan bu).
    QObject.deleteLater(sig)
    qtbot.wait(50)

    calisti = []

    def job():
        calisti.append(True)
        sig.emit_found("veri")          # RuntimeError fırlatacak

    task = _Task(job, (), {}, sig)
    task.run()                          # istisna DIŞARI sızmamalı
    assert calisti == [True]


def test_long_tasks_use_separate_pool():
    """Uzun işler ayrı havuzda koşmalı.

    Aksi hâlde 30 bölüm indirmeye basmak global havuzu doldurur ve arama
    görevleri kuyrukta beklerken arayüz kalıcı olarak 'aranıyor…' kalır.
    """
    from PySide6.QtCore import QThreadPool

    pool = long_task_pool()
    assert pool is not QThreadPool.globalInstance()
    assert pool.maxThreadCount() >= 1


# ── Havuz ayrımı: oynatma vs indirme ─────────────────────────────────────────
def test_oynatma_indirme_havuzunu_paylasmiyor():
    """Oynatma, indirme kuyruğunun arkasına düşmemeli."""
    from PySide6.QtCore import QThreadPool
    from turkanime_api.gui.qt.workers import playback_pool

    assert playback_pool() is not long_task_pool()
    assert playback_pool() is not QThreadPool.globalInstance()


def test_dolu_indirme_kuyrugu_oynatmayi_engellemiyor(qtbot):
    """Ölçüldü: 3 uzun indirme havuzu doldurunca "Oynat" hiç başlamıyordu.

    Kullanıcı 30 bölümü kuyruğa alınca havuz baştan sona dolu kalıyor; oynatma
    aynı havuza gönderildiği için sıraya giriyor ve `MainWindow._playing`
    bayrağı açık kaldığından ikinci deneme de reddediliyordu.
    """
    from turkanime_api.gui.qt.workers import (
        VARSAYILAN_UZUN_IS, set_long_task_limit,
    )

    birak = threading.Event()
    oynadi = threading.Event()
    set_long_task_limit(1)              # havuzu tek işle doldurmak yeter
    try:
        run_bg(lambda: birak.wait(20), long_running=True)
        qtbot.waitUntil(lambda: long_task_pool().activeThreadCount() == 1,
                        timeout=5000)

        run_bg(oynadi.set, playback=True)
        # İndirme HÂLÂ sürerken oynatma başlamış olmalı.
        qtbot.waitUntil(oynadi.is_set, timeout=5000)
        assert not birak.is_set(), "ölçüm geçersiz: indirme erken bitti"
    finally:
        birak.set()
        long_task_pool().waitForDone(5000)
        set_long_task_limit(VARSAYILAN_UZUN_IS)


# ── Kapanış ──────────────────────────────────────────────────────────────────
def test_shutdown_pools_bitmeyen_isi_bildiriyor(qtbot):
    """`shutdown_pools` iş bitmediyse False dönmeli.

    Dönüş değeri olmadan çağıran taraf "her şey durdu" sanıyor ve süreci normal
    yoldan kapatıyordu; `~QThreadPool` yıkıcısı ise zaman aşımsız
    `waitForDone()` çağırdığı için süreç indirme bitene kadar askıda kalıyordu.
    """
    from turkanime_api.gui.qt.workers import shutdown_pools

    birak = threading.Event()
    basladi = threading.Event()
    try:
        run_bg(lambda: (basladi.set(), birak.wait(20)), long_running=True)
        qtbot.waitUntil(basladi.is_set, timeout=5000)
        assert shutdown_pools(200) is False
    finally:
        birak.set()
        long_task_pool().waitForDone(5000)
    assert shutdown_pools(2000) is True
