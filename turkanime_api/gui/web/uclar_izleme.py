"""İzleme Listem (AniList) sayfasının köprü uçları.

İşi `gui.qt.anilist.AniListService` yapıyor (jeton, liste, senkron); burada
yalnızca servis sinyalleri sayfa olaylarına çevriliyor:

``izleme_listesi``  {durum, kartlar}   ``izleme_hatasi``  {durum, mesaj}
``izleme_senkron``  {sayi}             ``anilist_giris``  {giris, ad}
"""
from __future__ import annotations

from typing import Any, Dict, List

from .kopru import Kopru, uc
from .veri import kart


def _sayi(deger: Any) -> int:
    try:
        return int(deger or 0)
    except (TypeError, ValueError):
        return 0


def izleme_karti(media: Dict[str, Any]) -> Dict[str, Any]:
    """Liste girişi: kapak, durum rozeti (renkli), ilerleme, kullanıcı puanı."""
    from ..qt.anilist import DURUM_ETIKETI, DURUM_RENGI
    veri = kart(media)
    durum = str(media.get("user_status") or "")
    izlenen, toplam = _sayi(media.get("user_progress")), _sayi(media.get("episodes"))
    skor = _sayi(media.get("user_score"))
    veri["rozet"] = DURUM_ETIKETI.get(durum, durum or "")
    veri["rozet_renk"] = DURUM_RENGI.get(durum, "")
    # Toplam bilinmiyorsa (yayın sürüyor) dolu çubuk yanıltıcı olurdu.
    veri["ilerleme"] = min(1.0, izlenen / toplam) if toplam else None
    veri["alt"] = f"İzlenen: {izlenen}/{toplam or '?'}" + (f" · ★ {skor:g}" if skor else "")
    veri["puan"] = None
    return veri


class IzlemeUclari:
    """``izleme_durumu``, ``izleme_listesi``, ``izleme_senkron``."""

    def __init__(self, kopru: Kopru, servis):
        self._kopru = kopru
        self._servis = servis
        servis.list_ready.connect(self._liste_geldi)
        servis.list_failed.connect(self._liste_hatasi)
        servis.sync_done.connect(lambda sayi: kopru.yay("izleme_senkron", {"sayi": int(sayi)}))
        servis.auth_changed.connect(lambda _k: kopru.yay("anilist_giris", self.izleme_durumu()))
        self._son_durum = "CURRENT"

    def _liste_geldi(self, durum: str, girisler: Any) -> None:
        kartlar = [izleme_karti(g) for g in (girisler or []) if isinstance(g, dict)]
        self._kopru.yay("izleme_listesi", {"durum": durum, "kartlar": kartlar})

    def _liste_hatasi(self, mesaj: str) -> None:
        self._kopru.yay("izleme_hatasi", {"durum": self._son_durum, "mesaj": str(mesaj)})

    @uc()
    def izleme_durumu(self) -> Dict[str, Any]:
        from ..qt.anilist import DURUM_RENGI, DURUMLAR
        kullanici = getattr(self._servis, "kullanici", None)      # özellik
        return {
            "giris": bool(self._servis.giris_var_mi()),
            "ad": str((kullanici if isinstance(kullanici, dict) else {}).get("name") or ""),
            "durumlar": [{"kod": kod, "etiket": etiket, "renk": DURUM_RENGI.get(kod, "")}
                         for kod, etiket in DURUMLAR],
        }

    @uc()
    def izleme_listesi(self, durum: str = "CURRENT") -> bool:
        """Listeyi iste; sonuç `izleme_listesi` olayıyla gelir."""
        from ..qt.anilist import DURUM_ETIKETI
        if durum not in DURUM_ETIKETI:
            raise ValueError(f"bilinmeyen liste durumu: {durum}")
        if not self._servis.giris_var_mi():
            raise ValueError("AniList girişi yok")
        self._son_durum = durum
        return bool(self._servis.liste_getir(durum))

    @uc()
    def izleme_senkron(self) -> bool:
        return bool(self._servis.yereli_senkronla())


__all__ = ["IzlemeUclari", "izleme_karti"]
