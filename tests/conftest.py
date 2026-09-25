"""Ortak pytest altyapısı.

İki kural:

1. **Testler varsayılan olarak ağa çıkmaz.** Anime siteleri kararsız; ağa bağlı
   test paketi kırmızıya boyanır ve güvenilirliğini yitirir. Gerçek ağ isteyen
   testler `@pytest.mark.network` ile işaretlenir ve yalnızca `--network`
   verildiğinde çalışır.
2. **Qt testleri offscreen koşar.** `QT_QPA_PLATFORM=offscreen`, QApplication
   kurulmadan *önce* ayarlanmalı; bu yüzden import zamanında yapılıyor.
"""
from __future__ import annotations

import itertools
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

# QApplication'dan ÖNCE — aksi hâlde gerçek pencere açılmaya çalışılır.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ── Ağ işareti ───────────────────────────────────────────────────────────────
def pytest_addoption(parser):
    parser.addoption(
        "--network", action="store_true", default=False,
        help="Gerçek ağ erişimi gerektiren testleri de çalıştır",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--network"):
        return
    skip = pytest.mark.skip(reason="ağ testi (--network ile çalıştırılır)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


# ── Ağ mandalı ───────────────────────────────────────────────────────────────
class AgEngellendi(RuntimeError):
    """Test paketi dışarı bağlanmaya çalıştı."""


@pytest.fixture(scope="session", autouse=True)
def _ag_mandali(pytestconfig):
    """Loopback dışına çıkan her soket çağrısını kes.

    Sahteleme tek başına yetmiyor: açılış denetimi (`MainWindow` →
    `UpdateService.kontrol_et`) işi ARKA PLAN havuzuna atıyor ve o thread
    fonksiyona, testin `monkeypatch`'i çoktan geri alındıktan sonra ulaşıyor —
    yani sahtenin yanından dolaşıp gerçekten `raw.githubusercontent.com`'a
    çıkıyordu. Ölçüldü: paket başına 3 dış istek. Süreç genelinde kurulan bu
    mandal, hangi yoldan gelirse gelsin sızıntıyı yakalar.

    `--network` verildiğinde kurulmaz; işaretli testler gerçekten ağa çıkmalı.
    """
    if pytestconfig.getoption("--network"):
        yield
        return

    import socket

    yerel = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}
    ozgun = (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo)

    def _yerel_mi(adres):
        try:
            return str(adres[0]) in yerel
        except Exception:
            return True

    def _connect(self, adres):
        if not _yerel_mi(adres):
            raise AgEngellendi(f"testler ağa çıkamaz: {adres}")
        return ozgun[0](self, adres)

    def _connect_ex(self, adres):
        if not _yerel_mi(adres):
            raise AgEngellendi(f"testler ağa çıkamaz: {adres}")
        return ozgun[1](self, adres)

    def _getaddrinfo(host, *a, **k):
        if str(host) not in yerel:
            raise AgEngellendi(f"testler DNS sorgulayamaz: {host}")
        return ozgun[2](host, *a, **k)

    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.getaddrinfo = _getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = ozgun[0], ozgun[1]
        socket.getaddrinfo = ozgun[2]


# ── Qt ───────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def _qt_env():
    """QtWebEngine'in şart koştuğu attribute'u QApplication'dan önce ayarla.

    Oturum sonunda bekleyen `deleteLater`'lar işleniyor: pytest-qt son testin
    penceresini yalnızca `deleteLater` ile bırakıyor, olay döngüsü bir daha
    dönmüyor ve web sayfası, profili (ebeveyni QApplication) yıkılırken hâlâ
    yaşıyor olurdu ("WebEnginePage still not deleted. Expect troubles").
    """
    from turkanime_api.gui.qt.app import prepare_qt_env
    prepare_qt_env()
    yield
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is not None:
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _stub_discover_sources(request, monkeypatch):
    """Keşif ve AniList ağ uçlarını varsayılan olarak sustur.

    `DiscoverPage` ilk gösterimde veri çeker; `main_window` fixture'ı pencereyi
    `show()` ettiği için ana sayfa açılır ve bu, hiçbir şey yapmayan testleri
    bile Jikan/AniList'e çıkarır. Kural 1 gereği bunu kesiyoruz; gerçek veri
    isteyen testler kendi sahtelerini bu fixture'ın üstüne yazabilir.

    AniList uçları da susturuluyor: `MainWindow` açılışta diskteki jetonla
    kullanıcıyı tazeliyor. Geliştirme makinesinde gerçek bir jeton varsa test
    paketi habersizce AniList'e bağlanırdı.
    """
    if "network" in request.keywords:
        return
    import turkanime_api.anilist_client as anilist_mod
    import turkanime_api.jikan_client as jikan_mod

    monkeypatch.setattr(jikan_mod, "get_trending_anime_list", lambda *a, **k: [])
    monkeypatch.setattr(jikan_mod, "get_seasonal_anime_list", lambda *a, **k: [])
    for uc, sonuc in (("get_trending_anime", []), ("search_anime", []),
                      ("get_user_anime_list", []), ("get_current_user", None),
                      ("update_anime_progress", False)):
        monkeypatch.setattr(anilist_mod.anilist_client, uc,
                            lambda *a, _s=sonuc, **k: _s)


_ARSIV_YALITIM_SAYACI = itertools.count()


@pytest.fixture(scope="session")
def _arsiv_yalitim_koku(tmp_path_factory):
    return tmp_path_factory.mktemp("arsiv_yalitim")


@pytest.fixture(autouse=True)
def _arsiv_yalitimi(request, monkeypatch, _arsiv_yalitim_koku, tmp_path_factory):
    """AnimeDepo istemcisini gerçek arşivlerden ve ağdan yalıt.

    İstemci artık önce YEREL arşive bakıyor ve depodan çalışırken commit'lenmiş
    `arsiv/` (~500 MB) orada duruyor: yalıtılmasa AnimeDepo'ya dokunan her test
    sessizce gerçek arşivi okur ve sonucu arşivin o günkü içeriğine bağlanırdı.
    Aynı sebeple geliştiricinin indirdiği `cevrimdisi_arsiv/`, disk önbelleği ve
    `TURKANIME_ARSIV_*` ortam değişkenleri de devre dışı.

    `_session` da kesiliyor: curl_cffi kendi (libcurl) soketlerini açtığı için
    yukarıdaki ağ mandalı onu YAKALAMIYOR. HTTP isteyen test kendi sahtesini
    `monkeypatch.setattr(animedepo, "_session", ...)` ile bunun üstüne yazar.

    Önbellek klasörü test başına ayrı ve yalnızca yazılırsa oluşuyor: bir testin
    önbelleğe aldığı dosya başka bir testin "çevrimdışı" yoluna sızmasın.

    `ayarlar.json` da: istemci `animedepo_dizin` (seçilen klasör) ve
    `animedepo_url` (özel ayna) ayarlarını VERİ KÖKÜNDEN okuyor ve pytest
    depodan çalışınca veri kökü DEPO KÖKÜ. Geliştirici uygulamayı depodan
    açıp "Klasör seç…"e bastıysa `<depo>/ayarlar.json`'a yazılan klasör ve
    ayna bütün teste sızıyordu (ölçüldü: 31 test düştü). Kural: ayar dosyası
    yalnızca pytest'in GEÇİCİ kökü altındaysa okunur. Ayarı sınayan testler
    zaten `tmp_path`'teki `.git`'li bir klasöre `chdir` ediyor (`izole_ev`,
    `veri_koku`), onlar etkilenmez.
    """
    if "network" in request.keywords:
        yield
        return
    from turkanime_api.sources import animedepo

    kok = _arsiv_yalitim_koku / f"t{next(_ARSIV_YALITIM_SAYACI)}"
    gecici_kok = tmp_path_factory.getbasetemp().resolve()
    asil_ayar_dosyasi = animedepo._ayar_dosyasi

    def _yalniz_gecici_ayar_dosyasi():
        yol = asil_ayar_dosyasi()
        if yol is None:
            return None
        try:
            Path(yol).resolve().relative_to(gecici_kok)
        except ValueError:
            return None                  # geliştiricinin/kullanıcının gerçek ayarı
        return yol

    def _ag_yok():
        raise AgEngellendi("testler AnimeDepo aynalarına çıkamaz; "
                           "`animedepo._session`'ı sahteleyin")

    monkeypatch.delenv(animedepo.DIZIN_ORTAM_ANAHTARI, raising=False)
    monkeypatch.delenv(animedepo.ORTAM_ANAHTARI, raising=False)
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", kok / "depo_arsivi_yok")
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: kok / "indirilen_yok")
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: kok / "onbellek")
    monkeypatch.setattr(animedepo, "_session", _ag_yok)
    monkeypatch.setattr(animedepo, "_ayar_dosyasi", _yalniz_gecici_ayar_dosyasi)
    animedepo.sifirla()
    yield
    animedepo.sifirla()


