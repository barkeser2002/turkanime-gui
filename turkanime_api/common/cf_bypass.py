"""
Cloudflare Bypass Modülü

Bu modül, Cloudflare koruması olan sitelere erişim sağlamak için
farklı yöntemleri bir arada sunar:

1. curl_cffi - Firefox/Chrome TLS fingerprint taklidi
2. cloudscraper - JS Challenge çözümü
3. FlareSolverr - Uzak CF çözücü (headless browser sunucusu)
4. undetected-chromedriver - Selenium tabanlı tam bypass
5. Normal requests - Fallback

Kullanım:
    from turkanime_api.common.cf_bypass import CFSession

    session = CFSession()
    response = session.get("https://example.com")
"""
from __future__ import annotations

import time
import random
from typing import Optional, Dict, Any
from urllib.parse import urlparse

# curl_cffi - TLS fingerprint taklidi için
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# cloudscraper - JS Challenge bypass için
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# requests - Fallback için
import requests

# undetected-chromedriver - Son çare Selenium bypass
try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False


# User-Agent listesi (rotasyon için)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]


class CFBypassError(Exception):
    """Cloudflare bypass başarısız olduğunda fırlatılır."""
    pass


class CFSession:
    """
    Cloudflare korumalı sitelere erişim için akıllı session yöneticisi.
    
    Sırasıyla şu yöntemleri dener:
    1. curl_cffi (Firefox TLS fingerprint)
    2. cloudscraper (JS Challenge)
    3. FlareSolverr (uzak headless browser)
    4. undetected-chromedriver (Selenium)
    5. Normal requests (fallback)
    """

    # Varsayılan FlareSolverr adresi
    DEFAULT_FLARESOLVERR_URL = "http://node-kyb.bariskeser.com:8191"

    def __init__(
        self,
        impersonate: str = "chrome110",
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        flaresolverr_url: Optional[str] = None,
    ):
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.flaresolverr_url = flaresolverr_url or self.DEFAULT_FLARESOLVERR_URL
        
        self._curl_session: Optional[Any] = None
        self._cloud_session: Optional[Any] = None
        self._uc_driver: Optional[Any] = None
        self._cookies: Dict[str, str] = {}
        self._last_method: Optional[str] = None
        self._flaresolverr_user_agent: Optional[str] = None
        
        # Hangi yöntemlerin mevcut olduğunu kontrol et
        self._available_methods = []
        if HAS_CURL_CFFI:
            self._available_methods.append("curl_cffi")
        if HAS_CLOUDSCRAPER:
            self._available_methods.append("cloudscraper")
        self._available_methods.append("flaresolverr")  # Her zaman denenebilir
        if HAS_UC:
            self._available_methods.append("undetected_chrome")
        self._available_methods.append("requests")  # Her zaman mevcut

    def _get_curl_session(self):
        """curl_cffi session'ı lazy-load et."""
        if self._curl_session is None and HAS_CURL_CFFI:
            self._curl_session = curl_requests.Session(
                impersonate=self.impersonate,
                allow_redirects=True,
            )
        return self._curl_session

    def _get_cloud_session(self):
        """cloudscraper session'ı lazy-load et."""
        if self._cloud_session is None and HAS_CLOUDSCRAPER:
            self._cloud_session = cloudscraper.create_scraper(
                browser={
                    "browser": "firefox",
                    "platform": "windows",
                    "desktop": True,
                },
                delay=5,
            )
        return self._cloud_session

    def _get_uc_driver(self):
        """undetected-chromedriver'ı lazy-load et."""
        if self._uc_driver is None and HAS_UC:
            options = uc.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
            self._uc_driver = uc.Chrome(options=options)
        return self._uc_driver

    def _try_curl_cffi(self, url: str, headers: Dict[str, str], method: str = "GET", **kwargs) -> Optional[requests.Response]:
        """curl_cffi ile istek at."""
        if not HAS_CURL_CFFI:
            return None
        
        # Desteklenen impersonate değerleri (öncelik sırasına göre)
        impersonate_options = [
            self.impersonate,
            "chrome110",
            "chrome107", 
            "chrome104",
            "chrome101",
            "chrome100",
            "chrome99",
            "edge101",
            "edge99",
            "safari15_5",
            "safari15_3",
        ]
        
        for imp in impersonate_options:
            try:
                session = curl_requests.Session(
                    impersonate=imp,
                    allow_redirects=True,
                )
                
                if method.upper() == "GET":
                    resp = session.get(url, headers=headers, timeout=self.timeout, **kwargs)
                else:
                    resp = session.post(url, headers=headers, timeout=self.timeout, **kwargs)
                
                if resp.status_code == 200:
                    self._last_method = f"curl_cffi ({imp})"
                    # Çerezleri kaydet (curl_cffi dict döner)
                    try:
                        cookies_dict = session.cookies.get_dict() if hasattr(session.cookies, 'get_dict') else dict(session.cookies)
                        self._cookies.update(cookies_dict)
                    except Exception:
                        pass
                    return resp
                elif resp.status_code == 403:
                    # CF engeli, sonraki impersonate'i dene
                    continue
                    
            except Exception as e:
                # Bu impersonate desteklenmiyor, sonrakini dene
                if "not supported" in str(e).lower():
                    continue
                # Diğer hatalar için logla ve devam et  
                if "str" not in str(e) and "attribute" not in str(e):
                    print(f"[CF Bypass] curl_cffi ({imp}) hatası: {e}")
                continue
        
        return None

    def _try_cloudscraper(self, url: str, headers: Dict[str, str], method: str = "GET", **kwargs) -> Optional[requests.Response]:
        """cloudscraper ile istek at."""
        if not HAS_CLOUDSCRAPER:
            return None
        
        try:
            session = self._get_cloud_session()
            if method.upper() == "GET":
                resp = session.get(url, headers=headers, timeout=self.timeout, **kwargs)
            else:
                resp = session.post(url, headers=headers, timeout=self.timeout, **kwargs)
            
            if resp.status_code == 200:
                self._last_method = "cloudscraper"
                self._cookies.update(session.cookies.get_dict())
                return resp
        except cloudscraper.exceptions.CloudflareChallengeError as e:
            print(f"[CF Bypass] cloudscraper JS challenge hatası: {e}")
        except Exception as e:
            print(f"[CF Bypass] cloudscraper hatası: {e}")
        return None

    def _try_flaresolverr(self, url: str, method: str = "GET", post_data: Optional[str] = None) -> Optional[requests.Response]:
        """FlareSolverr ile CF bypass dene.
        
        FlareSolverr uzak bir headless browser sunucusudur.
        API: POST http://host:8191/v1
        """
        try:
            api_url = f"{self.flaresolverr_url.rstrip('/')}/v1"
            payload: Dict[str, Any] = {
                "cmd": f"request.{method.lower()}",
                "url": url,
                "maxTimeout": 60000,
            }
            if method.upper() == "POST" and post_data:
                payload["postData"] = post_data

            resp = requests.post(api_url, json=payload, timeout=65)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                print(f"[CF Bypass] FlareSolverr durum hatası: {data.get('message', 'bilinmeyen')}")
                return None

            solution = data.get("solution", {})
            sol_status = solution.get("status", 0)

            if sol_status != 200:
                print(f"[CF Bypass] FlareSolverr HTTP {sol_status}")
                return None

            # Çerezleri kaydet
            for cookie in solution.get("cookies", []):
                name = cookie.get("name", "")
                value = cookie.get("value", "")
                if name and value:
                    self._cookies[name] = value

            # User-Agent'i sakla
            ua = solution.get("userAgent")
            if ua:
                self._flaresolverr_user_agent = ua

            # Sahte Response nesnesi oluştur
            html_content = solution.get("response", "")
            fake_resp = requests.Response()
            fake_resp.status_code = 200
            fake_resp._content = html_content.encode("utf-8") if isinstance(html_content, str) else html_content
            fake_resp.headers["Content-Type"] = "text/html; charset=utf-8"
            fake_resp.url = solution.get("url", url)

            self._last_method = "flaresolverr"
            return fake_resp

        except requests.exceptions.ConnectionError:
            print("[CF Bypass] FlareSolverr sunucusuna bağlanılamadı")
        except requests.exceptions.Timeout:
            print("[CF Bypass] FlareSolverr zaman aşımı")
        except Exception as e:
            print(f"[CF Bypass] FlareSolverr hatası: {e}")
        return None

    def _try_undetected_chrome(self, url: str, headers: Dict[str, str]) -> Optional[str]:
        """undetected-chromedriver ile sayfa al (sadece GET)."""
        if not HAS_UC:
            return None
        
        try:
            driver = self._get_uc_driver()
            driver.get(url)
            
            # CF challenge için bekle
            time.sleep(5)
            
            # Sayfa yüklenene kadar bekle
            for _ in range(10):
                if "Just a moment" not in driver.page_source:
                    break
                time.sleep(1)
            
            self._last_method = "undetected_chrome"
            
            # Çerezleri al
            for cookie in driver.get_cookies():
                self._cookies[cookie["name"]] = cookie["value"]
            
            return driver.page_source
        except Exception as e:
            print(f"[CF Bypass] undetected-chromedriver hatası: {e}")
        return None

    def _try_requests_fallback(self, url: str, headers: Dict[str, str], method: str = "GET", **kwargs) -> Optional[requests.Response]:
        """Normal requests ile istek at (fallback)."""
        try:
            headers["User-Agent"] = random.choice(USER_AGENTS)
            if self._cookies:
                kwargs.setdefault("cookies", {}).update(self._cookies)
            
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, timeout=self.timeout, **kwargs)
            else:
                resp = requests.post(url, headers=headers, timeout=self.timeout, **kwargs)
            
            if resp.status_code == 200:
                self._last_method = "requests"
                return resp
        except Exception as e:
            print(f"[CF Bypass] requests hatası: {e}")
        return None

    def get(self, url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
        """
        GET isteği at, CF bypass yöntemlerini sırayla dene.
        
        Args:
            url: Hedef URL
            headers: İsteğe bağlı HTTP başlıkları
            **kwargs: Ek requests parametreleri
        
        Returns:
            requests.Response benzeri nesne
        
        Raises:
            CFBypassError: Tüm yöntemler başarısız olursa
        """
        headers = headers or {}
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        headers.setdefault("Accept-Language", "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
        
        last_error = None
        
        for attempt in range(self.max_retries):
            # 1. curl_cffi dene
            resp = self._try_curl_cffi(url, headers, "GET", **kwargs)
            if resp is not None:
                return resp
            
            # 2. cloudscraper dene
            resp = self._try_cloudscraper(url, headers, "GET", **kwargs)
            if resp is not None:
                return resp
            
            # 3. FlareSolverr dene
            resp = self._try_flaresolverr(url, "GET")
            if resp is not None:
                return resp
            
            # 4. undetected-chrome dene (HTML döner, Response değil)
            html = self._try_undetected_chrome(url, headers)
            if html is not None:
                # Sahte Response nesnesi oluştur
                fake_resp = requests.Response()
                fake_resp.status_code = 200
                fake_resp._content = html.encode("utf-8")
                fake_resp.headers["Content-Type"] = "text/html; charset=utf-8"
                return fake_resp
            
            # 5. Normal requests dene
            resp = self._try_requests_fallback(url, headers, "GET", **kwargs)
            if resp is not None:
                return resp
            
            # Retry delay
            if attempt < self.max_retries - 1:
                delay = self.retry_delay * (attempt + 1) + random.uniform(0, 1)
                print(f"[CF Bypass] Deneme {attempt + 1} başarısız, {delay:.1f}s bekliyor...")
                time.sleep(delay)
        
        raise CFBypassError(f"Cloudflare bypass başarısız: {url}")

    def post(self, url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
        """POST isteği at."""
        headers = headers or {}
        
        for attempt in range(self.max_retries):
            resp = self._try_curl_cffi(url, headers, "POST", **kwargs)
            if resp is not None:
                return resp
            
            resp = self._try_cloudscraper(url, headers, "POST", **kwargs)
            if resp is not None:
                return resp
            
            # FlareSolverr POST desteği
            post_data = kwargs.get("data", "")
            if isinstance(post_data, dict):
                from urllib.parse import urlencode
                post_data = urlencode(post_data)
            resp = self._try_flaresolverr(url, "POST", post_data=str(post_data) if post_data else None)
            if resp is not None:
                return resp
            
            resp = self._try_requests_fallback(url, headers, "POST", **kwargs)
            if resp is not None:
                return resp
            
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay * (attempt + 1))
        
        raise CFBypassError(f"Cloudflare bypass başarısız (POST): {url}")

    def close(self):
        """Tüm session ve driver'ları kapat."""
        if self._curl_session is not None:
            try:
                self._curl_session.close()
            except Exception:
                pass
        
        if self._cloud_session is not None:
            try:
                self._cloud_session.close()
            except Exception:
                pass
        
        if self._uc_driver is not None:
            try:
                self._uc_driver.quit()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @property
    def last_method(self) -> Optional[str]:
        """Son başarılı bypass yöntemini döndür."""
        return self._last_method

    @property
    def cookies(self) -> Dict[str, str]:
        """Toplanan çerezleri döndür."""
        return self._cookies.copy()


# Global session instance (lazy-load)
_global_session: Optional[CFSession] = None


def get_cf_session() -> CFSession:
    """Global CF session'ı döndür (singleton)."""
    global _global_session
    if _global_session is None:
        # Ayarlardan FlareSolverr URL'sini oku
        flaresolverr_url = None
        try:
            from turkanime_api.cli.dosyalar import Dosyalar
            dosya = Dosyalar()
            flaresolverr_url = dosya.ayarlar.get("flaresolverr_url", "")
        except Exception:
            pass
        _global_session = CFSession(
            flaresolverr_url=flaresolverr_url or None
        )
    return _global_session


def cf_get(url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
    """Kısayol: CF bypass ile GET isteği."""
    return get_cf_session().get(url, headers, **kwargs)


def cf_post(url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
    """Kısayol: CF bypass ile POST isteği."""
    return get_cf_session().post(url, headers, **kwargs)


__all__ = [
    "CFSession",
    "CFBypassError",
    "get_cf_session",
    "cf_get",
    "cf_post",
    "HAS_CURL_CFFI",
    "HAS_CLOUDSCRAPER",
    "HAS_UC",
]
