"""
Universal episode + season parser **ve** çok kaynaklı bölüm birleştirici.

Kaynaklar bölüm başlıklarını farklı formatlarda veriyor:
    - "1. Bölüm",  "Bölüm 1",  "Episode 1",  "Ep. 1"
    - "S01E02",  "S1E2",  "s01e02"
    - "1x02",  "01x02"
    - "B01S02",  "b1s2",  "B01-S02"  (Türkçe varyant)
    - "Sezon 1 Bölüm 2",  "Season 2 Episode 13"
    - "2. Sezon 5. Bölüm",  "Sezon 2 - 5. Bölüm"  (Türkçe sıra sayısı)
    - "01_02",  "S01.E02",  "S01 E02"
    - Çıplak sayı: "12",  "12 - The Title"
    - "Bölüm 12 Final",  "Movie",  "OVA 1"

Bu modül üç API verir:
    ``parse_episode(text)``       → :class:`EpisodeInfo` (zengin sonuç)
    ``extract_episode_info(text)``→ ``(sezon, bölüm)`` (eski GUI sözleşmesi)
    ``merge_episodes(data)``      → kaynakları tek listede birleştirir

Parser burada TEK yerde durur: `common/ui.py` (CustomTkinter GUI) ve
`gui/qt/pages/episodes.py` (Qt GUI) aynı fonksiyonları import eder. İki ayrı
regex seti tutmak, iki arayüzün aynı bölümü farklı numaralandırması demekti.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


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
    # ── Türkçe sıra sayılı varyantlar ───────────────────────────────────────
    # Bunlar "yalnız sezon" kalıbından ÖNCE denenmeli: "2. Sezon 5. Bölüm"
    # metninde "Sezon 5" alt dizesi vardır ve sezon-yalnız kalıbı sezonu 5
    # sanıp bölümü hiç bulamaz.
    # "2. Sezon 5. Bölüm" / "2.Sezon 5.Bolum"
    (re.compile(rf"(?i)\b{_N}\s*\.\s*(?:Sezon|Season)\b.*?\b{_N}\s*\.\s*"
                r"(?:Bölüm|Bolum|Episode)\b"), 1, 2,0.95),
    # "2. Sezon Bölüm 5"
    (re.compile(rf"(?i)\b{_N}\s*\.\s*(?:Sezon|Season)\b.*?"
                rf"(?:Bölüm|Bolum|Episode)\s*{_N}\b"), 1, 2,0.95),
    # "Sezon 2 - 5. Bölüm"
    (re.compile(rf"(?i)(?:Sezon|Season)\s*{_N}\b.*?\b{_N}\s*\.\s*"
                r"(?:Bölüm|Bolum|Episode)\b"), 1, 2,0.95),
    # Yalnız sezon: "Sezon 2" / "Season 2"  → episode None
    (re.compile(rf"(?i)(?:Season|Sezon)\s*{_N}\b"), 1, None,0.7),
    # "12. Bölüm" / "12.Bolum" / "12. Episode" — Türkçe kaynakların baskın
    # biçimi, bu yüzden "Bölüm 12"den ÖNCE denenir: "5. Bölüm 2. Kısım"
    # metninde ters sırada "Bölüm 2" eşleşir ve bölüm yanlış numaralanır.
    (re.compile(rf"(?i)\b{_N}\s*\.\s*(?:Bölüm|Bolum|Episode|Ep\.?)\b"), None, 1,0.9),
    # "Bölüm 12" / "Bolum 12" / "Episode 12" / "Ep. 12" / "Ep 12"
    (re.compile(rf"(?i)(?:Episode|Bölüm|Bolum|Ep\.?)\s*{_N}\b"), None, 1,0.9),
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


# ─────────────────────────────────────────────────────────────────────────────
# Eski GUI sözleşmesi — `common/ui.py` bunları buradan import eder
# ─────────────────────────────────────────────────────────────────────────────
_FIRST_NUMBER_RE = re.compile(r"(\d+)")


def extract_episode_info(title: str) -> Tuple[int, int]:
    """Bölüm başlığından ``(sezon, bölüm)`` çıkar — her zaman sayı döndürür.

    `parse_episode` "bilmiyorum" diyebilmek için ``None`` döndürür; birleştirme
    sözlüğünün anahtarı ise sayı olmak zorunda. Bu sarmalayıcı eski GUI'nin
    varsayımlarını korur:

        - sezon bulunamazsa 1
        - bölüm bulunamazsa başlıktaki İLK sayı (çoğu kaynak "Anime Adı 5"
          gibi başlıklar veriyor), o da yoksa 0

    Örnekler:
        "S02E05" → (2, 5) · "2. Sezon 5. Bölüm" → (2, 5) · "Bölüm 5" → (1, 5)
        "01" → (1, 1) · "Movie" → (1, 0)
    """
    if not title:
        return (1, 0)

    info = parse_episode(title)
    if info.episode is not None:
        return (info.season or 1, info.episode)

    match = _FIRST_NUMBER_RE.search(title)
    return (info.season or 1, int(match.group(1)) if match else 0)


def extract_episode_number(title: str) -> int:
    """Yalnızca bölüm numarası (geriye uyumluluk sarmalayıcısı)."""
    return extract_episode_info(title)[1]


def normalize_episode_title(title: str, episode_num: int, season_num: int = 1) -> str:
    """Bölüm başlığını normalize et — anime ismini at, sezon bilgisini koru.

    "Anime İsmi 1. Bölüm" → "1. Bölüm" · "2. Sezon 5. Bölüm" → "S02E05"

    Amaç, aynı bölümü farklı kaynakların farklı adlandırmasına rağmen tek
    satırda gösterebilmek: satır etiketi hangi kaynağın önce geldiğine göre
    değişmemeli.
    """
    if not title:
        if season_num > 1:
            return f"S{season_num:02d}E{episode_num:02d}"
        return f"{episode_num}. Bölüm"

    # Sezon bilgisi varsa S0XE0Y formatında döndür
    if season_num > 1:
        return f"S{season_num:02d}E{episode_num:02d}"

    stripped = title.strip()
    # Zaten normalize edilmişse (sadece bölüm bilgisi varsa) olduğu gibi bırak
    if re.match(r'^\d+\s*\.\s*[Bb][öÖ]l[üÜ]m', stripped):
        return stripped
    if re.match(r'^[Bb][öÖ]l[üÜ]m\s*\d+', stripped):
        return stripped
    if re.match(r'^[Ee]pisode\s*\d+', stripped, re.IGNORECASE):
        return stripped
    if re.match(r'^[Ss]\d+[Ee]\d+', stripped):
        return stripped

    # "Anime İsmi X. Bölüm" formatından "X. Bölüm"ü çıkar
    match = re.search(r'(\d+\s*\.\s*[Bb][öÖ]l[üÜ]m.*?)$', title)
    if match:
        return match.group(1).strip()

    return f"{episode_num}. Bölüm"


# ─────────────────────────────────────────────────────────────────────────────
# Çok kaynaklı birleştirme
# ─────────────────────────────────────────────────────────────────────────────
def episode_key(entry: Dict[str, Any], index: int = 0) -> Tuple[int, int]:
    """Tek bir kaynak kaydı için ``(sezon, bölüm)`` kimliği üret.

    Öncelik sırası kaynakların ne verdiğine göre: adapter numarayı alan olarak
    veriyorsa ona güveniriz, vermiyorsa başlığı parse ederiz, o da tutmazsa
    listedeki sıraya düşeriz. Sıraya düşmek son çare: numarasız bölümler
    (özel/OVA) aksi hâlde tek anahtarda toplanıp birbirini yerdi.
    """
    number = entry.get('episode_number', entry.get('number', 0))
    season = entry.get('season_number', entry.get('season', 1)) or 1
    title = entry.get('title', '') or ''

    if not number:
        season, number = extract_episode_info(title)
    elif season == 1:
        # Numara alanı var ama sezon yok: sezonu yine de başlıktan deneyelim,
        # yoksa 2. sezonun 1. bölümü 1. sezonunkiyle aynı satıra düşer.
        title_season, _ = extract_episode_info(title)
        if title_season > 1:
            season = title_season

    if not number:
        number = index + 1
    return (int(season), int(number))


def merge_episodes(
    sources_data: Dict[str, Sequence[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Kaynak → bölüm listesi sözlüğünü tek sıralı listeye indir.

    Farklı kaynaklar aynı bölümü farklı adlandırır:
        TürkAnime "Anime İsmi 1. Bölüm" · AnimeciX "1. Bölüm" · Anizle "Bölüm 1"
    Bu yüzden birleştirme anahtarı başlık değil, ``(sezon, bölüm)`` çiftidir.

    Returns:
        ``[{"season": int, "number": int, "title": str,
            "sources": {kaynak_adı: kaynak_kaydı}}, ...]``
        (sezon, bölüm) sırasında — sayısal sıralama, yani 2 < 10.
    """
    merged: Dict[Tuple[int, int], Dict[str, Any]] = {}

    for source_name, episodes in (sources_data or {}).items():
        if not episodes:
            continue
        for index, entry in enumerate(episodes):
            if not isinstance(entry, dict):
                continue
            season, number = episode_key(entry, index)
            key = (season, number)
            row = merged.get(key)
            if row is None:
                row = {
                    'season': season,
                    'number': number,
                    'title': normalize_episode_title(
                        entry.get('title', '') or '', number, season),
                    'sources': {},
                }
                merged[key] = row
            # `setdefault`: aynı kaynakta iki başlık aynı anahtara düşerse
            # (ör. "5. Bölüm" ve "5. Bölüm 2. Kısım") ilk kayıt korunur;
            # üzerine yazmak asıl bölümü listeden silmek olurdu.
            row['sources'].setdefault(source_name, entry)

    return sorted(merged.values(), key=lambda row: (row['season'], row['number']))


__all__ = [
    "EpisodeInfo", "parse_episode", "normalize_title", "sort_episodes",
    "extract_episode_info", "extract_episode_number", "normalize_episode_title",
    "episode_key", "merge_episodes",
]
