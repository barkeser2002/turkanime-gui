"""Detay sayfasının "Kaynaklar ve Bölümler" bölümü: akordiyon, seçim, aralık.

Eski Qt'de ayrı bir bölüm sayfasıydı (`EpisodePage`: kaynakları tek satırda
birleştiren liste + kaynak seçme penceresi). Web'de her bağlı kaynak kendi
akordiyonunda; seçim kaynak başına, indirme seçilen satırların KENDİ
kaynağından. Buradaki testler eski sayfanın sözleşmelerini yeni düzende
sınıyor: sayfalama, filtre, "tümü"nün çizilmemiş satırları da kapsaması,
aralık metni, izlenmemiş/indirilmemiş seçimi, Shift+tık, arşiv etiketi.

Ağ yok: bölümler `sahte_bolumler`, eşleştirme `kaynaklari_esle` sahtesiyle.
"""
from __future__ import annotations

import json

import pytest

from turkanime_api.gui.web import uclar_detay
from turkanime_api.gui.web.uclar_detay import kaynak_bilgisi

ARSIV_ETIKETI = "TürkAnime (arşiv)"


class Anime:
    def __init__(self, slug):
        self.slug = slug


class Bolum:
    def __init__(self, seri, slug):
        self.anime = Anime(seri)
        self.slug = slug


def bolumler(seri, adet, onek=""):
    return [{"title": f"{onek}{i}. Bölüm", "obj": Bolum(seri, f"{seri}-{i}")}
            for i in range(1, adet + 1)]


def ac(main_window, kaynak="TürkAnime", slug="naruto", baslik="Naruto"):
    main_window._on_anime_selected(kaynak, slug, baslik, None)


def akordiyon(kaynak):
    return f"document.querySelector('.akordiyon[data-kaynak=\"{kaynak}\"]')"


def arac(kaynak, metin):
    """Akordiyonun araç çubuğundaki düğme (metinle)."""
    return (f"[...{akordiyon(kaynak)}.querySelectorAll('.ak-arac button')]"
            f".find(b => b.textContent.trim() === {json.dumps(metin)})")


def eylem(metin):
    return ("[...document.querySelectorAll('.eylem-cubugu button')]"
            f".find(b => b.textContent.includes({json.dumps(metin)}))")


def secim_sayisi(web, kaynak="TürkAnime"):
    return web.js(f"{akordiyon(kaynak)}.querySelector('.secim-sayi').textContent")


def secim_bekle(web, adet, kaynak="TürkAnime"):
    metin = f"{adet} seçili" if adet else ""
    web.bekle(f"{akordiyon(kaynak)}.querySelector('.secim-sayi').textContent === "
              f"{json.dumps(metin)}")


def secili_siralar(web, kaynak="TürkAnime"):
    return web.js(f"[...{web.satirlar(kaynak)}].filter(li => li.classList.contains('secili'))"
                  ".map(li => Number(li.dataset.sira))")


# ── Saf yardımcılar ─────────────────────────────────────────────────────────
def test_kaynak_kisaltmasi_ve_etiketi():
    assert kaynak_bilgisi("TürkAnime")["kisaltma"] == "TA"
    assert kaynak_bilgisi("AnimeciX")["kisaltma"] == "CX"
    assert kaynak_bilgisi("YeniKaynak")["kisaltma"] == "YE"   # kayıtsız: ilk iki harf
    assert kaynak_bilgisi("TürkAnime")["etiket"] == ARSIV_ETIKETI