@pytest.fixture(autouse=True)
def _gorsel_onbellek_yalitimi(monkeypatch, tmp_path_factory):
    """Kapak önbelleği test başına boş ve geçici klasörde.

    Disk önbelleğinin kökü `veri_koku()`: pytest depodan çalışınca DEPO KÖKÜ.
    Yalıtılmasa testlerin sahte PNG'leri depoya yazılır; bellek önbelleği de
    testler arasında taşınıp "0 istek" iddialarını önceki testin indirdiğiyle
    geçirirdi.
    """
    from turkanime_api.gui.qt import gorsel

    kok = tmp_path_factory.mktemp("gorsel_onbellek")
    monkeypatch.setattr(gorsel, "onbellek_dizini", lambda: kok)
    gorsel.bellegi_temizle()
    yield
    gorsel.bellegi_temizle()


@pytest.fixture(autouse=True)
def _kutuphane_yalitimi(monkeypatch, tmp_path_factory):
    """Kitaplık dosyası (`kutuphane.json`) yalnızca pytest'in geçici kökünde.

    Kök `Dosyalar().ta_path`: depodan çalışınca DEPO KÖKÜ. `preserved_gecmis`
    kullanan eski oynatma testleri gerçek `gecmis.json`'a yazıp geri alıyor;
    kitaplık ayrı dosya olduğu için o yedek onu kapsamıyor ve başarılı her
    oynatma testi depoya `kutuphane.json` bırakırdı (ana sayfa da açılışta
    geliştiricinin gerçek kitaplığını okurdu). `izole_ev` kullanan testler
    etkilenmez: onların kökü zaten geçici.
    """
    from turkanime_api.common import kutuphane

    asil = kutuphane.kutuphane_yolu
    gecici_kok = tmp_path_factory.getbasetemp().resolve()
    yedek = {}
    kilit = threading.Lock()          # arka plan işleri de çağırıyor

    def _yalniz_gecici():
        yol = asil()
        try:
            Path(yol).resolve().relative_to(gecici_kok)
            return yol
        except ValueError:
            with kilit:
                if "yol" not in yedek:
                    yedek["yol"] = str(tmp_path_factory.mktemp("kutuphane")
                                       / kutuphane.DOSYA_ADI)
                return yedek["yol"]

    monkeypatch.setattr(kutuphane, "kutuphane_yolu", _yalniz_gecici)


