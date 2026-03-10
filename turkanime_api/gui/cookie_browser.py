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
import tempfile
import zipfile
import requests
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

# Windows'ta yaygın Chrome/Edge kurulum yolları
_WINDOWS_CHROME_PATHS = [
    os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
]


def _find_windows_chrome() -> Optional[str]:
    """Windows'ta Chrome binary yolunu bul."""
    if not _IS_WINDOWS:
        return None
    # shutil.which önce dene
    path = shutil.which("chrome") or shutil.which("chrome.exe")
    if path:
        return path
    # Bilinen kurulum yollarını dene
    for candidate in _WINDOWS_CHROME_PATHS:
        if candidate and os.path.isfile(candidate):
            return candidate
    # Registry'den dene
    try:
        import winreg
        for key_path in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ]:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                value, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if value and os.path.isfile(value):
                    return value
            except (FileNotFoundError, OSError):
                continue
    except ImportError:
        pass
    return None


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


def _ensure_chromium_local(status_fn) -> Optional[str]:
    """Chromium binary yoksa otomatik indir ve cache'e al.
    Returns binary path or None.
    """
    cache_dir = os.path.join(os.path.expanduser("~"), ".turkanime_chromium")
    os.makedirs(cache_dir, exist_ok=True)

    base = "https://commondatastorage.googleapis.com/chromium-browser-snapshots"
    if _IS_WINDOWS:
        platform_key = "Win"
        archive_name = "chrome-win.zip"
    elif _IS_LINUX:
        platform_key = "Linux_x64"
        archive_name = "chrome-linux.zip"
    elif _IS_MAC:
        platform_key = "Mac"
        archive_name = "chrome-mac.zip"
    else:
        return None

    try:
        status_fn("⬇️ Chromium bulunamadı, indiriliyor (büyük dosya)...")
        # get latest revision
        last_change_url = f"{base}/{platform_key}/LAST_CHANGE"
        r = requests.get(last_change_url, timeout=15)
        r.raise_for_status()
        rev = r.text.strip()

        dest_dir = os.path.join(cache_dir, rev)
        binary = None
        if os.path.isdir(dest_dir):
            # already extracted
            for root, _, files in os.walk(dest_dir):
                for fname in files:
                    if fname.lower().startswith("chrome") or fname.lower().startswith("chromium") or fname == "chrome.exe":
                        candidate = os.path.join(root, fname)
                        if os.access(candidate, os.X_OK) or fname.endswith('.exe'):
                            binary = candidate
                            break
                if binary:
                    break
            if binary:
                status_fn("✅ Yerel Chromium bulundu.")
                return binary

        # download archive
        download_url = f"{base}/{platform_key}/{rev}/{archive_name}"
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            with requests.get(download_url, stream=True, timeout=60) as r2:
                r2.raise_for_status()
                for chunk in r2.iter_content(chunk_size=8192):
                    if chunk:
                        tmp.write(chunk)
            tmp.close()
            # extract
            os.makedirs(dest_dir, exist_ok=True)
            with zipfile.ZipFile(tmp.name, 'r') as z:
                z.extractall(dest_dir)

            # find binary
            for root, _, files in os.walk(dest_dir):
                for fname in files:
                    if fname.lower() in ("chrome.exe", "chrome", "chromium") or fname.lower().startswith("chrome"):
                        candidate = os.path.join(root, fname)
                        if os.access(candidate, os.X_OK) or fname.endswith('.exe'):
                            binary = candidate
                            break
                if binary:
                    break

            if binary:
                status_fn(f"✅ Chromium indirildi: {binary}")
                return binary
            else:
                status_fn("⚠️ Chromium indirildi ama binary bulunamadı.")
                return None
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    except Exception as e:
        log.debug("Chromium indirme hatası: %s", e)
        status_fn(f"Chromium indirilemedi: {e}")
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


