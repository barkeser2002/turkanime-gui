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
    assert anime_title(fetch_discover("season")[0]) == "Sezon"

    assert calls["trending"] == 1 and calls["season"] == 1
    assert calls["anilist"] == 0, "Jikan çalışırken AniList'e gidilmemeli"


def test_fetch_falls_back_to_anilist_when_jikan_empty(fake_sources):
    """Trend ve ana sayfa yedeğe düşer; sezon DÜŞMEZ (bkz. sezon testleri)."""
    calls = fake_sources(trending=[], season=[], anilist=[make_item("Yedek")])

    for mode in ("home", "trending"):
        items = fetch_discover(mode)
        assert [anime_title(i) for i in items] == ["Yedek"], mode

    assert calls["anilist"] == 2


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


# ── "Bu Sezon" sessiz ikame etmez (Madde 14) ────────────────────────────────
def test_sezon_bos_donunce_anilist_trendine_dusmez(fake_sources):
    """ESKİ HATA: Jikan boş dönünce sezon kipi AniList TREND listesini çekiyordu.

    AniList istemcisinde sezon ucu yok; gelen liste "bu sezon" değil "trend"di.
    """
    calls = fake_sources(season=[], anilist=[make_item("AniList Trend")])

    assert fetch_discover("season") == []
    assert calls["anilist"] == 0


def test_sezon_patlayinca_anilist_trendine_dusmez(fake_sources):
    """ESKİ HATA: Jikan istisna atınca da aynı sessiz ikame devreye giriyordu."""
    def patla():
        raise RuntimeError("Jikan 504")

    calls = fake_sources(season=patla, anilist=[make_item("AniList Trend")])

    assert fetch_discover("season") == []
    assert calls["anilist"] == 0


# ── Ana sayfanın kendine ait içeriği var (Madde 24) ─────────────────────────
def test_ana_sayfa_sezon_ucunu_da_cagirir(fake_sources):
    """ESKİ HATA: "home" ile "trending" aynı tek çağrıyı yapıyordu."""
    calls = fake_sources(trending=[make_item("T0")], season=[make_item("S0")])

    fetch_discover("home")

    assert calls["season"] == 1, "Ana sayfa vitrini sezon listesini de çekmeli"
    assert calls["trending"] == 1


def test_ana_sayfa_trendden_farkli_liste_dondurur(fake_sources):
    """ESKİ HATA: iki sekme aynı veriydi, fark yalnızca kart sayısıydı."""
    fake_sources(trending=[make_item(f"T{i}") for i in range(4)],
                 season=[make_item(f"S{i}") for i in range(4)])

    home = [anime_title(i) for i in fetch_discover("home")]
    trending = [anime_title(i) for i in fetch_discover("trending")]

    assert home != trending
    assert set(home) & {"S0", "S1"}, "Ana sayfada sezon başlıkları da olmalı"
    assert not set(trending) & set("S%d" % i for i in range(4))


def test_ana_sayfa_vitrini_donusumlu_orer(fake_sources):
    """Uç uca eklense ilk ekran yine tek kaynak olurdu; sıra S,T,S,T olmalı."""
    fake_sources(trending=[make_item(f"T{i}") for i in range(3)],
                 season=[make_item(f"S{i}") for i in range(3)])

    assert [anime_title(i) for i in fetch_discover("home")] == [
        "S0", "T0", "S1", "T1", "S2", "T2"]


def test_ana_sayfa_vitrini_yinelenen_animeyi_tek_kez_gosterir(fake_sources):
    """Aynı anime iki listede de çıkabilir; iki özdeş kart hata gibi görünür."""
    ortak = make_item("Ortak")
    fake_sources(trending=[ortak, make_item("T1")], season=[ortak])

    assert [anime_title(i) for i in fetch_discover("home")] == ["Ortak", "T1"]


def test_ana_sayfa_limiti_asmaz(fake_sources):
    fake_sources(trending=[make_item(f"T{i}") for i in range(30)],
                 season=[make_item(f"S{i}") for i in range(30)])

    assert len(fetch_discover("home", limit=5)) == 5


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


def test_sezon_sayfasi_bos_kalinca_trend_verisi_gostermez(qtbot, fake_sources):
    """ESKİ HATA: alt başlık "… sezonu • MyAnimeList" derken kartlar AniList
    TRENDİNDEN geliyor, durum etiketi de "{n} anime" diyordu."""
    fake_sources(season=[], anilist=[make_item("AniList Trend")])

    page = DiscoverPage("season")
    qtbot.addWidget(page)
    page.refresh()

    qtbot.waitUntil(lambda: page.btnRefresh.isEnabled(), timeout=5000)
    assert [c.lblTitle.text() for c in page.cards()] == []
    assert "sezonu" in page.lblSubtitle.text()
    assert "Sezon verisi alınamadı" in page.lblStatus.text()


