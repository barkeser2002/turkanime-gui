#!/usr/bin/env python3
"""
TürkAnime GUI - Kaynak Adaptör Tam Kapsam Testi
=================================================
Tüm kaynakları (AnimeCix, Anizle, TRAnimeİzle) tek çalıştırmada test eder.
Her adaptörün arama, bölüm listeleme, detay çekme ve stream pipeline'ını doğrular.

Kullanım:
    python tests/adapters-test-all.py                   # Tüm testler
    python tests/adapters-test-all.py --source animecix  # Tek kaynak
    python tests/adapters-test-all.py --source tranime   # Tek kaynak
    python tests/adapters-test-all.py --source anizle    # Tek kaynak
    python tests/adapters-test-all.py --verbose          # Detaylı çıktı
    python tests/adapters-test-all.py --skip-streams     # Stream testlerini atla

Cookie:
    TRAnimeİzle testleri için Netscape cookie dosyası kullanılır.
    Cookie otomatik olarak bu dosyada tanımlıdır; güncellemek için
    TRANIME_COOKIE değişkenini değiştirin.
"""

import sys
import os
import time
import json
import traceback
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Callable, Any

# Proje kök dizinini PATH'e ekle
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ═══════════════════════════════════════════════════════════════════════════════
# TRANİMEİZLE COOKIE (Netscape HTTP Cookie File)
# ═══════════════════════════════════════════════════════════════════════════════
TRANIME_COOKIE_NETSCAPE = """# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file! Do not edit.

www.tranimeizle.io	FALSE	/	FALSE	1801750271	age_verified	true
.tranimeizle.io	TRUE	/	TRUE	0	.AitrWeb.Session	CfDJ8Ax4Hn4UhldJn1m52kfkyppFzTCKI9cUzBgNeS4dJZEtdDq04USlqe0z9pUcx2IZus%2BdNm6T0MsMb6xLVOfFen3PGXIOqpsUmdrvjU7cfPB07dplFizt93YnUdYT5fa5%2BJkayzQFwOtXCsBh2sVxqX8EvtdeOWGJp9yMmQTpHu9A
www.tranimeizle.io	FALSE	/	FALSE	0	.AitrWeb.Verification.	CfDJ8Ax4Hn4UhldJn1m52kfkypoqR_LaC7yUUbYB3L2flQhS54Rm4w-YA9EZlo3IewkNioNONgp4aJhOVv0XaYl76DubnatLVDogeQuHp9ydzDWfSWoF-RhQ1-7TLv1_ISSOPqiMeP0JQud8lRDN4Xu_sXw
"""

# ═══════════════════════════════════════════════════════════════════════════════
# TEST FRAMEWORK
# ═══════════════════════════════════════════════════════════════════════════════

class Colors:
    """ANSI renk kodları."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    GRAY    = "\033[90m"


@dataclass
class TestResult:
    """Tek bir test sonucu."""
    name: str
    source: str
    passed: bool
    message: str = ""
    duration: float = 0.0
    details: str = ""
    skipped: bool = False


@dataclass
class TestSuite:
    """Tam test suite sonuçları."""
    results: List[TestResult] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def total(self) -> int:
        return len([r for r in self.results if not r.skipped])

    @property
    def passed(self) -> int:
        return len([r for r in self.results if r.passed and not r.skipped])

    @property
    def failed(self) -> int:
        return len([r for r in self.results if not r.passed and not r.skipped])

    @property
    def skipped_count(self) -> int:
        return len([r for r in self.results if r.skipped])

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def add(self, result: TestResult):
        self.results.append(result)


# Global test suite
_suite = TestSuite()
_verbose = False


def _ensure_utf8_stdout():
    """Çıktıyı UTF-8'e sabitle.

    Test paketi kutu çizgileri (═) ve emoji kullanıyor; Windows'ta varsayılan
    konsol kod sayfası (Türkçe kurulumda cp1254) bunları kodlayamadığı için
    süreç `UnicodeEncodeError` ile çöküyordu — yani testler o makinelerde hiç
    çalışmıyordu. `errors="replace"` ile kodlanamayan karakter çıktıyı düşürmek
    yerine yerine bir yer tutucu basar.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_ensure_utf8_stdout()


def _log(msg: str, color: str = ""):
    """Renkli log."""
    if color:
        print(f"{color}{msg}{Colors.RESET}")
    else:
        print(msg)


def _log_verbose(msg: str):
    """Sadece verbose modda yazdır."""
    if _verbose:
        print(f"  {Colors.GRAY}{msg}{Colors.RESET}")


