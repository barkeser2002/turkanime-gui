"""
TurkAnime API Server
====================

Tüm anime kaynaklarını (animecix, anizle, tranime, openani, tranimaci) tek
HTTP API arkasında birleştiren Flask uygulaması. Kullanıcının anime-kaynak
eşleştirmelerini ve bölüm izleme/indirme durumunu MySQL'de tutar.

Endpoints
---------

Genel:
    GET  /health                       — sağlık kontrolü
    GET  /sources                      — desteklenen kaynakların listesi
    GET  /search?q=&source=&fuzzy=     — tüm veya tek kaynakta multi-dil arama
                                          (fuzzy=true → %95 altı adayları da döndür)

Kaynak başına (animecix, anizle, tranime, openani, tranimaci):
    GET  /{source}/search?q=
    GET  /{source}/episodes/{slug}
    GET  /{source}/streams/{episode_slug}

DB:
    GET   /anime-matches?limit=
    POST  /anime-matches                 {source, anime_id, anime_title}
    GET   /anime-matches/search?q=
    POST  /user/episode-status           {user_id, episode_id, watched, downloaded}
    GET   /user/{user_id}/episode-status

Çevre değişkenleri:
    PORT          — varsayılan 34665
    DEBUG         — "true" yaparsanız debug modu
    DB_HOST       — varsayılan ip.bariskeser.com
    DB_USER       — varsayılan turkanime-gui
    DB_PASSWORD   — varsayılan boş
    DB_NAME       — varsayılan turkanime-gui
"""
from __future__ import annotations

import os
import sys
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

# Repo kökünü sys.path'a ekle ki turkanime_api modülü import edilebilsin
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flask import Flask, request, jsonify
from flask_cors import CORS

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
def _safe_import():
    """Adapter modüllerini güvenle yükle — biri patlasa diğerleri çalışsın."""
    registry: Dict[str, Dict[str, Any]] = {}

    try:
        from turkanime_api.sources.animecix import search_animecix
        registry["animecix"] = {
            "search": search_animecix,
            "episodes": None,   # CixAnime ile yapılıyor — özel akış
            "streams": None,
        }
    except Exception as e:
        log.warning("animecix yüklenemedi: %s", e)

    try:
        from turkanime_api.sources.anizle import (
            search_anizle, get_anime_episodes, get_episode_streams,
        )
        registry["anizle"] = {
            "search": search_anizle,
            "episodes": get_anime_episodes,
            "streams": get_episode_streams,
        }
    except Exception as e:
        log.warning("anizle yüklenemedi: %s", e)

    try:
        from turkanime_api.sources.tranime import (
            search_tranime, get_anime_episodes as ge,
            get_episode_details,
        )
        def _tranime_streams(ep_slug):
            d = get_episode_details(ep_slug)
            if not d:
                return []
            streams = []
            for s in d.get_sources():
                iframe = s.get_iframe()
                if iframe:
                    streams.append({"url": iframe, "label": s.name, "type": "iframe"})
            return streams
        registry["tranime"] = {
            "search": search_tranime,
            "episodes": lambda slug: [(e.slug, e.title) for e in ge(slug) or []],
            "streams": _tranime_streams,
        }
    except Exception as e:
        log.warning("tranime yüklenemedi: %s", e)

    try:
        from turkanime_api.sources.openani import (
            search_openani, get_anime_episodes, get_episode_streams,
        )
        registry["openani"] = {
            "search": search_openani,
            "episodes": get_anime_episodes,
            "streams": get_episode_streams,
        }
    except Exception as e:
        log.warning("openani yüklenemedi: %s", e)

    try:
        from turkanime_api.sources.tranimaci import (
            search_tranimaci, get_anime_episodes, get_episode_streams,
        )
        registry["tranimaci"] = {
            "search": search_tranimaci,
            "episodes": get_anime_episodes,
            "streams": get_episode_streams,
        }
    except Exception as e:
        log.warning("tranimaci yüklenemedi: %s", e)

    log.info("Yüklenen kaynaklar: %s", ", ".join(registry.keys()))
    return registry


