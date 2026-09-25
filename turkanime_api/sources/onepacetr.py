"""One Pace TR kaynağı — https://www.onepacetr.net

NEDEN BU SİTE: turkanime.tv kapandı. One Pace, One Piece animesinin dolgu
sahnelerinden arındırılmış hayran kurgusu (fan re-edit); One Pace TR onu Türkçe
altyazıyla yayımlıyor. Katalog dar (tek seri: 35 ark, ~450 bölüm) ama başka
hiçbir kaynakta yok ve site koruma/çerez istemiyor.

Site bir Vite/React SPA'sı: her adres aynı ~1 KB'lık kabuk HTML'i döndürür, veri
Heroku'daki bir Strapi v5 arka ucundan gelir. Arka uç jetonsuz isteğe 403,
yanlış jetona 401 döner. Jeton bir kullanıcı girişi DEĞİL: sitenin her
ziyaretçiye gönderdiği JS paketinde (`/assets/index-<hash>.js`) açıkça duran,
salt okunur, herkese açık bir "API token". Buna rağmen koda gömülmüyor: paketin
adı her yayında değişiyor ve jeton döndürülürse gömülü değer sessizce ölür.
Bunun yerine ana sayfadan GÜNCEL paket bulunup jeton çalışma anında çıkarılıyor
(süreç başına önbellekli; 401/403 ya da arka ucun taşındığını gösteren JSON
olmayan bir 404 gelince bir kez tazeleniyor, arka uç adresi de paketten).

    Yapılandırma  GET /                       → <script src="/assets/index-*.js">
                  GET /assets/index-*.js      → Authorization:"Bearer <hex>" ve
                                                "https://<api>/api/..." adresi
    Arama         GET {api}/seasons?...       → bütün arklar (sunucu tarafında
                                                arama YOK; site de yerelde süzüyor)
    Bölümler      GET {api}/seasons?filters[slug][$eq]=<ark>&populate[episodes]...
    Akışlar       GET {api}/episodes/<bolum_slug>  → players: [{name, url}, ...]

Kimlikler (kararlı ve kendi başına yeterli):
    kaynak_id  ark slug'ı ("wano", "arabasta") ya da bütün seri için
               ``SERI_KIMLIGI`` ("one-pace"). Tek seri, arklar = sezonlar.
    bolum_id   "<ark_slug>/<bolum_slug>" ("wano/hasir-sapkali-luffy-1").
               Sitenin kendi bölüm adresi (/bolum/<n>) KULLANILAMAZ: n, bütün
               yayımlanmış bölümlerin sırası ve yeni bölüm çıktıkça kayıyor.
               Bölüm slug'ı 443 bölümde tekil (ölçüldü); ark yalnızca okunur
               kimlik ve bölüm adresi için taşınıyor.

Oynatıcılar: her bölümde gdrive (443/443) ve çoğunda sibnet (437/443) var;
ikisi de yt-dlp ile çözülüyor (ölçüldü). Diğerleri (clone/short.icu, vidguard,
gettsu, mixdrop, streamtape, vidhide, upnshare) yt-dlp'de ya çıkarıcısız ya
404; `best_video` yalnızca ilk birkaç adayı denediği için listeye hiç
girmiyorlar. Sibnet'in mp4'ü sibnet referer'ı ister: referer'sız da,
onepacetr.net referer'ıyla da 403 dönüyor.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

from ..common.oynatici_onceligi import oncelik_anahtari

log = logging.getLogger(__name__)

BASE_URL = "https://www.onepacetr.net"
# Arka ucun bilinen adresi (2026-09). Asıl adres her seferinde JS paketinden
# okunuyor; bu yalnızca paket adresi taşımayı bırakırsa (ör. göreli adrese
# geçilirse) kullanılan yedek. Jetonun yedeği YOK: gömülü jeton döndürüldüğü
# gün sessizce 401 üretir, açık hata daha iyi.
API_BASE_URL = "https://onepacetradmin-v3-4f0db9f5d700.herokuapp.com/api"
HTTP_TIMEOUT = 20

# Tüm seriyi (bütün arkları izleme sırasıyla) temsil eden kaynak kimliği.
SERI_KIMLIGI = "one-pace"
SERI_BASLIGI = "One Pace"
FANSUB = "One Pace TR"

_YAPILANDIRMA_OMRU = 6 * 3600     # jeton nadiren değişir; paket 770 KB
_ZORLA_TAZELEME_ARALIGI = 60      # 401/403 fırtınasında paketi her istekte indirme
_KATALOG_OMRU = 30 * 60           # arama her sorguda ark listesini istemesin

# curl_cffi yoksa düz requests'e düşülüyor; o zaman tanıdık bir tarayıcı
# kimliği gönder. curl_cffi'de UA'yı impersonate ayarlıyor: elle ezmek TLS
# parmak izi ile başlığı birbirine düşürür.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PAKET_RE = re.compile(r"""<script[^>]+src=["']([^"']*/assets/index-[^"']+\.js)["']""")
# Paket küçültücüsü tırnak türünü değiştirebilir; anahtar/değer ikisi de esnek.
_JETON_RE = re.compile(
    r"""Authorization["']?\s*:\s*["'`]Bearer\s+([A-Za-z0-9._~+/=-]{32,})["'`]""")
