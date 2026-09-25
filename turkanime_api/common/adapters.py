"""
Anime source adapters for the UI components.
Provides unified interface for searching anime across different sources.

Kaynak listesi burada TUTULMUYOR: `SearchEngine.adapters` her örneklemede
`sources/kayit.py`'deki kayıttan kurulur (bkz. `KaynakAdaptoru`). Eskiden
burada kaynak başına bir adaptör sınıfı vardı ve liste köprü/CLI'daki
kopyalarından ayrışıyordu; "TürkAnime" adaptörü de kapanan turkanime.tv'nin
arama ucuna (`objects.Anime.arama_yap`) gidiyordu. TürkAnime artık sitenin
statik arşivinde, ağsız aranıyor (`sources/animedepo.py`).
"""

from typing import Callable, Iterable, List, Tuple, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError

# Tüm kaynakların toplam bekleme süresi. Tarayıcı destekli kaynaklar (Tranimaci)
# ilk çağrıda yavaş olabildiği için 12 sn yetmiyordu. Bu süre GERÇEK bir üst
# sınır: dolduğunda arama elindeki sonuçlarla döner (bkz. `_paralel_ara`).
OVERALL_SEARCH_TIMEOUT = 25
from ..sources import kayit
from .title_match import siralama_skoru


def _alakaya_gore_sirala(sorgu: str, kayitlar: list, baslik) -> list:
    """Kaynağın döndürdüğü sırayı alakaya göre yeniden diz.

    Kaynakların çoğu kendi iç sırasını veriyor ve bu sıra sorguyla ilgisiz
    olabiliyor: "one piece" araması AnimeciX'te "ONE PIECE (Live Action)",
    TürkAnime'de "Koisuru One Piece", Tranimaci'de "One Piece Fan Letter" ile
    başlıyordu — gerçek One Piece TürkAnime'de 4. sıradaydı. Bölüm çekme yolu
    ilk sonucu aldığı için bu doğrudan "yanlış animenin bölümleri" demekti.

    Sıralama **kararlı**: eşit skorlu kayıtlar kaynağın kendi sırasını korur,
    yani kaynağın popülerlik bilgisi boşa gitmez.
    """
    if not kayitlar:
        return kayitlar
    try:
        return sorted(kayitlar, key=lambda k: -siralama_skoru(sorgu, baslik(k)))
    except Exception:
        return kayitlar          # skorlama asla aramayı düşürmesin


