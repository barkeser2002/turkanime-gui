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
