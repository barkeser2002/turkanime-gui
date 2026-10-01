"""Python → sayfa soruları (`gui.web.sorular`): Qt `exec()`'inin yerine geçen düzen.

Sözleşme: ``geri`` TAM OLARAK BİR KEZ çağrılır — kullanıcının cevabıyla ya da
soru cevapsız bittiğinde (sayfa bağlanmadı, render süreci öldü, uygulama
kapandı) sorunun ``varsayilan``ıyla. `dogrula` `UcHatasi` fırlatırsa soru
açık kalır. Sayfa bağlanmadan yayılan olay kaybolur; açık sorular
`bekleyen_sorular` ile çekilir.

Önce sayfasız (`SahteKopru`), sonra gerçek QtWebEngine sayfasıyla.
"""
from __future__ import annotations

import threading

import pytest

from turkanime_api.gui.web.kopru import UcHatasi
from turkanime_api.gui.web.sorular import SoruMerkezi


def _merkez(qtbot, bagli=True):
    from conftest import SahteKopru
    kopru = SahteKopru()
    merkez = SoruMerkezi(kopru)
    merkez.bagli = bagli
    return merkez, kopru


# ── Sayfasız ────────────────────────────────────────────────────────────────
def test_soru_olayla_gidiyor_cevap_bir_kez_donuyor(qtbot):
    merkez, kopru = _merkez(qtbot)
    cevaplar = []
    soru = merkez.sor("deneme", {"a": 1}, cevaplar.append)

    assert kopru.son("soru") == {"id": soru.kimlik, "tur": "deneme", "veri": {"a": 1}}
    assert merkez.bekleyenler("deneme") == [soru]
    assert merkez.soru_cevapla(soru.kimlik, {"b": 2}) is True
    assert cevaplar == [{"b": 2}]
    assert not soru.acik and merkez.bekleyenler() == []
    # İkinci cevap (çift tık, geç gelen Esc) yok sayılır; sayfa yine kapatır.
    assert merkez.soru_cevapla(soru.kimlik, {"b": 3}) is False
    soru.bitir("python")
    assert cevaplar == [{"b": 2}]


def test_dogrula_reddederse_soru_acik_kaliyor(qtbot):
    merkez, _ = _merkez(qtbot)
    cevaplar = []

    def dogrula(cevap):
        if cevap != "iyi":
            raise UcHatasi("olmadı")

    soru = merkez.sor("deneme", {}, cevaplar.append, dogrula=dogrula)
    with pytest.raises(UcHatasi, match="olmadı"):
        merkez.soru_cevapla(soru.kimlik, "kötü")
    assert soru.acik and cevaplar == []
    assert merkez.soru_cevapla(soru.kimlik, "iyi") is True
    assert cevaplar == ["iyi"]


def test_eylem_soruyu_kapatmiyor_guncelleme_veriye_isleniyor(qtbot):
    merkez, kopru = _merkez(qtbot)
    eylemler = []

    def eylem(ad, veri):
        eylemler.append((ad, veri))
        soru.guncelle(durum="iniyor", yuzde=10)
        return True

    soru = merkez.sor("deneme", {"durum": "hazir"}, eylem=eylem)
    assert merkez.soru_eylem(soru.kimlik, "indir", {"x": 1}) is True
    assert eylemler == [("indir", {"x": 1})]
    assert soru.acik
    assert kopru.son("soru_guncelle") == {"id": soru.kimlik, "veri": {"durum": "iniyor", "yuzde": 10}}
    # Yeniden çizimde (sayfa yenilendi) güncel hâl gider.
    assert merkez.bekleyen_sorular()[0]["veri"] == {"durum": "iniyor", "yuzde": 10}

    with pytest.raises(UcHatasi):
        merkez.soru_eylem("yok", "indir")
    yalin = merkez.sor("yalin", {})
    with pytest.raises(UcHatasi):
        merkez.soru_eylem(yalin.kimlik, "indir")


