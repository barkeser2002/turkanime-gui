"""Arşiv yayıncısı (Faz 12) — üretilen dosya ağacını git'e sürekli işler.

Tarayıcıdan ayrı bir paket, çünkü yaptığı iş de ayrı: burada ne SQLite durumu,
ne nezaket bütçesi, ne de kaynak adaptörü var. Yayıncı "bir dizin al, git'e
koy" işini yapar; dizini kimin ürettiği umurunda değildir. Ayrılık pratikte üç
şey kazandırır:

* Tarayıcı git kurulu olmayan bir makinede de koşabilir.
* Yayın ayrı zamanlanabilir (tarama saatlerce sürer, yayın saniyeler).
* Elle düzeltilmiş ya da başka yerde üretilmiş bir arşiv de yayınlanabilir.
"""
from .ayarlar import YayinAyarlari, ortamdan
from .depo import GitDepo, GitHatasi, GitYok, gizle
from .mesaj import Degisim, mesaj_uret
from .yayinci import Yayinci, YayinSonucu

__all__ = [
    "YayinAyarlari", "ortamdan",
    "GitDepo", "GitHatasi", "GitYok", "gizle",
    "Degisim", "mesaj_uret",
    "Yayinci", "YayinSonucu",
]
