"""Arka plan kaynak tarayıcısı.

Amaç: istemcinin her aramada 7 siteye canlı gitmesini bitirmek. Sunucu tarafında
**yavaş ve nazik** çalışan bu tarayıcı kaynakları periyodik gezer ve birleşik bir
arşiv üretir; arşiv `turkanime_api/sources/animedepo.py`'nin okuduğu şemadadır,
yani istemci tarafında tek satır değişiklik gerekmez.

Kullanım:
    python -m turkanime_server.crawler --kuru-calistir
    python -m turkanime_server.crawler --kaynak anizle --limit 3
"""
from .ayarlar import NezaketAyarlari, TarayiciAyarlari, VARSAYILAN_TOHUMLAR
from .durum import DurumDeposu, Gorev, Kayit
from .eslestirme import KumeDefteri, bolum_slugu, slugla
from .kaynaklar import KAYNAKLAR, KaynakDefteri, KaynakTanimi, KaynakUclari, KosulluSonuc
from .nezaket import GunlukTavanAsildi, KaynakDinlenmede, NezaketBekcisi, NezaketEngeli
from .tarayici import AKISLAR, ARAMA, BOLUMLER, Ozet, Tarayici, imzala
from .yazici import ArsivYazici, atomik_yaz, json_yaz

__all__ = [
    "NezaketAyarlari", "TarayiciAyarlari", "VARSAYILAN_TOHUMLAR",
    "DurumDeposu", "Gorev", "Kayit",
    "KumeDefteri", "slugla", "bolum_slugu",
    "KAYNAKLAR", "KaynakDefteri", "KaynakTanimi", "KaynakUclari", "KosulluSonuc",
    "NezaketBekcisi", "NezaketEngeli", "GunlukTavanAsildi", "KaynakDinlenmede",
    "Tarayici", "Ozet", "imzala", "ARAMA", "BOLUMLER", "AKISLAR",
    "ArsivYazici", "atomik_yaz", "json_yaz",
]
