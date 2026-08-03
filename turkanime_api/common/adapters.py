"""
Anime source adapters for the UI components.
Provides unified interface for searching anime across different sources.
"""

from typing import List, Tuple, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError

# Tüm kaynakların toplam bekleme süresi. Tarayıcı destekli kaynaklar (Tranimaci)
# ilk çağrıda yavaş olabildiği için 12 sn yetmiyordu.
OVERALL_SEARCH_TIMEOUT = 25
PER_SOURCE_TIMEOUT = 15
from ..anilist_client import anilist_client
from ..objects import Anime
from ..sources.animecix import search_animecix
from ..sources.anizle import search_anizle
from ..sources.tranime import search_tranime
from ..sources.animedepo import search_animedepo
from ..sources.openani import search_openani
from ..sources.tranimaci import search_tranimaci


class AniListAdapter:
    """Adapter for AniList anime search."""

    def __init__(self):
        self.client = anilist_client

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        """Search anime on AniList.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of (slug, title) tuples
        """
        try:
            results = self.client.search_anime(query, per_page=limit)
            return [(str(result.get('id', '')), result.get('title', {}).get('romaji', '')) for result in results]
        except Exception:
            return []

    def search_rich(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Kapak görseliyle birlikte arama (opsiyonel zengin sözleşme).

        `search_anime` (slug, title) döndürdüğü için görsel taşıyamıyor; bu
        metot yalnızca destekleyen adapterlerde bulunur ve `SearchEngine`
        tarafından varsa tercih edilir.
        """
        try:
            results = self.client.search_anime(query, per_page=limit) or []
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for r in results[:limit]:
            titles = r.get("title") or {}
            cover = r.get("coverImage") or {}
            out.append({
                "slug": str(r.get("id", "")),
                "title": titles.get("romaji") or titles.get("english") or "",
                "image": cover.get("medium") or cover.get("large"),
            })
        return out

    def get_anime_details(self, anime_id: str) -> Optional[Dict[str, Any]]:
        """Get anime details by ID.

        Args:
            anime_id: Anime ID as string

        Returns:
            Anime details dictionary or None if not found
        """
        try:
            anime_id_int = int(anime_id)
            return self.client.get_anime_by_id(anime_id_int)
        except (ValueError, Exception):
            return None


class TurkAnimeAdapter:
    """Adapter for TurkAnime local database search."""

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        """Search anime on TurkAnime.

        Sitenin "tüm anime listesi" ucu kaldırıldığı için `get_anime_listesi()`
        artık boş/eksik dönüyor (FutureWarning) ve arama hiç sonuç vermiyordu.
        Bu yüzden önce gerçek arama ucunu (`arama_yap`) kullanıyor, yalnızca o
        başarısız olursa eski liste-filtreleme yöntemine düşüyoruz.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of (slug, title) tuples
        """
        try:
            results = Anime.arama_yap(query) or []
            if results:
                return results[:limit]
        except Exception:
            pass

        # Yedek: eski (deprecated) tüm-liste + alt-dize filtresi
        try:
            all_list = Anime.get_anime_listesi()
            results = []
            query_lower = query.lower()

            for slug, name in all_list:
                if query_lower in (name or "").lower():
                    results.append((slug, name))
                    if len(results) >= limit:
                        break

            return results
        except Exception:
            return []


class AnimeciXAdapter:
    """Adapter for AnimeciX website search."""

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        """Search anime on AnimeciX.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of (slug, title) tuples
        """
        try:
            results = search_animecix(query)
            return results[:limit]
        except Exception:
            return []


class AnizleAdapter:
    """Adapter for Anizle website search."""

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        try:
            results = search_anizle(query, limit=limit)
            return results[:limit]
        except Exception:
            return []


class TRAnimeAdapter:
    """Adapter for TRAnimeİzle.io website search."""

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        """Search anime on TRAnimeİzle.io.
        
        Args:
            query: Search query  
            limit: Maximum number of results
            
        Returns:
            List of (slug, title) tuples
        """
        try:
            results = search_tranime(query, limit=limit)
            return results[:limit]
        except Exception:
            return []


class AnimeDepoAdapter:
    """Adapter for AnimeDepo (GitLab-hosted static archive, local fuzzy search)."""

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        try:
            results = search_animedepo(query, limit=limit)
            return results[:limit]
        except Exception:
            return []


class OpenAnimeAdapter:
    """Adapter for OpenAnime (openani.me) search."""

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        try:
            return (search_openani(query, limit=limit) or [])[:limit]
        except Exception:
            return []


class TranimaciAdapter:
    """Adapter for Tranimaci.com search."""

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        try:
            return (search_tranimaci(query, limit=limit) or [])[:limit]
        except Exception:
            return []


class SearchEngine:
    """Unified search engine for all anime sources.

    NOT: Buradaki anahtarlar `gui/qt/sources_bridge.py`'deki kaynak adlarıyla
    aynı olmalı; aksi hâlde bir kaynak aramada çıkar ama bölümleri açılamaz
    (ya da tersi — OpenAnime/Tranimaci uzun süre aramada hiç görünmüyordu).
    """

    def __init__(self):
        self.adapters = {
            "AniList": AniListAdapter(),
            "TürkAnime": TurkAnimeAdapter(),
            "AnimeciX": AnimeciXAdapter(),
            "Anizle": AnizleAdapter(),
            "TRAnimeİzle": TRAnimeAdapter(),
            "AnimeDepo": AnimeDepoAdapter(),
            "OpenAnime": OpenAnimeAdapter(),
            "Tranimaci": TranimaciAdapter(),
        }
    
    def search_all_sources(self, query: str, limit_per_source: int = 10) -> Dict[str, List[Tuple[str, str]]]:
        """Search anime across all sources in parallel.
        
        Args:
            query: Search query
            limit_per_source: Maximum results per source
            
        Returns:
            Dict mapping source names to list of (slug, title) tuples
        """
        results: Dict[str, List[Tuple[str, str]]] = {}

        def _search_single(source_name: str):
            adapter = self.adapters[source_name]
            try:
                return source_name, adapter.search_anime(query, limit=limit_per_source)
            except Exception as exc:
                print(f"{source_name} arama hatası: {exc}")
                return source_name, []

        with ThreadPoolExecutor(max_workers=len(self.adapters)) as executor:
            futures = {executor.submit(_search_single, name): name for name in self.adapters}
            # DİKKAT: `as_completed(..., timeout=)` süre dolunca KENDİSİ fırlatır
            # ve bu, aşağıdaki try/except'in DIŞINDADIR. Sarmalanmazsa tek bir
            # yavaş kaynak tüm aramayı çökertir (toplanan sonuçlar da kaybolur).
            try:
                for future in as_completed(futures, timeout=OVERALL_SEARCH_TIMEOUT):
                    try:
                        source_name, source_results = future.result(timeout=PER_SOURCE_TIMEOUT)
                        results[source_name] = source_results
                    except Exception as exc:
                        source_name = futures[future]
                        print(f"{source_name} arama hatası (timeout/exception): {exc}")
                        results[source_name] = []
            except FuturesTimeoutError:
                print("[Arama] Bazı kaynaklar zaman aşımına uğradı, mevcut sonuçlar döndürülüyor.")

        for name in self.adapters:          # yetişemeyenler boş
            results.setdefault(name, [])
        return results
    
    def get_adapter(self, source_name: str):
        """Get adapter by source name."""
        return self.adapters.get(source_name)

    def search_all_sources_rich(
        self, query: str, limit_per_source: int = 10
    ) -> Dict[str, List[Dict[str, Any]]]:
        """`search_all_sources` gibi, ama sonuçlar sözlük ve görsel taşıyabilir.

        Adapter `search_rich` sağlıyorsa o kullanılır; sağlamıyorsa
        `search_anime`'in (slug, title) çıktısı `image=None` ile sarılır.
        Böylece mevcut adapterlerin hiçbiri değişmek zorunda kalmaz.
        """
        rich: Dict[str, List[Dict[str, Any]]] = {}

        def _one(source_name: str):
            adapter = self.adapters[source_name]
            try:
                if hasattr(adapter, "search_rich"):
                    return source_name, adapter.search_rich(query, limit=limit_per_source)
                pairs = adapter.search_anime(query, limit=limit_per_source) or []
                return source_name, [
                    {"slug": s, "title": t, "image": None} for s, t in pairs
                ]
            except Exception as exc:
                print(f"{source_name} arama hatası: {exc}")
                return source_name, []

        with ThreadPoolExecutor(max_workers=len(self.adapters)) as executor:
            futures = {executor.submit(_one, n): n for n in self.adapters}
            # `as_completed` timeout'u kendisi fırlatır (bkz. search_all_sources).
            try:
                for future in as_completed(futures, timeout=OVERALL_SEARCH_TIMEOUT):
                    try:
                        name, res = future.result(timeout=PER_SOURCE_TIMEOUT)
                        rich[name] = res
                    except Exception as exc:
                        name = futures[future]
                        print(f"{name} arama hatası (timeout/exception): {exc}")
                        rich[name] = []
            except FuturesTimeoutError:
                print("[Arama] Bazı kaynaklar zaman aşımına uğradı, mevcut sonuçlar döndürülüyor.")

        for name in self.adapters:
            rich.setdefault(name, [])
        return rich