def test_python_kapatinca_sayfaya_haber_gidiyor(qtbot):
    merkez, kopru = _merkez(qtbot)
    cevaplar = []
    soru = merkez.sor("deneme", {}, cevaplar.append, varsayilan="vars")
    soru.bitir(varsayilanla=True)
    assert cevaplar == ["vars"]
    assert kopru.son("soru_kapat") == {"id": soru.kimlik}
    soru.guncelle(x=1)                          # kapanmış soru olay yaymaz
    assert kopru.hepsi("soru_guncelle") == []


def test_bekleyen_sorular_baglanmadan_sorulanlari_teslim_ediyor(qtbot):
    """Sayfa henüz bağlanmadı: olay yayılmaz, sayfa bağlanınca çeker."""
    merkez, kopru = _merkez(qtbot, bagli=False)
    cevaplar = []
    soru = merkez.sor("deneme", {"a": 1}, cevaplar.append, teslim_muhleti=200)
    assert kopru.hepsi("soru") == []

    assert merkez.bekleyen_sorular() == [soru.paket()]
    assert merkez.bagli and soru.teslim
    qtbot.wait(350)                             # mühlet teslimle durdu
    assert soru.acik and cevaplar == []
    ikinci = merkez.sor("deneme", {})
    assert kopru.son("soru")["id"] == ikinci.kimlik


def test_teslim_muhleti_dolunca_varsayilanla_bitiyor(qtbot):
    merkez, _ = _merkez(qtbot, bagli=False)
    cevaplar = []
    soru = merkez.sor("deneme", {}, cevaplar.append, varsayilan="vazgec",
                      teslim_muhleti=100)
    qtbot.waitUntil(lambda: bool(cevaplar), timeout=3000)
    assert cevaplar == ["vazgec"] and not soru.acik


def test_sifir_muhlet_baglanti_yoksa_hemen_varsayilan(qtbot):
    merkez, kopru = _merkez(qtbot, bagli=False)
    cevaplar = []
    soru = merkez.sor("kapanis", {}, cevaplar.append, varsayilan=True, teslim_muhleti=0)
    assert cevaplar == [True] and not soru.acik
    assert kopru.hepsi("soru") == []


def test_render_sureci_olunce_ve_kapanista_hepsi_varsayilanla(qtbot):
    merkez, kopru = _merkez(qtbot)
    cevaplar = []
    merkez.sor("a", {}, lambda c: cevaplar.append(("a", c)), varsayilan=1)
    merkez.sor("b", {}, lambda c: cevaplar.append(("b", c)), varsayilan=2)
    merkez.sayfa_gitti(1, 139)                  # renderProcessTerminated(durum, kod)
    assert sorted(cevaplar) == [("a", 1), ("b", 2)]
    assert not merkez.bagli
    assert len(kopru.hepsi("soru_kapat")) == 2

    merkez.bagli = True
    merkez.sor("c", {}, lambda c: cevaplar.append(("c", c)), varsayilan=3)
    merkez.hepsini_bitir()
    assert ("c", 3) in cevaplar and merkez.bekleyenler() == []


def test_cizilemeyen_soru_varsayilanla_bitiyor(qtbot):
    """Sayfa pencereyi çizemedi (bilinmeyen tür): `dogrula` atlanır."""
    merkez, _ = _merkez(qtbot)
    cevaplar = []
    soru = merkez.sor("bilinmeyen", {}, cevaplar.append, varsayilan="vars",
                      dogrula=lambda c: (_ for _ in ()).throw(UcHatasi("hayır")))
    assert merkez.soru_cevapla(soru.kimlik, "x", varsayilanla=True) is True
    assert cevaplar == ["vars"]


def test_geri_cagri_hatasi_soruyu_kilitlemiyor(qtbot, capsys):
    merkez, _ = _merkez(qtbot)

    def patla(_c):
        raise RuntimeError("çağıranın hatası")

    soru = merkez.sor("deneme", {}, patla)
    assert merkez.soru_cevapla(soru.kimlik, 1) is True
    assert not soru.acik
    assert "çağıranın hatası" in capsys.readouterr().err


