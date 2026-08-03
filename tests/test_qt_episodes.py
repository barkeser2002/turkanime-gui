"""Qt bölüm listesi + çok kaynaklı yükleme (Faz 5).

Hiçbir test ağa çıkmaz: `fetch_episodes` ve `SearchEngine` monkeypatch ile
sahtelenir. Testlerin ana derdi üç davranış:

1. Çok kaynak tek listede birleşiyor ve satırda kaynak düğmeleri çıkıyor.
2. Mevcut Qt davranışları (sayfalama, filtre, filtreli toplu seçim) bozulmadı.
3. Bir kaynak patladığında diğerleri geliyor ve durum kullanıcıya söyleniyor.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from turkanime_api.gui.qt.pages import detail as detail_mod
from turkanime_api.gui.qt.pages.detail import DetailPage
from turkanime_api.gui.qt.pages.episodes import (
    EpisodePage, SourceSelectDialog, active_sources, as_sources_data,
    episode_matches, source_counts, source_short,
)
from turkanime_api.gui.qt.sources_bridge import UnsupportedSource


def ep(title: str):
    return {"title": title, "obj": object()}


def numbered(count: int, template: str = "{}. Bölüm"):
    return [ep(template.format(i)) for i in range(1, count + 1)]


@pytest.fixture
def page(qtbot):
    widget = EpisodePage()
    qtbot.addWidget(widget)
    return widget


# ── Saf yardımcılar ─────────────────────────────────────────────────────────
def test_as_sources_data_iki_sekli_de_kabul_ediyor():
    """Tek kaynak düz liste, çok kaynak sözlük gönderiyor."""
    entry = ep("1. Bölüm")
    assert as_sources_data("TürkAnime", [entry]) == {"TürkAnime": [entry]}

    data = as_sources_data("x", {"A": [entry], "B": []})
    assert set(data) == {"A", "B"}
    assert data["B"] == []
    assert as_sources_data("TürkAnime", None) == {"TürkAnime": []}
    # Bozuk kayıtlar listeye girmemeli (kaynaklar bazen None döndürüyor)
    assert as_sources_data("TürkAnime", [entry, None, "x"]) == {"TürkAnime": [entry]}


def test_active_sources_bos_kaynaklari_saymiyor():
    assert active_sources({"A": [ep("1")], "B": [], "C": None}) == ["A"]


def test_source_short_bilinmeyen_kaynakta_ilk_iki_harf():
    assert source_short("TürkAnime") == "TA"
    assert source_short("AnimeciX") == "CX"
    assert source_short("YeniKaynak") == "YE"


def test_episode_matches_numara_ve_baslik():
    episode = {"title": "12. Bölüm", "number": 12}
    assert episode_matches(episode, "12")
    assert episode_matches(episode, "bölüm")
    assert not episode_matches(episode, "13")
    assert episode_matches(episode, "")


def test_source_counts():
    episodes = [
        {"sources": {"A": ep("1"), "B": ep("1")}},
        {"sources": {"A": ep("2")}},
    ]
    assert source_counts(episodes) == {"A": 2, "B": 1}


# ── Tek kaynak: sade görünüm ────────────────────────────────────────────────
def test_tek_kaynakta_sade_satir(page):
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(2))

    assert len(page.visible_rows()) == 2
    row = page._rows[0]
    assert row.btnPlay is not None and row.btnPlay.text() == "Oynat"
    assert row.btnDl is not None and row.btnDl.text() == "İndir"
    assert list(row.source_buttons) == ["TürkAnime"]
    assert page.lblTitle.text() == "Cowboy Bebop — TürkAnime"
    assert not page.lblSources.isVisible()


def test_tek_kaynak_oynat_ham_kaydi_yayiyor(qtbot, page):
    entries = numbered(1)
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=entries)

    with qtbot.waitSignal(page.play_requested, timeout=1000) as blocker:
        qtbot.mouseClick(page._rows[0].btnPlay, Qt.MouseButton.LeftButton)
    assert blocker.args[0] is entries[0]


# ── Çok kaynak: birleşik satır ──────────────────────────────────────────────
def test_cok_kaynak_tek_satirda_birlesiyor(page):
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes={
        "TürkAnime": [ep("Cowboy Bebop 1. Bölüm"), ep("Cowboy Bebop 2. Bölüm")],
        "AnimeciX": [ep("1. Bölüm"), ep("2. Bölüm"), ep("3. Bölüm")],
        "Anizle": [ep("Bölüm 1")],
    })

    assert len(page.visible_rows()) == 3
    assert set(page._rows[0].source_buttons) == {"TürkAnime", "AnimeciX", "Anizle"}
    # 2. bölüm Anizle'de yok: rozet de olmamalı
    assert set(page._rows[1].source_buttons) == {"TürkAnime", "AnimeciX"}
    # Tek kaynakta bulunan bölüm de rozet göstermeli (hangi kaynak belli olsun)
    assert set(page._rows[2].source_buttons) == {"AnimeciX"}
    assert page._rows[2].btnPlay is None
    assert page.lblTitle.text() == "Cowboy Bebop — 3 kaynak"
    assert "Anizle" in page.lblSources.text()
    assert "3 kaynak" in page.lblStatus.text()


def test_satir_dugmesi_kendi_kaynagini_yayiyor(qtbot, page):
    animecix = ep("1. Bölüm")
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes={
        "TürkAnime": [ep("Cowboy Bebop 1. Bölüm")],
        "AnimeciX": [animecix],
    })
    play, download = page._rows[0].source_buttons["AnimeciX"]

    with qtbot.waitSignal(page.play_requested, timeout=1000) as blocker:
        qtbot.mouseClick(play, Qt.MouseButton.LeftButton)
    assert blocker.args[0] is animecix

    with qtbot.waitSignal(page.download_requested, timeout=1000) as blocker:
        qtbot.mouseClick(download, Qt.MouseButton.LeftButton)
    assert blocker.args[0] is animecix


def test_bos_kaynak_liste_basligini_kirletmiyor(page):
    """Yüklenemeyen kaynak rozet üretmemeli."""
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes={
        "TürkAnime": numbered(1), "Anizle": [],
    })
    assert list(page._rows[0].source_buttons) == ["TürkAnime"]
    assert page.lblTitle.text() == "Cowboy Bebop — TürkAnime"


# ── Mevcut davranışlar: sayfalama / filtre / seçim ──────────────────────────
def test_sayfalama_30ar_yukluyor(page):
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(70))

    assert len(page._rows) == 30
    assert page.btnMore.isVisibleTo(page)
    page._load_more()
    assert len(page._rows) == 60
    page._load_more()
    assert len(page._rows) == 70
    assert not page.btnMore.isVisibleTo(page)


def test_filtre_satirlari_isaretliyor(page):
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(12))
    page.txtFilter.setText("1")

    # 1, 10, 11, 12
    assert len(page.visible_rows()) == 4
    assert len(page.filtered_episodes()) == 4
    page.txtFilter.setText("")
    assert len(page.visible_rows()) == 12


def test_tumunu_sec_yalnizca_filtrelenenleri_aliyor(page):
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(12))
    page.txtFilter.setText("3")
    page.btnAll.setChecked(True)

    assert len(page.selected_episodes()) == 1
    assert page.selected_episodes()[0]["number"] == 3
    assert page.btnAll.text() == "Seçimi Kaldır"


def test_filtre_secimi_daraltiyor(page):
    """Filtresiz seçilenler, filtre uygulanınca indirmeye gitmemeli."""
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(12))
    page.btnAll.setChecked(True)
    assert len(page.selected_entries()) == 12

    page.txtFilter.setText("3")
    assert len(page.selected_entries()) == 1


def test_secim_yuklenmemis_sayfalari_da_kapsiyor(page):
    """Eski GUI `get_selected_episodes` davranışı: seçim satıra bağlı değil."""
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(70))
    page.btnAll.setChecked(True)

    assert len(page._rows) == 30
    assert len(page.selected_episodes()) == 70
    assert len(page.selected_entries()) == 70

    page._load_more()
    assert page._rows[45].checked, "sonradan yüklenen satır seçimi göstermiyor"


def test_yeni_anime_secimi_sifirliyor(page):
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(5))
    page.btnAll.setChecked(True)
    assert len(page.selected_episodes()) == 5

    page.load("TürkAnime", "trigun", "Trigun", episodes=numbered(3))
    assert page.selected_episodes() == []
    assert page.btnAll.text() == "Tümünü Seç"


def test_secilen_kaynaktan_kayit_donuyor(page):
    turkanime, animecix = ep("Cowboy Bebop 1. Bölüm"), ep("1. Bölüm")
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes={
        "TürkAnime": [turkanime], "AnimeciX": [animecix],
    })
    page.btnAll.setChecked(True)

    assert page.selected_entries("AnimeciX") == [animecix]
    assert page.selected_entries() == [turkanime]      # varsayılan: ilk kaynak
    assert page.available_sources() == ["AnimeciX", "TürkAnime"]


# ── Kaynak seçim diyaloğu ───────────────────────────────────────────────────
def test_tek_kaynakta_dialog_sorulmuyor(qtbot, page, monkeypatch):
    asked = []
    monkeypatch.setattr(page, "_ask_source",
                        lambda counts, total: asked.append(counts))

    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(3))
    page.btnAll.setChecked(True)

    queued = []
    page.download_requested.connect(queued.append)
    page._download_selected()

    assert asked == [], "tek kaynakta kullanıcıya sorulmamalı"
    assert len(queued) == 3


def test_cok_kaynakta_dialog_soruluyor(page, monkeypatch):
    asked = []

    def fake_ask(counts, total):
        asked.append((dict(counts), total))
        return "AnimeciX"

    monkeypatch.setattr(page, "_ask_source", fake_ask)
    animecix = numbered(2, "Bölüm {}")
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes={
        "TürkAnime": numbered(2), "AnimeciX": animecix,
    })
    page.btnAll.setChecked(True)

    queued = []
    page.download_requested.connect(queued.append)
    page._download_selected()

    assert asked == [({"TürkAnime": 2, "AnimeciX": 2}, 2)]
    assert queued == animecix, "seçilen kaynağın kayıtları kuyruğa girmedi"


def test_iptal_edilen_dialog_indirmiyor(page, monkeypatch):
    monkeypatch.setattr(page, "_ask_source", lambda counts, total: None)
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes={
        "TürkAnime": numbered(2), "AnimeciX": numbered(2, "Bölüm {}"),
    })
    page.btnAll.setChecked(True)

    queued = []
    page.download_requested.connect(queued.append)
    page._download_selected()

    assert queued == []
    assert "iptal" in page.lblStatus.text().lower()


def test_secilen_kaynakta_olmayan_bolum_atlaniyor(page, monkeypatch):
    monkeypatch.setattr(page, "_ask_source", lambda counts, total: "Anizle")
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes={
        "TürkAnime": numbered(3),
        "Anizle": [ep("Bölüm 1")],
    })
    page.btnAll.setChecked(True)

    queued = []
    page.download_requested.connect(queued.append)
    page._download_selected()

    assert len(queued) == 1
    assert "2 bölüm bu kaynakta yok" in page.lblStatus.text()


def test_dialog_secimi_donduruyor(qtbot):
    dialog = SourceSelectDialog({"TürkAnime": 12, "AnimeciX": 3}, 12)
    qtbot.addWidget(dialog)

    assert set(dialog.buttons) == {"TürkAnime", "AnimeciX"}
    assert "3/12" in dialog.buttons["AnimeciX"].text()
    dialog.buttons["AnimeciX"].click()

    assert dialog.selection == "AnimeciX"
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_secim_yokken_uyari(page):
    page.load("TürkAnime", "cb", "Cowboy Bebop", episodes=numbered(3))
    page._download_selected()
    assert "en az bir bölüm" in page.lblStatus.text()


# ── Detay sayfası: çok kaynaklı yükleme ─────────────────────────────────────
@pytest.fixture
def detail(qtbot):
    widget = DetailPage()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def fake_engine(monkeypatch):
    """`SearchEngine.search_all_sources_rich`'i sahtele; çağrıları kaydet."""
    calls: list = []

    def _install(results):
        import turkanime_api.common.adapters as adapters_mod

        class FakeEngine:
            def search_all_sources_rich(self, query, limit_per_source=10):
                calls.append((query, limit_per_source))
                return results

        monkeypatch.setattr(adapters_mod, "SearchEngine", FakeEngine)
        return calls

    return _install


