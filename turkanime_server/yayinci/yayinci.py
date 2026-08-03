"""Yayın akışı — arşiv ağacını git deposuna işler ve uzağa iter.

Tur şöyle işler:

    hazırla → farkı çıkar → (boşsa dur) → sahnele → commit → bakım → it

**Boş commit atılmaz.** Fark git'in kendi durumundan okunur; Faz 11'in yazıcısı
içeriği değişmeyen dosyaya zaten dokunmadığı için "her tur bir commit" gürültüsü
oluşmaz. Bu iki mekanizma birbirini tamamlar: yazıcı diski, yayıncı geçmişi
temiz tutar.

**Depo şişmesi.** Arşiv on binlerce küçük JSON ve sürekli güncelleniyor; bir
yıllık günlük yayın, kaynak kodu deposu gibi ele alınırsa klonu kullanılmaz
hâle getirir. İki önlem uygulanır:

1. *Periyodik `git gc`* — her `bakim_araligi` commit'te bir tam toplama, aradaki
   turlarda `--auto`. Küçük dosya çokluğu gevşek (loose) nesne patlaması demek;
   paketlenmeden klon boyutu dosya sayısıyla doğru orantılı büyür.
2. *Geçmiş budama* — commit sayısı `azami_gecmis`i aşınca geçmiş tek bir kök
   commit'e indirilir (`--orphan` tazeleme) ve zorla itilir. Bu arşiv bir kod
   deposu değil, bir **ayna**: kimse `git bisect` yapmıyor, herkes son hâli
   okuyor. Eski sürümleri saklamanın bedeli (klon boyutu) faydasından büyük.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ayarlar import YayinAyarlari
from .depo import GitDepo, gizle
from .mesaj import DIZIN, Degisim, mesaj_uret

log = logging.getLogger("turkanime.yayinci")


@dataclass(frozen=True)
class YayinSonucu:
    """Bir yayın turunun sonucu."""

    yayinlandi: bool = False
    sebep: str = ""
    sha: Optional[str] = None
    mesaj: Optional[str] = None
    itildi: bool = False
    budandi: bool = False
    degisim: Degisim = field(default_factory=Degisim)

    def sozluk(self) -> Dict[str, Any]:
        return {
            "yayinlandi": self.yayinlandi,
            "sebep": self.sebep,
            "sha": self.sha,
            # Yalnızca başlık satırı: gövde JSON çıktısını gereksiz şişirir.
            "baslik": (self.mesaj or "").splitlines()[0] if self.mesaj else None,
            "itildi": self.itildi,
            "budandi": self.budandi,
            "degisim": self.degisim.sozluk(),
        }


class Yayinci:
    """Arşiv dizinini git'e yayınlar."""

    def __init__(self, ayarlar: YayinAyarlari, *, depo: Optional[GitDepo] = None):
        self.ayarlar = ayarlar
        self.depo = depo or GitDepo(
            ayarlar.arsiv,
            dal=ayarlar.dal,
            ad=ayarlar.ad,
            eposta=ayarlar.eposta,
            token=ayarlar.token,
            kullanici=ayarlar.kullanici,
            ssh_anahtar=ayarlar.ssh_anahtar,
        )

    # ── Yardımcılar ─────────────────────────────────────────────────────────
    def _arsiv_saglam_mi(self) -> bool:
        """`dizin.json` yoksa yayınlamayı reddet.

        Boş ya da yanlış bağlanmış bir birim (volume), tek commit'te tüm
        istemcilerin okuduğu arşivi boşaltırdı.
        """
        return (self.ayarlar.arsiv / DIZIN).is_file()

    def toplam_anime(self) -> Optional[int]:
        try:
            veri = json.loads((self.ayarlar.arsiv / DIZIN).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        adet = veri.get("adet") if isinstance(veri, dict) else None
        return int(adet) if isinstance(adet, int) else None

    def _fark(self) -> Degisim:
        """Çalışma ağacı farkını sahnelemeden çıkar."""
        eklenen: List[str] = []
        degisen: List[str] = []
        silinen: List[str] = []
        for kod, yol in self.depo.durum():
            if kod == "??" or "A" in kod:
                eklenen.append(yol)
            elif "D" in kod:
                silinen.append(yol)
            else:
                degisen.append(yol)
        return Degisim(tuple(sorted(eklenen)), tuple(sorted(degisen)),
                       tuple(sorted(silinen)))

    def _kuru_fark(self) -> Degisim:
        """Depo hiç kurulmamışken fark = ağaçtaki her dosya."""
        kok = self.ayarlar.arsiv
        yollar = [str(p.relative_to(kok)).replace("\\", "/")
                  for p in kok.rglob("*") if p.is_file()]
        return Degisim(eklenen=tuple(sorted(yollar)))

    def _toplu_silme_mi(self, degisim: Degisim) -> bool:
        if not degisim.silinen or self.ayarlar.guvenlik_esigi <= 0:
            return False
        takip = self.depo.takip_edilen_sayisi()
        return bool(takip) and len(degisim.silinen) / takip > self.ayarlar.guvenlik_esigi

    # ── Ana akış ────────────────────────────────────────────────────────────
    def yayinla(self) -> YayinSonucu:
        if not self._arsiv_saglam_mi():
            log.warning("%s içinde %s yok — yayın iptal", self.ayarlar.arsiv, DIZIN)
            return YayinSonucu(sebep="arsiv-eksik")

        if self.ayarlar.kuru_calistir and not self.depo.depo_mu():
            # Kuru koşu diske dokunmaz: `git init` bile bir yan etkidir.
            degisim = self._kuru_fark()
            return YayinSonucu(sebep="kuru", degisim=degisim,
                               mesaj=mesaj_uret(degisim, self.toplam_anime()))

        yeni_depo = self.depo.hazirla()
        if yeni_depo:
            log.info("arşiv deposu kuruldu: %s (dal: %s)",
                     self.ayarlar.arsiv, self.ayarlar.dal)

        degisim = self._fark()
        if degisim.bos:
            log.info("değişiklik yok — commit atılmadı")
            return YayinSonucu(sebep="degisiklik-yok", degisim=degisim)

        if self._toplu_silme_mi(degisim):
            log.error("değişikliğin %d dosyası silme — güvenlik eşiği aşıldı, "
                      "yayın durduruldu", len(degisim.silinen))
            return YayinSonucu(sebep="toplu-silme", degisim=degisim)

        mesaj = mesaj_uret(degisim, self.toplam_anime())
        if self.ayarlar.kuru_calistir:
            return YayinSonucu(sebep="kuru", degisim=degisim, mesaj=mesaj)

        self.depo.sahnele()
        sha = self.depo.commit(mesaj)
        if sha is None:
            # Fark yalnızca yok sayılan dosyalardan ibaretmiş (ör. yazıcının
            # atomik yazım artığı); sahneye hiçbir şey girmedi.
            return YayinSonucu(sebep="degisiklik-yok", degisim=degisim)
        log.info("commit %s — %s", sha[:8], mesaj.splitlines()[0])

        budandi = self._bakim()
        itildi = self._it(zorla=budandi)
        return YayinSonucu(yayinlandi=True, sebep="yayinlandi",
                           sha=self.depo.son_sha(), mesaj=mesaj,
                           itildi=itildi, budandi=budandi, degisim=degisim)

    # ── Bakım / itme ────────────────────────────────────────────────────────
    def _bakim(self) -> bool:
        """Depo şişmesi önlemleri. ``True`` = geçmiş budandı."""
        sayi = self.depo.commit_sayisi()
        if self.ayarlar.azami_gecmis and sayi > self.ayarlar.azami_gecmis:
            log.info("geçmiş budanıyor: %d commit → 1", sayi)
            self.depo.gecmisi_buda(
                f"arşiv: geçmiş sıfırlandı ({sayi} commit tek köke indirildi)\n\n"
                "Arşiv bir ayna; eski sürümlerin klon boyutuna bedeli\n"
                "faydasından büyük.\n")
            return True
        self.depo.gc(tam=bool(self.ayarlar.bakim_araligi)
                     and sayi % self.ayarlar.bakim_araligi == 0)
        return False

    def _it(self, zorla: bool = False) -> bool:
        if not (self.ayarlar.it and self.ayarlar.uzak):
            return False
        self.depo.it(self.ayarlar.uzak, zorla=zorla)
        log.info("itildi: %s (%s)", gizle(self.ayarlar.uzak, self.ayarlar.token),
                 self.ayarlar.dal)
        return True


def yayinla(ayarlar: YayinAyarlari) -> YayinSonucu:
    """Tek seferlik kolaylık sarmalayıcısı."""
    return Yayinci(ayarlar).yayinla()


__all__ = ["Yayinci", "YayinSonucu", "yayinla"]
