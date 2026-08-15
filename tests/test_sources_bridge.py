"""Kaynak → bölüm köprüsü (ağsız yapı testleri)."""
from __future__ import annotations

import pytest

from turkanime_api.gui.qt import sources_bridge as sb


def test_search_and_playback_sources_stay_aligned():
    """Aramada çıkan her kaynak oynatılabilir olmalı.

    Aksi hâlde kullanıcı sonuca tıklıyor ve "bu kaynak bağlı değil" duvarına
    çarpıyor. AniList bilinçli istisna: yalnızca metadata.
    """
    from turkanime_api.common.adapters import SearchEngine

    searchable = set(SearchEngine().adapters)
    playable = set(sb.supported_sources())
    assert searchable - playable == sb.METADATA_ONLY


def test_metadata_only_source_raises_with_guidance():
    with pytest.raises(sb.UnsupportedSource) as exc:
        sb.fetch_episodes("AniList", "1", "Naruto")
    assert "başka bir kaynak" in str(exc.value)


def test_unknown_source_lists_supported_ones():
    with pytest.raises(sb.UnsupportedSource) as exc:
        sb.fetch_episodes("YokBöyleKaynak", "x", "X")
    assert "AnimeDepo" in str(exc.value)


def test_animecix_rejects_non_numeric_slug():
    """Eskiden `hash(slug) % 1000000` ile sahte ID üretiliyordu.

    O ID hem PYTHONHASHSEED yüzünden her açılışta farklı hem de tamamen
    alakasız bir anime demekti — sessizce yanlış bölüm listesi. Artık açık hata.
    """
    with pytest.raises(sb.UnsupportedSource) as exc:
        sb.fetch_episodes("AnimeciX", "naruto-slug", "Naruto")
    assert "sayısal" in str(exc.value)


def test_every_function_source_is_wired():
    for name, cfg in sb.FUNCTION_SOURCES.items():
        assert callable(cfg["loader"]), name
        assert cfg["player"], name
        assert callable(cfg["ep_url"]), name


def test_animedepo_episode_id_passes_through_unchanged():
    """AnimeDepo bileşik kimlik ("anime/bolum") kullanır; URL'ye çevrilmemeli."""
    assert sb.FUNCTION_SOURCES["AnimeDepo"]["ep_url"]("naruto/naruto-1") == "naruto/naruto-1"


def test_stream_provider_swallows_source_errors():
    """Bozuk bir kaynak tüm bölüm listesini düşürmemeli."""
    def kaynak_patlar(_ep_id):
        raise RuntimeError("kaynak çöktü")

    provider = sb._make_provider(kaynak_patlar, "ep1")
    assert provider("http://x") == []
