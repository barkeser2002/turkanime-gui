"""Web arayüzünün kalan sayfaları: İzleme Listem (AniList), ...

AniList istemcisi sahte (`sahte_istemci`); ağ yok, OAuth açılmaz.
"""
from __future__ import annotations

import pytest

from turkanime_api.gui.web.uclar_izleme import izleme_karti


# ── İzleme Listem ────────────────────────────────────────────────────────────
def media(title, anime_id=1, episodes=26):
    return {"id": anime_id, "title": {"romaji": title, "english": None},
            "coverImage": {"large": f"https://img/{anime_id}.jpg"},
            "episodes": episodes, "averageScore": 80, "genres": ["Action"]}


def liste(*girisler):
    return [{"name": "Liste", "entries": list(girisler)}]


def giris(title, anime_id=1, episodes=26, progress=5, score=8, status="CURRENT"):
    return {"media": media(title, anime_id, episodes), "progress": progress,
            "score": score, "status": status, "updatedAt": 0}


class SahteIstemci:
    def __init__(self, token="jeton"):
        self.access_token = token
        self.client_id = "1"
        self.client_secret = ""
        self.redirect_uri = "http://localhost:9921/anilist-login"
        self.user_data = None
        self.listeler = {}
        self.istenen = []

    def get_current_user(self):
        return {"id": 7, "name": "barkeser", "avatar": {"large": None}} if self.access_token else None

    def get_user_anime_list(self, user_id, status=None):
        self.istenen.append(status)
        return self.listeler.get(status, [])


@pytest.fixture
def sahte_istemci(monkeypatch):
    import turkanime_api.anilist_client as anilist_mod

    def kur(**k):
        ist = SahteIstemci(**k)
        monkeypatch.setattr(anilist_mod, "anilist_client", ist)
        return ist

    return kur


def test_izleme_karti():
    veri = izleme_karti({"title": {"romaji": "Bebop"}, "episodes": 26,
                         "user_status": "CURRENT", "user_progress": 13, "user_score": 8})
    assert veri["rozet"] == "İzliyorum" and veri["rozet_renk"]
    assert veri["ilerleme"] == 0.5
    assert veri["alt"] == "İzlenen: 13/26 · ★ 8"
    # Toplam bilinmiyorsa çubuk yok.
    assert izleme_karti({"title": "X", "user_progress": 3})["ilerleme"] is None


def test_izleme_listesi_sekmeler_ve_kartlar(main_window, web, sahte_istemci):
    ist = sahte_istemci()
    ist.listeler["CURRENT"] = liste(giris("Cowboy Bebop", progress=13, episodes=26))
    ist.listeler["PLANNING"] = liste(giris("Frieren", 2, status="PLANNING", progress=0),
                                     giris("Bleach", 3, status="PLANNING", progress=0))
    main_window.anilist.baslat()
    main_window.show_page("watchlist")
    kartlar = "document.querySelectorAll('[data-sayfa=watchlist] .izgara .kart:not(.iskelet-kart)')"
    web.bekle(f"{kartlar}.length === 1")
    assert web.js(f"{kartlar}[0].querySelector('.kart-rozet').textContent") == "İzliyorum"
    assert "13/26" in web.js(f"{kartlar}[0].querySelector('.kart-alt').textContent")
    assert web.js(f"{kartlar}[0].querySelector('.ilerleme i').style.width") == "50%"
    assert web.js("document.querySelectorAll('[data-sayfa=watchlist] .sekme').length") == 5

    web.js("document.querySelector('.sekme[data-durum=PLANNING]').click()")
    web.bekle(f"{kartlar}.length === 2")
    assert "PLANNING" in ist.istenen


def test_bos_liste_mesaji(main_window, web, sahte_istemci):
    sahte_istemci()
    main_window.anilist.baslat()
    main_window.show_page("watchlist")
    web.bekle("document.querySelector('[data-sayfa=watchlist]').innerText.includes('Bu listede anime yok')")


