"""AnimeTR kaynağı — https://animetr.co

Türkçe altyazılı anime + donghua sitesi (~1.930 seri: ~1.450 anime, ~470
donghua; Türkçe dublaj yok). Asya Animeleri'nin kardeş sitesi: örneklenen
her bölümün fansub'ı "Asyaanimeleri". Arka uç Laravel, önünde Cloudflare var
ama sınama (challenge) yok; giriş, captcha, ücretli üyelik gerekmiyor —
yalnızca beğeni/takip/yorum giriş istiyor ve onlara dokunmuyoruz. Düz
curl/requests ve curl_cffi 200 alıyor, 30 ardışık aramada hız sınırı
görülmedi (2026-09-24/30 ölçümleri).

Akış:

    Arama     GET /seriler?title=<q>[&page=N]  → HTML kartlar, EN YENİ ÜSTTE,
                                                sayfa başına 12, bütün eşleşmeler
              GET /search-series?q=<q>         → JSON [{title, en_title,
                                                url_title, url_en, poster…}];
                                                sitenin otomatik tamamlaması,
                                                en çok 15 kayıt, iç kimlik sırasıyla
    Bölümler  GET /seri/<slug>                 → bütün liste tek sayfada
                                                (One Piece: 1.150 satır, ~2 MB)
    Akışlar   GET /izle/<slug>/<bolum-x>       → <script id="epspage-data"> JSON:
                                                {fansubs: [{name, players: [{provider,
                                                embed_url, original_url}]}]}

NEDEN İKİ ARAMA UCU: JSON ucu en çok 15 kayıt veriyor ve iç kimliğe göre
sıralı — "one piece" için 15 filmi verip asıl diziyi atlıyor. Liste sayfası
bütün eşleşmeleri veriyor ama yeni→eski sıralı. Önce liste, eksik kalırsa
JSON'la tamamlanıyor (JSON İngilizce/diğer adlarla da eşleşiyor: "soul land"
→ douluo-dalu). Liste sayfası okunamazsa JSON tek başına da yetiyor.

Kimlikler:
    kaynak_id  /seri/<slug> içindeki slug ("sousou-no-frieren"). JSON'daki
               `url_en` de açılıyor ama TEKİL DEĞİL (iki Douluo Dalu sezonu da
               "soul-land"); bu yüzden `url_title` tercih ediliyor.
    bolum_id   "<seri>/<bolum-x>" ("one-piece/bolum-1162"). Donghua'larda ve
               bazı One Piece çift bölümlerinde ARALIK slug'ı var ("bolum-1-5"):
               tek video, beş bölüm. Aralığın içindeki numara ("bolum-3") 404.

Aynalar: sitenin kendi "VIP" oynatıcıları bugün çalışmıyor (asyaanimeleri.pw
Cloudflare 522, azelvid.com "Player domain is does not match",
asyaanim.upns.one şifreli P2P); her yeni bölümde 2-4 üçüncü taraf ayna var.
Yalnızca yt-dlp'nin açabildikleri tutuluyor, sitenin kayıt hataları
düzeltilerek (bkz. `embed_duzelt`). İki barındırıcı yt-dlp'ye ölü videoyu
"çalışıyor" gösteriyor (Sibnet'in silinmiş videosu, Sendvid'in yer tutucu
MP4'ü); onlar akış listesi kurulurken yoklanıp atılıyor (`_DENETCILER`).
Sitenin kendi duyurusu: eski bölümler telif yüzünden silindi — arşivin eski
kısmında (Naruto gibi) çoğu bölümün sağlam aynası kalmamış.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

# Kullanıcının "Erişimi aç"la geçtiği bot doğrulaması (çerez + tarayıcı
# kimliği); kayıt yoksa sarmalayıcı istekleri olduğu gibi geçirir.
from ..common import oturumlar

# Engel listeleri ortak: kendi kopyamızı tutarsak iki liste ayrışır ve
# "Just a moment" bir yerde engel, öbür yerde "sonuç yok" sayılır. `try`
# içinde, çünkü kaynak sunucu tarayıcısında da koşuyor ve orada `cf_bypass`
# yüklenemeyebilir (asyaanimeleri.py/deokwave.py de aynı deseni kullanıyor).
try:
    from ..common.cf_bypass import CHALLENGE_MARKERS, ENGEL_DURUMLARI
except ImportError:  # pragma: no cover
    ENGEL_DURUMLARI = frozenset({403, 429, 503})
    CHALLENGE_MARKERS = ("Just a moment", "Checking your browser", "challenge-platform")

log = logging.getLogger(__name__)

# Site DMCA baskısı altında (sayfadaki duyuru: "Eski bölümler telif yüzünden
# silindi"); alan adı taşınırsa sürüm beklemeden ortam değişkeniyle
# düzeltilebilsin (asyaanimeleri.py'de de aynısı var, o site bir kez taşındı).
ORTAM_ANAHTARI = "ANIMETR_URL"
BASE_URL = "https://animetr.co"
BASE_URL = ((os.environ.get(ORTAM_ANAHTARI) or "").strip() or BASE_URL).rstrip("/")

HTTP_TIMEOUT = 20
# Ölü ayna denetimi (bkz. `_sibnet_canli_mi`, `_sendvid_canli_mi`) ayrı ve
# kısa: bölüm başına bir-iki istek, akış listesini bekleyen kullanıcıyı
# oyalamamalı.
DENETIM_TIMEOUT = 10
# Liste araması en çok bu kadar sayfa gezer (sayfa başına 12 kart). Arama
# motorunun toplam bütçesi 25 sn; tek kaynak onu yememeli.
AZAMI_ARAMA_SAYFASI = 3

# Ölçümde hız sınırı görülmedi, ama site tek kişinin elinde ve tek sunucu;
# aynı süreçten art arda istekler arasında en az bu kadar bırakılıyor. Normal
# kullanımda (arama = 2 istek, bölüm listesi = 1, akış = 1) hissedilmiyor.
_MIN_INTERVAL = 1.0

# curl_cffi yoksa düz requests'e düşülüyor; o zaman tanıdık bir tarayıcı
# kimliği gönder. curl_cffi'de UA'yı impersonate ayarlıyor: elle ezmek TLS
# parmak izi ile başlığı birbirine düşürür.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class AnimeTRHatasi(RuntimeError):
    """AnimeTR okunamadı, istek engellendi ya da sayfa beklenen biçimde değil.

    Mesaj kullanıcıya gösterilecek Türkçe cümle. ``status_code`` sunucu
    tarayıcısının (`nezaket.hata_turu`) ve `common.hatalar`'ın sınıflandırması
    için: 403/429 engellenme, 404 kalıcı/"video yok", diğerleri geçici.
    "Sonuç yok" ile "okunamadı" ayrı şeyler; boş liste dönseydik arama motoru
    da tarayıcı da "bu animede bölüm yok" sanırdı.
    """

    def __init__(self, mesaj: str, status_code: Optional[int] = None):
        super().__init__(mesaj)
        self.status_code = status_code


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
# İş parçacığı başına bir oturum: arama motoru kaynakları paralel çağırıyor ve
# arayüz aynı anda bölüm listesi ile akış isteyebiliyor. curl_cffi oturumu
# eşzamanlı kullanıma karşı güvenli değil (tek curl tutamacı paylaşılıyor).
_yerel = threading.local()
_kilit = threading.Lock()
_son_istek = 0.0


def _yeni_oturum() -> Any:
    if _HAS_CURL:
        return oturumlar.oturumlu(_http.Session(impersonate="chrome131"))
    oturum = _http.Session()
    oturum.headers.update({"User-Agent": _UA})
    return oturumlar.oturumlu(oturum, curl=False)


def _oturum_al() -> Any:
    oturum = getattr(_yerel, "oturum", None)
    if oturum is None:
        oturum = _yeni_oturum()
        _yerel.oturum = oturum
    return oturum


def _engellendi_mi(yanit: Any) -> bool:
    """Yanıt sitenin kendisi değil bir koruma/engel sayfası mı?

    429 her zaman engel. 403/503'te gövdeye bakılır: Laravel'in bakım modu da
    503 veriyor ve o bir engel değil, geçici arıza (tarayıcı dinlendirmek
    yerine yeniden denemeli). 200 sayfalar da "challenge-platform" betiği
    taşıyabildiği için gövde yalnızca engel durum kodlarında okunuyor.
    """
    kod = getattr(yanit, "status_code", 0)
    if kod not in ENGEL_DURUMLARI:
        return False
    if kod == 429:
        return True
    bas = (getattr(yanit, "text", "") or "")[:6000]
    return any(iz in bas for iz in CHALLENGE_MARKERS)


def _cf_yedegi(url: str, basliklar: Dict[str, str]) -> Optional[Any]:
    """Cloudflare sınaması çıkarsa ortak CF zincirini (cloudscraper →
    FlareSolverr → QtWebEngine) bir kez dene; o da geçemezse None.

    Bugün gerek yok ama site Cloudflare arkasında; "Under Attack" açılırsa
    kaynak tamamen düşmesin. Tembel import: zincir ağır (Qt alt süreci) ve
    sunucu imajında hiç olmayabilir. ``url`` sorgu dizgesiyle TAM adres:
    zincirin FlareSolverr/Qt basamakları ``params`` almıyor.
    """
    try:
        from ..common.cf_bypass import get_cf_session  # pylint: disable=import-outside-toplevel
        yanit = get_cf_session().get(url, headers=basliklar, timeout=HTTP_TIMEOUT)
    except Exception as exc:  # pylint: disable=broad-except
        log.info("AnimeTR CF yedeği başarısız: %s", exc)
        return None
    if yanit is None or _engellendi_mi(yanit):
        return None
    return yanit


def _get(yol: str, *, params: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None) -> Any:
    """Siteye GET: istekler arasında aralık, engel ve ağ hatası AÇIK hatayla.

    Döndürülen yanıtın durum kodu 200 olmayabilir (404 gibi sitenin gerçek
    cevapları çağırana bırakılır: "bulunamadı" bağlama göre farklı söylenir).
    """
    global _son_istek
    adres = yol if yol.startswith("http") else BASE_URL + yol
    # Tarayıcı sayfaları sitenin kendi sayfasından açıyor; aynısını gönder.
    basliklar = {"Referer": BASE_URL + "/"}
    if headers:
        basliklar.update(headers)
    with _kilit:
        bekle = _MIN_INTERVAL - (time.monotonic() - _son_istek)
        if bekle > 0:
            time.sleep(bekle)
        _son_istek = time.monotonic()
    try:
        yanit = _oturum_al().get(adres, params=params, headers=basliklar,
                                 timeout=HTTP_TIMEOUT)
    except Exception as exc:  # ağ katmanı: zaman aşımı, DNS, TLS, bağlantı
        raise AnimeTRHatasi(f"AnimeTR'ye ulaşılamadı ({adres}): {exc}") from exc
    if _engellendi_mi(yanit):
        tam = f"{adres}?{urlencode(params)}" if params else adres
        yedek = _cf_yedegi(tam, basliklar)
        if yedek is not None:
            return yedek
        kod = getattr(yanit, "status_code", None)
        raise AnimeTRHatasi(
            f"AnimeTR isteği engelledi (HTTP {kod}, koruma sayfası); bir süre "
            "sonra yeniden deneyin.", status_code=kod)
    return yanit


def _metin(ham: str) -> str:
    """HTML parçasını düz metne indir: etiketleri at, varlıkları çöz, boşlukları topla."""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", ham or ""))).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
# Kart bağlantısı; alan adı serbest (ORTAM_ANAHTARI ile taşınabilir). Başlıktaki
# "Rastgele Seri" bağlantısı da /seri/'ye gidiyor ama class="anime-card"
# taşımıyor, desen onu zaten atlıyor. Sayfadaki JSON-LD ItemList yalnızca 10
# kayıt taşıyor (12 kartın ilk 10'u); kullanılmıyor.
_KART_RE = re.compile(
    r'<a\s+href="(?:https?://[^/"]+)?/seri/([^"/?#]+)/?"\s+class="anime-card"\s+'
    r'data-category="([^"]*)"\s+aria-label="([^"]*)"')
_KART_IMG_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.S)
_LISTE_IZI = re.compile(r"\d+\s+sonuç\s+bulundu|card-slider-section")
# Başlık araması yapılan liste sayfasının başlığı: '"frieren" Arama Sonuçları'.
# Filtresiz liste "Anime ve Donghua Listesi" diyor; site `title` parametresini
# bir gün yok sayarsa her sorguya en yeni 12 seriyi "sonuç" diye döndürmeyelim.
_ARAMA_BASLIGI = "Arama Sonuçları"


def _gorsel(adres: str) -> Optional[str]:
    """Kapak adresini mutlak yap: kart ve JSON bazen göreli yol veriyor
    ("images/series/1456_poster.webp" → /storage/ altında)."""
    adres = _html.unescape((adres or "").strip())
    if not adres or adres.startswith("data:"):
        return None
    if re.match(r"^https?://", adres):
        return adres
    if adres.startswith("/"):
        return BASE_URL + adres
    return urljoin(BASE_URL + "/storage/", adres)


def liste_ayristir(sayfa: str) -> Tuple[List[Dict[str, Any]], bool]:
    """/seriler sayfası → ([{"slug", "title", "image"}, ...], sonraki_sayfa_var_mi).

    Sayfa bir liste sayfası değilse (site düzeni değişmiş) `AnimeTRHatasi`:
    kart bulamamak "sonuç yok"la karışmasın. Boş sonuç sayfası da "0 sonuç
    bulundu" yazıyor, onunla ayırt ediliyor.
    """
    eslesmeler = list(_KART_RE.finditer(sayfa))
    if not eslesmeler and not _LISTE_IZI.search(sayfa):
        raise AnimeTRHatasi(
            "AnimeTR arama sayfası beklenen biçimde değil (sonuç listesi yok); "
            "site düzeni değişmiş olabilir.")
    sonuc: List[Dict[str, Any]] = []
    gorulen = set()
    for i, m in enumerate(eslesmeler):
        slug = m.group(1)
        if slug in gorulen:
            continue
        gorulen.add(slug)
        # Kapak kartın içindeki ilk <img>; sonraki kartın bağlantısına taşmasın.
        son = eslesmeler[i + 1].start() if i + 1 < len(eslesmeler) else len(sayfa)
        img = _KART_IMG_RE.search(sayfa, m.end(), min(son, m.end() + 3000))
        baslik = _html.unescape(m.group(3)).strip() or slug.replace("-", " ").title()
        sonuc.append({"slug": slug, "title": baslik,
                      "image": _gorsel(img.group(1)) if img else None})
    # Sonraki sayfa: sitenin kendi `rel="next"` bağlantısı (<head>'de ve
    # sayfalamada). Sayfa boyuna (12) güvenilmiyor: değişirse sessizce ilk
    # sayfada kalınırdı.
    return sonuc, 'rel="next"' in sayfa


def json_ayristir(veri: Any) -> List[Dict[str, Any]]:
    """/search-series yanıtı → [{"slug", "title", "image"}, ...].

    Slug `url_title` (tekil); yoksa `url_en` (sitede o da açılıyor ama iki
    seri aynı `url_en`'i paylaşabiliyor). Beklenmeyen biçim → `AnimeTRHatasi`.
    """
    if not isinstance(veri, list):
        raise AnimeTRHatasi("AnimeTR arama ucunun yanıtı beklenen biçimde değil "
                            "(liste bekleniyordu); site değişmiş olabilir.")
    sonuc: List[Dict[str, Any]] = []
    gorulen = set()
    for kayit in veri:
        if not isinstance(kayit, dict):
            continue
        slug = str(kayit.get("url_title") or kayit.get("url_en") or "").strip().strip("/")
        baslik = _html.unescape(str(kayit.get("title") or kayit.get("en_title") or "")).strip()
        if not slug or not baslik or slug in gorulen:
            continue
        gorulen.add(slug)
        sonuc.append({"slug": slug, "title": baslik,
                      "image": _gorsel(str(kayit.get("poster") or ""))})
    return sonuc


def _sirala(sorgu: str, kayitlar: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Liste sayfası yeni→eski sıralı: "naruto" için filmler asıl diziyle
    karışık geliyor. Tam eşleşme, önek, içerme, en son kısa ad önce (aynı
    öneki taşıyan adlardan en kısası neredeyse hep ana seri). `sorted`
    kararlı; eşit kalanlar sitenin sırasını korur. Arama motoru ayrıca
    `title_match` ile sıralıyor; bu, CLI ve sunucu tarayıcısı için.
    """
    q = sorgu.casefold()

    def anahtar(kayit: Dict[str, Any]):
        ad = kayit["title"].casefold()
        return (ad != q, not ad.startswith(q), q not in ad, len(ad))

    return sorted(kayitlar, key=anahtar)


def zengin_ara(sorgu: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Kapak görselli arama: ``[{"slug", "title", "image"}, ...]``.

    Görsel aynı kartta/JSON kaydında geliyor, ek istek yok. Sonuç yoksa ``[]``.
    Uçlardan biri okunamazsa diğeriyle devam edilir; İKİSİ de okunamazsa
    `AnimeTRHatasi` (arama sayfası "sonuç yok" yerine sebebi göstersin).
    """
    q = (sorgu or "").strip()
    # Site 2 karakterden kısa sorguya boş dönüyor (kendi JS'i de öyle).
    if len(q) < 2 or limit <= 0:
        return []
    sonuc: List[Dict[str, Any]] = []
    gorulen = set()
    hatalar: List[AnimeTRHatasi] = []
    okundu = False

    def ekle(kayitlar: List[Dict[str, Any]]) -> None:
        for kayit in kayitlar:
            if kayit["slug"] not in gorulen:
                gorulen.add(kayit["slug"])
                sonuc.append(kayit)

    # 1) Liste sayfası: bütün eşleşmeler, 12'şerli sayfalar.
    for sayfa_no in range(1, AZAMI_ARAMA_SAYFASI + 1):
        params: Dict[str, Any] = {"title": q}
        if sayfa_no > 1:
            params["page"] = sayfa_no
        try:
            yanit = _get("/seriler", params=params)
            if yanit.status_code != 200:
                raise AnimeTRHatasi(f"AnimeTR araması başarısız (HTTP {yanit.status_code}).",
                                    status_code=yanit.status_code)
            if _ARAMA_BASLIGI not in yanit.text:
                raise AnimeTRHatasi("AnimeTR liste sayfası aramayı yok saydı (sonuç "
                                    "başlığı yok); site değişmiş olabilir.")
            kartlar, sonraki = liste_ayristir(yanit.text)
        except AnimeTRHatasi as exc:
            hatalar.append(exc)
            break
        okundu = True
        ekle(kartlar)
        if not sonraki or not kartlar or len(sonuc) >= limit:
            break

    # 2) JSON otomatik tamamlama: İngilizce/diğer adlarla eşleşenler ve liste
    #    sayfası okunamadıysa tek başına.
    if len(sonuc) < limit:
        try:
            yanit = _get("/search-series", params={"q": q}, headers={
                "Accept": "application/json, text/plain, */*"})
            if yanit.status_code != 200:
                raise AnimeTRHatasi(
                    f"AnimeTR arama ucu HTTP {yanit.status_code} döndü.",
                    status_code=yanit.status_code)
            try:
                veri = json.loads(yanit.text)
            except ValueError as exc:
                raise AnimeTRHatasi(
                    "AnimeTR arama ucu JSON yerine beklenmeyen bir yanıt verdi "
                    "(site değişmiş olabilir).") from exc
            ekle(json_ayristir(veri))
            okundu = True
        except AnimeTRHatasi as exc:
            hatalar.append(exc)

    if not okundu and hatalar:
        raise hatalar[0]
    for exc in hatalar:
        log.info("AnimeTR arama ucu atlandı: %s", exc)
    return _sirala(q, sonuc)[:limit]


def search_animetr(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """AnimeTR'de ara → ``[(slug, başlık), ...]``; sonuç yoksa ``[]``."""
    return [(k["slug"], k["title"]) for k in zengin_ara(query, limit)]


# ─────────────────────────────────────────────────────────────────────────────
# Kimlikler
# ─────────────────────────────────────────────────────────────────────────────
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~%-]*$")
_BOLUM_PARCA_RE = re.compile(r"^bolum-[A-Za-z0-9.-]+$")


def _yol(kimlik: str) -> str:
    """Kimliği site köküne göre yola çevir; tam adres verilmişse yolunu al.

    Başka bir konağın adresi kabul EDİLMEZ: kimlik yalnızca bu sitenin
    sayfasını gösterebilir (elle girilen ya da eski bir kayıttan gelen adres).
    """
    deger = str(kimlik or "").strip()
    if re.match(r"^https?://", deger, re.I):
        parca = urlsplit(deger)
        site = (urlsplit(BASE_URL).hostname or "").lower()
        konak = (parca.hostname or "").lower()
        if konak not in (site, "www." + site):
            raise AnimeTRHatasi(f"'{kimlik}' bir AnimeTR adresi değil ({konak}).",
                                status_code=400)
        deger = parca.path
    return deger.strip("/")


def _seri_slugu(kimlik: str) -> str:
    deger = _yol(kimlik)
    if deger.startswith("seri/"):
        deger = deger[len("seri/"):]
    if not _SLUG_RE.match(deger):
        raise AnimeTRHatasi(f"AnimeTR seri kimliği geçersiz: '{kimlik}'.", status_code=400)
    return deger


def _bolum_coz(kimlik: str) -> Tuple[str, str]:
    """"one-piece/bolum-1162" (ya da tam /izle/ adresi) → ("one-piece", "bolum-1162")."""
    deger = _yol(kimlik)
    if deger.startswith("izle/"):
        deger = deger[len("izle/"):]
    parcalar = deger.split("/")
    if (len(parcalar) != 2 or not _SLUG_RE.match(parcalar[0])
            or not _BOLUM_PARCA_RE.match(parcalar[1])):
        raise AnimeTRHatasi(
            f"AnimeTR bölüm kimliği '<seri>/bolum-<n>' biçiminde olmalı, "
            f"'{kimlik}' geçersiz.", status_code=400)
    return parcalar[0], parcalar[1]


def bolum_adresi(bolum_id: str) -> str:
    """Bölüm kimliğinden izleme sayfasının adresi (bölüm nesnesinin url'si)."""
    try:
        seri, bolum = _bolum_coz(bolum_id)
    except AnimeTRHatasi:
        # Adres bölüm nesnesinin kimliği gibi de kullanılıyor; tanınmayan
        # kimlikler aynı adrese çökmesin.
        return f"{BASE_URL}/izle/{quote(str(bolum_id or '').strip('/'), safe='/')}"
    return f"{BASE_URL}/izle/{seri}/{bolum}"


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
# Satırın başlık bağlantısı: içinde h5 (bölüm adı; örneklerde hep boş) ve
# "Bölüm No: N" / "Bölüm No: A - B". Aynı satırdaki "İzle" düğmesi de aynı
# adrese gidiyor ama h5/small taşımıyor; desen yalnızca başlık bağlantısını alır.
_BOLUM_RE = re.compile(
    r'<a\s+href="(?:https?://[^/"]+)?/izle/([^"/?#]+)/(bolum-[^"/?#]+)"[^>]*>\s*'
    r'<span\s+class="h5[^"]*">(.*?)</span>\s*'
    r'<small\s+class="text-muted">(.*?)</small>', re.S)
_BOLUM_NO_RE = re.compile(r"(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)")
_BOLUM_BASLIGI_IZI = re.compile(r"Bölümler\s*\(")


def _numara(bolum_no: str, bolum: str) -> str:
    """"Bölüm No: 1 - 5" → "1-5"; yazı yoksa slug'dan ("bolum-1-5" → "1-5").

    Aralık etiketi Asya Animeleri'ninkiyle aynı biçimde ("1-5. Bölüm"):
    iki kardeş sitenin bölümleri çok kaynaklı birleştirmede aynı satıra düşsün.
    """
    m = _BOLUM_NO_RE.search(_metin(bolum_no).replace("Bölüm No:", " "))
    if not m:
        m = _BOLUM_NO_RE.search(bolum[len("bolum-"):].replace("-", " - "))
    return re.sub(r"\s*-\s*", "-", m.group(1)) if m else ""


def _ilk_sayi(numara: str) -> Optional[float]:
    m = re.match(r"\d+(?:\.\d+)?", numara or "")
    return float(m.group(0)) if m else None


def bolumleri_ayristir(sayfa: str) -> List[Tuple[str, str]]:
    """Seri sayfası → izleme sırasıyla ``[(bolum_id, başlık), ...]``.

    Bölüm kimliğindeki seri slug'ı SATIRDAN alınır: seri `url_en` takma
    adıyla açılsa da satırlar kanonik slug'ı taşıyor. Aynı satır iki kez
    olabiliyor (one-piece/bolum-5), ayıklanır. Satır yoksa ama "Bölümler (…)"
    başlığı varsa seri gerçekten boş; başlık da yoksa site düzeni değişmiştir
    (`AnimeTRHatasi`, "0 bölüm" diye gizlenmez).
    """
    satirlar: List[Tuple[str, str, Optional[float]]] = []
    gorulen = set()
    for seri, bolum, ad, bolum_no in _BOLUM_RE.findall(sayfa):
        bolum_id = f"{seri}/{bolum}"
        if bolum_id in gorulen:
            continue
        gorulen.add(bolum_id)
        numara = _numara(bolum_no, bolum)
        etiket = f"{numara}. Bölüm" if numara else bolum
        ad = _metin(ad)
        if ad:
            etiket += f" - {ad}"
        satirlar.append((bolum_id, etiket, _ilk_sayi(numara)))
    if not satirlar:
        if _BOLUM_BASLIGI_IZI.search(sayfa):
            return []
        raise AnimeTRHatasi(
            "AnimeTR seri sayfasında bölüm listesi bulunamadı; site düzeni "
            "değişmiş olabilir.")
    # Site eskiden yeniye veriyor; bir gün tersine dönerse izleme sırası bozulmasın.
    ilk, son = satirlar[0][2], satirlar[-1][2]
    if ilk is not None and son is not None and ilk > son:
        satirlar.reverse()
    return [(bolum_id, etiket) for bolum_id, etiket, _ in satirlar]


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Serinin bölümleri, izleme sırasıyla ``[("<seri>/bolum-<n>", başlık), ...]``.

    Seride henüz bölüm yoksa ``[]``. Seri bulunamazsa (HTTP 404), site
    okunamazsa ya da sayfa tanınmazsa `AnimeTRHatasi`.
    """
    seri = _seri_slugu(slug)
    yanit = _get(f"/seri/{seri}")
    if yanit.status_code == 404:
        raise AnimeTRHatasi(f"AnimeTR'de '{seri}' serisi bulunamadı (HTTP 404).",
                            status_code=404)
    if yanit.status_code != 200:
        raise AnimeTRHatasi(
            f"AnimeTR '{seri}' serisinin sayfası açılamadı (HTTP {yanit.status_code}).",
            status_code=yanit.status_code)
    return bolumleri_ayristir(yanit.text)


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
_VERI_RE = re.compile(
    r'<script\b[^>]*\bid="epspage-data"[^>]*>(.*?)</script>', re.S | re.I)

# Denenme sırası: bu SİTEDE ölçülen başarıya göre, ortak
# `common.oynatici_onceligi` sırasından farklı. 28 bölümlük tarama (yt-dlp
# 2026.08.19, 2026-09-24) + 2026-09-30 kontrolü:
#   SIBNET  13/19; kalan 6'sı ölü video — ölüler `_sibnet_canli_mi` ile
#           atıldığından listede kalan Sibnet çalışıyor. mp4, hızlı.
#   VK       8/8 (+2/2), 1080p'ye kadar.
#   GDRIVE   9/25; çoğu silinmiş (404) ya da kota — ama hata hızlı (<1 sn).
#   VIDMOLY 12/19; HLS, yt-dlp'nin genel çıkarıcısıyla.
#   SENDVID  ilk taramada 4/4 göründü (yalnızca ilk baytlara bakılmıştı);
#           2026-09-30'da 3/3'ü yer tutucuydu — `_sendvid_canli_mi` atıyor,
#           kalan gerçek video. MAIL 1/1 (tek örnek).
#   DAILYMOTION 0/3 ("Not found").
#   ODNOKLASSNIKI: yt-dlp 2026.08.19 ve 2026.09.16 gecelik sürüm HER ok.ru
#           videosunda çöküyor ("the JSON object must be str … not dict");
#           çıkarıcı düzelince çalışır (yamalı yt-dlp'yle 4/14), o zamana
#           kadar yalnızca başka hiçbiri kalmazsa denensin diye en sonda.
# Ortak sırada ODNOKLASSNIKI ve DAILYMOTION üçüncü/dördüncü: tipik bir bölümde
# `best_video` önce iki kesin başarısız adayı yoklardı.
_ONCELIK: Tuple[str, ...] = (
    "SIBNET", "VK", "GDRIVE", "VIDMOLY", "SENDVID", "MAIL", "DAILYMOTION",
    "ODNOKLASSNIKI",
)
_SIRA = {ad: i for i, ad in enumerate(_ONCELIK)}

# Konak → ortak oynatıcı adı (`common/oynatici_onceligi.DESTEKLENEN_OYNATICILAR`).
# Bilinçli bir İZİN listesi; dışarıda kalanlar ve sebepleri (ölçüm 2026-09):
#   asyaanimeleri.pw ("Vip", FirePlayer) → Cloudflare 522, köken kapalı
#   azelvid.com ("AnimeTR VIP", FirePlayer) → "Player domain is does not match"
#   asyaanim.upns.one ("AsyaAnim") → şifreli P2P oynatıcı, yt-dlp desteklemiyor
#   rumble, voe (403), filemoon (yt-dlp "piracy" reddi), vidoza (404),
#   hdvid/vidthehd (vekil 502), mp4upload (genel çıkarıcıda sahte "çalışıyor"),
#   abyssplayer, gdplayer.to ("GDrive Player"), puterin, playtube, mystream,
#   vidoo, segavid, cloudvideo, nxload, short.icu/ink, luluvid, rpmvip
#   → yt-dlp desteklemiyor ya da ölü
# `best_video` yalnızca ilk birkaç adayı yokluyor; ölü aday saniyelere mal oluyor.
_KONAKLAR: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"(?:^|\.)sibnet\.ru$"), "SIBNET"),
    (re.compile(r"(?:^|\.)drive\.google\.com$"), "GDRIVE"),
    (re.compile(r"(?:^|\.)vidmoly\.(?:net|me|to|biz|org)$"), "VIDMOLY"),
    (re.compile(r"(?:^|\.)(?:ok|odnoklassniki)\.ru$"), "ODNOKLASSNIKI"),
    (re.compile(r"(?:^|\.)(?:vk\.com|vk\.ru|vkvideo\.ru)$"), "VK"),
    (re.compile(r"(?:^|\.)(?:dailymotion\.com|dai\.ly)$"), "DAILYMOTION"),
    (re.compile(r"(?:^|\.)sendvid\.com$"), "SENDVID"),
    (re.compile(r"(?:^|\.)my\.mail\.ru$"), "MAIL"),
)


def _mutlak(adres: str) -> str:
    adres = _html.unescape((adres or "").strip()).replace("\\/", "/")
    if adres.startswith("//"):
        adres = "https:" + adres
    return adres


def _konak(adres: str) -> str:
    try:
        return (urlsplit(adres).hostname or "").lower()
    except ValueError:
        return ""


def _oynatici(*adresler: str) -> Optional[str]:
    """Adreslerden ilk tanınan konağın oynatıcı adı (önce embed, sonra orijinal)."""
    for adres in adresler:
        konak = _konak(adres)
        for desen, ad in _KONAKLAR:
            if konak and desen.search(konak):
                return ad
    return None


def _ilk(desen: str, *metinler: str) -> Optional[str]:
    for metin in metinler:
        m = re.search(desen, metin or "")
        if m:
            return m.group(1)
    return None


def embed_duzelt(saglayici: str, embed: str, orijinal: str) -> Optional[Tuple[str, str]]:
    """Sitenin ayna kaydı → (yt-dlp'nin açabildiği adres, oynatıcı adı); yoksa None.

    ``saglayici`` sitenin yazdığı ad ("Sibnet", "VK Video", "OK.ru"); yalnızca
    bilgi, karar KONAKTAN veriliyor (aynı servis farklı adlarla geçiyor).
    Sitenin kayıt hataları burada düzeltiliyor, her biri ölçüldü:

    * Sibnet: videoid'e başlık yapışmış ("4850279-_AoiSubs__One_Piece___01_");
      baştaki rakamlar alınıyor.
    * Google Drive: "/file/u/0/d/<id>/view" yt-dlp'de "Unsupported URL";
      "/file/d/<id>/view"e çevriliyor.
    * Vidmoly: SİTE HATASI — "vidmoly.me/v/<id>" kaydı embed olarak
      "vidmoly.net/embed-v.html" (404) diye saklanmış; kimlik orijinal
      adresten (/v/ ya da /w/) alınıp "embed-<id>.html" kuruluyor.
    * VK: "video_ext.php?oid=…&id=…" gömme adresinde yt-dlp "Unable to extract
      player params" veriyor; "vkvideo.ru/video<oid>_<id>" çalışıyor (vk.com
      kayıtları da oraya).
    * Dailymotion: gömme biçiminde "No video formats"; sayfa adresi kullanılıyor.
    * Mail.ru: gömme biçimi (/video/embed/<n>) 404; orijinal sayfa adresi çalışıyor.
    """
    del saglayici  # yalnızca belge: bkz. yukarı
    embed, orijinal = _mutlak(embed), _mutlak(orijinal)
    oynatici = _oynatici(embed, orijinal)
    if oynatici == "SIBNET":
        sorgu = parse_qs(urlsplit(embed).query).get("videoid") or [""]
        vid = _ilk(r"^(\d+)", sorgu[0]) or _ilk(r"/video(\d+)", orijinal, embed)
        return (f"https://video.sibnet.ru/shell.php?videoid={vid}", oynatici) if vid else None
    if oynatici == "GDRIVE":
        fid = _ilk(r"/d/([A-Za-z0-9_-]{20,})", embed, orijinal) \
            or _ilk(r"[?&]id=([A-Za-z0-9_-]{20,})", embed, orijinal)
        return (f"https://drive.google.com/file/d/{fid}/view", oynatici) if fid else None
    if oynatici == "VIDMOLY":
        vid = _ilk(r"vidmoly\.[a-z]+/(?:v|w|e)/([A-Za-z0-9]{6,})", orijinal, embed) \
            or _ilk(r"/embed-([A-Za-z0-9]{6,})\.html", embed, orijinal)
        return (f"https://vidmoly.net/embed-{vid}.html", oynatici) if vid else None
    if oynatici == "ODNOKLASSNIKI":
        vid = _ilk(r"/video(?:embed)?/(\d+)", embed, orijinal)
        return (f"https://ok.ru/videoembed/{vid}", oynatici) if vid else None
    if oynatici == "VK":
        m = re.search(r"video(-?\d+)_(\d+)", orijinal) or re.search(r"video(-?\d+)_(\d+)", embed)
        if m:
            return f"https://vkvideo.ru/video{m.group(1)}_{m.group(2)}", oynatici
        sorgu = parse_qs(urlsplit(embed).query)
        oid, vid = (sorgu.get("oid") or [""])[0], (sorgu.get("id") or [""])[0]
        if re.match(r"^-?\d+$", oid) and vid.isdigit():
            return f"https://vkvideo.ru/video{oid}_{vid}", oynatici
        return None
    if oynatici == "DAILYMOTION":
        vid = _ilk(r"dailymotion\.com/(?:embed/)?video/([A-Za-z0-9]+)", orijinal, embed) \
            or _ilk(r"dai\.ly/([A-Za-z0-9]+)", orijinal, embed) \
            or _ilk(r"[?&]video=([A-Za-z0-9]+)", embed, orijinal)
        return (f"https://www.dailymotion.com/video/{vid}", oynatici) if vid else None
    if oynatici == "SENDVID":
        vid = _ilk(r"sendvid\.com/(?:embed/)?([A-Za-z0-9]+)", embed, orijinal)
        return (f"https://sendvid.com/embed/{vid}", oynatici) if vid else None
    if oynatici == "MAIL":
        for adres in (orijinal, embed):
            if (_konak(adres).endswith("my.mail.ru") and "/video/embed/" not in adres
                    and re.search(r"/video/.+\.html$", urlsplit(adres).path)):
                return re.sub(r"^http://", "https://", adres), oynatici
        return None
    return None


def sayfa_verisi(sayfa: str) -> Dict[str, Any]:
    """Bölüm sayfasındaki `<script id="epspage-data">` JSON'u.

    Aynı adresler `div.player-frame` içindeki iframe'lerde de var, ama JSON
    sağlayıcı adını ve orijinal adresi (bozuk embed'leri düzeltmek için şart)
    da taşıyor. Yoksa ya da bozuksa site düzeni değişmiştir: `AnimeTRHatasi`.
    """
    m = _VERI_RE.search(sayfa or "")
    if not m:
        raise AnimeTRHatasi(
            "AnimeTR bölüm sayfasında oynatıcı verisi (epspage-data) bulunamadı; "
            "site düzeni değişmiş olabilir.")
    try:
        veri = json.loads(m.group(1))
    except ValueError as exc:
        raise AnimeTRHatasi("AnimeTR bölüm sayfasındaki oynatıcı verisi okunamadı "
                            "(bozuk JSON); site düzeni değişmiş olabilir.") from exc
    if not isinstance(veri, dict):
        raise AnimeTRHatasi("AnimeTR bölüm sayfasındaki oynatıcı verisi beklenen "
                            "biçimde değil.")
    return veri


def akislari_ayristir(sayfa: str) -> List[Dict[str, str]]:
    """Bölüm sayfası → oynatılabilir akışlar, `_ONCELIK` sırasıyla.

    ``[{"url", "label", "player", "type": "iframe", "fansub"}, ...]``.

    ``referer`` BİLEREK yok: aynaların hepsi yt-dlp'de kendi biçim başlığını
    kuruyor (Sibnet'in MP4'ü Referer = shell adresi istiyor, animetr.co ya
    da boş Referer'la 403). Akışa "referer" konursa mpv `--referrer` olarak da
    geçiyor ve ytdl_hook'un biçim başlığıyla çakışabiliyor; hiçbir ayna
    animetr.co'yu görmek istemiyor (ölçüldü).
    """
    veri = sayfa_verisi(sayfa)
    adaylar: List[Tuple[int, int, int, Dict[str, str]]] = []
    gorulen = set()
    atilan: List[str] = []
    for fi, fansub in enumerate(veri.get("fansubs") or []):
        if not isinstance(fansub, dict):
            continue
        grup = _metin(str(fansub.get("name") or "")) or "AnimeTR"
        for pi, oynatici_kaydi in enumerate(fansub.get("players") or []):
            if not isinstance(oynatici_kaydi, dict):
                continue
            saglayici = _metin(str(oynatici_kaydi.get("provider") or ""))
            embed = str(oynatici_kaydi.get("embed_url") or "")
            orijinal = str(oynatici_kaydi.get("original_url") or "")
            duzelen = embed_duzelt(saglayici, embed, orijinal)
            if duzelen is None:
                atilan.append(saglayici or _konak(_mutlak(embed)) or "?")
                continue
            adres, oynatici = duzelen
            if adres in gorulen:
                continue            # aynı ayna iki kez kayıtlı (Vidmoly'de sık)
            gorulen.add(adres)
            adaylar.append((_SIRA.get(oynatici, len(_SIRA)), fi, pi, {
                "url": adres,
                "label": f"{grup} - {saglayici or oynatici.title()}",
                "player": oynatici,
                # Gömülü oynatıcı sayfası: yt-dlp/mpv çözüyor.
                "type": "iframe",
                "fansub": grup,
            }))
    if atilan:
        log.info("AnimeTR: oynatılamayan aynalar atlandı: %s", ", ".join(atilan))
    adaylar.sort(key=lambda a: a[:3])
    return [akis for *_sira, akis in adaylar]


# Canlı shell sayfası: `player.src([{src: "/v/<hash>/<id>.mp4", …}])`.
_SIBNET_MP4_RE = re.compile(r"""["'](/v/[^"'\s]+\.mp4)["']""")


def _sibnet_canli_mi(adres: str) -> bool:
    """Sibnet videosu silinmemiş mi?

    Silinen videonun shell sayfası da 200 dönüyor ("Ошибка обработки видео"),
    yt-dlp'nin genel çıkarıcısı onda "shell.php?videoid=" adresini ext=php
    diye "buluyor": `AdapterVideo.is_working` True diyor, oynatıcı boş açılıyor.
    Ölçümde 19 Sibnet aynasının 6'sı böyleydi. Yalnızca KESİN ölüm atılır
    (200 + MP4 yolu yok); ağ hatasında ya da başka durumda karar yt-dlp'nin —
    o da açamazsa aday zaten "çalışmıyor" sayılır, sahte olumlu olmaz.
    """
    try:
        yanit = _oturum_al().get(adres, headers={"Referer": BASE_URL + "/"},
                                 timeout=DENETIM_TIMEOUT)
    except Exception as exc:  # pylint: disable=broad-except
        log.info("AnimeTR: Sibnet denetlenemedi (%s): %s", adres, exc)
        return True
    if getattr(yanit, "status_code", 0) != 200:
        return True
    return bool(_SIBNET_MP4_RE.search(getattr(yanit, "text", "") or ""))


_SENDVID_KAYNAK_RE = re.compile(r'<source\b[^>]*\bsrc="([^"]+)"', re.I)
# Yer tutucu 5 sn'lik 480p bir MP4 (36.789 bayt); 24 dakikalık bir bölüm en
# düşük kalitede bile onlarca MB. Arada geniş pay var.
_SENDVID_ASGARI_BAYT = 1024 * 1024


def _sendvid_canli_mi(adres: str) -> bool:
    """Sendvid videosu gerçek mi, "This video is temporarily unavailable" mı?

    Sendvid erişilemeyen videoyu HTTP 200 ile, aynı adreste 5 sn'lik bir yer
    tutucu MP4 olarak veriyor; yt-dlp (html5 çıkarıcı) ve ilk baytlar
    ("ftyp") gerçek video gibi görünüyor. 2026-09-30'da AnimeTR'deki 3/3
    Sendvid aynası böyleydi. Yer tutucunun kime gösterildiği (bütün
    izleyicilere mi, bazı IP'lere mi) bilinmiyor; denetim oynatacak makinede
    koştuğu için ikisinde de doğru sonuç verir. Gövde indirilmez: embed
    sayfasındaki <source> adresine HEAD ve Content-Length.

    Yalnızca KESİN yer tutucu (boyut eşiğin altında) atılır; ağ hatasında,
    HEAD desteklenmiyorsa ya da boyut yoksa karar yt-dlp'nin.
    """
    try:
        sayfa = _oturum_al().get(adres, headers={"Referer": BASE_URL + "/"},
                                 timeout=DENETIM_TIMEOUT)
        m = _SENDVID_KAYNAK_RE.search(getattr(sayfa, "text", "") or "")
        if getattr(sayfa, "status_code", 0) != 200 or not m:
            return True
        video = _oturum_al().head(_html.unescape(m.group(1)), headers={"Referer": adres},
                                  timeout=DENETIM_TIMEOUT, allow_redirects=True)
    except Exception as exc:  # pylint: disable=broad-except
        log.info("AnimeTR: Sendvid denetlenemedi (%s): %s", adres, exc)
        return True
    if getattr(video, "status_code", 0) != 200:
        return True
    try:
        boyut = int((getattr(video, "headers", None) or {}).get("content-length") or -1)
    except (TypeError, ValueError):
        return True
    return boyut < 0 or boyut >= _SENDVID_ASGARI_BAYT


# Sahte "çalışıyor" veren aynaların denetimi: oynatıcı adı → yoklayıcı.
_DENETCILER = {
    "SIBNET": _sibnet_canli_mi,
    "SENDVID": _sendvid_canli_mi,
}


def get_episode_streams(episode_id: str, dogrula: bool = True) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları; aynaların hiçbiri açılamıyorsa ``[]``.

    ``dogrula``: yt-dlp'yi kandıran aynalar (Sibnet'in silinmiş videosu,
    Sendvid'in yer tutucusu) yoklanıp ölüler atılır — yoksa `best_video`
    onları "çalışıyor" sayıp sıradaki sağlam aynaya hiç geçmezdi. Bölüm
    bulunamazsa (HTTP 404), site okunamazsa ya da sayfa tanınmazsa
    `AnimeTRHatasi`.
    """
    seri, bolum = _bolum_coz(episode_id)
    yanit = _get(f"/izle/{seri}/{bolum}")
    if yanit.status_code == 404:
        raise AnimeTRHatasi(f"AnimeTR'de '{seri}/{bolum}' bölümü bulunamadı (HTTP 404).",
                            status_code=404)
    if yanit.status_code != 200:
        raise AnimeTRHatasi(
            f"AnimeTR '{seri}/{bolum}' bölüm sayfası açılamadı (HTTP {yanit.status_code}).",
            status_code=yanit.status_code)
    akislar = akislari_ayristir(yanit.text)
    if dogrula:
        akislar = [a for a in akislar
                   if a["player"] not in _DENETCILER or _DENETCILER[a["player"]](a["url"])]
    return akislar


__all__ = [
    "AnimeTRHatasi",
    "BASE_URL",
    "ORTAM_ANAHTARI",
    "bolum_adresi",
    "get_anime_episodes",
    "get_episode_streams",
    "search_animetr",
    "zengin_ara",
]
