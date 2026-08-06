"""
Cloudflare Bypass Modülü

Bu modül, Cloudflare koruması olan sitelere erişim sağlamak için
farklı yöntemleri bir arada sunar:

1. curl_cffi - Firefox/Chrome TLS fingerprint taklidi
2. cloudscraper - JS Challenge çözümü
3. FlareSolverr - Uzak CF çözücü (headless browser sunucusu, opsiyonel)
4. QtWebEngine - Yerel gömülü Chromium (ayrı süreçte; Selenium'un yerini aldı)
5. Normal requests - Fallback

Kullanım:
    from turkanime_api.common.cf_bypass import CFSession

    session = CFSession()
    response = session.get("https://example.com")
"""
from __future__ import annotations

import json
import os
import threading
import time
import random
from urllib.parse import urlparse
from typing import Optional, Dict, Any

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

# QtWebEngine çözücü - Selenium/undetected-chromedriver'ın yerini aldı.
# Gerçek bir Chromium'u ayrı süreçte çalıştırır (yerel, gömülü FlareSolverr gibi).
try:
    from .cf_qt_solver import (  # noqa: F401
        DEFAULT_TIMEOUT as _QT_SOLVER_TIMEOUT,
        CHALLENGE_MARKERS as _CHALLENGE_MARKERS,
    )
    import importlib.util as _ilu
    HAS_QTWEBENGINE = _ilu.find_spec("PySide6.QtWebEngineCore") is not None
except Exception:
    HAS_QTWEBENGINE = False
    _CHALLENGE_MARKERS = (
        "Just a moment", "Checking your browser", "cf-browser-verification",
        "challenge-platform", "Security Verification", "turn JavaScript on",
    )


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


# Zincirin ilerlemesi YALNIZCA bu durumlarda anlamlı: hepsi "sunucu seni
# istemedi" sinyali. Diğer 4xx'ler (404/410/401…) sitenin gerçek cevabıdır ve
# başka bir yöntemle tekrar sorulunca da aynısı gelir.
ENGEL_DURUMLARI = frozenset({403, 429, 503})


def flaresolverr_ayari() -> Optional[str]:
    """Ayarlardaki FlareSolverr adresi; anahtar hiç yoksa ``None``.

    Boş dize `None` DEĞİL: kullanıcı Ayarlar'da kutuyu bilerek temizlediyse bu
    "FlareSolverr kullanma" demektir. `or` ile varsayılana düşmek, ayarın
    yalan söylemesine yol açıyordu (kutu boş, istekler yine uzak sunucuya).
    """
    try:
        from turkanime_api.cli.dosyalar import Dosyalar
        ayarlar = Dosyalar().ayarlar or {}
    except Exception:
        return None
    if "flaresolverr_url" not in ayarlar:
        return None
    return str(ayarlar.get("flaresolverr_url") or "").strip()


