"""Sayfalara giden verinin biçimi — Python kayıtlarından JSON'a uygun sözlük.

Web sayfası kayıtları olduğu gibi değil, gösterime hazır alanlarla alıyor
(başlık, kapak, 0-10 puan, rozet, alt satır). Ham kayıt da ``kayit``
alanında gidiyor: kullanıcı karta tıkladığında sayfa onu geri yolluyor ve
Python tarafı (detay sayfası) kaydın tamamıyla çalışıyor — ikinci bir ağ
isteği yok.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Kayıt okuma yardımcıları eski Qt sayfalarında (`pages.discover`,
# `pages.detail`); içe aktarım fonksiyon içinde: `gui.qt` paketi açılırken
# `app` → `gui.web` zinciri bu modülü yüklüyor, modül düzeyinde geri import
# döngü olurdu.


# AniList/MyAnimeList türleri İngilizce geliyor; kartta Türkçe gösteriliyor.
# Tabloda olmayan tür (yeni eklenmiş, özel ad) olduğu gibi kalır.
TUR_ADLARI = {
    "Action": "Aksiyon", "Adventure": "Macera", "Comedy": "Komedi",
    "Drama": "Dram", "Fantasy": "Fantastik", "Horror": "Korku",
    "Mahou Shoujo": "Büyülü Kız", "Music": "Müzik", "Mystery": "Gizem",
    "Psychological": "Psikolojik", "Romance": "Romantik",
    "Sci-Fi": "Bilim Kurgu", "Slice of Life": "Gündelik Yaşam",
    "Sports": "Spor", "Supernatural": "Doğaüstü", "Thriller": "Gerilim",
    "Suspense": "Gerilim", "Award Winning": "Ödüllü", "Gourmet": "Yemek",
    "Avant Garde": "Avangart", "Mecha": "Mecha", "Ecchi": "Ecchi",
}


def tur_adi(tur: str) -> str:
    return TUR_ADLARI.get(str(tur), str(tur))


def puan10(item: Dict[str, Any]) -> Optional[float]:
    """0-100 puanı 0-10'a (bir basamak); puansız kayıtta ``None``."""
    from ..qt.pages.discover import score_of
    puan = score_of(item)
    return round(puan / 10.0, 1) if puan else None


def alt_satir(item: Dict[str, Any]) -> str:
    """Kartın alt satırı: ``"2026 · TV · Aksiyon"``."""
    from ..qt.pages.detail import FORMAT_LABELS, genre_names
    parcalar: List[str] = []
    yil = item.get("seasonYear")
    if yil:
        parcalar.append(str(yil))
    bicim = str(item.get("format") or "").strip()
    if bicim:
        parcalar.append(FORMAT_LABELS.get(bicim.upper(), bicim))
    turler = genre_names(item)
    if turler:
        parcalar.append(tur_adi(turler[0]))
    return " · ".join(parcalar)


def kart(item: Dict[str, Any]) -> Dict[str, Any]:
    """Keşif/izleme listesi kaydından kart verisi."""
    from ..qt.pages.discover import anime_title, cover_url
    baslik = anime_title(item)
    puan = puan10(item)
    bolum = item.get("episodes")
    ipucu = [baslik]
    if puan is not None:
        ipucu.append(f"Puan: {puan:.1f}/10")
    if bolum:
        ipucu.append(f"{bolum} bölüm")
    return {
        "baslik": baslik,
        "kapak": cover_url(item) or "",
        "puan": puan,
        "rozet": f"{bolum} bölüm" if bolum else "",
        "alt": alt_satir(item),
        "ipucu": "\n".join(ipucu),
        "kayit": item,
    }


def kartlar(items: Any) -> List[Dict[str, Any]]:
    return [kart(i) for i in (items or []) if isinstance(i, dict)]


__all__ = ["kart", "kartlar", "puan10", "alt_satir", "tur_adi", "TUR_ADLARI"]
