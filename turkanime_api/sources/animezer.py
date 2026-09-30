"""
Animezer kaynağı — https://animezer.com

Next.js ile yazılmış bir site; sayfalarını besleyen JSON API herkese açık.
Giriş, çerez, captcha ya da JS çözümü gerekmiyor: Turnstile yalnızca
/api/auth/login ve /register'da, "Premium" bir profil süsü ve hiçbir gömmeyi
kilitlemiyor. Cloudflare önde ama içerik ve API yollarında challenge yok
(2026-09-30'da ölçüldü: düz requests de, curl_cffi chrome131 de 200 aldı).

İçerik türleri (arama yanıtındaki `content_type` → sitenin yol öneki):
    anime       → /anime/<slug>        detay: /api/anime-detail/<slug>
    donghua     → /donghua/<slug>      detay: /api/donghua/<slug>
    cizgi_dizi  → /cizgi-dizi/<slug>   detay: /api/cizgi-dizi/<slug>
    movie       → /film/<slug>         detay: /api/film/<slug>
manga, manhwa, novel, game ve anivibe video değil, atlanıyor. Sitenin sayısal
kimlikleri yalnızca kendi türü içinde tekil (anime 1 One Piece, manga 1
ONE PIECE; film 35 ile donghua 35 de farklı yapımlar). Bu yüzden kaynak
kimliği tür + slug.

Uçlar:
- Arama:    GET /api/search?q=<q>&limit=50 (sitenin kendi JS'i de 50 istiyor)
            → {"success", "data": [{slug, title, poster, content_type, ...}]}
            2 karakterden kısa sorgu HTTP 400 alıyor; o yüzden hiç sorulmuyor.
- Bölümler: detay ucu. Anime şeması: data.seasons[].episodes[] (season_number,
            episode_number, episode_sub, episode_label, title). Donghua ve çizgi
            dizi şeması başka: data.seasons[].number + episodeList[]; numaralar
            seyrek olabiliyor ve 1. sezondan başlamayabiliyor (Battle Through
            the Heavens: sezon 3, 4, 5; 5. sezonda 1, 16, 21 … 207). Film:
            seasons boş, tek "bölüm". Detay, yüklemesi olmayan bölümleri de
            listeliyor; bölümün videosu olup olmadığı ancak akış ucunda belli
            oluyor ({"embeds": []}).
- Akışlar:  GET /api/<yol>/<slug>/embeds?season=&episode=&sub=&grouped=false
            → {"embeds": [{url, fansub_name, is_dubbed, ...}], "success"}.
            Adresler şifresiz, doğrudan gömme sayfaları. Film sezon/bölümü
            yok sayıyor.

Kimlikler:
- Kaynak: "<yol>/<slug>"                        ör. "anime/naruto", "film/jujutsu-kaisen-0"
- Bölüm:  "<yol>/<slug>/<sezon>/<bölüm>/<ara>"  ör. "anime/jujutsu-kaisen/3/11/0"
  <ara> sitenin `episode_sub`'ı: 0 normal bölüm, >0 araya eklenen özel bölüm
  (site "5A" yazıyor, izleme adresi bolum-5a). Film: "film/<slug>/1/1/0".
  Bölüm kimliği kendi başına yeter; akış isteği için detay ikinci kez okunmaz.

Barındırıcılar (25 bölümlük tarama, 2026-09-29/30, yt-dlp 2026.08.19):
- anizmplayer.com (25 bölümün 19'unda): Anizm'in FirePlayer CDN'i; Anizle ve
  Animeler de aynısını kullanıyor. `/video/<32 hex>` içindeki değer FirePlayer
  kimliğinin kendisi, `do=getVideo` (bkz. `anizle.fireplayer_istegi`) imzalı
  bir HLS master adresi veriyor. İmza (md5 + expires = şimdi+2 sa) adresi
  İSTEYEN istemciye bağlı, büyük olasılıkla IP'ye: tek IP'li sıradan bir
  bağlantıda Anizle kaynağı gibi oynuyor; çıkış IP'si bağlantıdan bağlantıya
  değişen ağda (ölçümün yapıldığı ortam) başka bir bağlantı 403 aldı.
  Site bunu kendi vekiliyle aşıyor (/api/proxy/anizmplayer → master ve bütün
  parçalar /api/proxy/stream?token=… üzerinden, Referer gerekmiyor; 9/9
  çalıştı). Vekil sitenin bant genişliğini harcadığı için burada YEDEK
  (bkz. `VEKIL_ORTAM_ANAHTARI`).
- video.sibnet.ru (15/25), my.mail.ru (3/25), vidmoly (1/25): yt-dlp çözüyor.
- ok.ru: yt-dlp'nin çıkarıcısı şu an hata veriyor ya da video telif engelli;
  tutuluyor ama sona. dailymotion görülen tek örnekte "bulunamadı", o da sonda.
- voe.sx (8/25, yt-dlp 403, sitenin vekili 502), abyssplayer (yt-dlp çıkarıcısı
  yok), vk.com (çıkarıcı bozuk, sitenin vekili 400): döndürülmüyor.
  `best_video` yalnızca ilk birkaç adayı yokluyor; ölü aday bütçeyi yer.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

from ..common.hatalar import KaynakEngellendi, KaynakHatasi, KaynakYanitVermedi

try:
    # Engel izlerinin tek listesi istemcide; ayrı bir kopya tutmak ayrışmaya
    # yol açıyor (bkz. ANIME_PROVIDER_GUIDE.md, "Engeli sessizce yutma").
    from ..common.cf_bypass import CHALLENGE_MARKERS as _CF_IZLERI
    from ..common.cf_bypass import ENGEL_DURUMLARI as _ENGEL_DURUMLARI
except Exception:  # pragma: no cover - sunucu tek başına da çalışabilmeli
    _CF_IZLERI = ("Just a moment", "cf-browser-verification", "challenge-platform")
    _ENGEL_DURUMLARI = frozenset({403, 429, 503})

log = logging.getLogger(__name__)

BASE_URL = "https://animezer.com"
# Gömmelerin çoğunun barındırıcısı; `getVideo` ucu bu konakta.
PLAYER_BASE_URL = "https://anizmplayer.com"
REFERER = BASE_URL + "/"
# One Piece'in detay yanıtı 338 KB (1179 bölüm); vekil ucu da sunucu tarafında
# anizmplayer'a gidip geliyor. 15 sn bu ikisi için dar kalabiliyor.
HTTP_TIMEOUT = 20
IMPERSONATE = "chrome131"
ARAMA_LIMITI = 50

# Sitede ölçülmüş bir hız sınırı yok (7 sn'de 25 istek hep 200, yanıtlarda
# rate-limit başlığı yok). Yine de ardışık API istekleri arasında 1 sn
# bırakılıyor: bir oynatma en çok 3-4 istek (gömmeler + yedek vekiller), bu
# bekleme kullanıcıya pahalı değil; site kısıt koyarsa ilk kırılan biz olmayalım.
_MIN_ARALIK = 1.0

# anizmplayer vekili: "yedek" (varsayılan) · "once" · "kapali".
#   yedek:  doğrudan HLS önde; vekil adayı yalnızca ilk VEKIL_YEDEK_SAYISI
#           anizm gömmesi ile doğrudan yolu çözülemeyenler için isteniyor ve
#           sibnet/mail.ru/vidmoly'den SONRA deneniyor. Doğrudan adres
#           oynatıcıda 403 verirse (IP'ye bağlı imza; ör. IPv4/IPv6 farkı)
#           yine de oynatılacak bir anizm kopyası kalıyor.
#   once:   her anizm gömmesi için vekil, üstelik doğrudan HLS'ten de önde.
#           Doğrudan yolun hiç çalışmadığı ağlar için (her oynatmada önce
#           403 alan adayları yoklamak zaman kaybı).
#   kapali: vekil hiç istenmez (ör. sunucu tarayıcısı: yazdığı adresler zaten
#           saatler içinde eskiyor, siteye boşuna yük olmasın).
VEKIL_ORTAM_ANAHTARI = "TURKANIME_ANIMEZER_VEKIL"
VEKIL_YEDEK_SAYISI = 2
# Doğrudan `getVideo` istekleri anizmplayer.com'a gidiyor (Animezer'e değil).
# Anizle aynı uca 8 paralel istek atıyor; burada 2 yetiyor: bölüm başına
# anizm gömmesi çoğunlukla 1-3 (en çok görülen 7), tek istek ~0.3 sn.
_COZUCU_ISCI = 2

_YEDEK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_BASLIKLAR = {"Accept": "application/json, text/plain, */*", "Referer": REFERER}

# Aramadaki `content_type` → sitenin yolu. Alt çizgi aramada, tire yollarda.
_ICERIK_YOLU = {
    "anime": "anime",
    "donghua": "donghua",
    "cizgi_dizi": "cizgi-dizi",
    "movie": "film",
}
_DETAY_UCU = {
    "anime": "/api/anime-detail/{}",
    "donghua": "/api/donghua/{}",
    "cizgi-dizi": "/api/cizgi-dizi/{}",
    "film": "/api/film/{}",
}
# Aynı adlı dizi ve film (ya da Çin yapımı) arama sonucunda ayırt edilebilsin.
_BASLIK_EKI = {"film": " (Film)", "donghua": " (Donghua)", "cizgi-dizi": " (Çizgi Dizi)"}
_FILM_SOZCUGU = re.compile(r"\b(?:movie|film)\b", re.I)

# Slug'lar tireyle de başlayabiliyor (".hack" → "-hack"). Nokta ilk karakter
# olamaz: kimlik doğrudan URL yoluna giriyor, ".." yola sızmasın.
_SLUG = r"[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,199}"
_SLUG_RE = re.compile(_SLUG)
_YOLLAR = "anime|donghua|cizgi-dizi|film"
_KAYNAK_ID = re.compile(rf"({_YOLLAR})/({_SLUG})")
_BOLUM_ID = re.compile(rf"({_YOLLAR})/({_SLUG})/(\d{{1,4}})/(\d{{1,5}})(?:/(\d{{1,2}}))?")

_ANIZM = re.compile(
    r"^https?://(?:www\.)?anizmplayer\.com/(?:video|player)/([0-9a-f]{32})(?:[/?#]|$)", re.I)

# (konak deseni, oynatıcı adı, görünen ad, deneme sırası). Adlar
# `common.oynatici_onceligi` ile aynı (ilerleme etiketinde görünüyor). Sıra
# genel öncelik listesinden FARKLI, çünkü bu sitedeki ölçüm farklı: sibnet
# 6/6, mail.ru 2/2, vidmoly 1/1 çözüldü; ok.ru 0/2, dailymotion 0/1.
# İzin listesi: tanınmayan konak döndürülmez (bkz. modül başlığı).
_SIRA_ANIZM = 0
_SIRA_VEKIL = 4
_BARINDIRICILAR: Tuple[Tuple["re.Pattern[str]", str, str, int], ...] = (
    (re.compile(r"(?:^|\.)video\.sibnet\.ru$"), "SIBNET", "Sibnet", 1),
    (re.compile(r"(?:^|\.)my\.mail\.ru$"), "MAIL", "Mail.ru", 2),
    (re.compile(r"(?:^|\.)vidmoly\.(?:biz|net|org|me)$"), "VIDMOLY", "Vidmoly", 3),
    # Sitenin çözücüsü tanıyor, taramada görülmedi; yt-dlp ikisini de açıyor.
    (re.compile(r"(?:^|\.)drive\.google\.com$"), "GDRIVE", "Google Drive", 5),
    (re.compile(r"(?:^|\.)sendvid\.com$"), "SENDVID", "Sendvid", 5),
    (re.compile(r"(?:^|\.)(?:ok|odnoklassniki)\.ru$"), "ODNOKLASSNIKI", "OK.ru", 6),
    (re.compile(r"(?:^|\.)dailymotion\.com$"), "DAILYMOTION", "Dailymotion", 7),
)

# Sitenin fansub adı yerine koyduğu dolgu değerleri: grup adı değiller.
_DOLGU_FANSUBLAR = frozenset({"", "-", "bilinmiyor", "bilinmeyen", "custom",
                              "güncellenecek", "guncellenecek", "null", "none"})
BILINMEYEN_FANSUB = "Bilinmeyen"

# "Bölüm 1", "Episode 12", "5. Bölüm": sitenin başlık yerine koyduğu numara.
# Sitenin kendi JS'indeki kalıbın (`/^(bölüm|episode|ep\.?)\s*\d+[a-z]?$/i`)
# Türkçe sıra sayılı hâli de eklendi.
_JENERIK_BASLIK = re.compile(
    r"^\s*(?:(?:bölüm|bolum|episode|ep\.?)\s*\d+[a-z]?|\d+\s*\.\s*(?:bölüm|bolum))\s*$",
    re.I)

_kilit = threading.Lock()
_son_istek = 0.0
_oturum = None


class AnimezerHatasi(KaynakHatasi):
    """Animezer'e ulaşılamadı ya da yanıt beklenen biçimde değil.

    Mesaj kullanıcıya gösterilecek Türkçe cümle (`KaynakHatasi` ailesi,
    arayüz aynen gösteriyor). ``status_code`` sunucu tarayıcısının hata
    sınıflandırması (`nezaket.hata_turu`) için: 403/429 engellenme, 404
    kalıcı, diğerleri geçici.
    """

    def __init__(self, mesaj: str, status_code: Optional[int] = None):
        super().__init__(mesaj)
        self.status_code = status_code


class AnimezerEngellendi(AnimezerHatasi, KaynakEngellendi):
    """Cloudflare/site isteği reddetti (403, 429 ya da challenge sayfası)."""


class AnimezerYanitVermedi(AnimezerHatasi, KaynakYanitVermedi):
    """Ağ hatası, zaman aşımı ya da sunucu tarafı 5xx."""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
def _yeni_oturum():
    """Yeni HTTP oturumu. Testler ağa çıkmamak için bunu sahteler."""
    if _HAS_CURL:
        return _http.Session(impersonate=IMPERSONATE)
    oturum = _http.Session()          # curl_cffi yoksa düz requests de 200 alıyor
    oturum.headers["User-Agent"] = _YEDEK_UA
    return oturum


def _oturum_al():
    """Paylaşılan oturum (bağlantı yeniden kullanılsın).

    curl_cffi oturumu her iş parçacığına kendi curl tutamacını veriyor;
    arama motorunun paralel çağrıları aynı oturumu güvenle paylaşabilir.
    """
    global _oturum
    with _kilit:
        if _oturum is None:
            _oturum = _yeni_oturum()
        return _oturum


def _sira_bekle() -> None:
    """Son API isteğinden bu yana en az `_MIN_ARALIK` geçsin."""
    global _son_istek
    with _kilit:
        bekle = _MIN_ARALIK - (time.monotonic() - _son_istek)
        if bekle > 0:
            time.sleep(bekle)
        _son_istek = time.monotonic()


def _engel_mi(yanit) -> bool:
    """Yanıt içerik değil, bir engel mi?

    429 ve 403 her zaman engel: API'nin hiçbir yolu içerik için 403
    döndürmüyor. 503 yalnızca gövdesi Cloudflare sayfasıysa engel; düz bir
    503 geçici sunucu hatası sayılır ve bir kez yeniden denenir.
    """
    kod = yanit.status_code
    if kod in (403, 429):
        return True
    if kod in _ENGEL_DURUMLARI:
        bas = str(getattr(yanit, "text", "") or "")[:6000]
        return any(iz in bas for iz in _CF_IZLERI)
    return False


def _getir(yol: str, params: Optional[Dict[str, Any]] = None):
    """Animezer API'sine GET. Yanıt nesnesi döner (404 gibi kodlar dahil).

    Ağ hatası ve 5xx BİR kez yeniden deneniyor (geçici kesintiler tek istekte
    düzeliyor). Engel yeniden denenmiyor: aynı parmak iziyle ikinci istek de
    engellenir; kullanıcı "sonuç yok" yerine sebebi görmeli.
    """
    adres = BASE_URL + yol
    son_sebep = ""
    son_kod: Optional[int] = None
    son_hata: Optional[BaseException] = None
    for _deneme in range(2):
        _sira_bekle()
        try:
            yanit = _oturum_al().get(adres, params=params, headers=dict(_BASLIKLAR),
                                     timeout=HTTP_TIMEOUT)
        except Exception as hata:  # ağ/zaman aşımı: bir kez daha dene
            son_hata, son_sebep, son_kod = hata, f"{type(hata).__name__}: {hata}", None
            continue
        kod = yanit.status_code
        if _engel_mi(yanit):
            raise AnimezerEngellendi(
                f"Animezer isteği geri çevirdi (HTTP {kod}; Cloudflare ya da hız "
                "sınırı). Birkaç dakika sonra yeniden deneyin ya da başka bir "
                "kaynak seçin.", status_code=kod)
        if kod >= 500:
            son_hata, son_sebep, son_kod = None, f"HTTP {kod}", kod
            continue
        return yanit
    hata = AnimezerYanitVermedi(f"Animezer'e ulaşılamadı ({yol}): {son_sebep}",
                                status_code=son_kod)
    raise hata from son_hata


def _json(yanit, ne: str) -> Dict[str, Any]:
    """Yanıt gövdesi → sözlük; JSON değilse (bakım sayfası vb.) hata."""
    try:
        veri = json.loads(yanit.text)
    except ValueError as hata:
        raise AnimezerHatasi(
            f"Animezer {ne} yanıtı JSON değil (HTTP {yanit.status_code}); site "
            "değişmiş ya da bakımda olabilir.", status_code=yanit.status_code) from hata
    if not isinstance(veri, dict):
        raise AnimezerHatasi(f"Animezer {ne} yanıtı beklenen biçimde değil.",
                             status_code=yanit.status_code)
    return veri


def _metin(deger: Any) -> str:
    return " ".join(str(deger or "").split())


def _sayi(deger: Any) -> Optional[int]:
    """Negatif olmayan tamsayı; sitede sayılar kimi yerde dizge ("51")."""
    try:
        sayi = int(str(deger).strip())
    except (TypeError, ValueError):
        return None
    return sayi if sayi >= 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def _baslik_eki(yol: str, baslik: str) -> str:
    if yol == "film" and _FILM_SOZCUGU.search(baslik):
        return ""                     # "Naruto the Movie (Film)" gereksiz tekrar
    return _BASLIK_EKI.get(yol, "")


def arama_ayristir(veri: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Arama yanıtı → ``[{"slug": "<yol>/<slug>", "title", "image"}]`` (site sırası).

    Video olmayan türler (manga, novel…) atılıyor. Site aynı kaydı iki kez
    verebiliyor ("avatar": Avatar: The Last Airbender iki kez); kimlik tekil.
    """
    out: List[Dict[str, Any]] = []
    gorulen = set()
    kayitlar = veri.get("data")
    for kayit in kayitlar if isinstance(kayitlar, list) else []:
        if not isinstance(kayit, dict):
            continue
        yol = _ICERIK_YOLU.get(str(kayit.get("content_type") or "").strip().lower())
        slug = str(kayit.get("slug") or "").strip()
        baslik = _metin(kayit.get("title"))
        if not yol or not baslik or not _SLUG_RE.fullmatch(slug):
            continue
        kimlik = f"{yol}/{slug}"
        if kimlik in gorulen:
            continue
        gorulen.add(kimlik)
        gorsel = kayit.get("poster")
        out.append({"slug": kimlik, "title": baslik + _baslik_eki(yol, baslik),
                    "image": gorsel if isinstance(gorsel, str) and gorsel else None})
    return out