SOURCES = _safe_import()

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
CORS(app)

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "ip.bariskeser.com"),
    "user": os.environ.get("DB_USER", "turkanime-gui"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "turkanime-gui"),
    "charset": "utf8mb4",
    "collation": "utf8mb4_unicode_ci",
}


def _db():
    if not _HAS_MYSQL:
        return None
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except MySQLError as e:
        log.warning("DB bağlantı hatası: %s", e)
        return None


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
@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "sources": list(SOURCES.keys()),
        "features": {
            "title_match": _HAS_MATCH,
            "episode_parser": _HAS_PARSER,
            "mysql": _HAS_MYSQL,
        },
    })


@app.route("/sources")
def list_sources():
    return jsonify([
        {
            "key": k,
            "has_search": v.get("search") is not None,
            "has_episodes": v.get("episodes") is not None,
            "has_streams": v.get("streams") is not None,
        }
        for k, v in SOURCES.items()
    ])


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

    only = request.args.get("source", "").strip().lower()
    fuzzy = request.args.get("fuzzy", "false").lower() in ("1", "true", "yes")
    threshold = float(request.args.get("threshold", "0.95"))

    targets = [only] if only and only in SOURCES else list(SOURCES.keys())
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
    src = source.lower()
    if src not in SOURCES:
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
    src = source.lower()
    if src not in SOURCES:
        return jsonify({"error": f"Bilinmeyen kaynak: {source}"}), 404
    fn = SOURCES[src].get("episodes")
    if not fn:
        return jsonify({"error": f"{src} bölüm listesi desteklemiyor"}), 405
    try:
        eps = _norm_results(fn(slug))
        return jsonify(_annotate_episodes(eps))
    except Exception as e:
        log.exception("%s episodes hatası", src)
        return jsonify({"error": str(e)}), 500


@app.route("/<source>/streams/<path:episode_slug>")
def source_streams(source: str, episode_slug: str):
    src = source.lower()
    if src not in SOURCES:
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
    limit = request.args.get("limit", 100, type=int)
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


@app.route("/anime-matches", methods=["POST"])
def save_anime_match():
    data = request.get_json(silent=True) or {}
    for f in ("source", "anime_id", "anime_title"):
        if not data.get(f):
            return jsonify({"error": f"{f} gerekli"}), 400
    conn = _db()
    if not conn:
        return jsonify({"error": "Veritabanı bağlantı hatası"}), 503
    try:
        cur = conn.cursor()
        aliases = data.get("aliases") or ""
        if isinstance(aliases, list):
            aliases = "|".join(aliases)
        cur.execute("""
            INSERT INTO anime_matches (source, anime_id, anime_title, aliases)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                anime_title = VALUES(anime_title),
                aliases = VALUES(aliases),
                updated_at = CURRENT_TIMESTAMP
        """, (data["source"], data["anime_id"], data["anime_title"], aliases))
        conn.commit()
        return jsonify({"success": True})
    except MySQLError as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: conn.close()
        except Exception: pass


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
@app.route("/user/episode-status", methods=["POST"])
def save_user_episode_status():
    data = request.get_json(silent=True) or {}
    if not data.get("user_id") or not data.get("episode_id"):
        return jsonify({"error": "user_id ve episode_id gerekli"}), 400
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
            data["user_id"], data["episode_id"],
            bool(data.get("watched", False)),
            bool(data.get("downloaded", False)),
            int(data.get("position_seconds", 0) or 0),
        ))
        conn.commit()
        return jsonify({"success": True})
    except MySQLError as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: conn.close()
        except Exception: pass


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
    _ensure_schema()


if __name__ == "__main__":
    _bootstrap()
    port = int(os.environ.get("PORT", "34665"))
    debug = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")
    log.info("TurkAnime API başlıyor — port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