_API_RE = re.compile(r"""["'`](https://[a-z0-9.-]+(?::\d+)?)/api/(?:seasons|episodes)\b""")

# Oynatılabilir (yt-dlp ile ölçülmüş) oynatıcılar: site adı → (etiket, player,
# beklenen konak, referer). Konak denetimi, "sibnet" adlı bir kayda başka bir
# adres yazılırsa sibnet referer'ının oraya gitmemesi için.
_OYNATICILAR: Dict[str, Tuple[str, str, str, Optional[str]]] = {
    "gdrive": ("Google Drive", "GDRIVE", "drive.google.com", None),
    "sibnet": ("Sibnet", "SIBNET", "video.sibnet.ru", "https://video.sibnet.ru/"),
}

# Seriyi bütünüyle çağıran sorgular. "one piece" de dahil: kullanıcı One Pace'i
# çoğunlukla asıl serinin adıyla arıyor.
_TAKMA_ADLAR = ("one pace", "onepace", "one piece", "onepiece")


class OnePaceTRHatasi(RuntimeError):
    """Siteye/arka uca ulaşılamadı, erişim reddedildi ya da yanıt beklenmedik.

    "Sonuç yok" ile "okunamadı" ayrı şeyler: arama motoru bu hatayı kaynağın
    adıyla `AramaSonuclari.hatalar`'a yazar, sunucu tarayıcısı kaynağı o tur
    için devre dışı bırakır. Boş liste dönseydik ikisi de "bu arkta bölüm
    yok" sanırdı.

    ``status_code``: hatanın HTTP karşılığı; yoksa None (ağ/biçim sorunu).
    Sunucu tarayıcısı (`turkanime_server/crawler/nezaket.hata_turu`) hatayı
    bununla sınıflıyor: 404/400 kalıcı (görev kapanır), 401/403/429
    engellenme, 5xx ve None geçici. Bulunamayan ark/bölüm (sitede kaldırılmış
    ya da adı değişmiş) bu alan olmadan "geçici" sayılıp her turda yeniden
    denenir ve art arda hatalar kaynağı bütünüyle dinlenmeye alırdı. Tersi
    de önemli: sunucunun kendisi yoksa (bkz. `_api`) kalıcı sayılmamalı.
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


def _yeni_oturum() -> Any:
    if _HAS_CURL:
        return _http.Session(impersonate="chrome131")
    oturum = _http.Session()
    oturum.headers.update({"User-Agent": _UA})
    return oturum


def _oturum() -> Any:
    oturum = getattr(_yerel, "oturum", None)
    if oturum is None:
        oturum = _yeni_oturum()
        _yerel.oturum = oturum
    return oturum


def _get(url: str, basliklar: Optional[Dict[str, str]] = None) -> Any:
    """Zaman aşımlı GET; ağ hatası açık Türkçe `OnePaceTRHatasi` olur."""
    try:
        return _oturum().get(url, headers=basliklar or {}, timeout=HTTP_TIMEOUT)
    except Exception as exc:  # pylint: disable=broad-except
        konak = urlsplit(url).hostname or url
        raise OnePaceTRHatasi(
            f"One Pace TR'ye ulaşılamadı ({konak}): {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Yapılandırma: arka uç adresi + herkese açık jeton (JS paketinden)
# ─────────────────────────────────────────────────────────────────────────────
_yapilandirma_kilidi = threading.Lock()
_yapilandirma: Dict[str, Any] = {"api": None, "jeton": None, "zaman": 0.0}


def paketten_yapilandirma(js: str) -> Tuple[Optional[str], Optional[str]]:
    """JS paketinden ``(api_kökü, jeton)``; bulunamayan None.

    Paketteki her `fetch` aynı başlığı ve aynı arka uç adresini taşıyor;
    birden fazla farklı değer görülürse ikisinde de en sık geçen alınır (tek
    bir unutulmuş deneme jetonu ya da test sunucusu adresi kazanmasın).
    """
    jetonlar = Counter(_JETON_RE.findall(js or ""))
    jeton = jetonlar.most_common(1)[0][0] if jetonlar else None
    konaklar = Counter(_API_RE.findall(js or ""))
    api = konaklar.most_common(1)[0][0] + "/api" if konaklar else None
    return api, jeton


def _site_durumu(kod: int) -> int:
    """Ana sayfa/paket hatasının sınıflandırma kodu (bkz. `OnePaceTRHatasi`).

    Engellenme (401/403/429) ve sunucu hataları olduğu gibi; diğerleri (ör.
    yeni yayın sırasında önbellekteki ana sayfanın gösterdiği, artık silinmiş
    paket → 404) 503: kaydın değil sitenin o anki durumu, kalıcı değil.
    """
    return kod if kod in (401, 403, 429) or kod >= 500 else 503


def _yapilandirmayi_getir() -> Tuple[str, str]:
    yanit = _get(BASE_URL + "/")
    if yanit.status_code != 200:
        raise OnePaceTRHatasi(
            f"One Pace TR ana sayfası HTTP {yanit.status_code} döndürdü.",
            status_code=_site_durumu(yanit.status_code))
    m = _PAKET_RE.search(yanit.text or "")
    if not m:
        raise OnePaceTRHatasi(
            "One Pace TR ana sayfasında uygulama paketi (index-*.js) bulunamadı; "
            "site yapısı değişmiş olabilir.")
    # Kök-göreli ("/assets/…"), tam ya da protokolsüz ("//cdn…/assets/…") olabilir.
    paket_adresi = urljoin(BASE_URL + "/", m.group(1))
    paket = _get(paket_adresi, {"Referer": BASE_URL + "/"})
    if paket.status_code != 200:
        raise OnePaceTRHatasi(
            f"One Pace TR uygulama paketi HTTP {paket.status_code} döndürdü.",
            status_code=_site_durumu(paket.status_code))
    api, jeton = paketten_yapilandirma(paket.text or "")
    if not jeton:
        raise OnePaceTRHatasi(
            "One Pace TR uygulama paketinde API anahtarı bulunamadı; "
            "site yapısı değişmiş olabilir.")
    return api or API_BASE_URL, jeton


def _yapilandirma_al(zorla: bool = False) -> Tuple[str, str]:
    """``(api_kökü, jeton)``; süreç içinde önbellekli.

    ``zorla``: 401/403 ya da JSON olmayan 404 sonrası tazele (bkz. `_api`).
    Yine de son tazelemeden bu yana `_ZORLA_TAZELEME_ARALIGI` geçmediyse
    eldeki kullanılır: arka uç bizi başka bir sebeple reddediyorsa her istekte
    770 KB'lık paketi yeniden indirmeyelim.
    """
    with _yapilandirma_kilidi:
        yas = time.time() - _yapilandirma["zaman"]
        if _yapilandirma["jeton"] and (
                (not zorla and yas < _YAPILANDIRMA_OMRU)
                or (zorla and yas < _ZORLA_TAZELEME_ARALIGI)):
            return _yapilandirma["api"], _yapilandirma["jeton"]
        api, jeton = _yapilandirmayi_getir()
        _yapilandirma.update(api=api, jeton=jeton, zaman=time.time())
        return api, jeton


def _strapi_bulunamadi_mi(yanit: Any) -> bool:
    """404 Strapi'nin kendi "kayıt yok" yanıtı mı (``{"error": {...}}``)?

    Strapi bulunamayan kaydı JSON gövdeyle bildirir. JSON olmayan bir 404 ise
    arka ucun o adreste artık olmadığı anlamına gelir (Heroku kaldırılan
    uygulama için HTML "no such app" sayfası döner): o "bölüm yok" değil,
    "sunucu taşındı" demek.
    """
    try:
        govde = yanit.json()
    except ValueError:
        return False
    return isinstance(govde, dict) and isinstance(govde.get("error"), dict)


def _api(yol: str) -> Optional[Any]:
    """``GET {api}/{yol}`` → çözülmüş JSON; kayıt yok (Strapi 404) → None.

    Diğer her sorun `OnePaceTRHatasi`. 401/403 (jeton döndürülmüş) ve JSON
    olmayan 404 (arka uç taşınmış; yeni adres yeni pakette) bir kez
    yapılandırma tazelenerek yeniden denenir.

    Strapi köşeli parantezleri (``filters[slug][$eq]``) olduğu gibi gidiyor;
    sunucu kabul ediyor, kodlanmış hâli de aynı sonucu veriyor.
    """
    yanit = None
    adres = ""
    kayit_yok = False
    for deneme in range(2):
        api, jeton = _yapilandirma_al(zorla=deneme > 0)
        adres = f"{api}/{yol}"
        yanit = _get(adres, {
            "Authorization": f"Bearer {jeton}",
            "Accept": "application/json",
            # SPA'nın kendi isteklerinin gönderdiği kökler; arka uç bugün
            # denetlemiyor ama sitenin sayfasından geliyormuş gibi görünmek
            # olası bir CORS/köken kuralına takılmamızı önler.
            "Origin": BASE_URL,
            "Referer": BASE_URL + "/",
        })
        kayit_yok = yanit.status_code == 404 and _strapi_bulunamadi_mi(yanit)
        tazele = yanit.status_code in (401, 403) or (yanit.status_code == 404 and not kayit_yok)
        if tazele and deneme == 0:
            log.info("One Pace TR arka ucu %s döndü; yapılandırma tazeleniyor",
                     yanit.status_code)
            continue
        break
    assert yanit is not None
    durum = yanit.status_code
    if kayit_yok:
        return None
    if durum == 404:
        # Ham durum 404 ama eksik olan kayıt değil sunucunun kendisi: 503
        # ("hizmet yok") olarak işaretleniyor. 404 işaretlenseydi sunucu
        # tarayıcısı bu kaynağın bütün görevlerini kalıcı olarak kapatırdı;
        # site yeni arka uca geçtiğinde de bir daha denemezdi.
        raise OnePaceTRHatasi(
            f"One Pace TR arka ucu {urlsplit(adres).hostname} adresinde yok "
            "(HTTP 404, JSON olmayan yanıt); sunucu taşınmış ya da kapatılmış olabilir.",
            status_code=503)
    if durum in (401, 403):
        raise OnePaceTRHatasi(
            f"One Pace TR arka ucu erişimi reddetti (HTTP {durum}); "
            "sitedeki API anahtarı değişmiş ya da kaldırılmış olabilir.", status_code=durum)
    if durum == 429:
        raise OnePaceTRHatasi(
            "One Pace TR arka ucu çok sık istek aldığını bildirdi (HTTP 429); "
            "biraz sonra yeniden deneyin.", status_code=durum)
    if durum >= 500:
        raise OnePaceTRHatasi(
            f"One Pace TR arka ucu şu an yanıt vermiyor (HTTP {durum}); sunucu "
            "uyuyor ya da geçici olarak kapalı olabilir.", status_code=durum)
    if durum != 200:
        raise OnePaceTRHatasi(
            f"One Pace TR arka ucu beklenmeyen bir yanıt verdi (HTTP {durum}).",
            status_code=durum)
    try:
        return yanit.json()
    except ValueError as exc:
        raise OnePaceTRHatasi(
            "One Pace TR arka ucundan beklenmeyen (JSON olmayan) yanıt geldi.") from exc


def _uc_yok_hatasi(ne: str) -> OnePaceTRHatasi:
    """Liste ucunun kendisi 404 (Strapi): eksik olan kayıt değil API'nin bir parçası.

    503 ("hizmet yok") olarak işaretleniyor: sunucu tarayıcısı 404'ü kalıcı
    sayıp görevi bir daha açmıyor, oysa bu kod güncellenince düzelen bir
    durum.
    """
    return OnePaceTRHatasi(
        f"One Pace TR {ne} alınamadı: arka uç bu ucu tanımıyor (HTTP 404); "
        "sitenin API'si değişmiş olabilir.", status_code=503)


def _veri_listesi(govde: Any, ne: str) -> List[Dict[str, Any]]:
    """Strapi liste yanıtının ``data`` dizisi; biçim tutmuyorsa açık hata."""
    veri = govde.get("data") if isinstance(govde, dict) else None
    if not isinstance(veri, list):
        raise OnePaceTRHatasi(f"One Pace TR {ne} yanıtı beklenen biçimde değil.")
    return [x for x in veri if isinstance(x, dict)]


# ─────────────────────────────────────────────────────────────────────────────
# Ortak yardımcılar
# ─────────────────────────────────────────────────────────────────────────────
def _norm(metin: Any) -> str:
    """Aksansız, küçük harf, yalnız harf/rakam/boşluk; Türkçe güvenli.

    "ı" NFKD ile "i"ye inmez ve `[^a-z0-9]` onu boşluğa çevirirdi ("Kılıç" →
    "k lic"); "İ".lower() de "i" + birleşik nokta verir. İkisi elle eşleniyor.
    """
    s = str(metin or "").replace("İ", "i").replace("I", "i").replace("ı", "i")
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _bosluk(metin: Any) -> str:
    """Baştaki/sondaki ve çift boşlukları topla (sitede " Luffy,  Bay 3" var)."""
    return re.sub(r"\s+", " ", str(metin or "")).strip()


def _sayi(deger: Any) -> int:
    try:
        return int(deger)
    except (TypeError, ValueError):
        return 0


def _slug_mu(deger: Any) -> bool:
    return isinstance(deger, str) and len(deger) <= 120 and bool(_SLUG_RE.fullmatch(deger))


def _ark_basligi(ark: Dict[str, Any]) -> str:
    return f"{SERI_BASLIGI} {_sayi(ark.get('number')):02d}: {_bosluk(ark.get('name'))}"


def _ark_gorseli(ark: Dict[str, Any]) -> Optional[str]:
    """Arama kartı için kapak: "small" (~450 px) yeterli, asıl dosya ~1000 px."""
    gorsel = ark.get("image")
    if not isinstance(gorsel, dict):
        return None
    bicimler = gorsel.get("formats")
    bicimler = bicimler if isinstance(bicimler, dict) else {}
    adaylar = [b.get("url") for b in (bicimler.get(boy) for boy in ("small", "medium", "thumbnail"))
               if isinstance(b, dict)]
    for adres in adaylar + [gorsel.get("url")]:
        if isinstance(adres, str) and adres.startswith("https://"):
            return adres
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
_katalog_kilidi = threading.Lock()
_katalog: Dict[str, Any] = {"arklar": None, "zaman": 0.0}


def _arklar() -> List[Dict[str, Any]]:
    """Bütün arklar, numara sırasıyla; `_KATALOG_OMRU` boyunca önbellekli.

    Sitede sunucu tarafı arama yok: /ara sayfası da bütün listeyi indirip
    tarayıcıda süzüyor. API listeyi sırasız döndürüyor, numaraya göre dizilir.
    Boş liste önbelleğe alınmaz (arka uç uyanırken boş dönerse kalıcılaşmasın).
    """
    with _katalog_kilidi:
        if (_katalog["arklar"] is not None
                and time.time() - _katalog["zaman"] < _KATALOG_OMRU):
            return _katalog["arklar"]
        govde = _api("seasons?pagination[pageSize]=100&fields[0]=name&fields[1]=number"
                     "&fields[2]=slug&populate[image][fields][0]=url"
                     "&populate[image][fields][1]=formats")
        if govde is None:
            raise _uc_yok_hatasi("ark listesi")
        arklar = sorted((a for a in _veri_listesi(govde, "ark listesi")
                         if _slug_mu(a.get("slug"))),
                        key=lambda a: _sayi(a.get("number")))
        if arklar:
            _katalog.update(arklar=arklar, zaman=time.time())
        return arklar


def _takma_ad_ayir(sorgu: str) -> Tuple[bool, str]:
    """``(seri_mi, kalan)``: sorgu seriyi adıyla mı çağırıyor, ardından ne var?

    "one piece" → (True, ""); "one pace wano" → (True, "wano");
    "one pi" (yazarken) → (True, ""); "wano" → (False, "wano").
    """
    for ad in sorted(_TAKMA_ADLAR, key=len, reverse=True):
        if sorgu == ad:
            return True, ""
        if sorgu.startswith(ad + " "):
            return True, sorgu[len(ad) + 1:].strip()
    # Yazılırken yarım kalan ad ("one p"); 5 harften kısası "one" gibi her
    # şeyle eşleşirdi.
    if len(sorgu) >= 5 and any(ad.startswith(sorgu) for ad in _TAKMA_ADLAR):
        return True, ""
    return False, sorgu


def _ark_uyuyor_mu(ark: Dict[str, Any], sorgu: str) -> bool:
    # Ad ve slug ayrı ayrı: sitede "Alabasta" arkının slug'ı "arabasta".
    if sorgu.isdigit():
        return _sayi(ark.get("number")) == int(sorgu)
    return sorgu in _norm(ark.get("name")) or sorgu in _norm(ark.get("slug"))


def _eslesen_arklar(sorgu: str) -> List[Tuple[str, str, Optional[str]]]:
    """``[(kaynak_id, başlık, görsel), ...]`` — seri önce, sonra arklar sırayla."""
    q = _norm(sorgu)
    if not q:
        return []
    arklar = _arklar()
    seri_mi, kalan = _takma_ad_ayir(q)
    if seri_mi and not kalan and arklar:
        # Seri girişi başta: `Kaynak.ara` sonucu `limit`'le kesiyor ve detay
        # sayfasının otomatik eşleştirmesi (limit=1) yalnızca ilk kaydı alıyor.
        # Tek bir arkı (4 bölüm) "One Piece"e bağlamaktansa bütün seriyi
        # (sezon = ark) bağlamak doğru; yoksa ark bölümleri asıl serinin
        # 1., 2., ... bölümleriyle aynı satıra düşerdi.
        return ([(SERI_KIMLIGI, SERI_BASLIGI, _ark_gorseli(arklar[0]))]
                + [(a["slug"], _ark_basligi(a), _ark_gorseli(a)) for a in arklar])
    return [(a["slug"], _ark_basligi(a), _ark_gorseli(a))
            for a in arklar if _ark_uyuyor_mu(a, kalan)]


def search_onepacetr(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """One Pace TR'de ara → ``[(kaynak_id, başlık), ...]``.

    "one pace"/"one piece" bütün seriyi (``SERI_KIMLIGI``) ve arkları; ark adı
    ("wano", "Alabasta") ya da "one pace <ark>" yalnızca uyan arkları döndürür.
    Uyan yoksa boş liste. Ark listesi okunamazsa `OnePaceTRHatasi`.
    """
    return [(kimlik, baslik) for kimlik, baslik, _ in _eslesen_arklar(query)][:max(0, limit)]


def zengin_ara(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Arama + kapak görseli: ``[{"slug", "title", "image"}, ...]``.

    Görsel aynı ark listesi yanıtında geliyor; ek istek yok.
    """
    return [{"slug": kimlik, "title": baslik, "image": gorsel}
            for kimlik, baslik, gorsel in _eslesen_arklar(query)][:max(0, limit)]


