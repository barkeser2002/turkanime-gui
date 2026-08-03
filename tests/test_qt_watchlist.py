"""AniList entegrasyonu: OAuth, İzleme Listem sayfası, ilerleme senkronu.

Hiçbir test ağa çıkmaz ve **gerçek OAuth akışı asla açılmaz**: `anilist_client`
singleton'ı tümüyle sahtelenir, `AniListAuthServer` yerine sayaç tutan bir ikiz
konur, `webbrowser.open` sahtelenip yalnızca çağrıldığı doğrulanır.
"""
from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from turkanime_api.gui.qt import prefs
from turkanime_api.gui.qt.anilist import (
    DURUMLAR, AniListService, deslug, en_iyi_eslesme, geri_donus_portu,
    girisleri_duzlestir, senkron_guncellemeleri,
)
from turkanime_api.gui.qt.pages.settings import SettingsPage
from turkanime_api.gui.qt.pages.watchlist import WatchlistPage
from turkanime_api.gui.qt.progress_dialog import ProgressDialog

DURUM_KODLARI = [kod for kod, _ in DURUMLAR]


# ── Sahteler ────────────────────────────────────────────────────────────────
class SahteIstemci:
    """`anilist_client` singleton'ının test ikizi: ağ yok, çağrılar kayıtlı."""

    def __init__(self, token: str | None = "jeton", user=None):
        self.access_token = token
        self.client_id = "29745"
        self.client_secret = "COK-GIZLI-ANAHTAR"
        self.redirect_uri = "http://localhost:9921/anilist-login"
        self.user_data = None
        self.cagrilar: list = []
        self.listeler: dict = {}
        self.arama_sonucu: list = []
        self.guncelleme_sonucu = True
        self._user = user if user is not None else {
            "id": 7, "name": "kullanici", "avatar": {"large": None}}

    def get_current_user(self):
        self.cagrilar.append(("user",))
        return self._user if self.access_token else None

    def get_user_anime_list(self, user_id, status=None):
        self.cagrilar.append(("list", user_id, status))
        return self.listeler.get(status, [])

    def update_anime_progress(self, media_id, progress, status=None):
        self.cagrilar.append(("progress", media_id, progress, status))
        return self.guncelleme_sonucu

    def search_anime(self, query, page=1, per_page=20):
        self.cagrilar.append(("search", query))
        return list(self.arama_sonucu)

    def get_auth_url(self, response_type=None, state=None):
        # Gerçek istemcideki seçim: secret varsa Authorization Code, yoksa
        # Implicit (bkz. `AniListClient.akis_turu`).
        response_type = response_type or ("code" if self.client_secret else "token")
        return ("https://anilist.co/api/v2/oauth/authorize"
                f"?client_id={self.client_id}&response_type={response_type}")

    def clear_tokens(self):
        self.cagrilar.append(("clear",))
        self.access_token = None

    def set_oauth_config(self, client_id, client_secret, redirect_uri):
        self.cagrilar.append(("config", client_id, client_secret, redirect_uri))
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri


class SahteAnime:
    def __init__(self, slug="naruto-test", title="Naruto Test"):
        self.slug = slug
        self.title = title


class SahteBolum:
    def __init__(self, slug="naruto-test-5-bolum"):
        self.slug = slug
        self.anime = SahteAnime()


@pytest.fixture
def sahte_anilist(monkeypatch):
    """`anilist_client` singleton'ını sahteyle değiştiren fabrika."""
    import turkanime_api.anilist_client as anilist_mod

    def _kur(**kwargs) -> SahteIstemci:
        ist = SahteIstemci(**kwargs)
        monkeypatch.setattr(anilist_mod, "anilist_client", ist)
        return ist

    return _kur


