"""Değişiklik sınıflandırması ve commit mesajı üretimi.

Saf fonksiyonlar: git'e de diske de dokunmaz, yalnızca yol listelerinden anlam
çıkarır. Mesajın işi `git log`'a bakan insanın "bu turda ne oldu"yu tek satırda
görmesi; bu yüzden başlık dosya değil **anime/bölüm** sayar — 340 dosyanın
değiştiğini bilmek hiçbir şey anlatmaz, 340 bölümün güncellendiğini bilmek
anlatır.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set, Tuple

DIZIN = "dizin.json"
ANIME_KOKU = "animeler/"
# Anime klasöründe bölüm akışı *olmayan* dosyalar.
META_DOSYALARI = {"info.json", "bolumler.json"}


def _parcala(yol: str) -> Tuple[Optional[str], Optional[str]]:
    """``animeler/<slug>/<dosya>`` → ``(slug, dosya)``; değilse ``(None, None)``."""
    duz = yol.replace("\\", "/")
    if not duz.startswith(ANIME_KOKU):
        return None, None
    kalan = duz[len(ANIME_KOKU):].split("/")
    if len(kalan) != 2 or not kalan[0] or not kalan[1]:
        return None, None
    return kalan[0], kalan[1]


def _bol(yollar: Iterable[str]) -> Tuple[Set[str], Set[str]]:
    """``(anime_slug'ları, bölüm dosyaları)`` — meta dosyaları bölüm sayılmaz."""
    animeler: Set[str] = set()
    bolumler: Set[str] = set()
    for yol in yollar:
        slug, dosya = _parcala(yol)
        if slug is None:
            continue
        animeler.add(slug)
        if dosya == "info.json":
            continue
        if dosya not in META_DOSYALARI:
            bolumler.add(yol)
    return animeler, bolumler


@dataclass(frozen=True)
class Degisim:
    """Bir yayın turunda arşivde olan biten."""

    eklenen: Tuple[str, ...] = ()
    degisen: Tuple[str, ...] = ()
    silinen: Tuple[str, ...] = ()

    # ── Türetilmişler ───────────────────────────────────────────────────────
    @property
    def bos(self) -> bool:
        return not (self.eklenen or self.degisen or self.silinen)

    @property
    def dosya_sayisi(self) -> int:
        return len(self.eklenen) + len(self.degisen) + len(self.silinen)

    @property
    def yeni_animeler(self) -> Set[str]:
        """`info.json`'ı yeni oluşan anime = arşive yeni giren anime."""
        return {s for s in (_parcala(y)[0] for y in self.eklenen
                            if _parcala(y)[1] == "info.json") if s}

    @property
    def silinen_animeler(self) -> Set[str]:
        return {s for s in (_parcala(y)[0] for y in self.silinen
                            if _parcala(y)[1] == "info.json") if s}

    @property
    def guncel_animeler(self) -> Set[str]:
        """Dokunulan ama yeni de silinmiş de olmayan animeler."""
        dokunulan = (_bol(self.eklenen)[0] | _bol(self.degisen)[0]
                     | _bol(self.silinen)[0])
        return dokunulan - self.yeni_animeler - self.silinen_animeler

    @property
    def yeni_bolumler(self) -> Set[str]:
        return _bol(self.eklenen)[1]

    @property
    def guncel_bolumler(self) -> Set[str]:
        return _bol(self.degisen)[1]

    @property
    def silinen_bolumler(self) -> Set[str]:
        return _bol(self.silinen)[1]

    @property
    def dizin_degisti(self) -> bool:
        return any(DIZIN in (y, y.replace("\\", "/"))
                   for y in (*self.eklenen, *self.degisen))

    def sozluk(self) -> Dict[str, Any]:
        return {
            "eklenen": len(self.eklenen),
            "degisen": len(self.degisen),
            "silinen": len(self.silinen),
            "yeni_anime": len(self.yeni_animeler),
            "guncel_anime": len(self.guncel_animeler),
            "silinen_anime": len(self.silinen_animeler),
            "yeni_bolum": len(self.yeni_bolumler),
            "guncel_bolum": len(self.guncel_bolumler),
            "silinen_bolum": len(self.silinen_bolumler),
            "dizin": self.dizin_degisti,
        }


def _satir(etiket: str, ciftler: Iterable[Tuple[int, str]]) -> Optional[str]:
    parcalar = [f"{n} {ad}" for n, ad in ciftler if n]
    return f"{etiket}: " + ", ".join(parcalar) if parcalar else None


def mesaj_uret(degisim: Degisim, toplam_anime: Optional[int] = None) -> str:
    """Commit mesajı: tek satır özet + sayıların döküldüğü gövde."""
    yeni_a, silinen_a = len(degisim.yeni_animeler), len(degisim.silinen_animeler)
    yeni_b, guncel_b = len(degisim.yeni_bolumler), len(degisim.guncel_bolumler)
    silinen_b = len(degisim.silinen_bolumler)

    basliklar = []
    if yeni_a:
        basliklar.append(f"+{yeni_a} anime")
    if silinen_a:
        basliklar.append(f"-{silinen_a} anime")
    if yeni_b:
        basliklar.append(f"+{yeni_b} bölüm")
    if guncel_b:
        basliklar.append(f"~{guncel_b} bölüm güncellendi")
    if not basliklar and degisim.dizin_degisti:
        basliklar.append("dizin güncellendi")
    if not basliklar:
        basliklar.append(f"{degisim.dosya_sayisi} dosya güncellendi")

    govde = [
        _satir("Dosya", [(len(degisim.eklenen), "eklendi"),
                         (len(degisim.degisen), "güncellendi"),
                         (len(degisim.silinen), "silindi")]),
        _satir("Anime", [(yeni_a, "yeni"),
                         (len(degisim.guncel_animeler), "güncellendi"),
                         (silinen_a, "kaldırıldı")]),
        _satir("Bölüm", [(yeni_b, "yeni"), (guncel_b, "güncellendi"),
                         (silinen_b, "kaldırıldı")]),
    ]
    if toplam_anime is not None:
        govde.append(f"Toplam: {toplam_anime} anime")

    metin = "arşiv: " + ", ".join(basliklar)
    dolu = [s for s in govde if s]
    return metin + "\n\n" + "\n".join(dolu) + "\n" if dolu else metin + "\n"


__all__ = ["Degisim", "mesaj_uret", "DIZIN", "ANIME_KOKU"]
