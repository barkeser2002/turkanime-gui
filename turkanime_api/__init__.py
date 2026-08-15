"""TurkAnime API paket giriş noktası.

`Anime`/`Bolum`/`Video`/`session` **tembel** açılıyor (PEP 562). Eskiden bu
dosyanın ilk iki satırı koşulsuz `objects` (→ yt_dlp, 71 modül, ~0.49 sn) ve
`bypass` (→ appdirs, Crypto) çekiyordu; `turkanime_api` altındaki *herhangi*
bir modülü import etmek — örneğin sunucu tarayıcısının kullandığı
`turkanime_api.common.episode_parser` — bu bedeli ödüyordu. Video indirmeyen
tarayıcı konteyneri yt-dlp'yi yalnızca bu zincir yüzünden kurmak zorundaydı.

Erişim biçimi değişmedi; `from turkanime_api import Anime` hâlâ çalışıyor,
sadece gerçekten kullanıldığı anda yükleniyor.
"""
from typing import TYPE_CHECKING

__all__ = ["Anime", "Bolum", "Video", "session"]

# Yalnızca statik çözümleyiciler için: pylint/mypy/IDE ve PyInstaller'ın
# modül grafiği PEP 562 `__getattr__`'ı izleyemiyor, `__all__` adlarını
# "tanımsız" sanıyorlar. Çalışma anında bu blok koşmuyor — tembellik bozulmaz.
if TYPE_CHECKING:
    from .bypass import session
    from .objects import Anime, Bolum, Video

_TEMBEL = {
    "Anime": ("objects", "Anime"),
    "Bolum": ("objects", "Bolum"),
    "Video": ("objects", "Video"),
    "session": ("bypass", "session"),
}


def __getattr__(ad):
    """İlk erişimde ilgili alt modülü yükle (PEP 562)."""
    try:
        modul_adi, nesne_adi = _TEMBEL[ad]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {ad!r}") from None
    from importlib import import_module
    nesne = getattr(import_module(f".{modul_adi}", __name__), nesne_adi)
    globals()[ad] = nesne          # ikinci erişim __getattr__'a hiç uğramasın
    return nesne


def __dir__():
    return sorted(set(globals()) | set(_TEMBEL))
