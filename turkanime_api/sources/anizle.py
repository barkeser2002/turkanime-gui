"""
Anizle/Anizm kaynağı için standalone istemci.

Bu modül, anizm.pro API'sini doğrudan kullanarak anime arama,
bölüm listeleme ve stream URL'lerini çekmeyi sağlar.
Cloudflare bypass desteği mevcuttur.

API Akışı (Stream'ler için):
1. Episode sayfasından translator butonlarını al
2. Translator endpoint'ine istek at → video butonları
3. Video endpoint'ine istek at → player iframe
4. Player iframe'den FirePlayer ID'sini çöz
5. anizmplayer.com getVideo endpoint'i → gerçek video URL'si
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

# CF Bypass modülünü içe aktar
try:
    from turkanime_api.common.cf_bypass import CFSession, CFBypassError, get_cf_session
    HAS_CF_BYPASS = True
except ImportError:
    HAS_CF_BYPASS = False

import requests

# ============================================================================
# Konfigürasyon
# ============================================================================

# Ana URL'ler - anizle.com ve anizle.org redirect yapıyor, anizm.pro ana API
BASE_URL = "https://anizm.pro"
API_BASE_URL = "https://anizle.org"  # API istekleri için
ANIME_LIST_URL = f"{BASE_URL}/getAnimeListForSearch"
PLAYER_BASE_URL = "https://anizmplayer.com"

# Fallback: Uzak sunucu (eski yöntem)
SERVER_URL = "https://turkanimeapi.bariskeser.com"
USE_REMOTE_SERVER = False  # True yapılırsa eski sunucu kullanılır

# Hız ayarları
HTTP_TIMEOUT = 10  # Varsayılan timeout (saniye)
MAX_WORKERS = 8  # Paralel işlem sayısı

# Global anime veritabanı (cache)
_anime_database: List[Dict[str, Any]] = []
_database_loaded: bool = False

# Global CF session
_cf_session: Optional[Any] = None


def _get_cf_session() -> Any:
    """CF session'ı döndür (singleton)."""
    global _cf_session
    if _cf_session is None:
        if HAS_CF_BYPASS:
            _cf_session = CFSession(timeout=60)
        else:
            # Fallback: basit bir nesne oluştur
            _cf_session = requests.Session()
    return _cf_session


def _http_get(url: str, timeout: int = 60, headers: Optional[Dict[str, str]] = None) -> Optional[Any]:
    """HTTP GET isteği yap (curl_cffi ile)."""
    
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if headers:
        default_headers.update(headers)
    
    try:
        # Önce curl_cffi dene
        try:
            from curl_cffi import requests as curl_requests
            session = curl_requests.Session(impersonate="chrome110")
            return session.get(url, headers=default_headers, timeout=timeout)
        except ImportError:
            pass
        
        # CF Bypass modülü dene
        if HAS_CF_BYPASS:
            session = _get_cf_session()
            return session.get(url, headers=default_headers)
        
        # Fallback: normal requests
        return requests.get(url, headers=default_headers, timeout=timeout)
        
    except Exception:
        # Sessiz başarısızlık - paralel işlemde çok fazla hata mesajı olmasın
        return None


def _http_post(url: str, timeout: int = 60, headers: Optional[Dict[str, str]] = None, data: Optional[Dict] = None) -> Optional[Any]:
    """HTTP POST isteği yap (curl_cffi ile)."""
    
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    if headers:
        default_headers.update(headers)
    
    try:
        # Önce curl_cffi dene
        try:
            from curl_cffi import requests as curl_requests
            session = curl_requests.Session(impersonate="chrome110")
            return session.post(url, headers=default_headers, timeout=timeout, data=data)
        except ImportError:
            pass
        
        # CF Bypass modülü dene
        if HAS_CF_BYPASS:
            session = _get_cf_session()
            return session.post(url, headers=default_headers, data=data)
        
        # Fallback: normal requests
        return requests.post(url, headers=default_headers, timeout=timeout, data=data)
        
    except Exception as e:
        print(f"[Anizle] HTTP POST hatası ({url}): {e}")
        return None


