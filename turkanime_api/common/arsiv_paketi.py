"""Çevrimdışı arşivin dosya ve paket yardımcıları.

Buradaki hiçbir fonksiyon ağ oturumu kurmaz, kaynak/GUI bilmez: HTTP yanıtını
çağıran verir, bu modül yalnızca bayt akıtır, tar açar, klasör değiştirir.
Böyle olması iki şey kazandırıyor:

- `sources.animedepo` (istemci) ile `common.arsiv_senkron` (bakımcı aracı) aynı
  doğrulamayı ve aynı `KAYNAK.json` biçimini kullanıyor; biri diğerinden kayamaz.
- Testler gerçek ağa ve 500 MB'lık `arsiv/`'e dokunmadan `tmp_path` altında
  birkaç dosyalık sahte arşivlerle her yolu (bozuk tar, iptal, yarım takas)
  deneyebiliyor.

Güvenlik notu: arşivin içeriği (slug'lar, bölüm adları, tar üye adları) bizim
kontrolümüzde değil. Bir slug `../../x` ya da bir tar üyesi `/etc/passwd` veya
sembolik bağ olursa dosya arşiv klasörünün DIŞINA yazılır. Bu yüzden diske
dokunan her göreli yol `goreli_parcalar`'dan geçiyor ve tar üyeleri açılmadan
önce tek tek eleniyor (`tarfile.extractall` hiç kullanılmıyor).
"""
from __future__ import annotations

import errno
import fnmatch
import json
import ntpath
import os
import queue
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, TypeVar

DIZIN_DOSYASI = "dizin.json"
MANIFEST_DOSYASI = "KAYNAK.json"

# `KAYNAK.json` anahtarları ve sırası. `arsiv/KAYNAK.json` bu biçimde; bakımcı
# aracı ile indirilen tam arşiv aynı dosyayı üretiyor ki arayüz ikisini de aynı
# kodla okuyabilsin.
MANIFEST_ANAHTARLARI = (
    "kaynak", "dal", "commit", "commit_tarihi", "cekilme_tarihi",
    "dizin_last_update", "anime_sayisi", "dosya_sayisi",
)

# Açılmış arşiv için üst sınır. Gerçek arşiv ~0,5 GB; sınır bozuk ya da kötü
# niyetli bir paketin ("tar bombası") diski doldurmasını engellemek için.
AZAMI_ACILMIS_BAYT = 4 * 1024 ** 3

_SURUCU = re.compile(r"^[A-Za-z]:")

IlerlemeFn = Callable[[int, Optional[int]], Any]
AsamaFn = Callable[[str], Any]

# `paketten_kur`'un `asama` geri çağrısına verdiği adlar, sırasıyla. Neden ayrı
# bir geri çağrı: bayt ilerlemesi yalnızca İNDİRME boyunca akıyor; ardından
# 83 bin dosyanın açılması onlarca saniye sürüyor ve bu sürede `ilerleme` hiç
# çağrılmıyor. Arayüz aşamayı bilmezse çubuk %100'de donmuş görünür, kullanıcı
# "takıldı" sanıp iptal eder.
# `sources.animedepo.tam_arsiv_indir` her kaynağa istek atmadan ÖNCE bunu
# bildiriyor: GitLab bağlanma zaman aşımına kadar (15 sn) yanıt vermezse
# kullanıcı hangi kaynağın beklendiğini görsün.
ASAMA_BAGLANMA = "baglaniyor"
ASAMA_INDIRME = "indiriliyor"
ASAMA_ACMA = "aciliyor"
ASAMA_YERLESTIRME = "yerlestiriliyor"


class ArsivHatasi(RuntimeError):
    """Arşiv indirilemedi, açılamadı ya da geçersiz çıktı."""


class GuvensizUye(ArsivHatasi):
    """Tar paketinde klasör dışına yazacak / bağ / aygıt üyesi var."""


class IptalEdildi(ArsivHatasi):
    """Kullanıcı işlemi iptal etti."""


class YerelDiskHatasi(ArsivHatasi):
    """Yerel diske yazılamadı: disk dolu, izin yok, klasör kullanımda…

    Paketin ya da kaynağın suçu DEĞİL; ayrı bir sınıf olmasının sebebi bu.
    `sources.animedepo.tam_arsiv_indir` her hatayı "bu kaynak olmadı" sayıp
    yedeğe geçiyordu: GitLab'dan ~230 MB indikten sonra disk dolunca GitHub'ın
    ~231 MB'lık paketini de baştan indiriyor, aynı yerde aynı sebeple
    düşüyordu. Kullanıcıya da "paket bozuk" deniyordu. Bu hata yedeğe
    geçirmez ve mesajı diski anlatır.
    """


# Bu kadar sık iptal denetlenir (sn). Ağ bekleyişleri (bağlanma, ilk bayt,
# takılan akış) yardımcı thread'de sürerken çağıran thread en geç bu aralıkla
# iptale bakar; düğmeye basıldıktan sonra "İptal ediliyor…" bu kadar sürer.
IPTAL_ARALIGI = 0.1

_T = TypeVar("_T")
_YOK = object()