# ─────────────────────────────────────────────────────────────────────────────
# Bölüm listesi
# ─────────────────────────────────────────────────────────────────────────────
_BOLUM_ALANLARI = ("&fields[0]=name&fields[1]=number&fields[2]=slug"
                   "&populate[episodes][fields][0]=name&populate[episodes][fields][1]=number"
                   "&populate[episodes][fields][2]=slug&populate[episodes][fields][3]=version")


def bolum_basligi(ark: Dict[str, Any], bolum: Dict[str, Any]) -> str:
    """"35. Sezon 55. Bölüm - Wano: Hasır Şapkalı Luffy (Extended)".

    Ark = sezon. Uygulama bölümleri başlıktan (sezon, bölüm) anahtarına çevirip
    birleştiriyor (`common.episode_parser.merge_episodes`); arksız "1. Bölüm"
    35 arkın 1. bölümünü tek satıra indirirdi. Aynı bölüm ark listesinde de
    seri listesinde de AYNI başlığı taşır; sunucu tarayıcısı ikisini aynı
    kümeye koysa bile anahtarlar çakışmaz.
    """
    ad = _bosluk(bolum.get("name"))
    ek = " (Extended)" if _bosluk(bolum.get("version")).casefold() == "extended" else ""
    return (f"{_sayi(ark.get('number'))}. Sezon {_sayi(bolum.get('number'))}. Bölüm - "
            f"{_bosluk(ark.get('name'))}" + (f": {ad}" if ad else "") + ek)