# ── İndirilenler ─────────────────────────────────────────────────────────────
def test_indirme_satirlari_olaylarla(main_window, web, monkeypatch):
    """Yönetici sinyalleri sayfaya: satır, toplu ilerleme, durum, bitiş."""
    y = main_window.downloads
    oynatilan = []
    main_window.indirme_uclari._oynat = oynatilan.append
    monkeypatch.setattr(y, "kayit", lambda tid: {"title": "Bölüm", "tid": tid})
    main_window.show_page("downloads")
    web.bekle("document.querySelector('[data-sayfa=downloads]').innerText.includes('Henüz indirme yok')")

    y.added.emit("t1", "Naruto — 1. Bölüm")
    y.added.emit("t2", "Naruto — 2. Bölüm")
    web.bekle("document.querySelectorAll('.indirme-satiri').length === 2")
    y.state.emit("t1", "indiriliyor")
    for yuzde in range(0, 50, 7):                # sık ilerleme → toplu gönderim
        y.progress.emit("t1", yuzde, f"{yuzde} MB / 100 MB")
    y.progress.emit("t1", 50, "50 MB / 100 MB · 2 MB/s")
    satir = "document.querySelector('.indirme-satiri[data-id=t1]')"
    web.bekle(f"{satir}.querySelector('.indirme-cubuk i').style.width === '50%'")
    assert "2 MB/s" in web.js(f"{satir}.querySelector('.indirme-detay').textContent")
    assert web.js("document.querySelector('[data-sayfa=downloads] .sayfa-baslik p').textContent") == \
        "2 aktif / 2 toplam"

    y.state.emit("t1", "tamamlandı")
    y.finished.emit("t1", True, "tamamlandı: naruto-1.mp4")
    web.bekle(f"{satir}.classList.contains('yesil')")
    web.js(f"Array.from({satir}.querySelectorAll('button')).find(b => b.textContent === 'Oynat').click()")
    web.qtbot.waitUntil(lambda: oynatilan == [{"title": "Bölüm", "tid": "t1"}], timeout=5000)

    y.state.emit("t2", "hata")
    y.finished.emit("t2", False, "bağlantı koptu")
    web.bekle("document.querySelector('.indirme-satiri[data-id=t2]').innerText.includes('bağlantı koptu')")
    assert "1 tamamlandı, 1 başarısız" in web.js(
        "document.querySelector('[data-sayfa=downloads] .sayfa-baslik p').textContent")
    web.js("Array.from(document.querySelectorAll('[data-sayfa=downloads] .sayfa-eylem button'))"
           ".find(b => b.textContent.includes('Temizle')).click()")
    web.bekle("document.querySelectorAll('.indirme-satiri').length === 0")


def test_indirme_eylemleri_yoneticiye_gidiyor(main_window, web, monkeypatch):
    y = main_window.downloads
    cagrilar = []
    for ad in ("pause", "resume", "cancel"):
        monkeypatch.setattr(y, ad, lambda tid, ad=ad: cagrilar.append((ad, tid)) or True)
    main_window.show_page("downloads")
    y.added.emit("t9", "Bleach — 3. Bölüm")
    satir = "document.querySelector('.indirme-satiri[data-id=t9]')"
    web.bekle(f"!!{satir}")
    tikla = f"Array.from({satir}.querySelectorAll('button')).find(b => b.textContent === %r).click()"
    web.js(tikla % "Duraklat")
    y.state.emit("t9", "duraklatıldı")
    web.bekle(f"{satir}.innerText.includes('Devam et')")
    web.js(tikla % "Devam et")
    web.js(tikla % "İptal")
    web.qtbot.waitUntil(lambda: len(cagrilar) == 3, timeout=5000)
    assert cagrilar == [("pause", "t9"), ("resume", "t9"), ("cancel", "t9")]