# ============================================================================
# Anime Veritabanı Yönetimi
# ============================================================================

def load_anime_database(force_reload: bool = False) -> List[Dict[str, Any]]:
    """
    Anizm.pro'dan anime veritabanını yükle.
    
    API Endpoint: https://anizm.pro/getAnimeListForSearch
    
    Her anime şu alanları içerir:
    - info_id: int
    - info_title: str (Türkçe/orijinal başlık)
    - info_titleoriginal: str
    - info_titleenglish: str
    - info_slug: str
    - info_poster: str
    - info_summary: str
    - info_year: str
    - info_malid: int (MyAnimeList ID)
    - info_malpoint: float
    - lastEpisode: list (son bölümler)
    - categories: list (kategoriler)
    """
    global _anime_database, _database_loaded
    
    if _database_loaded and not force_reload:
        return _anime_database
    
    try:
        response = _http_get(ANIME_LIST_URL, timeout=120)
        
        if response is None:
            return []
        
        if response.status_code != 200:
            return []
        
        data = response.json()
        
        if isinstance(data, list):
            _anime_database = data
            _database_loaded = True
            return _anime_database
        else:
            return []
            
    except json.JSONDecodeError:
        return []
    except Exception:
        return []


def _similarity_score(query: str, text: str) -> float:
    """İki metin arasındaki benzerlik oranını hesapla."""
    if not text:
        return 0.0
    query_lower = query.lower()
    text_lower = text.lower()
    
    # Tam eşleşme
    if query_lower == text_lower:
        return 1.0
    
    # İçerme kontrolü (yüksek puan)
    if query_lower in text_lower:
        return 0.9
    
    # SequenceMatcher ile fuzzy match
    return SequenceMatcher(None, query_lower, text_lower).ratio()


def search_anizle(query: str, limit: int = 20, timeout: int = 60) -> List[Tuple[str, str]]:
    """
    Anizle/Anizm üzerinde anime ara.
    
    Args:
        query: Arama sorgusu
        limit: Maksimum sonuç sayısı
        timeout: Zaman aşımı (saniye)
    
    Returns:
        Liste[Tuple[slug, title]] formatında sonuçlar
    """
    # Uzak sunucu modunda eski API'yi kullan
    if USE_REMOTE_SERVER:
        return _search_remote(query, limit, timeout)
    
    # Veritabanını yükle
    database = load_anime_database()
    if not database:
        print("[Anizle] Veritabanı boş, uzak sunucu deneniyor...")
        return _search_remote(query, limit, timeout)
    
    # Arama yap
    results: List[Tuple[float, str, str]] = []
    
    for anime in database:
        # Tüm başlıklardan en yüksek skoru al
        scores = [
            _similarity_score(query, anime.get("info_title", "")),
            _similarity_score(query, anime.get("info_titleoriginal", "")),
            _similarity_score(query, anime.get("info_titleenglish", "")),
            _similarity_score(query, anime.get("info_othernames", "")),
            _similarity_score(query, anime.get("info_japanese", "")),
        ]
        max_score = max(scores)
        
        if max_score > 0.3:  # Minimum eşik
            slug = anime.get("info_slug", "")
            title = anime.get("info_title", "")
            if slug and title:
                results.append((max_score, slug, title))
    
    # Skora göre sırala ve limitle
    results.sort(key=lambda x: x[0], reverse=True)
    return [(slug, title) for _, slug, title in results[:limit]]


def _search_remote(query: str, limit: int = 20, timeout: int = 60) -> List[Tuple[str, str]]:
    """Uzak sunucu üzerinden arama yap (fallback)."""
    try:
        response = requests.get(
            f"{SERVER_URL}/anizle/search",
            params={"q": query, "limit": limit},
            timeout=timeout
        )
        response.raise_for_status()
        results = response.json()
        return results if isinstance(results, list) else []
    except Exception as e:
        print(f"[Anizle] Uzak sunucu arama hatası: {e}")
        return []


