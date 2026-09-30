"""
AniMOM kaynağı — https://animom.org

Türkçe altyazılı (bir kısmı Türkçe dublajlı) anime ve donghua sitesi. Videoların
çoğu sitenin KENDİ yüklemesi ("AniMOM"): kendi FirePlayer kurulumunda
(hdplayersystem.com) ve kendi CDN'inde (playersystem*.sbs, hdmomplayer*.click)
duruyor, altyazı görüntüye gömülü (hardsub). Bazı bölümlerde fansub grupları
(Kirigana Fairies…) ayrı sekme olarak sibnet/ok.ru/gdrive aynaları veriyor.
Arka uç PHP (CodeIgniter), önünde Cloudflare var ama sınama yok; giriş, çerez,
CSRF ya da JS çözümü gerekmiyor (2026-09-30 ölçümü).

Akış:
- Arama:    POST /search  {query}  (XHR)  → {"success", "theme": HTML}
            Dizilerde en çok 5 sonuç, alfabetik. Filmler bu aramada YOK; film
            listesi (/anime-filmleri, /anime-filmleri/2 …; 28 film) 12 saat
            önbelleklenip yerelde aranıyor.
- Bölümler: GET /anime/<slug>  → ilk sayfa (anime tipinde 20 bölüm; "Daha fazla göster" =
            POST /episode/item/load, 20'şer). One Piece için bu 60 istek
            demek; ama herhangi bir BÖLÜM sayfası bütün sezonların bütün
            bölümlerini `episodes-slide` şeridinde taşıyor (One Piece: 1200).
            Liste bu yüzden 2 istekle kuruluyor: anime sayfası → ilk bölüm.
            Film: oynatıcı anime sayfasının kendisinde (data-group-hash).
- Akışlar:  GET /anime/<bölüm yolu> → `data-group-hash` sekmeleri ("Türkçe
            Altyazı", "Türkçe Dublaj" ya da fansub adı)
            POST /get/video/group {hash} → {"videos": [{name, link, lock, …}]}
            - hdplayersystem.com (FirePlayer) → `do=getVideo` → imzalı master
              (bkz. `_fireplayer_akislari`) → varyant listeleri.
            - anizm.net/player/<id> → Anizm'in oynatıcısı (bkz. `_anizm_ac`).
            - sibnet, ok.ru, gdrive, sendvid, mail.ru… → yt-dlp'ye aynen.

Kilitli video: sitenin JS'i `link` boş ve `lock` doluysa bağlantı yerine
üyelik mesajını (`tiers`, base64 HTML) gösteriyor. Böyle kayıtlar ATLANIR;
üyelik kapısı aşılmaz. (Ölçümde hiç kilitli video görülmedi.)

Kimlikler:
- Kaynak kimliği: sitenin anime slug'ı ("sousou-no-frieren-izle"; sondaki
  "-izle-hd11" gibi ekler slug'ın parçası, atılamaz).
- Bölüm kimliği: bölüm adresinin /anime/ sonrası yolu
  ("sousou-no-frieren-1-bolum", "blue-lock-2-sezon/sezon-2/bolum-1").
  Film: anime slug'ının kendisi (oynatıcı anime sayfasında).
"""
from __future__ import annotations

import html as _html
import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

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


BASE_URL = "https://animom.org"
REFERER = BASE_URL + "/"
HTTP_TIMEOUT = 15
IMPERSONATE = "chrome131"
_YEDEK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Aynı konağa ardışık iki istek arasında en az bu kadar. Sitede kısıt
# ölçülmedi (2026-09-30 anketinde 1.2 sn aralıklı ~170 istekten hiçbiri
# engellenmedi); aralık nezaket için ve KONAK BAŞINA: oynatıcının
# (hdplayersystem) isteği sitenin sırasını beklemesin.
_MIN_INTERVAL = 1.0

# Film listesi nadiren değişiyor (28 film); her aramada 2 sayfa çekmek israf.
_FILM_TAZELIK = 12 * 3600
_FILM_HATA_BEKLEMESI = 10 * 60
_FILM_AZAMI_SAYFA = 5

# "Daha fazla göster" yedeğinde en çok kaç sayfa (20'şer) istensin. Yalnızca
# bölüm sayfasındaki tam liste okunamazsa kullanılıyor.
_YEDEK_AZAMI_SAYFA = 80

# İmzalı master arada 403 verebiliyor (aynı FirePlayer'ın anizmplayer
# kurulumunda ~%15 ölçüldü); taze bir getVideo düzeltiyor.
_FIREPLAYER_DENEME = 3

_SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]*$")
# Bölüm yolu: tek parça (anime tipi) ya da "<slug>/sezon-S/bolum-N" (dizi
# tipi). Kimlik doğrudan adrese ekleniyor; "../" ya da sorgu dizgisi sızmasın.
_BOLUM_YOLU = re.compile(r"^[a-z0-9][a-z0-9\-]*(?:/sezon-\d{1,3}/bolum-\d{1,5})?$")

# Alan adı kalıplara gömülü değil; site taşınırsa yalnızca BASE_URL değişir.
_ANIME_ADRESI = r'(?:https?://[^/"]+)?/anime/'
_ARAMA_SONUCU = re.compile(
    r'<a href="' + _ANIME_ADRESI + r'([^"/?#]+)" class="block truncate">([^<]*)</a>')
_FILM_KARTI = re.compile(
    r'<div class="poster-long-subject">\s*<a class="block no-underline"\s+href="'
    + _ANIME_ADRESI + r'([^"/?#]+)">\s*<h2 class="truncate"\s*>([^<]*)</h2>')
