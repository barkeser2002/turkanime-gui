"""Arayüzde fansub seçimi ("Fansub'u kendim seçeyim").

ESKİ HATA: ayar kaydediliyordu ama yalnızca CLI soruyordu; arayüz her bölümde
oynatıcı önceliğiyle seçilen akışı açıyor, seri içinde çeviri grubu bölümden
bölüme değişebiliyordu. Ağ yok, mpv yok: sahte bölümün `best_video`'su video
döndürmüyor (oynatma/indirme "video yok" ile biter, diske bir şey yazılmaz).

Soru eskiden Qt diyaloğuydu (`FansubDialog`); artık web arayüzünde bir pencere
(tür ``fansub``). Akış testleri `FansubSecici.sor`'u sahteliyor; pencerenin
kendisi gerçek QtWebEngine sayfasında sınanıyor.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from turkanime_api.gui.qt.fansub import (
    OTOMATIK_ETIKETI, FansubSecici, fansub_ozeti, fansub_secenekleri,
)
from turkanime_api.gui.qt.indirme import BITMIS_DURUMLAR


class FansubluBolum:
    """`fansubs` okunduğunu sayar, `best_video` argümanlarını saklar."""

    def __init__(self, no: int = 1, fansubs=("A", "B"), seri="fansub-seri"):
        self.slug = f"{seri}-{no}-bolum"
        self.anime = SimpleNamespace(slug=seri, title="Fansub Seri")
        self._fansubs = list(fansubs)
        self.fansub_okuma = 0
        self.cagrilar: list = []
        self._bekleyen_akislar = [
            {"url": f"u{i}", "fansub": f, "player": "SIBNET", "label": "1080p"}
            for i, f in enumerate(self._fansubs)]

    @property
    def fansubs(self):
        self.fansub_okuma += 1
        return list(self._fansubs)

    def best_video(self, **kwargs):
        self.cagrilar.append(kwargs)
        return None


def _entry(bolum, no=1):
    return {"title": f"Fansub Seri {no}. Bölüm", "obj": bolum,
            "kaynak": "TürkAnime", "kimlik": "fansub-seri"}


@pytest.fixture
def sorulan(main_window, monkeypatch):
    kayit: list = []

    def sor(baslik, fansubs, ozet, geri):
        kayit.append((baslik, list(fansubs), ozet))
        geri(("B", True))

    monkeypatch.setattr(main_window.fansub, "sor", sor)
    return kayit


def _oynat(main_window, qtbot, bolum, no=1):
    main_window._on_play(_entry(bolum, no))
    qtbot.waitUntil(lambda: bool(bolum.cagrilar) and not main_window._playing,
                    timeout=10000)


def test_secilen_fansub_best_videoya_gidiyor_ve_seride_hatirlaniyor(
        main_window, qtbot, ayarla, tmp_path, sorulan):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    bolum = FansubluBolum()
    _oynat(main_window, qtbot, bolum)
    assert bolum.cagrilar[0]["by_fansub"] == "B"
    assert len(sorulan) == 1
    assert sorulan[0][1] == ["A", "B"]
    assert sorulan[0][2]["A"] == ["SIBNET 1080p"], "oynatıcı/kalite özeti gösterilmeli"

    ikinci = FansubluBolum(no=2)
    _oynat(main_window, qtbot, ikinci, no=2)
    assert ikinci.cagrilar[0]["by_fansub"] == "B"
    assert len(sorulan) == 1, "aynı seride ikinci kez sorulmamalı"
    assert ikinci.fansub_okuma == 0, "hatırlanan seçimde liste bile okunmamalı"


def test_tek_fansubda_ve_ayar_kapaliyken_sorulmuyor(main_window, qtbot, ayarla,
                                                    tmp_path, sorulan):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    tek = FansubluBolum(fansubs=("A",))
    _oynat(main_window, qtbot, tek)
    assert sorulan == [] and "by_fansub" not in tek.cagrilar[0]

    ayarla(**{"manuel fansub": False})
    kapali = FansubluBolum(seri="baska-seri")
    _oynat(main_window, qtbot, kapali)
    assert sorulan == [] and kapali.fansub_okuma == 0
    assert "by_fansub" not in kapali.cagrilar[0]


def test_iptal_oynatmayi_baslatmiyor(main_window, qtbot, ayarla, tmp_path, monkeypatch):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    monkeypatch.setattr(main_window.fansub, "sor", lambda *a: a[-1](None))
    bolum = FansubluBolum()
    main_window._on_play(_entry(bolum))
    qtbot.waitUntil(lambda: not main_window._playing, timeout=10000)
    assert bolum.cagrilar == []
    assert "fansub seçilmedi" in main_window.statusBar().currentMessage()


def test_toplu_indirme_tek_soru_hepsine_ayni_fansub(main_window, qtbot, ayarla,
                                                    tmp_path, sorulan):
    ayarla(**{"manuel fansub": True, "indirilenler": str(tmp_path)})
    bolumler = [FansubluBolum(no=i) for i in range(1, 4)]
    for i, b in enumerate(bolumler, start=1):
        main_window._on_download(_entry(b, i))
    qtbot.waitUntil(lambda: all(b.cagrilar for b in bolumler), timeout=10000)
    qtbot.waitUntil(lambda: all(main_window.downloads.durum(t) in BITMIS_DURUMLAR
                                for t in list(main_window.downloads._jobs)),
                    timeout=10000)
    assert len(sorulan) == 1, "12 bölümlük toplu indirme 12 kez sormamalı"
    assert [b.cagrilar[0].get("by_fansub") for b in bolumler] == ["B", "B", "B"]
    assert sum(b.fansub_okuma for b in bolumler) == 1


def test_secenekler_ozetle_listeleniyor():
    ozet = fansub_ozeti(FansubluBolum())
    assert ozet == {"A": ["SIBNET 1080p"], "B": ["SIBNET 1080p"]}
    secenekler = fansub_secenekleri(["A", "B"], ozet)
    assert [x["etiket"] for x in secenekler] == [OTOMATIK_ETIKETI, "A — SIBNET 1080p", "B — SIBNET 1080p"]
    assert secenekler[0]["deger"] == "", "Otomatik = fansub süzülmez"
    # Özet en fazla dört oynatıcı/kalite; özetsiz ad yalın.
    uzun = fansub_secenekleri(["C", "D"], {"C": ["p1", "p2", "p3", "p4", "p5"]})
    assert uzun[1]["etiket"] == "C — p1, p2, p3, p4" and uzun[2]["etiket"] == "D"


# ── Pencere (sayfa) ──────────────────────────────────────────────────────────
FANSUB = "document.querySelector('[data-soru=fansub]')"


@pytest.fixture
def pencere(main_window, web):
    """Sayfada açılmış fansub penceresi; ``sonuc`` `sor`'un cevapları."""
    sonuc: list = []
    main_window.fansub.sor("Seri", ["A", "B"], fansub_ozeti(FansubluBolum()), sonuc.append)
    web.bekle("!!" + FANSUB)
    web.sonuc = sonuc
    return web


