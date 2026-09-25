"""Kapak görselleri — tek indirme yolu, bellek + disk önbelleği.

Eskiden keşif, arama, izleme listesi ve detay sayfası görseli kendi
`requests.get(url, timeout=10)` çağrısıyla indiriyordu ve hiçbir şey
saklanmıyordu: "Yenile"ye her basış, sayfaya her dönüş bütün posterleri
yeniden indiriyordu; çevrimdışıyken hiçbiri görünmüyordu. Üstelik bu işler
aramayla, bölüm yüklemeyle aynı global havuzda koşuyordu (bkz.
`workers.gorsel_havuzu`): bir Trend sayfasının 12-24 yavaş posteri, ardından
başlatılan aramayı saniyelerce kuyrukta bekletiyordu (ölçüldü: 4.5 sn).

Bu modül Qt'SİZ: arka plan thread'inden çağrılır, bayt döndürür. `QPixmap`
yalnızca GUI thread'inde kurulabildiği için görselin geçerliliği burada
sihirli baytlarla (PNG/JPEG/GIF/WEBP/BMP imzası) sınanıyor.
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

# Tek isteğin süresi. Poster bir süs; 10 sn'den uzun bekleyen sunucu zaten
# kullanıcının gözünde "görsel yok" demek.
GORSEL_ZAMAN_ASIMI = 10

# Bellek önbelleği (bayt). Poster başına ~30-150 KB: 32 MB birkaç yüz poster
# eder, yani keşif sekmeleri arasında gidip gelmek hiç ağa çıkmaz.
BELLEK_SINIRI = 32 * 1024 * 1024

# Disk önbelleği (bayt). Aşılınca en eski ERİŞİLENLER silinir (LRU: okuma
# dosyanın mtime'ını tazeliyor). Sınır yoksa klasör sonsuza dek büyürdü.
DISK_SINIRI = 200 * 1024 * 1024
# Temizlikte sınırın bu oranına inilir; her yazışta tekrar temizlik olmasın.
DISK_HEDEF_ORANI = 0.9

# Tek görselin üst sınırı. Bozuk ya da kötü niyetli bir sunucunun yüzlerce
# megabaytlık "görseli" önbelleği tek başına doldurmasın.
AZAMI_GORSEL = 8 * 1024 * 1024

DISK_KLASORU = "gorsel_onbellek"

# turkanime.tv kapandı; arşivdeki "Resim" adresleri (turkanime.co/.tv) ölü.
# Oraya giden istek her seferinde zaman aşımını bekler — hiç gitmiyoruz.
_OLU_KONAKLAR = ("turkanime.",)

_IMZALAR = (
    b"\x89PNG\r\n\x1a\n",       # PNG
    b"\xff\xd8\xff",            # JPEG
    b"GIF87a", b"GIF89a",       # GIF
    b"BM",                      # BMP
)

_bellek: "OrderedDict[str, bytes]" = OrderedDict()
_bellek_boyutu = 0
_kilit = threading.Lock()
# Disk kullanımının tahmini; `None` = henüz ölçülmedi (ilk yazışta ölçülür).
_disk_boyutu: Optional[int] = None


def onbellek_dizini() -> Path:
    """Disk önbelleği klasörü — arşiv önbelleğiyle aynı veri kökünde.

    Kök kuralı (depodan çalışırken depo kökü, paketli uygulamada
    ``~/Turkanime``) `animedepo.veri_koku`'nda; ikinci bir kural yazmak
    iki önbelleğin ayrı yerlere düşmesi demekti. Testler bu fonksiyonu
    geçici klasöre yönlendirir (bkz. conftest).
    """
    from ...sources.animedepo import veri_koku
    return veri_koku() / DISK_KLASORU


def gorsel_mi(veri: Optional[bytes]) -> bool:
    """Baytlar tanınan bir görsel biçimi mi? (Qt'siz, imzadan)

    Sunucular hata sayfasını da 200 ile dönebiliyor (Cloudflare, "resim
    bulunamadı" HTML'i). Onu önbelleğe yazmak, posteri kalıcı olarak
    bozmak demekti: bir sonraki açılışta da diskten bozuk bayt okunurdu.
    """
    if not veri or len(veri) < 12:
        return False
    if veri[:4] == b"RIFF" and veri[8:12] == b"WEBP":
        return True
    return any(veri.startswith(imza) for imza in _IMZALAR)


def olu_adres_mi(url: str) -> bool:
    """Adres, kapanan turkanime sitesine mi gidiyor?"""
    try:
        konak = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return True
    return any(parca in konak for parca in _OLU_KONAKLAR)


def _anahtar(url: str) -> str:
    # URL dosya adı olamaz (/, ?, uzunluk); sha1 sabit uzunlukta ve çakışmasız.
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


# ── Bellek ──────────────────────────────────────────────────────────────────
def bellekten(url: str) -> Optional[bytes]:
    """Bellekte varsa baytlar (en son kullanılan olarak işaretlenir)."""
    with _kilit:
        veri = _bellek.get(url)
        if veri is not None:
            _bellek.move_to_end(url)
        return veri


def _bellege_yaz(url: str, veri: bytes) -> None:
    global _bellek_boyutu
    with _kilit:
        eski = _bellek.pop(url, None)
        if eski is not None:
            _bellek_boyutu -= len(eski)
        _bellek[url] = veri
        _bellek_boyutu += len(veri)
        while _bellek_boyutu > BELLEK_SINIRI and len(_bellek) > 1:
            _, atilan = _bellek.popitem(last=False)
            _bellek_boyutu -= len(atilan)


def bellegi_temizle() -> None:
    """Bellek önbelleğini boşalt (testler ve ayar değişimi için)."""
    global _bellek_boyutu, _disk_boyutu
    with _kilit:
        _bellek.clear()
        _bellek_boyutu = 0
        _disk_boyutu = None


# ── Disk ────────────────────────────────────────────────────────────────────
def _diskten(url: str) -> Optional[bytes]:
    yol = onbellek_dizini() / _anahtar(url)
    try:
        veri = yol.read_bytes()
    except OSError:
        return None
    if not gorsel_mi(veri):
        # Yarım yazılmış ya da elle bozulmuş dosya: sil, yeniden indirilsin.
        try:
            yol.unlink()
        except OSError:
            pass
        return None
    try:
        os.utime(yol)           # LRU: okunan dosya "yeni" sayılsın
    except OSError:
        pass
    return veri


def _diske_yaz(url: str, veri: bytes) -> None:
    """Atomik yaz (geçici dosya + `os.replace`), sonra gerekirse temizle.

    Disk hatası (salt okunur klasör, dolu disk) posteri göstermeye engel
    değil: bayt zaten bellekte ve çağırana dönüyor, yalnızca kalıcı olmuyor.
    """
    global _disk_boyutu
    klasor = onbellek_dizini()
    hedef = klasor / _anahtar(url)
    gecici = hedef.with_name(f"{hedef.name}.{threading.get_ident()}.tmp")
    try:
        klasor.mkdir(parents=True, exist_ok=True)
        gecici.write_bytes(veri)
        os.replace(gecici, hedef)
    except OSError:
        try:
            gecici.unlink()
        except OSError:
            pass
        return
    with _kilit:
        if _disk_boyutu is None:
            _disk_boyutu = _klasor_boyutu(klasor)
        else:
            _disk_boyutu += len(veri)
        tasti = _disk_boyutu > DISK_SINIRI
    if tasti:
        _diski_buda(klasor)


def _klasor_boyutu(klasor: Path) -> int:
    toplam = 0
    try:
        for giris in os.scandir(klasor):
            try:
                if giris.is_file():
                    toplam += giris.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return toplam


def _diski_buda(klasor: Path) -> None:
    """En eski erişilenleri sil, sınırın `DISK_HEDEF_ORANI`'na inene dek.

    Tam tarama yalnızca sınır aşılınca yapılıyor; her yazışta binlerce
    dosyayı `stat`'lamak, poster başına milisaniyeler demekti.
    """
    global _disk_boyutu
    dosyalar = []
    try:
        for giris in os.scandir(klasor):
            try:
                if giris.is_file():
                    bilgi = giris.stat()
                    dosyalar.append((bilgi.st_mtime, bilgi.st_size, giris.path))
            except OSError:
                continue
    except OSError:
        return
    toplam = sum(boyut for _, boyut, _ in dosyalar)
    hedef = int(DISK_SINIRI * DISK_HEDEF_ORANI)
    for _, boyut, yol in sorted(dosyalar):
        if toplam <= hedef:
            break
        try:
            os.unlink(yol)
            toplam -= boyut
        except OSError:
            continue
    with _kilit:
        _disk_boyutu = toplam


# ── Tek giriş noktası ───────────────────────────────────────────────────────
def gorsel_getir(url: Optional[str]) -> Optional[bytes]:
    """Görsel baytları: bellek → disk → ağ. Alınamazsa ``None``.

    ARKA PLAN thread'inden çağrılmalı (ağ isteği atabilir). Yalnızca 200
    dönen ve gerçekten görsel olan yanıt önbelleğe girer: hata sayfası ya da
    geçici bir 503 kalıcı "bozuk poster"e dönüşmesin.
    """
    if not url or not isinstance(url, str):
        return None
    if not url.lower().startswith(("http://", "https://")):
        return None
    if olu_adres_mi(url):
        return None

    veri = bellekten(url)
    if veri is not None:
        return veri
    veri = _diskten(url)
    if veri is not None:
        _bellege_yaz(url, veri)
        return veri

    try:
        import requests
        yanit = requests.get(url, timeout=GORSEL_ZAMAN_ASIMI)
    except Exception:
        return None
    if getattr(yanit, "status_code", None) != 200:
        return None
    veri = getattr(yanit, "content", None) or b""
    if len(veri) > AZAMI_GORSEL or not gorsel_mi(veri):
        return None
    _bellege_yaz(url, veri)
    _diske_yaz(url, veri)
    return veri


__all__ = ["gorsel_getir", "gorsel_mi", "olu_adres_mi", "bellekten",
           "bellegi_temizle", "onbellek_dizini", "BELLEK_SINIRI",
           "DISK_SINIRI", "GORSEL_ZAMAN_ASIMI"]
