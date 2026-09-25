"""
Animeler.pw kaynağı — https://animeler.pw

Türkçe altyazılı anime ve donghua sitesi (eski adı animeler.me; o adres 301 ile
buraya yönleniyor). Arka uç Laravel, önünde Cloudflare var ama JS sınaması yok:
curl_cffi'nin tarayıcı taklidi yetiyor, gerçek tarayıcı gerekmiyor. Eski
WordPress/Kiranime sürümünün `/wp-json/kiranime/...` uçları ölü; aniyomi
eklentilerindeki kod bu siteye artık uymuyor.

Akış (2026-09 ölçümü):

1. Arama     GET  /ajax/search?q=<q>          → JSON, en çok 8 sonuç, alaka sırasıyla
             GET  /filter?search=<q>&page=N   → HTML kartlar (8'den fazlası gerekirse)
2. Bölümler  GET  /<slug>                     → satır içi `var allEpisodesData = [...]`
                                                (görünen ızgara 100 bölümde kesiliyor,
                                                dizi TAM listeyi taşıyor)
3. Akışlar   GET  /<slug>/bolum-<n>           → `var allEpisodeSources = [...]` + csrf-token
                                                (YAVAŞ: sunucu 12-30 sn düşünüyor)
             POST /ajax/get-source-url        → {"url": ".../embed/<id>/<hash>"}
             GET  /embed/<id>/<hash>          → tek `<iframe src="BARINDIRICI">`
4. Barındırıcı:
   - sibnet, mail.ru, gdrive, dailymotion, sendvid, yadi.sk, ok.ru → yt-dlp'ye aynen.
   - FirePlayer (play.animeler.pw, anizmplayer.com "Mugen") → `do=getVideo`;
     protokol Anizle'ninkiyle aynı, `anizle.fireplayer_istegi` yeniden kullanılıyor.
   - loveulikeido.site/e/ → CryptoJS AES ile şifreli ayna listesi; çözülüyor ama
     yalnızca yt-dlp'nin açabildiği aynalar tutuluyor (ölçümde hiçbiri değildi:
     filemoon/voe/lulu/vidhide/savefiles).

Kimlikler: kaynak kimliği sitenin slug'ı ("sousou-no-frieren"), bölüm kimliği
bölüm adresinin yolu ("one-piece/bolum-1161", birleşik bölümde
"naruto-shippuuden/bolum-1-2"). İkisi de tek başına yeterli; sezon ayrı slug.

Hata sözleşmesi: arama "sonuç yok"ta `[]` döner; ağ/HTTP/ayrıştırma hataları
(aramada da) `AnimelerHatasi` ile YÜKSELİR. Arama motoru hatayı kaynak başına
yakalayıp "aranamadı" diye gösteriyor, sunucu tarayıcısı da `status_code`'a
bakıp geçici/kalıcı ayrımı yapıyor; boş liste döndürmek ikisini de kör ederdi.
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
from urllib.parse import quote, urljoin, urlparse

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi zorunlu bağımlılık
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

# FirePlayer istemcisi ve Cloudflare iz listesi Anizle'de; ikinci kopya yazılmıyor.
from . import anizle as _anizle

BASE_URL = "https://animeler.pw"

# Zaman aşımları (saniye). Arama motorunun toplam bütçesi 25 sn
# (`common.adapters.OVERALL_SEARCH_TIMEOUT`); arama istekleri onun altında
# kalmalı. Bölüm sayfası ise normalde 12-30 sn sürüyor: uzun süre YALNIZCA ona.
SEARCH_TIMEOUT = 15
PAGE_TIMEOUT = 30
EPISODE_TIMEOUT = 90
PLAYER_TIMEOUT = 20

# Kaynak çözümünde paralel istek sayısı. Yalnızca küçük uçlar (POST, embed,
# FirePlayer) paralel; sayfa yüklemeleri hep sıralı. Ölçüm: ~6 paralel sayfa
# yüklemesinden sonra site 10 dakika boyunca 502/504 döndü.
MAX_WORKERS = 3

# Aynı fansub + aynı sunucu adından en çok kaç kaynak çözülsün. JJK 1. bölüm
# 52 kaynak veriyor, 19'u "Sibnet": hepsini çözmek ~100 istek ve kırılgan siteye
# gereksiz yük. `best_video` zaten yalnızca ilk birkaç adayı deniyor.
SUNUCU_BASINA_AZAMI = 3

# Arama 8'den fazla sonuç isterse /filter sayfalarından kaç tanesine bakılsın.
FILTRE_AZAMI_SAYFA = 2

# Yetişkin içerik: aramada gösterilmiyor. Sitenin tür filtresi (genre[]=…)
# birden çok türü VE ile birleştiriyor, bu yüzden türler tek tek taranıyor.
YETISKIN_TURLERI = ("Hentai", "Erotica")
_YETISKIN_ETIKETLERI = frozenset({"hentai", "erotica", "erotik"})
_YETISKIN_AZAMI_SAYFA = 5
_YETISKIN_TAZELIK = 12 * 3600        # liste nadiren değişiyor
_YETISKIN_HATA_BEKLEMESI = 10 * 60   # alınamadıysa her aramada yeniden deneme
_YETISKIN_ZAMAN_ASIMI = 8            # sayfa başına; normalde ~2 sn

_UA_YEDEK = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_AJAX_BASLIKLARI = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": BASE_URL + "/",
}

# Aşırı yüklenmiş sitenin geçici yanıtları (Cloudflare 52x = kaynak sunucu
# yanıt vermedi). 503 burada engel değil: ölçümde sitenin kendi taşmasıydı.
_GECICI_DURUMLAR = frozenset({500, 502, 503, 504, 520, 521, 522, 523, 524})
# Bu kadar sürede düşen istek bir kez daha denenir; daha uzun sürüp düşen
# denenmez — site zaten boğulmuş, üstüne yük bindirmek onu daha da yavaşlatır.
_HIZLI_HATA_SN = 10.0


class AnimelerHatasi(RuntimeError):
    """Animeler'den beklenen veri alınamadı (ağ, HTTP durumu, sayfa yapısı).

    ``status_code``: varsa HTTP durumu. Sunucu tarayıcısı
    (`turkanime_server.crawler.nezaket.hata_turu`) bu alanı okuyup 404'ü
    kalıcı, 5xx'i geçici, 403/429'u engellenme sayıyor.
    """

    def __init__(self, mesaj: str, status_code: Optional[int] = None):
        super().__init__(mesaj)
        self.status_code = status_code


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
_oturum_kilidi = threading.Lock()
_ortak_oturum: Any = None


def _yeni_oturum() -> Any:
    if _HAS_CURL:
        return _http.Session(impersonate="chrome131")
    oturum = _http.Session()
    oturum.headers.update({"User-Agent": _UA_YEDEK})
    return oturum


def _oturum() -> Any:
    """Arama ve bölüm listesi için ortak oturum (bağlantı yeniden kullanılsın)."""
    global _ortak_oturum
    with _oturum_kilidi:
        if _ortak_oturum is None:
            _ortak_oturum = _yeni_oturum()
        return _ortak_oturum


def _engel_mi(yanit: Any) -> bool:
    """Yanıt Cloudflare/WAF engeli mi? (boş sonuç değil, erişim reddi)"""
    durum = getattr(yanit, "status_code", 200)
    if durum in _anizle.ENGEL_DURUMLARI and durum != 503:
        return True
    try:
        bas = (yanit.text or "")[:4000].lower()
    except Exception:
        return False
    return any(iz.lower() in bas for iz in _anizle.CHALLENGE_MARKERS)


def _istek(oturum: Any, yontem: str, url: str, *, zaman_asimi: float, ne: str,
           **kw: Any) -> Any:
    """Zaman aşımlı GET/POST; hızlı düşen geçici hata bir kez yeniden denenir.

    Engel ya da ağ hatası `AnimelerHatasi` olarak yükselir; HTTP durumu
    çağırana bırakılır (404 kimi yerde "yok", kimi yerde hata).
    """
    for deneme in range(2):
        baslangic = time.monotonic()
        try:
            yanit = getattr(oturum, yontem)(url, timeout=zaman_asimi, **kw)
        except Exception as exc:
            gecen = time.monotonic() - baslangic
            if deneme or gecen > _HIZLI_HATA_SN:
                sure = " (zaman aşımı)" if gecen >= zaman_asimi * 0.9 else ""
                raise AnimelerHatasi(
                    f"Animeler'e ulaşılamadı — {ne}{sure}: {exc}") from exc
            continue
        if _engel_mi(yanit):
            raise AnimelerHatasi(
                f"Animeler isteği engelledi — {ne}: HTTP {yanit.status_code} "
                "(Cloudflare/WAF doğrulaması ya da erişim reddi).", yanit.status_code)
        if (yanit.status_code in _GECICI_DURUMLAR and not deneme
                and time.monotonic() - baslangic < _HIZLI_HATA_SN):
            continue
        return yanit
    return yanit  # pragma: no cover - döngü her yolda döner


def _basarili(yanit: Any, ne: str) -> Any:
    if yanit.status_code != 200:
        raise AnimelerHatasi(f"Animeler {ne} alınamadı: HTTP {yanit.status_code}",
                             yanit.status_code)
    return yanit


def _json(yanit: Any, ne: str) -> Any:
    try:
        return yanit.json()
    except ValueError as exc:
        raise AnimelerHatasi(
            f"Animeler {ne} yanıtı JSON değil; site yapısı değişmiş olabilir.") from exc


def _metin(deger: Any) -> str:
    return " ".join(_html.unescape(str(deger or "")).split())


# ─────────────────────────────────────────────────────────────────────────────
# Ayrıştırıcılar (ağsız; testler gerçek sayfa örnekleriyle sınıyor)
# ─────────────────────────────────────────────────────────────────────────────
def ajax_aramasini_ayristir(veri: Any) -> List[Tuple[str, str]]:
    """`/ajax/search` JSON'undan ``[(slug, başlık)]`` (sitenin alaka sırasıyla).

    "Comic" kategorisi atılıyor: sitede manga da var, oynatılacak bölümü yok.
    """
    if not isinstance(veri, dict):
        raise AnimelerHatasi("Animeler arama yanıtı beklenen biçimde değil.")
    sonuc: List[Tuple[str, str]] = []
    for kayit in veri.get("results") or []:
        if not isinstance(kayit, dict):
            continue
        slug = str(kayit.get("slug") or "").strip()
        baslik = _metin(kayit.get("title"))
        if not slug or not baslik:
            continue
        if str(kayit.get("category") or "").strip().lower() == "comic":
            continue
        sonuc.append((slug, baslik))
    return sonuc


_KART = re.compile(r'<a href="https?://animeler\.pw/([a-z0-9][a-z0-9\-]*)"\s+'
                   r'class="anime-card-modern">')
_KART_BASLIK = re.compile(r'<span class="tooltip-title">([^<]*)</span>')
_KART_TURLER = re.compile(r'<div class="tooltip-tags">([^<]*)</div>')


def filtre_kartlarini_ayristir(sayfa: str) -> List[Tuple[str, str, List[str]]]:
    """`/filter` sayfasındaki kartlardan ``[(slug, başlık, türler)]``."""
    konumlar = list(_KART.finditer(sayfa))
    sonuc: List[Tuple[str, str, List[str]]] = []
    for i, kart in enumerate(konumlar):
        # Kartın alanı bir sonraki karta kadar: başlığı olmayan kart komşusunun
        # başlığını almasın.
        son = konumlar[i + 1].start() if i + 1 < len(konumlar) else kart.end() + 6000
        parca = sayfa[kart.end():son]
        baslik = _KART_BASLIK.search(parca)
        if not baslik:
            continue
        turler = _KART_TURLER.search(parca)
        sonuc.append((kart.group(1), _metin(baslik.group(1)),
                      [_metin(t) for t in turler.group(1).split(",")] if turler else []))
    return sonuc


def sonraki_sayfa_var(sayfa: str) -> bool:
    return bool(re.search(r'<a[^>]+rel="next"', sayfa))


def _yetiskin_mi(turler: List[str]) -> bool:
    return any(t.lower() in _YETISKIN_ETIKETLERI for t in turler)


_TUM_BOLUMLER = re.compile(r"var\s+allEpisodesData\s*=\s*\[(.*?)\]\s*;", re.S)
# Anahtarlar tırnaksız (JS nesnesi), JSON olarak okunamıyor.
_BOLUM_NESNESI = re.compile(
    r'\{\s*id:\s*(\d+),\s*url:\s*"([^"]+)",\s*start:\s*([\d.]+),\s*end:\s*([\d.]+),'
    r'\s*display:\s*"([^"]*)",\s*title:\s*"((?:[^"\\]|\\.)*)"\s*\}')
# Yol parçaları yalnızca küçük harf/rakam/nokta/tire: kimlik doğrudan adrese
# ekleniyor, "../" ya da boşluk geçmemeli. "bolum-1-2" (birleşik) dahil.
_BOLUM_YOLU = re.compile(r"^[a-z0-9][a-z0-9\-]*/bolum-[a-z0-9][a-z0-9.\-]*$")


def bolum_listesini_ayristir(sayfa: str, slug: str = "") -> List[Tuple[str, str]]:
    """Anime sayfasından izleme sırasıyla ``[(bolum_id, "N. Bölüm")]``.

    Sıra `start, end` ile: dizi sayfadaki sırayla geliyor ve birleşik bölümler
    ("215-216") araya giriyor. Dizi yoksa sayfa yapısı değişmiştir → hata;
    dizi var ama boşsa henüz bölümü olmayan anime → boş liste.
    """
    dizi = _TUM_BOLUMLER.search(sayfa)
    girdiler = _BOLUM_NESNESI.findall(dizi.group(1)) if dizi else []
    if not girdiler:
        # Sezonlu sayfada liste `seasonEpisodesData`'ya taşınabilir (13 örnekte
        # hep boştu). Nesne kalıbı yeterince özgül; bütün sayfada aranıyor ama
        # yalnızca bu animenin bölümleri alınıyor.
        girdiler = [g for g in _BOLUM_NESNESI.findall(sayfa)
                    if not slug or urlparse(g[1]).path.strip("/").startswith(slug + "/")]
        if not girdiler and not dizi:
            raise AnimelerHatasi(
                "Animeler anime sayfasında bölüm listesi (allEpisodesData) yok; "
                "site yapısı değişmiş olabilir.")
        if not girdiler and dizi.group(1).strip():
            # Dizi dolu ama hiçbir girdi kalıba uymuyor: biçim değişmiş. Boş
            # liste "bu animenin bölümü yok" demek olurdu — yanlış.
            raise AnimelerHatasi(
                "Animeler bölüm listesi okunamadı; girdilerin biçimi değişmiş.")
    sirali: List[Tuple[Tuple[float, float], str, str]] = []
    for _kimlik, adres, bas, son, gosterim, _baslik in girdiler:
        yol = urlparse(adres).path.strip("/")
        if not _BOLUM_YOLU.match(yol):
            continue
        try:
            anahtar = (float(bas), float(son))
        except ValueError:
            continue
        sirali.append((anahtar, yol, gosterim.strip() or yol.rsplit("bolum-", 1)[-1]))
    if girdiler and not sirali:
        raise AnimelerHatasi(
            "Animeler bölüm adresleri beklenen biçimde değil ('<slug>/bolum-N').")
    sirali.sort(key=lambda s: s[0])            # kararlı: eşitlerde sayfa sırası
    sonuc: List[Tuple[str, str]] = []
    gorulen: Set[str] = set()
    for _anahtar, yol, gosterim in sirali:
        if yol in gorulen:
            continue
        gorulen.add(yol)
        sonuc.append((yol, f"{gosterim}. Bölüm"))
    return sonuc


@dataclass
class BolumSayfasi:
    """Bölüm sayfasından çıkan, akış çözümü için gereken her şey."""

    kaynaklar: List[Dict[str, Any]]
    gruplar: Dict[int, str] = field(default_factory=dict)
    token: Optional[str] = None
    varsayilan_embed: Optional[str] = None
    kanonik: Optional[str] = None


_KAYNAK_DIZISI = re.compile(r"var\s+allEpisodeSources\s*=\s*")
_FANSUB_GRUBU = re.compile(
    r'data-fansub-group-id="(\d+)"(?:(?!data-fansub-group-id=).)*?'
    r'<span class="fansub-group-name">([^<]*)</span>', re.S)
_CSRF = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
_VARSAYILAN_EMBED = re.compile(r'id="fansubPlayerIframe"[^>]*?(?<![\w-])src="([^"]+)"')
_KANONIK = re.compile(r'<link rel="canonical" href="([^"]+)"')


def bolum_sayfasini_ayristir(sayfa: str) -> BolumSayfasi:
    """Bölüm sayfasındaki kaynak listesi, fansub adları, CSRF ve kanonik yol."""
    konum = _KAYNAK_DIZISI.search(sayfa)
    if not konum:
        raise AnimelerHatasi(
            "Animeler bölüm sayfasında kaynak listesi (allEpisodeSources) yok; "
            "site yapısı değişmiş olabilir.")
    try:
        # Dizi geçerli JSON; `raw_decode` sonunu kendisi buluyor (regex'le
        # "];" aramak, sunucu adında "]" geçerse diziyi keserdi).
        kaynaklar, _ = json.JSONDecoder().raw_decode(sayfa, konum.end())
    except ValueError as exc:
        raise AnimelerHatasi("Animeler kaynak listesi okunamadı (bozuk JSON).") from exc
    if not isinstance(kaynaklar, list):
        raise AnimelerHatasi("Animeler kaynak listesi beklenen biçimde değil.")
    kanonik = _KANONIK.search(sayfa)
    varsayilan = _VARSAYILAN_EMBED.search(sayfa)
    token = _CSRF.search(sayfa)
    return BolumSayfasi(
        kaynaklar=[k for k in kaynaklar if isinstance(k, dict) and k.get("id")],
        gruplar={int(i): _metin(ad) for i, ad in _FANSUB_GRUBU.findall(sayfa)},
        token=token.group(1) if token else None,
        varsayilan_embed=_html.unescape(varsayilan.group(1)) if varsayilan else None,
        kanonik=(urlparse(_html.unescape(kanonik.group(1))).path.strip("/")
                 if kanonik else None),
    )


_IFRAME = re.compile(r'<iframe\b[^>]*?(?<![\w-])src="([^"]+)"', re.I)
_KAYNAK_ETIKETI = re.compile(r'<source\b[^>]*?(?<![\w-])src="([^"]+)"', re.I)


def embed_adresi(sayfa: str, taban: str = BASE_URL) -> Optional[Tuple[str, str]]:
    """Embed sayfasındaki oynatıcı: ``("iframe", adres)`` ya da ``("video", adres)``."""
    for tur, kalip in (("iframe", _IFRAME), ("video", _KAYNAK_ETIKETI)):
        eslesme = kalip.search(sayfa)
        if eslesme and eslesme.group(1).strip():
            return tur, urljoin(taban, _html.unescape(eslesme.group(1).strip()))
    return None


def _aes() -> Any:
    """pycryptodome'un AES'i; kurulu değilse None.

    Sunucu tarayıcısının imajı pycryptodome'u BİLEREK kurmuyor
    (turkanime_server/requirements.txt, tests/test_server_dagitim.py). Şifreli
    aynalar isteğe bağlı bir ek (ölçümde hiçbiri oynatılamıyordu); onlar yüzünden
    imaja paket eklenmesin, yoksa yalnızca o adım atlansın.
    """
    try:
        from Crypto.Cipher import AES
    except ImportError:
        return None
    return AES


def cryptojs_coz(sifreli: str, parola: str) -> str:
    """`CryptoJS.AES.decrypt(metin, parola)`'nın karşılığı.

    CryptoJS parola verilince OpenSSL "Salted__" biçimini kullanır: base64
    içinde 8 bayt tuz, anahtar+IV ise EVP_BytesToKey(MD5) ile türetilir;
    AES-256-CBC + PKCS7. `bypass.decrypt_cipher` başka bir biçim ({ct, iv, s}
    JSON'u) bekliyor ve kapanan siteye ait; burada kullanılamaz.
    """
    AES = _aes()  # pylint: disable=invalid-name
    if AES is None:
        raise ValueError("pycryptodome kurulu değil; CryptoJS şifresi çözülemiyor")

    ham = base64.b64decode(sifreli)
    if ham[:8] != b"Salted__" or len(ham) < 32 or (len(ham) - 16) % 16:
        raise ValueError("CryptoJS tuzlu şifre biçimi değil")
    tuz, govde = ham[8:16], ham[16:]
    tureme, onceki = b"", b""
    while len(tureme) < 48:
        onceki = hashlib.md5(onceki + parola.encode("utf-8") + tuz).digest()
        tureme += onceki
    acik = AES.new(tureme[:32], AES.MODE_CBC, tureme[32:48]).decrypt(govde)
    dolgu = acik[-1]
    if not 1 <= dolgu <= 16 or acik[-dolgu:] != bytes([dolgu]) * dolgu:
        raise ValueError("PKCS7 dolgusu bozuk (parola yanlış olabilir)")
    return acik[:-dolgu].decode("utf-8")


_AYNA_PAROLASI = re.compile(r"CryptoJS\.AES\.decrypt\(\s*\w+\s*,\s*'([^']+)'")
_AYNA_VERISI = re.compile(r"const\s+dataLink\s*=\s*")


def sifreli_aynalari_coz(sayfa: str) -> List[Tuple[str, str, str]]:
    """loveulikeido.site/e/ sayfasındaki şifreli aynalar: ``[(sunucu, tür, adres)]``."""
    parola = _AYNA_PAROLASI.search(sayfa)
    konum = _AYNA_VERISI.search(sayfa)
    if not parola or not konum:
        raise AnimelerHatasi("Ayna sayfasında şifreli liste (dataLink) bulunamadı.")
    try:
        veri, _ = json.JSONDecoder().raw_decode(sayfa, konum.end())
    except ValueError as exc:
        raise AnimelerHatasi("Ayna listesi okunamadı (bozuk JSON).") from exc
    gommeler = ((veri or {}).get("data") or {}).get("embeds") or []
    sonuc: List[Tuple[str, str, str]] = []
    for gomme in gommeler:
        try:
            adres = cryptojs_coz(gomme["link"], parola.group(1)).strip()
        except (KeyError, TypeError, ValueError):
            continue                          # tek bozuk ayna ötekileri düşürmesin
        sonuc.append((str(gomme.get("servername") or ""), str(gomme.get("type") or ""),
                      adres))
    return sonuc


# ─────────────────────────────────────────────────────────────────────────────
# Barındırıcılar
# ─────────────────────────────────────────────────────────────────────────────
# (konak, oynatıcı adı, deneme sırası). Sıra yt-dlp 2026.08.19 ile ÖLÇÜLDÜ:
# sibnet/mail.ru/gdrive/dailymotion/sendvid/yadi.sk gerçek video baytı verdi;
# ok.ru her örnekte "yazar engelli" ya da çıkarıcı hatasıyla düştü, en sona.
# Adlar `common.oynatici_onceligi` ile aynı (ilerleme etiketinde görünüyor).
_OYNATICILAR: Tuple[Tuple[str, str, int], ...] = (
    ("sibnet.ru", "SIBNET", 0),
    ("mail.ru", "MAIL", 1),
    ("anizmplayer.com", "MUGEN", 2),
    ("drive.google.com", "GDRIVE", 3),
    ("dailymotion.com", "DAILYMOTION", 4),
    ("yadi.sk", "YADISK", 5),
    ("disk.yandex.ru", "YADISK", 5),
    ("disk.yandex.com", "YADISK", 5),
    ("sendvid.com", "SENDVID", 6),
    ("ok.ru", "ODNOKLASSNIKI", 8),
    ("odnoklassniki.ru", "ODNOKLASSNIKI", 8),
)
_BILINMEYEN_SIRA = 7

# yt-dlp'nin çıkarıcısı olmayan ya da 403/Cloudflare dönen barındırıcılar
# (ölçüldü). Bunları döndürmek `best_video`'nun sınırlı deneme bütçesini yer.
# Yalnızca KONAK adına uygulanır (sorgu dizgisindeki bir "dood" eşleşmesin).
_OYNATILAMAZ = re.compile(
    r"voe\.sx|filemoon|bysekoze|byse\.|luluvdo|lulustream|ryderjet|vidhide|"
    r"savefiles|streamwish|dood|streamtape|loveulikeido", re.I)


def _konak(adres: str) -> str:
    return (urlparse(adres).hostname or "").lower()


def _konak_eslesir(konak: str, alan: str) -> bool:
    return konak == alan or konak.endswith("." + alan)


def _oynatici(adres: str) -> Tuple[Optional[str], int]:
    """Adresin oynatıcı adı ve deneme sırası; tanınmıyorsa ``(None, 7)``."""
    konak = _konak(adres)
    for alan, ad, sira in _OYNATICILAR:
        if _konak_eslesir(konak, alan):
            return ad, sira
    return None, _BILINMEYEN_SIRA


def _fireplayer_mi(adres: str) -> bool:
    parca = urlparse(adres)
    if "/fireplayer/video/" in parca.path:
        return True
    return (_konak_eslesir(_konak(adres), "anizmplayer.com")
            and parca.path.startswith(("/video/", "/player/")))


def _koken(adres: str) -> str:
    parca = urlparse(adres)
    return f"{parca.scheme}://{parca.netloc}"


@dataclass
class _Aday:
    """Bir kaynağın çözümünden çıkan oynatılabilir adres (etiketsiz)."""

    url: str
    tur: str
    referer: Optional[str] = None
    alt_ad: str = ""


def _fireplayer_ac(oturum: Any, adres: str, derinlik: int) -> List[_Aday]:
    """FirePlayer sayfasının bütün sunucularını (s0, s2, …) aç.

    İlk `getVideo` varsayılan sunucuyu ve `sourceList`'i veriyor; diğerleri
    aynı uca ``s=<anahtar>`` ile tek tek soruluyor. Protokol Anizle'ninkiyle
    aynı (`anizle.fireplayer_istegi`), yalnızca sayfa adresi farklı.
    """
    md5 = urlparse(adres).path.rstrip("/").rsplit("/", 1)[-1]
    koken = _koken(adres)

    def iste(anahtar: str) -> Optional[Dict[str, Any]]:
        return _anizle.fireplayer_istegi(
            adres, form={"hash": md5, "r": BASE_URL + "/", "s": anahtar},
            timeout=PLAYER_TIMEOUT)

    ilk = iste("")
    if not ilk:
        raise AnimelerHatasi(f"FirePlayer yanıt vermedi: {adres}")
    adlar = ilk.get("sourceList") if isinstance(ilk.get("sourceList"), dict) else {}
    yanitlar = [ilk]
    for anahtar in adlar:
        if anahtar != ilk.get("sIndex"):
            ek = iste(str(anahtar))
            if ek:
                yanitlar.append(ek)

    adaylar: List[_Aday] = []
    for veri in yanitlar:
        alt_ad = _metin(adlar.get(veri.get("sIndex"), ""))
        if veri.get("videoSrc"):
            # Başka bir barındırıcının gömme sayfası (sibnet, ok.ru, voe…).
            for aday in _konagi_ac(oturum, str(veri["videoSrc"]), derinlik + 1):
                aday.alt_ad = aday.alt_ad or alt_ad
                adaylar.append(aday)
            continue
        akis = _anizle.fireplayer_akisi(veri, "", referer=koken + "/")
        if akis:
            adaylar.append(_Aday(akis["url"], akis["type"], akis.get("referer"),
                                 alt_ad or ("HLS" if akis["type"] == "hls" else "")))
    return adaylar


def _aynalari_ac(oturum: Any, adres: str, derinlik: int) -> List[_Aday]:
    """loveulikeido.site/e/ → şifresi çözülmüş aynalardan oynatılabilir olanlar.

    Yalnızca TANINAN oynatıcılar tutuluyor (kara liste değil beyaz liste):
    ayna listesi sitenin değil üçüncü tarafın; oraya yeni bir barındırıcı
    eklendiğinde deneme bütçesini bilinmeyen bir adrese harcamayalım.
    """
    if _aes() is None:
        return []           # şifre çözülemeyecekse sayfayı hiç isteme (sunucu imajı)
    yanit = _basarili(_istek(oturum, "get", adres, zaman_asimi=PLAYER_TIMEOUT,
                             ne="ayna sayfası", headers={"Referer": BASE_URL + "/"}),
                      "ayna sayfası")
    adaylar: List[_Aday] = []
    for sunucu, tur, ayna in sifreli_aynalari_coz(yanit.text):
        if tur != "video":                         # "file": indirme sayfası
            continue
        for aday in _konagi_ac(oturum, ayna, derinlik + 1):
            if _oynatici(aday.url)[0]:
                aday.alt_ad = aday.alt_ad or _metin(sunucu)
                adaylar.append(aday)
    return adaylar


def _konagi_ac(oturum: Any, adres: str, derinlik: int = 0) -> List[_Aday]:
    """Embed'deki barındırıcı adresini oynatılabilir adaylara çevir.

    `derinlik`: FirePlayer ya da ayna sayfası başka bir barındırıcıya
    yönlendirebiliyor; iki adımdan derini döngü sayılıp kesiliyor.
    """
    adres = urljoin("https:", adres.strip())      # "//video.sibnet.ru/…" gibi
    if derinlik > 1:
        return []
    if _fireplayer_mi(adres):
        return _fireplayer_ac(oturum, adres, derinlik)
    if _konak_eslesir(_konak(adres), "loveulikeido.site") and "/e/" in urlparse(adres).path:
        return _aynalari_ac(oturum, adres, derinlik)
    # ok.ru'nun eski alan adı; yt-dlp çıkarıcısı ok.ru'yu tanıyor.
    adres = re.sub(r"^(https?://)(?:www\.)?odnoklassniki\.ru/", r"\1ok.ru/", adres)
    if _OYNATILAMAZ.search(_konak(adres)):
        return []
    return [_Aday(adres, "iframe")]


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
_yetiskin_kilidi = threading.Lock()
_yetiskin: Tuple[FrozenSet[str], float] = (frozenset(), 0.0)


def _filtre_sayfasi(oturum: Any, parametreler: Dict[str, Any], zaman_asimi: float) -> str:
    yanit = _istek(oturum, "get", f"{BASE_URL}/filter", zaman_asimi=zaman_asimi,
                   ne="filtre sayfası", params=parametreler,
                   headers={"Referer": BASE_URL + "/"})
    return _basarili(yanit, "filtre sayfası").text


def _yetiskin_sluglari(oturum: Any, zaman_asimi: float) -> FrozenSet[str]:
    """Yetişkin türlerindeki bütün animelerin slug'ları (önbellekli).

    `/ajax/search` tür vermiyor; tür yalnızca /filter kartlarında. Her aramada
    iki tür sayfası daha istemek yerine liste bir kez çekiliyor (ölçüm: ~50
    anime, 3 sayfa, ~6 sn). Kilit ağ isteği boyunca tutuluyor: paralel
    aramalar listeyi aynı anda ikinci kez çekmesin (kırılgan site).
    Alınamazsa arama yine de sürer — elde olanla süzülür ve bir süre sonra
    yeniden denenir.
    """
    global _yetiskin
    with _yetiskin_kilidi:
        kume, gecerlilik = _yetiskin
        if time.monotonic() < gecerlilik:
            return kume
        yeni: Set[str] = set()
        try:
            for tur in YETISKIN_TURLERI:
                for sayfa_no in range(1, _YETISKIN_AZAMI_SAYFA + 1):
                    sayfa = _filtre_sayfasi(oturum, {"genre[]": tur, "page": sayfa_no},
                                            zaman_asimi)
                    yeni.update(slug for slug, _b, _t in filtre_kartlarini_ayristir(sayfa))
                    if not sonraki_sayfa_var(sayfa):
                        break
        except AnimelerHatasi as exc:
            print(f"[Animeler] Yetişkin tür listesi alınamadı, eldekiyle "
                  f"süzülüyor: {exc}")
            _yetiskin = (frozenset(kume | yeni),
                         time.monotonic() + _YETISKIN_HATA_BEKLEMESI)
            return _yetiskin[0]
        _yetiskin = (frozenset(yeni), time.monotonic() + _YETISKIN_TAZELIK)
        return _yetiskin[0]


def search_animeler(query: str, limit: int = 20,
                    timeout: float = SEARCH_TIMEOUT) -> List[Tuple[str, str]]:
    """Animeler'de ara: ``[(slug, başlık)]``, sitenin alaka sırasıyla.

    Sonuç yoksa ``[]``; site yanıt vermezse `AnimelerHatasi`. Sitenin kendi
    araması 2 karakterden kısa sorguya bakmıyor, burada da bakılmıyor.
    """
    sorgu = " ".join(str(query or "").split())
    if len(sorgu) < 2 or limit <= 0:
        return []
    oturum = _oturum()
    yanit = _istek(oturum, "get", f"{BASE_URL}/ajax/search", zaman_asimi=timeout,
                   ne="arama", params={"q": sorgu}, headers=_AJAX_BASLIKLARI)
    veri = _json(_basarili(yanit, "arama"), "arama")
    ham = ajax_aramasini_ayristir(veri)
    try:
        toplam = int(veri.get("total") or 0)
    except (TypeError, ValueError):
        toplam = 0
    # Yetişkin listesi yalnızca süzülecek bir şey varsa çekiliyor; kısa süre
    # sınırı arama motorunun 25 sn'lik toplam bütçesini korumak için.
    yetiskin = (_yetiskin_sluglari(oturum, min(timeout, _YETISKIN_ZAMAN_ASIMI))
                if toplam or ham else frozenset())
    sonuc = [cift for cift in ham if cift[0] not in yetiskin]
    if len(sonuc) < limit and toplam > len(veri.get("results") or []):
        sonuc = _filtreyle_tamamla(oturum, sorgu, sonuc, limit, yetiskin, timeout)
    return sonuc[:limit]


def _filtreyle_tamamla(oturum: Any, sorgu: str, sonuc: List[Tuple[str, str]], limit: int,
                       yetiskin: FrozenSet[str], zaman_asimi: float) -> List[Tuple[str, str]]:
    """Hızlı arama 8'de kesiliyor; kalanı /filter kartlarından tamamla.

    Kartlar alaka sırasında değil (arama motoru zaten yeniden sıralıyor). Bu
    ek adım düşerse eldeki sonuçlar yine döner: kullanıcı en alakalı 8'i
    zaten gördü, hata onları götürmemeli.
    """
    sonuc = list(sonuc)
    gorulen = {slug for slug, _ in sonuc}
    try:
        for sayfa_no in range(1, FILTRE_AZAMI_SAYFA + 1):
            sayfa = _filtre_sayfasi(oturum, {"search": sorgu, "page": sayfa_no}, zaman_asimi)
            for slug, baslik, turler in filtre_kartlarini_ayristir(sayfa):
                if slug in gorulen or slug in yetiskin or _yetiskin_mi(turler):
                    continue
                gorulen.add(slug)
                sonuc.append((slug, baslik))
            if len(sonuc) >= limit or not sonraki_sayfa_var(sayfa):
                break
    except AnimelerHatasi as exc:
        print(f"[Animeler] Ek arama sayfası alınamadı, ilk sonuçlar dönüyor: {exc}")
    return sonuc


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def _anime_slugu(kimlik: str) -> str:
    """"sousou-no-frieren", "/sousou-no-frieren/", tam adres → "sousou-no-frieren"."""
    metin = str(kimlik or "").strip()
    if metin.startswith(("http://", "https://")):
        metin = urlparse(metin).path
    slug = metin.strip("/").split("/")[0]
    if not slug or re.search(r"\s", slug):
        raise AnimelerHatasi(f"Geçersiz Animeler anime kimliği: {kimlik!r}", 400)
    return slug


def get_anime_episodes(slug: str, timeout: float = PAGE_TIMEOUT) -> List[Tuple[str, str]]:
    """Animenin bölümleri, izleme sırasıyla: ``[("slug/bolum-N", "N. Bölüm")]``.

    Anime yoksa (HTTP 404) ya da sayfa okunamazsa `AnimelerHatasi`.
    """
    kimlik = _anime_slugu(slug)
    yanit = _istek(_oturum(), "get", f"{BASE_URL}/{quote(kimlik)}", zaman_asimi=timeout,
                   ne=f"'{kimlik}' sayfası", headers={"Referer": BASE_URL + "/"})
    if yanit.status_code == 404:
        raise AnimelerHatasi(f"Animeler'de '{kimlik}' adlı bir anime yok (HTTP 404).", 404)
    return bolum_listesini_ayristir(_basarili(yanit, f"'{kimlik}' sayfası").text, kimlik)


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _bolum_yolu(bolum_id: str) -> str:
    metin = str(bolum_id or "").strip()
    if metin.startswith(("http://", "https://")):
        metin = urlparse(metin).path
    yol = metin.strip("/")
    if not _BOLUM_YOLU.match(yol):
        raise AnimelerHatasi(
            f"Geçersiz Animeler bölüm kimliği: {bolum_id!r} "
            "(beklenen: 'anime-slug/bolum-N')", 400)
    return yol


def _kaynaklari_sec(kaynaklar: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aynı fansub + sunucu adından en çok `SUNUCU_BASINA_AZAMI` kaynak (site sırasıyla)."""
    sayac: Dict[Tuple[Any, str], int] = {}
    secilen: List[Dict[str, Any]] = []
    for kaynak in kaynaklar:
        anahtar = (kaynak.get("fansub_group_id"),
                   str(kaynak.get("server_name") or "").strip().lower())
        if sayac.get(anahtar, 0) >= SUNUCU_BASINA_AZAMI:
            continue
        sayac[anahtar] = sayac.get(anahtar, 0) + 1
        secilen.append(kaynak)
    return secilen


def _embed_al(oturum: Any, sayfa: BolumSayfasi, sayfa_adresi: str,
              kaynak: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Kaynağın embed adresi: ``("embed", adres)``, ``("iframe"|"video", adres)`` ya da None.

    Varsayılan kaynağın embed'i sayfada hazır. Diğerleri için site, sayfanın
    CSRF jetonuyla POST istiyor; jeton oturuma bağlı olduğundan POST, sayfayı
    çeken AYNI oturumdan gitmeli (yoksa HTTP 419 "Session has expired").
    """
    kimlik = str(kaynak["id"])
    if sayfa.varsayilan_embed and f"/embed/{kimlik}/" in sayfa.varsayilan_embed:
        return "embed", sayfa.varsayilan_embed
    if not sayfa.token:
        raise AnimelerHatasi("Bölüm sayfasında CSRF jetonu yok; kaynak adresi istenemiyor.")
    yanit = _istek(oturum, "post", f"{BASE_URL}/ajax/get-source-url",
                   zaman_asimi=PLAYER_TIMEOUT, ne="kaynak adresi",
                   data={"_token": sayfa.token, "source_id": kimlik},
                   headers={**_AJAX_BASLIKLARI, "Referer": sayfa_adresi, "Origin": BASE_URL})
    if yanit.status_code == 419:
        raise AnimelerHatasi("Animeler oturum doğrulamasını (CSRF) reddetti: HTTP 419", 419)
    veri = _json(_basarili(yanit, "kaynak adresi"), "kaynak adresi")
    if not isinstance(veri, dict) or not veri.get("success") or not veri.get("url"):
        # "Kaynak bulunamadı": site kaynağı kaldırmış; hata değil, atlanır.
        return None
    adres = urljoin(BASE_URL, str(veri["url"]))
    if _konak(adres) == _konak(BASE_URL) and urlparse(adres).path.startswith("/embed/"):
        return "embed", adres
    return ("iframe" if str(veri.get("type") or "iframe") == "iframe" else "video"), adres


def _kaynagi_coz(oturum: Any, sayfa: BolumSayfasi, sayfa_adresi: str,
                 kaynak: Dict[str, Any]) -> List[_Aday]:
    embed = _embed_al(oturum, sayfa, sayfa_adresi, kaynak)
    if embed is None:
        return []
    tur, adres = embed
    if tur == "embed":
        yanit = _istek(oturum, "get", adres, zaman_asimi=PLAYER_TIMEOUT, ne="embed sayfası",
                       headers={"Referer": sayfa_adresi})
        oynatici = embed_adresi(_basarili(yanit, "embed sayfası").text)
        if oynatici is None:
            raise AnimelerHatasi(f"Embed sayfasında oynatıcı yok: {adres}")
        tur, adres = oynatici
    if tur == "video":
        # Sitenin kendi sunduğu doğrudan dosya (ölçümde görülmedi, JS'i destekliyor).
        return [_Aday(adres, "hls" if ".m3u8" in adres else "direct", BASE_URL + "/")]
    return _konagi_ac(oturum, adres)


def _yapay_zeka_mi(fansub: str) -> bool:
    # "Yapay Zeka" grubu makine çevirisi; insan çevirisi varsa o önce denensin.
    return "yapay zeka" in fansub.casefold()


def get_episode_streams(episode_id: str, timeout: float = EPISODE_TIMEOUT) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları, denenme sırasıyla.

    Returns: ``[{"url", "label", "type", "fansub", "player", "referer"?}]``.
        Sıra: insan çevirisi önce, sonra ölçülmüş barındırıcı güvenilirliği
        (`best_video` yalnızca ilk birkaç adayı deniyor).

    Raises: `AnimelerHatasi` — sayfa alınamadı/okunamadı, bölüm yok (site
        olmayan bölüm için 200 ile 1. bölümü döndürüyor; kanonik adres ele
        veriyor) ya da hiçbir kaynak çözülemedi.
    """
    yol = _bolum_yolu(episode_id)
    sayfa_adresi = f"{BASE_URL}/{yol}"
    # Bölüm başına TAZE oturum: CSRF jetonu ile çerezler birlikte kalsın.
    # Ortak oturumda başka bir iş parçacığının isteği çerezleri değiştirebilir.
    oturum = _yeni_oturum()
    yanit = _istek(oturum, "get", sayfa_adresi, zaman_asimi=timeout, ne="bölüm sayfası",
                   headers={"Referer": BASE_URL + "/"})
    if yanit.status_code == 404:
        raise AnimelerHatasi(f"Animeler'de böyle bir bölüm yok: {yol} (HTTP 404)", 404)
    sayfa = bolum_sayfasini_ayristir(_basarili(yanit, "bölüm sayfası").text)
    if sayfa.kanonik and sayfa.kanonik != yol:
        raise AnimelerHatasi(
            f"Animeler'de böyle bir bölüm yok: {yol} (site {sayfa.kanonik} "
            "sayfasını döndürdü; HTTP 404 sayıldı)", 404)
    secilenler = _kaynaklari_sec(sayfa.kaynaklar)
    if not secilenler:
        return []

    def coz(kaynak: Dict[str, Any]) -> Tuple[List[_Aday], Optional[Exception]]:
        try:
            return _kaynagi_coz(oturum, sayfa, sayfa_adresi, kaynak), None
        except Exception as exc:  # tek kaynağın hatası diğerlerini düşürmesin
            return [], exc

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(secilenler))) as havuz:
        sonuclar = list(havuz.map(coz, secilenler))

    akislar: List[Tuple[Tuple[bool, int], Dict[str, str]]] = []
    gorulen: Set[str] = set()
    hatalar: List[Exception] = []
    for kaynak, (adaylar, hata) in zip(secilenler, sonuclar):
        if hata is not None:
            hatalar.append(hata)
            continue
        try:
            fansub = sayfa.gruplar.get(int(kaynak.get("fansub_group_id") or 0), "")
        except (TypeError, ValueError):
            fansub = ""
        sunucu = _metin(kaynak.get("server_name"))
        for aday in adaylar:
            if aday.url in gorulen:
                continue
            gorulen.add(aday.url)
            oynatici, sira = _oynatici(aday.url)
            akis = {
                "url": aday.url,
                "label": " - ".join(p for p in (fansub, sunucu, aday.alt_ad) if p) or "Animeler",
                "type": aday.tur,
                "fansub": fansub,
                "player": oynatici or "ANIMELER",
            }
            if aday.referer:
                akis["referer"] = aday.referer
            akislar.append(((_yapay_zeka_mi(fansub), sira), akis))

    if not akislar and hatalar:
        # Boş liste "oynatılabilir akış yok" demek; kaynakların bir kısmı
        # hatayla düştüyse bunu iddia edemeyiz — sebebiyle birlikte söyle.
        raise AnimelerHatasi(
            f"Animeler: oynatılabilir akış bulunamadı; bölümün {len(secilenler)} "
            f"kaynağından {len(hatalar)} tanesi çözülemedi (ilk hata: {hatalar[0]})",
            getattr(hatalar[0], "status_code", None))
    akislar.sort(key=lambda a: a[0])           # kararlı: eşitlerde site sırası
    print(f"[Animeler] {yol}: {len(akislar)} akış ({len(secilenler)} kaynak, "
          f"{len(hatalar)} hata)")
    return [akis for _anahtar, akis in akislar]


def onbellegi_sifirla() -> None:
    """Ortak oturumu ve yetişkin listesi önbelleğini boşalt (testler, ayar değişimi)."""
    global _ortak_oturum, _yetiskin
    with _oturum_kilidi:
        _ortak_oturum = None
    with _yetiskin_kilidi:
        _yetiskin = (frozenset(), 0.0)


__all__ = [
    "AnimelerHatasi",
    "BASE_URL",
    "search_animeler",
    "get_anime_episodes",
    "get_episode_streams",
    "ajax_aramasini_ayristir",
    "filtre_kartlarini_ayristir",
    "bolum_listesini_ayristir",
    "bolum_sayfasini_ayristir",
    "embed_adresi",
    "cryptojs_coz",
    "sifreli_aynalari_coz",
    "onbellegi_sifirla",
]