def _satirlar(web):
    return web.js("[..." + FANSUB + ".querySelectorAll('.secenek')].map(s => s.innerText)")


def test_pencere_ozetle_listeliyor_ilk_fansub_secili(pencere):
    satirlar = _satirlar(pencere)
    assert satirlar[0].startswith("Otomatik")
    assert satirlar[1] == "A — SIBNET 1080p"
    assert "“Seri” birden çok çeviri grubuyla var" in pencere.js(FANSUB + ".innerText")
    assert pencere.js(FANSUB + ".querySelectorAll('input[type=radio]')[1].checked") is True
    assert pencere.js(FANSUB + ".querySelector('input[type=checkbox]').checked") is True, \
        "hatırla varsayılan açık"


def test_pencere_otomatik_ve_hatirlamadan_seciliyor(pencere):
    pencere.js(FANSUB + ".querySelectorAll('input[type=radio]')[0].click();"
               + FANSUB + ".querySelector('input[type=checkbox]').click();"
               "[..." + FANSUB + ".querySelectorAll('button')].find(b => b.textContent === 'Tamam').click()")
    pencere.qtbot.waitUntil(lambda: bool(pencere.sonuc), timeout=5000)
    assert pencere.sonuc == [("", False)]
    pencere.bekle("!" + FANSUB)


