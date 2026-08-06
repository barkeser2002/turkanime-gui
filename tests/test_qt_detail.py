"""Anime detay sayfası (Faz 4).

Hiçbir test ağa çıkmaz: `fetch_episodes`, `SearchEngine` ve eşleşme kaydı
`monkeypatch` ile sahtelenir. Jikan/AniList uçları zaten `conftest.py`'deki
autouse fixture ile susturuluyor.
"""
from __future__ import annotations

import threading

import pytest

from turkanime_api.gui.qt.pages import detail as detail_mod
from turkanime_api.gui.qt.pages.detail import (
    AnimeMatchDialog, DetailPage, clean_html, genre_names, meta_line,
    studio_names,
)
# Modül niteliği `_no_match_save` fixture'ı tarafından sahtelenecek; gerçek
# fonksiyonu ayrıca tutuyoruz ki kendisi de test edilebilsin.
from turkanime_api.gui.qt.pages.detail import save_match as real_save_match
from turkanime_api.gui.qt.sources_bridge import UnsupportedSource


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


@pytest.fixture
def page(qtbot):
    widget = DetailPage()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture(autouse=True)
def _no_match_save(monkeypatch):
    """Eşleşme kaydı gerçek API'ye gitmesin (kural 1)."""
    saved: list = []
    monkeypatch.setattr(detail_mod, "save_match",
                        lambda s, sl, t: saved.append((s, sl, t)) or True)
    return saved


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


# ── Render ──────────────────────────────────────────────────────────────────
def test_page_renders_given_dict(page):
    page.show_anime(make_anime())

    assert page.lblTitle.text() == "Cowboy Bebop"
    assert page.lblMeta.text() == "26 bölüm • 24 dk • İlkbahar 1998 • Tamamlandı"
    assert "86%" in page.lblScore.text()
    assert "#42" in page.lblPopularity.text()
    assert [b.text() for b in page.genre_badges] == ["Action", "Sci-Fi"]
    assert [b.text() for b in page.studio_badges] == ["Sunrise"]
    assert "ödül" in page.txtSummary.toPlainText()
    assert "<" not in page.txtSummary.toPlainText(), "HTML etiketi temizlenmedi"


def test_page_renders_anilist_studio_shape(page):
    """AniList `{"nodes": [...]}` şekli de rozete dönüşmeli."""
    page.show_anime(make_anime(studios={"nodes": [{"name": "MAPPA"}]}))
    assert [b.text() for b in page.studio_badges] == ["MAPPA"]


def test_page_survives_empty_dict(page):
    page.show_anime({})
    assert page.lblTitle.text() == "İsimsiz"
    assert page.lblScore.text() == ""
    assert page.genre_badges == []
    assert "Özet bulunamadı" in page.txtSummary.toPlainText()


def test_badge_overflow_is_summarised(page):
    """Çok tür varsa satır taşmasın; kalanlar "+N" rozetinde toplansın."""
    genres = [f"Tür{i}" for i in range(12)]
    page.show_anime(make_anime(genres=genres))
    assert len(page.genre_badges) == detail_mod.MAX_BADGES
    assert page.genre_badges[-1].text() == f"Tür{detail_mod.MAX_BADGES - 1}"


def test_render_replaces_previous_badges(page):
    page.show_anime(make_anime())
    page.show_anime(make_anime("Trigun", studios={"nodes": [{"name": "Madhouse"}]},
                               genres=["Comedy"]))
    assert [b.text() for b in page.genre_badges] == ["Comedy"]
    assert [b.text() for b in page.studio_badges] == ["Madhouse"]


# ── Bölüm getirme ───────────────────────────────────────────────────────────
@pytest.fixture
def fake_fetch(monkeypatch):
    """`sources_bridge.fetch_episodes`'i sahtele; çağrıları kaydet."""
    calls: list = []

    def _install(result=None, error=None, gate=None):
        def fake(source, slug, title):
            calls.append((source, slug, title))
            if gate is not None:
                gate.wait(5)
            if error is not None:
                raise error
            return list(result or [])

        monkeypatch.setattr(detail_mod, "fetch_episodes", fake)
        return calls

    return _install


