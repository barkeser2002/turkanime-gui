"""AnimeciX kaynağı (minimal Python port)

Bu modül, AnimeciX API uçlarından arama ve bölüm/izleme verilerini çeker.
Mevcut `objects.Anime/Bolum/Video` yapısına dokunmamak için, yalnızca
harici arama/episode/watch listesi sağlar; indirme/oynatma yine yt-dlp/mpv ile.

Cloudflare koruması için cf_bypass modülü entegre edilmiştir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import json
from urllib.parse import urlparse, parse_qs, quote, urlsplit, urlunsplit

import urllib.request

from ..common.hatalar import kaynak_hatasi

# Cloudflare bypass entegrasyonu
try:
    from ..common.cf_bypass import CFSession, CFBypassError
    HAS_CF_BYPASS = True
except ImportError:
    HAS_CF_BYPASS = False


BASE_URL = "https://animecix.tv/"
ALT_URL = "https://mangacix.net/"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
# Gömme sayfasının yönlendirdiği oynatıcı. Eskiden `["tau-video.xyz",
# "sibnet"]` listesiydi ama yalnızca ilk öğesi okunuyordu; "sibnet" hiçbir
# yolda kullanılmıyordu ve okuyana ikinci bir oynatıcı desteği varmış
# izlenimi veriyordu.
VIDEO_PLAYER = "tau-video.xyz"

# Global CF session (lazy-load)
_cf_session: Optional[CFSession] = None


def _get_cf_session() -> Optional[CFSession]:
    """CF session'ı lazy-load et."""
    global _cf_session
    if _cf_session is None and HAS_CF_BYPASS:
        _cf_session = CFSession(impersonate="chrome110", timeout=15, max_retries=3)
    return _cf_session