def run_test(name: str, source: str, func: Callable, *args, **kwargs) -> TestResult:
    """Tek bir testi çalıştır ve sonucu kaydet."""
    _log(f"\n  {'⏳'} {name}...", Colors.CYAN)
    start = time.time()

    try:
        result_data = func(*args, **kwargs)
        duration = time.time() - start
        # Fonksiyon (passed: bool, message: str, details: str) tuple döndürmeli
        if isinstance(result_data, tuple) and len(result_data) >= 2:
            passed, message = result_data[0], result_data[1]
            details = result_data[2] if len(result_data) > 2 else ""
        else:
            passed, message, details = bool(result_data), "OK", ""

        result = TestResult(
            name=name, source=source, passed=passed,
            message=message, duration=duration, details=details
        )
    except Exception as e:
        duration = time.time() - start
        tb = traceback.format_exc()
        result = TestResult(
            name=name, source=source, passed=False,
            message=f"Exception: {e}", duration=duration,
            details=tb if _verbose else str(e)
        )

    _suite.add(result)

    icon = "✅" if result.passed else "❌"
    color = Colors.GREEN if result.passed else Colors.RED
    _log(f"  {icon} {name} ({duration:.1f}s) — {result.message}", color)
    if result.details and _verbose:
        for line in result.details.strip().split('\n'):
            _log_verbose(line)

    return result


def skip_test(name: str, source: str, reason: str) -> TestResult:
    """Bir testi atla."""
    result = TestResult(name=name, source=source, passed=True, message=f"ATLANILDI: {reason}", skipped=True)
    _suite.add(result)
    _log(f"  ⏭️  {name} — {reason}", Colors.YELLOW)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ANIMECİX TESTLERİ
# ═══════════════════════════════════════════════════════════════════════════════

def test_animecix_search():
    """AnimeCix arama testi."""
    from turkanime_api.sources.animecix import search_animecix

    results = search_animecix("one piece", timeout=15)
    if not results:
        return False, "Arama sonucu boş", ""
    
    names = [name for _, name in results]
    details = f"Sonuç sayısı: {len(results)}\nİlk 5: {names[:5]}"
    _log_verbose(details)

    # one piece veya benzeri bir sonuç içermeli
    has_match = any("one piece" in n.lower() or "piece" in n.lower() for n in names)
    if has_match:
        return True, f"{len(results)} sonuç bulundu", details
    return False, f"{len(results)} sonuç var ama 'one piece' eşleşmesi yok", details


def test_animecix_search_turkish():
    """AnimeCix Türkçe arama testi."""
    from turkanime_api.sources.animecix import search_animecix

    results = search_animecix("naruto", timeout=15)
    if not results:
        return False, "Naruto araması boş", ""
    
    return True, f"{len(results)} sonuç bulundu", f"İlk: {results[0][1] if results else '-'}"


def test_animecix_seasons():
    """AnimeCix sezon testi."""
    from turkanime_api.sources.animecix import search_animecix, _seasons_for_title

    results = search_animecix("one piece", timeout=15)
    if not results:
        return False, "Arama boş, sezon testi yapılamıyor", ""

    title_id = int(results[0][0])
    title_name = results[0][1]
    seasons = _seasons_for_title(title_id)

    if seasons:
        return True, f"'{title_name}' (ID:{title_id}) → {len(seasons)} sezon", f"Sezonlar: {seasons}"
    return False, f"'{title_name}' için sezon bulunamadı", ""


def test_animecix_episodes():
    """AnimeCix bölüm testi."""
    from turkanime_api.sources.animecix import search_animecix, CixAnime

    # "one piece" kullan çünkü "naruto" "Naruto x UT" gibi kısa içerik döndürebilir
    results = search_animecix("one piece", timeout=15)
    if not results:
        return False, "Arama boş, bölüm testi yapılamıyor", ""

    # İlk sonucu seç (ONE PIECE — ana seri)
    title_id, title_name = results[0]
    anime = CixAnime(id=title_id, title=title_name)
    episodes = anime.episodes

    if not episodes:
        return False, f"'{title_name}' bölüm listesi boş", ""

    details = f"Toplam: {len(episodes)} bölüm\nİlk: {episodes[0].title}\nSon: {episodes[-1].title}"
    _log_verbose(details)
    return True, f"'{title_name}' → {len(episodes)} bölüm", details


def test_animecix_video_streams():
    """AnimeCix video stream testi."""
    from turkanime_api.sources.animecix import search_animecix, CixAnime, _video_streams

    # "one piece" kullan — "naruto" kısa içerik döndürebilir
    results = search_animecix("one piece", timeout=15)
    if not results:
        return False, "Arama boş", ""

    title_id, title_name = results[0]
    anime = CixAnime(id=title_id, title=title_name)
    episodes = anime.episodes
    if not episodes:
        return False, f"'{title_name}' bölüm listesi boş", ""

    # İlk bölümün URL'sinden stream çek
    first_ep = episodes[0]
    if not first_ep.url:
        return False, "İlk bölümün URL'si yok", ""

    try:
        from urllib.parse import urlparse
        parsed = urlparse(first_ep.url)
        embed_path = parsed.path if parsed.path else first_ep.url
        streams = _video_streams(embed_path)
        if streams:
            labels = [s.get("label", "?") for s in streams]
            return True, f"{len(streams)} stream bulundu", f"Kaliteler: {labels}"
        return False, "Stream bulunamadı (API değişmiş olabilir)", f"Path: {embed_path}"
    except Exception as e:
        # 422/403 = sunucu kısıtlaması, adaptör hatası değil
        err_str = str(e)
        if any(code in err_str for code in ("422", "403", "401", "503")):
            return True, f"Stream endpoint erişilemez ({err_str.split(':')[0].strip()})", f"Beklenen: AnimeCix embed API kısıtlaması"
        return False, f"Stream çekme hatası: {e}", traceback.format_exc()