@pytest.fixture
def fake_fetch(monkeypatch):
    """Kaynak başına sonuç/hata tanımlayan sahte `fetch_episodes`."""
    calls: list = []

    def _install(table):
        def fake(source, slug, title):
            calls.append((source, slug))
            result = table.get(source)
            if isinstance(result, Exception):
                raise result
            return list(result or [])

        monkeypatch.setattr(detail_mod, "fetch_episodes", fake)
        return calls

    return _install


ALL_MATCHES = {
    "TürkAnime": [{"slug": "cowboy-bebop", "title": "Cowboy Bebop"}],
    "AnimeciX": [{"slug": "17", "title": "Cowboy Bebop"}],
    "Anizle": [{"slug": "cowboy-bebop-anizle", "title": "Cowboy Bebop"}],
    "AniList": [{"slug": "1", "title": "Cowboy Bebop"}],
}


def test_cok_kaynak_yuklemesi_kismi_hatayi_raporluyor(qtbot, detail, fake_engine,
                                                      fake_fetch):
    """Biri patlasa da diğerleri gelmeli; kaç kaynağın yüklendiği yazmalı."""
    fake_engine(ALL_MATCHES)
    fake_fetch({
        "TürkAnime": numbered(2),
        "AnimeciX": numbered(2, "Bölüm {}"),
        "Anizle": UnsupportedSource("Anizle şu an kullanılamıyor"),
    })
    detail.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    detail.chkAllSources.setChecked(True)

    with qtbot.waitSignal(detail.episodes_ready, timeout=5000) as blocker:
        detail.load_episodes()

    source, slug, title, payload = blocker.args
    assert (source, slug, title) == ("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    assert isinstance(payload, dict)
    # AniList metadata kaynağı: otomatik eşleştirmeye girmemeli
    assert set(payload) == {"TürkAnime", "AnimeciX", "Anizle"}
    assert payload["Anizle"] == []
    assert "3 kaynaktan 2" in detail.lblStatus.text()
    assert "Anizle" in detail.lblStatus.text()
    assert detail.btnEpisodes.isEnabled()


def test_cok_kaynak_yuklemesi_dogru_slug_kullaniyor(qtbot, detail, fake_engine,
                                                    fake_fetch):
    """Her kaynak KENDİ slug'ıyla çekilmeli (slug'lar kaynağa özgü)."""
    fake_engine(ALL_MATCHES)
    calls = fake_fetch({"TürkAnime": numbered(1), "AnimeciX": numbered(1),
                        "Anizle": numbered(1)})
    detail.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    detail.chkAllSources.setChecked(True)

    with qtbot.waitSignal(detail.episodes_ready, timeout=5000):
        detail.load_episodes()

    assert dict(calls) == {
        "TürkAnime": "cowboy-bebop",
        "AnimeciX": "17",
        "Anizle": "cowboy-bebop-anizle",
    }


def test_tum_kaynaklar_patlarsa_hata_gosteriliyor(qtbot, detail, fake_engine,
                                                  fake_fetch):
    fake_engine(ALL_MATCHES)
    fake_fetch({
        "TürkAnime": UnsupportedSource("TürkAnime kapalı"),
        "AnimeciX": UnsupportedSource("AnimeciX kapalı"),
        "Anizle": UnsupportedSource("Anizle kapalı"),
    })
    detail.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    detail.chkAllSources.setChecked(True)

    emitted: list = []
    detail.episodes_ready.connect(lambda *a: emitted.append(a))
    detail.load_episodes()

    qtbot.waitUntil(lambda: "kapalı" in detail.lblStatus.text(), timeout=5000)
    assert emitted == []
    assert detail.btnEpisodes.isEnabled()


def test_tek_kaynak_varsayilani_arama_yapmiyor(qtbot, detail, fake_engine,
                                               fake_fetch):
    """Kutu işaretsizken tek kaynak, tek istek — fazladan ağ trafiği yok."""
    searches = fake_engine(ALL_MATCHES)
    calls = fake_fetch({"TürkAnime": numbered(2)})
    detail.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")

    with qtbot.waitSignal(detail.episodes_ready, timeout=5000) as blocker:
        detail.load_episodes()

    assert searches == []
    assert calls == [("TürkAnime", "cowboy-bebop")]
    assert "2 bölüm bulundu" in detail.lblStatus.text()
    assert set(blocker.args[3]) == {"TürkAnime"}


def test_baglanmamis_kaynak_secilince_eslesme_araniyor(qtbot, detail, fake_engine,
                                                       fake_fetch):
    """Kombo başka kaynağa çevrildiğinde ESKİ slug ile istek atılmamalı."""
    searches = fake_engine(ALL_MATCHES)
    calls = fake_fetch({"AnimeciX": numbered(3)})
    detail.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    detail.cmbSource.setCurrentText("AnimeciX")

    with qtbot.waitSignal(detail.episodes_ready, timeout=5000) as blocker:
        detail.load_episodes()

    assert searches == [("Cowboy Bebop", detail_mod.AUTO_MATCH_LIMIT)]
    assert calls == [("AnimeciX", "17")], "yanlış kaynağın slug'ı kullanıldı"
    assert set(blocker.args[3]) == {"AnimeciX"}


def test_hicbir_kaynakta_eslesme_yoksa_hata(qtbot, detail, fake_engine, fake_fetch):
    fake_engine({"TürkAnime": [], "AnimeciX": []})
    fake_fetch({})
    detail.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    detail.cmbSource.setCurrentText("Anizle")

    detail.load_episodes()
    # "Kaynaklarda eşleşme aranıyor…" ara durumu; SON mesajı bekliyoruz.
    qtbot.waitUntil(lambda: "bulunamadı" in detail.lblStatus.text(), timeout=5000)
    assert "kaynak eşleşmesi" in detail.lblStatus.text()
    assert detail.btnEpisodes.isEnabled()


def test_eski_sonuc_yeni_animeyi_ezmiyor(detail):
    """Çok kaynaklı toplama da yarış korumasına tabi."""
    detail.show_match("TürkAnime", "a", "Anime A")
    stale = detail.request_id
    detail._dispatch(stale, {"TürkAnime": "a"})
    detail.show_match("TürkAnime", "b", "Anime B")

    emitted: list = []
    detail.episodes_ready.connect(lambda *a: emitted.append(a))
    detail._on_source_loaded((stale, "TürkAnime", numbered(2), ""))

    assert emitted == []
    assert detail.lblTitle.text() == "Anime B"


# ── Ana pencere kablolaması ─────────────────────────────────────────────────
def test_ana_pencere_cok_kaynakli_yuku_devraliyor(main_window):
    episodes = main_window.pages["episodes"]
    main_window.pages["detail"].episodes_ready.emit(
        "TürkAnime", "cowboy-bebop", "Cowboy Bebop", {
            "TürkAnime": [ep("Cowboy Bebop 1. Bölüm")],
            "AnimeciX": [ep("1. Bölüm")],
        })

    assert main_window.stack.currentWidget() is episodes
    assert len(episodes.visible_rows()) == 1
    assert set(episodes._rows[0].source_buttons) == {"TürkAnime", "AnimeciX"}
    assert episodes.lblTitle.text() == "Cowboy Bebop — 2 kaynak"
