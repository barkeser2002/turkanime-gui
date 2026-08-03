"""GUI önyükleyici.

Geçiş dönemi: proje CustomTkinter'dan PySide6 + QtWebEngine'e taşınıyor.
Varsayılan olarak mevcut (çalışan) CustomTkinter GUI'si açılır; yeni Qt GUI'si
`TURKANIME_QT=1` ortam değişkeni ya da `turkanime-qt` giriş noktasıyla
başlatılır. Geçiş tamamlandığında (A5) varsayılan Qt olacak.
"""
import os


def _prepare_qt_env():
    """Qt ortamını QApplication kurulmadan **önce** hazırla.

    QtWebEngine `AA_ShareOpenGLContexts` attribute'unun QApplication'dan önce
    ayarlanmasını şart koşar. Asıl iş `gui.qt.app.prepare_qt_env` içinde.
    """
    try:
        from turkanime_api.gui.qt.app import prepare_qt_env
    except ImportError:
        return  # PySide6 kurulu değil: eski GUI etkilenmez
    prepare_qt_env()


def run_qt():
    """Yeni PySide6 GUI'sini başlat."""
    from turkanime_api.gui.qt import run as qt_run
    return qt_run()


def main():
    """Ortama göre uygun GUI'yi başlat."""
    if os.environ.get("TURKANIME_QT") == "1":
        return run_qt()
    from turkanime_api.gui import main as gui_main
    return gui_main.run()


if __name__ == '__main__':
    main()