@pytest.fixture
def sahte_oauth(monkeypatch):
    """Yerel geri dönüş sunucusunu ve tarayıcıyı sahtele.

    `webbrowser.open` GERÇEKTEN çağrılırsa test makinesinde AniList giriş
    sayfası açılırdı; burada yalnızca çağrının kaydı tutulur.
    """
    import turkanime_api.anilist_client as anilist_mod
    from turkanime_api.gui.qt import anilist as anilist_qt

    sunucular: list = []
    acilan_urller: list = []

    class SahteAuthServer:
        def __init__(self, client):
            self.client = client
            self.on_success = None
            self.port = None
            sunucular.append(self)

        def register_on_success(self, cb):
            self.on_success = cb

        def start_server(self, port=9921):
            self.port = port

    monkeypatch.setattr(anilist_mod, "AniListAuthServer", SahteAuthServer)
    monkeypatch.setattr(anilist_qt.webbrowser, "open",
                        lambda url: acilan_urller.append(url) or True)
    return sunucular, acilan_urller


def media(title="Cowboy Bebop", anime_id=1, episodes=26):
    return {"id": anime_id,
            "title": {"romaji": title, "english": None, "native": None},
            "coverImage": {"large": None, "medium": None},
            "episodes": episodes}


def entry(title="Cowboy Bebop", anime_id=1, episodes=26, progress=5,
          score=85, status="CURRENT"):
    return {"media": media(title, anime_id, episodes), "progress": progress,
            "score": score, "status": status, "updatedAt": 0}


def lists(*entries, name="Watching"):
    """`MediaListCollection.lists` şeklinde sarmala."""
    return [{"name": name, "entries": list(entries)}]


def sayfa(qtbot, servis: AniListService) -> WatchlistPage:
    page = WatchlistPage(servis)
    qtbot.addWidget(page)
    return page


# ── Saf yardımcılar (Qt'siz) ────────────────────────────────────────────────
def test_girisler_kullanici_alanlarini_tasiyor():
    girisler = girisleri_duzlestir(lists(entry(progress=9, score=77)))
    assert len(girisler) == 1
    kayit = girisler[0]
    assert kayit["user_progress"] == 9
    assert kayit["user_score"] == 77
    assert kayit["user_status"] == "CURRENT"
    assert kayit["episodes"] == 26, "media alanları korunmalı"


def test_girisler_yanlis_durumu_suzuyor():
    """AniList özel listeleri karışık durum döndürebiliyor; sunucuya güvenme."""
    ham = lists(entry(title="Biten", status="COMPLETED"),
                entry(title="Devam", status="CURRENT"))
    assert [g["title"]["romaji"] for g in girisleri_duzlestir(ham, "CURRENT")] == ["Devam"]
    assert [g["title"]["romaji"] for g in girisleri_duzlestir(ham, "COMPLETED")] == ["Biten"]
    assert len(girisleri_duzlestir(ham)) == 2


def test_girisler_bozuk_veriyi_yutuyor():
    assert girisleri_duzlestir(None) == []
    assert girisleri_duzlestir([{"entries": [{"media": None}, "bozuk"]}]) == []
    assert girisleri_duzlestir(["bozuk"]) == []


def test_en_iyi_eslesme_dogru_kaydi_seciyor():
    adaylar = [media("Bleach", anime_id=1),
               media("Frieren: Beyond Journeys End", anime_id=2)]
    assert en_iyi_eslesme(adaylar, "Frieren")["id"] == 2


def test_en_iyi_eslesme_esik_altinda_none():
    """Yanlış animeye yazmaktansa hiç yazmamak: eşik altı `None` döner."""
    assert en_iyi_eslesme([media("Bleach", anime_id=1)], "Frieren") is None
    assert en_iyi_eslesme([], "Frieren") is None
    assert en_iyi_eslesme([media("Bleach")], "") is None


@pytest.mark.parametrize("slug,beklenen", [
    ("naruto-shippuuden", "naruto shippuuden"),
    ("cowboy_bebop", "cowboy bebop"),
    ("", ""),
])
def test_deslug(slug, beklenen):
    assert deslug(slug) == beklenen


@pytest.mark.parametrize("uri,beklenen", [
    ("http://localhost:9921/anilist-login", 9921),
    ("http://localhost:8080/anilist-login", 8080),
    ("", 9921),
    ("bozuk-uri", 9921),
])
def test_geri_donus_portu(uri, beklenen):
    assert geri_donus_portu(uri) == beklenen


