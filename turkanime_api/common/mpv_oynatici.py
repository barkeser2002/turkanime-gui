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
import json
import os
import shutil
import subprocess as sp
import tempfile
from typing import Any, Dict, List, Optional

# `AdapterVideo.oynat`'ınkiyle aynı değer: HLS sunucularının bir kısmı mpv'nin
# kendi user-agent'ını reddediyor.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Kaldığı yeri raporlayan mpv betiği (pakete `turkanime-gui.spec` datas'ıyla,
# wheel'e paket dizininde olduğu için kendiliğinden giriyor).
LUA_BETIGI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "mpv_konum.lua")
# Betiğin okuduğu `--script-opts` anahtarı (bkz. `mpv_konum.lua`).
KONUM_ANAHTARI = "turkanime-konum"


def mpv_bul() -> Optional[str]:
    """Önce gömülü `bin/` (Windows'ta `mpv.exe`, diğerlerinde `mpv`), sonra PATH.

    Uzantı sabit yazılamaz: yayın Linux ve macOS paketi de üretiyor ve
    oradaki gömülü ikili uzantısız. Windows dışında `.exe` hiç denenmez ve
    dosyanın çalıştırılabilir olması aranır: depodaki `bin/mpv.exe` yer
    tutucusu Linux'ta "Permission denied" veriyor, sistemde kurulu mpv
    hiç denenmeden "oynatıcı başlatılamadı" deniyordu.
    """
    from .utils import BIN_PATH
    if os.name == "nt":
        aday = os.path.join(BIN_PATH, "mpv.exe")
        if os.path.isfile(aday):
            return aday
    else:
        aday = os.path.join(BIN_PATH, "mpv")
        if os.path.isfile(aday) and os.access(aday, os.X_OK):
            return aday
    return shutil.which("mpv")


def mpv_komutu(hedef: str, *, mpv: str = "mpv", referer: Optional[str] = None,
               user_agent: Optional[str] = USER_AGENT,
               dakika_hatirla: bool = False, baslangic: Optional[float] = None,
               konum_dosyasi: Optional[str] = None,
               kayit: Optional[str] = None) -> List[str]:
    """mpv argümanları (saf fonksiyon).

    Yerel dosyada user-agent/referer verilmez: HTTP yok, bayraklar yalnızca
    gürültü olurdu.

    ``baslangic``: bölümün KENDİ anahtarıyla saklanan konum. mpv'nin
    `--save-position-on-quit`'i yine veriliyor (Lua'sız mpv'de tek yedek),
    ama asıl devam bu: adres değişse de çalışır.

    ``konum_dosyasi``: `mpv_konum.lua` oynatma sonunda konumu/süreyi/sebebi
    buraya yazar. `--script-opts-append` tek anahtar alıyor, yani yoldaki
    virgül `--script-opts`'taki gibi değeri bölmez.

    ``kayit``: "İzlerken kaydet" hedefi (`--stream-record=<dosya>`; kap
    biçimi uzantıdan seçiliyor, bkz. `dosya_adi.kayit_hedefi`).
    """
    cmd = [mpv, hedef]
    if user_agent:
        cmd.append(f"--user-agent={user_agent}")
    if referer:
        cmd.append(f"--referrer={referer}")
    if dakika_hatirla:
        cmd.append("--save-position-on-quit")
    if baslangic and baslangic > 0:
        cmd.append(f"--start={int(baslangic)}")
    if konum_dosyasi and os.path.isfile(LUA_BETIGI):
        cmd.append(f"--script={LUA_BETIGI}")
        cmd.append(f"--script-opts-append={KONUM_ANAHTARI}={konum_dosyasi}")
    if kayit:
        cmd.append(f"--stream-record={kayit}")
    return cmd


def konum_dosyasi_ayir() -> str:
    """Betiğin raporu yazacağı geçici dosyanın yolu (dosya henüz YOK).

    Var olmaması bilerek: oynatmadan sonra dosya yoksa "mpv raporlamadı"
    (Lua'sız derleme, betik yüklenemedi) demektir ve çağıran eski davranışa
    döner. Boş bir dosya bırakmak bu ayrımı bulandırırdı.
    """
    fd, yol = tempfile.mkstemp(prefix="turkanime-konum-", suffix=".json")
    os.close(fd)
    os.remove(yol)
    return yol


