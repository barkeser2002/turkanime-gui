"""Web arayüzü altyapısı: `ta://` şeması, köprü, görünüm, keşif uçları.

Sayfalar QtWebEngine'de çiziliyor; testler JS'i `conftest.WebSurucu` ile
çalıştırıp DOM'a bakıyor. Ağ yok: keşif verisi sahte, görseller sahte
`gorsel_getir`'den.
"""
from __future__ import annotations

import json

import pytest

from turkanime_api.common import kutuphane
from turkanime_api.gui.web import sema
from turkanime_api.gui.web.kopru import Kopru, uc
from turkanime_api.gui.web.uclar_kesif import KesifUclari, devam_karti
from turkanime_api.gui.web.veri import kart

# 1×1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0005fe02fea7d6a0ee0000000049454e44ae426082")


def anime(ad="Cowboy Bebop", puan=86, **ek):
    kayit = {"id": abs(hash(ad)) % 10000, "title": {"romaji": ad},
             "coverImage": {"large": f"https://img.example/{ad}.jpg"},
             "averageScore": puan, "episodes": 26, "seasonYear": 1998,
             "format": "TV", "genres": ["Action", "Sci-Fi"]}
    kayit.update(ek)
    return kayit


# ── Şema yardımcıları (Qt'siz) ───────────────────────────────────────────────
def test_statik_yol_kokun_disina_cikmiyor(tmp_path):
    (tmp_path / "index.html").write_text("x")
    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "a.css").write_text("y")
    (tmp_path.parent / "gizli.json").write_text("{}")

    assert sema.statik_yol("/index.html", tmp_path) == (tmp_path / "index.html").resolve()
    assert sema.statik_yol("/", tmp_path) == (tmp_path / "index.html").resolve()
    assert sema.statik_yol("/css/a.css", tmp_path) == (tmp_path / "css" / "a.css").resolve()
    for kotu in ("/../gizli.json", "/css/../../gizli.json", "/C:/Windows/x",
                 "/css\\..\\..\\gizli.json", "/yok.js"):
        assert sema.statik_yol(kotu, tmp_path) is None, kotu


def test_icerik_turu_js_icin_sabit():
    """`mimetypes` Windows'ta `.js`'i text/plain sanabiliyor; tablo önde."""
    assert sema.icerik_turu("a/b.js") == "text/javascript"
    assert sema.icerik_turu("index.html") == "text/html"
    assert sema.icerik_turu("x.css") == "text/css"
    assert sema.icerik_turu("maskot.png") == "image/png"


