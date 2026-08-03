"""PySide6 tabanlı yeni TürkAnime GUI'si.

CustomTkinter GUI'si (`turkanime_api.gui.main`) geçiş tamamlanana kadar
çalışmaya devam eder; bu paket onun yerini kademeli olarak alır.
"""
from .app import MainWindow, run, prepare_qt_env  # noqa: F401
from .workers import UiBridge, WorkerSignals, run_bg  # noqa: F401

__all__ = ["MainWindow", "run", "prepare_qt_env", "WorkerSignals", "run_bg", "UiBridge"]
