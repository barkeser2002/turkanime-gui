"""
Title Matching API Client for TurkAnime GUI
Provides anime title matching across Japanese, Romaji, and English titles.
"""

import requests
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
import json
import os
import appdirs


@dataclass
class AnimeMatch:
    """Anime match data from API."""
    id: int
    source: str  # 'turkanime', 'animecix', 'anizle'
    anime_id: str
    anime_title: str
    created_at: Optional[str] = None
    
    # Additional matching fields
    title_romaji: Optional[str] = None
    title_english: Optional[str] = None
    title_native: Optional[str] = None  # Japanese title
    anilist_id: Optional[int] = None
    mal_id: Optional[int] = None


class TitleMatchingClient:
    """Client for anime title matching API."""
    
    # API Base URL - can be overridden
    API_BASE_URL = "https://turkanimeapi.bariskeser.com"
    
    # Local cache file
    CACHE_FILE = "anime_matches_cache.json"
    
    def __init__(self, api_url: Optional[str] = None):
        if api_url:
            self.API_BASE_URL = api_url
        
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        
        # Local cache for offline access
        self._cache: Dict[str, Any] = {}
        self._load_cache()
    
    def _get_cache_path(self) -> str:
        """Get cache file path."""
        data_dir = appdirs.user_data_dir("TurkAnime-GUI")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, self.CACHE_FILE)
    
    def _load_cache(self):
        """Load cache from disk."""
        try:
            cache_path = self._get_cache_path()
            if os.path.exists(cache_path):
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._cache = data
        except Exception as e:
            print(f"[TitleMatching] Cache yükleme hatası: {e}")
            self._cache = {}
    
    def _save_cache(self):
        """Save cache to disk."""
        try:
            cache_path = self._get_cache_path()
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TitleMatching] Cache kaydetme hatası: {e}")
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Any]:
        """Make API request with error handling."""
        url = f"{self.API_BASE_URL}{endpoint}"
        
        try:
            response = self.session.request(method, url, timeout=10, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            print(f"[TitleMatching] İstek zaman aşımı: {endpoint}")
            return None
        except requests.exceptions.ConnectionError:
            print(f"[TitleMatching] Bağlantı hatası: {endpoint}")
            return None
        except Exception as e:
            print(f"[TitleMatching] API hatası: {e}")
            return None
    
    def get_all_matches(self, limit: int = 100) -> List[AnimeMatch]:
        """Get all anime matches from API."""
        result = self._make_request('GET', f'/anime-matches?limit={limit}')
        
        if result:
            matches = [AnimeMatch(**item) for item in result]
            # Update cache
            self._cache['all'] = [self._match_to_dict(m) for m in matches]
            self._save_cache()
            return matches
        
        # Return from cache if API fails
        if 'all' in self._cache:
            return [AnimeMatch(**item) for item in self._cache['all']]
        
        return []
    
    def search_matches(self, query: str) -> List[AnimeMatch]:
        """
        Search anime matches by title.
        
        Searches across:
        - anime_title (main title in source)
        - Can match partial Japanese, Romaji, or English titles
        """
        result = self._make_request('GET', f'/anime-matches/search', params={'q': query})
        
        if result:
            return [AnimeMatch(**item) for item in result]
        
        # Fallback to local cache search
        return self._search_cache(query)
    
    def _search_cache(self, query: str) -> List[AnimeMatch]:
        """Search in local cache."""
        query_lower = query.lower()
        matches = []
        
        if 'all' in self._cache:
            for item in self._cache['all']:
                title = item.get('anime_title', '').lower()
                if query_lower in title:
                    matches.append(AnimeMatch(**item))
        
        return matches
    
    def save_match(self, source: str, anime_id: str, anime_title: str,
                   title_romaji: Optional[str] = None, title_english: Optional[str] = None,
                   title_native: Optional[str] = None, anilist_id: Optional[int] = None,
                   mal_id: Optional[int] = None) -> bool:
        """
        Save anime match to API.
        
        Args:
            source: Source identifier ('turkanime', 'animecix', 'anizle')
            anime_id: ID in the source system
            anime_title: Title as it appears in source
            title_romaji: Romanized Japanese title
            title_english: English title
            title_native: Native Japanese title
            anilist_id: AniList ID for cross-reference
            mal_id: MyAnimeList ID for cross-reference
        """
        data = {
            'source': source,
            'anime_id': anime_id,
            'anime_title': anime_title
        }
        
        # Add optional fields to title for better matching
        if title_romaji or title_english or title_native:
            # Store additional titles in a JSON format within anime_title
            titles_data = {
                'main': anime_title,
                'romaji': title_romaji,
                'english': title_english,
                'native': title_native,
                'anilist_id': anilist_id,
                'mal_id': mal_id
            }
            # Keep main title but append JSON metadata
            data['anime_title'] = f"{anime_title}||{json.dumps(titles_data, ensure_ascii=False)}"
        
        result = self._make_request('POST', '/anime-matches', json=data)
        
        if result and result.get('success'):
            # Update local cache
            self._invalidate_cache()
            return True
        
        return False
    
    def _invalidate_cache(self):
        """Invalidate cache to force refresh on next request."""
        if 'all' in self._cache:
            del self._cache['all']
        self._save_cache()
    
    def _match_to_dict(self, match: AnimeMatch) -> Dict:
        """Convert AnimeMatch to dict for caching."""
        return {
            'id': match.id,
            'source': match.source,
            'anime_id': match.anime_id,
            'anime_title': match.anime_title,
            'created_at': match.created_at
        }
    
    def find_match_for_anime(self, anime_title: str, source: Optional[str] = None) -> Optional[AnimeMatch]:
        """
        Find matching anime entry across all sources.
        
        Args:
            anime_title: Title to search for
            source: Optional source to filter by ('turkanime', 'animecix', 'anizle')
        
        Returns:
            Best matching AnimeMatch or None
        """
        matches = self.search_matches(anime_title)
        
        if source:
            matches = [m for m in matches if m.source == source]
        
        if matches:
            # Return best match (first result which should be most recent)
            return matches[0]
        
        return None
    
    def find_cross_source_matches(self, anime_title: str) -> Dict[str, AnimeMatch]:
        """
        Find matching anime entries across all sources.
        
        Returns dict with source as key and match as value.
        """
        matches = self.search_matches(anime_title)
        
        result = {}
        for match in matches:
            if match.source not in result:
                result[match.source] = match
        
        return result
    
    def get_episode_matches(self, episode_title: str, anime_title: Optional[str] = None) -> Dict[str, Any]:
        """
        Find matching episodes across sources.
        
        This is useful when merging episodes from different sources.
        """
        # First find anime matches
        if anime_title:
            anime_matches = self.find_cross_source_matches(anime_title)
        else:
            anime_matches = {}
        
        # Extract episode number from title
        import re
        ep_match = re.search(r'(\d+)\.?\s*[Bb]ölüm|[Ee]pisode\s*(\d+)|[Ss]\d+[Ee](\d+)', episode_title)
        episode_num = None
        if ep_match:
            episode_num = int(next(g for g in ep_match.groups() if g))
        
        return {
            'anime_matches': {source: {'anime_id': m.anime_id, 'title': m.anime_title} 
                             for source, m in anime_matches.items()},
            'episode_number': episode_num,
            'original_title': episode_title
        }


# User Episode Tracking Client
class UserTrackingClient:
    """Client for user episode tracking API."""
    
    API_BASE_URL = "https://turkanimeapi.bariskeser.com"
    
    def __init__(self, user_id: str, api_url: Optional[str] = None):
        self.user_id = user_id
        if api_url:
            self.API_BASE_URL = api_url
        
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        
        # Local cache
        self._episode_status: Dict[str, Dict] = {}
        self._load_local_status()
    
    def _get_status_path(self) -> str:
        """Get local status file path."""
        data_dir = appdirs.user_data_dir("TurkAnime-GUI", "KebabLord")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, f"episode_status_{self.user_id}.json")
    
    def _load_local_status(self):
        """Load status from local file."""
        try:
            status_path = self._get_status_path()
            if os.path.exists(status_path):
                with open(status_path, 'r', encoding='utf-8') as f:
                    self._episode_status = json.load(f)
        except Exception as e:
            print(f"[UserTracking] Local status yükleme hatası: {e}")
            self._episode_status = {}
    
    def _save_local_status(self):
        """Save status to local file."""
        try:
            status_path = self._get_status_path()
            with open(status_path, 'w', encoding='utf-8') as f:
                json.dump(self._episode_status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[UserTracking] Local status kaydetme hatası: {e}")
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Any]:
        """Make API request."""
        url = f"{self.API_BASE_URL}{endpoint}"
        
        try:
            response = self.session.request(method, url, timeout=10, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"[UserTracking] API hatası: {e}")
            return None
    
    def get_episode_status(self, episode_id: str) -> Dict:
        """Get status for a specific episode."""
        if episode_id in self._episode_status:
            return self._episode_status[episode_id]
        return {'watched': False, 'downloaded': False}
    
    def get_all_status(self) -> Dict[str, Dict]:
        """Get all episode statuses for user."""
        # Try to sync from API
        result = self._make_request('GET', f'/user/{self.user_id}/episode-status')
        
        if result:
            self._episode_status = result
            self._save_local_status()
        
        return self._episode_status
    
    def set_watched(self, episode_id: str, watched: bool = True) -> bool:
        """Mark episode as watched/unwatched."""
        return self._update_status(episode_id, watched=watched)
    
    def set_downloaded(self, episode_id: str, downloaded: bool = True) -> bool:
        """Mark episode as downloaded."""
        return self._update_status(episode_id, downloaded=downloaded)
    
    def _update_status(self, episode_id: str, watched: Optional[bool] = None, downloaded: Optional[bool] = None) -> bool:
        """Update episode status."""
        # Get current status
        current = self._episode_status.get(episode_id, {'watched': False, 'downloaded': False})
        
        if watched is not None:
            current['watched'] = watched
        if downloaded is not None:
            current['downloaded'] = downloaded
        
        # Update local cache
        self._episode_status[episode_id] = current
        self._save_local_status()
        
        # Sync to API
        data = {
            'user_id': self.user_id,
            'episode_id': episode_id,
            'watched': current['watched'],
            'downloaded': current['downloaded']
        }
        
        result = self._make_request('POST', '/user/episode-status', json=data)
        return result is not None and result.get('success', False)
    
    def get_watch_progress(self, anime_id: str) -> Dict:
        """
        Get watch progress for an anime.
        
        Returns:
            Dict with 'watched_count', 'total_episodes', 'last_watched'
        """
        watched = 0
        last_watched = None
        
        for ep_id, status in self._episode_status.items():
            if anime_id in ep_id and status.get('watched'):
                watched += 1
                updated = status.get('updated_at')
                if updated and (last_watched is None or updated > last_watched):
                    last_watched = updated
        
        return {
            'watched_count': watched,
            'last_watched': last_watched
        }


# Global client instances
_title_matching_client: Optional[TitleMatchingClient] = None
_user_tracking_client: Optional[UserTrackingClient] = None


def get_title_matching_client() -> TitleMatchingClient:
    """Get global title matching client instance."""
    global _title_matching_client
    if _title_matching_client is None:
        _title_matching_client = TitleMatchingClient()
    return _title_matching_client


def get_user_tracking_client(user_id: str) -> UserTrackingClient:
    """Get user tracking client for specific user."""
    global _user_tracking_client
    if _user_tracking_client is None or _user_tracking_client.user_id != user_id:
        _user_tracking_client = UserTrackingClient(user_id)
    return _user_tracking_client


# Convenience functions
def search_anime_title(title: str) -> List[AnimeMatch]:
    """Search for anime by title across all sources."""
    client = get_title_matching_client()
    return client.search_matches(title)


def save_anime_title_match(source: str, anime_id: str, title: str, **kwargs) -> bool:
    """Save anime title match."""
    client = get_title_matching_client()
    return client.save_match(source, anime_id, title, **kwargs)


def find_anime_in_sources(title: str) -> Dict[str, AnimeMatch]:
    """Find anime across all sources by title."""
    client = get_title_matching_client()
    return client.find_cross_source_matches(title)