def test_senkron_yalnizca_yereldeki_serileri_ilerletiyor():
    girisler = girisleri_duzlestir(
        lists(entry(title="Cowboy Bebop", progress=9),
              entry(title="Steins Gate", progress=4)))
    guncel = senkron_guncellemeleri(girisler, {"cowboy-bebop": 3})
    assert guncel == {"cowboy-bebop": 9}, "yerelde kaydı olmayan seri eklenmemeli"


def test_senkron_yerel_ilerlemeyi_geri_almiyor():
    """Kullanıcı çevrimdışı 12 bölüm izlediyse AniList'teki 9 onu ezmemeli."""
    girisler = girisleri_duzlestir(lists(entry(title="Cowboy Bebop", progress=9)))
    assert senkron_guncellemeleri(girisler, {"cowboy-bebop": 12}) == {}
    assert senkron_guncellemeleri(girisler, {"cowboy-bebop": "bozuk"}) == {"cowboy-bebop": 9}


# ── Listem sayfası ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("durum,etiket", DURUMLAR)
def test_her_durum_ayri_filtreleniyor(qtbot, sahte_anilist, durum, etiket):
    """Beş durumun her biri kendi listesini çekip göstermeli."""
    ist = sahte_anilist()
    for kod in DURUM_KODLARI:
        ist.listeler[kod] = lists(entry(title=f"{kod} Anime", status=kod))

    page = sayfa(qtbot, AniListService())
    page.cmbStatus.setCurrentIndex(DURUM_KODLARI.index(durum))
    page.refresh()

    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)
    assert page.durum() == durum
    assert page.cards()[0].lblTitle.text() == f"{durum} Anime"
    assert page.cards()[0].lblSource.text() == etiket
    assert ("list", 7, durum) in ist.cagrilar


def test_kart_alanlari_ilerleme_ve_skor(qtbot, sahte_anilist):
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(
        entry(title="Frieren", episodes=28, progress=7, score=92))

    page = sayfa(qtbot, AniListService())
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    kart = page.cards()[0]
    assert kart.lblProgress.text() == "İzlenen: 7/28"
    assert (kart.barProgress.maximum(), kart.barProgress.value()) == (28, 7)
    assert kart.lblScore.text() == "★ 92"
    assert kart.lblSource.text() == "İzliyorum"


def test_kart_bilinmeyen_toplam_ve_skorsuz(qtbot, sahte_anilist):
    """Yayını süren animede `episodes` boş gelir; çubuk dolu görünmemeli."""
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(
        entry(title="Devam Eden", episodes=None, progress=3, score=0))

    page = sayfa(qtbot, AniListService())
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    kart = page.cards()[0]
    assert kart.lblProgress.text() == "İzlenen: 3/?"
    assert kart.barProgress.value() == 0
    assert kart.lblScore.text() == "Puansız"


def test_izlenen_toplami_asarsa_cubuk_tasmiyor(qtbot, sahte_anilist):
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(episodes=12, progress=15))

    page = sayfa(qtbot, AniListService())
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)
    assert page.cards()[0].barProgress.value() == 12


def test_bos_liste_mesaji(qtbot, sahte_anilist):
    sahte_anilist()
    page = sayfa(qtbot, AniListService())
    page.refresh()
    qtbot.waitUntil(lambda: page.btnRefresh.isEnabled(), timeout=5000)
    assert page.cards() == []
    assert "anime yok" in page.lblStatus.text()


def test_giris_yokken_yonlendirme_gosteriliyor(qtbot, sahte_anilist):
    """Boş ekran değil, ne yapılacağını söyleyen panel çıkmalı."""
    ist = sahte_anilist(token=None)
    page = sayfa(qtbot, AniListService())
    page.show()

    assert page.pnlLogin.isVisibleTo(page)
    assert "Ayarlar" in page.lblLoginHint.text()
    assert not page.results.isVisibleTo(page)
    assert not page.btnRefresh.isEnabled()

    page.refresh()          # çökmemeli
    qtbot.wait(80)
    assert page.cards() == []
    assert ist.cagrilar == [], "giriş yokken ağ ucuna hiç gidilmemeli"