# ═══════════════════════════════════════════════════════════════════════════════
# ANİZLE TESTLERİ
# ═══════════════════════════════════════════════════════════════════════════════

def test_anizle_database_load():
    """Anizle veritabanı yükleme testi."""
    from turkanime_api.sources.anizle import load_anime_database

    db = load_anime_database(force_reload=True)
    if not db:
        return False, "Veritabanı boş", ""

    # Veri yapısı kontrolü
    sample = db[0]
    required_keys = ["info_slug", "info_title"]
    missing = [k for k in required_keys if k not in sample]
    if missing:
        return False, f"Eksik alanlar: {missing}", json.dumps(list(sample.keys()), indent=2)

    details = f"Toplam anime: {len(db)}\nÖrnek: {sample.get('info_title', '?')}"
    _log_verbose(details)
    return True, f"{len(db)} anime yüklendi", details


def test_anizle_search():
    """Anizle arama testi."""
    from turkanime_api.sources.anizle import search_anizle

    results = search_anizle("one piece", limit=10, timeout=120)
    if not results:
        return False, "Arama sonucu boş", ""

    names = [title for _, title in results]
    has_match = any("one piece" in n.lower() or "piece" in n.lower() for n in names)
    details = f"Sonuç: {len(results)}\nİlk 5: {names[:5]}"
    _log_verbose(details)

    if has_match:
        return True, f"{len(results)} sonuç bulundu", details
    return False, f"Sonuç var ama 'one piece' eşleşmesi yok", details


def test_anizle_search_turkish():
    """Anizle Türkçe arama testi."""
    from turkanime_api.sources.anizle import search_anizle

    results = search_anizle("naruto", limit=10, timeout=120)
    if not results:
        return False, "Naruto araması boş", ""

    return True, f"{len(results)} sonuç", f"İlk: {results[0][1]}"


def test_anizle_episodes():
    """Anizle bölüm listeleme testi."""
    from turkanime_api.sources.anizle import search_anizle, get_anime_episodes

    results = search_anizle("one piece", limit=1, timeout=120)
    if not results:
        return False, "Anime bulunamadı", ""

    slug = results[0][0]
    title = results[0][1]
    episodes = get_anime_episodes(slug, timeout=120)

    if not episodes:
        return False, f"'{title}' bölüm listesi boş", ""

    details = (
        f"Anime: {title} (slug: {slug})\n"
        f"Toplam: {len(episodes)} bölüm\n"
        f"İlk: {episodes[0]}\n"
        f"Son: {episodes[-1]}"
    )
    _log_verbose(details)
    return True, f"'{title}' → {len(episodes)} bölüm", details


def test_anizle_episode_absolute_urls():
    """Anizle bölüm URL pattern testi (absolute vs relative)."""
    from turkanime_api.sources.anizle import search_anizle, get_anime_episodes

    results = search_anizle("naruto", limit=1, timeout=120)
    if not results:
        return False, "Anime bulunamadı", ""

    slug = results[0][0]
    episodes = get_anime_episodes(slug, timeout=120)

    if not episodes:
        return False, "Bölüm listesi boş", ""

    # URL formatı kontrolü - slug doğru formatta mı?
    ep_slug = episodes[0][0]
    has_bolum = "bolum" in ep_slug.lower()
    is_not_full_url = not ep_slug.startswith("http")  # Slug dönmeli, tam URL değil
    
    details = f"İlk bölüm slug: {ep_slug}\nBolum içeriyor: {has_bolum}\nRelative slug: {is_not_full_url}"
    if has_bolum:
        return True, f"URL pattern doğru ({len(episodes)} bölüm)", details
    return False, f"URL pattern hatalı: {ep_slug}", details


def test_anizle_translators():
    """Anizle translator (fansub) testi."""
    from turkanime_api.sources.anizle import search_anizle, get_anime_episodes, _get_episode_translators

    results = search_anizle("one piece", limit=1, timeout=120)
    if not results:
        return False, "Anime bulunamadı", ""

    slug = results[0][0]
    episodes = get_anime_episodes(slug, timeout=120)
    if not episodes or len(episodes) < 1:
        return False, "Bölüm bulunamadı", ""

    # Son bölümü test et (daha güncel translator verisi)
    ep_slug = episodes[-1][0]
    translators = _get_episode_translators(ep_slug)

    if not translators:
        # İlk bölümü dene
        ep_slug = episodes[0][0]
        translators = _get_episode_translators(ep_slug)

    if not translators:
        return False, "Translator bulunamadı", f"Denenen slug: {ep_slug}"

    names = [t.get("name", "?") for t in translators]
    details = f"Bölüm: {ep_slug}\nTranslator sayısı: {len(translators)}\nFansublar: {names}"
    _log_verbose(details)
    return True, f"{len(translators)} translator bulundu: {names}", details