def test_ana_sayfa_ve_trend_sayfalari_ayni_listeyi_gostermez(qtbot, fake_sources):
    """ESKİ HATA: iki sekme aynı çağrıyı yapıyor, yalnızca kart sayısı farklıydı."""
    fake_sources(trending=[make_item(f"T{i}") for i in range(3)],
                 season=[make_item(f"S{i}") for i in range(3)])

    home = DiscoverPage("home")
    trending = DiscoverPage("trending")
    qtbot.addWidget(home)
    qtbot.addWidget(trending)
    home.refresh()
    trending.refresh()

    qtbot.waitUntil(lambda: len(home.cards()) == 6, timeout=5000)
    qtbot.waitUntil(lambda: len(trending.cards()) == 3, timeout=5000)

    assert home.cards()[0].lblTitle.text() == "S0"
    assert trending.cards()[0].lblTitle.text() == "T0"


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


# ── Izgara düzeni ───────────────────────────────────────────────────────────
# Kullanıcının ekran görüntüsünde bildirdiği bozukluk: sütun genişlikleri
# tutarsız, en sağdaki sütun dar. Kök neden ÖLÇÜLDÜ (bkz. `pages/_grid.py`
# başlığı): eski ızgara sütun sayısını kendi genişliğinden hesaplıyordu, o
# genişlik de kaydırma alanı yüzünden ızgaranın kendi asgarisinin altına
# inemiyordu. Sonuç: sütun sayısı yalnızca ARTABİLİYOR, pencere daralınca
# içerik görünen alandan taşıp kırpılıyordu.


def _yerles(qtbot, widget, width, height=900):
    """Widget'ı verilen genişliğe getir ve yerleşimin oturmasını bekle."""
    widget.resize(width, height)
    qtbot.wait(10)


def _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1200, kapak=None):
    """`adet` kart yüklenmiş, `width` genişliğinde bir keşif sayfası.

    `kapak` verilirse kartlar POSTER kipinde kurulur (kullanıcının gerçekte
    gördüğü hâl); görsel indirme arka planda denenir ve ağ mandalına takılıp
    sessizce vazgeçer — kart yine de poster geometrisiyle yerleşir.
    """
    fake_sources(trending=[make_item(f"Anime {i}", cover=kapak)
                           for i in range(adet)])
    page = DiscoverPage("trending")
    qtbot.addWidget(page)
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == adet, timeout=5000)
    page.show()
    _yerles(qtbot, page, width)
    return page


def _satirlar(page):
    """Kartları y konumuna göre satırlara ayır (soldan sağa sıralı)."""
    satirlar: dict = {}
    for card in page.cards():
        satirlar.setdefault(card.y(), []).append(card)
    return [sorted(satirlar[y], key=lambda c: c.x()) for y in sorted(satirlar)]


def test_izgara_sutunlari_esit_genislikte(qtbot, fake_sources):
    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1200)

    genislikler = page.results.grid.column_widths()
    assert len(genislikler) == page.results.columns() >= 2
    # TAM eşitlik: bölünmeden artan pikseller sütunlara dağıtılmaz, sağdaki
    # yutucu sütuna bırakılır.
    assert len(set(genislikler)) == 1


def test_izgara_bosluklari_sabit(qtbot, fake_sources):
    """Fazla genişlik boşluğa değil sütunlara gider."""
    from turkanime_api.gui.qt.pages._grid import GRID_GAP

    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1200)

    ilk_satir = _satirlar(page)[0]
    assert len(ilk_satir) >= 3
    bosluklar = [sag.x() - (sol.x() + sol.width())
                 for sol, sag in zip(ilk_satir, ilk_satir[1:])]
    assert bosluklar == [GRID_GAP] * len(bosluklar)


def test_son_satir_eksik_olsa_da_kart_genislikleri_ayni(qtbot, fake_sources):
    """12 kart 5 sütuna dizilince son satırda 2 kart kalır; o ikisi de
    üsttekilerle aynı genişlikte olmalı (kullanıcı "son sütun dar" diyordu)."""
    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1200)

    satirlar = _satirlar(page)
    assert len(satirlar) >= 2
    assert len(satirlar[-1]) < len(satirlar[0])      # son satır gerçekten eksik

    genislikler = {c.width() for satir in satirlar for c in satir}
    assert len(genislikler) == 1


def test_pencere_genisleyince_sutun_sayisi_artar(qtbot, fake_sources):
    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=700)
    dar = page.results.columns()

    _yerles(qtbot, page, 1600)
    genis = page.results.columns()

    assert genis > dar >= 1


