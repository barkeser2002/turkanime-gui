"""
Animexe Kaynağı
https://animexe.com

Site sunucu tarafında çizilen (Laravel) sayfalar veriyor: giriş, çerez, CSRF ya
da JS kapısı yok. Cloudflare arkasında ama challenge göstermiyor; düz istek
yetiyor. Yine de curl_cffi ile tarayıcı parmak izi taklit ediliyor ki CF bir
gün sıkılaştırırsa ilk kırılan kaynak bu olmasın.

Uçlar:
- Arama:    GET /search?q=<sorgu>[&page=N]    → `a-card` kartları (sayfa başına 24)
            yedek: GET /search/suggest?q=       → JSON, en çok 7 sonuç
- Bölümler: GET /anime/<slug>                  → bütün `ep-card`lar tek sayfada
            (One Piece: 1165 kart). JSON-LD listesi 20'de kesiliyor, kullanılmıyor.
- Akışlar:  GET /watch/<slug>/<sezon>/<bölüm>  → `const VIDEO_SOURCES = [...]`

Kimlikler:
- anime  = sitenin slug'ı ("one-piece-13928"; sondaki sayı slug'ın parçası)
- bölüm  = "slug/sezon/bölüm" ("jujutsu-kaisen-1992/3/12"). Doğrudan
  `/watch/<bölüm>` yoluna karşılık geliyor; sezon kimliğin içinde olduğu için
  ikinci bir istek gerekmeden bölüm sayfası açılıyor.

Akışlar YALNIZCA fansub akışları (`"source": "animecix"`, tau-video CDN'lerinde
Türkçe altyazısı gömülü MP4). Site her adresi kendi `/stream/proxy?u=<base64>`
vekiline sarıyor; o vekil MP4'te Range'i yok sayıp bütün dosyayı akıttığı için
(oynatıcıda ileri sarma çalışmaz) adres çözülüp CDN'e doğrudan gidiliyor.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

try:
    from curl_cffi import requests as _http
    _HAS_CURL = True
except ImportError:  # pragma: no cover - curl_cffi requirements.txt'te var
    import requests as _http  # type: ignore[no-redef]
    _HAS_CURL = False

# Engel tespiti tek kaynaktan: iki liste ayrışırsa "Just a moment" bir yerde
# engel, başka yerde boş sonuç sayılır (bkz. ANIME_PROVIDER_GUIDE.md).
try:
    from turkanime_api.common.cf_bypass import (
        CHALLENGE_MARKERS, ENGEL_DURUMLARI, USER_AGENTS,
    )
except ImportError:  # pragma: no cover - paket dışı kullanım
    ENGEL_DURUMLARI = frozenset({403, 429, 503})
    CHALLENGE_MARKERS = ("Just a moment", "Checking your browser", "challenge-platform")
    USER_AGENTS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"]


BASE_URL = "https://animexe.com"
# tau-video CDN'leri şu an Referer'a bakmıyor (Referer'sız da, animexe.com ile de
# 206 dönüyor). Sitede izleyen bir tarayıcının göndereceği değeri veriyoruz ki
# CDN bir gün Referer denetimi eklerse ilk bu kırılmasın.
AKIS_REFERER = BASE_URL + "/"
HTTP_TIMEOUT = 15
# Ölü CDN'ler (renjiabari, yhwach, misakina…) TLS'ten sonra ~11 sn bekletip
# bağlantıyı kesiyor. Yoklama bunun altında kalmalı; canlı uç ~1 sn'de 206 döner.
YOKLAMA_TIMEOUT = 6
YOKLAMA_ISCI = 8
SAYFA_BASINA = 24          # arama sayfası başına kart
AZAMI_SAYFA = 3            # 72 sonuçtan fazlasını hiçbir arayüz göstermiyor
IMPERSONATE = "chrome131"

_SLUG = re.compile(r"[a-z0-9][a-z0-9\-]*")
# Sezon/bölüm hanesi sınırlı: kimlik kullanıcıdan/arşivden de gelebilir ve
# doğrudan URL yoluna giriyor ("../" ya da sorgu dizgisi sızmasın).
_BOLUM_ID = re.compile(r"([a-z0-9][a-z0-9\-]*)/(\d{1,3})/(\d{1,5})")

# Kartlar sınıf adıyla bulunuyor. Aradaki `(?:(?!…).)*?` bir sonraki karta
# taşmayı önlüyor: başlığı eksik bir kart, sonraki kartın başlığını çalmasın.
# Alan adı kalıba gömülü değil; site taşınırsa yalnızca BASE_URL değişir.
_ARAMA_KARTI = re.compile(
    r'<a\s+href="(?:https?://[^/"]+)?/anime/([^"/?#]+)"\s+class="a-card(?:\s[^"]*)?"\s*>'
    r'((?:(?!class="a-card).)*?)<div class="a-title">([^<]*)</div>', re.S)
_KART_GORSELI = re.compile(r'<img\s+src="([^"]+)"')
_SONUC_SAYISI = re.compile(r"Toplam\s*<strong>\s*(\d+)\s*</strong>\s*anime bulundu")

_BOLUM_KARTI = re.compile(
    r'<a\s+href="(?:https?://[^/"]+)?/watch/([a-z0-9\-]+)/(\d+)/(\d+)"\s+'
    r'class="ep-card(?:\s[^"]*)?"\s*>'
    r'(?:(?!class="ep-card[\s"]).)*?<div class="ep-card-title">([^<]*)</div>', re.S)
# Bölüm kartı olmayan ama gerçekten anime sayfası olan yanıt (henüz bölümü
# eklenmemiş seri). Bunu tanımak, "site tasarımı değişti"yi "bölüm yok"tan ayırır.
_ANIME_BASLIGI = re.compile(r'class="ah-title"')
# Site bazı bölümlere yalnızca "2. Bölüm" / "2. Sezon 3. Bölüm" yazıyor; bunu
# başlığa ikinci kez eklemek "2. Bölüm - 2. Bölüm" gibi tekrar üretiyor.
_JENERIK_BASLIK = re.compile(r"^\s*(\d+\.\s*Sezon\s*)?\d+\.\s*B[öo]l[üu]m\s*$", re.I)

_VIDEO_SOURCES = re.compile(
    r"const\s+VIDEO_SOURCES\s*=\s*(\[.*?\])\s*;\s*(?:\n|</script>|const\s|let\s|var\s)", re.S)
# Yedek: aynı veri kaynak sekmelerinin data-* özniteliklerinde de var. Oynatıcı
# betiği yeniden yazılıp sabit taşınırsa sekmeler hâlâ okunabilir.
_KAYNAK_SEKMESI = re.compile(r'<button\s+class="src-tab[^"]*"(.*?)>', re.S)
_DATA_OZNITELIGI = re.compile(r'data-(url|lbl|key|type)="([^"]*)"')
_COZUNURLUK = re.compile(r"\s*\((\d{3,4}p)\)\s*$")
_KALITE = re.compile(r"\d{3,4}p")

# Anizium'un CDN'i. Aşağıdaki süzgeç `source` alanına ek olarak konağa da bakıyor:
# sekme yedeğinde `source` alanı yok.
_ANIZIUM_KONAGI = re.compile(r"(^|\.)aniziumserver\.[a-z]+$")
# tau-video'nun embed sayfası CF 403 veriyor ve yt-dlp çözemiyor.
_EMBED_KONAGI = re.compile(r"(^|\.)tau-video\.xyz$")
_VEKIL_YOLLARI = ("/stream/proxy", "/cf-proxy", "/stream/seg")

_oturum_kilidi = threading.Lock()
_paylasilan_oturum = None


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
def _yeni_oturum():
    """Yeni HTTP oturumu. Testler ağa çıkmamak için bunu sahteler."""
    if _HAS_CURL:
        return _http.Session(impersonate=IMPERSONATE)
    oturum = _http.Session()
    oturum.headers.update({"User-Agent": USER_AGENTS[0]})
    return oturum


def _oturum():
    """Site istekleri için paylaşılan oturum (bağlantı yeniden kullanılsın)."""
    global _paylasilan_oturum
    with _oturum_kilidi:
        if _paylasilan_oturum is None:
            _paylasilan_oturum = _yeni_oturum()
        return _paylasilan_oturum


def _engel_sayfasi_mi(yanit) -> bool:
    try:
        bas = yanit.text[:6000]
    except Exception:
        return False
    return any(iz in bas for iz in CHALLENGE_MARKERS)


def _getir(yol: str, *, params: Optional[Dict[str, Any]] = None,
           headers: Optional[Dict[str, str]] = None):
    """Siteye GET. 404 → None; engel/ağ hatası → ``ConnectionError``.

    5xx ve ağ hatası BİR kez yeniden deneniyor: aramada tek seferlik bir 503
    ölçüldü, hemen ardından aynı istek 200 döndü. 403/429 ya da challenge
    sayfası yeniden denenmiyor; aynı parmak iziyle ikinci istek de engellenir
    ve kullanıcı "sonuç yok" yerine gerçek sebebi görmeli.
    """
    url = yol if yol.startswith("http") else BASE_URL + yol
    son_hata = ""
    for _deneme in range(2):
        try:
            yanit = _oturum().get(url, params=params, headers=headers,
                                  timeout=HTTP_TIMEOUT)
        except Exception as exc:  # ağ/zaman aşımı: bir kez daha dene
            son_hata = f"{type(exc).__name__}: {exc}"
            continue
        kod = yanit.status_code
        if kod == 404:
            return None
        if kod >= 500:
            son_hata = f"HTTP {kod}"
            continue
        if kod in ENGEL_DURUMLARI or _engel_sayfasi_mi(yanit):
            raise ConnectionError(
                f"Animexe isteği engelledi (HTTP {kod}, {url}). Bir süre sonra "
                "tekrar deneyin ya da başka bir kaynak seçin.")
        if kod != 200:
            raise ConnectionError(f"Animexe beklenmeyen yanıt verdi: HTTP {kod} ({url})")
        return yanit
    raise ConnectionError(f"Animexe'ye ulaşılamadı ({url}): {son_hata}")


# ─────────────────────────────────────────────────────────────────────────────
# Arama
# ─────────────────────────────────────────────────────────────────────────────
def _metin(ham: str) -> str:
    return " ".join(unescape(ham or "").split())


def arama_sayfasini_ayristir(html: str) -> List[Dict[str, Any]]:
    """Arama sayfasındaki kartlar → ``[{"slug", "title", "image"}, ...]`` (site sırası)."""
    out: List[Dict[str, Any]] = []
    gorulen = set()
    for slug, ic, baslik in _ARAMA_KARTI.findall(html or ""):
        if slug in gorulen or not _SLUG.fullmatch(slug):
            continue
        gorulen.add(slug)
        gorsel = _KART_GORSELI.search(ic)
        out.append({"slug": slug, "title": _metin(baslik) or slug,
                    "image": unescape(gorsel.group(1)) if gorsel else None})
    return out


def oneri_yanitini_ayristir(veri: Any) -> List[Dict[str, Any]]:
    """`/search/suggest` JSON'u → ``[{"slug", "title", "image"}, ...]``."""
    out: List[Dict[str, Any]] = []
    kayitlar = veri.get("results") if isinstance(veri, dict) else None
    for kayit in kayitlar if isinstance(kayitlar, list) else []:
        if not isinstance(kayit, dict):
            continue
        slug = str(kayit.get("slug") or "")
        if not _SLUG.fullmatch(slug):
            continue
        baslik = kayit.get("title") or kayit.get("title_en") or slug
        out.append({"slug": slug, "title": _metin(str(baslik)),
                    "image": kayit.get("cover") or None})
    return out