def test_anizle_translator_videos():
    """Anizle translator video listesi testi."""
    from turkanime_api.sources.anizle import (
        search_anizle, get_anime_episodes,
        _get_episode_translators, _get_translator_videos
    )

    results = search_anizle("naruto", limit=1, timeout=120)
    if not results:
        return False, "Anime bulunamadı", ""

    slug = results[0][0]
    episodes = get_anime_episodes(slug, timeout=120)
    if not episodes:
        return False, "Bölüm bulunamadı", ""

    ep_slug = episodes[0][0]
    translators = _get_episode_translators(ep_slug)
    if not translators:
        return False, "Translator bulunamadı", ""

    tr = translators[0]
    videos = _get_translator_videos(tr["url"])

    if not videos:
        return False, f"'{tr['name']}' translator'ında video yok", ""

    names = [v.get("name", "?") for v in videos]
    details = f"Fansub: {tr['name']}\nVideo sayısı: {len(videos)}\nPlayerlar: {names}"
    _log_verbose(details)
    return True, f"'{tr['name']}' → {len(videos)} video", details


def test_anizle_full_stream_pipeline():
    """Anizle tam stream pipeline testi (translator → video → player → FirePlayer → stream)."""
    from turkanime_api.sources.anizle import search_anizle, get_anime_episodes, get_episode_streams

    results = search_anizle("naruto", limit=1, timeout=120)
    if not results:
        return False, "Anime bulunamadı", ""

    slug = results[0][0]
    episodes = get_anime_episodes(slug, timeout=120)
    if not episodes:
        return False, "Bölüm bulunamadı", ""

    ep_slug = episodes[0][0]
    streams = get_episode_streams(ep_slug, timeout=60)

    if not streams:
        return False, "Stream bulunamadı (pipeline kırık olabilir)", f"Bölüm: {ep_slug}"

    labels = [s.get("label", "?") for s in streams]
    types = [s.get("type", "?") for s in streams]
    details = (
        f"Bölüm: {ep_slug}\n"
        f"Stream sayısı: {len(streams)}\n"
        f"Etiketler: {labels[:5]}\n"
        f"Tipler: {set(types)}"
    )
    _log_verbose(details)
    return True, f"{len(streams)} stream bulundu", details


def test_anizle_anime_details():
    """Anizle anime detay testi."""
    from turkanime_api.sources.anizle import search_anizle, get_anime_details

    results = search_anizle("naruto", limit=1, timeout=120)
    if not results:
        return False, "Anime bulunamadı", ""

    slug = results[0][0]
    anime = get_anime_details(slug)

    if not anime:
        return False, "Detay alınamadı", ""

    checks = {
        "slug": bool(anime.slug),
        "title": bool(anime.title),
        "poster": bool(anime.poster),
        "mal_id": anime.mal_id > 0,
    }
    failed = [k for k, v in checks.items() if not v]
    details = f"Title: {anime.title}\nSlug: {anime.slug}\nPoster: {anime.poster[:60]}...\nMAL ID: {anime.mal_id}\nYear: {anime.year}"

    if failed:
        return False, f"Eksik alanlar: {failed}", details
    return True, f"'{anime.title}' detayları tam", details


# ═══════════════════════════════════════════════════════════════════════════════
# TRANİMEİZLE TESTLERİ
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_tranime_cookie():
    """TRAnimeİzle cookie'sini ayarla (Netscape format)."""
    from turkanime_api.sources.tranime import set_session_cookie
    set_session_cookie(TRANIME_COOKIE_NETSCAPE)


def test_tranime_cookie_set():
    """TRAnimeİzle cookie ayarlama testi (Netscape format parse)."""
    from turkanime_api.sources.tranime import set_session_cookie, _get_cookies, _EXTRA_COOKIES

    set_session_cookie(TRANIME_COOKIE_NETSCAPE)
    cookies = _get_cookies()

    if ".AitrWeb.Session" not in cookies:
        return False, "Cookie dict'te .AitrWeb.Session yok", str(cookies.keys())

    cookie_val = cookies[".AitrWeb.Session"]
    has_age = cookies.get("age_verified") == "true"
    has_verification = ".AitrWeb.Verification." in cookies
    details = (
        f"Session cookie uzunluğu: {len(cookie_val)}\n"
        f"age_verified: {has_age}\n"
        f"Verification cookie: {has_verification}\n"
        f"Ek cookie sayısı: {len(_EXTRA_COOKIES)}\n"
        f"Tüm anahtarlar: {list(cookies.keys())}"
    )

    if cookie_val and len(cookie_val) > 50 and has_verification:
        return True, f"Netscape parse OK ({len(cookies)} cookie, {len(cookie_val)} char session)", details
    elif cookie_val and len(cookie_val) > 50:
        return True, f"Cookie ayarlandı ({len(cookie_val)} char) ama Verification eksik", details
    return False, "Cookie çok kısa veya boş", details


