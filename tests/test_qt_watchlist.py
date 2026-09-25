"""AniList entegrasyonu: OAuth, İzleme Listem sayfası, ilerleme senkronu.

Sayfalar web'de: İzleme Listem `web.uclar_izleme` + `izleme.js`, AniList
ayarları `web.uclar_ayarlar`. Kart/durum mantığı Python'da sınanıyor, sayfa
davranışı (sekmeler, giriş paneli, hata) web sürücüsüyle.

Hiçbir test ağa çıkmaz ve **gerçek OAuth akışı asla açılmaz**: `anilist_client`
singleton'ı tümüyle sahtelenir, `AniListAuthServer` yerine sayaç tutan bir ikiz
konur, `webbrowser.open` sahtelenip yalnızca çağrıldığı doğrulanır.
"""
from __future__ import annotations

import threading

import pytest

from turkanime_api.gui.qt import prefs
from turkanime_api.gui.qt.anilist import (
    DURUMLAR, AniListService, baslik_skoru, deslug, en_iyi_eslesme,
    geri_donus_portu, girisleri_duzlestir, senkron_guncellemeleri,
)
from turkanime_api.gui.qt.progress_dialog import ProgressDialog
from turkanime_api.gui.web.uclar_izleme import izleme_karti

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


KARTLAR = ("document.querySelectorAll('[data-sayfa=watchlist] .izgara "
           ".kart:not(.iskelet-kart)')")
SAYFA = "document.querySelector('[data-sayfa=watchlist]')"


def listeyi_ac(main_window, web, kart_sayisi=None):
    """Giriş durumunu yükle, İzleme Listem'i aç (isteğe bağlı kart bekle)."""
    main_window.anilist.baslat()
    main_window.show_page("watchlist")
    if kart_sayisi is not None:
        web.bekle(f"{KARTLAR}.length === {kart_sayisi}")


def kart_verisi(**kw):
    """Sahte AniList girişinden sayfaya giden kart verisi."""
    return izleme_karti(girisleri_duzlestir(lists(entry(**kw)))[0])


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


# ── Alt-dize eşleşmesi (yanlış seriye yazma) ────────────────────────────────
def test_alt_dizi_baslik_tam_puan_almiyor():
    """"naruto" ⊂ "Naruto: Shippuuden" olduğu için skor 1.0 dönüyordu.

    İki kayıt aynı puanı alınca kazananı AniList'in liste sırası belirliyor,
    ilerleme sessizce sezon kaydına yazılabiliyordu.
    """
    assert baslik_skoru("naruto", "Naruto") == 1.0
    assert baslik_skoru("naruto", "Naruto: Shippuuden") < 1.0
    assert (baslik_skoru("naruto", "Naruto: Shippuuden")
            < baslik_skoru("naruto", "Naruto"))


def test_eslesme_sezon_kaydini_ana_serinin_onune_gecirmiyor():
    """Sezon kaydı listede ÖNCE gelse bile "naruto" ana seriyi bulmalı."""
    adaylar = [media("Naruto: Shippuuden", anime_id=1735, episodes=500),
               media("Naruto", anime_id=20, episodes=220)]
    assert en_iyi_eslesme(adaylar, "naruto")["id"] == 20
    assert en_iyi_eslesme(adaylar, "naruto shippuuden")["id"] == 1735


def test_senkron_sezon_ilerlemesini_ana_seriye_yazmiyor():
    """Yerelde 7. bölümde olan "naruto", Shippuuden'in 500'ünü almamalı."""
    girisler = girisleri_duzlestir(
        lists(entry(title="Naruto: Shippuuden", anime_id=1735, episodes=500,
                    progress=500),
              entry(title="Naruto", anime_id=20, episodes=220, progress=7)))
    assert senkron_guncellemeleri(girisler, {"naruto": 7}) == {}
    assert senkron_guncellemeleri(girisler, {"naruto-shippuuden": 100}) == {
        "naruto-shippuuden": 500}


def test_senkron_bolum_sayisini_asan_ilerlemeyi_yazmiyor():
    """26 bölümlük seride 500 ilerleme = eşleşme şaşmış; yerel kayıt korunur."""
    girisler = girisleri_duzlestir(
        lists(entry(title="Cowboy Bebop", episodes=26, progress=500)))
    assert senkron_guncellemeleri(girisler, {"cowboy-bebop": 7}) == {}


