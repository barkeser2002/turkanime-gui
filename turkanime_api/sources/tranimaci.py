"""
Tranimaci.com Provider Adapter
https://tranimaci.com

Site Next.js + özel SHA-256 proof-of-work WAF kullanır. WAF challenge'ı
session'a __waf_session cookie'sini eklenmek için ilk istek geldiğinde
çözülür ve session boyunca tekrar gerek kalmaz.

Yapı:
- Anime sayfası:   GET /anime/<slug>          (ör: /anime/885-one-piece)
- Bölüm sayfası:   GET /video/<slug>-<n>-bolum
- Doğrudan .mp4    Multi-CDN, token'lı URL'ler
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from curl_cffi import requests as _curl_requests
    _HAS_CURL = True
except ImportError:
    import requests as _curl_requests  # type: ignore[no-redef]
    _HAS_CURL = False


BASE_URL = "https://tranimaci.com"
WAF_ENDPOINT = f"{BASE_URL}/__waf_challenge"
HTTP_TIMEOUT = 20
CHALLENGE_MARKER = "Security Verification"
_MAX_NONCE = 5_000_000

_session_lock = threading.Lock()
_session = None
_session_solved_at = 0.0
_SESSION_TTL = 30 * 60  # 30 dakika


def _new_session():
    if _HAS_CURL:
        return _curl_requests.Session(impersonate="chrome131")
    sess = _curl_requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0"})
    return sess


def _extract_challenge(html: str) -> Optional[Dict[str, Any]]:
    m_c = re.search(r'const\s+challenge\s*=\s*"([^"]+)"', html)
    m_t = re.search(r'const\s+timestamp\s*=\s*"([^"]+)"', html)
    m_s = re.search(r'const\s+sessionId\s*=\s*"([^"]+)"', html)
    m_d = re.search(r'const\s+difficulty\s*=\s*(\d+)', html)
    if not all([m_c, m_t, m_s, m_d]):
        return None
    return {
        "challenge": m_c.group(1),
        "timestamp": m_t.group(1),
        "session_id": m_s.group(1),
        "difficulty": int(m_d.group(1)),
    }


def _solve_pow(ch: Dict[str, Any]) -> Optional[str]:
    prefix = "0" * ch["difficulty"]
    sid, cid, ts = ch["session_id"], ch["challenge"], ch["timestamp"]
    for n in range(_MAX_NONCE):
        h = hashlib.sha256(f"{sid}:{cid}:{ts}{n}".encode()).hexdigest()
        if h.startswith(prefix):
            return str(n)
    return None


def _solve_session(sess, url: str, html: str) -> bool:
    """WAF challenge'ı çöz ve session'a cookie'yi yerleştir."""
    ch = _extract_challenge(html)
    if not ch:
        return False
    nonce = _solve_pow(ch)
    if nonce is None:
        return False
    try:
        r = sess.post(
            WAF_ENDPOINT,
            data={"solution": nonce, "challenge": ch["challenge"], "timestamp": ch["timestamp"]},
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": url},
            timeout=HTTP_TIMEOUT,
        )
        return r.status_code == 200 and '"success"' in r.text and 'true' in r.text
    except Exception:
        return False


def _get_session(force: bool = False):
    """WAF cookie'si garanti edilmiş ortak session döndür."""
    global _session, _session_solved_at
    with _session_lock:
        if (_session is None or force
                or (time.time() - _session_solved_at) > _SESSION_TTL):
            _session = _new_session()
            _session_solved_at = 0.0
        return _session


def _is_blocked(r) -> bool:
    """Cevap gerçek içerik değil, bir koruma kapısı mı?

    Site iki katmanlı: önce HTTP 202 + "JavaScript'i açın" ara sayfası, sonra
    SHA-256 "Security Verification" PoW'u. İkisi de gerçek içerik değildir.
    """
    if r.status_code == 202:
        return True
    head = r.text[:5000]
    return CHALLENGE_MARKER in head or "turn JavaScript on" in head


def _request(method: str, path: str, **kwargs):
    """Koruma kapılarını tolere eden GET/POST.

    Sıra: (1) ucuz curl_cffi, (2) Python tarafı SHA-256 PoW çözümü,
    (3) hâlâ engelliyse QtWebEngine (gerçek tarayıcı) ile çöz.

    (3) gerekli çünkü site JS tabanlı bir ön kapı ekledi; bu kapı Python'da
    çözülemiyor, ancak gerçek bir tarayıcıda kendiliğinden geçiliyor.
    """
    global _session_solved_at
    sess = _get_session()
    url = path if path.startswith("http") else BASE_URL + path
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    r = sess.request(method, url, **kwargs)

    # (2) Klasik PoW challenge'ı Python'da çöz
    if r.status_code == 200 and CHALLENGE_MARKER in r.text[:5000]:
        if _solve_session(sess, url, r.text):
            _session_solved_at = time.time()
            r = sess.request(method, url, **kwargs)

    # (3) Hâlâ kapıdaysak gerçek tarayıcıya devret (yalnızca GET destekleniyor)
    if method.upper() == "GET" and _is_blocked(r):
        browser_resp = _browser_get(url)
        if browser_resp is not None:
            return browser_resp
    return r


