"""Web detay sayfası: künye, kaynak eşleştirme, akordiyonlar, bölüm eylemleri.

Bölümler `conftest.sahte_bolumler` ile sahte (ağ yok); eşleştirme motoru
`arama_motoru` üzerinden sahte `SearchEngine`. Sayfa gerçek QtWebEngine'de.
"""
from __future__ import annotations

import pytest

from turkanime_api.common import kutuphane
from turkanime_api.gui.web import uclar_detay
from turkanime_api.gui.web.uclar_detay import (
    DetayUclari, EskiIstek, dis_baglanti, kaynaklari_esle, kunye_verisi,
)


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


@pytest.fixture
def izole_ev(tmp_path, monkeypatch):
    yol = tmp_path / "kutuphane.json"
    monkeypatch.setattr(kutuphane, "kutuphane_yolu", lambda: str(yol))
    return tmp_path


class _Geri:
    def __init__(self):
        self.oynatilan = []
        self.indirilen = []
        self.kuyruk = set()

    def uclar(self):
        return DetayUclari(None, oynat=self.oynatilan.append,
                           indir=lambda e: (self.indirilen.append(e),
                                            self.kuyruk.add(id(e))),
                           kuyrukta=lambda e: id(e) in self.kuyruk)


# ── Qt'siz: künye ve eşleştirme ──────────────────────────────────────────────
def test_kunye_verisi_ve_dis_baglanti():
    k = kunye_verisi({
        "title": {"romaji": "Sousou no Frieren", "english": "Frieren", "native": "葬送のフリーレン"},
        "averageScore": 91, "popularity": 483340, "episodes": 28, "status": "FINISHED",
        "genres": ["Adventure", "Drama"], "description": "<p>Büyücü<br>elf</p>",
        "coverImage": {"large": "https://k/f.jpg"}, "siteUrl": "https://anilist.co/anime/154587"})
    assert k["baslik"] == "Sousou no Frieren"
    assert k["alt_baslik"] == "Frieren · 葬送のフリーレン"
    assert k["puan"] == 91 and k["bolum_sayisi"] == 28 and k["durum"] == "Tamamlandı"
    assert k["turler"] == ["Macera", "Dram"]
    assert "Büyücü" in k["ozet"] and "<" not in k["ozet"]
    assert k["dis"] == {"ad": "AniList", "adres": "https://anilist.co/anime/154587"}
    # AniList'te popülerlik kişi sayısı, MyAnimeList'te sıra.
    assert k["populerlik_sira"] is False
    assert kunye_verisi({"mal_id": 5, "popularity": 12})["populerlik_sira"] is True
    assert dis_baglanti({"mal_id": 5114}) == {
        "ad": "MyAnimeList", "adres": "https://myanimelist.net/anime/5114"}
    assert dis_baglanti({"siteUrl": "javascript:alert(1)"}) is None


def _sahte_motor(monkeypatch, tablo, cagrilar=None):
    """``arama_motoru(kaynaklar)`` → yalnızca istenen kaynakların sonuçları."""
    import turkanime_api.common.adapters as adapters_mod

    class Motor:
        def __init__(self, kaynaklar):
            self.kaynaklar = list(kaynaklar)

        def search_all_sources_rich(self, sorgu, limit_per_source=10):
            if cagrilar is not None:
                cagrilar.append((sorgu, tuple(self.kaynaklar)))
            return adapters_mod.AramaSonuclari(
                {k: tablo.get(k, {}).get(sorgu, []) for k in self.kaynaklar})

    monkeypatch.setattr(adapters_mod, "arama_motoru", lambda kaynaklar=None: Motor(kaynaklar or []))


