"""Harici araç (mpv, ffmpeg, aria2c, yt-dlp) tespiti ve kurulumu — arayüzsüz.

Eski karşılığı `gui/main.py::RequirementsManager` idi; o dosya CustomTkinter'a
bağlı ve Faz 9'da silinecek. Mantık burada duruyor ki hem Qt sihirbazı hem
(gerekirse) CLI aynı tespiti kullansın.

İki gerçek hata burada düzeltildi:

- Eski kod paket URL'sini `get_platform()` ("windows_x64") ile arıyordu, oysa
  `gereksinimler.json` yalnızca "windows"/"linux"/"macos" biliyor: hiçbir zaman
  eşleşme bulunmuyordu.
- Gömülü araç kontrolü yalnızca mpv için ve yalnızca indirme anında yapılıyordu;
  artık tespit de gömülüyü görüyor (paketlenmiş EXE'de sihirbaz hiç açılmamalı).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
from shutil import move
from typing import Any, Callable, Dict, List, Optional, Sequence
from zipfile import ZipFile

import requests

from .utils import get_arch, get_os

GEREKSINIM_URL = ("https://raw.githubusercontent.com/KebabLord/"
                  "turkanime-indirici/master/gereksinimler.json")

# Oynatma (mpv), birleştirme (ffmpeg), hızlandırma (aria2c) ve çözümleme
# (yt-dlp) — biri eksikken uygulamanın bir parçası sessizce çalışmıyor.
ARACLAR: Sequence[str] = ("yt-dlp", "mpv", "aria2c", "ffmpeg")

ZAMAN_ASIMI = 10
INDIRME_ZAMAN_ASIMI = 30
PARCA = 8192

# Depoda mpv.exe yerine "#" ile başlayan bir yer tutucu metin bulunabiliyor
# (git-lfs'siz kopyalar). Var sayıp geçmek, oynatmanın sessizce ölmesi demek.
PLACEHOLDER_ONEKI = "#"


def _calistirilabilir(ad: str) -> str:
    """Platformun beklediği dosya adı (`mpv` → Windows'ta `mpv.exe`)."""
    if get_os() == "windows" and not ad.lower().endswith(".exe"):
        return ad + ".exe"
    return ad


def _placeholder_mi(yol: str) -> bool:
    """İlk satırı "#" ile başlayan metin dosyası mı?

    `utf-8-sig` şart: depodaki yer tutucular BOM ile yazılmış ve düz "utf-8"
    okumada ilk karakter "﻿" oluyor. Eski kontrol tam bu yüzden hiçbir
    yer tutucuyu yakalamıyor, uygulama 58 baytlık metin dosyasını "mpv kurulu"
    sanıyordu.
    """
    try:
        with open(yol, "r", encoding="utf-8-sig", errors="ignore") as fp:
            return fp.readline().strip().startswith(PLACEHOLDER_ONEKI)
    except OSError:
        return False


def gomulu_arac_yolu(ad: str) -> Optional[str]:
    """Uygulamayla gelen aracın yolu; yoksa ya da yer tutucuysa ``None``.

    PyInstaller tek-dosya modunda `sys._MEIPASS/bin`, geliştirmede depo kökündeki
    `bin/` klasörü.
    """
    dosya = _calistirilabilir(ad)
    adaylar = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        adaylar.append(os.path.join(meipass, "bin", dosya))
    paket = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    adaylar.append(os.path.join(os.path.dirname(paket), "bin", dosya))
    for yol in adaylar:
        if os.path.isfile(yol) and not _placeholder_mi(yol):
            return yol
    return None


def arama_yollari() -> List[str]:
    """Araçların bulunabileceği ek klasörler (var olanlar)."""
    yollar: List[str] = []
    try:
        from ..cli.dosyalar import Dosyalar
        yollar.append(Dosyalar().ta_path)       # kurulum hedefi
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        yollar.append(os.path.join(meipass, "bin"))
    paket = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yollar.append(os.path.join(os.path.dirname(paket), "bin"))
    return [yol for yol in yollar if yol and os.path.isdir(yol)]


def path_hazirla() -> None:
    """Ek klasörleri `PATH`e ekle.

    Eski CTk giriş noktası bunu yapıyordu, Qt'ninki yapmıyordu: sihirbaz mpv'yi
    uygulama dizinine kuruyor ama `sp.run(["mpv", ...])` onu PATH'te bulamıyor,
    yani kurulum işe yaramıyordu. Kurulumdan sonra da çağrılır ki yeniden
    başlatmak gerekmesin.
    """
    mevcut = os.environ.get("PATH", "")
    parcalar = [p for p in mevcut.split(os.pathsep) if p]
    ekler = [yol for yol in arama_yollari() if yol not in parcalar]
    if ekler:
        os.environ["PATH"] = os.pathsep.join(parcalar + ekler)


def arac_var_mi(ad: str) -> bool:
    """Araç kullanılabilir mi? (önce gömülü kopya, sonra PATH)"""
    if gomulu_arac_yolu(ad):
        return True
    try:
        sonuc = subprocess.run([ad, "--version"], capture_output=True,
                               text=True, timeout=5, check=False)
    except Exception:
        return False
    return sonuc.returncode == 0


def eksik_araclar(araclar: Optional[Sequence[str]] = None) -> List[str]:
    """Bulunamayan araçların listesi (hepsi varsa boş liste)."""
    return [ad for ad in (araclar or ARACLAR) if not arac_var_mi(ad)]


# ── Paket listesi ───────────────────────────────────────────────────────────
def gereksinim_listesi_getir(url: str = GEREKSINIM_URL,
                             timeout: int = ZAMAN_ASIMI) -> List[Dict[str, Any]]:
    """`gereksinimler.json`'ı indir."""
    yanit = requests.get(url, timeout=timeout)
    yanit.raise_for_status()
    veri = yanit.json()
    return [k for k in veri if isinstance(k, dict)] if isinstance(veri, list) else []


def paket_kaydi(liste: Sequence[Dict[str, Any]], ad: str) -> Optional[Dict[str, Any]]:
    for kayit in liste or []:
        if str(kayit.get("name") or "") == ad:
            return kayit
    return None


def paket_url(liste: Sequence[Dict[str, Any]], ad: str,
              isletim: Optional[str] = None, mimari: Optional[str] = None) -> str:
    """Bu platform/mimari için indirme adresi; yoksa boş metin.

    Mimari bulunamazsa x64'e düşülür: liste bazı araçlar için yalnızca 64-bit
    yayımlıyor ve boş dönmek "desteklenmiyor" demek olurdu.
    """
    kayit = paket_kaydi(liste, ad)
    if not kayit:
        return ""
    platformlar = kayit.get("platforms")
    if not isinstance(platformlar, dict):      # eski düz biçim: doğrudan url
        return str(kayit.get("url") or "")
    hedef = platformlar.get(isletim or get_os())
    if not isinstance(hedef, dict):
        return ""
    return str(hedef.get(mimari or get_arch()) or hedef.get("x64") or "")


# ── Kurulum ─────────────────────────────────────────────────────────────────
def _ars_ac(dosya_yolu: str, hedef: str) -> bool:
    """Arşivi `hedef`e çıkar; biçim tanınmazsa ``False``."""
    uzanti = dosya_yolu.rsplit(".", 1)[-1].lower()
    if uzanti == "zip":
        with ZipFile(dosya_yolu, "r") as arsiv:
            arsiv.extractall(hedef)
    elif uzanti in ("xz", "gz", "bz2", "tar"):
        with tarfile.open(dosya_yolu, "r:*") as arsiv:
            arsiv.extractall(hedef)
    elif uzanti == "7z":
        from py7zr import SevenZipFile
        with SevenZipFile(dosya_yolu, mode="r") as arsiv:
            arsiv.extractall(hedef)
    else:
        return False
    return True


def _arsivde_bul(kok: str, ad: str) -> Optional[str]:
    """Çıkarılan ağaçta aracın çalıştırılabilirini bul."""
    hedef = _calistirilabilir(ad).lower()
    for dizin, _alt, dosyalar in os.walk(kok):
        for dosya in dosyalar:
            if dosya.lower() == hedef:
                return os.path.join(dizin, dosya)
    return None


def indir_ve_kur(ad: str, url: str, hedef_dizin: str,
                 ilerleme: Optional[Callable[[int, int], None]] = None) -> str:
    """Aracı indir, gerekiyorsa arşivden çıkar, `hedef_dizin`e kur.

    Kurulan dosyanın yolunu döndürür; başarısızlıkta `RuntimeError` yükseltir.
    Kurucu (`.exe` setup) ÇALIŞTIRILMAZ: sessiz bir kurulum sihirbazı açmak
    kullanıcının haberi olmadan sistem değiştirmek olurdu — dosya indirilip
    kullanıcıya bırakılır.
    """
    if not url:
        raise RuntimeError(f"{ad}: bu platform için indirme adresi yok")
    os.makedirs(hedef_dizin, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        indirilen = os.path.join(tmp, os.path.basename(url.split("?")[0]) or ad)
        yanit = requests.get(url, stream=True, timeout=INDIRME_ZAMAN_ASIMI)
        yanit.raise_for_status()
        toplam = int(yanit.headers.get("content-length") or 0)
        inen = 0
        with open(indirilen, "wb") as fp:
            for parca in yanit.iter_content(chunk_size=PARCA):
                if not parca:
                    continue
                fp.write(parca)
                inen += len(parca)
                if ilerleme is not None:
                    ilerleme(inen, toplam)

        cikti = os.path.join(tmp, "cikarilan")
        os.makedirs(cikti, exist_ok=True)
        if _ars_ac(indirilen, cikti):
            kaynak = _arsivde_bul(cikti, ad)
            if kaynak is None:
                raise RuntimeError(f"{ad}: arşivde çalıştırılabilir bulunamadı")
        else:
            kaynak = indirilen           # arşiv değil, doğrudan çalıştırılabilir

        hedef = os.path.join(hedef_dizin, _calistirilabilir(ad))
        if os.path.exists(hedef):
            os.remove(hedef)             # move() var olan dosyanın üstüne yazmaz
        move(kaynak, hedef)

    if get_os() != "windows":
        try:
            os.chmod(hedef, 0o755)       # arşivden çıkan dosya çalıştırılabilir olmayabilir
        except OSError:
            pass
    return hedef


__all__ = ["ARACLAR", "GEREKSINIM_URL", "arac_var_mi", "eksik_araclar",
           "gomulu_arac_yolu", "gereksinim_listesi_getir", "paket_kaydi",
           "paket_url", "indir_ve_kur", "arama_yollari", "path_hazirla"]