# ── Ayarlar ──────────────────────────────────────────────────────────────────
@pytest.fixture
def temiz_kaynak_global(monkeypatch):
    """Kaynak modüllerinin süreç-içi çerez/jeton kopyaları test başında boş."""
    from turkanime_api.sources import openani, tranime
    monkeypatch.setattr(tranime, "SESSION_COOKIE", None)
    monkeypatch.setattr(tranime, "_EXTRA_COOKIES", {})
    monkeypatch.setattr(openani, "OPENANI_TOKEN", None)
    monkeypatch.setattr(openani, "OPENANI_REFRESH_TOKEN", None)


@pytest.fixture
def ayar_uclari(izole_ev, main_window):
    return main_window.ayarlar_uclari


def test_ayarlar_okunup_kaydediliyor(ayar_uclari, temiz_kaynak_global):
    from turkanime_api.cli.dosyalar import Dosyalar
    from turkanime_api.gui.qt import prefs
    from turkanime_api.sources import openani
    v = ayar_uclari.ayarlar()["degerler"]
    assert v["max_res"] is True and v["izlendi_ikonu"] is True and v["aria2c"] is False
    ayar_uclari.ayarlari_kaydet({
        "indirilenler": "  /tmp/indir  ", "paralel": 99, "aday": 12,
        "izlerken_kaydet": True, "ilerlemeyi_sor": True, "manuel_fansub": True,
        "openani_token": " jeton ", "max_res": False})
    ayar = Dosyalar().ayarlar
    assert ayar["indirilenler"] == "/tmp/indir"
    assert ayar["paralel indirme sayisi"] == 10            # üst sınır
    assert ayar["1080p aday sayisi"] == 12
    assert ayar["izlerken kaydet"] is True and ayar["max resolution"] is False
    t = prefs.oku()
    assert (t.izlerken_kaydet, t.ilerlemeyi_sor, t.manuel_fansub, t.aday_sayisi) == (
        True, True, True, 12)
    # Jeton aynı anda kaynağa gidiyor (yeniden başlatmadan).
    assert openani.OPENANI_TOKEN == "jeton"
    with pytest.raises(ValueError):
        ayar_uclari.ayarlari_kaydet({"paralel": "çok"})


def test_acilista_diskteki_cerez_kaynaga_ulasiyor(qtbot, izole_ev, temiz_kaynak_global):
    """Ayar denetleyicisi kurulurken (pencere açılışı) çerezi uygular."""
    from turkanime_api.cli.dosyalar import Dosyalar
    from turkanime_api.gui.qt.app import MainWindow
    from turkanime_api.sources import tranime
    Dosyalar().set_ayar("tranime_cookie",
                        "# Netscape\n.tranimeizle.io\tTRUE\t/\tTRUE\t0\t.AitrWeb.Session\tabc\n")
    win = MainWindow()
    qtbot.addWidget(win)
    win._kapanis_onayi = lambda _a: True
    assert tranime.SESSION_COOKIE == "abc"
    assert win.ayarlar_uclari.ayarlar()["cerez"]["var"] is True
    win.ayarlar_uclari.cerez_temizle()
    assert tranime.SESSION_COOKIE in (None, "")


def test_discord_anahtari_aninda_yaziliyor(ayar_uclari):
    from turkanime_api.cli.dosyalar import Dosyalar
    s = ayar_uclari.discord_ayarla(False)
    assert Dosyalar().ayarlar["discord_rich_presence"] is False
    assert "kapatıldı" in s["mesaj"]


class SahteKatki:
    KAYNAK_TRANIME = "tranime"

    def __init__(self, onay=True, hata_ver=()):
        self.onay = onay
        self.hata_ver = set(hata_ver)
        self.gonderilen = []
        self.silinen = []

    def onay_al(self, kaynak, parent=None):
        return self.onay

    def bagis_gonder(self, deger, kaynak, ayarlar):
        self.gonderilen.append(deger)
        return f"b{len(self.gonderilen)}"

    def bagis_geri_cek(self, bid, ayarlar):
        if bid in self.hata_ver:
            raise RuntimeError("sunucu yok")
        self.silinen.append(bid)


