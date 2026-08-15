# -*- coding: utf-8 -*-
"""Kaynaktan gelen adları dosya sistemi sınırında güvenli hâle getirir.

Anime ve bölüm slug'ları **kaynağın** verisidir: sitenin HTML'i, arşivin
`dizin.json`'ı. Bunlar indirme yolunun parçası oluyor, yani ele geçirilmiş bir
kaynak `"../../../../evil-dir"` gibi bir slug yayınlarsa dosya kullanıcının
indirme klasörünün DIŞINA yazılır. Slug'ın kendisi temizlenmiyor — o bir ağ
kimliği, arşivdeki JSON yolunu kuruyor; temizlik yalnızca diske dokunulan yerde
yapılıyor.

İki katman var ve ikincisi bilerek gereksiz:

1. `guvenli_ad` — bileşeni tek bir dosya adına indirger.
2. `guvenli_alt_yol` — kurulan yolun hedef klasörün altında kaldığını
   `os.path.commonpath` ile ayrıca doğrular.

Yalnızca temizliğe güvenmemenin nedeni: kural listesi (Windows'un ayrılmış
adları, sürücü harfi, unicode ayırıcılar, gelecekteki bir platform tuhaflığı)
tam olduğunu kanıtlayamayacağımız bir listedir; kaçan tek durum sessiz bir
dizin dolaşımına dönüşür. Sınır denetimi ise yol çözümlendikten SONRA çalışır
ve tam olması listeye bağlı değildir.
"""
from __future__ import annotations

import os
from typing import Any

# Windows'ta dosya adında yasak olanlar. Yol ayırıcıları (`/`, `\`) ve `:`
# (sürücü harfi) bu kümede olduğu için POSIX'te de dolaşım kapanıyor.
YASAK_KARAKTERLER = frozenset('<>:"/\\|?*')

# Windows'ta bunlar dosya değil AYGIT adıdır; uzantılı hâlleri de (CON.txt)
# aynı aygıta çözülür, bu yüzden nokta öncesi kök ada bakılır.
AYRILMIS_ADLAR = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

# Uzun slug + uzantı + uzun kök dizin, Windows'un MAX_PATH sınırını zorluyor.
UZUNLUK_SINIRI = 120

YEDEK_AD = "dosya"


def guvenli_ad(ham: Any, yedek: str = YEDEK_AD,
               uzunluk: int = UZUNLUK_SINIRI) -> str:
    """`ham`ı tek bir dosya/klasör adına indirge.

    Sonuç hiçbir zaman boş, `.`/`..`, yol ayırıcı içeren, ayrılmış bir aygıt
    adı ya da boşluk/nokta ile biten bir ad olmaz.
    """
    ad = "" if ham is None else str(ham).strip()
    ad = "".join("_" if (ch in YASAK_KARAKTERLER or ord(ch) < 32) else ch
                 for ch in ad)
    # Baştaki noktalar `..`yı, sondakiler Windows'un açamadığı "ad." biçimini
    # eler; `strip` ikisini tek adımda halleder.
    ad = ad.strip(" .")
    if len(ad) > uzunluk:
        ad = ad[:uzunluk].strip(" .")
    if not ad:
        return yedek
    if ad.split(".")[0].upper() in AYRILMIS_ADLAR:
        ad = "_" + ad
    return ad


def alt_yolda_mi(kok: Any, hedef: Any) -> bool:
    """`hedef`, `kok`un altında (ya da kendisi) mi?

    `normcase`: Windows'ta yol karşılaştırması büyük/küçük harf duyarsızdır ve
    `commonpath` sonucu normalize edilmiş döner; iki tarafı da normalize
    etmezsek "C:\\Users" ile "c:\\users" farklı sayılırdı.
    """
    try:
        k = os.path.normcase(os.path.abspath(str(kok)))
        h = os.path.normcase(os.path.abspath(str(hedef)))
        return os.path.commonpath([k, h]) == k
    except (ValueError, OSError):
        # Farklı sürücüler (Windows) ya da çözülemeyen yol: ortak kök yok.
        return False


def guvenli_alt_yol(kok: Any, *parcalar: Any, yedek: str = YEDEK_AD) -> str:
    """`kok` altında, temizlenmiş bileşenlerden kurulmuş **mutlak** yol.

    Boş `kok` çalışma dizini demektir (`Video.indir`'in varsayılanı böyle).
    Sonuç `kok`un altında değilse `ValueError` yükselir: buraya düşmek
    `guvenli_ad`'da bir boşluk kaldığı anlamına gelir ve indirmeyi sessizce
    yanlış yere yazmaktansa patlamak yeğdir.
    """
    kok_mutlak = os.path.abspath(str(kok) if str(kok or "").strip() else ".")
    temiz = [guvenli_ad(p, yedek) for p in parcalar
             if p is not None and str(p).strip()]
    hedef = os.path.abspath(os.path.join(kok_mutlak, *temiz))
    if not alt_yolda_mi(kok_mutlak, hedef):
        raise ValueError(
            f"Güvensiz hedef yol: {hedef!r} — {kok_mutlak!r} altında değil")
    return hedef


__all__ = ["guvenli_ad", "guvenli_alt_yol", "alt_yolda_mi",
           "YASAK_KARAKTERLER", "AYRILMIS_ADLAR", "UZUNLUK_SINIRI"]
