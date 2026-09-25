"""Kitaplığım sayfasının köprü ucu: izlemeye devam, favoriler, geçmiş.

Veri `common.kutuphane`'den (``kutuphane.json``); her kayıt kaynağın KENDİ
anime kimliğini taşıyor, karta tıklamak detayı kaynağa bağlı açıyor
(eşleştirme yok). Okuma arka planda: `kutuphane_yolu` ayar dosyasını
okuyor ve sayfa her gösterimde tazeleniyor.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from ...common import kutuphane
from ...sources import kayit as kaynak_kaydi
from .kopru import uc
from .uclar_kesif import devam_karti, devam_kayitlari, kaynak_rengi


def tarih_metni(zaman: Any, simdi: float = None) -> str:
    """"az önce", "5 dk önce", "3 sa önce", "dün", "12.09.2026"."""
    try:
        zaman = float(zaman)
    except (TypeError, ValueError):
        return ""
    simdi = time.time() if simdi is None else simdi
    fark = max(0.0, simdi - zaman)
    if fark < 60:
        return "az önce"
    if fark < 3600:
        return f"{int(fark // 60)} dk önce"
    if fark < 86400:
        return f"{int(fark // 3600)} sa önce"
    if fark < 2 * 86400:
        return "dün"
    if fark < 7 * 86400:
        return f"{int(fark // 86400)} gün önce"
    try:
        return time.strftime("%d.%m.%Y", time.localtime(zaman))
    except (OverflowError, OSError, ValueError):
        return ""


def _kaynakli(kayit: Dict[str, Any]) -> Dict[str, Any]:
    kaynak = str(kayit.get("kaynak") or "")
    bilgi = kaynak_kaydi.bul(kaynak)
    return {
        "kaynak_adi": kaynak_kaydi.gorunen_ad(kaynak),
        "kaynak_renk": kaynak_rengi(kaynak),
        "rozet": bilgi.kisaltma if bilgi is not None else "",
    }


def favori_karti(kayit: Dict[str, Any]) -> Dict[str, Any]:
    ek = _kaynakli(kayit)
    return {"baslik": str(kayit.get("baslik") or kayit.get("kimlik") or ""),
            "kapak": str(kayit.get("kapak") or ""), "puan": None,
            "rozet": ek["rozet"], "rozet_renk": ek["kaynak_renk"],
            "alt": ek["kaynak_adi"], "kayit": kayit}


def gecmis_satiri(kayit: Dict[str, Any], seriler: Dict[str, Any]) -> Dict[str, Any]:
    seri = seriler.get(kutuphane.anahtar(str(kayit.get("kaynak") or ""),
                                         str(kayit.get("kimlik") or ""))) or {}
    acilacak = dict(kayit)
    if not acilacak.get("kapak") and seri.get("kapak"):
        acilacak["kapak"] = seri["kapak"]
    return {"baslik": str(kayit.get("baslik") or kayit.get("kimlik") or ""),
            "bolum": str(kayit.get("bolum_baslik") or kayit.get("bolum_slug") or ""),
            "kapak": str(acilacak.get("kapak") or ""),
            "zaman": tarih_metni(kayit.get("zaman")),
            **_kaynakli(kayit), "kayit": acilacak}


class KitaplikUclari:
    """``kitaplik``."""

    @uc(arka=True)
    def kitaplik(self) -> Dict[str, Any]:
        veri = kutuphane.oku()
        devam = [dict(devam_karti(k), **{"rozet": _kaynakli(k)["rozet"]})
                 for k in devam_kayitlari(veri)]
        favori = [favori_karti(k) for k in kutuphane.favoriler(veri)]
        gecmis = [gecmis_satiri(k, veri["seriler"]) for k in kutuphane.gecmis_listesi(veri)]
        return {"devam": devam, "favori": favori, "gecmis": gecmis}


__all__ = ["KitaplikUclari", "tarih_metni", "favori_karti", "gecmis_satiri"]
