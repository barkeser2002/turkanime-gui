"""
SeiCode kaynağı — https://seicode.net

SeiCode'un kendi Türkçe fansub'ı: altyazı görüntüye gömülü (hardsub), ayrı
altyazı dosyası yok. Katalog küçük ve güncel (≈180 dizi, çoğu 2024-2026
sezonları, birkaç donghua); One Piece/Naruto/Frieren yok. Ön yüz SvelteKit;
bütün veri herkese açık bir JSON API'den geliyor. Giriş, çerez, CAPTCHA ya da
JS kapısı yok (2026-09-24/30 ölçümleri).

API (https://next.seicode.net, Cloudflare önünde, challenge YOK):
- Arama:    GET /anime/search?q=<q>  -> [{slug, english, pictures{avatar,banner}, ...}]
            Yalnızca İngilizce (TMDB) adda, büyük/küçük harf duyarsız KELİME
            araması (kelimeler "veya"lanıyor: "demon school" dört dizi
            döndürüyor); romaji/Türkçe ad eşleşmez ("kusuriya" → [],
            "apothecary" → The Apothecary Diaries). Kelime parçası da her
            zaman eşleşmiyor: "iruma" → [], "Iruma-kun" → Iruma-kun.
            2 karakterden kısa sorgu HTTP 400 (gövde "[]").
- Katalog:  GET /anime?page=<n>  -> {animes: [32 kayıt], page, totalPages}
            (2026-09: 6 sayfa, 180 dizi). Arama boş dönerse yedek olarak
            bunun üzerinde alt dizge araması yapılıyor.
- Bölümler: GET /anime/<slug>  -> {slug, english, seasons: [{season_number,
            episodes: [{episode_number, video_links: {<ad>: <gömme adresi>}}]}]}
            Bilinmeyen slug 404 {"code": "error.animeNotFound"}.
- Akışlar:  ayrı uç YOK. Sitenin oynatıcısı `video_links[<ad>]` değerini olduğu
            gibi <iframe src> yapıyor; bölümün gömme adresleri aynı detay
            yanıtında. Adlar serbest metin ("OkRu", "vidmoly.to", "short.ink"),
            bu yüzden karar ADRESİN KONAĞINA göre veriliyor.

Kimlikler:
- Kaynak kimliği: sitenin slug'ı ("jujutsu-kaisen").
- Bölüm kimliği: "<slug>/<sezon>/<bölüm>" ("jujutsu-kaisen/3/1"); sitenin
  /anime/<slug>/<sezon>/<bölüm> izleme yoluyla birebir aynı.

Sezon numaraları TMDB'ninki ve yalnızca sitede OLAN sezonlar geliyor: JJK'de
yalnız 3. sezon var, Bleach'te 1. sezonun 46. bölümü ve 2. sezonun 27-48'i.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

# Kullanıcının "Erişimi aç"la geçtiği bot doğrulaması (çerez + tarayıcı
# kimliği); kayıt yoksa sarmalayıcı istekleri olduğu gibi geçirir.
from ..common import oturumlar

try:
    # Engel izlerinin tek listesi istemcide; ayrı bir kopya tutmak ayrışmaya
    # yol açıyor (bkz. ANIME_PROVIDER_GUIDE.md, "Engeli sessizce yutma").
    from ..common.cf_bypass import CHALLENGE_MARKERS as _CF_IZLERI
    from ..common.cf_bypass import USER_AGENTS as _UA_LISTESI
except Exception:  # pragma: no cover - sunucu tek başına da çalışabilmeli
    _CF_IZLERI = ("Just a moment", "cf-browser-verification", "challenge-platform")
    _UA_LISTESI = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"]

log = logging.getLogger(__name__)

BASE_URL = "https://seicode.net"
API_URL = "https://next.seicode.net"
REFERER = BASE_URL + "/"
FANSUB = "SeiCode"
HTTP_TIMEOUT = 15
# Ölü CDN TLS'ten sonra bağlantıyı ~11 sn bekletip kesiyor (Animexe'nin aynı
# tau-video CDN ailesinde ölçüldü); yoklama bunun altında kalmalı.
YOKLAMA_TIMEOUT = 6
IMPERSONATE = "chrome131"

# API'de hız sınırı gözlenmedi (180 detay isteği 6 iş parçacığıyla 5,6 sn'de,
# hepsi 200). Yine de aynı konağa arka arkaya istek arasında kısa bir aralık
# bırakılıyor: kullanıcı başına zaten 1-2 istek, küçük bir fansub sitesini
# gereksiz yere zorlamayalım.
_MIN_INTERVAL = 0.5
# Detay yanıtı hem bölüm listesi hem akışlar için gerekiyor; bölüm listesini
# açıp hemen oynatan kullanıcı ikinci kez indirmesin. Kısa tutuluyor: site
# yeni bölüm ekledikçe liste tazelenmeli.
_DETAY_TTL = 5 * 60
# 5xx/ağ hatasından sonraki tek yeniden denemeden önce bekleme.
_YENIDEN_DENEME_BEKLEMESI = 1.0
_COZUCU_ISCI = 4
# Arama yedeği için bütün katalog (6 istek). Site haftada birkaç dizi
# ekliyor; 6 saat eski liste yalnızca yedek aramayı etkiler.
_KATALOG_TTL = 6 * 60 * 60
_KATALOG_SAYFA_SINIRI = 20             # bugün 6; site bozulursa sonsuz döngü olmasın

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Sezon/bölüm haneleri sınırlı: kimlik arşivden/kullanıcıdan da gelebilir ve
# doğrudan URL yoluna giriyor ("../" ya da sorgu dizgisi sızmasın).
_BOLUM_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)/(\d{1,3})/(\d{1,5})$")
_KALITE_RE = re.compile(r"\d{3,4}p")

_kilit = threading.Lock()               # oturum + detay önbelleği
_aralik_kilidi = threading.Lock()       # API istek aralığı (uyurken önbelleği tutmasın)
_son_istek = 0.0
_oturum_nesnesi = None
_detay_onbellek: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_katalog_onbellek: Tuple[float, List[Dict[str, Any]]] = (0.0, [])


class SeiCodeHatasi(RuntimeError):
    """SeiCode'a ulaşılamadı ya da yanıt beklenen biçimde değil.

    Mesaj kullanıcıya gösterilecek Türkçe cümle. ``status_code`` hem
    `common.hatalar` (engel/zaman aşımı/video yok ayrımı) hem de sunucu
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
    """Yeni HTTP oturumu. Testler ağa çıkmamak için bunu sahteler.

    Tarayıcı User-Agent'ı ŞART: Cloudflare yalnızca urllib'in varsayılanına
    ("Python-urllib/3.x") 403 veriyor; curl_cffi'nin taklidi ve düz requests
    ile tarayıcı UA'sı 200 alıyor.
    """
    if _HAS_CURL:
        return oturumlar.oturumlu(_http.Session(impersonate=IMPERSONATE))
    oturum = _http.Session()
    oturum.headers["User-Agent"] = _UA_LISTESI[0]
    return oturumlar.oturumlu(oturum, curl=False)


