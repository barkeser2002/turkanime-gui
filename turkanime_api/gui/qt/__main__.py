"""Qt GUI giriş noktası (paketlenmiş EXE dahil).

Donmuş (PyInstaller) build'de `sys.executable -m paket.modul` çalışmaz; bu yüzden
CF çözücü alt-süreci uygulamanın **kendisini** özel bir bayrakla yeniden çağırır.
Bu dosya o bayrağı yakalayıp GUI yerine çözücüyü çalıştırır.

    python -m turkanime_api.gui.qt                 -> GUI
    <app.exe> --cf-qt-solver                       -> CF çözücü alt-süreci
"""
import sys

SOLVER_FLAG = "--cf-qt-solver"


def main() -> int:
    # DİKKAT: burada **mutlak** import kullanılmalı. PyInstaller bu dosyayı
    # donmuş girdi betiği olarak `__main__` adıyla çalıştırır; o bağlamda
    # `__package__` boştur ve göreli import (`from ...common import ...`)
    # çöker — paketlenmiş uygulama sessizce asılır.
    if SOLVER_FLAG in sys.argv:
        from turkanime_api.common.cf_qt_solver import main as solver_main
        return solver_main()
    from turkanime_api.gui.qt.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