@pytest.fixture(autouse=True)
def _indirme_kuyrugu_yalitimi(monkeypatch, tmp_path_factory):
    """İndirme kuyruğu dosyası (`indirme_kuyrugu.json`) yalnızca geçici kökte.

    Kök `Dosyalar().ta_path`: depodan çalışınca DEPO KÖKÜ. Yalıtılmasa gerçek
    `AdapterBolum`'la kuyruğa iş koyan bir test depoya kuyruk dosyası bırakır,
    her `MainWindow` açılışı da onu "duraklatıldı" işler olarak geri yüklerdi.
    `izole_ev` kullanan testler etkilenmez: onların kökü zaten geçici.
    """
    from turkanime_api.gui.qt.pages import downloads

    asil = downloads.kuyruk_yolu
    gecici_kok = tmp_path_factory.getbasetemp().resolve()
    yedek = {}
    kilit = threading.Lock()

    def _yalniz_gecici():
        yol = asil()
        try:
            Path(yol).resolve().relative_to(gecici_kok)
            return yol
        except ValueError:
            with kilit:
                if "yol" not in yedek:
                    yedek["yol"] = str(tmp_path_factory.mktemp("kuyruk")
                                       / downloads.KUYRUK_DOSYASI)
                return yedek["yol"]

    monkeypatch.setattr(downloads, "kuyruk_yolu", _yalniz_gecici)


