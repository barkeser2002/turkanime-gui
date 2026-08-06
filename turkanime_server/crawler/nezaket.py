"""Nezaket bekçisi — kaynaklara nasıl davrandığımızın tek yeri.

Beş kural, beşi de burada zorlanır:

1. **Kaynak başına eşzamanlılık 1.** Kaynak başına bir kilit; aynı siteye iki
   isteği asla üst üste bindirmeyiz. Kaynaklar arası paralellik serbest.
2. **Koşu başına tek süreç.** 1. kural `threading.Lock` ile, yani yalnızca süreç
   *içinde* geçerli. Üst üste binen cron turları için işletim sistemi kilidi
   (`KosuKilidi`) gerekiyor.
3. **Gecikme + jitter.** İki istek arası taban gecikme, ± jitter ile dalgalanır.
   Saniyesi saniyesine düzenli istek dizisi bot imzasıdır; WAF'lar tam olarak
   bunu arar.
4. **Üstel geri çekilme.** Hata alan kaynak katlanarak artan bir süre dinlenir.
   Israr etmek karşı tarafta ban, bizde de boşa dönen kuyruk turu demek. Hangi
   hatanın geri çekilmeyi hak ettiğine `hata_turu()` karar verir.
5. **Günlük istek tavanı.** Kaynak × gün sayacı `DurumDeposu`da tutulur, yani
   süreç yeniden başlasa da tavan sıfırlanmaz.

Bekçi *bloklamayı* dışarı sızdırmaz: dinlenmedeki ya da tavanı dolmuş kaynak
için istisna fırlatır, böylece tarayıcı başka kaynağa geçebilir. Yalnızca kısa
istek-arası gecikme ve (tarayıcı isterse) geri çekilme için gerçekten uyur.
"""
from __future__ import annotations

import os
import random
import re
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Dict, Iterator, Optional

from .ayarlar import NezaketAyarlari
from .durum import DurumDeposu

# ── Hata sınıfları ──────────────────────────────────────────────────────────
# Üçü de farklı davranış ister; hepsini "hata" saymak tek bir 503'ün koşuyu
# baştan sona düşürmesine yol açıyordu.
GECICI = "gecici"          # 5xx / timeout / bağlantı — biraz sonra tekrar dene
KALICI = "kalici"          # 404 / 410 — kayıt yok, tekrar denemek boşuna
ENGELLENME = "engellenme"  # 403 / 429 / captcha — kaynağın tamamı bize kapalı

# Durum kodunu yalnızca *bağlamıyla* birlikte ara: adaptörler hata metnine
# anime adı da yazıyor ve "Naruto 502" gibi bir başlık kod sanılabilirdi.
_DURUM_DESENI = re.compile(
    r"(?:HTTP|HTTPS|status(?:[ _-]?code)?|durum|kod|code|error)\D{0,4}([1-5]\d{2})",
    re.I,
)

# Engellenmenin durum kodu olmayan hâlleri: WAF sayfası 200 ile de gelebilir.
_ENGEL_IZLERI = (
    "captcha", "cloudflare", "access denied", "forbidden", "rate limit",
    "too many requests", "banned", "blocked", "erişim engellendi",
)


class NezaketEngeli(Exception):
    """Kaynak şu an istek almaya uygun değil.

    ``kalan`` — saniye cinsinden tahmini bekleme (tavan için gün sonuna kadar).
    """

    def __init__(self, kaynak: str, kalan: float, sebep: str):
        super().__init__(f"{kaynak}: {sebep} ({kalan:.0f} sn)")
        self.kaynak = kaynak
        self.kalan = kalan
        self.sebep = sebep


class GunlukTavanAsildi(NezaketEngeli):
    """Kaynağın günlük istek tavanı doldu."""


class KaynakDinlenmede(NezaketEngeli):
    """Kaynak üstel geri çekilme yüzünden dinleniyor."""


