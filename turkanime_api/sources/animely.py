"""
Animely.net API Client
https://animely.net

API Endpoints:
- GET /api/animes -> Tüm anime listesi
- POST /api/searchAnime -> Bölüm listesi (payload: anime slug)
- Direkt video linkleri (backblaze_link, watch_link_1/2/3)
"""

import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

try:
    from curl_cffi import requests as curl_requests
    SESSION = curl_requests.Session(impersonate="chrome110")
except ImportError:
    import requests
    SESSION = requests.Session()

# ─────────────────────────────────────────────────────────────────────────────
# YAPILANDIRMA
# ─────────────────────────────────────────────────────────────────────────────
BASE_URL = "https://animely.net"
API_URL = f"{BASE_URL}/api"
CACHE_DIR = Path.home() / ".turkanime" / "animely_cache"
CACHE_DURATION = 30 * 60  # 30 dakika (saniye)
HTTP_TIMEOUT = 15

# API Endpoints
ENDPOINTS = {
    "anime_list": "/anime-list",      # GET - Tüm anime listesi
    "animes": "/animes",              # GET - Alternatif anime listesi
    "search_anime": "/searchAnime",   # POST - Bölüm listesi (payload: slug)
    "search_by_id": "/searchAnimeById",  # GET - ID ile anime ara
}

# ─────────────────────────────────────────────────────────────────────────────
# CACHE YÖNETİMİ
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_cache_dir():
    """Cache dizinini oluştur."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _get_cached_anime_list() -> Optional[list]:
    """Cache'den anime listesini al."""
    cache_file = CACHE_DIR / "anime_list.json"
    if not cache_file.exists():
        return None
    
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cache = json.load(f)
        
        if time.time() - cache.get("timestamp", 0) > CACHE_DURATION:
            return None
        
        return cache.get("data")
    except Exception:
        return None

def _save_anime_list_to_cache(data: list):
    """Anime listesini cache'e kaydet."""
    _ensure_cache_dir()
    cache_file = CACHE_DIR / "anime_list.json"
    
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "data": data}, f, ensure_ascii=False)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# VERİ SINIFLARI
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class AnimelyVideo:
    """Tek bir video stream."""
    url: str
    quality: str = "default"
    fansub: str = "Animely"
    
    def __repr__(self):
        return f"AnimelyVideo({self.quality}, {self.fansub})"

@dataclass
class AnimelyEpisode:
    """Tek bir bölüm."""
    id: int
    episode_number: int
    name: str
    ep_type: str
    fansub: str
    _links: list = field(default_factory=list)
    
    @property
    def title(self) -> str:
        return f"{self.episode_number}. Bölüm"
    
    @property
    def url(self) -> str:
        """İlk geçerli linki döndür."""
        for link in self._links:
            if link and isinstance(link, str) and link.strip():
                return link.strip()
        return ""
    
    def get_streams(self) -> List[AnimelyVideo]:
        """Bölümün video linklerini döndür."""
        streams = []
        for i, link in enumerate(self._links):
            if link and isinstance(link, str) and link.strip():
                quality = "HD" if i == 0 else f"Link {i}"
                streams.append(AnimelyVideo(
                    url=link.strip(),
                    quality=quality,
                    fansub=self.fansub or "Animely"
                ))
        return streams
    
    def __repr__(self):
        return f"AnimelyEpisode({self.episode_number}, links={len(self._links)})"

@dataclass
class AnimelyAnime:
    """Tek bir anime."""
    slug: str
    name: str
    other_names: list = field(default_factory=list)
    poster: str = ""
    total_episodes: int = 0
    _raw: dict = field(default_factory=dict)
    _episodes: list = field(default_factory=list, repr=False)
    
    @property
    def title(self) -> str:
        return self.name
    
    @property
    def episodes(self) -> List[AnimelyEpisode]:
        """Bölüm listesini lazy-load et."""
        if not self._episodes:
            self._episodes = get_anime_episodes(self.slug)
        return self._episodes
    
    def __repr__(self):
        return f"AnimelyAnime({self.name}, {self.total_episodes} ep)"

