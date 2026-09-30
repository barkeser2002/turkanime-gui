"""Anime kaydı okuma yardımcıları (Qt'siz): başlık, kapak, puan, künye satırı.

Keşif (Jikan/AniList) ve arşiv künyesi aynı sözlük şeklini üretiyor
(`title.romaji`, `coverImage.large`, `averageScore` 0-100, `genres`,
`studios`...). Sayfalar bu alanları buradan okuyor; iki kaynağın farklı
biçimleri (düz liste / `{"nodes": ...}`) tek yerde soğuruluyor.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

def anime_title(item: Dict[str, Any]) -> str:
    """Kart başlığı. Jikan ve AniList'te `title` bir sözlüktür."""
    title = item.get("title")
    if isinstance(title, dict):
        return (title.get("romaji") or title.get("english")
                or title.get("native") or "İsimsiz")
    return str(title or "İsimsiz")


def cover_url(item: Dict[str, Any]) -> Optional[str]:
    """Kapak görseli URL'si (yoksa None)."""
    cover = item.get("coverImage")
    if isinstance(cover, dict):
        return cover.get("large") or cover.get("medium")
    return None


def score_of(item: Dict[str, Any]) -> Optional[float]:
    """0-100 aralığındaki puan. İki istemci de bu ölçeği kullanır."""
    raw = item.get("averageScore")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


# AniList/Jikan büyük harfli sabitler döndürür; kullanıcıya Türkçe gösteriyoruz.
SEASON_LABELS = {"WINTER": "Kış", "SPRING": "İlkbahar", "SUMMER": "Yaz",
                 "FALL": "Sonbahar"}


STATUS_LABELS = {"RELEASING": "Yayında", "FINISHED": "Tamamlandı",
                 "NOT_YET_RELEASED": "Henüz yayınlanmadı",
                 "CANCELLED": "İptal edildi", "HIATUS": "Ara verildi"}


# AniList `format` sabitleri; arşivin "Kategori"si ("TV", "Film", "OVA")
# zaten Türkçe/okunur olduğu için tabloda olmayan değer olduğu gibi basılır.
FORMAT_LABELS = {"MOVIE": "Film", "TV_SHORT": "TV (kısa)", "SPECIAL": "Özel",
                 "MUSIC": "Müzik"}


AY_ADLARI = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


_TAG_RE = re.compile(r"<[^>]+>")


_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


_BLANKS_RE = re.compile(r"\n{3,}")


# ── Veri yardımcıları (Qt'siz; doğrudan test edilebilir) ────────────────────
def clean_html(text: Any) -> str:
    """Özeti düz metne indir.

    Hem AniList hem Jikan özeti HTML taşır (`<br>`, `<i>`, `&quot;`). Ham
    hâliyle basılırsa kullanıcı etiketleri okur.
    """
    if not text:
        return ""
    plain = _BR_RE.sub("\n", str(text))
    plain = _TAG_RE.sub("", plain)
    return _BLANKS_RE.sub("\n\n", html.unescape(plain)).strip()


def studio_names(item: Dict[str, Any]) -> List[str]:
    """Stüdyo adları — iki farklı kaynak şekli de desteklenir.

    Jikan `to_anilist_format` düz `List[str]` üretir, AniList GraphQL ise
    `{"nodes": [{"name": ...}]}` döndürür. Birini varsayan kod diğerinde
    sessizce boş liste gösterir; eski GUI de bu yüzden iki dalı taşıyordu.
    """
    raw = item.get("studios")
    out: List[str] = []
    if isinstance(raw, dict):
        for node in (raw.get("nodes") or []):
            if isinstance(node, dict) and node.get("name"):
                out.append(str(node["name"]).strip())
        for edge in (raw.get("edges") or []):
            node = (edge or {}).get("node") if isinstance(edge, dict) else None
            if isinstance(node, dict) and node.get("name"):
                out.append(str(node["name"]).strip())
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
            elif isinstance(entry, dict) and entry.get("name"):
                # Ham Jikan kaydı (to_anilist_format'tan geçmemiş)
                out.append(str(entry["name"]).strip())
    return [s for s in out if s]