def search_animezer_zengin(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Kapak görseliyle arama: ``[{"slug", "title", "image"}, ...]``.

    Sonuç yoksa boş liste. Siteye ulaşılamazsa ya da engellenirse
    `AnimezerHatasi` yükselir: arama sayfası "sonuç yok" yerine sebebi
    göstersin (`AramaSonuclari.hatalar`).
    """
    sorgu = _metin(query)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    if len(sorgu) < 2 or limit <= 0:
        return []                     # site 400 veriyor; boşuna istek atma
    yanit = _getir("/api/search", params={"q": sorgu, "limit": ARAMA_LIMITI})
    if yanit.status_code == 400:
        return []                     # "Query must be at least 2 characters"
    if yanit.status_code != 200:
        raise AnimezerHatasi(f"Animezer arama ucu HTTP {yanit.status_code} döndü.",
                             status_code=yanit.status_code)
    veri = _json(yanit, "arama")
    if veri.get("success") is False:
        return []
    return arama_ayristir(veri)[:limit]


def search_animezer(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """Animezer'de ara → ``[("<yol>/<slug>", başlık), ...]``; sonuç yoksa []."""
    return [(k["slug"], k["title"]) for k in search_animezer_zengin(query, limit)]


# ─────────────────────────────────────────────────────────────────────────────
# Bölümler
# ─────────────────────────────────────────────────────────────────────────────
def _kaynak_coz(anime_id: str) -> Tuple[str, str]:
    """"anime/naruto" → ("anime", "naruto"). Çıplak slug anime sayılır.

    Çıplak slug kabulü elle girilen/eski kimlikler için; sitenin içeriğinin
    büyük çoğunluğu anime.
    """
    ham = str(anime_id or "").strip().strip("/")
    eslesme = _KAYNAK_ID.fullmatch(ham)
    if eslesme:
        return eslesme.group(1), eslesme.group(2)
    if _SLUG_RE.fullmatch(ham):
        return "anime", ham
    raise AnimezerHatasi(
        f"Animezer kaynak kimliği '<tür>/<slug>' biçiminde olmalı (ör. anime/naruto), "
        f"'{anime_id}' geçersiz.", status_code=400)


def _bolum_basligi(sezon: int, bolum: int, ara: int, sezon_yaz: bool,
                   etiket: Any = None, ad: Any = None) -> str:
    """"5. Bölüm", "2. Sezon 5. Bölüm - Ad", "215. Bölüm (215-216)", "5.1. Bölüm".

    Biçim `common.episode_parser`'ın tanıdığı biçim: çok kaynaklı birleştirme
    (sezon, bölüm) üzerinden yapılıyor. Ara bölüm "5.1" (ayrıştırıcının
    kendi yazımı; "5A" yazsaydık numarasız kalırdı). Sitenin `episode_label`'ı
    numaradan farklıysa (One Piece "215-216": iki bölüm tek video) parantezde,
    gerçek bölüm adı varsa sonda.
    """
    numara = f"{bolum}.{ara}" if ara else str(bolum)
    baslik = f"{sezon}. Sezon {numara}. Bölüm" if sezon_yaz else f"{numara}. Bölüm"
    etiket = _metin(etiket)
    if etiket and etiket not in (numara, str(bolum)):
        baslik += f" ({etiket})"
    ad = _metin(ad)
    if ad and not _JENERIK_BASLIK.match(ad):
        baslik += f" - {ad}"
    return baslik


def bolumleri_ayristir(yol: str, slug: str, veri: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Detay yanıtı → izleme sırasıyla ``[(bolum_id, başlık)]``.

    Sıra sayısal (sezon, bölüm, ara); sitenin sırasına güvenilmiyor. Tek sezon
    ve o sezon 1 ise başlığa sezon yazılmaz ("5. Bölüm").
    """
    data = veri.get("data")
    if not isinstance(data, dict):
        raise AnimezerHatasi(f"Animezer detay yanıtı beklenen biçimde değil ({yol}/{slug}).")
    if yol == "film":
        return [(f"film/{slug}/1/1/0", _metin(data.get("title")) or "Film")]
    sezonlar = data.get("seasons")
    if not isinstance(sezonlar, list):
        raise AnimezerHatasi(
            f"Animezer detay yanıtında sezon listesi yok ({yol}/{slug}); site "
            "değişmiş olabilir.")
    satirlar: Dict[Tuple[int, int, int], Tuple[Any, Any]] = {}
    for sezon in sezonlar:
        if not isinstance(sezon, dict):
            continue
        if yol == "anime":
            no, bolumler = _sayi(sezon.get("season_number")), sezon.get("episodes")
        else:
            # Donghua / çizgi dizi: "episodes" burada SAYI, liste episodeList.
            no = _sayi(sezon.get("number", sezon.get("season_number")))
            bolumler = sezon.get("episodeList")
        if no is None or not isinstance(bolumler, list):
            continue
        for bolum in bolumler:
            if not isinstance(bolum, dict):
                continue
            numara = _sayi(bolum.get("episode_number"))
            if numara is None:
                continue              # numarasız kayıt için akış istenemez
            ara = (_sayi(bolum.get("episode_sub")) or 0) if yol == "anime" else 0
            satirlar.setdefault((no, numara, ara),
                                (bolum.get("episode_label"), bolum.get("title")))
    sezon_yaz = {s for s, _, _ in satirlar} != {1}
    return [(f"{yol}/{slug}/{s}/{e}/{a}", _bolum_basligi(s, e, a, sezon_yaz, etiket, ad))
            for (s, e, a), (etiket, ad) in sorted(satirlar.items())]


def get_anime_episodes(anime_id: str) -> List[Tuple[str, str]]:
    """Bölümler → ``[("<yol>/<slug>/<sezon>/<bölüm>/<ara>", başlık), ...]``.

    Bilinmeyen kimlik, ağ/engel sorunu ve tanınmayan yanıt `AnimezerHatasi`
    ("0 bölüm" demek hatayı gizlerdi). Detayda sezon yoksa gerçekten [] döner.
    """
    yol, slug = _kaynak_coz(anime_id)
    yanit = _getir(_DETAY_UCU[yol].format(quote(slug, safe="")))
    if yanit.status_code == 404:
        raise AnimezerHatasi(f"Animezer'de '{yol}/{slug}' bulunamadı.", status_code=404)
    if yanit.status_code != 200:
        raise AnimezerHatasi(f"Animezer detay ucu HTTP {yanit.status_code} döndü "
                             f"({yol}/{slug}).", status_code=yanit.status_code)
    veri = _json(yanit, "detay")
    if veri.get("success") is False:
        sebep = _metin(veri.get("error")) or "bulunamadı"
        raise AnimezerHatasi(f"Animezer '{yol}/{slug}' için bölüm vermedi: {sebep}.",
                             status_code=404)
    return bolumleri_ayristir(yol, slug, veri)


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _bolum_coz(episode_id: str) -> Tuple[str, str, int, int, int]:
    eslesme = _BOLUM_ID.fullmatch(str(episode_id or "").strip().strip("/"))
    if not eslesme:
        raise AnimezerHatasi(
            "Animezer bölüm kimliği '<tür>/<slug>/<sezon>/<bölüm>/<ara>' biçiminde "
            f"olmalı, '{episode_id}' geçersiz.", status_code=400)
    yol, slug, sezon, bolum, ara = eslesme.groups()
    return yol, slug, int(sezon), int(bolum), int(ara or 0)


def watch_url(episode_id: str) -> str:
    """Bölüm kimliği → sitedeki izleme sayfası (bölüm nesnesinin adresi)."""
    try:
        yol, slug, sezon, bolum, ara = _bolum_coz(episode_id)
    except AnimezerHatasi:
        # Adres bölüm nesnesinin kimliği gibi de kullanılıyor; tanınmayan
        # kimlikler aynı adrese çökmesin.
        return f"{BASE_URL}/{quote(str(episode_id or ''), safe='/')}"
    if yol == "film":
        return f"{BASE_URL}/film/{quote(slug, safe='')}/izle"
    ek = chr(96 + ara) if 0 < ara <= 26 else ""       # 1 → "a" (sitenin "5A"sı)
    return f"{BASE_URL}/{yol}/{quote(slug, safe='')}/sezon-{sezon}/bolum-{bolum}{ek}"


def _fansub_adi(ham: Any) -> str:
    ad = _metin(ham)
    return BILINMEYEN_FANSUB if ad.casefold() in _DOLGU_FANSUBLAR else ad


def _barindirici(adres: str) -> Optional[Tuple[str, str, int]]:
    """Adres → (oynatıcı adı, görünen ad, deneme sırası); tanınmıyorsa None."""
    konak = (urlsplit(adres).hostname or "").lower()
    for desen, oynatici, ad, sira in _BARINDIRICILAR:
        if desen.search(konak):
            return oynatici, ad, sira
    return None


def _adres_duzelt(ham: Any) -> Optional[str]:
    adres = str(ham or "").strip()
    if adres.startswith("//"):            # protokolsüz gömme: //ok.ru/videoembed/…
        adres = "https:" + adres
    parca = urlsplit(adres)
    if parca.scheme not in ("http", "https") or not parca.hostname:
        return None                       # r2:// gibi sitenin iç şemaları
    return adres


def _vekil_kipi() -> str:
    """`VEKIL_ORTAM_ANAHTARI` → "yedek" | "once" | "kapali" (tanınmayan: yedek)."""
    ham = unicodedata.normalize("NFKD", os.environ.get(VEKIL_ORTAM_ANAHTARI, ""))
    deger = "".join(c for c in ham if not unicodedata.combining(c))
    deger = deger.replace("ı", "i").strip().casefold()
    return deger if deger in ("yedek", "once", "kapali") else "yedek"


def _gomme_akisi(adres: str, fansub: str, dublaj: str,
                 barindirici: Tuple[str, str, int]) -> Dict[str, Any]:
    oynatici, ad, _sira = barindirici
    return {
        "url": adres,
        "label": f"{fansub} - {ad}{dublaj}",
        # Gömme sayfası: yt-dlp (mpv'de ytdl_hook) çözüyor.
        "type": "iframe",
        # Tarayıcı iframe'i bu siteden açıyor; bazı gömülü oynatıcılar
        # (vidmoly) gömen sayfayı görmek istiyor. yt-dlp'nin biçim başına
        # başlıkları (sibnet CDN'i) bunu zaten eziyor.
        "referer": REFERER,
        "fansub": fansub,
        "player": oynatici,
    }


def _dogrudan_coz(kimlik: str, gomme: str, etiket: str) -> Optional[Dict[str, Any]]:
    """anizmplayer `getVideo` → imzalı HLS master (Anizle'nin istemcisiyle).

    Protokol Anizle ve Animeler'deki FirePlayer'la aynı; istek ve yanıtın
    yorumu `anizle.fireplayer_istegi` / `fireplayer_akisi`'nda TEK yerde.
    Referer gömme sayfasının kendisi: tarayıcıda POST'u o sayfa atıyor.
    Tembel import: anizle requests/cf_bypass çekiyor; arama ve bölüm listesi
    ona hiç ihtiyaç duymuyor.
    """
    from . import anizle as _anizle

    veri = _anizle.fireplayer_istegi(f"{PLAYER_BASE_URL}/player/index.php?data={kimlik}",
                                     referer=gomme, timeout=HTTP_TIMEOUT)
    if not veri:
        return None
    akis = _anizle.fireplayer_akisi(veri, etiket)
    if akis is None and isinstance(veri.get("videoSrc"), str):
        # Oynatıcı videoyu başka bir barındırıcıdan gömüyorsa (sibnet vb.) o.
        adres = _adres_duzelt(veri["videoSrc"])
        barindirici = _barindirici(adres) if adres else None
        if adres and barindirici:
            return {"url": adres, "label": etiket, "type": "iframe",
                    "referer": REFERER, "player": barindirici[0]}
        return None
    if akis is not None:
        akis["player"] = "ANIZM"
    return akis


def _vekil_coz(gomme: str) -> str:
    """Sitenin anizmplayer vekili → oynatılabilir master adresi.

    Yanıt ``{"success", "masterUrl", "streamUrl", "sources": [...]}``;
    `masterUrl` ana liste (sesi ayrı grupta olan yeni yüklemeler de bütün
    kalır), `streamUrl` en yüksek kalitenin tek başına listesi. Göreli
    adresler siteye göre. Çözülemezse `AnimezerHatasi`.
    """
    yanit = _getir("/api/proxy/anizmplayer", params={"url": gomme})
    if yanit.status_code != 200:
        raise AnimezerHatasi(f"Animezer vekili HTTP {yanit.status_code} döndü.",
                             status_code=yanit.status_code)
    veri = _json(yanit, "vekil")
    adres = veri.get("masterUrl") or veri.get("streamUrl")
    if not veri.get("success") or not isinstance(adres, str) or not adres.strip():
        raise AnimezerHatasi("Animezer vekili bu video için adres vermedi.")
    adres = adres.strip()
    if adres.startswith("/"):
        return BASE_URL + adres
    if urlsplit(adres).scheme == "https":
        return adres
    raise AnimezerHatasi(f"Animezer vekilinin adresi tanınmadı: {adres[:80]}")


def akislari_kur(gommeler: List[Any]) -> List[Dict[str, Any]]:
    """Akış ucunun gömmeleri → denenme sırasıyla oynatılabilir akışlar.

    Sıra: anizm doğrudan HLS → sibnet → mail.ru → vidmoly → anizm (Animezer
    vekili) → gdrive/sendvid → ok.ru → dailymotion; aynı barındırıcıda
    sitenin sırası (en yeni yükleme önce). Etiket "Fansub - Barındırıcı";
    çözünürlük yazılmıyor, `best_video` etiketten çözünürlük okuyup yeniden
    sıralıyor ve olmayan bir değer bu sırayı bozardı.

    Bölümün hiç oynatılabilir gömmesi yoksa [] (yalnızca voe/vk gibi
    açılamayanlar da buna dahil). Gömmeler vardı ama anizm adreslerinin
    hiçbiri çözülemediyse ve başka aday yoksa `AnimezerYanitVermedi`:
    bu "video yok" değil, geçici bir çözüm hatası.
    """
    siralilar: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
    anizmler: List[Tuple[int, str, str, str, str]] = []     # (sıra, kimlik, adres, fansub, dublaj)
    atilan: List[str] = []
    gorulen = set()
    for sira, gomme in enumerate(gommeler or []):
        if not isinstance(gomme, dict):
            continue
        adres = _adres_duzelt(gomme.get("url"))
        if not adres or adres in gorulen:
            continue
        gorulen.add(adres)
        fansub = _fansub_adi(gomme.get("fansub_name"))
        dublaj = " (Dublaj)" if gomme.get("is_dubbed") else ""
        anizm = _ANIZM.match(adres)
        if anizm:
            anizmler.append((sira, anizm.group(1).lower(), adres, fansub, dublaj))
            continue
        barindirici = _barindirici(adres)
        if barindirici is None:
            atilan.append(urlsplit(adres).hostname or adres)
            continue
        siralilar.append(((barindirici[2], sira), _gomme_akisi(adres, fansub, dublaj,
                                                               barindirici)))
    if atilan:
        log.info("Animezer: oynatılamayan gömmeler atlandı: %s", ", ".join(atilan))

    kip = _vekil_kipi()
    hatalar: List[str] = []
    cozulemeyen = set()
    if anizmler:
        with ThreadPoolExecutor(max_workers=min(_COZUCU_ISCI, len(anizmler))) as havuz:
            isler = [havuz.submit(_dogrudan_coz, kimlik, adres, f"{fansub} - Anizm{dublaj}")
                     for _s, kimlik, adres, fansub, dublaj in anizmler]
            for (sira, _k, _a, fansub, _d), is_ in zip(anizmler, isler):
                try:
                    akis = is_.result()
                except Exception as hata:  # pragma: no cover - fireplayer_istegi yutuyor
                    akis, sebep = None, f"{type(hata).__name__}: {hata}"
                else:
                    sebep = "anizmplayer adres vermedi"
                if akis is None:
                    cozulemeyen.add(sira)
                    hatalar.append(sebep)
                    continue
                akis["fansub"] = fansub
                siralilar.append(((_SIRA_ANIZM, sira), akis))

    if kip != "kapali":
        vekil_sirasi = -1 if kip == "once" else _SIRA_VEKIL
        for adet, (sira, _k, adres, fansub, dublaj) in enumerate(anizmler):
            if kip == "yedek" and adet >= VEKIL_YEDEK_SAYISI and sira not in cozulemeyen:
                continue
            try:
                vekil = _vekil_coz(adres)
            except AnimezerHatasi as hata:
                hatalar.append(str(hata))
                continue
            siralilar.append(((vekil_sirasi, sira), {
                "url": vekil,
                "label": f"{fansub} - Anizm{dublaj} (Animezer vekili)",
                "type": "hls",
                "fansub": fansub,
                "player": "ANIMEZER",
            }))

    siralilar.sort(key=lambda satir: satir[0])
    akislar: List[Dict[str, Any]] = []
    verilen = set()
    for _anahtar, akis in siralilar:
        # İki gömme aynı videoya çıkabiliyor (aynı yükleme iki grubun adıyla);
        # `best_video`'nun sınırlı bütçesi aynı adresi iki kez yoklamasın.
        if akis["url"] not in verilen:
            verilen.add(akis["url"])
            akislar.append(akis)
    if not akislar and hatalar:
        raise AnimezerYanitVermedi(
            f"Animezer: bu bölümün {len(anizmler)} anizmplayer kaydı çözülemedi "
            f"({hatalar[-1]}). Biraz sonra yeniden deneyin ya da başka bir kaynak seçin.")
    return akislar


def get_episode_streams(episode_id: str) -> List[Dict[str, Any]]:
    """Bölümün oynatılabilir akışları; sitede bölümün kaydı yoksa [].

    Her akış ``{"url", "label", "type", "fansub", "player", "referer"?}``.
    Ağ/engel/ayrıştırma sorunlarında `AnimezerHatasi`.
    """
    yol, slug, sezon, bolum, ara = _bolum_coz(episode_id)
    yanit = _getir(f"/api/{yol}/{quote(slug, safe='')}/embeds",
                   params={"season": sezon, "episode": bolum, "sub": ara,
                           "grouped": "false"})
    if yanit.status_code == 404:
        raise AnimezerHatasi(f"Animezer'de bu bölüm bulunamadı ({episode_id}).",
                             status_code=404)
    if yanit.status_code != 200:
        raise AnimezerHatasi(f"Animezer bölüm kaynakları HTTP {yanit.status_code} "
                             f"döndü ({episode_id}).", status_code=yanit.status_code)
    veri = _json(yanit, "bölüm kaynakları")
    gommeler = veri.get("embeds")
    if veri.get("success") is False or not isinstance(gommeler, list):
        sebep = _metin(veri.get("error")) or "gömme listesi yok"
        raise AnimezerHatasi(f"Animezer bölüm kaynaklarını vermedi: {sebep}.")
    return akislari_kur(gommeler)


__all__ = [
    "BASE_URL",
    "AnimezerHatasi",
    "search_animezer",
    "search_animezer_zengin",
    "get_anime_episodes",
    "get_episode_streams",
    "watch_url",
]
