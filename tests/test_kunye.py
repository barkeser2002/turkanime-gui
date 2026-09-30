"""Anime künyesi okuma yardımcıları (`gui/web/kunye.py`) ve eşleşme kaydı.

Detay sayfasının kendisi `test_web_detay.py`'de (gerçek QtWebEngine).
"""
from __future__ import annotations

from turkanime_api.gui.web.eslestirme import save_match as real_save_match
from turkanime_api.gui.web.kunye import clean_html, genre_names, meta_line, studio_names


def make_anime(title="Cowboy Bebop", studios=None, **extra):
    """Jikan `to_anilist_format` / AniList media çıktısıyla aynı şekil."""
    data = {
        "id": 1,
        "title": {"romaji": title, "english": None, "native": None},
        "coverImage": {"large": None, "medium": None},
        "description": "<p>Uzayda <i>ödül</i> avcıları.<br>İkinci satır.</p>",
        "episodes": 26,
        "duration": 24,
        "season": "SPRING",
        "seasonYear": 1998,
        "status": "FINISHED",
        "averageScore": 86,
        "popularity": 42,
        "genres": ["Action", "Sci-Fi"],
        "studios": studios if studios is not None else ["Sunrise"],
    }
    data.update(extra)
    return data


# ── Saf veri katmanı (Qt'siz) ───────────────────────────────────────────────
def test_clean_html_strips_tags_and_entities():
    assert clean_html("<p>Bir <b>iki</b><br>üç &amp; dört</p>") == "Bir iki\nüç & dört"
    assert clean_html(None) == ""
    assert clean_html("") == ""


def test_studio_names_supports_both_shapes():
    """Jikan düz liste, AniList `{"nodes": [...]}` döndürür; ikisi de geçerli."""
    assert studio_names({"studios": ["Sunrise", "Bones"]}) == ["Sunrise", "Bones"]
    assert studio_names(
        {"studios": {"nodes": [{"name": "MAPPA"}, {"name": "Wit"}]}}
    ) == ["MAPPA", "Wit"]
    # Ham Jikan kaydı (to_anilist_format'tan geçmemiş)
    assert studio_names({"studios": [{"name": "Ufotable"}]}) == ["Ufotable"]
    assert studio_names({}) == []
    assert studio_names({"studios": None}) == []
    assert studio_names({"studios": {"nodes": [{}, {"name": ""}]}}) == []


def test_genre_names_tolerates_shapes():
    assert genre_names({"genres": ["Action"]}) == ["Action"]
    assert genre_names({"genres": [{"name": "Drama"}]}) == ["Drama"]
    assert genre_names({"genres": "Action"}) == []
    assert genre_names({}) == []


def test_meta_line_is_turkish_and_skips_missing():
    assert meta_line(make_anime()) == "26 bölüm • 24 dk • İlkbahar 1998 • Tamamlandı"
    assert meta_line({"episodes": 12}) == "12 bölüm"
    assert meta_line({"status": "RELEASING"}) == "Yayında"
    assert meta_line({}) == ""


def test_save_match_swallows_api_failure(monkeypatch):
    """API kapalıysa çevrimdışı akış bozulmamalı."""
    import turkanime_api.common.db as db_mod

    class Bozuk:
        def __init__(self):
            raise OSError("API yok")

    monkeypatch.setattr(db_mod, "APIManager", Bozuk)
    assert real_save_match("TürkAnime", "cb", "Cowboy Bebop") is False
