"""
Jikan API Client for TurkAnime GUI
Provides seasonal and trending anime data from MyAnimeList via Jikan API.
"""

import requests
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import json
import os
import random
import time


@dataclass
class JikanAnime:
    """Anime data from Jikan API."""
    mal_id: int
    title: str
    title_english: Optional[str]
    title_japanese: Optional[str]
    images: Dict[str, Any]
    synopsis: Optional[str]
    episodes: Optional[int]
    duration: Optional[str]
    score: Optional[float]
    scored_by: Optional[int]
    rank: Optional[int]
    popularity: Optional[int]
    members: Optional[int]
    favorites: Optional[int]
    status: Optional[str]
    rating: Optional[str]
    source: Optional[str]
    season: Optional[str]
    year: Optional[int]
    studios: List[Dict]
    genres: List[Dict]
    themes: List[Dict]
    demographics: List[Dict]
    aired: Optional[Dict]
    broadcast: Optional[Dict]
    trailer: Optional[Dict]


class JikanCache:
    """
    ETag-based cache for Jikan API responses.
    
    Uses HTTP ETag/If-None-Match headers for cache validation.
    Jikan API caches data for 24 hours on their servers.
    Local cache respects Expires header when available.
    """
    
    DEFAULT_CACHE_DURATION = 86400  # 24 hours (matches Jikan server cache)
    LOCAL_CACHE_DURATION = 600  # 10 minutes for local freshness check
    
    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            # ~/.turkanime klasörü
            cache_dir = os.path.join(os.path.expanduser("~"), ".turkanime")
        
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def _get_cache_path(self, key: str) -> str:
        """Get cache file path for a key."""
        # Sanitize key for filename
        safe_key = "".join(c if c.isalnum() or c in '-_' else '_' for c in key)
        return os.path.join(self.cache_dir, f"cache_{safe_key}.json")
    
    def get(self, key: str) -> tuple[Optional[Any], Optional[str], bool]:
        """
        Get cached data with ETag.
        
        Returns:
            (data, etag, needs_revalidation)
            - data: Cached data or None
            - etag: ETag for If-None-Match header
            - needs_revalidation: True if cache should be revalidated with server
        """
        cache_path = self._get_cache_path(key)
        
        if not os.path.exists(cache_path):
            return None, None, True
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            
            data = cached.get('data')
            etag = cached.get('etag')
            timestamp = cached.get('timestamp')
            expires = cached.get('expires')
            
            # Check if we have valid data
            if data is None:
                return None, etag, True
            
            # Check Expires header first (if available)
            if expires:
                try:
                    expires_time = datetime.fromisoformat(expires)
                    if datetime.now() < expires_time:
                        # Not expired yet, use cached data directly
                        return data, etag, False
                except:
                    pass
            
            # Check local cache freshness (10 minutes for revalidation check)
            if timestamp:
                try:
                    cached_time = datetime.fromisoformat(timestamp)
                    age = (datetime.now() - cached_time).total_seconds()
                    
                    if age < self.LOCAL_CACHE_DURATION:
                        # Fresh enough, use without revalidation
                        return data, etag, False
                    else:
                        # Needs revalidation but we have data to potentially use
                        return data, etag, True
                except:
                    pass
            
            # Default: return data but suggest revalidation
            return data, etag, True
            
        except Exception as e:
            print(f"[JikanCache] Cache read error: {e}")
            return None, None, True
    
    def set(self, key: str, data: Any, etag: Optional[str] = None, expires: Optional[str] = None, last_modified: Optional[str] = None):
        """
        Save data to cache with metadata.
        
        Args:
            key: Cache key
            data: Response data to cache
            etag: ETag header value
            expires: Expires header value
            last_modified: Last-Modified header value
        """
        cache_path = self._get_cache_path(key)
        
        try:
            # Parse expires if provided
            expires_iso = None
            if expires:
                try:
                    # HTTP date format: "Thu, 01 Jan 2026 00:00:00 GMT"
                    from email.utils import parsedate_to_datetime
                    expires_dt = parsedate_to_datetime(expires)
                    expires_iso = expires_dt.isoformat()
                except:
                    pass
            
            cached = {
                'timestamp': datetime.now().isoformat(),
                'etag': etag,
                'expires': expires_iso,
                'last_modified': last_modified,
                'data': data
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cached, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[JikanCache] Cache write error: {e}")
    
    def update_validated(self, key: str):
        """Update timestamp when cache is validated (304 response)."""
        cache_path = self._get_cache_path(key)
        
        if not os.path.exists(cache_path):
            return
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            
            cached['timestamp'] = datetime.now().isoformat()
            
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cached, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[JikanCache] Cache update error: {e}")
    
    def clear(self):
        """Clear all cache files."""
        try:
            for f in os.listdir(self.cache_dir):
                if f.startswith('cache_') and f.endswith('.json'):
                    os.remove(os.path.join(self.cache_dir, f))
            print("[JikanCache] Cache cleared")
        except Exception as e:
            print(f"[JikanCache] Cache clear error: {e}")
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        stats = {'files': 0, 'total_size': 0, 'oldest': None, 'newest': None}
        
        try:
            for f in os.listdir(self.cache_dir):
                if f.startswith('cache_') and f.endswith('.json'):
                    path = os.path.join(self.cache_dir, f)
                    stats['files'] += 1
                    stats['total_size'] += os.path.getsize(path)
                    
                    mtime = datetime.fromtimestamp(os.path.getmtime(path))
                    if stats['oldest'] is None or mtime < stats['oldest']:
                        stats['oldest'] = mtime
                    if stats['newest'] is None or mtime > stats['newest']:
                        stats['newest'] = mtime
        except:
            pass
        
        return stats


class JikanClient:
    """Jikan API client for MyAnimeList data."""
    
    BASE_URL = "https://api.jikan.moe/v4"
    
    # Rate limiting: Jikan has 3 requests/second limit
    RATE_LIMIT_DELAY = 0.4  # 400ms between requests

    # Geçici hatalarda yeniden deneme.
    # Neden: Jikan zaman zaman 502/503/504 döndürüyor ve tek denemede pes
    # edilince keşif sayfaları bomboş açılıyordu. Toplam bekleme 3 denemede
    # ~3sn'yi geçmez, yani kullanıcı "yükleniyor"da takılı kalmaz.
    RETRY_ATTEMPTS = 3          # toplam deneme sayısı (ilk istek dahil)
    RETRY_BASE_DELAY = 1.0      # 1sn, 2sn, 4sn... şeklinde üstel
    RETRY_MAX_DELAY = 30.0      # sunucunun verdiği Retry-After için tavan

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'TurkAnime-GUI/1.0',
            'Accept': 'application/json'
        })
        self.cache = JikanCache()
        self._last_request_time = 0
    
    def _rate_limit(self):
        """Ensure rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()
    
    def _retry_delay(self, attempt: int, response: Optional[Any] = None) -> float:
        """`attempt` (0'dan başlar) için beklenecek süre.

        Üstel geri çekilme + küçük jitter. Jitter'ın nedeni: aynı anda açılan
        birkaç istek (ör. üç keşif sayfası) aynı ritimde yeniden denerse
        sunucuya senkron çarpar ve 429/5xx tekrar eder.

        Sunucu `Retry-After` verdiyse ona uyulur; tavan uygulanır, aksi hâlde
        kötü niyetli/bozuk bir başlık arayüzü dakikalarca dondurabilir.
        """
        if response is not None:
            try:
                retry_after = response.headers.get('Retry-After')
            except Exception:
                retry_after = None
            if retry_after:
                try:
                    return min(float(retry_after), self.RETRY_MAX_DELAY)
                except (TypeError, ValueError):
                    pass
        return self.RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.25)

    def _make_request(self, endpoint: str, params: Optional[Dict] = None, use_cache: bool = True) -> Optional[Dict]:
        """
        Make API request with ETag-based caching, rate limiting and retries.

        Uses HTTP ETag/If-None-Match for cache validation:
        - Sends If-None-Match header with cached ETag
        - On 304 Not Modified: returns cached data without re-downloading
        - On 200 OK: caches new data with new ETag

        Retry policy: yalnızca geçici hatalar tekrar denenir — 5xx, 429 ve
        bağlantı/timeout hataları. 4xx (429 hariç) kalıcı istemci hatasıdır,
        tekrar denemek yalnızca gecikme üretir. Tüm denemeler tükenirse eski
        davranış korunur: varsa önbellekteki veri döndürülür.
        """
        # Create cache key
        cache_key = f"{endpoint}_{json.dumps(params or {}, sort_keys=True)}"

        # Check cache first
        cached_data = None
        cached_etag = None
        needs_revalidation = True

        if use_cache:
            cached_data, cached_etag, needs_revalidation = self.cache.get(cache_key)

            # If cache is fresh (within 10 min), use it directly
            if cached_data is not None and not needs_revalidation:
                return cached_data

        url = f"{self.BASE_URL}{endpoint}"

        # Prepare headers with ETag for conditional request
        headers = {}
        if cached_etag:
            headers['If-None-Match'] = cached_etag

        last_error: Any = None

        for attempt in range(self.RETRY_ATTEMPTS):
            # Rate limit before every attempt (yeniden denemeler de sayılır)
            self._rate_limit()

            try:
                response = self.session.get(url, params=params, headers=headers, timeout=15)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                # Ağ katmanı hatası: neredeyse her zaman geçici, yeniden dene.
                last_error = e
                if attempt + 1 < self.RETRY_ATTEMPTS:
                    print(f"[Jikan] Bağlantı hatası ({e}), yeniden deneniyor…")
                    time.sleep(self._retry_delay(attempt))
                    continue
                break
            except Exception as e:
                # Beklenmeyen istemci hatası: tekrar denemek anlamsız.
                last_error = e
                break

            status = getattr(response, 'status_code', None)

            # Handle 304 Not Modified - cache is still valid
            if status == 304 and cached_data is not None:
                # Update cache timestamp to mark as recently validated
                self.cache.update_validated(cache_key)
                return cached_data

            if status == 429 or (isinstance(status, int) and 500 <= status < 600):
                # 429: hız sınırı, 5xx: sunucu tarafı geçici arıza.
                last_error = f"HTTP {status}"
                if attempt + 1 < self.RETRY_ATTEMPTS:
                    delay = self._retry_delay(attempt, response)
                    print(f"[Jikan] HTTP {status}, {delay:.1f}sn sonra yeniden deneniyor…")
                    time.sleep(delay)
                    continue
                break

            try:
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                # 4xx (429 hariç) ya da bozuk gövde: kalıcı hata, döngüden çık.
                last_error = e
                break

            # Cache the response with headers
            if use_cache:
                etag = response.headers.get('ETag')
                expires = response.headers.get('Expires')
                last_modified = response.headers.get('Last-Modified')
                self.cache.set(cache_key, data, etag=etag, expires=expires, last_modified=last_modified)

            return data

        print(f"[Jikan] İstek başarısız ({endpoint}): {last_error}")
        # Return cached data as fallback on error
        if cached_data is not None:
            print("[Jikan] Using cached data as fallback")
            return cached_data
        return None

    def get_current_season(self) -> List[JikanAnime]:
        """Get anime from current season."""
        data = self._make_request("/seasons/now", {"limit": 25})
        
        if data and 'data' in data:
            return [self._parse_anime(item) for item in data['data']]
        return []
    
    def get_seasonal_anime(self, year: int, season: str, page: int = 1) -> List[JikanAnime]:
        """
        Get anime from a specific season.
        
        Args:
            year: Year (e.g., 2026)
            season: One of "winter", "spring", "summer", "fall"
            page: Page number
        """
        data = self._make_request(f"/seasons/{year}/{season}", {
            "page": page,
            "limit": 25
        })
        
        if data and 'data' in data:
            return [self._parse_anime(item) for item in data['data']]
        return []
    
    def get_top_airing(self, page: int = 1, limit: int = 25) -> List[JikanAnime]:
        """Get top airing anime (for trending)."""
        data = self._make_request("/top/anime", {
            "filter": "airing",
            "type": "tv",
            "page": page,
            "limit": limit
        })
        
        if data and 'data' in data:
            return [self._parse_anime(item) for item in data['data']]
        return []
    
    def get_top_anime(self, filter_type: str = "bypopularity", page: int = 1, limit: int = 25) -> List[JikanAnime]:
        """
        Get top anime by filter.
        
        Args:
            filter_type: "airing", "upcoming", "bypopularity", "favorite"
        """
        data = self._make_request("/top/anime", {
            "filter": filter_type,
            "type": "tv",
            "page": page,
            "limit": limit
        })
        
        if data and 'data' in data:
            return [self._parse_anime(item) for item in data['data']]
        return []
    
    def get_anime_by_id(self, mal_id: int) -> Optional[JikanAnime]:
        """Get detailed anime info by MAL ID."""
        data = self._make_request(f"/anime/{mal_id}/full")
        
        if data and 'data' in data:
            return self._parse_anime(data['data'])
        return None
    
    def search_anime(self, query: str, limit: int = 10) -> List[JikanAnime]:
        """Search for anime by title."""
        data = self._make_request("/anime", {
            "q": query,
            "limit": limit,
            "order_by": "score",
            "sort": "desc"
        })
        
        if data and 'data' in data:
            return [self._parse_anime(item) for item in data['data']]
        return []
    
    def _parse_anime(self, data: Dict) -> JikanAnime:
        """Parse anime data from API response."""
        return JikanAnime(
            mal_id=data.get('mal_id', 0),
            title=data.get('title', 'Unknown'),
            title_english=data.get('title_english'),
            title_japanese=data.get('title_japanese'),
            images=data.get('images', {}),
            synopsis=data.get('synopsis'),
            episodes=data.get('episodes'),
            duration=data.get('duration'),
            score=data.get('score'),
            scored_by=data.get('scored_by'),
            rank=data.get('rank'),
            popularity=data.get('popularity'),
            members=data.get('members'),
            favorites=data.get('favorites'),
            status=data.get('status'),
            rating=data.get('rating'),
            source=data.get('source'),
            season=data.get('season'),
            year=data.get('year'),
            studios=data.get('studios', []),
            genres=data.get('genres', []),
            themes=data.get('themes', []),
            demographics=data.get('demographics', []),
            aired=data.get('aired'),
            broadcast=data.get('broadcast'),
            trailer=data.get('trailer')
        )
    
    def to_dict(self, anime: JikanAnime) -> Dict:
        """Convert JikanAnime to dictionary compatible with existing UI."""
        # Get best image URL
        cover_url = None
        if anime.images:
            jpg = anime.images.get('jpg', {})
            webp = anime.images.get('webp', {})
            cover_url = (
                jpg.get('large_image_url') or 
                webp.get('large_image_url') or
                jpg.get('image_url') or
                webp.get('image_url')
            )
        
        # Extract studio names
        studios = [s.get('name', '') for s in anime.studios if s.get('name')]
        
        # Extract genre names
        genres = [g.get('name', '') for g in anime.genres if g.get('name')]
        
        # Convert score to 0-100 format (MAL uses 0-10)
        average_score = int(anime.score * 10) if anime.score else None
        
        return {
            'mal_id': anime.mal_id,
            'id': anime.mal_id,  # Compatibility
            'title': {
                'romaji': anime.title,
                'english': anime.title_english,
                'native': anime.title_japanese
            },
            'coverImage': {
                'large': cover_url,
                'medium': cover_url
            },
            'studios': studios,
            'genres': genres,
            'description': anime.synopsis,
            'episodes': anime.episodes,
            'duration': self._parse_duration(anime.duration),
            'averageScore': average_score,
            'popularity': anime.popularity,
            'rank': anime.rank,
            'members': anime.members,
            'favorites': anime.favorites,
            'status': self._map_status(anime.status),
            'source': anime.source,
            'season': anime.season.upper() if anime.season else None,
            'seasonYear': anime.year,
            'rating': anime.rating,
            'aired': anime.aired,
            'trailer': anime.trailer
        }
    
    def _parse_duration(self, duration_str: Optional[str]) -> Optional[int]:
        """Parse duration string to minutes."""
        if not duration_str:
            return None
        
        import re
        # "24 min per ep" -> 24
        match = re.search(r'(\d+)\s*min', duration_str)
        if match:
            return int(match.group(1))
        return None
    
    def _map_status(self, status: Optional[str]) -> str:
        """Map MAL status to AniList-compatible status."""
        status_map = {
            'Currently Airing': 'RELEASING',
            'Finished Airing': 'FINISHED',
            'Not yet aired': 'NOT_YET_RELEASED'
        }
        return status_map.get(status or '', 'RELEASING')


# Global client instance
jikan_client = JikanClient()


def get_seasonal_anime_list(year: Optional[int] = None, season: Optional[str] = None) -> List[Dict]:
    """
    Get seasonal anime list in format compatible with existing UI.
    
    Returns list of dicts similar to AniList API response format.
    """
    if year is None or season is None:
        # Current season
        anime_list = jikan_client.get_current_season()
    else:
        anime_list = jikan_client.get_seasonal_anime(year, season)
    
    return [jikan_client.to_dict(anime) for anime in anime_list]


def get_trending_anime_list(limit: int = 25) -> List[Dict]:
    """
    Get trending (top airing) anime list.
    
    Returns list in format compatible with existing UI.
    """
    anime_list = jikan_client.get_top_airing(limit=limit)
    return [jikan_client.to_dict(anime) for anime in anime_list]


def search_anime(query: str, limit: int = 10) -> List[Dict]:
    """Search anime by title."""
    anime_list = jikan_client.search_anime(query, limit)
    return [jikan_client.to_dict(anime) for anime in anime_list]


def get_anime_details(mal_id: int) -> Optional[Dict]:
    """Get detailed anime info by MAL ID."""
    anime = jikan_client.get_anime_by_id(mal_id)
    if anime:
        return jikan_client.to_dict(anime)
    return None


# Check if module is available
JIKAN_AVAILABLE = True
