"""
Deokwave kaynağı — https://deokwave.com

Türk fansub gruplarının (HolySubs, ShiroSubs, YukiSubs, Adonis...) çevirilerini
tek sayfada toplayan bir PHP sitesi. Aynı bölümün birden çok grubu var; her
grubun videosu Türkçe altyazısı görüntüye gömülü (hardsub) doğrudan MP4.
Giriş, tarayıcı ya da JS çözümü gerekmiyor (2026-09-24'te ölçüldü).

Akış:
- Arama:    GET /api/v1/animes/search/?q=<q>&page=<n>
            -> {"success", "animes": [{animeid, name, type, ...}], "total_pages"}
            Yedek: GET /search_api.php?q=<q> (sitenin kendi otomatik
            tamamlaması; ~10 sonuç, "users" anahtarı üye profilleri).
- Bölümler: GET /anime/<ANIMEID>/   (sondaki "/" şart; yoksa 404)
            HTML'de tek satırlık bir JSON değişmezi: `var allSezonlar = {...};`
            dict: {"<sezon>": {"<bölüm>": {...}}}   → dizi
            list: [[{...}]]                         → film / tek parça (0/0)
            []                                      → henüz bölüm yok
- Akışlar:  GET /watch/video-info/?animeid=<ID>&season=<s>&episode=<e>
            -> {"success", "videoid", "qualities", "fansubs": [{key, name,
               extra, videoid, qualities, hasSub, isSibnet}]}
            GET /api/v1/video/token/ -> {"token": "<64 hex>"}
            Video: https://sw2.deokwave.com/v/<videoid>/<1080|720|480>/?vt=<token>
            Referer https://deokwave.com/ ŞART (yoksa 404 "Access is denied").

Kimlikler:
- Kaynak kimliği: 7 haneli büyük harf onaltılık ("0C61BB4").
- Bölüm kimliği: "<ID>/<sezon>/<bölüm>" — kendi başına yeter, akış isteği
  ikinci bir sayfa okumadan kurulur. Film: "<ID>/0/0" (site böyle soruyor).
"""
from __future__ import annotations

import html as _html
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

try:
    # Engel izlerinin tek listesi istemcide; ayrı bir kopya tutmak ayrışmaya
    # yol açıyor (bkz. ANIME_PROVIDER_GUIDE.md, "Engeli sessizce yutma").
    from ..common.cf_bypass import CHALLENGE_MARKERS as _CF_IZLERI
    from ..common.cf_bypass import ENGEL_DURUMLARI as _ENGEL_DURUMLARI
except Exception:  # pragma: no cover - sunucu tek başına da çalışabilmeli
    _CF_IZLERI = ("Just a moment", "cf-browser-verification", "challenge-platform")
    _ENGEL_DURUMLARI = frozenset({403, 429, 503})


BASE_URL = "https://deokwave.com"
STREAM_BASE = "https://sw2.deokwave.com"
REFERER = BASE_URL + "/"
HTTP_TIMEOUT = 15

