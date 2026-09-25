from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Any, Dict, Callable, Iterable
import errno
import hashlib
import json
from tempfile import NamedTemporaryFile
from os import remove
import subprocess as sp
import re
import unicodedata

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .animecix import _video_streams
from ..common.dosya_adi import bolum_hedefi, indirilen_dosya
from ..common.utils import get_ydl_opts, get_video_resolution_mpv, extract_video_info


# Başlıktan ÜRETİLEN slug'ın üst sınırı. Kaynağın kendi verdiği slug'a
# uygulanmaz (bkz. `AdapterBolum`): o bir kimlik, kesilirse kimlik olmaktan çıkar.
SLUG_SINIRI = 80


def _slugify(text: str, azami: Optional[int] = SLUG_SINIRI) -> str:
    """Basit ve güvenli bir slug üretici: ASCII'ye indirger,
    boşlukları '-' yapar, gereksizleri temizler.

    ``azami``: uzunluk sınırı; ``None`` kesmez. Sınırı aşan slug düz kesilmez,
    sonuna tam slug'ın kısa özeti eklenir. Düz kesmek iki farklı slug'ı AYNI
    yapıyordu: arşivde 633 bölüm slug'ı 80 karakterden uzun ve 394'ü aynı
    animenin başka bir bölümüyle ilk 80 karakteri paylaşıyor ("…-youna-mo").
    Slug izleme geçmişinin anahtarı ve indirme dosyasının adı olduğu için
    sonuç: bütün bölümler aynı dosyaya iniyor, biri izlenince hepsi
    "izlendi" görünüyordu. Özet sayesinde kesilen slug'lar da ayrık kalır ve
    aynı girdi her zaman aynı slug'ı verir (Python'un `hash`'i gibi süreçten
    sürece değişmez).
    """
    if not text:
        return ""
    # Unicode -> ASCII transliterasyon
    t = unicodedata.normalize("NFKD", str(text))
    t = t.encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    t = re.sub(r"\s+", "-", t)
    t = re.sub(r"[^a-z0-9\-]", "-", t)
    t = re.sub(r"-+", "-", t).strip("-")
    if azami is not None and len(t) > azami:
        ozet = hashlib.sha1(t.encode("ascii")).hexdigest()[:8]
        t = f"{t[:max(1, azami - len(ozet) - 1)].rstrip('-')}-{ozet}"
    return t


@dataclass
class AdapterAnime:
    slug: str
    title: str

    def __post_init__(self):
        # Eğer slug sayı/ID ise ya da boşsa, başlıktan güvenli bir slug üret.
        raw = (self.slug or "").strip()
        if not raw or raw.isdigit() or not re.search(r"[a-zA-Z]", raw):
            self.slug = _slugify(self.title)


