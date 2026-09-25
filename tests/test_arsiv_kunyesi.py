"""Arşiv künyesi (`info.json`) → detay sayfası; kapak yer tutucusu ve taşınması.

Ağa çıkılmaz: arşiv `tmp_path`'te kurulur ve "depodaki arşiv" sayılır
(`DEPO_ARSIVI`); uzak aynalar conftest'te zaten kesik (`_session` patlar).
Kapak indirmesi `gorsel_getir` sahtesiyle sayılır.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from turkanime_api.gui.qt.pages import detail as detail_mod
from turkanime_api.gui.qt.pages.detail import DetailPage, kunye_birlestir, meta_line
from turkanime_api.sources import animedepo

OZET = ("Bir köle olan Teito Klein, Zaiphon adlı bir güce sahiptir. <br /><br />"
        "Ellerinden kurtulan Teito &amp; papazlar…")

GHOST = {
    "Kategori": "TV",
    "Japonca": "セブンゴースト",
    "Anime Türü": ["Fantastik", "Aksiyon"],
    "Bölüm Sayısı": "25 / 25",
    "Başlama Tarihi": "07 Nisan 2009, Salı",
    "Bitiş Tarihi": "22 Eylül 2009, Salı",
    "Stüdyo": "Studio Deen",
    "Puanı": 7.57,
    "Özet": OZET,
    "Resim": "http://www.turkanime.co/imajlar/serilerb/1.jpg",
}


def arsiv_kur(kok: Path, animeler: dict) -> Path:
    """slug → (başlık, info.json içeriği ya da None) şeklinde küçük arşiv."""
    index: dict = {}
    for slug, (baslik, bilgi) in animeler.items():
        index.setdefault(slug[0].upper(), {})[slug] = {"title": baslik}
        klasor = kok / "animeler" / slug
        klasor.mkdir(parents=True, exist_ok=True)
        (klasor / "bolumler.json").write_text(
            json.dumps([["1-bolum", "1. Bölüm"]]), "utf-8")
        if bilgi is not None:
            (klasor / "info.json").write_text(
                json.dumps(bilgi, ensure_ascii=False), "utf-8")
    (kok / "dizin.json").write_text(
        json.dumps({"last_update": 1, "index": index}), "utf-8")
    return kok


@pytest.fixture
def arsiv(tmp_path, monkeypatch):
    kok = arsiv_kur(tmp_path / "arsiv", {
        "07-ghost": ("07-Ghost", GHOST),
        "bilgisiz": ("Bilgisiz Anime", None),
        "puansiz": ("Puansız", {"Puanı": 0, "Özet": "Puansız özet",
                                "Bölüm Sayısı": "12 / ?",
                                "Stüdyo": "A Studio, B Studio"}),
        "bos-puan": ("Boş Puan", {"Puanı": "", "Özet": "x"}),
        "null-puan": ("Null Puan", {"Puanı": None, "Özet": "y"}),
        "ikinci": ("İkinci", {"Özet": "İkinci animenin özeti",
                              "Anime Türü": ["Dram"]}),
    })
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", kok)
    animedepo.sifirla()
    yield kok
    animedepo.sifirla()


@pytest.fixture
def kapak_istekleri(monkeypatch):
    """Detay sayfasının kapak indirmesini say (ağa çıkmaz)."""
    istekler: list = []

    def _getir(url):
        istekler.append(url)
        return None

    monkeypatch.setattr(detail_mod, "gorsel_getir", _getir)
    return istekler


@pytest.fixture
def page(qtbot):
    widget = DetailPage()
    qtbot.addWidget(widget)
    return widget


# ── animedepo.anime_bilgisi ─────────────────────────────────────────────────
def test_anime_bilgisi_anilist_bicimine_ceviriyor(arsiv):
    bilgi = animedepo.anime_bilgisi("07-ghost")

    assert bilgi["genres"] == ["Fantastik", "Aksiyon"]
    assert bilgi["studios"] == ["Studio Deen"]
    assert bilgi["averageScore"] == 76
    assert bilgi["episodes"] == 25
    assert bilgi["format"] == "TV"
    assert "Teito" in bilgi["description"]
    assert bilgi["startDate"] == {"year": 2009, "month": 4, "day": 7}
    assert bilgi["endDate"] == {"year": 2009, "month": 9, "day": 22}
    assert bilgi["title"] == {"native": "セブンゴースト"}
    # Ölü siteye giden "Resim" hiçbir biçimde taşınmıyor.
    assert "coverImage" not in bilgi
    assert "turkanime" not in json.dumps(bilgi)


def test_anime_bilgisi_eksik_ve_bos_alanlar(arsiv):
    assert animedepo.anime_bilgisi("bilgisiz") == {}
    assert animedepo.anime_bilgisi("olmayan-anime") == {}
    assert animedepo.anime_bilgisi("../dizin") == {}

    puansiz = animedepo.anime_bilgisi("puansiz")
    assert "averageScore" not in puansiz
    assert puansiz["episodes"] == 12            # "12 / ?" → yayınlanan
    assert puansiz["studios"] == ["A Studio", "B Studio"]
    for slug in ("bos-puan", "null-puan"):
        assert "averageScore" not in animedepo.anime_bilgisi(slug)


@pytest.mark.parametrize("ham, beklenen", [
    ("25 / 25", 25), ("12 / ?", 12), ("12 / 24+", 12), ("? / 13", 13),
    ("", None), (None, None), ("0 / 0", None),
])
def test_bolum_sayisi(ham, beklenen):
    assert animedepo._bolum_sayisi(ham) == beklenen


def test_anime_bilgisi_arsiv_okunamazsa_hata(monkeypatch, tmp_path):
    """Yerel arşiv yok, aynalar kapalı: "yok" değil "okunamadı" (sessiz değil)."""
    with pytest.raises(animedepo.ArsivOkunamadi):
        animedepo.anime_bilgisi("07-ghost")


# ── Qt'siz yardımcılar ──────────────────────────────────────────────────────
def test_kunye_birlestir_yalnizca_bos_alanlari_dolduruyor():
    anime = {"title": {"romaji": "07-Ghost"}, "genres": ["Action"],
             "description": ""}
    birlesik = kunye_birlestir(anime, {"genres": ["Fantastik"],
                                       "description": "Özet",
                                       "title": {"native": "セブン", "romaji": "X"}})
    assert birlesik["genres"] == ["Action"], "AniList türleri ezildi"
    assert birlesik["description"] == "Özet"
    assert birlesik["title"] == {"romaji": "07-Ghost", "native": "セブン"}
    assert anime["description"] == "", "girdi yerinde değiştirildi"


def test_meta_satiri_tarih_ve_bicim():
    satir = meta_line({"episodes": 25, "format": "TV",
                       "startDate": {"year": 2009, "month": 4, "day": 7},
                       "endDate": {"year": 2009, "month": 9, "day": 22}})
    assert satir == "25 bölüm • TV • 7 Nisan 2009 – 22 Eylül 2009"
    # Film: başlangıç = bitiş, tek tarih.
    film = {"year": 2010, "month": 1, "day": 2}
    assert meta_line({"format": "MOVIE", "startDate": film, "endDate": film}) \
        == "Film • 2 Ocak 2010"
    # Sezonu bilinen kayıtta tarih YAZILMAZ (yıl iki kez geçmesin).
    assert meta_line({"season": "SPRING", "seasonYear": 1998,
                      "startDate": {"year": 1998, "month": 4}}) == "İlkbahar 1998"


# ── Detay sayfası ───────────────────────────────────────────────────────────
def test_arsiv_sonucu_kunyeyle_aciliyor(qtbot, page, arsiv, kapak_istekleri):
    page.show_match("TürkAnime", "07-ghost", "07-Ghost")

    qtbot.waitUntil(lambda: "Teito" in page.txtSummary.toPlainText(), timeout=5000)
    ozet = page.txtSummary.toPlainText()
    assert "<" not in ozet and "&amp;" not in ozet
    assert "\n" in ozet, "<br /> satır sonuna çevrilmedi"
    assert [b.text() for b in page.genre_badges] == ["Fantastik", "Aksiyon"]
    assert [b.text() for b in page.studio_badges] == ["Studio Deen"]
    assert "76" in page.lblScore.text()
    assert "25 bölüm" in page.lblMeta.text()
    assert "7 Nisan 2009" in page.lblMeta.text()
    assert page.lblTitle.text() == "07-Ghost", "künye başlığı ezdi"
    # Kapak yok (arşiv) → çizilmiş yer tutucu; ölü "Resim" adresi istenmedi.
    assert page.kapak_yer_tutucuda
    assert not page.lblCover.pixmap().isNull()
    assert kapak_istekleri == []


def test_kunyesiz_arsiv_kaydi_sessizce_bos_kaliyor(qtbot, page, arsiv):
    page.show_match("TürkAnime", "bilgisiz", "Bilgisiz Anime")
    qtbot.wait(150)
    assert "Özet bulunamadı." in page.txtSummary.toPlainText()
    assert page.lblScore.text() == ""


@pytest.mark.parametrize("slug", ["puansiz", "bos-puan", "null-puan"])
def test_puansiz_kayitta_skor_yok(qtbot, page, arsiv, slug):
    page.show_match("TürkAnime", slug, slug)
    qtbot.waitUntil(lambda: "Özet bulunamadı" not in page.txtSummary.toPlainText(),
                    timeout=5000)
    assert page.lblScore.text() == ""


def test_eski_anime_kunyesi_yenisini_ezmiyor(qtbot, page, arsiv):
    """İki hızlı tıklama: yalnızca İKİNCİ animenin künyesi görünmeli."""
    rid_eski = page.show_match("TürkAnime", "07-ghost", "07-Ghost")
    page.show_match("TürkAnime", "ikinci", "İkinci")
    # Birincinin künyesi ŞİMDİ geliyor (geç dönen arka plan işi):
    page._arsiv_bilgisini_uygula(rid_eski, animedepo.anime_bilgisi("07-ghost"))

    qtbot.waitUntil(lambda: "İkinci animenin" in page.txtSummary.toPlainText(),
                    timeout=5000)
    qtbot.wait(100)                          # birincinin gerçek işi de bitsin
    assert "Teito" not in page.txtSummary.toPlainText()
    assert [b.text() for b in page.genre_badges] == ["Dram"]


def test_kunye_okunurken_gui_thread_bloklanmiyor(qtbot, page, arsiv, monkeypatch):
    """Künye arka planda okunuyor: yavaş disk/ayna arayüzü dondurmamalı."""
    kapi = threading.Event()
    gercek = animedepo.anime_bilgisi
    thread_ler: list = []

    def yavas(slug):
        thread_ler.append(threading.get_ident())
        kapi.wait(5)
        return gercek(slug)

    monkeypatch.setattr(animedepo, "anime_bilgisi", yavas)
    try:
        page.show_match("TürkAnime", "07-ghost", "07-Ghost")
        assert "Teito" not in page.txtSummary.toPlainText()   # henüz gelmedi
    finally:
        kapi.set()
    qtbot.waitUntil(lambda: "Teito" in page.txtSummary.toPlainText(), timeout=5000)
    assert thread_ler and thread_ler[0] != threading.get_ident()


def test_arsiv_disi_kaynakta_kunye_okunmuyor(qtbot, page, monkeypatch):
    cagrilar: list = []
    monkeypatch.setattr(animedepo, "anime_bilgisi",
                        lambda slug: cagrilar.append(slug) or {})
    page.show_match("AnimeciX", "17", "Cowboy Bebop")
    qtbot.wait(100)
    assert cagrilar == []


# ── Kapak: arama kartından detaya ───────────────────────────────────────────
def test_arama_kartinin_kapagi_detaya_tasiniyor(main_window, web, sahte_arama,
                                                 kapak_istekleri):
    sahte_arama(sonuclar={"AniList": [{"slug": "154587", "title": "Sousou no Frieren",
                                       "image": "https://img/frieren.jpg"}]})
    main_window.txtSearch.setText("frieren")
    main_window._on_search()
    web.bekle("document.querySelectorAll('.sonuc-grubu .kart').length === 1")
    web.js("document.querySelector('.sonuc-grubu .kart').click()")

    detay = main_window.pages["detail"]
    web.qtbot.waitUntil(lambda: main_window.stack.currentWidget() is detay,
                        timeout=5000)
    assert detay._anime.get("coverImage") == {"large": "https://img/frieren.jpg"}
    web.qtbot.waitUntil(lambda: kapak_istekleri == ["https://img/frieren.jpg"],
                        timeout=5000)


def test_kapaksiz_detayda_yer_tutucu(page, kapak_istekleri):
    page.show_anime({"title": {"romaji": "Kapaksız"}})
    assert page.kapak_yer_tutucuda
    assert not page.lblCover.pixmap().isNull()
    assert kapak_istekleri == []
