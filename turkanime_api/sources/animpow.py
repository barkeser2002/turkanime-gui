"""
AnimPow kaynağı — https://animpow.com

Site bir Next.js ön yüzü; veriyi İKİ ayrı arka uçtan çekiyor. İkisi de tek
başına yetmiyor, bu yüzden ikisi birleştiriliyor:

1. **Eski içerik API'si** — ``https://client-api.animpow.com/api/v1``.
   Kataloğun büyük kısmı burada (40 TV başlığından 37'sinin bölümleri). Her
   yanıt, sitenin KENDİ web istemcisinin kurduğu hibrit şifreyle geliyor
   (JS parçaları 3241 ``AnimPowApiClient`` ve 3301 WebCrypto yardımcıları):

   - ``GET /auth/public-key`` → ``{"publicKey": "<PEM, RSA-2048>"}``
   - İstemci rastgele 32 baytlık bir AES anahtarı ve ``sessionId = uuid4``
     üretir; anahtarı RSA-OAEP-SHA256 ile şifreleyip
     ``POST /auth/handshake {"sessionId", "encryptedKey"}`` ile yollar.
   - Sonraki her istek ``X-Session-Id`` başlığı taşır; yanıt
     ``{"iv", "authTag", "data"}`` biçiminde AES-256-GCM ile şifrelidir.

   Açık anahtar ve oturum anahtarı her süreçte YENİDEN alınır/üretilir; hiçbir
   anahtar koda gömülmez. Sunucu oturumu unutursa (400 "Invalid or expired
   session key" / "Encryption is required") bir kez yeniden el sıkışılır —
   sitenin istemcisi de tam olarak bunu yapıyor.

2. **QuadroGG** — ``https://quadrogg.best`` (düz JSON). Sitenin yeni arka ucu:
   HLS kodlamaları, Türkçe altyazı görüntüye gömülü. Kataloğun küçük bir
   kısmını kapsıyor ama Türkçe başlıkla arama YALNIZCA burada çalışıyor
   ("iblis" → Kimetsu no Yaiba); eski API Türkçe başlık tanımıyor.

Kimlikler:

- ``kaynak_id``: sitenin ``animpow_core_id``'si — ``"9072-D03257F18BEC"`` ya da
  eski sayısal ``"960"``. İki arka uç da aynı kimliği tanıyor (QuadroGG yalnızca
  ``9072-`` biçimini).
- ``bolum_id``: ``"{core}:{sezon}:{bolum}"``. Kendi başına yeterli: akışları
  bulmak için başka hiçbir şey gerekmez. Ayraç ``:`` çünkü core kimliği zaten
  ``-`` içeriyor. Sitenin izleme adresi ``/watch/{core}/s{S}e{E}``
  (bkz. ``kayit._animpow_adresi``).

Akışlar (sıra = deneme sırası):

- QuadroGG HLS (``s3i--cdn-sN-*.benstreamsunucusuyum.xyz``): şifresiz, referer
  istemiyor.
- "AnimPow Cdn 1" MP4 (``benstreamsunucusuyum.xyz/stream/?token=…``): CDN
  Referer'a bakıyor — ``https://animpow.com/`` yoksa 403 ("git ana siteye gir").
  Bu yüzden bu akışlar ``referer`` taşır. Bazı 1080p dosyaları eksik (500);
  her kalite ayrı akış olarak verilir ki `best_video` bir sonrakine düşsün.
- Gömülü oynatıcılar: yt-dlp'nin çözebildikleri (sibnet, Google Drive,
  dailymotion, ok.ru…). Sibnet adresleri sitenin kendi vekiline sarılı geliyor
  (``sibnet-api-server-v1.animpow.com/api/sibnet?url=…``) ve o vekil 500 dönüyor;
  içteki asıl ``video.sibnet.ru`` adresi çıkarılır (yt-dlp sibnet'in istediği
  Referer'ı kendisi ekliyor). yt-dlp'nin çözemediği ya da ölü olan konaklar
  (doodstream, streamtape, uqload, vidmoly, mega…) atılır.

Hata sözleşmesi: arama sonuç yoksa ``[]`` döner. Bir arka uç çökerse diğerinin
sonucu kullanılır (loglanır); hiçbir arka uç sonuç veremediyse ve en az biri
HATA verdiyse ``AnimPowHatasi`` yükselir — ağ sorunu "bu animenin bölümü yok"
gibi görünmesin.

``Crypto`` (pycryptodome) modül düzeyinde import EDİLMEZ: sunucu tarayıcısının
imajında yok (`tests/test_sunucu_bagimliliklari.py`). Orada eski API devre dışı
kalır, kaynak yalnızca QuadroGG ile çalışır.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - kütüphane yoksa düz requests'e düş
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False


log = logging.getLogger(__name__)

BASE_URL = "https://animpow.com"
# Eski (şifreli) içerik API'si ve QuadroGG. İkisi de DISCLAIMER tablosunda
# yazılı olmalı (`tests/test_disclaimer_adresleri.py` bu sabitleri okuyor).
API_BASE_URL = "https://client-api.animpow.com/api/v1"
ALT_URL = "https://quadrogg.best"
REFERER = BASE_URL + "/"
HTTP_TIMEOUT = 20

# One Piece'in bölüm listesi 5.6 MB ve ~2.6 sn. Her akış sorgusu aynı listeyi
# yeniden çekmesin diye kısa süre saklanıyor; Cdn1 jetonları en az ~10 dk
# geçerli kaldığı ölçüldü.
_BOLUM_TTL = 15 * 60
_BOLUM_ONBELLEK_SINIRI = 16

# Sitenin kendi istemcisinin gönderdiği başlıklar. Eski API Origin/Referer'a
# bakmıyor ama gerçek istemciden ayırt edilmemek için aynıları yollanıyor.
_H_SITE = {"Origin": BASE_URL, "Referer": REFERER, "Accept": "application/json"}
_H_QUADRO = {"Origin": BASE_URL, "Referer": REFERER, "Accept": "application/json",
             "this_is_client_is": "animpow"}
# curl_cffi yoksa (düz requests) tarayıcı kimliği elle verilir.
_YEDEK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# core kimliği: "9072-" + 12 onaltılık hane ya da eski sayısal kimlik. Kimlik
# URL yoluna giriyor; başka bir şeye ("../account") izin verilmez.
_CORE_RE = re.compile(r"^(?:9072-[0-9A-Fa-f]{12}|\d{1,12})$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Bölüm adı olarak işe yaramayan değerler. Sitenin kendi JS'indeki (parça 2157)
# ayıklamanın aynısı: "S1-E1", "5. Bölüm" ya da barındırıcı adı ("Vudeo") —
# Naruto'nun eski kayıtlarında adlar gerçekten böyle.
_COP_AD_RE = re.compile(r"^(?:s\d+\s*-?\s*e\d+|\d+\s*\.?\s*b[öo]l[üu]m)$", re.I)
_KONAK_ADI_RE = re.compile(
    r"^(?:vudeo|streamsb|streamtape|mystream|sibnet|doodstream|dood|fembed|uqload|"
    r"mp4upload|vidmoly|okru|ok\.ru|mailru|mail\.ru|gdrive|faststream|voe|filelions|"
    r"streamwish|vidhide|luluvdo|filler)", re.I)
# Adın içinde "12. Bölüm"/"2. Sezon" geçerse başlık ayrıştırıcısı
# (`common.episode_parser`) numarayı addan okur: "3. Bölüm - 12. Bölüm" → 12.
# Sunucu tarayıcısı ve "Tüm kaynaklar" birleştirmesi başlığı ayrıştırdığı için
# böyle bir ad bölümü yanlış yere koyar; ad atılır, numara kalır.
_NUMARALI_AD_RE = re.compile(r"\d+\s*\.?\s*(?:b[öo]l[üu]m|sezon)", re.I)

# Gömülü oynatıcı konağı → oynatıcı adı (`common.oynatici_onceligi` adları).
# Listede olmayan konak ATILIR. Bu sitenin kayıtlarında sık geçen ama
# oynatılamayanlar (2026-09, JJK + Demon Slayer + Frieren + Naruto kayıtları,
# her konaktan 3 örnek yt-dlp ile denendi):
#   doodstream, streamtape, streamsb, mystream, vudeo, mega → yt-dlp'de karşılığı
#       yok ya da servis kapandı;
#   uqload.io → 3/3 Cloudflare sınavı (403; `generic:impersonate` ile de 403);
#   vidmoly.to → 3/3 "Unsupported URL".
# Genel oynatıcı listesinde (AnimeDepo arşivi için) bu ikisi duruyor; burada
# eklenseler `best_video`'nun sınırlı deneme bütçesini boşa harcarlardı.
# Alan adı TAM ya da alt alan adı olarak eşleşir ("m.ok.ru" evet, "book.ru" hayır).
_GOMULU_KONAKLAR: Tuple[Tuple[str, str], ...] = (
    ("sibnet.ru", "SIBNET"),
    ("ok.ru", "ODNOKLASSNIKI"),
    ("odnoklassniki.ru", "ODNOKLASSNIKI"),
    ("dailymotion.com", "DAILYMOTION"),
    ("dai.ly", "DAILYMOTION"),
    ("drive.google.com", "GDRIVE"),
    ("vk.com", "VK"),
    ("vkvideo.ru", "VK"),
    ("mail.ru", "MAIL"),
    ("myvi.ru", "MYVI"),
    ("myvi.tv", "MYVI"),
    ("myvi.top", "MYVI"),
    ("sendvid.com", "SENDVID"),
    ("yourupload.com", "YOURUPLOAD"),
)
# Gömülüler arasında deneme sırası. Genel öncelik listesinden
# (`oynatici_onceligi`) FARKLI, çünkü bu sitedeki ölçüm farklı (aynı deneme):
# sibnet 2/3 mp4'e çözüldü, Google Drive 2/3, Dailymotion 1/3, ok.ru 0/3
# ("telif nedeniyle engellendi" / "yazar engelli"). `best_video` yalnızca ilk
# birkaç adayı yokladığı için ölçümde çalışmayanlar sona.
_GOMULU_SIRASI: Tuple[str, ...] = (
    "SIBNET", "GDRIVE", "DAILYMOTION", "VK", "MAIL", "MYVI", "SENDVID",
    "YOURUPLOAD", "ODNOKLASSNIKI",
)
_OYNATICI = "ANIMPOW"


class AnimPowHatasi(RuntimeError):
    """AnimPow'a ulaşılamadı ya da yanıt beklenen biçimde değil."""