def _browser_get(url: str):
    """QtWebEngine çözücüsüyle sayfayı al; başarısızsa None."""
    try:
        from ..common.cf_bypass import get_cf_session
        resp = get_cf_session().get(url)
        if resp is not None and not _is_blocked(resp):
            return resp
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def _parse_search_html(html: str) -> List[Tuple[str, str]]:
    """Arama sayfasının HTML'inden (slug, başlık) listesi çıkar."""
    out: List[Tuple[str, str]] = []
    seen = set()
    # Her anime kartı /anime/<slug> linki ve içinde başlık taşıyor.
    # Next.js streamed JSON içinden de slug+title eşleştirmesi yapılır.
    # Önce HTML href'leri — her anime kartında <img alt="Anime Başlığı"> var
    for m in re.finditer(
        r'<a[^>]+href="(/anime/([a-z0-9\-]+))"[^>]*>(.*?)</a>',
        html, flags=re.DOTALL,
    ):
        slug = m.group(2)
        if slug in seen:
            continue
        inner = m.group(3)
        title = None
        # 1) <img alt="..."> en güvenilir
        tm = re.search(r'<img[^>]+\balt="([^"]{2,100})"', inner)
        if tm:
            title = tm.group(1).strip()
        # 2) <h2/h3/h4>...</hX>
        if not title or title.upper() in ("DEVAM", "DEVAM EDİYOR", "BİTTİ"):
            tm = re.search(r'<h\d[^>]*>([^<]{2,100})</h\d>', inner)
            if tm:
                title = tm.group(1).strip()
        # Yedek: slug'dan başlık üret
        if not title:
            title = re.sub(r'^\d+\-', '', slug).replace("-", " ").title()
        seen.add(slug)
        out.append((slug, title))
    # Next.js streamed data: "name":"...","slug":"885-one-piece"
    for m in re.finditer(
        r'\\"slug\\":\\"([a-z0-9\-]+)\\"[^}]*?\\"(?:name|title)\\":\\"([^"\\]+)\\"',
        html,
    ):
        slug, title = m.group(1), m.group(2)
        if slug not in seen:
            seen.add(slug)
            out.append((slug, title))
    return out


def search_tranimaci(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """Tranimaci.com'da anime ara.

    Returns: [(slug, başlık), ...]
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    try:
        r = _request("GET", f"/arama?q={q}")
        if r.status_code != 200 or CHALLENGE_MARKER in r.text[:5000]:
            return []
        results = _parse_search_html(r.text)
        return results[:limit]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Bölüm listesi
# ─────────────────────────────────────────────────────────────────────────────
def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Anime'nin bölüm listesini döndür.

    Returns: [(episode_slug, başlık), ...] - episode_slug ör: "885-one-piece-1-bolum"
    """
    try:
        r = _request("GET", f"/anime/{slug}")
        if r.status_code != 200 or CHALLENGE_MARKER in r.text[:5000]:
            return []
        html = r.text
        episodes: List[Tuple[str, str]] = []
        seen = set()
        for m in re.finditer(r'/video/([a-z0-9\-]+-(\d+)-bolum)\b', html):
            ep_slug = m.group(1)
            ep_no = m.group(2)
            if ep_slug in seen:
                continue
            seen.add(ep_slug)
            episodes.append((ep_slug, f"{ep_no}. Bölüm"))
        # Bölüm numarasına göre sırala
        episodes.sort(key=lambda x: int(re.search(r'-(\d+)-bolum$', x[0]).group(1))
                                   if re.search(r'-(\d+)-bolum$', x[0]) else 0)
        return episodes
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Stream linkleri
# ─────────────────────────────────────────────────────────────────────────────
def get_episode_streams(episode_slug: str) -> List[Dict[str, str]]:
    """Bölümün doğrudan video stream URL'lerini döndür.

    Returns: [{"url": ..., "label": "1080p", "type": "direct"}, ...]
    """
    try:
        r = _request("GET", f"/video/{episode_slug}")
        if r.status_code != 200 or CHALLENGE_MARKER in r.text[:5000]:
            return []
        html = r.text
        # .mp4 URL'lerini bul - quality URL içinde
        urls: Dict[str, List[str]] = {}
        for m in re.finditer(r'https?://[^"\\\s]+/(\d{3,4}p)\.mp4\?token=[^"\\\s]+', html):
            url = m.group(0)
            quality = m.group(1)
            urls.setdefault(quality, []).append(url)
        # Quality'lere göre en yüksek sırada streamleri döndür (her quality için ilk CDN)
        streams: List[Dict[str, str]] = []
        quality_order = sorted(urls.keys(), key=lambda q: -int(q[:-1]))
        for q in quality_order:
            # CDN yedekleri için aynı quality'nin tüm URL'lerini liste olarak ekle
            for i, url in enumerate(urls[q][:5]):  # en fazla 5 CDN yedeği
                streams.append({
                    "url": url,
                    "label": f"{q}" + (f" (CDN{i+1})" if i > 0 else ""),
                    "type": "direct",
                    "referer": BASE_URL + "/",
                })
        return streams
    except Exception:
        return []


__all__ = [
    "search_tranimaci",
    "get_anime_episodes",
    "get_episode_streams",
    "BASE_URL",
]