def test_ayarlara_yonlendirme_butonu(qtbot, main_window, sahte_anilist):
    sahte_anilist(token=None)
    page = main_window.pages["watchlist"]
    main_window.show_page("watchlist")
    page._apply_auth_state()

    qtbot.mouseClick(page.btnGoSettings, Qt.MouseButton.LeftButton)
    assert main_window.stack.currentWidget() is main_window.pages["settings"]


def test_kart_tiklamasi_detay_sayfasini_aciyor(qtbot, main_window, sahte_anilist):
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(title="Cowboy Bebop"))

    page = main_window.pages["watchlist"]
    main_window.show_page("watchlist")
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    qtbot.mouseClick(page.cards()[0], Qt.MouseButton.LeftButton)

    detay = main_window.pages["detail"]
    assert main_window.stack.currentWidget() is detay
    assert detay.lblTitle.text() == "Cowboy Bebop"


def test_kullanici_bilgisi_alinamayinca_liste_silinmiyor(qtbot, sahte_anilist):
    """Jeton dururken gelen `auth_changed(None)` "çıkış yapıldı" demek değil."""
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(title="Cowboy Bebop"))

    servis = AniListService()
    page = sayfa(qtbot, servis)
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    servis.auth_changed.emit(None)          # ağ dalgalanması
    assert len(page.cards()) == 1, "kartlar sebepsiz silinmemeli"

    ist.access_token = None
    servis.auth_changed.emit(None)          # gerçek çıkış
    assert page.cards() == []
    assert page.pnlLogin.isVisibleTo(page) or not page.results.isVisibleTo(page)


def test_otomatik_senkron_kart_sayisini_silmiyor(qtbot, sahte_anilist):
    """Girişten sonraki kendiliğinden senkron durum satırını ele geçirmemeli."""
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(title="Cowboy Bebop"))

    servis = AniListService()
    page = sayfa(qtbot, servis)
    page.refresh()
    qtbot.waitUntil(lambda: len(page.cards()) == 1, timeout=5000)

    servis.sync_done.emit(0)            # otomatik senkron, değişen yok
    assert page.lblStatus.text() == "1 anime"

    page._on_sync_clicked()             # kullanıcı istedi
    servis.sync_done.emit(0)
    assert "güncel" in page.lblStatus.text()


def test_liste_hatasi_bildiriliyor(qtbot, sahte_anilist):
    ist = sahte_anilist()

    def patla(user_id, status=None):
        raise RuntimeError("AniList 503")

    ist.get_user_anime_list = patla
    page = sayfa(qtbot, AniListService())
    page.refresh()
    qtbot.waitUntil(lambda: "503" in page.lblStatus.text(), timeout=5000)
    assert page.btnRefresh.isEnabled()


# ── İlerleme yazma ──────────────────────────────────────────────────────────
def test_ilerleme_girisliyken_anilist_e_yaziliyor(qtbot, sahte_anilist):
    ist = sahte_anilist()
    ist.arama_sonucu = [media("Frieren: Beyond Journeys End", anime_id=52991,
                              episodes=28)]

    servis = AniListService()
    assert servis.ilerleme_yaz("frieren-beyond-journeys-end", 7, "Frieren") is True

    qtbot.waitUntil(lambda: any(c[0] == "progress" for c in ist.cagrilar),
                    timeout=5000)
    assert ("search", "Frieren") in ist.cagrilar
    assert ("progress", 52991, 7, None) in ist.cagrilar


def test_ilerleme_giris_yokken_yazilmiyor(qtbot, sahte_anilist):
    """Yerel kayıt zaten yapıldı; giriş yoksa sessizce atlanmalı, hata yok."""
    ist = sahte_anilist(token=None)
    servis = AniListService()

    assert servis.ilerleme_yaz("frieren", 7, "Frieren") is False
    qtbot.wait(120)
    assert ist.cagrilar == []