_SONRAKI_SAYFA = re.compile(r'<a href="([^"]+)"[^>]*rel="next"')
_LI = re.compile(r"<li\b[^>]*>((?:(?!<li\b).)*?)</li>", re.S)
_GORSEL = re.compile(r'data-src="([^"]+)"')
# Anime sayfasındaki (ilk sayfa) bölümler: anime tipinde "<data>N</data>.
# Bölüm", dizi tipinde "Bölüm <data>N</data>".
_ILK_BOLUMLER = re.compile(
    r'href="' + _ANIME_ADRESI + r'([^"?#]+)">\s*'
    r'(?:<data>\d+</data>\.\s*Bölüm|Bölüm\s*<data>\d+</data>)')
# Bölüm sayfasının şeridi: her bölüm için numara, sezon ve adres. Aradaki
# `(?:(?!swiper-slide).)*?` bir sonraki bölüme taşmayı önlüyor.
_SERIT = re.compile(
    r'<div class="swiper-slide" data-move-episode="(\d+)" data-season="(\d+)">'
    r'(?:(?!class="swiper-slide").)*?<a class="block"\s+href="' + _ANIME_ADRESI
    + r'([^"?#]+)"', re.S)
_ANIME_ID = re.compile(r'class="[^"]*item-episode[^"]*"[^>]*data-id="(\d+)"'
                       r'|data-id="(\d+)"[^>]*class="[^"]*item-episode')
_SEZON_SEKMESI = re.compile(r'id="sea-(\d+)"')
_GRUP = re.compile(r'<li[^>]*data-group-hash="([^"]+)"[^>]*>(.*?)</li>', re.S)
# Sayfaya gömülü (etkin sekmenin) videoları; grup ucu düşerse yedek.
_GOMULU_VIDEO = re.compile(r'<button[^>]*data-hhs="([^"]*)"[^>]*title="([^"]*)"')
_BASLIK = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_ANIME_SAYFASI_IZI = ("series-profile", "series-watch")
_YOK_IZI = "Kaybolmuş gibisin"          # sitenin 404 sayfasının başlığı
# Listede olup videosu henüz yüklenmemiş bölüm ("Bu bölüm çok yakında
# yayınlanacak."): sekme yok, boş liste doğru cevap.
_HAZIR_DEGIL_IZI = "this-episode-not-ready"

# (konak, oynatıcı adı, deneme sırası). Adlar `common.oynatici_onceligi` ile
# aynı (ilerleme etiketinde görünüyor). Sıra: AniMOM'un kendi CDN'i önce
# (anket: 16 dizinin 25 AniMOM videosunun hepsi okunabilir master verdi),
# sonra yt-dlp'nin sorunsuz açtıkları.
_OYNATICILAR: Tuple[Tuple[str, str, int], ...] = (
    ("hdplayersystem.com", "ANIMOM", 0),
    ("anizmplayer.com", "MUGEN", 1),
    ("video.sibnet.ru", "SIBNET", 2),
    ("drive.google.com", "GDRIVE", 3),
    ("my.mail.ru", "MAIL", 4),
    ("sendvid.com", "SENDVID", 5),
    ("vk.com", "VK", 5),
    ("dailymotion.com", "DAILYMOTION", 5),
    ("myvi.ru", "MYVI", 6),
    ("myvi.tv", "MYVI", 6),
    ("hdvid.tv", "HDVID", 6),
    ("vidmoly.to", "VIDMOLY", 6),
    ("vidmoly.me", "VIDMOLY", 6),
    ("ok.ru", "ODNOKLASSNIKI", 8),
    ("odnoklassniki.ru", "ODNOKLASSNIKI", 8),
    ("mp4upload.com", "MP4UPLOAD", 9),
    ("yourupload.com", "YOURUPLOAD", 9),
)
_BILINMEYEN_SIRA = 7
# yt-dlp'nin çıkarıcısı olmayan, kapanmış ya da ölçümde hiç açılmayan
# barındırıcılar. Döndürmek `best_video`'nun sınırlı deneme bütçesini yer.
# Konak adına ve (anizm.net oynatıcılarında adres konağı söylemediği için)
# sitenin verdiği video adına uygulanır.
_OYNATILAMAZ = re.compile(
    r"voe|filemoon|embedgram|cloudvideo|dood|streamtape|vidhide|lulu|streamwish|"
    r"savefile|byse|uqload|abyss|fembed|streamsb|streamlare|tubeload|embedo|"
    r"mega\.nz|liiivideo", re.I)
# Bölüm başına ağ isteğiyle çözülen (FirePlayer, Anizm yönlendirmesi) en çok
# video. Blue Lock 1. bölüm 5 fansub sekmesinde 77 video veriyor; hepsini
# çözmek gereksiz yük, `best_video` zaten yalnızca ilk 8 adayı deniyor.
_AZAMI_COZUM = 8
# Anizm'in kendi oynatıcı sayfası; asıl barındırıcıya 302 ile gidiyor.
_ANIZM_OYNATICI = re.compile(r"^/player/(\d{1,12})/?$")
_ANIZM_KONAKLARI = ("anizm.net", "anizm.pro", "anizle.co", "anizm.com.tr", "puffytr.com")
_FIREPLAYER_KIMLIGI = re.compile(r"/(?:video|embed)/([A-Za-z0-9]{6,64})")
_FIREPLAYER_VERISI = re.compile(r"[?&]data=([A-Za-z0-9]{6,64})")
_VARYANT = re.compile(r"#EXT-X-STREAM-INF:([^\n]*)\n\s*([^\s#][^\n]*)")