def _oturum():
    """Paylaşılan oturum (bağlantı yeniden kullanılsın).

    curl_cffi oturumu iş parçacığı başına ayrı curl tutamacı kullanıyor
    (`use_thread_local_curl`), ok.ru/tau çözücüleri aynı oturumu paralel
    kullanabiliyor.
    """
    global _oturum_nesnesi
    with _kilit:
        if _oturum_nesnesi is None:
            _oturum_nesnesi = _yeni_oturum()
        return _oturum_nesnesi


def _engel_sayfasi_mi(yanit) -> bool:
    """Yanıt gerçek içerik değil, Cloudflare'in engel/challenge sayfası mı?

    Yalnızca hata durumlarında gövdeye bakılır: normal bir sayfa da
    "challenge-platform" gibi dizgeler taşıyabilir. "Sorry, you have been
    blocked" / "Attention Required!" CF'nin IP tabanlı WAF engeli (tau-video bu
    makineye böyle cevap veriyor).
    """
    if yanit.status_code in (403, 429):
        return True
    if yanit.status_code < 500:
        return False
    bas = (yanit.text or "")[:6000]
    return any(iz in bas for iz in (*_CF_IZLERI, "Attention Required!",
                                    "you have been blocked"))


def _get(adres: str, *, params: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None, timeout: float = HTTP_TIMEOUT,
         aralikli: bool = False):
    """Tek GET. Ağ hatası olduğu gibi yükselir; HTTP durumu çağıranın işi.

    ``aralikli``: SeiCode API'sine giden istekler arasında `_MIN_INTERVAL`
    bırakılır. ok.ru/tau-video gibi başka konaklara giden çözücü istekleri
    beklemez (farklı sunucular, paralel koşuyorlar).
    """
    global _son_istek
    if aralikli:
        with _aralik_kilidi:
            bekle = _MIN_INTERVAL - (time.monotonic() - _son_istek)
            if bekle > 0:
                time.sleep(bekle)
            _son_istek = time.monotonic()
    return _oturum().get(adres, params=params, headers=headers, timeout=timeout)


