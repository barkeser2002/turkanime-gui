"""mpv'yi başlatmanın ortak yolu: komut kurma ve süreç çalıştırma.

Kaynak katmanının (`AdapterVideo.oynat`) dışında duruyor çünkü oynatıcının
bilmesi gereken şeyler kaynağa değil KULLANICIYA ait: indirilmiş bölümü
yerel dosyadan oynatmak (ağ yok), bölümün kaldığı saniye, "izlerken kaydet".
`AdapterVideo.oynat` yalnızca adresi ve `dakika_hatirla`'yı biliyor; bu
yüzden oynatma, kaynağın verdiği adres + başlıklarla burada kuruluyor.

Modül Qt'siz: CLI de aynı komutu kullanabilsin, testler `subprocess.Popen`'ı
sahteleyip argümanları doğrudan okuyabilsin.
"""
from __future__ import annotations

import errno
import os
import shutil
import subprocess as sp
from typing import List, Optional

# `AdapterVideo.oynat`'ınkiyle aynı değer: HLS sunucularının bir kısmı mpv'nin
# kendi user-agent'ını reddediyor.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def mpv_bul() -> Optional[str]:
    """Önce gömülü `bin/` (Windows'ta `mpv.exe`, diğerlerinde `mpv`), sonra PATH.

    Uzantı sabit yazılamaz: yayın Linux ve macOS paketi de üretiyor ve
    oradaki gömülü ikili uzantısız (bkz. `AdapterVideo.oynat`).
    """
    from .utils import BIN_PATH
    for aday in (os.path.join(BIN_PATH, "mpv.exe"), os.path.join(BIN_PATH, "mpv")):
        if os.path.isfile(aday):
            return aday
    return shutil.which("mpv")


def mpv_komutu(hedef: str, *, mpv: str = "mpv", referer: Optional[str] = None,
               user_agent: Optional[str] = USER_AGENT,
               dakika_hatirla: bool = False) -> List[str]:
    """mpv argümanları (saf fonksiyon).

    Yerel dosyada user-agent/referer verilmez: HTTP yok, bayraklar yalnızca
    gürültü olurdu.
    """
    cmd = [mpv, hedef]
    if user_agent:
        cmd.append(f"--user-agent={user_agent}")
    if referer:
        cmd.append(f"--referrer={referer}")
    if dakika_hatirla:
        cmd.append("--save-position-on-quit")
    return cmd


def calistir(cmd: List[str]) -> Optional[sp.Popen]:
    """mpv'yi başlat, kapanmasını bekle; başlatılamazsa None.

    Gömülü ikili çalışmazsa (Windows'ta 216 "uyumsuz", diğerlerinde ENOEXEC
    ya da ENOENT) sistem mpv'si denenir — `AdapterVideo.oynat`'taki yedeğin
    aynısı; `winerror` yalnızca Windows'ta var, `getattr` şart.
    """
    try:
        proc = sp.Popen(cmd)
        proc.wait()
        return proc
    except OSError as hata:
        uyumsuz = (getattr(hata, "winerror", None) == 216
                   or hata.errno in (errno.ENOEXEC, errno.ENOENT))
        sistem = shutil.which("mpv") if uyumsuz else None
        if not sistem or os.path.abspath(sistem) == os.path.abspath(cmd[0]):
            print(f"MPV başlatma hatası: {hata}")
            return None
        try:
            proc = sp.Popen([sistem] + list(cmd[1:]))
            proc.wait()
            return proc
        except Exception as hata2:
            print(f"Sistem MPV hatası: {hata2}")
            return None
    except Exception as hata:
        print(f"MPV başlatma hatası: {hata}")
        return None


def yerel_oynat(yol: str, *, dakika_hatirla: bool = False) -> Optional[sp.Popen]:
    """İndirilmiş dosyayı oynat — ağ yok, `best_video` yok."""
    mpv = mpv_bul()
    if not mpv:
        print("MPV bulunamadı! Lütfen mpv'yi yükleyin veya bin/ klasörüne koyun.")
        return None
    return calistir(mpv_komutu(os.path.abspath(yol), mpv=mpv, user_agent=None,
                               dakika_hatirla=dakika_hatirla))


__all__ = ["USER_AGENT", "mpv_bul", "mpv_komutu", "calistir", "yerel_oynat"]