class AnimomHatasi(RuntimeError):
    """AniMOM'dan beklenen veri alınamadı (ağ, HTTP durumu, sayfa yapısı).

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
_kilit = threading.Lock()
_son_istek: Dict[str, float] = {}
_ortak_oturum: Any = None


def _yeni_oturum() -> Any:
    """Yeni HTTP oturumu. Testler ağa çıkmamak için bunu sahteler."""
    if _HAS_CURL:
        return _http.Session(impersonate=IMPERSONATE)
    oturum = _http.Session()          # curl_cffi yoksa düz requests de 200 alıyor
    oturum.headers["User-Agent"] = _YEDEK_UA
    return oturum


def _oturum() -> Any:
    """Site istekleri için ortak oturum (bağlantı yeniden kullanılsın)."""
    global _ortak_oturum
    with _kilit:
        if _ortak_oturum is None:
            _ortak_oturum = _yeni_oturum()
        return _ortak_oturum


def _sira_bekle(adres: str) -> None:
    """Aynı konağa ardışık istekler arasında `_MIN_INTERVAL` bırak."""
    konak = (urlparse(adres).hostname or "").lower()
    with _kilit:
        bekle = _MIN_INTERVAL - (time.monotonic() - _son_istek.get(konak, -1e9))
        if bekle > 0:
            time.sleep(bekle)
        _son_istek[konak] = time.monotonic()


def _engel_mi(yanit: Any) -> bool:
    """Yanıt gerçek içerik değil, Cloudflare/WAF engeli mi?

    Yalnızca engel durum kodlarında (403/429/503) gövdeye bakılır: normal bir
    sayfa da "challenge-platform" gibi dizgeler taşıyabilir.
    """
    kod = getattr(yanit, "status_code", 200)
    if kod not in _ENGEL_DURUMLARI:
        return False
    if kod == 429:
        return True
    try:
        bas = (yanit.text or "")[:6000]
    except Exception:
        return False
    return any(iz in bas for iz in _CF_IZLERI)


def _istek(yontem: str, adres: str, *, ne: str, oturum: Any = None,
           bekle: bool = True, **kw: Any) -> Any:
    """Zaman aşımlı, aralıklı GET/POST. Engel ve ağ hatası `AnimomHatasi`.

    HTTP durumu çağırana bırakılır (404 kimi yerde "yok", kimi yerde hata).
    5xx ve ağ hatası BİR kez yeniden deneniyor; engel denenmiyor: aynı parmak
    iziyle ikinci istek de engellenir, kullanıcı gerçek sebebi görmeli.
    ``bekle=False``: aralık uygulanmaz (yalnızca oynatıcının kendi JS'inin de
    hemen attığı, gecikmeye dayanıksız istek; bkz. `_fireplayer_akislari`).
    """
    adres = adres if adres.startswith("http") else BASE_URL + adres
    oturum = oturum if oturum is not None else _oturum()
    kw.setdefault("timeout", HTTP_TIMEOUT)
    son_hata: Optional[BaseException] = None
    yanit = None
    for _deneme in range(2):
        if bekle:
            _sira_bekle(adres)
        try:
            yanit = getattr(oturum, yontem)(adres, **kw)
        except Exception as exc:  # ağ/zaman aşımı: bir kez daha dene
            son_hata = exc
            continue
        if _engel_mi(yanit):
            raise AnimomHatasi(
                f"AniMOM isteği engelledi — {ne}: HTTP {yanit.status_code} "
                "(Cloudflare/WAF doğrulaması ya da erişim reddi). Bir süre sonra "
                "yeniden deneyin ya da başka bir kaynak seçin.", yanit.status_code)
        if yanit.status_code >= 500:
            continue
        return yanit
    if yanit is not None:
        return yanit              # iki kez 5xx: durum çağırana (hata mesajıyla)
    raise AnimomHatasi(f"AniMOM'a ulaşılamadı — {ne}: {son_hata}") from son_hata


def _xhr_basliklari(sayfa: str) -> Dict[str, str]:
    """Sitenin jQuery isteklerinin başlıkları. X-Requested-With olmadan POST
    uçları JSON yerine HTTP 403 sayfası veriyor (ölçüldü: /search ve
    /get/video/group)."""
    return {"X-Requested-With": "XMLHttpRequest", "Referer": sayfa,
            "Origin": BASE_URL,
            "Accept": "application/json, text/javascript, */*; q=0.01"}


def _json(yanit: Any, ne: str) -> Dict[str, Any]:
    if yanit.status_code != 200:
        raise AnimomHatasi(f"AniMOM {ne} alınamadı: HTTP {yanit.status_code}",
                           yanit.status_code)
    try:
        veri = json.loads(yanit.text)
    except ValueError as exc:
        raise AnimomHatasi(
            f"AniMOM {ne} yanıtı JSON değil; site yapısı değişmiş olabilir.") from exc
    if not isinstance(veri, dict):
        raise AnimomHatasi(f"AniMOM {ne} yanıtı beklenen biçimde değil.")
    return veri


def _metin(ham: Any) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", str(ham or ""))).split())


def _konak(adres: str) -> str:
    konak = (urlparse(adres).hostname or "").lower()
    return konak[4:] if konak.startswith("www.") else konak


def _konak_eslesir(konak: str, alan: str) -> bool:
    return konak == alan or konak.endswith("." + alan)


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def _kartlar(html: str, kalip: "re.Pattern[str]") -> List[Dict[str, Any]]:
    """Liste öğelerindeki anime kartları → ``[{"slug", "title", "image"}]``.

    Kart `<li>` başına ayrıştırılıyor: başlığı ya da görseli eksik bir kart,
    komşusunun değerini almasın.
    """
    out: List[Dict[str, Any]] = []
    gorulen = set()
    for parca in _LI.findall(html or ""):
        m = kalip.search(parca)
        if not m:
            continue
        slug = m.group(1)
        if slug in gorulen or not _SLUG.match(slug):
            continue
        gorulen.add(slug)
        gorsel = _GORSEL.search(parca)
        out.append({"slug": slug, "title": _metin(m.group(2)) or slug,
                    "image": _html.unescape(gorsel.group(1)) if gorsel else None})
    return out


def arama_sonucunu_ayristir(tema: str) -> List[Dict[str, Any]]:
    """`/search` yanıtının HTML'i → ``[{"slug", "title", "image"}]`` (site sırası).

    Yanıtta "Kullanıcılar" bölümü de var (üye profilleri); yalnızca
    "Animeler" bölümündeki anime kartları alınıyor.
    """
    return _kartlar(str(tema or "").split("Kullanıcılar", 1)[0], _ARAMA_SONUCU)


def film_listesini_ayristir(sayfa: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """/anime-filmleri sayfası → ``([{"slug", "title", "image"}], sonraki sayfa | None)``."""
    sonraki = _SONRAKI_SAYFA.search(sayfa or "")
    return (_kartlar(sayfa, _FILM_KARTI),
            _html.unescape(sonraki.group(1)) if sonraki else None)


_film_kilidi = threading.Lock()
_filmler: Tuple[Tuple[Dict[str, Any], ...], float] = ((), 0.0)


def _film_listesi() -> Tuple[Dict[str, Any], ...]:
    """Bütün filmler (önbellekli). Alınamazsa eldeki (belki boş) liste.

    Kilit ağ isteği boyunca tutuluyor: paralel aramalar listeyi aynı anda
    ikinci kez çekmesin. Film listesi aramanın küçük bir eki: düşerse arama
    dizilerle sürer, bir süre sonra yeniden denenir.
    """
    global _filmler
    with _film_kilidi:
        liste, gecerlilik = _filmler
        if time.monotonic() < gecerlilik:
            return liste
        yeni: Dict[str, Dict[str, Any]] = {}
        adres: Optional[str] = f"{BASE_URL}/anime-filmleri"
        try:
            for _ in range(_FILM_AZAMI_SAYFA):
                if not adres:
                    break
                yanit = _istek("get", adres, ne="film listesi", headers={"Referer": REFERER})
                if yanit.status_code != 200:
                    raise AnimomHatasi(f"film listesi HTTP {yanit.status_code}",
                                       yanit.status_code)
                kartlar, adres = film_listesini_ayristir(yanit.text)
                for kart in kartlar:
                    yeni.setdefault(kart["slug"], kart)
        except AnimomHatasi as exc:
            print(f"[AniMOM] Film listesi alınamadı, aramada filmler eksik: {exc}")
            _filmler = (liste or tuple(yeni.values()),
                        time.monotonic() + _FILM_HATA_BEKLEMESI)
            return _filmler[0]
        _filmler = (tuple(yeni.values()), time.monotonic() + _FILM_TAZELIK)
        return _filmler[0]


def _normal(metin: str) -> str:
    return re.sub(r"[^0-9a-zçğıöşü]+", " ", str(metin or "").casefold()).strip()


def _sirala(sorgu: str, kayitlar: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Site alfabetik veriyor ("naruto" → önce "Boruto: Naruto…"). Tam eşleşme,
    önek, içerme, sonra kısa ad önce; eşitlerde sitenin sırası (sıralama
    kararlı). Arama motoru ayrıca `title_match` ile sıralıyor; bu, CLI ve
    sunucu tarayıcısı için de doğru sıra."""
    q = _normal(sorgu)

    def anahtar(kayit: Dict[str, Any]):
        ad = _normal(kayit["title"])
        return (ad != q, not ad.startswith(q), q not in ad, len(ad))

    return sorted(kayitlar, key=anahtar)


