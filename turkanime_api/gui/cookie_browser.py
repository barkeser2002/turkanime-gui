"""
TRAnimeİzle Cookie Tarayıcısı
==============================
Selenium ile gerçek bir tarayıcı penceresi açarak kullanıcıdan
captcha çözmesini ister ve cookie'leri Netscape formatında döndürür.

Desteklenen tarayıcılar (otomatik fallback sırasıyla):
  Chrome → Edge → Firefox → Chromium (Linux)

Driver'lar Selenium 4.6+ dahili SeleniumManager tarafından
otomatik indirilir — ek paket gerekmez.

Platform desteği: Windows, Linux, macOS
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import threading
import time
from typing import Optional, Callable, Dict, List

log = logging.getLogger(__name__)

# ── Selenium import ──────────────────────────────────────────────────────────
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.firefox.service import Service as FirefoxService
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

# ─────────────────────────────────────────────────────────────────────────────
# YAPILANDIRMA
# ─────────────────────────────────────────────────────────────────────────────
TRANIME_BASE = "https://www.tranimeizle.io"
# Popüler anime — captcha tetiklemek için detay sayfası gerekli
TARGET_ANIME = f"{TRANIME_BASE}/anime/naruto-izle"
COOKIE_DOMAIN = "tranimeizle.io"

# Gerekli cookie isimleri
REQUIRED_COOKIES = {".AitrWeb.Session"}
# Ek olarak yararlı cookie'ler
BONUS_COOKIES = {"age_verified", ".AitrWeb.Verification."}

# Zamanlama
MAX_WAIT_SECONDS = 300  # 5 dakika
POLL_INTERVAL = 2  # 2 saniye aralıklarla cookie kontrol


def is_available() -> bool:
    """Selenium yüklü mü?"""
    return HAS_SELENIUM


# ── Platform algılama ────────────────────────────────────────────────────────
_IS_LINUX = platform.system() == "Linux"
_IS_WINDOWS = platform.system() == "Windows"
_IS_MAC = platform.system() == "Darwin"

_CHROMIUM_LINUX_NAMES = ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable")


def _find_linux_chromium() -> Optional[str]:
    """Linux'ta Chromium/Chrome binary yolunu bul."""
    for name in _CHROMIUM_LINUX_NAMES:
        path = shutil.which(name)
        if path:
            return path
    # Snap / flatpak gibi özel konumlar
    for candidate in (
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/snap/bin/chromium",
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


# ── Driver oluşturma (Chrome → Edge → Firefox → Linux Chromium) ─────────────

def _apply_stealth(driver, is_chromium_based: bool = True):
    """Bot algılama koruması uygula."""
    if is_chromium_based:
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = window.chrome || {runtime: {}};
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US', 'en']});
                """
            })
        except Exception:
            pass


def _common_chrome_options(options):
    """Chrome/Chromium/Edge ortak seçenekler."""
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    try:
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
    except Exception:
        pass
    # Linux sandboxing
    if _IS_LINUX:
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
    return options


def _try_chrome(status_fn) -> Optional[webdriver.Chrome]:
    """Chrome tarayıcısı ile driver oluştur."""
    status_fn("🔍 Chrome deneniyor...")
    try:
        options = _common_chrome_options(ChromeOptions())
        # Selenium 4.6+ dahili SeleniumManager driver'ı otomatik indirir
        driver = webdriver.Chrome(options=options)
        _apply_stealth(driver, True)
        log.info("Chrome driver başarıyla oluşturuldu")
        return driver
    except Exception as exc:
        log.debug("Chrome başarısız: %s", exc)
        return None


def _try_edge(status_fn) -> Optional[webdriver.Edge]:
    """Microsoft Edge tarayıcısı ile driver oluştur."""
    status_fn("🔍 Edge deneniyor...")
    try:
        options = _common_chrome_options(EdgeOptions())
        driver = webdriver.Edge(options=options)
        _apply_stealth(driver, True)
        log.info("Edge driver başarıyla oluşturuldu")
        return driver
    except Exception as exc:
        log.debug("Edge başarısız: %s", exc)
        return None


def _try_firefox(status_fn) -> Optional[webdriver.Firefox]:
    """Firefox tarayıcısı ile driver oluştur."""
    status_fn("🔍 Firefox deneniyor...")
    try:
        options = FirefoxOptions()
        # Firefox bot koruması
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference("useAutomationExtension", False)
        options.set_preference("general.useragent.override",
                               "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
                               "Gecko/20100101 Firefox/120.0")
        driver = webdriver.Firefox(options=options)
        driver.maximize_window()
        log.info("Firefox driver başarıyla oluşturuldu")
        return driver
    except Exception as exc:
        log.debug("Firefox başarısız: %s", exc)
        return None


def _try_linux_chromium(status_fn) -> Optional[webdriver.Chrome]:
    """Linux'ta Chromium binary'si ile Chrome driver oluştur."""
    if not _IS_LINUX:
        return None
    chromium_path = _find_linux_chromium()
    if not chromium_path:
        return None
    status_fn(f"🔍 Chromium deneniyor ({chromium_path})...")
    try:
        options = _common_chrome_options(ChromeOptions())
        options.binary_location = chromium_path
        driver = webdriver.Chrome(options=options)
        _apply_stealth(driver, True)
        log.info("Linux Chromium driver başarıyla oluşturuldu: %s", chromium_path)
        return driver
    except Exception as exc:
        log.debug("Linux Chromium başarısız: %s", exc)
        return None


def _create_driver(status_fn=None):
    """
    Tüm tarayıcıları sırayla dene: Chrome → Edge → Firefox → Chromium (Linux).
    Selenium 4.6+ dahili SeleniumManager ile driver'lar otomatik indirilir.
    """
    _status = status_fn or (lambda m: None)
    errors: List[str] = []

    for name, factory in [
        ("Chrome", _try_chrome),
        ("Edge", _try_edge),
        ("Firefox", _try_firefox),
        ("Chromium", _try_linux_chromium),
    ]:
        try:
            driver = factory(_status)
            if driver is not None:
                _status(f"✅ {name} tarayıcısı başlatıldı")
                return driver
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    log.error("Hiçbir tarayıcı bulunamadı: %s", errors)
    return None


def _cookies_to_netscape(cookies: List[Dict]) -> str:
    """Selenium cookie listesini Netscape HTTP Cookie File formatına çevir."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# TürkAnime GUI tarafından otomatik oluşturuldu.",
        "",
    ]

    for c in cookies:
        domain = c.get("domain", "")
        # domain '.' ile başlıyorsa flag TRUE
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        expiry = str(int(c.get("expiry", 0)))
        name = c.get("name", "")
        value = c.get("value", "")

        if not name:
            continue

        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}")

    return "\n".join(lines) + "\n"