def _ark_bolumleri(ark: Dict[str, Any]) -> List[Tuple[str, str]]:
    bolumler = [b for b in (ark.get("episodes") or [])
                if isinstance(b, dict) and _slug_mu(b.get("slug"))]
    # API bölümleri sırasız döndürüyor; numara ark içinde 1..N.
    bolumler.sort(key=lambda b: _sayi(b.get("number")))
    return [(f"{ark['slug']}/{b['slug']}", bolum_basligi(ark, b)) for b in bolumler]


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Arkın (ya da ``SERI_KIMLIGI`` ile bütün serinin) bölümleri, izleme sırasıyla.

    Returns: ``[("<ark>/<bolum_slug>", "<ark_no>. Sezon <n>. Bölüm - <Ark>: <ad>"), ...]``
    Raises: `OnePaceTRHatasi` — geçersiz kimlik, ark bulunamadı, ağ/arka uç
        hatası. Ark var ama yayımlanmış bölümü yoksa boş liste.
    """
    slug = str(slug or "").strip()
    if not _slug_mu(slug):
        raise OnePaceTRHatasi(f"Geçersiz One Pace TR ark kimliği: {slug!r}", status_code=400)
    if slug == SERI_KIMLIGI:
        govde = _api("seasons?pagination[pageSize]=100" + _BOLUM_ALANLARI)
    else:
        govde = _api(f"seasons?filters[slug][$eq]={slug}" + _BOLUM_ALANLARI)
    if govde is None:
        raise _uc_yok_hatasi("bölüm listesi")
    arklar = [a for a in _veri_listesi(govde, "bölüm listesi") if _slug_mu(a.get("slug"))]
    if slug != SERI_KIMLIGI:
        arklar = [a for a in arklar if a["slug"] == slug]
        if not arklar:
            # Strapi süzgeci bilinmeyen slug'a 200 + boş liste döndürüyor.
            raise OnePaceTRHatasi(
                f"One Pace TR'de '{slug}' arkı bulunamadı; site kaldırmış ya da "
                "adını değiştirmiş olabilir. Yeniden arayın.", status_code=404)
    arklar.sort(key=lambda a: _sayi(a.get("number")))
    out: List[Tuple[str, str]] = []
    for ark in arklar:
        out.extend(_ark_bolumleri(ark))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _bolum_slugu(episode_id: str) -> str:
    kimlik = str(episode_id or "").strip()
    ark, _, bolum = kimlik.rpartition("/")
    if not _slug_mu(bolum) or (ark and not _slug_mu(ark)):
        raise OnePaceTRHatasi(f"Geçersiz One Pace TR bölüm kimliği: {kimlik!r}",
                              status_code=400)
    return bolum


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün oynatılabilir akışları: önce Google Drive, sonra Sibnet.

    Returns: ``[{"url", "label", "type", "player", "fansub", "referer"?}, ...]``
        Sibnet akışı ``referer=https://video.sibnet.ru/`` taşır (mp4 onsuz ve
        onepacetr.net referer'ıyla 403). Oynatılabilir oynatıcısı olmayan
        bölümde boş liste.
    Raises: `OnePaceTRHatasi` — geçersiz kimlik, bölüm bulunamadı (404),
        ağ/arka uç hatası.
    """
    bolum = _bolum_slugu(episode_id)
    # Sitenin kendi kullandığı uç: bölümü SLUG ile arayan özel denetleyici
    # (documentId burada 404 verir).
    govde = _api(f"episodes/{bolum}")
    if govde is None:
        raise OnePaceTRHatasi(
            f"One Pace TR'de '{bolum}' bölümü bulunamadı; site kaldırmış ya da "
            "adını değiştirmiş olabilir.", status_code=404)
    veri = govde.get("data") if isinstance(govde, dict) else None
    if not isinstance(veri, dict):
        raise OnePaceTRHatasi("One Pace TR bölüm yanıtı beklenen biçimde değil.")

    cozunurluk = _bosluk(veri.get("resolution"))
    akislar: List[Dict[str, str]] = []
    for oynatici in veri.get("players") or []:
        akis = _oynatici_akisi(oynatici, cozunurluk)
        if akis is not None and all(a["url"] != akis["url"] for a in akislar):
            akislar.append(akis)
    # Ortak oynatıcı önceliği: GDRIVE, SIBNET'ten önce. `sort` kararlı; aynı
    # oynatıcının birden çok kaydı sitenin sırasını korur.
    akislar.sort(key=lambda a: oncelik_anahtari(a["player"]))
    return akislar