def zengin_ara(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """AniMOM'da ara → ``[{"slug", "title", "image"}, ...]``; sonuç yoksa ``[]``.

    Kapak görseli arama yanıtında ve film kartında zaten var; ek istek yok.
    Siteye ulaşılamazsa `AnimomHatasi`: arama sayfası "sonuç yok" yerine
    sebebi göstersin. Tek harfli sorguya site bütün katalogun başını
    döndürüyor (anlamsız sonuç); 2 karakterden kısası ağa çıkmıyor.
    """
    sorgu = " ".join(str(query or "").split())
    if len(sorgu) < 2 or limit <= 0:
        return []
    yanit = _istek("post", "/search", ne="arama", data={"query": sorgu},
                   headers=_xhr_basliklari(BASE_URL + "/home"))
    veri = _json(yanit, "arama")
    # `success` yanlışsa site mesajı "theme" yerine "error"da veriyor
    # (ör. sorgu reddedildi): sonuç yok sayılıyor.
    diziler = arama_sonucunu_ayristir(veri.get("theme") or "") if veri.get("success") else []
    q = _normal(sorgu)
    gorulen = {k["slug"] for k in diziler}
    filmler = [k for k in _film_listesi()
               if k["slug"] not in gorulen
               and (q in _normal(k["title"]) or q in _normal(k["slug"].replace("-", " ")))]
    return _sirala(sorgu, diziler + filmler)[:limit]


def search_animom(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """AniMOM'da ara → ``[(slug, başlık), ...]`` (bkz. `zengin_ara`)."""
    return [(k["slug"], k["title"]) for k in zengin_ara(query, limit)]


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def _anime_slugu(kimlik: str) -> str:
    """"sousou-no-frieren-izle", tam adres ya da "/anime/…" → slug."""
    metin = str(kimlik or "").strip()
    if metin.startswith(("http://", "https://")):
        metin = urlparse(metin).path
    metin = metin.strip("/")
    if metin.startswith("anime/"):
        metin = metin[len("anime/"):]
    slug = metin.split("/")[0]
    if not _SLUG.match(slug):
        raise AnimomHatasi(f"Geçersiz AniMOM anime kimliği: {kimlik!r}", 400)
    return slug


def _bolum_yolu(bolum_id: str) -> str:
    metin = str(bolum_id or "").strip()
    if metin.startswith(("http://", "https://")):
        metin = urlparse(metin).path
    metin = metin.strip("/")
    if metin.startswith("anime/"):
        metin = metin[len("anime/"):]
    if not _BOLUM_YOLU.match(metin):
        raise AnimomHatasi(
            f"Geçersiz AniMOM bölüm kimliği: {bolum_id!r} "
            "(beklenen: 'bolum-slug' ya da 'anime-slug/sezon-S/bolum-N')", 400)
    return metin


def bolum_adresi(bolum_id: str) -> str:
    """Bölüm kimliği → sitedeki izleme sayfası (bölüm nesnesinin adresi)."""
    try:
        return f"{BASE_URL}/anime/{_bolum_yolu(bolum_id)}"
    except AnimomHatasi:
        # Adres bölüm nesnesinin kimliği gibi de kullanılıyor; tanınmayan
        # kimlikler aynı adrese çökmesin.
        return f"{BASE_URL}/anime/{quote(str(bolum_id or ''), safe='/')}"


_SLUG_EKI = re.compile(r"(?:-izle(?:-hd\d*)?|-hd\d+)+$")


def bolum_slugu(bolum_id: str) -> str:
    """Bölüm kimliği → izleme geçmişi/dosya adı slug'ı.

    "one-piece-1100-bolum-izle-hd-izle-hd1" → "one-piece-1100-bolum";
    "blue-lock-2-sezon/sezon-2/bolum-1" → "blue-lock-2-sezon-sezon-2-bolum-1".
    Varsayılan slug çağıranın verdiği anime BAŞLIĞINDAN üretiliyor; aynı dizi
    AniList eşleşmesiyle başka adla açılınca geçmiş anahtarı ve dosya adı
    değişirdi. Sitenin SEO ekleri ("-izle-hd11") atılıyor: dosya adında
    anlamsız, ve site aynı dizide bölümden bölüme farklı ek kullanıyor.
    """
    yol = str(bolum_id or "").strip().strip("/")
    return _SLUG_EKI.sub("", yol).replace("/", "-") or yol.replace("/", "-")


def _sayfa_basligi(sayfa: str) -> str:
    m = _BASLIK.search(sayfa or "")
    return _metin(m.group(1)) if m else ""


def serit_ayristir(sayfa: str) -> List[Tuple[int, int, str]]:
    """Bölüm sayfasının şeridi → ``[(sezon, bölüm, yol)]`` (sayfa sırası)."""
    out: List[Tuple[int, int, str]] = []
    gorulen = set()
    for no, sezon, yol in _SERIT.findall(sayfa or ""):
        yol = yol.strip("/")
        if yol in gorulen or not _BOLUM_YOLU.match(yol):
            continue
        gorulen.add(yol)
        out.append((int(sezon), int(no), yol))
    return out


def ilk_bolumleri_ayristir(sayfa: str) -> List[str]:
    """Anime sayfasındaki (ilk sayfa) bölümlerin yolları, sayfa sırasıyla."""
    return list(dict.fromkeys(
        yol.strip("/") for yol in _ILK_BOLUMLER.findall(sayfa or "")
        if _BOLUM_YOLU.match(yol.strip("/"))))


def _numara(yol: str) -> Tuple[int, int]:
    """Yoldan (sezon, bölüm); şerit okunamadığında yedek listede kullanılıyor."""
    m = re.search(r"/sezon-(\d+)/bolum-(\d+)$", yol)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"-(\d+)-bolum(?:-|$)", yol)
    return 1, int(m.group(1)) if m else 0


def bolum_listesini_kur(ogeler: List[Tuple[int, int, str]]) -> List[Tuple[str, str]]:
    """``[(sezon, bölüm, yol)]`` → izleme sırasıyla ``[(yol, başlık)]``.

    Tek sezonda başlığa sezon yazılmaz ("5. Bölüm"); çok sezonda "2. Sezon
    5. Bölüm" — bölüm ayrıştırıcısı ikisini de tanıyor ve çok kaynaklı
    birleştirme (sezon, bölüm) üzerinden yapılıyor.
    """
    sirali = sorted(ogeler, key=lambda o: (o[0], o[1]))      # kararlı
    sezon_yaz = len({o[0] for o in sirali}) > 1
    out: List[Tuple[str, str]] = []
    gorulen = set()
    for sezon, no, yol in sirali:
        if yol in gorulen:
            continue
        gorulen.add(yol)
        out.append((yol, f"{sezon}. Sezon {no}. Bölüm" if sezon_yaz else f"{no}. Bölüm"))
    return out


def _anime_sayfasi(slug: str) -> Any:
    yanit = _istek("get", f"/anime/{quote(slug)}", ne=f"'{slug}' sayfası",
                   headers={"Referer": REFERER})
    if yanit.status_code == 404 or (yanit.status_code == 200 and _YOK_IZI in yanit.text[:4000]
                                    and not any(iz in yanit.text for iz in _ANIME_SAYFASI_IZI)):
        raise AnimomHatasi(f"AniMOM'da '{slug}' adlı bir anime yok (HTTP 404).", 404)
    if yanit.status_code != 200:
        raise AnimomHatasi(f"AniMOM '{slug}' sayfası alınamadı: HTTP {yanit.status_code}",
                           yanit.status_code)
    return yanit


def _daha_fazla_ile(slug: str, sayfa: str, ilk: List[str]) -> List[Tuple[int, int, str]]:
    """Yedek: anime sayfasının ilk 20'si + "Daha fazla göster" sayfaları.

    Yalnızca bölüm sayfasındaki tam liste okunamadığında (şerit yok/bozuk)
    kullanılıyor; sitenin kendi düğmesinin yaptığı istek.
    """
    ogeler = [(*_numara(y), y) for y in ilk]
    kimlik = _ANIME_ID.search(sayfa)
    anime_id = (kimlik.group(1) or kimlik.group(2)) if kimlik else None
    if not anime_id:
        return ogeler
    sezonlar = _SEZON_SEKMESI.findall(sayfa) or ["1"]
    cok_sezon = len(sezonlar) > 1
    for sezon in sezonlar:
        for no in range(2, _YEDEK_AZAMI_SAYFA + 2):
            veri = {"page": no, "type": 1, "id": anime_id}
            if cok_sezon:
                veri["season"] = sezon
            try:
                cevap = _json(_istek("post", "/episode/item/load", ne="bölüm sayfası",
                                     data=veri, headers=_xhr_basliklari(
                                         f"{BASE_URL}/anime/{slug}")), "bölüm listesi")
            except AnimomHatasi as exc:
                print(f"[AniMOM] {slug}: bölüm listesinin devamı alınamadı: {exc}")
                return ogeler
            yeni = [y for y in ilk_bolumleri_ayristir(str(cevap.get("theme") or ""))
                    if y not in {o[2] for o in ogeler}]
            ogeler.extend((*_numara(y), y) for y in yeni)
            if cevap.get("last") or not yeni:
                break
    return ogeler


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Animenin bölümleri, izleme sırasıyla: ``[(bolum_id, "N. Bölüm")]``.

    Anime yoksa ya da sayfa okunamazsa `AnimomHatasi`. Henüz bölümü
    eklenmemiş anime → ``[]``.
    """
    kimlik = _anime_slugu(slug)
    yanit = _anime_sayfasi(kimlik)
    sayfa = yanit.text
    ilk = ilk_bolumleri_ayristir(sayfa)
    if not ilk:
        if _GRUP.search(sayfa):
            # Film / tek parça: oynatıcı anime sayfasının kendisinde.
            return [(kimlik, _sayfa_basligi(sayfa) or "Film")]
        if not any(iz in sayfa for iz in _ANIME_SAYFASI_IZI):
            raise AnimomHatasi(
                f"AniMOM '{kimlik}' sayfasında bölüm listesi bulunamadı; sitenin "
                "yapısı değişmiş olabilir.")
        return []

    # Herhangi bir bölüm sayfası bütün bölümleri taşıyor (bkz. modül belgesi).
    try:
        bolum = _istek("get", f"/anime/{ilk[0]}", ne="bölüm sayfası",
                       headers={"Referer": f"{BASE_URL}/anime/{kimlik}"})
        serit = serit_ayristir(bolum.text) if bolum.status_code == 200 else []
    except AnimomHatasi as exc:
        print(f"[AniMOM] {kimlik}: bölüm sayfası alınamadı, yedek listeye geçiliyor: {exc}")
        serit = []
    # Şerit anime sayfasındakilerden KISA olamaz; öyleyse bozuk sayılıyor.
    if len(serit) >= len(ilk):
        return bolum_listesini_kur(serit)
    return bolum_listesini_kur(_daha_fazla_ile(kimlik, sayfa, ilk))


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def gruplari_ayristir(sayfa: str) -> List[Tuple[str, str]]:
    """Bölüm/film sayfasındaki video sekmeleri → ``[(hash, ad)]``."""
    out: List[Tuple[str, str]] = []
    for kimlik, ic in _GRUP.findall(sayfa or ""):
        kimlik = _html.unescape(kimlik).strip()
        if kimlik and kimlik not in {k for k, _ in out}:
            out.append((kimlik, _metin(ic)))
    return out


def _oynatici(adres: str) -> Tuple[Optional[str], int]:
    """Adresin oynatıcı adı ve deneme sırası; tanınmıyorsa ``(None, 7)``."""
    konak = _konak(adres)
    for alan, ad, sira in _OYNATICILAR:
        if _konak_eslesir(konak, alan):
            return ad, sira
    return None, _BILINMEYEN_SIRA


def _fireplayer_kimligi(adres: str) -> Optional[str]:
    m = _FIREPLAYER_VERISI.search(adres) or _FIREPLAYER_KIMLIGI.search(urlparse(adres).path)
    return m.group(1) if m else None


def varyantlari_ayristir(master: str, adres: str) -> List[Tuple[int, str]]:
    """HLS master → ``[(yükseklik, mutlak varyant adresi)]``, yüksekten düşüğe."""
    out: List[Tuple[int, str]] = []
    for oznitelik, uri in _VARYANT.findall(master or ""):
        m = re.search(r"RESOLUTION=\d+x(\d+)", oznitelik)
        out.append((int(m.group(1)) if m else 0, urljoin(adres, uri.strip())))
    out.sort(key=lambda v: -v[0])                              # kararlı
    return out


def _fireplayer_akislari(adres: str, derinlik: int) -> List[Dict[str, str]]:
    """FirePlayer (hdplayersystem/anizmplayer) sayfası → oynatılabilir akışlar.

    `do=getVideo` imzalı bir master veriyor (``md5=…&expires=…``) ve imza
    getVideo'yu çağıran IP'ye bağlı: aynı adres başka IP'den 403 (ölçüldü;
    IPv4/IPv6 farkı bile yeter). Master'ın gösterdiği varyant listeleri
    (/hls/<belirteç>) ise IP'den bağımsız ve en az 25 dk geçerli. Bu yüzden
    master getVideo'yu çağıran AYNI oturumla hemen okunuyor ve oynatıcıya
    varyantlar veriliyor. İstisna: ses ayrı bir izdeyse (TYPE=AUDIO) varyant
    tek başına SESSİZ oynar; o zaman master'ın kendisi döner.
    """
    kimlik = _fireplayer_kimligi(adres)
    if not kimlik:
        return []
    koken = f"{urlparse(adres).scheme or 'https'}://{urlparse(adres).netloc}"
    for _deneme in range(_FIREPLAYER_DENEME):
        oturum = _yeni_oturum()             # getVideo + master aynı bağlantıdan
        yanit = _istek("post", f"{koken}/player/index.php?data={kimlik}&do=getVideo",
                       ne="oynatıcı", oturum=oturum,
                       data={"hash": kimlik, "r": REFERER},
                       headers={"X-Requested-With": "XMLHttpRequest",
                                "Referer": adres, "Origin": koken})
        veri = _json(yanit, "oynatıcı yanıtı")
        if veri.get("videoSrc") and derinlik < 1:
            # Oynatıcı başka bir barındırıcının gömme sayfasını gösteriyor.
            return _konagi_ac(str(veri["videoSrc"]), derinlik + 1)
        master = str(veri.get("securedLink") or "")
        if not (veri.get("hls") and master):
            kaynak = str(veri.get("videoSource") or "")
            return ([{"url": kaynak, "type": "hls" if ".m3u8" in kaynak else "direct",
                      "referer": koken + "/", "alt": ""}] if kaynak else [])
        # Oynatıcının JS'i master'ı getVideo'nun hemen ardından istiyor;
        # arada beklemek imzayı düşürüyor. Ölçüm (2026-09-30): 1.2 sn
        # bekleyen istemcide 25 master'ın 8'i 403 aldı; aynı 8 video
        # beklemeden istenince 8/8 200.
        cevap = _istek("get", master, ne="HLS master", oturum=oturum, bekle=False,
                       headers={"Referer": adres})
        if cevap.status_code == 200 and "#EXTM3U" in (cevap.text or ""):
            if "TYPE=AUDIO" in cevap.text:
                return [{"url": master, "type": "hls", "referer": koken + "/", "alt": "HLS"}]
            varyantlar = varyantlari_ayristir(cevap.text, master)
            if varyantlar:
                return [{"url": u, "type": "hls", "referer": koken + "/",
                         "alt": f"{h}p" if h else "HLS"} for h, u in varyantlar]
            return [{"url": master, "type": "hls", "referer": koken + "/", "alt": "HLS"}]
    # Master hiç okunamadı: imzalı adres yine de verilir. Kullanıcının IP'si
    # sabitse (ev bağlantısı) oynatıcı aynı IP'den açabilir; `best_video`
    # açamazsa sıradakine geçer.
    return [{"url": master, "type": "hls", "referer": koken + "/", "alt": "HLS"}] if master else []


def _anizm_ac(adres: str, derinlik: int) -> List[Dict[str, str]]:
    """anizm.net/player/<id> → asıl barındırıcı.

    anizm.net veri merkezi IP'lerine Cloudflare sınaması gösteriyor. Aynı
    oynatıcı kimlikleri Anizm'in anizle.co aynasında sınamasız ve aynı 302
    yönlendirmesiyle (sibnet, ok.ru, gdrive ya da anizmplayer) çalışıyor —
    Anizle kaynağının kullandığı yol; sınama çözülmüyor, aynaya gidiliyor.
    """
    m = _ANIZM_OYNATICI.match(urlparse(adres).path)
    if not m:
        return []
    from . import anizle as _anizle           # tembel: yalnızca bu barındırıcıda
    for kok in _anizle.aday_kokler()[:2]:
        try:
            yanit = _istek("get", f"{kok}/player/{m.group(1)}", ne="Anizm oynatıcısı",
                           headers={"Referer": kok + "/"}, allow_redirects=False)
        except AnimomHatasi:
            continue
        hedef = (getattr(yanit, "headers", None) or {}).get("location") or ""
        if 300 <= yanit.status_code < 400 and hedef:
            return _konagi_ac(urljoin(kok + "/", hedef), derinlik + 1)
    return []


def _cozum_ister(adres: str) -> bool:
    """Adres oynatılabilir hâle gelmek için ağ isteği istiyor mu?"""
    konak = _konak(urljoin("https:", adres.strip()))
    return any(_konak_eslesir(konak, a)
               for a in ("hdplayersystem.com", "anizmplayer.com", *_ANIZM_KONAKLARI))


def _konagi_ac(adres: str, derinlik: int = 0) -> List[Dict[str, str]]:
    """Video bağlantısını oynatılabilir aday(lar)a çevir."""
    adres = urljoin("https:", adres.strip())          # "//video.sibnet.ru/…" gibi
    if derinlik > 1 or not adres.startswith(("http://", "https://")):
        return []
    konak = _konak(adres)
    if _konak_eslesir(konak, "hdplayersystem.com") or _konak_eslesir(konak, "anizmplayer.com"):
        return _fireplayer_akislari(adres, derinlik)
    if any(_konak_eslesir(konak, a) for a in _ANIZM_KONAKLARI):
        return _anizm_ac(adres, derinlik)
    if _OYNATILAMAZ.search(konak):
        return []
    # ok.ru'nun eski alan adı; yt-dlp çıkarıcısı ok.ru'yu tanıyor.
    adres = re.sub(r"^(https?://)(?:www\.)?odnoklassniki\.ru/", r"\1ok.ru/", adres)
    # Site bazı Drive bağlantılarının sonuna "/prev(iew)" ekliyor
    # ("…/view?usp=drive_link/prev"); yt-dlp dosya kimliğini yoldan okuyor,
    # sorgu dizgisini atmak yetiyor.
    if _konak_eslesir(konak, "drive.google.com"):
        adres = adres.split("?", 1)[0]
    return [{"url": adres, "type": "iframe", "alt": ""}]


def _video_gruplari(sayfa: str, sayfa_adresi: str) -> Tuple[List[Tuple[str, List[Dict[str, Any]]]],
                                                               List[Exception]]:
    """Sekmeler ve videoları: ``[(sekme adı, [video, …])]`` + grup hataları."""
    gruplar = gruplari_ayristir(sayfa)
    sonuc: List[Tuple[str, List[Dict[str, Any]]]] = []
    hatalar: List[Exception] = []
    for kimlik, ad in gruplar:
        try:
            veri = _json(_istek("post", "/get/video/group", ne="video listesi",
                                data={"hash": kimlik},
                                headers=_xhr_basliklari(sayfa_adresi)), "video listesi")
        except AnimomHatasi as exc:
            hatalar.append(exc)
            continue
        videolar = [v for v in (veri.get("videos") or []) if isinstance(v, dict)]
        sonuc.append((ad, videolar))
    if not gruplar:
        # Sekmesiz sayfa: sitenin çizdiği düğmeler (etkin sekmenin videoları).
        gomulu = [{"name": _metin(ad), "link": _html.unescape(link)}
                  for link, ad in _GOMULU_VIDEO.findall(sayfa or "") if link.strip()]
        if gomulu:
            sonuc.append(("", gomulu))
    return sonuc, hatalar


def _dublaj_mi(ad: str) -> bool:
    return "dublaj" in (ad or "").casefold()


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları, denenme sırasıyla.

    Returns: ``[{"url", "label", "type", "fansub", "player", "referer"?}]``.
        Sıra: altyazılı sekmeler dublajdan önce (uygulamanın amacı Türkçe
        altyazı; dublaj yine seçilebilir), sonra ölçülmüş barındırıcı
        güvenilirliği, sonra kalite. `best_video` yalnızca ilk birkaç adayı
        deniyor.

    Raises: `AnimomHatasi` — sayfa alınamadı, bölüm yok ya da videolardan
        hiçbiri çözülemedi (çözülenler varsa hatalı olanlar atlanır).
    """
    yol = _bolum_yolu(episode_id)
    sayfa_adresi = f"{BASE_URL}/anime/{yol}"
    yanit = _istek("get", sayfa_adresi, ne="bölüm sayfası", headers={"Referer": REFERER})
    if yanit.status_code == 404 or (yanit.status_code == 200 and _YOK_IZI in yanit.text[:4000]
                                    and "series-watch" not in yanit.text):
        raise AnimomHatasi(f"AniMOM'da böyle bir bölüm yok: {yol} (HTTP 404)", 404)
    if yanit.status_code != 200:
        raise AnimomHatasi(f"AniMOM bölüm sayfası alınamadı: HTTP {yanit.status_code}",
                           yanit.status_code)

    gruplar, hatalar = _video_gruplari(yanit.text, sayfa_adresi)
    if not gruplar and _HAZIR_DEGIL_IZI in yanit.text:
        print(f"[AniMOM] {yol}: bölümün videosu henüz yüklenmemiş (site: 'çok yakında').")
        return []
    akislar: List[Tuple[Tuple[bool, int, int], Dict[str, str]]] = []
    gorulen = set()
    video_sayisi = cozum = 0
    for grup, videolar in gruplar:
        for video in videolar:
            link = str(video.get("link") or "").strip()
            if video.get("lock") or not link:
                continue            # üyeliğe kilitli (ya da boş) kayıt: kapı aşılmaz
            video_sayisi += 1
            ad = _metin(video.get("name")) or _konak(link)
            if _cozum_ister(link):
                if _OYNATILAMAZ.search(ad) or cozum >= _AZAMI_COZUM:
                    continue        # yönlendirme ölü bir barındırıcıya / bütçe doldu
                cozum += 1
            try:
                adaylar = _konagi_ac(link)
            except Exception as exc:  # tek videonun hatası diğerlerini düşürmesin
                hatalar.append(exc)
                continue
            for aday in adaylar:
                if aday["url"] in gorulen:
                    continue
                gorulen.add(aday["url"])
                oynatici, sira = _oynatici(aday["url"])
                alt = aday.get("alt") or ""
                # Etiket çözünürlükle başlıyor: `best_video`'nun sıralaması onu okuyor.
                parcalar = [alt if alt.endswith("p") else "", grup, ad,
                            alt if alt and not alt.endswith("p") else ""]
                akis = {
                    "url": aday["url"],
                    "label": " ".join(p for p in parcalar if p) or "AniMOM",
                    "type": aday["type"],
                    "fansub": grup or ad,
                    "player": oynatici or ad.upper() or "ANIMOM",
                }
                if aday.get("referer"):
                    akis["referer"] = aday["referer"]
                yukseklik = int(alt[:-1]) if alt[:-1].isdigit() else 0
                akislar.append(((_dublaj_mi(grup), sira, -yukseklik), akis))

    if not akislar and hatalar:
        # Boş liste "oynatılabilir akış yok" demek; videoların bir kısmı
        # hatayla düştüyse bunu iddia edemeyiz — sebebiyle birlikte söyle.
        raise AnimomHatasi(
            f"AniMOM: oynatılabilir akış bulunamadı; {len(hatalar)} video/sekme "
            f"çözülemedi (ilk hata: {hatalar[0]})",
            getattr(hatalar[0], "status_code", None))
    akislar.sort(key=lambda a: a[0])           # kararlı: eşitlerde site sırası
    print(f"[AniMOM] {yol}: {len(akislar)} akış ({video_sayisi} video, "
          f"{len(hatalar)} hata)")
    return [akis for _anahtar, akis in akislar]


def onbellegi_sifirla() -> None:
    """Ortak oturumu, istek saatlerini ve film listesini boşalt (testler)."""
    global _ortak_oturum, _filmler
    with _kilit:
        _ortak_oturum = None
        _son_istek.clear()
    with _film_kilidi:
        _filmler = ((), 0.0)


__all__ = [
    "AnimomHatasi",
    "BASE_URL",
    "search_animom",
    "zengin_ara",
    "get_anime_episodes",
    "get_episode_streams",
    "bolum_adresi",
    "bolum_slugu",
    "arama_sonucunu_ayristir",
    "film_listesini_ayristir",
    "serit_ayristir",
    "ilk_bolumleri_ayristir",
    "gruplari_ayristir",
    "varyantlari_ayristir",
    "onbellegi_sifirla",
]
