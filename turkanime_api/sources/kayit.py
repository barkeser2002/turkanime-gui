"""Kaynak kaydı — uygulamanın bildiği bütün anime kaynaklarının TEK listesi.

NEDEN VAR: Kaynak listesi eskiden altı ayrı yerde elle tutuluyordu:

    sources/__init__.py              PROVIDERS
    common/adapters.py               SearchEngine.adapters
    gui/qt/sources_bridge.py         FUNCTION_SOURCES / BUILDERS
    gui/qt/pages/episodes.py         SOURCE_COLORS / SOURCE_SHORT
    cli/__main__.py                  SOURCE_TITLES + "Kaynak seç" menüsü
    turkanime_server/crawler/kaynaklar.py   KAYNAKLAR

Her yeni kaynakta birini unutmak kolaydı ve oldu: OpenAnime/Tranimaci uzun
süre aramada hiç görünmedi (köprüde vardı, SearchEngine'de yoktu), AnimeDepo
CLI menüsüne eklenmediği için seçilemiyordu. Artık hepsi buradan türetiliyor;
yeni kaynak eklemek = kaynak modülü (`sources/<modul>.py`, üç uç) + aşağıdaki
`KAYNAKLAR` demetine tek bir `Kaynak(...)` satırı. Ayrıntı:
`ANIME_PROVIDER_GUIDE.md`.

Üç uç sözleşmesi (sunucu tarayıcısının kullandığıyla aynı):
    ara(sorgu, limit=...)  -> [(kaynak_id, başlık), ...]
    bolumler(kaynak_id)    -> [(bolum_id, başlık), ...]
    akislar(bolum_id)      -> [{"url", "label", "type"?, "referer"?, "fansub"?}, ...]

TÜRKANİME = ARŞİV: turkanime.tv kapandı (görselleri bile 503 dönüyor). Sitenin
anime/bölüm/video kayıtları AnimeDepo'nun statik JSON arşivinde yaşıyor
(`sources/animedepo.py`, depoda `arsiv/`). "TürkAnime" kaynağı artık o arşiv;
aynı arşiv bir süre "AnimeDepo" adıyla AYRI bir kaynak olarak da listelendi.
İki kez görünmesin (aynı sonuçlar iki rozetle, "Tüm kaynaklar"da aynı bölümler
iki kez) diye tek kayıtta birleşti. "AnimeDepo" eski ad olarak okunmaya devam
ediyor: kayıtlı eşleşmeler, `ayarlar.json`'daki "kaynak" değeri ve köprüye
eski adla gelen istekler yeni kayda düşer (bkz. `bul`).

BU MODÜL BİLEREK HAFİF: modül düzeyinde hiçbir kaynak import edilmiyor;
yükleyiciler tembel. Sunucu tarayıcısı da tablosunu buradan türetiyor ve
imajında yt-dlp yok (tests/test_sunucu_bagimliliklari.py,
tests/test_server_dagitim.py). Bu yüzden `sources.adapter` (→ yt_dlp) buradan
fonksiyon içinde bile import EDİLMEZ; bölüm nesnesi kurmak
`sources.adapter.kayittan_bolumler`'in işi.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple

Arama = Callable[..., List[Tuple[str, str]]]
ZenginArama = Callable[..., List[Dict[str, Any]]]
Bolumler = Callable[[str], List[Tuple[str, str]]]
Akislar = Callable[[str], List[Dict[str, Any]]]


@dataclass(frozen=True)
class KaynakUclari:
    """Bir kaynağın uçları.

    Sunucu tarayıcısının (`turkanime_server/crawler/kaynaklar.py`) kullandığı
    sınıfın ta kendisi — ilk üç alan konumsal olarak aynı sırada, orada
    ``KaynakUclari(ara, bolumler, akislar)`` diye kuruluyor.

    ``zengin_ara`` isteğe bağlı: kapak görseli verebilen kaynaklar (AniList)
    ``[{"slug", "title", "image"}, ...]`` döndürür; vermeyenlerde arama motoru
    ``ara``'nın çiftlerini ``image=None`` ile sarar.
    """

    ara: Arama
    bolumler: Optional[Bolumler] = None
    akislar: Optional[Akislar] = None
    zengin_ara: Optional[ZenginArama] = None


def _aynen(bolum_id: str) -> str:
    """Bölüm kimliği zaten bölümün adresi/kimliği (çoğu kaynak)."""
    return bolum_id


@dataclass(frozen=True)
class Kaynak:
    """Kayıt satırı: bir kaynağın kimliği, görünüşü, uçları ve bayrakları."""

    # Kanonik anahtar. SearchEngine sonuç sözlüğünün, köprünün, bölüm listesi
    # rozetinin ve API'ye kaydedilen eşleşmenin kullandığı ad. DEĞİŞTİRME:
    # kullanıcıların kayıtlı eşleşmeleri bu adla duruyor.
    ad: str
    # İnsana gösterilen ad ("TürkAnime (arşiv)"). Arayüz kartında, detay
    # sayfasının kaynak kutusunda ve CLI menüsünde bu görünür.
    etiket: str
    kisaltma: str                       # bölüm satırındaki iki harfli rozet
    renk: str                           # rozet rengi
    # AdapterBolum'un ilerleme etiketi ve tarayıcının arşive yazdığı "player".
    oynatici: str
    yukleyici: Callable[[], KaynakUclari]
    # `turkanime_api.sources` altındaki modül adı. PROVIDERS ve sunucu
    # tarayıcısı kaynakları bu adla anahtarlıyor ("tranime", "openani").
    modul: str = ""
    # `ayarlar.json` → "kaynak" değeri. None: CLI menüsünde yok (AniList).
    cli_kodu: Optional[str] = None
    # Eski adlar: okunurken kanonik kayda düşer, hiçbir listede görünmez.
    takma_adlar: Tuple[str, ...] = ()
    # Bölüm kimliği → AdapterBolum.url (bkz. `sources.adapter`).
    bolum_adresi: Callable[[str], str] = _aynen
    # Bölüm kimliği → izleme geçmişinde/dosya adında kullanılacak bölüm slug'ı.
    # None: slug başlıktan üretilir (AdapterBolum'un eski davranışı).
    bolum_slugu: Optional[Callable[[str], str]] = None
    # Kaynak kimliği bu kaynakta açılamıyorsa kullanıcıya gösterilecek mesaj.
    kimlik_hatasi: Optional[Callable[[str], Optional[str]]] = None
    # Yalnızca arama/metadata (AniList): bölüm/akış yok, oynatma yok.
    yalnizca_metadata: bool = False
    # Oturum çerezi olmadan sonuç vermiyor (TRAnimeİzle: bot kontrolü).
    cerez_gerekir: bool = False
    # Sunucu tarayıcısı gezsin mi? Arşiv kaynağı gezilmez: tarayıcının
    # ÜRETTİĞİ şema o, taramak kendi çıktımızı geri okumak olur.
    taranabilir: bool = False
    # Açılışta bir kez çağrılacak hazırlık (CLI). Kaynak kullanılamıyorsa hata
    # fırlatır; çağıran uyarır ama menüyü yine açar. TürkAnime: arşiv dizinini
    # yükler, böylece ilk arama beklemez ve arşiv hiç yoksa hemen bilinir.
    hazirlik: Optional[Callable[[], Any]] = None
    # CLI menüsünde "(deneysel)" notu.
    deneysel: bool = False
    # `sources.<modul>` içindeki eski adaptör sınıfı (PROVIDERS["adapter"]).
    adaptor_sinifi: Optional[str] = None
    # Akış listesi BOŞ geldiğinde kullanıcıya söylenecek sebep (bkz.
    # `akis_saglayici`). Yalnızca boş listenin anlamı KESİN olan kaynakta
    # dolu: arşiv okunduysa ve bölümün oynatılabilir kaydı yoksa bu bir
    # "hata" değil gerçek. Canlı sitelerde boş liste "site bozuldu" da
    # olabilir; orada eski "çalışan video bulunamadı" kalır.
    bos_akis_mesaji: str = ""

    # ── Uçlar ───────────────────────────────────────────────────────────────
    def uclar(self) -> KaynakUclari:
        """Kaynağın uçlarını yükle (tembel import; önbelleklenmez).

        Önbellek yok: yükleyici her çağrıda modül ÖZNİTELİĞİNİ okuyor, böylece
        `monkeypatch.setattr(animedepo, "search_animedepo", ...)` gibi
        sahtelemeler de, çalışma anında yeniden yüklenen modül de görünür.
        Maliyeti bir `sys.modules` bakışı.
        """
        return self.yukleyici()

    def ara(self, sorgu: str, limit: int = 10) -> List[Tuple[str, str]]:
        """``[(kaynak_id, başlık), ...]``; en çok ``limit`` kayıt."""
        return list(self.uclar().ara(sorgu, limit=limit) or [])[:limit]

    @property
    def oynatilabilir(self) -> bool:
        return not self.yalnizca_metadata

    @property
    def cli_etiketi(self) -> str:
        return f"{self.etiket} (deneysel)" if self.deneysel else self.etiket

    def kimlik_denetle(self, kaynak_id: str) -> Optional[str]:
        """Kimlik bu kaynakta açılamıyorsa mesaj, açılabiliyorsa None."""
        return self.kimlik_hatasi(kaynak_id) if self.kimlik_hatasi else None


# ── Yükleyiciler (tembel) ───────────────────────────────────────────────────
def _turkanime_arsivi() -> KaynakUclari:
    # turkanime.tv kapandı; kayıtları statik arşivden okunuyor. Arşivdeki
    # "Resim" adresleri ölü site olduğu için arama görsel döndürmüyor.
    from . import animedepo
    return KaynakUclari(animedepo.search_animedepo, animedepo.get_anime_episodes,
                        animedepo.get_episode_streams)


def _arsiv_hazirligi() -> Dict[str, Any]:
    """Arşiv dizinini yükle; hiçbir yerden okunamıyorsa hata.

    Yerel arşiv (depodaki `arsiv/`, indirilen `cevrimdisi_arsiv/`) varsa ağa
    çıkılmaz; yoksa uzak aynalar, en son disk önbelleği denenir. Okunamazsa
    `animedepo.ArsivOkunamadi` sebebiyle yükselir; okunduğu hâlde boşsa da
    hata (arşivde tek anime yoksa kaynak kullanılamaz).
    """
    from . import animedepo
    veri = animedepo.dizin_ya_da_hata()
    if not (veri or {}).get("index"):
        raise ConnectionError("TürkAnime arşivi boş: dizin.json'da hiç anime yok.")
    return veri


def _anilist() -> KaynakUclari:
    from ..anilist_client import anilist_client

    def zengin_ara(sorgu: str, limit: int = 10) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for kayit in (anilist_client.search_anime(sorgu, per_page=limit) or [])[:limit]:
            basliklar = kayit.get("title") or {}
            kapak = kayit.get("coverImage") or {}
            out.append({
                "slug": str(kayit.get("id", "")),
                "title": basliklar.get("romaji") or basliklar.get("english") or "",
                "image": kapak.get("medium") or kapak.get("large"),
            })
        return out

    def ara(sorgu: str, limit: int = 10) -> List[Tuple[str, str]]:
        return [(k["slug"], k["title"]) for k in zengin_ara(sorgu, limit)]

    return KaynakUclari(ara, zengin_ara=zengin_ara)


def _animecix() -> KaynakUclari:
    from .animecix import CixAnime, _video_streams, search_animecix

    def ara(sorgu: str, limit: int = 20) -> List[Tuple[str, str]]:
        # AnimeciX ucu `limit` almıyor (sunucuya zaten limit=20 gidiyor).
        return (search_animecix(sorgu) or [])[:limit]

    def bolumler(anime_id: str) -> List[Tuple[str, str]]:
        # Bölüm kimliği olarak embed yolunu taşıyoruz: `_video_streams` zaten
        # onu bekliyor, ayrıca ikinci bir çözümleme isteği gerekmiyor.
        return [(e.url, e.title) for e in CixAnime(id=str(anime_id), title="").episodes
                if e.url]

    return KaynakUclari(ara, bolumler, _video_streams)


def _animecix_kimlik_hatasi(kaynak_id: str) -> Optional[str]:
    # AnimeciX araması sayısal kimlik döndürür. Eskiden sayı olmayan kimlik için
    # `hash(slug) % 1000000` üretiliyordu: PYTHONHASHSEED yüzünden her açılışta
    # FARKLI, üstelik tamamen alakasız bir anime. Sessizce yanlış bölüm listesi
    # göstermektense açıkça söylüyoruz.
    try:
        int(str(kaynak_id).strip())
        return None
    except (TypeError, ValueError):
        return (f"AnimeciX sayısal kimlik bekliyor, '{kaynak_id}' geçersiz. "
                "Bu sonucu AnimeciX üzerinden açamıyoruz; başka bir kaynak seçin.")


def _anizle() -> KaynakUclari:
    from .anizle import get_anime_episodes, get_episode_streams, search_anizle
    return KaynakUclari(search_anizle, get_anime_episodes, get_episode_streams)


def _tranime() -> KaynakUclari:
    from .tranime import get_anime_episodes, get_episode_details, search_tranime

    def bolumler(slug: str) -> List[Tuple[str, str]]:
        return [(e.slug, e.title) for e in (get_anime_episodes(slug) or [])]

    def ara(sorgu: str, limit: int = 10) -> List[Tuple[str, str]]:
        sonuc = search_tranime(sorgu, limit)
        if not sonuc and not _tranime_cerezi_var():
            # Çerezsiz yalnızca harf listesinde bulanık arama yapılabiliyor;
            # "0 sonuç" demek yanlış olur, arama sayfası sebebi göstersin.
            from ..common.hatalar import OturumGerekli
            raise OturumGerekli(
                "TRAnimeİzle çerez istiyor: çerezsiz tam arama yapılamıyor. "
                "Ayarlar > Kaynaklar'dan TRAnimeİzle çerezini girin.")
        return sonuc

    def akislar(ep_slug: str) -> List[Dict[str, Any]]:
        detay = get_episode_details(ep_slug)
        if not detay:
            # `get_episode_details` bot kontrolünü (ve ağ hatasını) konsola
            # basıp None dönüyor; boş liste "video yok" diye raporlanıyordu.
            # Kaynak modülüne dokunmadan (kaynak işi durduruldu) sebep burada
            # çerezin varlığından çıkarılıyor.
            from ..common.hatalar import KaynakHatasi, OturumGerekli
            if not _tranime_cerezi_var():
                raise OturumGerekli(
                    "TRAnimeİzle çerez gerekli: bölüm sayfası bot kontrolüne "
                    "takıldı. Ayarlar > Kaynaklar'dan TRAnimeİzle çerezini girin.")
            raise KaynakHatasi(
                "TRAnimeİzle bölüm sayfası okunamadı: çerezin süresi dolmuş, "
                "bot kontrolü ya da ağ hatası olabilir. Ayarlar'dan çerezi "
                "yenileyip yeniden deneyin (ayrıntı konsolda).")
        out: List[Dict[str, Any]] = []
        for s in detay.get_sources():
            iframe = s.get_iframe()
            if iframe:
                out.append({"url": iframe, "label": s.name, "type": "iframe"})
        return out

    return KaynakUclari(ara, bolumler, akislar)


def _tranime_cerezi_var() -> bool:
    """TRAnimeİzle oturum çerezi süreçte yüklü mü (`kimlikler` basıyor)."""
    from . import tranime
    return bool(getattr(tranime, "SESSION_COOKIE", None))


def _openani() -> KaynakUclari:
    from .openani import get_anime_episodes, get_episode_streams, search_openani
    return KaynakUclari(search_openani, get_anime_episodes, get_episode_streams)


def _tranimaci() -> KaynakUclari:
    from .tranimaci import get_anime_episodes, get_episode_streams, search_tranimaci
    return KaynakUclari(search_tranimaci, get_anime_episodes, get_episode_streams)


def _arsiv_bolum_slugu(bolum_id: str) -> str:
    """"anime_slug/bolum_slug" → "bolum_slug" (turkanime.tv'nin kendi slug'ı).

    Kapanan sitenin `objects.Bolum.slug`'ı tam olarak buydu. İzleme geçmişi
    (`gecmis.json`) ve indirilen dosya adları bu slug'la anahtarlı; aynısını
    kullanmak eski TürkAnime kullanıcısının "izlendi" işaretlerini ve
    indirdiği dosyaların adlarını korur.
    """
    return str(bolum_id).rsplit("/", 1)[-1]


def _openani_adresi(bolum_id: str) -> str:
    return f"https://openani.me/anime/{bolum_id}"


def _tranimaci_adresi(bolum_id: str) -> str:
    return f"https://tranimaci.com/video/{bolum_id}"


# ── Tablo ───────────────────────────────────────────────────────────────────
# Sıra önemli: arama sonuçları, CLI menüsü ve PROVIDERS önceliği bu sırayı
# izler. TürkAnime en başta: CLI'ın varsayılanı ve ağsız çalışan tek kaynak.
KAYNAKLAR: Tuple[Kaynak, ...] = (
    Kaynak("AniList", "AniList", "AL", "#02a9ff", "ANILIST", _anilist,
           yalnizca_metadata=True),
    Kaynak("TürkAnime", "TürkAnime (arşiv)", "TA", "#ffd93d", "ANIMEDEPO",
           _turkanime_arsivi, modul="animedepo", cli_kodu="turkanime",
           takma_adlar=("AnimeDepo",), bolum_slugu=_arsiv_bolum_slugu,
           hazirlik=_arsiv_hazirligi,
           bos_akis_mesaji="arşivde bu bölüm için oynatılabilir kayıt yok; "
                           "başka bir kaynak deneyin"),
    Kaynak("AnimeciX", "AnimeciX", "CX", "#ff6b6b", "ANIMECIX", _animecix,
           modul="animecix", cli_kodu="animecix",
           kimlik_hatasi=_animecix_kimlik_hatasi, taranabilir=True, deneysel=True),
    Kaynak("Anizle", "Anizle", "AZ", "#9b59b6", "ANIZLE", _anizle,
           modul="anizle", cli_kodu="anizle", taranabilir=True, deneysel=True),
    Kaynak("TRAnimeİzle", "TRAnimeİzle", "TR", "#e84393", "TRANIME", _tranime,
           modul="tranime", cli_kodu="tranimeizle", cerez_gerekir=True,
           taranabilir=True),
    Kaynak("OpenAnime", "OpenAnime", "OA", "#4ecdc4", "OPENANI", _openani,
           modul="openani", cli_kodu="openanime", bolum_adresi=_openani_adresi,
           taranabilir=True, adaptor_sinifi="OpenAniAdapter"),
    Kaynak("Tranimaci", "Tranimaci", "TC", "#0984e3", "TRANIMACI", _tranimaci,
           modul="tranimaci", cli_kodu="tranimaci", bolum_adresi=_tranimaci_adresi,
           taranabilir=True),
)

# CLI'ın ve eski ayarların varsayılanı (`cli/dosyalar.py`: "kaynak": "turkanime").
VARSAYILAN = "TürkAnime"


# ── Ad çözümleme ────────────────────────────────────────────────────────────
_PARANTEZ = re.compile(r"\([^)]*\)")


def _anahtar(deger: Any) -> str:
    """Karşılaştırma anahtarı: aksansız, küçük harf, parantezsiz, yalnız harf/rakam.

    "TürkAnime (arşiv)", "turkanime", "TURKANIME" → "turkanime";
    "TRAnimeİzle" → "tranimeizle" ("İ" casefold'da i + birleşik nokta olur,
    NFKD ile ayrılıp atılıyor); "AnimeciX (deneysel)" → "animecix".
    Böylece CLI'ın eski ayar değerleri ve menü başlıkları da çözülüyor.
    """
    metin = _PARANTEZ.sub(" ", str(deger or ""))
    metin = unicodedata.normalize("NFKD", metin)
    metin = "".join(c for c in metin if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", metin.casefold())


_indeks_kilidi = Lock()
_indeks: Tuple[Optional[Tuple[Kaynak, ...]], Dict[str, Kaynak]] = (None, {})


def _indeks_kur(kaynaklar: Tuple[Kaynak, ...]) -> Dict[str, Kaynak]:
    """Her adı (kanonik, etiket, CLI kodu, modül, takma adlar) kayda bağla.

    İki kaynak aynı adı iddia ederse ÇÖZÜMLEME BELİRSİZ olur (ayar hangisini
    kastediyor?); sessizce birini seçmek yerine hemen hata verilir.
    """
    indeks: Dict[str, Kaynak] = {}
    for kaynak in kaynaklar:
        adlar = {kaynak.ad, kaynak.etiket, kaynak.modul, kaynak.cli_kodu or "",
                 *kaynak.takma_adlar}
        for ad in adlar:
            anahtar = _anahtar(ad)
            if not anahtar:
                continue
            onceki = indeks.get(anahtar)
            if onceki is not None and onceki.ad != kaynak.ad:
                raise ValueError(
                    f"kaynak adı çakışıyor: {ad!r} hem {onceki.ad} hem {kaynak.ad}")
            indeks[anahtar] = kaynak
    return indeks


def _guncel_indeks() -> Dict[str, Kaynak]:
    """`KAYNAKLAR` değiştiyse (kaydet/test sahtelemesi) indeksi yeniden kur."""
    global _indeks
    kaynaklar = KAYNAKLAR
    tablo, indeks = _indeks
    if tablo is kaynaklar:
        return indeks
    with _indeks_kilidi:
        if _indeks[0] is not kaynaklar:
            _indeks = (kaynaklar, _indeks_kur(kaynaklar))
        return _indeks[1]


# Tablo IMPORT ANINDA doğrulanır: iki kaynak aynı adı (kanonik, etiket, CLI
# kodu, modül, takma ad) iddia ederse modül hiç yüklenmez (`ValueError`).
# Eskiden indeks ilk tam-olmayan `bul()`'da kuruluyordu; çakışma o ana kadar
# görünmüyor, bu arada `cli_kaynaklari()` aynı CLI kodunu iki kez listeliyordu.
# Ucuz ve saf: yalnızca dizgi normalizasyonu, hiçbir kaynak yüklenmez.
_indeks = (KAYNAKLAR, _indeks_kur(KAYNAKLAR))


def kaynaklar(*, metadata: bool = True) -> List[Kaynak]:
    """Kayıttaki kaynaklar, tablo sırasıyla. ``metadata=False``: yalnız oynatılabilir."""
    return [k for k in KAYNAKLAR if metadata or k.oynatilabilir]


def bul(ad: Any) -> Optional[Kaynak]:
    """Herhangi bir addan (kanonik, etiket, CLI kodu, modül, eski ad) kaydı bul.

    Tam eşleşme önce denenir; olmazsa `_anahtar` ile büyük/küçük harf,
    aksan ve "(arşiv)" gibi eklerden bağımsız eşleşme. Bilinmeyen ad → None.
    """
    if not ad:
        return None
    for kaynak in KAYNAKLAR:
        if kaynak.ad == ad:
            return kaynak
    return _guncel_indeks().get(_anahtar(ad))


def kanonik_ad(ad: str) -> str:
    """Kayıtlı bir adın kanonik hâli ("AnimeDepo" → "TürkAnime"); bilinmeyen ad aynen."""
    kaynak = bul(ad)
    return kaynak.ad if kaynak is not None else ad


def gorunen_ad(ad: str) -> str:
    """Arayüzde gösterilecek ad: kanonik anahtar ise etiketi, değilse adın kendisi.

    Yalnızca KANONİK anahtar etikete çevrilir. Takma ad ya da bilinmeyen bir ad
    olduğu gibi gösterilir: o dizgeyi anahtar olarak kullanan kod (bağlanmış
    eşleşmeler, sahte kaynaklar) ekranda da aynı adı görmeli; aksi hâlde iki
    farklı anahtar aynı etiketle görünür ve hangisinin seçildiği anlaşılmaz.
    """
    for kaynak in KAYNAKLAR:
        if kaynak.ad == ad:
            return kaynak.etiket
    return ad


def cli_kaynagi(deger: Any) -> Kaynak:
    """`ayarlar.json` → "kaynak" değerinden CLI kaynağı; tanınmazsa varsayılan.

    "animedepo" (eski ayrı arşiv kaynağı) TürkAnime'ye düşer. Tanınmayan ya da
    CLI'da olmayan (AniList) değer varsayılana döner: CLI'ın eski
    `_norm_source`'u da bilinmeyen her şeyi TürkAnime sayıyordu.
    """
    kaynak = bul(deger)
    if kaynak is None or not kaynak.cli_kodu:
        kaynak = bul(VARSAYILAN)
    assert kaynak is not None, "varsayılan kaynak kayıtta yok"
    return kaynak


def cli_kaynaklari() -> List[Kaynak]:
    """CLI menüsündeki kaynaklar (tablo sırasıyla)."""
    return [k for k in KAYNAKLAR if k.cli_kodu and k.oynatilabilir]


def tarayici_kaynaklari() -> List[Kaynak]:
    """Sunucu tarayıcısının gezdiği kaynaklar."""
    return [k for k in KAYNAKLAR if k.taranabilir and k.oynatilabilir]


def kaydet(kaynak: Kaynak) -> None:
    """Çalışma anında kaynak ekle (eklenti/deneme). Aynı adlı kayıt değiştirilir.

    Kalıcı kaynaklar tabloya yazılmalı; bu yalnızca modül düzeyinde türetilmiş
    sabitleri (SOURCE_TITLES gibi) değil, çağrı anında kaydı okuyan yolları
    (arama motoru, köprü, CLI menüsü) etkiler.
    """
    global KAYNAKLAR
    yeni = tuple(k for k in KAYNAKLAR if k.ad != kaynak.ad) + (kaynak,)
    _indeks_kur(yeni)                 # çakışma varsa kayda girmeden hata ver
    KAYNAKLAR = yeni


def akis_saglayici(akislar: Akislar, bolum_id: str, etiket: str = "",
                   bos_mesaji: str = "") -> Callable[[str], List[Dict[str, Any]]]:
    """Bölüm kimliğini kapatan akış sağlayıcı; hata SEBEBİYLE yükselir.

    `AdapterBolum` sağlayıcıyı kendi `url`'siyle çağırıyor; bazı kaynaklarda
    url ile kimlik farklı (OpenAnime: tam adres ↔ "anime/bolum"), bu yüzden
    kimlik burada kapatılıp url yok sayılıyor.

    ESKİDEN arşiv dışındaki her hata boş listeye çevriliyordu: `best_video`
    boş listeyi "hiçbiri çalışmıyor" diye raporluyor, kullanıcı süresi dolmuş
    çerezle, Cloudflare engeliyle ve zaman aşımıyla aynı "çalışan video
    bulunamadı"yı görüyordu. Artık:

    * `common.hatalar.KaynakHatasi` ailesi (arşivin `ArsivOkunamadi`'sı dahil)
      olduğu gibi geçer — mesajı kullanıcıya yazılmış Türkçe cümle.
    * Başka her hata sınıflandırılıp ``etiket``'li bir `KaynakHatasi`'na
      çevrilir ("AnimeciX: video listesi alınamadı — zaman aşımı: …"); asıl
      istisna ``__cause__``'da kalır.
    * Liste boşsa ve ``bos_mesaji`` verildiyse (`Kaynak.bos_akis_mesaji`:
      yalnızca arşiv) `VideoYok` yükselir; verilmediyse boş liste döner.

    Bölüm LİSTESİ bundan etkilenmez: listeyi kurarken akış istenmiyor, ve
    `AdapterBolum.fansubs` sağlayıcı hatasını kendisi yutuyor.

    Import fonksiyon içinde: `common.hatalar` yalnızca standart kütüphane,
    ama bu modül bilerek hafif (sunucu imajı; bkz. modül başlığı) ve import
    anında hiçbir şey çekmiyor.
    """
    def saglayici(_url: str) -> List[Dict[str, Any]]:
        from ..common.hatalar import KaynakHatasi, VideoYok, kaynak_hatasi
        try:
            sonuc = akislar(bolum_id) or []
        except KaynakHatasi:
            raise
        except Exception as hata:
            raise kaynak_hatasi(hata, etiket) from hata
        if not sonuc and bos_mesaji:
            raise VideoYok(f"{etiket}: {bos_mesaji}." if etiket else f"{bos_mesaji}.")
        return sonuc
    return saglayici


__all__ = [
    "Kaynak", "KaynakUclari", "KAYNAKLAR", "VARSAYILAN",
    "kaynaklar", "bul", "kanonik_ad", "gorunen_ad", "cli_kaynagi",
    "cli_kaynaklari", "tarayici_kaynaklari", "kaydet", "akis_saglayici",
]
