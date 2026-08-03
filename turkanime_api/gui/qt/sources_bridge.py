"""Kaynak → bölüm/stream köprüsü.

Eski `gui/main.py`, her kaynak için ~200 satırlık inline `if/elif` bloklarıyla
`AdapterAnime`/`AdapterBolum` kuruyordu. Burada aynı iş tek yerde, tablo
sürücülü biçimde yapılır; yeni bir fonksiyon-stili kaynak eklemek `FUNCTION_SOURCES`
sözlüğüne bir satır eklemekten ibaret.

Dönen bölüm nesneleri `best_video(...)` arayüzünü sağlar; böylece indirme/oynatma
boru hattı (yt-dlp + mpv) değişmeden çalışır.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# Kaynak adı -> playback/indirme desteği yok (yalnızca arama/metadata)
METADATA_ONLY = {"AniList"}


# ── Fonksiyon-stili kaynaklar (search/episodes/streams üçlüsü) ──────────────
def _openani():
    from ...sources.openani import (
        get_anime_episodes as episodes, get_episode_streams as streams,
    )
    return episodes, streams


def _tranimaci():
    from ...sources.tranimaci import (
        get_anime_episodes as episodes, get_episode_streams as streams,
    )
    return episodes, streams


def _animedepo():
    from ...sources.animedepo import (
        get_anime_episodes as episodes, get_episode_streams as streams,
    )
    return episodes, streams


FUNCTION_SOURCES: Dict[str, Dict[str, Any]] = {
    "OpenAnime": {
        "loader": _openani,
        "player": "OPENANI",
        "ep_url": lambda ep: f"https://openani.me/anime/{ep}",
    },
    "Tranimaci": {
        "loader": _tranimaci,
        "player": "TRANIMACI",
        "ep_url": lambda ep: f"https://tranimaci.com/video/{ep}",
    },
    "AnimeDepo": {
        "loader": _animedepo,
        "player": "ANIMEDEPO",
        "ep_url": lambda ep: ep,   # "anime_slug/bolum_slug" bileşik kimlik
    },
}


class UnsupportedSource(Exception):
    """Bu kaynak Qt GUI'sinde henüz oynatma/indirme için bağlanmadı."""


def _build_function_source(source: str, slug: str, title: str) -> List[Dict[str, Any]]:
    from ...sources.adapter import AdapterAnime, AdapterBolum

    cfg = FUNCTION_SOURCES[source]
    episodes_fn, streams_fn = cfg["loader"]()
    raw = episodes_fn(slug) or []
    if not raw:
        return []

    anime = AdapterAnime(slug=slug, title=title)
    out: List[Dict[str, Any]] = []
    for ep_id, ep_title in raw:
        out.append({
            "title": ep_title,
            "obj": AdapterBolum(
                url=cfg["ep_url"](ep_id),
                title=ep_title,
                anime=anime,
                stream_provider=_make_provider(streams_fn, ep_id),
                player_name=cfg["player"],
            ),
        })
    return out


def _make_provider(streams_fn: Callable, ep_id: str) -> Callable[[str], List[Dict[str, str]]]:
    """Bölüm kimliğini kapatan stream sağlayıcı (url argümanı yok sayılır)."""
    def provider(_url: str) -> List[Dict[str, str]]:
        try:
            return streams_fn(ep_id) or []
        except Exception:
            return []
    return provider


# ── TürkAnime: yerel (legacy) Anime/Bolum yolu ──────────────────────────────
def _build_turkanime(slug: str, title: str) -> List[Dict[str, Any]]:
    from ...objects import Anime

    anime = Anime(slug)
    return [{"title": b.title, "obj": b} for b in anime.bolumler]


# ── TRAnimeİzle: bölüm detayından iframe kaynakları ─────────────────────────
def _build_tranime(slug: str, title: str) -> List[Dict[str, Any]]:
    from ...sources.adapter import AdapterAnime, AdapterBolum
    from ...sources.tranime import (
        get_anime_episodes, get_episode_details,
    )

    raw = get_anime_episodes(slug) or []
    if not raw:
        return []

    anime = AdapterAnime(slug=slug, title=title)

    def provider_for(ep_slug: str):
        def provider(_url: str) -> List[Dict[str, str]]:
            try:
                details = get_episode_details(ep_slug)
                if not details:
                    return []
                out = []
                for s in details.get_sources():
                    iframe = s.get_iframe()
                    if iframe:
                        out.append({"url": iframe, "label": s.name})
                return out
            except Exception:
                return []
        return provider

    return [{
        "title": e.title,
        "obj": AdapterBolum(
            url=e.slug, title=e.title, anime=anime,
            stream_provider=provider_for(e.slug), player_name="TRANIME",
        ),
    } for e in raw]