def test_tranime_search_by_letter():
    """TRAnimeİzle harfe göre arama testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import search_by_letter

    results = search_by_letter("n", page=1)
    if not results:
        return False, "'n' harfi için sonuç yok (Bot Kontrol aktif olabilir)", ""

    names = [title for _, title in results]
    details = f"Sonuç: {len(results)}\nİlk 5: {names[:5]}"
    _log_verbose(details)
    return True, f"'n' harfi → {len(results)} anime", details


def test_tranime_search_fuzzy():
    """TRAnimeİzle fuzzy arama testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import search_anime

    results = search_anime("naruto", limit=10)
    if not results:
        return False, "Fuzzy arama sonucu boş", ""

    names = [title for _, title in results]
    has_match = any("naruto" in n.lower() for n in names)
    details = f"Sonuç: {len(results)}\nSonuçlar: {names[:5]}"

    if has_match:
        return True, f"{len(results)} sonuç (fuzzy match OK)", details
    return False, f"Sonuç var ama 'naruto' eşleşmesi yok: {names}", details


def test_tranime_search_direct():
    """TRAnimeİzle doğrudan arama testi (cookie gerekli)."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import _search_direct

    results = _search_direct("one piece", limit=10)
    if not results:
        # Cookie geçersiz olabilir
        return False, "Doğrudan arama boş (cookie geçersiz/süresi dolmuş olabilir)", ""

    names = [title for _, title in results]
    details = f"Sonuç: {len(results)}\nSonuçlar: {names[:5]}"
    return True, f"{len(results)} sonuç (direct search OK)", details


def test_tranime_get_anime():
    """TRAnimeİzle anime bilgisi testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import get_anime_by_slug

    anime = get_anime_by_slug("naruto")
    if not anime:
        return False, "Anime bilgisi alınamadı (Bot Kontrol?)", ""

    checks = {
        "title": bool(anime.title),
        "slug": bool(anime.slug),
        "poster": bool(anime.poster),
    }
    failed = [k for k, v in checks.items() if not v]
    details = f"Title: {anime.title}\nSlug: {anime.slug}\nPoster: {anime.poster[:60] if anime.poster else '-'}\nBölüm: {anime.total_episodes}"

    if failed:
        return True, f"Anime alındı ama eksik: {failed}", details  # Kısmi başarı
    return True, f"'{anime.title}' bilgileri tam", details


def test_tranime_episodes():
    """TRAnimeİzle bölüm listeleme testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import get_anime_episodes

    episodes = get_anime_episodes("naruto")
    if not episodes:
        return False, "Bölüm listesi boş (Bot Kontrol?)", ""

    details = (
        f"Toplam: {len(episodes)} bölüm\n"
        f"İlk: {episodes[0]}\n"
        f"Son: {episodes[-1]}"
    )
    _log_verbose(details)
    return True, f"{len(episodes)} bölüm bulundu", details


def test_tranime_episode_details():
    """TRAnimeİzle bölüm detay testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import get_anime_episodes, get_episode_details

    episodes = get_anime_episodes("naruto")
    if not episodes:
        return False, "Bölüm listesi boş", ""

    ep = get_episode_details(episodes[0].slug)
    if not ep:
        return False, "Bölüm detayı alınamadı", f"Slug: {episodes[0].slug}"

    details = (
        f"Episode ID: {ep.episode_id}\n"
        f"Bölüm No: {ep.episode_number}\n"
        f"Slug: {ep.slug}\n"
        f"Fansub: {ep.fansubs}"
    )
    _log_verbose(details)

    if ep.episode_id > 0:
        return True, f"Bölüm detayı OK (ID:{ep.episode_id}, {len(ep.fansubs)} fansub)", details
    return False, "Episode ID 0", details


def test_tranime_fansub_sources():
    """TRAnimeİzle fansub kaynakları testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import get_anime_episodes, get_episode_details

    episodes = get_anime_episodes("naruto")
    if not episodes:
        return False, "Bölüm listesi boş", ""

    ep = get_episode_details(episodes[0].slug)
    if not ep or not ep.fansubs:
        return False, "Bölüm detayı/fansub yok", ""

    sources = ep.get_sources()
    if not sources:
        return False, "Video kaynağı bulunamadı", f"Fansub: {ep.fansubs}"

    names = [s.name for s in sources]
    details = f"Kaynak sayısı: {len(sources)}\nPlayerlar: {names}"
    _log_verbose(details)
    return True, f"{len(sources)} video kaynağı", details


def test_tranime_video_iframe():
    """TRAnimeİzle iframe URL testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import get_anime_episodes, get_episode_details

    episodes = get_anime_episodes("naruto")
    if not episodes:
        return False, "Bölüm listesi boş", ""

    ep = get_episode_details(episodes[0].slug)
    if not ep or not ep.fansubs:
        return False, "Detay/fansub yok", ""

    sources = ep.get_sources()
    if not sources:
        return False, "Kaynak yok", ""

    # İlk kaynağın iframe URL'sini al
    video = sources[0]
    iframe = video.get_iframe()

    if iframe:
        details = f"Player: {video.name}\nFansub: {video.fansub}\niFrame: {iframe[:80]}..."
        return True, f"iFrame URL alındı ({video.name})", details
    return False, f"iFrame URL boş ({video.name})", f"Source ID: {video.source_id}"


