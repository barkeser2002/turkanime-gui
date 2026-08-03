"""GUI paketi.

Tek arayüz PySide6 + QtWebEngine: `turkanime_api.gui.qt`.
CustomTkinter yığını (main.py, update_manager.py, cookie_browser.py,
match_popup.py, boot.py) Faz 9'da kaldırıldı.

Burada Qt'den hiçbir şey import ETME: `turkanime_api.gui.qt` PySide6'ya
bağlı, ama CLI de `turkanime_api.gui` paketinin altındaki bir şeye
dokunduğunda bu dosya çalışır. PySide6 kurulu olmayan CLI kullanıcısında
ImportError patlamasın diye paket kökü bilerek boş tutuluyor.
"""
from turkanime_api.version import __version__ as APP_VERSION

__all__ = ["APP_VERSION"]
