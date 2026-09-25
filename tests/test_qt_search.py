"""Arama sayfası — gövdenin GERÇEKTEN koştuğu testler.

Bu dosya yokken sayfanın tek dolaylı kapsamı `test_qt_app.py`'deki iki testti ve
ikisi de `start_search`'ü sahteliyordu; yani `_on_results` hiç çalışmıyordu.
Sonuç: slot'un imzası ile gövdesi aylarca birbirini yalanladı (imza
`Tuple[str, str]`, gövde `item.get("slug")`) ve hiçbir test bunu görmedi.

Buradaki testler `start_search`'ü SAHTELEMEZ; yalnızca `SearchEngine` sahtelenir,
böylece ağa çıkılmadan gerçek sinyal/slot yolu koşar.
"""
from __future__ import annotations

import threading
from typing import get_type_hints

import pytest
from PySide6.QtCore import QBuffer, QByteArray, Qt

from turkanime_api.gui.qt.pages.search import LIMIT_PER_SOURCE, SearchPage


# ── Yardımcılar ─────────────────────────────────────────────────────────────
def kayit(slug: str, title: str, image: str | None = None) -> dict:
    """`search_all_sources_rich`'in tek kayıt biçimi."""
    return {"slug": slug, "title": title, "image": image}


@pytest.fixture
def page(qtbot):
    p = SearchPage()
    qtbot.addWidget(p)
    return p


@pytest.fixture
def sahte_motor(monkeypatch):
    """`SearchEngine`'i sahtele; sorgu/limit çağrılarını kaydet.

    `_do_search` motoru fonksiyon içinde import ettiği için modül özniteliğini
    değiştirmek yeterli.
    """
    import turkanime_api.common.adapters as adapters_mod

    cagrilar: list = []

    def _kur(sonuc, gecikme: threading.Event | None = None):
        class SahteMotor:
            def search_all_sources_rich(self, query, limit_per_source=10):
                cagrilar.append((query, limit_per_source))
                if gecikme is not None:
                    gecikme.wait(10)
                if callable(sonuc):
                    return sonuc(query)
                return sonuc

        monkeypatch.setattr(adapters_mod, "SearchEngine", SahteMotor)
        return cagrilar

    return _kur


def png_baytlari() -> bytes:
    """Diskten/ağdan bir şey almadan geçerli PNG üret."""
    from PySide6.QtGui import QPixmap

    pix = QPixmap(6, 6)
    pix.fill()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    assert pix.save(buf, "PNG")
    return bytes(ba)


# ── (a) İmza / gövde sözleşmesi ─────────────────────────────────────────────
def test_slot_imzasi_ureticinin_donus_tipiyle_ayni():
    """ESKİ HATA: imza `Dict[str, List[Tuple[str, str]]]` diyordu.

    Gerçek çağrıcı (`SearchEngine.search_all_sources_rich`) sözlük listesi
    döndürüyor ve gövde de `item.get(...)` ile sözlük okuyor — yani yanlış olan
    GÖVDE değil İMZAydı. Bu test iki tarafı birbirine çiviler: üretici sözleşme
    değiştirirse ya da imza yine eskiye kaydırılırsa kırmızı olur.
    """
    from turkanime_api.common.adapters import SearchEngine

    uretici = get_type_hints(SearchEngine.search_all_sources_rich)["return"]
    tuketici = get_type_hints(SearchPage._on_results)["results"]
    assert tuketici == uretici, (
        f"slot {tuketici} bekliyor ama üretici {uretici} döndürüyor")


def test_gercek_motor_ciktisi_dogrudan_islenebiliyor(page):
    """ESKİ HATA: sözleşme yalnızca elle yazılmış sahtelerle sınanıyordu.

    Burada sahte olan yalnızca ADAPTERLER; sözlüğü üreten kod gerçek
    `SearchEngine.search_all_sources_rich`. Böylece "gövde adapter biçimini
    okuyabiliyor mu?" sorusu tahminle değil ölçümle yanıtlanır.
    """
    from turkanime_api.common.adapters import SearchEngine

    class ZenginAdapter:                      # AniList gibi: görsel de verir
        def search_rich(self, query, limit=10):
            return [kayit("101", "Cowboy Bebop", "http://kapak/1.jpg")]

    class SadeAdapter:                        # yalnızca (slug, title) verir
        def search_anime(self, query, limit=10):
            return [("cowboy-bebop", "Cowboy Bebop TR")]

    motor = SearchEngine.__new__(SearchEngine)
    motor.adapters = {"AniList": ZenginAdapter(), "TurkAnime": SadeAdapter()}
    sonuc = motor.search_all_sources_rich("cowboy")

    page._on_results(sonuc)

    # Gruplar kayıt sırasında, metadata kaynağı (AniList) EN SONDA.
    assert [c.lblTitle.text() for c in page.cards()] == [
        "Cowboy Bebop TR", "Cowboy Bebop"]
    assert page.cards()[1].payload == ("AniList", "101", "Cowboy Bebop")
    assert page.cards()[0].payload == ("TurkAnime", "cowboy-bebop",
                                       "Cowboy Bebop TR")