def test_kaynaklari_esle_esik_ve_varyant(monkeypatch):
    cagrilar = []
    _sahte_motor(monkeypatch, {
        "AnimeciX": {"Sousou no Frieren": [{"slug": "1", "title": "Sousou no Frieren"}]},
        "Anizle": {"Sousou no Frieren": [{"slug": "koisuru", "title": "Koisuru Frieren Fan"}],
                   "Frieren": [{"slug": "frieren", "title": "Frieren"}]},
    }, cagrilar)
    baglar, eslesen, eslesmeyen = kaynaklari_esle(
        ["Sousou no Frieren", "Frieren"], ["AnimeciX", "Anizle", "OpenAnime"])
    assert baglar == {"AnimeciX": "1", "Anizle": "frieren"}
    assert eslesen["Anizle"] == "Frieren"
    assert eslesmeyen == ["OpenAnime"]
    # İkinci tur yalnızca cevap verip eşleşmeyenlere (AnimeciX bağlandı).
    assert cagrilar[1] == ("Frieren", ("Anizle", "OpenAnime"))


# ── Qt'siz: oturum uçları ────────────────────────────────────────────────────
def test_oturumlar_ve_eski_istek(sahte_bolumler, izole_ev):
    geri = _Geri()
    uclar = geri.uclar()
    sahte_bolumler({"AnimeciX": bolumler("naruto", 3)})
    rid = uclar.ac_sonuc("AnimeciX", "1234", "Naruto", {"image": "https://k/n.jpg"})
    assert uclar.oturum.baglar == {"AnimeciX": "1234"}
    sonuc = uclar.bolumler(rid, "AnimeciX")
    assert [b["no"] for b in sonuc["bolumler"]] == [1, 2, 3]
    # Kitaplık anahtarı kaynağın KENDİ kimliği (başlık slug'ı değil).
    entry = uclar.oturum.bolumler["AnimeciX"][0]
    assert (entry["kaynak"], entry["kimlik"], entry["kapak"]) == (
        "AnimeciX", "1234", "https://k/n.jpg")

    uclar.oynat(rid, "AnimeciX", 1)
    assert geri.oynatilan[0]["title"] == "2. Bölüm"
    assert uclar.indir(rid, [["AnimeciX", 0], ["AnimeciX", 2]]) == {"yeni": 2, "zaten": 0}
    assert uclar.indir(rid, [["AnimeciX", 0]]) == {"yeni": 0, "zaten": 1}

    yeni = uclar.ac_kesif({"title": {"romaji": "Bleach"}})
    assert yeni != rid and uclar.oturum.baglar == {}
    with pytest.raises(EskiIstek):
        uclar.bolumler(rid, "AnimeciX")
    with pytest.raises(ValueError):
        uclar.bolumler(yeni, "AnimeciX")          # bağsız


def test_anilist_sonucu_bagsiz_aciliyor():
    uclar = _Geri().uclar()
    uclar.ac_sonuc("AniList", "154587", "Sousou no Frieren")
    assert uclar.oturum.baglar == {}
    with pytest.raises(ValueError):
        uclar.elle_eslestir(uclar.oturum.rid, "AniList", "1", "x")


def test_elle_secim_otomatigi_eziyor_ve_eski_adi_dusuruyor(monkeypatch):
    uclar = _Geri().uclar()
    monkeypatch.setattr(uclar_detay, "oynatilabilir_kaynaklar", lambda: ["TürkAnime", "AnimeciX"])
    monkeypatch.setattr(uclar_detay, "kaynaklari_esle",
                        lambda b, h, bagli=None, limit=None: (
                            {k: "oto-" + k for k in h}, {k: "Oto" for k in h}, []))
    import turkanime_api.gui.web.eslestirme as detail_mod
    monkeypatch.setattr(detail_mod, "save_match", lambda *a: True)
    rid = uclar.ac_sonuc("AnimeDepo", "eski", "Naruto")      # eski ad
    uclar.elle_eslestir(rid, "TürkAnime", "naruto", "Naruto")
    assert uclar.oturum.baglar == {"TürkAnime": "naruto"}   # eski adlı bağ gitti
    sonuc = uclar.eslestir(rid, None)
    assert uclar.oturum.baglar == {"TürkAnime": "naruto", "AnimeciX": "oto-AnimeciX"}
    assert sonuc["yeni"] == ["AnimeciX"]
    ciktilar = {k["ad"]: k for k in sonuc["kaynaklar"]}
    assert ciktilar["TürkAnime"]["elle"] and not ciktilar["TürkAnime"]["eslesme"]
    assert ciktilar["AnimeciX"]["eslesme"] == "Oto"


