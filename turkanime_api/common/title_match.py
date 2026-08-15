"""
Multi-language anime title search + fuzzy matching.

İşlevler:
    - ``get_title_aliases(query)``    — sorgu için romaji, english, japanese,
                                         turkish ve synonyms title'larını döndür
                                         (AniList + Jikan üzerinden, cache'li).
    - ``fuzzy_score(a, b)``           — iki string arasında 0..1 karakter
                                         tabanlı benzerlik skoru.
    - ``score_match(query, candidate, aliases=None)`` — sorgu + alias listesini
                                         aday başlığa karşı skorlar, en iyi
                                         skoru döndürür.
    - ``multilang_search(searcher, query, threshold=0.95)`` — verilen kaynak
                                         arama fonksiyonunu tüm alias'lar
                                         üzerinde dener, sonuçları benzersizleştirip
                                         skorlayarak döndürür.

threshold (varsayılan 0.95) ile "tam eşleşme" ve "olası eşleşme" ayrımı yapılır.
Olası eşleşme varsa UI buna göre kullanıcıya popup açabilir.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# rapidfuzz varsa öncelikli — çok daha hızlı + doğru
try:
    from rapidfuzz import fuzz as _rf_fuzz  # type: ignore
    _HAS_RF = True
except ImportError:
    _HAS_RF = False


_CACHE_DIR = Path.home() / ".turkanime" / "title_cache"
_CACHE_TTL = 7 * 24 * 3600  # 1 hafta


# ─────────────────────────────────────────────────────────────────────────────
# Skor
# ─────────────────────────────────────────────────────────────────────────────
def _normalize(s: str) -> str:
    """Lower-case, aksan kaldır, sembolleri boşluğa çevir, çoklu boşluğu tek yap."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def fuzzy_score(a: str, b: str) -> float:
    """İki başlık arasında 0..1 benzerlik skoru. Karakter+token bazlı.

    rapidfuzz varsa partial_ratio + token_set_ratio max'ı, yoksa SequenceMatcher.
    """
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if _HAS_RF:
        s1 = _rf_fuzz.token_set_ratio(na, nb) / 100.0
        s2 = _rf_fuzz.partial_ratio(na, nb) / 100.0
        s3 = _rf_fuzz.ratio(na, nb) / 100.0
        return max(s1, s2, s3)
    # Yedek: stdlib SequenceMatcher
    return SequenceMatcher(None, na, nb).ratio()


