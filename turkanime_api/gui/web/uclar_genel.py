"""Sayfalardan bağımsız köprü uçları: gezinti, dış bağlantı, kaynak listesi."""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from ...sources import kayit as kaynak_kaydi
from .kopru import uc

# Sayfanın isteyebileceği gezinti hedefleri. `ac` bunları pencereye iletiyor;
# listede olmayan hedef reddediliyor (sayfa hatası sessiz kalmasın).
HEDEFLER = ("sayfa", "anime", "kitaplik", "arama", "sonuc")


class GenelUclar:
    """``ac``, ``disari_ac``, ``kaynaklar``."""

    def __init__(self, ac: Callable[[str, Dict[str, Any]], Any]):
        self._ac = ac

    @uc()
    def ac(self, hedef: str, veri: Dict[str, Any] = None) -> bool:
        """Sayfadan gezinti isteği: menü/"Geri" durumu pencerede tutuluyor."""
        if hedef not in HEDEFLER:
            raise ValueError(f"bilinmeyen hedef: {hedef}")
        self._ac(hedef, dict(veri or {}))
        return True

    @uc()
    def disari_ac(self, adres: str) -> bool:
        """http(s) adresini sistem tarayıcısında aç."""
        if not str(adres).startswith(("http://", "https://")):
            raise ValueError("yalnızca http(s) adresi açılabilir")
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
