"""PySide6 tabanlı TürkAnime GUI'si.

Projenin tek arayüzü. Faz 9'da CustomTkinter yığını silindi ve hem
`turkanime-gui` hem `turkanime-qt` giriş noktaları buradaki `run`'a bağlandı.
"""
from .app import MainWindow, run, prepare_qt_env  # noqa: F401
from .workers import UiBridge, WorkerSignals, run_bg  # noqa: F401

__all__ = ["MainWindow", "run", "prepare_qt_env", "WorkerSignals", "run_bg", "UiBridge"]
