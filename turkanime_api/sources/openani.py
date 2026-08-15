"""
OpenAnime Provider Adapter.

Bu modül, openani.me kaynağının içeriklerini çekmek için kullanılır.
Site SvelteKit ile Server-Side Rendering (SSR) kullandığından, arama ve bölüm 
bilgileri HTML içerisindeki JSON datalarından parse edilir.
"""
from __future__ import annotations

import os
import re
import json
import time
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING
from bs4 import BeautifulSoup

# Koşulsuz import: eskiden yalnızca CF bypass yoksa içe aktarılıyordu, yani
# `HAS_CF_BYPASS` yolu dışında `requests` adı hiç tanımlı olmuyordu. Statik
# çözümleyici de bunu "atanmadan kullanım" diye işaretliyordu; koşulsuz import
# hem uyarıyı hem kırılganlığı bitiriyor (requests zaten zorunlu bağımlılık).
import requests

# `..objects` yt_dlp'yi (71 modül, ~0.5 sn) ve `..bypass` üzerinden Crypto'yu
# çeker. `Anime`/`Bolum` bu modülde yalnızca `OpenAniAdapter`'ın iki fabrika
# metodunda kullanılıyor; arama/bölüm/stream uçlarını çağıran sunucu tarayıcısı
# onlara hiç dokunmuyor. `from __future__ import annotations` sayesinde
# anotasyonlar string olduğu için tip yalnızca denetleyiciye görünüyor,
# çalışma anında import metodun içinde yapılıyor.
if TYPE_CHECKING:
    from ..objects import Anime, Bolum

# CF Bypass modülünü içe aktar
try:
    from turkanime_api.common.cf_bypass import CFSession, CFBypassError, get_cf_session
    HAS_CF_BYPASS = True
except ImportError:
    HAS_CF_BYPASS = False

# Konfigürasyon
BASE_URL = "https://openani.me"

# Sayfadan CDN_LINK yakalanamadığında kullanılan **varsayılan** CDN kökü ve
# `%CDN_HOST%` yer tutucusunun karşılığı. Adres sitenin barındırıcısına ait ve
# değişebiliyor; sabit kodlu kaldığı sürece CDN taşındığında tek çare sürüm
# çıkmaktı. Ortam değişkeniyle ezilebilsin (aynı desen: `animedepo.py`
# ORTAM_ANAHTARI / `taban_url()`).
CDN_HOST = "https://de2---vn-t9g4tsan-5qcl.yeshi.eu.org"
CDN_ORTAM_ANAHTARI = "TURKANIME_OPENANI_CDN"

# `search/episodes/streams` fonksiyonlarının varsayılan istek süresi.
VARSAYILAN_TIMEOUT = 30


def cdn_host() -> str:
    """Kullanılacak CDN kökü: ortam değişkeni varsa o, yoksa `CDN_HOST`.

    Değer her çağrıda okunuyor (bir `os.environ` bakışı, ölçülebilir maliyeti
    yok); böylece süreç ortasında değişen ortam da geçerli oluyor ve
    `animedepo.taban_url()`teki gibi bir sıfırlama fonksiyonu gerekmiyor.
    Sondaki `/` atılıyor: yer tutucu `%CDN_HOST%/animes/...` biçiminde
    dolduruluyor, çift eğik çizgi bazı CDN'lerde 404 demek.
    """
    ortam = (os.environ.get(CDN_ORTAM_ANAHTARI) or "").strip().rstrip("/")
    return ortam or CDN_HOST

# OpenAni tokens - kullanıcı tarafından sağlanmalı
OPENANI_TOKEN = None
OPENANI_REFRESH_TOKEN = None

def set_openani_tokens(token: str, refresh_token: str):
    """OpenAni API tokens'ı ayarla."""
    global OPENANI_TOKEN, OPENANI_REFRESH_TOKEN
    OPENANI_TOKEN = token
    OPENANI_REFRESH_TOKEN = refresh_token

