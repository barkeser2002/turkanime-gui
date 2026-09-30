"""Ana pencere: sayfa yönlendirme, indirme klasörü, temiz kapanış."""
from __future__ import annotations

import os

import pytest

from turkanime_api.gui.qt.app import NAV_ITEMS, MainWindow


def test_window_builds_all_pages(main_window):
    """Pencerenin tamamı tek web görünümü; her sayfa onun bir rotası."""
    for key, _label in NAV_ITEMS:
        assert main_window.pages[key] is main_window.web, f"{key} rotası yok"
    # Detay (künye + bölümler) menüde yok ama web görünümünde bir rota.
    assert main_window.pages["detail"] is main_window.web
    assert "episodes" not in main_window.pages
    assert main_window.centralWidget() is main_window.web
    # Qt durum çubuğu gizli: mesajlar sayfanın alt çubuğunda.
    assert not main_window.statusBar().isVisible()


def test_page_switching(main_window, web):
    for key in ("downloads", "settings", "search", "home"):
        main_window.show_page(key)
        assert main_window._current_page == key
        web.bekle(f"TA.aktif === {key!r}")
    # Menü üst çubukta; seçili öğe işaretli.
    web.bekle("document.querySelector('.menu-ogesi[data-git=home]').classList.contains('aktif')")


def test_ust_cubuk_menusu_ve_arama(main_window, web, sahte_arama):
    """Menü tıklaması Python'dan geçiyor; arama kutusu kaynak seçebiliyor."""
    sorgular = sahte_arama(sonuclar={"AnimeciX": [{"slug": "1", "title": "Naruto"}]})
    web.js("document.querySelector('.menu-ogesi[data-git=library]').click()")
    web.qtbot.waitUntil(lambda: main_window._current_page == "library", timeout=5000)
    web.bekle("document.querySelectorAll('.ust-kaynak option').length > 5")
    web.js("var f = document.querySelector('.ust-ara'); f.querySelector('input').value = 'naruto';"
           "f.querySelector('select').value = 'AnimeciX'; f.requestSubmit()")
    web.qtbot.waitUntil(lambda: sorgular == ["naruto"], timeout=5000)
    web.bekle("document.querySelector('[data-sayfa=search] .sayfa-baslik p').textContent"
              ".includes('yalnızca AnimeciX')")


def test_durum_mesaji_alt_cubukta(main_window, web):
    main_window.statusBar().showMessage("merhaba dünya", 0)
    web.bekle("document.querySelector('.durum-metni').textContent === 'merhaba dünya'")
    main_window._hata_durumu("oynatılamadı: kaynak yok")
    web.bekle("document.querySelector('.durum-metni').classList.contains('hata')")
    assert web.js("document.querySelector('.surum').textContent").startswith("v")


def test_search_from_header_routes_to_search_page(main_window, web, sahte_arama):
    """Aramaya basınca arama sayfasına geçilmeli; sayfa aramayı başlatır."""
    sorgular = sahte_arama(sonuclar={"TürkAnime": [{"slug": "naruto", "title": "Naruto"}]})

    main_window.ara("naruto")

    assert main_window._current_page == "search"
    web.bekle("TA.aktif === 'search'")
    web.qtbot.waitUntil(lambda: sorgular == ["naruto"], timeout=5000)
    assert web.js("document.querySelector('.arama-cubugu input').value") == "naruto"
    web.bekle("document.querySelector('.ust-ara input').value === 'naruto'")


def test_empty_search_is_ignored(main_window, web, sahte_arama):
    sorgular = sahte_arama()
    main_window.ara("   ")
    web.qtbot.wait(200)
    assert sorgular == []
    assert main_window._current_page == "home"


def test_download_dir_never_empty_or_cwd():
    """Boş string yt-dlp'de çalışma dizini demek.

    Paketlenmiş uygulamada bu `Program Files` altı olur: ya yazma izni yok ya da
    indirilenler kaybolur. Ayar okunamasa bile gerçek bir dizin dönmeli.
    """
    d = MainWindow._download_dir()
    assert d, "boş string döndü"
    assert os.path.isabs(d)
    assert os.path.isdir(d)


def test_download_dir_falls_back_when_settings_broken(monkeypatch):
    import turkanime_api.cli.dosyalar as dosyalar_mod

    class Bozuk:
        def __init__(self):
            raise OSError("ayar dosyası bozuk")

    monkeypatch.setattr(dosyalar_mod, "Dosyalar", Bozuk)
    d = MainWindow._download_dir()
    assert os.path.isdir(d)
    assert os.path.abspath(d) != os.path.abspath(os.getcwd())


# ── Kapanış: süreç gerçekten çıkmalı ─────────────────────────────────────────
COCUK_KAPANIS = """
import os, sys, time
sys.path.insert(0, {repo!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from turkanime_api.gui.qt import app as app_mod
from turkanime_api.gui.qt.workers import run_bg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow


class SahtePencere(QMainWindow):
    # Gerçek MainWindow'un yerine: uzun bir indirme başlatıp kendini kapatır.
    def __init__(self):
        super().__init__()
        run_bg(lambda: time.sleep(25), long_running=True)
        QTimer.singleShot(600, self.close)


app_mod.MainWindow = SahtePencere
sys.exit(app_mod.run())
"""


