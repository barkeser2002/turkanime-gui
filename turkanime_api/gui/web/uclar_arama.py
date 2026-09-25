"""Arama sayfasının köprü uçları — bütün kaynaklarda artımlı arama.

Sonuçlar KAYNAK KAYNAK geliyor (`SearchEngine.artimli_ara`): yerel arşivin
anlık sonuçları, en yavaş ağ kaynağını beklemeden sayfaya düşüyor. Akış
olaylarla:

``arama_kaynaklar``  aranan kaynakların listesi (sayfa her biri için
                     "aranıyor" hapı çiziyor)
``arama_kaynak``     bir kaynak bitti: kartları ya da hatası
``arama_bitti``      hepsi bitti: yetişemeyenler ("zaman aşımı") dahil hatalar

Her arama bir istek numarası taşıyor; yeni sorgu süren aramayı beklemeden
başlıyor, sayfa eski isteğin geç olaylarını atıyor.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

from ...sources import kayit as kaynak_kaydi
from .kopru import Kopru, uc

# Kaynak başına gösterilecek azami sonuç (eski Qt sayfasıyla aynı).
LIMIT_PER_SOURCE = 10
# Bir kaynağın hata sebebi en çok bu kadar karakter.
HATA_SEBEBI_SINIRI = 300


def kisalt(metin: Any, sinir: int = HATA_SEBEBI_SINIRI) -> str:
    metin = " ".join(str(metin).split())
    return metin if len(metin) <= sinir else metin[:sinir - 1].rstrip() + "…"


def kaynak_sirasi(ad: str):
    """Grupların sırası: kayıt sırası, yalnız-metadata kaynakları EN SONDA.

    Oynatılamayan AniList kayıtları ilk ve en göze batan kartlar olmasın
    (eski Qt sayfasındaki kuralın aynısı).
    """
    kaynak = kaynak_kaydi.bul(ad)
    if kaynak is None:
        return (1, 0, str(ad))
    sira = [k.ad for k in kaynak_kaydi.kaynaklar()].index(kaynak.ad)
    return (2 if kaynak.yalnizca_metadata else 0, sira, str(ad))


def kaynak_bilgisi(ad: str) -> Dict[str, Any]:
    kaynak = kaynak_kaydi.bul(ad)
    return {
        "ad": ad,
        "etiket": kaynak_kaydi.gorunen_ad(ad),
        "renk": kaynak.renk if kaynak is not None else "",
        "kisaltma": kaynak.kisaltma if kaynak is not None else "",
        "metadata": bool(kaynak is not None and kaynak.yalnizca_metadata),
    }


def sonuc_kartlari(kaynak: str, kayitlar: Any) -> List[Dict[str, Any]]:
    """Kaynağın arama kayıtlarından kartlar.

    Slug'sız kayıt atılıyor: tıklanabilir ama bölüm sayfası boş kimlikle
    sorgulanırdı, kullanıcı sessiz bir hiçlikle karşılaşırdı.
    """
    bilgi = kaynak_bilgisi(kaynak)
    kartlar = []
    for item in kayitlar or []:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        baslik = str(item.get("title") or item["slug"])
        kartlar.append({
            "baslik": baslik,
            "kapak": str(item.get("image") or ""),
            "puan": None,
            "rozet": bilgi["kisaltma"],
            "rozet_renk": bilgi["renk"],
            "alt": bilgi["etiket"],
            "ipucu": f"{baslik}\n{bilgi['etiket']}",
            "kaynak": kaynak,
            "slug": str(item["slug"]),
            "kayit": dict(item),
        })
    return kartlar


class AramaUclari:
    """``ara`` (arama başlatır, akış olaylarla)."""

    def __init__(self, kopru: Kopru):
        self._kopru = kopru
        self._sayac = itertools.count(1)
        self.son_istek = 0

    @uc()
    def ara(self, sorgu: str, istek: Optional[int] = None,
            kaynak: str = "") -> Dict[str, Any]:
        """Aramayı başlat. ``istek`` numarasını sayfa veriyor: olaylar yanıttan
        önce gelse bile sayfa hangi aramaya ait olduklarını biliyor.
        ``kaynak`` verilirse yalnızca o kaynakta aranır (üst çubuktaki seçim)."""
        sorgu = " ".join(str(sorgu or "").split())
        if not sorgu:
            raise ValueError("arama metni boş")
        istek = self.son_istek = int(istek) if istek is not None else next(self._sayac)
        from ..qt.workers import run_bg
        run_bg(self._ara, istek, sorgu, str(kaynak or ""))
        return {"istek": istek, "sorgu": sorgu}

    def _ara(self, istek: int, sorgu: str, kaynak: str = "") -> None:
        """Arka plan: bütün kaynaklarda paralel ara, bittikçe olay yay."""
        from ...common.adapters import SearchEngine, arama_motoru
        from ...common.hatalar import sebep_metni

        yay = self._kopru.yay
        gelen = set()

        def kaynak_bitti(ad: str, kayitlar: Any, hata: Optional[str]) -> None:
            gelen.add(ad)
            yay("arama_kaynak", {
                "istek": istek, "kaynak": kaynak_bilgisi(ad),
                "kartlar": sonuc_kartlari(ad, kayitlar),
                "hata": kisalt(hata) if hata else "",
            })

        try:
            motor = arama_motoru([kaynak]) if kaynak else SearchEngine()
            adlar = sorted(getattr(motor, "adapters", None) or [], key=kaynak_sirasi)
            yay("arama_kaynaklar", {"istek": istek, "sorgu": sorgu,
                                    "kaynaklar": [kaynak_bilgisi(a) for a in adlar]})
            if hasattr(motor, "artimli_ara"):
                sonuc = motor.artimli_ara(sorgu, kaynak_bitti,
                                          limit_per_source=LIMIT_PER_SOURCE)
            else:            # eski sözleşmeli motor (sahteler): tek seferde
                sonuc = motor.search_all_sources_rich(
                    sorgu, limit_per_source=LIMIT_PER_SOURCE)
            for ad, kayitlar in (sonuc or {}).items():
                if ad not in gelen:
                    kaynak_bitti(ad, kayitlar, None)
            hatalar = {ad: kisalt(sebep) for ad, sebep
                       in (getattr(sonuc, "hatalar", None) or {}).items()}
        except Exception as exc:
            yay("arama_bitti", {"istek": istek, "hata": sebep_metni(exc),
                                "hatalar": {}})
            return
        yay("arama_bitti", {"istek": istek, "hata": "", "hatalar": hatalar})


__all__ = ["AramaUclari", "sonuc_kartlari", "kaynak_sirasi", "kaynak_bilgisi",
           "LIMIT_PER_SOURCE"]