def konum_oku(yol: Optional[str], sil: bool = False) -> Optional[Dict[str, Any]]:
    """Betiğin raporu: ``{"konum", "sure", "sebep"}``; yoksa/bozuksa None.

    Rapor yoksa ASLA istisna yok: Lua'sız mpv'de dosya hiç oluşmaz ve bu
    oynatmanın başarısız olduğu anlamına gelmez.
    """
    if not yol:
        return None
    try:
        with open(yol, encoding="utf-8") as fp:
            veri = json.load(fp)
    except (OSError, ValueError):
        veri = None
    finally:
        if sil:
            try:
                os.remove(yol)
            except OSError:
                pass
    if not isinstance(veri, dict):
        return None

    def _sayi(deger):
        try:
            return float(deger) if deger is not None else None
        except (TypeError, ValueError):
            return None

    return {"konum": _sayi(veri.get("konum")), "sure": _sayi(veri.get("sure")),
            "sebep": str(veri.get("sebep") or "")}


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
                   or hata.errno in (errno.ENOEXEC, errno.ENOENT, errno.EACCES))
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


def _mpv() -> Optional[str]:
    mpv = mpv_bul()
    if not mpv:
        print("MPV bulunamadı! Lütfen mpv'yi yükleyin veya bin/ klasörüne koyun.")
    return mpv


def yerel_oynat(yol: str, *, dakika_hatirla: bool = False,
                baslangic: Optional[float] = None,
                konum_dosyasi: Optional[str] = None) -> Optional[sp.Popen]:
    """İndirilmiş dosyayı oynat — ağ yok, `best_video` yok."""
    mpv = _mpv()
    if not mpv:
        return None
    return calistir(mpv_komutu(os.path.abspath(yol), mpv=mpv, user_agent=None,
                               dakika_hatirla=dakika_hatirla,
                               baslangic=baslangic, konum_dosyasi=konum_dosyasi))


def kaynak_videosu_mu(video: Any) -> bool:
    """Kaynak katmanının videosu mu (`sources.adapter.AdapterVideo`)?

    Yalnızca onun adresi + başlıkları burada oynatılabiliyor; eski
    `objects.Video` ve test sahteleri kendi `oynat`'larıyla kalır.
    """
    try:
        from ..sources.adapter import AdapterVideo
    except Exception:
        return False
    return isinstance(video, AdapterVideo)


def video_oynat(video: Any, *, dakika_hatirla: bool = False,
                baslangic: Optional[float] = None,
                konum_dosyasi: Optional[str] = None,
                kayit: Optional[str] = None) -> Optional[sp.Popen]:
    """Kaynak videosunu (adres + referer) mpv'yle oynat.

    `AdapterVideo.oynat`'ın yerine: o yalnızca `dakika_hatirla` alıyor ve
    "İzlerken kaydet" ayarı bu yüzden hiçbir kaynakta çalışmıyordu (ayar
    sayfasında ve CLI menüsünde duruyor, `prefs.oynat` ise imzada olmadığı
    için sessizce atlıyordu).
    """
    mpv = _mpv()
    if not mpv:
        return None
    return calistir(mpv_komutu(str(video.url), mpv=mpv,
                               referer=getattr(video, "referer", None),
                               dakika_hatirla=dakika_hatirla,
                               baslangic=baslangic, konum_dosyasi=konum_dosyasi,
                               kayit=kayit))


def kayit_hedefi_kur(kok: str, bolum: Any) -> Optional[str]:
    """"İzlerken kaydet" hedefi; kurulamazsa None (bayrak hiç geçilmez).

    Değersiz ya da yazılamayan `--stream-record` oynatmayı bozar; sessizce
    kaydetmemek yeğdir (eski `objects.Video.oynat` ile aynı kural).
    """
    from .dosya_adi import kayit_hedefi
    try:
        return kayit_hedefi(kok, bolum)
    except (OSError, ValueError, TypeError):
        return None


def kaydi_sonlandir(kayit: Optional[str], tam: bool) -> Optional[str]:
    """Oynatma bitti: kayıt TAM değilse yarım adına taşı.

    Kayıt indirilenlerin yanına, indirmeyle aynı adla yazılıyor; böylece tam
    kayıt "Oynat"ta yerel dosya olarak açılır. Yarıda kapatılan (ya da
    `--start` ile ortadan başlayan) kayıt o adda kalsaydı bir sonraki
    "Oynat" akış yerine bu yarım dosyayı açardı. Silinmiyor: kullanıcının
    verisi, `.yarim.mkv` olarak klasörde duruyor.
    """
    if not kayit or not os.path.isfile(kayit):
        return None
    if tam:
        return kayit
    from .dosya_adi import YARIM_KAYIT_EKI
    kok, uzanti = os.path.splitext(kayit)
    yarim = kok + YARIM_KAYIT_EKI + uzanti
    try:
        os.replace(kayit, yarim)
    except OSError:
        return None
    return yarim


__all__ = ["USER_AGENT", "LUA_BETIGI", "KONUM_ANAHTARI", "mpv_bul", "mpv_komutu",
           "konum_dosyasi_ayir", "konum_oku", "calistir", "yerel_oynat",
           "kaynak_videosu_mu", "video_oynat", "kayit_hedefi_kur",
           "kaydi_sonlandir"]