def _api(yol: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
    """API çağrısı → çözülmüş JSON; 400/404 → None.

    404 bilinmeyen slug, 400 geçersiz (kısa) sorgu: ikisi de "yok" demek,
    hata değil. 403/429 ve CF sayfası engellenme. Ağ hatası ve 5xx bir kez
    yeniden denenir (Cloudflare arada bir 502/520 verebiliyor); yine olmazsa
    `SeiCodeHatasi`.
    """
    adres = API_URL + yol
    basliklar = {"Accept": "application/json", "Referer": REFERER}
    son_hata = ""
    # Asıl istisna zincirde kalsın: `common.hatalar` zaman aşımını/DNS'i ondan
    # tanıyıp kullanıcıya "zaman aşımı" gibi sebebi söylüyor.
    son_istisna: Optional[BaseException] = None
    kod: Optional[int] = None
    for deneme in range(2):
        if deneme:
            time.sleep(_YENIDEN_DENEME_BEKLEMESI)
        try:
            yanit = _get(adres, params=params, headers=basliklar, aralikli=True)
        except Exception as hata:
            son_hata, son_istisna, kod = f"{type(hata).__name__}: {hata}", hata, None
            continue
        kod = yanit.status_code
        if kod in (400, 404):
            return None
        if _engel_sayfasi_mi(yanit):
            raise SeiCodeHatasi(
                f"SeiCode istekleri engelledi (HTTP {kod}); birkaç dakika sonra "
                "yeniden deneyin.", status_code=kod)
        if kod >= 500:
            son_hata, son_istisna = f"HTTP {kod}", None
            continue
        if kod != 200:
            raise SeiCodeHatasi(f"SeiCode {yol} ucu HTTP {kod} döndü.", status_code=kod)
        try:
            return json.loads(yanit.text)
        except ValueError as hata:
            raise SeiCodeHatasi(
                f"SeiCode {yol} ucu JSON yerine beklenmeyen bir yanıt verdi "
                "(site değişmiş olabilir).") from hata
    raise SeiCodeHatasi(f"SeiCode'a bağlanılamadı ({son_hata}).",
                        status_code=kod) from son_istisna


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def _kapak(resimler: Any) -> Optional[str]:
    """TMDB kapak adresi, kart boyutunda.

    Site "original" boyutu veriyor (çoğu 1-3 MB); arama kartı için w342 yeter.
    Yol "original//abc.jpg" gibi çift eğik çizgili geliyor, tekilleştiriliyor.
    """
    if not isinstance(resimler, dict):
        return None
    adres = str(resimler.get("avatar") or resimler.get("banner") or "").strip()
    if not adres.startswith("https://"):
        return None
    eslesme = re.match(r"^(https://image\.tmdb\.org/t/p/)original/+([^/?#]+)$", adres)
    return f"{eslesme.group(1)}w342/{eslesme.group(2)}" if eslesme else adres


def arama_ayristir(veri: Any) -> List[Dict[str, Any]]:
    """Arama yanıtı → ``[{"slug", "title", "image"}]`` (site sırasıyla, tekrarsız).

    Slug doğrulanıyor: kimlik doğrudan /anime/<slug> yoluna giriyor.
    """
    out: List[Dict[str, Any]] = []
    gorulen = set()
    for kayit in veri if isinstance(veri, list) else []:
        if not isinstance(kayit, dict):
            continue
        slug = str(kayit.get("slug") or "").strip()
        if not _SLUG_RE.match(slug) or slug in gorulen:
            continue
        gorulen.add(slug)
        baslik = _html.unescape(str(kayit.get("english") or "")).strip() or slug
        out.append({"slug": slug, "title": baslik, "image": _kapak(kayit.get("pictures"))})
    return out


def katalog() -> List[Dict[str, Any]]:
    """Sitenin bütün kataloğu (/anime?page=1..N), 6 saat önbellekli.

    Ağ/engel hatası `SeiCodeHatasi` olarak yükselir; yarım liste önbelleğe
    yazılmaz.
    """
    global _katalog_onbellek
    simdi = time.monotonic()
    with _kilit:
        zaman, kayitlar = _katalog_onbellek
        if kayitlar and simdi - zaman < _KATALOG_TTL:
            return kayitlar
    kayitlar = []
    sayfa, toplam = 1, 1
    while sayfa <= min(toplam, _KATALOG_SAYFA_SINIRI):
        veri = _api("/anime", params={"page": sayfa})
        if not isinstance(veri, dict):
            break
        kayitlar.extend(k for k in veri.get("animes") or [] if isinstance(k, dict))
        try:
            toplam = int(veri.get("totalPages") or 1)
        except (TypeError, ValueError):
            toplam = 1
        sayfa += 1
    with _kilit:
        _katalog_onbellek = (simdi, kayitlar)
    return kayitlar


def _kelimeler(metin: Any) -> List[str]:
    """Aksansız, küçük harf harf/rakam dizileri ("Re:ZERO -Starting…" → re, zero, …)."""
    metin = unicodedata.normalize("NFKD", _html.unescape(str(metin or "")).casefold())
    return re.findall(r"[a-z0-9]+", "".join(c for c in metin if not unicodedata.combining(c)))


def katalogda_ara(sorgu: str, kayitlar: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Katalogda alt dizge araması: sorgunun HER kelimesi ad ya da slug içinde.

    Sitenin araması kelime tabanlı ve kelime parçasını kaçırıyor ("iruma" →
    [], dizi "Welcome to Demon School! Iruma-kun"). Boşluksuz yazım da
    tanınıyor ("rezero" → "Re:ZERO"). Tek harfli parçalar yok sayılıyor ("spy x
    family" → spy, family): alt dizge olarak her ada uyarlar. En az bir parça
    3+ harf değilse ("x!", "no") yedek hiç çalışmaz, bütün katalog dönerdi.
    Sıra katalogdaki sıra; alaka sıralamasını arama motoru
    (`title_match.siralama_skoru`) zaten yapıyor.
    """
    parcalar = [p for p in _kelimeler(sorgu) if len(p) >= 2]
    if not any(len(p) >= 3 for p in parcalar):
        return []
    uyanlar = []
    for kayit in kayitlar:
        kelimeler = _kelimeler(kayit.get("english")) + _kelimeler(kayit.get("slug"))
        metin, bitisik = " ".join(kelimeler), "".join(kelimeler)
        if all(p in metin or p in bitisik for p in parcalar):
            uyanlar.append(kayit)
    return arama_ayristir(uyanlar)


def _katalog_yedegi(sorgu: str) -> List[Dict[str, Any]]:
    """Arama boş döndüğünde katalogda ara; katalog alınamazsa [] (arama yine de
    cevap verdi, "sonuç yok" doğru; hata yalnızca kayda)."""
    try:
        return katalogda_ara(sorgu, katalog())
    except SeiCodeHatasi as hata:
        log.info("SeiCode: katalog yedeği alınamadı: %s", hata)
        return []


def search_seicode_zengin(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Kapak görselli arama: ``[{"slug", "title", "image"}]``; sonuç yoksa [].

    Site 2 karakterden kısa sorguya 400 veriyor; o istek hiç atılmıyor.
    Sitenin araması boş dönerse kataloğun kendisinde alt dizge araması
    yapılır (bkz. `katalogda_ara`). Siteye ulaşılamazsa `SeiCodeHatasi`:
    arama sayfası "sonuç yok" yerine sebebi göstersin.
    """
    q = (query or "").strip()
    if len(q) < 2 or limit <= 0:
        return []
    sonuc = arama_ayristir(_api("/anime/search", params={"q": q}))
    if not sonuc:
        sonuc = _katalog_yedegi(q)
    return sonuc[:limit]


def search_seicode(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """SeiCode'da anime ara → ``[(slug, başlık), ...]``.

    Yalnızca İngilizce (TMDB) adla eşleşiyor; katalogda romaji ya da Türkçe ad
    hiç yok. Romaji ile aranan dizi (ör. "Kusuriya no Hitorigoto") burada
    bulunmaz; detay sayfasının otomatik eşleştirmesi romajiden sonra İngilizce
    adı da denediği için orada bağlanır. AniList/Jikan'dan takma ad çekip
    yeniden aramak bilerek yapılmıyor: sunucu tarayıcısı bu ucu kullanıyor ve
    "yalnızca hedef kaynağa istek" garantisi var (bkz.
    `turkanime_server/crawler/eslestirme.py`); katalog yedeği de yalnızca
    SeiCode'a gidiyor.
    """
    return [(k["slug"], k["title"]) for k in search_seicode_zengin(query, limit)]


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def _slug_dogrula(slug: Any) -> str:
    kimlik = str(slug or "").strip().strip("/").lower()
    if not _SLUG_RE.match(kimlik):
        raise SeiCodeHatasi(
            f"SeiCode anime kimliği küçük harf/rakam/tire olmalı (ör. jujutsu-kaisen), "
            f"'{slug}' geçersiz.", status_code=400)
    return kimlik


def _detay(slug: str) -> Dict[str, Any]:
    """/anime/<slug> yanıtı (5 dk önbellekli); anime yoksa `SeiCodeHatasi` (404).

    Bilinmeyen slug hata, "0 bölüm" değil: kayıtlı bir eşleşme sitede
    kaldırılmış seriye işaret ediyorsa kullanıcı sebebi görmeli.
    """
    simdi = time.monotonic()
    with _kilit:
        kayit = _detay_onbellek.get(slug)
        if kayit and simdi - kayit[0] < _DETAY_TTL:
            return kayit[1]
    veri = _api(f"/anime/{slug}")
    if veri is None:
        raise SeiCodeHatasi(f"SeiCode'da '{slug}' kimlikli anime bulunamadı.",
                            status_code=404)
    if not isinstance(veri, dict) or not isinstance(veri.get("seasons") or [], list):
        raise SeiCodeHatasi(f"SeiCode anime yanıtı ({slug}) beklenen biçimde değil "
                            "(site değişmiş olabilir).")
    with _kilit:
        # Süresi dolanlar atılıyor: sunucu tarayıcısı bütün kataloğu gezer.
        for eski in [k for k, (t, _v) in _detay_onbellek.items() if simdi - t >= _DETAY_TTL]:
            del _detay_onbellek[eski]
        _detay_onbellek[slug] = (simdi, veri)
    return veri


def _numara(deger: Any) -> Optional[int]:
    """Sezon/bölüm numarası; sayı değilse ya da negatifse None."""
    if isinstance(deger, bool):
        return None
    try:
        sayi = int(deger)
    except (TypeError, ValueError):
        return None
    return sayi if sayi >= 0 else None


def _sezonlar(veri: Dict[str, Any]) -> List[Tuple[int, List[Tuple[int, Dict[str, Any]]]]]:
    """[(sezon, [(bölüm, bölüm_kaydı), ...]), ...] — ikisi de sayısal sırayla.

    Site şu an sıralı ve tekrarsız veriyor (2064 bölümün hepsi); yine de sıra
    ve tekillik burada garanti ediliyor ki bir gün karışık gelirse bölüm
    kimlikleri çakışmasın. Aynı numara iki kez gelirse ilki kalır.
    """
    sezonlar: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for sezon in veri.get("seasons") or []:
        if not isinstance(sezon, dict):
            continue
        s = _numara(sezon.get("season_number"))
        if s is None:
            continue
        bolumler = sezonlar.setdefault(s, {})
        for bolum in sezon.get("episodes") or []:
            if not isinstance(bolum, dict):
                continue
            e = _numara(bolum.get("episode_number"))
            if e is not None and e not in bolumler:
                bolumler[e] = bolum
    return [(s, sorted(sezonlar[s].items())) for s in sorted(sezonlar)]


def bolumleri_ayristir(slug: str, veri: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Detay yanıtı → ``[("<slug>/<sezon>/<bölüm>", başlık), ...]`` izleme sırasıyla.

    Tek sezon ve o sezon 1 ise başlığa sezon yazılmaz ("5. Bölüm"); aksi
    hâlde "3. Sezon 5. Bölüm". JJK'de sitede yalnız 3. sezon var: "5. Bölüm"
    yazmak 1. sezonun 5. bölümü sanılırdı (çok kaynaklı birleştirme de
    (sezon, bölüm) üzerinden yapılıyor). Sitenin bölüm başlığı yok.
    """
    sezonlar = _sezonlar(veri)
    sezon_yaz = [s for s, _ in sezonlar] != [1]
    out: List[Tuple[str, str]] = []
    for s, bolumler in sezonlar:
        for e, _kayit in bolumler:
            baslik = f"{s}. Sezon {e}. Bölüm" if sezon_yaz else f"{e}. Bölüm"
            out.append((f"{slug}/{s}/{e}", baslik))
    return out


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Animenin bölümleri → ``[("<slug>/<sezon>/<bölüm>", başlık), ...]``.

    Sitede henüz bölümü olmayan dizi gerçek bir "0 bölüm" ([]); bilinmeyen
    slug ve ağ hatası `SeiCodeHatasi`.
    """
    kimlik = _slug_dogrula(slug)
    return bolumleri_ayristir(kimlik, _detay(kimlik))


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar: gömme adreslerini oynatılabilir adreslere çevir
# ─────────────────────────────────────────────────────────────────────────────
# Oynatma sırası = ölçülen güvenilirlik (60 rastgele bölüm + ~25 hedefli adres,
# yt-dlp 2026.08.19, 2026-09-24): SIBNET 16/16, ODNOKLASSNIKI (kendi
# çözücümüzle) 8/8, SENDVID 7/7, VIDMOLY 8/11, MAIL 1/1, GDRIVE 10/21 (gerisi
# silinmiş), DAILYMOTION 0/7 (12 adresin 11'i "bulunamadı"). TAUVIDEO en başta:
# bölümlerin %63'ünün yt-dlp'nin açabildiği TEK kopyası o ve doğrudan 1080p MP4
# veriyor (bu makineden Cloudflare IP engeli yüzünden doğrulanamadı, bkz.
# `_tau_coz`). `best_video` etiketteki çözünürlüğe göre de sıralıyor, "1080p"
# etiketli tau/ok.ru kayıtları zaten öne geçer. 2026-09-30 canlı denetimi (6
# dizi, 7 bölüm; yt-dlp + 2 baytlık Range): SIBNET 5/5, SENDVID 4/4, VIDMOLY
# 5/5, ODNOKLASSNIKI 5/5 (`OKRU_UA` ile), GDRIVE 1/7 (404/403/429).
OYNATICI_SIRASI: Tuple[str, ...] = ("TAUVIDEO", "ODNOKLASSNIKI", "SIBNET", "SENDVID",
                                    "VIDMOLY", "MAIL", "GDRIVE", "DAILYMOTION")

# Atlanan konaklar (ölçüm 2026-09-24, bu makineden):
#   dood*/doodstream, uqload  -> Cloudflare anti-bot 403; yt-dlp da KnownPiracy
#                                listesinde tutup reddediyor
#   voe.sx                    -> 403
#   filemoon                  -> yt-dlp "[Piracy] no longer supported"
#   mp4upload, lulu*          -> yt-dlp "Unsupported URL" (mp4upload depoda da
#                                SONA_BIRAKILANLAR'da: yanlış "çalışıyor")
#   abyssplayer, short.icu/ink/inc -> Abyss/Hydrax, yt-dlp desteği yok
#   seicode.rpmvip.com        -> Cloudflare 530 / error 1016 (kaynak sunucu ölü)
#   hdvid.tv                  -> vidhdnow16.shop'a yönleniyor, erişilemedi
#   streamtape                -> 404
#   files.fm, mega.nz, embedrise, vidhdthe, vudeo -> yt-dlp desteği yok
# Döndürülmüyorlar: `best_video` yalnızca ilk birkaç adayı yokluyor ve bunlar
# oynatılabilir kopyaların yerini yerdi.
_ATLANAN_KONAKLAR = ("dood", "uqload", "voe.sx", "filemoon", "mp4upload", "lulu",
                     "streamtape", "abyss", "short.icu", "short.ink", "short.inc",
                     "rpmvip", "hdvid", "files.fm", "mega.nz", "embedrise",
                     "vidhdthe", "vudeo")


def _konak_eslesir(konak: str, alan: str) -> bool:
    """Konak alanın kendisi ya da alt alan adı mı ("m.ok.ru" evet, "book.ru" hayır)."""
    return konak == alan or konak.endswith("." + alan)


def gomme_adresi_temizle(ham: Any) -> Optional[str]:
    """`video_links` değeri → http(s) adres ya da None.

    Değerlerin bir kısmı baştan 8 boşlukla geliyor, bazıları protokolsüz
    ("//ok.ru/..."), arada çöp de var ("Encoder: Mercury").
    """
    if not isinstance(ham, str):
        return None
    adres = ham.strip()
    if adres.startswith("//"):
        adres = "https:" + adres
    parca = urlsplit(adres)
    if parca.scheme.lower() not in ("http", "https") or not parca.hostname:
        return None
    return adres


def _akis(url: str, etiket: str, oynatici: str, **ek: Any) -> Dict[str, Any]:
    akis: Dict[str, Any] = {"url": url, "label": etiket, "player": oynatici,
                            "fansub": FANSUB}
    akis.update({k: v for k, v in ek.items() if v})
    return akis


def normalize_et(adres: str) -> Optional[Dict[str, Any]]:
    """Ağ gerektirmeyen gömme adresini yt-dlp'nin açabileceği biçime çevir.

    Tanınmayan ya da atlanan konak → None. ok.ru ve tau-video burada değil:
    onlar çözücü ister (`cozulecek_mi`).
    """
    parca = urlsplit(adres)
    konak = (parca.hostname or "").lower()
    yol = parca.path or ""
    sorgu = parse_qs(parca.query)
    if any(iz in konak for iz in _ATLANAN_KONAKLAR):
        return None

    if _konak_eslesir(konak, "sibnet.ru"):
        vid = (sorgu.get("videoid") or [""])[0]
        if not vid:
            eslesme = re.search(r"/video(\d+)", yol)
            vid = eslesme.group(1) if eslesme else ""
        if not vid.isdigit():
            return None
        return _akis(f"https://video.sibnet.ru/shell.php?videoid={vid}", "Sibnet", "SIBNET")

    if re.match(r"^(?:www\.)?vidmoly\.(?:me|to|net|biz)$", konak):
        # vidmoly.me/e/<id> 404, vidmoly.me/v/<id> Nuxt SPA ve vidmoly.to JS
        # yönlendirmeli ara sayfa (yt-dlp ikisine de "Unsupported URL");
        # vidmoly.net 301 ile .biz'e gidiyor. Kimlik alanı hepsinde aynı ve
        # .biz/embed-<id>.html'de yt-dlp'nin genel çıkarıcısı master.m3u8'i
        # buluyor. ".htm" (tek adreste) de kabul.
        eslesme = re.match(r"^/(?:e/|v/|embed-|embed/)?([a-z0-9]{12})(?:\.html?)?/?$",
                           yol, re.I)
        if not eslesme:
            return None
        return _akis(f"https://vidmoly.biz/embed-{eslesme.group(1).lower()}.html",
                     "Vidmoly", "VIDMOLY")

    if _konak_eslesir(konak, "sendvid.com"):
        eslesme = re.match(r"^/(?:embed/)?([a-z0-9]{6,})/?$", yol, re.I)
        if not eslesme:
            return None
        return _akis(f"https://sendvid.com/{eslesme.group(1)}", "SendVid", "SENDVID")

    if konak == "drive.google.com":
        # /file/d/<id>/(preview|view)[?usp=...] ya da /open?id=<id>. Klasör
        # bağlantıları (/drive/folders/...) video değil, atlanıyor.
        eslesme = re.match(r"^/file/d/([A-Za-z0-9_-]{20,})", yol)
        kimlik = eslesme.group(1) if eslesme else ""
        if not kimlik and yol.rstrip("/") == "/open":
            aday = (sorgu.get("id") or [""])[0]
            kimlik = aday if re.fullmatch(r"[A-Za-z0-9_-]{20,}", aday) else ""
        if not kimlik:
            return None
        return _akis(f"https://drive.google.com/file/d/{kimlik}/view", "GDrive", "GDRIVE")

    if konak == "dai.ly" or _konak_eslesir(konak, "dailymotion.com"):
        vid = (sorgu.get("video") or [""])[0]
        if not vid:
            eslesme = re.match(r"^/(?:embed/video/|video/)?([A-Za-z0-9]{5,})/?$", yol)
            vid = eslesme.group(1) if eslesme else ""
        if not re.fullmatch(r"[A-Za-z0-9]{5,}", vid or ""):
            return None
        return _akis(f"https://www.dailymotion.com/video/{vid}", "Dailymotion", "DAILYMOTION")

    if _konak_eslesir(konak, "mail.ru"):
        # yt-dlp'nin mailru çıkarıcısı sayfa adresini olduğu gibi tanıyor.
        return _akis(adres, "Mail.ru", "MAIL")
    return None


def _okru_kimligi(adres: str) -> Optional[str]:
    """ok.ru video kimliği (/videoembed/<n>, /video/<n>; eski odnoklassniki.ru da)."""
    parca = urlsplit(adres)
    konak = (parca.hostname or "").lower()
    if not (_konak_eslesir(konak, "ok.ru") or _konak_eslesir(konak, "odnoklassniki.ru")):
        return None
    eslesme = re.match(r"^/video(?:embed)?/(\d+)/?$", parca.path or "")
    return eslesme.group(1) if eslesme else None


def _tau_kimligi(adres: str) -> Optional[Tuple[str, Optional[str]]]:
    parca = urlsplit(adres)
    if not _konak_eslesir((parca.hostname or "").lower(), "tau-video.xyz"):
        return None
    eslesme = re.match(r"^/(?:embed|api/video)/([A-Za-z0-9_-]{6,64})/?$", parca.path or "")
    if not eslesme:
        return None
    vid = (parse_qs(parca.query).get("vid") or [""])[0]
    return eslesme.group(1), (vid if vid.isdigit() else None)


# ok.ru kalite adları → çözünürlük. Etiket çözünürlük taşımalı: `best_video`
# adayları etiketteki "1080p"ye göre sıralıyor.
OKRU_KALITELERI: Dict[str, str] = {
    "ultra": "2160p", "quad": "1440p", "full": "1080p", "hd": "720p",
    "sd": "480p", "low": "360p", "lowest": "240p", "mobile": "144p",
}
_OKRU_SIRA = {ad: i for i, ad in enumerate(OKRU_KALITELERI)}
_OKRU_EN_COK = 2                       # aynı dosyanın iki kalitesi yeter

# okcdn adresi, gömme sayfasını İSTEYENİN tarayıcı ailesine bağlı (`srcAg=`):
# curl_cffi'nin chrome131 taklidi (macOS UA) "CHROME_MAC" alıyor ve o adres
# yt-dlp'nin UA'sıyla HTTP 400 veriyor (2026-09-30 ölçümü; tersi de aynı).
# Adresi yt-dlp oynatıyor/indiriyor (`AdapterVideo.info`, `indir`; mpv de doğrudan
# açamazsa ytdl_hook ile yt-dlp'nin başlıklarına düşüyor) ve yt-dlp'nin
# `random_user_agent`'ı her zaman Windows Chrome: sayfa da Windows Chrome UA'sıyla
# istenir ki adres "CHROME" ailesine bağlansın. Sürüm numarası önemsiz.
OKRU_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# Uygulama mpv'yi kendi kısa UA'sıyla açıyor (`mpv_oynatici.USER_AGENT`, ok.ru
# onu "WEBKIT" sayıyor, adres 400 veriyor); bu yüzden akış UA'sını da taşır
# (`user_agent`), yt-dlp ve mpv aynı UA'yı kullanır.

# Çözülen ok.ru akışları kısa süre saklanıyor. NEDEN: `best_video` her çağrıda
# akışları yeniden istiyor ve okcdn adresi her istekte değişiyor
# (expires/sig). `common.oynatma.yedekli_oynat` oynatılamayan adresi `atla`
# ile eliyor; adres her seferinde yeni olunca aynı ok.ru kopyası her denemede
# yeniden seçilirdi. Adres 24 saat geçerli (2026-09 ölçümü), 5 dk güvenli.
_OKRU_TTL = 5 * 60
_okru_onbellek: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def okru_akislari(sayfa: str) -> List[Dict[str, Any]]:
    """ok.ru gömme sayfası → en iyi iki kalitenin doğrudan MP4 adresi.

    NEDEN kendi çözücümüz: yt-dlp 2026.08.19'un Odnoklassniki çıkarıcısı her
    canlı gömmede ÇÖKÜYOR ("the JSON object must be str, bytes or bytearray,
    not dict"): `flashvars.metadata` artık JSON dizgesi değil nesne. Burada
    ikisi de kabul ediliyor. Telif/yazar engeliyle kaldırılmış videoda
    `data-options` hiç yok (sayfada yalnızca `vp_video_stub_txt`), boş liste.

    okcdn adresleri `expires=`, `srcIp=` (isteyenin IP'si) ve `srcAg=`
    (isteyenin tarayıcı ailesi, bkz. `OKRU_UA`) taşıyor: süreli, IP'ye ve
    UA'ya bağlı. Oynatmadan hemen önce istendiği için masaüstünde sorun değil;
    saklanıp başka makinede kullanılamaz.
    """
    for eslesme in re.finditer(r'data-options="([^"]+)"', sayfa or ""):
        try:
            secenekler = json.loads(_html.unescape(eslesme.group(1)))
        except ValueError:
            continue
        flashvars = secenekler.get("flashvars") if isinstance(secenekler, dict) else None
        meta = (flashvars or {}).get("metadata") if isinstance(flashvars, dict) else None
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                continue
        if not isinstance(meta, dict):
            continue
        videolar = [v for v in meta.get("videos") or []
                    if isinstance(v, dict) and v.get("name") in _OKRU_SIRA
                    and str(v.get("url") or "").startswith("https://")]
        videolar.sort(key=lambda v: _OKRU_SIRA[v["name"]])
        return [_akis(v["url"], f"OK.ru {OKRU_KALITELERI[v['name']]}", "ODNOKLASSNIKI",
                      type="direct", user_agent=OKRU_UA)
                for v in videolar[:_OKRU_EN_COK]]
    return []


def _okru_coz(adres: str) -> List[Dict[str, Any]]:
    kimlik = _okru_kimligi(adres)
    if not kimlik:
        return []
    simdi = time.monotonic()
    with _kilit:
        kayit = _okru_onbellek.get(kimlik)
        if kayit and simdi - kayit[0] < _OKRU_TTL:
            return [dict(a) for a in kayit[1]]
    yanit = _get(f"https://ok.ru/videoembed/{kimlik}",
                 headers={"Referer": REFERER, "User-Agent": OKRU_UA})
    if yanit.status_code != 200:
        log.info("SeiCode: ok.ru %s HTTP %s", kimlik, yanit.status_code)
        return []
    akislar = okru_akislari(yanit.text)
    if not akislar:
        log.info("SeiCode: ok.ru %s oynatılamıyor (kaldırılmış/engelli)", kimlik)
    with _kilit:
        # Kaldırılmış video da ("[]") kesin bir cevap; 5 dk yeniden sorulmaz.
        for eski in [k for k, (t, _v) in _okru_onbellek.items() if simdi - t >= _OKRU_TTL]:
            del _okru_onbellek[eski]
        _okru_onbellek[kimlik] = (simdi, [dict(a) for a in akislar])
    return akislar


def tau_akislari(veri: Any) -> List[Dict[str, Any]]:
    """tau-video `/api/video/<id>` yanıtı → akışlar (yanıttaki sırayla).

    Biçim AnimeciX'inkiyle aynı: ``{"urls": [{"label": "1080p", "url": ...}]}``.
    """
    out: List[Dict[str, Any]] = []
    for kayit in (veri.get("urls") if isinstance(veri, dict) else None) or []:
        if not isinstance(kayit, dict):
            continue
        url = str(kayit.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            continue
        kalite = _KALITE_RE.search(str(kayit.get("label") or ""))
        etiket = f"TauVideo {kalite.group(0)}" if kalite else "TauVideo"
        tur = "hls" if urlsplit(url).path.endswith(".m3u8") else "direct"
        out.append(_akis(url, etiket, "TAUVIDEO", type=tur,
                         referer="https://tau-video.xyz/"))
    return out


def _tau_coz(adres: str) -> List[Dict[str, Any]]:
    """tau-video gömmesi → doğrudan MP4'ler; yanıt vermeyen CDN'ler elenir.

    BU MAKİNEDEN DOĞRULANAMADI: tau-video.xyz veri merkezi IP'lerine hem
    /embed hem /api için Cloudflare "Sorry, you have been blocked" (403)
    veriyor; UA/TLS taklidi değiştirmiyor. Aynı API AnimeciX kaynağında
    kullanılıyor; ev bağlantılarından çalışması bekleniyor.

    Engel HATA DEĞİL, boş liste: tau-video'nun bizi engellemesi SeiCode'un
    engellemesi değil. Hata yükselseydi sunucu tarayıcısı (`nezaket`) bunu
    "kaynak bize kapalı" sayıp SeiCode'u o tur için tamamen kapatırdı; veri
    merkezindeki tarayıcı tau-only ilk bölümde oynatılabilir %37'yi de
    bırakırdı. Kullanıcı sebebi kaydın `bos_akis_mesaji`'ndan görüyor.

    SeiCode'un adreslerinde `?vid=` yok (2064 bölümde 7 istisna); varsa
    AnimeciX'teki gibi iletiliyor.
    """
    kimlik = _tau_kimligi(adres)
    if not kimlik:
        return []
    embed_id, vid = kimlik
    yanit = _get(f"https://tau-video.xyz/api/video/{embed_id}",
                 params={"vid": vid} if vid else None,
                 headers={"Referer": f"https://tau-video.xyz/embed/{embed_id}",
                          "Accept": "application/json"})
    if yanit.status_code != 200:
        log.info("SeiCode: tau-video %s HTTP %s%s", embed_id, yanit.status_code,
                 " (Cloudflare engeli)" if _engel_sayfasi_mi(yanit) else "")
        return []
    try:
        veri = json.loads(yanit.text)
    except ValueError:
        log.info("SeiCode: tau-video %s JSON değil", embed_id)
        return []
    akislar = tau_akislari(veri)
    if not akislar:
        return []
    with ThreadPoolExecutor(max_workers=min(_COZUCU_ISCI, len(akislar))) as havuz:
        canli = list(havuz.map(_canli_mi, akislar))
    return [a for a, tamam in zip(akislar, canli) if tamam]


def _canli_mi(akis: Dict[str, Any]) -> bool:
    """Doğrudan adres gerçekten video veriyor mu? 2 baytlık Range isteğiyle bak.

    NEDEN: tau-video CDN'lerinin yaklaşık yarısı ölü (Animexe'de ölçüldü) ve
    her biri `best_video`'da ~11 sn yt-dlp zaman aşımına mal oluyor; tau
    kayıtları "1080p" etiketiyle en önde durduğu için çalışan Sibnet'e sıra
    gelmeden bütçe bitebilir. ``stream=True`` şart: Range'i yok sayan bir
    sunucu bütün dosyayı yollar. Yoklama kendi oturumunda: akış yanıtı
    kapanana dek bağlantıyı tutuyor, paylaşılan oturumu meşgul etmesin.
    """
    oturum = None
    try:
        oturum = _yeni_oturum()
        yanit = oturum.get(akis["url"], headers={"Range": "bytes=0-1",
                                                 "Referer": akis.get("referer") or REFERER},
                           timeout=YOKLAMA_TIMEOUT, stream=True)
        try:
            kod = yanit.status_code
            tur = str(yanit.headers.get("Content-Type") or "").lower()
        finally:
            try:
                yanit.close()
            except Exception:
                pass
    except Exception as hata:
        log.info("SeiCode: %s yanıt vermedi (%s)", urlsplit(akis["url"]).hostname,
                 type(hata).__name__)
        return False
    finally:
        if oturum is not None:
            try:
                oturum.close()
            except Exception:
                pass
    return kod in (200, 206) and not tur.startswith(("text/", "application/json"))


def cozulecek_mi(adres: str) -> Optional[Callable[[str], List[Dict[str, Any]]]]:
    """Adres ağ isteğiyle çözülmesi gereken bir gömme mi? Öyleyse çözücüsü."""
    if _okru_kimligi(adres):
        return _okru_coz
    if _tau_kimligi(adres):
        return _tau_coz
    return None


def _guvenli(cozucu: Callable[[str], List[Dict[str, Any]]],
             adres: str) -> Tuple[List[Dict[str, Any]], Optional[BaseException]]:
    """Tek bir kopyanın çözülememesi bölümün diğer kopyalarını düşürmesin.

    Hata yutulmuyor, çağırana dönüyor: başka kopya kalmadıysa sebep o.
    """
    try:
        return cozucu(adres) or [], None
    except Exception as hata:
        log.info("SeiCode: %s çözülemedi: %s", urlsplit(adres).hostname, hata)
        return [], hata


def akislari_kur(video_linkleri: Any) -> List[Dict[str, Any]]:
    """Bölümün `video_links` sözlüğü → oynatılabilir akışlar, güvenilirlik sırasıyla.

    ok.ru ve tau-video paralel çözülüyor (her biri ayrı bir konak, ~1 sn);
    diğerleri ağsız normalize ediliyor. Aynı adres iki adla gelirse bir kez.
    Her akışta ``fansub: "SeiCode"`` (tek grup; CLI fansub sormaz).

    Bir çözücü istisnayla düştüyse (zaman aşımı, bağlantı) ve bölümün
    oynatılabilir BAŞKA kopyası da yoksa `SeiCodeHatasi` yükselir: bu geçici
    bir arıza, "video yok" demek yanlış olurdu (sunucu tarayıcısı da onu
    geçici sayıp sonra yeniden dener). Kopya kaldıysa hata yalnızca kayda
    yazılır. tau-video'nun Cloudflare engeli istisna değil (bkz. `_tau_coz`).
    """
    if not isinstance(video_linkleri, dict):
        return []
    akislar: List[Dict[str, Any]] = []
    hatalar: List[BaseException] = []
    cozulecek: List[Tuple[Callable[[str], List[Dict[str, Any]]], str]] = []
    for ham in video_linkleri.values():
        adres = gomme_adresi_temizle(ham)
        if not adres:
            continue
        cozucu = cozulecek_mi(adres)
        if cozucu is not None:
            cozulecek.append((cozucu, adres))
            continue
        akis = normalize_et(adres)
        if akis is not None:
            akislar.append(akis)
    if cozulecek:
        with ThreadPoolExecutor(max_workers=min(_COZUCU_ISCI, len(cozulecek))) as havuz:
            for sonuc, hata in havuz.map(lambda is_: _guvenli(*is_), cozulecek):
                akislar.extend(sonuc)
                if hata is not None:
                    hatalar.append(hata)
    if not akislar and hatalar:
        ilk = hatalar[0]
        raise SeiCodeHatasi(
            "SeiCode: bu bölümün oynatılabilir kopyası alınamadı — "
            f"{type(ilk).__name__}: {ilk}") from ilk

    sira = {ad: i for i, ad in enumerate(OYNATICI_SIRASI)}
    tekil: List[Dict[str, Any]] = []
    gorulen = set()
    for akis in akislar:
        if akis["url"] in gorulen:
            continue
        gorulen.add(akis["url"])
        tekil.append(akis)
    # `sort` kararlı: aynı oynatıcının kopyaları sitenin sırasını korur.
    tekil.sort(key=lambda a: sira.get(a.get("player"), len(sira)))
    return tekil


def _bolum_coz(episode_id: Any) -> Tuple[str, int, int]:
    eslesme = _BOLUM_RE.match(str(episode_id or "").strip().strip("/").lower())
    if not eslesme:
        raise SeiCodeHatasi(
            f"SeiCode bölüm kimliği '<slug>/<sezon>/<bölüm>' biçiminde olmalı, "
            f"'{episode_id}' geçersiz.", status_code=400)
    return eslesme.group(1), int(eslesme.group(2)), int(eslesme.group(3))


def bolum_video_linkleri(veri: Dict[str, Any], sezon: int, bolum: int) -> Optional[Dict[str, Any]]:
    """Detay yanıtında bölümün `video_links`'i; bölüm yoksa None."""
    for s, bolumler in _sezonlar(veri):
        if s != sezon:
            continue
        for e, kayit in bolumler:
            if e == bolum:
                linkler = kayit.get("video_links")
                return linkler if isinstance(linkler, dict) else {}
    return None


def get_episode_streams(episode_id: str) -> List[Dict[str, Any]]:
    """Bölümün oynatılabilir akışları; sitede böyle bir bölüm yoksa [].

    ``[{"url", "label", "player", "fansub", "type"?, "referer"?, "user_agent"?}]``.
    Sibnet/SendVid/Vidmoly/GDrive/Mail.ru/Dailymotion adresleri gömme
    sayfası; yt-dlp açıyor. ok.ru ve tau-video burada doğrudan MP4'e
    çözülüyor; ok.ru adresi `user_agent`'a bağlı (bkz. `OKRU_UA`).
    Geçersiz kimlik, bilinmeyen anime ve ağ/engel hatası `SeiCodeHatasi`.
    """
    slug, sezon, bolum = _bolum_coz(episode_id)
    linkler = bolum_video_linkleri(_detay(slug), sezon, bolum)
    if linkler is None:
        return []
    return akislari_kur(linkler)


def izleme_adresi(episode_id: str) -> str:
    """Bölüm kimliği → sitedeki izleme sayfası (/anime/<slug>/<sezon>/<bölüm>).

    Adres bölüm nesnesinin kimliği gibi de kullanılıyor; tanınmayan kimlik de
    (kaçışlanarak) kendi adresini alır, farklı kimlikler aynı adrese çökmez.
    """
    kimlik = str(episode_id or "").strip().strip("/")
    return f"{BASE_URL}/anime/{quote(kimlik, safe='/')}"


__all__ = [
    "search_seicode",
    "search_seicode_zengin",
    "get_anime_episodes",
    "get_episode_streams",
    "izleme_adresi",
    "SeiCodeHatasi",
    "BASE_URL",
    "API_URL",
]