# ── (b) Gövde: arama akışı uçtan uca ────────────────────────────────────────
def test_sonuc_gelince_kart_uretiliyor(qtbot, page, sahte_motor):
    """ESKİ HATA: `start_search` her testte sahtelendiği için bu yol hiç koşmadı."""
    cagrilar = sahte_motor({
        "TurkAnime": [kayit("naruto", "Naruto"), kayit("naruto-s", "Naruto Shippuden")],
        "AnimeDepo": [kayit("naruto-d", "Naruto Depo")],
    })

    page.start_search("naruto")

    qtbot.waitUntil(lambda: len(page.cards()) == 3, timeout=5000)
    # Kaynaklar alfabetik: AnimeDepo önce gelir.
    assert [c.lblSource.text() for c in page.cards()] == [
        "AnimeDepo", "TurkAnime", "TurkAnime"]
    assert page.results.grid.count() == 3
    assert "3 sonuç" in page.lblStatus.text()
    assert cagrilar == [("naruto", LIMIT_PER_SOURCE)]


def test_sorgu_kirpiliyor_ve_baslikta_gosteriliyor(qtbot, page, sahte_motor):
    cagrilar = sahte_motor({"TurkAnime": [kayit("one-piece", "One Piece")]})

    page.start_search("  one piece  ")

    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)
    assert cagrilar == [("one piece", LIMIT_PER_SOURCE)]
    assert "one piece" in page.lblTitle.text()


def test_bos_sorgu_aramayi_baslatmiyor(page, sahte_motor):
    cagrilar = sahte_motor({"TurkAnime": [kayit("x", "X")]})
    page.start_search("   ")
    assert cagrilar == []
    assert page._busy is False


def test_bos_sonucta_bulunamadi_yaziliyor(qtbot, page, sahte_motor):
    """ESKİ HATA: sonuç yokken kullanıcı boş ızgaraya bakıp kalıyordu."""
    sahte_motor({"TurkAnime": [], "AnimeDepo": []})

    page.start_search("yokboyleanime")

    qtbot.waitUntil(lambda: "bulunamadı" in page.lblStatus.text(), timeout=5000)
    assert page.cards() == []
    assert "yokboyleanime" in page.lblStatus.text()
    assert page._busy is False, "sonuç boş da olsa arama bitmiş sayılmalı"


def test_bos_sonuc_onceki_kartlari_temizliyor(page):
    """ESKİ HATA: "sonuç bulunamadı" yazarken altta eski sonuçlar duruyordu."""
    page._on_results({"TurkAnime": [kayit("naruto", "Naruto")]})
    assert len(page.cards()) == 1

    page._on_results({"TurkAnime": []})

    assert page.cards() == []
    assert page.results.grid.count() == 0
    assert "bulunamadı" in page.lblStatus.text()


def test_aranamayan_kaynak_sebebiyle_soyleniyor(qtbot, page, monkeypatch):
    """ESKİ HATA: TürkAnime arşivi okunamadığında (aynalar kapalı, önbellek
    boş — paketli uygulamada ağ gidince olağan durum) arama "sonuç
    bulunamadı" diyordu; oysa arama hiç yapılamamıştı. Gerçek motor, gerçek
    TürkAnime adaptörü: ağ conftest'te kesik, yerel arşiv yok."""
    import turkanime_api.common.adapters as adapters_mod
    from turkanime_api.sources import animedepo

    animedepo.sifirla()
    asil = adapters_mod.SearchEngine

    class YalnizArsiv(asil):
        def __init__(self):
            super().__init__()
            self.adapters = {"TürkAnime": self.adapters["TürkAnime"]}

    monkeypatch.setattr(adapters_mod, "SearchEngine", YalnizArsiv)

    page.start_search("naruto")

    qtbot.waitUntil(lambda: not page._busy, timeout=5000)
    metin = page.lblStatus.text()
    assert "bulunamadı" in metin and "TürkAnime (arşiv)" in metin
    assert "okunamadı" in metin and "uzak aynalar yanıt vermedi" in metin