def test_bolum_filtresi_ve_aralik_cozumu_saf(main_window, web):
    """`TA.bolumEslesir` / `TA.aralikCoz` (sayfanın saf yardımcıları)."""
    def eslesir(igne):
        return web.js(f"TA.bolumEslesir({{baslik: '12. Bölüm', no: 12}}, {json.dumps(igne)})")

    assert eslesir("12") and eslesir("bölüm") and eslesir("")
    assert not eslesir("13")

    def coz(metin, mevcut=range(1, 13)):
        return web.js(f"(() => {{ var r = TA.aralikCoz({json.dumps(metin)}, "
                      f"{json.dumps(list(mevcut))});"
                      " return [Array.from(r.secilen).sort((a, b) => a - b), r.hatali]; })()")

    assert coz("1-3, 5, 10-") == [[1, 2, 3, 5, 10, 11, 12], []]
    assert coz("-2 12") == [[1, 2, 12], []]
    assert coz("1 - 3") == [[1, 2, 3], []]
    assert coz("1–3") == [[1, 2, 3], []]                     # uzun tire
    assert coz("1-5", [1, 2, 4, 5]) == [[1, 2, 4, 5], []]    # yayınlanmamış atlanır
    # Geçersiz parça istisna değil, dönüşte.
    assert coz("1-2, abc, 9-7") == [[1, 2], ["abc", "9-7"]]


# ── Çok kaynak: kaynak başına akordiyon ─────────────────────────────────────
@pytest.fixture
def iki_kaynak(main_window, web, sahte_bolumler, monkeypatch):
    """Keşif kartı → "Kaynaklarda Eşleştir" → AnimeciX + Anizle bağlı;
    TürkAnime eşleşmedi. Dönen: (bölüm çağrıları, oynatılanlar)."""
    monkeypatch.setattr(uclar_detay, "arsiv_yerel_mi", lambda: False)
    monkeypatch.setattr(uclar_detay, "oynatilabilir_kaynaklar",
                        lambda: ["TürkAnime", "AnimeciX", "Anizle"])
    monkeypatch.setattr(uclar_detay, "kaynaklari_esle", lambda b, hedefler, bagli=None, limit=None: (
        {"AnimeciX": "17", "Anizle": "cowboy-bebop"},
        {"AnimeciX": "Cowboy Bebop", "Anizle": "Cowboy Bebop"}, ["TürkAnime"]))
    cagrilar = sahte_bolumler({"AnimeciX": bolumler("cb", 3),
                               "Anizle": bolumler("cb", 2, "Bölüm ")})
    oynatilan: list = []
    main_window.detay._oynat = oynatilan.append
    main_window._on_discover_selected({"title": {"romaji": "Cowboy Bebop"}})
    web.bekle("!!document.querySelector('.akordiyonlar .bos-durum button')", timeout=8000)
    web.js("document.querySelector('.akordiyonlar .bos-durum button').click()")
    web.detay_bekle("AnimeciX", 3)
    web.bekle(f"{web.satirlar('Anizle')}.length === 2")
    return cagrilar, oynatilan


def test_cok_kaynak_ayri_akordiyonlarda(web, iki_kaynak):
    cagrilar, _ = iki_kaynak
    assert sorted(cagrilar) == [("AnimeciX", "17"), ("Anizle", "cowboy-bebop")], \
        "bölümler eşleşen kaydın kendi kimliğiyle istenmeli"
    assert web.js("[...document.querySelectorAll('.akordiyon')].map(a => a.dataset.kaynak)") \
        == ["AnimeciX", "Anizle"]
    # İlk akordiyon açık, ötekiler kapalı (liste yine yüklü).
    assert web.js(f"{akordiyon('AnimeciX')}.classList.contains('acik')") is True
    assert web.js(f"{akordiyon('Anizle')}.classList.contains('acik')") is False
    assert "3 bölüm" in web.js(f"{akordiyon('AnimeciX')}.querySelector('.ak-sayi').textContent")
    # Eşleşmeyen kaynak arşiv etiketiyle anılıyor (anahtar kanonik kalıyor).
    assert f"Eşleşme bulunamayan: {ARSIV_ETIKETI}" in web.js(
        "document.querySelector('.kaynak-notu').textContent")