class AnimPowHizSiniri(AnimPowHatasi):
    """Site HTTP 429 döndü; sitenin istemcisi de bu durumda bekletiyor."""


class SifrelemeYok(AnimPowHatasi):
    """pycryptodome kurulu değil: eski API kullanılamaz, yalnızca QuadroGG."""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
_oturum_kilidi = threading.Lock()
_oturum = None


def _yeni_oturum():
    if _HAS_CURL:
        # Cloudflare arkasında ama JS sınavı yok; curl_cffi'nin Chrome parmak
        # izi yine de düz istemciden daha az dikkat çekiyor.
        return _http.Session(impersonate="chrome131")
    oturum = _http.Session()
    oturum.headers.update({"User-Agent": _YEDEK_UA})
    return oturum


def _session():
    """Modül genelinde tek HTTP oturumu (tembel kurulur)."""
    global _oturum
    with _oturum_kilidi:
        if _oturum is None:
            _oturum = _yeni_oturum()
        return _oturum


def _json_oku(yanit, ne: str) -> Any:
    try:
        return yanit.json()
    except ValueError as hata:
        parca = (getattr(yanit, "text", "") or "")[:80].replace("\n", " ")
        raise AnimPowHatasi(
            f"AnimPow {ne} JSON yerine başka bir şey döndü "
            f"(HTTP {yanit.status_code}: {parca!r}); site değişmiş ya da "
            "araya bir engel sayfası girmiş olabilir.") from hata