def _http_get(url: str, timeout: int = 10) -> bytes:
    """HTTP GET isteği - önce CF bypass, sonra fallback urllib."""
    # Non-ASCII pathleri ASCII'ye uygun hale getirmek için yüzde-encode et
    sp = urlsplit(url)
    safe_path = quote(sp.path, safe="/:%@")
    safe_url = urlunsplit((sp.scheme, sp.netloc, safe_path, sp.query, sp.fragment))
    
    # Önce CF bypass ile dene
    cf_session = _get_cf_session()
    if cf_session is not None:
        try:
            resp = cf_session.get(safe_url, headers=HEADERS)
            if resp.status_code == 200:
                return resp.content
        except (CFBypassError, Exception) as e:
            print(f"[AnimeCix] CF bypass başarısız, fallback kullanılıyor: {e}")
    
    # Fallback: Normal urllib
    req = urllib.request.Request(safe_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _sayisal_kimlik(deger: Any) -> Optional[int]:
    """AnimeciX başlık kimliğini int'e çevir; olmuyorsa ``None``.

    Eskiden burada `hash(str(deger)) % 1000000` yedeği vardı ve iki yönden
    yanlıştı:

    * `hash` PYTHONHASHSEED'e bağlı — aynı slug her süreçte FARKLI sayı
      üretiyordu, yani kimlik ne kalıcı ne de süreçler arası tutarlıydı;
    * üretilen sayı gerçek bir başlığın kimliği olabileceği için kullanıcıya
      sessizce ALAKASIZ bir animenin sezon/bölüm listesi gösteriliyordu.

    Kimlik üretilemiyorsa doğru davranış kaydı atlamak: uydurma bir kimlikle
    ağa çıkmak, hiç çıkmamaktan kötü.
    """
    try:
        return int(deger)
    except (ValueError, TypeError):
        return None


def search_animecix(query: str, timeout: int = 8) -> List[Tuple[str, str]]:
    # Boşluk -> '-' ve non-ASCII karakterleri encode et
    q = (query or "").strip().replace(" ", "-")
    q_enc = quote(q, safe="-")
    url = f"{BASE_URL}secure/search/{q_enc}?type=&limit=20"
    data = json.loads(_http_get(url, timeout=timeout))
    results = []
    res = data.get("results") or []
    for item in res:
        name = item.get("name")
        _id = item.get("id")
        if name is None or _id is None:
            continue
        results.append((str(_id), str(name)))
    return results


def _baslik_bilgisi(safe_id: int) -> Tuple[str, int]:
    """``secure/titles/{id}``'den (ilk videonun kimliği, sezon sayısı).

    Tek istek: `_episodes_for_title` aynı adresi eskiden iki kez istiyordu
    (bir kez video kimliği için, bir kez de `_seasons_for_title` içinde).
    Ağ/JSON hatası yükselir; kararı çağıran verir.
    """
    title_data = json.loads(_http_get(f"{BASE_URL}secure/titles/{safe_id}"))
    title_obj = title_data.get("title", title_data) if isinstance(title_data, dict) else {}
    videos = title_obj.get("videos") or []
    video_id = str((videos[0] or {}).get("id") or "") if videos else ""
    return video_id, len(title_obj.get("seasons") or [])


def _seasons_for_title(title_id: int,
                       bilgi: Optional[Tuple[str, int]] = None) -> List[int]:
    """Sezon indeksleri (0'dan). ``bilgi``: çağıran başlığı zaten çektiyse."""
    # Sayısal olmayan kimlikle AnimeciX'e gitmenin anlamı yok (bkz.
    # `_sayisal_kimlik`): uç zaten 404 verir, uydurma kimlik ise yanlış animeyi
    # getirir. Kaydı atlıyoruz.
    safe_id = _sayisal_kimlik(title_id)
    if safe_id is None:
        return []

    if bilgi is None:
        try:
            bilgi = _baslik_bilgisi(safe_id)
        except Exception:
            return []
    video_id, sezon_sayisi = bilgi
    if sezon_sayisi:
        return list(range(sezon_sayisi))

    # Başlık sezon saymadıysa related-videos'a sorulur; o uç bir video
    # kimliği istiyor. Kimlik yoksa sorulmaz. Eskiden burada sabit
    # bir video kimliği (BAŞKA bir animenin videosu) kullanılıyordu: yanıt o animenin
    # sezonlarını, dolayısıyla alakasız bir bölüm listesini getirebiliyordu.
    if not video_id:
        return []
    url = f"{ALT_URL}secure/related-videos?episode=1&season=1&titleId={safe_id}&videoId={video_id}"
    try:
        data = json.loads(_http_get(url))
        videos = data.get("videos") or []
        if not videos:
            return []
        title = (videos[0] or {}).get("title") or {}
        seasons = title.get("seasons") or []
        return list(range(len(seasons)))
    except Exception:
        return []


def _episodes_for_title(title_id: int) -> List[Dict[str, Any]]:
    safe_id = _sayisal_kimlik(title_id)
    if safe_id is None:
        return []

    try:
        bilgi = _baslik_bilgisi(safe_id)
    except Exception as e:
        raise kaynak_hatasi(e, "AnimeciX", "bölüm listesi alınamadı") from e
    video_id = bilgi[0]
    # Video kimliği yoksa related-videos sorulamaz (bkz. `_seasons_for_title`);
    # uydurma kimlikle sormak başka animenin bölümlerini getirirdi.
    if not video_id:
        return []

    episodes: List[Dict[str, Any]] = []
    seen = set()
    for sidx in _seasons_for_title(safe_id, bilgi):
        url = (
            f"{ALT_URL}secure/related-videos?"
            f"episode=1&season={sidx+1}&titleId={safe_id}&videoId={video_id}"
        )
        try:
            data = json.loads(_http_get(url))
            for v in data.get("videos", []):
                name = v.get("name")
                ep_url = v.get("url")
                if not name or not ep_url:
                    continue
                if name in seen:
                    continue
                episodes.append({"name": name, "url": ep_url, "season_num": v.get("season_num")})
                seen.add(name)
        except Exception:
            continue
    return episodes


def _video_streams(embed_path: str, timeout: int = 10) -> List[Dict[str, str]]:
    # BASE_URL + embed path'e gidip yönlendirilmiş URL'den player id/vid al
    # Embed path non-ASCII içerebilir; güvenle encode et
    full = f"{BASE_URL}{quote(embed_path, safe='/:?=&')}"
    # Basit urllib ile final URL. `timeout` ŞART: `socket.setdefaulttimeout`
    # hiçbir yerde çağrılmıyor, yani parametresiz `urlopen` soketin sonsuz
    # varsayılanına düşüyordu. Bu fonksiyon `adapter.py`nin varsayılan stream
    # sağlayıcısı ve sunucu tarayıcısının AnimeciX ucu; yanıt vermeyen bir
    # sunucu oynatma akışını ve tarama turunu SÜRESİZ askıda bırakıyordu.
    # `_http_get` (bkz. yukarısı) zaten doğru deseni kullanıyordu.
    istek = urllib.request.Request(full, headers=HEADERS)
    with urllib.request.urlopen(istek, timeout=timeout) as resp:
        final_url = resp.geturl()
    p = urlparse(final_url)
    parts = p.path.strip("/").split("/")
    if len(parts) < 2:
        return []
    embed_id = parts[1] if parts[0] == "embed" else parts[0]
    qs = parse_qs(p.query)
    vid = (qs.get("vid") or [None])[0]
    if not embed_id or not vid:
        return []
    api = f"https://{VIDEO_PLAYER}/api/video/{embed_id}?vid={vid}"
    data = json.loads(_http_get(api, timeout=timeout))
    out: List[Dict[str, str]] = []
    for u in data.get("urls", []):
        label = u.get("label")
        url = u.get("url")
        if label and url:
            out.append({"label": label, "url": url})
    return out


@dataclass
class CixEpisode:
    title: str
    url: str


@dataclass
class CixAnime:
    """AnimeciX başlığı.

    Not: Bu sınıf, yalnızca isim ve bölümleri sağlar. Oynatma/indirme için
    mevcut Video/Bolum akışı kullanılmaya devam edilir.
    """
    id: str  # ID artık string olabilir
    title: str

    @property
    def episodes(self) -> List[CixEpisode]:
        # AnimeciX sayısal başlık kimliği bekliyor. Kimlik sayısal değilse bu
        # kaydı AnimeciX üzerinden açamıyoruz; boş liste dönüyoruz. (Qt tarafı
        # aynı durumu `UnsupportedSource` ile kullanıcıya söylüyor; sunucu
        # tarayıcısı ise kaynağı sessizce atlıyor.)
        title_id = _sayisal_kimlik(self.id)
        if title_id is None:
            return []

        eps = _episodes_for_title(title_id)
        out: List[CixEpisode] = []
        for i, e in enumerate(eps):
            out.append(CixEpisode(title=e.get("name") or f"Bölüm {i+1}", url=e.get("url") or ""))
        return out
