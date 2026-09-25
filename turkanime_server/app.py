"""
TurkAnime API Server
====================

Kaynak kaydındaki (`turkanime_api/sources/kayit.py`) taranabilir kaynakları
tek HTTP API arkasında birleştiren Flask uygulaması. Kullanıcının
anime-kaynak eşleştirmelerini MySQL'de tutar.

Kaynak tablosu KAYITTAN TÜRETİLİR (`kayit.tarayici_kaynaklari()`, sunucu
tarayıcısıyla aynı liste); burada elle yazılmış ikinci bir liste yok. Arşiv
kaynağı (TürkAnime) listede değil: imaj `arsiv/` taşımıyor.

Endpoints
---------

Genel:
    GET  /health                       — sağlık: veritabanını gerçekten yoklar
                                          (erişilemiyorsa 503 "degraded")
    GET  /health/live                  — canlılık: süreç ayakta mı (hep 200)
    GET  /sources                      — yüklenen kaynaklar ve uçları
    GET  /search?q=&source=&fuzzy=     — tüm veya tek kaynakta multi-dil arama
                                          (fuzzy=true → %95 altı adayları da döndür)

Kaynak başına (`/sources`'taki anahtarlar: animecix, anizle, tranime,
openani, tranimaci; kayıttaki etiket de olur: "TRAnimeİzle"):
    GET  /{source}/search?q=
    GET  /{source}/episodes/{kimlik}
    GET  /{source}/streams/{bolum_kimligi}

    AnimeciX bölüm kimliği bir embed YOLU; içinde "?" ya da "&" varsa istemci
    yüzde-kodlamalı (%3F, %26).

DB:
    GET   /anime-matches?limit=          (limit en çok 500)
    POST  /anime-matches                 {source, anime_id, anime_title, aliases?}
    GET   /anime-matches/search?q=
    POST  /user/episode-status           {user_id, episode_id, watched, downloaded}
    GET   /user/{user_id}/episode-status

    `/user/...` uçları ESKİ: yalnızca v9.4.3 ve öncesi (CustomTkinter)
    istemciler çağırıyor, v10 istemcisi hiç kullanmıyor. Eski kurulumlar
    bozulmasın diye duruyor; tablo da silinmiyor.

Yazma uçları (iki POST):
    - Hız sınırı: istemci adresi başına dakikada API_YAZMA_SINIRI istek
      (aşılınca 429).
    - `/user/episode-status`: API_YAZMA_ANAHTARI ayarlıysa `X-API-Key`
      başlığı onunla eşleşmeli (401). `/anime-matches` BİLEREK anahtarsız:
      masaüstü istemcisi eşleşmeyi anahtarsız gönderiyor. Onu kayıtta olmayan
      kaynak adı ve şema sınırlarını aşan alan reddi (400) koruyor.

Çevre değişkenleri (örnek: `.env.example`):
    PORT               — varsayılan 34665
    DEBUG              — "true" yaparsanız debug modu (yalnızca `python app.py`)
    DB_HOST, DB_USER, DB_NAME — ZORUNLU, varsayılan yok. Eksikse sunucu
                         açılmaz (`_bootstrap`), DB uçları 503 döner.
    DB_PASSWORD        — varsayılan boş
    CORS_ORIGINS       — virgülle ayrılmış izinli kökenler. Boşsa CORS başlığı
                         hiç verilmez (masaüstü istemcisi CORS'a bakmaz).
    API_YAZMA_ANAHTARI — isteğe bağlı; ayarlıysa `/user/episode-status` ister.
    API_YAZMA_SINIRI   — dakikada yazma isteği (varsayılan 30, 0 = kapalı).
    TRUST_PROXY        — "1": istemci adresini `CF-Connecting-IP`'den al
                         (Cloudflare arkasında `remote_addr` kenar sunucusudur).
                         Vekil yokken AÇMAYIN: başlığı herkes yazabilir.

Üretimde gunicorn çalıştırır (`gunicorn.conf.py`); `python app.py` yerel
geliştirme içindir.
"""
from __future__ import annotations

import hmac
import logging
import math
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Deque, Dict, List, Optional

# Repo kökünü sys.path'a ekle ki turkanime_api modülü import edilebilsin
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flask import Flask, request, jsonify
from flask_cors import CORS

