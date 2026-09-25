"""
Anizle/Anizm kaynağı için standalone istemci.

Anizm aynı veritabanını ve aynı sayfaları birden çok alan adından sunuyor
(bkz. `AYNALAR`). Modül Cloudflare'e takılmayan aynayı seçip onunla konuşur;
pahalı CF bypass zinciri yalnızca bütün aynalar engelliyken devreye girer.

Arama: `getAnimeListForSearch` (~8.8 MB JSON, bütün katalog) bir kez indirilir,
diske (`<veri kökü>/onbellek/anizle_db.json`) yazılır ve yerelde bulanık
eşleşmeyle aranır.

Bölüm listesi: HER ZAMAN animenin sayfasından. Veritabanındaki `lastEpisode`
yalnızca en yeni 1-3 bölüm (bkz. `get_anime_episodes`).

Akış (stream) zinciri:
1. Bölüm sayfası → translator (fansub) butonları
2. Translator ucu → video butonları
3. Video ucu → player iframe'i; player sayfası, video adresiyle AYNI konaktan
   alınır (konak sabit yazılmıyor, adresten türetiliyor)
4. Player sayfasında iki tür oynatıcı var:
   a) Eski FirePlayer: paketli JS'ten FirePlayer kimliği çözülür
      (`_extract_fireplayer_id`) → anizmplayer.com `getVideo` ucu gerçek
      adresi verir (`_get_video_stream_from_player`).
   b) Yeni video.js / HLS: sayfadaki `masterUrl` / `nativeMasterUrl`
      (`_extract_hls_stream`). Adres yalnızca yanıt `#EXTM3U` ile başlıyorsa
      kabul edilir; bu uçlar gömülü player dışından çoğunlukla 404 veriyor.
5. Yerelde hiçbir şey çıkmazsa uzak sunucu (`SERVER_URL`) yedeği.
"""
from __future__ import annotations

import os
import re
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Callable
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from turkanime_api.common.hatalar import (
    KaynakEngellendi, KaynakYanitVermedi,
)

# CF Bypass modülünü içe aktar
try:
    from turkanime_api.common.cf_bypass import (
        CFSession, CFBypassError, get_cf_session,
        ENGEL_DURUMLARI, CHALLENGE_MARKERS,
    )
    HAS_CF_BYPASS = True
except ImportError:
    HAS_CF_BYPASS = False
    ENGEL_DURUMLARI = frozenset({403, 429, 503})
    CHALLENGE_MARKERS = ("Just a moment", "Checking your browser", "challenge-platform")

import requests

# ============================================================================
# Konfigürasyon
# ============================================================================

# Anizm'in aynaları, deneme sırasıyla. Ölçüm (2026-09-25, veri merkezi IP'si):
# anizle.co, anizm.com.tr ve puffytr.com `/getAnimeListForSearch`'te bayt bayt
# aynı JSON'u (8.808.024 B) ve aynı bölüm slug'larını challenge'sız veriyor;
# anizm.pro (ve anizm.net) 403 + "Just a moment" Cloudflare sayfası döndürüyor.
# Eskiden tek konak anizm.pro'ydu: engellenen her istek curl → CFSession (5
# yöntem × 3 deneme, 65 sn'lik FlareSolverr) → requests zincirini baştan
# yürüyordu. anizm.pro sonda duruyor: ev bağlantılarında hâlâ açılabiliyor.
AYNALAR: Tuple[str, ...] = (
    "https://anizle.co",
    "https://anizm.com.tr",
    "https://puffytr.com",
    "https://anizm.pro",
)
# Kullanıcının/sunucunun kendi aynası (ör. yeni bir alan adı): listenin başına.
AYNA_ORTAM_ANAHTARI = "TURKANIME_ANIZLE_URL"

# Varsayılan ayna. anizle.com → anizle.org → anizle.co (301 zinciri) burada biter.
BASE_URL = "https://anizle.co"
# API (bölüm sayfası, translator ve video uçları) da aynı konakta: anizle.co
API_BASE_URL = "https://anizle.co"
ANIME_LIST_YOLU = "/getAnimeListForSearch"
ANIME_LIST_URL = f"{BASE_URL}{ANIME_LIST_YOLU}"
PLAYER_BASE_URL = "https://anizmplayer.com"

# Fallback: Uzak sunucu (eski yöntem)
SERVER_URL = "https://turkanimeapi.bariskeser.com"
USE_REMOTE_SERVER = False  # True yapılırsa eski sunucu kullanılır

# Hız ayarları
HTTP_TIMEOUT = 10  # Varsayılan timeout (saniye)
MAX_WORKERS = 8  # Paralel işlem sayısı