def siralama_skoru(query: str, candidate: str) -> float:
    """Arama sonuçlarını sıralamak için skor. `fuzzy_score`'dan FARKLI.

    `fuzzy_score` "bu ikisi aynı seri olabilir mi?" sorusunu yanıtlıyor ve bu
    yüzden `partial_ratio`/`token_set_ratio` kullanıyor — sorgu adayın *içinde*
    geçiyorsa 1.0 veriyor. Sıralama için bu işe yaramaz: "one piece" sorgusunda
    "One Piece", "Koisuru One Piece" ve "Noroi no One Piece" hepsi 1.00 alır,
    sıra kaynağın döndürdüğü rastgele düzende kalır.

    Ölçüm: 8 kaynaktan 3'ü (AnimeciX, TürkAnime, Tranimaci) "one piece" için
    ilk sıraya yanlış kaydı koyuyordu — TürkAnime'de gerçek One Piece 4.
    sıradaydı. Bölüm çekme yolu ilk sonucu aldığı için bu doğrudan "yanlış
    animenin bölümleri" demek.

    Buradaki skor tam dizgi benzerliğine bakıyor (fazladan kelimeyi cezalandırır)
    ve seri adıyla *başlayan* adaylara bonus veriyor:

        One Piece                 1.00   (birebir)
        One Piece Film: Z         0.97   (devam/film — aynı seri)
        ONE PIECE (Live Action)   0.85
        Koisuru One Piece         0.69   (farklı seri, adı içinde geçiyor)
    """
    a, b = _normalize(query), _normalize(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if _HAS_RF:
        temel = _rf_fuzz.ratio(a, b) / 100.0
    else:
        temel = SequenceMatcher(None, a, b).ratio()
    if b.startswith(a + " "):
        temel += 0.25          # "One Piece Film: Z" — seri doğru
    elif f" {a} " in f" {b} ":
        temel += 0.05          # "Koisuru One Piece" — yalnızca içinde geçiyor
    return min(temel, 0.99)    # birebir eşleşme her zaman tepede kalsın


def score_match(query: str, candidate: str, aliases: Optional[List[str]] = None) -> float:
    """Sorgu + alias listesi içinde aday başlığa en yakın eşleşmenin skorunu döndür."""
    best = fuzzy_score(query, candidate)
    if aliases:
        for alt in aliases:
            sc = fuzzy_score(alt, candidate)
            if sc > best:
                best = sc
            if best >= 1.0:
                break
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Multi-language alias getirme (AniList + Jikan)
# ─────────────────────────────────────────────────────────────────────────────
def _cache_path(query: str) -> Path:
    safe = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_") or "q"
    return _CACHE_DIR / f"{safe}.json"


def _load_cache(query: str) -> Optional[Dict]:
    p = _cache_path(query)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > _CACHE_TTL:
            return None
        return data
    except Exception:
        return None


def _save_cache(query: str, payload: Dict):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _cache_path(query)
        p.write_text(json.dumps({"ts": time.time(), **payload},
                                ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _fetch_anilist_titles(query: str) -> List[str]:
    """AniList GraphQL'den romaji+english+native+synonyms al."""
    try:
        import requests
    except ImportError:
        return []
    q = """
    query ($search: String) {
      Page(perPage: 5) {
        media(type: ANIME, search: $search) {
          title { romaji english native }
          synonyms
        }
      }
    }
    """
    try:
        r = requests.post("https://graphql.anilist.co",
                          json={"query": q, "variables": {"search": query}},
                          timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {}).get("Page", {}).get("media", []) or []
        titles: List[str] = []
        for m in data:
            t = m.get("title") or {}
            for k in ("romaji", "english", "native"):
                v = t.get(k)
                if v:
                    titles.append(v)
            for syn in m.get("synonyms") or []:
                if syn:
                    titles.append(syn)
        return titles
    except Exception:
        return []


def _fetch_jikan_titles(query: str) -> List[str]:
    """Jikan v4 üzerinden titles[] (Synonym/Japanese/English vb.) çek."""
    try:
        import requests
    except ImportError:
        return []
    try:
        r = requests.get("https://api.jikan.moe/v4/anime",
                         params={"q": query, "limit": 5}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data") or []
        titles: List[str] = []
        for m in data:
            for tt in m.get("titles") or []:
                v = tt.get("title")
                if v:
                    titles.append(v)
            # En azından ana title da
            if m.get("title"):
                titles.append(m["title"])
            if m.get("title_english"):
                titles.append(m["title_english"])
            if m.get("title_japanese"):
                titles.append(m["title_japanese"])
        return titles
    except Exception:
        return []


def get_title_aliases(query: str, use_cache: bool = True) -> List[str]:
    """Sorgu için tüm dillerdeki anime title varyantlarını döndür.

    Sırasıyla dener: cache → AniList → Jikan. İkisinden de gelirse birleştirir.
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    if use_cache:
        cached = _load_cache(q)
        if cached and "aliases" in cached:
            return cached["aliases"]

    seen = set()
    out: List[str] = []
    # Sorgunun kendisi her zaman ilk alias
    seen.add(_normalize(q))
    out.append(q)
    for fetcher in (_fetch_anilist_titles, _fetch_jikan_titles):
        try:
            for t in fetcher(q) or []:
                n = _normalize(t)
                if n and n not in seen:
                    seen.add(n)
                    out.append(t)
        except Exception:
            continue
    _save_cache(q, {"aliases": out})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Multi-language search across adapters
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class MatchResult:
    """Skorlu arama sonucu — kullanıcıya gösterilecek aday.

    ``slug``        — kaynak adapter'ın iç tanımlayıcısı (genelde slug veya id)
    ``title``       — kaynaktan dönen başlık
    ``score``       — orijinal sorguya (+ alias'lara) karşı en iyi skor 0..1
    ``matched_via`` — hangi alias eşleşmeyi sağladı
    """
    slug: str
    title: str
    score: float
    matched_via: str = ""


@dataclass
class SearchResponse:
    """multilang_search çıktısı.

    ``exact``      — score >= threshold olan sonuçlar
    ``possible``   — score < threshold ama >= floor olan adaylar (popup için)
    ``aliases``    — kullanılan title varyantları
    """
    exact: List[MatchResult] = field(default_factory=list)
    possible: List[MatchResult] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)

    @property
    def has_exact(self) -> bool:
        return bool(self.exact)

    @property
    def has_possible(self) -> bool:
        return bool(self.possible)


SearchFn = Callable[[str], Iterable[Tuple[str, str]]]


def multilang_search(
    searcher: SearchFn,
    query: str,
    threshold: float = 0.95,
    possible_floor: float = 0.55,
    max_aliases: int = 6,
    aliases: Optional[List[str]] = None,
) -> SearchResponse:
    """Bir kaynak arama fonksiyonunu tüm dillerde dener.

    Args:
        searcher: kaynak adapter'ın ``search(query) -> [(slug, title), ...]`` fonksiyonu.
        query: kullanıcının yazdığı orijinal sorgu.
        threshold: ``exact`` sayılması için minimum skor (varsayılan 0.95).
        possible_floor: ``possible`` listesine girmek için minimum skor.
        max_aliases: kaç farklı dil/varyant denenecek (rate-limit'i koru).
        aliases: hazır alias listesi (verilirse harici API çağrısı atlanır).

    Returns:
        :class:`SearchResponse` — exact + possible bölünmüş, skor sıralı.
    """
    if not query or not query.strip():
        return SearchResponse()

    aliases_list = aliases if aliases is not None else get_title_aliases(query)
    tried_aliases = aliases_list[:max_aliases]

    seen: Dict[str, MatchResult] = {}

    for alias in tried_aliases:
        try:
            results = list(searcher(alias)) or []
        except Exception:
            continue
        for slug, title in results:
            # Her aday için en iyi skoru tüm alias'lara karşı hesapla
            best_score = 0.0
            best_alias = ""
            for a in tried_aliases:
                sc = fuzzy_score(a, title)
                if sc > best_score:
                    best_score = sc
                    best_alias = a
                if best_score >= 1.0:
                    break
            mr = MatchResult(slug=slug, title=title, score=best_score, matched_via=best_alias)
            prev = seen.get(slug)
            if prev is None or mr.score > prev.score:
                seen[slug] = mr

    all_results = sorted(seen.values(), key=lambda r: r.score, reverse=True)
    exact = [r for r in all_results if r.score >= threshold]
    possible = [r for r in all_results if possible_floor <= r.score < threshold]
    return SearchResponse(exact=exact, possible=possible, aliases=tried_aliases)


__all__ = [
    "MatchResult",
    "SearchResponse",
    "fuzzy_score",
    "score_match",
    "get_title_aliases",
    "multilang_search",
]
