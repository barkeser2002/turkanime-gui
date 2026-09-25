"""Asya Animeleri kaynağı — https://asyaanimeleri.top

NEDEN BU SİTE: turkanime.tv kapandı ve mevcut kaynakların hiçbiri Çin
animasyonunu (donghua) iyi kapsamıyor. Asya Animeleri Türkçe altyazılı
donghua ağırlıklı (Xian Ni, Perfect World, Battle Through the Heavens…),
yanında kısmi bir Japon anime listesi de var (One Piece yalnızca 1071+,
Naruto 10 bölüm).

Site WordPress + Themesia "animestream" teması; işaretleme aniyomi'nin
`animestream` şablonuyla aynı. Üç sayfa türü yetiyor, AJAX yok:

    Arama     GET /?s=<sorgu>            → <div class="listupd"> içinde kartlar
              GET /page/<n>/?s=<sorgu>   (sayfa başına 10 kart)
    Bölümler  GET /series/<slug>/        → <div class="eplister"><ul><li>…
              (tek sayfada hepsi, EN YENİ ÜSTTE)
    Akışlar   GET /<bolum_yolu>/         → <select class="mirror"><option
              value="<base64>">; her değer bir <iframe src=…> etiketi

Kimlikler:
    kaynak_id  /series/<slug>/ içindeki slug. Yüzde kodlu olabilir
               ("attack-on-titan%ef%bc%9arequiem"); OLDUĞU GİBİ tutulur,
               yeniden kodlanırsa sayfa 404 döner.
    bolum_id   bölüm sayfasının kök düzeyindeki yolu
               ("one-piece-1161-bolum-izle", "xian-ni-1-3-bolum"). Sitenin
               slug'ları düzensiz ("-izle", "-4k", "-turkce-altyazili",
               "-bolun-full-izle" yazım hatası…); bu yüzden numaradan ÜRETİLMEZ,
               sayfadaki bağlantıdan aynen alınır. Kendi başına yeterli: bölüm
               adresi `bolum_adresi(bolum_id)`.

Koruma: Cloudflare önde ama challenge yok (2026-09: ~200 istekte hep 200).
Sitenin eskiden bir nginx "testcookie" kapısı vardı (HTTP 202 + slowAES ile
AES-128-CBC çözülüp `__test` çerezi yazılıyor; aniyomi eklentisi hâlâ
çözüyor). Bugün kapalı ama geri gelirse tek istekte çözülsün diye küçük bir
çözücü burada: `cf_bypass.CFSession` 202'yi meşru yanıt saydığı için bu
denetim ortak zincire bırakılamıyor.
"""
from __future__ import annotations

import base64
import binascii
import html as _html
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

# Engel listeleri ortak: kendi kopyamızı tutarsak iki liste ayrışır ve
# "Just a moment" bir yerde engel, öbür yerde "sonuç yok" sayılır. `try`
# içinde, çünkü kaynak sunucu tarayıcısında da koşuyor: `cf_bypass`'ın Qt
# çözücü yolu PySide6 istiyor ve sunucu imajında PySide6 yok
# (tests/test_server_dagitim.py korumasız importları imaja girmesi gereken
# paket sayar). Yedek liste yalnızca `cf_bypass` hiç yüklenemezse devrede
# (anizle.py ve crawler/nezaket.py de aynı deseni kullanıyor).
try:
    from ..common.cf_bypass import CHALLENGE_MARKERS, ENGEL_DURUMLARI
except ImportError:  # pragma: no cover
    ENGEL_DURUMLARI = frozenset({403, 429, 503})
    CHALLENGE_MARKERS = ("Just a moment", "Checking your browser", "challenge-platform")
from ..common.oynatici_onceligi import oncelik_anahtari

log = logging.getLogger(__name__)

# Site alan adı değiştirdi (.com artık park edilmiş bir sayfa); yeni adrese
# taşınırsa sürüm beklemeden ortam değişkeniyle düzeltilebilsin.
ORTAM_ANAHTARI = "ASYAANIMELERI_URL"
BASE_URL = "https://asyaanimeleri.top"
BASE_URL = ((os.environ.get(ORTAM_ANAHTARI) or "").strip() or BASE_URL).rstrip("/")

HTTP_TIMEOUT = 20
# Arama en çok bu kadar sayfa gezer (sayfa başına 10 kart). "xian" gibi genel
# bir sorgu 10 sayfa tutuyor; 25 sn'lik toplam arama bütçesini tek kaynak
# yememeli.
AZAMI_ARAMA_SAYFASI = 3