# Veritabanı disk önbelleği. Sunucu `cache-control: max-age=3600` gönderiyor;
# katalog günde birkaç yeni anime alıyor, 8.8 MB'ı her açılışta indirmek
# gereksiz. 24 saatten eskisi taze sayılmaz ama bütün aynalar düşükken
# (engel, kesinti) hiç yoktan iyidir: arama eski katalogla çalışmaya devam eder.
DB_ONBELLEK_KLASORU = "onbellek"
DB_ONBELLEK_ADI = "anizle_db.json"
DB_TAZELIK_SN = 24 * 3600
# Başarısız yüklemeden sonra bu süre ağa çıkılmaz. Eskiden hata hatırlanmıyordu:
# her arama engelli zinciri baştan yürüyor, arka plan iş parçacığı aramanın 25
# sn'lik sınırını aşıp birikiyordu.
GERI_CEKILME_SN = 5 * 60

# Global anime veritabanı (cache)
_anime_database: List[Dict[str, Any]] = []
_database_loaded: bool = False
_db_yuklenme_zamani: float = 0.0          # time.monotonic()
_son_db_hatasi_zamani: float = 0.0        # time.monotonic(); 0 = hata yok
_son_db_hatasi: Optional[Tuple[type, str]] = None
_db_kilidi = threading.Lock()             # aramalar paralel iş parçacığında

# Bu süreçte çalıştığı görülen ayna; sonraki istekler önce ona gider.
_secili_kok: Optional[str] = None

# Global CF session
_cf_session: Optional[Any] = None

_TARAYICI_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _engellenmis(yanit: Any) -> bool:
    """Yanıt WAF/Cloudflare tarafından kesilmiş mi?

    curl_cffi bir challenge sayfasını hata saymaz; HTTP 403/503 ile birlikte
    "Just a moment" gövdesini sorunsuzca döndürür. Bunu engel saymazsak
    ayrıştırıcı boş sonuç üretiyor ve arkasındaki bypass zinciri hiç
    denenmiyor — kullanıcı "kaynak çalışmıyor" görüyor.
    """
    if yanit is None:
        return True
    if getattr(yanit, "status_code", 200) in ENGEL_DURUMLARI:
        return True
    try:
        govde = yanit.text
    except Exception:
        return False
    if not govde:
        return False
    dusuk = govde[:4000].lower()
    return any(iz.lower() in dusuk for iz in CHALLENGE_MARKERS)


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