def test_sonuc_varken_aranamayan_kaynak_adiyla_belirtiliyor(page):
    from turkanime_api.common.adapters import AramaSonuclari

    page._on_results(AramaSonuclari(
        {"AniList": [kayit("1", "Naruto")], "TürkAnime": []},
        hatalar={"TürkAnime": "TürkAnime arşivi okunamadı: ..."}))

    assert len(page.cards()) == 1
    metin = page.lblStatus.text()
    assert "1 sonuç" in metin and "aranamayan: TürkAnime (arşiv)" in metin


# ── (b) Bozuk kayıtlar ──────────────────────────────────────────────────────
def test_slugsuz_kayit_cokme_yapmiyor_ve_atiliyor(page):
    """ESKİ HATA: slug'sız kayıt boş payload'lu ölü bir kart üretiyordu.

    Kart tıklanabiliyor ama `anime_selected` boş slug taşıdığı için bölüm
    sayfası sessizce hiçbir şey bulamıyordu. Kaydı hiç göstermemek dürüst.
    """
    page._on_results({"TurkAnime": [
        kayit("naruto", "Naruto"),
        {"title": "Slug'sız", "image": None},      # slug yok
        {"slug": "", "title": "Boş slug"},         # slug boş
    ]})

    assert [c.lblTitle.text() for c in page.cards()] == ["Naruto"]
    assert all(c.payload[1] for c in page.cards()), "boş slug'lı kart kaldı"


def test_sozluk_olmayan_kayit_slotu_dusurmuyor(page):
    """ESKİ HATA: gövde `item.get` çağırıyor; demet/None gelirse AttributeError.

    Slot içindeki istisna Qt sinyal yolunda yutulup arayüzü kalıcı olarak
    "aranıyor…" durumunda bırakıyordu (`_busy` sıfırlanmadan).
    """
    page._on_results({"TurkAnime": [
        ("naruto", "Naruto"),                      # eski (slug, title) biçimi
        None,
        kayit("bleach", "Bleach"),
    ]})

    assert [c.lblTitle.text() for c in page.cards()] == ["Bleach"]
    assert page._busy is False


def test_kaynak_dokumu_gosterilen_kart_sayisini_soyluyor(page):
    """ESKİ HATA: sayaç ham kayıtları sayıyordu; atılanlarla toplam tutmuyordu."""
    page._on_results({"TurkAnime": [
        kayit("a", "A"), {"title": "slugsuz"}, kayit("b", "B"),
    ]})

    assert "2 sonuç" in page.lblStatus.text()
    assert "TurkAnime: 2" in page.lblStatus.text()


def test_sozluk_olmayan_sonuc_hata_olarak_bildiriliyor(page):
    page._on_results(["beklenmedik"])
    assert "Beklenmeyen" in page.lblStatus.text()
    assert page._busy is False


def test_bos_kaynak_dokumde_gorunmuyor(page):
    page._on_results({"AnimeDepo": [], "TurkAnime": [kayit("a", "A")]})
    assert "AnimeDepo" not in page.lblStatus.text()
    assert "TurkAnime: 1" in page.lblStatus.text()


# ── Tıklama, hata ve eşzamanlılık ───────────────────────────────────────────
def test_karta_tiklayinca_secim_sinyali_yayiliyor(qtbot, page):
    """ESKİ HATA: kart payload'ı üçlü; sinyal yolu hiç sınanmamıştı."""
    page._on_results({"TurkAnime": [kayit("naruto", "Naruto")]})
    page.show()
    qtbot.waitExposed(page)

    with qtbot.waitSignal(page.anime_selected, timeout=2000) as sinyal:
        qtbot.mouseClick(page.cards()[0], Qt.MouseButton.LeftButton)

    assert sinyal.args[:3] == ["TurkAnime", "naruto", "Naruto"]
    # Dördüncü alan arama kaydının kendisi: kapak detay sayfasına taşınsın.
    assert sinyal.args[3] == {"slug": "naruto", "title": "Naruto", "image": None}