def _engel_denetle(yanit, ne: str) -> None:
    """429/403/503: boş sonuç değil, ENGELLENME. Sessizce yutulmaz."""
    durum = yanit.status_code
    if durum == 429:
        raise AnimPowHizSiniri(
            f"AnimPow {ne} isteği hız sınırına takıldı (HTTP 429); "
            "birkaç dakika sonra tekrar deneyin.")
    if durum in (403, 503):
        raise AnimPowHatasi(
            f"AnimPow {ne} isteği engellendi (HTTP {durum}); Cloudflare ya da "
            "sitenin erişim denetimi isteği geri çevirdi.")


# ─────────────────────────────────────────────────────────────────────────────
# Eski API: hibrit şifre (RSA-OAEP-SHA256 + AES-256-GCM)
# ─────────────────────────────────────────────────────────────────────────────
_sifre_kilidi = threading.Lock()
_sifre: Optional[Tuple[str, bytes]] = None     # (sessionId, AES anahtarı)


def _kripto():
    """pycryptodome'u İHTİYAÇ ANINDA yükle (bkz. modül belgesi)."""
    try:
        from Crypto.Cipher import AES, PKCS1_OAEP
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA
    except ImportError as hata:
        raise SifrelemeYok(
            "AnimPow'un ana kataloğu şifreli ve çözmek için pycryptodome "
            "gerekiyor (pip install pycryptodome); yalnızca QuadroGG "
            "kullanılabiliyor.") from hata
    return AES, PKCS1_OAEP, SHA256, RSA


def _el_sik() -> Tuple[str, bytes]:
    """Sitenin istemcisinin yaptığı el sıkışma; (sessionId, anahtar) döndürür."""
    _aes, pkcs1_oaep, sha256, rsa = _kripto()
    oturum = _session()
    yanit = oturum.get(f"{API_BASE_URL}/auth/public-key", headers=_H_SITE,
                       timeout=HTTP_TIMEOUT)
    _engel_denetle(yanit, "açık anahtar")
    if yanit.status_code != 200:
        raise AnimPowHatasi(
            f"AnimPow açık anahtarı alınamadı (HTTP {yanit.status_code}).")
    pem = (_json_oku(yanit, "açık anahtar") or {}).get("publicKey")
    try:
        acik_anahtar = rsa.import_key(pem)
    except (ValueError, IndexError, TypeError) as hata:
        raise AnimPowHatasi(
            "AnimPow'un verdiği açık anahtar okunamadı; şifreleme şeması "
            "değişmiş olabilir.") from hata

    anahtar = os.urandom(32)
    sid = str(uuid.uuid4())
    sifreli = pkcs1_oaep.new(acik_anahtar, hashAlgo=sha256).encrypt(anahtar)
    yanit = oturum.post(
        f"{API_BASE_URL}/auth/handshake",
        headers={**_H_SITE, "Content-Type": "application/json"},
        data=json.dumps({"sessionId": sid,
                         "encryptedKey": base64.b64encode(sifreli).decode("ascii")}),
        timeout=HTTP_TIMEOUT)
    _engel_denetle(yanit, "el sıkışma")
    govde = _json_oku(yanit, "el sıkışma") if yanit.status_code == 200 else {}
    if yanit.status_code != 200 or not (isinstance(govde, dict) and govde.get("ok")):
        raise AnimPowHatasi(
            f"AnimPow şifreli oturumu kurulamadı (HTTP {yanit.status_code}); "
            "el sıkışma şeması değişmiş olabilir.")
    return sid, anahtar


