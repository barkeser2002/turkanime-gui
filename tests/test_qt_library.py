"""Kitaplığım: başarılı oynatma kaydı, kaynak kimliği, favori, yeniden açma.

Gerçek mpv/ağ yok: sahte bölüm/video, `fetch_episodes` sahtesi. Kitaplık
dosyası `izole_ev`'in geçici kökünde.
"""
from __future__ import annotations

import pytest

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.common import kutuphane
from turkanime_api.gui.qt.pages import detail as detail_mod
from turkanime_api.gui.qt.pages.detail import FAVORI_EKLE, FAVORI_VAR, DetailPage
from turkanime_api.gui.qt.pages.library import LibraryPage
from turkanime_api.gui.qt.progress_dialog import ProgressDialog


class Surec:
    def __init__(self, kod):
        self.returncode = kod


class Video:
    def __init__(self, kod=0):
        self.url = f"https://ornek/{kod}.mp4"
        self.player = "SIBNET"
        self.kod = kod

    def oynat(self, dakika_hatirla=False):
        return Surec(self.kod)


class Anime:
    def __init__(self, slug):
        self.slug = slug
        self.title = slug


class Bolum:
    def __init__(self, seri, slug, kod=0):
        self.anime = Anime(seri)
        self.slug = slug
        self.kod = kod
        self.cagri = 0

    def best_video(self, **kwargs):
        self.cagri += 1
        # Başarısız senaryoda her aday 2 ile düşer; üçüncü denemeden sonra
        # aday kalmaz.
        return Video(self.kod) if self.cagri <= 3 else None


@pytest.fixture
def diyalogsuz(monkeypatch):
    monkeypatch.setattr(ProgressDialog, "exec", lambda self: 0)


def _oynat_ve_bekle(main_window, qtbot, tetikle):
    tetikle()
    qtbot.waitUntil(lambda: main_window._playing is False, timeout=10000)
    qtbot.wait(50)


def _detay(main_window, web, sahte_bolumler, kaynak, kimlik, baslik, bolumler):
    """Detay sayfasını kaynağa bağlı aç, bölümler (sahte) çizilene kadar bekle."""
    sahte_bolumler({kaynak: bolumler})
    main_window._on_anime_selected(kaynak, kimlik, baslik)
    web.detay_bekle(kaynak, len(bolumler))


def _oynat_dugmesi(web, kaynak, sira=0):
    return (f"{web.satirlar(kaynak)}[{sira}].querySelector('.oynat-dugme').click()")


def test_basarili_oynatma_kaynak_ve_kimlikle_yaziliyor(
        izole_ev, main_window, web, qtbot, diyalogsuz, sahte_bolumler):
    bolum = Bolum("07-ghost", "07-ghost-1-bolum")
    _detay(main_window, web, sahte_bolumler, "TürkAnime", "07-ghost", "07-Ghost",
           [{"title": "07-Ghost 1. Bölüm", "obj": bolum}])
    _oynat_ve_bekle(main_window, qtbot, lambda: web.js(_oynat_dugmesi(web, "TürkAnime")))

    seri = kutuphane.oku()["seriler"]["TürkAnime:07-ghost"]
    assert seri["baslik"] == "07-Ghost"
    assert seri["son"]["bolum_slug"] == "07-ghost-1-bolum"
    assert seri["son"]["bolum_baslik"] == "07-Ghost 1. Bölüm"
    assert seri["son"]["zaman"] > 0
    assert kutuphane.gecmis_listesi()[0]["kaynak"] == "TürkAnime"


def test_basarisiz_oynatma_kitapliga_yazilmiyor(
        izole_ev, main_window, web, qtbot, diyalogsuz, sahte_bolumler):
    bolum = Bolum("07-ghost", "07-ghost-1-bolum", kod=2)
    _detay(main_window, web, sahte_bolumler, "TürkAnime", "07-ghost", "07-Ghost",
           [{"title": "07-Ghost 1. Bölüm", "obj": bolum}])
    _oynat_ve_bekle(main_window, qtbot, lambda: web.js(_oynat_dugmesi(web, "TürkAnime")))
    assert kutuphane.oku()["seriler"] == {}
    assert kutuphane.gecmis_listesi() == []