# ─────────────────────────────────────────────────────────────────────────────
# API FONKSİYONLARI
# ─────────────────────────────────────────────────────────────────────────────
def get_anime_list(use_cache: bool = True) -> List[dict]:
    """
    Tüm anime listesini çek.
    
    Returns:
        list[dict]: Ham anime verileri
    """
    if use_cache:
        cached = _get_cached_anime_list()
        if cached:
            return cached
    
    # Önce anime-list endpoint'ini dene, başarısız olursa animes'i dene
    endpoints = [f"{API_URL}/anime-list", f"{API_URL}/animes"]
    
    for endpoint in endpoints:
        try:
            resp = SESSION.get(endpoint, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            
            if data:  # Veri varsa kaydet ve döndür
                if use_cache:
                    _save_anime_list_to_cache(data)
                return data
        except Exception:
            continue
    
    print("[Animely] Anime listesi alınamadı")
    return []

def search_anime(query: str) -> List[AnimelyAnime]:
    """
    Anime ara.
    
    Args:
        query: Arama sorgusu
        
    Returns:
        list[AnimelyAnime]: Eşleşen animeler
    """
    animes = get_anime_list()
    query_lower = query.lower().strip()
    
    results = []
    
    # 1. Tam eşleşme
    for anime in animes:
        name = anime.get("NAME", "").lower()
        other_names = [n.lower() for n in anime.get("OTHER_NAMES", [])]
        
        if name == query_lower or query_lower in other_names:
            results.append(anime)
    
    # 2. Kısmi eşleşme
    if not results:
        for anime in animes:
            name = anime.get("NAME", "").lower()
            other_names = [n.lower() for n in anime.get("OTHER_NAMES", [])]
            
            if query_lower in name or any(query_lower in n for n in other_names):
                results.append(anime)
    
    # 3. Kelime bazlı eşleşme
    if not results:
        words = query_lower.split()
        for anime in animes:
            all_names = [anime.get("NAME", "").lower()] + [n.lower() for n in anime.get("OTHER_NAMES", [])]
            
            if any(all(word in name for word in words) for name in all_names):
                results.append(anime)
    
    # AnimelyAnime objelerine dönüştür
    return [
        AnimelyAnime(
            slug=a.get("SLUG", ""),
            name=a.get("NAME", ""),
            other_names=a.get("OTHER_NAMES", []),
            poster=a.get("FIRST_IMAGE", ""),
            total_episodes=a.get("TOTAL_EPISODES", 0),
            _raw=a
        )
        for a in results
    ]

def search_animely(query: str, limit: int = 10) -> List[tuple]:
    """
    Anime ara ve (slug, title) tuple listesi döndür.
    Adapter uyumluluğu için.
    
    Args:
        query: Arama sorgusu
        limit: Maksimum sonuç sayısı
        
    Returns:
        list[tuple]: (slug, title) tuple listesi
    """
    results = search_anime(query)
    return [(a.slug, a.name) for a in results[:limit]]

def get_anime_episodes(anime_slug: str) -> List[AnimelyEpisode]:
    """
    Anime bölümlerini çek.
    
    Args:
        anime_slug: Anime slug'ı
        
    Returns:
        list[AnimelyEpisode]: Bölüm listesi
    """
    try:
        resp = SESSION.post(
            f"{API_URL}/searchAnime",
            json={"payload": anime_slug},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        
        episodes_data = data.get("episodes", [])
        episodes = []
        
        for ep in episodes_data:
            links = [
                ep.get("backblaze_link"),
                ep.get("watch_link_1"),
                ep.get("watch_link_2"),
                ep.get("watch_link_3")
            ]
            
            episodes.append(AnimelyEpisode(
                id=ep.get("id", 0),
                episode_number=ep.get("episode_number", 0),
                name=f"{ep.get('episode_number', 0)}. Bölüm",
                ep_type=ep.get("type", ""),
                fansub=ep.get("fansub", "Animely"),
                _links=links
            ))
        
        # Bölüm numarasına göre sırala
        episodes.sort(key=lambda x: x.episode_number)
        
        return episodes
    
    except Exception as e:
        print(f"[Animely] Bölümler alınamadı ({anime_slug}): {e}")
        return []

def get_episode_streams(episode: AnimelyEpisode) -> List[AnimelyVideo]:
    """
    Bölümün video linklerini al.
    
    Args:
        episode: AnimelyEpisode objesi
        
    Returns:
        list[AnimelyVideo]: Video linkleri
    """
    return episode.get_streams()

# ─────────────────────────────────────────────────────────────────────────────
# ADAPTER UYUMLULUĞU
# ─────────────────────────────────────────────────────────────────────────────
def get_anime_by_slug(slug: str) -> Optional[AnimelyAnime]:
    """
    Slug ile anime bul.
    
    Args:
        slug: Anime slug'ı
        
    Returns:
        AnimelyAnime veya None
    """
    animes = get_anime_list()
    
    for anime in animes:
        if anime.get("SLUG", "").lower() == slug.lower():
            return AnimelyAnime(
                slug=anime.get("SLUG", ""),
                name=anime.get("NAME", ""),
                other_names=anime.get("OTHER_NAMES", []),
                poster=anime.get("FIRST_IMAGE", ""),
                total_episodes=anime.get("TOTAL_EPISODES", 0),
                _raw=anime
            )
    
    return None


def get_anime_by_id(anime_id: int) -> Optional[AnimelyAnime]:
    """
    ID ile anime bul (API üzerinden).
    
    Args:
        anime_id: Anime ID'si
        
    Returns:
        AnimelyAnime veya None
    """
    try:
        resp = SESSION.get(f"{API_URL}/searchAnimeById/{anime_id}", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        
        if data:
            return AnimelyAnime(
                slug=data.get("slug", ""),
                name=data.get("name", data.get("NAME", "")),
                other_names=data.get("other_names", data.get("OTHER_NAMES", [])),
                poster=data.get("poster", data.get("FIRST_IMAGE", "")),
                total_episodes=data.get("total_episodes", data.get("TOTAL_EPISODES", 0)),
                _raw=data
            )
    except Exception as e:
        print(f"[Animely] Anime bulunamadı (ID: {anime_id}): {e}")
    
    return None


def get_anime_url(slug: str, episode_number: Optional[int] = None) -> str:
    """
    Anime veya bölüm URL'i oluştur.
    
    Args:
        slug: Anime slug'ı
        episode_number: Bölüm numarası (opsiyonel)
        
    Returns:
        str: Tam URL
    """
    if episode_number is not None:
        return f"{BASE_URL}/anime/{slug}/izle/{episode_number}"
    return f"{BASE_URL}/anime/{slug}"

# ─────────────────────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Animely.net API Test ===\n")
    
    # Anime listesi
    print("[1] Anime listesi çekiliyor...")
    animes = get_anime_list()
    print(f"    Toplam: {len(animes)} anime\n")
    
    # Arama
    print("[2] 'one piece' aranıyor...")
    results = search_anime("one piece")
    print(f"    Sonuç: {len(results)} anime")
    if results:
        anime = results[0]
        print(f"    İlk: {anime.name} ({anime.total_episodes} bölüm)\n")
        
        # Bölümler
        print("[3] Bölümler çekiliyor...")
        episodes = get_anime_episodes(anime.slug)
        print(f"    Toplam: {len(episodes)} bölüm")
        if episodes:
            ep = episodes[0]
            print(f"    İlk: {ep.name}")
            
            # Streamler
            streams = ep.get_streams()
            print(f"    Linkler: {len(streams)}")
            for s in streams[:3]:
                print(f"      - {s.quality}: {s.url[:50]}...")
    
    print("\n=== Test Tamamlandı ===")