def _sifreli_oturum(dusen: Optional[str] = None) -> Tuple[str, bytes]:
    """Geçerli (sessionId, anahtar); yoksa ya da ``dusen`` ise yeniden kur.

    ``dusen``: sunucunun reddettiği sessionId. Birden çok thread aynı anda
    reddedilirse yalnızca ilki yeniden el sıkışır; diğerleri onun kurduğu
    oturumu kullanır.
    """
    global _sifre
    with _sifre_kilidi:
        if _sifre is None or (dusen is not None and _sifre[0] == dusen):
            _sifre = _el_sik()
        return _sifre


def _coz(govde: Any, anahtar: bytes) -> Any:
    """``{"iv", "authTag", "data"}`` zarfını AES-256-GCM ile aç; zarf değilse aynen."""
    if not (isinstance(govde, dict) and govde.get("iv") and govde.get("authTag")
            and govde.get("data")):
        return govde
    aes = _kripto()[0]
    try:
        sifre = aes.new(anahtar, aes.MODE_GCM, nonce=base64.b64decode(govde["iv"]))
        ham = sifre.decrypt_and_verify(base64.b64decode(govde["data"]),
                                       base64.b64decode(govde["authTag"]))
        return json.loads(ham.decode("utf-8"))
    except (ValueError, TypeError) as hata:
        # Etiket tutmadı (yanlış anahtar/bozuk veri) ya da içerik JSON değil.
        raise AnimPowHatasi(
            "AnimPow yanıtının şifresi çözülemedi; oturum anahtarı "
            "tutmuyor ya da şifreleme şeması değişmiş.") from hata


def _oturum_dustu(govde: Any) -> bool:
    hata = str((govde or {}).get("error") or "") if isinstance(govde, dict) else ""
    return "expired session key" in hata or "Encryption is required" in hata


def _bulunamadi(durum: int, govde: Any) -> bool:
    """Bilinmeyen anime: 404 ya da 500 {"ok": false, "error": "...404 Not Found..."}."""
    if durum == 404:
        return True
    hata = str(govde.get("error") or "") if isinstance(govde, dict) else ""
    return durum == 500 and "404" in hata


def _eski_api(yol: str) -> Optional[Dict[str, Any]]:
    """Eski API'den şifresi çözülmüş JSON; kayıt yoksa ``None``, hata yükselir."""
    sid, anahtar = _sifreli_oturum()
    for deneme in range(2):
        yanit = _session().get(
            API_BASE_URL + yol,
            headers={**_H_SITE, "X-Session-Id": sid, "Content-Type": "application/json"},
            timeout=HTTP_TIMEOUT)
        _engel_denetle(yanit, "katalog")
        if yanit.status_code == 400 and deneme == 0:
            govde = _json_oku(yanit, "katalog")
            if _oturum_dustu(govde):
                # Sunucu anahtarı unuttu (süresi doldu ya da yeniden başladı).
                sid, anahtar = _sifreli_oturum(dusen=sid)
                continue
        break
    govde = _json_oku(yanit, "katalog")
    if yanit.status_code == 200:
        veri = _coz(govde, anahtar)
        if not isinstance(veri, dict):
            raise AnimPowHatasi("AnimPow kataloğu beklenmeyen biçimde yanıt verdi.")
        if veri.get("ok") is False:
            raise AnimPowHatasi(
                f"AnimPow kataloğu hata döndü: {veri.get('error') or veri.get('mesaj')}")
        return veri
    govde = _coz(govde, anahtar)
    if _bulunamadi(yanit.status_code, govde):
        return None
    hata = govde.get("error") if isinstance(govde, dict) else None
    raise AnimPowHatasi(
        f"AnimPow kataloğu HTTP {yanit.status_code} döndü"
        + (f": {hata}" if hata else "") + ".")


