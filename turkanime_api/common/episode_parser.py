"""
Universal episode + season parser.

Kaynaklar bölüm başlıklarını farklı formatlarda veriyor:
    - "1. Bölüm",  "Bölüm 1",  "Episode 1",  "Ep. 1"
    - "S01E02",  "S1E2",  "s01e02"
    - "1x02",  "01x02"
    - "B01S02",  "b1s2",  "B01-S02"  (Türkçe varyant)
    - "Sezon 1 Bölüm 2",  "Season 2 Episode 13"
    - "01_02",  "S01.E02",  "S01 E02"
    - Çıplak sayı: "12",  "12 - The Title"
    - "Bölüm 12 Final",  "Movie",  "OVA 1"

Bu modül tek bir API verir: ``parse_episode(text)`` → :class:`EpisodeInfo`.
Skor + olası tüm permutasyonlar tek regex tablosu üzerinden çözülür.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class EpisodeInfo:
    """Bölüm/sezon parse sonucu.

    ``raw``      — orijinal metin
    ``season``   — sezon numarası (yoksa None)
    ``episode``  — bölüm numarası (yoksa None)
    ``label``    — özel etiket: "movie", "ova", "special", "final" vb (yoksa None)
    ``title``    — bölüm adı kalıntısı (numarayı temizledikten sonra)
    ``score``    — eşleştirme güveni (0..1) — hangi pattern'in eşleştiğine göre
    """
    raw: str
    season: Optional[int]
    episode: Optional[int]
    label: Optional[str]
    title: str
    score: float

    def normalized(self) -> str:
        """Normalize edilmiş kanonik form üret.

        Örnekler:
            S01E02
            S01E02 (Final)
            Movie
            OVA 1
        """
        if self.label and self.episode is None:
            return self.label.upper() if not self.label.endswith("1") else self.label
        if self.season is not None and self.episode is not None:
            base = f"S{self.season:02d}E{self.episode:02d}"
        elif self.episode is not None:
            base = f"E{self.episode:02d}"
        else:
            return self.raw.strip()
        if self.label:
            base += f" ({self.label.title()})"
        return base

    def key(self) -> Tuple[int, int]:
        """Sıralama için (season, episode) tuple — None'lar 0 sayılır."""
        return (self.season or 0, self.episode or 0)


# ─────────────────────────────────────────────────────────────────────────────
# Pattern tablosu — sıra önemli: önce en spesifik
# ─────────────────────────────────────────────────────────────────────────────

# Bir sayıyı yakalayan grup: en az 1, en fazla 4 hane
_N = r"(\d{1,4})"

# (regex, season_group, episode_group, score) — group indeksleri 1-tabanlı int
_PATTERNS: List[Tuple[re.Pattern, Optional[int], Optional[int], float]] = [
    # S01E02, S1E2, S01.E02, S01 E02, S01-E02
    (re.compile(rf"(?i)\bS\s*{_N}[\s._-]*E\s*{_N}\b"), 1, 2,1.0),
    # 1x02, 01x02
    (re.compile(rf"\b{_N}\s*[xX]\s*{_N}\b"), 1, 2,0.95),
    # B01S02 / b1s2 / B01-S02  (Türkçe: B=Bölüm, S=Sezon — tersi varyant)
    (re.compile(rf"(?i)\bB\s*{_N}[\s._-]*S\s*{_N}\b"), 2, 1,0.95),
    # S01B02 (S=Sezon, B=Bölüm — yine Türkçe)
    (re.compile(rf"(?i)\bS\s*{_N}[\s._-]*B\s*{_N}\b"), 1, 2,0.95),
    # "Sezon 1 Bölüm 2" / "Season 2 Episode 13"
    (re.compile(rf"(?i)(?:Season|Sezon)\s*{_N}\s*(?:Episode|Bölüm|Bolum)\s*{_N}"), 1, 2,0.95),
    # "Bölüm 2 Sezon 1" / "Episode 13 Season 2"
    (re.compile(rf"(?i)(?:Episode|Bölüm|Bolum)\s*{_N}\s*(?:Season|Sezon)\s*{_N}"), 2, 1,0.95),
    # Yalnız sezon: "Sezon 2" / "Season 2"  → episode None
    (re.compile(rf"(?i)(?:Season|Sezon)\s*{_N}\b"), 1, None,0.7),
    # "Bölüm 12" / "Bolum 12" / "Episode 12" / "Ep. 12" / "Ep 12"
    (re.compile(rf"(?i)(?:Episode|Bölüm|Bolum|Ep\.?)\s*{_N}\b"), None, 1,0.9),
    # "12. Bölüm" / "12.Bolum" / "12. Episode"
    (re.compile(rf"(?i)\b{_N}\s*\.\s*(?:Bölüm|Bolum|Episode|Ep\.?)\b"), None, 1,0.9),
    # "S01" yalnız sezon
    (re.compile(rf"(?i)\bS\s*{_N}\b"), 1, None,0.6),
    # "E12" / "E 12"
    (re.compile(rf"(?i)\bE\s*{_N}\b"), None, 1,0.7),
    # "01_02"  (sezon_bolum)
    (re.compile(rf"\b{_N}_{_N}\b"), 1, 2,0.6),
    # Çıplak: tek sayı (en zayıf eşleşme) — string'in başında
    (re.compile(rf"^\s*{_N}(?:\s*[-–:|.]|\s*$)"), None, 1,0.5),
]