def test_devam_hedefi_yarim_bolum_ve_siradaki(sahte_bolumler, izole_ev, monkeypatch):
    from turkanime_api.gui.qt import prefs

    class Gecmis:
        def durum(self, bolum):
            return (bolum.slug in ("naruto-1", "naruto-2"), False)

    monkeypatch.setattr(prefs.Gecmis, "yukle", classmethod(lambda cls: Gecmis()))
    uclar = _Geri().uclar()
    sahte_bolumler({"TürkAnime": bolumler("naruto", 5)})
    rid = uclar.ac_sonuc("TürkAnime", "naruto", "Naruto")
    sonuc = uclar.bolumler(rid, "TürkAnime")
    assert sonuc["devam"] == {"kaynak": "TürkAnime", "sira": 2, "metin": "Sıradaki: 3. Bölüm"}
    assert [b["izlendi"] for b in sonuc["bolumler"]] == [True, True, False, False, False]
    # Yarım bırakılan bölüm önce gelir.
    kutuphane.konum_kaydet("TürkAnime", "naruto", "naruto-4", 734, 1400)
    durum = uclar.bolum_durumlari(rid)
    assert durum["devam"]["sira"] == 3
    assert durum["devam"]["metin"] == "Devam et: 4. Bölüm (12:14)"
    assert durum["durumlar"]["TürkAnime"][3]["konum"] == "12:14"


# ── Sayfa (QtWebEngine) ──────────────────────────────────────────────────────
def ac(main_window, kaynak="TürkAnime", slug="naruto", baslik="Naruto", kayit=None):
    main_window._on_anime_selected(kaynak, slug, baslik, kayit)


def test_arama_sonucu_detayi_bolumlerle_aciyor(main_window, web, sahte_bolumler):
    cagrilar = sahte_bolumler({"TürkAnime": bolumler("naruto", 60)})
    main_window.show_page("search")
    ac(main_window, kayit={"image": "https://k/n.jpg"})
    web.detay_bekle("TürkAnime", 50)
    assert cagrilar == [("TürkAnime", "naruto")]
    assert web.js("document.querySelector('.detay-bilgi h1').textContent") == "Naruto"
    # 60 bölüm: 50'si çizili, "Daha Fazla Yükle (10 kaldı)".
    assert web.js(f"{web.satirlar('TürkAnime')}.length") == 50
    assert "10 kaldı" in web.js("document.querySelector('.daha-fazla').textContent")
    web.js("document.querySelector('.daha-fazla').click()")
    web.bekle(f"{web.satirlar('TürkAnime')}.length === 60")
    # İlk akordiyon açık, başlıkta sayı ve kaynak etiketi.
    assert web.js("document.querySelector('.akordiyon').classList.contains('acik')")
    assert "60 bölüm" in web.js("document.querySelector('.ak-baslik').innerText")
    assert "TürkAnime (arşiv)" in web.js("document.querySelector('.ak-baslik').innerText")
    # Geri → arama.
    web.js("document.querySelector('.geri-dugme').click()")
    web.qtbot.waitUntil(lambda: main_window._current_page == "search", timeout=5000)