def test_payloadsiz_tiklama_sinyal_yaymiyor(page):
    yayilan: list = []
    page.anime_selected.connect(lambda *a: yayilan.append(a))
    page._on_card_clicked(None)
    assert yayilan == []


def test_arama_hatasi_bildiriliyor_ve_busy_sifirlaniyor(qtbot, page, monkeypatch):
    """ESKİ HATA: hata sonrası `_busy` açık kalırsa sayfa bir daha aramaz."""
    import turkanime_api.common.adapters as adapters_mod

    class PatlayanMotor:
        def search_all_sources_rich(self, query, limit_per_source=10):
            raise RuntimeError("kaynaklar kapalı")

    monkeypatch.setattr(adapters_mod, "SearchEngine", PatlayanMotor)

    page.start_search("naruto")

    qtbot.waitUntil(lambda: "hata" in page.lblStatus.text().lower(), timeout=5000)
    assert "kaynaklar kapalı" in page.lblStatus.text()
    assert page._busy is False


def test_yeni_sorgu_surenin_yerini_aliyor(qtbot, page, sahte_motor):
    """ESKİ DAVRANIŞ: süren arama varken ikinci sorgu REDDEDİLİYORDU; bir
    yazım hatası en yavaş kaynak kadar (25 sn'ye dek) bekletiyordu. Artık
    yeni sorgu hemen başlıyor, eskisinin geç sonucu ekrana düşmüyor."""
    kapi = threading.Event()

    def sonuc(sorgu):
        if sorgu == "a":
            kapi.wait(10)                       # "a" yavaş kaynak gibi
            return {"TurkAnime": [kayit("eski", "Eski Sonuç")]}
        return {"TurkAnime": [kayit("yeni", "Yeni Sonuç")]}

    cagrilar = sahte_motor(sonuc)
    page.start_search("a")
    try:
        qtbot.waitUntil(lambda: len(cagrilar) == 1, timeout=5000)
        page.start_search("b")
        qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)
        assert "b" in page.lblTitle.text()
        assert page.cards()[0].lblTitle.text() == "Yeni Sonuç"
    finally:
        kapi.set()                              # "a"nın cevabı ŞİMDİ dönüyor

    qtbot.wait(200)
    assert [c.lblTitle.text() for c in page.cards()] == ["Yeni Sonuç"]
    assert "“a”" not in page.lblStatus.text()
    assert page._busy is False


# ── Kapak görselleri ────────────────────────────────────────────────────────
def test_gorsel_baytlari_karta_uygulaniyor(page):
    page._on_results({"AniList": [kayit("1", "Bebop", "http://kapak/1.jpg")]})
    kart = page.cards()[0]

    page._apply_thumb(kart, png_baytlari())

    assert not kart.lblThumb.pixmap().isNull()


def test_silinmis_karta_gorsel_uygulamak_cokmuyor(page):
    """ESKİ HATA: görsel inerken yeni arama yapılırsa kart C++'ta yıkılmış olur.

    O anda `set_thumbnail` `RuntimeError` fırlatıp UI thread'ini düşürüyordu.
    """
    shiboken6 = pytest.importorskip("shiboken6")

    page._on_results({"AniList": [kayit("1", "Bebop", "http://kapak/1.jpg")]})
    kart = page.cards()[0]
    shiboken6.delete(kart)

    page._apply_thumb(kart, png_baytlari())      # istisna fırlatmamalı


def test_gorselsiz_kayit_icin_indirme_kuyruga_alinmiyor(page, monkeypatch):
    """ESKİ HATA: `image=None` olan kaynaklar için boşuna iş açılıyordu."""
    import turkanime_api.gui.qt.pages.search as search_mod

    isler: list = []
    monkeypatch.setattr(search_mod, "run_bg",
                        lambda fn, *a, **k: isler.append(a))

    page._on_results({"TurkAnime": [kayit("naruto", "Naruto")],
                      "AniList": [kayit("1", "Bebop", "http://kapak/1.jpg")]})

    assert [a[1] for a in isler] == ["http://kapak/1.jpg"]


# ── Artımlı sonuçlar (kaynak kaynak) ────────────────────────────────────────
class _Anlik:
    def __init__(self, ciftler):
        self.ciftler = ciftler

    def search_anime(self, query, limit=10):
        return list(self.ciftler)