def _try_chrome(status_fn, allow_download: Optional[Callable[[], bool]] = None) -> Optional[webdriver.Chrome]:
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
        log.debug("Chrome ilk deneme başarısız: %s", exc)
        # Windows'ta Chrome binary'sini manuel olarak bul ve tekrar dene
        if _IS_WINDOWS:
            try:
                chrome_path = _find_windows_chrome()
                if chrome_path:
                    status_fn(f"🔍 Chrome bulundu: {chrome_path}")
                    options = _common_chrome_options(ChromeOptions())
                    options.binary_location = chrome_path
                    driver = webdriver.Chrome(options=options)
                    _apply_stealth(driver, True)
                    log.info("Chrome driver (windows manual path) başarıyla oluşturuldu: %s", chrome_path)
                    return driver
            except Exception as win_exc:
                log.debug("Chrome windows manual path başarısız: %s", win_exc)

        # Eğer sistemde Chrome yoksa, lokal Chromium indir ve tekrar dene (izin varsa)
        try:
            do_download = True
            if allow_download is not None:
                try:
                    do_download = bool(allow_download())
                except Exception:
                    do_download = False
            if do_download:
                local = _ensure_chromium_local(status_fn)
            else:
                local = None

            if local:
                options = _common_chrome_options(ChromeOptions())
                options.binary_location = local
                # Eğer local Chromium kullanılıyorsa bazı Windows ortamlarında
                # sandbox erişimi engellenebilir. İzin problemi görülürse
                # --no-sandbox ile yeniden denemek yardımcı olabilir.
                try:
                    driver = webdriver.Chrome(options=options)
                    _apply_stealth(driver, True)
                    log.info("Chrome driver (local chromium) başarıyla oluşturuldu")
                    return driver
                except Exception:
                    try:
                        options.add_argument("--no-sandbox")
                        options.add_argument("--disable-gpu")
                        options.add_argument("--disable-software-rasterizer")
                        driver = webdriver.Chrome(options=options)
                        _apply_stealth(driver, True)
                        log.info("Chrome driver (local chromium, no-sandbox) başarıyla oluşturuldu")
                        return driver
                    except Exception:
                        raise
        except Exception as exc2:
            log.debug("Chrome local chromium denemesi başarısız: %s", exc2)
        return None


def _try_edge(status_fn, allow_download: Optional[Callable[[], bool]] = None) -> Optional[webdriver.Edge]:
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


def _try_firefox(status_fn, allow_download: Optional[Callable[[], bool]] = None) -> Optional[webdriver.Firefox]:
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


def _try_linux_chromium(status_fn, allow_download: Optional[Callable[[], bool]] = None) -> Optional[webdriver.Chrome]:
    """Linux'ta Chromium binary'si ile Chrome driver oluştur."""
    if not _IS_LINUX:
        return None
    chromium_path = _find_linux_chromium()
    if not chromium_path:
        # deneyelim otomatik indirip getirip getiremediğine
        chromium_path = _ensure_chromium_local(status_fn)
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