class AramaSonuclari(dict):
    """`search_all_sources*`'ın dönüşü: kaynak adı → kayıtlar, artı ``hatalar``.

    ``hatalar``: arama sırasında HATA veren kaynaklar → hata metni. "0 sonuç"
    ile "aranamadı" ayrı şeyler: eskiden hata veren kaynak da boş liste
    döndürüyordu ve arşiv okunamadığında (aynalar kapalı, önbellek boş)
    kullanıcı "sonuç bulunamadı" görüyordu. Arama sayfası bunu artık ayrıca
    söylüyor.

    Düz `dict` alt sınıfı: eski çağıranlar (detay sayfasının eşleştirmesi,
    testler) sonucu sözlük olarak okumaya devam eder.
    """

    def __init__(self, *args: Any, hatalar: Optional[Dict[str, str]] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.hatalar: Dict[str, str] = dict(hatalar or {})


def _hata_metni(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


class KaynakAdaptoru:
    """Kayıttaki bir kaynağı `SearchEngine`'in adaptör sözleşmesine uydurur.

    Sözleşme: ``search_anime(query, limit) -> [(slug, title), ...]``; kaynak
    kapak görseli verebiliyorsa (AniList) ek olarak ``search_rich`` — bkz.
    `ZenginKaynakAdaptoru`. Kaynak hatası burada YUTULMAZ: `SearchEngine`
    onu kaynak başına yakalıyor (tek bir kaynağın çökmesi paralel aramanın
    diğer sonuçlarını götürmez) ve `AramaSonuclari.hatalar`'a yazıyor.
    Eskiden burada boş listeye çevriliyordu; hata kimsenin göremeyeceği yerde
    kayboluyordu.
    """

    def __init__(self, kaynak: "kayit.Kaynak"):
        self.kaynak = kaynak

    def search_anime(self, query: str, limit: int = 10) -> List[Tuple[str, str]]:
        return self.kaynak.ara(query, limit=limit)


class ZenginKaynakAdaptoru(KaynakAdaptoru):
    """Kapak görseli verebilen kaynak (`KaynakUclari.zengin_ara`).

    Ayrı sınıf çünkü `search_all_sources_rich` `hasattr(adapter,
    "search_rich")` ile karar veriyor; görsel veremeyen kaynakta bu metodun
    VAR olması, sonuçları gereksiz yere başka bir yoldan geçirirdi.
    """

    def search_rich(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        uclar = self.kaynak.uclar()
        return list(uclar.zengin_ara(query, limit=limit) or [])[:limit]


def kaynak_adaptoru(kaynak: "kayit.Kaynak") -> KaynakAdaptoru:
    """Kaynağa uygun adaptör: görsel verebiliyorsa zengin, değilse sade.

    `zengin_ara` var mı diye bakmak uçları yüklemeyi (tembel import) gerektirir;
    yükleme patlarsa kaynak sade adaptörle kalır ve araması zaten `[]` döner.
    """
    try:
        zengin = kaynak.uclar().zengin_ara is not None
    except Exception:
        zengin = False
    return ZenginKaynakAdaptoru(kaynak) if zengin else KaynakAdaptoru(kaynak)


class SearchEngine:
    """Unified search engine for all anime sources.

    `adapters` kayıttaki (`sources/kayit.py`) her kaynak için bir adaptör;
    anahtarlar kaynakların kanonik adı, yani `gui/qt/sources_bridge.py` ile
    aynı adlar — ikisi aynı kayıttan türediği için artık ayrışamazlar
    (OpenAnime/Tranimaci uzun süre aramada hiç görünmüyordu). Eski
    "AnimeDepo" ayrı bir kaynak değil: aynı arşiv "TürkAnime" adıyla aranıyor,
    iki kez listelenmiyor. Testler `adapters`'ı sahte adaptörlerle
    değiştirebilir; sözleşme yalnızca `search_anime`/`search_rich`.
    """

    def __init__(self):
        self.adapters = {k.ad: kaynak_adaptoru(k) for k in kayit.kaynaklar()}

    def _paralel_ara(self, gorev: Callable[[str], Any],
                     timeout: Optional[float] = None,
                     bitince: Optional[Callable[[str, Any], Any]] = None,
                     hatalar: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """`gorev`'i her kaynak için paralel çalıştır, süre dolunca ELİNDEKİYLE dön.

        ``bitince(kaynak, sonuc)``: her kaynak bittiği AN (bu thread'de)
        çağrılır — arama sayfası yerel arşivin anlık sonuçlarını en yavaş
        kaynağı beklemeden gösterebilsin. ``hatalar`` verilirse süreye
        yetişemeyen kaynaklar oraya "zaman aşımı" olarak yazılır: eskiden
        ne sonuçta ne hatada görünüyorlardı, kullanıcı "0 sonuç" sanıyordu.

        `with ThreadPoolExecutor(...)` KULLANILMIYOR: bağlam çıkışında
        `shutdown(wait=True)` çalışır ve hâlâ süren işleri bekler. Yani toplam
        zaman aşımı hiçbir şeyi sınırlamıyordu — konsola "zaman aşımı" yazılıyor
        ama arama, en yavaş kaynak (45 sn) bitene kadar dönmüyordu. Arayüz
        zamanında yanıt versin diye havuz `wait=False` ile bırakılıyor;
        yetişemeyen iş arka planda sessizce ölür, sonucu kimse okumaz.
        """
        # Sabit çağrı anında okunur (varsayılan argümanda değil): süre sınırını
        # sahteleyen testler modül sabitini değiştirebilsin diye.
        if timeout is None:
            timeout = OVERALL_SEARCH_TIMEOUT
        sonuc: Dict[str, Any] = {}
        if not self.adapters:
            # Boş kaynak kümesi (bkz. `arama_motoru`): `max_workers=0` hata verir.
            return sonuc
        havuz = ThreadPoolExecutor(max_workers=len(self.adapters))
        try:
            futures = {havuz.submit(gorev, name): name for name in self.adapters}
            # DİKKAT: `as_completed(..., timeout=)` süre dolunca KENDİSİ fırlatır
            # ve bu, aşağıdaki try/except'in DIŞINDADIR. Sarmalanmazsa tek bir
            # yavaş kaynak tüm aramayı çökertir (toplanan sonuçlar da kaybolur).
            try:
                for future in as_completed(futures, timeout=timeout):
                    name = futures[future]
                    try:
                        # Future zaten tamamlandı; `result()` beklemez.
                        _, source_results = future.result()
                        sonuc[name] = source_results
                    except Exception as exc:
                        print(f"{name} arama hatası (timeout/exception): {exc}")
                        sonuc[name] = []
                    if bitince is not None:
                        try:
                            bitince(name, sonuc[name])
                        except Exception as exc:   # alıcı aramayı düşürmesin
                            print(f"[Arama] {name} sonucu iletilemedi: {exc}")
            except FuturesTimeoutError:
                print("[Arama] Bazı kaynaklar zaman aşımına uğradı, "
                      "mevcut sonuçlar döndürülüyor.")
        finally:
            # Başlamamış işler iptal, sürenler beklenmez (bkz. yukarıdaki not).
            havuz.shutdown(wait=False, cancel_futures=True)

        for name in self.adapters:          # yetişemeyenler boş
            if name not in sonuc and hatalar is not None:
                hatalar.setdefault(name, f"zaman aşımı ({timeout:g} sn)")
            sonuc.setdefault(name, [])
        return sonuc

    def search_all_sources(self, query: str, limit_per_source: int = 10) -> Dict[str, List[Tuple[str, str]]]:
        """Search anime across all sources in parallel.

        DURUM: Üretimde çağrılmıyor — GUI'nin arama uç noktası da dahil olmak
        üzere tüm gerçek çağrılar `search_all_sources_rich`'e gidiyor (o, kapak
        görselini taşıyabildiği için tercih edildi). Silinmemesinin tek sebebi
        `test_arama_zaman_asimi.py`: oradaki dört test (zaman aşımında toplanan
        sonuçların korunması, yavaş kaynak yokken erken dönüş, patlayan kaynağın
        diğerlerini düşürmemesi) paralel toplama sözleşmesini `_rich`'ten
        bağımsız olarak bu metot üzerinden sınıyor. Yeni çağrı eklemeden önce
        `_rich` varyantının işi görüp görmediğine bak.

        Args:
            query: Search query
            limit_per_source: Maximum results per source

        Returns:
            Dict mapping source names to list of (slug, title) tuples
        """
        hatalar: Dict[str, str] = {}

        def _search_single(source_name: str):
            adapter = self.adapters[source_name]
            try:
                ciftler = adapter.search_anime(query, limit=limit_per_source) or []
                return source_name, _alakaya_gore_sirala(query, ciftler,
                                                         lambda c: c[1])
            except Exception as exc:
                print(f"{source_name} arama hatası: {exc}")
                hatalar[source_name] = _hata_metni(exc)
                return source_name, []

        sonuc = self._paralel_ara(_search_single, hatalar=hatalar)
        # Kopya: süre dolduktan sonra biten geç bir iş sözlüğe yazmaya devam
        # edebilir; döndürülen sonuç o anki hâliyle sabit kalmalı.
        return AramaSonuclari(sonuc, hatalar=dict(hatalar))

    def search_all_sources_rich(
        self, query: str, limit_per_source: int = 10
    ) -> Dict[str, List[Dict[str, Any]]]:
        """`search_all_sources` gibi, ama sonuçlar sözlük ve görsel taşıyabilir.

        Adapter `search_rich` sağlıyorsa o kullanılır; sağlamıyorsa
        `search_anime`'in (slug, title) çıktısı `image=None` ile sarılır.
        Böylece mevcut adapterlerin hiçbiri değişmek zorunda kalmaz.

        Dönüş bir `AramaSonuclari` (dict): hata veren kaynağın listesi boş,
        sebebi ``.hatalar[kaynak]``'ta — arama sayfası "bulunamadı" ile
        "okunamadı"yı ayırt edebilsin. Süreye yetişemeyen kaynak da
        ``hatalar``'da ("zaman aşımı").
        """
        return self._zengin_ara(query, limit_per_source)

    def artimli_ara(
        self, query: str,
        kaynak_bitti: Callable[[str, List[Dict[str, Any]], Optional[str]], Any],
        limit_per_source: int = 10,
    ) -> "AramaSonuclari":
        """`search_all_sources_rich` gibi, ama her kaynak bittiği AN haber verir.

        ``kaynak_bitti(kaynak, kayitlar, hata)`` aramayı yürüten thread'de,
        kaynak bittikçe çağrılır (hata veren kaynakta ``kayitlar=[]`` ve
        ``hata`` dolu). Eskiden arama sayfası hiçbir şey göstermeden en yavaş
        kaynağı (25 sn'ye kadar) bekliyordu; yerel arşivin milisaniyelik
        sonuçları da o sürenin sonunda geliyordu. Dönüş, tamamlanmış
        `AramaSonuclari` (yetişemeyenler "zaman aşımı" olarak ``hatalar``'da).
        """
        return self._zengin_ara(query, limit_per_source, kaynak_bitti)

    def _zengin_ara(self, query: str, limit_per_source: int,
                    kaynak_bitti: Optional[Callable[..., Any]] = None
                    ) -> "AramaSonuclari":
        hatalar: Dict[str, str] = {}

        def _one(source_name: str):
            adapter = self.adapters[source_name]
            try:
                if hasattr(adapter, "search_rich"):
                    kayitlar = adapter.search_rich(query, limit=limit_per_source) or []
                else:
                    pairs = adapter.search_anime(query, limit=limit_per_source) or []
                    kayitlar = [{"slug": s, "title": t, "image": None}
                                for s, t in pairs]
                return source_name, _alakaya_gore_sirala(
                    query, kayitlar, lambda k: k.get("title") or "")
            except Exception as exc:
                print(f"{source_name} arama hatası: {exc}")
                hatalar[source_name] = _hata_metni(exc)
                return source_name, []

        def _ilet(ad: str, kayitlar: Any) -> None:
            # `_one` hatayı dönmeden ÖNCE `hatalar`'a yazıyor; burada okunur.
            kaynak_bitti(ad, list(kayitlar or []), hatalar.get(ad))

        sonuc = self._paralel_ara(
            _one, bitince=_ilet if kaynak_bitti is not None else None,
            hatalar=hatalar)
        return AramaSonuclari(sonuc, hatalar=dict(hatalar))


def arama_motoru(kaynaklar: Optional[Iterable[str]] = None) -> "SearchEngine":
    """`SearchEngine` örneği; ``kaynaklar`` verilirse YALNIZCA onlar aranır.

    Detay sayfasının otomatik eşleştirmesi tek kaynak (ya da henüz bağlanmamış
    birkaç kaynak) için eşleşme arıyor; eskiden her seferinde bütün kaynaklar
    aranıyor, istenmeyenlerin sonucu atılıyordu — yani 25 sn'ye kadar süren
    ağ turu çoğunlukla hiç kullanılmayan kaynaklar için yapılıyordu. Arşiv
    (TürkAnime) tek başına istendiğinde arama ağa hiç çıkmıyor.

    Adlar kanonik ada çevrilerek karşılaştırılır ("AnimeDepo" → TürkAnime).
    `SearchEngine` modül özniteliği ÇAĞRI anında okunuyor: testler sınıfı
    sahte bir motorla değiştirebiliyor. Sahte motorun `adapters` sözlüğü
    yoksa kısıt uygulanmaz (sahte zaten kendi sonucunu döndürüyor).
    """
    motor = SearchEngine()
    if kaynaklar is None:
        return motor
    adaptorler = getattr(motor, "adapters", None)
    if isinstance(adaptorler, dict):
        istenen = {kayit.kanonik_ad(ad) for ad in kaynaklar}
        motor.adapters = {ad: adaptor for ad, adaptor in adaptorler.items()
                          if kayit.kanonik_ad(ad) in istenen}
    return motor
