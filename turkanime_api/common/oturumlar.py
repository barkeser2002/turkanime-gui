"""Erişim oturumları: kullanıcının "Erişimi aç" penceresinde geçtiği bot doğrulamaları.

NEDEN VAR: Bazı kaynaklar bot korumasının arkasında (Cloudflare "Just a
moment…"/Turnstile, LiteSpeed bot doğrulaması, sitenin kendi JS kapısı).
Deokwave 2026-09-30'dan beri BÜTÜN yollarında Cloudflare'ın yönetilen
doğrulamasını gösteriyor. Uygulama bu doğrulamaları kendisi GEÇMİYOR:
geçmek için yazılmış kod onu atlatmak olurdu. Kullanıcı ise geçebilir.
"Erişimi aç" kaynağın sitesini uygulamanın içindeki gerçek bir tarayıcıda
(QtWebEngine, `gui/qt/erisim_penceresi.py`) açıyor, doğrulamayı KULLANICI
çözüyor; ortaya çıkan oturum (çerezler + o tarayıcının kimliği) burada
saklanıyor ve kaynağın istekleri artık o oturumla gidiyor. Kullanıcının
siteyi kendi tarayıcısında açıp gezmesiyle aynı şey.

Qt'siz ve yalnızca standart kütüphane: kaynak modülleri (sunucu imajında da
yükleniyorlar), `common.cf_bypass` ve CLI okuyor; yalnızca pencere yazıyor.

Dosya: ``<veri kökü>/oturumlar.json`` (`ayarlar.json`'da DEĞİL: her yazımda
ayar dosyasını yeniden yazmak, eşzamanlı ayar yazımlarıyla yarışırdı)::

    {"surum": 1, "kaynaklar": {"Deokwave": {
        "alanlar": ["deokwave.com"],
        "cerezler": [{"name", "value", "domain", "path", "expiry", "secure",
                      "httponly"}],
        "user_agent": "Mozilla/5.0 (…) QtWebEngine/6.11.2 Chrome/140.0.0.0 …",
        "basliklar": {"sec-ch-ua": …, "sec-ch-ua-mobile": "?0",
                      "sec-ch-ua-platform": "\\"Linux\\""},
        "kaydedildi": 1790752299}}}

KULLANICI AJANI ŞART: Cloudflare `cf_clearance` çerezini doğrulamayı geçen
tarayıcının User-Agent'ına (ve IP'sine) bağlıyor; çerez başka bir UA ile
gönderilince doğrulama yeniden çıkıyor. Bu yüzden istekler çerezle birlikte
gömülü tarayıcının TAM UA'sını ve istemci ipuçlarını (sec-ch-ua*) taşıyor;
curl_cffi'nin taklit profili de UA'daki Chrome sürümüne en yakın profile
çekiliyor (`taklit_hedefi`): TLS parmak izi, başlıklar ve UA aynı tarayıcıyı
anlatsın.

Süresi dolan çerezler OKURKEN atılıyor (dosyaya ancak bir sonraki yazımda
yansıyor: okuma işçi thread'lerinde, her HTTP isteğinde oluyor).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

DOSYA_ADI = "oturumlar.json"
SURUM = 1

# Kaynağın profil klasörü `<veri kökü>/erisim_profilleri/<modül>` (pencere).
PROFIL_KLASORU = "erisim_profilleri"

# Bir konakta görülen doğrulama sayfası bu kadar süre "hâlâ istiyor" sayılır
# (arayüzün "Erişimi aç" düğmesi için; bkz. `dogrulama_bekliyor`).
DOGRULAMA_OMRU = 15 * 60

# Doğrulama sayfasının izleri (küçük harf). Yalnızca doğrulama SAYFASINA
# özgü olanlar: Cloudflare her HTML yanıta `/cdn-cgi/challenge-platform/
# scripts/jsd/…` betiğini ekliyor (normal sayfada da var); doğrulama
# sayfasının ise `_cf_chl_opt` nesnesi ve `/challenge-platform/h/…/orchestrate`
# betiği var (deokwave.com, 2026-09-30). "cf-chl-widget" BİLEREK yok: sayfaya
# gömülü her Turnstile kutusunun gizli alanı o adı taşıyor (nowsecure.nl'nin
# doğrulama SONRASI sayfasında ölçüldü); pencere geçilmiş sayfayı doğrulama
# sanıp hiç kapanmıyordu.
DOGRULAMA_IZLERI = (
    "_cf_chl_opt",                          # Cloudflare doğrulama sayfası
    "/cdn-cgi/challenge-platform/h/",       # onun yönetici betiği
    "<title>just a moment...</title>",
    "checking your browser",                # eski IUAM / DDoS-Guard
    "lsrecaptcha",                          # LiteSpeed bot doğrulaması
    "<title>bot verification</title>",
    "__waf_challenge",                      # Tranimaci'nin JS kapısı
)

# Kullanıcıya yazılmış hata cümlelerinde doğrulamayı anlatan ifadeler
# (küçük harf). `common.hatalar` Cloudflare engelini "site bot doğrulaması
# istiyor" diye anlatıyor; TRAnimeİzle "bot kontrolüne takıldı" diyor.
_METIN_IZLERI = (
    "just a moment", "cf-chl", "cf_chl", "bot doğrulama", "bot kontrol",
    "bot verification", "lsrecaptcha", "__waf_challenge",
    "checking your browser", "enable javascript and cookies",
)

# Doğrulamanın geldiği durum kodları: Cloudflare yönetilen doğrulaması 403,
# eski "Under Attack" 503, Tranimaci'nin JS kapısı 202.
_DOGRULAMA_DURUMLARI = frozenset({202, 403, 429, 503})

# Pencereye gerek olmayan kaynaklar: arşiv GitLab'daki statik dosyalar.
_ERISIMSIZ = frozenset({"TürkAnime"})

# Sürüm bilinmiyorsa taklit edilecek Chrome'lar (curl_cffi'den okunamazsa).
_YEDEK_HEDEFLER = (99, 100, 101, 104, 107, 110, 116, 119, 120, 123, 124, 131,
                   136, 142)

_kilit = threading.RLock()
_onbellek: Dict[str, Any] = {"yol": None, "damga": None, "veri": None}
# Konak → (doğrulama mı, time.monotonic()). Yalnızca süreç içi.
_konak_durumu: Dict[str, Tuple[bool, float]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Dosya
# ─────────────────────────────────────────────────────────────────────────────
def dosya_yolu() -> Optional[Path]:
    """``<veri kökü>/oturumlar.json``; veri kökü çözülemezse None.

    Tembel import: `cli.dosyalar` hafif ama bu modül sunucu imajında da
    yükleniyor; orada kök yoksa oturum da yok, istekler olduğu gibi gider.
    """
    try:
        from ..cli.dosyalar import veri_koku
        return Path(veri_koku()) / DOSYA_ADI
    except Exception:
        return None


def _bos() -> Dict[str, Any]:
    return {"surum": SURUM, "kaynaklar": {}}


def _ham_oku(yol: Path) -> Dict[str, Any]:
    """Dosyanın kendisi (süresi dolanlar dahil); yoksa/bozuksa boş yapı.

    Önbellek dosyanın (mtime, boyut) damgasına bağlı: HTTP katmanı her
    istekte okuyor, ayrı süreçteki CLI de pencerenin yazdığını görmeli.
    """
    try:
        bilgi = yol.stat()
        damga = (bilgi.st_mtime_ns, bilgi.st_size, bilgi.st_ino)
    except OSError:
        return _bos()
    with _kilit:
        if _onbellek["yol"] == str(yol) and _onbellek["damga"] == damga:
            return _onbellek["veri"]
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        veri = None
    if not isinstance(veri, dict) or not isinstance(veri.get("kaynaklar"), dict):
        veri = _bos()
    with _kilit:
        _onbellek.update(yol=str(yol), damga=damga, veri=veri)
    return veri


def _yaz(yol: Path, veri: Dict[str, Any]) -> None:
    from ..cli.dosyalar import atomik_json_yaz
    atomik_json_yaz(str(yol), veri)
    with _kilit:
        _onbellek.update(yol=None, damga=None, veri=None)


def _gecerli_cerezler(cerezler: Any, simdi: float) -> List[Dict[str, Any]]:
    """Adı olan ve süresi dolmamış çerezler (``expiry`` 0 = oturum çerezi)."""
    out = []
    for cerez in cerezler if isinstance(cerezler, list) else []:
        if not isinstance(cerez, dict) or not cerez.get("name"):
            continue
        try:
            bitis = int(cerez.get("expiry") or 0)
        except (TypeError, ValueError):
            bitis = 0
        if bitis and bitis <= simdi:
            continue
        out.append(cerez)
    return out


def _temiz_kayit(kayit: Any, simdi: float) -> Optional[Dict[str, Any]]:
    """Kaydın süresi dolmamış çerezlerle kopyası; geçerli çerez yoksa None."""
    if not isinstance(kayit, dict):
        return None
    cerezler = _gecerli_cerezler(kayit.get("cerezler"), simdi)
    if not cerezler:
        return None
    return dict(kayit, cerezler=cerezler)


def kayitlar() -> Dict[str, Dict[str, Any]]:
    """Geçerli oturumlar: kaynak → kayıt (süresi dolan çerezler atılmış).

    Bütün çerezleri dolmuş kaynak hiç dönmez: o oturumla gidecek istek
    doğrulamaya yeniden takılır, "kayıtlı" göstermek yanıltır.
    """
    yol = dosya_yolu()
    if yol is None:
        return {}
    simdi = time.time()
    out = {}
    for ad, kayit in (_ham_oku(yol).get("kaynaklar") or {}).items():
        temiz = _temiz_kayit(kayit, simdi)
        if temiz is not None:
            out[str(ad)] = temiz
    return out


def kayit(kaynak: str) -> Optional[Dict[str, Any]]:
    """Kaynağın geçerli oturumu (kanonik adla ya da takma adla); yoksa None."""
    return kayitlar().get(_kanonik(kaynak))


def kaydet(kaynak: str, *, cerezler: Iterable[Dict[str, Any]], user_agent: str,
           alanlar: Iterable[str] = (), basliklar: Optional[Dict[str, str]] = None,
           zaman: Optional[float] = None) -> Dict[str, Any]:
    """Kaynağın oturumunu yaz (öncekinin yerine); yazılan kaydı döndür.

    Yazarken bütün dosya temizleniyor: süresi dolan çerezler ve çerezi
    kalmamış kayıtlar gidiyor (okuma bunu yapmıyor, bkz. modül belgesi).
    """
    yol = dosya_yolu()
    if yol is None:
        raise OSError("veri klasörü bulunamadı; erişim oturumu kaydedilemedi")
    simdi = time.time() if zaman is None else float(zaman)
    yeni = {
        "alanlar": sorted({alan_koku(a) for a in alanlar if alan_koku(a)}),
        "cerezler": [_cerez_normalle(c) for c in cerezler if isinstance(c, dict)
                     and c.get("name")],
        "user_agent": str(user_agent or ""),
        "basliklar": {str(k): str(v) for k, v in (basliklar or {}).items() if v},
        "kaydedildi": int(simdi),
    }
    with _kilit:
        veri = _ham_oku(yol)
        kaynaklar = {}
        for ad, eski in (veri.get("kaynaklar") or {}).items():
            temiz = _temiz_kayit(eski, simdi)
            if temiz is not None:
                kaynaklar[ad] = temiz
        kaynaklar[_kanonik(kaynak)] = yeni
        _yaz(yol, {"surum": SURUM, "kaynaklar": kaynaklar})
    return yeni


def sil(kaynak: str) -> bool:
    """Kaynağın oturumunu sil; silinecek bir şey vardıysa True."""
    yol = dosya_yolu()
    if yol is None:
        return False
    ad = _kanonik(kaynak)
    with _kilit:
        veri = _ham_oku(yol)
        kaynaklar = dict(veri.get("kaynaklar") or {})
        if ad not in kaynaklar:
            return False
        kaynaklar.pop(ad)
        _yaz(yol, {"surum": SURUM, "kaynaklar": kaynaklar})
    for alan in erisim_alanlari(ad):
        _konak_durumu_temizle(alan)
    return True


def _cerez_normalle(cerez: Dict[str, Any]) -> Dict[str, Any]:
    try:
        bitis = max(0, int(cerez.get("expiry") or 0))
    except (TypeError, ValueError):
        bitis = 0
    return {
        "name": str(cerez.get("name") or ""),
        "value": str(cerez.get("value") or ""),
        "domain": str(cerez.get("domain") or ""),
        "path": str(cerez.get("path") or "/") or "/",
        "expiry": bitis,
        "secure": bool(cerez.get("secure")),
        "httponly": bool(cerez.get("httponly")),
    }


def _kanonik(ad: str) -> str:
    try:
        from ..sources import kayit as kaynak_kaydi
        return kaynak_kaydi.kanonik_ad(str(ad))
    except Exception:
        return str(ad)


# ─────────────────────────────────────────────────────────────────────────────
# Çerez eşleştirme (RFC 6265'in alan/yol/secure kuralları)
# ─────────────────────────────────────────────────────────────────────────────
def alan_koku(alan: str) -> str:
    """"www.deokwave.com" / ".deokwave.com" / adres → "deokwave.com"."""
    alan = str(alan or "").strip().lower()
    if "://" in alan:
        alan = urlsplit(alan).hostname or ""
    alan = alan.lstrip(".")
    return alan[4:] if alan.startswith("www.") else alan


def konak_alanda_mi(konak: str, alan: str) -> bool:
    """Konak alanın kendisi ya da alt alanı mı? ("sw2.deokwave.com" ∈ "deokwave.com")."""
    konak, alan = str(konak or "").lower().rstrip("."), alan_koku(alan)
    return bool(alan) and (konak == alan or konak.endswith("." + alan))


def _cerez_uyar(cerez: Dict[str, Any], konak: str, yol: str, guvenli: bool) -> bool:
    alan = str(cerez.get("domain") or "").lower()
    if not alan:
        return False
    if alan.startswith("."):
        tamam = konak == alan[1:] or konak.endswith(alan)
    else:
        tamam = konak == alan          # yalnızca-konak çerezi
    if not tamam:
        return False
    if cerez.get("secure") and not guvenli:
        return False
    cerez_yolu = str(cerez.get("path") or "/")
    return (yol == cerez_yolu or yol.startswith(cerez_yolu.rstrip("/") + "/")
            or cerez_yolu == "/")


def _adrese_ait(url: str) -> Tuple[Dict[str, str], Optional[Dict[str, Any]]]:
    """(adrese gidecek çerezler, onları veren EN YENİ kayıt)."""
    try:
        parca = urlsplit(str(url))
    except ValueError:
        return {}, None
    konak = (parca.hostname or "").lower()
    if not konak or parca.scheme not in ("http", "https"):
        return {}, None
    yol, guvenli = parca.path or "/", parca.scheme == "https"
    cerezler: Dict[str, str] = {}
    secilen: Optional[Dict[str, Any]] = None
    # Eskiden yeniye: aynı adlı çerezde yeni kayıt kazanır.
    for kayit_ in sorted(kayitlar().values(), key=lambda k: k.get("kaydedildi") or 0):
        uyanlar = [c for c in kayit_["cerezler"] if _cerez_uyar(c, konak, yol, guvenli)]
        if not uyanlar:
            continue
        for cerez in uyanlar:
            cerezler[str(cerez["name"])] = str(cerez.get("value") or "")
        secilen = kayit_
    return cerezler, secilen


def istek_ekleri(url: str) -> Tuple[Dict[str, str], Optional[str]]:
    """``(çerezler, user_agent)``: adrese kayıtlı oturum; yoksa ``({}, None)``."""
    cerezler, kayit_ = _adrese_ait(url)
    if not cerezler or kayit_ is None:
        return {}, None
    return cerezler, (kayit_.get("user_agent") or None)


def istek_basliklari(url: str) -> Dict[str, str]:
    """Adrese kayıtlı oturumun tarayıcı başlıkları (UA + istemci ipuçları)."""
    cerezler, kayit_ = _adrese_ait(url)
    if not cerezler or kayit_ is None:
        return {}
    return _kayit_basliklari(kayit_)


def _kayit_basliklari(kayit_: Dict[str, Any]) -> Dict[str, str]:
    ua = str(kayit_.get("user_agent") or "")
    if not ua:
        return {}
    ipuclari = kayit_.get("basliklar") if isinstance(kayit_.get("basliklar"), dict) else {}
    basliklar = dict(istemci_ipuclari(ua))
    basliklar.update({str(k): str(v) for k, v in ipuclari.items() if v})
    basliklar["User-Agent"] = ua
    return basliklar


def istege_ekle(url: str, kwargs: Dict[str, Any], *, curl: bool = True) -> bool:
    """Kayıtlı oturum varsa isteğin argümanlarına ekle; eklendiyse True.

    ``kwargs`` bir `Session.get/post/request` çağrısının anahtar kelimeleri:
    ``headers`` (UA ve istemci ipuçları ÜZERİNE yazılır — büyük/küçük harf
    farkı gözetmeden; kaynağın kendi UA'sı çerezi geçersiz kılardı),
    ``cookies`` (kaynağın kendi verdiği aynı adlı çerez kazanır) ve curl_cffi
    oturumunda ``impersonate`` (kaynak kendisi vermediyse).
    """
    cerezler, kayit_ = _adrese_ait(url)
    if not cerezler or kayit_ is None:
        return False
    ekler = _kayit_basliklari(kayit_)
    if ekler:
        eski = kwargs.get("headers")
        basliklar = {k: v for k, v in dict(eski or {}).items()
                     if str(k).lower() not in {a.lower() for a in ekler}}
        basliklar.update(ekler)
        kwargs["headers"] = basliklar
    verilen = kwargs.get("cookies")
    if verilen is None:
        kwargs["cookies"] = dict(cerezler)
    elif isinstance(verilen, dict):
        kwargs["cookies"] = {**cerezler, **verilen}
    # Çerez kavanozu (CookieJar) verildiyse ona dokunulmuyor: kaynağın kendi
    # çerezleri o; UA/ipuçları yine de eklendi.
    hedef = taklit_hedefi(str(kayit_.get("user_agent") or ""))
    if curl and hedef and not kwargs.get("impersonate"):
        kwargs["impersonate"] = hedef
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Tarayıcı kimliği
# ─────────────────────────────────────────────────────────────────────────────
_CHROME_RE = re.compile(r"(?:Chrome|Chromium|CriOS)/(\d{2,3})\.")


def _curl_hedefleri() -> Tuple[int, ...]:
    """curl_cffi'nin taklit edebildiği masaüstü Chrome sürümleri."""
    try:
        from curl_cffi.requests import BrowserType
        surumler = sorted({int(m.group(1)) for b in BrowserType
                           for m in [re.fullmatch(r"chrome(\d+)", str(b.value))] if m})
        return tuple(surumler) or _YEDEK_HEDEFLER
    except Exception:
        return _YEDEK_HEDEFLER


def taklit_hedefi(user_agent: str) -> Optional[str]:
    """UA'daki Chrome sürümüne uyan curl_cffi profili ("chrome136"); yoksa None.

    UA'nın sürümünü geçmeyen en yeni profil: taklit, gerçek tarayıcının
    bilmediği bir TLS özelliğini ilan etmesin. UA'dan daha eskisi yoksa en eski.
    """
    eslesme = _CHROME_RE.search(str(user_agent or ""))
    if not eslesme:
        return None
    surum = int(eslesme.group(1))
    hedefler = _curl_hedefleri()
    uygun = [s for s in hedefler if s <= surum]
    secilen = max(uygun) if uygun else min(hedefler)
    return f"chrome{secilen}"


_GREASE_KARAKTER = (" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_")
_GREASE_SURUM = ("8", "99", "24")


def istemci_ipuclari(user_agent: str) -> Dict[str, str]:
    """UA'dan Chromium'un varsayılan istemci ipuçları (sec-ch-ua*).

    Pencere bunları sayfanın kendisinden okuyor (`navigator.userAgentData`);
    bu yalnızca yedek. curl_cffi'nin taklit profili kendi sec-ch-ua'sını
    (başka sürüm, macOS) ekliyor: UA "Linux" derken ipucunun "macOS" demesi
    tam da bot korumasının aradığı tutarsızlık. Karmaşık (GREASE) marka
    Chromium'un kuralıyla sürümden türetiliyor ("Not=A?Brand";v="24", 140).
    """
    ua = str(user_agent or "")
    eslesme = _CHROME_RE.search(ua)
    if not eslesme:
        return {}
    surum = int(eslesme.group(1))
    grease = (f"Not{_GREASE_KARAKTER[surum % 11]}A"
              f"{_GREASE_KARAKTER[(surum + 1) % 11]}Brand")
    markalar = f'"{grease}";v="{_GREASE_SURUM[surum % 3]}", "Chromium";v="{surum}"'
    if "Windows" in ua:
        platform = "Windows"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        platform = "macOS"
    elif "Android" in ua:
        platform = "Android"
    elif "CrOS" in ua:
        platform = "Chrome OS"
    elif "Linux" in ua:
        platform = "Linux"
    else:
        platform = "Unknown"
    return {"sec-ch-ua": markalar,
            "sec-ch-ua-mobile": "?1" if "Mobile" in ua else "?0",
            "sec-ch-ua-platform": f'"{platform}"'}


# ─────────────────────────────────────────────────────────────────────────────
# Doğrulama sayfası ve konak durumu
# ─────────────────────────────────────────────────────────────────────────────
def dogrulama_sayfasi_mi(html: str) -> bool:
    """HTML bir bot doğrulama sayfası mı (Cloudflare, LiteSpeed, WAF kapısı)?"""
    bas = str(html or "")[:30000].lower()
    return any(iz in bas for iz in DOGRULAMA_IZLERI)


def dogrulama_yaniti_mi(yanit: Any, *, govde: bool = True) -> bool:
    """HTTP yanıtı bir doğrulama mı? ``cf-mitigated: challenge`` ya da
    doğrulama durum kodunda doğrulama sayfası. ``govde=False``: yalnızca başlık
    (akış yanıtının gövdesi okunursa tüketilir)."""
    try:
        basliklar = getattr(yanit, "headers", None) or {}
        if str(basliklar.get("cf-mitigated") or "").strip().lower() == "challenge":
            return True
    except Exception:
        pass
    if not govde or getattr(yanit, "status_code", 200) not in _DOGRULAMA_DURUMLARI:
        return False
    try:
        return dogrulama_sayfasi_mi(getattr(yanit, "text", "") or "")
    except Exception:
        return False


def yanit_denetle(url: str, yanit: Any, *, govde: bool = True) -> Optional[bool]:
    """Yanıtı konağın durumuna işle: doğrulama → True, açık → False.

    Arayüz "Erişimi aç"ı buna bakarak gösteriyor (`dogrulama_bekliyor`):
    kaynak modüllerinin engel mesajları doğrulama ile düz 403'ü ayırmıyor,
    istemci katmanı ise sayfanın kendisini görüyor.
    """
    konak = (urlsplit(str(url)).hostname or "").lower()
    if not konak or yanit is None:
        return None
    if dogrulama_yaniti_mi(yanit, govde=govde):
        with _kilit:
            _konak_durumu[konak] = (True, time.monotonic())
        return True
    if int(getattr(yanit, "status_code", 0) or 0) < 400:
        with _kilit:
            if konak in _konak_durumu:
                _konak_durumu[konak] = (False, time.monotonic())
        return False
    return None


def _konak_durumu_temizle(alan: str) -> None:
    with _kilit:
        for konak in [k for k in _konak_durumu if konak_alanda_mi(k, alan)]:
            _konak_durumu.pop(konak, None)


def dogrulama_bekliyor(kaynak: str) -> bool:
    """Kaynağın bir konağında son `DOGRULAMA_OMRU` içinde doğrulama görüldü mü
    (ve ardından o konaktan düzgün yanıt gelmedi mi)?"""
    alanlar = erisim_alanlari(kaynak)
    if not alanlar:
        return False
    simdi = time.monotonic()
    with _kilit:
        durumlar = list(_konak_durumu.items())
    return any(dogrulama and simdi - zaman < DOGRULAMA_OMRU
               and any(konak_alanda_mi(konak, a) for a in alanlar)
               for konak, (dogrulama, zaman) in durumlar)


def dogrulama_gecildi(kaynak: str) -> None:
    """Pencere doğrulamayı geçti: kaynağın konakları artık "istiyor" değil."""
    for alan in erisim_alanlari(kaynak):
        _konak_durumu_temizle(alan)


# ─────────────────────────────────────────────────────────────────────────────
# Kaynak → erişim hedefi
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ErisimHedefi:
    """Bir kaynağın "Erişimi aç" penceresi: ne açılacak, ne yakalanacak."""

    kaynak: str                          # kanonik ad ("Deokwave")
    etiket: str                          # insana görünen ad
    adres: str                           # pencerede açılacak sayfa
    alanlar: Tuple[str, ...]             # çerezleri yakalanacak alanlar
    profil_adi: str                      # profil klasörünün adı (modül adı)
    # Bu çerezler görününce doğrulama geçilmiş sayılır (TRAnimeİzle:
    # ".AitrWeb.Session"); boşsa ölçüt sayfanın doğrulama olmaktan çıkması.
    gerekli_cerezler: FrozenSet[str] = frozenset()
    # TRAnimeİzle'nin eski çerez akışı (`gui/qt/cookie_browser`): oturum
    # `ayarlar.json`'a Netscape metni olarak yazılıyor, burada değil.
    cerez_akisi: bool = False


def erisim_hedefi(ad: str) -> Optional[ErisimHedefi]:
    """Kaynağın erişim hedefi; penceresi olmayan kaynakta None.

    Adres kaynak modülünün `BASE_URL`'si: alan adı ortam değişkeniyle
    taşınabilen kaynaklarda (AnimeTR, Asya Animeleri) modül özniteliği
    değişkeni zaten okumuş oluyor.
    """
    try:
        from ..sources import kayit as kaynak_kaydi
        kaynak = kaynak_kaydi.bul(ad)
    except Exception:
        return None
    if (kaynak is None or kaynak.yalnizca_metadata or not kaynak.modul
            or kaynak.ad in _ERISIMSIZ):
        return None
    try:
        import importlib
        modul = importlib.import_module(f"turkanime_api.sources.{kaynak.modul}")
    except Exception:
        return None
    adres = str(getattr(modul, "BASE_URL", "") or "").strip()
    if not adres.startswith(("http://", "https://")):
        return None
    if not urlsplit(adres).path:
        adres += "/"
    alan = alan_koku(adres)
    if not alan:
        return None
    return ErisimHedefi(
        kaynak=kaynak.ad, etiket=kaynak.etiket, adres=adres, alanlar=(alan,),
        profil_adi=re.sub(r"[^a-z0-9_-]", "_", kaynak.modul.lower()),
        gerekli_cerezler=(frozenset({".AitrWeb.Session"}) if kaynak.cerez_gerekir
                          else frozenset()),
        cerez_akisi=bool(kaynak.cerez_gerekir))


def erisim_alanlari(ad: str) -> Tuple[str, ...]:
    """Kaynağın alanları: hedefinki + kayıtlı oturumunkiler."""
    alanlar: List[str] = []
    hedef = erisim_hedefi(ad)
    if hedef is not None:
        alanlar.extend(hedef.alanlar)
    kayit_ = kayit(ad) if hedef is None or not hedef.cerez_akisi else None
    for alan in (kayit_ or {}).get("alanlar") or []:
        if alan not in alanlar:
            alanlar.append(str(alan))
    return tuple(alanlar)


# ─────────────────────────────────────────────────────────────────────────────
# Hata → "Erişimi aç"
# ─────────────────────────────────────────────────────────────────────────────
def _zincir(hata: BaseException) -> Iterable[BaseException]:
    gorulen = set()
    sira: List[Any] = [hata]
    while sira and len(gorulen) < 8:
        e = sira.pop(0)
        if e is None or id(e) in gorulen:
            continue
        gorulen.add(id(e))
        yield e
        sira.extend((e.__cause__, e.__context__))


def _metinde_dogrulama(metin: str) -> bool:
    kucuk = str(metin or "").casefold()
    return any(iz in kucuk for iz in _METIN_IZLERI)


def erisim_engeli_mi(hata: Any, kaynak: Optional[str] = None) -> bool:
    """Bu hata "Erişimi aç" ile çözülebilir bir bot doğrulaması mı?

    ``hata`` istisna ya da (arama sayfasının aldığı gibi) hata metni. Ölçüt:

    * `common.hatalar.BotDogrulamasi` (ve `DeokwaveDogrulamasi`, ortak CF
      zincirinin `CFBypassError`'ı);
    * metinde doğrulama izi ("bot doğrulaması", "Just a moment"…);
    * TRAnimeİzle gibi çerez isteyen kaynakta `OturumGerekli` (çerez o
      pencereden geliyor);
    * ``kaynak`` verildiyse: kaynağın bir konağında az önce doğrulama sayfası
      görülmüş (`dogrulama_bekliyor`) — kaynakların engel mesajları
      doğrulama ile düz 403'ü ayırmıyor.

    Penceresi olmayan kaynakta (arşiv, AniList) hep False: düğme işe yaramaz.
    """
    hedef = erisim_hedefi(kaynak) if kaynak else None
    if kaynak and hedef is None:
        return False
    if isinstance(hata, BaseException):
        for e in _zincir(hata):
            adlar = {c.__name__ for c in type(e).__mro__}
            if adlar & {"BotDogrulamasi", "DeokwaveDogrulamasi", "CFBypassError"}:
                return True
            if hedef is not None and hedef.cerez_akisi and "OturumGerekli" in adlar:
                return True
            yanit = getattr(e, "response", None)
            try:
                govde = str(getattr(yanit, "text", "") or "")[:6000]
            except Exception:
                govde = ""
            if _metinde_dogrulama(f"{e} {govde}"):
                return True
    elif hata is not None:
        metin = str(hata)
        if _metinde_dogrulama(metin):
            return True
        # Arama sayfası istisnayı değil metnini görüyor: çerez isteyen
        # kaynağın "çerez istiyor/gerekli" cümlesi `OturumGerekli`'nin metni.
        if hedef is not None and hedef.cerez_akisi and "çerez" in metin.casefold():
            return True
    return bool(kaynak) and dogrulama_bekliyor(kaynak)


def erisim_isaretle(hata: BaseException, kaynak: str) -> BaseException:
    """Doğrulama engeliyse istisnaya ``erisim_kaynagi`` yaz (köprü sayfaya iletir)."""
    try:
        if erisim_engeli_mi(hata, kaynak):
            hata.erisim_kaynagi = _kanonik(kaynak)  # type: ignore[attr-defined]
    except Exception:
        pass
    return hata


# ─────────────────────────────────────────────────────────────────────────────
# HTTP oturumu sarmalayıcı
# ─────────────────────────────────────────────────────────────────────────────
class OturumluIstemci:
    """Kaynağın HTTP oturumunu saran ince katman.

    Her istekte adresin alanına kayıtlı erişim oturumu eklenir (`istege_ekle`)
    ve yanıtın doğrulama sayfası olup olmadığı not edilir (`yanit_denetle`).
    Kayıt yoksa istek OLDUĞU GİBİ geçer. Diğer her öznitelik (``headers``,
    ``cookies``, ``close``…) alttaki oturumun kendisi.

    Kaynak modüllerinde tek satırlık değişiklik (oturum fabrikası) yetsin
    diye var: istekler onlarca yerden atılıyor (AnimPow'un iki arka ucu,
    SeiCode'un çözücüleri) ve hepsine ayrı ayrı dokunmak ayrışmaya davetti.
    """

    def __init__(self, ic: Any, *, curl: bool = True):
        self._ic = ic
        self._curl = curl

    def _gonder(self, yontem: str, url: str, args: tuple, kwargs: Dict[str, Any]) -> Any:
        istege_ekle(str(url), kwargs, curl=self._curl)
        yanit = getattr(self._ic, yontem)(url, *args, **kwargs)
        yanit_denetle(str(url), yanit, govde=not kwargs.get("stream"))
        return yanit

    def get(self, url, *args, **kwargs):
        return self._gonder("get", url, args, kwargs)

    def post(self, url, *args, **kwargs):
        return self._gonder("post", url, args, kwargs)

    def head(self, url, *args, **kwargs):
        return self._gonder("head", url, args, kwargs)

    def put(self, url, *args, **kwargs):
        return self._gonder("put", url, args, kwargs)

    def request(self, yontem, url, *args, **kwargs):
        istege_ekle(str(url), kwargs, curl=self._curl)
        yanit = self._ic.request(yontem, url, *args, **kwargs)
        yanit_denetle(str(url), yanit, govde=not kwargs.get("stream"))
        return yanit

    def __getattr__(self, ad: str) -> Any:
        if ad in ("_ic", "_curl"):          # kopyalama/pickle sırasında sonsuz döngü olmasın
            raise AttributeError(ad)
        return getattr(self._ic, ad)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        kapat = getattr(self._ic, "close", None)
        if kapat is not None:
            kapat()
        return False


def oturumlu(oturum: Any, *, curl: bool = True) -> Any:
    """`OturumluIstemci` ile sar (zaten sarılıysa aynısı)."""
    if isinstance(oturum, OturumluIstemci):
        return oturum
    return OturumluIstemci(oturum, curl=curl)


__all__ = [
    "DOSYA_ADI", "PROFIL_KLASORU", "DOGRULAMA_IZLERI", "ErisimHedefi",
    "dosya_yolu", "kayitlar", "kayit", "kaydet", "sil",
    "alan_koku", "konak_alanda_mi", "istek_ekleri", "istek_basliklari",
    "istege_ekle", "taklit_hedefi", "istemci_ipuclari",
    "dogrulama_sayfasi_mi", "dogrulama_yaniti_mi", "yanit_denetle",
    "dogrulama_bekliyor", "dogrulama_gecildi",
    "erisim_hedefi", "erisim_alanlari", "erisim_engeli_mi", "erisim_isaretle",
    "OturumluIstemci", "oturumlu",
]