# curl_cffi yoksa düz requests'e düşülüyor; o zaman tanıdık bir tarayıcı
# kimliği gönder. curl_cffi'de UA'yı impersonate ayarlıyor: elle ezmek TLS
# parmak izi ile başlığı birbirine düşürür.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class AsyaAnimeleriHatasi(RuntimeError):
    """Siteye ulaşılamadı, istek engellendi ya da sayfa beklenen biçimde değil.

    "Sonuç yok" ile "okunamadı" ayrı şeyler: arama motoru bu hatayı kaynağın
    adıyla `AramaSonuclari.hatalar`'a yazar, sunucu tarayıcısı kaynağı o tur
    için devre dışı bırakır. Boş liste dönseydik ikisi de "bu animede bölüm
    yok" sanırdı.
    """


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
# İş parçacığı başına bir oturum: arama motoru kaynakları paralel çağırıyor ve
# arayüz aynı anda bölüm listesi ile akış isteyebiliyor. curl_cffi oturumu
# eşzamanlı kullanıma karşı güvenli değil (tek curl tutamacı paylaşılıyor).
_yerel = threading.local()


def _yeni_oturum() -> Any:
    if _HAS_CURL:
        return _http.Session(impersonate="chrome131")
    oturum = _http.Session()
    oturum.headers.update({"User-Agent": _UA})
    return oturum


def _oturum_al() -> Any:
    oturum = getattr(_yerel, "oturum", None)
    if oturum is None:
        oturum = _yeni_oturum()
        _yerel.oturum = oturum
    return oturum


_TESTCOOKIE_RE = re.compile(r'toNumbers\(\s*"([0-9a-fA-F]+)"\s*\)')


def _testcookie_kapisi_mi(yanit: Any) -> bool:
    return (getattr(yanit, "status_code", 0) == 202
            and "slowAES" in (getattr(yanit, "text", "") or ""))


def _testcookie_coz(govde: str) -> Optional[str]:
    """nginx testcookie sayfasındaki `__test` çerezinin değeri; çözülemezse None.

    Sayfa `a=toNumbers(anahtar), b=toNumbers(iv), c=toNumbers(şifreli)` verip
    `slowAES.decrypt(c, 2, a, b)` (mod 2 = CBC) sonucunu onaltılık yazıyor.
    pycryptodome TEMBEL yükleniyor: sunucu imajında yok ve kaynak modülü onu
    import anında sürüklememeli (tests/test_sunucu_bagimliliklari.py).
    """
    sayilar = _TESTCOOKIE_RE.findall(govde or "")
    if len(sayilar) < 3:
        return None
    try:
        from Crypto.Cipher import AES  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    try:
        anahtar, iv, sifreli = (bytes.fromhex(s) for s in sayilar[:3])
        return AES.new(anahtar, AES.MODE_CBC, iv).decrypt(sifreli).hex()
    except ValueError:            # tek sayıda hex / blok boyu tutmuyor
        return None


def _engellendi_mi(yanit: Any) -> bool:
    """Yanıt sitenin kendisi değil bir koruma/engel sayfası mı?"""
    if getattr(yanit, "status_code", 0) in ENGEL_DURUMLARI:
        return True
    bas = (getattr(yanit, "text", "") or "")[:6000]
    return any(iz in bas for iz in CHALLENGE_MARKERS)


def _cf_yedegi(url: str, basliklar: Dict[str, str]) -> Optional[Any]:
    """Cloudflare challenge çıkarsa ortak CF zincirini (cloudscraper →
    FlareSolverr → QtWebEngine) bir kez dene; o da geçemezse None.

    Bugün gerek yok ama site Cloudflare arkasında; "Under Attack" açılırsa
    kaynak tamamen düşmesin. Tembel import: zincir ağır (Qt alt süreci).
    """
    try:
        from ..common.cf_bypass import get_cf_session  # pylint: disable=import-outside-toplevel
        yanit = get_cf_session().get(url, headers=basliklar, timeout=HTTP_TIMEOUT)
    except Exception as exc:  # pylint: disable=broad-except
        log.info("Asya Animeleri CF yedeği başarısız: %s", exc)
        return None
    if yanit is None or _engellendi_mi(yanit) or _testcookie_kapisi_mi(yanit):
        return None
    return yanit