# ============================================================================
# Bölüm Listeleme
# ============================================================================

def get_anime_episodes(slug: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """
    Bir animenin bölümlerini getir.
    
    Args:
        slug: Anime slug'ı (örn: "one-piece")
        timeout: Zaman aşımı (saniye)
    
    Returns:
        Liste[Tuple[episode_slug, episode_title]] formatında bölümler
    """
    # Önce veritabanından dene
    database = load_anime_database()
    
    for anime in database:
        if anime.get("info_slug") == slug:
            episodes = anime.get("lastEpisode", [])
            if episodes:
                # Veritabanındaki son bölümler sınırlı olabilir
                # Tam listeyi almak için sayfayı çekmemiz gerekebilir
                result = []
                for ep in episodes:
                    ep_slug = ep.get("episode_slug", "")
                    ep_title = ep.get("episode_title", "")
                    if ep_slug and ep_title:
                        result.append((ep_slug, ep_title))
                
                # Veritabanında sadece son birkaç bölüm var
                # Tam liste için anime sayfasını çek
                if len(result) > 0:
                    full_episodes = _fetch_all_episodes_from_page(slug, timeout)
                    if full_episodes:
                        return full_episodes
                    return result
    
    # Veritabanında bulunamadı, sayfadan çek
    return _fetch_all_episodes_from_page(slug, timeout)


def _fetch_all_episodes_from_page(slug: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """Anime sayfasından tüm bölümleri çek."""
    # URL prefix'i varsa temizle
    clean_slug = slug
    if clean_slug.startswith(BASE_URL):
        clean_slug = clean_slug[len(BASE_URL):]
    if clean_slug.startswith("http"):
        url = clean_slug
    else:
        clean_slug = clean_slug.lstrip("/")
        url = f"{BASE_URL}/{clean_slug}"
    
    try:
        response = _http_get(url, timeout)
        if response is None or response.status_code != 200:
            return _get_episodes_remote(slug, timeout)
        
        html = response.text
        
        # Bölüm linklerini regex ile çek
        # Site hem absolute hem relative URL kullanabilir:
        # href="https://anizm.pro/anime-slug-1-bolum-izle"
        # href="/anime-slug-1-bolum-izle"
        
        episodes: List[Tuple[int, str, str]] = []
        seen_episode_nums: set = set()  # Bölüm numarasına göre duplikasyon kontrolü
        
        # Pattern 1: Absolute URL (https://anizm.pro/slug-N-bolum-izle)
        abs_pattern = re.escape(BASE_URL) + r'/([^"]+?-(\d+)-bolum[^"]*)'
        matches_abs = re.findall(r'href="' + abs_pattern + r'"', html, re.IGNORECASE)
        for ep_slug, ep_num in matches_abs:
            ep_slug_clean = ep_slug.strip('/')
            try:
                order_num = int(ep_num)
                if order_num not in seen_episode_nums:
                    seen_episode_nums.add(order_num)
                    episodes.append((order_num, ep_slug_clean, f"{ep_num}. Bölüm"))
            except ValueError:
                pass
        
        # Pattern 2: data-order ile (relative veya absolute)
        if not episodes:
            pattern1 = r'href="(?:' + re.escape(BASE_URL) + r')?/?([^"]+?-bolum[^"]*)"[^>]*data-order="(\d+)"[^>]*>([^<]+)'
            matches1 = re.findall(pattern1, html, re.IGNORECASE)
            for ep_slug, order, title in matches1:
                ep_slug_clean = ep_slug.strip('/')
                try:
                    order_num = int(order)
                    if order_num not in seen_episode_nums:
                        seen_episode_nums.add(order_num)
                        episodes.append((order_num, ep_slug_clean, title.strip()))
                except ValueError:
                    pass
        
        # Pattern 3: Relative URL fallback
        if not episodes:
            pattern2 = r'href="/?([^"]+?-(\d+)-bolum[^"]*)"[^>]*>([^<]*)'
            matches2 = re.findall(pattern2, html, re.IGNORECASE)
            for ep_slug, ep_num, title in matches2:
                ep_slug_clean = ep_slug.strip('/')
                # Absolute URL temizleme
                if ep_slug_clean.startswith(('http://', 'https://')):
                    from urllib.parse import urlparse
                    ep_slug_clean = urlparse(ep_slug_clean).path.strip('/')
                try:
                    order_num = int(ep_num)
                    if order_num not in seen_episode_nums:
                        seen_episode_nums.add(order_num)
                        final_title = title.strip() if title.strip() else f"{ep_num}. Bölüm"
                        episodes.append((order_num, ep_slug_clean, final_title))
                except ValueError:
                    pass
        
        if not episodes:
            return _get_episodes_remote(slug, timeout)
        
        # Sırala ve döndür
        episodes.sort(key=lambda x: x[0])
        return [(ep_slug, title) for _, ep_slug, title in episodes]
        
    except Exception as e:
        print(f"[Anizle] Bölüm çekme hatası: {e}")
        return _get_episodes_remote(slug, timeout)


def _get_episodes_remote(slug: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """Uzak sunucu üzerinden bölümleri al (fallback)."""
    try:
        response = requests.get(
            f"{SERVER_URL}/anizle/episodes/{slug}",
            timeout=timeout
        )
        response.raise_for_status()
        results = response.json()
        return results if isinstance(results, list) else []
    except Exception as e:
        print(f"[Anizle] Uzak sunucu bölüm hatası: {e}")
        return []


# ============================================================================
# Stream URL'leri - YENİ API AKIŞI
# ============================================================================

def _unpack_js(p: str, a: int, c: int, k: List[str]) -> str:
    """Dean Edwards' JavaScript packer decoder."""
    def e(c: int, a: int) -> str:
        """Base conversion function."""
        first = '' if c < a else e(c // a, a)
        c = c % a
        if c > 35:
            second = chr(c + 29)  # A-Z (uppercase)
        elif c > 9:
            second = chr(c + 87)  # a-z (lowercase)
        else:
            second = str(c)
        return first + second
    
    # Sözlük oluştur
    d = {}
    temp_c = c
    while temp_c:
        temp_c -= 1
        key = e(temp_c, a)
        d[key] = k[temp_c] if temp_c < len(k) and k[temp_c] else key
    
    # Kelimeleri değiştir
    def replace_func(match):
        return d.get(match.group(0), match.group(0))
    
    return re.sub(r'\b\w+\b', replace_func, p)


def _extract_fireplayer_id(player_html: str) -> Optional[str]:
    """Player HTML'inden FirePlayer ID'sini çıkar."""
    
    # Packed JS'i bul
    eval_match = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}return p\}\('(.*?)',(\d+),(\d+),'([^']+)'\.split\('\|'\),0,\{\}\)\)",
        player_html, re.S
    )
    
    if eval_match:
        p = eval_match.group(1)
        a = int(eval_match.group(2))
        c = int(eval_match.group(3))
        k = eval_match.group(4).split('|')
        
        # Decode edip FirePlayer pattern'ini ara
        try:
            decoded = _unpack_js(p, a, c, k)
            id_match = re.search(r'FirePlayer\s*\(\s*["\']([a-f0-9]{32})["\']', decoded)
            if id_match:
                return id_match.group(1)
        except Exception as e:
            print(f"[Anizle] JS decode hatası: {e}")
    
    # Fallback: Doğrudan HTML'de FirePlayer pattern'i ara
    fp_direct = re.search(r'FirePlayer\s*\(["\']([a-f0-9]{32})["\']', player_html)
    if fp_direct:
        return fp_direct.group(1)
    
    return None