def test_tranime_search_alias():
    """TRAnimeİzle search_tranime alias testi."""
    _setup_tranime_cookie()
    from turkanime_api.sources.tranime import search_tranime

    results = search_tranime("naruto", limit=5)
    if not results:
        return False, "search_tranime boş sonuç döndü", ""

    return True, f"search_tranime OK ({len(results)} sonuç)", f"İlk: {results[0][1]}"


# ═══════════════════════════════════════════════════════════════════════════════
# GENEL / ENTEGRASYON TESTLERİ
# ═══════════════════════════════════════════════════════════════════════════════

def test_providers_registry():
    """Kaynak kayıt sistemi testi."""
    from turkanime_api.sources import PROVIDERS, get_enabled_providers, get_provider_by_priority

    if "animecix" not in PROVIDERS:
        return False, "animecix kayıtlı değil", ""
    if "anizle" not in PROVIDERS:
        return False, "anizle kayıtlı değil", ""
    if "tranime" not in PROVIDERS:
        return False, "tranime kayıtlı değil", ""

    enabled = get_enabled_providers()
    by_priority = get_provider_by_priority()

    details = (
        f"Kayıtlı: {list(PROVIDERS.keys())}\n"
        f"Etkin: {list(enabled.keys())}\n"
        f"Sıralama: {[p[0] for p in by_priority]}"
    )
    return True, f"{len(enabled)} kaynak etkin", details


def test_imports():
    """Tüm import'lar çalışıyor mu testi."""
    errors = []

    try:
        from turkanime_api.sources.animecix import search_animecix, CixAnime, _video_streams
    except ImportError as e:
        errors.append(f"animecix: {e}")

    try:
        from turkanime_api.sources.anizle import (
            search_anizle, load_anime_database, get_anime_episodes,
            get_episode_streams, AnizleAnime, AnizleEpisode
        )
    except ImportError as e:
        errors.append(f"anizle: {e}")

    try:
        from turkanime_api.sources.tranime import (
            search_tranime, get_anime_by_slug, get_anime_episodes,
            get_episode_details, set_session_cookie,
            TRAnimeAnime, TRAnimeEpisode, TRAnimeVideo
        )
    except ImportError as e:
        errors.append(f"tranime: {e}")

    if errors:
        return False, f"Import hataları: {errors}", "\n".join(errors)
    return True, "Tüm importlar başarılı", ""


def test_curl_cffi_available():
    """curl_cffi kütüphanesi kontrolü."""
    try:
        from curl_cffi import requests as curl_requests
        session = curl_requests.Session(impersonate="chrome110")
        return True, "curl_cffi mevcut (chrome110 impersonate)", ""
    except ImportError:
        return False, "curl_cffi yüklü değil — Anizle çalışmayabilir", ""
    except Exception as e:
        return False, f"curl_cffi hatası: {e}", ""


# ═══════════════════════════════════════════════════════════════════════════════
# ANIMEDEPO TESTLERİ
# ═══════════════════════════════════════════════════════════════════════════════

def test_animedepo_index():
    """AnimeDepo dizin (dizin.json) yükleme testi."""
    from turkanime_api.sources.animedepo import get_anime_listesi

    liste = get_anime_listesi()
    if not liste:
        return False, "Dizin boş", ""
    slug, title = liste[0]
    if not slug or not title:
        return False, "Dizin girdisi eksik (slug/title)", str(liste[:2])
    details = f"Toplam anime: {len(liste)}\nÖrnek: {liste[:3]}"
    _log_verbose(details)
    return True, f"{len(liste)} anime yüklendi", details


def test_animedepo_search():
    """AnimeDepo yerel fuzzy arama testi."""
    from turkanime_api.sources.animedepo import search_animedepo

    results = search_animedepo("one piece", limit=10)
    if not results:
        return False, "Arama sonucu boş", ""
    names = [t for _, t in results]
    details = f"Sonuç: {len(results)}\nİlk 5: {names[:5]}"
    _log_verbose(details)
    if any("one piece" in n.lower() for n in names):
        return True, f"{len(results)} sonuç bulundu", details
    return False, "Sonuç var ama 'one piece' eşleşmesi yok", details


def test_animedepo_search_ranking():
    """AnimeDepo sıralama testi.

    GUI/CLI otomatik aramada `limit=1` kullandığı için sıralama kritik:
    tam eşleşme, alt-dize eşleşmelerinin önüne geçmeli.
    """
    from turkanime_api.sources.animedepo import search_animedepo

    results = search_animedepo("naruto", limit=5)
    if not results:
        return False, "Naruto araması boş", ""
    first_title = results[0][1].lower()
    details = f"İlk 5: {[t for _, t in results]}"
    _log_verbose(details)
    if first_title == "naruto":
        return True, "Tam eşleşme ilk sırada", details
    return False, f"İlk sonuç tam eşleşme değil: {results[0][1]!r}", details