# ── Listem sayfası ──────────────────────────────────────────────────────────
def test_her_durum_ayri_filtreleniyor(main_window, web, sahte_anilist):
    """Beş durumun her biri kendi listesini çekip göstermeli."""
    ist = sahte_anilist()
    for kod in DURUM_KODLARI:
        ist.listeler[kod] = lists(entry(title=f"{kod} Anime", status=kod))
    listeyi_ac(main_window, web, 1)

    for kod, etiket in DURUMLAR:
        web.js(f"document.querySelector('.sekme[data-durum={kod}]').click()")
        web.bekle(f"{KARTLAR}.length === 1 && {KARTLAR}[0]"
                  f".querySelector('.kart-baslik').textContent === '{kod} Anime'")
        assert web.js(f"{KARTLAR}[0].querySelector('.kart-rozet').textContent") == etiket
        assert web.js(f"document.querySelector('.sekme.secili').dataset.durum") == kod
        assert ("list", 7, kod) in ist.cagrilar


def test_kart_alanlari_ilerleme_ve_skor():
    kart = kart_verisi(title="Frieren", episodes=28, progress=7, score=92)
    assert kart["baslik"] == "Frieren"
    assert kart["alt"] == "İzlenen: 7/28 · ★ 92"
    assert kart["ilerleme"] == 0.25
    assert kart["rozet"] == "İzliyorum"


def test_kart_bilinmeyen_toplam_ve_skorsuz():
    """Yayını süren animede `episodes` boş gelir; çubuk dolu görünmemeli."""
    kart = kart_verisi(title="Devam Eden", episodes=None, progress=3, score=0)
    assert kart["alt"] == "İzlenen: 3/?"
    assert kart["ilerleme"] is None


def test_izlenen_toplami_asarsa_cubuk_tasmiyor():
    assert kart_verisi(episodes=12, progress=15)["ilerleme"] == 1.0


def test_bos_liste_mesaji(main_window, web, sahte_anilist):
    sahte_anilist()
    listeyi_ac(main_window, web)
    web.bekle(f"{SAYFA}.innerText.includes('Bu listede anime yok')")
    assert web.js(f"{KARTLAR}.length") == 0


def test_giris_yokken_yonlendirme_gosteriliyor(main_window, web, sahte_anilist):
    """Boş ekran değil, ne yapılacağını söyleyen panel çıkmalı."""
    ist = sahte_anilist(token=None)
    listeyi_ac(main_window, web)

    web.bekle("document.querySelector('.giris-paneli').innerText.includes('Ayarlar')")
    assert web.js(f"{SAYFA}.querySelector('.sekmeler').hidden") is True
    dugmeler = f"[...{SAYFA}.querySelectorAll('.sayfa-eylem button')]"
    assert web.js(f"{dugmeler}.every(d => d.disabled)") is True
    assert web.js(f"{KARTLAR}.length") == 0
    assert not any(c[0] == "list" for c in ist.cagrilar), \
        "giriş yokken liste ucuna hiç gidilmemeli"


def test_ayarlara_yonlendirme_butonu(qtbot, main_window, web, sahte_anilist):
    """Girişsiz liste sayfası nereye gidileceğini söylüyor ve götürüyor."""
    sahte_anilist(token=None)
    main_window.show_page("watchlist")
    web.bekle("document.querySelector('.giris-paneli').innerText.includes('AniList girişi gerekli')")
    web.js("document.querySelector('.giris-paneli button').click()")
    qtbot.waitUntil(lambda: main_window._current_page == "settings", timeout=5000)
    web.bekle("TA.aktif === 'settings'")
    # Üst çubuktaki dişli seçili görünüyor.
    web.bekle("document.querySelector('.ust-ayar').classList.contains('aktif')")


def test_kart_tiklamasi_detay_sayfasini_aciyor(qtbot, main_window, web, sahte_anilist):
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(title="Cowboy Bebop"))

    listeyi_ac(main_window, web, 1)
    web.js(f"{KARTLAR}[0].click()")
    qtbot.waitUntil(lambda: main_window._current_page == "detail", timeout=5000)

    web.bekle("TA.aktif === 'detail' && !!document.querySelector('.detay-bilgi h1')")
    assert web.js("document.querySelector('.detay-bilgi h1').textContent") == "Cowboy Bebop"