def _oneri_ara(sorgu: str) -> List[Dict[str, Any]]:
    yanit = _getir("/search/suggest", params={"q": sorgu},
                   headers={"Accept": "application/json"})
    if yanit is None:
        raise ConnectionError("Animexe arama ucu bulunamadı (HTTP 404); site yapısı değişmiş olabilir.")
    try:
        veri = json.loads(yanit.text)
    except ValueError as exc:
        raise ValueError(f"Animexe arama yanıtı çözümlenemedi: {exc}") from exc
    return oneri_yanitini_ayristir(veri)


def search_animexe_zengin(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Kapak görseliyle arama: ``[{"slug", "title", "image"}, ...]``.

    Sonuç yoksa boş liste. Engel/ağ hatası ``ConnectionError`` olarak yükselir
    (arama motoru kaynak başına yakalayıp raporluyor).
    """
    sorgu = " ".join((query or "").split())
    if len(sorgu) < 2:           # site 2 karakterden kısa sorguya sonuç vermiyor
        return []
    limit = max(1, int(limit or 1))
    out: List[Dict[str, Any]] = []
    gorulen = set()
    for sayfa in range(1, AZAMI_SAYFA + 1):
        params: Dict[str, Any] = {"q": sorgu}
        if sayfa > 1:
            params["page"] = sayfa
        yanit = _getir("/search", params=params)
        html = yanit.text if yanit is not None else ""
        kartlar = arama_sayfasini_ayristir(html)
        if sayfa == 1 and not kartlar:
            # "Toplam 0 anime bulundu" sitenin açık "sonuç yok" cevabı. Sayaç da
            # yoksa sayfa tanınmıyor demektir: JSON öneri ucuna düş, orada da
            # bir şey yoksa gerçekten sonuç yok.
            if _SONUC_SAYISI.search(html):
                return []
            return _oneri_ara(sorgu)[:limit]
        for kart in kartlar:
            if kart["slug"] not in gorulen:
                gorulen.add(kart["slug"])
                out.append(kart)
        if len(out) >= limit or len(kartlar) < SAYFA_BASINA:
            break
    return out[:limit]


def search_animexe(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """Animexe'de ara → ``[(slug, başlık), ...]``; sonuç yoksa boş liste."""
    return [(k["slug"], k["title"]) for k in search_animexe_zengin(query, limit)]


# ─────────────────────────────────────────────────────────────────────────────
# Bölüm listesi
# ─────────────────────────────────────────────────────────────────────────────
def _slug_dogrula(slug: str) -> str:
    temiz = (slug or "").strip().strip("/")
    if not _SLUG.fullmatch(temiz):
        raise ValueError(f"Geçersiz Animexe anime kimliği: {slug!r}")
    return temiz


def anime_sayfasini_ayristir(slug: str, html: str) -> List[Tuple[str, str]]:
    """Anime sayfasındaki bölüm kartları → izleme sırasıyla ``[(bolum_id, başlık)]``.

    Sıra sayısal (sezon, bölüm): sitenin sezon numaraları kendine özgü
    (Jujutsu Kaisen'de yalnızca 2 ve 3; Naruto'da 220 bölümün ardından tek
    başına bir "2. sezon 3. bölüm") ve dizgi sıralaması 10'u 9'dan önce koyar.
    Başka bir animenin kartı (öneri şeridi) kimliğe karışmasın diye slug
    eşleşmesi aranıyor.
    """
    satirlar: Dict[Tuple[int, int], str] = {}
    for kart_slug, sezon, bolum, baslik in _BOLUM_KARTI.findall(html or ""):
        if kart_slug != slug:
            continue
        anahtar = (int(sezon), int(bolum))
        satirlar.setdefault(anahtar, _metin(baslik))
    cok_sezon = len({s for s, _ in satirlar}) > 1
    out: List[Tuple[str, str]] = []
    for (sezon, bolum), baslik in sorted(satirlar.items()):
        etiket = f"{sezon}. Sezon {bolum}. Bölüm" if cok_sezon else f"{bolum}. Bölüm"
        if baslik and not _JENERIK_BASLIK.match(baslik):
            etiket += f" - {baslik}"
        out.append((f"{slug}/{sezon}/{bolum}", etiket))
    return out


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """Animenin bölümleri → ``[("slug/sezon/bölüm", başlık), ...]`` izleme sırasıyla.

    Raises:
        ValueError: kimlik geçersiz ya da sayfa çözümlenemedi (site değişmiş).
        LookupError: sitede böyle bir anime yok (HTTP 404).
        ConnectionError: engel ya da ağ hatası.
    """
    slug = _slug_dogrula(slug)
    yanit = _getir(f"/anime/{slug}")
    if yanit is None:
        raise LookupError(f"Animexe'de '{slug}' adlı anime bulunamadı (HTTP 404).")
    bolumler = anime_sayfasini_ayristir(slug, yanit.text)
    if not bolumler and not _ANIME_BASLIGI.search(yanit.text):
        raise ValueError(
            f"Animexe anime sayfası çözümlenemedi ({slug}): ne bölüm kartı ne "
            "başlık bulundu; site tasarımı değişmiş olabilir.")
    return bolumler


# ─────────────────────────────────────────────────────────────────────────────
# Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def _b64_coz(deger: str) -> str:
    # parse_qs "+"yı boşluğa çeviriyor; base64 alfabesinde boşluk yok, "+" var.
    ham = unquote(deger).strip().replace(" ", "+")
    ham += "=" * (-len(ham) % 4)
    try:
        return base64.b64decode(ham, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        # URL-güvenli alfabe ("-_"). Bu da katı: gevşek çözücü alfabe dışı
        # karakterleri atıp çöp bir "adres" üretebilirdi.
        return base64.b64decode(ham, altchars=b"-_", validate=True).decode("utf-8")


def vekili_coz(url: str) -> str:
    """Sitenin vekil adresini gerçek CDN adresine çevir.

    ``/stream/proxy?u=<base64>`` (ve ``/cf-proxy``, ``/stream/seg``) → base64
    çözülür; ``/hls-proxy.php?url=<yüzde kodlu>`` → yüzde kodu çözülür. Vekil
    olmayan adres aynen döner. Çözülemeyen base64 ``ValueError`` yükseltir.
    """
    parca = urlsplit(url or "")
    sorgu = parse_qs(parca.query)
    if parca.path.endswith(_VEKIL_YOLLARI) and sorgu.get("u"):
        try:
            return _b64_coz(sorgu["u"][0])
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"Animexe vekil adresi çözülemedi: {url}") from exc
    if parca.path.endswith("/hls-proxy.php") and sorgu.get("url"):
        return sorgu["url"][0]   # parse_qs yüzde kodunu zaten çözdü
    return url


def _ham_kaynaklar(html: str) -> Optional[List[Dict[str, Any]]]:
    """Sayfadaki kaynak listesi; hiçbir biçim bulunamazsa ``None``.

    Boş liste ile ``None`` ayrı: ``[]`` "bu bölümün kaynağı yok", ``None``
    "sayfa tanınmadı".
    """
    eslesme = _VIDEO_SOURCES.search(html or "")
    if eslesme:
        try:
            veri = json.loads(eslesme.group(1))
            if isinstance(veri, list):
                return [k for k in veri if isinstance(k, dict)]
        except ValueError:
            pass              # bozuk JSON: sekmelere düş
    sekmeler = _KAYNAK_SEKMESI.findall(html or "")
    if not sekmeler:
        return None
    out = []
    for oznitelikler in sekmeler:
        alanlar = {ad: unescape(deger) for ad, deger in _DATA_OZNITELIGI.findall(oznitelikler)}
        if alanlar.get("url"):
            # Sekmede "source" alanı yok; fansub olup olmadığına aşağıda tür
            # (mp4) ve konak (Anizium CDN'i değil) bakılarak karar veriliyor.
            out.append({"url": alanlar["url"], "label": alanlar.get("lbl", ""),
                        "key": alanlar.get("key", ""), "type": alanlar.get("type", "")})
    return out


def _fansub_girdisi_mi(kaynak: Dict[str, Any]) -> bool:
    # Yalnızca fansub (AnimeciX / tau-video) girdileri. "anizium" türündeki
    # girdiler ücretli abonelik servisi Anizium'un içeriğinin aktarımıdır; bu
    # proje ücretli bir servisi aşmaz, o yüzden onlar döndürülmez.
    kaynak_turu = str(kaynak.get("source") or "").lower()
    if kaynak_turu:
        return kaynak_turu == "animecix"
    return str(kaynak.get("type") or "").lower() == "mp4"      # sekme yedeği


def izleme_sayfasini_ayristir(html: str) -> List[Dict[str, Any]]:
    """İzleme sayfası → fansub akışları (site sırasıyla).

    Her akış ``{"url", "label", "type": "direct"|"hls", "referer", "fansub"?}``.

    Raises:
        ValueError: sayfada kaynak listesi yok (site değişmiş) ya da fansub
            adreslerinin hiçbiri çözülemedi.
    """
    ham = _ham_kaynaklar(html)
    if ham is None:
        raise ValueError("Animexe izleme sayfası çözümlenemedi: kaynak listesi "
                         "(VIDEO_SOURCES / src-tab) bulunamadı; site tasarımı değişmiş olabilir.")
    akislar: List[Dict[str, Any]] = []
    gorulen = set()
    aday = cozulemeyen = 0
    for kaynak in ham:
        if not _fansub_girdisi_mi(kaynak) or str(kaynak.get("type") or "").lower() == "embed":
            continue
        aday += 1
        try:
            url = vekili_coz(str(kaynak.get("url") or ""))
        except ValueError:
            cozulemeyen += 1
            continue
        parca = urlsplit(url)
        konak = (parca.hostname or "").lower()
        if parca.scheme not in ("http", "https") or not konak:
            cozulemeyen += 1
            continue
        if _ANIZIUM_KONAGI.search(konak) or _EMBED_KONAGI.search(konak) or url in gorulen:
            continue
        gorulen.add(url)
        # Etiket "Grup (720p)" biçiminde: `best_video` çözünürlüğü etiketten
        # okuyor, fansub seçimi de grup adından. İkisi ayrı alanlara bölünüyor.
        etiket = _metin(str(kaynak.get("label") or ""))
        eslesme = _COZUNURLUK.search(etiket)
        kalite = str(kaynak.get("quality") or "")
        cozunurluk = eslesme.group(1) if eslesme else (kalite if _KALITE.fullmatch(kalite) else "")
        fansub = _COZUNURLUK.sub("", etiket).strip()
        # Adı bilinmeyen grupları site sayısal kimliğiyle yazıyor ("7 (480p)");
        # "7" diye bir fansub göstermek kullanıcıya bir şey söylemez.
        if fansub.isdigit():
            fansub = ""
        if fansub and cozunurluk:
            etiket = f"{fansub} ({cozunurluk})"
        else:
            etiket = fansub or cozunurluk or "MP4"
        akis: Dict[str, Any] = {
            "url": url,
            "label": etiket,
            "type": "hls" if parca.path.endswith(".m3u8") else "direct",
            "referer": AKIS_REFERER,
        }
        if fansub:
            akis["fansub"] = fansub
        akislar.append(akis)
    if aday and cozulemeyen == aday:
        raise ValueError("Animexe akış adreslerinin hiçbiri çözülemedi "
                         "(vekil biçimi değişmiş olabilir).")
    return akislar


def _bolum_kimligi_coz(episode_id: str) -> Tuple[str, int, int]:
    eslesme = _BOLUM_ID.fullmatch((episode_id or "").strip().strip("/"))
    if not eslesme:
        raise ValueError(f"Geçersiz Animexe bölüm kimliği: {episode_id!r} "
                         "(beklenen: 'slug/sezon/bölüm')")
    slug, sezon, bolum = eslesme.groups()
    return slug, int(sezon), int(bolum)


def _akis_yokla(akis: Dict[str, Any]) -> Tuple[bool, str]:
    """Uç gerçekten video veriyor mu? 2 baytlık Range isteğiyle bak.

    Neden: tau-video CDN'lerinin yaklaşık yarısı ölü ve her biri oynatıcıda
    (best_video → yt-dlp) ~11 sn zaman aşımına mal oluyor. Ağ hatası/zaman
    aşımı da ölü sayılıyor: ölü konaklar tam olarak böyle görünüyor.
    ``stream=True`` şart: Range'i yok sayan bir sunucu bütün dosyayı yollar.
    """
    oturum = None
    try:
        oturum = _yeni_oturum()
        yanit = oturum.get(akis["url"], headers={"Range": "bytes=0-1",
                                                "Referer": akis.get("referer") or AKIS_REFERER},
                           timeout=YOKLAMA_TIMEOUT, stream=True)
        try:
            kod = yanit.status_code
            tur = str(yanit.headers.get("Content-Type") or "").lower()
        finally:
            try:
                yanit.close()
            except Exception:
                pass
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        if oturum is not None:
            try:
                oturum.close()
            except Exception:
                pass
    if kod not in (200, 206):
        return False, f"HTTP {kod}"
    if tur.startswith(("text/", "application/json")):
        return False, tur.split(";")[0]      # 200 ile dönen hata sayfası
    return True, ""


def canli_akislar(akislar: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Yanıt veren akışlar (sıra korunur); hiçbiri yoksa ``ConnectionError``."""
    if not akislar:
        return []
    with ThreadPoolExecutor(max_workers=min(YOKLAMA_ISCI, len(akislar))) as havuz:
        sonuclar = list(havuz.map(_akis_yokla, akislar))
    canli = [a for a, (tamam, _) in zip(akislar, sonuclar) if tamam]
    if canli:
        return canli
    olu = ", ".join(sorted({f"{urlsplit(a['url']).hostname} ({sebep})"
                            for a, (_, sebep) in zip(akislar, sonuclar)}))
    raise ConnectionError(
        f"Animexe: bu bölümün {len(akislar)} fansub akışının hiçbiri yanıt vermiyor "
        f"(ulaşılamayan CDN: {olu}). Başka bir kaynak deneyin.")


def get_episode_streams(episode_id: str, yokla: bool = True) -> List[Dict[str, Any]]:
    """Bölümün fansub akışları.

    ``[{"url", "label", "type", "referer", "fansub"?}, ...]``; URL'ler CDN'in
    kendisi (vekil değil), yt-dlp/mpv doğrudan oynatır. Bölümde fansub akışı
    yoksa boş liste. ``yokla=False`` CDN yoklamasını atlar (testler, tarama).

    Raises:
        ValueError: kimlik geçersiz ya da sayfa çözümlenemedi.
        LookupError: sitede böyle bir bölüm yok (HTTP 404).
        ConnectionError: engel/ağ hatası ya da akışların hiçbiri yanıt vermiyor.
    """
    slug, sezon, bolum = _bolum_kimligi_coz(episode_id)
    yanit = _getir(f"/watch/{slug}/{sezon}/{bolum}")
    if yanit is None:
        raise LookupError(f"Animexe'de bu bölüm yok (HTTP 404): {slug} {sezon}. sezon {bolum}. bölüm")
    akislar = izleme_sayfasini_ayristir(yanit.text)
    return canli_akislar(akislar) if yokla else akislar


__all__ = [
    "BASE_URL", "search_animexe", "search_animexe_zengin", "get_anime_episodes",
    "get_episode_streams", "vekili_coz",
]
