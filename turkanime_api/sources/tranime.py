"""
TRAnimeİzle.io API Client
https://www.tranimeizle.io

API Endpoints:
- GET /anime/{slug}-izle -> Anime detay sayfası
- GET /{slug}-{episode}-bolum-izle -> Bölüm izleme sayfası
- POST /api/fansubSources -> Kaynak listesi (JSON: EpisodeId, FansubId)
- POST /api/sourcePlayer/{source_id} -> Video iframe (JSON response)
- GET /harfler/{letter}/sayfa-{page} -> Harfe göre anime listesi
"""

import json
import time
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import unquote

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL = True
except ImportError:
    HAS_CURL = False

import requests as std_requests

# ─────────────────────────────────────────────────────────────────────────────
# YAPILANDIRMA
# ─────────────────────────────────────────────────────────────────────────────
BASE_URL = "https://www.tranimeizle.io"
CACHE_DIR = Path.home() / ".turkanime" / "tranime_cache"
CACHE_DURATION = 30 * 60  # 30 dakika
HTTP_TIMEOUT = 15

# Cookie - bot korumasını aşmak için gerekli
# Bu cookie kullanıcının tarayıcısından alınmalı
SESSION_COOKIE = None
# Ek cookie'ler (age_verified, Verification vb.)
_EXTRA_COOKIES: Dict[str, str] = {}


def set_session_cookie(cookie_value: str):
    """Session cookie'yi ayarla.

    Netscape HTTP Cookie File formatını da kabul eder.
    Eğer birden fazla satır varsa ve 'tranimeizle' içeriyorsa,
    tüm cookie'ler otomatik olarak parse edilir.
    Tek satır verilirse sadece .AitrWeb.Session olarak kullanılır.
    """
    global SESSION_COOKIE, _EXTRA_COOKIES
    value = cookie_value.strip()
    if not value:
        SESSION_COOKIE = None
        _EXTRA_COOKIES = {}
        return

    # Netscape cookie file formatı kontrolü (birden fazla satır + tab ayrımı)
    lines = value.splitlines()
    parsed_cookies: Dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            # Netscape: domain  flag  path  secure  expiry  name  value
            name = parts[5].strip()
            val = parts[6].strip()
            if name and val:
                parsed_cookies[name] = val

    if parsed_cookies:
        # Netscape formatı başarıyla parse edildi
        session_val = parsed_cookies.pop('.AitrWeb.Session', '')
        if session_val:
            SESSION_COOKIE = unquote(session_val) if '%' in session_val else session_val
        else:
            # .AitrWeb.Session yoksa en uzun cookie'yi session olarak kullan
            longest = max(parsed_cookies.items(), key=lambda x: len(x[1]), default=('', ''))
            if longest[1]:
                SESSION_COOKIE = unquote(longest[1]) if '%' in longest[1] else longest[1]
                parsed_cookies.pop(longest[0], None)
        _EXTRA_COOKIES = parsed_cookies
    else:
        # Tek satır — doğrudan .AitrWeb.Session değeri
        SESSION_COOKIE = unquote(value) if '%' in value else value
        _EXTRA_COOKIES = {}


def _get_session():
    """HTTP session oluştur."""
    if HAS_CURL:
        session = curl_requests.Session(impersonate="chrome110")
    else:
        session = std_requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        })
    return session


def _get_cookies() -> dict:
    """Cookie'leri döndür (Session + ek cookie'ler)."""
    cookies: dict = {}
    if _EXTRA_COOKIES:
        cookies.update(_EXTRA_COOKIES)
    if SESSION_COOKIE:
        cookies['.AitrWeb.Session'] = SESSION_COOKIE
    # age_verified her zaman gönder
    cookies.setdefault('age_verified', 'true')
    return cookies if SESSION_COOKIE else {}


# ─────────────────────────────────────────────────────────────────────────────
# CACHE YÖNETİMİ
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_cache_dir():
    """Cache dizinini oluştur."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _get_cache(key: str) -> Optional[Any]:
    """Cache'den veri al."""
    cache_file = CACHE_DIR / f"{key}.json"
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