# Sitenin kaynak sunucusu (LiteSpeed, Cloudflare arkasında) aynı IP + istemci
# parmak izine kısa süreli kısıt koyuyor: ~20 dk'da ~100 istek (saniye altı
# patlamalar dahil) sonrası o parmak izi 1-3 dk boyunca 403 alıyor; bazen de
# tek tek isteklere "Bot Verification" (LiteSpeed reCAPTCHA) sayfası dönüyor.
# O sırada başka bir parmak izi geçiyor. Saniyede 1 istek 45/45 geçti; bu
# yüzden istekler arasında en az 1 sn bırakılıyor ve engelde profil
# değiştirilip yeniden deneniyor.
_MIN_INTERVAL = 1.0
_PROFILES = ("chrome131", "safari17_0", "chrome124", "firefox133")
_SITE_ENGEL_IZLERI = (
    "Access to this resource on the server is denied",   # LiteSpeed 403
    "Bot Verification",                                  # LiteSpeed reCAPTCHA
    "lsrecaptcha",
)
_ENGEL_IZLERI = tuple(dict.fromkeys((*_SITE_ENGEL_IZLERI, *_CF_IZLERI)))
_YEDEK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Oynatma anahtarı GLOBAL (IP'ye bağlı değil) ve zamanla dönüyor: bir kez en
# az 25 dk değişmedi, bir kez de 05:00 UTC civarında değişti; bir önceki
# anahtar değişimden sonra da bir süre kabul ediliyordu, 10 saatlik anahtar
# ise 401 veriyordu (2026-09-24/25 ölçümleri). 5 dk'dan uzun saklanmaz; her
# oynatmada taze akış listesi zaten yeniden isteniyor. Aynı sebeple sunucu
# tarayıcısının arşive yazdığı Deokwave adresleri saatler içinde eskir.
_TOKEN_TTL = 5 * 60

_ID_RE = re.compile(r"^[0-9A-Fa-f]{7}$")
_BOLUM_RE = re.compile(r"^([0-9A-Fa-f]{7})/(\d{1,4})/(\d{1,5})$")
_VIDEO_ID_RE = re.compile(r"^[0-9a-z]{16,64}$")
_TOKEN_RE = re.compile(r"^[0-9A-Za-z_.~-]{16,256}$")
_VT_RE = re.compile(r"__VT__\s*=\s*['\"]([0-9A-Za-z_.~-]{16,256})['\"]")
_SEZON_ISARETI = "var allSezonlar = "

_kilit = threading.Lock()
_son_istek = 0.0
_profil_sirasi = 0
_oturum = None
_token: Optional[str] = None
_token_zamani = 0.0


class DeokwaveHatasi(RuntimeError):
    """Deokwave'e ulaşılamadı ya da yanıt beklenen biçimde değil.

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
def _yeni_oturum(profil: str):
    if _HAS_CURL:
        return _http.Session(impersonate=profil)
    oturum = _http.Session()          # curl_cffi yoksa düz requests de 200 alıyor
    oturum.headers["User-Agent"] = _YEDEK_UA
    return oturum


def _oturum_al():
    global _oturum
    if _oturum is None:
        _oturum = _yeni_oturum(_PROFILES[_profil_sirasi])
    return _oturum


def _profil_degistir() -> None:
    global _oturum, _profil_sirasi
    _profil_sirasi = (_profil_sirasi + 1) % len(_PROFILES)
    _oturum = _yeni_oturum(_PROFILES[_profil_sirasi])


def _engellendi_mi(yanit) -> bool:
    """Yanıt gerçek içerik değil, sitenin/CF'in engel sayfası mı?

    Yalnızca engel durum kodlarında (403/429/503) gövdeye bakılır: normal bir
    anime sayfası da "challenge-platform" gibi dizgeler taşıyabilir.
    """
    if yanit.status_code not in _ENGEL_DURUMLARI:
        return False
    if yanit.status_code == 429:
        return True
    bas = (yanit.text or "")[:6000]
    return any(iz in bas for iz in _ENGEL_IZLERI)


def _get(yol: str, *, params: Optional[Dict[str, Any]] = None,
         headers: Optional[Dict[str, str]] = None):
    """Kısıta dayanıklı GET: istekler arasında aralık, engelde profil değiştir.

    Ağ hatası ve bütün profillerin engellenmesi `DeokwaveHatasi` olarak
    yükselir; diğer HTTP durumlarında yanıt olduğu gibi döner (çağıran
    yorumlar).
    """
    global _son_istek
    adres = yol if yol.startswith("http") else BASE_URL + yol
    basliklar = {"Referer": REFERER}
    if headers:
        basliklar.update(headers)
    yanit = None
    for _ in range(len(_PROFILES)):
        with _kilit:
            bekle = _MIN_INTERVAL - (time.monotonic() - _son_istek)
            if bekle > 0:
                time.sleep(bekle)
            _son_istek = time.monotonic()
            oturum = _oturum_al()
        try:
            yanit = oturum.get(adres, params=params, headers=basliklar,
                               timeout=HTTP_TIMEOUT)
        except Exception as hata:
            raise DeokwaveHatasi(f"Deokwave'e bağlanılamadı: {hata}") from hata
        if not _engellendi_mi(yanit):
            return yanit
        with _kilit:
            _profil_degistir()
    kod = getattr(yanit, "status_code", 403)
    raise DeokwaveHatasi(
        f"Deokwave istekleri geçici olarak engelledi (HTTP {kod}, bot doğrulaması); "
        "birkaç dakika sonra yeniden deneyin.", status_code=kod)


def _json(yol: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """JSON uç çağrısı; 200 dışı yanıt ya da bozuk JSON `DeokwaveHatasi`."""
    yanit = _get(yol, params=params, headers={
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    if yanit.status_code != 200:
        raise DeokwaveHatasi(f"Deokwave {yol} ucu HTTP {yanit.status_code} döndü.",
                             status_code=yanit.status_code)
    try:
        veri = json.loads(yanit.text)
    except ValueError as hata:
        raise DeokwaveHatasi(
            f"Deokwave {yol} ucu JSON yerine beklenmeyen bir yanıt verdi "
            "(site değişmiş olabilir).") from hata
    if not isinstance(veri, dict):
        raise DeokwaveHatasi(f"Deokwave {yol} ucunun yanıtı beklenen biçimde değil.")
    return veri


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def arama_ayristir(veri: Dict[str, Any], ad_alani: str = "name") -> List[Tuple[str, str, str]]:
    """Arama yanıtı → [(animeid, ad, tür)].

    ``ad_alani``: yeni API "name", sitenin otomatik tamamlaması
    (`/search_api.php`) "name_english" kullanıyor. Tür "series" | "movie" | ""
    (site boş da bırakıyor). "users" anahtarı (üye profilleri) yok sayılır.
    """
    out: List[Tuple[str, str, str]] = []
    for kayit in veri.get("animes") or []:
        if not isinstance(kayit, dict):
            continue
        kimlik = str(kayit.get("animeid") or "").strip()
        ad = _html.unescape(str(kayit.get(ad_alani) or "")).strip()
        if _ID_RE.match(kimlik) and ad:
            out.append((kimlik.upper(), ad, str(kayit.get("type") or "")))
    return out


def _sirala(sorgu: str, kayitlar: List[Tuple[str, str, str]]) -> List[Tuple[str, str]]:
    """Sitenin sırası zayıf: "naruto" için 9 filmi "Naruto" dizisinden önce veriyor.

    Tam eşleşme, önek, içerme, sonra dizi filmden önce, en son kısa ad önce
    ("frieren": site "... Mini Anime"yi asıl diziden önce veriyor; aynı
    öneki taşıyan adlardan en kısası neredeyse her zaman ana seri). `sorted`
    kararlı; tamamen eşit kalanlar sitenin sırasını korur. (Arama motoru
    ayrıca `title_match` ile sıralıyor; bu, CLI ve tarayıcı için de doğru sıra.)
    """
    q = sorgu.casefold()

    def anahtar(kayit: Tuple[str, str, str]):
        ad = kayit[1].casefold()
        return (ad != q, not ad.startswith(q), q not in ad, kayit[2] == "movie",
                len(ad))

    return [(kimlik, ad) for kimlik, ad, _tur in sorted(kayitlar, key=anahtar)]


def search_deokwave(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """Deokwave'de anime ara → [(animeid, başlık), ...]; sonuç yoksa [].

    Site 2 karakterden kısa sorguya boş dönüyor (kendi JS'i de öyle). Siteye
    hiç ulaşılamazsa `DeokwaveHatasi` yükselir: arama sayfası "sonuç yok"
    yerine sebebi göstersin.
    """
    q = (query or "").strip()
    if len(q) < 2 or limit <= 0:
        return []
    kayitlar: List[Tuple[str, str, str]] = []
    gorulen = set()
    hata: Optional[DeokwaveHatasi] = None
    sayfa, toplam_sayfa = 1, 1
    # Yeni API bütün eşleşmeleri sayfalı veriyor; en çok 5 sayfa (120 kayıt).
    while sayfa <= min(toplam_sayfa, 5) and len(kayitlar) < limit:
        try:
            veri = _json("/api/v1/animes/search/", params={"q": q, "page": sayfa})
        except DeokwaveHatasi as e:
            hata = e
            break
        if not veri.get("success"):
            break
        for kayit in arama_ayristir(veri, "name"):
            if kayit[0] not in gorulen:
                gorulen.add(kayit[0])
                kayitlar.append(kayit)
        try:
            toplam_sayfa = int(veri.get("total_pages") or 1)
        except (TypeError, ValueError):
            toplam_sayfa = 1
        sayfa += 1

    if not kayitlar:
        # Sitenin JS'i aramayı bu uca taşıdı; ikisi de çalışıyor, ikisini de
        # tutuyoruz ki biri kapanınca kaynak düşmesin.
        try:
            kayitlar = arama_ayristir(_json("/search_api.php", params={"q": q}),
                                      "name_english")
        except DeokwaveHatasi as e:
            if hata is not None:
                raise hata from e
            # Yeni API "sonuç yok" dedi, yedek uç erişilemedi: sonuç yok.
            return []
    return _sirala(q, kayitlar)[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def sezonlari_ayristir(sayfa: str) -> Any:
    """Anime sayfasındaki `var allSezonlar = ...;` değişmezi; yoksa None.

    Sayfada yalnızca 1. sezonun /watch/ bağlantıları çiziliyor, diğer sezonlar
    JS ile bu değişmezden kuruluyor; bağlantıları kazımak eksik liste verir.
    """
    i = sayfa.find(_SEZON_ISARETI)
    if i < 0:
        return None
    try:
        veri, _son = json.JSONDecoder().raw_decode(sayfa[i + len(_SEZON_ISARETI):])
    except ValueError:
        return None
    return veri


def anime_basligi(sayfa: str) -> str:
    """<title>'dan anime adı ("Deokwave | X İzle - 4K Türkçe Altyazılı" → X)."""
    m = re.search(r"<title>(.*?)</title>", sayfa, re.S)
    baslik = _html.unescape((m.group(1) if m else "").strip())
    baslik = re.sub(r"^\s*Deokwave\s*\|\s*", "", baslik)
    baslik = re.sub(r"\s*İzle\s*-.*$", "", baslik)
    return baslik.strip()


def _numaralar(deger: Any) -> List[int]:
    """Sezon/bölüm anahtarları sayısal sırayla ("1","10","2" değil 1,2,10).

    PHP "0".."n-1" anahtarlı diziyi JSON'da listeye çeviriyor; o durumda
    indeksler numara.
    """
    if isinstance(deger, list):
        return list(range(len(deger)))
    if isinstance(deger, dict):
        return sorted(int(k) for k in deger if str(k).isdigit())
    return []


def bolumleri_ayristir(anime_id: str, sayfa: str) -> List[Tuple[str, str]]:
    """Anime sayfası → [(bolum_id, başlık)], izleme sırasıyla.

    Sayfada `allSezonlar` yoksa (site değişmiş ya da başka bir sayfa gelmiş)
    `DeokwaveHatasi`: bunu "0 bölüm" diye göstermek hatayı gizlerdi.
    """
    veri = sezonlari_ayristir(sayfa)
    if veri is None:
        raise DeokwaveHatasi(
            f"Deokwave anime sayfasında ({anime_id}) bölüm listesi bulunamadı; "
            "sitenin yapısı değişmiş olabilir.")
    out: List[Tuple[str, str]] = []
    if isinstance(veri, dict):
        sezonlar = _numaralar(veri)
        # Tek sezon ve o sezon 1 ise başlığa sezon yazılmaz ("5. Bölüm"); aksi
        # hâlde "2. Sezon 5. Bölüm" — bölüm ayrıştırıcısı ikisini de tanıyor
        # ve çok kaynaklı birleştirme (sezon, bölüm) üzerinden yapılıyor.
        sezon_yaz = sezonlar != [1]
        for s in sezonlar:
            for e in _numaralar(veri.get(str(s))):
                baslik = f"{s}. Sezon {e}. Bölüm" if sezon_yaz else f"{e}. Bölüm"
                out.append((f"{anime_id}/{s}/{e}", baslik))
    elif isinstance(veri, list) and veri:
        # Film / tek parça: izleme sayfası /watch/<ID>/, video-info 0/0 ister.
        out.append((f"{anime_id}/0/0", anime_basligi(sayfa) or "Film"))
    # [] → henüz bölüm eklenmemiş: gerçek bir "0 bölüm".
    return out


def get_anime_episodes(anime_id: str) -> List[Tuple[str, str]]:
    """Animenin bölümleri → [("<ID>/<sezon>/<bölüm>", başlık), ...]."""
    kimlik = str(anime_id or "").strip().strip("/").upper()
    if not _ID_RE.match(kimlik):
        raise DeokwaveHatasi(
            f"Deokwave 7 haneli anime kimliği bekliyor (ör. 0C61BB4), "
            f"'{anime_id}' geçersiz.", status_code=400)
    # Sondaki "/" şart: onsuz sunucu 404 veriyor.
    yanit = _get(f"/anime/{kimlik}/")
    if yanit.status_code != 200:
        raise DeokwaveHatasi(f"Deokwave anime sayfası HTTP {yanit.status_code} döndü "
                             f"({kimlik}).", status_code=yanit.status_code)
    # Bilinmeyen kimlik 302 ile ana sayfaya yönleniyor (curl yönlendirmeyi
    # izliyor, sonuç 200 ana sayfa).
    son_adres = str(getattr(yanit, "url", "") or "")
    if son_adres and "/anime/" not in son_adres and _SEZON_ISARETI not in yanit.text:
        raise DeokwaveHatasi(f"Deokwave'de {kimlik} kimlikli anime bulunamadı.",
                             status_code=404)
    return bolumleri_ayristir(kimlik, yanit.text)


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _bolum_coz(episode_id: str) -> Tuple[str, int, int]:
    m = _BOLUM_RE.match(str(episode_id or "").strip())
    if not m:
        raise DeokwaveHatasi(
            f"Deokwave bölüm kimliği '<ID>/<sezon>/<bölüm>' biçiminde olmalı, "
            f"'{episode_id}' geçersiz.", status_code=400)
    return m.group(1).upper(), int(m.group(2)), int(m.group(3))


def watch_url(episode_id: str) -> str:
    """Bölüm kimliği → sitedeki izleme sayfası (bölüm nesnesinin adresi)."""
    try:
        kimlik, s, e = _bolum_coz(episode_id)
    except DeokwaveHatasi:
        # Adres bölüm nesnesinin kimliği gibi de kullanılıyor; tanınmayan
        # kimlikler aynı adrese çökmesin.
        return f"{BASE_URL}/watch/{quote(str(episode_id or ''), safe='/')}"
    if s == 0 and e == 0:
        return f"{BASE_URL}/watch/{kimlik}/"
    return f"{BASE_URL}/watch/{kimlik}/season/{s}/episode/{e}"


def token_from_watch_html(sayfa: str) -> Optional[str]:
    """İzleme sayfasına gömülü oynatma anahtarı (`window.__VT__ = '...'`)."""
    m = _VT_RE.search(sayfa or "")
    return m.group(1) if m else None


def _oynatma_anahtari(episode_id: str) -> str:
    """Taze (≤5 dk) oynatma anahtarı; alınamazsa `DeokwaveHatasi`.

    Önce API ucu, olmazsa izleme sayfasına gömülü aynı değer. Anahtar akış
    listesi istenirken alınıyor, yani oynatmanın hemen öncesinde: süresi
    dolmuş anahtarla sw2 401 veriyor.
    """
    global _token, _token_zamani
    with _kilit:
        if _token and time.monotonic() - _token_zamani < _TOKEN_TTL:
            return _token
    anahtar: Optional[str] = None
    ilk_hata: Optional[Exception] = None
    try:
        aday = _json("/api/v1/video/token/").get("token")
        if isinstance(aday, str) and _TOKEN_RE.match(aday):
            anahtar = aday
    except DeokwaveHatasi as e:
        ilk_hata = e
    if not anahtar:
        try:
            yanit = _get(watch_url(episode_id))
            if yanit.status_code == 200:
                anahtar = token_from_watch_html(yanit.text)
        except DeokwaveHatasi as e:
            ilk_hata = ilk_hata or e
    if not anahtar:
        mesaj = "Deokwave oynatma anahtarı alınamadı; videolar açılamaz."
        if ilk_hata is not None:
            mesaj += f" ({ilk_hata})"
        raise DeokwaveHatasi(mesaj, status_code=getattr(ilk_hata, "status_code", None))
    with _kilit:
        _token, _token_zamani = anahtar, time.monotonic()
    return anahtar


def _kalite_no(etiket: str) -> str:
    """"1080p" → "1080" (sitenin `_dkQId`'si); başka biçim küçük harfle aynen."""
    m = re.match(r"^\s*(\d{3,4})p\s*$", etiket or "", re.I)
    return m.group(1) if m else (etiket or "").strip().lower()


def _kalite_sirasi(etiket: str) -> int:
    no = _kalite_no(etiket)
    return -int(no) if no.isdigit() else 0


def akislari_kur(bilgi: Dict[str, Any], token: str) -> List[Dict[str, str]]:
    """video-info yanıtı + oynatma anahtarı → oynatılabilir akışlar.

    Her Türk fansub'ı ayrı akış ("fansub" alanı dolu), sitenin varsayılanı
    ("main") önce, grup içinde kalite yüksekten düşüğe. Etiket çözünürlükle
    başlıyor ki `best_video`'nun çözünürlük sıralaması onu okuyabilsin.
    """
    if not bilgi or not bilgi.get("success"):
        return []
    fansublar = [f for f in (bilgi.get("fansubs") or []) if isinstance(f, dict)]
    if not fansublar and bilgi.get("videoid"):
        # Tek gruplu içerikte (çoğu film) liste boş; üst düzey video kullanılır.
        fansublar = [{"key": "main", "name": "Deokwave", "videoid": bilgi.get("videoid"),
                      "qualities": bilgi.get("qualities") or [],
                      "hasSub": bilgi.get("hasSub")}]
    # Sibnet'ten sw2 üzerinden vekillenen kayıtlar (isSibnet) en sona: yalnızca
    # 480p ve ölçümde ikisinden biri 25 sn boyunca tek bayt göndermedi; site
    # kendi kopyasını da bazen "main" yapıyor. Kalanlarda sitenin varsayılanı
    # ("main") önce, sonra sitenin sırası (`sort` kararlı).
    fansublar.sort(key=lambda f: (bool(f.get("isSibnet")), f.get("key") != "main"))

    out: List[Dict[str, str]] = []
    gorulen = set()
    for fansub in fansublar:
        video_id = str(fansub.get("videoid") or "")
        if not _VIDEO_ID_RE.match(video_id):
            continue
        # hasSub: altyazı MP4'ün içinde değil, ayrı dosyada ve yalnızca giriş
        # yapmış/PRO kullanıcıya veriliyor. Açılsa Türkçe altyazısız (ham)
        # oynardı; bu uygulamanın amacına ters, atlanıyor.
        if fansub.get("hasSub"):
            continue
        ad = _html.unescape(str(fansub.get("name") or "")).strip() or "Deokwave"
        ek = _html.unescape(str(fansub.get("extra") or ""))
        dublaj = "dublaj" in f"{ad} {ek}".casefold()
        kaliteler = sorted((k for k in (fansub.get("qualities") or [])
                            if isinstance(k, str) and k.strip()), key=_kalite_sirasi)
        for kalite in kaliteler:
            no = _kalite_no(kalite)
            if (video_id, no) in gorulen:
                continue          # aynı video iki grup kaydında (üst düzey + main)
            gorulen.add((video_id, no))
            out.append({
                "url": f"{STREAM_BASE}/v/{video_id}/{quote(no, safe='')}/"
                       f"?vt={quote(token, safe='')}",
                "label": f"{kalite.strip()} {ad}" + (" (TR Dublaj)" if dublaj else ""),
                "type": "direct",
                "referer": REFERER,
                "fansub": ad,
                "player": "DEOKWAVE",
            })
    return out


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları; site "video yok" derse [].

    Ağ/engel/ayrıştırma sorunlarında `DeokwaveHatasi` yükselir.
    """
    kimlik, s, e = _bolum_coz(episode_id)
    bilgi = _json("/watch/video-info/",
                  params={"animeid": kimlik, "season": s, "episode": e})
    if not bilgi.get("success"):
        return []            # site bu bölüm için video tanımıyor ({"success": false})
    return akislari_kur(bilgi, _oynatma_anahtari(episode_id))


__all__ = [
    "search_deokwave",
    "get_anime_episodes",
    "get_episode_streams",
    "watch_url",
    "DeokwaveHatasi",
    "BASE_URL",
]