def test_oynat_ve_toplu_indir(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 12)})
    oynatilan, indirilen = [], []
    main_window.detay._oynat = oynatilan.append
    main_window.detay._indir = indirilen.append
    ac(main_window)
    web.detay_bekle("TürkAnime", 12)

    web.js(f"{web.satirlar('TürkAnime')}[4].querySelector('.oynat-dugme').click()")
    web.qtbot.waitUntil(lambda: len(oynatilan) == 1, timeout=5000)
    assert oynatilan[0]["title"] == "5. Bölüm"
    assert oynatilan[0]["kimlik"] == "naruto"

    # Aralıkla seç → "Seçilenleri İndir (3)".
    web.js("var a = document.querySelector('.aralik-girdi'); a.value = '2-3, 10';"
           "a.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}))")
    web.bekle("document.querySelector('.secim-sayi').textContent === '3 seçili'")
    web.js("Array.from(document.querySelectorAll('.eylem-cubugu button'))"
           ".find(b => b.textContent.includes('Seçilenleri İndir')).click()")
    web.qtbot.waitUntil(lambda: len(indirilen) == 3, timeout=5000)
    assert [e["title"] for e in indirilen] == ["2. Bölüm", "3. Bölüm", "10. Bölüm"]


def test_shift_ile_aralik_secimi(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 8)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 8)
    kutu = f"{web.satirlar('TürkAnime')}[%d].querySelector('input')"
    web.js(f"{kutu % 1}.click()")
    web.js(f"{kutu % 5}.dispatchEvent(new MouseEvent('click', {{shiftKey: true, bubbles: true}}))")
    web.bekle("document.querySelector('.secim-sayi').textContent === '5 seçili'")