def _save_cache(key: str, data: Any):
    """Cache'e veri kaydet."""
    _ensure_cache_dir()
    cache_file = CACHE_DIR / f"{key}.json"
    
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"timestamp": time.time(), "data": data}, f, ensure_ascii=False)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# VERİ SINIFLARI
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TRAnimeVideo:
    """Video kaynağı."""
    source_id: str
    name: str
    fansub: str
    iframe_url: str = ""
    
    def get_iframe(self) -> str:
        """Video iframe URL'ini al."""
        if self.iframe_url:
            return self.iframe_url
        
        try:
            session = _get_session()
            resp = session.post(
                f"{BASE_URL}/api/sourcePlayer/{self.source_id}",
                cookies=_get_cookies(),
                timeout=HTTP_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
            
            if 'source' in data:
                match = re.search(r'src="([^"]+)"', data['source'])
                if match:
                    self.iframe_url = match.group(1)
                    return self.iframe_url
        except Exception as e:
            print(f"[TRAnime] Video alınamadı: {e}")
        
        return ""
    
    def __repr__(self):
        return f"TRAnimeVideo({self.name}, {self.fansub})"


@dataclass  
class TRAnimeEpisode:
    """Bölüm."""
    episode_id: int
    episode_number: int
    slug: str
    title: str
    fansubs: List[Tuple[str, str]] = field(default_factory=list)  # [(fid, name), ...]
    
    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.slug}"
    
    def get_sources(self, fansub_id: Optional[str] = None) -> List[TRAnimeVideo]:
        """Bölümün video kaynaklarını al."""
        if not fansub_id and self.fansubs:
            fansub_id = self.fansubs[0][0]
        
        if not fansub_id:
            return []
        
        try:
            session = _get_session()
            resp = session.post(
                f"{BASE_URL}/api/fansubSources",
                json={"EpisodeId": self.episode_id, "FansubId": int(fansub_id)},
                cookies=_get_cookies(),
                timeout=HTTP_TIMEOUT
            )
            resp.raise_for_status()
            
            # HTML parse
            sources = []
            items = re.findall(
                r'data-id="(\d+)"[^>]*>.*?<p[^>]*class="title"[^>]*>\s*(\S+)',
                resp.text, re.DOTALL
            )
            
            fansub_name = next((f[1] for f in self.fansubs if f[0] == fansub_id), "Unknown")
            
            for source_id, name in items:
                sources.append(TRAnimeVideo(
                    source_id=source_id,
                    name=name.strip(),
                    fansub=fansub_name
                ))
            
            return sources
        except Exception as e:
            print(f"[TRAnime] Kaynaklar alınamadı: {e}")
            return []
    
    def __repr__(self):
        return f"TRAnimeEpisode({self.episode_number}, {self.title})"


@dataclass
class TRAnimeAnime:
    """Anime."""
    slug: str
    title: str
    poster: str = ""
    total_episodes: int = 0
    _episodes: List[TRAnimeEpisode] = field(default_factory=list, repr=False)
    
    @property
    def url(self) -> str:
        return f"{BASE_URL}/anime/{self.slug}"
    
    @property
    def episodes(self) -> List[TRAnimeEpisode]:
        """Bölüm listesini lazy-load et."""
        if not self._episodes:
            self._episodes = get_anime_episodes(self.slug)
        return self._episodes
    
    def __repr__(self):
        return f"TRAnimeAnime({self.title}, {self.total_episodes} ep)"