def _istek(url: str) -> Any:
    """GET; testcookie kapısını çözer, engel ve ağ hatasını AÇIK hatayla bildirir.

    Döndürülen yanıtın durum kodu 200 olmayabilir (404 gibi sitenin gerçek
    cevapları çağırana bırakılır: "bulunamadı" bağlama göre farklı söylenir).
    """
    # Tarayıcı iframe'i ve sayfaları sitenin kendi sayfasından açıyor; aynısını
    # göndermek sitenin "doğrudan erişim" kurallarına takılmamızı önler.
    basliklar = {"Referer": BASE_URL + "/"}
    oturum = _oturum_al()
    try:
        yanit = oturum.get(url, headers=basliklar, timeout=HTTP_TIMEOUT)
        if _testcookie_kapisi_mi(yanit):
            deger = _testcookie_coz(yanit.text)
            if deger is None:
                raise AsyaAnimeleriHatasi(
                    "Asya Animeleri koruma sayfası (testcookie) döndürdü ve çözülemedi "
                    "(pycryptodome kurulu değil ya da sayfa biçimi değişmiş).")
            konak = urlsplit(BASE_URL).hostname or ""
            oturum.cookies.set("__test", deger, domain=konak)
            yanit = oturum.get(url, headers=basliklar, timeout=HTTP_TIMEOUT)
    except AsyaAnimeleriHatasi:
        raise
    except Exception as exc:  # ağ katmanı: zaman aşımı, DNS, TLS, bağlantı
        raise AsyaAnimeleriHatasi(
            f"Asya Animeleri'ye ulaşılamadı ({url}): {exc}") from exc

    if _engellendi_mi(yanit) or _testcookie_kapisi_mi(yanit):
        yedek = _cf_yedegi(url, basliklar)
        if yedek is not None:
            return yedek
        raise AsyaAnimeleriHatasi(
            f"Asya Animeleri isteği engelledi (HTTP {getattr(yanit, 'status_code', '?')}, "
            "koruma sayfası). Bir süre sonra yeniden deneyin.")
    return yanit


def _metin(ham: str) -> str:
    """HTML parçasını düz metne indir: etiketleri at, varlıkları çöz, boşlukları topla."""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", ham or ""))).strip()


def _yol(kimlik: str, onek: str = "") -> str:
    """Kimliği site köküne göre yola çevir; tam adres verilmişse yolunu al.

    Kimlikler normalde çıplak yol ("one-piece-1161-bolum-izle"), ama elle ya da
    eski bir kayıttan tam adres gelirse de açılabilsin. Başka bir konağın
    adresi kabul EDİLMEZ: kimlik yalnızca bu sitenin sayfasını gösterebilir.
    """
    deger = (kimlik or "").strip()
    if re.match(r"^https?://", deger, re.I):
        parca = urlsplit(deger)
        site = (urlsplit(BASE_URL).hostname or "").lower()
        konak = (parca.hostname or "").lower()
        if konak not in (site, "www." + site):
            raise AsyaAnimeleriHatasi(
                f"'{kimlik}' bir Asya Animeleri adresi değil ({konak}).")
        deger = parca.path
    deger = deger.strip("/")
    if onek and deger.startswith(onek + "/"):
        deger = deger[len(onek) + 1:]
    if not deger:
        raise AsyaAnimeleriHatasi("Asya Animeleri kimliği boş.")
    return deger


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
_KART_RE = re.compile(r'<article\b[^>]*class="[^"]*\bbs\b[^"]*"[^>]*>(.*?)</article>', re.S)
_KART_BAGLANTI_RE = re.compile(
    r'<a\b[^>]*?\bhref="https?://[^/"]+/series/([^/"?#]+)/?"[^>]*>', re.S)
_TITLE_ATTR_RE = re.compile(r'\btitle="([^"]*)"')
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_IMG_RE = re.compile(r"<img\b([^>]*)>", re.S)
_IMG_SRC_RE = re.compile(r'\b(?:data-lazy-src|data-src|src)="([^"]+)"')