def _get_video_stream_from_player(player_id: str, video_name: str) -> Optional[Dict[str, str]]:
    """
    FirePlayer ID'sinden gerçek video stream URL'sini al.
    
    Endpoint: anizmplayer.com/player/index.php?data=ID&do=getVideo
    """
    try:
        url = f"{PLAYER_BASE_URL}/player/index.php?data={player_id}&do=getVideo"
        response = _http_post(
            url,
            headers={
                "Referer": f"{PLAYER_BASE_URL}/player/{player_id}",
                "Origin": PLAYER_BASE_URL,
            }
        )
        
        if response is None or response.status_code != 200:
            return None
        
        data = response.json()
        
        # HLS stream
        if data.get("hls") and data.get("securedLink"):
            return {
                "url": data["securedLink"],
                "label": f"{video_name} (HLS)",
                "type": "hls"
            }
        
        # Video source
        if data.get("videoSource"):
            return {
                "url": data["videoSource"],
                "label": video_name,
                "type": "direct"
            }
        
        return None
        
    except Exception as e:
        print(f"[Anizle] FirePlayer video çekme hatası: {e}")
        return None


def _get_player_iframe_url(video_url: str) -> Optional[Tuple[str, str]]:
    """
    Video endpoint'inden player ID'sini al.
    
    Returns: (player_id, video_name) tuple
    """
    try:
        response = _http_get(
            video_url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
                "Referer": API_BASE_URL,
            }
        )
        
        if response is None or response.status_code != 200:
            return None
        
        data = response.json()
        player_html = data.get("player", "")
        
        # iframe src'den player ID'yi çıkar
        # src="https://anizle.org/player/1538440" -> 1538440
        iframe_match = re.search(r'/player/(\d+)', player_html)
        if iframe_match:
            return iframe_match.group(1), "Anizm Player"
        
        return None
        
    except Exception as e:
        print(f"[Anizle] Player iframe çekme hatası: {e}")
        return None