# errno → kullanıcıya gösterilecek sebep. Sistem mesajları dile ve platforma
# göre değişiyor ("No space left on device"); tanıdık olanlar Türkçe söylenir.
_DISK_SEBEPLERI = {
    errno.ENOSPC: "diskte yer kalmadı",
    errno.EACCES: "yazma izni yok ya da dosya başka bir programda açık",
    errno.EPERM: "yazma izni yok",
    errno.EROFS: "disk salt okunur",
    errno.EBUSY: "klasör başka bir program tarafından kullanılıyor",
}
if hasattr(errno, "EDQUOT"):
    _DISK_SEBEPLERI[errno.EDQUOT] = "disk kotası doldu"


def disk_mesaji(islem: str, yol: Any, hata: OSError) -> str:
    """"yerel disk hatası: <yol> yazılamadı — diskte yer kalmadı" biçiminde metin."""
    sebep = _DISK_SEBEPLERI.get(getattr(hata, "errno", None)) or hata.strerror or str(hata)
    return f"yerel disk hatası: {yol} {islem} — {sebep}"


@contextmanager
def yerel_disk(islem: str, yol: Any) -> Iterator[None]:
    """Bloktaki `OSError`'u `YerelDiskHatasi`'na çevir.

    YALNIZCA yerel dosya işlemlerinin (aç, yaz, klasör kur, taşı) çevresinde
    kullanılır. Ağ hataları da `OSError` olabildiği için (requests ve
    curl_cffi istisnaları `OSError`'dan türüyor) bu sarmalayıcı bir ağ
    okumasını ASLA kapsamamalı; yoksa kopan bağlantı "disk dolu" görünür.
    """
    try:
        yield
    except OSError as hata:
        raise YerelDiskHatasi(disk_mesaji(islem, yol, hata)) from hata


# ─────────────────────────────────────────────────────────────────────────────
# Göreli yol güvenliği
# ─────────────────────────────────────────────────────────────────────────────
def goreli_parcalar(yol: str) -> Tuple[str, ...]:
    """Arşiv içi göreli yolu doğrula ve bileşenlerine ayır.

    Reddedilenler (``ValueError``): mutlak yol, Windows sürücüsü (`C:`), ``..``
    bileşeni, ters bölü (Windows'ta ayraç: ``a\\..\\..\\x`` orada kaçış olur),
    NUL baytı ve boş yol. ``.`` ve boş bileşenler (``a//b``) sessizce atılır.

    `:` genel olarak serbest: arşivde gerçekten ``One Piece Movie 6: ….json``
    adlı bir bölüm dosyası var. Yalnızca bileşenin başındaki sürücü kalıbı
    yasak — Windows'ta ``Path("x") / "C:y"`` yolu C: sürücüsüne sıçratır.
    """
    if not isinstance(yol, str) or not yol:
        raise ValueError("boş arşiv yolu")
    if "\x00" in yol or "\\" in yol:
        raise ValueError(f"güvensiz arşiv yolu: {yol!r}")
    if yol.startswith("/"):
        raise ValueError(f"mutlak arşiv yolu: {yol!r}")
    parcalar = tuple(p for p in yol.split("/") if p not in ("", "."))
    if not parcalar:
        raise ValueError(f"boş arşiv yolu: {yol!r}")
    for parca in parcalar:
        if parca == ".." or _SURUCU.match(parca):
            raise ValueError(f"klasör dışına çıkan arşiv yolu: {yol!r}")
    return parcalar


def guvenli_birlestir(kok: Path, yol: str) -> Path:
    """``kok`` altındaki dosya yolunu kur; dışarı çıkamayan yol garanti.

    Windows'ta `:` içeren bileşen ayrıca reddedilir: NTFS onu "alternatif veri
    akışı" sayar, dosya yazılmaz, adı bölünmüş başka bir dosyaya gider.

    Dönen yol GÖSTERİM içindir (mesajlarda, karşılaştırmada); diske verirken
    `disk_yolu`'ndan geçirilmeli — bkz. aşağıdaki uzun yol açıklaması.
    """
    parcalar = goreli_parcalar(yol)
    if os.name == "nt" and any(":" in p for p in parcalar):
        raise ValueError(f"Windows'ta yazılamayan arşiv yolu: {yol!r}")
    return Path(kok).joinpath(*parcalar)


# ─────────────────────────────────────────────────────────────────────────────
# Windows uzun yolları
# ─────────────────────────────────────────────────────────────────────────────
# Windows'un klasik yol sınırı MAX_PATH = 260 karakter (sondaki NUL dahil,
# yani en çok 259). Arşivde göreli yolu 311 karaktere varan dosyalar var
# (69'u 240'ın üstünde; uzun bölüm adları). Tipik bir profilde tam arşiv
# `C:\Users\<ad>\Turkanime\.cevrimdisi_arsiv-indirme-XXXXXXXX\acilan\`
# altına açılıyor (71 karakter önek): 280 dosya sınırı aşıyor, son yer olan
# `cevrimdisi_arsiv\` altında bile 132'si. Sınırı yalnızca kayıt defterindeki
# LongPathsEnabled kaldırıyor ve Windows 10/11'de varsayılanı KAPALI.
# Sonuç: ilk uzun üyede `open()` ENOENT veriyordu, `yerel_disk` onu "yerel
# disk hatası" yapıyordu ve "Tüm arşivi indir" her seferinde yarıda
# kalıyordu (yedek kaynak da aynı yere düşerdi). Okuma tarafında da o
# dosyalar "yok" görünüyordu.
#
# Çare Win32'nin "genişletilmiş uzunluk" öneki: `\\?\C:\...` biçimindeki yol
# ~32 bin karaktere kadar kabul ediliyor, ayar gerekmiyor. Önek Win32'nin yol
# normalleştirmesini de kapatıyor (`/` → `\`, `..` çözümü yapılmıyor); yol bu
# yüzden önce `ntpath.abspath` ile mutlak ve normal hâle getiriliyor.
#
# Kural: diske dokunan her çağrı (open, mkdir, replace, rmtree, stat…) yolu
# `disk_yolu`'ndan geçirerek verir; mesajlarda ve `Path` hesaplarında öneksiz
# hâl kalır (kullanıcı `\\?\` görmesin). Windows dışında `disk_yolu` yolu
# olduğu gibi döndürür — davranış değişmez.
_UZUN_ONEK = "\\\\?\\"               # \\?\
_UNC_UZUN_ONEK = "\\\\?\\UNC\\"      # \\?\UNC\


