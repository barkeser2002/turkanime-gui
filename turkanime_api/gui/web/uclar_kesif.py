"""Keşif sayfalarının (Ana Sayfa, Trend, Bu Sezon) köprü uçları.

Veri mantığı eski Qt sayfasıyla ortak (`pages.discover.fetch_discover`,
`pages.library.devam_kayitlari`); burada yalnızca sayfanın istediği biçime
çevriliyor. Ağ ve disk işleri ``arka=True``: GUI thread'i beklemiyor.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ...common import kutuphane
from ...sources import kayit as kaynak_kaydi
from .kopru import uc
from .veri import kartlar

# Ana sayfa şeridinde en fazla kaç seri (Kitaplığım'da tamamı).
SERIT_SINIRI = 10

_ALT_BASLIKLAR = {
    "trending": "Şu anda yayında ve en çok izlenenler",
}


def kaynak_rengi(ad: str) -> str:
    kaynak = kaynak_kaydi.bul(ad)
    return kaynak.renk if kaynak is not None else ""


def devam_karti(kayit: Dict[str, Any]) -> Dict[str, Any]:
    """Kitaplık "devam" kaydından şerit kartı (konum → ilerleme oranı)."""
    from ..qt.pages.library import son_bolum_metni
    konum = kayit.get("konum") if isinstance(kayit.get("konum"), dict) else {}
    ilerleme = None
    try:
        sure = float(konum.get("sure") or 0)
        if sure > 0:
            ilerleme = max(0.0, min(1.0, float(konum.get("konum") or 0) / sure))
    except (TypeError, ValueError):
        ilerleme = None
    kaynak = str(kayit.get("kaynak") or "")
    return {
        "baslik": str(kayit.get("baslik") or kayit.get("kimlik") or ""),
        "kapak": str(kayit.get("kapak") or ""),
        "bolum_metni": son_bolum_metni(kayit),
        "kaynak_adi": kaynak_kaydi.gorunen_ad(kaynak),
        "kaynak_renk": kaynak_rengi(kaynak),
        "ilerleme": ilerleme,
        "kayit": kayit,
    }


class KesifUclari:
    """``kesif``, ``devam_listesi``, ``istatistik``."""

    @uc(arka=True)
    def kesif(self, mod: str = "home", limit: int = 24) -> Dict[str, Any]:
        from ..qt.pages.discover import (
            _BOS_MESAJ, _BOS_VARSAYILAN, MODES, fetch_discover, season_label,
        )
        if mod not in MODES:
            raise ValueError(f"bilinmeyen keşif kipi: {mod}")
        limit = max(1, min(int(limit or 24), 50))
        items = fetch_discover(mod, limit)
        altbaslik = _ALT_BASLIKLAR.get(mod, "")
        if mod == "season":
            altbaslik = f"{season_label()} sezonu • MyAnimeList"
        return {
            "mod": mod,
            "altbaslik": altbaslik,
            "kartlar": kartlar(items),
            "bos_mesaj": _BOS_MESAJ.get(mod, _BOS_VARSAYILAN),
        }

    @uc(arka=True)
    def devam_listesi(self, sinir: int = SERIT_SINIRI) -> List[Dict[str, Any]]:
        from ..qt.pages.library import devam_kayitlari
        return [devam_karti(k) for k in devam_kayitlari(sinir=int(sinir or SERIT_SINIRI))]

    @uc(arka=True)
    def istatistik(self) -> Dict[str, Any]:
        """Hero sayıları — hepsi gerçek veri (eski sabit "10.000+" yok).

        Arşiv sayısı `arsiv_durumu`'ndan: ağa ÇIKMAZ; uzak aynada dizin
        henüz inmemişse ``None`` ve sayfa "—" gösteriyor.
        """
        arsiv = None
        try:
            from ...sources import animedepo
            arsiv = animedepo.arsiv_durumu().anime_sayisi
        except Exception as exc:
            print(f"[Web] arşiv durumu okunamadı: {exc}")
        return {
            "arsiv": arsiv,
            "kaynak": len(kaynak_kaydi.kaynaklar(metadata=False)),
            "kitaplik": kutuphane.seri_sayisi(),
        }


__all__ = ["KesifUclari", "devam_karti", "SERIT_SINIRI"]
