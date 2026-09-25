"""Kaynaklar (TürkAnime arşivi, AnimeciX, Anizle, TRAnimeİzle, OpenAnime,
Tranimaci) için facade.

Kaynak listesinin tek yeri `kayit.py`; buradaki `PROVIDERS` ondan türetilir.
Yeni kaynak eklemek için `ANIME_PROVIDER_GUIDE.md`'ye bakın —
`register_provider` yalnızca geriye uyum için duruyor ve üretimde okunmuyor.
"""

from .animecix import CixAnime, search_animecix  # noqa: F401
from .anizle import AnizleAnime, search_anizle  # noqa: F401
from .tranime import (  # noqa: F401
    TRAnimeAnime, TRAnimeEpisode, TRAnimeVideo,
    search_tranime, get_anime_by_slug as get_tranime_anime,
    get_anime_episodes as get_tranime_episodes,
    get_episode_details as get_tranime_episode_details,
    set_session_cookie as set_tranime_cookie
)
from .openani import ( # noqa: F401
    OpenAniAdapter, OpenAniAnime, search_openani,
    get_anime_episodes as get_openani_episodes,
    get_episode_streams as get_openani_streams
)
from .tranimaci import (  # noqa: F401
    search_tranimaci,
    get_anime_episodes as get_tranimaci_episodes,
    get_episode_streams as get_tranimaci_streams,
)
from .animedepo import (  # noqa: F401
    search_animedepo,
    get_anime_episodes as get_animedepo_episodes,
    get_episode_streams as get_animedepo_streams,
)

from . import kayit  # noqa: F401  (tek kaynak kaydı; aşağıdaki PROVIDERS ondan türüyor)


def _saglayicilar():
    """`kayit.KAYNAKLAR`'dan eski `PROVIDERS` biçimi: {modül: {...}}.

    Liste eskiden burada elle tutuluyordu ve SearchEngine/köprü/CLI'daki
    kopyalarıyla ayrışmıştı (TürkAnime hiç yoktu, AnimeDepo ayrı bir kaynaktı).
    Artık tek kaynak `kayit.py`; anahtarlar yine modül adı ("tranime",
    "openani"), çünkü sunucu tarayıcısı ve katkı API'si bu adları kullanıyor.
    TürkAnime burada "animedepo" anahtarıyla duruyor: arşivi okuyan modül o.
    AniList (yalnızca metadata) sağlayıcı sayılmaz, listede yok.
    """
    out = {}
    for sira, kaynak in enumerate(kayit.kaynaklar(metadata=False), start=1):
        out[kaynak.modul] = {
            "name": kaynak.ad,
            "adapter": globals().get(kaynak.adaptor_sinifi or ""),
            "enabled": True,
            "priority": sira,
        }
    return out


PROVIDERS = _saglayicilar()


def register_provider(name: str, adapter_class, enabled: bool = True, priority: int = 5):
    """Yeni bir anime sağlayıcısı kaydet."""
    PROVIDERS[name] = {
        "name": name,
        "adapter": adapter_class,
        "enabled": enabled,
        "priority": priority
    }

def get_enabled_providers():
    """Etkin sağlayıcıları döndür."""
    return {name: data for name, data in PROVIDERS.items() if data["enabled"]}

def get_provider_by_priority():
    """Öncelik sırasına göre sağlayıcıları döndür."""
    enabled = get_enabled_providers()
    return sorted(enabled.items(), key=lambda x: x[1]["priority"])
