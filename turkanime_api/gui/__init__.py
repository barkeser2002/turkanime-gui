"""GUI modülü."""
from turkanime_api.version import __version__ as APP_VERSION

try:
    from turkanime_api.gui.update_manager import UpdateManager
except ImportError:
    UpdateManager = None