def _bugun() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def http_durumu(e: BaseException) -> Optional[int]:
    """İstisnadan HTTP durum kodunu çıkar; bulunamazsa ``None``."""
    for aday in (getattr(e, "response", None), e):
        for alan in ("status_code", "status"):
            kod = getattr(aday, alan, None)
            if isinstance(kod, int) and 100 <= kod <= 599:
                return kod
    eslesme = _DURUM_DESENI.search(str(e))
    return int(eslesme.group(1)) if eslesme else None


def hata_turu(e: BaseException) -> str:
    """Adaptör istisnasını geçici / kalıcı / engellenme diye ayır.

    Bilinmeyen istisna **geçici** sayılır: adaptörlerin çoğu ağ hatasını kendi
    tipine sarıyor ve kalıcı sanıp görevi kapatmak arşivden veri düşürür. Yanlış
    tarafta durmanın bedeli asimetrik — geçici sanılan kalıcı hata yalnızca
    birkaç boş deneme, kalıcı sanılan geçici hata ise kayıp bölüm demek.
    """
    metin = str(e).lower()
    if any(iz in metin for iz in _ENGEL_IZLERI):
        return ENGELLENME

    kod = http_durumu(e)
    if kod is not None:
        if kod in (401, 403, 407, 429):
            return ENGELLENME
        if kod in (408, 425) or 500 <= kod <= 599:
            return GECICI
        if 400 <= kod <= 499:
            # İstek bizden kaynaklı ve tekrarı aynı cevabı verir.
            return KALICI
        return GECICI

    # Durum kodu ve engellenme izi yoksa geriye ağ arızası ihtimali kalıyor
    # (timeout, bağlantı kopması, adaptörün kendi sardığı hata): geçici.
    return GECICI