def test_kullanici_bilgisi_alinamayinca_liste_silinmiyor(main_window, web, sahte_anilist):
    """Jeton dururken gelen `auth_changed(None)` "çıkış yapıldı" demek değil."""
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(title="Cowboy Bebop"))
    listeyi_ac(main_window, web, 1)

    main_window.anilist.auth_changed.emit(None)          # ağ dalgalanması
    web.bekle(f"{KARTLAR}.length === 1")
    assert web.js("document.querySelector('.giris-paneli').childElementCount") == 0

    ist.access_token = None
    main_window.anilist.auth_changed.emit(None)          # gerçek çıkış
    web.bekle(f"{KARTLAR}.length === 0 && "
              "document.querySelector('.giris-paneli').childElementCount === 1")


def test_otomatik_senkron_durum_satirini_ezmiyor(main_window, web, sahte_anilist):
    """Girişten sonraki kendiliğinden senkron durum satırını ele geçirmemeli;
    kullanıcının başlattığı senkron ise bitince "sürüyor"da kalmamalı."""
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(entry(title="Cowboy Bebop"))
    listeyi_ac(main_window, web, 1)
    durum = f"{SAYFA}.querySelector('.sayfa-eylem .durum')"
    web.bekle(f"{durum}.textContent === '1 anime'")

    main_window.anilist.sync_done.emit(0)            # otomatik senkron, değişen yok
    web.qtbot.wait(100)
    assert web.js(f"{durum}.textContent") == "1 anime"
    assert not web.js("!!document.querySelector('.bildirim')")

    web.js(f"[...{SAYFA}.querySelectorAll('.sayfa-eylem button')]"
           ".find(d => d.textContent.includes('Senkronize')).click()")
    web.bekle(f"{durum}.classList.contains('suruyor') || "
              f"{durum}.textContent.includes('güncel')")
    main_window.anilist.sync_done.emit(0)
    web.bekle(f"{durum}.textContent.includes('güncel') && "
              f"!{durum}.classList.contains('suruyor')")


def test_liste_hatasi_bildiriliyor(main_window, web, sahte_anilist):
    ist = sahte_anilist()

    def patla(user_id, status=None):
        raise RuntimeError("AniList 503")

    ist.get_user_anime_list = patla
    listeyi_ac(main_window, web)
    web.bekle(f"!!{SAYFA}.querySelector('.bos-durum.hata') && "
              f"{SAYFA}.querySelector('.bos-durum.hata').innerText.includes('503')")
    yenile = (f"[...{SAYFA}.querySelectorAll('.sayfa-eylem button')]"
              ".find(d => d.textContent.includes('Yenile'))")
    assert web.js(f"{yenile}.disabled") is False


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