from turkanime_api.sources import kayit

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
    _HAS_MYSQL = True
except ImportError:
    _HAS_MYSQL = False
    MySQLError = Exception  # type: ignore[misc, assignment]

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("turkanime-api")

# ─────────────────────────────────────────────────────────────────────────────
# Source adapter registry
# ─────────────────────────────────────────────────────────────────────────────
def kaynak_tablosu() -> Dict[str, Dict[str, Any]]:
    """Kaynak tablosunu kayıttan türet — biri patlasa diğerleri çalışsın.

    Eskiden burada beş kaynak elle import ediliyordu (`_safe_import`). Kayda
    eklenen kaynak API'de görünmüyordu ve AnimeciX, istemcide bölüm/akış
    uçları çalışırken burada "bölüm listesi desteklemiyor" (405) diyordu.
    Artık liste ve uçlar `kayit.tarayici_kaynaklari()`'ndan geliyor; sunucu
    tarayıcısı (`crawler/kaynaklar.py`) da aynı listeyi kullanıyor.

    Anahtar modül adı ("tranime", "openani"): eski URL'ler aynen çalışıyor.
    """
    tablo: Dict[str, Dict[str, Any]] = {}
    for kaynak in kayit.tarayici_kaynaklari():
        try:
            uclar = kaynak.uclar()
        except Exception as e:  # bir modülün import hatası API'yi düşürmesin
            log.warning("%s yüklenemedi: %s", kaynak.ad, e)
            continue
        tablo[kaynak.modul] = {
            "search": (lambda q, _k=kaynak: _k.ara(q, limit=20)),
            "episodes": uclar.bolumler,
            "streams": uclar.akislar,
            "kaynak": kaynak,
        }
    log.info("Yüklenen kaynaklar: %s", ", ".join(tablo))
    return tablo


SOURCES = kaynak_tablosu()

# ─────────────────────────────────────────────────────────────────────────────
# Title matching utilities
# ─────────────────────────────────────────────────────────────────────────────
try:
    from turkanime_api.common.title_match import multilang_search, SearchResponse
    _HAS_MATCH = True
except Exception as e:
    log.warning("title_match yüklenemedi: %s", e)
    _HAS_MATCH = False

try:
    from turkanime_api.common.episode_parser import parse_episode
    _HAS_PARSER = True
except Exception as e:
    log.warning("episode_parser yüklenemedi: %s", e)
    _HAS_PARSER = False


# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)


def cors_kokenleri(ham: str) -> List[str]:
    """`CORS_ORIGINS` değerinden izinli kökenler ("a, b" → ["a", "b"])."""
    return [x.strip() for x in (ham or "").split(",") if x.strip()]


# Eskiden `CORS(app)`: her kökene her yöntem açıktı, yabancı bir sayfa
# ziyaretçinin tarayıcısından yazma uçlarına istek atabiliyordu. Tarayıcıda
# çalışan bir istemcimiz yok (masaüstü uygulaması CORS'a bakmaz), yani boş
# liste güvenli varsayılan: hiçbir CORS başlığı verilmez.
CORS_KOKENLERI = cors_kokenleri(os.environ.get("CORS_ORIGINS", ""))
if CORS_KOKENLERI:
    CORS(app, origins=CORS_KOKENLERI)

# Varsayılan YOK: eskiden işletmecinin gerçek sunucusu varsayılandı, yani
# ayarsız bir kopya başkasının veritabanına bağlanmaya çalışıyordu.
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", ""),
    "user": os.environ.get("DB_USER", ""),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", ""),
    "charset": "utf8mb4",
    "collation": "utf8mb4_unicode_ci",
    # /health veritabanını yokluyor; HEALTHCHECK 10 sn'de kesiyor.
    "connection_timeout": 3,
}
_DB_ORTAM_ADLARI = {"host": "DB_HOST", "user": "DB_USER", "database": "DB_NAME"}
_db_eksik_uyarildi = False


def db_eksikleri() -> List[str]:
    """Boş bırakılmış zorunlu DB değişkenleri (ortam adıyla)."""
    return [ad for alan, ad in _DB_ORTAM_ADLARI.items() if not DB_CONFIG.get(alan)]