class NezaketBekcisi:
    """Kaynak başına hız/hata/tavan yönetimi."""

    def __init__(
        self,
        ayarlar: NezaketAyarlari,
        depo: DurumDeposu,
        *,
        uyku: Callable[[float], None] = time.sleep,
        simdi: Callable[[], float] = time.monotonic,
        gun: Callable[[], str] = _bugun,
        rastgele: Callable[[], float] = random.random,
    ):
        self.ayarlar = ayarlar
        self.depo = depo
        self._uyku = uyku
        self._simdi = simdi
        self._gun = gun
        self._rastgele = rastgele

        self._kilitler: Dict[str, Lock] = {}
        self._kilit_kilidi = Lock()
        self._son_istek: Dict[str, float] = {}
        self._ardisik_hata: Dict[str, int] = {}
        self._dinlenme_sonu: Dict[str, float] = {}

    # ── Yardımcılar ─────────────────────────────────────────────────────────
    def _kaynak_kilidi(self, kaynak: str) -> Lock:
        with self._kilit_kilidi:
            kilit = self._kilitler.get(kaynak)
            if kilit is None:
                kilit = Lock()
                self._kilitler[kaynak] = kilit
            return kilit

    def _gecikme(self) -> float:
        """Taban gecikmeyi ± jitter ile dalgalandır (asla negatif değil)."""
        a = self.ayarlar
        sapma = a.gecikme * a.jitter * (2.0 * self._rastgele() - 1.0)
        return max(0.0, a.gecikme + sapma)

    def kalan_kota(self, kaynak: str) -> int:
        """Bugün bu kaynağa kaç istek daha yapılabilir."""
        return max(0, self.ayarlar.gunluk_tavan - self.depo.sayac_oku(kaynak, self._gun()))

    def dinlenme_kalani(self, kaynak: str) -> float:
        sonu = self._dinlenme_sonu.get(kaynak)
        if sonu is None:
            return 0.0
        return max(0.0, sonu - self._simdi())

    def uygun_mu(self, kaynak: str) -> bool:
        """İstisna fırlatmadan hızlı kontrol — tarayıcı sıralaması için."""
        return self.kalan_kota(kaynak) > 0 and self.dinlenme_kalani(kaynak) <= 0

    # ── Hata / başarı geri bildirimi ────────────────────────────────────────
    def hata_bildir(self, kaynak: str, tur: str = GECICI) -> float:
        """Hatayı kaydet, geri çekilme uygula, beklenecek süreyi döndür.

        Kalıcı hatada kaynak **dinlendirilmez**: 404 kaydın yokluğunu söyler,
        kaynağın sağlığını değil; siteyi cezalandırmak diğer görevleri de
        gereksiz yere bekletirdi.
        """
        if tur == KALICI:
            return 0.0
        a = self.ayarlar
        n = self._ardisik_hata.get(kaynak, 0) + 1
        self._ardisik_hata[kaynak] = n
        if tur == ENGELLENME:
            # Engellenmede ısrar banı kalıcılaştırır; en uzun süreyi uygula.
            bekle = a.azami_geri_cekilme
        else:
            bekle = min(a.geri_cekilme_tabani * (2 ** (n - 1)), a.azami_geri_cekilme)
            if n >= a.ardisik_hata_siniri:
                # Sınırı aşan kaynak tam geri çekilme süresi kadar dinlenir.
                bekle = a.azami_geri_cekilme
        self._dinlenme_sonu[kaynak] = self._simdi() + bekle
        return bekle

    def basari_bildir(self, kaynak: str) -> None:
        self._ardisik_hata.pop(kaynak, None)
        self._dinlenme_sonu.pop(kaynak, None)

    def dinlenmeyi_bekle(self, kaynak: str) -> float:
        """Kaynağın geri çekilmesi bitene kadar uyu; uyunan süreyi döndür.

        Tek kaynaklı bir koşuda alternatif yok: ya kısa bir süre beklenir ya da
        kuyrukta hazır iş varken tur terk edilir. İkincisi günlük kotayı çöpe
        atıyordu.
        """
        kalan = self.dinlenme_kalani(kaynak)
        if kalan > 0:
            self._uyku(kalan)
        return max(0.0, kalan)

    # ── Kapı ────────────────────────────────────────────────────────────────
    @contextmanager
    def kapi(self, kaynak: str) -> Iterator[None]:
        """Kaynağa tek bir istek yapmak için izin al.

        Raises:
            GunlukTavanAsildi — bugünlük kota bitti.
            KaynakDinlenmede  — üstel geri çekilme sürüyor.
        """
        kalan = self.dinlenme_kalani(kaynak)
        if kalan > 0:
            raise KaynakDinlenmede(kaynak, kalan, "geri çekilme sürüyor")
        if self.kalan_kota(kaynak) <= 0:
            raise GunlukTavanAsildi(kaynak, self._gun_sonuna_kalan(), "günlük tavan doldu")

        kilit = self._kaynak_kilidi(kaynak)
        # Eşzamanlılık 1: kilit alınana kadar bekle. Kaynaklar arası paralellik
        # etkilenmez çünkü kilit kaynağa özel.
        with kilit:
            onceki = self._son_istek.get(kaynak)
            if onceki is not None:
                gereken = self._gecikme() - (self._simdi() - onceki)
                if gereken > 0:
                    self._uyku(gereken)
            try:
                yield
            finally:
                # Başarısız istek de karşı tarafa yük bindirdi: sayaç ve son
                # istek zamanı her hâlükârda güncellenir.
                self._son_istek[kaynak] = self._simdi()
                self.depo.sayac_artir(kaynak, self._gun())

    @staticmethod
    def _gun_sonuna_kalan(simdi: Optional[datetime] = None) -> float:
        now = simdi or datetime.now(timezone.utc)
        return (86400.0
                - now.hour * 3600.0 - now.minute * 60.0 - now.second)