def _has_required_cookies(cookies: List[Dict]) -> bool:
    """Gerekli cookie'ler mevcut mu?"""
    names = {c.get("name", "") for c in cookies}
    return REQUIRED_COOKIES.issubset(names)


def _filter_tranime_cookies(cookies: List[Dict]) -> List[Dict]:
    """Sadece TRAnimeİzle'ye ait cookie'leri filtrele."""
    return [c for c in cookies if COOKIE_DOMAIN in c.get("domain", "")]


class CookieBrowserWorker:
    """
    Arka plan thread'inde Selenium tarayıcı penceresi açıp
    cookie'leri toplayan worker.

    Kullanım:
        worker = CookieBrowserWorker(
            on_status=lambda msg: print(msg),
            on_cookies=lambda netscape_str: save(netscape_str),
            on_error=lambda err: print(err),
        )
        worker.start()
        # ... kullanıcı captcha çözer ...
        worker.stop()  # İptal
    """

    def __init__(
        self,
        on_status: Optional[Callable[[str], None]] = None,
        on_cookies: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.on_status = on_status or (lambda m: None)
        self.on_error = on_error or (lambda m: None)
        self.on_cookies = on_cookies or (lambda m: None)
        self._driver = None
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = False
        self._done = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Tarayıcıyı arka planda başlat."""
        if self.is_running:
            return
        self._stop_flag = False
        self._done = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """İptal et ve tarayıcıyı kapat."""
        self._stop_flag = True
        self._close_driver()

    def _close_driver(self):
        """Driver'ı güvenli şekilde kapat."""
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def _emit_status(self, msg: str):
        try:
            self.on_status(msg)
        except Exception:
            pass

    def _run(self):
        """Ana worker döngüsü."""
        try:
            # 1) Tarayıcı oluştur
            self._emit_status("🚀 Tarayıcı aranıyor ve başlatılıyor...\n(Driver otomatik indirilecek)")
            self._driver = _create_driver(status_fn=self._emit_status)

            if self._driver is None:
                self.on_error(
                    "Hiçbir tarayıcı bulunamadı!\n\n"
                    "Aşağıdakilerden en az biri yüklü olmalıdır:\n"
                    "• Google Chrome\n"
                    "• Microsoft Edge\n"
                    "• Mozilla Firefox\n"
                    + ("• Chromium\n" if _IS_LINUX else "")
                    + "\nDriver'lar otomatik indirilir, sadece\n"
                    "tarayıcının kendisinin yüklü olması yeterlidir."
                )
                return

            if self._stop_flag:
                self._close_driver()
                return

            # 2) Anasayfaya git (cookie'lerin oturması için)
            self._emit_status("🌐 TRAnimeİzle açılıyor...")
            self._driver.get(TRANIME_BASE)
            time.sleep(3)

            if self._stop_flag:
                self._close_driver()
                return

            # 3) Anime detay sayfasına git (Bot Kontrol tetiklenir)
            self._emit_status("📺 Anime sayfasına yönlendiriliyor...")
            self._driver.get(TARGET_ANIME)
            time.sleep(2)

            # 4) age_verified cookie'sini otomatik ekle
            try:
                self._driver.add_cookie({
                    "name": "age_verified",
                    "value": "true",
                    "domain": "www.tranimeizle.io",
                    "path": "/",
                })
            except Exception:
                pass

            # 5) Bot Kontrol var mı kontrol et
            page_source = self._driver.page_source or ""
            has_bot_check = "Bot Kontrol" in page_source or "captcha" in page_source.lower()

            if has_bot_check:
                self._emit_status(
                    "🔐 Bot kontrolü algılandı!\n"
                    "Tarayıcıda captcha'yı çözün.\n"
                    "Cookie'ler otomatik kaydedilecek..."
                )
            else:
                self._emit_status(
                    "✅ Sayfa yüklendi, cookie'ler kontrol ediliyor..."
                )

            # 6) Cookie polling döngüsü
            start_time = time.time()
            last_status = ""
            while not self._stop_flag:
                elapsed = time.time() - start_time
                if elapsed > MAX_WAIT_SECONDS:
                    self.on_error(
                        f"⏰ Süre doldu ({MAX_WAIT_SECONDS // 60} dakika).\n"
                        "Tekrar deneyin."
                    )
                    break

                try:
                    all_cookies = self._driver.get_cookies()
                except Exception:
                    # Tarayıcı kapanmış olabilir
                    if not self._stop_flag:
                        self.on_error("Tarayıcı penceresi kapatıldı.")
                    break

                tranime_cookies = _filter_tranime_cookies(all_cookies)

                if _has_required_cookies(tranime_cookies):
                    # Başarı!
                    netscape = _cookies_to_netscape(tranime_cookies)
                    self._emit_status(
                        f"✅ Cookie'ler alındı! ({len(tranime_cookies)} cookie)"
                    )
                    self._done = True

                    try:
                        self.on_cookies(netscape)
                    except Exception:
                        pass
                    break

                # Durum güncelle
                remaining = int(MAX_WAIT_SECONDS - elapsed)
                cookie_names = [c.get("name", "?") for c in tranime_cookies]
                status = (
                    f"⏳ Bekleniyor... ({remaining}s kaldı)\n"
                    f"Mevcut cookie: {len(tranime_cookies)}\n"
                    f"Gerekli: .AitrWeb.Session"
                )
                if cookie_names:
                    status += f"\nBulunan: {', '.join(cookie_names[:5])}"

                if status != last_status:
                    self._emit_status(status)
                    last_status = status

                time.sleep(POLL_INTERVAL)

        except Exception as e:
            if not self._stop_flag:
                self.on_error(f"Hata: {e}")
        finally:
            self._close_driver()