def _get_basliklari(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    basliklar = {
        "User-Agent": _TARAYICI_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if headers:
        basliklar.update(headers)
    return basliklar


def _curl_get(url: str, timeout: int, headers: Dict[str, str],
              yonlendir: bool = True) -> Optional[Any]:
    """Yalnızca curl_cffi (TLS parmak izi taklidi); ağ hatasında None."""
    try:
        from curl_cffi import requests as curl_requests
        session = curl_requests.Session(impersonate="chrome110")
        return session.get(url, headers=headers, timeout=timeout,
                           allow_redirects=yonlendir)
    except Exception:
        return None


def _http_get(url: str, timeout: int = 60, headers: Optional[Dict[str, str]] = None,
              *, curl_denendi: bool = False) -> Optional[Any]:
    """HTTP GET isteği yap (curl_cffi → CF bypass zinciri → requests).

    ``curl_denendi``: çağıran (`_aynadan_get`) aynı adresi az önce curl ile
    denedi ve engele takıldı; ilk kademeyi ikinci kez yürümenin anlamı yok.
    """
    default_headers = _get_basliklari(headers)

    # 1) curl_cffi (TLS parmak izi taklidi) — en hızlısı, çoğu zaman yeter.
    #
    # DİKKAT: `session.get` eskiden `except ImportError` bloğunun içindeydi.
    # curl_cffi zorunlu bağımlılık olduğu için o except hiç tetiklenmiyordu;
    # istek ağ hatasıyla düşse de 403 challenge dönse de aşağıdaki iki kademe
    # ÖLÜ KODDU. Anizle CF arkasındaki kaynak olduğu için pratikte "kaynak
    # çalışmıyor" demekti. Artık her kademe gerçekten sırasını alıyor.
    if not curl_denendi:
        yanit = _curl_get(url, timeout, default_headers)
        if not _engellenmis(yanit):
            return yanit

    # 2) CF bypass zinciri (cloudscraper → FlareSolverr → QtWebEngine → requests)
    if HAS_CF_BYPASS:
        try:
            session = _get_cf_session()
            yanit = session.get(url, headers=default_headers)
            if not _engellenmis(yanit):
                return yanit
        except Exception:
            pass

    # 3) Son çare: düz requests
    try:
        return requests.get(url, headers=default_headers, timeout=timeout)
    except Exception:
        # Sessiz başarısızlık - paralel işlemde çok fazla hata mesajı olmasın
        return None


def _http_post(url: str, timeout: int = 60, headers: Optional[Dict[str, str]] = None, data: Optional[Dict] = None) -> Optional[Any]:
    """HTTP POST isteği yap (curl_cffi ile)."""

    default_headers = {
        "User-Agent": _TARAYICI_UA,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    if headers:
        default_headers.update(headers)

    # Kademeler `_http_get` ile aynı; gerekçesi için oradaki nota bak.
    try:
        from curl_cffi import requests as curl_requests
        session = curl_requests.Session(impersonate="chrome110")
        yanit = session.post(url, headers=default_headers, timeout=timeout, data=data)
        if not _engellenmis(yanit):
            return yanit
    except ImportError:
        pass
    except Exception:
        pass

    if HAS_CF_BYPASS:
        try:
            session = _get_cf_session()
            yanit = session.post(url, headers=default_headers, data=data)
            if not _engellenmis(yanit):
                return yanit
        except Exception:
            pass

    try:
        return requests.post(url, headers=default_headers, timeout=timeout, data=data)
    except Exception as e:
        print(f"[Anizle] HTTP POST hatası ({url}): {e}")
        return None


# ============================================================================
# Ayna seçimi
# ============================================================================

def aday_kokler() -> List[str]:
    """Denenecek aynalar, sırayla: çalıştığı görülen → ortam değişkeni → `AYNALAR`.

    Ortam değişkeni her çağrıda okunuyor (tek `os.environ` bakışı); süreç
    ortasında verilen değer de geçerli olsun, sıfırlama fonksiyonu gerekmesin.
    """
    ortam = (os.environ.get(AYNA_ORTAM_ANAHTARI) or "").strip().rstrip("/")
    sira: List[str] = []
    for kok in (_secili_kok, ortam, *AYNALAR):
        if kok and kok not in sira:
            sira.append(kok)
    return sira


def _kok() -> str:
    """Sayfa/poster adreslerinin kurulacağı ayna (seçilmiş ya da ilk aday)."""
    return aday_kokler()[0]


def _bilinen_konak(url: str) -> bool:
    """Adres Anizm aynalarından birinde mi (eski anizle.org dahil)?"""
    konak = (urlparse(url).hostname or "").lower()
    if konak.startswith("www."):
        konak = konak[4:]
    bilinen = {(urlparse(k).hostname or "").lower() for k in aday_kokler()}
    return konak in bilinen or konak == "anizle.org"


def _yola_cevir(adres: str) -> Optional[str]:
    """Slug ya da Anizm adresini aynadan bağımsız ``/yol``'a çevir.

    Başka bir konağın tam adresiyse None: çağıran onu olduğu gibi kullanır.
    """
    adres = (adres or "").strip()
    if adres.startswith(("http://", "https://")):
        if not _bilinen_konak(adres):
            return None
        parca = urlparse(adres)
        return (parca.path or "/") + (f"?{parca.query}" if parca.query else "")
    return "/" + adres.lstrip("/")


def _aynadan_get(yol: str, timeout: int = 60, headers: Optional[Dict[str, str]] = None,
                 gecerli: Optional[Callable[[Any], bool]] = None) -> Optional[Any]:
    """``yol``'u aynalarda sırayla dene; ilk geçerli yanıtı veren aynayı hatırla.

    Her ayna yalnızca curl ile (hızlı kademe) deneniyor. Hepsi engelliyse
    CF bypass zinciri BİR kez, ilk adayda yürüyor: o zincir FlareSolverr'a
    (varsayılanı üçüncü taraf bir sunucu) adres gönderiyor ve dakikalar
    sürebiliyor; engelsiz bir ayna varken ona hiç gerek yok.

    ``gecerli``: yanıtın kabul ölçütü; varsayılanı "engel değil". 404 gibi
    engel olmayan yanıtlar varsayılan ölçütte kabul ediliyor, çünkü aynalar
    aynı içeriği sunuyor: birinde olmayan sayfa ötekinde de yok.
    """
    global _secili_kok
    olcut = gecerli or (lambda y: not _engellenmis(y))
    basliklar = _get_basliklari(headers)
    adaylar = aday_kokler()
    son_yanit = None
    for kok in adaylar:
        yanit = _curl_get(kok + yol, timeout, basliklar)
        if yanit is not None and olcut(yanit):
            _secili_kok = kok
            return yanit
        son_yanit = yanit if yanit is not None else son_yanit
    zincir = _http_get(adaylar[0] + yol, timeout, headers, curl_denendi=True)
    # Zincir de düştüyse aynaların (engel) yanıtı döner: çağıran "ulaşılamadı"
    # değil "Cloudflare engeli" diyebilsin.
    return zincir if zincir is not None else son_yanit


def _erisim_hatasi(yanit: Any, is_adi: str) -> Tuple[type, str]:
    """Okunamayan yanıttan (hata sınıfı, kullanıcı cümlesi)."""
    if yanit is not None and _engellenmis(yanit):
        return KaynakEngellendi, (
            f"Anizle: {is_adi} — Cloudflare engeli: bütün Anizm aynaları "
            "bot doğrulaması istedi; biraz sonra yeniden deneyin ya da başka "
            "kaynak seçin")
    if yanit is not None:
        return KaynakYanitVermedi, (
            f"Anizle: {is_adi} — HTTP {getattr(yanit, 'status_code', '?')}: "
            "Anizm sunucusu hata verdi")
    return KaynakYanitVermedi, (
        f"Anizle: {is_adi} — Anizm aynalarına ulaşılamadı (bağlantı/zaman "
        "aşımı)")


# ============================================================================
# Anime Veritabanı Yönetimi
# ============================================================================

def _db_onbellek_yolu() -> Path:
    """Disk önbelleği; kök kuralı arşiv/görsel önbellekleriyle aynı yerde."""
    from .animedepo import veri_koku
    return veri_koku() / DB_ONBELLEK_KLASORU / DB_ONBELLEK_ADI


def _diskten_oku(en_fazla_yas: Optional[float]) -> Optional[List[Dict[str, Any]]]:
    """Disk önbelleğindeki katalog; yoksa, bozuksa ya da eskiyse None.

    Yaş dosyanın mtime'ından: dosya sunucunun gönderdiği gövdenin kendisi
    (bayt bayt), 8.8 MB'ı zaman damgalı bir zarfa sarmak için yeniden
    serileştirmek gereksiz.
    """
    try:
        yol = _db_onbellek_yolu()
        if en_fazla_yas is not None and time.time() - yol.stat().st_mtime > en_fazla_yas:
            return None
        veri = json.loads(yol.read_bytes())
    except (OSError, ValueError):
        return None
    return veri if isinstance(veri, list) and veri else None


def _diske_yaz(govde: bytes) -> None:
    """Gövdeyi atomik yaz (yarım dosya bir sonraki açılışta bozuk JSON olmasın)."""
    try:
        yol = _db_onbellek_yolu()
        yol.parent.mkdir(parents=True, exist_ok=True)
        gecici = yol.with_name(yol.name + ".tmp")
        gecici.write_bytes(govde)
        os.replace(gecici, yol)
    except OSError as e:
        print(f"[Anizle] Veritabanı önbelleğe yazılamadı: {e}")


def _db_yaniti_gecerli(yanit: Any) -> bool:
    """Katalog yanıtı mı? (Engel sayfası, 404 ya da HTML değil.)"""
    if _engellenmis(yanit) or getattr(yanit, "status_code", 0) != 200:
        return False
    try:
        return (yanit.text or "").lstrip()[:1] == "["
    except Exception:
        return False


def _agdan_indir() -> Tuple[Optional[List[Dict[str, Any]]], Optional[bytes], Any]:
    """(katalog, ham gövde, son yanıt); başarısızsa katalog None."""
    yanit = _aynadan_get(ANIME_LIST_YOLU, timeout=120, gecerli=_db_yaniti_gecerli)
    if yanit is None or not _db_yaniti_gecerli(yanit):
        return None, None, yanit
    try:
        govde = yanit.content
        if isinstance(govde, str):
            govde = govde.encode("utf-8")
        veri = json.loads(govde)
    except (ValueError, TypeError, AttributeError):
        return None, None, yanit
    if not isinstance(veri, list) or not veri:
        return None, None, yanit
    return veri, govde, yanit


def load_anime_database(force_reload: bool = False) -> List[Dict[str, Any]]:
    """
    Anizm'in bütün kataloğunu yükle (bellek → disk önbelleği → aynalar).

    API Endpoint: <ayna>/getAnimeListForSearch

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
    - lastEpisode: list (EN YENİ 1-3 bölüm; tam liste DEĞİL)
    - categories: list (kategoriler)

    Ağ başarısızsa `GERI_CEKILME_SN` boyunca yeniden denenmez; o sürede ve
    hiçbir ayna yanıt vermezken eski disk önbelleği (varsa) döner.
    ``force_reload`` bellek/disk önbelleğini ve geri çekilmeyi atlar.
    """
    global _anime_database, _database_loaded, _db_yuklenme_zamani
    global _son_db_hatasi_zamani, _son_db_hatasi

    def _bellekte_taze() -> bool:
        return (_database_loaded
                and time.monotonic() - _db_yuklenme_zamani < DB_TAZELIK_SN)

    if not force_reload and _bellekte_taze():
        return _anime_database

    with _db_kilidi:
        # Kilidi beklerken başka bir iş parçacığı yüklemiş olabilir.
        if not force_reload and _bellekte_taze():
            return _anime_database

        if not force_reload:
            disk = _diskten_oku(DB_TAZELIK_SN)
            if disk is not None:
                _anime_database, _database_loaded = disk, True
                _db_yuklenme_zamani = time.monotonic()
                return _anime_database
            if (_son_db_hatasi_zamani
                    and time.monotonic() - _son_db_hatasi_zamani < GERI_CEKILME_SN):
                return _anime_database or _diskten_oku(None) or []

        veri, govde, yanit = _agdan_indir()
        if veri is not None:
            _anime_database, _database_loaded = veri, True
            _db_yuklenme_zamani = time.monotonic()
            _son_db_hatasi_zamani, _son_db_hatasi = 0.0, None
            if govde:
                _diske_yaz(govde)
            return _anime_database

        _son_db_hatasi_zamani = time.monotonic()
        _son_db_hatasi = _erisim_hatasi(yanit, "katalog indirilemedi")
        # Eski önbellek taze sayılmaz ama bütün aynalar düşükken aramayı
        # ayakta tutar. `_database_loaded` kapalı kalıyor: geri çekilme bitince
        # yeniden indirme denenir.
        eski = _anime_database or _diskten_oku(None)
        if eski:
            _anime_database = eski
        return eski or []


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

    Raises:
        KaynakHatasi: katalog indirilemedi VE uzak sunucu da yanıt vermedi
        ("0 sonuç" demek yanlış olurdu; arama sayfası sebebi gösteriyor).
    """
    # Uzak sunucu modunda eski API'yi kullan
    if USE_REMOTE_SERVER:
        return _search_remote(query, limit, timeout)

    # Veritabanını yükle
    database = load_anime_database()
    if not database:
        print("[Anizle] Veritabanı boş, uzak sunucu deneniyor...")
        try:
            return _uzak_ara(query, limit, timeout)
        except Exception as e:
            sinif, mesaj = _son_db_hatasi or (
                KaynakYanitVermedi, "Anizle: katalog indirilemedi")
            raise sinif(f"{mesaj}; yedek sunucu da yanıt vermedi") from e

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


def _uzak_ara(query: str, limit: int = 20, timeout: int = 60) -> List[Tuple[str, str]]:
    """Uzak sunucuda ara; ağ/HTTP hatası yükselir."""
    response = requests.get(
        f"{SERVER_URL}/anizle/search",
        params={"q": query, "limit": limit},
        timeout=timeout
    )
    response.raise_for_status()
    # Bölüm ucuyla aynı sözleşme sorunu: sunucu sözlük döndürüyor.
    return _ikiliye_cevir(response.json())[:limit]


def _search_remote(query: str, limit: int = 20, timeout: int = 60) -> List[Tuple[str, str]]:
    """Uzak sunucu üzerinden arama yap (fallback); hata = boş liste."""
    try:
        return _uzak_ara(query, limit, timeout)
    except Exception as e:
        print(f"[Anizle] Uzak sunucu arama hatası: {e}")
        return []


# ============================================================================
# Bölüm Listeleme
# ============================================================================

def get_anime_episodes(slug: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """
    Bir animenin bütün bölümlerini getir (artan sırada, bölüm numarasına göre tekil).

    Veritabanındaki `lastEpisode` BİLEREK kullanılmıyor. Eskiden slug
    veritabanında bulununca o alan "tam liste" diye döndürülüyordu; oysa alan
    yalnızca en yeni 1-3 bölümü (yeniden eskiye) tutuyor. Canlı katalogda
    dağılım {0: 147, 1: 780, 2: 213, 3: 3708}: 4.848 animenin 4.701'i 1-3
    bölümle, ters sırada görünüyordu (One Piece: 1179, 1178, 1177). Sayfa
    ayrıştırıcısına yalnızca katalogda olmayan slug'larda ulaşılıyordu. Aynı
    fonksiyon sunucu tarayıcısını ve yedek sunucuyu da besliyor; onların
    listeleri de kesikti.

    Args:
        slug: Anime slug'ı (örn: "one-piece")
        timeout: Zaman aşımı (saniye)

    Returns:
        Liste[Tuple[episode_slug, episode_title]] formatında bölümler

    Raises:
        KaynakHatasi: sayfa okunamadı (engel/ağ) VE uzak sunucu da yanıt vermedi.
    """
    return _fetch_all_episodes_from_page(slug, timeout)


def _bolumleri_ayikla(html: str) -> List[Tuple[str, str]]:
    """Anime sayfasının HTML'inden (bölüm slug'ı, başlık) listesi, artan sırada."""
    # Site hem absolute hem relative URL kullanabilir:
    # href="https://anizle.co/anime-slug-1-bolum-izle"
    # href="/anime-slug-1-bolum-izle"
    episodes: List[Tuple[int, str, str]] = []
    seen_episode_nums: set = set()  # Bölüm numarasına göre duplikasyon kontrolü

    # Sayfa hangi aynadan geldiyse bağlantılar o konağı taşıyor; hepsi kabul.
    konaklar = "|".join(
        re.escape((urlparse(k).hostname or "").lower()) for k in aday_kokler())
    kok_deseni = rf'https?://(?:www\.)?(?:{konaklar})'

    # Pattern 1: Absolute URL (https://<ayna>/slug-N-bolum-izle)
    abs_pattern = kok_deseni + r'/([^"]+?-(\d+)-bolum[^"]*)'
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
        pattern1 = (r'href="(?:' + kok_deseni + r')?/?([^"]+?-bolum[^"]*)"[^>]*'
                    r'data-order="(\d+)"[^>]*>([^<]+)')
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
                ep_slug_clean = urlparse(ep_slug_clean).path.strip('/')
            try:
                order_num = int(ep_num)
                if order_num not in seen_episode_nums:
                    seen_episode_nums.add(order_num)
                    final_title = title.strip() if title.strip() else f"{ep_num}. Bölüm"
                    episodes.append((order_num, ep_slug_clean, final_title))
            except ValueError:
                pass

    episodes.sort(key=lambda x: x[0])
    return [(ep_slug, title) for _, ep_slug, title in episodes]


def _fetch_all_episodes_from_page(slug: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """Anime sayfasından tüm bölümleri çek; olmazsa uzak sunucu.

    Sayfa hiç okunamadıysa (engel, bağlantı, 5xx) ve uzak sunucu da yanıt
    vermediyse `KaynakHatasi` yükselir: boş liste "bu animenin bölümü yok"
    diye okunuyordu. Sayfa açılıp bölüm çıkmadıysa (404, yayınlanmamış anime)
    uzak sunucunun yanıtı, o da yoksa boş liste.
    """
    yol = _yola_cevir(slug)
    if yol is None:                       # başka bir konağın tam adresi
        response = _http_get(slug.strip(), timeout)
    else:
        response = _aynadan_get(yol, timeout)

    okunamadi = (response is None or _engellenmis(response)
                 or getattr(response, "status_code", 0) >= 500)
    if not okunamadi and response.status_code == 200:
        try:
            bolumler = _bolumleri_ayikla(response.text)
        except Exception as e:
            print(f"[Anizle] Bölüm ayrıştırma hatası: {e}")
            bolumler = []
        if bolumler:
            return bolumler

    try:
        return _uzak_bolumler(slug, timeout)
    except Exception as e:
        if not okunamadi:
            print(f"[Anizle] Uzak sunucu bölüm hatası: {e}")
            return []
        sinif, mesaj = _erisim_hatasi(response, "bölüm listesi alınamadı")
        raise sinif(f"{mesaj}; yedek sunucu da yanıt vermedi") from e


def _ikiliye_cevir(kayitlar: Any) -> List[Tuple[str, str]]:
    """Uzak sunucu yanıtını `(slug, başlık)` ikililerine çevir.

    Sunucu JSON sözlüğü döndürüyor:
        arama   -> [{"id": "naruto", "title": "Naruto"}, …]
        bölümler-> [{"id": "naruto-218-bolum", "title": "218. Bölüm", …}, …]

    Tüketiciler ise `for slug, baslik in …` diyor (imza da
    `List[Tuple[str, str]]`). Eskiden yanıt olduğu gibi döndürülüyordu ve
    sözlük ikiye açılamadığı için `ValueError: too many values to unpack`
    fırlıyordu. Uzak yol tam da ana site engellendiğinde devreye girdiği için
    hata en çok ihtiyaç duyulan anda ortaya çıkıyordu.

    Uzun süre görünmedi çünkü sunucudaki kazıyıcı bozuktu ve boş liste
    dönüyordu; sunucu onarılınca hata canlıya çıktı.
    """
    if not isinstance(kayitlar, list):
        return []
    ikili: List[Tuple[str, str]] = []
    for kayit in kayitlar:
        if isinstance(kayit, dict):
            slug = kayit.get("id") or kayit.get("slug") or kayit.get("episode_slug")
            baslik = (kayit.get("title") or kayit.get("label")
                      or kayit.get("episode_title") or slug)
        elif isinstance(kayit, (list, tuple)) and len(kayit) >= 2:
            slug, baslik = kayit[0], kayit[1]
        else:
            continue
        if slug:
            ikili.append((str(slug), str(baslik or slug)))
    return ikili


def _uzak_bolumler(slug: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """Uzak sunucudaki bölüm listesi; ağ/HTTP hatası yükselir."""
    response = requests.get(
        f"{SERVER_URL}/anizle/episodes/{slug}",
        timeout=timeout
    )
    response.raise_for_status()
    return _ikiliye_cevir(response.json())


def _get_episodes_remote(slug: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """Uzak sunucu üzerinden bölümleri al (fallback); hata = boş liste."""
    try:
        return _uzak_bolumler(slug, timeout)
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


def fireplayer_istegi(sayfa: str, *, form: Optional[Dict[str, str]] = None,
                      referer: Optional[str] = None,
                      timeout: int = 60) -> Optional[Dict[str, Any]]:
    """FirePlayer oynatıcısının `do=getVideo` ucunu çağır; ham JSON sözlüğü.

    FirePlayer bir PHP oynatıcı betiği; aynı betik birden çok sitede çalışıyor
    (anizmplayer.com, Animeler'in play.animeler.pw'si). Tarayıcıdaki JS, oynatıcı
    sayfasının KENDİ adresine `do=getVideo` ekleyip POST ediyor; bu yüzden uç
    sayfa adresinden türetiliyor, sabit yazılmıyor. Anizle'nin eski yolu
    (`/player/index.php?data=<id>`) da aynı betiğe çıkıyor.

    Args:
        sayfa: Oynatıcı sayfasının adresi (sorgu dizgisi taşıyabilir).
        form:  POST gövdesi. Anizle göndermiyor; Animeler'in oynatıcısı
               ``{"hash", "r", "s"}`` ile diğer sunucuları (s1, s2…) veriyor.
        referer: Verilmezse sayfanın kendisi (tarayıcının gönderdiği).

    Returns: JSON sözlüğü; istek başarısız ya da yanıt JSON değilse None.
    """
    ayrac = "&" if "?" in sayfa else "?"
    origin = _origin_of(sayfa) or PLAYER_BASE_URL
    response = _http_post(
        f"{sayfa}{ayrac}do=getVideo",
        timeout=timeout,
        headers={"Referer": referer or sayfa, "Origin": origin},
        data=form,
    )
    if response is None or response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError as e:        # json.JSONDecodeError de ValueError
        print(f"[FirePlayer] JSON parse hatası ({sayfa}): {e}")
        return None
    if not isinstance(data, dict):
        print(f"[FirePlayer] cevap dict değil: {type(data)}")
        return None
    return data


def fireplayer_akisi(data: Dict[str, Any], etiket: str,
                     referer: Optional[str] = None) -> Optional[Dict[str, str]]:
    """`fireplayer_istegi` yanıtındaki oynatılabilir adresi akış sözlüğüne çevir.

    ``securedLink`` (HLS, istek atan IP'ye bağlı imzalı adres) önce gelir;
    yoksa ``videoSource`` doğrudan dosya sayılır. ``videoSrc`` (başka bir
    barındırıcının gömme sayfası) burada ele alınmaz: onu açmak çağıranın işi.
    """
    if data.get("hls") and data.get("securedLink"):
        akis = {"url": data["securedLink"], "label": f"{etiket} (HLS)", "type": "hls"}
    elif data.get("videoSource"):
        akis = {"url": data["videoSource"], "label": etiket, "type": "direct"}
    else:
        return None
    if referer:
        akis["referer"] = referer
    return akis


def _get_video_stream_from_player(player_id: str, video_name: str) -> Optional[Dict[str, str]]:
    """
    FirePlayer ID'sinden gerçek video stream URL'sini al.

    Endpoint: anizmplayer.com/player/index.php?data=ID&do=getVideo
    """
    try:
        data = fireplayer_istegi(
            f"{PLAYER_BASE_URL}/player/index.php?data={player_id}",
            referer=f"{PLAYER_BASE_URL}/player/{player_id}",
        )
        return fireplayer_akisi(data, video_name) if data else None
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
                "Referer": (_origin_of(video_url) or _kok()) + "/",
            }
        )
        
        if response is None or response.status_code != 200:
            return None
        
        data = response.json()
        player_html = data.get("player", "")
        
        # iframe src'den player ID'yi çıkar
        # src="https://anizle.co/player/1538440" -> 1538440
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
                "Referer": (_origin_of(translator_url) or _kok()) + "/",
            }
        )
        
        if response is None or response.status_code != 200:
            return []
        
        data = response.json()
        html = data.get("data", "")
        
        # video attribute'li anchor'ları bul
        # <a href="#" video="https://anizle.co/video/1538440" data-video-name="Player Name">
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

    # Bölüm sayfası aynadan bağımsız bir yola çevrilip aynalarda deneniyor.
    # Eskiden konaklar sabitti (önce anizle.org, sonra anizm.pro): anizle.org
    # yalnızca 301 ile anizle.co'ya gidiyor, anizm.pro ise veri merkezi
    # IP'lerine Cloudflare challenge'ı döndürüyor.
    yol = _yola_cevir(episode_slug)
    try:
        if yol is None:                   # başka bir konağın tam adresi
            response = _http_get(episode_slug.strip())
        else:
            response = _aynadan_get(yol)
        if response is None or response.status_code != 200:
            return translators
        html = response.text

        # translator attribute'li elementleri bul
        # translator="https://anizle.co/episode/18851/translator/83196"
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
    except Exception as e:
        print(f"[Anizle] Translator listesi hatası ({episode_slug}): {e}")

    return translators


def _origin_of(url: str) -> Optional[str]:
    """URL'den 'https://host' kökünü çıkar."""
    m = re.match(r"(https?://[^/]+)", url or "")
    return m.group(1) if m else None


def _extract_hls_stream(page_html: str, origin: str, player_page_url: str,
                        label: str) -> Optional[Dict[str, str]]:
    """Yeni video.js player'ından HLS stream'i çıkar ve DOĞRULA.

    Anizle FirePlayer'dan video.js + HLS'e geçti; sayfa artık şunu içeriyor:
        const masterUrl       = "/stream/<32hex>/master.txt";
        const nativeMasterUrl = "/stream/<32hex>/native.m3u8";

    ÖNEMLİ: URL'yi doğrulamadan döndürmüyoruz. Bu uçlar hâlâ gömülü-player
    bağlamı dışından 404 veriyor; erişilemeyen bir adresi "stream" diye
    döndürmek yt-dlp'nin onu generic link sanıp bozuk indirme üretmesine yol
    açar (sessiz başarısızlık). Yalnızca gerçekten '#EXTM3U' dönen adresi
    kabul ediyoruz; böylece koruma ileride gevşerse kod kendiliğinden çalışır.
    """
    m = (re.search(r'\bmasterUrl\s*=\s*"([^"]+)"', page_html)
         or re.search(r'nativeMasterUrl\s*=\s*"([^"]+)"', page_html))
    if not m:
        return None

    path = m.group(1)
    url = path if path.startswith("http") else origin + path

    try:
        resp = _http_get(url, timeout=HTTP_TIMEOUT, headers={
            "Referer": player_page_url,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        })
    except Exception:
        return None

    body = "" if resp is None else (resp.text or "")
    if resp is None or resp.status_code != 200 or not body.lstrip().startswith("#EXTM3U"):
        print("[Anizle] Yeni video.js player'ı bulundu ama stream korumalı "
              f"({'yanıt yok' if resp is None else resp.status_code}); atlanıyor.")
        return None

    return {"url": url, "label": label, "type": "hls", "referer": player_page_url}


def _anizm_oynaticisi(url: str) -> bool:
    """Adres Anizm'in kendi oynatıcısında (ya da bir aynada) mı?"""
    konak = (urlparse(url).hostname or "").lower()
    return konak == (urlparse(PLAYER_BASE_URL).hostname or "") or _bilinen_konak(url)


def _oynatici_sayfasi(url: str, basliklar: Dict[str, str]) -> Optional[Tuple[Any, str]]:
    """Player sayfası: (yanıt, son adres); üçüncü taraf gömmeyse None.

    `<ayna>/player/<id>` 302 ile videonun asıl barındırıcısına gidiyor.
    Ölçüm (2026-09-25, naruto-1-bolum, 39 video): yalnızca anizmplayer.com'a
    gidenler (FirePlayer/HLS) çözülebiliyor; voe.sx, drive.google.com,
    abyssplayer, dood… sayfalarında çıkarılacak bir şey yok. Eskiden hepsi
    takip ediliyor, düşenler CF zincirini (FlareSolverr dahil) yürüyordu; bir
    bölümün akışları 285 sn sürdü. Yönlendirme artık önce okunuyor ve üçüncü
    taraf gömmeye hiç gidilmiyor.
    """
    ilk = _curl_get(url, HTTP_TIMEOUT, _get_basliklari(basliklar), yonlendir=False)
    if ilk is not None and 300 <= getattr(ilk, "status_code", 0) < 400:
        hedef = (getattr(ilk, "headers", None) or {}).get("location") or ""
        if not hedef:
            return None
        url = urljoin(url, hedef)
        if not _anizm_oynaticisi(url):
            return None
    elif ilk is not None and not _engellenmis(ilk):
        return ilk, url
    return _http_get(url, timeout=HTTP_TIMEOUT, headers=basliklar), url


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

        # Player sayfası, video URL'siyle AYNI host'tan alınmalı. Sabit konak
        # kırılgan: anizle.org yalnızca 301 ile anizle.co'ya yönlendiriyor ve
        # player sayfasına 404 döndürüyor; aynalar (anizm.com.tr, puffytr.com)
        # kendi adreslerini veriyor. Video URL'leri API'den mutlak geldiği için
        # host'u oradan türetiyoruz (domain yine değişirse kendiliğinden uyar).
        origin = _origin_of(video_url) or _kok()
        sayfa = _oynatici_sayfasi(f"{origin}/player/{player_id}",
                                  {"Referer": f"{origin}/"})
        if sayfa is None:
            return None
        player_response, player_page_url = sayfa
        if player_response is None or player_response.status_code != 200:
            return None
        # Göreli `masterUrl` sayfanın KENDİ konağına göre (yönlendirmeden
        # sonra anizmplayer.com), aynaya göre değil.
        origin = _origin_of(player_page_url) or origin

        page_html = player_response.text
        label = f"{fansub_name} - {video_name}"

        # 1) Eski FirePlayer yolu (hâlâ kullanan player'lar için)
        fireplayer_id = _extract_fireplayer_id(page_html)
        if fireplayer_id:
            return _get_video_stream_from_player(fireplayer_id, label)

        # 2) Yeni video.js / HLS player'ı
        return _extract_hls_stream(page_html, origin, player_page_url, label)

    except Exception:
        return None


def get_episode_streams(episode_slug: str, timeout: int = HTTP_TIMEOUT) -> List[Dict[str, str]]:
    """
    Bir bölümün video stream URL'lerini getir (paralel işlem).
    
    API Akışı:
    1. Episode sayfasından translator'ları al
    2. Her translator için video listesini al
    3. Tüm videolar paralel olarak işlenir:
       - Player ID'sini al, player sayfasını video adresinin konağından çek
       - FirePlayer ise kimliği çöz → anizmplayer.com getVideo
       - video.js ise HLS master adresini doğrula (bkz. modül belgesi)
    
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
        print("[Anizle] Yerel işlemde stream bulunamadı, uzak sunucu deniyor...")
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
        if isinstance(results, list):
            print(f"[Anizle] Uzak sunucudan {len(results)} stream alındı")
            return results
        else:
            print(f"[Anizle] Uzak sunucu cevabı liste değil: {type(results)}")
            return []
    except Exception as e:
        print(f"[Anizle] Uzak sunucu hatası: {e}")
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
        return f"{_kok()}/uploads/img/{self.poster}"


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
    "fireplayer_akisi",
    "fireplayer_istegi",
    "get_anime_details",
    "get_anime_episodes",
    "get_episode_streams",
    "load_anime_database",
    "search_anizle",
    "USE_REMOTE_SERVER",
]