def test_yalnizca_gui_threadinden_sorulabilir(qtbot):
    merkez, _ = _merkez(qtbot)
    hata = []

    def is_():
        try:
            merkez.sor("deneme", {})
        except RuntimeError as exc:
            hata.append(str(exc))

    t = threading.Thread(target=is_)
    t.start()
    t.join()
    assert hata and "GUI" in hata[0]


# ── Gerçek sayfa ────────────────────────────────────────────────────────────
PENCERE = "document.querySelectorAll('.modal-ortu').length"


def test_sayfa_yuklenmeden_sorulan_soru_baglaninca_gorunuyor(qtbot):
    """Açılış denetimi sayfa bağlanmadan soruyor; olay kaybolsa da pencere çıkmalı."""
    from conftest import WebSurucu
    from turkanime_api.gui.qt.app import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win._kapanis_onayi = lambda _a, geri: geri(True)
    try:
        assert not win.sorular.bagli
        cevaplar = []
        win.sorular.sor("kapanis", {"baslik": "Erken", "metin": "sayfadan önce"},
                        cevaplar.append)
        win.show()
        web = WebSurucu(qtbot, win.web).hazir()
        web.bekle("!!document.querySelector('[data-soru=kapanis]')")
        assert "sayfadan önce" in web.js("document.querySelector('[data-soru=kapanis]').innerText")
        assert win.sorular.bagli
        web.js("document.querySelector('[data-soru=kapanis] .dugme.hayalet').click()")
        qtbot.waitUntil(lambda: bool(cevaplar), timeout=5000)
        assert cevaplar == [False]
    finally:
        win.close()


def test_python_kapattigi_pencere_sayfadan_kalkiyor(main_window, web):
    soru = main_window.sorular.sor("ilerleme", {
        "anime_adi": "A", "bolum_adi": "B", "bolum_no": 1, "en_az": 1, "en_cok": 9999,
        "kaydedilebilir": True, "not": ""})
    web.bekle("!!document.querySelector('[data-soru=ilerleme]')")
    soru.bitir(None)
    web.bekle(PENCERE + " === 0")
    assert web.js("TA.acikSorular().length") == 0


def test_esc_yalnizca_en_ustteki_pencereyi_kapatiyor(main_window, web):
    """Açılışta güncelleme + gereksinim birlikte gelebiliyor: tek Esc tek pencere."""
    cevaplar = []
    veri = {"anime_adi": "A", "bolum_adi": "B", "bolum_no": 1, "en_az": 1,
            "en_cok": 9999, "kaydedilebilir": True, "not": ""}
    alt = main_window.sorular.sor("ilerleme", veri, lambda c: cevaplar.append(("alt", c)))
    ust = main_window.sorular.sor("ilerleme", veri, lambda c: cevaplar.append(("ust", c)))
    web.bekle(PENCERE + " === 2")
    web.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    web.bekle(PENCERE + " === 1")
    web.qtbot.waitUntil(lambda: bool(cevaplar), timeout=5000)
    assert cevaplar == [("ust", None)]
    assert alt.acik and not ust.acik
    web.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    web.bekle(PENCERE + " === 0")
    web.qtbot.waitUntil(lambda: len(cevaplar) == 2, timeout=5000)


def test_bilinmeyen_tur_varsayilanla_donuyor(main_window, web):
    cevaplar = []
    main_window.sorular.sor("boyle-bir-pencere-yok", {}, cevaplar.append, varsayilan="vars")
    web.qtbot.waitUntil(lambda: bool(cevaplar), timeout=5000)
    assert cevaplar == ["vars"]
    assert web.js(PENCERE) == 0


def test_render_sureci_olunce_acik_soru_bitiyor(main_window, web):
    """Sayfa gitti: fansub sorusu `_playing`'i açık bırakmasın (varsayılan = iptal)."""
    cevaplar = []
    main_window.sorular.sor("ilerleme", {}, cevaplar.append, varsayilan="iptal")
    main_window.web.page().renderProcessTerminated.emit(
        main_window.web.page().RenderProcessTerminationStatus.CrashedTerminationStatus, 139)
    assert cevaplar == ["iptal"]
    assert not main_window.sorular.bagli
