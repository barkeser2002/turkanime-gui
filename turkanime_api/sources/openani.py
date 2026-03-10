"""
OpenAnime Provider Adapter.

Bu modül, openani.me kaynağının içeriklerini çekmek için kullanılır.
Site SvelteKit ile Server-Side Rendering (SSR) kullandığından, arama ve bölüm 
bilgileri HTML içerisindeki JSON datalarından parse edilir.
"""
from __future__ import annotations

import re
import json
import time
from typing import List, Dict, Any, Optional, Tuple
from bs4 import BeautifulSoup

from ..objects import Anime, Bolum

# CF Bypass modülünü içe aktar
try:
    from turkanime_api.common.cf_bypass import CFSession, CFBypassError, get_cf_session
    HAS_CF_BYPASS = True
except ImportError:
    HAS_CF_BYPASS = False
    import requests

# Konfigürasyon
BASE_URL = "https://openani.me"
CDN_HOST = "https://de2---vn-t9g4tsan-5qcl.yeshi.eu.org" # Örnek CDN Host

# Kullanıcının sağladığı Authentication cookielerini ayarlıyoruz
OPENANI_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjcyOTQ2NTAxMzE1MjQwOTY1NTMiLCJ2ZXJpZmllZCI6dHJ1ZSwiaWF0IjoxNzcyMjY4OTE2LCJleHAiOjE3NzIyNzA3MTZ9.S3ft-Pep7vLNOzw8f3kJ-LxJVXV6vxAgVc4nA34sv_U"
OPENANI_REFRESH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjcyOTQ2NTAxMzE1MjQwOTY1NTMiLCJpYXQiOjE3NzIyNjg5MTYsImV4cCI6MTc4MDA0NDkxNn0.RRt-iUJ3kizjk6LSBc3gjPovy0CO-QDABAopXRNzVIY"

def _get_cf_session() -> Any:
    """CF session'ı döndür (singleton)."""
    if HAS_CF_BYPASS:
        session = CFSession(timeout=30)
        # Authentication cookielerini manuel olarak ekle
        session._cookies["token"] = OPENANI_TOKEN
        session._cookies["refreshToken"] = OPENANI_REFRESH_TOKEN
        return session
    else:
        # Fallback: normal session oluştur ve cookieleri ekle
        session = requests.Session()
        session.cookies.set("token", OPENANI_TOKEN, domain=".openani.me")
        session.cookies.set("refreshToken", OPENANI_REFRESH_TOKEN, domain=".openani.me")
        return session

def _extract_svelte_json(html: str) -> Optional[Dict]:
    """SvelteKit'in script tag'i içerisine gömdüğü type="application/json" yapısını ayrıştırır."""
    soup = BeautifulSoup(html, 'html.parser')
    script_tags = soup.find_all('script', type='application/json', attrs={'data-sveltekit-fetched': True})
    
    for script in script_tags:
        try:
            raw_data = json.loads(script.string)
            if 'body' in raw_data:
                # Body içinde asıl JSON payload'u var
                body_data = json.loads(raw_data['body'])
                return body_data
        except Exception:
            pass
            
    # Alternatif SvelteKit object serialization
    match = re.search(r'const data = (\[.*?\]);\s*Promise\.all', html, re.DOTALL)
    if match:
        pass
    
    return None