class AdapterVideo:
    """TürkAnime Video arayüzüne minimum uyumlu basit video nesnesi."""

    def __init__(
        self,
        bolum: 'AdapterBolum',
        url: Optional[str],
        label: Optional[str] = None,
        player: str = "ANIMECIX",
        referer: Optional[str] = None,
    ):
        self.bolum = bolum
        self._url = url or ""
        self.label = label
        self.player = player or "ANIMECIX"
        self.referer = referer
        self._info: Optional[Dict[str, Any]] = None
        self.is_supported = True
        self._is_working: Optional[bool] = None
        self._resolution: Optional[int] = None
        self.ydl_opts = get_ydl_opts()
        # Kaynak referer verdiyse yt-dlp'nin TÜM isteklerine iliştirilir:
        # AnimeDepo/Tranimaci/Anizle CDN'leri referer'sız istekte 403 dönüyor,
        # bu da `info`yu boşaltıp bölümü "çalışmıyor" gösteriyordu. yt-dlp bu
        # başlığı harici indiriciye (aria2c) de kendisi aktarır.
        if self.referer:
            self.ydl_opts["http_headers"] = {
                **(self.ydl_opts.get("http_headers") or {}),
                "Referer": self.referer,
            }

    @property
    def url(self) -> str:
        return self._url

    @property
    def info(self) -> Optional[Dict[str, Any]]:
        if self._info is None:
            # OPENANI linkleri Cloudflare arkasında olduğu için yt-dlp 404 dönecektir.
            # Bu linkler direkt mp4/m3u8 olduğu için info'yu sahte (mock) oluşturuyoruz.
            if self.player == "OPENANI":
                # `id`/`extractor` şart: yt-dlp `process_video_result`'ta
                # `info_dict['extractor']`ı okuyor; yoksa KeyError('extractor')
                # ile her OpenAnime indirmesi "hata: 'extractor'" diye düşüyordu.
                self._info = {
                    "id": str(getattr(self.bolum, "slug", "") or "video"),
                    "url": self.url,
                    "ext": "mp4" if "mp4" in self.url else "m3u8",
                    "title": self.bolum.title if self.bolum else "Video",
                    "extractor": "generic",
                    "extractor_key": "Generic",
                    "webpage_url": self.url,
                }
                return self._info

            info = extract_video_info(self.url, self.ydl_opts)
            if not info:
                self._info = {}
            else:
                # info'nun Dict[str, Any] olduğunu garanti edelim
                if isinstance(info, dict):
                    if "direct" in info:
                        del info["direct"]
                    if info.get("video_ext") == "html":
                        self._info = None
                    else:
                        self._info = info
                else:
                    self._info = {}
        return self._info

    @property
    def is_working(self) -> bool:
        if self._is_working is None:
            try:
                self._is_working = self.info not in (None, {})
            except Exception:
                self._is_working = False
        return self._is_working

    @is_working.setter
    def is_working(self, value: bool):
        self._is_working = value

    def indir(self, callback=None, output=""):
        assert self.is_working, "Video çalışmıyor."
        # Slug kaynağın verisi: arşivin dizin.json'ına `"../../../evil"` konursa
        # yt-dlp dosyayı indirme klasörünün DIŞINA yazar. Bkz. common.dosya_adi.
        out_tmpl_dir = bolum_hedefi(output, self.bolum)
        opts = self.ydl_opts.copy()
        if callback:
            opts['progress_hooks'] = [callback]
        opts['outtmpl'] = {'default': out_tmpl_dir + r'.%(ext)s'}
        # `get_ydl_opts` 'ignoreerrors': 'only_download' veriyor; o ayarla
        # yt-dlp HTTP 403'te istisna FIRLATMIYOR, yalnızca 1 döndürüyordu.
        # Dönüş değeri de okunmadığı için arayüz ve CLI klasör boşken işi
        # "tamamlandı" sayıp geçmişe "indirildi" yazıyordu; kuyruğun kendi
        # yeniden denemesi de hiç çalışmıyordu. İndirmede hata hata olmalı.
        opts['ignoreerrors'] = False
        # delete=False şart: yt-dlp dosyayı adıyla ikinci kez açıyor (Windows'ta
        # açık bir NamedTemporaryFile yeniden açılamaz). Bu yüzden temizliği biz
        # yapıyoruz — aksi hâlde her indirme bir geçici dosya sızdırır.
        with NamedTemporaryFile("w", delete=False, suffix=".info.json") as tmp:
            json.dump(self.info, tmp)
        try:
            with YoutubeDL(opts) as ydl:  # type: ignore
                kod = ydl.download_with_info_file(tmp.name)
            # İki kat güvence: çıkış kodu da, diskteki sonuç da denetlenir.
            # Yalnızca `.part`/`.ytdl` kaldıysa indirme bitmemiştir.
            if isinstance(kod, int) and kod != 0:
                raise DownloadError(f"yt-dlp indirmeyi bitiremedi (çıkış kodu {kod})")
            if indirilen_dosya(out_tmpl_dir) is None:
                raise DownloadError("indirme bitti ama dosya diskte yok: "
                                    f"{out_tmpl_dir}.*")
        finally:
            try:
                remove(tmp.name)
            except OSError:
                pass

    def get(self, key, default=None):
        """Dictionary-like get method for compatibility."""
        if key == 'url':
            return self.url
        elif key == 'label':
            return self.label
        elif key == 'player':
            return self.player
        elif key == 'referer':
            return self.referer
        return default

    def oynat(self, dakika_hatirla: bool = False):
        """Videoyu mpv ile oynat."""
        import shutil
        from ..common.utils import BIN_PATH
        from os.path import join, exists
        
        # Önce bin/ klasöründeki mpv'yi dene, sonra sistem PATH'ini.
        # Uzantı sabit "mpv.exe" YAZILAMAZ: yayın Linux ve macOS paketi de
        # üretiyor, o platformlarda gömülü ikili uzantısız "mpv" oluyor.
        # Sabit ad yüzünden `exists()` hep False dönüyor ve paketle GELEN
        # mpv hiç kullanılmıyordu — kullanıcı sisteminde mpv yoksa uygulama
        # "MPV bulunamadı" diyordu, oysa ikili kutunun içindeydi.
        mpv_path = next(
            (aday for aday in (join(BIN_PATH, "mpv.exe"), join(BIN_PATH, "mpv"))
             if exists(aday)),
            None,
        )
        if not mpv_path:
            # Sistem PATH'inde mpv ara
            mpv_path = shutil.which("mpv")
            if not mpv_path:
                print("MPV bulunamadı! Lütfen mpv'yi yükleyin veya bin/ klasörüne koyun.")
                return None
        
        cmd = [mpv_path, self.url]

        # User-agent ekle (HLS için gerekli olabilir)
        cmd.extend(["--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"])

        # Referer, user-agent ile aynı gerekçeyle: CDN'ler kaynak sayfayı
        # görmezse 403 döner ve mpv boş ekranla kapanır.
        if self.referer:
            cmd.append(f"--referrer={self.referer}")

        # Dakika hatırlama özelliği
        if dakika_hatirla:
            cmd.append("--save-position-on-quit")
        
        try:
            proc = sp.Popen(cmd)
            proc.wait()  # İşlemin bitmesini bekle
            return proc
        except OSError as e:
            # Gömülü mpv çalıştırılamadı — sistem mpv'sine düş.
            #
            # `e.winerror` DOĞRUDAN okunamaz: o öznitelik yalnızca Windows'ta
            # var. Linux/macOS'ta bu satır, asıl hatayı maskeleyen bir
            # `AttributeError` fırlatıyordu ve yedeğe hiç geçilmiyordu — yani
            # yedek tam olarak Windows dışı platformlarda ölü koddu. Yayın üç
            # platforma da paket ürettiği için bu gerçek bir kusurdu.
            #
            # `getattr` her platformda çalışır. Windows'ta 216 "uyumsuz binary"
            # demek; diğer platformlarda ENOEXEC (8) aynı anlama geliyor ve
            # ikili hiç bulunamadığında da (ENOENT) sistem mpv'si denenmeli.
            winerror = getattr(e, "winerror", None)
            uyumsuz = winerror == 216 or e.errno in (errno.ENOEXEC, errno.ENOENT)
            if uyumsuz:
                print("bin/mpv.exe uyumsuz, sistem mpv deneniyor...")
                sys_mpv = shutil.which("mpv")
                if sys_mpv:
                    cmd[0] = sys_mpv
                    try:
                        proc = sp.Popen(cmd)
                        proc.wait()
                        return proc
                    except Exception as e2:
                        print(f"Sistem MPV hatası: {e2}")
                        return None
            print(f"MPV başlatma hatası: {e}")
            return None
        except Exception as e:
            print(f"MPV başlatma hatası: {e}")
            return None

    @property
    def resolution(self) -> int:
        if self._resolution is None:
            info = self.info or {}
            res = info.get("resolution")
            if res:
                m = re.findall(r"(\d{3,4})p", str(res))
                if m:
                    self._resolution = int(m[0])
                    return self._resolution
            fmts = info.get("formats") or []
            if fmts:
                try:
                    if "height" in (fmts[0] or {}):
                        self._resolution = max(
                            fmts,
                            key=lambda x: x.get("height") or 0
                        ).get("height") or 0
                    else:
                        t = max(fmts, key=lambda x: (x.get("height") or 0, x.get("tbr") or 0))
                        self._resolution = (
                            t.get("height") or
                            (720 if (t.get("tbr") or 0) > 1500 else 480)
                        ) or 0
                except Exception:
                    self._resolution = 0
            else:
                # Label'dan tahmin
                m = re.findall(r"(\d{3,4})p", str(self.label or ""))
                self._resolution = int(m[0]) if m else 0
            # mpv ile son çare çözünürlük tespiti
            if not self._resolution:
                self._resolution = get_video_resolution_mpv(self.url,
                                                            self.referer) or 0
        return self._resolution or 0


