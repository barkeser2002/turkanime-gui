"""Yerel FlareSolverr: kurulum, süreç yaşam döngüsü, CF zincirine adres — arayüzsüz.

NEDEN YEREL: CF zincirinin FlareSolverr basamağı yıllarca projenin uzak
sunucusuna (`CFSession.DEFAULT_FLARESOLVERR_URL`) gidiyordu. Yani korumalı bir
siteye yapılan istek, adresiyle birlikte üçüncü bir makineden geçiyordu; o
makine yavaşladığında ya da kapandığında bütün kullanıcıların basamağı aynı
anda ölüyordu. FlareSolverr (MIT, github.com/FlareSolverr/FlareSolverr) Windows
x64 ve Linux x64 için hazır paket yayımlıyor: aynı işi kullanıcının kendi
makinesinde, yalnızca 127.0.0.1'e bağlı olarak yapıyoruz.

Üç iş, üç kural:

1. **Kurulum sabit sürümden.** Resmî GitHub sürüm varlığı (`SURUM`), bilinen
   boyut + SHA-256 ile doğrulanıyor; tutmazsa hiçbir şey yerleşmiyor. "latest"
   indirmek, yayımcının hesabını ele geçiren birinin istemcilere ikili
   dağıtması demek olurdu. Yayın hattı (`release.yml`) paketlere gömerken de
   AYNI kodu (`python -m turkanime_api.common.flaresolverr kur`) kullanıyor:
   sürüm, adres ve özet tek yerde.
2. **Süreç yetim kalmaz.** Uygulama kapanırken durdurulur; çökerse de ölür:
   Linux'ta süreç bir kabuk bekçisinin altında koşuyor (PR_SET_PDEATHSIG +
   PR_SET_CHILD_SUBREAPER, bkz. `BEKCI_BETIGI`), Windows'ta bir iş nesnesinde
   (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE). Sebep ölçüldü: FlareSolverr her istek
   için Chrome'u `setsid` + çift fork ile KOPARARAK başlatıyor; süreç grubunu
   öldürmek Chrome'a ulaşmıyor, ortada kalan başsız Chrome açılışa kadar RAM
   yiyordu.
3. **Tembel başlatma.** FlareSolverr her açılışta Chrome'u bir kez deneme
   amaçlı çalıştırıyor (ölçüldü: ~6 sn, ilk açılışta chromedriver da iner) ve
   boşta ~100 MB tutuyor. Oysa zincirin ilk iki basamağı (curl_cffi,
   cloudscraper) çoğu oturumda yetiyor. Bu yüzden süreç ilk Cloudflare
   ihtiyacında, zincirin FlareSolverr basamağına gelindiğinde başlatılıyor —
   ve ARAYÜZ THREAD'İNDE HİÇ BEKLENMİYOR (`hazir_adres`). Ayarlar'daki
   "Başlat" düğmesi elle başlatmak için.

Qt yok: CLI da aynı yöneticiyi kullanıyor.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import weakref
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional

import requests

# ── Sabit sürüm ─────────────────────────────────────────────────────────────
AD = "flaresolverr"
SURUM = "3.5.2"
YAYIN_ADRESI = "https://github.com/FlareSolverr/FlareSolverr/releases/download"


@dataclass(frozen=True)
class Varlik:
    """Bir platformun resmî sürüm varlığı.

    ``boyut`` ve ``sha256`` varlıklar indirilip ölçülerek yazıldı (iki ayrı
    indirme, iki ayrı araç: `sha256sum` ve `hashlib`). ``acik_boyut`` budama
    (`BUDANACAKLAR`) sonrası açılmış hâlin boyutu: disk alanı denetimi için.
    """

    platform: str
    ad: str
    boyut: int
    sha256: str
    acik_boyut: int

    @property
    def url(self) -> str:
        return f"{YAYIN_ADRESI}/v{SURUM}/{self.ad}"


VARLIKLAR: Dict[str, Varlik] = {
    "windows_x64": Varlik(
        "windows_x64", "flaresolverr_windows_x64.zip", 378_135_875,
        "309abd7c821546325ae6c509b40d969c5e39034be2314d21b3f5e0517feda7a7",
        524_215_364),
    "linux_x64": Varlik(
        "linux_x64", "flaresolverr_linux_x64.tar.gz", 262_840_948,
        "84f6df48849b2e1742692841805c4f284118fe4e2798b49d3a78159e89a46a91",
        785_352_054),
}

# Arşivin içinde çalışma anında HİÇ kullanılmayan dosyalar (arşivin kök
# klasörüne göre). Windows paketi, Chromium anlık yapısının test ikilisini
# (`interactive_ui_tests.exe`) de taşıyor: sıkışık 149 MB, açık 357 MB —
# paketin %40'ı. FlareSolverr yalnızca `chrome.exe`'yi çalıştırıyor. Budama
# özet doğrulandıktan SONRA yapılıyor; bütünlük garantisi zayıflamıyor.
BUDANACAKLAR = ("_internal/chrome/interactive_ui_tests.exe",)

# Linux'ta arşivden çıkınca çalıştırılabilir olması gerekenler. tar kipleri
# koruyor ama paket zip'ten (GUI paketi) ya da kipleri yok sayan bir araçla
# açılmışsa FlareSolverr "Chrome binary is not executable" deyip duruyor.
CALISTIRILABILIRLER = (
    "flaresolverr", "_internal/chrome/chrome",
    "_internal/chrome/chrome_crashpad_handler", "_internal/chrome/chrome_sandbox",
    "_internal/chrome/chrome-wrapper",
)

# ── Süreç ayarları ──────────────────────────────────────────────────────────
VARSAYILAN_PORT = 8191
YEREL_KONAK = "127.0.0.1"
# Açılış: ilk kez chromedriver indiriliyor, Windows Defender 500 MB'lık yeni
# klasörü tarıyor. Ölçülen ~6 sn; mühlet cömert.
BASLANGIC_ZAMAN_ASIMI = 120.0
# CF zincirinin bir istek için başlatmayı bekleyeceği süre. Aşılırsa istek bu
# basamağı atlar, açılış arka planda sürer ve sonraki istek hazır bulur.
TEMBEL_BEKLEME = 45.0
# Açılamayan FlareSolverr her istekte yeniden denenmesin (her deneme Chrome
# açıp kapatıyor). Elle "Başlat" bu süreyi beklemez.
YENIDEN_DENEME_ARALIGI = 300.0
# FlareSolverr her isteğe ayrı bir Chrome açıyor (~300 MB). Arama motoru
# kaynakları paralel sorguladığı için beş kaynağın aynı anda duvara çarpması
# beş Chrome demekti.
AYNI_ANDA_ISTEK = 2
DURDURMA_MUHLETI = 5.0
SAGLIK_ARALIGI = 0.3
HAZIR_MESAJI = "FlareSolverr is ready"
GUNLUK_DOSYASI = "flaresolverr.log"

# Yerel sunucuya giden istek sistemin vekil sunucusundan GEÇMEMELİ:
# `requests` ortamdaki HTTPS_PROXY'yi `no_proxy` yoksa 127.0.0.1 için de
# kullanıyor ve istek vekilde "bağlanılamadı" diye ölüyordu.
VEKILSIZ = {"http": None, "https": None}

# Linux bekçisi. Neden bir kabuk: FlareSolverr çalışırken HİÇBİR kodu bizim
# değil (donmuş ikili) ve öldüğünde arkasında Xvfb (kendi süreç grubunda) ile
# Chrome (setsid ile koparılmış, başka oturumda) kalıyor. Bekçi:
#   * PR_SET_PDEATHSIG: uygulama NASIL ölürse ölsün (SIGKILL dahil) SIGTERM alır;
#   * PR_SET_CHILD_SUBREAPER: koparılan Chrome init'e değil bekçiye bağlanır,
#     yani `pkill -P $$` ona ulaşır;
#   * FlareSolverr kendiliğinden kapansa da geride kalanları temizler.
# /bin/sh her Linux'ta var; ek bağımlılık yok. `pkill` yoksa (çok küçük
# dağıtımlar) Python tarafı ağacı /proc'tan kendisi topluyor (`_linux_agac`).
BEKCI_BETIGI = r'''
temizle() {
  trap '' TERM INT HUP
  kill -TERM 0 2>/dev/null
  pkill -TERM -P $$ 2>/dev/null
  exit "$1"
}
trap 'temizle 143' TERM INT HUP
"$@" &
wait $!
temizle $?
'''
BEKCI_ADI = "flaresolverr-bekci"
PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36

# Windows
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_SINIFI = 9

KIPLER = {
    "desteklenmiyor": "Desteklenmiyor",
    "kurulu_degil": "Kurulu değil",
    "eksik": "Eksik bağımlılık",
    "kuruluyor": "Kuruluyor…",
    "basliyor": "Başlatılıyor…",
    "calisiyor": "Çalışıyor",
    "durdu": "Durdu",
    "hata": "Hata",
}


class KurulumHatasi(RuntimeError):
    """Kurulum yapılamadı; mesaj kullanıcıya yazılmış Türkçe cümle."""


class IptalEdildi(KurulumHatasi):
    """Kullanıcı indirmeyi kesti."""


# ── Platform ────────────────────────────────────────────────────────────────
def platform_anahtari(sistem: Optional[str] = None,
                      makine: Optional[str] = None) -> Optional[str]:
    """`VARLIKLAR` anahtarı ("windows_x64"/"linux_x64"); hazır paket yoksa None.

    `utils.get_arch` bilinmeyen mimaride "x64"e düşüyor (araç listesi için
    makul); burada düşmek ARM makineye x64 ikili kurmak olurdu.
    """
    sistem = (sistem if sistem is not None else platform.system()).lower()
    makine = (makine if makine is not None else platform.machine()).lower()
    if makine not in ("x86_64", "amd64", "x64"):
        return None
    if sistem == "windows":
        return "windows_x64"
    if sistem == "linux":
        return "linux_x64"
    return None


YERINE_KULLANILAN = ("Cloudflare korumalı sitelerde yine de zincirin diğer "
                     "basamakları çalışır: Ayarlar'daki FlareSolverr adresi "
                     "(doluysa) ve yerleşik QtWebEngine çözücüsü.")


def desteklenmeme_sebebi(sistem: Optional[str] = None,
                         makine: Optional[str] = None) -> str:
    """Neden yok ve yerine ne kullanılıyor — kullanıcıya gösterilecek metin."""
    if platform_anahtari(sistem, makine):
        return ""
    sistem = (sistem if sistem is not None else platform.system()) or "bilinmiyor"
    makine = (makine if makine is not None else platform.machine()) or "bilinmiyor"
    if sistem.lower() == "darwin":
        neden = ("FlareSolverr macOS için hazır paket yayımlamıyor (kaynaktan "
                 "kurulum Python 3.11, Chrome ve XQuartz ister).")
    elif sistem.lower() in ("windows", "linux"):
        neden = (f"FlareSolverr yalnızca x64 işlemciler için hazır paket "
                 f"yayımlıyor; bu makine {makine}.")
    else:
        neden = f"FlareSolverr {sistem} için hazır paket yayımlamıyor."
    return f"{neden} {YERINE_KULLANILAN}"


def _windows_mu(anahtar: Optional[str] = None) -> bool:
    anahtar = anahtar or platform_anahtari() or ""
    return anahtar.startswith("windows")


def exe_adi(anahtar: Optional[str] = None) -> str:
    return AD + (".exe" if _windows_mu(anahtar) else "")


# ── Yerler ──────────────────────────────────────────────────────────────────
def veri_koku() -> Path:
    # Çağrı anında çözülüyor: testler ve taşınabilir kurulum kökü değiştiriyor.
    from ..cli.dosyalar import veri_koku as _kok
    return Path(_kok())


def kurulum_dizini() -> Path:
    """Sihirbazın/Ayarlar'ın kurduğu yer: diğer araçlarla aynı veri kökü
    (`requirements.RequirementsService._hedef_dizin`)."""
    return veri_koku() / AD


def gunluk_yolu() -> Path:
    """FlareSolverr çıktısı (CLI'nin `error.log`'u gibi veri kökünde)."""
    return veri_koku() / GUNLUK_DOSYASI


def gomulu_dizinler() -> List[Path]:
    """Uygulamayla gelen kopyanın aranacağı yerler, öncelik sırasıyla.

    Paketlenmiş GUI'de FlareSolverr exe'nin YANINDA (`<zip>/flaresolverr/`),
    onefile arşivinin içinde DEĞİL: içinde olsaydı her açılışta ~800 MB daha
    geçici dizine açılırdı (bkz. `turkanime-gui.spec`). `_MEIPASS/bin` ve
    deponun `bin/` klasörü `requirements.gomulu_arac_yolu` ile aynı kural.
    """
    adaylar: List[Path] = []
    if getattr(sys, "frozen", False):
        adaylar.append(Path(sys.executable).resolve().parent / AD)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        adaylar.append(Path(meipass) / "bin" / AD)
    adaylar.append(Path(__file__).resolve().parents[2] / "bin" / AD)
    return adaylar


def surum_oku(dizin: Path) -> str:
    """Kurulu kopyanın sürümü (paketin kendi `package.json`'undan)."""
    for yol in (dizin / "_internal" / "package.json", dizin / "package.json"):
        try:
            with open(yol, encoding="utf-8") as fp:
                return str(json.load(fp).get("version") or "")
        except (OSError, ValueError, AttributeError):
            continue
    return ""


@dataclass(frozen=True)
class Kurulum:
    dizin: Path
    exe: Path
    surum: str
    gomulu: bool          # uygulamayla geldi (kullanıcı kurmadı)


def kurulumu_bul(anahtar: Optional[str] = None) -> Optional[Kurulum]:
    """Kullanılacak kopya: önce uygulamayla gelen, sonra veri köküne kurulan."""
    anahtar = anahtar or platform_anahtari()
    if anahtar is None:
        return None
    ad = exe_adi(anahtar)
    for dizin in gomulu_dizinler():
        if (dizin / ad).is_file():
            return Kurulum(dizin, dizin / ad, surum_oku(dizin), True)
    dizin = kurulum_dizini()
    if (dizin / ad).is_file():
        return Kurulum(dizin, dizin / ad, surum_oku(dizin), False)
    return None


def eksik_bagimliliklar(anahtar: Optional[str] = None) -> List[str]:
    """Sistemde olması gereken ama paketle gelmeyenler.

    Linux paketi başsız kipte bile Xvfb başlatıyor (xvfbwrapper) ve yoksa
    "Could not find Xvfb" deyip açılmıyor. Paket Xvfb'yi taşımıyor, biz de
    taşıyamayız (X sunucusu): kullanıcı dağıtımının paketinden kurmalı.
    """
    anahtar = anahtar or platform_anahtari()
    if anahtar == "linux_x64" and shutil.which("Xvfb") is None:
        return ["Xvfb"]
    return []


XVFB_KURULUMU = ("Linux'ta FlareSolverr sanal ekran (Xvfb) ister. Kurmak için: "
                 "Debian/Ubuntu `sudo apt install xvfb`, Fedora `sudo dnf install "
                 "xorg-x11-server-Xvfb`, Arch `sudo pacman -S xorg-server-xvfb`.")


# ── İndirme ve doğrulama ────────────────────────────────────────────────────
def _ozet_ve_boyut(yol: Path) -> tuple:
    toplam = hashlib.sha256()
    boyut = 0
    with open(yol, "rb") as fp:
        for parca in iter(lambda: fp.read(1 << 20), b""):
            toplam.update(parca)
            boyut += len(parca)
    return toplam.hexdigest(), boyut


def _dogrula(ozet: str, boyut: int, varlik: Varlik) -> None:
    if boyut != varlik.boyut:
        raise KurulumHatasi(
            f"{varlik.ad} beklenen boyutta değil ({boyut} bayt, beklenen "
            f"{varlik.boyut}); dosya kurulmadı.")
    if ozet.lower() != varlik.sha256.lower():
        raise KurulumHatasi(
            f"{varlik.ad} SHA-256 özeti tutmuyor; dosya değiştirilmiş ya da "
            "bozuk indi, kurulmadı.")


def dosyayi_dogrula(yol: Path, varlik: Varlik) -> None:
    """Elde duran arşivi (`kur --arsiv`) sabit özete göre denetle."""
    ozet, boyut = _ozet_ve_boyut(Path(yol))
    _dogrula(ozet, boyut, varlik)


def indir(varlik: Varlik, hedef: Path,
          ilerleme: Optional[Callable[[int, int], None]] = None,
          iptal: Optional[threading.Event] = None, url: Optional[str] = None) -> None:
    """Varlığı indir; boyut ya da SHA-256 tutmazsa `KurulumHatasi`.

    Özet akış sırasında hesaplanıyor (380 MB'ı iki kez okumamak için) ve
    beyan edilen/gelen boyut beklenenden büyükse indirme hemen kesiliyor:
    yanlış dosyayı sonuna kadar indirmenin anlamı yok.
    """
    yanit = requests.get(url or varlik.url, stream=True, timeout=(15, 60))
    try:
        yanit.raise_for_status()
        beyan = int(yanit.headers.get("content-length") or 0)
        if beyan and beyan != varlik.boyut:
            raise KurulumHatasi(
                f"{varlik.ad} beklenen boyutta değil (sunucu {beyan} bayt "
                f"diyor, beklenen {varlik.boyut}); indirilmedi.")
        toplam = hashlib.sha256()
        inen = 0
        with open(hedef, "wb") as fp:
            for parca in yanit.iter_content(chunk_size=1 << 16):
                if iptal is not None and iptal.is_set():
                    raise IptalEdildi("FlareSolverr indirmesi iptal edildi.")
                if not parca:
                    continue
                fp.write(parca)
                toplam.update(parca)
                inen += len(parca)
                if inen > varlik.boyut:
                    raise KurulumHatasi(
                        f"{varlik.ad} beklenenden büyük geliyor; indirme kesildi.")
                if ilerleme is not None:
                    ilerleme(inen, varlik.boyut)
    finally:
        yanit.close()
    _dogrula(toplam.hexdigest(), inen, varlik)


# ── Arşivden çıkarma ────────────────────────────────────────────────────────
def _goreli_ad(ham: str) -> Optional[str]:
    """Arşiv üyesinin kök klasöre ("flaresolverr/") göre yolu; güvensizse None.

    Mutlak yol, `..` ve sürücü harfi reddediliyor: resmî arşivde yoklar, ama
    özet tutsa bile çıkarma kodu arşive güvenmemeli (zip-slip).
    """
    ad = ham.replace("\\", "/")
    parcalar = PurePosixPath(ad).parts
    if not parcalar or ad.startswith("/") or ":" in parcalar[0]:
        return None
    if any(p == ".." for p in parcalar):
        return None
    return "/".join(parcalar)


def _budanacak_mi(goreli: str) -> bool:
    ic = goreli.split("/", 1)[1] if "/" in goreli else ""
    return ic in BUDANACAKLAR


def _cikar(arsiv: Path, hedef: Path) -> None:
    """Arşivi `hedef`e aç; `BUDANACAKLAR` hiç yazılmaz (357 MB boşuna yazma)."""
    hedef.mkdir(parents=True, exist_ok=True)
    if arsiv.name.lower().endswith(".zip"):
        with zipfile.ZipFile(arsiv) as zf:
            uyeler = []
            for bilgi in zf.infolist():
                goreli = _goreli_ad(bilgi.filename)
                if goreli is None:
                    raise KurulumHatasi(f"arşivde güvensiz yol: {bilgi.filename!r}")
                if not _budanacak_mi(goreli):
                    uyeler.append(bilgi)
            zf.extractall(hedef, members=uyeler)
        return
    with tarfile.open(arsiv, "r:*") as tf:
        uyeler = []
        for uye in tf.getmembers():
            goreli = _goreli_ad(uye.name)
            if goreli is None or uye.isdev() or ((uye.issym() or uye.islnk())
                                                  and _goreli_ad(uye.linkname) is None):
                raise KurulumHatasi(f"arşivde güvensiz üye: {uye.name!r}")
            if not _budanacak_mi(goreli):
                uyeler.append(uye)
        if hasattr(tarfile, "data_filter"):
            # 3.12+ (ve güvenlik yamalı eskiler): bağlantı hedefleri de denetlenir.
            tf.extractall(hedef, members=uyeler, filter="data")
        else:                                   # pragma: no cover - eski Python
            tf.extractall(hedef, members=uyeler)


def _calistirilabilir_yap(dizin: Path) -> None:
    if os.name == "nt":
        return
    for goreli in CALISTIRILABILIRLER:
        yol = dizin / goreli
        try:
            if yol.is_file():
                yol.chmod(yol.stat().st_mode | 0o755)
        except OSError:
            pass            # salt okunur kurulum: FlareSolverr kendi hatasını yazar


def _bos_alan_denetle(dizin: Path, varlik: Varlik, arsiv_var: bool) -> None:
    gerekli = varlik.acik_boyut + (0 if arsiv_var else varlik.boyut) + 64 * 1024 * 1024
    try:
        bos = shutil.disk_usage(dizin).free
    except OSError:
        return
    if bos < gerekli:
        raise KurulumHatasi(
            f"Yeterli disk alanı yok: FlareSolverr için {gerekli // 1_000_000} MB "
            f"gerekiyor, {dizin} altında {bos // 1_000_000} MB boş.")


def kur(anahtar: Optional[str] = None, hedef: Optional[Path] = None,
        ilerleme: Optional[Callable[[int, int], None]] = None,
        iptal: Optional[threading.Event] = None, arsiv: Optional[Path] = None,
        url: Optional[str] = None) -> Kurulum:
    """Sabit sürümü indir (ya da `arsiv`i al), doğrula, aç ve `hedef`e koy.

    Yerleştirme atomik: yeni kopya aynı dosya sisteminde geçici bir klasöre
    açılıyor, eski kopya kenara alınıp yenisi tek `os.replace` ile yerine
    konuyor. Yarıda kesilen kurulum eski kopyayı bozmuyor; özet tutmazsa hiçbir
    dosya kalıcı yere yazılmıyor.
    """
    anahtar = anahtar or platform_anahtari()
    varlik = VARLIKLAR.get(anahtar or "")
    if varlik is None:
        raise KurulumHatasi(desteklenmeme_sebebi() or f"bilinmeyen platform: {anahtar}")
    hedef = Path(hedef) if hedef is not None else kurulum_dizini()
    ust = hedef.parent
    ust.mkdir(parents=True, exist_ok=True)
    _bos_alan_denetle(ust, varlik, arsiv is not None)
    gecici = Path(tempfile.mkdtemp(prefix=f".{AD}-kurulum-", dir=str(ust)))
    try:
        if arsiv is not None:
            dosya = Path(arsiv)
            dosyayi_dogrula(dosya, varlik)
        else:
            dosya = gecici / varlik.ad
            indir(varlik, dosya, ilerleme, iptal, url=url)
        acik = gecici / "acik"
        _cikar(dosya, acik)
        kaynak = acik / AD
        if not (kaynak / exe_adi(anahtar)).is_file():
            raise KurulumHatasi(f"arşivde {AD}/{exe_adi(anahtar)} yok; paket biçimi değişmiş.")
        _calistirilabilir_yap(kaynak)
        eski = None
        if hedef.exists():
            eski = gecici / "eski"
            os.replace(hedef, eski)
        try:
            os.replace(kaynak, hedef)
        except OSError:
            if eski is not None:
                os.replace(eski, hedef)       # eski kopyayı geri koy
            raise
        return Kurulum(hedef, hedef / exe_adi(anahtar), surum_oku(hedef), False)
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


# ── Süreç yardımcıları ──────────────────────────────────────────────────────
def bos_port(tercih: int = VARSAYILAN_PORT, konak: str = YEREL_KONAK) -> int:
    """`tercih` boşsa o, değilse işletim sisteminin verdiği boş bir port.

    8191 başka bir şeydeyse (kullanıcının kendi FlareSolverr'ı, başka bir
    program) oraya bağlanmaya çalışmak iki süreci de bozardı.
    """
    for port in (tercih, 0):
        if port is None:
            continue
        soket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            soket.bind((konak, int(port)))
            return int(soket.getsockname()[1])
        except OSError:
            continue
        finally:
            soket.close()
    raise OSError("boş port bulunamadı")


def saglik(adres: str, zaman_asimi: float = 2.0) -> str:
    """`GET /` "FlareSolverr is ready!" derse sürümü (ya da "?"), yoksa "".

    Mesajın kendisine bakılıyor: port başka bir programa geçmişse (8191 yarışı)
    her 200 yanıtını "hazır" saymak isteği yanlış sunucuya gönderirdi.
    """
    try:
        yanit = requests.get(adres.rstrip("/") + "/", timeout=zaman_asimi,
                             proxies=VEKILSIZ)
        veri = yanit.json()
    except Exception:
        return ""
    if isinstance(veri, dict) and HAZIR_MESAJI in str(veri.get("msg") or ""):
        return str(veri.get("version") or "?")
    return ""


def cocuk_ortami(port: int, taban: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """FlareSolverr'ın ortam değişkenleri.

    PyInstaller ile donmuş uygulamamızın ortamı AYNEN geçmemeli: onefile
    açılışı `LD_LIBRARY_PATH`'i kendi geçici dizinine çeviriyor ve özgününü
    `LD_LIBRARY_PATH_ORIG`'e saklıyor; o ortamla açılan Chrome bizim Qt
    kitaplıklarımızı yüklemeye kalkıyor. `_PYI_*`/`_MEIPASS2` başka bir donmuş
    uygulamanın (FlareSolverr da PyInstaller) açılışını şaşırtıyor.
    """
    ortam = dict(os.environ if taban is None else taban)
    for anahtar in list(ortam):
        if anahtar.startswith("_PYI_") or anahtar in (
                "_MEIPASS2", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                "LOG_FILE", "PROMETHEUS_PORT"):
            del ortam[anahtar]
    if getattr(sys, "frozen", False):
        for ad in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            ozgun = ortam.pop(ad + "_ORIG", None)
            if ozgun is not None:
                ortam[ad] = ozgun
            else:
                ortam.pop(ad, None)
    # FlareSolverr LANG'i olduğu gibi `--accept-lang=` yapıyor: "tr_TR.UTF-8"
    # Chrome'a bozuk bir Accept-Language yazdırır — tuhaf başlık bot
    # tespitini kolaylaştırır. BCP 47'ye çevriliyor ("tr-TR"); C/POSIX atılıyor.
    dil = str(ortam.get("LANG") or "").split(".", 1)[0].split("@", 1)[0]
    if dil and dil not in ("C", "POSIX"):
        ortam["LANG"] = dil.replace("_", "-")
    else:
        ortam.pop("LANG", None)
    ortam.update({
        # 0.0.0.0 FlareSolverr'ın varsayılanı: yerel ağdaki herkes bu makinenin
        # Chrome'unu vekil olarak kullanabilirdi.
        "HOST": YEREL_KONAK,
        "PORT": str(int(port)),
        # Kullanıcının başka bir program için koyduğu LOG_LEVEL (ör. "verbose")
        # FlareSolverr'ın `logging.basicConfig`'ini düşürüp açılışı öldürüyordu.
        "LOG_LEVEL": "info",
        "HEADLESS": "true",
        "LOG_HTML": "false",
        "PROMETHEUS_ENABLED": "false",
    })
    return ortam


def gunluk_sonu(yol: Path, bayt: int = 6000) -> List[str]:
    """Günlüğün son dolu satırları (başlatma hatasını anlatmak için)."""
    try:
        with open(yol, "rb") as fp:
            fp.seek(0, os.SEEK_END)
            fp.seek(max(0, fp.tell() - bayt))
            metin = fp.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [s.strip() for s in metin.splitlines() if s.strip()][-12:]


_BILINEN_HATALAR = (
    (re.compile(r"error while loading shared libraries: (\S+?):"),
     "Sistem kitaplığı eksik: {0}. Dağıtımınızın paket yöneticisiyle kurun "
     "(ör. libwayland-server.so.0 → Ubuntu'da libwayland-server0)."),
    (re.compile(r"Could not find Xvfb"), XVFB_KURULUMU),
    (re.compile(r"(Address already in use|Errno 98|WinError 10048|only one usage of each socket)", re.I),
     "Port başka bir program tarafından kullanılıyor."),
    (re.compile(r"(Error getting browser User-Agent|Error starting Chrome|Chrome / Chromium version not detected)"),
     "FlareSolverr'ın Chrome'u açılamadı. İlk açılışta chromedriver indirildiği "
     "için internet bağlantısı gerekir; ayrıntı günlükte."),
)


def hata_ozeti(satirlar: List[str], son_satir: bool = True) -> str:
    """Günlük satırlarından kullanıcıya bir cümle.

    Bilinen bir kalıp yoksa son satır (``son_satir=False`` ise boş): açılış
    hatasında FlareSolverr'ın son sözü işe yarıyor, ama sonradan ölen süreçte
    son satır kabuğun "Killed"ı olabiliyor — o bir sebep değil.
    """
    metin = "\n".join(satirlar)
    for kalip, cumle in _BILINEN_HATALAR:
        eslesme = kalip.search(metin)
        if eslesme:
            return cumle.format(*eslesme.groups())
    return satirlar[-1] if (satirlar and son_satir) else ""


def _arayuz_threadi_mi() -> bool:
    """Qt arayüzünün (ana) thread'inde miyiz? Orada hiçbir şey beklenmez.

    Qt import edilmeden bakılıyor: bu modül CLI'da da çalışıyor. CLI'da
    QApplication yok, orada ana thread'in beklemesi doğal.
    """
    if threading.current_thread() is not threading.main_thread():
        return False
    qtcore = sys.modules.get("PySide6.QtCore")
    if qtcore is None:
        return False
    try:
        return qtcore.QCoreApplication.instance() is not None
    except Exception:
        return False


class _OmurlukIplik:
    """Linux süreçlerini başlatan, SÜREÇ BOYU yaşayan tek thread.

    PR_SET_PDEATHSIG süreci değil, çocuğu yaratan THREAD'i izliyor: "ebeveyn
    thread bitince sinyal gelir" (man 2 prctl). Tembel başlatma bir işçi
    thread'inden geliyor ve QThreadPool boşta kalan thread'i 30 sn'de
    emekliye ayırıyor — FlareSolverr yarım dakika sonra durup dururken
    ölürdü. Başlatma bu yüzden hep buradan yapılıyor.
    """

    def __init__(self):
        self._kuyruk: "queue.Queue" = queue.Queue()
        self._iplik: Optional[threading.Thread] = None
        self._kilit = threading.Lock()

    def _dongu(self) -> None:
        while True:
            fn, kutu = self._kuyruk.get()
            try:
                kutu.put((True, fn()))
            except BaseException as exc:  # noqa: BLE001 - çağırana taşınıyor
                kutu.put((False, exc))

    def calistir(self, fn: Callable[[], Any]) -> Any:
        with self._kilit:
            if self._iplik is None or not self._iplik.is_alive():
                self._iplik = threading.Thread(target=self._dongu, daemon=True,
                                               name="flaresolverr-baslatici")
                self._iplik.start()
        kutu: "queue.Queue" = queue.Queue(maxsize=1)
        self._kuyruk.put((fn, kutu))
        basarili, deger = kutu.get()
        if not basarili:
            raise deger
        return deger


_OMURLUK = _OmurlukIplik()


def _linux_cocuk_hazirligi(ebeveyn: int) -> Callable[[], None]:
    """`preexec_fn`: fork ile exec arasında, çocukta çalışır.

    ctypes işlevi EBEVEYNDE çözülüyor; çocukta import ya da kilit alan hiçbir
    şey yapılmıyor (çok thread'li süreçte fork sonrası güvenli kalmak için).
    """
    import ctypes
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                      ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    olum_sinyali = int(signal.SIGTERM)

    def hazirla() -> None:
        prctl(PR_SET_PDEATHSIG, olum_sinyali, 0, 0, 0)
        prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        # Ebeveyn fork ile prctl arasında öldüyse sinyal hiç gelmeyecek.
        if os.getppid() != ebeveyn:
            os._exit(1)

    return hazirla


def _linux_agac(kok: int) -> List[int]:
    """`kok`un bütün torunları (/proc'tan). Bekçi alt-biçer olduğu için
    koparılmış Chrome'lar da burada — oturumları farklı olsa bile."""
    cocuklar: Dict[int, List[int]] = {}
    try:
        adlar = os.listdir("/proc")
    except OSError:
        return []
    for ad in adlar:
        if not ad.isdigit():
            continue
        try:
            with open(f"/proc/{ad}/stat", encoding="utf-8", errors="replace") as fp:
                veri = fp.read()
            ebeveyn = int(veri.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        cocuklar.setdefault(ebeveyn, []).append(int(ad))
    sonuc: List[int] = []
    bekleyen = list(cocuklar.get(kok, []))
    while bekleyen:
        pid = bekleyen.pop()
        sonuc.append(pid)
        bekleyen.extend(cocuklar.get(pid, []))
    return sonuc


def _sinyal(pidler: List[int], sinyal: int) -> None:
    for pid in pidler:
        try:
            os.kill(pid, sinyal)
        except OSError:
            pass


# Windows iş nesnesi yapıları (winnt.h). Modül düzeyinde: yapı boyutu Linux'ta
# da sınanabiliyor (x64'te 144 bayt olmalı).
import ctypes as _ctypes  # noqa: E402


class _IO_COUNTERS(_ctypes.Structure):
    _fields_ = [(ad, _ctypes.c_ulonglong) for ad in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(_ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", _ctypes.c_int64),
                ("PerJobUserTimeLimit", _ctypes.c_int64),
                ("LimitFlags", _ctypes.c_uint32),
                ("MinimumWorkingSetSize", _ctypes.c_size_t),
                ("MaximumWorkingSetSize", _ctypes.c_size_t),
                ("ActiveProcessLimit", _ctypes.c_uint32),
                ("Affinity", _ctypes.c_size_t),
                ("PriorityClass", _ctypes.c_uint32),
                ("SchedulingClass", _ctypes.c_uint32)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(_ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", _ctypes.c_size_t),
                ("JobMemoryLimit", _ctypes.c_size_t),
                ("PeakProcessMemoryUsed", _ctypes.c_size_t),
                ("PeakJobMemoryUsed", _ctypes.c_size_t)]


def _windows_kernel32():  # pragma: no cover - yalnızca Windows
    from ctypes import wintypes
    k32 = _ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = [_ctypes.c_void_p, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, _ctypes.c_int,
                                            _ctypes.c_void_p, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def _windows_is_nesnesi(surec: subprocess.Popen) -> Optional[int]:  # pragma: no cover
    """Süreci "tutacak kapanınca hepsini öldür" iş nesnesine koy.

    Tutacak yalnızca bizde; uygulama nasıl ölürse ölsün (görev yöneticisi
    dahil) Windows tutacağı kapatır ve iş nesnesindeki HER süreç — FlareSolverr
    ve onun başlattığı Chrome'lar — ölür. Sonradan doğan torunlar da iş
    nesnesine kendiliğinden giriyor.
    """
    k32 = _windows_kernel32()
    is_nesnesi = k32.CreateJobObjectW(None, None)
    if not is_nesnesi:
        return None
    bilgi = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    bilgi.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (k32.SetInformationJobObject(is_nesnesi, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_SINIFI,
                                        _ctypes.byref(bilgi), _ctypes.sizeof(bilgi))
            and k32.AssignProcessToJobObject(is_nesnesi, int(surec._handle))):
        k32.CloseHandle(is_nesnesi)
        return None
    return is_nesnesi


# ── Yönetici ────────────────────────────────────────────────────────────────
class Yonetici:
    """Tek yerel FlareSolverr örneği (süreç başına bir tane, bkz. `yonetici`).

    Durumlar (`KIPLER`): desteklenmiyor, kurulu_degil, eksik, kuruluyor,
    basliyor, calisiyor, durdu, hata. Dinleyiciler her değişimde
    `durum_ozeti()` sözlüğüyle çağrılır (her thread'den).
    """

    def __init__(self, tercih_port: int = VARSAYILAN_PORT,
                 baslangic_zaman_asimi: float = BASLANGIC_ZAMAN_ASIMI):
        self.tercih_port = tercih_port
        self.baslangic_zaman_asimi = baslangic_zaman_asimi
        self._kilit = threading.RLock()
        self._surec: Optional[subprocess.Popen] = None
        self._is_nesnesi: Optional[int] = None
        self._nesil = 0
        self._kip = "durdu"
        self._adres = ""
        self._port: Optional[int] = None
        self._surum = ""
        self._hata = ""
        self._hata_zamani = 0.0
        self._elle_durduruldu = False
        self._kuruluyor = False
        self._sonuc = threading.Event()
        self._dinleyiciler: List[Any] = []
        self._istek_siniri = threading.BoundedSemaphore(AYNI_ANDA_ISTEK)
        self._atexit_kayitli = False

    # ── Dinleyiciler ────────────────────────────────────────────────────────
    def dinle(self, fn: Callable[[Dict[str, Any]], Any]) -> None:
        """Durum değişimlerini bildir. Bağlı metotlar ZAYIF tutuluyor: ayar
        sayfası her pencerede yeniden kuruluyor, süreç boyu yaşayan yönetici
        onları ölümsüz kılmamalı."""
        # Yalnızca Python bağlı metotları (`__func__` taşıyan); `list.append`
        # gibi yerleşik metotlar zayıf tutulamıyor.
        ref = weakref.WeakMethod(fn) if hasattr(fn, "__func__") else (lambda: fn)
        with self._kilit:
            self._dinleyiciler.append(ref)

    def _bildir(self) -> None:
        ozet = self.durum_ozeti()
        with self._kilit:
            canlilar = []
            for ref in self._dinleyiciler:
                fn = ref()
                if fn is not None:
                    canlilar.append((ref, fn))
            self._dinleyiciler = [ref for ref, _fn in canlilar]
        for _ref, fn in canlilar:
            try:
                fn(dict(ozet))
            except Exception as exc:  # dinleyici hatası yönetimi bozmamalı
                print(f"[FlareSolverr] dinleyici hatası: {exc}")

    # ── Sorgular ────────────────────────────────────────────────────────────
    def kurulum(self) -> Optional[Kurulum]:
        return kurulumu_bul()

    def _canli_mi(self) -> bool:
        return self._surec is not None and self._surec.poll() is None

    def kullanilabilir(self) -> bool:
        """Destekleniyor, kurulu, bağımlılıkları tam (çalışıyor olması gerekmez)."""
        return (platform_anahtari() is not None and self.kurulum() is not None
                and not eksik_bagimliliklar())

    def kendiliginden_baslayabilir(self) -> bool:
        """Tembel başlatma serbest mi? Elle durdurulmuşsa ya da az önce
        açılamadıysa hayır — kullanıcının "Durdur"u, zincirin ilk ihtiyacında
        yok sayılmamalı."""
        with self._kilit:
            if self._elle_durduruldu or self._kuruluyor:
                return False
            if (self._kip == "hata"
                    and time.monotonic() - self._hata_zamani < YENIDEN_DENEME_ARALIGI):
                return False
        return self.kullanilabilir()

    def basliyor(self) -> bool:
        with self._kilit:
            return self._kip == "basliyor" and self._canli_mi()

    def adres(self) -> str:
        with self._kilit:
            if self._kip == "calisiyor" and self._canli_mi():
                return self._adres
        return ""

    def istek_siniri(self) -> threading.BoundedSemaphore:
        return self._istek_siniri

    def durum_ozeti(self) -> Dict[str, Any]:
        """Ayarlar sayfasının ve sihirbazın gösterdiği her şey (ağa çıkmaz)."""
        anahtar = platform_anahtari()
        kurulum = self.kurulum() if anahtar else None
        eksik = eksik_bagimliliklar(anahtar) if anahtar else []
        varlik = VARLIKLAR.get(anahtar or "")
        with self._kilit:
            canli = self._canli_mi()
            kip = self._kip
            if kip in ("basliyor", "calisiyor") and not canli:
                kip = "durdu"
            if self._kuruluyor:
                kip = "kuruluyor"
            elif anahtar is None:
                kip = "desteklenmiyor"
            elif kurulum is None and not canli:
                kip = "kurulu_degil"
            elif eksik and not canli:
                kip = "eksik"
            ozet = {
                "durum": kip,
                "etiket": KIPLER[kip],
                "destekli": anahtar is not None,
                "kurulu": kurulum is not None,
                "gomulu": bool(kurulum and kurulum.gomulu),
                "surum": (self._surum if canli and self._surum not in ("", "?")
                          else (kurulum.surum if kurulum else "")),
                "sabit_surum": SURUM,
                "adres": self._adres if kip == "calisiyor" else "",
                "port": self._port if canli else None,
                "yol": str(kurulum.dizin) if kurulum else str(kurulum_dizini()),
                "gunluk": str(gunluk_yolu()),
                "eksik": eksik,
                "hata": self._hata if kip == "hata" else "",
                "elle_durduruldu": self._elle_durduruldu,
                "boyut_mb": round(varlik.boyut / 1_000_000) if varlik else 0,
            }
        ozet["guncel"] = bool(ozet["surum"]) and ozet["surum"] == SURUM
        ozet["kurulabilir"] = bool(anahtar) and not ozet["gomulu"]
        ozet["metin"] = self._aciklama(ozet)
        return ozet

    @staticmethod
    def _aciklama(ozet: Dict[str, Any]) -> str:
        kip = ozet["durum"]
        if kip == "desteklenmiyor":
            return desteklenmeme_sebebi()
        if kip == "kurulu_degil":
            return (f"Yerel FlareSolverr kurulu değil (~{ozet['boyut_mb']} MB indirme). "
                    "Kurulana kadar Ayarlar'daki adres (doluysa) kullanılır.")
        if kip == "eksik":
            return XVFB_KURULUMU if "Xvfb" in ozet["eksik"] else (
                "Eksik: " + ", ".join(ozet["eksik"]))
        if kip == "kuruluyor":
            return "FlareSolverr indiriliyor ve doğrulanıyor…"
        if kip == "basliyor":
            return "FlareSolverr açılıyor (Chrome deneniyor; ilk açılış biraz sürebilir)…"
        if kip == "calisiyor":
            return f"{ozet['adres']} adresinde çalışıyor (yalnızca bu bilgisayardan erişilebilir)."
        if kip == "hata":
            return f"Açılamadı: {ozet['hata']} Ayrıntı: {ozet['gunluk']}"
        kaynak = "uygulamayla geldi" if ozet["gomulu"] else "veri klasörüne kuruldu"
        ek = (" Elle durduruldu; bu oturumda kendiliğinden başlamaz."
              if ozet["elle_durduruldu"] else
              " İlk Cloudflare engelinde kendiliğinden başlar.")
        return f"Kurulu ({kaynak}).{ek}"

    # ── Başlatma ────────────────────────────────────────────────────────────
    def _hata_yaz(self, mesaj: str) -> None:
        self._kip = "hata"
        self._hata = mesaj
        self._hata_zamani = time.monotonic()
        self._adres = ""
        self._sonuc.set()

    def _baslatilamaz_sebebi(self) -> str:
        anahtar = platform_anahtari()
        if anahtar is None:
            return desteklenmeme_sebebi()
        if self.kurulum() is None:
            return "FlareSolverr kurulu değil."
        eksik = eksik_bagimliliklar(anahtar)
        if eksik:
            return XVFB_KURULUMU if "Xvfb" in eksik else "Eksik: " + ", ".join(eksik)
        return ""

    def baslat(self, bekle: bool = True, zaman_asimi: Optional[float] = None,
               elle: bool = False) -> str:
        """Başlat (çalışıyor/açılıyorsa yenisini açmaz); hazırsa adres, yoksa "".

        ``bekle=True`` hazır ya da başarısız olana kadar bekler — arayüz
        thread'inden ÇAĞRILMAMALI (Ayarlar bunu arka planda yapıyor).
        ``elle=True``: kullanıcı istedi; "Durdur" tercihi ve bekleme aralığı
        sıfırlanır.
        """
        zaman_asimi = self.baslangic_zaman_asimi if zaman_asimi is None else zaman_asimi
        bildir = False
        with self._kilit:
            if elle:
                self._elle_durduruldu = False
            if self._kuruluyor:
                return ""
            if not self._canli_mi():
                sebep = self._baslatilamaz_sebebi()
                if sebep:
                    self._hata_yaz(sebep)
                    bildir = True
                else:
                    self._baslat_kilitli(zaman_asimi)
                    bildir = True
            olay = self._sonuc
        if bildir:
            self._bildir()
        if bekle:
            olay.wait(zaman_asimi + DURDURMA_MUHLETI)
        return self.adres()

    def hazir_adres(self, bekle: float = TEMBEL_BEKLEME) -> str:
        """CF zinciri için: gerekiyorsa başlat, en çok ``bekle`` sn bekle.

        Arayüz thread'inde beklenmiyor: orada açılış arka planda başlar, bu
        istek basamağı atlar.
        """
        adres = self.adres()
        if adres or not self.kendiliginden_baslayabilir():
            return adres
        self.baslat(bekle=False)
        if not _arayuz_threadi_mi() and bekle > 0:
            with self._kilit:
                olay = self._sonuc
            olay.wait(bekle)
        return self.adres()

    def _baslat_kilitli(self, zaman_asimi: float) -> None:
        """Kilit altında: süreci aç, izleyiciyi başlat."""
        kurulum = self.kurulum()
        assert kurulum is not None
        self._nesil += 1
        nesil = self._nesil
        self._sonuc = threading.Event()
        self._kip = "basliyor"
        self._hata = ""
        self._adres = ""
        self._surum = ""
        try:
            port = bos_port(self.tercih_port)
            gunluk = self._gunluk_ac()
            try:
                surec, is_nesnesi = self._surec_ac(kurulum, port, gunluk)
            finally:
                gunluk.close()          # çocuk kendi kopyasını tutuyor
        except Exception as exc:
            self._hata_yaz(f"süreç başlatılamadı: {exc}")
            return
        self._surec, self._is_nesnesi, self._port = surec, is_nesnesi, port
        if not self._atexit_kayitli:
            # CLI ve Qt'siz kullanım için. GUI `closeEvent`'te zaten durduruyor;
            # `os._exit` yolu atexit'i atladığı için orada da açıkça çağrılıyor.
            atexit.register(self.kapat)
            self._atexit_kayitli = True
        threading.Thread(target=self._izle, args=(nesil, surec, port, zaman_asimi),
                         daemon=True, name="flaresolverr-izleyici").start()

    @staticmethod
    def _gunluk_ac():
        yol = gunluk_yolu()
        yol.parent.mkdir(parents=True, exist_ok=True)
        try:
            if yol.is_file() and yol.stat().st_size:
                os.replace(yol, yol.with_name(yol.name + ".1"))   # bir önceki koşu
        except OSError:
            pass
        return open(yol, "ab")

    def _surec_ac(self, kurulum: Kurulum, port: int, gunluk) -> tuple:
        ortam = cocuk_ortami(port)
        ortak = dict(cwd=str(kurulum.dizin), env=ortam, stdin=subprocess.DEVNULL,
                     stdout=gunluk, stderr=subprocess.STDOUT)
        if os.name == "nt":  # pragma: no cover - yalnızca Windows
            surec = subprocess.Popen([str(kurulum.exe)], creationflags=CREATE_NO_WINDOW,
                                     **ortak)
            is_nesnesi = None
            try:
                is_nesnesi = _windows_is_nesnesi(surec)
            except Exception as exc:
                print(f"[FlareSolverr] iş nesnesi kurulamadı ({exc}); kapanışta "
                      "ağaç taskkill ile temizlenecek")
            return surec, is_nesnesi
        _calistirilabilir_yap(kurulum.dizin)
        hazirla = _linux_cocuk_hazirligi(os.getpid())
        komut = ["/bin/sh", "-c", BEKCI_BETIGI, BEKCI_ADI, str(kurulum.exe)]
        surec = _OMURLUK.calistir(lambda: subprocess.Popen(
            komut, start_new_session=True, preexec_fn=hazirla, **ortak))
        return surec, None

    def _izle(self, nesil: int, surec: subprocess.Popen, port: int,
              zaman_asimi: float) -> None:
        """Sağlık denetimi, sonra kapanışı bekle (arka plan thread'i)."""
        adres = f"http://{YEREL_KONAK}:{port}"
        bitis = time.monotonic() + zaman_asimi
        hazir = ""
        while time.monotonic() < bitis:
            if surec.poll() is not None:
                break
            hazir = saglik(adres)
            if hazir:
                break
            time.sleep(SAGLIK_ARALIGI)
        ayrilan: tuple = (None, None)
        with self._kilit:
            guncel = nesil == self._nesil
            if guncel and hazir and surec.poll() is None:
                self._kip = "calisiyor"
                self._adres = adres
                self._surum = hazir
                self._sonuc.set()
            elif guncel:
                if surec.poll() is None:
                    sebep = f"{int(zaman_asimi)} sn içinde hazır olmadı."
                else:
                    sebep = hata_ozeti(gunluk_sonu(gunluk_yolu())) or (
                        f"süreç açılırken kapandı (kod {surec.returncode}).")
                ayrilan = self._ayir()
                self._hata_yaz(sebep)
        self._oldur(*ayrilan)
        if guncel:
            self._bildir()
        if not (guncel and hazir):
            return
        kod = surec.wait()
        with self._kilit:
            if nesil != self._nesil or self._surec is not surec:
                return               # biz durdurduk ya da yenisi açıldı
            ayrilan = self._ayir()   # bekçi öldüyse geride kalan olmasın
            sebep = hata_ozeti(gunluk_sonu(gunluk_yolu()), son_satir=False)
            self._hata_yaz(f"beklenmedik şekilde kapandı (kod {kod})."
                           + (f" {sebep}" if sebep else ""))
        self._oldur(*ayrilan)
        self._bildir()

    # ── Durdurma ────────────────────────────────────────────────────────────
    def durdur(self, elle: bool = False) -> bool:
        """Süreci ve bütün torunlarını durdur; bir şey durdurulduysa True.

        ``elle=True``: kullanıcı "Durdur"a bastı — bu oturumda tembel
        başlatma onu yeniden açmasın.
        """
        with self._kilit:
            if elle:
                self._elle_durduruldu = True
            ayrilan = self._ayir()
            self._kip, self._hata, self._adres = "durdu", "", ""
            self._sonuc.set()
        self._oldur(*ayrilan)
        self._bildir()
        return ayrilan[0] is not None

    def kapat(self) -> None:
        """Uygulama kapanırken (atexit, `closeEvent`): tercihlere dokunmadan durdur."""
        try:
            with self._kilit:
                if self._surec is None:
                    return
                ayrilan = self._ayir()
                self._kip, self._adres = "durdu", ""
                self._sonuc.set()
            self._oldur(*ayrilan)
        except Exception as exc:  # kapanış yolu asla fırlatmamalı
            print(f"[FlareSolverr] kapanışta durdurulamadı: {exc}")

    def _ayir(self) -> tuple:
        """Kilit altında: süreci yönetimden çıkar, izleyiciyi eskit.

        Öldürme kilit DIŞINDA (`_oldur`): 5+5 sn sürebiliyor ve ayarlar
        sayfası durumu arayüz thread'inden okuyor — kilit tutulsaydı pencere
        o süre donardı.
        """
        ayrilan = (self._surec, self._is_nesnesi)
        self._surec, self._is_nesnesi, self._port = None, None, None
        self._nesil += 1
        return ayrilan

    @classmethod
    def _oldur(cls, surec: Optional[subprocess.Popen], is_nesnesi: Optional[int]) -> None:
        """Süreci ve torunlarını öldür (önce SIGTERM, mühlet sonunda SIGKILL)."""
        if surec is None:
            return
        if os.name == "nt":  # pragma: no cover - yalnızca Windows
            cls._windows_durdur(surec, is_nesnesi)
            return
        agac = _linux_agac(surec.pid)
        son = time.monotonic() + DURDURMA_MUHLETI
        try:
            os.killpg(surec.pid, signal.SIGTERM)
        except OSError:
            pass
        _sinyal(agac, signal.SIGTERM)
        try:
            surec.wait(DURDURMA_MUHLETI)
        except subprocess.TimeoutExpired:
            pass
        # Bekçi hemen çıkıyor ama torunlar (Xvfb, Chrome) SIGTERM'de kendi
        # temizliğini yapıyor: Xvfb /tmp/.X<n>-lock'u siliyor. Hemen SIGKILL
        # atmak o dosyaları ortada bırakıyordu (canlı koşuda görüldü); aynı
        # mühlet içinde kendiliğinden bitmelerini bekle.
        agac = list(set(agac) | set(_linux_agac(surec.pid)))
        while time.monotonic() < son and any(_yasiyor_mu(p) for p in agac):
            time.sleep(0.05)
        kalan = [p for p in agac if _yasiyor_mu(p)]
        if surec.poll() is None or kalan:
            try:
                os.killpg(surec.pid, signal.SIGKILL)
            except OSError:
                pass
            _sinyal(kalan, signal.SIGKILL)
            try:
                surec.wait(DURDURMA_MUHLETI)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def _windows_durdur(surec: subprocess.Popen, is_nesnesi: Optional[int]) -> None:  # pragma: no cover
        try:
            if is_nesnesi:
                k32 = _windows_kernel32()
                k32.TerminateJobObject(is_nesnesi, 1)
                k32.CloseHandle(is_nesnesi)
            else:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(surec.pid)],
                               capture_output=True, timeout=15,
                               creationflags=CREATE_NO_WINDOW, check=False)
        except Exception as exc:
            print(f"[FlareSolverr] durdurma hatası: {exc}")
        try:
            surec.wait(DURDURMA_MUHLETI)
        except subprocess.TimeoutExpired:
            surec.kill()

    # ── Kurulum (yönetici üzerinden) ────────────────────────────────────────
    def kur(self, ilerleme: Optional[Callable[[int, int], None]] = None,
            iptal: Optional[threading.Event] = None) -> Kurulum:
        """Veri köküne kur. Çalışan kopya veri kökündeyse önce durdurulur:
        Windows kullanımdaki dosyanın üstüne yazdırmaz."""
        ayrilan: tuple = (None, None)
        with self._kilit:
            if self._kuruluyor:
                raise KurulumHatasi("Kurulum zaten sürüyor.")
            self._kuruluyor = True
            mevcut = self.kurulum()
            if mevcut is not None and not mevcut.gomulu and self._surec is not None:
                ayrilan = self._ayir()
                self._kip, self._adres = "durdu", ""
                self._sonuc.set()
        self._oldur(*ayrilan)
        self._bildir()
        try:
            sonuc = kur(ilerleme=ilerleme, iptal=iptal)
        finally:
            with self._kilit:
                self._kuruluyor = False
                if self._kip == "hata":
                    self._kip, self._hata = "durdu", ""
        self._bildir()
        return sonuc


def _yasiyor_mu(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                          # zombi "yaşıyor" sayılmasın
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fp:
            return fp.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


_YONETICI: Optional[Yonetici] = None
_YONETICI_KILIDI = threading.Lock()


def yonetici() -> Yonetici:
    """Süreç boyu tek yönetici (tek yerel FlareSolverr)."""
    global _YONETICI
    with _YONETICI_KILIDI:
        if _YONETICI is None:
            _YONETICI = Yonetici()
        return _YONETICI


def kapat() -> None:
    """Varsa çalışan yerel FlareSolverr'ı durdur (uygulama kapanışı)."""
    if _YONETICI is not None:
        _YONETICI.kapat()


# ── Gereksinim sihirbazı ────────────────────────────────────────────────────
def kurulum_oner() -> bool:
    """Sihirbaz FlareSolverr'ı önersin mi? Destekli ve hiçbir kopya yoksa."""
    return platform_anahtari() is not None and kurulumu_bul() is None


def sihirbaz_bilgisi(onerildi: bool = True) -> Dict[str, Any]:
    """Gereksinim penceresindeki FlareSolverr satırı."""
    ozet = yonetici().durum_ozeti()
    kurulabilir = ozet["durum"] == "kurulu_degil"
    if ozet["durum"] == "desteklenmiyor":
        metin = ozet["metin"]
    elif kurulabilir:
        metin = (f"İsteğe bağlı, önerilir. Cloudflare korumalı kaynakları bu "
                 f"bilgisayarda çözer (uzak sunucuya gitmeden). ~{ozet['boyut_mb']} MB "
                 f"indirme, sürüm {SURUM}, SHA-256 ile doğrulanır.")
    else:
        metin = ozet["metin"]
    return {"goster": bool(onerildi or ozet["durum"] in ("desteklenmiyor", "eksik")),
            "durum": ozet["durum"], "kurulabilir": kurulabilir,
            "sec": kurulabilir and onerildi, "boyut_mb": ozet["boyut_mb"],
            "surum": SURUM, "metin": metin}


# ── Komut satırı (yayın hattı) ──────────────────────────────────────────────
def _mb(bayt: int) -> str:
    return f"{bayt / 1_000_000:.1f} MB"


def _dizin_boyutu(dizin: Path) -> int:
    toplam = 0
    for kok, _alt, dosyalar in os.walk(dizin):
        for ad in dosyalar:
            try:
                toplam += os.path.getsize(os.path.join(kok, ad))
            except OSError:
                pass
    return toplam


def _ciktiyi_utf8_yap() -> None:
    """Çıktı UTF-8 değilse UTF-8'e çevir.

    Windows'ta Python boruya ya da dosyaya yazarken sistemin kod sayfasını
    (cp1252) kullanıyor; "←" ve ş/ı/ğ orada yok ve `print` süreci
    `UnicodeEncodeError` ile düşürüyor. Ölçüldü: v10.3.1'in Windows derlemesi
    FlareSolverr'ı paketleyemeden bu yüzden kırmızıya döndü. Gerçek konsol
    zaten UTF-8 (PEP 528) ve dokunulmuyor; GitHub Actions günlükleri UTF-8
    okuyor.
    """
    for akis in (sys.stdout, sys.stderr):
        kodlama = str(getattr(akis, "encoding", "") or "").lower().replace("-", "").replace("_", "")
        if kodlama == "utf8":
            continue
        try:
            akis.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass            # yeniden yapılandırılamayan akış: olduğu gibi


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m turkanime_api.common.flaresolverr kur --platform P --hedef D``

    Yayın hattı paketlere gömerken bunu çağırıyor: indirme, boyut + SHA-256
    doğrulaması ve budama uygulamadakiyle AYNI kod. Başarısızlıkta 1 döner ve
    CI adımı kırmızıya döner — FlareSolverr'sız paket sessizce çıkmaz.
    """
    _ciktiyi_utf8_yap()
    ayristirici = argparse.ArgumentParser(prog="python -m turkanime_api.common.flaresolverr")
    alt = ayristirici.add_subparsers(dest="komut", required=True)
    k = alt.add_parser("kur", help="sabit sürümü indir, doğrula, hedefe aç")
    k.add_argument("--platform", choices=sorted(VARLIKLAR), default=None)
    k.add_argument("--hedef", type=Path, default=None,
                   help="flaresolverr çalıştırılabilirinin doğrudan içinde olacağı klasör")
    k.add_argument("--arsiv", type=Path, default=None,
                   help="önceden indirilmiş varlık (yine doğrulanır)")
    alt.add_parser("durum", help="yerel kurulumun durumunu yaz")
    secenek = ayristirici.parse_args(argv)

    if secenek.komut == "durum":
        print(json.dumps(yonetici().durum_ozeti(), ensure_ascii=False, indent=2))
        return 0

    varlik = VARLIKLAR.get(secenek.platform or platform_anahtari() or "")
    if varlik is None:
        print(f"::error::{desteklenmeme_sebebi()}")
        return 1
    son = [-1]

    def ilerleme(inen: int, toplam: int) -> None:
        onda = inen * 10 // max(1, toplam)
        if onda != son[0]:
            son[0] = onda
            print(f"  {varlik.ad}: {_mb(inen)} / {_mb(toplam)}", flush=True)

    print(f"FlareSolverr {SURUM} ({varlik.platform}) ← {varlik.url}")
    print(f"  beklenen: {varlik.boyut} bayt, sha256 {varlik.sha256}")
    try:
        kurulum = kur(varlik.platform, hedef=secenek.hedef, ilerleme=ilerleme,
                      arsiv=secenek.arsiv)
    except Exception as exc:
        print(f"::error::FlareSolverr kurulamadı: {exc}")
        return 1
    print(f"  doğrulandı ve açıldı: {kurulum.dizin} (sürüm {kurulum.surum or '?'}, "
          f"{_mb(_dizin_boyutu(kurulum.dizin))} açık)")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["AD", "SURUM", "VARLIKLAR", "Varlik", "Kurulum", "KurulumHatasi",
           "IptalEdildi", "Yonetici", "yonetici", "kapat", "kur", "indir",
           "kurulumu_bul", "kurulum_dizini", "gunluk_yolu", "platform_anahtari",
           "desteklenmeme_sebebi", "eksik_bagimliliklar", "bos_port", "saglik",
           "cocuk_ortami", "kurulum_oner", "sihirbaz_bilgisi", "BEKCI_BETIGI",
           "BUDANACAKLAR", "VARSAYILAN_PORT", "main"]