def test_satir_dugmesi_kendi_kaynagini_oynatiyor(web, iki_kaynak):
    _, oynatilan = iki_kaynak
    web.js(f"{akordiyon('Anizle')}.querySelector('.ak-baslik').click()")
    web.js(f"{web.satirlar('Anizle')}[1].querySelector('.oynat-dugme').click()")
    web.qtbot.waitUntil(lambda: bool(oynatilan), timeout=5000)
    assert oynatilan[0]["title"] == "Bölüm 2. Bölüm"
    assert oynatilan[0]["kaynak"] == "Anizle"


def test_secim_kaynak_basina_indirme_kendi_kaynagindan(main_window, web, iki_kaynak):
    indirilen: list = []
    main_window.detay._indir = indirilen.append
    web.js(arac("AnimeciX", "Tümü") + ".click()")
    web.js(f"{akordiyon('Anizle')}.querySelector('.ak-baslik').click()")
    web.js(f"{web.satirlar('Anizle')}[0].querySelector('input').click()")
    secim_bekle(web, 3, "AnimeciX")
    secim_bekle(web, 1, "Anizle")
    web.bekle(eylem("Seçilenleri İndir") + ".textContent.includes('(4)')")

    web.js(eylem("Seçilenleri İndir") + ".click()")

    web.qtbot.waitUntil(lambda: len(indirilen) == 4, timeout=5000)
    assert [(e["kaynak"], e["title"]) for e in indirilen] == [
        ("AnimeciX", "1. Bölüm"), ("AnimeciX", "2. Bölüm"), ("AnimeciX", "3. Bölüm"),
        ("Anizle", "Bölüm 1. Bölüm")]


def test_kismi_hata_yalnizca_kendi_akordiyonunda(main_window, web, sahte_bolumler,
                                                  monkeypatch):
    """Bir kaynak patlarsa öteki listelenir; hata o kaynağın akordiyonunda."""
    from turkanime_api.common.hatalar import KaynakYanitVermedi
    monkeypatch.setattr(uclar_detay, "arsiv_yerel_mi", lambda: False)
    monkeypatch.setattr(uclar_detay, "oynatilabilir_kaynaklar",
                        lambda: ["AnimeciX", "Anizle"])
    monkeypatch.setattr(uclar_detay, "kaynaklari_esle", lambda b, h, bagli=None, limit=None: (
        {"AnimeciX": "17", "Anizle": "cb"}, {}, []))
    sahte_bolumler({"AnimeciX": bolumler("cb", 3),
                    "Anizle": KaynakYanitVermedi("Anizle yanıt vermedi")})
    main_window._on_discover_selected({"title": {"romaji": "Cowboy Bebop"}})
    web.bekle("!!document.querySelector('.akordiyonlar .bos-durum button')", timeout=8000)
    web.js("document.querySelector('.akordiyonlar .bos-durum button').click()")

    web.detay_bekle("AnimeciX", 3)
    web.bekle(f"!!{akordiyon('Anizle')}.querySelector('.bolum-hata')")
    assert "yanıt vermedi" in web.js(f"{akordiyon('Anizle')}.querySelector('.bolum-hata').innerText")
    # Hatalı kaynak kendiliğinden açılıyor: kullanıcı sebebi görsün.
    assert web.js(f"{akordiyon('Anizle')}.classList.contains('acik')") is True
    assert not web.js(f"!!{akordiyon('AnimeciX')}.querySelector('.bolum-hata')")