class OpenAniAdapter:
    """OpenAnime provider implementation."""

    PROVIDER_CONFIG = {
        "name": "OpenAnime",
        "base_url": BASE_URL,
        "search_url": f"{BASE_URL}/explore?q={{query}}",
        "anime_url": f"{BASE_URL}/anime/{{anime_id}}",
        "supported_resolutions": ["360p", "480p", "720p", "1080p"],
        "rate_limit": 1,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "timeout": 30,
    }

    def __init__(self):
        self.session = _get_cf_session()
        self.last_request = 0

    def _rate_limit_wait(self):
        """Rate limit kontrolü."""
        elapsed = time.time() - self.last_request
        if elapsed < self.PROVIDER_CONFIG['rate_limit']:
            time.sleep(self.PROVIDER_CONFIG['rate_limit'] - elapsed)
        self.last_request = time.time()

    def search_anime(self, query: str) -> List[Dict[str, Any]]:
        """Anime arama işlemi."""
        self._rate_limit_wait()
        search_url = self.PROVIDER_CONFIG['search_url'].format(query=query)
        headers = {"User-Agent": self.PROVIDER_CONFIG["user_agent"]}
        
        try:
            response = self.session.get(search_url, headers=headers)
            if response.status_code != 200:
                print(f"[OpenAni] Arama hatası: HTTP {response.status_code}")
                return []
                
            html = response.text if hasattr(response, 'text') else response.content.decode('utf-8')
            
            # openani.me tam eşleşme bulursa /anime/<slug> adresine direkt redirect eder
            actual_url = response.url if hasattr(response, 'url') else search_url
            if "/anime/" in actual_url and "/explore" not in actual_url:
                slug = actual_url.split("/anime/")[-1].split("?")[0].strip("/")
                
                # Başlığı sayfadan al
                body_data = _extract_svelte_json(html)
                title = slug.replace("-", " ").title()
                if body_data and "english" in body_data and body_data["english"]:
                    title = body_data["english"]
                elif body_data and "turkish" in body_data and body_data["turkish"]:
                    title = body_data["turkish"]
                    
                image = ""
                if body_data and "pictures" in body_data and "avatar" in body_data["pictures"]:
                    image = body_data["pictures"]["avatar"]
                    
                return [{
                    "title": title,
                    "url": actual_url,
                    "image": image,
                    "provider_data": {"item_id": slug, "search_query": query}
                }]
                
            # Eğer redirect yapmadıysa (liste sayfası)
            results = []
            slugs_found = set()
            
            # Svelte objesi içinden `english` ve `slug` değerlerini RegExp ile parse ediyoruz
            data_match = re.search(r'const data = (\[.*?\]);', html, re.DOTALL)
            if data_match:
                data_text = data_match.group(1)
                matches = re.finditer(r'(?:english|romaji|turkish):"([^"]+)",.*?(?:slug):"([^"]+)"', data_text)
                for match in matches:
                    title = match.group(1)
                    slug = match.group(2)
                    
                    try:
                        title = title.encode('ascii', 'ignore').decode('unicode_escape')
                    except:
                        pass
                        
                    if slug not in slugs_found:
                        slugs_found.add(slug)
                        anime_url = self.PROVIDER_CONFIG["anime_url"].format(anime_id=slug)
                        results.append({
                            "title": title,
                            "url": anime_url,
                            "image": "",
                            "provider_data": {"item_id": slug, "search_query": query}
                        })
                        if len(results) >= 20:
                            break
                            
                # Fallback
                if not results:
                     matches = re.finditer(r'slug:"([^"]+)"', data_text)
                     for match in matches:
                        slug = match.group(1)
                        if slug not in slugs_found and len(slug) > 2:
                            slugs_found.add(slug)
                            anime_url = self.PROVIDER_CONFIG["anime_url"].format(anime_id=slug)
                            results.append({
                                "title": slug.replace("-", " ").title(),
                                "url": anime_url,
                                "image": "",
                                "provider_data": {"item_id": slug, "search_query": query}
                            })
                            if len(results) >= 20:
                                break
                                
            return results

        except Exception as e:
            print(f"[OpenAni] Arama parse hatası: {e}")
            return []

    def get_anime_details(self, anime_url: str) -> Optional[Dict[str, Any]]:
        """Anime detaylarını getir."""
        self._rate_limit_wait()
        headers = {"User-Agent": self.PROVIDER_CONFIG["user_agent"]}
        
        try:
            response = self.session.get(anime_url, headers=headers)
            if response.status_code != 200:
                print(f"[OpenAni] Detay hatası: HTTP {response.status_code}")
                return None
                
            html = response.text if hasattr(response, 'text') else response.content.decode('utf-8')
            body_data = _extract_svelte_json(html)
            
            if not body_data:
                print("[OpenAni] Detay JS parsing başarısız.")
                return None
                
            title = body_data.get("english") or body_data.get("turkish") or "Bilinmeyen Anime"
            description = body_data.get("summary", "")
            
            image_url = ""
            if "pictures" in body_data and "avatar" in body_data["pictures"]:
                image_url = body_data["pictures"]["avatar"]
                
            genres = body_data.get("genres", [])
            episodes = body_data.get("numberOfEpisodes", 0)
            score = body_data.get("tmdbScore", 0.0)
            
            # Svelte objesinden sezon bilgisini de al
            seasons = body_data.get("seasons", [])

            return {
                "title": title,
                "description": description,
                "image": image_url,
                "genres": genres,
                "year": None,
                "episodes": episodes,
                "status": "Bilinmiyor",
                "score": score,
                "provider_data": {
                    "anime_url": anime_url,
                    "seasons": seasons,
                    "parsed_at": time.time(),
                    "slug": anime_url.split("/")[-1]
                }
            }

        except Exception as e:
            print(f"[OpenAni] Detay parse hatası: {e}")
            return None

    def get_episodes(self, anime_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Anime bölümlerini getir."""
        anime_url = anime_data.get('provider_data', {}).get('anime_url')
        slug = anime_data.get('provider_data', {}).get('slug')
        seasons = anime_data.get('provider_data', {}).get('seasons', [])
        
        if not anime_url or not slug:
            return []
            
        episodes = []
        episode_number = 1
        
        if seasons:
            for season in seasons:
                season_num = season.get("season_number", 1)
                episode_count = season.get("episode_count", 0)
                season_name = season.get("name", f"Sezon {season_num}")
                
                # Special (0) bölümleri bazen 404 veriyor veya farklı linkte oluyor.
                # Şimdilik ana sezonları alalım.
                if season_num == 0:
                    continue
                
                # Eğer episode detayları yoksa ama episode sayısı varsa, döngüyle oluştur
                for ep_num in range(1, episode_count + 1):
                    ep_slug = f"{slug}/{season_num}/{ep_num}"
                    ep_title = f"{season_name} - Bölüm {ep_num}"
                    ep_url = f"{BASE_URL}/anime/{ep_slug}"
                    
                    episode_data = {
                        "title": ep_title,
                        "episode_number": episode_number,
                        "url": ep_url,
                        "thumbnail": "",
                        "duration": None,
                        "provider_data": {
                            "episode_id": ep_slug,
                            "anime_url": anime_url
                        }
                    }
                    episodes.append(episode_data)
                    episode_number += 1
        else:
             # Eğer sezon bilgisi yoksa toplam bölüm kadar feyk link oluştur (1. sezon varsayılarak)
             episode_count = anime_data.get("episodes", 0)
             for ep_num in range(1, episode_count + 1):
                ep_slug = f"{slug}/1/{ep_num}"
                ep_title = f"1. Sezon - Bölüm {ep_num}"
                ep_url = f"{BASE_URL}/anime/{ep_slug}"
                
                episode_data = {
                    "title": ep_title,
                    "episode_number": ep_num,
                    "url": ep_url,
                    "thumbnail": "",
                    "duration": None,
                    "provider_data": {
                        "episode_id": ep_slug,
                        "anime_url": anime_url
                    }
                }
                episodes.append(episode_data)

        # Bölümleri numarasına göre sırala
        episodes.sort(key=lambda x: x['episode_number'])
        return episodes

    def get_video_urls(self, episode_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Bölümün video URL'lerini getir."""
        self._rate_limit_wait()
        episode_url = episode_data.get('url')
        if not episode_url:
            return []

        # Referer olarak serinin ana url'sini ver
        anime_url = episode_data.get('provider_data', {}).get('anime_url', BASE_URL)
        headers = {
            "User-Agent": self.PROVIDER_CONFIG["user_agent"],
            "Referer": anime_url
        }

        video_urls = []

        try:
            response = self.session.get(episode_url, headers=headers)
            if response.status_code != 200:
                print(f"[OpenAni] Video hatası: HTTP {response.status_code}")
                return []

            html = response.text if hasattr(response, 'text') else response.content.decode('utf-8')
            # Svelte data array içinden extract
            data_match = re.search(r'const data = (\[.*?\]);', html, re.DOTALL)
            if data_match:
                data_text = data_match.group(1)
                
                # try to find the actual cdn host dynamically
                dynamic_cdn = CDN_HOST
                cdn_match = re.search(r'CDN_LINK:"([^"]+)"', data_text)
                if cdn_match:
                     dynamic_cdn = cdn_match.group(1).replace("%CDN_HOST%", CDN_HOST)
                else:
                     dynamic_cdn = f"{CDN_HOST}/animes/"
                     
                files_match = re.search(r'files:(\[\{.*?\}\])', data_text)
                if files_match:
                     files_str = files_match.group(1)
                     vid_matches = re.finditer(r'resolution:(\d+),file:"([^"]+)"', files_str)
                     for v in vid_matches:
                         res = f"{v.group(1)}p"
                         url = v.group(2)
                         
                         if not url.startswith("http"):
                             url = f"{dynamic_cdn}{url}"
                             
                         video_data = {
                             "url": url,
                             "quality": res,
                             "format": "mp4",
                             "size": None,
                             "referer": anime_url,
                             "provider_data": {
                                 "episode_url": episode_url,
                                 "source_type": "html5",
                                 "fansub": "OpenAnime"
                             }
                         }
                         video_urls.append(video_data)
                         
                # Fallback for older/different formats
                if not video_urls:
                    matches = re.finditer(r'"videoUrl":"([^"]+)".*?"fansubName":"([^"]+)"', data_text)
                    for match in matches:
                        vid_url = match.group(1).replace("\\u002F", "/")
                        if "%CDN_HOST%" in vid_url:
                            vid_url = vid_url.replace("%CDN_HOST%", CDN_HOST)
                            
                        try:
                            fansub_name = match.group(2).encode('ascii', 'ignore').decode('unicode_escape')
                        except:
                            fansub_name = match.group(2)
                        
                        quality = "720p"
                        if "1080p" in vid_url:
                            quality = "1080p"
                        elif "480p" in vid_url:
                            quality = "480p"
                            
                        video_urls.append({
                            "url": vid_url,
                            "quality": quality,
                            "format": self._get_video_format(vid_url),
                            "size": None,
                            "referer": anime_url,
                            "provider_data": {
                                 "episode_url": episode_url,
                                 "source_type": "html5",
                                 "fansub": fansub_name
                            }
                        })
                
            # Genel raw string taraması (Eğer yukarıdaki regex hata verdiyse)
            if not video_urls:
                 patterns = [
                     r'https?://[a-zA-Z0-9\-\.]+\.eu\.org/[^"\'\s]+\.mp4',
                     r'https?://[a-zA-Z0-9\-\.]+\.openani\.me/[^"\'\s]+\.m3u8'
                 ]
                 for pattern in patterns:
                     vid_matches = re.finditer(pattern, html)
                     for i, match in enumerate(vid_matches):
                         vid_url = match.group(0).replace("\\u002F", "/")
                         if "%CDN_HOST%" in vid_url:
                             vid_url = vid_url.replace("%CDN_HOST%", CDN_HOST)
                             
                         quality = "720p"
                         if "1080p" in vid_url:
                             quality = "1080p"
                         elif "480p" in vid_url:
                             quality = "480p"
                             
                         video_data = {
                             "url": vid_url,
                             "quality": quality,
                             "format": self._get_video_format(vid_url),
                             "size": None,
                             "referer": anime_url,
                             "provider_data": {
                                 "episode_url": episode_url,
                                 "source_type": "html5",
                                 "fansub": f"Kaynak {i+1}"
                             }
                         }
                         video_urls.append(video_data)

        except Exception as e:
            print(f"[OpenAni] Video parse hatası: {e}")

        # Kaliteye göre sırala (yüksekten düşüğe)
        quality_order = {'1080p': 4, '720p': 3, '480p': 2, '360p': 1}
        video_urls.sort(key=lambda x: quality_order.get(x['quality'], 0), reverse=True)

        return video_urls

    def _get_video_format(self, url: str) -> str:
        """URL'den video formatını belirle."""
        if '.mp4' in url.lower():
            return 'mp4'
        elif '.m3u8' in url.lower():
            return 'm3u8'
        elif '.webm' in url.lower():
            return 'webm'
        elif '.avi' in url.lower():
            return 'avi'
        elif '.mkv' in url.lower():
            return 'mkv'
        else:
            return 'unknown'

    def create_anime_object(self, anime_data: Dict[str, Any]) -> Anime:
        """Adapter verisinden Anime objesi oluştur."""
        slug = anime_data.get('provider_data', {}).get('slug', 'bilinmeyen-anime')
        anime = Anime(slug)

        anime.info["Özet"] = anime_data.get('description', '')
        anime.info["Resim"] = anime_data.get('image', '')
        anime.info["Anime Türü"] = anime_data.get('genres', [])
        anime.info["Bölüm Sayısı"] = anime_data.get('episodes', 0)
        anime.info["Puanı"] = anime_data.get('score', 0.0)

        if anime.title is None:
            anime.title = anime_data.get('title', 'Bilinmeyen Anime')

        return anime

    def create_episode_object(self, episode_data: Dict[str, Any], anime: Anime) -> Bolum:
        """Adapter verisinden Bolum objesi oluştur."""
        slug = episode_data.get('provider_data', {}).get('episode_id', 'bolum-0')
        title = episode_data.get('title', f"Bölüm {episode_data.get('episode_number', 0)}")
        
        bolum = Bolum(slug=slug, anime=anime, title=title)
        return bolum

# Geriye dönük uyumluluk için eski metotları sarmalayan fonksiyonlar 
# (Zorunlu değilse Adapter classı direkt kullanılabilir, ancak turkanime_api yapısı dışarıya fonksiyonlar ihraç eder)

adapter = OpenAniAdapter()

def search_openani(query: str, limit: int = 20, timeout: int = 30) -> List[Tuple[str, str]]:
    results = adapter.search_anime(query)
    # result: [{'url': ..., 'title': ..., 'provider_data': {'item_id': slug}}]
    return [(res["provider_data"]["item_id"], res["title"]) for res in results[:limit]]

def get_anime_episodes(slug: str, timeout: int = 30) -> List[Tuple[str, str]]:
    anime_url = adapter.PROVIDER_CONFIG["anime_url"].format(anime_id=slug)
    anime_data = adapter.get_anime_details(anime_url)
    if not anime_data:
        return []
    
    episodes = adapter.get_episodes(anime_data)
    # result: [{'url': ..., 'title': ..., 'provider_data': {'episode_id': ep_slug}}]
    return [(ep["provider_data"]["episode_id"], ep["title"]) for ep in episodes]

def get_episode_streams(episode_slug: str, timeout: int = 30) -> List[Dict[str, str]]:
    episode_url = f"{BASE_URL}/anime/{episode_slug}"
    anime_slug = episode_slug.split("/")[0]
    anime_url = adapter.PROVIDER_CONFIG["anime_url"].format(anime_id=anime_slug)
    
    episode_data = {
        "url": episode_url,
        "provider_data": {
             "anime_url": anime_url
        }
    }
    
    videos = adapter.get_video_urls(episode_data)
    # result: [{'url': ..., 'quality': ..., 'provider_data': {'fansub': fansub_name}}]
    
    streams = []
    for vid in videos:
        streams.append({
            "url": vid["url"],
            "label": f"{vid['provider_data'].get('fansub', 'Video')} - {vid['quality']} ({vid['format']})",
            "type": "hls" if vid["format"] == "m3u8" else "direct",
            "referer": vid.get("referer")
        })
    return streams

class OpenAniAnime:
    """Class definition for backward compatibility if needed."""
    pass