def windows_uzun_yol(yol: str) -> str:
    """Windows yolunu genişletilmiş uzunluk (`\\\\?\\`) biçimine çevir.

    Saf dizgi işlemi (`ntpath`): her platformda aynı sonucu verir, bu yüzden
    Linux'ta da sınanabiliyor. Zaten önekli (`\\\\?\\`) ya da aygıt (`\\\\.\\`)
    yolu olduğu gibi döner. Ağ paylaşımı (`\\\\sunucu\\paylaşım\\…`) için önek
    `\\\\?\\UNC\\`.
    """
    if yol.startswith((_UZUN_ONEK, "\\\\.\\")):
        return yol
    mutlak = ntpath.abspath(yol)
    if mutlak.startswith("\\\\"):
        return _UNC_UZUN_ONEK + mutlak[2:]
    return _UZUN_ONEK + mutlak


def _uzun_yol_gerekli() -> bool:
    """Önek gerekiyor mu? Ayrı fonksiyon: testler Windows'u taklit edebilsin
    (`os.name`'i "nt" yapmak `pathlib`'i bozuyor — WindowsPath kurmaya
    kalkıyor)."""
    return os.name == "nt"


def disk_yolu(yol: Any) -> str:
    """Diske verilecek yol: Windows'ta `\\\\?\\` önekli, başka yerde aynen."""
    metin = os.fspath(yol)
    if not _uzun_yol_gerekli():
        return metin
    return windows_uzun_yol(metin)


def gorunen_yol(yol: Any) -> str:
    """`disk_yolu`'nun tersi: mesajda gösterilecek öneksiz yol."""
    metin = os.fspath(yol)
    if metin.startswith(_UNC_UZUN_ONEK):
        return "\\\\" + metin[len(_UNC_UZUN_ONEK):]
    if metin.startswith(_UZUN_ONEK):
        return metin[len(_UZUN_ONEK):]
    return metin


def klasor_kur(yol: Any) -> None:
    """`Path.mkdir(parents=True, exist_ok=True)`'un uzun yol güvenli hâli."""
    os.makedirs(disk_yolu(yol), exist_ok=True)