def _get_translator_videos(translator_url: str) -> List[Dict[str, str]]:
    """
    Translator endpoint'inden video listesini al.
    
    Returns: [{"url": video_url, "name": video_name}, ...]
    """
    videos = []
    
    try:
        response = _http_get(
            translator_url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
                "Referer": API_BASE_URL,
            }
        )
        
        if response is None or response.status_code != 200:
            return []
        
        data = response.json()
        html = data.get("data", "")
        
        # video attribute'li anchor'ları bul
        # <a href="#" video="https://anizle.org/video/1538440" data-video-name="Player Name">
        pattern = r'video="([^"]+)"[^>]*data-video-name="([^"]*)"'
        matches = re.findall(pattern, html)
        
        for video_url, video_name in matches:
            videos.append({
                "url": video_url,
                "name": video_name or "Player"
            })
        
        # Alternatif pattern (data-video-name önce)
        if not videos:
            pattern2 = r'data-video-name="([^"]*)"[^>]*video="([^"]+)"'
            matches2 = re.findall(pattern2, html)
            for video_name, video_url in matches2:
                videos.append({
                    "url": video_url,
                    "name": video_name or "Player"
                })
        
        return videos
        
    except Exception as e:
        print(f"[Anizle] Translator video listesi hatası: {e}")
        return []