class AdapterBolum:
    def __init__(
        self,
        url: Optional[str],
        title: str,
        anime: AdapterAnime,
        stream_provider: Optional[Callable[[str], List[Dict[str, str]]]] = None,
        player_name: str = "ANIMECIX",
        slug: Optional[str] = None,
    ):
        self.url = url
        self._title = title
        self.anime = anime
        self._stream_provider = stream_provider
        self._player_name = player_name or "ANIMECIX"
        # TürkAnime ile uyumlu: animeadı-bolumadı (klasör: anime.slug, dosya adı: animeadı-bolumadı).
        # Kaynak kendi bölüm slug'ını veriyorsa (TürkAnime arşivi: sitenin
        # "naruto-1-bolum"u) o kullanılır: izleme geçmişi ve eski indirmelerin
        # dosya adları o slug'la duruyor. Yine `_slugify`'dan geçer — değer
        # arşivden geliyor; indirme yolu ayrıca `guvenli_alt_yol` ile korunuyor.
        # KESİLMEZ (`azami=None`): sitenin slug'ı hiç kısaltılmamıştı; 80'de
        # kesmek uzun adlı serilerde bölümleri birbirine karıştırıyordu (aynı
        # geçmiş anahtarı, aynı indirme dosyası). Dosya adı uzunluğu diske
        # dokunulan yerde, `guvenli_alt_yol`'da sınırlanıyor.
        verilen = _slugify(slug, azami=None) if slug else ""
        self.slug = verilen or _slugify(f"{anime.title}-{title}" if anime else title)
        # `fansubs` akışları getirmek zorunda (fansub adları akışların içinde).
        # CLI hemen ardından `best_video` çağırıyor; aynı listeyi ikinci kez
        # istememek için burada bekletilir ve İLK `best_video` onu tüketir.
        # Kalıcı cache DEĞİL: bazı kaynakların adresleri imzalı/süreli; aynı
        # bölüm nesnesiyle saatler sonra yapılan oynatma taze liste almalı.
        self._bekleyen_akislar: Optional[List[Dict[str, Any]]] = None
        self._fansub_listesi: Optional[List[str]] = None

    @property
    def title(self):
        return self._title

    def _saglayici(self) -> Callable[[str], List[Dict[str, Any]]]:
        return self._stream_provider or _video_streams

    def _fansublari_not_et(self, akislar: List[Dict[str, Any]]) -> None:
        """Akışlardaki fansub adlarını (ilk görülme sırasıyla) hatırla."""
        if self._fansub_listesi is not None or not akislar:
            return
        adlar: List[str] = []
        for akis in akislar:
            ad = akis.get("fansub") if isinstance(akis, dict) else None
            if isinstance(ad, str) and ad.strip() and ad.strip() not in adlar:
                adlar.append(ad.strip())
        self._fansub_listesi = adlar

    @property
    def fansubs(self):
        """Bölümün fansub adları; sağlayıcı "fansub" vermiyorsa boş liste.

        AnimeciX/Tranimaci gibi kaynaklarda fansub kavramı yok, liste boş kalır
        ve CLI seçim sormaz. AnimeDepo her akışta "fansub" taşıyor; aynı
        bölümün birden çok grubu varsa kullanıcı seçebilir.
        """
        if self._fansub_listesi is None and self.url:
            try:
                akislar = self._saglayici()(self.url) or []
            except Exception:
                akislar = []          # fansub listesi yüzünden oynatma çökmesin
            if akislar:
                self._bekleyen_akislar = akislar
                self._fansublari_not_et(akislar)
        return list(self._fansub_listesi or [])

    def best_video(
        self,
        by_res=True,
        by_fansub=None,
        default_res=600,
        callback=lambda x: None,
        early_subset: int = 8,
        atla: Optional[Iterable[str]] = None,
    ):
        """En iyi çalışan videoyu bul; hiçbiri yoksa None.

        ``atla``: bu adreslerdeki akışlar hiç denenmez. CLI'ın yeniden deneme
        döngüsü, mpv'de oynatılamayan videonun adresini buraya ekliyor.
        Gerekli çünkü bu sınıf videoları önbelleklemiyor: her çağrı akışlardan
        YENİ `AdapterVideo`'lar kuruyor, çağıranın başarısız videoya koyduğu
        ``is_working = False`` bir sonraki çağrıda kayboluyor ve aynı (ilk
        sıradaki) adres yeniden seçiliyordu — CLI üç denemenin üçünde de aynı
        bozuk videoyu açıyor, çalışan diğerlerine hiç geçmiyordu. (Eski
        `objects.Bolum` videolarını sakladığı için bu sorun orada yoktu.)

        Kaynak okunamadıysa (TürkAnime arşivine ulaşılamadı) sağlayıcının
        hatası YÜKSELİR, "hiçbiri çalışmıyor" denmez; bkz.
        `kayit.akis_saglayici`.
        """
        # URL kontrolü
        if not self.url:
            callback({"current": 1, "total": 1, "player": "ANIMECIX", "status": "URL bulunamadı"})
            return None

        # Kaynağa uygun stream sağlayıcısını kullan
        player_label = self._player_name

        callback({"current": 0, "total": 1, "player": player_label, "status": "üstbilgi çekiliyor"})
        if self._bekleyen_akislar is not None:
            # `fansubs` az önce getirdi; aynı listeyi ikinci kez isteme.
            streams, self._bekleyen_akislar = self._bekleyen_akislar, None
        else:
            try:
                streams = self._saglayici()(self.url)
            except Exception:
                callback({"current": 1, "total": 1, "player": player_label,
                          "status": "kaynak okunamadı"})
                raise
            self._fansublari_not_et(streams or [])
        if not streams:
            callback({
                "current": 1,
                "total": 1,
                "player": player_label,
                "status": "hiçbiri çalışmıyor"
            })
            return None

        def parse_res(label: str) -> int:
            m = re.findall(r"(\d{3,4})p", label or "")
            return int(m[0]) if m else default_res

        adaylar = [s for s in streams if isinstance(s, dict) and s.get("url")]
        if not adaylar:
            callback({
                "current": 1,
                "total": 1,
                "player": player_label,
                "status": "video URL bulunamadı"
            })
            return None

        # Daha önce denenip oynatılamayanlar elenir. Hepsi denendiyse "video
        # yok" değil "hiçbiri çalışmıyor": adresler vardı, çalışmadılar.
        if atla:
            atlanacak = set(atla)
            kalan = [s for s in adaylar if s.get("url") not in atlanacak]
            if not kalan:
                callback({"current": 1, "total": 1, "player": player_label,
                          "status": "hiçbiri çalışmıyor"})
                return None
            adaylar = kalan

        # Seçilen fansub'un akışlarıyla sınırla. Hiçbiri eşleşmiyorsa (fansub
        # kavramı olmayan kaynak ya da o grubun kaydı artık yok) hepsiyle devam:
        # kullanıcı "bu grubu tercih ederim" dedi, "başka grup oynatma" demedi.
        if by_fansub:
            secili = [s for s in adaylar if s.get("fansub") == by_fansub]
            if secili:
                adaylar = secili

        # Kaynaklar aynı kalite için birden çok CDN yedeği döndürüyor
        # ("1080p", "1080p (CDN2)", ...). Eskiden yalnızca en yüksek çözünürlüklü
        # İLK aday deneniyordu; o CDN 403/504 verdiğinde çalışan yedekler varken
        # kullanıcı "video bulunamadı" görüyordu. Sıralama kararlı olduğu için
        # aynı kalitede kaynağın verdiği CDN sırası korunur.
        if by_res:
            adaylar.sort(key=lambda s: parse_res(s.get("label") or "0p"),
                         reverse=True)
        # `early_subset` ile aynı bütçe: her CDN'i denemek yt-dlp zaman aşımları
        # yüzünden dakikalara mal olabilir.
        adaylar = adaylar[:max(1, int(early_subset or 1))]

        toplam = len(adaylar)
        for sira, aday in enumerate(adaylar, start=1):
            # Akış kendi oynatıcısını söylüyorsa (AnimeDepo: SIBNET, MAIL…)
            # ilerlemede o görünür; söylemeyen kaynaklarda eski etiket kalır.
            oynatici = aday.get("player") or player_label
            callback({"current": sira, "total": toplam, "player": oynatici,
                      "status": "üstbilgi çekiliyor"})
            vid = AdapterVideo(self, aday.get("url"), aday.get("label"),
                               player=oynatici, referer=aday.get("referer"))
            if vid.is_working:
                callback({"current": sira, "total": toplam,
                          "player": oynatici, "status": "çalışıyor"})
                return vid
            callback({"current": sira, "total": toplam, "player": oynatici,
                      "status": "çalışmıyor"})
        callback({"current": toplam, "total": toplam, "player": player_label,
                  "status": "hiçbiri çalışmıyor"})
        return None