# ─────────────────────────────────────────────────────────────────────────────
# API FONKSİYONLARI
# ─────────────────────────────────────────────────────────────────────────────
def get_anime_by_slug(slug: str) -> Optional[TRAnimeAnime]:
    """
    Slug ile anime bilgilerini al.
    
    Args:
        slug: Anime slug'ı (örn: "naruto-izle")
    """
    # Slug formatını düzelt
    if not slug.endswith('-izle'):
        slug = f"{slug}-izle"
    
    try:
        session = _get_session()
        resp = session.get(
            f"{BASE_URL}/anime/{slug}",
            cookies=_get_cookies(),
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        
        # Bot kontrolü varsa
        if 'Bot Kontrol' in resp.text:
            if not SESSION_COOKIE:
                print("[TRAnime] Bot kontrolü - cookie ayarlanmamış. Ayarlar'dan TRAnimeİzle cookie'si girin.")
            else:
                print("[TRAnime] Bot kontrolü - cookie geçersiz veya süresi dolmuş. Yeni cookie alın.")
            return None
        
        # Başlık - birden fazla yöntem dene
        title = None
        # Yöntem 1: H1
        title_match = re.search(r'<h1[^>]*>([^<]+)</h1>', resp.text)
        if title_match:
            title = title_match.group(1).strip()
        # Yöntem 2: og:title
        if not title:
            og_match = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', resp.text)
            if og_match:
                title = og_match.group(1).strip()
        # Yöntem 3: <title> etiketi
        if not title:
            page_match = re.search(r'<title>([^<]+)</title>', resp.text)
            if page_match:
                title = page_match.group(1).split(' - ')[0].strip()
        if not title:
            title = slug.replace('-izle', '').replace('-', ' ').title()
        
        # Poster - birden fazla yöntem dene
        poster = ""
        poster_match = re.search(r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*thumbnail', resp.text)
        if poster_match:
            poster = poster_match.group(1)
        else:
            og_img = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', resp.text)
            if og_img:
                poster = og_img.group(1)
            else:
                poster_alt = re.search(r'<img[^>]*src="([^"]+)"[^>]*(?:alt|class)="[^"]*(?:poster|cover|thumbnail)[^"]*"', resp.text, re.I)
                if poster_alt:
                    poster = poster_alt.group(1)
        
        if poster and not poster.startswith('http'):
            poster = BASE_URL + poster
        
        # Bölüm sayısı
        episodes = re.findall(r'href="(/[^"]*-\d+-bolum-izle)"', resp.text)
        
        return TRAnimeAnime(
            slug=slug.replace('-izle', ''),
            title=title.replace(' İzle', '').strip(),
            poster=poster,
            total_episodes=len(episodes)
        )
    except Exception as e:
        print(f"[TRAnime] Anime alınamadı ({slug}): {e}")
        return None


def get_anime_episodes(anime_slug: str) -> List[TRAnimeEpisode]:
    """
    Anime bölümlerini al.
    
    Args:
        anime_slug: Anime slug'ı
    """
    if not anime_slug.endswith('-izle'):
        anime_slug = f"{anime_slug}-izle"
    
    try:
        session = _get_session()
        resp = session.get(
            f"{BASE_URL}/anime/{anime_slug}",
            cookies=_get_cookies(),
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        
        if 'Bot Kontrol' in resp.text:
            if not SESSION_COOKIE:
                print("[TRAnime] Bot kontrolü - cookie ayarlanmamış. Ayarlar'dan TRAnimeİzle cookie'si girin.")
            else:
                print("[TRAnime] Bot kontrolü - cookie geçersiz veya süresi dolmuş.")
            return []
        
        # Bölüm linklerini bul
        episode_links = re.findall(r'href="(/([^"]*)-(\d+)-bolum-izle)"', resp.text)
        
        episodes = []
        seen = set()
        
        for full_path, slug_part, ep_num in episode_links:
            if full_path in seen:
                continue
            seen.add(full_path)
            
            ep_slug = full_path.lstrip('/')
            episodes.append(TRAnimeEpisode(
                episode_id=0,  # Sonradan doldurulacak
                episode_number=int(ep_num),
                slug=ep_slug,
                title=f"{ep_num}. Bölüm"
            ))
        
        # Bölüm numarasına göre sırala
        episodes.sort(key=lambda x: x.episode_number)
        
        return episodes
    except Exception as e:
        print(f"[TRAnime] Bölümler alınamadı ({anime_slug}): {e}")
        return []


def get_episode_details(episode_slug: str) -> Optional[TRAnimeEpisode]:
    """
    Bölüm detaylarını al (episode_id ve fansub listesi).
    
    Args:
        episode_slug: Bölüm slug'ı (örn: "naruto-4-bolum-izle")
    """
    try:
        session = _get_session()
        resp = session.get(
            f"{BASE_URL}/{episode_slug}",
            cookies=_get_cookies(),
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        
        if 'Bot Kontrol' in resp.text:
            if not SESSION_COOKIE:
                print("[TRAnime] Bot kontrolü - cookie ayarlanmamış. Ayarlar'dan TRAnimeİzle cookie'si girin.")
            else:
                print("[TRAnime] Bot kontrolü - cookie geçersiz veya süresi dolmuş.")
            return None
        
        # Episode ID
        ep_id_match = re.search(r'id="EpisodeId"[^>]*value="(\d+)"', resp.text)
        if not ep_id_match:
            return None
        
        episode_id = int(ep_id_match.group(1))
        
        # Bölüm numarası
        ep_num_match = re.search(r'-(\d+)-bolum-izle', episode_slug)
        episode_number = int(ep_num_match.group(1)) if ep_num_match else 0
        
        # Fansub listesi
        fansubs = re.findall(r'data-fid="(\d+)"[^>]*data-fad="([^"]+)"', resp.text)
        
        return TRAnimeEpisode(
            episode_id=episode_id,
            episode_number=episode_number,
            slug=episode_slug,
            title=f"{episode_number}. Bölüm",
            fansubs=fansubs
        )
    except Exception as e:
        print(f"[TRAnime] Bölüm detayları alınamadı ({episode_slug}): {e}")
        return None


def search_by_letter(letter: str, page: int = 1) -> List[Tuple[str, str]]:
    """
    Harfe göre anime ara.
    
    Args:
        letter: Harf (a-z veya #)
        page: Sayfa numarası
        
    Returns:
        [(slug, title), ...]
    """
    try:
        session = _get_session()
        resp = session.get(
            f"{BASE_URL}/harfler/{letter.lower()}/sayfa-{page}",
            cookies=_get_cookies(),
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        
        if 'Bot Kontrol' in resp.text:
            if not SESSION_COOKIE:
                print("[TRAnime] Bot kontrolü - cookie ayarlanmamış.")
            else:
                print("[TRAnime] Bot kontrolü - cookie geçersiz.")
            return []
        
        # Anime linklerini bul
        results = []
        matches = re.findall(r'href="/anime/([^"]+)"[^>]*>.*?<h\d[^>]*>([^<]+)</h\d>', resp.text, re.DOTALL)
        
        for slug, title in matches:
            clean_slug = slug.replace('-izle', '')
            clean_title = title.strip()
            if clean_title and clean_slug not in [r[0] for r in results]:
                results.append((clean_slug, clean_title))
        
        return results
    except Exception as e:
        print(f"[TRAnime] Arama hatası: {e}")
        return []


def _search_direct(query: str, limit: int = 10) -> List[Tuple[str, str]]:
    """
    Doğrudan arama sayfası üzerinden anime ara.
    Site path-based arama kullanır: /arama/{query}
    Cookie gerektirir - Bot kontrolü varsa boş döner.
    
    Args:
        query: Arama sorgusu
        limit: Maksimum sonuç sayısı
        
    Returns:
        [(slug, title), ...]
    """
    if not SESSION_COOKIE:
        return []
    
    try:
        from urllib.parse import quote
        encoded_query = quote(query.strip(), safe='')
        search_url = f"{BASE_URL}/arama/{encoded_query}"
        
        session = _get_session()
        resp = session.get(
            search_url,
            cookies=_get_cookies(),
            timeout=HTTP_TIMEOUT,
            allow_redirects=True
        )
        
        if resp.status_code != 200 or 'Bot Kontrol' in resp.text:
            return []
        
        # Anime linklerini bul
        results = []
        seen = set()
        
        # Pattern 1: h3/h4 iç içe link
        pattern = r'href="/anime/([^"]+?)-izle"[^>]*>.*?<h\d[^>]*>([^<]+)</h\d>'
        matches = re.findall(pattern, resp.text, re.DOTALL)
        
        for slug, title in matches:
            if slug not in seen:
                seen.add(slug)
                results.append((slug, title.strip().replace(' İzle', '')))
                if len(results) >= limit:
                    break
        
        # Pattern 2: Sadece anime link'leri
        if not results:
            links = re.findall(r'href="/anime/([^"]+?)-izle"', resp.text)
            for slug in links:
                if slug not in seen:
                    seen.add(slug)
                    title = slug.replace('-', ' ').title()
                    results.append((slug, title))
                    if len(results) >= limit:
                        break
        
        return results
    except Exception:
        return []


def _fuzzy_match(query: str, text: str) -> float:
    """Basit fuzzy eşleşme skoru hesapla."""
    from difflib import SequenceMatcher
    q = query.lower()
    t = text.lower()
    
    if q == t:
        return 1.0
    if q in t:
        return 0.9
    if t in q:
        return 0.7
    return SequenceMatcher(None, q, t).ratio()


def search_anime(query: str, limit: int = 10) -> List[Tuple[str, str]]:
    """
    Anime ara.
    
    Önce doğrudan arama (cookie varsa), ardından harfler sayfasından
    fuzzy matching ile filtreler.
    
    Args:
        query: Arama sorgusu
        limit: Maksimum sonuç sayısı
        
    Returns:
        [(slug, title), ...]
    """
    query_lower = query.lower().strip()
    
    if not query_lower:
        return []
    
    # Önce doğrudan aramayı dene (cookie varsa)
    direct_results = _search_direct(query, limit)
    if direct_results:
        return direct_results
    
    # İlk harfi al
    first_letter = query_lower[0]
    if not first_letter.isalpha():
        first_letter = '#'
    
    # Cache kontrol
    cache_key = f"search_{first_letter}"
    cached = _get_cache(cache_key)
    
    if cached is None:
        # Tüm sayfaları çek (max 5 sayfa)
        all_results = []
        for page in range(1, 6):
            results = search_by_letter(first_letter, page)
            if not results:
                break
            all_results.extend(results)
            time.sleep(0.3)  # Rate limit
        
        _save_cache(cache_key, all_results)
        cached = all_results
    
    # Fuzzy matching ile filtrele ve skorla
    scored_matches = []
    for slug, title in cached:
        score = max(
            _fuzzy_match(query_lower, title.lower()),
            _fuzzy_match(query_lower, slug.replace('-', ' '))
        )
        if score > 0.3:
            scored_matches.append((score, slug, title))
    
    # Skora göre sırala
    scored_matches.sort(key=lambda x: x[0], reverse=True)
    
    return [(slug, title) for _, slug, title in scored_matches[:limit]]


def search_tranime(query: str, limit: int = 10) -> List[Tuple[str, str]]:
    """
    Anime ara - adapter uyumluluğu için alias.
    """
    return search_anime(query, limit)


# ─────────────────────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== TRAnimeİzle.io API Test ===\n")
    
    # Cookie ayarla (test için)
    # set_session_cookie("YOUR_COOKIE_HERE")
    
    # Anime bilgisi
    print("[1] Anime bilgisi...")
    anime = get_anime_by_slug("naruto")
    if anime:
        print(f"    {anime.title} - {anime.total_episodes} bölüm")
        print(f"    URL: {anime.url}")
    
    # Bölümler
    print("\n[2] Bölümler...")
    if anime:
        episodes = anime.episodes
        print(f"    Toplam: {len(episodes)} bölüm")
        if episodes:
            print(f"    İlk: {episodes[0]}")
    
    # Bölüm detayı
    print("\n[3] Bölüm detayı...")
    if anime and anime.episodes:
        ep = get_episode_details(anime.episodes[0].slug)
        if ep:
            print(f"    Episode ID: {ep.episode_id}")
            print(f"    Fansubs: {ep.fansubs}")
            
            # Kaynaklar
            sources = ep.get_sources()
            print(f"    Kaynaklar: {len(sources)}")
            for s in sources[:3]:
                print(f"      - {s.name}")
    
    # Arama
    print("\n[4] Arama...")
    results = search_anime("one piece", limit=5)
    print(f"    Sonuç: {len(results)}")
    for slug, title in results:
        print(f"      - {title}")
    
    print("\n=== Test Tamamlandı ===")
