"""Keşif sayfaları (Ana Sayfa / Trend / Bu Sezon).

Hiçbir test ağa çıkmaz: Jikan ve AniList uçları `monkeypatch` ile sahtelenir.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from turkanime_api.gui.qt.pages.discover import (
    DiscoverPage, anime_title, cover_url, fetch_discover, score_of, season_label,
)
from turkanime_api.gui.qt.theme import ACCENT, DANGER


def make_item(title: str, score: int | None = 80, cover: str | None = None):
    """Jikan `to_dict` / AniList media çıktısıyla aynı şekle sahip sahte kayıt."""
    return {
        "id": abs(hash(title)) % 100000,
        "title": {"romaji": title, "english": None, "native": None},
        "coverImage": {"large": cover, "medium": cover},
        "averageScore": score,
        "episodes": 12,
        "genres": ["Action"],
    }


@pytest.fixture
def fake_sources(monkeypatch):
    """Jikan/AniList uçlarını sahtele; çağrı kayıtlarını döndür."""
    import turkanime_api.anilist_client as anilist_mod
    import turkanime_api.jikan_client as jikan_mod

    calls: dict = {"trending": 0, "season": 0, "anilist": 0}

    def _install(trending=None, season=None, anilist=None):
        def fake_trending(limit=25, **_kw):
            calls["trending"] += 1
            if callable(trending):
                return trending(limit)
            return list(trending or [])

        def fake_season(year=None, season_name=None, **_kw):
            calls["season"] += 1
            if callable(season):
                return season()
            return list(season or [])

        def fake_anilist(*_a, **_kw):
            calls["anilist"] += 1
            if callable(anilist):
                return anilist()
            return list(anilist or [])

        monkeypatch.setattr(jikan_mod, "get_trending_anime_list", fake_trending)
        monkeypatch.setattr(jikan_mod, "get_seasonal_anime_list", fake_season)
        monkeypatch.setattr(anilist_mod.anilist_client, "get_trending_anime",
                            fake_anilist)
        return calls

    return _install


# ── Saf veri katmanı (Qt'siz) ───────────────────────────────────────────────
def test_fetch_uses_jikan_per_mode(fake_sources):
    calls = fake_sources(trending=[make_item("Trend")], season=[make_item("Sezon")])

    assert anime_title(fetch_discover("trending")[0]) == "Trend"
    assert anime_title(fetch_discover("home")[0]) == "Trend"
    assert anime_title(fetch_discover("season")[0]) == "Sezon"

    assert calls["trending"] == 2 and calls["season"] == 1
    assert calls["anilist"] == 0, "Jikan çalışırken AniList'e gidilmemeli"


def test_fetch_falls_back_to_anilist_when_jikan_empty(fake_sources):
    calls = fake_sources(trending=[], season=[], anilist=[make_item("Yedek")])

    for mode in ("home", "trending", "season"):
        items = fetch_discover(mode)
        assert [anime_title(i) for i in items] == ["Yedek"], mode

    assert calls["anilist"] == 3


def test_fetch_falls_back_when_jikan_raises(fake_sources):
    def boom(_limit=None):
        raise RuntimeError("Jikan 504")

    calls = fake_sources(trending=boom, anilist=[make_item("Yedek")])
    assert [anime_title(i) for i in fetch_discover("trending")] == ["Yedek"]
    assert calls["anilist"] == 1


def test_fetch_returns_empty_when_both_fail(fake_sources):
    def boom(*_a):
        raise RuntimeError("kopuk")

    fake_sources(trending=boom, season=boom, anilist=boom)
    assert fetch_discover("home") == []


def test_fetch_honours_limit(fake_sources):
    fake_sources(trending=[make_item(f"A{i}") for i in range(50)])
    assert len(fetch_discover("trending", limit=7)) == 7


def test_helpers_tolerate_missing_fields():
    assert anime_title({}) == "İsimsiz"
    assert anime_title({"title": {"english": "Only English"}}) == "Only English"
    assert cover_url({}) is None
    assert score_of({"averageScore": None}) is None
    assert score_of({"averageScore": "abc"}) is None
    assert score_of({"averageScore": 84}) == 84.0


@pytest.mark.parametrize("month,expected", [
    (1, "Kış"), (3, "Kış"), (4, "İlkbahar"), (6, "İlkbahar"),
    (7, "Yaz"), (9, "Yaz"), (10, "Sonbahar"), (12, "Sonbahar"),
])
def test_season_label(month, expected):
    assert season_label(datetime(2026, month, 15)) == f"{expected} 2026"


# ── Sayfa davranışı ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("mode", ["home", "trending", "season"])
def test_page_builds_cards_in_every_mode(qtbot, fake_sources, mode):
    items = [make_item(f"Anime {i}") for i in range(5)]
    fake_sources(trending=items, season=items)

    page = DiscoverPage(mode)
    qtbot.addWidget(page)
    page.refresh()

    qtbot.waitUntil(lambda: len(page.cards()) == 5, timeout=5000)
    assert page.results.grid.count() == 5
    assert [c.lblTitle.text() for c in page.cards()][0] == "Anime 0"
    assert "5" in page.lblStatus.text()


def test_page_shows_fallback_data(qtbot, fake_sources):
    """Jikan boş dönerse kullanıcı boş ekran değil AniList verisi görmeli."""
    fake_sources(trending=[], anilist=[make_item("AniList Anime")])

    page = DiscoverPage("trending")
    qtbot.addWidget(page)
    page.refresh()

    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)
    assert page.cards()[0].lblTitle.text() == "AniList Anime"


def test_page_reports_empty_result(qtbot, fake_sources):
    fake_sources(trending=[], anilist=[])

    page = DiscoverPage("home")
    qtbot.addWidget(page)
    page.refresh()

    qtbot.waitUntil(lambda: page.btnRefresh.isEnabled(), timeout=5000)
    assert page.cards() == []
    assert "alınamadı" in page.lblStatus.text()


def test_page_loads_once_on_first_show(qtbot, fake_sources):
    """Sekmeye her dönüşte yeniden ağ isteği atılmamalı."""
    calls = fake_sources(trending=[make_item("Anime")])

    page = DiscoverPage("trending")
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    page.hide()
    page.show()
    qtbot.wait(50)
    assert calls["trending"] == 1


def test_refresh_button_reloads(qtbot, fake_sources):
    """`clicked` bool taşır; slot bunu yutmalı, aksi hâlde buton hiç çalışmaz."""
    from PySide6.QtCore import Qt

    calls = fake_sources(trending=[make_item("Anime")])

    page = DiscoverPage("trending")
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    qtbot.mouseClick(page.btnRefresh, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: calls["trending"] == 2, timeout=5000)
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)


def test_score_badge_uses_score_color(qtbot, fake_sources):
    fake_sources(trending=[make_item("İyi", 90), make_item("Kötü", 20),
                           make_item("Puansız", None)])

    page = DiscoverPage("trending")
    qtbot.addWidget(page)
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 3, timeout=5000)

    good, bad, none = page.cards()
    assert good.lblSource.text() == "★ 9.0"
    assert ACCENT in good.lblSource.styleSheet()
    assert DANGER in bad.lblSource.styleSheet()
    assert none.lblSource.text() == "Puansız"


def test_card_click_opens_detail_page(qtbot, main_window, fake_sources):
    """Kart tıklaması detay sayfasını açar ve TÜM kaydı taşır.

    Faz 4 öncesi yalnızca başlık taşınıp aramaya köprüleniyordu; artık özet ve
    türlerin yeniden çekilmesine gerek kalmasın diye sözlüğün tamamı gider.
    """
    from PySide6.QtCore import Qt

    fake_sources(trending=[make_item("Cowboy Bebop")])

    page = main_window.pages["trending"]
    main_window.show_page("trending")
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    qtbot.mouseClick(page.cards()[0], Qt.MouseButton.LeftButton)

    detail = main_window.pages["detail"]
    assert main_window.stack.currentWidget() is detail
    assert detail.lblTitle.text() == "Cowboy Bebop"
    assert [b.text() for b in detail.genre_badges] == ["Action"]


def test_main_window_wires_all_discover_modes(main_window):
    for key in ("home", "trending", "season"):
        page = main_window.pages[key]
        assert isinstance(page, DiscoverPage)
        assert page.mode == key
