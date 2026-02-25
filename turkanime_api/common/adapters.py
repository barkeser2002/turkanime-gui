"""
Anime source adapters for the UI components.
Provides unified interface for searching anime across different sources.
"""

from typing import List, Tuple, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..anilist_client import anilist_client
from ..objects import Anime
from ..sources.animecix import search_animecix
from ..sources.anizle import search_anizle
from ..sources.tranime import search_tranime


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
        """Search anime in local TurkAnime database.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of (slug, title) tuples
        """
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


class SearchEngine:
    """Unified search engine for all anime sources."""
    
    def __init__(self):
        self.adapters = {
            "AniList": AniListAdapter(),
            "TürkAnime": TurkAnimeAdapter(),
            "AnimeciX": AnimeciXAdapter(),
            "Anizle": AnizleAdapter(),
            "TRAnimeİzle": TRAnimeAdapter()
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
            for future in as_completed(futures, timeout=12):
                try:
                    source_name, source_results = future.result(timeout=8)
                    results[source_name] = source_results
                except Exception as exc:
                    source_name = futures[future]
                    print(f"{source_name} arama hatası (timeout/exception): {exc}")
                    results[source_name] = []
        
        return results
    
    def get_adapter(self, source_name: str):
        """Get adapter by source name."""
        return self.adapters.get(source_name)