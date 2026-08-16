"""Güncelleme çekirdeği — arayüzsüz.

Mantık neden `gui/update_manager.py`'de bırakılmadı: o modül `customtkinter` ve
`tkinter.messagebox`'ı modül seviyesinde import ediyor. Qt tarafından onu import
etmek, Qt sürecine tüm Tk yığınını sokmak demekti. Üstelik Faz 9'da eski GUI
silinecek; çekirdeğin o silmeden sağ çıkması gerekiyor. Bu yüzden ağ/dosya/sürüm
işleri buraya taşındı, eski CTk yöneticisi de artık buradan besleniyor —
böylece iki arayüzde iki farklı "güncelleme mantığı" oluşmuyor.

Ayarları BU MODÜL OKUMAZ: indirme klasörü çağıran tarafından geçilir (Qt'de
`gui.qt.prefs.indirme_dizini`). Ayar okumanın tek yeri orası kalsın diye.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

from .utils import get_os

VERSION_URL = ("https://raw.githubusercontent.com/barkeser2002/"
               "turkanime-gui/main/docs/version.json")

ZAMAN_ASIMI = 10          # sürüm bilgisi (küçük JSON)
INDIRME_ZAMAN_ASIMI = 30  # paket indirme
PARCA = 8192


class IndirmeHatasi(Exception):
    """İndirme ya da SHA-256 doğrulaması başarısız."""


# ── Sürüm ───────────────────────────────────────────────────────────────────
def surum_parcala(surum: Any) -> Optional[Tuple[int, ...]]:
    """``"9.4.12.2"`` → ``(9, 4, 12, 2)``; çözülemezse ``None``.

    Parça başına yalnızca baştaki rakamlar okunur: "9.5.0-beta" gibi etiketli
    sürümler `int()` ile patlıyor ve karşılaştırma sessizce "güncel" diyordu.
    """
    metin = str(surum or "").strip().lstrip("vV")
    if not metin:
        return None
    parcalar: List[int] = []
    for ham in metin.split("."):
        rakam = ""
        for karakter in ham.strip():
            if not karakter.isdigit():
                break
            rakam += karakter
        if not rakam:
            return None
        parcalar.append(int(rakam))
    return tuple(parcalar) if parcalar else None


def yeni_mi(uzak: Any, yerel: Any) -> bool:
    """`uzak` sürüm `yerel`den yeni mi?

    Eşitlikte ve çözülemeyen sürümlerde `False`: bilinmeyen bir biçim yüzünden
    kullanıcıyı güncelleme diyaloğuyla rahatsız etmektense sessiz kalmak yeğdir.
    """
    a, b = surum_parcala(uzak), surum_parcala(yerel)
    if a is None or b is None:
        return False
    uzunluk = max(len(a), len(b))
    a += (0,) * (uzunluk - len(a))   # "9.5" ile "9.5.0" aynı sürüm
    b += (0,) * (uzunluk - len(b))
    return a > b


def mevcut_surum(varsayilan: Optional[str] = None) -> str:
    """Çalışan sürüm: `version.py`, geliştirme ortamında `pyproject.toml` ezer.

    Geliştirme kopyasında `pyproject.toml` gerçeği söyler (`version.py` sürüm
    yükseltmesinde geride kalabiliyor). Paketlenmiş uygulamada o dosya yok, bu
    yüzden `frozen` durumunda hiç aranmaz — ve `toml` opsiyonel bir geliştirme
    bağımlılığı olduğu için import da gövdenin içinde.
    """
    yedek = str(varsayilan or "")
    if not yedek:
        try:
            from ..version import __version__ as paket_surumu
            yedek = str(paket_surumu)
        except Exception:
            yedek = "0.0.0"
    if getattr(sys, "frozen", False):
        return yedek
    try:
        import toml
        paket = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        yol = os.path.join(os.path.dirname(paket), "pyproject.toml")
        with open(yol, "r", encoding="utf-8") as fp:
            veri = toml.load(fp)
        surum = str(veri["tool"]["poetry"]["version"] or "")
    except Exception:
        return yedek
    return surum or yedek


# ── Uzak sürüm bilgisi ──────────────────────────────────────────────────────
def surum_bilgisi_getir(url: str = VERSION_URL,
                        timeout: int = ZAMAN_ASIMI) -> Dict[str, Any]:
    """`version.json`'ı indir. Ağ/biçim hatasında `requests` istisnası yükselir."""
    yanit = requests.get(url, timeout=timeout)
    yanit.raise_for_status()
    veri = yanit.json()
    return veri if isinstance(veri, dict) else {}