def _create_driver(status_fn=None, allow_download: Optional[Callable[[], bool]] = None):
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
            driver = factory(_status, allow_download)
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
        # Optional dispatcher to run callbacks on the main/UI thread.
        # Signature: dispatcher(func, args_tuple, kwargs_dict)
        dispatch: Optional[Callable[[Callable, tuple, dict], None]] = None,
        # Optional allow_download hook. If provided, it's called to ask
        # whether automatic Chromium download is permitted. Signature: ()->bool
        allow_download: Optional[Callable[[], bool]] = None,
    ):
        self.on_status = on_status or (lambda m: None)
        self.on_error = on_error or (lambda m: None)
        self.on_cookies = on_cookies or (lambda m: None)
        self._dispatch = dispatch
        self._allow_download = allow_download
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

    def _dispatch_call(self, callback: Callable, *args, **kwargs):
        """Run callback either via dispatcher or directly.

        The `dispatch` attribute is an optional function provided by the
        caller that schedules execution on the main/UI thread. It should
        accept (func, args_tuple, kwargs_dict). If no dispatcher is
        supplied we invoke the callback directly.
        """
        try:
            if self._dispatch:
                self._dispatch(callback, args, kwargs)
            else:
                callback(*args, **kwargs)
        except Exception:
            pass

    def _run(self):
        """Ana worker döngüsü."""
        try:
            # 1) Tarayıcı oluştur
            self._emit_status("🚀 Tarayıcı aranıyor ve başlatılıyor...\n(Driver otomatik indirilecek)")
            self._driver = _create_driver(status_fn=self._emit_status, allow_download=self._allow_download)

            if self._driver is None:
                self._dispatch_call(self.on_error,
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
            # 4) age_verified cookie'sini otomatik ekle (deneme)
            try:
                self._driver.add_cookie({
                    "name": "age_verified",
                    "value": "true",
                    "domain": "www.tranimeizle.io",
                    "path": "/",
                })
            except Exception:
                pass

            # 5) Bot Kontrol var mı kontrol et (ve yönlendirme bekle)
            page_source = self._driver.page_source or ""
            has_bot_check = "Bot Kontrol" in page_source or "captcha" in page_source.lower()

            if has_bot_check:
                self._emit_status(
                    "🔐 Bot kontrolü algılandı!\n"
                    "Tarayıcıda captcha'yı çözün.\n"
                    "Sayfa yönlendirmesi tamamlandıktan sonra cookie otomatik kaydedilecek..."
                )
            else:
                self._emit_status(
                    "✅ Sayfa yüklendi, cookie'ler kontrol ediliyor..."
                )

            # 6) Cookie polling döngüsü
            start_time = time.time()
            last_status = ""
            # Kaydettiğimiz başlangıç URL'si; yönlendirme olup olmadığını anlamak için
            try:
                last_url = self._driver.current_url
            except Exception:
                last_url = None
            while not self._stop_flag:
                elapsed = time.time() - start_time
                if elapsed > MAX_WAIT_SECONDS:
                    self._dispatch_call(self.on_error,
                        f"⏰ Süre doldu ({MAX_WAIT_SECONDS // 60} dakika).\n"
                        "Tekrar deneyin."
                    )
                    break
                try:
                    all_cookies = self._driver.get_cookies()
                except Exception:
                    # Tarayıcı kapanmış olabilir
                    if not self._stop_flag:
                        self._dispatch_call(self.on_error, "Tarayıcı penceresi kapatıldı.")
                    break

                tranime_cookies = _filter_tranime_cookies(all_cookies)

                # URL'in değişip değişmediğini kontrol et (yönlendirme tamamlandı mı?)
                try:
                    current_url = self._driver.current_url
                except Exception:
                    current_url = None

                url_changed = False
                if last_url is not None and current_url is not None:
                    url_changed = current_url != last_url

                # Eğer URL değişikliği gerçekleşmediyse, cookie olsa bile kabul etme
                if not url_changed:
                    # sadece durum güncellemesi göster
                    if tranime_cookies:
                        names = [c.get("name", "?") for c in tranime_cookies]
                        status_preview = f"⏳ Cookie bulundu fakat yönlendirme bekleniyor. Bulunan: {', '.join(names[:5])}"
                        if status_preview != last_status:
                            self._emit_status(status_preview)
                            last_status = status_preview
                    time.sleep(POLL_INTERVAL)
                    continue
                # Eğer URL değiştiyse, yeni URL'i kaydet
                last_url = current_url

                if _has_required_cookies(tranime_cookies):
                    # Başarı!
                    netscape = _cookies_to_netscape(tranime_cookies)
                    self._emit_status(
                        f"✅ Cookie'ler alındı! ({len(tranime_cookies)} cookie)"
                    )
                    self._done = True

                    # Driver'ı önce kapat, sonra GUI'ye callback yollarız (ana thread'te olmalı)
                    try:
                        self._close_driver()
                    except Exception:
                        pass

                    # Dispatch cookie callback (may be scheduled to main thread by caller)
                    self._dispatch_call(self.on_cookies, netscape)
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
                self._dispatch_call(self.on_error, f"Hata: {e}")
        finally:
            self._close_driver()