def test_pencere_cift_tik_ve_enter_seciyor(main_window, pencere):
    pencere.js(FANSUB + ".querySelectorAll('.secenek')[2].dispatchEvent("
               "new MouseEvent('dblclick', {bubbles: true}))")
    pencere.qtbot.waitUntil(lambda: bool(pencere.sonuc), timeout=5000)
    assert pencere.sonuc == [("B", True)]
    pencere.bekle("!" + FANSUB)

    ikinci: list = []
    main_window.fansub.sor("Seri", ["A", "B"], {}, ikinci.append)
    pencere.bekle("!!" + FANSUB)
    pencere.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', "
               "{key: 'Enter', bubbles: true}))")
    pencere.qtbot.waitUntil(lambda: bool(ikinci), timeout=5000)
    assert ikinci == [("A", True)]


@pytest.mark.parametrize("vazgec", [
    "[...{p}.querySelectorAll('button')].find(b => b.textContent === 'Vazgeç').click()",
    "document.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape'}}))",
    "{p}.parentElement.click()",
], ids=["vazgec", "esc", "dis-tik"])
def test_pencere_vazgecince_iptal(pencere, vazgec):
    pencere.js(vazgec.format(p=FANSUB))
    pencere.qtbot.waitUntil(lambda: bool(pencere.sonuc), timeout=5000)
    assert pencere.sonuc == [None]
    pencere.bekle("!" + FANSUB)


def test_listede_olmayan_secim_reddediliyor(soru_merkezi):
    from turkanime_api.gui.web.kopru import UcHatasi
    secici = FansubSecici(sorular=soru_merkezi)
    sonuc: list = []
    secici.sor("Seri", ["A", "B"], {}, sonuc.append)
    (soru,) = soru_merkezi.bekleyenler("fansub")
    with pytest.raises(UcHatasi):
        soru_merkezi.cevapla(soru.kimlik, {"secim": "Uydurma", "hatirla": True})
    assert soru.acik and sonuc == []
    soru_merkezi.cevapla(soru.kimlik, {"secim": "B", "hatirla": 1})
    assert sonuc == [("B", False)], "hatırla yalnızca gerçek true"


def test_soru_merkezi_yoksa_iptal():
    sonuc: list = []
    FansubSecici().sor("Seri", ["A", "B"], {}, sonuc.append)
    assert sonuc == [None]


def test_soru_acikken_gelen_istek_ayni_cevabi_aliyor(qtbot, monkeypatch):
    """Cevap sonradan geliyor: pencere açıkken gelen bölüm ikinci soru açmamalı."""
    secici = FansubSecici()
    sorular: list = []
    monkeypatch.setattr(secici, "sor", lambda b, f, o, geri: sorular.append(geri))
    sonuclar: list = []
    ilk, ikinci = FansubluBolum(), FansubluBolum(no=2)
    secici.iste(_entry(ilk), lambda t, f: sonuclar.append((1, t, f)))
    qtbot.waitUntil(lambda: bool(sorular), timeout=5000)
    secici.iste(_entry(ikinci, 2), lambda t, f: sonuclar.append((2, t, f)))
    qtbot.wait(100)
    assert len(sorular) == 1 and ikinci.fansub_okuma == 0
    sorular[0](("A", False))
    assert sonuclar == [(1, True, "A"), (2, True, "A")]
    assert secici.tercih == {}, "hatırla kapalıyken seri tercihi yazılmaz"


def test_otomatik_secimi_de_hatirlaniyor(qtbot, monkeypatch):
    secici = FansubSecici()
    monkeypatch.setattr(secici, "sor", lambda *a: a[-1](("", True)))
    sonuclar: list = []
    secici.iste(_entry(FansubluBolum()), lambda t, f: sonuclar.append((t, f)))
    qtbot.waitUntil(lambda: bool(sonuclar), timeout=5000)
    secici.iste(_entry(FansubluBolum(no=2)), lambda t, f: sonuclar.append((t, f)))
    assert sonuclar == [(True, None), (True, None)]