def test_kartlar_genis_pencerede_orantisiz_buyumez(qtbot, fake_sources):
    """Fazla genişlik kartları şişirmez, YENİ SÜTUNA gider.

    Kart genişliği hiçbir pencere boyutunda asgari kart genişliğinin iki
    katına çıkmamalı; çıkıyorsa ızgara yeni bir sütun açmalıydı.
    """
    from turkanime_api.gui.qt.widgets import CARD_MIN_WIDTH

    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=700)

    for width in (700, 900, 1200, 1600, 2000):
        _yerles(qtbot, page, width)
        assert page.results.columns() >= 2, width
        for card in page.cards():
            assert CARD_MIN_WIDTH <= card.width() < 2 * CARD_MIN_WIDTH, width


def test_pencere_daralinca_sutun_sayisi_azalir(qtbot, fake_sources):
    """GERİLEME TESTİ — mandal etkisi.

    Eski ızgarada sütun sayısı bir kez arttı mı bir daha inmiyordu: 1600px'te
    7 sütuna çıkan ızgara 900px'e indirildiğinde 1542px genişliğinde kalıyor,
    yatay kaydırma da kapalı olduğu için son sütun ekran kenarında
    kırpılıyordu.
    """
    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1600)
    genis = page.results.columns()

    _yerles(qtbot, page, 700)

    assert page.results.columns() < genis


def test_kartlar_gorunen_alani_asmaz(qtbot, fake_sources):
    """Hiçbir genişlikte içerik viewport'tan taşmamalı (yatay kaydırma kapalı,
    taşan sütun kırpılır)."""
    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1600)

    for width in (1600, 1200, 900, 700, 500, 1400):
        _yerles(qtbot, page, width)
        gorunen = page.results.available_width()
        assert page.results.grid.width() <= gorunen
        for card in page.cards():
            assert card.x() + card.width() <= gorunen


def test_izgara_her_genislikte_tutarli(qtbot, fake_sources):
    """Genişlik taraması: büyürken de küçülürken de eşit sütun + taşma yok.

    Tek tek genişlikleri denemek yetmiyordu; bu tarama gerçek bir hatayı
    yakaladı: sütun sayısı düşünce, yeni yutucu sütun bir önceki turdan kalma
    asgari genişliğini koruyor ve ızgarayı görünen alandan taşırıyordu
    (320px pencerede gövde 531px).
    """
    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1200)

    genislikler = list(range(360, 1801, 120)) + list(range(1800, 359, -120))
    for width in genislikler:
        _yerles(qtbot, page, width)
        gorunen = page.results.available_width()
        assert page.results.grid.width() <= gorunen, width
        assert len(set(page.results.grid.column_widths())) == 1, width
        for card in page.cards():
            assert card.x() + card.width() <= gorunen, width


def test_poster_kartlari_ayni_satirda_ayni_hizada(qtbot, fake_sources):
    """Poster kartında kart yüksekliği genişlikten türetiliyor (2:3), bu yüzden
    sütunlar arasındaki 1px fark satırı dikeyde kaydırırdı. Sütunlar tam eşit
    olduğu için aynı satırdaki kartların hepsi aynı y'de durmalı.
    """
    page = _izgara_sayfasi(qtbot, fake_sources, adet=12, width=1200,
                           kapak="http://ornek/kapak.jpg")

    for width in (1200, 1340, 900, 1610):
        _yerles(qtbot, page, width)
        satirlar = _satirlar(page)
        assert len(satirlar[0]) >= 2, width
        # `_satirlar` kartları y'ye göre gruplar: satır kayması olsaydı tek bir
        # görsel satır iki ayrı gruba bölünür ve grup sayısı artardı.
        beklenen_satir = -(-len(page.cards()) // page.results.columns())
        assert len(satirlar) == beklenen_satir, width
        assert len({c.height() for c in page.cards()}) == 1, width


def test_izgara_uste_hizali(qtbot, fake_sources):
    """Az kart varken kartlar sayfaya yayılmaz; boş yer altta toplanır."""
    page = _izgara_sayfasi(qtbot, fake_sources, adet=3, width=1200)

    ilk_satir = _satirlar(page)[0]
    assert [c.y() for c in ilk_satir] == [0] * len(ilk_satir)
    # Izgara gövdesi kaydırma alanının tepesinde durur ve yalnızca kartların
    # kapladığı yeri tutar.
    assert page.results.grid.y() == 0
    assert page.results.grid.height() < page.results.viewport().height()


def test_sutun_hesabi_hicbir_genislikte_tasmaz():
    """Sütun sayısı formülünün kendisi (Qt geometrisi olmadan)."""
    from turkanime_api.gui.qt.pages._grid import GRID_GAP, CardGridBody

    body = CardGridBody()
    onceki = 0
    for available in range(0, 2400, 7):
        cols = body.columns_for(available)
        assert cols >= 1
        assert cols >= onceki                     # genişlikle birlikte artar
        onceki = cols
        if available >= body._min_item_width:
            gereken = cols * body._min_item_width + (cols - 1) * GRID_GAP
            assert gereken <= available           # asla taşmaz