# ── Süreçler arası koşu kilidi ──────────────────────────────────────────────
# Yukarıdaki dört kural yalnızca **tek süreç** içinde geçerli: `_kaynak_kilidi`
# bir `threading.Lock`. README ise 30 dakikalık cron öneriyor ve tam tarama
# saatler sürüyor; turlar üst üste bindiğinde kaynak başına eşzamanlılık 1
# garantisi çöküyor (ölçüm: 3 sn arayla başlatılan iki süreçte ardışık istek
# aralığı 2.00 sn yerine 0.87 sn). Aynı duruma yazan ikinci koşu ayrıca kuyruğu
# da bozar: iki süreç aynı görevi "işlemde" görmeden çekebilir.
class KosuZatenSuruyor(RuntimeError):
    """Aynı durum dosyası üzerinde başka bir tarama koşusu sürüyor."""

    def __init__(self, yol: Path | str, sahip: str = ""):
        ek = f" (sahip: {sahip})" if sahip else ""
        super().__init__(
            f"başka bir tarama koşusu sürüyor{ek}; kilit: {yol}. "
            "Bu tur atlanıyor — çakışan koşular nezaket garantisini bozar")
        self.yol = Path(yol)
        self.sahip = sahip


def _kilit_dene(fd: int) -> bool:
    """Dosyanın ilk baytını bloklamadan kilitle."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt  # pylint: disable=import-outside-toplevel,import-error
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            # fcntl yalnızca POSIX'te var; Windows'ta bu dal hiç çalışmaz.
            import fcntl  # pylint: disable=import-outside-toplevel,import-error
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _kilit_coz(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt  # pylint: disable=import-outside-toplevel,import-error
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            # fcntl yalnızca POSIX'te var; Windows'ta bu dal hiç çalışmaz.
            import fcntl  # pylint: disable=import-outside-toplevel,import-error
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


class KosuKilidi:
    """Bir durum dosyası üzerinde tek koşu garantisi (süreçler arası).

    Kilit **işletim sisteminden** alınıyor (POSIX'te ``flock``, Windows'ta
    ``msvcrt.locking``), PID'li bir kilit dosyası "protokolü" uydurulmuyor:
    süreç çökerse ya da ``SIGKILL`` yerse kilidi çekirdek düşürür, dolayısıyla
    bayat kilit temizleme mantığına — ve onun yanlış pozitiflerine — gerek
    kalmaz.

    Kilitlenen bölge yalnızca 0. bayt; teşhis satırı (PID, başlangıç zamanı,
    makine adı) 1. bayttan itibaren yazılır ve bekleyen süreç onu okuyabilir
    (Windows'ta kilitli bölgeyi okumak hata verir, bu yüzden ayrı duruyor).
    """

    def __init__(self, yol: Path | str, *, simdi: Callable[[], float] = time.time):
        self.yol = Path(yol)
        self._simdi = simdi
        self._fd: Optional[int] = None

    def al(self) -> "KosuKilidi":
        self.yol.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.yol), os.O_RDWR | os.O_CREAT, 0o644)
        if not _kilit_dene(fd):
            sahip = self._sahip_oku(fd)
            os.close(fd)
            raise KosuZatenSuruyor(self.yol, sahip)
        self._fd = fd
        self._imza_yaz(fd)
        return self

    def birak(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _kilit_coz(fd)
        finally:
            os.close(fd)

    def _imza_yaz(self, fd: int) -> None:
        bilgi = (f"pid={os.getpid()} "
                 f"baslangic={datetime.fromtimestamp(self._simdi(), timezone.utc):%Y-%m-%dT%H:%M:%SZ} "
                 f"makine={socket.gethostname()}")
        ham = b"#" + bilgi.encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, ham)
        os.ftruncate(fd, len(ham))

    @staticmethod
    def _sahip_oku(fd: int) -> str:
        try:
            os.lseek(fd, 1, os.SEEK_SET)
            return os.read(fd, 256).decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def __enter__(self) -> "KosuKilidi":
        return self.al()

    def __exit__(self, *_) -> None:
        self.birak()


__all__ = [
    "NezaketBekcisi", "NezaketEngeli", "GunlukTavanAsildi", "KaynakDinlenmede",
    "KosuKilidi", "KosuZatenSuruyor",
    "hata_turu", "http_durumu", "GECICI", "KALICI", "ENGELLENME",
]