def kayittan_bolumler(kaynak: Any, slug: str, title: str) -> List[AdapterBolum]:
    """Kayıttaki bir kaynağın (`sources.kayit.Kaynak`) bölümlerini nesneye çevir.

    Qt köprüsü (`gui/qt/sources_bridge.py`) ve CLI aynı işi eskiden kaynak
    başına ayrı ayrı yazıyordu — CLI'da her kaynak için iki kez (izle ve indir
    dalları), toplam ~250 satır neredeyse aynı `AdapterBolum(...)` kurulumu.
    Bu fonksiyon o kurulumun tek kopyası: bölüm kimliği kaynağın
    `bolum_adresi` ile url'ye çevrilir, akışlar kimliği kapatan sağlayıcıyla
    getirilir (bkz. `kayit.akis_saglayici`).

    Burada, `kayit.py`'de değil: `AdapterBolum` bu modülde ve bu modül yt_dlp
    çekiyor; kayıt ise sunucu tarayıcısının da okuduğu hafif modül.

    Kimlik bu kaynakta açılamıyorsa (AnimeciX: sayısal değil) ``ValueError``
    kaynağın kendi mesajıyla yükselir; kaynak bölüm vermiyorsa boş liste.
    """
    from .kayit import akis_saglayici

    hata = kaynak.kimlik_denetle(slug)
    if hata:
        raise ValueError(hata)
    uclar = kaynak.uclar()
    if uclar.bolumler is None or uclar.akislar is None:
        return []                       # yalnızca metadata (AniList)
    ham = uclar.bolumler(slug) or []
    anime = AdapterAnime(slug=slug, title=title)
    bolumler: List[AdapterBolum] = []
    gorulen = set()
    for bolum_id, bolum_basligi in ham:
        # Aynı kimlik iki kez: Anizle'nin veritabanı ve sayfası aynı bölümü
        # tekrar verebiliyor (eskiden `AnizleAnime.episodes` bu yüzden
        # ayıklıyordu). Aynı bölüm listede iki satır olmasın.
        if bolum_id in gorulen:
            continue
        gorulen.add(bolum_id)
        bolumler.append(AdapterBolum(
            url=kaynak.bolum_adresi(bolum_id),
            title=bolum_basligi,
            anime=anime,
            stream_provider=akis_saglayici(uclar.akislar, bolum_id),
            player_name=kaynak.oynatici,
            slug=kaynak.bolum_slugu(bolum_id) if kaynak.bolum_slugu else None,
        ))
    return bolumler