def _db():
    global _db_eksik_uyarildi
    if not _HAS_MYSQL:
        return None
    eksik = db_eksikleri()
    if eksik:
        if not _db_eksik_uyarildi:
            log.error("Veritabanı ayarlanmamış (%s boş); DB uçları 503 döner.",
                      ", ".join(eksik))
            _db_eksik_uyarildi = True
        return None
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except MySQLError as e:
        log.warning("DB bağlantı hatası: %s", e)
        return None


def _kapat(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Yazma uçlarının korunması: hız sınırı + isteğe bağlı anahtar
# ─────────────────────────────────────────────────────────────────────────────
class HizSiniri:
    """Kayan pencere: anahtar başına ``pencere`` saniyede en çok ``sinir`` istek.

    Süreç içi bellekte tutulur. gunicorn'da her işçinin kendi sayacı var,
    yani gerçek tavan işçi sayısı × ``sinir``. Flask-Limiter + Redis gibi ağır
    bir bağımlılık yerine bilinçli seçim; kesin bir tavan gerekiyorsa önündeki
    vekilde (Cloudflare kuralı) uygulanmalı.
    """

    AZAMI_ANAHTAR = 10_000

    def __init__(self, sinir: int, pencere: float = 60.0,
                 saat: Callable[[], float] = time.monotonic):
        self.sinir = sinir
        self.pencere = pencere
        self._saat = saat
        self._kayitlar: Dict[str, Deque[float]] = {}
        self._kilit = threading.Lock()

    def izin_ver(self, anahtar: str) -> bool:
        if self.sinir <= 0:
            return True
        simdi = self._saat()
        with self._kilit:
            if len(self._kayitlar) > self.AZAMI_ANAHTAR:
                self._bayatlari_at(simdi)
            kuyruk = self._kayitlar.setdefault(anahtar, deque())
            while kuyruk and simdi - kuyruk[0] >= self.pencere:
                kuyruk.popleft()
            if len(kuyruk) >= self.sinir:
                return False
            kuyruk.append(simdi)
            return True

    def _bayatlari_at(self, simdi: float) -> None:
        """Penceresi tamamen geçmiş adresleri unut (bellek sınırsız büyümesin)."""
        for anahtar in [a for a, k in self._kayitlar.items()
                        if not k or simdi - k[-1] >= self.pencere]:
            del self._kayitlar[anahtar]


def _tamsayi_ortam(ad: str, varsayilan: int) -> int:
    try:
        return int(os.environ.get(ad, "") or varsayilan)
    except ValueError:
        log.warning("%s tamsayı değil; varsayılan %d kullanılıyor", ad, varsayilan)
        return varsayilan


YAZMA_ANAHTARI = os.environ.get("API_YAZMA_ANAHTARI", "")
VEKIL_GUVENILIR = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
yazma_siniri = HizSiniri(_tamsayi_ortam("API_YAZMA_SINIRI", 30))


def istemci_adresi() -> str:
    """Hız sınırının anahtarı. Vekil başlığına yalnızca TRUST_PROXY ile güvenilir."""
    if VEKIL_GUVENILIR:
        cf = request.headers.get("CF-Connecting-IP", "").strip()
        if cf:
            return cf
    return request.remote_addr or "?"


def _anahtar_gecerli() -> bool:
    if not YAZMA_ANAHTARI:
        return True
    verilen = request.headers.get("X-API-Key", "")
    return hmac.compare_digest(verilen.encode("utf-8"), YAZMA_ANAHTARI.encode("utf-8"))


def yazma_korumasi(anahtar_ister: bool = False):
    """POST uçlarına hız sınırı (429) ve istenirse anahtar denetimi (401).

    Sınır anahtardan ÖNCE sayılır: yanlış anahtarla sınırsız deneme yapılamasın.
    """
    def sar(fn):
        @wraps(fn)
        def ic(*args, **kwargs):
            if not yazma_siniri.izin_ver(istemci_adresi()):
                yanit = jsonify({"error": "Çok fazla istek; bir dakika sonra deneyin"})
                yanit.headers["Retry-After"] = str(int(yazma_siniri.pencere))
                return yanit, 429
            if anahtar_ister and not _anahtar_gecerli():
                return jsonify({"error": "Geçersiz ya da eksik X-API-Key"}), 401
            return fn(*args, **kwargs)
        return ic
    return sar


def _ensure_schema():
    conn = _db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS anime_matches (
                id INT AUTO_INCREMENT PRIMARY KEY,
                source VARCHAR(50) NOT NULL,
                anime_id VARCHAR(150) NOT NULL,
                anime_title VARCHAR(500) NOT NULL,
                aliases TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_match (anime_id, source)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_episode_status (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                episode_id VARCHAR(500) NOT NULL,
                watched BOOLEAN DEFAULT FALSE,
                downloaded BOOLEAN DEFAULT FALSE,
                position_seconds INT DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_ep (user_id, episode_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()
        log.info("DB şeması hazır.")
    except MySQLError as e:
        log.warning("Şema oluşturma hatası: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _norm_results(results: Any) -> List[Dict[str, str]]:
    """Adapter sonuçlarını (tuple, dict vs.) homojen JSON formatına çevir."""
    out: List[Dict[str, str]] = []
    if not results:
        return out
    for r in results:
        if isinstance(r, dict):
            out.append({
                "id": str(r.get("id") or r.get("slug") or ""),
                "title": str(r.get("title") or r.get("name") or ""),
            })
        elif isinstance(r, (tuple, list)) and len(r) >= 2:
            out.append({"id": str(r[0]), "title": str(r[1])})
        else:
            out.append({"id": str(r), "title": str(r)})
    return out


def _annotate_episodes(eps: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Her bölüm için universal parser ile season+episode metadata ekle."""
    if not _HAS_PARSER:
        return eps  # type: ignore[return-value]
    out = []
    for e in eps:
        info = parse_episode(e.get("title", ""))
        out.append({
            **e,
            "season": info.season,
            "episode": info.episode,
            "label": info.label,
            "normalized": info.normalized(),
            "parse_score": round(info.score, 2),
        })
    # season+episode'a göre sırala (None'lar sona)
    out.sort(key=lambda x: (
        x.get("season") is None,
        x.get("season") or 0,
        x.get("episode") is None,
        x.get("episode") or 0,
    ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Generic endpoints
# ─────────────────────────────────────────────────────────────────────────────
def db_durumu() -> str:
    """Veritabanını gerçekten yokla: "ok" ya da okunur bir sebep.

    Hata metni bilerek yalnızca sınıf adı: MySQL mesajı sunucu adını ve
    kullanıcı adını taşıyabiliyor, /health ise herkese açık.
    """
    if not _HAS_MYSQL:
        return "sürücü yok"
    eksik = db_eksikleri()
    if eksik:
        return "ayarlanmamış: " + ", ".join(eksik)
    conn = _db()
    if not conn:
        return "erişilemiyor"
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        return "ok"
    except Exception as e:
        return f"erişilemiyor: {type(e).__name__}"
    finally:
        _kapat(conn)


@app.route("/health")
def health():
    # Eskiden sabit "healthy" dönüyordu: veritabanı düşse de, hiç ayarlanmasa
    # da. Artık yalnızca DB yanıt verirse 200; değilse aynı gövde 503 ile.
    db = db_durumu()
    saglam = db == "ok"
    return jsonify({
        "status": "healthy" if saglam else "degraded",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "db": db,
        "sources": list(SOURCES.keys()),
        "features": {
            "title_match": _HAS_MATCH,
            "episode_parser": _HAS_PARSER,
            "mysql": _HAS_MYSQL,
        },
    }), (200 if saglam else 503)


@app.route("/health/live")
def health_live():
    """Canlılık: süreç istek karşılıyor mu. Konteyner HEALTHCHECK'i buna bakar;
    DB kesintisi API konteynerini "unhealthy" yapıp yeniden başlatmamalı."""
    return jsonify({"status": "alive"})


@app.route("/sources")
def list_sources():
    return jsonify([
        {
            "key": k,
            "name": v["kaynak"].ad if v.get("kaynak") else k,
            "has_search": v.get("search") is not None,
            "has_episodes": v.get("episodes") is not None,
            "has_streams": v.get("streams") is not None,
        }
        for k, v in SOURCES.items()
    ])


def kaynak_anahtari(ad: str) -> Optional[str]:
    """URL'deki kaynak adını tablo anahtarına çevir; bilinmiyorsa None.

    Kayıttaki her ad kabul edilir: modül ("tranime"), kanonik ad ya da etiket
    ("TRAnimeİzle", büyük/küçük harf ve aksandan bağımsız).
    """
    kaynak = kayit.bul(ad)
    anahtar = kaynak.modul if kaynak is not None and kaynak.modul else ad.lower()
    return anahtar if anahtar in SOURCES else None


THRESHOLD_HATASI = "threshold 0..1 arası sayı olmalı"


@app.route("/search")
def universal_search():
    """Tüm kaynaklarda (veya tek kaynakta) multi-dil arama yap.

    Query params:
        q       — sorgu
        source  — sadece bu kaynakta ara (boş ise hepsi)
        fuzzy   — "true" ise %95 altı possible adayları da döndür
        threshold — varsayılan 0.95
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q gerekli"}), 400

    only = request.args.get("source", "").strip()
    fuzzy = request.args.get("fuzzy", "false").lower() in ("1", "true", "yes")
    # `float()` doğrudan çağrılınca "abc" ya da boş değer ValueError fırlatıp
    # 500 dönüyordu; 1.5, -1, nan, inf ise sessizce kabul ediliyordu (eşik
    # anlamsızlaşıp ya hiçbir sonuç "exact" olmuyor ya da hepsi oluyordu).
    # İstemci hatası 400 ile söylenir. `request.args.get(type=float)`
    # kullanılmadı: o bozuk değeri sessizce varsayılana çevirirdi.
    ham = request.args.get("threshold", "0.95")
    try:
        threshold = float(ham)
    except ValueError:
        return jsonify({"error": THRESHOLD_HATASI}), 400
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        return jsonify({"error": THRESHOLD_HATASI}), 400

    secilen = kaynak_anahtari(only) if only else None
    targets = [secilen] if secilen else list(SOURCES.keys())
    by_source: Dict[str, Any] = {}

    for src in targets:
        searcher = SOURCES[src].get("search")
        if not searcher:
            continue
        if _HAS_MATCH:
            resp: SearchResponse = multilang_search(
                lambda x, _s=searcher: _norm_to_tuples(_s(x)),
                q, threshold=threshold,
            )
            by_source[src] = {
                "exact": [_mr_to_dict(r) for r in resp.exact],
                "possible": [_mr_to_dict(r) for r in resp.possible] if fuzzy else [],
                "aliases": resp.aliases,
            }
        else:
            try:
                rows = _norm_results(searcher(q))
            except Exception as e:
                rows = []
                log.warning("%s search hatası: %s", src, e)
            by_source[src] = {"exact": rows, "possible": [], "aliases": [q]}
    return jsonify({"query": q, "results": by_source})


def _norm_to_tuples(rows):
    out = []
    for r in rows or []:
        if isinstance(r, (tuple, list)) and len(r) >= 2:
            out.append((str(r[0]), str(r[1])))
        elif isinstance(r, dict):
            out.append((str(r.get("id") or r.get("slug") or ""),
                        str(r.get("title") or r.get("name") or "")))
    return out


def _mr_to_dict(mr) -> Dict[str, Any]:
    return {
        "id": mr.slug,
        "title": mr.title,
        "score": round(mr.score, 3),
        "matched_via": mr.matched_via,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-source endpoints — generic dispatcher
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/<source>/search")
def source_search(source: str):
    src = kaynak_anahtari(source)
    if src is None:
        return jsonify({"error": f"Bilinmeyen kaynak: {source}"}), 404
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q gerekli"}), 400
    fn = SOURCES[src].get("search")
    if not fn:
        return jsonify({"error": f"{src} arama desteklemiyor"}), 405
    try:
        return jsonify(_norm_results(fn(q)))
    except Exception as e:
        log.exception("%s search hatası", src)
        return jsonify({"error": str(e)}), 500


@app.route("/<source>/episodes/<path:slug>")
def source_episodes(source: str, slug: str):
    src = kaynak_anahtari(source)
    if src is None:
        return jsonify({"error": f"Bilinmeyen kaynak: {source}"}), 404
    fn = SOURCES[src].get("episodes")
    if not fn:
        return jsonify({"error": f"{src} bölüm listesi desteklemiyor"}), 405
    # Kimliği bu kaynakta açılamayacaksa (AnimeciX sayısal ister) siteye hiç
    # gitmeden söyle: istemcideki mesajın aynısı.
    kaynak = SOURCES[src].get("kaynak")
    hata = kaynak.kimlik_denetle(slug) if kaynak is not None else None
    if hata:
        return jsonify({"error": hata}), 400
    try:
        eps = _norm_results(fn(slug))
        return jsonify(_annotate_episodes(eps))
    except Exception as e:
        log.exception("%s episodes hatası", src)
        return jsonify({"error": str(e)}), 500


@app.route("/<source>/streams/<path:episode_slug>")
def source_streams(source: str, episode_slug: str):
    src = kaynak_anahtari(source)
    if src is None:
        return jsonify({"error": f"Bilinmeyen kaynak: {source}"}), 404
    fn = SOURCES[src].get("streams")
    if not fn:
        return jsonify({"error": f"{src} stream desteklemiyor"}), 405
    try:
        result = fn(episode_slug)
        return jsonify(result if isinstance(result, list) else [])
    except Exception as e:
        log.exception("%s streams hatası", src)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Anime matches (DB)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/anime-matches", methods=["GET"])
def get_anime_matches():
    limit = max(1, min(request.args.get("limit", 100, type=int), AZAMI_LIMIT))
    conn = _db()
    if not conn:
        return jsonify({"error": "Veritabanı bağlantı hatası"}), 503
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, source, anime_id, anime_title, aliases, created_at, updated_at "
            "FROM anime_matches ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        )
        return jsonify(cur.fetchall())
    finally:
        try: conn.close()
        except Exception: pass


AZAMI_LIMIT = 500
# Şemadaki sütun genişlikleri (`_ensure_schema`); aliases TEXT ama sınırsız
# değil: istemci birkaç başlık gönderiyor, fazlası kötüye kullanım.
ALAN_SINIRLARI = {"anime_id": 150, "anime_title": 500, "aliases": 2000}
DB_HATASI = "Veritabanı hatası"


def _metin_alani(data: Dict[str, Any], ad: str) -> Optional[str]:
    """JSON alanını metne çevir; metin/sayı değilse ya da boşsa None."""
    deger = data.get(ad)
    if isinstance(deger, bool) or not isinstance(deger, (str, int)):
        return None
    return str(deger).strip() or None


@app.route("/anime-matches", methods=["POST"])
@yazma_korumasi()
def save_anime_match():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "JSON nesnesi bekleniyor"}), 400
    alanlar: Dict[str, str] = {}
    for f in ("source", "anime_id", "anime_title"):
        deger = _metin_alani(data, f)
        if deger is None:
            return jsonify({"error": f"{f} gerekli"}), 400
        alanlar[f] = deger
    # Kayıtta olmayan kaynak adı reddedilir; eski ad ("AnimeDepo") kanonik
    # adla yazılır ki aynı eşleşme iki adla birikmesin.
    kaynak = kayit.bul(alanlar["source"])
    if kaynak is None:
        return jsonify({"error": f"Bilinmeyen kaynak: {alanlar['source']}"}), 400
    aliases = data.get("aliases") or ""
    if isinstance(aliases, list) and all(isinstance(a, str) for a in aliases):
        aliases = "|".join(aliases)
    if not isinstance(aliases, str):
        return jsonify({"error": "aliases metin ya da metin listesi olmalı"}), 400
    alanlar["aliases"] = aliases
    for ad, sinir in ALAN_SINIRLARI.items():
        if len(alanlar[ad]) > sinir:
            return jsonify({"error": f"{ad} en çok {sinir} karakter olabilir"}), 400
    conn = _db()
    if not conn:
        return jsonify({"error": "Veritabanı bağlantı hatası"}), 503
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO anime_matches (source, anime_id, anime_title, aliases)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                anime_title = VALUES(anime_title),
                aliases = VALUES(aliases),
                updated_at = CURRENT_TIMESTAMP
        """, (kaynak.ad, alanlar["anime_id"], alanlar["anime_title"], aliases))
        conn.commit()
        return jsonify({"success": True})
    except MySQLError:
        # `str(e)` sunucu/kullanıcı adını ve şemayı dışarı sızdırıyordu.
        log.exception("anime-matches yazılamadı")
        return jsonify({"error": DB_HATASI}), 500
    finally:
        _kapat(conn)


@app.route("/anime-matches/search")
def search_anime_matches():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q gerekli"}), 400
    conn = _db()
    if not conn:
        return jsonify({"error": "Veritabanı bağlantı hatası"}), 503
    try:
        cur = conn.cursor(dictionary=True)
        like = f"%{q}%"
        cur.execute("""
            SELECT id, source, anime_id, anime_title, aliases, updated_at
            FROM anime_matches
            WHERE anime_title LIKE %s OR aliases LIKE %s
            ORDER BY updated_at DESC LIMIT 50
        """, (like, like))
        return jsonify(cur.fetchall())
    finally:
        try: conn.close()
        except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# User episode status (DB)
# ─────────────────────────────────────────────────────────────────────────────
#
# ESKİ UÇLAR: yalnızca v9.4.3 ve öncesi istemciler çağırıyor (v10 kullanmıyor).
# Silinmedi: eski kurulumların hata yutan istemcisi 404'ü sessizce yerdi.
# Tablo da DROP edilmiyor. API_YAZMA_ANAHTARI ayarlanırsa yazma anahtar ister.
@app.route("/user/episode-status", methods=["POST"])
@yazma_korumasi(anahtar_ister=True)
def save_user_episode_status():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "JSON nesnesi bekleniyor"}), 400
    user_id = _metin_alani(data, "user_id")
    episode_id = _metin_alani(data, "episode_id")
    if not user_id or not episode_id:
        return jsonify({"error": "user_id ve episode_id gerekli"}), 400
    if len(user_id) > 64 or len(episode_id) > 500:
        return jsonify({"error": "user_id en çok 64, episode_id en çok 500 karakter"}), 400
    try:
        konum = int(data.get("position_seconds", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "position_seconds tamsayı olmalı"}), 400
    conn = _db()
    if not conn:
        return jsonify({"error": "Veritabanı bağlantı hatası"}), 503
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_episode_status
                (user_id, episode_id, watched, downloaded, position_seconds)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                watched = VALUES(watched),
                downloaded = VALUES(downloaded),
                position_seconds = VALUES(position_seconds),
                updated_at = CURRENT_TIMESTAMP
        """, (
            user_id, episode_id,
            bool(data.get("watched", False)),
            bool(data.get("downloaded", False)),
            konum,
        ))
        conn.commit()
        return jsonify({"success": True})
    except MySQLError:
        log.exception("user_episode_status yazılamadı")
        return jsonify({"error": DB_HATASI}), 500
    finally:
        _kapat(conn)


@app.route("/user/<user_id>/episode-status")
def get_user_episode_status(user_id: str):
    conn = _db()
    if not conn:
        return jsonify({"error": "Veritabanı bağlantı hatası"}), 503
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT episode_id, watched, downloaded, position_seconds, updated_at
            FROM user_episode_status WHERE user_id = %s
        """, (user_id,))
        out: Dict[str, Any] = {}
        for row in cur.fetchall():
            out[row["episode_id"]] = {
                "watched": bool(row["watched"]),
                "downloaded": bool(row["downloaded"]),
                "position_seconds": int(row["position_seconds"] or 0),
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
        return jsonify(out)
    finally:
        try: conn.close()
        except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# Boot
# ─────────────────────────────────────────────────────────────────────────────
def _bootstrap():
    """Açılış: DB ayarı eksikse sunucu hiç kalkmasın, varsa şemayı kur.

    gunicorn bunu ana süreçte bir kez çağırır (`gunicorn.conf.py` →
    `on_starting`); `python app.py` de aşağıda çağırır.
    """
    eksik = db_eksikleri()
    if eksik:
        raise SystemExit(f"{', '.join(eksik)} zorunlu (bkz. .env.example)")
    _ensure_schema()


if __name__ == "__main__":
    _bootstrap()
    port = int(os.environ.get("PORT", "34665"))
    debug = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
    log.info("TurkAnime API başlıyor — port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
