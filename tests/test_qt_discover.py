"""Keşif sayfaları (Ana Sayfa / Trend / Bu Sezon).

Hiçbir test ağa çıkmaz: Jikan ve AniList uçları `monkeypatch` ile sahtelenir.
Veri katmanı `web.uclar_kesif`'te (Qt'siz), sayfalar `kesif.js`'te; sayfa
davranışı ve ızgara düzeni web sürücüsüyle sınanıyor.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from turkanime_api.gui.web.kunye import anime_title, cover_url, score_of
from turkanime_api.gui.web.uclar_kesif import KesifUclari, fetch_discover, season_label

# CSS'teki `minmax(162px, 1fr)` ve sütun aralığı (bkz. `bilesenler.css` .izgara).
KART_ASGARI = 162
SUTUN_ARALIGI = 18


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


# ── Keşif ucu ───────────────────────────────────────────────────────────────
def kesif(mod):
    return KesifUclari().kesif(mod)


def basliklar(sonuc):
    return [k["baslik"] for k in sonuc["kartlar"]]


def test_uc_yedek_veriyi_gosteriyor(fake_sources):
    """Jikan boş dönerse kullanıcı boş ekran değil AniList verisi görmeli."""
    fake_sources(trending=[], anilist=[make_item("AniList Anime")])
    assert basliklar(kesif("trending")) == ["AniList Anime"]


def test_sezon_sayfasi_bos_kalinca_trend_verisi_gostermez(fake_sources):
    """ESKİ HATA: alt başlık "… sezonu • MyAnimeList" derken kartlar AniList
    TRENDİNDEN geliyor, durum etiketi de "{n} anime" diyordu."""
    fake_sources(season=[], anilist=[make_item("AniList Trend")])

    sonuc = kesif("season")

    assert sonuc["kartlar"] == []
    assert "sezonu" in sonuc["altbaslik"]
    assert "Sezon verisi alınamadı" in sonuc["bos_mesaj"]


def test_ana_sayfa_ve_trend_ayni_listeyi_gostermez(fake_sources):
    """ESKİ HATA: iki sekme aynı çağrıyı yapıyor, yalnızca kart sayısı farklıydı."""
    fake_sources(trending=[make_item(f"T{i}") for i in range(3)],
                 season=[make_item(f"S{i}") for i in range(3)])

    ana, trend = basliklar(kesif("home")), basliklar(kesif("trending"))

    assert len(ana) == 6 and ana[0] == "S0"
    assert trend == ["T0", "T1", "T2"]


def test_bos_sonuc_sebebiyle(fake_sources):
    fake_sources(trending=[], anilist=[])
    sonuc = kesif("home")
    assert sonuc["kartlar"] == [] and "alınamadı" in sonuc["bos_mesaj"]


def test_bilinmeyen_kip_reddediliyor():
    with pytest.raises(ValueError):
        kesif("yok")


# ── Sayfa davranışı ─────────────────────────────────────────────────────────
def kartlar_js(mod):
    return f"document.querySelectorAll('[data-sayfa={mod}] .izgara .kart:not(.iskelet-kart)')"


def yenile_js(mod):
    return (f"[...document.querySelectorAll('[data-sayfa={mod}] .sayfa-eylem button')]"
            ".find(d => d.textContent.includes('Yenile'))")


@pytest.mark.parametrize("mode", ["trending", "season"])
def test_izgara_sayfasi_kartlari_kuruyor(main_window, web, fake_sources, mode):
    items = [make_item(f"Anime {i}") for i in range(5)]
    fake_sources(trending=items, season=items)

    main_window.show_page(mode)

    web.bekle(f"{kartlar_js(mode)}.length === 5")
    assert web.js(f"{kartlar_js(mode)}[0].querySelector('.kart-baslik').textContent") \
        == "Anime 0"
    assert web.js(f"document.querySelector('[data-sayfa={mode}] .durum').textContent") \
        == "5 anime"


def test_sezon_bos_kalinca_sebep_ve_yenile(main_window, web, fake_sources):
    fake_sources(season=[], anilist=[make_item("AniList Trend")])
    main_window.show_page("season")
    bos = "document.querySelector('[data-sayfa=season] .kesif-bos')"
    web.bekle(f"{bos}.innerText.includes('Sezon verisi alınamadı')")
    assert web.js(f"{kartlar_js('season')}.length") == 0
    assert "sezonu" in web.js("document.querySelector('[data-sayfa=season] "
                              ".sayfa-baslik p').textContent")
    assert web.js(f"{yenile_js('season')}.disabled") is False


def test_sayfa_ilk_gosterimde_bir_kez_yukleniyor(main_window, web, fake_sources):
    """Sekmeye her dönüşte yeniden ağ isteği atılmamalı."""
    calls = fake_sources(trending=[make_item("Anime")])
    main_window.show_page("trending")
    web.bekle(f"{kartlar_js('trending')}.length === 1")
    once = calls["trending"]

    main_window.show_page("season")
    web.bekle("TA.aktif === 'season'")
    main_window.show_page("trending")
    web.bekle("TA.aktif === 'trending'")
    web.qtbot.wait(100)

    assert calls["trending"] == once
    assert web.js(f"{kartlar_js('trending')}.length") == 1


def test_yenile_dugmesi_yeniden_yukluyor(main_window, web, fake_sources):
    calls = fake_sources(trending=[make_item("Anime")])
    main_window.show_page("trending")
    web.bekle(f"{kartlar_js('trending')}.length === 1")
    once = calls["trending"]

    web.js(f"{yenile_js('trending')}.click()")

    web.qtbot.waitUntil(lambda: calls["trending"] == once + 1, timeout=5000)
    web.bekle(f"{kartlar_js('trending')}.length === 1")


def test_puan_rozeti_puan_rengiyle(main_window, web, fake_sources):
    fake_sources(trending=[make_item("İyi", 90), make_item("Kötü", 20),
                           make_item("Puansız", None)])
    main_window.show_page("trending")
    web.bekle(f"{kartlar_js('trending')}.length === 3")

    puanlar = web.js(f"[...{kartlar_js('trending')}].map(k => {{"
                     "var p = k.querySelector('.kart-puan');"
                     "return p ? [p.textContent, p.style.getPropertyValue('--renk')] : null; })")
    iyi, kotu, puansiz = puanlar
    assert iyi == ["9.0", "var(--yesil)"]
    assert kotu == ["2.0", "var(--kirmizi)"]
    assert puansiz is None


def test_card_click_opens_detail_page(main_window, web, fake_sources):
    """Kart tıklaması detay sayfasını açar ve TÜM kaydı taşır.

    Faz 4 öncesi yalnızca başlık taşınıp aramaya köprüleniyordu; artık özet ve
    türlerin yeniden çekilmesine gerek kalmasın diye sözlüğün tamamı gider
    (web kartı kaydı köprüden geri yolluyor).
    """
    fake_sources(trending=[make_item("Cowboy Bebop")])

    main_window.show_page("trending")
    kartlar = "document.querySelectorAll('[data-sayfa=trending] .izgara .kart')"
    web.bekle(f"{kartlar}.length === 1")
    web.js(f"{kartlar}[0].click()")

    web.bekle("TA.aktif === 'detail' && !!document.querySelector('.detay-bilgi h1')")
    assert main_window._current_page == "detail"
    assert web.js("document.querySelector('.detay-bilgi h1').textContent") == "Cowboy Bebop"
    turler = web.js("Array.from(document.querySelectorAll('.etiket-blok .hap')).map(e => e.textContent)")
    assert turler == ["Aksiyon"]
    assert main_window.detay.oturum.anime["genres"] == ["Action"]


def test_main_window_wires_all_discover_modes(main_window, web):
    """Üç keşif kipi de tek web görünümünde birer rota."""
    for key in ("home", "trending", "season"):
        assert main_window.pages[key] is main_window.web
        main_window.show_page(key)
        web.bekle(f"TA.aktif === {key!r}")


# ── Izgara düzeni ───────────────────────────────────────────────────────────
# Eski Qt ızgarasında kullanıcının bildirdiği bozukluk: sütun genişlikleri
# tutarsız, en sağdaki sütun dar, pencere daralınca içerik kırpılıyordu.
# Web'de ızgara CSS grid (`repeat(auto-fill, minmax(162px, 1fr))`); aynı
# sözleşme tarayıcının gerçek yerleşimiyle sınanıyor.
OLCUM = ("(() => { var iz = document.querySelector('[data-sayfa=trending] .izgara');"
         " var r = iz.getBoundingClientRect();"
         " return {izgara: [r.left, r.right, r.top],"
         "  sayfa: document.documentElement.clientWidth,"
         "  tasma: document.documentElement.scrollWidth - document.documentElement.clientWidth,"
         "  kartlar: [...iz.querySelectorAll('.kart')].map(k => {"
         "   var b = k.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; })}; })()")


def olc(web, main_window, genislik):
    main_window.resize(genislik, 900)
    onceki = None
    for _ in range(60):                   # yerleşim oturana kadar
        web.qtbot.wait(25)
        olcum = web.js(OLCUM)
        if olcum == onceki:
            return olcum
        onceki = olcum
    return onceki


def satirlar(olcum):
    rows: dict = {}
    for sol, ust, gen, yuk in olcum["kartlar"]:
        rows.setdefault(round(ust), []).append((sol, gen, yuk))
    return [sorted(rows[y]) for y in sorted(rows)]


@pytest.fixture
def izgara(main_window, web, fake_sources):
    """12 kapaklı kart yüklü Trend sayfası."""
    fake_sources(trending=[make_item(f"Anime {i}", cover="http://ornek/k.jpg")
                           for i in range(12)])
    main_window.show_page("trending")
    web.bekle(f"{kartlar_js('trending')}.length === 12")
    return web


def test_izgara_her_genislikte_tutarli(izgara, main_window):
    """Genişlik taraması: eşit sütun, sabit aralık, taşma yok; fazla genişlik
    kartları şişirmez yeni sütuna gider, daralınca sütun sayısı azalır."""
    sutunlar = {}
    for genislik in (1600, 1200, 1024, 900, 1400, 2000, 900):
        olcum = olc(izgara, main_window, genislik)
        rows = satirlar(olcum)
        ilk = rows[0]
        sutunlar[genislik] = len(ilk)
        assert len(ilk) >= 2, genislik
        genislikler = {round(g, 1) for satir in rows for _, g, _ in satir}
        assert len(genislikler) == 1, (genislik, genislikler)   # son satır da
        kart = genislikler.pop()
        assert KART_ASGARI <= kart < 2 * KART_ASGARI, (genislik, kart)
        bosluklar = {round(sag[0] - (sol[0] + sol[1])) for sol, sag in zip(ilk, ilk[1:])}
        assert bosluklar == {SUTUN_ARALIGI}, (genislik, bosluklar)
        # Aynı satırda aynı yükseklik (poster 2:3); satır kayması yok.
        assert len(rows) == -(-12 // len(ilk)), genislik
        assert len({round(y) for satir in rows for _, _, y in satir}) == 1, genislik
        sag_kenar = max(sol + gen for satir in rows for sol, gen, _ in satir)
        assert sag_kenar <= olcum["izgara"][1] + 0.5, genislik
        assert olcum["tasma"] <= 0, f"{genislik}px'te yatay taşma"
    assert sutunlar[2000] > sutunlar[1600] > sutunlar[1200] > sutunlar[900]


def test_izgara_uste_hizali(main_window, web, fake_sources):
    """Az kart varken kartlar sayfaya yayılmaz; ilk satır ızgaranın tepesinde."""
    fake_sources(trending=[make_item(f"Anime {i}") for i in range(3)])
    main_window.show_page("trending")
    web.bekle(f"{kartlar_js('trending')}.length === 3")
    olcum = olc(web, main_window, 1200)
    tepe = olcum["izgara"][2]
    assert [round(k[1] - tepe) for k in olcum["kartlar"]] == [0, 0, 0]
    assert len({round(k[2], 1) for k in olcum["kartlar"]}) == 1