def test_senkron_yerel_ilerlemeyi_yanlis_seriyle_ezmiyor(qtbot, sahte_anilist,
                                                         preserved_gecmis):
    """Girişten sonraki otomatik senkron yerel 7'yi Shippuuden'in 500'ü yapıyordu.

    Uçtan uca: `gecmis.json`'a yazılan değer gerçekten değişmemeli.
    """
    from turkanime_api.cli.dosyalar import Dosyalar

    Dosyalar().set_ilerleme("naruto", 7)
    ist = sahte_anilist()
    ist.listeler["CURRENT"] = lists(
        entry(title="Naruto: Shippuuden", anime_id=1735, episodes=500,
              progress=500),
        entry(title="Naruto", anime_id=20, episodes=220, progress=7))

    servis = AniListService()
    bitti: list = []
    servis.sync_done.connect(bitti.append)
    assert servis.yereli_senkronla() is True

    qtbot.waitUntil(lambda: bool(bitti), timeout=5000)
    assert prefs.yerel_ilerleme()["naruto"] == 7


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

    qtbot.waitUntil(lambda: getattr(main_window, "anilist_kullanici", {}).get("ad")
                    == "kullanici",
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


# ── Ayarlar sayfası (AniList bölümü) ────────────────────────────────────────
@pytest.fixture
def ayarlar(ayar_uclari, sahte_anilist):
    """AniList bölümünün Python tarafı; ``kur(**istemci)`` sahte istemciyi de kurar."""
    def kur(**kw):
        ist = sahte_anilist(**kw)
        servis = AniListService()
        u = ayar_uclari(anilist=servis)
        return u, ist, servis
    return kur


def test_ayarlar_anilist_alanlarini_dolduruyor(ayarlar):
    u, ist, _ = ayarlar()
    a = u.ayarlar()["anilist"]
    assert a["client_id"] == ist.client_id
    assert a["redirect_uri"] == ist.redirect_uri
    assert a["client_secret"] == ist.client_secret
    assert a["giris"] is True


def test_ayarlar_anilist_alanlari_kaydediliyor(ayarlar, preserved_settings):
    u, ist, _ = ayarlar()

    sonuc = u.ayarlari_kaydet({}, anilist={
        "client_id": "12345", "client_secret": "yeni-secret",
        "redirect_uri": "http://localhost:9999/anilist-login"})

    assert ("config", "12345", "yeni-secret",
            "http://localhost:9999/anilist-login") in ist.cagrilar
    assert "kaydedildi" in sonuc["mesaj"]


def test_ayarlar_giris_butonu_once_yapilandirmayi_kaydediyor(qtbot, ayarlar, sahte_oauth):
    """Yeni yapıştırılan Client ID yok sayılıp eski ayarla giriş denenmemeli."""
    u, ist, _ = ayarlar(token=None)
    _sunucular, acilan = sahte_oauth

    assert u.anilist_giris("777", ist.client_secret, ist.redirect_uri) is True

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


def test_ayarlar_secret_alani_sayfada(main_window, web, sahte_anilist, izole_ev):
    """Alan zorunlu değil (kullanıcı boş bırakabileceğini görmeli) ve gizli."""
    sahte_anilist()
    main_window.show_page("settings")
    alan = "document.querySelector('input[placeholder=\"Client Secret (opsiyonel)\"]')"
    web.bekle(f"!!{alan}", timeout=8000)
    assert web.js(f"{alan}.type") == "password"
    ipucu = web.js(f"{alan}.closest('.ayar-deger').querySelector('.ipucu').textContent")
    assert "opsiyonel" in ipucu.lower() and "implicit" in ipucu.lower()
    assert "sızmış" not in ipucu.lower()


def test_ayarlar_bos_secret_kaydedilebiliyor(ayarlar, preserved_settings):
    """Secret'ı silip kaydetmek hata vermemeli (Implicit akışa geçiş)."""
    u, ist, _ = ayarlar()

    sonuc = u.ayarlari_kaydet({}, anilist={
        "client_id": ist.client_id, "client_secret": "",
        "redirect_uri": ist.redirect_uri})

    assert ("config", ist.client_id, "", ist.redirect_uri) in ist.cagrilar
    assert "kaydedildi" in sonuc["mesaj"]


def test_ayarlar_sizan_secret_temizligini_duyuruyor(ayarlar, sahte_anilist):
    """Sessiz temizlik "secret'ım nereye gitti?" sorusuyla baş başa bırakırdı."""
    u, ist, _ = ayarlar()
    ist.client_secret = ""
    ist.sizan_secret_temizlendi = True

    uyari = u.ayarlar()["anilist"]["sizan_uyari"].lower()
    assert "sızmış" in uyari and "silindi" in uyari
    assert "implicit" in uyari


def test_ayarlar_temizlik_yokken_uyari_yok(ayarlar):
    u, _, _ = ayarlar()
    assert u.ayarlar()["anilist"]["sizan_uyari"] == ""


def test_ayarlar_cikis_durumu_gosteriyor(ayarlar):
    u, ist, _ = ayarlar()

    durum = u.anilist_cikis()

    assert ist.access_token is None
    assert durum["giris"] is False and "Giriş yapılmamış" in durum["metin"]


def test_ayarlar_giris_durumunu_yansitiyor(ayarlar):
    u, _, servis = ayarlar()

    servis.auth_changed.emit({"id": 7, "name": "kullanici"})

    durum = u.kopru.son("ayar_anilist")
    assert durum["giris"] is True and "kullanici" in durum["metin"]
