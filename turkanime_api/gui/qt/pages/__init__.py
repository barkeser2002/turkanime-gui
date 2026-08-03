"""PySide6 GUI sayfaları."""
from .detail import DetailPage  # noqa: F401
from .discover import DiscoverPage  # noqa: F401
from .downloads import DownloadManager, DownloadsPage  # noqa: F401
from .episodes import EpisodePage  # noqa: F401
from .search import SearchPage  # noqa: F401
from .settings import SettingsPage  # noqa: F401
from .watchlist import WatchlistPage  # noqa: F401

__all__ = ["SearchPage", "EpisodePage", "DownloadsPage", "DownloadManager",
           "SettingsPage", "DiscoverPage", "DetailPage", "WatchlistPage"]