def test_sayisal_kaynak_kimligi_slugla_degismiyor(
        izole_ev, main_window, web, qtbot, diyalogsuz, sahte_bolumler):
    """`AdapterAnime` "1234"ü başlık slug'ına çeviriyor; kitaplık "1234" tutmalı."""
    bolum = Bolum("naruto", "naruto-1")
    _detay(main_window, web, sahte_bolumler, "AnimeciX", "1234", "Naruto",
           [{"title": "1. Bölüm", "obj": bolum}])
    _oynat_ve_bekle(main_window, qtbot, lambda: web.js(_oynat_dugmesi(web, "AnimeciX")))
    assert set(kutuphane.oku()["seriler"]) == {"AnimeciX:1234"}


def test_cok_kaynakli_listede_tiklanan_kaynagin_kimligi(
        izole_ev, main_window, web, qtbot, diyalogsuz, sahte_bolumler, monkeypatch):
    """İkinci kaynak (AnimeciX) sonradan bağlanıyor; onun akordiyonundan
    oynatılan bölüm kitaplığa AnimeciX'in KENDİ kimliğiyle yazılmalı."""
    from turkanime_api.gui.web import uclar_detay
    ta = {"title": "1. Bölüm", "obj": Bolum("naruto", "naruto-1-bolum")}
    ax = {"title": "1. Bölüm", "obj": Bolum("naruto", "naruto-1")}
    sahte_bolumler({"TürkAnime": [ta], "AnimeciX": [ax]})
    monkeypatch.setattr(uclar_detay, "kaynaklari_esle",
                        lambda b, h, bagli=None, limit=None: (
                            {"AnimeciX": "1234"}, {"AnimeciX": "Naruto"}, []))
    main_window._on_anime_selected("TürkAnime", "naruto", "Naruto")
    web.detay_bekle("TürkAnime", 1)
    web.js("Array.from(document.querySelectorAll('.kaynaklar-baslik button'))"
           ".find(b => b.textContent.includes('Eşleştir')).click()")
    web.bekle("document.querySelectorAll('.akordiyon').length === 2")
    web.js("document.querySelector('.akordiyon[data-kaynak=AnimeciX] .ak-baslik').click()")
    web.detay_bekle("AnimeciX", 1)
    _oynat_ve_bekle(main_window, qtbot, lambda: web.js(_oynat_dugmesi(web, "AnimeciX")))
    assert set(kutuphane.oku()["seriler"]) == {"AnimeciX:1234"}


# ── Detay sayfası: kitaplığa ekle ────────────────────────────────────────────
def test_detay_kitapliga_ekle_dugmesi(izole_ev, qtbot):
    d = Dosyalar()
    d.set_gecmis("07-ghost", "07-ghost-1-bolum", "izlendi")
    d.set_gecmis("07-ghost", "07-ghost-1-bolum", "indirildi")
    d.set_ilerleme("07-ghost", 1)

    page = DetailPage()
    qtbot.addWidget(page)
    page.show_anime({"title": {"romaji": "07-Ghost"}})
    assert not page.btnFavori.isEnabled(), "bağsız kayıtta kimlik yok"

    page.show_match("TürkAnime", "07-ghost", "07-Ghost",
                    kayit={"image": "http://k/07.jpg"})
    assert page.btnFavori.isEnabled()
    assert page.btnFavori.text() == FAVORI_EKLE

    page.btnFavori.click()
    assert page.btnFavori.text() == FAVORI_VAR
    qtbot.waitUntil(lambda: kutuphane.favori_mi("TürkAnime", "07-ghost"), timeout=5000)
    seri = kutuphane.favoriler()[0]
    assert (seri["baslik"], seri["kapak"]) == ("07-Ghost", "http://k/07.jpg")

    gecmis = Dosyalar().gecmis
    assert gecmis["izlendi"] == {"07-ghost": ["07-ghost-1-bolum"]}
    assert gecmis["indirildi"] == {"07-ghost": ["07-ghost-1-bolum"]}
    assert gecmis["ilerleme"] == {"07-ghost": 1}
    Dosyalar().set_gecmis("07-ghost", "07-ghost-2-bolum", "izlendi")

    # Yeni sayfa aynı kaydı açınca düğme dolu gelir; tekrar basınca çıkar.
    page.show_match("TürkAnime", "07-ghost", "07-Ghost")
    assert page.btnFavori.isChecked()
    page.btnFavori.click()
    qtbot.waitUntil(lambda: not kutuphane.favori_mi("TürkAnime", "07-ghost"),
                    timeout=5000)