class CFSession:
    """
    Cloudflare korumalı sitelere erişim için akıllı session yöneticisi.
    
    Sırasıyla şu yöntemleri dener:
    1. curl_cffi (Firefox TLS fingerprint)
    2. cloudscraper (JS Challenge)
    3. FlareSolverr (uzak headless browser — opsiyonel)
    4. QtWebEngine (yerel gömülü Chromium, ayrı süreçte)
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
        # `None` = "ayara bak", boş dize = "kullanma". İkisini `or` ile aynı
        # kefeye koymak, ayarı sessizce ezip varsayılan sunucuya gitmek demekti.
        if flaresolverr_url is None:
            flaresolverr_url = flaresolverr_ayari()
        self.flaresolverr_url = (self.DEFAULT_FLARESOLVERR_URL
                                 if flaresolverr_url is None
                                 else str(flaresolverr_url).strip())

        self._curl_session: Optional[Any] = None
        self._cloud_session: Optional[Any] = None
        self._qt_solver: Optional[Any] = None      # QtWebEngine alt-süreci
        # Çözücü tek seferde tek istek işler; SearchEngine adapterleri paralel
        # çalıştığı için kilitsiz yazıp okursak thread'ler birbirinin cevabını
        # tüketir (A sitesinin HTML'i B'nin parser'ına gider).
        self._qt_lock = threading.Lock()
        self._cookies: Dict[str, str] = {}
        self._last_method: Optional[str] = None
        self._flaresolverr_user_agent: Optional[str] = None
        self._flaresolverr_down: bool = False   # devre kesici (bkz. _try_flaresolverr)

        # Hangi yöntemlerin mevcut olduğunu kontrol et
        self._available_methods = []
        if HAS_CURL_CFFI:
            self._available_methods.append("curl_cffi")
        if HAS_CLOUDSCRAPER:
            self._available_methods.append("cloudscraper")
        if self.flaresolverr_url:
            self._available_methods.append("flaresolverr")
        if HAS_QTWEBENGINE:
            self._available_methods.append("qtwebengine")
        self._available_methods.append("requests")  # Her zaman mevcut

    @staticmethod
    def _cookie_matches_host(name: str, value: str, host: str, data: Dict[str, Any]) -> bool:
        """Çerez, isteğin host'una mı ait? (çözücü tüm kavanozu döndürüyor)"""
        domains = data.get("cookie_domains") or {}
        dom = (domains.get(name) or "").lstrip(".").lower()
        if not dom or not host:
            return True          # domain bilgisi yoksa eski davranış
        return host == dom or host.endswith("." + dom)

    @staticmethod
    def _is_challenge(resp) -> bool:
        """Cevap gerçek içerik değil, bir koruma/challenge sayfası mı?

        KRİTİK: Challenge sayfaları da HTTP 200 döner. Bunu kontrol etmezsek
        zincir ilk yöntemde (curl_cffi) "başarı" sanıp kısa devre yapar ve
        gerçek tarayıcı (QtWebEngine) rung'ına hiç ulaşılmaz.
        """
        if resp is None:
            return False
        if getattr(resp, "status_code", 200) == 202:
            return True
        try:
            head = resp.text[:6000]
        except Exception:
            return False
        return any(marker in head for marker in _CHALLENGE_MARKERS)

    @staticmethod
    def _mesru_yanit(resp) -> bool:
        """Bu, sitenin gerçek cevabı mı — zinciri sürdürmek anlamsız mı?

        Eskiden yalnızca HTTP 200 "başarı" sayılıyordu; var olmayan bir bölümün
        404'ü hiçbir basamakta kabul edilmediği için tüm zincir + 3 retry
        dönüyor, kullanıcı basit bir "bulunamadı" yerine onlarca saniye bekleyip
        istisna görüyordu. 404/410/401 gibi yanıtlar başka bir yöntemle tekrar
        sorulunca da değişmez; doğrudan çağırana verilir.
        """
        if resp is None:
            return False
        kod = int(getattr(resp, "status_code", 200) or 200)
        if kod in ENGEL_DURUMLARI:
            return False          # CF engeli/limit — sıradaki yöntem denensin
        if kod >= 500:
            return False          # geçici sunucu hatası: yeniden denemeye değer
        return not CFSession._is_challenge(resp)

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

    def _get_qt_solver(self):
        """QtWebEngine çözücü alt-sürecini lazy-spawn et ve yeniden kullan.

        Ayrı süreç kullanmamızın sebebi: QtWebEngine bir QApplication'ın ana
        thread'inde çalışmak zorunda, oysa CFSession senkron olarak hem GUI
        worker thread'lerinden hem de Qt'siz CLI'dan çağrılıyor.
        """
        if not HAS_QTWEBENGINE:
            return None
        if self._qt_solver is not None and self._qt_solver.poll() is None:
            return self._qt_solver

        import subprocess
        import sys as _sys
        from .cf_qt_solver import SOLVER_FLAG

        proc = None
        try:
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
            if getattr(_sys, "frozen", False):
                # Paketlenmiş EXE'de `-m modul` çalışmaz (sys.executable uygulamanın
                # kendisi). Uygulamayı özel bayrakla yeniden çağırıyoruz; giriş
                # noktaları (gui/qt/__main__.py ve cli/__main__.py) bunu yakalayıp
                # çözücüyü başlatır.
                cmd = [_sys.executable, SOLVER_FLAG]
            else:
                cmd = [_sys.executable, "-m", "turkanime_api.common.cf_qt_solver"]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1, env=env,
            )
            ready = proc.stdout.readline()          # hazır sinyalini bekle
            if not ready or not json.loads(ready).get("ok"):
                proc.kill()
                return None
            self._qt_solver = proc
            return proc
        except Exception as e:
            # Süreç KESİNLİKLE öldürülmeli: el sıkışma bozuk çıktığında (ör.
            # bayrağı tanımayan bir giriş noktası cevap yerine menü basarsa)
            # `json.loads` burada patlıyor ve alt-süreç yetim kalıyordu.
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            print(f"[CF Bypass] QtWebEngine çözücü başlatılamadı: {e}")
            return None

    def _try_qtwebengine(self, url: str, timeout: Optional[int] = None) -> Optional[requests.Response]:
        """Gerçek Chromium ile challenge'ı çöz (undetected-chromedriver'ın yerine).

        Yalnızca GET destekler; POST için zincir requests'e düşer.
        """
        try:
            # Kilit: istek/cevap çifti bölünmez olmalı (bkz. _qt_lock).
            with self._qt_lock:
                proc = self._get_qt_solver()
                if proc is None:
                    return None
                req = {"url": url, "timeout": int(timeout or self.timeout)}
                proc.stdin.write(json.dumps(req, ensure_ascii=True) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
            if not line:
                return None
            data = json.loads(line)
            if not data.get("ok"):
                print(f"[CF Bypass] QtWebEngine: {data.get('error')}")
                return None

            # Çözücü tüm çerez kavanozunu döndürür; yalnızca BU host'a ait
            # olanları alıyoruz. Aksi hâlde bir sitenin cf_clearance'ı başka
            # siteye iliştirilip 403/challenge döngüsü yaratır.
            host = (urlparse(url).hostname or "").lower()
            for name, value in (data.get("cookies") or {}).items():
                if self._cookie_matches_host(name, value, host, data):
                    self._cookies[name] = value
            ua = data.get("user_agent")
            if ua:
                self._flaresolverr_user_agent = ua

            html = data.get("html") or ""
            fake_resp = requests.Response()
            fake_resp.status_code = int(data.get("status") or 200)
            fake_resp._content = html.encode("utf-8")
            fake_resp.headers["Content-Type"] = "text/html; charset=utf-8"
            fake_resp.url = data.get("url") or url

            self._last_method = "qtwebengine"
            return fake_resp
        except Exception as e:
            print(f"[CF Bypass] QtWebEngine hatası: {e}")
            # Bozulmuş süreci at ki bir sonraki çağrı temiz başlasın
            try:
                self._qt_solver.kill()
            except Exception:
                pass
            self._qt_solver = None
        return None

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
                
                if resp.status_code in ENGEL_DURUMLARI:
                    # CF engeli/limit — parmak izini değiştirip tekrar dene
                    continue

                # 200 dışındaki yanıtlar da (404/410/301…) sitenin CEVABIdır;
                # başka impersonate ile sormak aynı sonucu verir.
                self._last_method = f"curl_cffi ({imp})"
                # Çerezleri kaydet (curl_cffi dict döner)
                try:
                    cookies_dict = session.cookies.get_dict() if hasattr(session.cookies, 'get_dict') else dict(session.cookies)
                    self._cookies.update(cookies_dict)
                except Exception:
                    pass
                return resp
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
            
            if resp.status_code not in ENGEL_DURUMLARI and resp.status_code < 500:
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
        # Adres boşsa kullanıcı bu basamağı kapatmış demektir (bkz. __init__).
        if not self.flaresolverr_url:
            return None
        # Devre kesici: sunucuya bir kez bağlanılamadıysa oturum boyunca tekrar
        # deneme. Aksi hâlde erişilemez bir FlareSolverr her istekte timeout
        # süresi kadar gecikme ve log gürültüsü üretiyor.
        if getattr(self, "_flaresolverr_down", False):
            return None
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
            # Eğer sunucu HTTP 500 dönse bile JSON çıktısı verebiliyor (örn: Cloudflare engeli)
            try:
                data = resp.json()
            except ValueError:
                resp.raise_for_status()
                return None

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
            self._flaresolverr_down = True
            print("[CF Bypass] FlareSolverr sunucusuna bağlanılamadı "
                  "— bu oturumda tekrar denenmeyecek")
        except requests.exceptions.Timeout:
            self._flaresolverr_down = True
            print("[CF Bypass] FlareSolverr zaman aşımı "
                  "— bu oturumda tekrar denenmeyecek")
        except Exception as e:
            print(f"[CF Bypass] FlareSolverr hatası: {e}")
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
            
            # Son çare: durum ne olursa olsun cevabı geri ver. Buraya kadar
            # gelindiyse zincirin verecek başka bir şeyi kalmadı.
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
            if self._mesru_yanit(resp):
                return resp

            # 2. cloudscraper dene
            resp = self._try_cloudscraper(url, headers, "GET", **kwargs)
            if self._mesru_yanit(resp):
                return resp

            # 3. FlareSolverr dene
            resp = self._try_flaresolverr(url, "GET")
            if self._mesru_yanit(resp):
                return resp

            # 4. QtWebEngine dene (yerel gömülü Chromium, ayrı süreçte)
            resp = self._try_qtwebengine(url)
            if self._mesru_yanit(resp):
                return resp

            # 5. Normal requests dene (son çare: challenge olsa bile döndür)
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
        
        if self._qt_solver is not None:
            try:
                self._qt_solver.stdin.close()
                self._qt_solver.wait(timeout=5)
            except Exception:
                try:
                    self._qt_solver.kill()
                except Exception:
                    pass
            self._qt_solver = None

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
        """Toplanan çerezleri döndür (KOPYA — yazmak için `set_cookie`)."""
        return self._cookies.copy()

    def set_cookie(self, name: str, value: str) -> None:
        """Oturuma çerez ekle.

        Yazma yolu ayrı bir metot: `cookies` bilinçli olarak kopya döndürüyor
        (çağıran taraf iç sözlüğü kazara bozmasın diye). Kopya üzerinden yazmayı
        denemek sessizce kaybolurdu — nitekim `sources/openani.py` yıllarca
        `session.cookies.set(...)` çağırıyordu; dict'te böyle bir metot olmadığı
        için token ayarlanır ayarlanmaz AttributeError atıyordu.
        """
        if name:
            self._cookies[str(name)] = str(value)


# Global session instance (lazy-load)
_global_session: Optional[CFSession] = None


def get_cf_session() -> CFSession:
    """Global CF session'ı döndür (singleton).

    FlareSolverr adresini `CFSession` kendisi ayardan okur — böylece kendi
    oturumunu kuran kaynaklar (animecix, anizle, openani) da aynı tercihe uyar.
    """
    global _global_session
    if _global_session is None:
        _global_session = CFSession()
    return _global_session


def reset_cf_session() -> None:
    """Global oturumu düşür; bir sonraki istek ayarları yeniden okusun.

    Ayarlar sayfası FlareSolverr adresini değiştirdiğinde çağrılır: adres
    kurulum anında okunduğu için, sıfırlanmazsa değişiklik ancak uygulama
    yeniden başlatılınca etkili olurdu.
    """
    global _global_session
    eski, _global_session = _global_session, None
    if eski is not None:
        try:
            eski.close()
        except Exception:
            pass


def cf_get(url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
    """Kısayol: CF bypass ile GET isteği."""
    return get_cf_session().get(url, headers, **kwargs)


def cf_post(url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
    """Kısayol: CF bypass ile POST isteği."""
    return get_cf_session().post(url, headers, **kwargs)


__all__ = [
    "CFSession",
    "CFBypassError",
    "ENGEL_DURUMLARI",
    "flaresolverr_ayari",
    "get_cf_session",
    "reset_cf_session",
    "cf_get",
    "cf_post",
    "HAS_CURL_CFFI",
    "HAS_CLOUDSCRAPER",
    "HAS_QTWEBENGINE",
]