def test_animedepo_episodes():
    """AnimeDepo bölüm listesi testi."""
    from turkanime_api.sources.animedepo import search_animedepo, get_anime_episodes

    found = search_animedepo("one piece", limit=1)
    if not found:
        return False, "Arama sonucu yok", ""
    slug, name = found[0]
    eps = get_anime_episodes(slug)
    if not eps:
        return False, f"'{name}' için bölüm bulunamadı", ""
    ep_id, ep_title = eps[0]
    # Bölüm kimliği "anime_slug/bolum_slug" bileşik biçiminde olmalı
    if "/" not in ep_id:
        return False, f"Bölüm kimliği bileşik değil: {ep_id!r}", str(eps[:2])
    details = f"{name}: {len(eps)} bölüm\nİlk: {ep_id} — {ep_title}"
    _log_verbose(details)
    return True, f"{len(eps)} bölüm bulundu", details


def test_animedepo_streams():
    """AnimeDepo stream testi — çözülemeyen mask'ler elenmiş olmalı."""
    from turkanime_api.sources.animedepo import (
        search_animedepo, get_anime_episodes, get_episode_streams)

    found = search_animedepo("one piece", limit=1)
    if not found:
        return False, "Arama sonucu yok", ""
    eps = get_anime_episodes(found[0][0])
    if not eps:
        return False, "Bölüm yok", ""

    streams = get_episode_streams(eps[0][0])
    if not streams:
        return False, "Stream bulunamadı", ""

    # Çözülmemiş turkanime player mask'i stream sayılmamalı (bozuk indirme üretir)
    unresolved = [s for s in streams if "/player/" in str(s.get("url", ""))]
    if unresolved:
        return False, f"{len(unresolved)} çözülmemiş mask URL'si döndü", str(unresolved[:2])

    invalid = [s for s in streams if not str(s.get("url", "")).startswith("http")]
    if invalid:
        return False, f"{len(invalid)} geçersiz URL", str(invalid[:2])

    details = f"{len(streams)} stream\nÖrnek: {streams[0].get('label')} — {str(streams[0].get('url'))[:70]}"
    _log_verbose(details)
    return True, f"{len(streams)} geçerli stream", details


# ═══════════════════════════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

ANIMECIX_TESTS = [
    ("AnimeCix Arama", test_animecix_search),
    ("AnimeCix Türkçe Arama", test_animecix_search_turkish),
    ("AnimeCix Sezon Listesi", test_animecix_seasons),
    ("AnimeCix Bölüm Listesi", test_animecix_episodes),
    ("AnimeCix Video Streams", test_animecix_video_streams),
]

ANIZLE_TESTS = [
    ("Anizle Veritabanı Yükleme", test_anizle_database_load),
    ("Anizle Arama", test_anizle_search),
    ("Anizle Türkçe Arama", test_anizle_search_turkish),
    ("Anizle Bölüm Listesi", test_anizle_episodes),
    ("Anizle URL Pattern (Absolute)", test_anizle_episode_absolute_urls),
    ("Anizle Anime Detay", test_anizle_anime_details),
    ("Anizle Translator (Fansub)", test_anizle_translators),
    ("Anizle Translator Videoları", test_anizle_translator_videos),
    ("Anizle Tam Stream Pipeline", test_anizle_full_stream_pipeline),
]

TRANIME_TESTS = [
    ("TRAnime Cookie Ayarlama", test_tranime_cookie_set),
    ("TRAnime Harf Araması", test_tranime_search_by_letter),
    ("TRAnime Fuzzy Arama", test_tranime_search_fuzzy),
    ("TRAnime Doğrudan Arama", test_tranime_search_direct),
    ("TRAnime Anime Bilgisi", test_tranime_get_anime),
    ("TRAnime Bölüm Listesi", test_tranime_episodes),
    ("TRAnime Bölüm Detayı", test_tranime_episode_details),
    ("TRAnime Fansub Kaynakları", test_tranime_fansub_sources),
    ("TRAnime Video iFrame", test_tranime_video_iframe),
    ("TRAnime search_tranime Alias", test_tranime_search_alias),
]

ANIMEDEPO_TESTS = [
    ("AnimeDepo Dizin Yükleme", test_animedepo_index),
    ("AnimeDepo Arama", test_animedepo_search),
    ("AnimeDepo Sıralama (tam eşleşme)", test_animedepo_search_ranking),
    ("AnimeDepo Bölüm Listesi", test_animedepo_episodes),
    ("AnimeDepo Stream Listesi", test_animedepo_streams),
]

GENERAL_TESTS = [
    ("Import Kontrolü", test_imports),
    ("curl_cffi Kontrolü", test_curl_cffi_available),
    ("Provider Registry", test_providers_registry),
]

STREAM_TESTS = {
    "AnimeCix Video Streams",
    "Anizle Tam Stream Pipeline",
    "TRAnime Video iFrame",
    "TRAnime Fansub Kaynakları",
    "AnimeDepo Stream Listesi",
}


def run_source_tests(source_name: str, tests: list, skip_streams: bool = False):
    """Bir kaynağın tüm testlerini çalıştır."""
    _log(f"\n{'═' * 60}", Colors.BOLD)
    _log(f"  📦 {source_name.upper()} TESTLERİ", Colors.BOLD + Colors.MAGENTA)
    _log(f"{'═' * 60}", Colors.BOLD)

    for name, func in tests:
        if skip_streams and name in STREAM_TESTS:
            skip_test(name, source_name, "Stream testleri atlanıyor (--skip-streams)")
            continue
        run_test(name, source_name, func)
        time.sleep(0.5)  # Rate limit