# ── Kitaplığım sayfası ve ana sayfa şeridi ───────────────────────────────────
@pytest.fixture
def iki_seri(izole_ev):
    kutuphane.izleme_kaydet("AnimeciX", "1234", "Naruto", "naruto-3",
                            "3. Bölüm", zaman=100)
    kutuphane.izleme_kaydet("TürkAnime", "07-ghost", "07-Ghost", "07-ghost-5-bolum",
                            "5. Bölüm", zaman=200)
    return izole_ev


def test_kitaplik_sayfasi_listeler(iki_seri, qtbot):
    page = LibraryPage()
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(lambda: len(page.kartlar("devam")) == 2, timeout=5000)
    assert [k.payload["kimlik"] for k in page.kartlar("devam")] == ["07-ghost", "1234"]
    assert page.kartlar("favori") == []
    assert not page.lblBosFavori.isHidden(), "boş favori sekmesi açıklama göstermeli"
    assert page.lblBosDevam.isHidden()
    assert page.lstGecmis.count() == 2
    assert "07-Ghost — 5. Bölüm" in page.lstGecmis.item(0).text()


def test_kart_detayi_bagli_acip_bolumleri_getiriyor(
        iki_seri, main_window, web, qtbot, monkeypatch, sahte_bolumler):
    """Kitaplık kartı detayı kaynağa BAĞLI açar: eşleştirme yok, bölümler gelir."""
    from turkanime_api.gui.web import uclar_detay

    def eslestirme_yasak(*_a, **_k):
        raise AssertionError("kitaplık kaydında eşleştirme aranmamalı")

    monkeypatch.setattr(uclar_detay, "kaynaklari_esle", eslestirme_yasak)
    istenen = sahte_bolumler({"TürkAnime": [
        {"title": "07-Ghost 1. Bölüm", "obj": Bolum("07-ghost", "07-ghost-1")}]})

    main_window.show_page("library")
    page = main_window.pages["library"]
    qtbot.waitUntil(lambda: len(page.kartlar("devam")) == 2, timeout=5000)
    page.kartlar("devam")[0].clicked.emit(page.kartlar("devam")[0].payload)

    assert main_window.detay.oturum.baglar == {"TürkAnime": "07-ghost"}
    web.detay_bekle("TürkAnime", 1)
    assert istenen == [("TürkAnime", "07-ghost")]
    assert main_window.detay.oturum.bolumler["TürkAnime"][0]["kimlik"] == "07-ghost"


def test_ana_sayfa_devam_seridi(iki_seri, main_window, web, monkeypatch, sahte_bolumler):
    """Jikan/AniList boş (autouse sahte) ama şerit yerel kitaplıktan dolu.

    Ana sayfa web arayüzünde: şerit `devam_listesi` ucundan çiziliyor, karta
    tıklamak köprüden `ac("kitaplik")` ile detayı kaynağa bağlı açıyor.
    """
    sahte_bolumler({})
    web.bekle("document.querySelectorAll('#devam .devam-kart').length === 2")
    assert web.js("document.getElementById('devam').hidden") is False
    ilk = web.js("document.querySelectorAll('#devam .devam-kart')[0].innerText")
    assert "07-Ghost" in ilk and "5. Bölüm" in ilk

    acilan = []
    ozgun = main_window.detay.ac_kitaplik
    monkeypatch.setattr(main_window.detay, "ac_kitaplik",
                        lambda k: (acilan.append(k), ozgun(k))[1])
    web.js("document.querySelectorAll('#devam .devam-kart')[1].click()")
    web.qtbot.waitUntil(lambda: bool(acilan), timeout=5000)
    assert acilan[0]["kimlik"] == "1234"
    assert main_window.detay.oturum.baglar == {"AnimeciX": "1234"}
    assert main_window._current_page == "detail"


def test_bos_kitaplikta_serit_gizli(izole_ev, main_window, web):
    # İstatistik ucu döndüyse (hero sayıları dolu) şerit ucu da dönmüştür:
    # ikisi aynı `goster`'de, aynı havuzda istendi.
    web.bekle("document.querySelector('.hero-sayilar b') && "
              "document.querySelector('.hero-sayilar').innerText.includes('kaynak')")
    web.qtbot.wait(200)
    assert web.js("document.querySelectorAll('#devam .devam-kart').length") == 0
    assert web.js("document.getElementById('devam').hidden") is True


def test_menude_kitaplik_ve_bilinmeyen_anahtar_korumasi(main_window):
    assert isinstance(main_window.pages["library"], LibraryPage)
    assert main_window._nav_buttons["library"].text() == "Kitaplığım"
    with pytest.raises(ValueError):
        main_window._make_page("yok-boyle", "Yok")