@pytest.fixture(scope="session", autouse=True)
def _cevresel_taban(pytestconfig):
    """Ağ uçlarının OTURUM BOYU tabanını sahteye çek.

    Fonksiyon kapsamlı `monkeypatch` teardown'da *eski* değeri geri koyuyor;
    açılış denetiminin arka plan thread'i tam o aralığa denk geldiğinde gerçek
    fonksiyonu buluyor ve ağa çıkıyordu. Taban da sahte olunca geri koyulan
    değer yine sahtedir — yarış ortadan kalkar.
    """
    if pytestconfig.getoption("--network"):
        yield
        return
    from turkanime_api.common import requirements as req_mod
    from turkanime_api.common import updater as upd_mod

    mp = pytest.MonkeyPatch()
    mp.setattr(upd_mod, "surum_bilgisi_getir", lambda *a, **k: {})
    mp.setattr(req_mod, "eksik_araclar", lambda *a, **k: [])
    try:
        yield
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def _stub_cevresel_servisler(request, monkeypatch):
    """Açılış denetimlerini (güncelleme, gereksinim, Discord) sustur.

    `MainWindow` açılıştan kısa süre sonra `version.json` ve `gereksinimler.json`
    adreslerine çıkıyor, ayrıca Discord'a bağlanmayı deniyor. Kural 1 gereği
    kesiliyor; bu servisleri sınayan testler kendi sahtelerini üstüne yazar.
    """
    if "network" in request.keywords:
        return
    import turkanime_api.gui.qt.discord as discord_mod
    from turkanime_api.common import requirements as req_mod
    from turkanime_api.common import updater as upd_mod

    monkeypatch.setattr(upd_mod, "surum_bilgisi_getir", lambda *a, **k: {})
    monkeypatch.setattr(req_mod, "eksik_araclar", lambda *a, **k: [])
    monkeypatch.setattr(discord_mod, "KULLANILABILIR", False)


@pytest.fixture
def main_window(qtbot):
    """Gösterilmiş `MainWindow`.

    `show()` şart: gösterilmemiş bir QWebEngineView render sürecini başlatmaz ve
    yüklemeler sessizce `loadFinished(False)` ile düşer.
    """
    from turkanime_api.gui.qt.app import MainWindow
    from turkanime_api.gui.qt.theme import apply_theme
    from PySide6.QtWidgets import QApplication

    apply_theme(QApplication.instance())
    win = MainWindow()
    # Süren indirme varken kapanış modal soru açıyor; teardown asla beklememeli.
    # `yield`'den ÖNCE: pytest-qt `addWidget` ile kaydedilen pencereyi
    # fixture sonlandırıcılarından önce (kendi teardown kancasında) kapatıyor.
    # Soruyu sınayan testler bunu kendi `monkeypatch`'leriyle eziyor.
    win._kapanis_onayi = lambda _adet: True
    qtbot.addWidget(win)
    win.show()
    yield win
    win.close()


# ── Web arayüzü (QtWebEngine sayfası) ────────────────────────────────────────
class WebSurucu:
    """Web görünümünde JS çalıştırıp sonucunu bekleyen küçük sürücü.

    `runJavaScript` sonucu geri çağrıyla, olay döngüsü döndükçe geliyor;
    testler düz değer istiyor. `bekle` koşul doğru olana kadar betiği
    yeniden çalıştırıyor (sayfa köprüden gelen veriyi eşzamansız çiziyor).

    DİKKAT: DOM düğümü JSON'a çevrilemiyor ve Python'a boş dizge olarak
    geliyor; varlık sınamasında ``!!document.querySelector(...)`` yazın.
    """

    def __init__(self, qtbot, gorunum):
        self.qtbot = qtbot
        self.gorunum = gorunum

    def hazir(self, timeout: int = 15000) -> "WebSurucu":
        self.qtbot.waitUntil(lambda: self.gorunum.hazir, timeout=timeout)
        self.bekle("!!(window.TA && TA.aktif)", timeout=timeout)
        return self

    def js(self, betik: str, timeout: int = 5000):
        sonuc: list = []
        self.gorunum.page().runJavaScript(betik, 0, sonuc.append)
        self.qtbot.waitUntil(lambda: bool(sonuc), timeout=timeout)
        return sonuc[0]

    def bekle(self, betik: str, timeout: int = 5000):
        import time
        son = time.monotonic() + timeout / 1000.0
        deger = None
        while time.monotonic() < son:
            deger = self.js(betik, timeout=timeout)
            if deger:
                return deger
            self.qtbot.wait(40)
        raise AssertionError(f"koşul gerçekleşmedi: {betik!r} → {deger!r}")


