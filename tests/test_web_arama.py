"""Web arama sayfası: artımlı sonuçlar, kaynak hapları, hatalar, eski istek.

Motor `conftest.sahte_arama` ile sahte (ağ yok); akış gerçek köprüden,
olaylar gerçek QtWebEngine sayfasına gidiyor.
"""
from __future__ import annotations

import threading

import pytest

from turkanime_api.gui.web.uclar_arama import (
    AramaUclari, kaynak_sirasi, sonuc_kartlari,
)

GRUPLAR = "Array.from(document.querySelectorAll('.sonuc-grubu')).map(g => g.dataset.kaynak)"


def kayit(slug, baslik=None, resim=None):
    return {"slug": slug, "title": baslik or slug.title(), "image": resim}


def ara(main_window, web, sorgu):
    main_window.txtSearch.setText(sorgu)
    main_window._on_search()
    web.bekle("TA.aktif === 'search'")


# ── Qt'siz yardımcılar ───────────────────────────────────────────────────────
def test_sonuc_kartlari_slugsuzu_atiyor():
    kartlar = sonuc_kartlari("AnimeciX", [
        kayit("naruto", "Naruto", "https://x/n.jpg"), {"title": "slug yok"},
        None, ("eski", "demet"), {"slug": "bleach"}])
    assert [k["slug"] for k in kartlar] == ["naruto", "bleach"]
    assert kartlar[0]["kapak"] == "https://x/n.jpg"
    assert kartlar[0]["alt"] == "AnimeciX"
    assert kartlar[0]["rozet"] and kartlar[0]["rozet_renk"]
    assert kartlar[1]["baslik"] == "bleach"          # başlık yoksa slug


def test_metadata_kaynagi_en_sonda():
    adlar = sorted(["AniList", "AnimeciX", "TürkAnime", "Bilinmeyen"], key=kaynak_sirasi)
    assert adlar[0] == "TürkAnime"
    assert adlar[-1] == "AniList"


def test_bos_arama_reddediliyor():
    with pytest.raises(ValueError):
        AramaUclari(kopru=None).ara("   ")


# ── Sayfa ────────────────────────────────────────────────────────────────────
def test_gruplar_kayit_sirasinda_haplar_sayili(main_window, web, sahte_arama):
    sahte_arama(sonuclar={
        "AniList": [kayit("1", "Naruto")],
        "AnimeciX": [kayit("naruto", "Naruto"), kayit("boruto")],
        "TürkAnime": [kayit("naruto", "Naruto"), {"title": "slug yok"}],
        "OpenAnime": [],
    })
    ara(main_window, web, "naruto")
    web.bekle(f"{GRUPLAR}.length === 3")
    assert web.js(GRUPLAR) == ["TürkAnime", "AnimeciX", "AniList"]
    web.bekle("document.querySelector('.sayfa-baslik p').textContent.includes('4 sonuç')")
    haplar = web.js("Array.from(document.querySelectorAll('.cip')).map(c => c.innerText.replace(/\\s+/g, ' ').trim())")
    assert haplar[0] == "Tümü 4"
    assert "TürkAnime (arşiv) 1" in haplar
    # Sonuçsuz kaynağın hapı kapalı, tıklanamıyor.
    assert web.js("document.querySelector('.cip.sonucsuz').disabled") is True
    # AniList grubunda "yalnızca bilgi" notu.
    assert "yalnızca bilgi" in web.js(
        "document.querySelector('.sonuc-grubu[data-kaynak=AniList] .grup-baslik').innerText")


def test_hap_filtreliyor(main_window, web, sahte_arama):
    sahte_arama(sonuclar={"TürkAnime": [kayit("a")], "AnimeciX": [kayit("b")]})
    ara(main_window, web, "x")
    web.bekle(f"{GRUPLAR}.length === 2")
    web.js("Array.from(document.querySelectorAll('.cip')).find(c => c.innerText.includes('AnimeciX')).click()")
    gorunen = ("Array.from(document.querySelectorAll('.sonuc-grubu'))"
               ".filter(g => !g.hidden).map(g => g.dataset.kaynak)")
    web.bekle(f"{gorunen}.length === 1")
    assert web.js(gorunen) == ["AnimeciX"]
    web.js("document.querySelector('.cip').click()")          # Tümü
    web.bekle(f"{gorunen}.length === 2")