def _get_episode_translators(episode_slug: str) -> List[Dict[str, str]]:
    """
    Episode sayfasından translator listesini al.
    
    Returns: [{"url": translator_url, "name": fansub_name}, ...]
    """
    translators = []
    
    # URL'yi düzelt
    clean_slug = episode_slug
    if clean_slug.startswith(("http://", "https://")):
        # Tam URL verilmişse — anizm.pro URL'sini anizle.org'a çevir
        if "anizm.pro" in clean_slug:
            from urllib.parse import urlparse
            path = urlparse(clean_slug).path
            url = f"{API_BASE_URL}{path}"
        else:
            url = clean_slug
    else:
        clean_slug = clean_slug.lstrip("/")
        url = f"{API_BASE_URL}/{clean_slug}"
    
    # anizle.org'da dene, sonra anizm.pro dene
    urls_to_try = [url]
    if API_BASE_URL in url:
        urls_to_try.append(url.replace(API_BASE_URL, BASE_URL))
    elif BASE_URL in url:
        urls_to_try.insert(0, url.replace(BASE_URL, API_BASE_URL))
    
    for try_url in urls_to_try:
        try:
            response = _http_get(try_url)
            
            if response is None or response.status_code != 200:
                continue
            
            html = response.text
            
            # translator attribute'li elementleri bul
            # translator="https://anizle.org/episode/18851/translator/83196"
            # data-fansub-name="VictoriaSubs"
            pattern = r'translator="([^"]+)"[^>]*data-fansub-name="([^"]*)"'
            matches = re.findall(pattern, html)
            
            # Alternatif sıralama: data-fansub-name önce
            if not matches:
                pattern2 = r'data-fansub-name="([^"]*)"[^>]*translator="([^"]+)"'
                matches2 = re.findall(pattern2, html)
                matches = [(url, name) for name, url in matches2]
            
            seen_urls = set()
            for tr_url, fansub_name in matches:
                if tr_url not in seen_urls:
                    seen_urls.add(tr_url)
                    translators.append({
                        "url": tr_url,
                        "name": fansub_name or "Fansub"
                    })
            
            if translators:
                return translators
        
        except Exception as e:
            print(f"[Anizle] Translator listesi hatası ({try_url}): {e}")
    
    return translators


def _process_single_video(video_info: Dict[str, str]) -> Optional[Dict[str, str]]:
    """
    Tek bir video için stream URL'sini al.
    Thread-safe helper fonksiyon.
    
    Args:
        video_info: {"url": video_url, "name": video_name, "fansub": fansub_name}
    
    Returns:
        Dict[url, label] veya None
    """
    try:
        video_url = video_info["url"]
        video_name = video_info["name"]
        fansub_name = video_info["fansub"]
        
        # Player ID'yi al
        iframe_result = _get_player_iframe_url(video_url)
        if not iframe_result:
            return None
        
        player_id, _ = iframe_result
        
        # Player sayfasını al (anizle.org/player/, Referer gerekli!)
        player_page_url = f"{API_BASE_URL}/player/{player_id}"
        player_response = _http_get(
            player_page_url,
            timeout=HTTP_TIMEOUT,
            headers={"Referer": f"{API_BASE_URL}/"}
        )
        
        if player_response is None or player_response.status_code != 200:
            return None
        
        # FirePlayer ID'sini çöz
        fireplayer_id = _extract_fireplayer_id(player_response.text)
        if not fireplayer_id:
            return None
        
        # Gerçek video URL'sini al
        return _get_video_stream_from_player(fireplayer_id, f"{fansub_name} - {video_name}")
        
    except Exception:
        return None


def get_episode_streams(episode_slug: str, timeout: int = HTTP_TIMEOUT) -> List[Dict[str, str]]:
    """
    Bir bölümün video stream URL'lerini getir (paralel işlem).
    
    API Akışı:
    1. Episode sayfasından translator'ları al
    2. Her translator için video listesini al
    3. Tüm videolar paralel olarak işlenir:
       - Player ID'sini al
       - anizmplayer.com player sayfasından FirePlayer ID'sini çöz
       - anizmplayer.com'dan gerçek video URL'sini al
    
    Args:
        episode_slug: Bölüm slug'ı
        timeout: Zaman aşımı (saniye)
    
    Returns:
        Liste[Dict[url, label]] formatında stream'ler
    """
    streams: List[Dict[str, str]] = []
    
    # 1. Translator'ları al
    translators = _get_episode_translators(episode_slug)
    
    if not translators:
        return _get_streams_remote(episode_slug, timeout)
    
    # 2. Tüm video bilgilerini topla
    all_videos: List[Dict[str, str]] = []
    for translator in translators:
        fansub_name = translator["name"]
        videos = _get_translator_videos(translator["url"])
        
        for video in videos:
            all_videos.append({
                "url": video["url"],
                "name": video["name"],
                "fansub": fansub_name
            })
    
    if not all_videos:
        return _get_streams_remote(episode_slug, timeout)
    
    print(f"[Anizle] {len(all_videos)} video taranıyor...")
    
    # 3. Paralel olarak tüm videoları işle
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_process_single_video, video): video for video in all_videos}
        
        for future in as_completed(futures):
            try:
                result = future.result(timeout=timeout)
                if result:
                    streams.append(result)
            except Exception:
                pass
    
    if not streams:
        return _get_streams_remote(episode_slug, timeout)
    
    print(f"[Anizle] {len(streams)} stream bulundu")
    return streams