# ── AnimeciX: bölüm URL'leri; stream çözümü adapter.py varsayılanı ──────────
def _build_animecix(slug: str, title: str) -> List[Dict[str, Any]]:
    from ...sources.adapter import AdapterAnime, AdapterBolum
    from ...sources.animecix import CixAnime

    # AnimeciX araması sayısal ID döndürür. Eskiden ID olmayan slug'lar için
    # `hash(slug) % 1000000` üretiliyordu; bu hem PYTHONHASHSEED yüzünden her
    # açılışta FARKLI hem de tamamen ALAKASIZ bir anime kimliği demek. Sessizce
    # yanlış bölüm listesi göstermektense açıkça hata veriyoruz.
    try:
        safe_id = int(slug)
    except (ValueError, TypeError):
        raise UnsupportedSource(
            f"AnimeciX sayısal kimlik bekliyor, '{slug}' geçersiz. "
            "Bu sonucu AnimeciX üzerinden açamıyoruz; başka bir kaynak seçin."
        )

    cix = CixAnime(id=str(safe_id), title=str(title))
    anime = AdapterAnime(slug=str(cix.id), title=cix.title)
    # stream_provider verilmiyor: AdapterBolum varsayılan olarak
    # animecix._video_streams kullanır.
    return [{
        "title": e.title,
        "obj": AdapterBolum(url=e.url, title=e.title, anime=anime),
    } for e in cix.episodes]


# ── Anizle: bölüm URL'sinden stream (FirePlayer boru hattı) ─────────────────
def _build_anizle(slug: str, title: str) -> List[Dict[str, Any]]:
    from ...sources.adapter import AdapterAnime, AdapterBolum
    from ...sources.anizle import AnizleAnime, get_episode_streams

    an = AnizleAnime(slug=slug, title=title)
    anime = AdapterAnime(slug=an.slug, title=an.title)

    def provider(url: str) -> List[Dict[str, str]]:
        # Anizle bölüm URL'sini doğrudan kullanır; ep kimliği kapatmaya gerek yok.
        try:
            return get_episode_streams(url, timeout=10) or []
        except Exception:
            return []

    return [{
        "title": e.title,
        "obj": AdapterBolum(url=e.url, title=e.title, anime=anime,
                            stream_provider=provider, player_name="ANIZLE"),
    } for e in an.episodes]


BUILDERS: Dict[str, Callable[[str, str], List[Dict[str, Any]]]] = {
    "TürkAnime": _build_turkanime,
    "TRAnimeİzle": _build_tranime,
    "AnimeciX": _build_animecix,
    "Anizle": _build_anizle,
}


def supported_sources() -> List[str]:
    """Oynatma/indirme için bağlanmış kaynaklar."""
    return sorted(set(BUILDERS) | set(FUNCTION_SOURCES))


def fetch_episodes(source: str, slug: str, title: str) -> List[Dict[str, Any]]:
    """Kaynağa göre bölüm listesini döndür.

    Returns: [{"title": str, "obj": <best_video() sağlayan nesne>}, ...]
    Raises:  UnsupportedSource — kaynak henüz Qt GUI'sinde bağlanmadıysa.
    """
    if source in METADATA_ONLY:
        raise UnsupportedSource(
            f"{source} yalnızca arama/metadata kaynağı; oynatma için başka bir kaynak seçin."
        )
    if source in FUNCTION_SOURCES:
        return _build_function_source(source, slug, title)
    builder = BUILDERS.get(source)
    if builder is None:
        raise UnsupportedSource(
            f"{source} kaynağı Qt arayüzünde henüz bağlanmadı "
            f"(desteklenenler: {', '.join(supported_sources())})."
        )
    return builder(slug, title)


__all__ = ["fetch_episodes", "supported_sources", "UnsupportedSource", "METADATA_ONLY"]