class _Yavas:
    def __init__(self, kapi, ciftler):
        self.kapi = kapi
        self.ciftler = ciftler

    def search_anime(self, query, limit=10):
        self.kapi.wait(10)
        return list(self.ciftler)


@pytest.fixture
def adaptorlu_motor(monkeypatch):
    """GERÇEK `SearchEngine` (artımlı yol), adaptörler sahte."""
    import turkanime_api.common.adapters as adapters_mod

    def _kur(adaptorler):
        class Motor(adapters_mod.SearchEngine):
            def __init__(self):
                self.adapters = dict(adaptorler)

        monkeypatch.setattr(adapters_mod, "SearchEngine", Motor)

    return _kur


def test_anlik_arsiv_sonucu_yavas_kaynagi_beklemiyor(qtbot, page, adaptorlu_motor):
    import time

    kapi = threading.Event()
    adaptorlu_motor({"TürkAnime": _Anlik([("naruto", "Naruto")]),
                     "Yavas": _Yavas(kapi, [("y1", "Naruto Yavaş"),
                                            ("y2", "Naruto Yavaş 2")])})
    try:
        basla = time.monotonic()
        page.start_search("naruto")
        qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=2000)
        assert time.monotonic() - basla < 0.5
        ilk_kart = page.cards()[0]
        assert ilk_kart.lblTitle.text() == "Naruto"
        qtbot.waitUntil(lambda: "1 kaynak bekleniyor" in page.lblStatus.text(),
                        timeout=2000)
        assert page._busy
    finally:
        kapi.set()

    qtbot.waitUntil(lambda: len(page.cards()) == 3, timeout=5000)
    assert page.cards()[0] is ilk_kart, "gelen grup mevcut kartları yeniden kurdu"
    qtbot.waitUntil(lambda: not page._busy, timeout=5000)
    metin = page.lblStatus.text()
    assert "3 sonuç" in metin and "TürkAnime (arşiv): 1" in metin and "Yavas: 2" in metin
    assert "bekleniyor" not in metin


def test_sureye_yetisemeyen_kaynak_durumda_adiyla(qtbot, page, adaptorlu_motor,
                                                  monkeypatch):
    import turkanime_api.common.adapters as adapters_mod

    monkeypatch.setattr(adapters_mod, "OVERALL_SEARCH_TIMEOUT", 0.3)
    kapi = threading.Event()
    adaptorlu_motor({"TürkAnime": _Anlik([("naruto", "Naruto")]),
                     "Yavas": _Yavas(kapi, [("y", "Y")])})
    try:
        page.start_search("naruto")
        qtbot.waitUntil(lambda: not page._busy, timeout=5000)
    finally:
        kapi.set()
    assert "zaman aşımı: Yavas" in page.lblStatus.text()
    assert len(page.cards()) == 1


def test_gruplar_kayit_sirasinda_metadata_en_sonda(page):
    """ESKİ HATA: alfabetik sıra AniList'i (oynatılamaz) en başa koyuyordu."""
    page._on_results({"AniList": [kayit("1", "A-AniList")],
                      "AnimeciX": [kayit("2", "B-AnimeciX")],
                      "TürkAnime": [kayit("3", "C-Arşiv")]})
    assert [c.payload[0] for c in page.cards()] == ["TürkAnime", "AnimeciX", "AniList"]


def test_gec_gelen_grup_kendi_yerine_giriyor(page):
    """Kaynaklar hangi sırayla gelirse gelsin dizilim kayıt sırası."""
    page._busy = True
    rid = page._istek
    page._kaynak_geldi((rid, "AniList", [kayit("1", "A")], None))
    page._kaynak_geldi((rid, "TürkAnime", [kayit("3", "C")], None))
    page._kaynak_geldi((rid, "AnimeciX", [kayit("2", "B")], None))
    assert [c.payload[0] for c in page.cards()] == ["TürkAnime", "AnimeciX", "AniList"]


def test_eski_istegin_kaynak_sonucu_atiliyor(page):
    page._busy = True
    eski = page._istek
    page._istek += 1                     # yeni arama başlamış gibi
    page._kaynak_geldi((eski, "TürkAnime", [kayit("x", "Eski")], None))
    page._arama_bitti((eski, {"TürkAnime": [kayit("x", "Eski")]}))
    page._arama_hatasi((eski, "eski patladı"))
    assert page.cards() == []
    assert "eski" not in page.lblStatus.text().lower()
