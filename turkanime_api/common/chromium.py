"""QtWebEngine (Chromium) başlatma bayrakları.

İki yer QtWebEngine açıyor: GUI'deki çerez penceresi (`gui/qt/cookie_browser.py`)
ve Cloudflare çözücü alt-süreci (`common/cf_qt_solver.py`). Bayraklar
Chromium ilk kez ayağa kalkmadan önce ortamda olmalı; ikisi de bu modülü
kullansın ki biri değişince öteki unutulmasın.

NEDEN `--disable-gpu`: Eskiden yalnızca `--disable-gpu-compositing` veriliyordu;
Chromium'un GPU süreci yine açılıyordu. GPU'suz ortamda (yazılımla çizim,
sanal makine, başsız sunucu) o süreç sayfa kapanırken ara ara kendi
thread'inde çöküyor ve bütün uygulamayı segfault ile götürüyordu. Ölçüldü:
çerez penceresi testi eski bayraklarla 130 koşunun 5'inde çöktü,
`--disable-gpu` ile 130'da 0. QtWebEngine burada yalnızca bot kontrolü /
Cloudflare sayfası göstermek için var; GPU'ya ihtiyacı yok, yazılımla çizim
bu sayfalar için fazlasıyla yeterli.

Kullanıcı ortam değişkenini kendisi verdiyse ona dokunulmaz (`setdefault`).
"""
from __future__ import annotations

import os

ORTAM_ANAHTARI = "QTWEBENGINE_CHROMIUM_FLAGS"

BAYRAKLAR = " ".join((
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-features=UseChromeOSDirectVideoDecoder",
))


KUM_HAVUZU_ANAHTARI = "QTWEBENGINE_DISABLE_SANDBOX"


def root_mu() -> bool:
    """Süreç root (uid 0) olarak mı çalışıyor? Windows'ta hep False."""
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid) and geteuid() == 0


def bayraklari_hazirla() -> str:
    """Bayrakları ortama koy (zaten verilmişse dokunma); geçerli değeri döndür.

    ROOT: Chromium root olarak kum havuzuyla (sandbox) çalışmayı reddediyor
    ve süreci olduğu gibi sonlandırıyor — uygulama çerez penceresi açıldığı
    an kapanıyordu. Konteynerde (Gitea runner, Docker) iş root olarak koşuyor;
    test kapısı orada özetsiz "exit 1" ile bitiyordu (yerelde root olarak
    yeniden üretildi). Root'ta kum havuzu kapatılıyor: Chromium'un o durumda
    kabul ettiği tek yol bu. Normal kullanıcıda kum havuzuna dokunulmaz.
    """
    if root_mu():
        os.environ.setdefault(KUM_HAVUZU_ANAHTARI, "1")
    return os.environ.setdefault(ORTAM_ANAHTARI, BAYRAKLAR)


__all__ = ["ORTAM_ANAHTARI", "BAYRAKLAR", "KUM_HAVUZU_ANAHTARI", "root_mu",
           "bayraklari_hazirla"]