def _oynatici_akisi(oynatici: Any, cozunurluk: str) -> Optional[Dict[str, str]]:
    """Sitenin oynatıcı kaydı → akış; gizli, bozuk ya da oynatılamayan kayıtta None."""
    if not isinstance(oynatici, dict) or oynatici.get("hide"):
        return None
    tanim = _OYNATICILAR.get(_bosluk(oynatici.get("name")).casefold())
    if tanim is None:
        return None
    etiket, player, konak, referer = tanim
    adres = _bosluk(oynatici.get("url"))
    parca = urlsplit(adres)
    if parca.scheme != "https" or (parca.hostname or "").lower() != konak:
        return None                     # "#", boş ya da beklenmeyen konak
    akis = {
        "url": adres,
        "label": f"{etiket} {cozunurluk}".strip(),
        "type": "iframe",               # gömme sayfası; yt-dlp çözüyor
        "player": player,
        "fansub": FANSUB,
    }
    if referer:
        akis["referer"] = referer
    return akis


def bolum_adresi(episode_id: str) -> str:
    """Bölüm kimliği → insan için adres (`AdapterBolum.url`).

    Sitenin /bolum/<n> adresi kayan bir sıra numarası, kimlik olamaz; ark
    sayfası + bölüm slug'ı (parça) hem kararlı hem tekil.
    """
    ark, _, bolum = str(episode_id or "").rpartition("/")
    return f"{BASE_URL}/{ark}#{bolum}" if ark else f"{BASE_URL}/#{bolum}"


def sifirla() -> None:
    """Önbellekleri boşalt (testler ve elle tazeleme için)."""
    with _yapilandirma_kilidi:
        _yapilandirma.update(api=None, jeton=None, zaman=0.0)
    with _katalog_kilidi:
        _katalog.update(arklar=None, zaman=0.0)


__all__ = [
    "BASE_URL",
    "API_BASE_URL",
    "SERI_KIMLIGI",
    "OnePaceTRHatasi",
    "search_onepacetr",
    "zengin_ara",
    "get_anime_episodes",
    "get_episode_streams",
    "bolum_adresi",
    "bolum_basligi",
    "paketten_yapilandirma",
    "sifirla",
]
