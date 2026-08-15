"""Tarayıcı yapılandırması.

Varsayılanlar bilinçli olarak **yavaş**: bu tarayıcı bir arşivci, indirme
hızlandırıcısı değil. Kaynak başına 8 saniyelik gecikme ve 400 istek/gün tavanı
ile tam bir tarama günlere yayılır — istenen de bu. Faz 12 çıktıyı git'e
yayınlayacağı için tarama sıklığı değil, arşivin bütünlüğü önemli.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

# Keşif tohumları: kaynakların "hepsini listele" ucu yok, yalnızca arama var.
# Alfabe + rakamlar üzerinden gezmek her kaynakta çalışan tek ortak yöntem.
VARSAYILAN_TOHUMLAR: Tuple[str, ...] = tuple("abcdefghijklmnopqrstuvwxyz0123456789")


@dataclass(frozen=True)
class NezaketAyarlari:
    """Sitelere nasıl davranacağımız.

    ``gecikme`` kaynak başına ardışık iki isteğin arasındaki taban süredir;
    eşzamanlılık zaten 1 olduğu için bu doğrudan istek/saniye tavanını verir.
    ``jitter`` sabit ritmi kırar: sabit aralıklı istekler bot imzasıdır ve
    WAF'lar tam olarak bunu yakalar.
    """

    gecikme: float = 8.0
    jitter: float = 0.35
    geri_cekilme_tabani: float = 30.0
    azami_geri_cekilme: float = 1800.0
    # Bu kadar ardışık hatadan sonra kaynak tur boyunca dinlenmeye alınır;
    # ısrar etmek karşı tarafta ban, bizde de boş kuyruk turu demek.
    ardisik_hata_siniri: int = 5
    gunluk_tavan: int = 400
    # Bir koşuda geri çekilme beklemelerine ayrılan **toplam** bütçe. Tek tek
    # beklemeleri sınırlamak yetmezdi: üst üste binen kısa dinlenmeler de turu
    # yiyebilir. Bütçe dolunca kaynak bu tura kapatılır ve kalan iş bir sonraki
    # tetiğe devredilir — cron zaten 30 dakikada bir çağırıyor.
    dinlenme_butcesi: float = 300.0


@dataclass(frozen=True)
class TarayiciAyarlari:
    """Tek bir tarama koşusunun tüm parametreleri."""

    cikti: Path = Path("arsiv")
    durum: Path = Path("durum/tarayici.sqlite3")
    kaynaklar: Tuple[str, ...] = ()
    tohumlar: Tuple[str, ...] = VARSAYILAN_TOHUMLAR
    # Koşu başına işlenecek görev tavanı (None = kuyruk bitene kadar).
    limit: Optional[int] = None
    kuru_calistir: bool = False

    # ── Koşullu istek / tazelik ─────────────────────────────────────────────
    # Bir kayıt en erken ``tazelik`` saniye sonra yeniden çekilir. İçerik
    # değişmemişse aralık ``tazelik_carpani`` ile büyür (``azami_tazelik``e
    # kadar): bitmiş animeler ayda bir, süregelen sezonlar günlük ziyaret alır.
    tazelik: float = 6 * 3600.0
    azami_tazelik: float = 7 * 24 * 3600.0
    tazelik_carpani: float = 2.0

    eslesme_esigi: float = 0.90
    nezaket: NezaketAyarlari = field(default_factory=NezaketAyarlari)

    @property
    def kilit(self) -> Path:
        """Koşu kilidinin yolu — durum dosyasının yanında.

        Kilit çıktı ağacında değil durumun yanında duruyor: arşiv dizini git
        çalışma ağacı ve oraya konan her dosya yayınlanan arşive sızardı.
        """
        return self.durum.with_name(self.durum.name + ".kilit")

    def cozulmus(self, kok: Path) -> "TarayiciAyarlari":
        """Göreli yolları ``kok`` altına sabitlenmiş bir kopya döndür."""
        from dataclasses import replace

        return replace(
            self,
            cikti=kok / self.cikti if not self.cikti.is_absolute() else self.cikti,
            durum=kok / self.durum if not self.durum.is_absolute() else self.durum,
        )


__all__ = ["NezaketAyarlari", "TarayiciAyarlari", "VARSAYILAN_TOHUMLAR"]