def test_bagli_acilan_detay_esleme_aramiyor(main_window, web, sahte_bolumler, monkeypatch):
    """Arama sonucu kaynağa bağlı geliyor: tek kaynak için ağda eşleşme aranmaz."""
    aranan: list = []
    monkeypatch.setattr(uclar_detay, "kaynaklari_esle",
                        lambda *a, **k: aranan.append(a) or ({}, {}, []))
    sahte_bolumler({"TürkAnime": bolumler("naruto", 3)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 3)
    web.qtbot.wait(100)
    assert aranan == []
    assert ARSIV_ETIKETI in web.js(f"{akordiyon('TürkAnime')}.querySelector('.kaynak-hap').textContent")


# ── Sayfalama, filtre, "Tümü" ───────────────────────────────────────────────
def test_sayfalama_50ser_yukluyor(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 120)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 50)
    daha = f"{akordiyon('TürkAnime')}.querySelector('.daha-fazla')"
    assert web.js(f"{web.satirlar('TürkAnime')}.length") == 50
    assert web.js(f"{daha}.textContent") == "Daha Fazla Yükle (70 kaldı)"

    web.js(f"{daha}.click()")
    web.bekle(f"{web.satirlar('TürkAnime')}.length === 100")
    web.js(f"{daha}.click()")
    web.bekle(f"{web.satirlar('TürkAnime')}.length === 120")
    assert web.js(f"{daha}.hidden") is True


def test_tumu_filtrelenenleri_aliyor_filtre_kalkinca_secim_kaliyor(main_window, web,
                                                                    sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 25)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 25)
    ara = f"{akordiyon('TürkAnime')}.querySelector('.ak-arac input[type=search]')"
    web.js(f"var g = {ara}; g.value = '2'; g.dispatchEvent(new Event('input'))")
    web.bekle(f"{web.satirlar('TürkAnime')}.length === 8")

    web.js(arac("TürkAnime", "Tümü") + ".click()")
    secim_bekle(web, 8)

    web.js(f"var g = {ara}; g.value = ''; g.dispatchEvent(new Event('input'))")
    web.bekle(f"{web.satirlar('TürkAnime')}.length === 25")
    assert secim_sayisi(web) == "8 seçili"
    # Sıra 0'dan: "2." → 1, "12." → 11, "20.-25." → 19..24.
    assert secili_siralar(web) == [1, 11, 19, 20, 21, 22, 23, 24]


def test_tumu_cizilmemis_satirlari_da_kapsiyor(main_window, web, sahte_bolumler):
    indirilen: list = []
    main_window.detay._indir = indirilen.append
    sahte_bolumler({"TürkAnime": bolumler("naruto", 120)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 50)

    web.js(arac("TürkAnime", "Tümü") + ".click()")
    secim_bekle(web, 120)
    web.js(eylem("Seçilenleri İndir") + ".click()")

    web.qtbot.waitUntil(lambda: len(indirilen) == 120, timeout=5000)
    assert indirilen[-1]["title"] == "120. Bölüm"

    web.js(arac("TürkAnime", "Temizle") + ".click()")
    secim_bekle(web, 0)


def test_secim_yokken_eylemler_kapali(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 3)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 3)
    assert web.js(eylem("İlk Seçiliyi Oynat") + ".disabled") is True
    assert web.js(eylem("Seçilenleri İndir") + ".disabled") is True
    web.js(f"{web.satirlar('TürkAnime')}[2].querySelector('input').click()")
    web.bekle(eylem("İlk Seçiliyi Oynat") + ".disabled === false")