def _arama_govdesi(sayfa: str) -> str:
    """Yalnızca sonuç listesi. Kenar çubuğundaki "popüler seriler" bileşeni de
    /series/ bağlantıları taşıyor; sayfanın tamamı taranırsa sorguyla ilgisiz
    seriler sonuçlara sızar."""
    if 'class="listupd"' not in sayfa:
        raise AsyaAnimeleriHatasi(
            "Asya Animeleri arama sayfası beklenen biçimde değil (sonuç listesi yok); "
            "site düzeni değişmiş olabilir.")
    return sayfa.split('class="listupd"', 1)[1].split('<div id="sidebar"', 1)[0]


def _arama_ayristir(sayfa: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Arama sayfasından ([{"slug","title","image"}, ...], sonraki_sayfa_var_mi)."""
    govde = _arama_govdesi(sayfa)
    sonuc: List[Dict[str, Any]] = []
    gorulen = set()
    for kart in _KART_RE.findall(govde):
        m = _KART_BAGLANTI_RE.search(kart)
        if not m:
            continue
        slug = m.group(1)
        if slug in gorulen:
            continue
        baslik_m = _TITLE_ATTR_RE.search(m.group(0))
        baslik = _html.unescape(baslik_m.group(1)).strip() if baslik_m else ""
        if not baslik:
            h2 = _H2_RE.search(kart)
            baslik = _metin(h2.group(1)) if h2 else slug.replace("-", " ").title()
        gorsel = None
        img = _IMG_RE.search(kart)
        if img:
            src = _IMG_SRC_RE.search(img.group(1))
            if src and not src.group(1).startswith("data:"):
                gorsel = _html.unescape(src.group(1))
        gorulen.add(slug)
        sonuc.append({"slug": slug, "title": baslik, "image": gorsel})
    return sonuc, 'class="next page-numbers"' in govde


def zengin_ara(sorgu: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Kapak görselli arama: ``[{"slug", "title", "image"}, ...]``.

    Görsel aynı kartta geldiği için ek istek yok. Sonuç yoksa ``[]``; site
    okunamıyorsa `AsyaAnimeleriHatasi`.
    """
    q = (sorgu or "").strip()
    if not q or limit <= 0:
        return []
    kodlu = quote_plus(q)
    sonuc: List[Dict[str, Any]] = []
    gorulen = set()
    for sayfa_no in range(1, AZAMI_ARAMA_SAYFASI + 1):
        url = (f"{BASE_URL}/?s={kodlu}" if sayfa_no == 1
               else f"{BASE_URL}/page/{sayfa_no}/?s={kodlu}")
        yanit = _istek(url)
        if yanit.status_code == 404 and sayfa_no > 1:
            break                      # sayfalama beklenenden kısa: elde olanla dön
        if yanit.status_code != 200:
            raise AsyaAnimeleriHatasi(
                f"Asya Animeleri araması başarısız (HTTP {yanit.status_code}).")
        kayitlar, sonraki = _arama_ayristir(yanit.text)
        for kayit in kayitlar:
            if kayit["slug"] not in gorulen:
                gorulen.add(kayit["slug"])
                sonuc.append(kayit)
        if not sonraki or len(sonuc) >= limit:
            break
    return sonuc[:limit]


def search_asyaanimeleri(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """Asya Animeleri'nde ara → ``[(kaynak_id, başlık), ...]``; sonuç yoksa ``[]``."""
    return [(k["slug"], k["title"]) for k in zengin_ara(query, limit)]


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
_LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)
_BOLUM_BAGLANTI_RE = re.compile(r'<a\b[^>]*?\bhref="https?://[^/"]+/([^"?#]+?)/?"', re.S)
_EPL_NUM_RE = re.compile(r'<div class="epl-num">(.*?)</div>', re.S)
_EPL_TITLE_RE = re.compile(r'<div class="epl-title">(.*?)</div>', re.S)
_BASLIKTA_NUMARA_RE = re.compile(r"(\d+(?:\s*-\s*\d+)?)\s*\.?\s*B[öo]l[üu]m", re.I)
_NUMARA_RE = re.compile(r"^\d+(?:-\d+)?$")


def _bolum_numarasi(num: str, baslik: str) -> str:
    """`epl-num` ile başlıktaki numaradan doğru olanı seç.

    Sitede ikisinde de yazım hatası var ve hatalar hep DÜŞEN rakam: One Piece'te
    epl-num "146-1150" iken başlık "1146-1150.Bölüm"; epl-num "1125" iken
    başlık "125.Bölüm". Bu yüzden ilk sayısı daha çok basamaklı aday seçiliyor
    (eşitlikte epl-num). Sayısal aday yoksa ("Movie") ham epl-num döner.
    """
    num = re.sub(r"\s+", "", num or "")
    m = _BASLIKTA_NUMARA_RE.search(baslik or "")
    basliktan = re.sub(r"\s+", "", m.group(1)) if m else ""
    adaylar = [a for a in (num, basliktan) if _NUMARA_RE.match(a)]
    if not adaylar:
        return num
    return max(adaylar, key=lambda a: len(a.split("-")[0]))


def _ilk_sayi(numara: str) -> Optional[int]:
    m = re.match(r"(\d+)", numara or "")
    return int(m.group(1)) if m else None


def _bolumler_ayristir(sayfa: str) -> List[Tuple[str, str]]:
    """Seri sayfasından izleme sırasıyla ``[(bolum_id, başlık), ...]``."""
    if 'class="eplister"' not in sayfa:
        raise AsyaAnimeleriHatasi(
            "Asya Animeleri seri sayfasında bölüm listesi yok; site düzeni "
            "değişmiş olabilir.")
    govde = sayfa.split('class="eplister"', 1)[1]
    if "<ul" in govde:
        govde = govde.split("<ul", 1)[1]
    govde = govde.split("</ul>", 1)[0]

    satirlar: List[Tuple[str, str, Optional[int]]] = []
    gorulen = set()
    li_sayisi = 0
    for li in _LI_RE.findall(govde):
        li_sayisi += 1
        baglanti = _BOLUM_BAGLANTI_RE.search(li)
        if not baglanti:
            continue
        yol = baglanti.group(1).strip("/")
        if not yol or yol in gorulen or yol.startswith("series/"):
            continue
        num_m, baslik_m = _EPL_NUM_RE.search(li), _EPL_TITLE_RE.search(li)
        ham_baslik = _metin(baslik_m.group(1)) if baslik_m else ""
        numara = _bolum_numarasi(_metin(num_m.group(1)) if num_m else "", ham_baslik)
        if _NUMARA_RE.match(numara):
            etiket = f"{numara}. Bölüm"
        else:                                 # "Movie", "Special"…
            etiket = ham_baslik or numara or yol
        gorulen.add(yol)
        satirlar.append((yol, etiket, _ilk_sayi(numara)))

    if li_sayisi and not satirlar:
        raise AsyaAnimeleriHatasi(
            f"Asya Animeleri bölüm listesi ayrıştırılamadı ({li_sayisi} satır "
            "tanınmadı); site düzeni değişmiş olabilir.")

    # Site en yeniyi üstte veriyor; izleme sırası eskiden yeniye.
    satirlar.reverse()
    # Numaraya göre diz — AMA yalnızca numaralar tekilse. Aynı sayfada iki sezon
    # "1, 2, …" diye yeniden başlıyorsa numara sıralaması sezonları birbirine
    # geçirirdi; o durumda sitenin (ters çevrilmiş) sırası daha doğru. Sayısız
    # satırlar ("Movie") sona, kendi aralarında site sırasıyla (kararlı sıralama).
    sayilar = [s for _, _, s in satirlar if s is not None]
    if len(sayilar) == len(set(sayilar)):
        satirlar.sort(key=lambda s: (s[2] is None, s[2] or 0))
    return [(yol, etiket) for yol, etiket, _ in satirlar]


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Serinin bölümleri, izleme sırasıyla ``[(bolum_id, başlık), ...]``.

    Seride henüz bölüm yoksa ``[]``. Seri bulunamazsa, site okunamazsa ya da
    sayfa tanınmazsa `AsyaAnimeleriHatasi`.
    """
    seri = _yol(slug, onek="series")
    yanit = _istek(f"{BASE_URL}/series/{seri}/")
    if yanit.status_code == 404:
        raise AsyaAnimeleriHatasi(f"Asya Animeleri'de '{seri}' serisi bulunamadı (HTTP 404).")
    if yanit.status_code != 200:
        raise AsyaAnimeleriHatasi(
            f"Asya Animeleri '{seri}' serisinin sayfası açılamadı (HTTP {yanit.status_code}).")
    return _bolumler_ayristir(yanit.text)


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
# Yalnızca ayna menüsünün seçenekleri. Bugün bölüm sayfasındaki tek <select>
# bu, ama temanın filtre/sıralama formları da <option value=…> üretir; sayfanın
# tamamını taramak ileride o seçenekleri de "ayna" diye çözmeye kalkardı.
_AYNA_MENUSU_RE = re.compile(
    r'<select\b[^>]*\bclass="[^"]*\bmirror\b[^"]*"[^>]*>(.*?)</select>', re.S | re.I)
_SECENEK_RE = re.compile(r'<option\b([^>]*)>(.*?)</option>', re.S | re.I)
_DEGER_RE = re.compile(r'\bvalue\s*=\s*"([^"]*)"', re.I)
# Tırnaksız src de var: naruto-1-bolum'da `<iframe … src=https://video.sibnet.ru/…&share=1>`.
_IFRAME_SRC_RE = re.compile(r'<iframe\b[^>]*?\bsrc\s*=\s*["\']?([^"\'\s>]+)', re.I | re.S)
_SRC_RE = re.compile(r'\bsrc\s*=\s*["\']?([^"\'\s>]+)', re.I)
_PEMBED_RE = re.compile(
    r'id="pembed"[^>]*>\s*<iframe\b[^>]*?\bsrc\s*=\s*["\']?([^"\'\s>]+)', re.I | re.S)

# yt-dlp'nin oynatabildiği aynalar → ortak oynatıcı adı
# (`common/oynatici_onceligi.DESTEKLENEN_OYNATICILAR`). Liste bilinçli olarak
# bir İZİN listesi: `best_video` yalnızca ilk birkaç adayı yokluyor ve ölü bir
# aday saniyelere mal oluyor. 58 bölümlük canlı taramada (2026-09, yt-dlp
# 2026.08.19) DIŞARIDA bırakılanlar ve sebepleri:
#   asyaanimeleri.pw ("VİP", varsayılan iframe) → her istekte HTTP 522
#   asyaanim.upns.one ("VP")     → yt-dlp desteklemiyor, parçalar 522
#   vidmoly.to                   → alan adı park edilmiş (.biz/.net çalışıyor)
#   vk.com / vkvideo.ru          → yt-dlp çıkarıcısı bozuk (0/16), adres IP'ye kilitli
#   filemoon (yt-dlp "piracy" diye reddediyor), voe/dood/rumble (403),
#   puterin, gdplayer, gdriveplayer, short.icu/ink, abyssplayer, animtube,
#   videoplayer.vip, embedrise, playerwish → yt-dlp desteklemiyor
# ok.ru ve dailymotion tutuluyor: hatalar videoya özgüydü ("yazar engelli",
# "bulunamadı"), çıkarıcı çalışıyor; o aynası sağlam olan bölüm oynar.
_OYNATICILAR: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"(?:^|\.)video\.sibnet\.ru$"), "SIBNET"),
    (re.compile(r"(?:^|\.)vidmoly\.(?:biz|net|org|me)$"), "VIDMOLY"),
    (re.compile(r"(?:^|\.)my\.mail\.ru$"), "MAIL"),
    (re.compile(r"(?:^|\.)drive\.google\.com$"), "GDRIVE"),
    (re.compile(r"(?:^|\.)dailymotion\.com$"), "DAILYMOTION"),
    (re.compile(r"(?:^|\.)(?:ok|odnoklassniki)\.ru$"), "ODNOKLASSNIKI"),
)


def _adres_duzelt(ham: str) -> str:
    """iframe adresini oynatılabilir mutlak adrese çevir."""
    adres = _html.unescape((ham or "").strip()).replace("\\/", "/")
    if adres.startswith("//"):            # protokolsüz: //ok.ru/videoembed/…
        adres = "https:" + adres
    return adres


def _base64_coz(deger: str) -> Optional[str]:
    """Temanın `putMi`'si `innerHTML = atob(value)` yapıyor; aynısı."""
    temiz = re.sub(r"\s+", "", deger or "")
    if not temiz:
        return None
    try:
        ham = base64.b64decode(temiz + "=" * (-len(temiz) % 4))
    except (binascii.Error, ValueError):
        return None
    return ham.decode("utf-8", "replace")


def _aynalar(sayfa: str) -> List[Tuple[str, str]]:
    """Bölüm sayfasındaki bütün aynalar: ``[(etiket, adres), ...]`` sayfa sırasıyla.

    Liste boşsa ve sayfada hiç oynatıcı izi yoksa site düzeni değişmiş demektir.
    """
    aynalar: List[Tuple[str, str]] = []
    menuler = _AYNA_MENUSU_RE.findall(sayfa)
    for nitelikler, icerik in _SECENEK_RE.findall("".join(menuler)):
        deger_m = _DEGER_RE.search(nitelikler)
        deger = (deger_m.group(1) if deger_m else "").strip()
        if not deger:                     # "Player Seç" başlığı
            continue
        if re.match(r"^(?:https?:)?//", deger):
            adres = deger                 # bazı temalar çıplak adres koyuyor
        else:
            cozulen = _base64_coz(deger)
            if not cozulen:
                continue
            src = _IFRAME_SRC_RE.search(cozulen) or _SRC_RE.search(cozulen)
            if not src:
                continue
            adres = src.group(1)
        adres = _adres_duzelt(adres)
        if re.match(r"^https?://", adres):
            aynalar.append((_metin(icerik), adres))
    if not aynalar:
        m = _PEMBED_RE.search(sayfa)
        if m:
            aynalar.append(("", _adres_duzelt(m.group(1))))
    if not aynalar and not menuler and 'id="pembed"' not in sayfa:
        raise AsyaAnimeleriHatasi(
            "Asya Animeleri bölüm sayfasında oynatıcı bulunamadı; site düzeni "
            "değişmiş olabilir.")
    return aynalar


def _oynatici(adres: str) -> Optional[str]:
    konak = (urlsplit(adres).hostname or "").lower()
    for desen, ad in _OYNATICILAR:
        if desen.search(konak):
            return ad
    return None


def _akislar_ayristir(sayfa: str) -> List[Dict[str, str]]:
    """Bölüm sayfasından oynatılabilir akışlar, ortak oynatıcı önceliğiyle sıralı."""
    adaylar: List[Tuple[Tuple[int, int], int, Dict[str, str]]] = []
    gorulen = set()
    atilan: List[str] = []
    for sira, (etiket, adres) in enumerate(_aynalar(sayfa)):
        if adres in gorulen:
            continue
        gorulen.add(adres)
        oynatici = _oynatici(adres)
        if oynatici is None:
            atilan.append(urlsplit(adres).hostname or adres)
            continue
        adaylar.append((oncelik_anahtari(oynatici), sira, {
            "url": adres,
            "label": etiket or oynatici.title(),
            "player": oynatici,
            # Gömülü oynatıcı sayfası: yt-dlp/mpv çözüyor.
            "type": "iframe",
            # Tarayıcı iframe'i bu siteden açıyor; bazı gömülü oynatıcılar
            # (vidmoly) gömen sayfayı görmek istiyor. yt-dlp'nin biçim başına
            # başlıkları (sibnet/vidmoly CDN'i) bunu zaten ezer, bozan olmadı.
            "referer": BASE_URL + "/",
        }))
    if atilan:
        log.info("Asya Animeleri: oynatılamayan aynalar atlandı: %s", ", ".join(atilan))
    # Önce ortak oynatıcı önceliği (tek kaynak: common/oynatici_onceligi),
    # eşitlikte sayfadaki sıra.
    adaylar.sort(key=lambda a: (a[0], a[1]))
    return [akis for _, _, akis in adaylar]


def bolum_adresi(bolum_id: str) -> str:
    """Bölüm kimliğinden bölüm sayfasının adresi."""
    return f"{BASE_URL}/{_yol(bolum_id)}/"


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları.

    ``[{"url", "label", "player", "type": "iframe", "referer"}, ...]``; aynaların
    hepsi desteklenmeyen oynatıcılardaysa ``[]``. Bölüm bulunamazsa, site
    okunamazsa ya da sayfa tanınmazsa `AsyaAnimeleriHatasi`.
    """
    yol = _yol(episode_id)
    yanit = _istek(f"{BASE_URL}/{yol}/")
    if yanit.status_code == 404:
        raise AsyaAnimeleriHatasi(f"Asya Animeleri'de '{yol}' bölümü bulunamadı (HTTP 404).")
    if yanit.status_code != 200:
        raise AsyaAnimeleriHatasi(
            f"Asya Animeleri '{yol}' bölüm sayfası açılamadı (HTTP {yanit.status_code}).")
    return _akislar_ayristir(yanit.text)


__all__ = [
    "AsyaAnimeleriHatasi",
    "BASE_URL",
    "ORTAM_ANAHTARI",
    "bolum_adresi",
    "get_anime_episodes",
    "get_episode_streams",
    "search_asyaanimeleri",
    "zengin_ara",
]