# ─────────────────────────────────────────────────────────────────────────────
# QuadroGG (düz JSON)
# ─────────────────────────────────────────────────────────────────────────────
def _quadro(yol: str) -> Optional[Dict[str, Any]]:
    """QuadroGG'den JSON; anime orada yoksa ``None``, hata yükselir."""
    yanit = _session().get(ALT_URL + yol, headers=_H_QUADRO, timeout=HTTP_TIMEOUT)
    _engel_denetle(yanit, "QuadroGG")
    if yanit.status_code == 404:
        return None                     # {"success": false, "message": "Anime not found"}
    if yanit.status_code != 200:
        raise AnimPowHatasi(f"AnimPow (QuadroGG) HTTP {yanit.status_code} döndü.")
    govde = _json_oku(yanit, "QuadroGG")
    if not isinstance(govde, dict):
        raise AnimPowHatasi("AnimPow (QuadroGG) beklenmeyen biçimde yanıt verdi.")
    if not govde.get("success"):
        mesaj = str(govde.get("message") or "")
        if "not found" in mesaj.lower():
            return None
        raise AnimPowHatasi(f"AnimPow (QuadroGG) hata döndü: {mesaj or govde}")
    return govde


def _quadro_kimligi(core: str) -> Optional[str]:
    """QuadroGG yol kimliği; sayısal eski kimlikleri QuadroGG tanımıyor (404)."""
    if _UUID_RE.match(core):
        return core
    if core.startswith("9072-"):
        return f"get-from-core-id-{core}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────
def _core_denetle(core: Any) -> str:
    deger = str(core or "").strip()
    if not _CORE_RE.match(deger):
        raise ValueError(
            f"Geçersiz AnimPow kimliği: {core!r} (beklenen: '9072-XXXXXXXXXXXX' "
            "ya da sayısal kimlik).")
    return deger


def _tamsayi(deger: Any, varsayilan: Optional[int] = None) -> Optional[int]:
    if deger is None or deger == "":
        return varsayilan
    try:
        return int(deger)
    except (TypeError, ValueError):
        return None


def _anlamli_ad(ad: Any, anime_adi: str = "") -> str:
    """Bölüm adı gerçekten bir ad mı? Değilse boş dize."""
    metin = " ".join(str(ad or "").split())
    if not metin:
        return ""
    if anime_adi and metin.casefold() == anime_adi.casefold():
        return ""
    if _COP_AD_RE.match(metin) or _KONAK_ADI_RE.match(metin):
        return ""
    if _NUMARALI_AD_RE.search(metin):
        return ""
    return metin


def _hata_ozeti(hatalar: List[Tuple[str, BaseException]]) -> str:
    return "; ".join(f"{arka_uc}: {hata}" for arka_uc, hata in hatalar)