def test_bagis_iki_kapili_ve_geri_cekme(ayar_uclari, monkeypatch):
    from turkanime_api.cli.dosyalar import Dosyalar
    katki = SahteKatki()
    monkeypatch.setattr(type(ayar_uclari), "_katki", staticmethod(lambda: katki))
    # Kapı 1: ayar kapalı → pencere bile açılmaz, gönderim yok.
    ayar_uclari.kimlik_bagisi_teklif("cerez")
    assert katki.gonderilen == []
    Dosyalar().set_ayar("kimlik paylas", True)
    # Kapı 2: onay yok → gönderim yok.
    katki.onay = False
    ayar_uclari.kimlik_bagisi_teklif("cerez")
    assert katki.gonderilen == []
    katki.onay = True
    ayar_uclari.kimlik_bagisi_teklif("cerez-1")
    ayar_uclari.kimlik_bagisi_teklif("cerez-2")
    assert Dosyalar().ayarlar["kimlik bagis id"] == ["b1", "b2"]
    # Biri silinemezse numarası saklanır.
    katki.hata_ver = {"b2"}
    s = ayar_uclari.bagis_geri_cek()
    assert s["tur"] == "hata" and "1/2" in s["mesaj"]
    assert Dosyalar().ayarlar["kimlik bagis id"] == ["b2"]
    katki.hata_ver = set()
    s = ayar_uclari.bagis_geri_cek()
    assert s["tur"] == "tamam" and s["kimlikler"] == []


def test_arsiv_silme_onaysiz_calismiyor(ayar_uclari):
    with pytest.raises(ValueError):
        ayar_uclari.arsiv_sil(onay=False)


def test_ayarlar_sayfasi_form_ve_kaydet(izole_ev, main_window, web):
    from turkanime_api.cli.dosyalar import Dosyalar
    main_window.show_page("settings")
    web.bekle("!!document.querySelector('[data-bolum=oynatma] input')")
    assert web.js("document.querySelector('.kaydet-cubugu').hidden") is True
    web.js("var g = document.querySelector('[data-bolum=oynatma] .girdi-grup input');"
           "g.value = '/tmp/yeni'; g.dispatchEvent(new Event('input'))")
    web.bekle("document.querySelector('.kaydet-cubugu').hidden === false")
    web.js("document.querySelector('.kaydet-cubugu .dugme.birincil').click()")
    web.bekle("document.querySelector('.kaydet-cubugu').hidden === true")
    assert Dosyalar().ayarlar["indirilenler"] == "/tmp/yeni"
    # Arşiv paneli durumu okudu (testte arşiv yok → uzak ayna uyarısı).
    web.bekle("document.querySelector('.arsiv-paneli').innerText.includes('Uzak ayna')")


def test_onay_penceresi_vazgec_ve_esc(izole_ev, main_window, web):
    """Tehlikeli eylemin onayı: "Vazgeç" ve Esc hayır demek (odak Vazgeç'te)."""
    main_window.show_page("settings")
    web.bekle("!!document.querySelector('.arsiv-paneli')")
    web.js("TA.onayla({baslik: 'Sil', metin: 'emin misin', tehlikeli: true})"
           ".then(v => window._onay = v)")
    web.bekle("!!document.querySelector('.onay-penceresi')")
    assert web.js("document.activeElement.textContent") == "Vazgeç"
    web.js("document.querySelector('.onay-penceresi .dugme.hayalet').click()")
    web.bekle("window._onay === false")
    assert not web.js("!!document.querySelector('.modal-ortu')")
    web.js("TA.onayla({baslik: 'Sil', metin: 'x'}).then(v => window._onay2 = v);"
           "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    web.bekle("window._onay2 === false")
