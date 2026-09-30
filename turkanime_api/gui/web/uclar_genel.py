"""Sayfalardan bağımsız köprü uçları: gezinti, dış bağlantı, kaynak listesi."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from ...sources import kayit as kaynak_kaydi
from .kopru import UcHatasi, uc

# Sayfanın isteyebileceği gezinti hedefleri. `ac` bunları pencereye iletiyor;
# listede olmayan hedef reddediliyor (sayfa hatası sessiz kalmasın).
HEDEFLER = ("sayfa", "anime", "kitaplik", "arama", "sonuc", "geri")


class GenelUclar:
    """``ac``, ``disari_ac``, ``kaynaklar``, ``kabuk_durumu``."""

    def __init__(self, ac: Callable[[str, Dict[str, Any]], Any],
                 kabuk: Callable[[], Dict[str, Any]] = lambda: {}):
        self._ac = ac
        self._kabuk = kabuk

    @uc()
    def kabuk_durumu(self) -> Dict[str, Any]:
        """Üst/alt çubuğun ilk hâli: sürüm, AniList kullanıcısı, indirme sayısı."""
        return self._kabuk()

    @uc()
    def ac(self, hedef: str, veri: Dict[str, Any] = None) -> bool:
        """Sayfadan gezinti isteği: menü/"Geri" durumu pencerede tutuluyor."""
        if hedef not in HEDEFLER:
            raise UcHatasi(f"bilinmeyen hedef: {hedef}")
        self._ac(hedef, dict(veri or {}))
        return True

    @uc()
    def disari_ac(self, adres: str) -> bool:
        """http(s) adresini sistem tarayıcısında aç."""
        if not str(adres).startswith(("http://", "https://")):
            raise UcHatasi("yalnızca http(s) adresi açılabilir")
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        return bool(QDesktopServices.openUrl(QUrl(adres)))

    @uc()
    def kaynaklar(self) -> List[Dict[str, Any]]:
        """Kayıttaki kaynaklar (rozet renkleri, kısaltmalar)."""
        return [{"ad": k.ad, "etiket": k.etiket, "kisaltma": k.kisaltma,
                 "renk": k.renk, "oynatilabilir": k.oynatilabilir}
                for k in kaynak_kaydi.kaynaklar()]


__all__ = ["GenelUclar", "HEDEFLER"]
