"""Nezaket bekçisi — kaynaklara nasıl davrandığımızın tek yeri.

Dört kural, dördü de burada zorlanır:

1. **Kaynak başına eşzamanlılık 1.** Kaynak başına bir kilit; aynı siteye iki
   isteği asla üst üste bindirmeyiz. Kaynaklar arası paralellik serbest.
2. **Gecikme + jitter.** İki istek arası taban gecikme, ± jitter ile dalgalanır.
   Saniyesi saniyesine düzenli istek dizisi bot imzasıdır; WAF'lar tam olarak
   bunu arar.
3. **Üstel geri çekilme.** Hata alan kaynak katlanarak artan bir süre dinlenir.
   Israr etmek karşı tarafta ban, bizde de boşa dönen kuyruk turu demek.
4. **Günlük istek tavanı.** Kaynak × gün sayacı `DurumDeposu`da tutulur, yani
   süreç yeniden başlasa da tavan sıfırlanmaz.

Bekçi *bloklamayı* dışarı sızdırmaz: dinlenmedeki ya da tavanı dolmuş kaynak
için istisna fırlatır, böylece tarayıcı başka kaynağa geçebilir. Yalnızca kısa
istek-arası gecikme için gerçekten uyur.
"""
from __future__ import annotations

import random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Dict, Iterator, Optional

from .ayarlar import NezaketAyarlari
from .durum import DurumDeposu


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
    def hata_bildir(self, kaynak: str) -> float:
        """Hatayı kaydet, üstel geri çekilme uygula, beklenecek süreyi döndür."""
        a = self.ayarlar
        n = self._ardisik_hata.get(kaynak, 0) + 1
        self._ardisik_hata[kaynak] = n
        bekle = min(a.geri_cekilme_tabani * (2 ** (n - 1)), a.azami_geri_cekilme)
        if n >= a.ardisik_hata_siniri:
            # Sınırı aşan kaynak tam geri çekilme süresi kadar dinlenir.
            bekle = a.azami_geri_cekilme
        self._dinlenme_sonu[kaynak] = self._simdi() + bekle
        return bekle

    def basari_bildir(self, kaynak: str) -> None:
        self._ardisik_hata.pop(kaynak, None)
        self._dinlenme_sonu.pop(kaynak, None)

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


__all__ = [
    "NezaketBekcisi", "NezaketEngeli", "GunlukTavanAsildi", "KaynakDinlenmede",
]