def test_gorsel_turu_imzadan():
    assert sema.gorsel_turu(PNG) == "image/png"
    assert sema.gorsel_turu(b"\xff\xd8\xff\xe0....") == "image/jpeg"
    assert sema.gorsel_turu(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sema.gorsel_turu(b"<html>hata</html>") is None
    assert sema.gorsel_turu(b"") is None


def test_gorsel_adresi_js_ile_ayni_bicimde():
    assert (sema.gorsel_adresi("https://a.b/c d.jpg?x=1&y=2")
            == "ta://gorsel/?u=https%3A%2F%2Fa.b%2Fc%20d.jpg%3Fx%3D1%26y%3D2")


# ── Köprü ────────────────────────────────────────────────────────────────────
class _Uclar:
    def __init__(self):
        self.cagrilar = []

    @uc()
    def topla(self, a: int, b: int = 1):
        self.cagrilar.append((a, b))
        return {"toplam": a + b, "kume": {1}}

    @uc("yavas", arka=True)
    def _arkada(self, deger: str):
        return deger.upper()

    @uc()
    def patla(self):
        raise RuntimeError("olmadı")


def _yanitlar(kopru):
    gelen = []
    kopru.yanit.connect(lambda i, ok, js: gelen.append((i, ok, json.loads(js))))
    return gelen


def test_kopru_cagri_ve_yanit(qtbot):
    kopru = Kopru()
    uclar = kopru.bagla(_Uclar())
    gelen = _yanitlar(kopru)
    assert kopru.uc_adlari() == ["patla", "topla", "yavas"]

    kopru.cagir("1", "topla", json.dumps({"a": 2, "b": 3}))
    assert gelen == [("1", True, {"toplam": 5, "kume": [1]})]
    assert uclar.cagrilar == [(2, 3)]

    kopru.cagir("2", "yavas", json.dumps({"deger": "abc"}))
    qtbot.waitUntil(lambda: len(gelen) == 2, timeout=3000)
    assert gelen[1] == ("2", True, "ABC")


def test_kopru_hatalari_mesajla_donuyor(qtbot):
    kopru = Kopru()
    kopru.bagla(_Uclar())
    gelen = _yanitlar(kopru)

    kopru.cagir("a", "yok", "{}")
    kopru.cagir("b", "topla", "[1, 2]")
    kopru.cagir("c", "topla", "bozuk json")
    kopru.cagir("d", "topla", json.dumps({"x": 1}))     # yanlış argüman
    kopru.cagir("e", "patla", "{}")
    sonuc = {i: (ok, v["mesaj"]) for i, ok, v in gelen}
    assert sonuc["a"] == (False, "bilinmeyen uç: yok")
    assert sonuc["b"][0] is False and "sözlük" in sonuc["b"][1]
    assert sonuc["c"][0] is False
    assert sonuc["d"][0] is False
    assert sonuc["e"] == (False, "beklenmeyen hata: olmadı")


def test_kopru_ayni_adi_iki_kez_kaydetmiyor():
    kopru = Kopru()
    kopru.bagla(_Uclar())
    with pytest.raises(ValueError):
        kopru.bagla(_Uclar())


def test_kopru_olay_json(qtbot):
    kopru = Kopru()
    gelen = []
    kopru.olay.connect(lambda ad, js: gelen.append((ad, json.loads(js))))
    kopru.yay("indirme", {"yuzde": 40, "yol": __import__("pathlib").Path("/x")})
    assert gelen == [("indirme", {"yuzde": 40, "yol": "/x"})]


# ── Veri biçimi ──────────────────────────────────────────────────────────────
def test_kart_verisi():
    veri = kart(anime())
    assert veri["baslik"] == "Cowboy Bebop"
    assert veri["puan"] == 8.6
    assert veri["rozet"] == "26 bölüm"
    assert veri["alt"] == "1998 · TV · Aksiyon"      # tür Türkçe
    assert veri["kapak"] == "https://img.example/Cowboy Bebop.jpg"
    assert veri["kayit"]["title"]["romaji"] == "Cowboy Bebop"
    assert kart(anime(averageScore=None))["puan"] is None


def test_devam_karti_ilerleme():
    kayit = {"kaynak": "TürkAnime", "kimlik": "07-ghost", "baslik": "07-Ghost",
             "son": {"bolum_baslik": "5. Bölüm"},
             "konum": {"konum": 600, "sure": 1200}}
    veri = devam_karti(kayit)
    assert veri["ilerleme"] == 0.5
    assert veri["bolum_metni"] == "5. Bölüm · 10:00"
    assert veri["kaynak_adi"] == "TürkAnime (arşiv)"
    assert veri["kaynak_renk"]
    assert devam_karti({"kaynak": "x", "kimlik": "y", "konum": None})["ilerleme"] is None


def test_kesif_ucu_sahte_veriyle(monkeypatch):
    import turkanime_api.jikan_client as jikan_mod
    monkeypatch.setattr(jikan_mod, "get_trending_anime_list",
                        lambda limit=25, **k: [anime("A"), anime("B")])
    sonuc = KesifUclari().kesif(mod="trending", limit=10)
    assert [k["baslik"] for k in sonuc["kartlar"]] == ["A", "B"]
    with pytest.raises(ValueError):
        KesifUclari().kesif(mod="yok")


def test_istatistik_gercek_sayilar(izole_ev):
    kutuphane.izleme_kaydet("TürkAnime", "x", "X", "x-1", "1. Bölüm")
    sonuc = KesifUclari().istatistik()
    assert sonuc["kitaplik"] == 1
    assert sonuc["kaynak"] >= 10
    assert sonuc["arsiv"] is None          # testte arşiv yok (conftest)


@pytest.fixture
def izole_ev(tmp_path, monkeypatch):
    """Kitaplık dosyası geçici klasörde."""
    yol = tmp_path / "kutuphane.json"
    monkeypatch.setattr(kutuphane, "kutuphane_yolu", lambda: str(yol))
    return tmp_path


# ── Görünüm (QtWebEngine) ────────────────────────────────────────────────────
@pytest.fixture
def sahte_kesif(monkeypatch):
    import turkanime_api.jikan_client as jikan_mod
    trend = [anime(f"Trend {i}", 70 + i) for i in range(6)]
    sezon = [anime(f"Sezon {i}", 60 + i) for i in range(4)]
    monkeypatch.setattr(jikan_mod, "get_trending_anime_list",
                        lambda limit=25, **k: trend[:limit])
    monkeypatch.setattr(jikan_mod, "get_seasonal_anime_list",
                        lambda *a, **k: sezon)
    return trend, sezon


@pytest.fixture
def sahte_gorsel(monkeypatch):
    istenen = []

    def getir(url, *a, **k):
        istenen.append(url)
        return PNG

    import turkanime_api.gui.qt.gorsel as gorsel_mod
    monkeypatch.setattr(gorsel_mod, "gorsel_getir", getir)
    return istenen


def test_ana_sayfa_seritleri_ve_hero(sahte_kesif, sahte_gorsel, main_window, web):
    trend = "document.querySelectorAll('[data-sayfa=home] .serit')[1]"
    web.bekle(f"{trend}.querySelectorAll('.kart:not(.iskelet-kart)').length === 6")
    assert web.js(f"{trend}.querySelector('.kart-baslik').textContent") == "Trend 0"
    # Trend şeridi sıralı (1, 2, ...); puan rozeti 0-10 ölçeğinde.
    assert web.js(f"{trend}.querySelector('.kart-sira').textContent") == "1"
    assert web.js(f"{trend}.querySelector('.kart-puan').textContent") == "7.0"
    sezon = "document.querySelectorAll('[data-sayfa=home] .serit')[2]"
    web.bekle(f"{sezon}.querySelectorAll('.kart:not(.iskelet-kart)').length === 4")
    # Hero sahnesi haftanın ilk üç posteri; görseller `ta://gorsel`'den.
    web.bekle("document.querySelectorAll('.hero-poster.yuklu').length === 3")
    assert "https://img.example/Trend 0.jpg" in sahte_gorsel
    # Hero sayıları gerçek veri (testte arşiv yok → "—").
    web.bekle("document.querySelector('.hero-sayilar').innerText.includes('kaynak')")
    assert "—" in web.js("document.querySelector('.hero-sayilar').innerText")


def test_gorsel_semasi_yuklenemeyen_gorselde_bas_harf(sahte_kesif, monkeypatch,
                                                      main_window, web):
    import turkanime_api.gui.qt.gorsel as gorsel_mod
    monkeypatch.setattr(gorsel_mod, "gorsel_getir", lambda *a, **k: None)
    main_window.show_page("trending")
    kart = "document.querySelector('[data-sayfa=trending] .izgara .kart')"
    web.bekle(f"{kart} && !{kart}.classList.contains('iskelet-kart')")
    # Görsel düşünce <img> kaldırılıyor, baş harf kalıyor.
    web.bekle(f"!{kart}.querySelector('img')")
    assert web.js(f"{kart}.querySelector('.bas-harf').textContent") == "T"


def test_tumunu_gor_trend_sayfasina_geciyor(sahte_kesif, main_window, web):
    web.bekle("document.querySelectorAll('[data-sayfa=home] .serit .baglanti').length === 2")
    web.js("document.querySelectorAll('[data-sayfa=home] .serit .baglanti')[0].click()")
    web.qtbot.waitUntil(lambda: main_window._current_page == "trending", timeout=5000)
    assert main_window._nav_buttons["trending"].isChecked()
    web.bekle("TA.aktif === 'trending'")
    web.bekle("document.querySelectorAll('[data-sayfa=trending] .izgara .kart:not(.iskelet-kart)').length === 6")


def test_hero_aramasi_arama_sayfasini_aciyor(main_window, web, monkeypatch):
    istenen = []
    monkeypatch.setattr(main_window.pages["search"], "start_search", istenen.append)
    web.js("var f = document.querySelector('.hero-ara'); "
           "f.querySelector('input').value = '  frieren '; f.requestSubmit()")
    web.qtbot.waitUntil(lambda: istenen == ["frieren"], timeout=5000)
    assert main_window.stack.currentWidget() is main_window.pages["search"]
    assert main_window.txtSearch.text() == "frieren"


def test_bos_keşif_bos_durum_gosteriyor(main_window, web):
    """Jikan/AniList boş (autouse sahte): kipin mesajı + Yenile düğmesi."""
    main_window.show_page("season")
    web.bekle("!!document.querySelector('[data-sayfa=season] .bos-durum')")
    metin = web.js("document.querySelector('[data-sayfa=season] .bos-durum').innerText")
    assert "Sezon verisi alınamadı" in metin
    assert web.js("!!document.querySelector('[data-sayfa=season] .bos-durum button')")


def test_dis_adres_gorunumde_acilmiyor(main_window, web, monkeypatch):
    """`ta://` dışı gezinti sistem tarayıcısına gidiyor, görünüm yerinde."""
    from PySide6.QtGui import QDesktopServices
    acilan = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: acilan.append(url.toString()) or True))
    web.js("location.href = 'https://anilist.co/anime/1'")
    web.qtbot.waitUntil(lambda: acilan == ["https://anilist.co/anime/1"], timeout=5000)
    web.qtbot.wait(200)
    assert main_window.web.url().scheme() == "ta"
    assert web.js("TA.aktif") == "home"


def test_bilinmeyen_hedef_reddediliyor(main_window, web):
    hata = web.bekle("TA.cagir('ac', {hedef: 'rm -rf', veri: {}})"
                     ".then(() => 'kabul', e => (window._h = e.message)) && window._h")
    assert "bilinmeyen hedef" in hata


def test_statik_disi_yol_404(main_window, web):
    durum = web.bekle(
        "(window._d === undefined && fetch('ta://uygulama/../ayarlar.json')"
        ".then(r => window._d = r.status, () => window._d = 'hata'), window._d)")
    assert durum in ("hata", 404)
