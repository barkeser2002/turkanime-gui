"""
BuguiTR kaynağı — https://buguitr.com

BU'GUI TR bir Türk çeviri grubunun WordPress blogu. Sitenin ağırlığı Asya BL
dizileri (canlı çekim, yüzlerce yazı); bunun yanında "ANİME" ve "DONGHUA"
kategorilerinde ~20 animasyon dizisi/filmi var, çoğu BL: Sasaki to Miyano
(+ Sotsugyou-hen), Cherry Magic! (2024 animesi), Tadaima Okaeri, Tasogare Out
Focus, Umibe no Étranger, Kore BL animasyonları (Mignon, No Love Zone,
Shutline, Hyperventilation, The Dangerous Convenience Store, Unbelievable
Space Love) ve donghua (Link Click, Heaven Official's Blessing, Can Ci Pin,
Lord of Mysteries, Mo Dao Zu Shi Q). Hepsi grubun kendi Türkçe çevirisi.

YALNIZCA animasyon kategorileri açılıyor: uygulama bir anime oynatıcısı,
arama sonuçları AniList'le eşleştiriliyor; canlı çekim diziler aramaya
karışırsa "Sasaki" gibi sorgularda alakasız dizi sonuçları görünür.

Giriş, çerez, captcha yok (2026-09-30'da ölçüldü). Tek kapı: barındırıcının
güvenlik duvarı tarayıcı olmayan User-Agent'a (python-requests, curl) 403
veriyor; curl_cffi'nin Chrome taklidi 200 alıyor.

Akış (WordPress REST API; HTML sayfası ~250-500 KB, REST yanıtı 2-10 KB):
- Arama:    GET /wp-json/wp/v2/posts?search=<q>&categories=676,1072,282
            → [{id, slug, title, categories}]. Kategori 676 "ANİME" ve 1072
            "DONGHUA" yalnızca seri tanıtım yazılarını, 282 "Anime" ise tanıtım
            + bölüm yazılarını taşıyor; bölüm yazıları başlıklarından ayıklanıyor.
- Bölümler: GET /wp-json/wp/v2/posts?slug=<seri>  → tanıtım yazısının içeriği
            bölüm yazılarına bağlantılar ("1", "I V", "01", "5,5", "1. & 2.
            BÖLÜM", "FİLMİ İZLE"...). Film/tek video tanıtımı videoyu kendisi
            gömüyor.
- Akışlar:  GET /wp-json/wp/v2/posts?slug=<bölüm> → içerikteki <iframe>'ler
            (ok.ru, sibnet, vidmoly, Google Drive, krakenfiles, videa…). Her
            iframe'in önünde "1. BÖLÜM | SIBNET" gibi bir başlık duruyor.

Kimlikler:
- Kaynak kimliği: seri tanıtım yazısının slug'ı ("tadaima-okaeri").
- Bölüm kimliği: bölüm yazısının slug'ı ("tadaima-okaeri-3-bolum"). Bazı
  yazılar İKİ bölümü birlikte taşıyor ("no-love-zone-1-2-bolum"); onlar
  "<slug>#<bölüm>" diye ikiye ayrılıyor, akışlar o bölümün başlığı altındaki
  iframe'lerden seçiliyor.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

try:
    # Engel izlerinin tek listesi istemcide (bkz. ANIME_PROVIDER_GUIDE.md,
    # "Engeli sessizce yutma"); oynatıcı önceliği de tek yerde.
    from ..common.cf_bypass import CHALLENGE_MARKERS as _CF_IZLERI
    from ..common.cf_bypass import ENGEL_DURUMLARI as _ENGEL_DURUMLARI
except Exception:  # pragma: no cover - sunucu tek başına da çalışabilmeli
    _CF_IZLERI = ("Just a moment", "cf-browser-verification", "challenge-platform")
    _ENGEL_DURUMLARI = frozenset({403, 429, 503})
from ..common.oynatici_onceligi import oncelik_anahtari

log = logging.getLogger(__name__)

BASE_URL = "https://buguitr.com"
API = BASE_URL + "/wp-json/wp/v2"
REFERER = BASE_URL + "/"
FANSUB = "BU'GUI TR"
HTTP_TIMEOUT = 15
IMPERSONATE = "chrome131"
_YEDEK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Barındırıcının güvenlik duvarı sayfası (tests/fixtures/buguitr/403-barindirici.html)
# ve CF izleri. Tek liste: iki liste ayrışırsa engel bir yerde "boş sonuç" olur.
_ENGEL_IZLERI = tuple(dict.fromkeys((
    "403 Forbidden", "Access to this resource on the server is denied", *_CF_IZLERI)))

# Küçük bir paylaşımlı WordPress barındırması; hız sınırı gözlenmedi ama
# nezaket gereği istekler arasında en az 1 sn bırakılıyor. Her uç tek istek
# attığı için (arama, bölüm listesi, akışlar) kullanıcı bunu hissetmiyor.
_MIN_INTERVAL = 1.0

# Kategori kimlikleri (slug'ları: anime-2 "ANİME", donghua-izle "DONGHUA",
# anime "Anime"). WordPress kimlikleri kalıcı; slug'la sorgulamak her aramaya
# ikinci bir istek eklerdi.
SERI_KATEGORILERI = frozenset({676, 1072})
ANIME_KATEGORILERI = (676, 1072, 282)

_SLUG_RE = re.compile(r"^[a-z0-9%_-]{1,200}$")
_BOLUM_ID_RE = re.compile(r"^([a-z0-9%_-]{1,200})(?:#(\d{1,4}))?$")
# İçerikteki site içi bağlantılar: tek parçalı yol = yazı. /category/, /tag/,
# /wp-content/ gibi çok parçalı yollar yazı değil.
_ICBAG_RE = re.compile(
    r'<a\b[^>]*?\bhref\s*=\s*["\']https?://(?:www\.)?buguitr\.com/([^/"\'#?]+)/?'
    r'(?:[?#][^"\']*)?["\'][^>]*>(.*?)</a>', re.I | re.S)
_IFRAME_RE = re.compile(r"<iframe\b([^>]*)>", re.I | re.S)
# Blok sınırları: bir iframe'in etiketi, önündeki son boş olmayan blok
# ("1. BÖLÜM | SIBNET", "Lord of Mysteries | OKRU"). Eski (klasik
# düzenleyici) yazılarda iframe <p>'nin İÇİNDE; bu yüzden "paragrafı bütün
# oku" değil, "iframe'den öncesini bloklara böl" yapılıyor.
_BLOK_RE = re.compile(
    r"<(?:/?(?:h[1-6]|p|div|figure|li|ul|ol|table|tr|td|center|blockquote|iframe)\b[^>]*"
    r"|hr\b[^>]*|br\b[^>]*)>", re.I)
# Tembel yükleme eklentileri gerçek adresi data-src'ye taşıyıp src'ye boş
# sayfa koyabiliyor; öncelik o yüzden bu sırada. (?<![\w-]) "data-src"in
# içindeki "src"yi ayrı nitelik sanmasın diye.
_SRC_ADLARI = ("data-lazy-src", "data-src", "src")
_ETIKET_RE = re.compile(r"<[^>]+>")

# yt-dlp'nin oynatabildiği gömülü oynatıcılar → ortak oynatıcı adı
# (`common/oynatici_onceligi`) ve kullanıcıya gösterilecek ad. Liste bilinçli
# bir İZİN listesi: `best_video` yalnızca ilk birkaç adayı yokluyor, ölü aday
# saniyelere mal oluyor. Animasyon kategorilerindeki bütün yazıların
# taramasında (2026-09-30, yt-dlp 2026.08.19) DIŞARIDA bırakılanlar:
#   mega.nz      → yt-dlp desteklemiyor (24 iframe)
#   vidmoly.to   → alan adı park edilmiş, yt-dlp "Unsupported URL"; aynı
#                  kimlik vidmoly.net'te 404 (32 iframe)
#   vk.com       → yt-dlp çıkarıcısı "Unable to extract player params"
#                  (Asya Animeleri'nde de 0/16)
# ok.ru listede kalıyor ama şu an oynamıyor: ok.ru sayfası `metadata`'yı
# JSON dizgesi yerine nesne olarak vermeye başladı, yt-dlp 2026.08.19'un
# çıkarıcısı her canlı videoda TypeError veriyor. Bütün kaynakları etkileyen
# bir yt-dlp sorunu; düzelince bu aynalar kendiliğinden çalışır.
# krakenfiles ve videa listede ama ortak öncelik tablosunda yok: bilinmeyen
# kuşağa, desteklenenlerin arkasına düşüyorlar.
_OYNATICILAR: Tuple[Tuple["re.Pattern[str]", str, str], ...] = (
    (re.compile(r"(?:^|\.)video\.sibnet\.ru$"), "SIBNET", "Sibnet"),
    (re.compile(r"(?:^|\.)(?:ok|odnoklassniki)\.ru$"), "ODNOKLASSNIKI", "Ok.ru"),
    (re.compile(r"(?:^|\.)vidmoly\.(?:net|biz|org|me)$"), "VIDMOLY", "Vidmoly"),
    (re.compile(r"(?:^|\.)drive\.google\.com$"), "GDRIVE", "Google Drive"),
    (re.compile(r"(?:^|\.)my\.mail\.ru$"), "MAIL", "Mail.ru"),
    (re.compile(r"(?:^|\.)dailymotion\.com$"), "DAILYMOTION", "Dailymotion"),
    (re.compile(r"(?:^|\.)krakenfiles\.com$"), "KRAKENFILES", "Krakenfiles"),
    (re.compile(r"(?:^|\.)videa\.hu$"), "VIDEA", "Videa"),
)
# Oynatıcı değil: fragman ya da süs. Tanıtım yazısında bunlar "video var"
# sayılmamalı, yoksa bölüm bağlantıları yerine tek bir "film" listelenir.
_VIDEO_SAYILMAYAN = re.compile(
    r"(?:^|\.)(?:youtube\.com|youtube-nocookie\.com|youtu\.be|twitter\.com|x\.com|"
    r"instagram\.com|disqus\.com|a-ads\.com|spotify\.com)$")

_kilit = threading.Lock()
_son_istek = 0.0
_oturum = None


class BuguiTRHatasi(RuntimeError):
    """BuguiTR'ye ulaşılamadı ya da yanıt beklenen biçimde değil.

    Mesaj kullanıcıya gösterilecek Türkçe cümle. ``status_code`` sunucu
    tarayıcısının hata sınıflandırması (`nezaket.hata_turu`) için: 403/429
    engellenme, 404 kalıcı, diğerleri geçici sayılıyor.
    """

    def __init__(self, mesaj: str, status_code: Optional[int] = None):
        super().__init__(mesaj)
        self.status_code = status_code


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
def _yeni_oturum():
    if _HAS_CURL:
        return _http.Session(impersonate=IMPERSONATE)
    oturum = _http.Session()
    # Düz requests'in kendi User-Agent'ı 403 alıyor; tarayıcı UA'sı şart.
    oturum.headers["User-Agent"] = _YEDEK_UA
    return oturum


def _oturum_al():
    global _oturum
    if _oturum is None:
        _oturum = _yeni_oturum()
    return _oturum


def _engellendi_mi(yanit) -> bool:
    """Yanıt içerik değil, barındırıcının/CF'in engel sayfası mı?

    Barındırıcının 403'ü HTML bir "403 Forbidden" sayfası; REST uçları hata
    olarak JSON döndürür (``{"code": "rest_..."}``). Yalnızca engel durum
    kodlarında (403/429/503) HTML gövdeye bakılır.
    """
    if yanit.status_code not in _ENGEL_DURUMLARI:
        return False
    if yanit.status_code == 429:
        return True
    bas = (yanit.text or "")[:6000]
    if bas.lstrip().startswith("{"):
        return False          # WordPress'in kendi JSON hatası (ör. rest_forbidden)
    return any(iz in bas for iz in _ENGEL_IZLERI)


def _get_json(yol: str, params: Dict[str, Any]) -> Any:
    """REST ucu çağrısı; engel, ağ hatası, 200 dışı yanıt ya da bozuk JSON
    `BuguiTRHatasi` olarak yükselir."""
    global _son_istek
    with _kilit:
        bekle = _MIN_INTERVAL - (time.monotonic() - _son_istek)
        if bekle > 0:
            time.sleep(bekle)
        _son_istek = time.monotonic()
        oturum = _oturum_al()
    try:
        yanit = oturum.get(API + yol, params=params, timeout=HTTP_TIMEOUT, headers={
            "Accept": "application/json", "Referer": REFERER})
    except Exception as hata:
        raise BuguiTRHatasi(f"BuguiTR'ye bağlanılamadı: {hata}") from hata
    if _engellendi_mi(yanit):
        raise BuguiTRHatasi(
            f"BuguiTR isteği engelledi (HTTP {yanit.status_code}); biraz sonra "
            "yeniden deneyin.", status_code=yanit.status_code)
    if yanit.status_code != 200:
        raise BuguiTRHatasi(f"BuguiTR {yol} ucu HTTP {yanit.status_code} döndü.",
                            status_code=yanit.status_code)
    try:
        return json.loads(yanit.text)
    except ValueError as hata:
        raise BuguiTRHatasi(
            f"BuguiTR {yol} ucu JSON yerine beklenmeyen bir yanıt verdi "
            "(site değişmiş olabilir).") from hata


def _yazi(slug: str) -> Dict[str, Any]:
    """Slug'ı verilen yazı (id, slug, title, content); yoksa 404 `BuguiTRHatasi`.

    Bölüm bağlantılarının hepsi yazıya (post) gidiyor; sayfa (page) yedeği,
    grup bir gün bölümü sayfa olarak yayımlarsa diye: yalnızca yazı
    bulunamadığında ikinci istek atılır.
    """
    alanlar = {"slug": slug, "_fields": "id,slug,title,content,categories"}
    for tur in ("posts", "pages"):
        veri = _get_json(f"/{tur}", alanlar)
        if not isinstance(veri, list):
            raise BuguiTRHatasi(f"BuguiTR /{tur} ucunun yanıtı beklenen biçimde değil.")
        for kayit in veri:
            if isinstance(kayit, dict) and kayit.get("slug") == slug:
                return kayit
    raise BuguiTRHatasi(f"BuguiTR'de '{slug}' yazısı bulunamadı.", status_code=404)


# ─────────────────────────────────────────────────────────────────────────────
# Metin yardımcıları
# ─────────────────────────────────────────────────────────────────────────────
def _metin(parca: str) -> str:
    """HTML parçası → düz, tek boşluklu metin."""
    return re.sub(r"\s+", " ", _html.unescape(_ETIKET_RE.sub(" ", parca or ""))).strip()


def _sade(metin: str) -> str:
    """Karşılaştırma için: aksansız, küçük harf ("BÖLÜM" → "bolum", "İZLE" → "izle")."""
    metin = unicodedata.normalize("NFKD", metin or "")
    metin = "".join(c for c in metin if not unicodedata.combining(c))
    return metin.casefold().replace("ı", "i")


def _baslik(kayit: Dict[str, Any]) -> str:
    ham = (kayit.get("title") or {}).get("rendered", "") if isinstance(kayit, dict) else ""
    return _metin(str(ham))


_ROMA = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _roma(metin: str) -> Optional[int]:
    """"I V" → 4, "X II" → 12 (site harfleri ayrı <span>'larda yazıyor, arada
    boşluk kalıyor); Roma rakamı değilse None."""
    harfler = re.sub(r"\s+", "", metin or "").casefold()
    if not harfler or any(h not in _ROMA for h in harfler):
        return None
    toplam = 0
    for i, h in enumerate(harfler):
        deger = _ROMA[h]
        if i + 1 < len(harfler) and _ROMA[harfler[i + 1]] > deger:
            toplam -= deger
        else:
            toplam += deger
    return toplam if 0 < toplam < 400 else None


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
# "Tadaima, Okaeri 3. Bölüm", "Shutline 12. BÖLÜM – SON", "Mignon – Episode 1"
_BOLUM_BASLIGI_RE = re.compile(r"\d+\s*\.?\s*(?:bolum|bolumu)\b|\bepisode\s*\d+|\bbolum\s*\d+")


def arama_ayristir(veri: Any) -> List[Tuple[str, str]]:
    """REST arama yanıtı → [(seri slug'ı, başlık)], sitenin sırasıyla.

    Yalnızca seri tanıtımları kalır: "ANİME"/"DONGHUA" kategorisindekiler ya
    da yalnız "Anime" kategorisinde olup başlığı bölüm gibi durmayanlar (film
    tanıtımı: "Umibe no Étranger").
    """
    out: List[Tuple[str, str]] = []
    gorulen = set()
    for kayit in veri if isinstance(veri, list) else []:
        if not isinstance(kayit, dict):
            continue
        slug = str(kayit.get("slug") or "")
        baslik = _baslik(kayit)
        if not _SLUG_RE.match(slug) or not baslik or slug in gorulen:
            continue
        kategoriler = {k for k in kayit.get("categories") or [] if isinstance(k, int)}
        if not kategoriler & SERI_KATEGORILERI and _BOLUM_BASLIGI_RE.search(_sade(baslik)):
            continue
        gorulen.add(slug)
        out.append((slug, baslik))
    return out


def _sirala(sorgu: str, kayitlar: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Tam eşleşme, önek, içerme, kısa ad önce; eşitlikte sitenin sırası.

    WordPress araması içerikte de arıyor: "sasaki" için önce Sotsugyou-hen
    filmi gelebiliyor. (Arama motoru ayrıca `title_match` ile sıralıyor; bu,
    CLI ve tarayıcı için de doğru sıra.)
    """
    q = _sade(sorgu)

    def anahtar(kayit: Tuple[str, str]):
        ad = _sade(kayit[1])
        return (ad != q, not ad.startswith(q), q not in ad, len(ad))

    return sorted(kayitlar, key=anahtar)


def search_buguitr(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """BuguiTR'nin animasyon kategorilerinde ara → [(seri slug'ı, başlık)].

    İki karakterden kısa sorgu ağa çıkmaz. Siteye ulaşılamazsa
    `BuguiTRHatasi`: arama sayfası "sonuç yok" yerine sebebi göstersin.
    """
    q = (query or "").strip()
    if len(q) < 2 or limit <= 0:
        return []
    veri = _get_json("/posts", {
        "search": q,
        "categories": ",".join(str(k) for k in ANIME_KATEGORILERI),
        # Katalog küçük (~20 seri, ~100 bölüm yazısı); tek sayfa yetiyor.
        # Bölüm yazıları da eşleşip ayıklandığı için limit'in birkaç katı.
        "per_page": 50,
        "_fields": "id,slug,title,categories",
    })
    return _sirala(q, arama_ayristir(veri))[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
_BOLUM_SOZCUKLERI = ("bolum", "episode", "film", "izle", "ozel", "final")
_BOLUM_SLUG_RE = re.compile(r"(?:^|-)(?:bolum|bolumler|episode)(?:-|$)")
_SEZON_SLUG_RE = re.compile(r"(?:^|-)(\d{1,2})-sezon(?:-|$)")
# "1. & 2. BÖLÜM", "9. & 10. BÖLÜM | FİNAL", "3 ve 4. bölüm"
_COKLU_RE = re.compile(r"(\d{1,4})\s*\.?\s*(?:&|ve|-|,)\s*(\d{1,4})\s*\.?\s*bolum")
_TEK_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*\.?\s*(?:bolum|episode)|"
                     r"(?:bolum|episode)\s*(\d{1,4})")
_SAYI_RE = re.compile(r"^(\d{1,4})(?:[.,](\d{1,2}))?$")


def _bolum_baglantisi_mi(metin: str, slug: str) -> bool:
    """Tanıtım yazısındaki bağlantı bir bölüme mi gidiyor?

    Bölüm düğmeleri "1", "01", "I V", "5,5", "1. Bölüm", "ÖZEL BÖLÜM",
    "FİLMİ İZLE" yazıyor. Diğer serilere çapraz bağlantılar seri adını
    ("NO LOVE ZONE", "MIGNON", "ANİME") taşıyor ve slug'larında "bolum" yok.
    """
    sade = _sade(metin)
    if _SAYI_RE.match(sade) or _roma(sade) is not None:
        return True
    if any(s in sade for s in _BOLUM_SOZCUKLERI):
        return True
    return bool(_BOLUM_SLUG_RE.search(slug))


def _numara(deger: str) -> str:
    """"01" → "1", "5,5" → "5.5" (ayrıştırıcı "5.5. Bölüm"ü ara bölüm tanıyor)."""
    m = _SAYI_RE.match(deger)
    if not m:
        return deger
    return str(int(m.group(1))) + (f".{int(m.group(2))}" if m.group(2) else "")


def _bolum_girdileri(metin: str, slug: str) -> List[Tuple[str, str]]:
    """Bir bölüm bağlantısı → [(bölüm kimliği, başlık)] (çoklu yazıda birden çok).

    Başlıklar ayrıştırıcının (`common/episode_parser`) tanıdığı biçimde
    kuruluyor ("3. Bölüm", "2. Sezon 1. Bölüm", "5.5. Bölüm"): düğme metni
    sezonsuz ("1. Bölüm" iki sezonda da var), sezon slug'da ("-2-sezon-").
    """
    sade = _sade(metin)
    m = _SEZON_SLUG_RE.search(slug)
    sezon = int(m.group(1)) if m else 1
    onek = f"{sezon}. Sezon " if sezon > 1 else ""

    coklu = _COKLU_RE.search(sade)
    if coklu:
        ilk, son = int(coklu.group(1)), int(coklu.group(2))
        if 0 < son - ilk <= 3:
            return [(f"{slug}#{n}", f"{onek}{n}. Bölüm") for n in range(ilk, son + 1)]

    numara: Optional[str] = None
    if _SAYI_RE.match(sade):
        numara = _numara(sade)
    elif _roma(sade) is not None:
        numara = str(_roma(sade))
    else:
        tek = _TEK_RE.search(sade)
        if tek:
            numara = _numara(tek.group(1) or tek.group(2))
    if numara is not None:
        return [(slug, f"{onek}{numara}. Bölüm")]

    if "tum bolumler" in sade or "tum-bolumler" in slug:
        return [(slug, "Tüm Bölümler (tek video)")]
    if "ozel" in sade:
        return [(slug, f"{onek}Özel Bölüm")]
    if "film" in sade:
        return [(slug, "Film")]
    return [(slug, metin.strip() or slug)]


def _video_iframeleri(icerik: str) -> List[str]:
    """İçerikteki oynatıcı iframe adresleri (fragman/süs iframe'leri hariç)."""
    out = []
    for nitelikler in _IFRAME_RE.findall(icerik or ""):
        adres = _iframe_adresi(nitelikler)
        konak = (urlsplit(adres).hostname or "").lower() if adres else ""
        if konak and not _VIDEO_SAYILMAYAN.search(konak):
            out.append(adres)
    return out


def bolumleri_ayristir(seri_slug: str, kayit: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Yazı → [(bölüm kimliği, başlık)], sitenin sırasıyla.

    * Yazı videoyu kendisi gömüyorsa (film tanıtımı, ya da kimlik zaten bir
      bölüm yazısı) tek girdi: yazının kendisi. Bölüm yazılarındaki "sonraki
      bölüm" düğmeleri bölüm listesi sanılmasın diye bu kural önce.
    * Değilse tanıtımdaki bölüm bağlantıları.
    * İkisi de yoksa (tanıtım yazıldı, bölüm henüz yok) gerçek bir "0 bölüm".
    """
    icerik = str(((kayit or {}).get("content") or {}).get("rendered") or "")
    if _video_iframeleri(icerik):
        return [(seri_slug, _baslik(kayit) or "Film")]
    out: List[Tuple[str, str]] = []
    gorulen = set()
    for hedef, ic in _ICBAG_RE.findall(icerik):
        hedef = hedef.strip().lower()
        metin = _metin(ic)
        if (hedef == seri_slug or not _SLUG_RE.match(hedef)
                or hedef in ("wp-content", "category", "tag", "author", "page")
                or not _bolum_baglantisi_mi(metin, hedef)):
            continue
        for bolum_id, baslik in _bolum_girdileri(metin, hedef):
            if bolum_id not in gorulen:
                gorulen.add(bolum_id)
                out.append((bolum_id, baslik))
    return out


def _seri_slugu(anime_id: str) -> str:
    slug = str(anime_id or "").strip().strip("/").lower()
    # Tam adres de kabul: "https://buguitr.com/tadaima-okaeri/" → slug.
    m = re.match(r"^https?://(?:www\.)?buguitr\.com/([^/?#]+)", slug)
    if m:
        slug = m.group(1)
    if not _SLUG_RE.match(slug):
        raise BuguiTRHatasi(
            f"BuguiTR yazı slug'ı bekliyor (ör. tadaima-okaeri), '{anime_id}' geçersiz.",
            status_code=400)
    return slug


def get_anime_episodes(anime_id: str) -> List[Tuple[str, str]]:
    """Serinin bölümleri → [(bölüm kimliği, başlık), ...]."""
    slug = _seri_slugu(anime_id)
    return bolumleri_ayristir(slug, _yazi(slug))


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _bolum_coz(episode_id: str) -> Tuple[str, Optional[int]]:
    m = _BOLUM_ID_RE.match(str(episode_id or "").strip().lower())
    if not m:
        raise BuguiTRHatasi(
            f"BuguiTR bölüm kimliği yazı slug'ı olmalı (ör. tadaima-okaeri-3-bolum), "
            f"'{episode_id}' geçersiz.", status_code=400)
    return m.group(1), (int(m.group(2)) if m.group(2) else None)


def bolum_adresi(episode_id: str) -> str:
    """Bölüm kimliği → sitedeki yazının adresi (bölüm nesnesinin url'si).

    Çoklu yazının bölümleri parça (#2) ile ayrışıyor: adres nesnenin kimliği
    gibi de kullanılıyor, iki bölüm aynı adrese çökmesin.
    """
    try:
        slug, parca = _bolum_coz(episode_id)
    except BuguiTRHatasi:
        return f"{BASE_URL}/{str(episode_id or '').strip('/')}"
    return f"{BASE_URL}/{slug}/" + (f"#{parca}" if parca is not None else "")


def _iframe_adresi(nitelikler: str) -> str:
    """iframe nitelikleri → oynatılabilir mutlak adres; yoksa ""."""
    for ad in _SRC_ADLARI:
        m = re.search(rf"(?<![\w-]){ad}\s*=\s*[\"']?([^\"'\s>]+)", nitelikler or "", re.I)
        if not m:
            continue
        # İçerik HTML: "&#038;" → "&" (vk/ok.ru sorgu dizgileri).
        adres = _html.unescape(m.group(1).strip()).replace("\\/", "/")
        if adres.startswith("//"):          # protokolsüz: //ok.ru/videoembed/…
            adres = "https:" + adres
        if re.match(r"^https?://", adres):
            return adres
    return ""


def _aynalar(icerik: str) -> List[Tuple[str, str]]:
    """İçerikteki iframe'ler → [(önündeki son metin bloğu, adres)], sayfa sırasıyla."""
    icerik = icerik or ""
    out: List[Tuple[str, str]] = []
    for m in _IFRAME_RE.finditer(icerik):
        adres = _iframe_adresi(m.group(1))
        if not adres:
            continue
        baslik = ""
        for parca in reversed(_BLOK_RE.split(icerik[:m.start()])):
            baslik = _metin(parca)
            if baslik:
                break
        out.append((baslik, adres))
    return out


def _bolume_ait(aynalar: List[Tuple[str, str]], bolum: int) -> List[Tuple[str, str]]:
    """Çoklu yazıda yalnızca ``bolum``'ün başlığı altındaki iframe'ler.

    Başlıkların hiçbiri bölüm numarası taşımıyorsa ayrım yapılamaz; bütün
    iframe'ler döner (yanlış bölümü oynatmaktansa kullanıcı seçsin).
    """
    def numaralar(baslik: str) -> List[int]:
        return [int(n) for n in re.findall(r"(\d{1,4})\s*\.?\s*bolum", _sade(baslik))]

    if not any(numaralar(b) for b, _ in aynalar):
        return aynalar
    return [(b, a) for b, a in aynalar if bolum in numaralar(b)]


def _oynatici(adres: str) -> Optional[Tuple[str, str]]:
    konak = (urlsplit(adres).hostname or "").lower()
    for desen, ad, gorunen in _OYNATICILAR:
        if desen.search(konak):
            return ad, gorunen
    return None


def akislari_ayristir(icerik: str, bolum: Optional[int] = None) -> List[Dict[str, str]]:
    """Bölüm yazısının içeriği → oynatılabilir akışlar, ortak öncelikle sıralı."""
    aynalar = _aynalar(icerik)
    if bolum is not None:
        aynalar = _bolume_ait(aynalar, bolum)
    adaylar: List[Tuple[Tuple[int, int], int, Dict[str, str]]] = []
    gorulen = set()
    atilan: List[str] = []
    for sira, (_, adres) in enumerate(aynalar):
        if adres in gorulen:
            continue
        gorulen.add(adres)
        oynatici = _oynatici(adres)
        if oynatici is None:
            atilan.append(urlsplit(adres).hostname or adres)
            continue
        ad, gorunen = oynatici
        adaylar.append((oncelik_anahtari(ad), sira, {
            "url": adres,
            "label": gorunen,
            "player": ad,
            # Gömülü oynatıcı sayfası: yt-dlp/mpv çözüyor.
            "type": "iframe",
            # Tarayıcı iframe'i bu siteden açıyor; bazı gömülü oynatıcılar
            # (vidmoly) gömen sayfayı görmek istiyor.
            "referer": REFERER,
            "fansub": FANSUB,
        }))
    if atilan:
        log.info("BuguiTR: oynatılamayan aynalar atlandı: %s", ", ".join(atilan))
    adaylar.sort(key=lambda a: (a[0], a[1]))
    # Aynı oynatıcıdan iki ayna (ör. iki Sibnet kopyası) aynı etiketle
    # görünmesin: kullanıcı hangisini seçtiğini ayırt edebilmeli.
    sayac: Dict[str, int] = {}
    out = []
    for _, _, akis in adaylar:
        sayac[akis["label"]] = sayac.get(akis["label"], 0) + 1
        if sayac[akis["label"]] > 1:
            akis["label"] = f"{akis['label']} {sayac[akis['label']]}"
        out.append(akis)
    return out


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları.

    ``[{"url", "label", "player", "type": "iframe", "referer", "fansub"}, ...]``;
    aynaların hepsi desteklenmeyen oynatıcılardaysa ``[]``. Yazı bulunamazsa,
    site okunamazsa `BuguiTRHatasi`.
    """
    slug, bolum = _bolum_coz(episode_id)
    kayit = _yazi(slug)
    icerik = str((kayit.get("content") or {}).get("rendered") or "")
    return akislari_ayristir(icerik, bolum)


__all__ = [
    "BASE_URL",
    "BuguiTRHatasi",
    "bolum_adresi",
    "get_anime_episodes",
    "get_episode_streams",
    "search_buguitr",
]