def atomik_bayt_yaz(hedef: Path, veri: bytes) -> None:
    """Baytları geçici dosya + `os.replace` ile yaz.

    Yarım yazılmış bir önbellek dosyası sonraki çevrimdışı açılışta bozuk JSON
    olarak okunurdu. Geçici dosya hedefle AYNI klasörde: `os.replace` yalnızca
    aynı dosya sisteminde atomik. Adı süreç + rastgele ek taşıyor; aynı bölümü
    iki thread aynı anda önbelleğe yazarsa birbirinin geçici dosyasını ezmez.

    `fsync` bilerek yok: bu önbellek, kaybı "bir sonraki açılışta yeniden
    indir" demek. Binlerce küçük dosyada fsync disk bekleyişi pahalı.
    """
    hedef = Path(hedef)
    klasor_kur(hedef.parent)
    gecici = hedef.with_name(f".{hedef.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(disk_yolu(gecici), "wb") as fp:
            fp.write(veri)
        os.replace(disk_yolu(gecici), disk_yolu(hedef))
    except BaseException:
        try:
            os.remove(disk_yolu(gecici))
        except OSError:
            pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Arşiv doğrulama ve manifest
# ─────────────────────────────────────────────────────────────────────────────
def arsivi_dogrula(kok: Path) -> Dict[str, Any]:
    """``kok/dizin.json`` okunabilir ve AnimeDepo şemasında mı? Dizini döndürür.

    Yalnızca dosyanın VARLIĞINA bakmak yetmiyor: yarım kalmış bir kopyalama ya
    da bozuk indirme sıfır baytlık bir `dizin.json` bırakabilir; o klasörü
    "yerel arşiv" sayarsak her arama sessizce 0 sonuç döndürür.
    """
    yol = Path(kok) / DIZIN_DOSYASI
    try:
        with open(disk_yolu(yol), encoding="utf-8") as fp:
            veri = json.load(fp)
    except (OSError, ValueError) as hata:
        raise ArsivHatasi(f"{yol} okunamadı: {hata}") from hata
    if not isinstance(veri, dict) or not isinstance(veri.get("index"), dict):
        raise ArsivHatasi(f"{yol} AnimeDepo dizini değil (\"index\" yok)")
    return veri


def arsiv_gecerli_mi(kok: Optional[Path]) -> bool:
    """`arsivi_dogrula`'nın istisnasız hâli; `None`/yok klasör → False."""
    if kok is None:
        return False
    try:
        arsivi_dogrula(Path(kok))
    except ArsivHatasi:
        return False
    return True


def anime_sayisi(dizin: Dict[str, Any]) -> int:
    """Dizindeki toplam anime (slug) sayısı."""
    return sum(len(grup) for grup in (dizin.get("index") or {}).values()
               if isinstance(grup, dict))


def manifest_uret(kok: Path, *, kaynak: str, dal: str, commit: Optional[str],
                  commit_tarihi: Optional[str], cekilme_tarihi: Optional[str] = None,
                  dosya_sayisi: Optional[int] = None) -> Dict[str, Any]:
    """`KAYNAK.json` içeriğini üret (dosyaya yazmaz).

    ``dosya_sayisi`` verilmezse klasör sayılır. Sayıma manifestin KENDİSİ de
    dahil (yazılmadan önce sayıldığı için +1): `arsiv/KAYNAK.json`'daki değer
    README.md ve KAYNAK.json dahil klasördeki toplam dosya sayısı.
    """
    kok = Path(kok)
    dizin = arsivi_dogrula(kok)
    if dosya_sayisi is None:
        # Önekli kökten sayılıyor: uzun adlı dosyalarda `is_file()` öneksiz
        # yolda Windows'ta False dönerdi ve sayı eksik çıkardı.
        taban = Path(disk_yolu(kok))
        dosya_sayisi = sum(1 for p in taban.rglob("*")
                           if p.is_file() and p != taban / MANIFEST_DOSYASI) + 1
    return {
        "kaynak": kaynak,
        "dal": dal,
        "commit": commit,
        "commit_tarihi": commit_tarihi,
        "cekilme_tarihi": cekilme_tarihi or date.today().isoformat(),
        "dizin_last_update": dizin.get("last_update"),
        "anime_sayisi": anime_sayisi(dizin),
        "dosya_sayisi": dosya_sayisi,
    }


def manifest_yaz(kok: Path, manifest: Dict[str, Any]) -> Path:
    """Manifesti `arsiv/KAYNAK.json` ile aynı biçimde (2 boşluk, sonda \\n) yaz."""
    yol = Path(kok) / MANIFEST_DOSYASI
    metin = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    atomik_bayt_yaz(yol, metin.encode("utf-8"))
    return yol


# ─────────────────────────────────────────────────────────────────────────────
# İndirme, açma, takas
# ─────────────────────────────────────────────────────────────────────────────
def iptal_denetle(iptal: Any) -> None:
    """``iptal`` `threading.Event` benzeri (``is_set()``) bir nesne ya da None."""
    if iptal is not None and iptal.is_set():
        raise IptalEdildi("işlem iptal edildi")


def _sessizce(fn: Callable[..., Any], *argumanlar: Any) -> None:
    try:
        fn(*argumanlar)
    except Exception:                    # temizlik en iyi çaba; asıl sonucu gölgelemesin
        pass


def iptal_edilebilir(islem: Callable[[], _T], iptal: Any,
                     birak: Optional[Callable[[_T], Any]] = None,
                     aralik: float = IPTAL_ARALIGI) -> _T:
    """``islem()``'i yardımcı thread'de çalıştır; beklerken iptal gelirse hemen dön.

    NEDEN: `session.get(..., stream=True)` bağlanana ya da sunucu ilk baytı
    gönderene kadar BLOKLAR (bağlanma sınırı 15 sn; GitLab paketi anında
    ürettiği için ilk bayt daha da gecikebilir) ve bu sürede iptal
    denetlenemez. Kullanıcı "İptal"e bastıktan sonra düğmeler o kadar süre
    kilitli kalıyordu. Burada istek başka thread'de sürer, çağıran thread
    ``aralik`` saniyede bir iptale bakar ve iptalde `IptalEdildi` fırlatır.

    İptalden SONRA gelen sonucu kimse okumayacak: ``birak(sonuc)`` onu
    temizler (akış yanıtını kapatır; kapatılmazsa bağlantı açık kalır).
    ``iptal`` None ise thread açılmaz, ``islem()`` doğrudan çağrılır.
    """
    if iptal is None:
        return islem()
    kilit = threading.Lock()
    kutu: Dict[str, Any] = {}
    bitti = threading.Event()

    def calis() -> None:
        try:
            sonuc = islem()
        except BaseException as hata:        # çağıran thread'de yeniden fırlatılır
            with kilit:
                kutu["hata"] = hata
            bitti.set()
            return
        with kilit:
            terk = kutu.get("terk", False)
            if not terk:
                kutu["sonuc"] = sonuc
        if terk and birak is not None:
            _sessizce(birak, sonuc)
        bitti.set()

    threading.Thread(target=calis, name="arsiv-baglanti", daemon=True).start()
    while not bitti.wait(aralik):
        if iptal.is_set():
            with kilit:
                kutu["terk"] = True
                gec = kutu.pop("sonuc", _YOK)
            if gec is not _YOK and birak is not None:
                # Tam bu arada gelmiş: kapatma da bekleyebilir (curl akışı
                # kapanırken çalışan aktarımın bitmesini bekliyor), çağıranı
                # tutmasın.
                threading.Thread(target=_sessizce, args=(birak, gec), daemon=True).start()
            raise IptalEdildi("işlem iptal edildi")
    if "hata" in kutu:
        raise kutu["hata"]
    return kutu["sonuc"]


def _parcalar(yanit: Any, parca: int, iptal: Any,
              aralik: float = IPTAL_ARALIGI) -> Iterator[bytes]:
    """Yanıt gövdesinin parçaları; ``iptal`` verildiyse bekleyiş iptalle kesilir.

    `iter_content` bir sonraki parça gelene kadar BLOKLAR. Takılan bir
    bağlantıda bu, curl'ün "hiç veri gelmiyor" sınırına (60 sn) kadar sürer ve
    iptal ancak sonra görülürdü. Parçaları yardımcı thread okuyup kuyruğa
    koyar; bu jeneratör kuyruktan ``aralik`` zaman aşımıyla alır, her boşlukta
    iptale bakar. Tüketici durunca (iptal, disk hatası, bitiş) yardımcıya
    "dur" denir; ağ bekleyişinden döndüğü ilk anda kendiliğinden biter.
    Kuyruk küçük tutuluyor: ağ diskten hızlıysa bellekte yığılma olmasın.
    """
    if iptal is None:
        yield from yanit.iter_content(chunk_size=parca)
        return
    kuyruk: "queue.Queue[Tuple[str, Any]]" = queue.Queue(maxsize=8)
    dur = threading.Event()

    def koy(oge: Tuple[str, Any]) -> bool:
        while not dur.is_set():
            try:
                kuyruk.put(oge, timeout=aralik)
                return True
            except queue.Full:
                continue
        return False

    def uret() -> None:
        try:
            for veri in yanit.iter_content(chunk_size=parca):
                if not koy(("veri", veri)):
                    return
            koy(("bitti", None))
        except BaseException as hata:        # ağ hatası tüketicide fırlatılır
            koy(("hata", hata))

    threading.Thread(target=uret, name="arsiv-indirme", daemon=True).start()
    try:
        while True:
            try:
                tur, deger = kuyruk.get(timeout=aralik)
            except queue.Empty:
                iptal_denetle(iptal)
                continue
            if tur == "veri":
                yield deger
            elif tur == "hata":
                raise deger
            else:
                return
    finally:
        dur.set()


def _uzunluk(basliklar: Any) -> Optional[int]:
    try:
        deger = int((basliklar or {}).get("Content-Length") or 0)
    except (TypeError, ValueError):
        return None
    return deger if deger > 0 else None


def govdeyi_indir(yanit: Any, hedef_dosya: Path, ilerleme: Optional[IlerlemeFn] = None,
                  iptal: Any = None, parca: int = 1 << 16) -> int:
    """Akış kipindeki HTTP yanıtını dosyaya yaz; yazılan bayt sayısını döndür.

    ``ilerleme(indirilen, toplam)`` her parçada çağrılır; ``toplam`` sunucu
    `Content-Length` vermediyse ``None`` (GitLab'ın arşiv ucu paketi anında
    ürettiği için vermiyor — arayüz belirsiz ilerleme göstermeli).
    İptal her parçada ve parça BEKLENİRKEN de denetlenir (bkz. `_parcalar`):
    ~230 MB'lık bir indirme bitmeden, takılan bir bağlantıda da durabilmeli.

    Dosyayı açma/yazma/kapama hataları `YerelDiskHatasi` olur (disk dolu);
    okuma (ağ) hataları olduğu gibi geçer — ikisini karıştırmamak için
    `yerel_disk` yalnızca dosya işlemlerini sarıyor.
    """
    toplam = _uzunluk(getattr(yanit, "headers", None))
    indirilen = 0
    if ilerleme:
        ilerleme(0, toplam)
    with yerel_disk("açılamadı", hedef_dosya):
        fp = open(disk_yolu(hedef_dosya), "wb")  # pylint: disable=consider-using-with
    akis = _parcalar(yanit, parca, iptal)
    try:
        for veri in akis:
            iptal_denetle(iptal)
            if not veri:
                continue
            with yerel_disk("yazılamadı", hedef_dosya):
                fp.write(veri)
            indirilen += len(veri)
            if ilerleme:
                ilerleme(indirilen, toplam)
    except BaseException:
        _sessizce(fp.close)              # asıl hatayı kapama hatası gölgelemesin
        raise
    finally:
        akis.close()                     # yardımcı thread'e "dur" (iptal/hata)
    # Tamponda kalan son parça kapanırken yazılıyor: disk tam burada dolabilir.
    with yerel_disk("yazılamadı", hedef_dosya):
        fp.close()
    iptal_denetle(iptal)
    if toplam is not None and indirilen < toplam:
        raise ArsivHatasi(f"indirme yarım kaldı ({indirilen}/{toplam} bayt)")
    return indirilen


def _uye_yolu(uye: tarfile.TarInfo, ust_desen: str,
              alt_klasor: Optional[str]) -> Optional[Tuple[str, ...]]:
    """Tar üyesinin açılacağı göreli yol; açılmayacaksa ``None``.

    İki kademe:
    1. AD her üye için denetlenir. Mutlak yol, ``..`` ya da beklenmeyen üst
       klasör paketin bozuk/kötü niyetli olduğunu gösterir → tüm paket reddedilir.
    2. TÜR yalnızca açılacak üyeler için denetlenir. GitHub paketi tüm depoyu
       taşıyor; `arsiv/` dışındaki meşru bir sembolik bağ yüzünden yedek kaynağı
       kullanılamaz hâle getirmek yanlış olurdu — o üye zaten diske yazılmıyor.
       Açılacak kısımda bağ (sym/hard) ya da aygıt/FIFO görülürse paket reddedilir:
       bağ, sonraki üyelerin klasör dışına yazılmasının klasik yolu.
    """
    try:
        parcalar = goreli_parcalar(uye.name)
    except ValueError as hata:
        raise GuvensizUye(str(hata)) from hata
    if not fnmatch.fnmatchcase(parcalar[0], ust_desen):
        raise GuvensizUye(f"beklenmeyen üst klasör: {uye.name!r}")

    goreli = parcalar[1:]
    if alt_klasor:
        if not goreli or goreli[0] != alt_klasor:
            return None                  # istenen alt ağacın dışında: atla
        goreli = goreli[1:]
    if not goreli:
        return None                      # üst klasörün / alt klasörün kendisi

    if uye.issym() or uye.islnk():
        raise GuvensizUye(f"bağ üyesi reddedildi: {uye.name!r}")
    if uye.ischr() or uye.isblk() or uye.isfifo() or uye.isdev():
        raise GuvensizUye(f"aygıt üyesi reddedildi: {uye.name!r}")
    if not (uye.isdir() or uye.isfile()):
        raise GuvensizUye(f"desteklenmeyen üye türü: {uye.name!r}")
    if os.name == "nt" and any(":" in p for p in goreli):
        return None                      # NTFS'e yazılamaz (bkz. guvenli_birlestir)
    return goreli


def _uyeyi_yaz(kaynak: Any, yol: Path, parca: int = 1 << 20) -> None:
    """Tar üyesini diske kopyala; okuma ve yazma hatalarını AYRI tut.

    `shutil.copyfileobj` ikisini tek çağrıda yapıyor ve ikisi de `OSError`
    olabiliyor (bozuk gzip de, dolu disk de); kimin düştüğü anlaşılmıyordu.
    Okuma hatası olduğu gibi yükselir (çağıran "paket bozuk" der), yazma
    hatası `YerelDiskHatasi` olur.
    """
    with yerel_disk("yazılamadı", yol):
        fp = open(disk_yolu(yol), "wb")  # pylint: disable=consider-using-with
    try:
        while True:
            veri = kaynak.read(parca)
            if not veri:
                break
            with yerel_disk("yazılamadı", yol):
                fp.write(veri)
    except BaseException:
        _sessizce(fp.close)
        raise
    with yerel_disk("yazılamadı", yol):
        fp.close()


def guvenli_ac(tar_yolu: Path, hedef: Path, *, ust_desen: str,
               alt_klasor: Optional[str] = None, iptal: Any = None,
               azami_bayt: int = AZAMI_ACILMIS_BAYT) -> Dict[str, Any]:
    """Tar(.gz) paketini akış kipinde ``hedef`` klasörüne aç.

    Paket tek bir üst klasör taşımalı (``ust_desen`` ile eşleşen; git arşivleri
    böyle üretilir) ve o klasör soyulur. ``alt_klasor`` verilirse yalnızca
    ``<üst>/<alt_klasor>/`` altı açılır.

    Döner: ``{"dosya": açılan dosya sayısı, "commit": git arşivinin pax
    başlığındaki commit ya da None, "commit_zamani": ilk üyenin mtime'ı}``.
    git arşivlerinde her üyenin mtime'ı commit zamanıdır, ilki yeterli.

    İki hata sınıfı ayrı tutuluyor: paketi OKURKEN çıkan hata (kesik gzip,
    bozuk tar) "paket bozuk" → `ArsivHatasi`; diske YAZARKEN çıkan hata
    (klasör kurulamadı, disk doldu) → `YerelDiskHatasi`. Eskiden ikisi de
    "paket bozuk" diyordu; diski dolan kullanıcıya paketin bozuk olduğu
    söyleniyor ve boşuna yedek kaynak indiriliyordu.
    """
    hedef = Path(hedef)
    with yerel_disk("oluşturulamadı", hedef):
        klasor_kur(hedef)
    dosya = 0
    toplam = 0
    commit_zamani: Optional[float] = None
    try:
        tar = tarfile.open(disk_yolu(tar_yolu), mode="r|*")
    except (tarfile.TarError, OSError) as hata:
        raise ArsivHatasi(f"paket açılamadı: {hata}") from hata
    with tar:
        try:
            for uye in tar:
                iptal_denetle(iptal)
                goreli = _uye_yolu(uye, ust_desen, alt_klasor)
                if commit_zamani is None and uye.mtime:
                    commit_zamani = float(uye.mtime)
                if goreli is None:
                    continue
                # `yol` öneksiz (mesajlar için); diske `disk_yolu` ile gider.
                yol = hedef.joinpath(*goreli)
                if uye.isdir():
                    with yerel_disk("oluşturulamadı", yol):
                        klasor_kur(yol)
                    continue
                toplam += max(0, int(uye.size))
                if toplam > azami_bayt:
                    raise ArsivHatasi("açılan arşiv beklenenden çok büyük; paket reddedildi")
                with yerel_disk("oluşturulamadı", yol.parent):
                    klasor_kur(yol.parent)
                kaynak = tar.extractfile(uye)
                if kaynak is None:
                    raise ArsivHatasi(f"üye okunamadı: {uye.name!r}")
                with kaynak:
                    _uyeyi_yaz(kaynak, yol)
                dosya += 1
        except (tarfile.TarError, EOFError, OSError) as hata:
            # Kesik gzip (indirme yarıda kopmuş) EOFError/ReadError olarak gelir;
            # bozuk gzip başlığı `gzip.BadGzipFile` (bir OSError). Yazma tarafı
            # buraya hiç düşmez: `yerel_disk` onu `YerelDiskHatasi`'na çevirdi
            # ve o bir OSError değil.
            raise ArsivHatasi(f"paket bozuk: {hata}") from hata
        pax = dict(getattr(tar, "pax_headers", None) or {})
    commit = pax.get("comment")
    if commit is not None and not re.fullmatch(r"[0-9a-f]{7,64}", str(commit)):
        commit = None                    # git dışı bir paketin yorumu olabilir
    return {"dosya": dosya, "commit": commit, "commit_zamani": commit_zamani}


def eski_kopya_adi(hedef: Path) -> str:
    """Takasta kenara alınan eski kopyanın ad öneki (`.gitignore`'da da bu kalıp)."""
    return f".{Path(hedef).name}-eski-"


def bag_hedefi(yol: Path) -> Path:
    """``yol`` sembolik bağsa gösterdiği gerçek klasör, değilse kendisi.

    Kullanıcı ~0,5 GB'lık arşivi başka bir diske koyup `cevrimdisi_arsiv`'i
    oraya bağlamış olabilir. Takas bağın KENDİSİNE yapılırsa bağ gizli ada
    taşınıyor, yeni arşiv bağın yerine GERÇEK klasör olarak veri kökünün
    diskine iniyor ve eski veri öbür diskte kalıyordu (bağa `rmtree` hiçbir
    şey yapmıyor). Bağ izlenince yeni arşiv kullanıcının seçtiği diske gider,
    bağ olduğu gibi kalır.

    Bağ olmayan bir yeri gösteriyorsa (disk takılı değil) `YerelDiskHatasi`:
    o yolu kurmak veriyi takılı olmayan diskin bağlama noktasına, yani
    sistem diskine yazardı. Yerel bir sorun olduğu için yedek kaynak denenmez.
    """
    yol = Path(yol)
    if not yol.is_symlink():
        return yol
    gercek = Path(os.path.realpath(yol))
    if not gercek.parent.is_dir():
        raise YerelDiskHatasi(
            f"{yol} sembolik bağı olmayan bir yeri gösteriyor ({gercek}); "
            "bağın bulunduğu disk takılı mı?")
    return gercek


def agaci_sil(yol: Path) -> List[str]:
    """Klasörü sil; SİLİNEMEYEN yolları döndür (boş liste = tamamı silindi).

    `rmtree(ignore_errors=True)` hatayı yutuyor ve geriye yarım yüz megabayt
    görünmez veri kalıyordu. Sembolik bağsa yalnızca bağ kaldırılır —
    `rmtree` bağı reddeder, izlemek ise bağın gösterdiği (kullanıcının) veriyi
    silerdi.
    """
    yol = Path(yol)
    if os.path.islink(disk_yolu(yol)):
        try:
            os.unlink(disk_yolu(yol))
        except OSError:
            return [str(yol)]
        return []
    silinemeyen: List[str] = []

    def _hata(_fn: Any, hatali: Any, _bilgi: Any) -> None:
        silinemeyen.append(gorunen_yol(hatali))

    # Önekli kökten silinir: `rmtree` alt yolları kökü birleştirerek kuruyor,
    # öneksiz kökte uzun adlı dosyalar Windows'ta silinemeyip kalırdı.
    # 3.12 `onerror`'ı `onexc` lehine kullanımdan kaldırdı; iki geri çağrı da
    # (fonksiyon, yol, hata) alıyor, yalnızca argüman adı farklı.
    if sys.version_info >= (3, 12):
        shutil.rmtree(disk_yolu(yol), onexc=_hata)   # pylint: disable=unexpected-keyword-arg
    else:
        shutil.rmtree(disk_yolu(yol), onerror=_hata)
    return silinemeyen


def yerine_koy(yeni: Path, hedef: Path) -> List[str]:
    """``yeni`` klasörünü ``hedef``'in yerine koy; eskisi yalnızca başarıda silinir.

    Sıra: eski → kenara (aynı üst klasörde yeniden adlandırma), yeni → hedef.
    İkinci adım düşerse eski geri konur; kullanıcı arşivsiz kalmaz. İkisi de
    aynı dosya sisteminde yeniden adlandırma olduğu için yarım kopya görülmez.

    ``hedef`` sembolik bağsa bağ izlenir (bkz. `bag_hedefi`): takas bağın
    gösterdiği klasörde yapılır, bağ yerinde kalır. ``yeni`` o klasörle aynı
    dosya sisteminde olmalı (`paketten_kur` geçiciyi zaten onun yanına kurar).

    Döner: eski kopyadan silinemeyen yollar. Takas başarılı olduğu için bu
    durumda istisna fırlatılmaz (yeni arşiv kullanılabilir durumda); kalan
    `.<ad>-eski-*` klasörü `sources.animedepo.arsiv_durumu` bulup Ayarlar
    sayfasında gösteriyor.
    """
    yeni, hedef = Path(yeni), bag_hedefi(Path(hedef))
    eski: Optional[Path] = None
    if os.path.lexists(disk_yolu(hedef)):
        eski = hedef.with_name(f"{eski_kopya_adi(hedef)}{uuid.uuid4().hex[:8]}")
        os.replace(disk_yolu(hedef), disk_yolu(eski))
    try:
        os.replace(disk_yolu(yeni), disk_yolu(hedef))
    except BaseException:
        if eski is not None:
            os.replace(disk_yolu(eski), disk_yolu(hedef))
        raise
    if eski is not None:
        return agaci_sil(eski)
    return []


def paketten_kur(yanit: Any, hedef: Path, *, ust_desen: str,
                 alt_klasor: Optional[str] = None, kaynak: str = "", dal: str = "",
                 ilerleme: Optional[IlerlemeFn] = None, iptal: Any = None,
                 asama: Optional[AsamaFn] = None) -> Path:
    """Akıştaki tar.gz paketini indir, güvenle aç, doğrula ve ``hedef``'e koy.

    Her ara ürün ``hedef``'in YANINDAKİ geçici bir klasörde durur (aynı dosya
    sistemi → son takas atomik yeniden adlandırma). Başarı, hata ya da iptal:
    geçici klasör her durumda silinir, ``hedef``'e ancak doğrulama geçtikten
    sonra dokunulur.

    Paket kendi `KAYNAK.json`'ını taşımıyorsa (GitLab paketi) git arşivinin
    commit bilgisinden bir tane yazılır; arayüz hangi sürümün inik olduğunu
    iki kaynakta da aynı dosyadan okuyabilir.

    ``asama(ad)`` her aşamanın BAŞINDA çağrılır: `ASAMA_INDIRME`,
    `ASAMA_ACMA`, `ASAMA_YERLESTIRME` (bkz. modül sabitleri).

    ``hedef`` sembolik bağsa arşiv bağın gösterdiği klasöre kurulur ve bağ
    korunur (bkz. `bag_hedefi`); geçici klasör de oraya, aynı diske açılır.
    Diske yazılamayan her adım `YerelDiskHatasi` fırlatır (bkz. o sınıf).
    """
    hedef = Path(hedef)
    gercek = bag_hedefi(hedef)
    with yerel_disk("oluşturulamadı", gercek.parent):
        klasor_kur(gercek.parent)
        # `mkdtemp` önekli yol döndürüyor; yalnızca ADI alınıyor ki `gecici`
        # (ve ondan türeyen her yol) mesajlarda öneksiz görünsün.
        gecici = gercek.parent / Path(tempfile.mkdtemp(
            prefix=f".{gercek.name}-indirme-", dir=disk_yolu(gercek.parent))).name
    try:
        paket = gecici / "paket.tar.gz"
        if asama:
            asama(ASAMA_INDIRME)
        govdeyi_indir(yanit, paket, ilerleme=ilerleme, iptal=iptal)
        acilan = gecici / "acilan"
        if asama:
            asama(ASAMA_ACMA)
        bilgi = guvenli_ac(paket, acilan, ust_desen=ust_desen,
                           alt_klasor=alt_klasor, iptal=iptal)
        with yerel_disk("silinemedi", paket):
            os.remove(disk_yolu(paket))  # takastan önce yer aç (~230 MB)
        arsivi_dogrula(acilan)
        if not os.path.isfile(disk_yolu(acilan / MANIFEST_DOSYASI)):
            zaman = bilgi.get("commit_zamani")
            with yerel_disk("yazılamadı", acilan / MANIFEST_DOSYASI):
                manifest_yaz(acilan, manifest_uret(
                    acilan, kaynak=kaynak, dal=dal, commit=bilgi.get("commit"),
                    commit_tarihi=(datetime.fromtimestamp(zaman, tz=timezone.utc)
                                   .isoformat() if zaman else None),
                    dosya_sayisi=bilgi["dosya"] + 1,
                ))
        iptal_denetle(iptal)
        if asama:
            asama(ASAMA_YERLESTIRME)
        # Takas düşerse (Windows'ta klasör açık: EACCES/EBUSY) eski arşiv
        # zaten geri konmuş oluyor; kullanıcıya diski anlatan hata gider.
        with yerel_disk("yerine konamadı", gercek):
            yerine_koy(acilan, gercek)
        return hedef
    finally:
        shutil.rmtree(disk_yolu(gecici), ignore_errors=True)


__all__ = [
    "ArsivHatasi", "GuvensizUye", "IptalEdildi", "YerelDiskHatasi",
    "DIZIN_DOSYASI", "MANIFEST_DOSYASI", "MANIFEST_ANAHTARLARI",
    "ASAMA_BAGLANMA", "ASAMA_INDIRME", "ASAMA_ACMA", "ASAMA_YERLESTIRME",
    "IPTAL_ARALIGI",
    "goreli_parcalar", "guvenli_birlestir", "atomik_bayt_yaz",
    "windows_uzun_yol", "disk_yolu", "gorunen_yol", "klasor_kur",
    "arsivi_dogrula", "arsiv_gecerli_mi", "anime_sayisi",
    "manifest_uret", "manifest_yaz", "disk_mesaji", "yerel_disk",
    "iptal_denetle", "iptal_edilebilir", "govdeyi_indir", "guvenli_ac",
    "eski_kopya_adi", "bag_hedefi", "agaci_sil", "yerine_koy", "paketten_kur",
]