def test_uzun_is_surerken_surec_asili_kalmiyor(tmp_path):
    """Ölçüldü: pencere kapansa da süreç indirme bitene kadar çıkmıyordu.

    `~QThreadPool` yıkıcısı zaman aşımsız `waitForDone()` çağırıyor; 20 sn'lik
    sahte bir indirmeyle süreç 20,7 sn sonra çıkıyordu (beklenen ~3,7 sn).
    """
    import subprocess
    import sys
    import time

    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    betik = tmp_path / "kapanis_cocugu.py"
    betik.write_text(COCUK_KAPANIS.format(repo=kok), encoding="utf-8")

    t0 = time.time()
    proc = subprocess.Popen([sys.executable, str(betik)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("süreç 20 sn içinde çıkmadı: uzun iş kapanışı kilitliyor")
    sure = time.time() - t0
    assert sure < 15, f"kapanış {sure:.1f} sn sürdü — uzun işi bekliyor"


# ── İndirme bölüm listesinden koparmıyor; aynı bölüm iki kez kuyruğa girmiyor ─
class _Anime:
    slug = "naruto-test"
    title = "Naruto Test"


class _Bolum:
    def __init__(self, slug):
        self.slug = slug
        self.anime = _Anime()


def _giris(no: int):
    return {"title": f"{no}. Bölüm", "obj": _Bolum(f"naruto-test-{no}-bolum")}


@pytest.fixture
def indirme_penceresi(izole_ev, main_window, monkeypatch, tmp_path):
    """İndirme işleri başlamaz (bekliyor'da kalır)."""
    import turkanime_api.gui.qt.indirme as dl_mod

    monkeypatch.setattr(dl_mod, "run_bg", lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "_download_dir",
                        staticmethod(lambda: str(tmp_path / "indir")))
    return main_window


def test_indir_bolum_listesinde_birakiyor_menude_sayac(indirme_penceresi, qtbot):
    """ESKİ HATA: "İndir" İndirilenler'e geçiyordu; bölüm listesinin menüde
    düğmesi ve "Geri"si olmadığından kullanıcı listeye dönemiyordu."""
    win = indirme_penceresi
    win.show_page("detail")
    onceki = win._current_page

    win._on_download(_giris(1))

    assert win._current_page == onceki
    qtbot.waitUntil(lambda: getattr(win, "indirme_sayisi", 0) == 1, timeout=2000)
    assert "sırasına alındı" in win.statusBar().currentMessage()

    win.downloads.cancel_all()
    qtbot.waitUntil(lambda: win.indirme_sayisi == 0, timeout=2000)


def test_ayni_bolum_ikinci_kez_indirilince_zaten_kuyrukta(indirme_penceresi):
    win = indirme_penceresi
    giris = _giris(1)

    win._on_download(giris)
    win._on_download({"title": giris["title"], "obj": _Bolum(giris["obj"].slug)})

    assert len(win.downloads.active_ids()) == 1
    assert "zaten kuyrukta" in win.statusBar().currentMessage()


def test_toplu_indirme_sayfada_kaliyor_tekrari_sayiyor(indirme_penceresi, web,
                                                      sahte_bolumler):
    """Detay sayfasında seçilenleri indir: sayfada kalınır, ikinci basışta
    kuyruktakiler sayılır ve yeniden eklenmez."""
    win = indirme_penceresi
    sahte_bolumler({"TürkAnime": [_giris(i) for i in (1, 2, 3)]})
    win._on_anime_selected("TürkAnime", "naruto-test", "Naruto Test")
    web.detay_bekle("TürkAnime", 3)
    web.js("Array.from(document.querySelectorAll('.ak-arac button'))"
           ".find(b => b.textContent === 'Tümü').click()")
    indir = ("Array.from(document.querySelectorAll('.eylem-cubugu button'))"
             ".find(b => b.textContent.includes('Seçilenleri İndir')).click()")
    web.js(indir)
    web.bekle("document.querySelector('#bildirimler').innerText"
              ".includes('3 bölüm indirme sırasına alındı')")
    assert win._current_page == "detail"
    web.qtbot.waitUntil(lambda: win.indirme_sayisi == 3, timeout=2000)
    # Üst çubukta İndirilenler rozeti.
    web.bekle("document.querySelector('.menu-sayac').textContent === '3'")
    # Satırlar "Kuyrukta" rozetini alır.
    web.bekle("document.querySelectorAll('.bolum-satiri .rozet .donen').length === 3")

    web.js(indir)
    web.bekle("document.querySelector('#bildirimler').innerText"
              ".includes('3 bölüm zaten kuyrukta')")
    assert len(win.downloads.active_ids()) == 3