def test_bolum_filtresi_numara_oneki(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": bolumler("naruto", 25)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 25)
    web.js("var g = document.querySelector('.ak-arac input[type=search]'); g.value = '2';"
           "g.dispatchEvent(new Event('input'))")
    # Başlıkta geçen ("12. Bölüm") ya da numarası "2" ile başlayan: 2, 12, 20-25.
    web.bekle(f"{web.satirlar('TürkAnime')}.length === 8")


def test_kesif_karti_yerel_arsivde_kendiliginden_eslesiyor(main_window, web,
                                                            sahte_bolumler, monkeypatch):
    cagrilar = sahte_bolumler({"TürkAnime": bolumler("frieren", 3)})
    monkeypatch.setattr(uclar_detay, "arsiv_yerel_mi", lambda: True)
    istenen = []

    def esle(basliklar, hedefler, bagli=None, limit=None):
        istenen.append(list(hedefler))
        return {"TürkAnime": "frieren"}, {"TürkAnime": "Sousou no Frieren"}, []

    monkeypatch.setattr(uclar_detay, "kaynaklari_esle", esle)
    main_window._on_discover_selected({"title": {"romaji": "Sousou no Frieren"},
                                       "averageScore": 91})
    web.detay_bekle("TürkAnime", 3)
    assert istenen == [["TürkAnime"]]                  # yalnızca yerel arşiv
    assert cagrilar == [("TürkAnime", "frieren")]
    assert "Sousou no Frieren" in web.js("document.querySelector('.ak-eslesme').textContent")
    assert "91%" in web.js("document.querySelector('.istatistikler').innerText")


def test_eslesme_yoksa_elle_secim_penceresi(main_window, web, sahte_bolumler,
                                           sahte_arama, monkeypatch):
    sahte_bolumler({"AnimeciX": bolumler("bleach", 2)})
    monkeypatch.setattr(uclar_detay, "arsiv_yerel_mi", lambda: False)
    monkeypatch.setattr(uclar_detay, "kaynaklari_esle",
                        lambda b, h, bagli=None, limit=None: ({}, {}, list(h)))
    import turkanime_api.gui.web.eslestirme as detail_mod
    kaydedilen = []
    monkeypatch.setattr(detail_mod, "save_match", lambda *a: kaydedilen.append(a) or True)
    sahte_arama(sonuclar={"AnimeciX": [{"slug": "77", "title": "Bleach"}],
                          "AniList": [{"slug": "1", "title": "Bleach"}]})

    main_window._on_discover_selected({"title": {"romaji": "Bleach"}})
    web.bekle("TA.aktif === 'detail' && !!document.querySelector('.akordiyonlar .bos-durum')")
    assert "bağlı değil" in web.js("document.querySelector('.akordiyonlar').innerText")
    web.js("document.querySelector('.akordiyonlar .bos-durum button').click()")
    web.bekle("document.querySelector('.akordiyonlar').innerText.includes('Otomatik eşleşme bulunamadı')")
    web.js("document.querySelector('.akordiyonlar .bos-durum button').click()")
    # Pencere: AniList (yalnızca bilgi) listelenmez.
    web.bekle("document.querySelectorAll('.modal-sonuc').length === 1")
    web.js("document.querySelector('.modal-sonuc').click()")
    web.detay_bekle("AnimeciX", 2)
    assert not web.js("!!document.querySelector('.modal-ortu')")
    assert main_window.detay.oturum.elle == {"AnimeciX": "77"}
    web.qtbot.waitUntil(lambda: kaydedilen == [("AnimeciX", "77", "Bleach")], timeout=5000)


def test_bolum_hatasi_akordiyonda_tekrar_dene(main_window, web, sahte_bolumler):
    from turkanime_api.common.hatalar import KaynakYanitVermedi
    sahte_bolumler({"AnimeciX": KaynakYanitVermedi("AnimeciX yanıt vermedi")})
    ac(main_window, "AnimeciX", "1", "Naruto")
    web.bekle("!!document.querySelector('.bolum-hata')")
    assert "yanıt vermedi" in web.js("document.querySelector('.bolum-hata').innerText")
    sahte_bolumler({"AnimeciX": bolumler("naruto", 2)})
    web.js("document.querySelector('.bolum-hata button').click()")
    web.detay_bekle("AnimeciX", 2)


def test_gecmis_olayi_rozetleri_tazeliyor(main_window, web, sahte_bolumler, monkeypatch):
    from turkanime_api.gui.qt import prefs
    izlenen = set()

    class Gecmis:
        def durum(self, bolum):
            return (bolum.slug in izlenen, False)

    monkeypatch.setattr(prefs.Gecmis, "yukle", classmethod(lambda cls: Gecmis()))
    sahte_bolumler({"TürkAnime": bolumler("naruto", 3)})
    ac(main_window)
    web.detay_bekle("TürkAnime", 3)
    assert web.js("document.querySelectorAll('.bolum-satiri.izlendi').length") == 0
    izlenen.add("naruto-1")
    main_window._refresh_episode_history()
    web.bekle("document.querySelectorAll('.bolum-satiri.izlendi').length === 1")
    web.bekle("document.querySelector('.eylem-cubugu').innerText.includes('Sıradaki: 2. Bölüm')")


def test_kitapliga_ekle(main_window, web, sahte_bolumler, izole_ev):
    sahte_bolumler({"TürkAnime": bolumler("07-ghost", 1)})
    ac(main_window, "TürkAnime", "07-ghost", "07-Ghost", {"image": "http://k/07.jpg"})
    web.detay_bekle("TürkAnime", 1)
    assert "Kitaplığa Ekle" in web.js("document.querySelector('.detay-eylemler .dugme.marka').textContent")
    web.js("document.querySelector('.detay-eylemler .dugme.marka').click()")
    web.qtbot.waitUntil(lambda: kutuphane.favori_mi("TürkAnime", "07-ghost"), timeout=5000)
    web.bekle("document.querySelector('.detay-eylemler .dugme.marka').textContent.includes('Kitaplıkta')")
    seri = kutuphane.favoriler()[0]
    assert (seri["baslik"], seri["kapak"]) == ("07-Ghost", "http://k/07.jpg")


def test_yeni_anime_eskisinin_gec_bolumlerini_ezmiyor(main_window, web, sahte_bolumler):
    import threading
    birak = threading.Event()

    def yavas(kimlik):
        birak.wait(10)
        return bolumler("eski", 4)

    sahte_bolumler({"TürkAnime": yavas, "AnimeciX": bolumler("yeni", 2)})
    ac(main_window, "TürkAnime", "eski", "Eski")
    ac(main_window, "AnimeciX", "yeni", "Yeni")
    web.detay_bekle("AnimeciX", 2)
    birak.set()
    web.qtbot.wait(300)
    assert web.js("document.querySelectorAll('.akordiyon').length") == 1
    assert web.js("document.querySelector('.detay-bilgi h1').textContent") == "Yeni"


# ── Bölüm yükleme hataları: sebep, "bulunamadı" değil ────────────────────────
def test_desteklenmeyen_kaynak_mesaji_oldugu_gibi(main_window, web, sahte_bolumler):
    """Kaynağın kendi cümlesi "beklenmeyen hata" önekiyle bozulmadan gelir."""
    from turkanime_api.gui.qt.sources_bridge import UnsupportedSource
    sahte_bolumler({"AnimeciX": UnsupportedSource(
        "AnimeciX sayısal kimlik bekliyor, 'cowboy-bebop' geçersiz.")})
    ac(main_window, "AnimeciX", "cowboy-bebop", "Cowboy Bebop")
    web.bekle("!!document.querySelector('.bolum-hata')")
    metin = web.js("document.querySelector('.bolum-hata').innerText")
    assert metin.startswith("AnimeciX sayısal kimlik bekliyor")
    assert "beklenmeyen" not in metin


def test_bos_liste_bulunamadi_diyor(main_window, web, sahte_bolumler):
    sahte_bolumler({"TürkAnime": []})
    ac(main_window, "TürkAnime", "yok", "Yok")
    web.bekle("!!document.querySelector('.bolum-bos')")
    assert "bölüm bulunamadı" in web.js("document.querySelector('.bolum-bos').innerText")


def test_okunamayan_arsiv_sebebini_soyluyor(main_window, web, tmp_path, monkeypatch):
    """ESKİ HATA: arşive ulaşılamayınca bölüm okuyucusu hatayı boş listeye
    çeviriyor, sayfa "bölüm bulunamadı" diyordu. Gerçek köprü + gerçek arşiv
    istemcisi: ağ conftest'te kapalı, disk önbelleğinde yalnızca dizin var."""
    import json
    from turkanime_api.sources import animedepo

    onbellek = tmp_path / "onbellek"
    onbellek.mkdir()
    (onbellek / "dizin.json").write_text(json.dumps(
        {"index": {"N": {"naruto": {"title": "Naruto"}}}}), "utf-8")
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: onbellek)
    animedepo.sifirla()
    assert animedepo.search_animedepo("naruto") == [("naruto", "Naruto")]

    ac(main_window, "TürkAnime", "naruto", "Naruto")
    web.bekle("!!document.querySelector('.bolum-hata')", timeout=10000)
    metin = web.js("document.querySelector('.bolum-hata').innerText")
    assert "okunamadı" in metin and "bulunamadı" not in metin


def test_arama_sonucu_detayi_bagli_aciyor_ve_geri(main_window, web, sahte_bolumler):
    """Arama sonucu detayı kaynağa bağlı açar; "Geri" aramaya döner."""
    cagrilar = sahte_bolumler({"TürkAnime": bolumler("cowboy-bebop", 2)})
    main_window.show_page("trending")
    main_window._web_ac("sonuc", {
        "kaynak": "TürkAnime", "slug": "cowboy-bebop", "baslik": "Cowboy Bebop",
        "kayit": {"slug": "cowboy-bebop", "title": "Cowboy Bebop", "image": None}})
    assert main_window._current_page == "detail"
    web.detay_bekle("TürkAnime", 2)
    assert web.js("document.querySelector('.detay-bilgi h1').textContent") == "Cowboy Bebop"
    assert main_window.detay.oturum.baglar == {"TürkAnime": "cowboy-bebop"}
    assert cagrilar == [("TürkAnime", "cowboy-bebop")]
    main_window._web_ac("geri", {})
    assert main_window._current_page == "trending"
    assert main_window.web.rota == "trending"
