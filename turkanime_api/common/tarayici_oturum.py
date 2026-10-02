"""Gerçek tarayıcıyla (Chrome ailesi) kullanıcının çözdüğü erişim oturumu.

"Erişimi aç"ın gömülü QtWebEngine penceresine bir seçenek: makinedeki gerçek
bir Chrome ailesi tarayıcısını (Chrome, Chromium, Brave, Edge, ungoogled-
chromium) selenium + undetected-chromedriver ile GÖRÜNÜR açar. Bazı siteler
QtWebEngine'in parmak izini tanıyıp doğrulamayı hiç çözdürmüyor; kullanıcının
gerçek tarayıcısı çözdürüyor.

İLKE (değişmedi): doğrulamayı/girişi UYGULAMA GEÇMEZ. Pencere görünür açılır,
Cloudflare/Turnstile/giriş ne varsa KULLANICI kendi çözer; biz yalnızca ortaya
çıkan oturumu (çerezler + User-Agent + istemci ipuçları) okuyup `oturumlar`a
yazarız — kullanıcının siteyi kendi tarayıcısında açmasıyla aynı. Otomatik
tıklama ya da otomatik doğrulama çözme YOK; undetected-chromedriver yalnızca
gerçek tarayıcının kendi parmak izini koruyor (QtWebEngine'inkini değil).

selenium ve undetected-chromedriver İSTEĞE BAĞLI (`pip install
"turkanime-gui[tarayici]"`). Yoksa bu motor kapalı ve uygulama gömülü pencereye
düşüyor; paket onları ya da bir tarayıcıyı İÇERMİYOR (yer kazanmak için —
kullanıcının kendi tarayıcısı kullanılıyor). Bu modül Qt'siz: pencere/işçi
katmanı `gui/qt/tarayici_penceresi` onu bir thread'de koşturuyor.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import oturumlar

# Kullanıcı tarayıcısının yolunu elle vermek için (otomatik bulunamazsa).
TARAYICI_ORTAM = "TURKANIME_TARAYICI"

# Ayar anahtarı (cli.dosyalar varsayılanları): hangi erişim motoru.
#   "oto"    → gerçek tarayıcı hazırsa o, değilse gömülü (varsayılan)
#   "gomulu" → her zaman QtWebEngine penceresi
#   "chrome" → gerçek tarayıcı (hazır değilse çağıran gömülüye düşer)
AYAR_ANAHTARI = "erisim tarayici"
MOTOR_OTO, MOTOR_GOMULU, MOTOR_CHROME = "oto", "gomulu", "chrome"

# Kullanıcı doğrulamayı çözsün diye pencere bu kadar açık kalır (sn).
VARSAYILAN_ZAMAN_ASIMI = 300.0
ANKET_ARALIGI = 1.0
# Sayfa bu kadar ardışık ankette doğrulama DEĞİL ve kaynağın alanındaysa geçildi
# sayılır (tek ankette geçici bir ara sayfa yanıltmasın).
ARDISIK_TEMIZ = 2

# Chrome ailesi tarayıcı çalıştırılabilirleri (PATH'te aranır).
_ADAYLAR = (
    "chromium", "chromium-browser", "chrome", "google-chrome",
    "google-chrome-stable", "google-chrome-beta", "brave", "brave-browser",
    "microsoft-edge", "ungoogled-chromium",
)
# Platforma özgü iyi bilinen kurulum yolları.
_BILINEN_YOLLAR = {
    "win32": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ),
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Ungoogled Chromium.app/Contents/MacOS/Chromium",
    ),
    "linux": (
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
        "/usr/bin/brave-browser", "/usr/bin/microsoft-edge",
        "/snap/bin/chromium",
    ),
}


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _ayardan_yol() -> str:
    """Ayarlar'daki ("erisim tarayici yolu") elle verilen tarayıcı yolu.

    `common` katmanı `cli`ye bağımlı olmasın diye tembel ve geri sarılı import;
    ayar okunamazsa boş döner (ör. test paketi, ayar dosyası yok)."""
    try:
        from ..cli.dosyalar import Dosyalar
        return str((Dosyalar().ayarlar or {}).get("erisim tarayici yolu") or "").strip()
    except Exception:
        return ""


def tarayici_bul() -> Optional[str]:
    """Kullanıcının Chrome ailesi tarayıcısının yolu; bulunamazsa None.

    Sıra: ``TURKANIME_TARAYICI`` ortam değişkeni (elle), Ayarlar'daki elle yol,
    PATH'teki adaylar, platformun bilinen kurulum yolları. İndirme YOK:
    kullanıcının zaten kurulu tarayıcısı kullanılıyor.
    """
    elle = os.environ.get(TARAYICI_ORTAM, "").strip()
    if elle and os.path.isfile(elle):
        return elle
    ayar = _ayardan_yol()
    if ayar and os.path.isfile(ayar):
        return ayar
    for ad in _ADAYLAR:
        yol = shutil.which(ad)
        if yol:
            return yol
    for yol in _BILINEN_YOLLAR.get(_platform(), ()):
        if os.path.isfile(yol):
            return yol
    return None


def _uc_var() -> bool:
    """undetected-chromedriver (ve selenium) import edilebilir mi?"""
    try:
        import importlib.util as u
        return bool(u.find_spec("undetected_chromedriver") and u.find_spec("selenium"))
    except Exception:
        return False


def motor_hazir() -> Tuple[bool, str]:
    """``(hazır mı, değilse sebep)``: gerçek tarayıcı motoru kullanılabilir mi?"""
    if not _uc_var():
        return False, ('Gerçek tarayıcı motoru için undetected-chromedriver gerekli '
                       '(pip install "turkanime-gui[tarayici]").')
    if tarayici_bul() is None:
        return False, ("Makinede Chrome ailesi bir tarayıcı bulunamadı; kurun ya da "
                       f"yolunu {TARAYICI_ORTAM} ortam değişkenine yazın.")
    return True, ""


def erisim_motoru(ayarlar: Optional[Dict[str, Any]]) -> str:
    """Ayardan + hazırlıktan seçilen motor: ``"chrome"`` ya da ``"gomulu"``.

    ``"oto"`` (varsayılan) gerçek tarayıcı hazırsa onu, değilse gömülüyü seçer;
    böylece selenium kurulu olmayan normal pakette davranış bugünküyle aynı.
    """
    secim = str((ayarlar or {}).get(AYAR_ANAHTARI) or MOTOR_OTO).strip().lower()
    if secim == MOTOR_GOMULU:
        return MOTOR_GOMULU
    if secim == MOTOR_CHROME:
        return MOTOR_CHROME if motor_hazir()[0] else MOTOR_GOMULU
    return MOTOR_CHROME if motor_hazir()[0] else MOTOR_GOMULU


# ─────────────────────────────────────────────────────────────────────────────
# Sürücü (undetected-chromedriver)
# ─────────────────────────────────────────────────────────────────────────────
def _profil_dizini(hedef: "oturumlar.ErisimHedefi") -> str:
    """Kaynağa özel kalıcı kullanıcı-veri klasörü (gömülü pencereyle AYNI kök,
    ayrı alt klasör: selenium ve QtWebEngine aynı profili paylaşamaz)."""
    try:
        from ..cli.dosyalar import veri_koku
        kok = os.path.join(str(veri_koku()), oturumlar.PROFIL_KLASORU, "chrome")
    except Exception:
        kok = os.path.join(os.path.expanduser("~"), ".turkanime-tarayici")
    yol = os.path.join(kok, hedef.profil_adi or "ortak")
    os.makedirs(yol, exist_ok=True)
    return yol


def profili_sil(hedef: "oturumlar.ErisimHedefi") -> None:
    """Kaynağın gerçek-tarayıcı profilini (çerezler dahil) sil — "Temizle".

    Gömülü pencerenin profil sıfırlamasıyla aynı gerekçe: içinde bizim artık
    bilmediğimiz geçerli bir çerez kalırsa bir sonraki "Erişimi aç" doğrulama
    görmez, çerezi de yakalayamaz. Yoksa ya da silinemezse sessizce geçer.
    """
    try:
        yol = _profil_dizini(hedef)
    except Exception:
        return
    shutil.rmtree(yol, ignore_errors=True)


def _surucu_kur(hedef: "oturumlar.ErisimHedefi"):
    """Görünür bir undetected-chromedriver Chrome'u aç (kaynağa özel profille).

    Yalnızca gerçek sürücü yolunda (motor hazırsa) çağrılır; testler kendi sahte
    sürücülerini enjekte ediyor, bu fonksiyonu hiç çalıştırmıyor.
    """
    import undetected_chromedriver as uc    # isteğe bağlı bağımlılık

    secenekler = uc.ChromeOptions()
    secenekler.add_argument(f"--user-data-dir={_profil_dizini(hedef)}")
    secenekler.add_argument("--no-first-run")
    secenekler.add_argument("--no-default-browser-check")
    ikili = tarayici_bul()
    if ikili:
        secenekler.binary_location = ikili
    # headless YOK: kullanıcı doğrulamayı görünür pencerede kendi çözüyor.
    return uc.Chrome(options=secenekler)


# Yüksek-entropili istemci ipuçlarını (platform sürümü, tam sürüm listesi…)
# tarayıcının kendisinden okur; sec-ch-ua başlıkları bundan türüyor.
_IPUCU_JS = """
const cb = arguments[arguments.length - 1];
const u = navigator.userAgentData;
if (!u || !u.getHighEntropyValues) { cb(null); return; }
u.getHighEntropyValues(["platform","platformVersion","architecture","bitness",
  "model","uaFullVersion","fullVersionList","wow64"])
 .then(function (v) { v.brands = u.brands; v.mobile = u.mobile; cb(v); })
 .catch(function () { cb(null); });