def test_hatalar_ve_zaman_asimi(main_window, web, sahte_arama):
    sahte_arama(sonuclar={"TürkAnime": [kayit("a")]},
                hatalar={"TRAnimeİzle": "oturum çerezi gerekli"},
                yetismeyen={"Anizle": "zaman aşımı (25 sn)"})
    ara(main_window, web, "x")
    web.bekle("document.querySelectorAll('.cip.hatali').length === 2")
    uyari = web.js("document.querySelector('.arama-uyari').innerText")
    assert "oturum çerezi gerekli" in uyari and "zaman aşımı" in uyari
    # Hatalı hap soluk değil ve sebebi araç ipucunda.
    assert web.js("document.querySelector('.cip.hatali.sonucsuz') === null")
    assert "zaman aşımı" in web.js(
        "Array.from(document.querySelectorAll('.cip.hatali')).map(c => c.title).join('|')")


def test_sonuc_yokken_sebepler_listeleniyor(main_window, web, sahte_arama):
    sahte_arama(sonuclar={"AnimeciX": []},
                hatalar={"TürkAnime": "arşiv okunamadı: aynalar kapalı"})
    ara(main_window, web, "yokboyle")
    web.bekle("!!document.querySelector('.arama-bos .bos-durum')")
    metin = web.js("document.querySelector('.arama-bos').innerText")
    assert "Sonuç bulunamadı" in metin
    assert "arşiv okunamadı" in metin
    assert "için sonuç bulunamadı" in web.js("document.querySelector('.sayfa-baslik p').textContent")


def test_karta_tiklamak_detayi_kaynaga_bagli_aciyor(main_window, web, sahte_arama,
                                                     sahte_bolumler):
    sahte_bolumler({"AnimeciX": [{"title": "1. Bölüm", "obj": object()}]})
    sahte_arama(sonuclar={"AnimeciX": [kayit("naruto", "Naruto", "https://x/n.jpg")]})
    ara(main_window, web, "naruto")
    web.bekle("document.querySelectorAll('.sonuc-grubu .kart').length === 1")
    web.js("document.querySelector('.sonuc-grubu .kart').click()")
    web.detay_bekle("AnimeciX", 1)
    oturum = main_window.detay.oturum
    assert oturum.baglar == {"AnimeciX": "naruto"}
    assert oturum.anime["coverImage"] == {"large": "https://x/n.jpg"}
    # Detaydan "Geri" aramaya, sonuçlar yerinde.
    web.js("document.querySelector('.geri-dugme').click()")
    web.qtbot.waitUntil(lambda: main_window._current_page == "search", timeout=5000)
    web.bekle("TA.aktif === 'search'")
    assert web.js("document.querySelectorAll('.sonuc-grubu .kart').length") == 1


def test_eski_aramanin_gec_sonucu_atiliyor(main_window, web, monkeypatch):
    """İlk arama yavaş kaynakta takılıyken ikinci arama başlıyor; ilkinin
    geç dönen sonuçları ekrana düşmemeli."""
    import turkanime_api.common.adapters as adapters_mod

    birak = threading.Event()

    class Motor:
        adapters = {"TürkAnime": None}

        def artimli_ara(self, sorgu, kaynak_bitti, limit_per_source=10):
            if sorgu == "yavas":
                birak.wait(10)
            kaynak_bitti("TürkAnime", [kayit(f"{sorgu}-sonuc")], None)
            return adapters_mod.AramaSonuclari({"TürkAnime": [kayit(f"{sorgu}-sonuc")]})

    monkeypatch.setattr(adapters_mod, "SearchEngine", Motor)
    ara(main_window, web, "yavas")
    ara(main_window, web, "hizli")
    web.bekle("document.querySelectorAll('.sonuc-grubu .kart').length === 1")
    birak.set()
    web.qtbot.wait(300)
    basliklar = web.js("Array.from(document.querySelectorAll('.sonuc-grubu .kart-baslik')).map(e => e.textContent)")
    assert basliklar == ["Hizli-Sonuc"]
    assert web.js("document.querySelector('.arama-cubugu input').value") == "hizli"


def test_ayni_sorguya_donmek_yeniden_aramiyor(main_window, web, sahte_arama):
    sorgular = sahte_arama(sonuclar={"TürkAnime": [kayit("a")]})
    ara(main_window, web, "naruto")
    web.qtbot.waitUntil(lambda: sorgular == ["naruto"], timeout=5000)
    main_window.show_page("home")
    main_window.show_page("search")
    web.bekle("TA.aktif === 'search'")
    web.qtbot.wait(200)
    assert sorgular == ["naruto"]