def platform_paketi(version_data: Dict[str, Any],
                    isletim: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Bu platformun paket kaydı: ``{"url": ..., "checksum": ...}``.

    Anahtar `get_os()` ile aranıyor; eski kod `get_platform()` ("windows_x64")
    kullandığı için sözlükte HİÇBİR ZAMAN eşleşme bulamıyor ve her güncellemede
    "bu platform desteklenmiyor" diyordu.
    """
    kayit = ((version_data or {}).get("platforms") or {}).get(isletim or get_os())
    if not isinstance(kayit, dict) or not kayit.get("url"):
        return None
    return {"url": str(kayit["url"]), "checksum": str(kayit.get("checksum") or "")}


def guncelleme_var_mi(version_data: Dict[str, Any],
                      yerel: Optional[str] = None) -> bool:
    """Uzak kayıt yerelden yeni bir sürüm mü tarif ediyor?"""
    return yeni_mi((version_data or {}).get("version"), yerel or mevcut_surum())


# ── İndirme ─────────────────────────────────────────────────────────────────
def dosya_ozeti(yol: str) -> str:
    """Dosyanın SHA-256 özeti (dosyayı belleğe almadan)."""
    ozet = hashlib.sha256()
    with open(yol, "rb") as fp:
        for parca in iter(lambda: fp.read(PARCA), b""):
            ozet.update(parca)
    return ozet.hexdigest()


def _sil(yol: str) -> None:
    try:
        os.remove(yol)
    except OSError:
        pass


def indir_ve_dogrula(url: str, hedef_dizin: str, checksum: str = "",
                     ilerleme: Optional[Callable[[int, int], None]] = None) -> str:
    """Paketi indir, SHA-256 ile doğrula, dosya yolunu döndür.

    Özet uyuşmazsa dosya **silinir** ve `IndirmeHatasi` yükselir: bozuk (ya da
    araya girilmiş) bir kurulum dosyasını diskte bırakmak, kullanıcının onu
    yine de çalıştırması demek olurdu. `checksum` boşsa doğrulama yapılmaz —
    `version.json` bazı sürümlerde özet yayımlamıyor.
    """
    os.makedirs(hedef_dizin, exist_ok=True)
    dosya_adi = os.path.basename(urlsplit(url).path) or "turkanime-guncelleme"
    yol = os.path.join(hedef_dizin, dosya_adi)

    yanit = requests.get(url, stream=True, timeout=INDIRME_ZAMAN_ASIMI)
    yanit.raise_for_status()
    toplam = int(yanit.headers.get("content-length") or 0)
    inen = 0
    with open(yol, "wb") as fp:
        for parca in yanit.iter_content(chunk_size=PARCA):
            if not parca:
                continue
            fp.write(parca)
            inen += len(parca)
            if ilerleme is not None:
                ilerleme(inen, toplam)

    # Boş checksum "doğrulamayı atla" DEĞİL, "sürüm bilgisi eksik" demektir.
    # Eskiden `if checksum:` yazıyordu ve boş string doğrulama bloğuna hiç
    # girmiyordu — yayındaki `version.json` üç platformda da boş checksum
    # taşıdığı için güncelleme kanalında bütünlük doğrulaması fiilen sıfırdı.
    # Yayıncı tarafı da bunu üretebiliyor: artefakt eksikse özet "" yazılıyor.
    # Kullanıcıya doğrulanmamış ikili çalıştırtmaktansa durmak doğru.
    beklenen = str(checksum or "").strip()
    if not beklenen:
        _sil(yol)
        raise IndirmeHatasi(
            "Sürüm bilgisinde SHA-256 özeti yok; indirilen dosya "
            "doğrulanamadığı için silindi. Sürümü GitHub Releases "
            "sayfasından elle indirin.")
    if dosya_ozeti(yol).lower() != beklenen.lower():
        _sil(yol)
        raise IndirmeHatasi(
            "İndirilen dosyanın SHA-256 özeti uyuşmuyor; dosya silindi.")
    return yol


# ── Kurulum sonrası ─────────────────────────────────────────────────────────
def arsiv_mi(dosya_yolu: str) -> bool:
    """Artefakt doğrudan çalıştırılabilir değil, açılması gereken bir arşiv mi?

    GUI paketi her platformda `.zip` olarak yayımlanıyor (bkz. `release.yml`).
    Bunu bilmeyen talimat "yeni dosyayı eskisinin yerine kopyalayın" diyordu —
    kullanıcı bir zip'i exe'nin üstüne kopyalayamaz.
    """
    return os.path.splitext(str(dosya_yolu or ""))[1].lower() == ".zip"


def kurulum_talimati(dosya_yolu: str, isletim: Optional[str] = None) -> str:
    """Platforma ve ARTEFAKT BİÇİMİNE göre kurulum adımları — gerçek yolla.

    Eski metin dosyayı her zaman "Downloads/" altında gösteriyordu; kullanıcı
    indirme klasörünü değiştirdiyse orada olmayan bir dosya tarif ediliyordu.
    """
    isletim = isletim or get_os()
    dizin = os.path.dirname(dosya_yolu)
    if arsiv_mi(dosya_yolu):
        # v10.1.0'dan itibaren paket TEK DOSYA: zip'in içinde bir klasör değil,
        # tek bir çalıştırılabilir var (bkz. `turkanime-gui.spec`, `TEK_DOSYA`).
        #
        # Metin bu sürümde düzeltildi. Öncesinde "çıkardığınız KLASÖRÜ eskisinin
        # yerine koyun" diyordu; artık ortada öyle bir klasör yok, dolayısıyla
        # kullanıcı tarif edilen adımı hiç yapamazdı.
        #
        # Yükseltenler için ek bir ayrıntı var: 10.0.0 kurulumunun yanında
        # `_internal/` klasörü duruyor. Tek dosya onu okumaz, yani zararsızdır;
        # ama yarım gigabaytlık ölü ağırlıktır ve "hangisi güncel" sorusunu
        # doğurur. Silinebileceği açıkça söyleniyor.
        adimlar = ["1. Çalışan uygulamayı kapatın",
                   f"2. İndirilen arşiv: {dosya_yolu}",
                   "3. Arşivi çıkarın (sağ tık → Tümünü ayıkla) — "
                   "içinde tek bir uygulama dosyası var",
                   "4. Eski uygulama dosyasını yedekleyin",
                   "5. Yeni dosyayı eskisinin yerine koyun",
                   "6. Eski kurulumdan kalan \"_internal\" klasörü varsa "
                   "artık gereksizdir, silebilirsiniz",
                   "7. Uygulamayı yeniden başlatın"]
        if isletim in ("linux", "macos"):
            adimlar[2] = (f"3. Arşivi çıkarın: unzip {dosya_yolu} — "
                          "içinde tek bir uygulama dosyası var")
            adimlar.insert(6, "6. Çalıştırma izni verin: chmod +x turkanime-gui")
            # Ekleme sonrası numaralar kaydı; baştan numaralandır.
            adimlar = [f"{i}. {s.split('. ', 1)[1]}"
                       for i, s in enumerate(adimlar, 1)]
        return "\n".join(adimlar)
    if isletim == "windows":
        return ("1. Çalışan uygulamayı kapatın\n"
                f"2. İndirilen dosya: {dosya_yolu}\n"
                "3. Eski uygulama dosyasını yedekleyin\n"
                "4. Yeni dosyayı eskisinin yerine kopyalayın\n"
                "5. Uygulamayı yeniden başlatın")
    if isletim == "linux":
        return ("1. Çalışan uygulamayı kapatın\n"
                "2. Terminal açın\n"
                f"3. chmod +x {dosya_yolu}\n"
                f"4. {dosya_yolu} komutuyla çalıştırın")
    if isletim == "macos":
        return ("1. Çalışan uygulamayı kapatın\n"
                f"2. {dosya_yolu} dosyasını Applications klasörüne taşıyın\n"
                "3. Güvenlik ayarlarından uygulamaya izin verin")
    return (f"Dosya indirildi: {dosya_yolu}\n"
            f"Klasör: {dizin}\nPlatformunuz için elle kurulum gerekebilir.")


def konumu_ac(dizin: str, isletim: Optional[str] = None) -> bool:
    """İndirme klasörünü dosya yöneticisinde aç."""
    if not dizin or not os.path.isdir(dizin):
        return False
    isletim = isletim or get_os()
    try:
        if isletim == "windows":
            os.startfile(dizin)  # pylint: disable=no-member
        elif isletim == "linux":
            subprocess.run(["xdg-open", dizin], timeout=5,
                           stderr=subprocess.DEVNULL, check=False)
        elif isletim == "macos":
            subprocess.run(["open", dizin], timeout=5,
                           stderr=subprocess.DEVNULL, check=False)
        else:
            return False
    except Exception:
        return False
    return True


__all__ = ["VERSION_URL", "IndirmeHatasi", "surum_parcala", "yeni_mi",
           "mevcut_surum", "surum_bilgisi_getir", "platform_paketi",
           "guncelleme_var_mi", "dosya_ozeti", "indir_ve_dogrula",
           "arsiv_mi", "kurulum_talimati", "konumu_ac"]