def _birlestir(ne: str, sonuc: list, hatalar: List[Tuple[str, BaseException]]) -> list:
    """Arka uç hataları için ortak karar: sonuç varsa döndür, yoksa yükselt.

    Bir arka ucun çökmesi diğerinin sonucunu götürmemeli (loglanır). Ama iki
    arka uç da sonuç vermediyse ve biri HATA verdiyse boş liste "yok" demek
    olur, oysa gerçek sebep ağ/protokol — o hata yükseltilir.

    pycryptodome'un olmaması (`SifrelemeYok`) sunucu imajında BEKLENEN durum:
    QuadroGG sonuç verdiyse yalnızca bilgi olarak loglanır. Hiç sonuç yoksa o
    da yükselir — kullanıcı boş liste yerine sebebi görmeli.
    """
    if hatalar and sonuc:
        beklenen = all(isinstance(hata, SifrelemeYok) for _, hata in hatalar)
        log.log(logging.INFO if beklenen else logging.WARNING,
                "AnimPow %s kısmi: %s", ne, _hata_ozeti(hatalar))
    if hatalar and not sonuc:
        raise AnimPowHatasi(f"AnimPow {ne} alınamadı — {_hata_ozeti(hatalar)}") \
            from hatalar[0][1]
    return sonuc


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def search_animpow(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """AnimPow'da anime ara.

    İki arka uç birleşir: önce eski API (kataloğun çoğu, romaji/İngilizce ad),
    sonra QuadroGG (Türkçe adla da eşleşir). Aynı anime core kimliğiyle tekilleşir.
    ``is_ecchi`` işaretli kayıtlar atılır (sitenin kendi bayrağı).

    Returns: ``[(core_id, başlık), ...]``; sonuç yoksa ``[]``.
    """
    sorgu = " ".join(str(query or "").split())
    if not sorgu or limit <= 0:
        return []
    sonuc: List[Tuple[str, str]] = []
    gorulen = set()
    hatalar: List[Tuple[str, BaseException]] = []

    def ekle(core: Any, baslik: Any, ecchi: Any) -> None:
        core = str(core or "").strip()
        baslik = " ".join(str(baslik or "").split())
        if not core or not baslik or ecchi or core in gorulen or not _CORE_RE.match(core):
            return
        gorulen.add(core)
        sonuc.append((core, baslik))

    try:
        veri = _eski_api("/anime/arama?" + urlencode({"q": sorgu,
                                                       "limit": max(limit, 20)}))
        for kayit in (veri or {}).get("veri") or []:
            if isinstance(kayit, dict):
                ekle(kayit.get("animpow_core_id"),
                     kayit.get("name_english") or kayit.get("name"),
                     kayit.get("is_ecchi"))
    except (AnimPowHatasi, OSError, ValueError) as hata:
        hatalar.append(("katalog", hata))

    try:
        veri = _quadro("/api/animes/catalog?" + urlencode(
            {"q": sorgu, "page": 1, "limit": 30, "sort_by": "popularity"}))
        for kayit in (veri or {}).get("data") or []:
            if isinstance(kayit, dict):
                ekle(kayit.get("animpow_core_id"), kayit.get("name"),
                     kayit.get("is_ecchi"))
    except (AnimPowHatasi, OSError, ValueError) as hata:
        hatalar.append(("QuadroGG", hata))

    return _birlestir("araması", sonuc, hatalar)[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Bölüm listesi
# ─────────────────────────────────────────────────────────────────────────────
_bolum_kilidi = threading.Lock()
_bolum_onbellegi: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _sade_kayit(kayit: Any) -> Optional[Dict[str, Any]]:
    """Eski API bölüm kaydının işe yarayan kısmı (önbellek küçük kalsın).

    Kayıt başına ~30 alan geliyor (açıklama, oy, tarih…); One Piece'te 4012
    kayıt. Yalnızca akış ve ad için gerekenler saklanıyor.
    """
    if not isinstance(kayit, dict):
        return None
    sezon = _tamsayi(kayit.get("season_num"), 1)
    bolum = _tamsayi(kayit.get("episode_num"))
    if sezon is None or bolum is None or bolum <= 0 or sezon < 0:
        return None
    pro = kayit.get("pro_cdn_data") if kayit.get("pro_cdn_active") else None
    return {
        "sezon": sezon,
        "bolum": bolum,
        "ad": kayit.get("episode_name"),
        "fansub": " ".join(str(kayit.get("fansub_name") or "").split()),
        "mp4": {q: kayit.get(f"cdn_mp4_{q}") for q in ("1080", "720", "480")
                if isinstance(kayit.get(f"cdn_mp4_{q}"), str)},
        "m3u8": kayit.get("cdn_m3u8") if isinstance(kayit.get("cdn_m3u8"), str) else None,
        "pro": pro if isinstance(pro, list) else [],
        "url": kayit.get("url") if isinstance(kayit.get("url"), str) else None,
    }


def _eski_bolumler(core: str) -> List[Dict[str, Any]]:
    """Eski API'deki bölüm × kaynak × fansub kayıtları (15 dk önbellekli)."""
    simdi = time.monotonic()
    with _bolum_kilidi:
        kayit = _bolum_onbellegi.get(core)
        if kayit and simdi - kayit[0] < _BOLUM_TTL:
            return kayit[1]
    veri = _eski_api(f"/anime/{core}/bolumler")
    ham = [] if veri is None else (veri.get("episodes") or veri.get("veri") or [])
    if not isinstance(ham, list):
        raise AnimPowHatasi("AnimPow bölüm listesi beklenmeyen biçimde geldi.")
    kayitlar = [k for k in map(_sade_kayit, ham) if k is not None]
    with _bolum_kilidi:
        _bolum_onbellegi[core] = (time.monotonic(), kayitlar)
        while len(_bolum_onbellegi) > _BOLUM_ONBELLEK_SINIRI:
            en_eski = min(_bolum_onbellegi, key=lambda c: _bolum_onbellegi[c][0])
            del _bolum_onbellegi[en_eski]
    return kayitlar


def _quadro_bolumleri(core: str) -> Tuple[str, Dict[Tuple[int, int], str]]:
    """QuadroGG'deki normal bölümler: (anime adı, {(sezon, bölüm): ad})."""
    qid = _quadro_kimligi(core)
    if qid is None:
        return "", {}
    veri = ((_quadro(f"/api/animes/{qid}/data-list") or {}).get("data")) or {}
    if not isinstance(veri, dict):
        raise AnimPowHatasi("AnimPow (QuadroGG) bölüm listesi beklenmeyen biçimde geldi.")
    adlar: Dict[Tuple[int, int], str] = {}
    for sezon_kaydi in veri.get("seasons") or []:
        if not isinstance(sezon_kaydi, dict):
            continue
        sezon = _tamsayi(sezon_kaydi.get("season"))
        for bolum_kaydi in sezon_kaydi.get("episodes") or []:
            if not isinstance(bolum_kaydi, dict):
                continue
            # OVA/özel bölümler ayrı numaralanıyor ve ayrı istek türü istiyor;
            # normal bölümlerle karışıp aynı numarayı almasınlar diye alınmıyor.
            if (bolum_kaydi.get("episode_type") or "regular") != "regular":
                continue
            bolum = _tamsayi(bolum_kaydi.get("episode"))
            if sezon is None or bolum is None or bolum <= 0 or sezon < 0:
                continue
            adlar.setdefault((sezon, bolum), str(bolum_kaydi.get("episode_title") or ""))
    return str(veri.get("name") or ""), adlar


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Animenin bölümleri, izleme sırasıyla (sezon, bölüm).

    İki arka ucun birleşimi: bir bölüm hangisinde varsa listede. Ad önce eski
    API'den (ilk anlamlı ad), yoksa QuadroGG'den.

    Returns: ``[("{core}:{sezon}:{bolum}", "1. Sezon 1. Bölüm - Ad"), ...]``.
    Birden fazla sezon yoksa başlık "5. Bölüm - Ad" biçimindedir (başlık
    ayrıştırıcısı ikisini de doğru okuyor).
    """
    core = _core_denetle(slug)
    hatalar: List[Tuple[str, BaseException]] = []
    eski: List[Dict[str, Any]] = []
    try:
        eski = _eski_bolumler(core)
    except (AnimPowHatasi, OSError, ValueError) as hata:
        hatalar.append(("katalog", hata))
    anime_adi, quadro = "", {}
    try:
        anime_adi, quadro = _quadro_bolumleri(core)
    except (AnimPowHatasi, OSError, ValueError) as hata:
        hatalar.append(("QuadroGG", hata))

    adlar: Dict[Tuple[int, int], str] = {}
    for kayit in eski:
        anahtar = (kayit["sezon"], kayit["bolum"])
        ad = _anlamli_ad(kayit["ad"], anime_adi)
        if not adlar.get(anahtar):
            adlar[anahtar] = ad
    for anahtar, ad in quadro.items():
        if not adlar.get(anahtar):
            adlar[anahtar] = _anlamli_ad(ad, anime_adi)

    cok_sezon = len({sezon for sezon, _ in adlar}) > 1
    sonuc: List[Tuple[str, str]] = []
    for sezon, bolum in sorted(adlar):
        baslik = f"{sezon}. Sezon {bolum}. Bölüm" if cok_sezon else f"{bolum}. Bölüm"
        ad = adlar[(sezon, bolum)]
        sonuc.append((f"{core}:{sezon}:{bolum}", f"{baslik} - {ad}" if ad else baslik))
    return _birlestir("bölüm listesi", sonuc, hatalar)


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _bolum_kimligini_coz(bolum_id: Any) -> Tuple[str, int, int]:
    parca = str(bolum_id or "").rsplit(":", 2)
    sezon = _tamsayi(parca[1]) if len(parca) == 3 else None
    bolum = _tamsayi(parca[2]) if len(parca) == 3 else None
    if sezon is None or bolum is None or sezon < 0 or bolum <= 0 \
            or not _CORE_RE.match(parca[0]):
        raise ValueError(
            f"Geçersiz AnimPow bölüm kimliği: {bolum_id!r} "
            "(beklenen: '<core>:<sezon>:<bölüm>').")
    return parca[0], sezon, bolum


def _gomuluyu_ac(url: str) -> str:
    """Sitenin kendi vekiline sarılı adresten asıl gömülü adresi çıkar.

    ``https://sibnet-api-server-v1.animpow.com/api/sibnet?url=<asıl>``: vekil
    500 dönüyor ("Sibnet WARP IP'sini de engelledi"), asıl adres ise yt-dlp ile
    çözülüyor. Protokolsüz ("//ok.ru/…") adreslere https eklenir.
    """
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        parca = urlsplit(url)
    except ValueError:
        return ""
    konak = (parca.hostname or "").lower()
    if konak == "animpow.com" or konak.endswith(".animpow.com"):
        ic = (parse_qs(parca.query).get("url") or [""])[0].strip()
        return _gomuluyu_ac(ic) if ic and ic != url else ""
    return url if parca.scheme in ("http", "https") and konak else ""


def _gomulu_oynatici(url: str) -> Optional[str]:
    konak = (urlsplit(url).hostname or "").lower()
    for alan, oynatici in _GOMULU_KONAKLAR:
        if konak == alan or konak.endswith("." + alan):
            return oynatici
    return None


def _etiket(*parcalar: str) -> str:
    return " - ".join(p for p in (" ".join(str(x or "").split()) for x in parcalar) if p)


def _quadro_akislari(core: str, sezon: int, bolum: int) -> List[Dict[str, str]]:
    qid = _quadro_kimligi(core)
    if qid is None:
        return []
    veri = _quadro(f"/api/animes/{qid}/episodes?"
                   + urlencode({"season": sezon, "episode": bolum})) or {}
    akislar: List[Dict[str, str]] = []
    for kayit in veri.get("data") or []:
        if not isinstance(kayit, dict):
            continue
        if (kayit.get("episode_type") or "regular") != "regular":
            continue
        url = kayit.get("cdn_url")
        if not (isinstance(url, str) and url.startswith("https://")):
            continue
        fansub = " ".join(str(kayit.get("fansub_name") or "").split())
        kalite = str(kayit.get("quality") or "").strip()
        # Referer YOK: s3i--cdn-* HLS düğümleri istemiyor (ölçüldü).
        akislar.append({"url": url, "label": _etiket(f"{kalite} HLS".strip(), fansub),
                        "type": "hls", "player": _OYNATICI, "fansub": fansub})
    return akislar


def _eski_akislar(core: str, sezon: int, bolum: int
                  ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """(doğrudan CDN akışları, gömülü oynatıcılar) — ayrı ayrı sıralanacaklar."""
    cdn: List[Dict[str, str]] = []
    gomulu: List[Dict[str, str]] = []
    for kayit in _eski_bolumler(core):
        if kayit["sezon"] != sezon or kayit["bolum"] != bolum:
            continue
        fansub = kayit["fansub"]
        for kalite, url in kayit["mp4"].items():
            if url.startswith("https://"):
                cdn.append({"url": url, "label": _etiket(f"{kalite}p MP4", fansub),
                            "type": "direct", "player": _OYNATICI, "fansub": fansub,
                            "referer": REFERER})
        if kayit["m3u8"] and kayit["m3u8"].startswith("https://"):
            cdn.append({"url": kayit["m3u8"], "label": _etiket("HLS", fansub),
                        "type": "hls", "player": _OYNATICI, "fansub": fansub,
                        "referer": REFERER})
        for pro in kayit["pro"]:
            if not isinstance(pro, dict):
                continue
            url = pro.get("link")
            if not (isinstance(url, str) and url.startswith("https://")):
                continue
            bicim = str(pro.get("format") or "").lower()
            tur = "hls" if bicim in ("m3u8", "hls") or ".m3u8" in url else "direct"
            kalite = str(pro.get("quality") or "").strip()
            if kalite.isdigit():
                kalite += "p"
            cdn.append({"url": url, "label": _etiket(f"{kalite} Pro".strip(), fansub),
                        "type": tur, "player": _OYNATICI, "fansub": fansub,
                        "referer": REFERER})
        if kayit["url"]:
            url = _gomuluyu_ac(kayit["url"])
            oynatici = _gomulu_oynatici(url) if url else None
            if oynatici:
                gomulu.append({"url": url, "label": _etiket(oynatici, fansub),
                               "type": "iframe", "player": oynatici, "fansub": fansub})
    return cdn, gomulu


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları.

    Sıra: QuadroGG HLS → "AnimPow Cdn 1" MP4 (1080 → 720 → 480, referer'lı) →
    gömülü oynatıcılar (bu sitede çalıştığı ölçülenler önce). `best_video`
    etiketteki çözünürlüğe göre yeniden sıralıyor; sıralama kararlı olduğu için
    aynı çözünürlükte buradaki sıra korunur.

    Returns: ``[{"url", "label", "type", "player", "fansub", "referer"?}, ...]``
    """
    core, sezon, bolum = _bolum_kimligini_coz(episode_id)
    hatalar: List[Tuple[str, BaseException]] = []
    quadro: List[Dict[str, str]] = []
    cdn: List[Dict[str, str]] = []
    gomulu: List[Dict[str, str]] = []
    try:
        quadro = _quadro_akislari(core, sezon, bolum)
    except (AnimPowHatasi, OSError, ValueError) as hata:
        hatalar.append(("QuadroGG", hata))
    try:
        cdn, gomulu = _eski_akislar(core, sezon, bolum)
    except (AnimPowHatasi, OSError, ValueError) as hata:
        hatalar.append(("katalog", hata))

    sira = {ad: i for i, ad in enumerate(_GOMULU_SIRASI)}
    gomulu.sort(key=lambda a: sira.get(a["player"], len(sira)))
    sonuc: List[Dict[str, str]] = []
    gorulen = set()
    for akis in quadro + cdn + gomulu:
        # Aynı dosya birden çok kayıtta: `best_video`'nun sınırlı deneme
        # bütçesi aynı adresi iki kez yoklamasın.
        if akis["url"] in gorulen:
            continue
        gorulen.add(akis["url"])
        sonuc.append(akis)
    return _birlestir("akışları", sonuc, hatalar)


def sifirla() -> None:
    """Oturumu, şifre anahtarını ve bölüm önbelleğini unut (testler, ayar değişimi)."""
    global _oturum, _sifre
    with _oturum_kilidi:
        _oturum = None
    with _sifre_kilidi:
        _sifre = None
    with _bolum_kilidi:
        _bolum_onbellegi.clear()


__all__ = [
    "search_animpow",
    "get_anime_episodes",
    "get_episode_streams",
    "sifirla",
    "AnimPowHatasi",
    "AnimPowHizSiniri",
    "SifrelemeYok",
    "BASE_URL",
    "API_BASE_URL",
    "ALT_URL",
    "REFERER",
]