def test_ilerleme_eslesme_yoksa_yazilmiyor(qtbot, sahte_anilist):
    """Arama alakasız kayıt döndürdüyse yanlış animenin ilerlemesi ezilmemeli."""
    ist = sahte_anilist()
    ist.arama_sonucu = [media("Bleach", anime_id=5)]

    servis = AniListService()
    servis.ilerleme_yaz("frieren", 7, "Frieren")

    qtbot.waitUntil(lambda: any(c[0] == "search" for c in ist.cagrilar),
                    timeout=5000)
    qtbot.wait(150)
    assert not any(c[0] == "progress" for c in ist.cagrilar)


def test_ayni_seri_ikinci_kez_aranmiyor(qtbot, sahte_anilist):
    ist = sahte_anilist()
    ist.arama_sonucu = [media("Cowboy Bebop", anime_id=1)]
    servis = AniListService()

    servis.ilerleme_yaz("cowboy-bebop", 3, "Cowboy Bebop")
    qtbot.waitUntil(lambda: any(c[0] == "progress" for c in ist.cagrilar),
                    timeout=5000)
    servis.ilerleme_yaz("cowboy-bebop", 4, "Cowboy Bebop")
    qtbot.waitUntil(
        lambda: len([c for c in ist.cagrilar if c[0] == "progress"]) == 2,
        timeout=5000)

    assert len([c for c in ist.cagrilar if c[0] == "search"]) == 1


def test_ana_pencere_ilerlemeyi_anilist_e_gonderiyor(qtbot, main_window,
                                                     sahte_anilist,
                                                     preserved_gecmis):
    """`_on_progress_saved` bağlantı noktası AniList'e ulaşmalı."""
    ist = sahte_anilist()
    ist.arama_sonucu = [media("Naruto Test", anime_id=99, episodes=220)]

    main_window._on_progress_saved("naruto-test", 5, "Naruto Test")

    qtbot.waitUntil(lambda: any(c[0] == "progress" for c in ist.cagrilar),
                    timeout=5000)
    assert ("progress", 99, 5, None) in ist.cagrilar


def test_dialog_seri_adini_anilist_aramasina_tasiyor(qtbot, main_window,
                                                     sahte_anilist, monkeypatch,
                                                     preserved_gecmis):
    """Sinyal slug taşıyor; AniList'te "naruto-test" diye aramak eşleşmezdi."""
    ist = sahte_anilist()
    ist.arama_sonucu = [media("Naruto Test", anime_id=99)]

    acilan: list = []
    monkeypatch.setattr(ProgressDialog, "exec",
                        lambda self: acilan.append(self) or 0)

    main_window._ask_progress(SahteBolum(), "Naruto Test 5. Bölüm")
    dialog = acilan[0]
    dialog.spnEpisode.setValue(5)
    dialog.save()

    qtbot.waitUntil(lambda: any(c[0] == "progress" for c in ist.cagrilar),
                    timeout=5000)
    assert ("search", "Naruto Test") in ist.cagrilar
    assert ("progress", 99, 5, None) in ist.cagrilar


# ── AniList → yerel senkron ─────────────────────────────────────────────────
def test_senkron_yerel_ilerlemeye_yaziyor(qtbot, sahte_anilist, preserved_gecmis):
    from turkanime_api.cli.dosyalar import Dosyalar

    Dosyalar().set_ilerleme("cowboy-bebop", 3)
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(title="Cowboy Bebop", progress=9))

    servis = AniListService()
    bitti: list = []
    servis.sync_done.connect(bitti.append)
    assert servis.yereli_senkronla() is True

    qtbot.waitUntil(lambda: bool(bitti), timeout=5000)
    assert prefs.yerel_ilerleme()["cowboy-bebop"] == 9


def test_senkron_giris_yokken_calismiyor(qtbot, sahte_anilist):
    ist = sahte_anilist(token=None)
    assert AniListService().yereli_senkronla() is False
    qtbot.wait(100)
    assert ist.cagrilar == []