def test_load_episodes_hands_off_to_episode_page(qtbot, page, fake_fetch):
    """`clicked` bool taşır; slot bunu yutmalı, aksi hâlde buton hiç çalışmaz."""
    from PySide6.QtCore import Qt

    fake_fetch(result=[{"title": "1. Bölüm", "obj": object()}])
    page.show()
    page.show_anime(make_anime(), source="TürkAnime", slug="cowboy-bebop")

    with qtbot.waitSignal(page.episodes_ready, timeout=5000) as blocker:
        qtbot.mouseClick(page.btnEpisodes, Qt.MouseButton.LeftButton)

    source, slug, title, episodes = blocker.args
    assert (source, slug, title) == ("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    assert len(episodes) == 1
    assert "1 bölüm" in page.lblStatus.text()


def test_empty_episode_list_reports_instead_of_handoff(qtbot, page, fake_fetch):
    fake_fetch(result=[])
    page.show_anime(make_anime(), source="TürkAnime", slug="yok")

    emitted: list = []
    page.episodes_ready.connect(lambda *a: emitted.append(a))
    page.load_episodes()

    qtbot.waitUntil(lambda: "bulunamadı" in page.lblStatus.text(), timeout=5000)
    assert emitted == []


def test_unsupported_source_becomes_clear_message(qtbot, page, fake_fetch):
    """AniList (METADATA_ONLY) seçilirse çökme değil, net mesaj gelmeli."""
    fake_fetch(error=UnsupportedSource(
        "AniList yalnızca arama/metadata kaynağı; oynatma için başka bir kaynak seçin."))
    page.show_anime(make_anime(), source="AniList", slug="1")

    emitted: list = []
    page.episodes_ready.connect(lambda *a: emitted.append(a))
    page.load_episodes()

    qtbot.waitUntil(lambda: "metadata" in page.lblStatus.text(), timeout=5000)
    assert emitted == []
    assert page.btnEpisodes.isEnabled(), "hata sonrası buton kilitli kalmamalı"


def test_metadata_only_source_is_selectable_and_warns(page):
    """`supported_sources()` AniList'i vermez; gelen kaynak yine de gösterilmeli."""
    page.show_anime(make_anime(), source="AniList", slug="1")
    assert page.current_source() == "AniList"
    assert "metadata" in page.lblStatus.text()


def test_animecix_non_numeric_slug_message(qtbot, page, fake_fetch):
    """AnimeciX sayısal kimlik bekler; slug uymazsa köprü hata fırlatır."""
    fake_fetch(error=UnsupportedSource(
        "AnimeciX sayısal kimlik bekliyor, 'cowboy-bebop' geçersiz."))
    page.show_anime(make_anime(), source="AnimeciX", slug="cowboy-bebop")
    page.load_episodes()

    qtbot.waitUntil(lambda: "AnimeciX sayısal" in page.lblStatus.text(), timeout=5000)


def test_unbound_anime_opens_match_dialog(page, monkeypatch):
    """Keşiften gelen kayıtta slug yok: kullanıcı çıkmaza girmemeli."""
    opened: list = []
    monkeypatch.setattr(page, "open_match_dialog", lambda: opened.append(True))
    page.show_anime(make_anime())          # kaynak/slug verilmedi

    page.load_episodes()
    assert opened == [True]


# ── Yarış koruması ──────────────────────────────────────────────────────────
def test_stale_result_does_not_overwrite_new_anime(page, fake_fetch):
    """A'nın geç dönen cevabı B'nin ekranını ezmemeli (doğrudan slot çağrısı)."""
    fake_fetch(result=[])
    page.show_anime(make_anime("Anime A"), source="TürkAnime", slug="a")
    stale = page.request_id
    page.show_anime(make_anime("Anime B"), source="TürkAnime", slug="b")

    emitted: list = []
    page.episodes_ready.connect(lambda *a: emitted.append(a))

    page._on_episodes((stale, "TürkAnime", "a", "Anime A", [{"title": "1"}]))
    page._on_failed((stale, "A patladı"))

    assert emitted == [], "eski isteğin sonucu bölüm sayfasına devredildi"
    assert page.lblTitle.text() == "Anime B"
    assert "A patladı" not in page.lblStatus.text()


def test_stale_background_result_is_dropped(qtbot, page, fake_fetch):
    """Aynı koruma gerçek arka plan thread'iyle: A yavaş döner, B beklenir."""
    gate = threading.Event()
    fake_fetch(result=[{"title": "A-1", "obj": object()}], gate=gate)

    emitted: list = []
    page.episodes_ready.connect(lambda *a: emitted.append(a))

    page.show_anime(make_anime("Anime A"), source="TürkAnime", slug="a")
    page.load_episodes()                      # arka planda gate'te bekliyor
    page.show_anime(make_anime("Anime B"), source="TürkAnime", slug="b")
    gate.set()                                # A'nın cevabı ŞİMDİ dönüyor

    qtbot.wait(300)
    assert emitted == []
    assert page.lblTitle.text() == "Anime B"


def test_stale_cover_is_not_applied(page):
    """Geç inen kapak, o sırada açık olan animenin üstüne basılmamalı."""
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
        "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    page.show_anime(make_anime("Anime A"))
    stale = page.request_id
    page.show_anime(make_anime("Anime B"))

    page._apply_cover(stale, png)
    assert page.lblCover.pixmap().isNull(), "eski kapak yeni animeye yapıştı"

    page._apply_cover(page.request_id, png)
    assert not page.lblCover.pixmap().isNull()


# ── Eşleştirme diyaloğu ─────────────────────────────────────────────────────
@pytest.fixture
def fake_engine(monkeypatch):
    """`SearchEngine.search_all_sources_rich`'i sahtele (ağa çıkma yok)."""
    def _install(results):
        import turkanime_api.common.adapters as adapters_mod

        class FakeEngine:
            def search_all_sources_rich(self, query, limit_per_source=10):
                return results

        monkeypatch.setattr(adapters_mod, "SearchEngine", FakeEngine)

    return _install


def test_match_dialog_groups_results_by_source(qtbot, fake_engine):
    fake_engine({
        "TürkAnime": [{"slug": "cb", "title": "Cowboy Bebop"}],
        "AnimeciX": [{"slug": "17", "title": "Cowboy Bebop"},
                     {"slug": "18", "title": "Cowboy Bebop: Film"}],
        "Anizle": [],
    })
    dlg = AnimeMatchDialog("Cowboy Bebop")
    qtbot.addWidget(dlg)
    dlg.search()

    qtbot.waitUntil(lambda: dlg.tree.topLevelItemCount() == 2, timeout=5000)
    groups = {dlg.tree.topLevelItem(i).text(0) for i in range(2)}
    assert groups == {"TürkAnime (1)", "AnimeciX (2)"}
    assert "3 aday" in dlg.lblStatus.text()


def test_match_dialog_returns_selection(qtbot, fake_engine):
    from PySide6.QtWidgets import QDialog

    fake_engine({"TürkAnime": [{"slug": "cb", "title": "Cowboy Bebop"}]})
    dlg = AnimeMatchDialog("Cowboy Bebop")
    qtbot.addWidget(dlg)
    dlg.search()
    qtbot.waitUntil(lambda: dlg.tree.topLevelItemCount() == 1, timeout=5000)

    leaf = dlg.tree.topLevelItem(0).child(0)
    dlg.tree.setCurrentItem(leaf)
    dlg.accept_selection()

    assert dlg.selection == ("TürkAnime", "cb", "Cowboy Bebop")
    assert dlg.result() == QDialog.DialogCode.Accepted


def test_match_dialog_rejects_group_header(qtbot, fake_engine):
    """Kaynak başlığına basmak seçim sayılmamalı."""
    fake_engine({"TürkAnime": [{"slug": "cb", "title": "Cowboy Bebop"}]})
    dlg = AnimeMatchDialog("Cowboy Bebop")
    qtbot.addWidget(dlg)
    dlg.search()
    qtbot.waitUntil(lambda: dlg.tree.topLevelItemCount() == 1, timeout=5000)

    dlg.tree.setCurrentItem(dlg.tree.topLevelItem(0))
    dlg.accept_selection()
    assert dlg.selection is None
    assert "seçin" in dlg.lblStatus.text()


# ── Elle eşleştirme vs. arka plan eşleşmesi ─────────────────────────────────
def test_elle_secim_gec_donen_otomatik_eslesmeyi_yeniyor(qtbot, page, fake_fetch):
    """Kullanıcı yükleme sürerken doğru sezonu seçti; arka plan onu EZMEMELİ."""
    calls = fake_fetch(result=[{"title": "1. Bölüm", "obj": object()}])
    page.show_anime(make_anime(), source="TürkAnime", slug="yanlis-sezon")
    rid = page.request_id

    # "İstediğin anime değil mi?" → doğru sezon
    page.apply_match("TürkAnime", "dogru-sezon", "Doğru Sezon")
    # Arka planda süren otomatik eşleştirme ŞİMDİ dönüyor (eski anlık görüntüyle)
    page._on_sources_resolved((rid, {"TürkAnime": "otomatik-yanlis"},
                               False, "TürkAnime"))

    qtbot.waitUntil(lambda: bool(calls), timeout=5000)
    assert calls[0][1] == "dogru-sezon", "kullanıcının seçimi ezildi"
    assert page._bindings["TürkAnime"] == "dogru-sezon"


def test_otomatik_eslesme_bos_kaynaklari_doldurmaya_devam_ediyor(page, fake_fetch):
    """Koruma yalnızca ELLE seçilen kaynağa: diğerleri yine otomatik bağlanmalı."""
    fake_fetch(result=[])
    page.show_anime(make_anime(), source="TürkAnime", slug="yanlis")
    rid = page.request_id
    page.apply_match("TürkAnime", "dogru-sezon", "Doğru Sezon")

    page._on_sources_resolved((rid, {"TürkAnime": "otomatik", "AnimeDepo": "depo"},
                               True, "TürkAnime"))

    assert page._bindings["TürkAnime"] == "dogru-sezon"
    assert page._bindings["AnimeDepo"] == "depo"


def test_elle_secim_gercek_arka_plan_isiyle_de_korunuyor(qtbot, page, fake_fetch,
                                                         monkeypatch):
    """Aynı koruma gerçek thread'le: `_do_resolve` yavaş döner, seçim beklenir."""
    import turkanime_api.common.adapters as adapters_mod

    kapi = threading.Event()
    calls = fake_fetch(result=[{"title": "1. Bölüm", "obj": object()}])

    class GecikenEngine:
        def search_all_sources_rich(self, query, limit_per_source=10):
            kapi.wait(5)
            return {"TürkAnime": [{"slug": "otomatik-yanlis", "title": query}]}

    monkeypatch.setattr(adapters_mod, "SearchEngine", GecikenEngine)

    page.show_anime(make_anime(), source="AnimeDepo", slug="depo-slug")
    page.chkAllSources.setChecked(True)      # tüm kaynaklarda eşleşme aransın
    page.load_episodes()                     # arka planda kapıda bekliyor

    page.apply_match("TürkAnime", "dogru-sezon", "Doğru Sezon")
    kapi.set()                               # otomatik eşleşme ŞİMDİ dönüyor

    qtbot.waitUntil(lambda: len(calls) >= 2, timeout=5000)
    slugs = {kaynak: slug for kaynak, slug, _t in calls}
    assert slugs["TürkAnime"] == "dogru-sezon", "kullanıcının seçimi ezildi"
    assert slugs["AnimeDepo"] == "depo-slug"


def test_apply_match_binds_source_and_saves(page, _no_match_save):
    page.show_anime(make_anime())
    page.apply_match("AnimeDepo", "cowboy-bebop", "Cowboy Bebop (TR)")

    assert page.current_source() == "AnimeDepo"
    assert _no_match_save == [("AnimeDepo", "cowboy-bebop", "Cowboy Bebop (TR)")]
    # Künye korunmalı: kullanıcı aynı animenin doğru kaydını seçti.
    assert page.lblTitle.text() == "Cowboy Bebop"


def test_save_match_swallows_api_failure(monkeypatch):
    """API kapalıysa çevrimdışı akış bozulmamalı."""
    import turkanime_api.common.db as db_mod

    class Bozuk:
        def __init__(self):
            raise OSError("API yok")

    monkeypatch.setattr(db_mod, "APIManager", Bozuk)
    assert real_save_match("TürkAnime", "cb", "Cowboy Bebop") is False


# ── Ana pencere kablolaması ─────────────────────────────────────────────────
def test_search_result_opens_detail_then_episodes(qtbot, main_window, fake_fetch):
    from turkanime_api.gui.qt.pages.detail import DetailPage as _DetailPage

    fake_fetch(result=[{"title": "1. Bölüm", "obj": object()},
                       {"title": "2. Bölüm", "obj": object()}])

    detail = main_window.pages["detail"]
    assert isinstance(detail, _DetailPage)

    main_window.pages["search"].anime_selected.emit(
        "TürkAnime", "cowboy-bebop", "Cowboy Bebop")

    assert main_window.stack.currentWidget() is detail
    assert detail.lblTitle.text() == "Cowboy Bebop"
    assert detail.current_source() == "TürkAnime"

    detail.load_episodes()
    episodes = main_window.pages["episodes"]
    qtbot.waitUntil(lambda: main_window.stack.currentWidget() is episodes,
                    timeout=5000)
    assert len(episodes.visible_rows()) == 2
    assert "Cowboy Bebop — TürkAnime" == episodes.lblTitle.text()


def test_episode_page_does_not_refetch_when_given_list(main_window, fake_fetch):
    """Devralınan liste ikinci kez ağdan çekilmemeli."""
    calls = fake_fetch(result=[{"title": "1. Bölüm", "obj": object()}])
    episodes = main_window.pages["episodes"]
    episodes.load("TürkAnime", "cb", "Cowboy Bebop",
                  episodes=[{"title": "1. Bölüm", "obj": object()}])

    assert calls == []
    assert len(episodes.visible_rows()) == 1


def test_detail_back_returns_to_origin(main_window):
    main_window.show_page("trending")
    main_window.pages["trending"].anime_selected.emit(make_anime())
    assert main_window.stack.currentWidget() is main_window.pages["detail"]

    main_window.pages["detail"].back_requested.emit()
    assert main_window.stack.currentWidget() is main_window.pages["trending"]