def test_yeni_anime_secimi_sifirliyor(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": lambda kimlik: bolumler(kimlik, 3)})
    ac(main_window, slug="naruto", baslik="Naruto")
    web.detay_bekle("TürkAnime", 3)
    web.js(arac("TürkAnime", "Tümü") + ".click()")
    secim_bekle(web, 3)

    ac(main_window, slug="bleach", baslik="Bleach")
    web.bekle("document.querySelector('.detay-bilgi h1') && "
              "document.querySelector('.detay-bilgi h1').textContent === 'Bleach'")
    web.detay_bekle("TürkAnime", 3)
    assert secim_sayisi(web) == ""
    assert web.js(eylem("Seçilenleri İndir") + ".disabled") is True


# ── Aralık, izlenmemiş/indirilmemiş, Shift+tık ──────────────────────────────
def aralik_yaz(web, metin, kaynak="TürkAnime"):
    web.js(f"var g = {akordiyon(kaynak)}.querySelector('.aralik-girdi'); g.value = "
           f"{json.dumps(metin)}; g.dispatchEvent(new KeyboardEvent('keydown', "
           "{key: 'Enter', bubbles: true}))")


def test_aralik_cizilmemis_satirlari_da_seciyor(main_window, web, sahte_bolumler):
    indirilen: list = []
    main_window.detay._indir = indirilen.append
    sahte_bolumler({"TürkAnime": bolumler("naruto", 120)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 50)

    aralik_yaz(web, "1-12")
    secim_bekle(web, 12)
    assert secili_siralar(web) == list(range(12))

    aralik_yaz(web, "100-")
    secim_bekle(web, 21)                    # seçim değişmeli, eklenmemeli
    assert secili_siralar(web) == []        # 100+ henüz çizilmedi
    daha = f"{akordiyon('TürkAnime')}.querySelector('.daha-fazla')"
    web.js(f"{daha}.click()")
    web.js(f"{daha}.click()")
    web.bekle(f"{web.satirlar('TürkAnime')}.length === 120")
    assert secili_siralar(web) == list(range(99, 120)), \
        "sonradan çizilen satır seçimi göstermeli"
    web.js(eylem("Seçilenleri İndir") + ".click()")
    web.qtbot.waitUntil(lambda: len(indirilen) == 21, timeout=5000)
    assert indirilen[0]["title"] == "100. Bölüm"


def test_anlasilamayan_aralik_uyariyor(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 12)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 12)
    aralik_yaz(web, "abc")
    web.bekle("[...document.querySelectorAll('.bildirim')]"
              ".some(b => b.textContent.includes('anlaşılamadı'))")
    assert secim_sayisi(web) == ""


def test_izlenmemisler_ve_indirilmemisler(main_window, web, sahte_bolumler, monkeypatch):
    from turkanime_api.gui.qt import prefs

    class Gecmis:
        def durum(self, bolum):
            no = int(bolum.slug.rsplit("-", 1)[1])
            return (no <= 2, 3 <= no <= 5)

    monkeypatch.setattr(prefs.Gecmis, "yukle", classmethod(lambda cls: Gecmis()))
    # Kuyruktaki bölüm "indirilmemiş" seçimine girmez.
    main_window.detay._kuyrukta = lambda e: e["obj"].slug == "naruto-40"
    sahte_bolumler({"TürkAnime": bolumler("naruto", 40)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 40)
    web.bekle(f"{web.satirlar('TürkAnime')}[39].innerText.includes('Kuyrukta')")

    web.js(arac("TürkAnime", "İzlenmemişler") + ".click()")
    secim_bekle(web, 38)
    assert secili_siralar(web) == list(range(2, 40))

    web.js(arac("TürkAnime", "İndirilmemişler") + ".click()")
    secim_bekle(web, 36)
    assert secili_siralar(web) == [0, 1] + list(range(5, 39))


def test_shift_tik_araligi_birakiyor_da(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 12)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 12)
    kutu = f"{web.satirlar('TürkAnime')}[%d].querySelector('input')"

    def tikla(i, shift=False):
        if shift:
            web.js(f"{kutu % i}.dispatchEvent(new MouseEvent('click', "
                   "{shiftKey: true, bubbles: true}))")
        else:
            web.js(f"{kutu % i}.click()")

    tikla(2)
    tikla(6, shift=True)
    secim_bekle(web, 5)
    assert secili_siralar(web) == [2, 3, 4, 5, 6]

    # Shift'le bırakmak aralığı bırakır.
    tikla(4)
    tikla(2, shift=True)
    secim_bekle(web, 2)
    assert secili_siralar(web) == [5, 6]
    # Shift'siz tık tek satır.
    tikla(9)
    secim_bekle(web, 3)
    assert secili_siralar(web) == [5, 6, 9]