# ── OAuth ───────────────────────────────────────────────────────────────────
def test_oauth_tarayiciyi_aciyor_ve_sunucuyu_baslatiyor(qtbot, sahte_anilist,
                                                        sahte_oauth):
    sahte_anilist(token=None)
    sunucular, acilan = sahte_oauth

    servis = AniListService()
    assert servis.giris_yap() is True

    qtbot.waitUntil(lambda: bool(acilan) and bool(sunucular)
                    and sunucular[0].port is not None, timeout=5000)
    assert acilan[0].startswith("https://anilist.co/api/v2/oauth/authorize")
    assert "response_type=code" in acilan[0]
    assert sunucular[0].port == 9921, "port redirect URI'den çözülmeli"
    assert callable(sunucular[0].on_success)


def test_oauth_port_redirect_uriden_geliyor(qtbot, sahte_anilist, sahte_oauth):
    ist = sahte_anilist(token=None)
    ist.redirect_uri = "http://localhost:8080/anilist-login"
    sunucular, _ = sahte_oauth

    AniListService().giris_yap()
    qtbot.waitUntil(lambda: sunucular and sunucular[0].port is not None,
                    timeout=5000)
    assert sunucular[0].port == 8080


def test_oauth_basarisi_kullaniciyi_yansitiyor(qtbot, sahte_anilist, sahte_oauth):
    ist = sahte_anilist(token=None)
    sunucular, _ = sahte_oauth

    servis = AniListService()
    goruldu: list = []
    servis.auth_changed.connect(goruldu.append)
    servis.giris_yap()
    qtbot.waitUntil(lambda: bool(sunucular), timeout=5000)

    # Gerçek akışta bu noktada sunucu jetonu almış olur.
    ist.access_token = "yeni-jeton"
    sunucular[0].on_success()

    qtbot.waitUntil(lambda: any(isinstance(u, dict) for u in goruldu),
                    timeout=5000)
    assert servis.kullanici["name"] == "kullanici"


def test_oauth_basarisi_headeri_guncelliyor(qtbot, main_window, sahte_anilist,
                                            sahte_oauth, preserved_gecmis):
    """Başarı callback'i HTTP handler thread'inden gelir; UI sinyalle güncellenir."""
    ist = sahte_anilist(token=None)
    sunucular, acilan = sahte_oauth

    main_window.anilist.giris_yap()
    qtbot.waitUntil(lambda: bool(acilan) and bool(sunucular), timeout=5000)

    ist.access_token = "yeni-jeton"
    threading.Thread(target=sunucular[0].on_success, daemon=True).start()

    qtbot.waitUntil(lambda: main_window.lblAniList.text() == "kullanici",
                    timeout=5000)


def test_oauth_client_id_yoksa_tarayici_acilmiyor(qtbot, sahte_anilist,
                                                  sahte_oauth):
    ist = sahte_anilist(token=None)
    ist.client_id = ""
    sunucular, acilan = sahte_oauth

    servis = AniListService()
    mesajlar: list = []
    servis.status_changed.connect(lambda m, h: mesajlar.append((m, h)))

    assert servis.giris_yap() is False
    assert acilan == [] and sunucular == []
    assert mesajlar and mesajlar[0][1] is True


def test_client_secret_mesajlara_sizmiyor(qtbot, sahte_anilist, sahte_oauth):
    """Hata mesajı kullanıcıya ve loglara gidiyor; gizli anahtar oraya girmemeli."""
    ist = sahte_anilist(token=None)
    _sunucular, acilan = sahte_oauth

    def patla(**_kw):
        raise RuntimeError("auth url üretilemedi")

    ist.get_auth_url = patla
    servis = AniListService()
    mesajlar: list = []
    servis.status_changed.connect(lambda m, h: mesajlar.append(m))

    assert servis.giris_yap() is False
    assert acilan == []
    assert mesajlar and all("COK-GIZLI-ANAHTAR" not in m for m in mesajlar)


def test_cikis_jetonu_siliyor(qtbot, sahte_anilist):
    ist = sahte_anilist()
    servis = AniListService()
    goruldu: list = []
    servis.auth_changed.connect(goruldu.append)

    servis.cikis_yap()
    assert ist.access_token is None
    assert goruldu == [None]
    assert servis.giris_var_mi() is False