def _get_streams_remote(episode_slug: str, timeout: int = HTTP_TIMEOUT) -> List[Dict[str, str]]:
    """Uzak sunucu üzerinden stream'leri al (fallback)."""
    try:
        response = requests.get(
            f"{SERVER_URL}/anizle/streams/{episode_slug}",
            timeout=timeout
        )
        response.raise_for_status()
        results = response.json()
        return results if isinstance(results, list) else []
    except Exception:
        return []


# ============================================================================
# Dataclass'lar
# ============================================================================

@dataclass
class AnizleEpisode:
    """Anizle bölüm nesnesi."""
    title: str
    url: str

    def streams(self, timeout: int = 60) -> List[Dict[str, str]]:
        """Bölümün stream URL'lerini getir."""
        return get_episode_streams(self.url, timeout=timeout)


@dataclass
class AnizleAnime:
    """Anizle anime nesnesi."""
    slug: str
    title: str
    info_id: int = 0
    poster: str = ""
    year: str = ""
    mal_id: int = 0
    mal_score: float = 0.0
    summary: str = ""
    categories: List[str] = field(default_factory=list)

    @classmethod
    def from_database(cls, data: Dict[str, Any]) -> "AnizleAnime":
        """Veritabanı kaydından AnizleAnime oluştur."""
        categories = []
        for cat in data.get("categories", []):
            if isinstance(cat, dict) and "tag_title" in cat:
                categories.append(cat["tag_title"])
        
        return cls(
            slug=data.get("info_slug", ""),
            title=data.get("info_title", ""),
            info_id=data.get("info_id", 0),
            poster=data.get("info_poster", ""),
            year=data.get("info_year", ""),
            mal_id=data.get("info_malid", 0),
            mal_score=data.get("info_malpoint", 0.0),
            summary=data.get("info_summary", ""),
            categories=categories,
        )

    @property
    def episodes(self) -> List[AnizleEpisode]:
        """Animenin bölümlerini getir (duplikasyonlar filtrelenir)."""
        eps: List[AnizleEpisode] = []
        seen_urls: set = set()  # Duplikasyon kontrolü
        episodes_data = get_anime_episodes(self.slug)
        if episodes_data:
            for slug, label in episodes_data:
                # URL bazlı duplikasyon kontrolü
                if slug not in seen_urls:
                    seen_urls.add(slug)
                    eps.append(AnizleEpisode(title=label, url=slug))
        return eps

    @property
    def poster_url(self) -> str:
        """Tam poster URL'ini döndür."""
        if not self.poster:
            return ""
        if self.poster.startswith("http"):
            return self.poster
        return f"https://anizm.pro/uploads/img/{self.poster}"


def get_anime_details(slug: str) -> Optional[AnizleAnime]:
    """
    Anime detaylarını al.
    
    Args:
        slug: Anime slug'ı
    
    Returns:
        AnizleAnime nesnesi veya None
    """
    database = load_anime_database()
    
    for anime in database:
        if anime.get("info_slug") == slug:
            return AnizleAnime.from_database(anime)
    
    # Bulunamazsa basit nesne döndür
    return AnizleAnime(slug=slug, title=slug.replace("-", " ").title())


__all__ = [
    "AnizleAnime",
    "AnizleEpisode",
    "get_anime_details",
    "get_anime_episodes",
    "get_episode_streams",
    "load_anime_database",
    "search_anizle",
    "USE_REMOTE_SERVER",
]
