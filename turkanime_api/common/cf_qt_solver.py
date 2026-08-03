"""QtWebEngine tabanlı Cloudflare challenge çözücü (alt-süreç yardımcısı).

Neden ayrı süreç? QtWebEngine yalnızca bir `QApplication` içinde ve ana thread'de
çalışabilir. Oysa `CFSession.get()` senkron bir API ve hem GUI worker
thread'lerinden hem de **hiç Qt olmayan CLI'dan** çağrılıyor. Çözümü ayrı bir
sürece taşımak bu bağımlılığı tamamen ortadan kaldırır: çağıran taraf sadece
stdin/stdout üzerinden JSON konuşur.

Pratikte bu, *yerel ve gömülü bir FlareSolverr*'dır — uzak sunucuya ihtiyaç yok.

Protokol (satır başına bir JSON):
    istek : {"url": "...", "timeout": 60}
    cevap : {"ok": true, "status": 200, "html": "...", "cookies": {...},
             "url": "...", "user_agent": "..."}
            {"ok": false, "error": "..."}

Çalıştırma:  python -m turkanime_api.common.cf_qt_solver
"""
from __future__ import annotations

import json
import os
import sys
import threading

# Challenge sayfası hâlâ duruyor mu?
# NOT: Yalnızca Cloudflare'e özgü metinlere bakmak yetmiyor — siteye özel
# koruma sayfaları (ör. Tranimaci'nin SHA-256 "Security Verification" PoW'u ya da
# "JavaScript'i açın" ara sayfası) aksi hâlde *gerçek içerik* sanılıp
# başarı olarak döndürülüyordu.
CHALLENGE_MARKERS = (
    # Cloudflare
    "Just a moment",
    "Checking your browser",
    "cf-browser-verification",
    "challenge-platform",
    "Enable JavaScript and cookies to continue",
    # Siteye özel / genel JS kapıları
    "Security Verification",
    "turn JavaScript on",
    "Please enable JavaScript",
    "__waf_challenge",
)
DEFAULT_TIMEOUT = 60
POLL_MS = 700
PROFILE_NAME = "cfsolver"


def _looks_like_challenge(html: str) -> bool:
    head = html[:6000]
    return any(marker in head for marker in CHALLENGE_MARKERS)


def main() -> int:
    # Görünür pencere olmasın
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QCoreApplication, Qt, QTimer, QUrl, QObject, Signal, Slot
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage

    app = QApplication(sys.argv)

    profile = QWebEngineProfile(PROFILE_NAME)
    try:
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
    except Exception:
        pass

    cookies: dict[str, str] = {}
    cookie_domains: dict[str, str] = {}
    store = profile.cookieStore()

    def _on_cookie(c) -> None:
        # Domain'i de saklıyoruz: çağıran taraf çerezleri isteğin host'una göre
        # süzebilsin. Aksi hâlde bir sitenin oturum çerezi başka bir siteye
        # iliştirilir (cf_clearance domain'e bağlıdır -> 403 döngüsü).
        name = bytes(c.name()).decode("utf-8", "replace")
        cookies[name] = bytes(c.value()).decode("utf-8", "replace")
        cookie_domains[name] = c.domain()

    store.cookieAdded.connect(_on_cookie)

    page = QWebEnginePage(profile)

    def _write(obj) -> None:
        # ensure_ascii=True: Windows'ta çocuk sürecin stdout kod sayfası UTF-8
        # olmayabiliyor; saf ASCII yazınca kod sayfası hiç önemli olmuyor.
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()

    class Solver(QObject):
        request = Signal(object)

        def __init__(self):
            super().__init__()
            self.request.connect(self._handle)
            self._busy = False

        @Slot(object)
        def _handle(self, req: dict) -> None:
            url = req.get("url") or ""
            timeout = float(req.get("timeout") or DEFAULT_TIMEOUT)
            if not url:
                _write({"ok": False, "error": "url yok"})
                return
            if self._busy:
                _write({"ok": False, "error": "cozucu mesgul"})
                return
            self._busy = True

            state = {"done": False, "elapsed": 0.0}

            def finish(payload) -> None:
                if state["done"]:
                    return
                state["done"] = True
                self._busy = False
                # Bağlantıyı kopar: aksi hâlde sonraki isteklerde birikir.
                try:
                    page.loadFinished.disconnect(on_load)
                except (RuntimeError, TypeError):
                    pass
                _write(payload)

            def on_html(html: str) -> None:
                if state["done"]:
                    return
                if not _looks_like_challenge(html):
                    finish({
                        "ok": True,
                        "status": 200,
                        "html": html,
                        "cookies": dict(cookies),
                        "cookie_domains": dict(cookie_domains),
                        "url": page.url().toString() or url,
                        "user_agent": profile.httpUserAgent(),
                    })
                    return
                # Challenge sürüyor: süre dolmadıysa tekrar yokla
                state["elapsed"] += POLL_MS / 1000.0
                if state["elapsed"] >= timeout:
                    finish({"ok": False, "error": "challenge zaman asimi"})
                else:
                    QTimer.singleShot(POLL_MS, lambda: page.toHtml(on_html))

            def on_load(ok: bool) -> None:
                if not ok:
                    finish({"ok": False, "error": "sayfa yuklenemedi"})
                    return
                # İlk okuma; challenge varsa on_html kendini tekrar zamanlar
                QTimer.singleShot(POLL_MS, lambda: page.toHtml(on_html))

            # NOT: UniqueConnection yerel bir closure ile kullanılamaz (Qt hata
            # verir); istek başına normal bağlanıp finish() içinde koparıyoruz.
            page.loadFinished.connect(on_load)

            # Sert emniyet kemeri
            QTimer.singleShot(int(timeout * 1000) + 5000,
                              lambda: finish({"ok": False, "error": "genel zaman asimi"}))
            page.load(QUrl(url))

    solver = Solver()

    def reader() -> None:
        """stdin'i arka planda oku, istekleri GUI thread'ine kuyrukla."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                _write({"ok": False, "error": "geçersiz JSON"})
                continue
            solver.request.emit(req)
        app.quit()

    threading.Thread(target=reader, daemon=True).start()
    _write({"ok": True, "ready": True})
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
