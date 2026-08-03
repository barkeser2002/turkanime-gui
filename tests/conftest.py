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

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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


# ── Qt ───────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def _qt_env():
    """QtWebEngine'in şart koştuğu attribute'u QApplication'dan önce ayarla."""
    from turkanime_api.gui.qt.app import prepare_qt_env
    prepare_qt_env()


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
    qtbot.addWidget(win)
    win.show()
    yield win
    win.close()


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
def ayarla(preserved_settings):
    """Gerçek `ayarlar.json`'a yazan yardımcı (fixture testten sonra geri alır).

    Amaç ayarın GERÇEKTEN okunduğunu doğrulamak: `prefs.oku`'yu sahtelemek
    "ayar okunuyor mu?" sorusunu yanıtlamaz, yalnızca sahteyi test ederdi.
    """
    from turkanime_api.cli.dosyalar import Dosyalar

    def _ayarla(**degerler):
        Dosyalar().set_ayar(ayar_list=degerler)

    return _ayarla
