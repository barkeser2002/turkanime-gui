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

    assert [c.lblTitle.text() for c in page.cards()] == [
        "Cowboy Bebop", "Cowboy Bebop TR"]
    assert page.cards()[0].payload == ("AniList", "101", "Cowboy Bebop")
    assert page.cards()[1].payload == ("TurkAnime", "cowboy-bebop",
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

    assert sinyal.args == ["TurkAnime", "naruto", "Naruto"]


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


def test_suren_arama_varken_ikincisi_reddediliyor(qtbot, page, sahte_motor):
    """ESKİ HATA: üst üste arama, ilkinin sonucunu ikincinin üstüne yazıyordu."""
    kapi = threading.Event()
    cagrilar = sahte_motor({"TurkAnime": [kayit("naruto", "Naruto")]}, gecikme=kapi)

    page.start_search("naruto")
    try:
        qtbot.waitUntil(lambda: len(cagrilar) == 1, timeout=5000)
        page.start_search("bleach")
        assert len(cagrilar) == 1, "önceki arama sürerken ikincisi başladı"
        assert "sürüyor" in page.lblStatus.text()
    finally:
        kapi.set()

    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)
    # Kilit çözüldükten sonra sayfa yeniden arama kabul etmeli.
    page.start_search("bleach")
    qtbot.waitUntil(lambda: len(cagrilar) == 2, timeout=5000)


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
