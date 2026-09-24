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

import fnmatch
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

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


class ArsivHatasi(RuntimeError):
    """Arşiv indirilemedi, açılamadı ya da geçersiz çıktı."""


class GuvensizUye(ArsivHatasi):
    """Tar paketinde klasör dışına yazacak / bağ / aygıt üyesi var."""


class IptalEdildi(ArsivHatasi):
    """Kullanıcı işlemi iptal etti."""


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
    """
    parcalar = goreli_parcalar(yol)
    if os.name == "nt" and any(":" in p for p in parcalar):
        raise ValueError(f"Windows'ta yazılamayan arşiv yolu: {yol!r}")
    return Path(kok).joinpath(*parcalar)


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
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gecici = hedef.with_name(f".{hedef.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(gecici, "wb") as fp:
            fp.write(veri)
        os.replace(gecici, hedef)
    except BaseException:
        try:
            os.remove(gecici)
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
        with open(yol, encoding="utf-8") as fp:
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
        dosya_sayisi = sum(1 for p in kok.rglob("*")
                           if p.is_file() and p != kok / MANIFEST_DOSYASI) + 1
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
    İptal her parçada denetlenir: 100 MB'lık bir indirme bitmeden durabilmeli.
    """
    toplam = _uzunluk(getattr(yanit, "headers", None))
    indirilen = 0
    if ilerleme:
        ilerleme(0, toplam)
    with open(hedef_dosya, "wb") as fp:
        for veri in yanit.iter_content(chunk_size=parca):
            iptal_denetle(iptal)
            if not veri:
                continue
            fp.write(veri)
            indirilen += len(veri)
            if ilerleme:
                ilerleme(indirilen, toplam)
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
    """
    hedef = Path(hedef)
    hedef.mkdir(parents=True, exist_ok=True)
    dosya = 0
    toplam = 0
    commit_zamani: Optional[float] = None
    try:
        tar = tarfile.open(tar_yolu, mode="r|*")
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
                yol = hedef.joinpath(*goreli)
                if uye.isdir():
                    yol.mkdir(parents=True, exist_ok=True)
                    continue
                toplam += max(0, int(uye.size))
                if toplam > azami_bayt:
                    raise ArsivHatasi("açılan arşiv beklenenden çok büyük; paket reddedildi")
                yol.parent.mkdir(parents=True, exist_ok=True)
                kaynak = tar.extractfile(uye)
                if kaynak is None:
                    raise ArsivHatasi(f"üye okunamadı: {uye.name!r}")
                with kaynak, open(yol, "wb") as fp:
                    shutil.copyfileobj(kaynak, fp, 1 << 20)
                dosya += 1
        except (tarfile.TarError, EOFError, OSError) as hata:
            # Kesik gzip (indirme yarıda kopmuş) EOFError/ReadError olarak gelir.
            raise ArsivHatasi(f"paket bozuk: {hata}") from hata
        pax = dict(getattr(tar, "pax_headers", None) or {})
    commit = pax.get("comment")
    if commit is not None and not re.fullmatch(r"[0-9a-f]{7,64}", str(commit)):
        commit = None                    # git dışı bir paketin yorumu olabilir
    return {"dosya": dosya, "commit": commit, "commit_zamani": commit_zamani}


def yerine_koy(yeni: Path, hedef: Path) -> None:
    """``yeni`` klasörünü ``hedef``'in yerine koy; eskisi yalnızca başarıda silinir.

    Sıra: eski → kenara (aynı üst klasörde yeniden adlandırma), yeni → hedef.
    İkinci adım düşerse eski geri konur; kullanıcı arşivsiz kalmaz. İkisi de
    aynı dosya sisteminde yeniden adlandırma olduğu için yarım kopya görülmez.
    """
    yeni, hedef = Path(yeni), Path(hedef)
    eski: Optional[Path] = None
    if hedef.exists() or hedef.is_symlink():
        eski = hedef.with_name(f".{hedef.name}-eski-{uuid.uuid4().hex[:8]}")
        os.replace(hedef, eski)
    try:
        os.replace(yeni, hedef)
    except BaseException:
        if eski is not None:
            os.replace(eski, hedef)
        raise
    if eski is not None:
        shutil.rmtree(eski, ignore_errors=True)


def paketten_kur(yanit: Any, hedef: Path, *, ust_desen: str,
                 alt_klasor: Optional[str] = None, kaynak: str = "", dal: str = "",
                 ilerleme: Optional[IlerlemeFn] = None, iptal: Any = None) -> Path:
    """Akıştaki tar.gz paketini indir, güvenle aç, doğrula ve ``hedef``'e koy.

    Her ara ürün ``hedef``'in YANINDAKİ geçici bir klasörde durur (aynı dosya
    sistemi → son takas atomik yeniden adlandırma). Başarı, hata ya da iptal:
    geçici klasör her durumda silinir, ``hedef``'e ancak doğrulama geçtikten
    sonra dokunulur.

    Paket kendi `KAYNAK.json`'ını taşımıyorsa (GitLab paketi) git arşivinin
    commit bilgisinden bir tane yazılır; arayüz hangi sürümün inik olduğunu
    iki kaynakta da aynı dosyadan okuyabilir.
    """
    hedef = Path(hedef)
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gecici = Path(tempfile.mkdtemp(prefix=f".{hedef.name}-indirme-", dir=hedef.parent))
    try:
        paket = gecici / "paket.tar.gz"
        govdeyi_indir(yanit, paket, ilerleme=ilerleme, iptal=iptal)
        acilan = gecici / "acilan"
        bilgi = guvenli_ac(paket, acilan, ust_desen=ust_desen,
                           alt_klasor=alt_klasor, iptal=iptal)
        paket.unlink()                   # takastan önce yer aç (~100 MB)
        arsivi_dogrula(acilan)
        if not (acilan / MANIFEST_DOSYASI).is_file():
            zaman = bilgi.get("commit_zamani")
            manifest_yaz(acilan, manifest_uret(
                acilan, kaynak=kaynak, dal=dal, commit=bilgi.get("commit"),
                commit_tarihi=(datetime.fromtimestamp(zaman, tz=timezone.utc)
                               .isoformat() if zaman else None),
                dosya_sayisi=bilgi["dosya"] + 1,
            ))
        iptal_denetle(iptal)
        yerine_koy(acilan, hedef)
        return hedef
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


__all__ = [
    "ArsivHatasi", "GuvensizUye", "IptalEdildi",
    "DIZIN_DOSYASI", "MANIFEST_DOSYASI", "MANIFEST_ANAHTARLARI",
    "goreli_parcalar", "guvenli_birlestir", "atomik_bayt_yaz",
    "arsivi_dogrula", "arsiv_gecerli_mi", "anime_sayisi",
    "manifest_uret", "manifest_yaz",
    "iptal_denetle", "govdeyi_indir", "guvenli_ac", "yerine_koy", "paketten_kur",
]
