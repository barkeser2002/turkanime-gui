"""Arşiv künyesi (`info.json`) → detay sayfası; kapak yer tutucusu ve taşınması.

Ağa çıkılmaz: arşiv `tmp_path`'te kurulur ve "depodaki arşiv" sayılır
(`DEPO_ARSIVI`); uzak aynalar conftest'te zaten kesik (`_session` patlar).
Detay sayfası web'de: künye `DetayUclari.detay` ucundan geliyor.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from turkanime_api.gui.web.kopru import Kopru
from turkanime_api.gui.web.kunye import kunye_birlestir, meta_line
from turkanime_api.gui.web.uclar_detay import DetayUclari, EskiIstek
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
def uclar():
    return DetayUclari(None, oynat=lambda e: None, indir=lambda e: None)


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


# ── Detay ucu ───────────────────────────────────────────────────────────────
def test_arsiv_sonucu_kunyeyle_aciliyor(uclar, arsiv):
    rid = uclar.ac_sonuc("TürkAnime", "07-ghost", "07-Ghost")
    k = uclar.detay(rid)["kunye"]

    assert "Teito" in k["ozet"]
    assert "<" not in k["ozet"] and "&amp;" not in k["ozet"]
    assert "\n" in k["ozet"], "<br /> satır sonuna çevrilmedi"
    assert k["turler"] == ["Fantastik", "Aksiyon"]
    assert k["studyolar"] == ["Studio Deen"]
    assert k["puan"] == 76
    assert "25 bölüm" in k["meta"] and "7 Nisan 2009" in k["meta"]
    assert k["baslik"] == "07-Ghost", "künye başlığı ezdi"
    # Ölü siteye giden "Resim" adresi kapak olarak taşınmadı.
    assert k["kapak"] == ""


def test_kunyesiz_arsiv_kaydi_sessizce_bos_kaliyor(uclar, arsiv):
    k = uclar.detay(uclar.ac_sonuc("TürkAnime", "bilgisiz", "Bilgisiz Anime"))["kunye"]
    assert k["ozet"] == "" and k["puan"] is None


@pytest.mark.parametrize("slug", ["puansiz", "bos-puan", "null-puan"])
def test_puansiz_kayitta_skor_yok(uclar, arsiv, slug):
    k = uclar.detay(uclar.ac_sonuc("TürkAnime", slug, slug))["kunye"]
    assert k["ozet"] and k["puan"] is None


def test_eski_anime_kunyesi_yenisini_ezmiyor(uclar, arsiv):
    """İki hızlı tıklama: birincinin geç dönen künyesi ikinciyi ezmemeli."""
    rid_eski = uclar.ac_sonuc("TürkAnime", "07-ghost", "07-Ghost")
    rid = uclar.ac_sonuc("TürkAnime", "ikinci", "İkinci")
    with pytest.raises(EskiIstek):
        uclar.detay(rid_eski)                # geç dönen arka plan işi
    assert "Teito" not in json.dumps(uclar.oturum.anime, ensure_ascii=False)
    k = uclar.detay(rid)["kunye"]
    assert "İkinci animenin" in k["ozet"] and k["turler"] == ["Dram"]


def test_kunye_okunurken_gui_thread_bloklanmiyor(qtbot, uclar, arsiv, monkeypatch):
    """Künye arka planda okunuyor: yavaş disk/ayna arayüzü dondurmamalı."""
    kapi = threading.Event()
    gercek = animedepo.anime_bilgisi
    thread_ler: list = []

    def yavas(slug):
        thread_ler.append(threading.get_ident())
        kapi.wait(5)
        return gercek(slug)

    monkeypatch.setattr(animedepo, "anime_bilgisi", yavas)
    kopru = Kopru()
    kopru.bagla(uclar)
    yanitlar: list = []
    kopru.yanit.connect(lambda istek, ok, veri: yanitlar.append((istek, ok, veri)))
    rid = uclar.ac_sonuc("TürkAnime", "07-ghost", "07-Ghost")
    try:
        kopru.cagir("1", "detay", json.dumps({"rid": rid}))
        assert yanitlar == []                            # çağrı hemen döndü
    finally:
        kapi.set()
    qtbot.waitUntil(lambda: bool(yanitlar), timeout=5000)
    assert yanitlar[0][1] is True and "Teito" in yanitlar[0][2]
    assert thread_ler and thread_ler[0] != threading.get_ident()


def test_arsiv_disi_kaynakta_kunye_okunmuyor(uclar, monkeypatch):
    cagrilar: list = []
    monkeypatch.setattr(animedepo, "anime_bilgisi",
                        lambda slug: cagrilar.append(slug) or {})
    uclar.detay(uclar.ac_sonuc("AnimeciX", "17", "Cowboy Bebop"))
    assert cagrilar == []


def test_arsiv_kunyesi_sayfada(main_window, web, arsiv, sahte_bolumler):
    """Sayfa: özet satır sonlarıyla, tür/stüdyo hapları, skor kartı."""
    sahte_bolumler({"TürkAnime": []})
    main_window._on_anime_selected("TürkAnime", "07-ghost", "07-Ghost", None)
    web.bekle("!!document.querySelector('.ozet-metin') && "
              "document.querySelector('.ozet-metin').textContent.includes('Teito')")
    assert web.js("[...document.querySelectorAll('.etiket-blok .hap')]"
                  ".map(e => e.textContent)") == ["Fantastik", "Aksiyon", "Studio Deen"]
    assert web.js("document.querySelector('.stat-deger').textContent") == "76%"
    assert "25 bölüm" in web.js("document.querySelector('.detay-meta').textContent")


# ── Kapak: arama kartından detaya ───────────────────────────────────────────
def test_arama_kartinin_kapagi_detaya_tasiniyor(main_window, web, sahte_arama,
                                                 monkeypatch):
    import turkanime_api.gui.qt.gorsel as gorsel_mod
    istenen: list = []
    monkeypatch.setattr(gorsel_mod, "gorsel_getir",
                        lambda url, *a, **k: istenen.append(url) or None)
    sahte_arama(sonuclar={"AniList": [{"slug": "154587", "title": "Sousou no Frieren",
                                       "image": "https://img/frieren.jpg"}]})
    main_window.ara("frieren")
    web.bekle("document.querySelectorAll('.sonuc-grubu .kart').length === 1")
    web.js("document.querySelector('.sonuc-grubu .kart').click()")

    web.bekle("TA.aktif === 'detail' && !!document.querySelector('.detay-poster')")
    assert main_window.detay.oturum.anime.get("coverImage") == {
        "large": "https://img/frieren.jpg"}
    # Poster detayda da aynı adresten (önbellekli `ta://gorsel`) isteniyor.
    web.bekle("!!document.querySelector('.detay-poster .bas-harf')")
    web.qtbot.waitUntil(lambda: istenen.count("https://img/frieren.jpg") >= 2,
                        timeout=5000)


def test_kapaksiz_detayda_yer_tutucu(main_window, web, monkeypatch):
    import turkanime_api.gui.qt.gorsel as gorsel_mod
    istenen: list = []
    monkeypatch.setattr(gorsel_mod, "gorsel_getir",
                        lambda url, *a, **k: istenen.append(url) or None)
    main_window._on_discover_selected({"title": {"romaji": "Kapaksız"}})
    web.bekle("TA.aktif === 'detail' && !!document.querySelector('.detay-poster .bas-harf')")
    assert web.js("document.querySelector('.detay-poster .bas-harf').textContent") == "K"
    assert not web.js("!!document.querySelector('.detay-poster img')")
    assert istenen == []