_LABEL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bmovie\b|\bfilm\b"), "movie"),
    (re.compile(r"(?i)\bOVA\s*(\d*)\b"), "ova"),
    (re.compile(r"(?i)\bspecial\s*(\d*)\b|\bözel\s*(\d*)\b"), "special"),
    (re.compile(r"(?i)\bfinal\b|\bson bölüm\b"), "final"),
    (re.compile(r"(?i)\brecap\b"), "recap"),
    (re.compile(r"(?i)\bopening\b|\bOP\b"), "opening"),
    (re.compile(r"(?i)\bending\b|\bED\b"), "ending"),
]


def _detect_label(text: str) -> Optional[str]:
    for pat, name in _LABEL_PATTERNS:
        if pat.search(text):
            return name
    return None


def parse_episode(text: str) -> EpisodeInfo:
    """Bir bölüm başlığını parse et.

    Args:
        text: Kaynak adapter'ın verdiği ham başlık (ör. "Bölüm 12 - Yeni Dünya")

    Returns:
        EpisodeInfo - hiçbir şey eşleşmezse season=episode=None ve score=0.
    """
    if not text:
        return EpisodeInfo(raw="", season=None, episode=None, label=None, title="", score=0.0)

    raw = text.strip()
    label = _detect_label(raw)

    for pat, sgrp, egrp, score in _PATTERNS:
        m = pat.search(raw)
        if not m:
            continue
        try:
            season = int(m.group(sgrp)) if sgrp else None
            episode = int(m.group(egrp)) if egrp else None
        except (ValueError, IndexError):
            continue
        # Numara mantıklı mı? (4 hane max, 0 değil)
        if episode is not None and (episode > 9999 or episode == 0):
            # season 0 olabilir ama episode 0 nadir
            if score < 0.7:
                continue
        # Numarayı sil, kalan başlık
        title = (raw[:m.start()] + raw[m.end():]).strip(" -–:|.")
        return EpisodeInfo(
            raw=raw, season=season, episode=episode,
            label=label, title=title, score=score,
        )

    # Hiçbir pattern eşleşmedi
    if label:
        return EpisodeInfo(raw=raw, season=None, episode=None, label=label, title=raw, score=0.5)
    return EpisodeInfo(raw=raw, season=None, episode=None, label=None, title=raw, score=0.0)


def normalize_title(text: str) -> str:
    """Bir bölüm başlığını kanonik forma çevir.

    Kısa yol — sadece normalize edilmiş string ister."""
    return parse_episode(text).normalized()


def sort_episodes(titles: List[str]) -> List[Tuple[str, EpisodeInfo]]:
    """Bir başlık listesini (season, episode) sırasına göre sırala.

    Returns:
        [(original_title, EpisodeInfo), ...]
    """
    parsed = [(t, parse_episode(t)) for t in titles]
    return sorted(parsed, key=lambda x: x[1].key())


__all__ = ["EpisodeInfo", "parse_episode", "normalize_title", "sort_episodes"]