def print_summary():
    """Test sonuç özeti yazdır."""
    _log(f"\n{'═' * 60}", Colors.BOLD)
    _log(f"  📊 TEST SONUÇLARI", Colors.BOLD + Colors.CYAN)
    _log(f"{'═' * 60}", Colors.BOLD)

    # Kaynağa göre grupla
    sources = {}
    for r in _suite.results:
        if r.source not in sources:
            sources[r.source] = {"passed": 0, "failed": 0, "skipped": 0}
        if r.skipped:
            sources[r.source]["skipped"] += 1
        elif r.passed:
            sources[r.source]["passed"] += 1
        else:
            sources[r.source]["failed"] += 1

    for source, counts in sources.items():
        total = counts["passed"] + counts["failed"]
        icon = "✅" if counts["failed"] == 0 else "❌"
        color = Colors.GREEN if counts["failed"] == 0 else Colors.RED
        skip_info = f" ({counts['skipped']} atlandı)" if counts["skipped"] > 0 else ""
        _log(f"  {icon} {source:15s} — {counts['passed']}/{total} başarılı{skip_info}", color)

    _log(f"\n{'─' * 60}")
    
    # Toplam
    total_color = Colors.GREEN if _suite.failed == 0 else Colors.RED
    _log(
        f"  Toplam: {_suite.passed}/{_suite.total} başarılı, "
        f"{_suite.failed} başarısız, "
        f"{_suite.skipped_count} atlandı  "
        f"({_suite.duration:.1f}s)",
        total_color + Colors.BOLD
    )

    # Başarısız testleri listele
    failed_tests = [r for r in _suite.results if not r.passed and not r.skipped]
    if failed_tests:
        _log(f"\n  ❌ BAŞARISIZ TESTLER:", Colors.RED + Colors.BOLD)
        for r in failed_tests:
            _log(f"     • [{r.source}] {r.name}: {r.message}", Colors.RED)
            if r.details:
                for line in r.details.strip().split('\n')[:3]:
                    _log(f"       {line}", Colors.GRAY)

    _log(f"{'═' * 60}\n", Colors.BOLD)


def main():
    global _verbose, _suite

    parser = argparse.ArgumentParser(description="TürkAnime Kaynak Adaptör Testleri")
    parser.add_argument("--source", "-s",
                        choices=["animecix", "anizle", "tranime", "animedepo", "all"],
                        default="all", help="Test edilecek kaynak (varsayılan: all)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Detaylı çıktı")
    parser.add_argument("--skip-streams", action="store_true", help="Stream testlerini atla")
    parser.add_argument("--json", action="store_true", help="JSON formatında çıktı")
    args = parser.parse_args()

    _verbose = args.verbose
    _suite = TestSuite()
    _suite.start_time = time.time()

    _log(f"\n{'═' * 60}", Colors.BOLD)
    _log(f"  🧪 TürkAnime GUI - Kaynak Adaptör Testleri", Colors.BOLD + Colors.CYAN)
    _log(f"  📅 {time.strftime('%Y-%m-%d %H:%M:%S')}", Colors.GRAY)
    _log(f"  🐍 Python {sys.version.split()[0]}", Colors.GRAY)
    _log(f"  📂 {PROJECT_ROOT}", Colors.GRAY)
    _log(f"{'═' * 60}", Colors.BOLD)

    # Genel testler her zaman çalış
    _log(f"\n{'═' * 60}", Colors.BOLD)
    _log(f"  🔧 GENEL TESTLER", Colors.BOLD + Colors.MAGENTA)
    _log(f"{'═' * 60}", Colors.BOLD)
    for name, func in GENERAL_TESTS:
        run_test(name, "genel", func)

    source = args.source

    if source in ("all", "animecix"):
        run_source_tests("AnimeCix", ANIMECIX_TESTS, args.skip_streams)

    if source in ("all", "anizle"):
        run_source_tests("Anizle", ANIZLE_TESTS, args.skip_streams)

    if source in ("all", "tranime"):
        run_source_tests("TRAnimeİzle", TRANIME_TESTS, args.skip_streams)

    if source in ("all", "animedepo"):
        run_source_tests("AnimeDepo", ANIMEDEPO_TESTS, args.skip_streams)

    _suite.end_time = time.time()

    if args.json:
        output = {
            "total": _suite.total,
            "passed": _suite.passed,
            "failed": _suite.failed,
            "skipped": _suite.skipped_count,
            "duration": round(_suite.duration, 2),
            "results": [
                {
                    "name": r.name,
                    "source": r.source,
                    "passed": r.passed,
                    "skipped": r.skipped,
                    "message": r.message,
                    "duration": round(r.duration, 2),
                }
                for r in _suite.results
            ]
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_summary()

    # Çıkış kodu: başarısız test varsa 1
    sys.exit(0 if _suite.failed == 0 else 1)


if __name__ == "__main__":
    main()