@pytest.fixture
def web(qtbot, main_window):
    """Ana penceredeki web görünümünün sürücüsü (sayfa yüklenmiş)."""
    return WebSurucu(qtbot, main_window.web).hazir()


# ── Yerel HTTP sunucusu (dış servis yerine) ──────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    """Sabit HTML + istenirse çerez döndürür."""

    body = b"<html><body>merhaba</body></html>"
    set_cookie: str | None = None

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        if self.set_cookie:
            self.send_header("Set-Cookie", self.set_cookie)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):  # sessiz
        pass


@pytest.fixture
def local_server():
    """`http://127.0.0.1:<port>/` döndüren fabrika.

    Kullanım:
        url = local_server(set_cookie="AitrSession=TOKEN; Path=/")
    """
    servers = []

    def _start(body: bytes | None = None, set_cookie: str | None = None) -> str:
        handler = type("H", (_Handler,), {
            "body": body or _Handler.body,
            "set_cookie": set_cookie,
        })
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}/"

    yield _start
    for srv in servers:
        srv.shutdown()


def _yedekli_dosya(path: str):
    """`path`'i yedekle, test bitince geri yükle."""
    import shutil

    backup = path + ".pytest-backup"
    shutil.copy2(path, backup)
    try:
        yield path
    finally:
        shutil.copy2(backup, path)
        os.remove(backup)


@pytest.fixture
def preserved_settings():
    """`ayarlar.json`'ı yedekler ve test sonrası geri yükler.

    Ayar sayfası testleri kullanıcının gerçek yapılandırmasına yazıyor;
    bu fixture olmadan test çalıştırmak ayarları bozar.
    """
    from turkanime_api.cli.dosyalar import Dosyalar
    yield from _yedekli_dosya(Dosyalar().ayar_path)


@pytest.fixture
def preserved_gecmis():
    """`gecmis.json`'ı yedekler ve test sonrası geri yükler.

    İzlendi/indirildi kayıtları ve izleme ilerlemesi gerçek dosyaya yazılıyor;
    kullanıcının geçmişini test verisiyle kirletmemek için.
    """
    from turkanime_api.cli.dosyalar import Dosyalar
    yield from _yedekli_dosya(Dosyalar().gecmis_path)


@pytest.fixture
def izole_ev(tmp_path, monkeypatch):
    """`Dosyalar()`'ı geçici bir dizine bağla — gerçek dosyalara hiç dokunma.

    `preserved_*` yalnızca YAZIMI geri alıyor; test kullanıcının gerçek
    `gecmis.json`'unu OKUMAYA devam ettiği için sonucu oradaki kayıtlara
    bağlıydı. Ölçüldü: `naruto-test-1-bolum` zaten "indirildi" listesindeyse
    `test_liste_gercek_gecmisi_okuyor` düşüyor — yani test başka bir makinede
    farklı davranıyor.

    `Dosyalar`, çalışma dizininde `.git` görürse orayı kök sayar; mandal bu
    daldan geçiyor. Ayrı bir alt dizin kullanılıyor ki `tmp_path`'i indirme
    hedefi olarak kullanan testler ayar/geçmiş dosyalarıyla karışmasın.
    """
    ev = tmp_path / "ev"
    (ev / ".git").mkdir(parents=True)
    monkeypatch.chdir(ev)
    from turkanime_api.cli.dosyalar import Dosyalar
    Dosyalar()                       # varsayılan ayarlar + boş geçmiş üret
    return ev


@pytest.fixture
def ayarla(preserved_settings):
    """Gerçek `ayarlar.json`'a yazan yardımcı (fixture testten sonra geri alır).

    Amaç ayarın GERÇEKTEN okunduğunu doğrulamak: `prefs.oku`'yu sahtelemek
    "ayar okunuyor mu?" sorusunu yanıtlamaz, yalnızca sahteyi test ederdi.
    """
    from turkanime_api.cli.dosyalar import Dosyalar

    def _ayarla(**degerler):
        Dosyalar().set_ayar(ayar_list=degerler)

    return _ayarla