def _get_cf_session(timeout: int = VARSAYILAN_TIMEOUT) -> Any:
    """CF session'ı döndür (singleton)."""
    if HAS_CF_BYPASS:
        session = CFSession(timeout=timeout)
        # `CFSession.cookies` bir dict property ve KOPYA döndürüyor; burada
        # `.set(...)` çağırmak dict'te öyle bir metot olmadığı için token
        # ayarlanır ayarlanmaz AttributeError atıyordu (ve olsaydı bile yazma
        # kopyada kalıp kaybolurdu). Yazma yolu `set_cookie`.
        if OPENANI_TOKEN:
            session.set_cookie("token", OPENANI_TOKEN)
        if OPENANI_REFRESH_TOKEN:
            session.set_cookie("refreshToken", OPENANI_REFRESH_TOKEN)
        return session
    else:
        # Fallback: normal session oluştur ve cookieleri ekle
        session = requests.Session()
        if OPENANI_TOKEN:
            session.cookies.set("token", OPENANI_TOKEN, domain=".openani.me")
        if OPENANI_REFRESH_TOKEN:
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
        try:
            array_str = match.group(1)
            data_array = json.loads(array_str)
            if isinstance(data_array, list) and len(data_array) > 0:
                first_item = data_array[0]
                if isinstance(first_item, dict):
                    return first_item
        except (json.JSONDecodeError, IndexError, AttributeError):
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
        "timeout": VARSAYILAN_TIMEOUT,
    }

    def __init__(self, timeout: Optional[int] = None):
        # `PROVIDER_CONFIG["timeout"]` yıllarca hiç okunmuyordu: istekler ya
        # sabit sürelerle (15/10) ya da HİÇ timeout'suz atılıyordu. Artık
        # adaptörün istek süresi burada ve bütün isteklere o gidiyor.
        self.timeout = int(timeout or self.PROVIDER_CONFIG["timeout"])
        self.session = _get_cf_session(self.timeout)
        self.last_request = 0

    def _rate_limit_wait(self):
        """Rate limit kontrolü."""
        elapsed = time.time() - self.last_request
        if elapsed < self.PROVIDER_CONFIG['rate_limit']:
            time.sleep(self.PROVIDER_CONFIG['rate_limit'] - elapsed)
        self.last_request = time.time()

    def _slugify(self, query: str) -> List[str]:
        """Olası slug varyantlarını üret. openani slugları küçük harf + tire."""
        import unicodedata
        q = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode("ascii")
        q = q.lower().strip()
        base = re.sub(r"[^a-z0-9]+", "-", q).strip("-")
        if not base:
            return []
        variants = [base]
        # Genel devam/biçim ekleri. "-shippuden" buradan çıkarıldı: yalnızca
        # Naruto'ya özgü, üstelik sitenin yazımı "shippuuden" — yani her sorguya
        # eklenen ("one-piece-shippuden" gibi) anlamsız bir probe'du.
        for suffix in ("-season-1", "-1", "-2", "-tv", "-the-movie"):
            variants.append(base + suffix)
        # Uzun adlar sık sık kısaltılmış slug alıyor ("shingeki-no-kyojin").
        # İlk iki kelimeyi de dene; tek kelimelik sorguda kopya üretmiyor.
        parcalar = base.split("-")
        if len(parcalar) > 2:
            variants.append("-".join(parcalar[:2]))
        return variants

    def _light_get(self, url: str, headers: dict):
        """Hafif istek — CF bypass'a takılmadan direkt curl_cffi kullan.

        OpenAnime'ın /anime ve /explore HTML sayfaları CF challenge fırlatmıyor,
        ama mevcut CFSession her isteği selenium/flaresolverr ile çözmeye
        çalışıyor — bu hem yavaş hem ortama bağımlı. Burada plain curl_cffi
        kullanarak hızlı doğrudan istek atıyoruz.
        """
        try:
            from curl_cffi import requests as _curl
            sess = getattr(self, "_light_session", None)
            if sess is None:
                sess = _curl.Session(impersonate="chrome110")
                self._light_session = sess
            return sess.get(url, headers=headers, timeout=self.timeout,
                            allow_redirects=False)
        except Exception:
            # Yedek yolda da süre veriyoruz: `requests.Session` varsayılanı
            # SONSUZ bekler (CFSession kendi varsayılanını koyar ama düz
            # requests'e düşülen dalda kimse koymuyordu).
            return self.session.get(url, headers=headers, allow_redirects=False,
                                    timeout=self.timeout)

    def _probe_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Doğrudan /anime/<slug> URL'ini deneyip varsa anime kartını döndür.

        Varlık sinyali sayfa **başlığı**: gerçek anime "One Piece | OpenAnime",
        olmayan slug ise HTTP 500 + "undefined | OpenAnime" döndürüyor.

        Eskiden doğrulama `const data` içinde slug aramaktı. O blok sayfaya
        özel değil — sitenin her sayfasında duran 46 kayıtlık "son eklenenler"
        katalogu. Sonuç iki yönlü yanlıştı: kataloğun içindeki her slug hangi
        URL istenirse istensin "bulundu" sayılıyor, dışındaki her anime ise
        sitede gerçekten varken bulunamıyordu. Ölçüm: naruto, bleach,
        jujutsu-kaisen, death-note, shingeki-no-kyojin — hepsi sitede var,
        hiçbiri eski kontrolden geçemiyordu. Arama pratikte 46 animeye
        kilitlenmişti.
        """
        url = self.PROVIDER_CONFIG["anime_url"].format(anime_id=slug)
        headers = {"User-Agent": self.PROVIDER_CONFIG["user_agent"]}
        try:
            r = self._light_get(url, headers=headers)
            if r.status_code != 200:
                return None
            html = r.text if hasattr(r, "text") else r.content.decode("utf-8", "ignore")
            title = self._sayfa_basligi(html)
            if not title:
                return None
            return {
                "title": title,
                "url": url,
                "image": "",
                "provider_data": {"item_id": slug, "search_query": slug}
            }
        except Exception:
            return None

    @staticmethod
    def _sayfa_basligi(html: str) -> Optional[str]:
        """`<title>` içinden anime adı; sayfa geçersizse ``None``.

        Site adı ayracı olarak hem "|" hem "-" kullanılabiliyor; adın kendisi
        "|" içermediği için ilk parça güvenli.
        """
        m = re.search(r"<title[^>]*>([^<]*)</title>", html, re.IGNORECASE)
        if not m:
            return None
        ham = m.group(1).strip()
        ad = ham.split("|")[0].strip()
        if not ad or ad.lower() in ("undefined", "null", "openanime"):
            return None
        return ad

    def search_anime(self, query: str) -> List[Dict[str, Any]]:
        """Anime arama işlemi.

        openani.me'nin gerçek arama API'si Vanguard auth gerektirir ve dışarıdan
        erişilemez. İki kademeli yedek strateji kullanıyoruz:
        1) Sorguyu slug'a çevirip /anime/<slug> URL'lerini doğrudan probe et.
        2) /explore sayfasının SSR ettiği popüler katalog içinden filtrele.
        """
        self._rate_limit_wait()
        results: List[Dict[str, Any]] = []
        slugs_found: set = set()

        # 1) Doğrudan slug probe — en güvenilir yöntem
        for variant in self._slugify(query):
            if variant in slugs_found:
                continue
            hit = self._probe_slug(variant)
            if hit:
                slugs_found.add(variant)
                results.append(hit)
                if len(results) >= 5:
                    break

        # 2) Explore sayfası fallback — popüler liste içinden filtre
        search_url = self.PROVIDER_CONFIG['search_url'].format(query=query)
        headers = {"User-Agent": self.PROVIDER_CONFIG["user_agent"]}

        try:
            response = self._light_get(search_url, headers=headers)
            if response.status_code != 200:
                return results

            html = response.text if hasattr(response, 'text') else response.content.decode('utf-8')
            
            # explore sayfası SSR ile popüler animeleri gömüyor — query bazlı
            # server-side filtreleme YOK. O yüzden parse edip client-side filtrele.
            query_norm = query.lower().strip()
            query_slug = query_norm.replace(" ", "-")

            def _matches(title: str, slug: str) -> bool:
                if not query_norm:
                    return True
                t = title.lower()
                s = slug.lower()
                return query_norm in t or query_slug in s

            # Svelte objesi içinden `english` ve `slug` değerlerini RegExp ile parse ediyoruz
            data_match = re.search(r'const data = (\[.*?\]);', html, re.DOTALL)
            if data_match:
                data_text = data_match.group(1)
                # Daha güvenli regex: beraber bulunan english/romaji/turkish ve slug
                matches = re.finditer(r'\{[^}]*?(?:english|romaji|turkish):"([^"]+)"[^}]*?slug:"([^"]+)"[^}]*?\}', data_text)
                for match in matches:
                    title = match.group(1)
                    slug = match.group(2)

                    try:
                        title = title.encode('ascii', 'ignore').decode('unicode_escape')
                    except Exception:
                        pass

                    if slug in slugs_found:
                        continue
                    if not _matches(title, slug):
                        continue
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

                # Fallback: başlık eşleşmesi yoksa sadece slug üzerinden dene
                if not results:
                    for match in re.finditer(r'slug:"([^"]+)"', data_text):
                        slug = match.group(1)
                        if slug in slugs_found or len(slug) <= 2:
                            continue
                        if not _matches(slug.replace("-", " "), slug):
                            continue
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
            return results

    def get_anime_details(self, anime_url: str) -> Optional[Dict[str, Any]]:
        """Anime detaylarını getir."""
        self._rate_limit_wait()
        headers = {"User-Agent": self.PROVIDER_CONFIG["user_agent"]}
        
        try:
            # timeout ŞART: düz `requests.Session` yedeğine düşüldüğünde
            # varsayılan sonsuzdur, yani yanıt vermeyen site GUI'yi asardı.
            response = self.session.get(anime_url, headers=headers,
                                        timeout=self.timeout)
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
            if episode_count == 0:
                print(f"[OpenAni] {anime_url}: Bölüm sayısı bulunamadı")
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
        provider_data = episode_data.get('provider_data', {})
        anime_url = provider_data.get('anime_url') if provider_data else None
        if not anime_url:
            anime_url = BASE_URL

        headers = {
            "User-Agent": self.PROVIDER_CONFIG["user_agent"],
            "Referer": anime_url
        }

        video_urls = []

        try:
            response = self.session.get(episode_url, headers=headers,
                                        timeout=self.timeout)
            if response.status_code != 200:
                print(f"[OpenAni] Video hatası: HTTP {response.status_code}")
                return []

            html = response.text if hasattr(response, 'text') else response.content.decode('utf-8')
            # Svelte data array içinden extract
            data_match = re.search(r'const data = (\[.*?\]);', html, re.DOTALL)
            if data_match:
                data_text = data_match.group(1)
                
                # try to find the actual cdn host dynamically
                cdn = cdn_host()
                cdn_match = re.search(r'CDN_LINK:"([^"]+)"', data_text)
                if cdn_match:
                     dynamic_cdn = cdn_match.group(1).replace("%CDN_HOST%", cdn)
                else:
                     dynamic_cdn = f"{cdn}/animes/"
                     
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
                            vid_url = vid_url.replace("%CDN_HOST%", cdn_host())

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
                             vid_url = vid_url.replace("%CDN_HOST%", cdn_host())

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
        from ..objects import Anime  # tembel: modül düzeyinde yt_dlp çekiyor
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
        from ..objects import Bolum  # tembel: modül düzeyinde yt_dlp çekiyor
        slug = episode_data.get('provider_data', {}).get('episode_id', 'bolum-0')
        title = episode_data.get('title', f"Bölüm {episode_data.get('episode_number', 0)}")
        
        bolum = Bolum(slug=slug, anime=anime, title=title)
        return bolum

# Geriye dönük uyumluluk için eski metotları sarmalayan fonksiyonlar 
# (Zorunlu değilse Adapter classı direkt kullanılabilir, ancak turkanime_api yapısı dışarıya fonksiyonlar ihraç eder)

adapter = OpenAniAdapter()

# Varsayılandan farklı süre isteyen çağrılar için adaptör önbelleği: her istekte
# yeni `CFSession` kurmak (ayar dosyası okuma + oturum kurulumu) gereksiz.
_ozel_adaptorler: Dict[int, OpenAniAdapter] = {}


def _adaptor(timeout: Optional[int]) -> OpenAniAdapter:
    """İstenen istek süresine sahip adaptör.

    Aşağıdaki üç dışa açık fonksiyonun `timeout` parametresi eskiden HİÇBİR
    yere gitmiyordu — üç gövdede de adı geçmiyordu, istekler sabit sürelerle
    (15/10) veya süresiz atılıyordu. İmza yalan söylüyordu; çağıran "10 saniye"
    dediğinde 30 saniye bekleyebiliyordu. Artık süre adaptöre veriliyor ve
    oradan bütün HTTP isteklerine geçiyor.

    Varsayılan süre paylaşılan tekil adaptörü kullanır; testlerin
    `oa.adapter`ı yamalayabilmesi de buna bağlı.
    """
    if timeout is None:
        return adapter
    sure = int(timeout)
    if sure == adapter.timeout:
        return adapter
    ozel = _ozel_adaptorler.get(sure)
    if ozel is None:
        ozel = _ozel_adaptorler[sure] = OpenAniAdapter(timeout=sure)
    return ozel


def search_openani(query: str, limit: int = 20,
                   timeout: int = VARSAYILAN_TIMEOUT) -> List[Tuple[str, str]]:
    results = _adaptor(timeout).search_anime(query)
    # result: [{'url': ..., 'title': ..., 'provider_data': {'item_id': slug}}]
    return [(res["provider_data"]["item_id"], res["title"]) for res in results[:limit]]

def get_anime_episodes(slug: str, timeout: int = VARSAYILAN_TIMEOUT) -> List[Tuple[str, str]]:
    ada = _adaptor(timeout)
    anime_url = ada.PROVIDER_CONFIG["anime_url"].format(anime_id=slug)
    anime_data = ada.get_anime_details(anime_url)
    if not anime_data:
        return []

    episodes = ada.get_episodes(anime_data)
    # result: [{'url': ..., 'title': ..., 'provider_data': {'episode_id': ep_slug}}]
    return [(ep["provider_data"]["episode_id"], ep["title"]) for ep in episodes]

# Stream uçlarını dönmeden önce yokla. Kapatmak için `False` yap (test/CLI).
UCLARI_DOGRULA = True

# JSON/HTML gövdesi "çalışan stream" değildir; CDN hatayı da 200 ile verebilir.
_MEDYA_TIPLERI = ("video/", "audio/", "application/x-mpegurl",
                  "application/vnd.apple.mpegurl", "application/dash+xml",
                  "application/octet-stream", "binary/octet-stream")

# Yoklama üst sınırı. Sayfa isteğinden ayrı ve bilerek kısa: istenen tek şey
# 1 baytlık yanıt, uç başına saniyelerce beklemenin karşılığı yok.
YOKLAMA_TIMEOUT = 10


def _uc_calisiyor(url: str, referer: Optional[str] = None,
                  timeout: int = YOKLAMA_TIMEOUT) -> Optional[bool]:
    """Uç gerçekten veri veriyor mu? 1 baytlık Range isteğiyle yokla.

    ``True``  = medya geldi
    ``False`` = kesin ölü (404 vb. ya da JSON/HTML hata gövdesi)
    ``None``  = karar verilemedi (ağ hatası) — uç atılmamalı

    Ölçüm: ölü uç için ~0.08 sn. Oynatıcının açılıp başarısız olmasından
    kat kat ucuz ve kullanıcıya sebebini söyleyebiliyoruz.
    """
    basliklar = {
        "User-Agent": OpenAniAdapter.PROVIDER_CONFIG["user_agent"],
        "Range": "bytes=0-0",
    }
    if referer:
        basliklar["Referer"] = referer
    try:
        from curl_cffi import requests as _curl
        yanit = _curl.get(url, headers=basliklar, timeout=timeout)
    except Exception:
        return None
    if yanit.status_code not in (200, 206):
        return False
    tur = (yanit.headers.get("Content-Type") or "").lower()
    if not tur:
        return None
    return any(tur.startswith(t) for t in _MEDYA_TIPLERI)


def get_episode_streams(episode_slug: str,
                        timeout: int = VARSAYILAN_TIMEOUT) -> List[Dict[str, str]]:
    """Bölümün stream uçları.

    `timeout` bölüm sayfası isteği içindir; uçların canlılık yoklaması ayrıca
    `YOKLAMA_TIMEOUT` ile sınırlı (uç başına 1 baytlık istek).
    """
    ada = _adaptor(timeout)
    episode_url = f"{BASE_URL}/anime/{episode_slug}"
    anime_slug = episode_slug.split("/")[0]
    anime_url = ada.PROVIDER_CONFIG["anime_url"].format(anime_id=anime_slug)


    episode_data = {
        "url": episode_url,
        "provider_data": {
            "anime_url": anime_url
        }
    }
    
    videos = ada.get_video_urls(episode_data)
    # result: [{'url': ..., 'quality': ..., 'provider_data': {'fansub': fansub_name}}]
    
    streams = []
    for vid in videos:
        streams.append({
            "url": vid["url"],
            "label": f"{vid['provider_data'].get('fansub', 'Video')} - {vid['quality']} ({vid['format']})",
            "type": "hls" if vid["format"] == "m3u8" else "direct",
            "referer": vid.get("referer")
        })

    if not streams or not UCLARI_DOGRULA:
        return streams

    # Sayfadan çıkarılan CDN yolu ölü olabiliyor: site "File with such name
    # does not exist" (HTTP 404) döndürüyor. Eskiden bu uçlar olduğu gibi
    # veriliyordu; kullanıcı oynat'a basıp sebepsiz bir hata görüyordu.
    # Çalışanları öne al, hiçbiri çalışmıyorsa sebebini SÖYLE.
    calisan, olu = [], []
    for akis in streams:
        if _uc_calisiyor(akis["url"], akis.get("referer")) is False:
            olu.append(akis)
        else:
            calisan.append(akis)

    if calisan:
        return calisan

    # Ölü uçları yine de döndürüyoruz: yoklama oynatıcıdan farklı başlıklar
    # kullanıyor, yanılma payı var. Ama kullanıcı artık kör değil.
    print("[OpenAnime] Stream uçlarının hiçbiri yanıt vermiyor (CDN 404). "
          "Bu kaynak giriş yapmış oturum istiyor olabilir — Ayarlar'dan "
          "OpenAnime token'ını girmeyi deneyin.")
    return olu

class OpenAniAnime:
    """Class definition for backward compatibility if needed."""
    pass