# ── Ayarlar sayfası ─────────────────────────────────────────────────────────
def test_ayarlar_anilist_alanlarini_dolduruyor(qtbot, sahte_anilist):
    ist = sahte_anilist()
    page = SettingsPage(AniListService())
    qtbot.addWidget(page)

    assert page.txtAniListId.text() == ist.client_id
    assert page.txtAniListRedirect.text() == ist.redirect_uri
    assert page.txtAniListSecret.echoMode() == QLineEdit.EchoMode.Password


def test_ayarlar_anilist_alanlari_kaydediliyor(qtbot, sahte_anilist,
                                               preserved_settings):
    ist = sahte_anilist()
    page = SettingsPage(AniListService())
    qtbot.addWidget(page)

    page.txtAniListId.setText("12345")
    page.txtAniListSecret.setText("yeni-secret")
    page.txtAniListRedirect.setText("http://localhost:9999/anilist-login")
    page.save()

    assert ("config", "12345", "yeni-secret",
            "http://localhost:9999/anilist-login") in ist.cagrilar
    assert "kaydedildi" in page.lblStatus.text()


def test_ayarlar_giris_butonu_once_yapilandirmayi_kaydediyor(qtbot, sahte_anilist,
                                                             sahte_oauth):
    """Yeni yapıştırılan Client ID yok sayılıp eski ayarla giriş denenmemeli."""
    ist = sahte_anilist(token=None)
    _sunucular, acilan = sahte_oauth

    page = SettingsPage(AniListService())
    qtbot.addWidget(page)
    page.txtAniListId.setText("777")
    page._anilist_login()

    qtbot.waitUntil(lambda: bool(acilan), timeout=5000)
    assert ("config", "777", ist.client_secret, ist.redirect_uri) in ist.cagrilar
    assert "client_id=777" in acilan[0]


def test_oauth_secretsiz_istemci_implicit_akisa_dusuyor(qtbot, sahte_anilist,
                                                        sahte_oauth):
    """Secret yoksa jeton fragment'la gelmeli: `response_type=token`."""
    ist = sahte_anilist(token=None)
    ist.client_secret = ""
    _sunucular, acilan = sahte_oauth

    assert AniListService().giris_yap() is True
    qtbot.waitUntil(lambda: bool(acilan), timeout=5000)
    assert "response_type=token" in acilan[0]
    assert "response_type=code" not in acilan[0]


def test_ayarlar_secret_alani_opsiyonel_oldugunu_soyluyor(qtbot, sahte_anilist):
    """Alan artık zorunlu değil; kullanıcı boş bırakabileceğini görmeli."""
    sahte_anilist()
    page = SettingsPage(AniListService())
    qtbot.addWidget(page)

    assert "opsiyonel" in page.txtAniListSecret.placeholderText().lower()
    ipucu = page.lblAniListSecretIpucu.text().lower()
    assert "opsiyonel" in ipucu and "implicit" in ipucu


def test_ayarlar_bos_secret_kaydedilebiliyor(qtbot, sahte_anilist,
                                             preserved_settings):
    """Secret'ı silip kaydetmek hata vermemeli (Implicit akışa geçiş)."""
    ist = sahte_anilist()
    page = SettingsPage(AniListService())
    qtbot.addWidget(page)

    page.txtAniListSecret.setText("")
    page.save()

    assert ("config", ist.client_id, "", ist.redirect_uri) in ist.cagrilar
    assert "kaydedildi" in page.lblStatus.text()


def test_ayarlar_cikis_durumu_gosteriyor(qtbot, sahte_anilist):
    ist = sahte_anilist()
    page = SettingsPage(AniListService())
    qtbot.addWidget(page)

    page._anilist_logout()
    assert ist.access_token is None
    assert "Giriş yapılmamış" in page.lblAniList.text()


def test_ayarlar_giris_durumunu_yansitiyor(qtbot, sahte_anilist):
    sahte_anilist()
    servis = AniListService()
    page = SettingsPage(servis)
    qtbot.addWidget(page)

    servis.auth_changed.emit({"id": 7, "name": "kullanici"})
    assert "kullanici" in page.lblAniList.text()