def genre_names(item: Dict[str, Any]) -> List[str]:
    """Tür adları — düz metin listesi ya da `{"name": ...}` sözlükleri."""
    raw = item.get("genres")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif isinstance(entry, dict) and entry.get("name"):
            out.append(str(entry["name"]).strip())
    return out


def tarih_metni(tarih: Any) -> str:
    """AniList `{"year", "month", "day"}` → "7 Nisan 2009" (eksik alan atlanır)."""
    if not isinstance(tarih, dict) or not tarih.get("year"):
        return ""
    parcalar: List[str] = []
    ay = tarih.get("month")
    if isinstance(ay, int) and 1 <= ay <= 12:
        if tarih.get("day"):
            parcalar.append(str(int(tarih["day"])))
        parcalar.append(AY_ADLARI[ay - 1])
    parcalar.append(str(tarih["year"]))
    return " ".join(parcalar)


def meta_line(item: Dict[str, Any]) -> str:
    """"12 bölüm • 24 dk • Yaz 2026 • Tamamlandı" biçimindeki özet satır."""
    parts: List[str] = []
    if item.get("episodes"):
        parts.append(f"{item['episodes']} bölüm")
    if item.get("duration"):
        parts.append(f"{item['duration']} dk")
    fmt = str(item.get("format") or "").strip()
    if fmt:
        parts.append(FORMAT_LABELS.get(fmt.upper(), fmt))

    season = SEASON_LABELS.get(str(item.get("season") or "").upper())
    year = item.get("seasonYear")
    baslangic = tarih_metni(item.get("startDate"))
    bitis = tarih_metni(item.get("endDate"))
    if season and year:
        parts.append(f"{season} {year}")
    elif season:
        parts.append(season)
    elif baslangic:
        # Sezonu olmayan kayıt (arşiv künyesi) tarih taşıyor; yıl zaten içinde.
        # Sezon bilinen kayıtta tarih YAZILMIYOR: satır iki kez yıl söylerdi.
        parts.append(baslangic if bitis in ("", baslangic)
                     else f"{baslangic} – {bitis}")
    elif year:
        parts.append(str(year))

    status = STATUS_LABELS.get(str(item.get("status") or "").upper())
    if status:
        parts.append(status)
    return " • ".join(parts)


def kunye_birlestir(anime: Dict[str, Any], ek: Dict[str, Any]) -> Dict[str, Any]:
    """``ek``'teki alanlarla YALNIZCA boş olanları doldur (kopya döner).

    Arşiv künyesi keşiften gelen tam AniList kaydının üstüne yazılmamalı
    (AniList türleri, puanı, özeti daha zengin); ama arama sonucundan gelen
    başlık-yalnızca kayıtta her alan boştur ve künye onları doldurur.
    Başlık sözlüğü alan alan birleşir: "native" eksikse eklenir, mevcut
    "romaji" korunur.
    """
    out = dict(anime or {})
    for anahtar, deger in (ek or {}).items():
        if anahtar == "title" and isinstance(deger, dict):
            basliklar = dict(out.get("title") or {}) if isinstance(
                out.get("title"), dict) else {}
            for alt, metin in deger.items():
                if metin and not basliklar.get(alt):
                    basliklar[alt] = metin
            out["title"] = basliklar
        elif deger not in (None, "", [], {}) and not out.get(anahtar):
            out[anahtar] = deger
    return out



__all__ = ["anime_title", "cover_url", "score_of", "clean_html", "studio_names",
           "genre_names", "tarih_metni", "meta_line", "kunye_birlestir",
           "SEASON_LABELS", "STATUS_LABELS", "FORMAT_LABELS", "AY_ADLARI"]
