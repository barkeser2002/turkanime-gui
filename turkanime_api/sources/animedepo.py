"""
AnimeDepo sağlayıcı (KebabLord upstream V10 driver'ından uyarlandı).

AnimeDepo, GitLab üzerinde barındırılan statik bir JSON arşividir; gerçek bir
arama endpoint'i yoktur (gözat-tabanlı). Bu modül fork'un fonksiyon-stili kaynak
sözleşmesine (search / episodes / streams) uyar ve `dizin.json` dizinini indirip
**yerel fuzzy arama** yapar.

Dizin yapısı (GitLab raw):
- dizin.json                              -> {"index": {grup: {slug: {"title": ...}}}}
- animeler/{slug}/info.json               -> anime metadata
- animeler/{slug}/bolumler.json           -> [[bolum_slug, baslik], ...]
- animeler/{slug}/{bolum_slug}.json       -> [{url|mask|path, player, fansub, alive}, ...]

Bölüm kimliği bu modülde "anime_slug/bolum_slug" bileşik biçiminde taşınır;
böylece `get_episode_streams` doğru JSON yolunu kurabilir.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

try:
    from curl_cffi import requests as _requests  # type: ignore
    _HAS_CURL = True
except ImportError:  # pragma: no cover - kütüphane yoksa düz requests'e düş
    import requests as _requests  # type: ignore[no-redef]
    _HAS_CURL = False


BASE_URL = "https://gitlab.com/AnimeDepo/animedepo/-/raw/master"
HTTP_TIMEOUT = 10

_dizin_lock = Lock()
_dizin_cache: Optional[Dict[str, Any]] = None


def _session():
    if _HAS_CURL:
        return _requests.Session(impersonate="chrome131")
    sess = _requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0"
    })
    return sess


def fetch_json(path: str) -> Any:
    """AnimeDepo JSON dosyasını getir."""
    url = BASE_URL + "/" + path.lstrip("/")
    r = _session().get(url, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def dizin() -> Dict[str, Any]:
    """AnimeDepo dizin.json dosyasını cache'li döndür.

    NOT: Başarısızlık cache'lenmez. Aksi hâlde tek bir geçici ağ hatası
    AnimeDepo'yu süreç boyunca sessizce devre dışı bırakır (her arama 0 sonuç,
    hiçbir hata görünmez) ve tek çare uygulamayı yeniden başlatmak olur.
    """
    global _dizin_cache
    with _dizin_lock:
        if _dizin_cache:
            return _dizin_cache
        try:
            data = fetch_json("dizin.json")
        except Exception:
            return {}            # cache'leme: sonraki çağrı tekrar dener
        if data:
            _dizin_cache = data
        return data or {}


def get_anime_listesi() -> List[Tuple[str, str]]:
    """AnimeDepo anime listesini [(slug, title), ...] biçiminde döndür."""
    liste: List[Tuple[str, str]] = []
    for grup in dizin().get("index", {}).values():
        if not isinstance(grup, dict):
            continue
        for slug, anime in grup.items():
            title = (anime or {}).get("title") if isinstance(anime, dict) else None
            liste.append((slug, title or slug.replace("-", " ").title()))
    return liste


# ─────────────────────────────────────────────────────────────────────────────
# Arama (yerel fuzzy — AnimeDepo'da gerçek arama endpoint'i yok)
# ─────────────────────────────────────────────────────────────────────────────
def search_animedepo(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """AnimeDepo dizininde yerel fuzzy arama yap.

    Returns: [(slug, başlık), ...]
    """
    if not query or not query.strip():
        return []
    q = query.strip().lower()
    scored: List[Tuple[float, Tuple[str, str]]] = []
    for slug, title in get_anime_listesi():
        hay = (title or "").lower()
        ratio = SequenceMatcher(None, q, hay).ratio()
        if hay == q or slug.lower() == q:
            score = 3.0 + ratio            # tam eşleşme en üstte
        elif hay.startswith(q) or slug.lower().startswith(q):
            score = 2.0 + ratio            # başlangıç eşleşmesi
        elif q in hay or q in slug.lower():
            score = 1.0 + ratio            # alt-dize eşleşmesi (kısa başlık öne)
        elif ratio >= 0.55:
            score = ratio                  # yalnızca fuzzy
        else:
            continue
        scored.append((score, (slug, title)))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:limit]]


# ─────────────────────────────────────────────────────────────────────────────
# Bölüm listesi
# ─────────────────────────────────────────────────────────────────────────────
def get_anime_episodes(anime_slug: str) -> List[Tuple[str, str]]:
    """Anime'nin bölüm listesini döndür.

    Returns: [(episode_id, başlık), ...]; episode_id = "anime_slug/bolum_slug"
    """
    try:
        data = fetch_json(f"animeler/{anime_slug}/bolumler.json")
    except Exception:
        return []
    episodes: List[Tuple[str, str]] = []
    for entry in data or []:
        # entry ya [slug, title] ya da {"slug":..,"title":..}
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            ep_slug, title = entry[0], entry[1]
        elif isinstance(entry, dict):
            ep_slug, title = entry.get("slug"), entry.get("title")
        else:
            continue
        if not ep_slug:
            continue
        episodes.append((f"{anime_slug}/{ep_slug}", title or ep_slug))
    return episodes


# ─────────────────────────────────────────────────────────────────────────────
# Stream linkleri
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_mask(mask: str) -> Optional[str]:
    """Turkanime-stili mask'i çöz; çözülemezse None.

    DİKKAT: `bypass.unmask_real_url()` başarısız olduğunda **girdiyi aynen geri
    döndürür**. Bu değeri doğrulamadan kullanırsak `turkanime.tv/player/<MASK>`
    gibi oynatılamaz bir adresi geçerli stream sanırız (yt-dlp de onu "generic
    direct link" diye kabul edip sessizce bozuk indirme üretir).
    """
    try:
        from .. import bypass  # type: ignore
        full = mask if mask.startswith("http") else getattr(bypass, "BASE_URL", "") + mask
        resolved = bypass.unmask_real_url(full)
    except Exception:
        return None
    if not resolved or "/player/" in resolved:
        return None          # çözülemedi
    return resolved


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün video stream URL'lerini döndür.

    Args:
        episode_id: "anime_slug/bolum_slug" bileşik kimliği.

    Returns: [{"url": ..., "label": ..., "type": "direct", "referer"?: ...}, ...]
    """
    if "/" not in episode_id:
        return []
    anime_slug, ep_slug = episode_id.split("/", 1)
    try:
        data = fetch_json(f"animeler/{anime_slug}/{ep_slug}.json")
    except Exception:
        return []

    streams: List[Dict[str, str]] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        if item.get("alive") is False:
            continue
        player = (item.get("player") or "AnimeDepo").upper()
        fansub = item.get("fansub") or ""
        label = " ".join(p for p in (player.title(), fansub) if p).strip() or player

        url = item.get("url")
        if not url:
            mask = item.get("mask") or item.get("path")
            if mask:
                url = _resolve_mask(mask)
        if not url:
            continue

        stream: Dict[str, str] = {"url": url, "label": label, "type": "direct"}
        # Doğrudan .mp4/.m3u8 dosyaları için turkanime referer'ı yardımcı olabilir
        if re.search(r"\.(mp4|m3u8)(\?|$)", url):
            stream["referer"] = "https://www.turkanime.co/"
        streams.append(stream)
    return streams


__all__ = [
    "search_animedepo",
    "get_anime_episodes",
    "get_episode_streams",
    "get_anime_listesi",
    "BASE_URL",
]
