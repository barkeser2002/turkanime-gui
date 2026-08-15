"""Yayıncı yapılandırması.

**Sırlar yalnızca ortamdan gelir.** Deploy token / SSH anahtarı ne depoda, ne
`ayarlar.json`'da, ne de komut satırında durur. Komut satırı bilinçli olarak
dışarıda: `ps` çıktısı, shell geçmişi ve CI log'ları argümanları görür, ortam
değişkenlerini görmez. Bu yüzden `--token` diye bir bayrak *yok*.

Token alanı `repr=False`: yapılandırmayı log'a basan bir satır (ya da bir
istisna izi) sırrı sızdırmasın.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

ON_EK = "TURKANIME_ARSIV_"

# Kimlik yardımcısının okuduğu değişken adı. Sabit, çünkü yardımcı script'i
# sırrı *değerle* değil *adla* taşır (bkz. depo.py).
TOKEN_DEGISKENI = ON_EK + "TOKEN"
KULLANICI_DEGISKENI = ON_EK + "KULLANICI"


@dataclass(frozen=True)
class YayinAyarlari:
    """Tek bir yayın koşusunun tüm parametreleri."""

    arsiv: Path = Path("arsiv")
    uzak: Optional[str] = None
    dal: str = "master"

    # ── Kimlik (yalnızca ortamdan) ──────────────────────────────────────────
    token: Optional[str] = field(default=None, repr=False)
    kullanici: str = "oauth2"
    ssh_anahtar: Optional[Path] = None
    ad: str = "turkanime-arsiv"
    eposta: str = "turkanime-arsiv@users.noreply.localhost"

    # ── Davranış ────────────────────────────────────────────────────────────
    it: bool = True
    kuru_calistir: bool = False

    # ── Depo şişmesi ────────────────────────────────────────────────────────
    # Arşiv on binlerce küçük JSON'dan oluşuyor ve her turda bir kısmı
    # değişiyor. `azami_gecmis` commit'i aşınca geçmiş tek bir kök commit'e
    # indirilir (bkz. yayinci.py). `bakim_araligi` her N commit'te bir tam
    # `git gc` çalıştırır; aradaki turlarda yalnızca `--auto` koşar.
    azami_gecmis: int = 200
    bakim_araligi: int = 25

    # ── Güvenlik ────────────────────────────────────────────────────────────
    # Takip edilen dosyaların bu oranından fazlası silinecekse yayın durur.
    # Boş bir birim (volume) bağlanması ya da tarayıcının yarım çıktısı,
    # yayınlanmış arşivi tüm istemciler için tek commit'te boşaltabilirdi.
    guvenlik_esigi: float = 0.5

    def cozulmus(self, kok: Path) -> "YayinAyarlari":
        """Göreli yolları ``kok`` altına sabitlenmiş bir kopya döndür."""
        from dataclasses import replace

        return replace(
            self,
            arsiv=self.arsiv if self.arsiv.is_absolute() else kok / self.arsiv,
        )


def _sayi(ortam: Mapping[str, str], ad: str, varsayilan: int) -> int:
    try:
        return int(ortam[ON_EK + ad])
    except (KeyError, ValueError):
        return varsayilan


def ortamdan(ortam: Optional[Mapping[str, str]] = None,
             temel: Optional[YayinAyarlari] = None) -> YayinAyarlari:
    """Ortam değişkenlerini ``temel`` ayarların üzerine bindir.

    Boş string "ayarlanmamış" sayılır: `.env` dosyalarında bir değişkeni
    silmek yerine boşaltmak yaygın ve `TURKANIME_ARSIV_UZAK=` gördüğünde
    uzağa itmeye çalışmak anlamsız.
    """
    from dataclasses import replace

    ortam = os.environ if ortam is None else ortam
    temel = temel or YayinAyarlari()

    def oku(ad: str) -> Optional[str]:
        deger = (ortam.get(ON_EK + ad) or "").strip()
        return deger or None

    ssh = oku("SSH_ANAHTAR")
    return replace(
        temel,
        uzak=oku("UZAK") or temel.uzak,
        dal=oku("DAL") or temel.dal,
        token=oku("TOKEN") or temel.token,
        kullanici=oku("KULLANICI") or temel.kullanici,
        ssh_anahtar=Path(ssh) if ssh else temel.ssh_anahtar,
        ad=oku("AD") or temel.ad,
        eposta=oku("EPOSTA") or temel.eposta,
        azami_gecmis=_sayi(ortam, "AZAMI_GECMIS", temel.azami_gecmis),
        bakim_araligi=_sayi(ortam, "BAKIM_ARALIGI", temel.bakim_araligi),
    )


__all__ = ["YayinAyarlari", "ortamdan", "ON_EK",
           "TOKEN_DEGISKENI", "KULLANICI_DEGISKENI"]