"""


def _basliklari_kur(veri: Optional[Dict[str, Any]], user_agent: str) -> Dict[str, str]:
    """userAgentData'dan sec-ch-ua başlıkları; okunamazsa UA'dan türet."""
    if not isinstance(veri, dict):
        return dict(oturumlar.istemci_ipuclari(user_agent))
    markalar = veri.get("fullVersionList") or veri.get("brands") or []
    parcalar = [f'"{m.get("brand")}";v="{m.get("version")}"' for m in markalar
                if isinstance(m, dict) and m.get("brand")]
    basliklar: Dict[str, str] = {}
    if parcalar:
        basliklar["sec-ch-ua"] = ", ".join(parcalar)
    basliklar["sec-ch-ua-mobile"] = "?1" if veri.get("mobile") else "?0"
    if veri.get("platform"):
        basliklar["sec-ch-ua-platform"] = f'"{veri.get("platform")}"'
    # UA'dan türetilenlerle doldur (eksik kalan olursa).
    for k, v in oturumlar.istemci_ipuclari(user_agent).items():
        basliklar.setdefault(k, v)
    return basliklar


def _cerez_cevir(ham: Dict[str, Any]) -> Dict[str, Any]:
    """selenium çerezini `oturumlar` biçimine (``httpOnly`` → ``httponly``)."""
    return {
        "name": ham.get("name"), "value": ham.get("value"),
        "domain": ham.get("domain"), "path": ham.get("path") or "/",
        "expiry": ham.get("expiry") or ham.get("expires") or 0,
        "secure": bool(ham.get("secure")),
        "httponly": bool(ham.get("httpOnly") or ham.get("httponly")),
    }


def _gerekli_cerezler_var(cerezler: List[Dict[str, Any]],
                          gerekli: Any) -> bool:
    adlar = {str(c.get("name")) for c in cerezler}
    return bool(gerekli) and set(map(str, gerekli)).issubset(adlar)


def _konak(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def oturum_yakala(hedef: "oturumlar.ErisimHedefi", *,
                  surucu: Any = None,
                  surucu_fabrikasi: Optional[Callable[[Any], Any]] = None,
                  zaman_asimi: float = VARSAYILAN_ZAMAN_ASIMI,
                  anket: float = ANKET_ARALIGI,
                  iptal: Optional[Callable[[], bool]] = None,
                  uyu: Callable[[float], Any] = time.sleep,
                  simdi: Callable[[], float] = time.monotonic) -> Optional[Dict[str, Any]]:
    """Görünür tarayıcıda kullanıcı doğrulamayı çözene kadar bekle, oturumu oku.

    Dönüş ``{"cerezler", "user_agent", "basliklar"}`` — çağıran bunu
    `oturumlar.kaydet`e verir. İptal/zaman aşımı/pencere kapandı → None.

    Bitiş ölçütü (gömülü pencereyle aynı): gerekli çerezler görününce (varsa),
    ya da sayfa `ARDISIK_TEMIZ` ankette doğrulama DEĞİL ve kaynağın alanında.
    Otomatik hiçbir tık yok: yalnızca okuyup bekliyor.

    ``surucu``/``surucu_fabrikasi`` testler için enjekte edilebilir; verilmezse
    gerçek undetected-chromedriver açılır (yalnızca motor hazırsa çağırın).
    """
    sahip = surucu is None
    if surucu is None:
        yap = surucu_fabrikasi or _surucu_kur
        surucu = yap(hedef)
    temiz = 0
    baslangic = simdi()
    try:
        surucu.get(hedef.adres)
        while True:
            if iptal is not None and iptal():
                return None
            if simdi() - baslangic > zaman_asimi:
                return None
            try:
                html = surucu.page_source or ""
                mevcut = surucu.current_url or ""
            except Exception:
                # Kullanıcı pencereyi kapattı: sürücü artık cevap vermiyor.
                return None
            cerezler = [_cerez_cevir(c) for c in (_cerezleri_al(surucu) or [])]
            if hedef.gerekli_cerezler and _gerekli_cerezler_var(cerezler, hedef.gerekli_cerezler):
                return _topla(surucu, cerezler)
            dogrulama = oturumlar.dogrulama_sayfasi_mi(html)
            alanda = any(oturumlar.konak_alanda_mi(_konak(mevcut), a)
                         for a in hedef.alanlar)
            if not dogrulama and alanda and not hedef.gerekli_cerezler:
                temiz += 1
                if temiz >= ARDISIK_TEMIZ:
                    return _topla(surucu, cerezler)
            else:
                temiz = 0
            uyu(anket)
    finally:
        if sahip:
            _kapat(surucu)


def _cerezleri_al(surucu: Any) -> List[Dict[str, Any]]:
    try:
        return list(surucu.get_cookies() or [])
    except Exception:
        return []


def _topla(surucu: Any, cerezler: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        ua = str(surucu.execute_script("return navigator.userAgent") or "")
    except Exception:
        ua = ""
    veri = None
    try:
        veri = surucu.execute_async_script(_IPUCU_JS)
    except Exception:
        veri = None
    return {"cerezler": cerezler, "user_agent": ua,
            "basliklar": _basliklari_kur(veri, ua)}


def _kapat(surucu: Any) -> None:
    try:
        surucu.quit()
    except Exception:
        pass


__all__ = [
    "TARAYICI_ORTAM", "AYAR_ANAHTARI", "MOTOR_OTO", "MOTOR_GOMULU", "MOTOR_CHROME",
    "VARSAYILAN_ZAMAN_ASIMI", "ARDISIK_TEMIZ",
    "tarayici_bul", "motor_hazir", "erisim_motoru", "oturum_yakala", "profili_sil",
]
