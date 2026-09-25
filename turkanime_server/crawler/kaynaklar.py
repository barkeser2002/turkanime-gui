"""Kaynak tablosu — `turkanime_api.sources.*` üzerine ince bir sarmalayıcı.

**İkinci bir kazıyıcı yazılmaz.** İstemcinin kullandığı adaptörlerin aynısı
burada da kullanılır; aksi hâlde aynı siteyi iki farklı regex setiyle kazıyıp
site değiştiğinde birini düzeltip diğerini unutmuş oluruz (bu depoda bir kez
oldu: `turkanime_server/anizle_scraper.py`, Selenium'lu ve yarım bir
`sources/anizle.py` kopyasıydı, Faz 11'de silindi).

Tablo da ikinci kez yazılmaz: istemcinin kaynak kaydından
(`turkanime_api/sources/kayit.py`) `taranabilir=True` olanlar alınır. Eskiden
yükleyiciler burada ayrıca yazılıyordu (TRAnimeİzle/AnimeciX sarmalayıcıları
dahil); istemci tarafında bir kaynak değiştiğinde bu kopya geride kalıyordu.
Yeni kaynağı taratmak = kayıtta `taranabilir=True`. İmport'lar yine tembel;
bir modül patlarsa yalnızca o kaynak devre dışı kalır (bkz. `KaynakDefteri`).

TürkAnime (arşiv) bilerek taranmaz: ürettiğimiz arşivin ta kendisi o şemada,
onu taramak kendi çıktımızı geri okumak olurdu.

Üç uç sözleşmesi (hepsi ``turkanime_api.sources`` biçiminde):
    ara(sorgu)       -> [(kaynak_id, başlık), ...]
    bolumler(id)     -> [(bolum_id, başlık), ...]
    akislar(bolum_id)-> [{"url", "label", "type"?, "referer"?}, ...]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# İstemcinin sınıfının ta kendisi: kayıttaki yükleyiciler bunu döndürüyor ve
# tarayıcı `KaynakUclari(ara, bolumler, akislar)` diye elle de kurabiliyor.
from turkanime_api.sources import kayit as _kayit
from turkanime_api.sources.kayit import KaynakUclari

log = logging.getLogger("turkanime.crawler.kaynaklar")

Arama = Callable[[str], List[Tuple[str, str]]]
Bolumler = Callable[[str], List[Tuple[str, str]]]
Akislar = Callable[[str], List[Dict[str, Any]]]


@dataclass(frozen=True)
class KaynakTanimi:
    """Tablo satırı: kaynağın kimliği + tembel yükleyicisi."""

    anahtar: str
    ad: str
    oynatici: str                       # AnimeDepo şemasındaki "player" alanı
    yukleyici: Callable[[], KaynakUclari]


@dataclass(frozen=True)
class KosulluSonuc:
    """Koşullu istek destekleyen adaptörler için isteğe bağlı zarf.

    Bir uç bunu döndürürse tarayıcı ETag/Last-Modified değerlerini saklar ve
    sonraki turda geri gönderir. ``degismedi=True`` (HTTP 304) gelirse veri hiç
    işlenmez. Düz liste döndüren adaptörler için içerik hash'ine düşülür.
    """

    veri: Any
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    degismedi: bool = False


def _tanim(kaynak: "_kayit.Kaynak") -> KaynakTanimi:
    # Anahtar modül adı ("tranime", "openani"): durum veritabanı, `--kaynak`
    # seçeneği ve katkı API'si kaynakları bu adla tanıyor.
    return KaynakTanimi(kaynak.modul, kaynak.ad, kaynak.oynatici, kaynak.yukleyici)


KAYNAKLAR: Dict[str, KaynakTanimi] = {
    k.modul: _tanim(k) for k in _kayit.tarayici_kaynaklari()
}


class KaynakDefteri:
    """Yüklenmiş uçları önbellekler; patlayan modülü sessizce devre dışı bırakır."""

    def __init__(self, tablo: Optional[Dict[str, KaynakTanimi]] = None):
        self.tablo = dict(tablo if tablo is not None else KAYNAKLAR)
        self._onbellek: Dict[str, Optional[KaynakUclari]] = {}

    def anahtarlar(self) -> List[str]:
        return sorted(self.tablo)

    def tanim(self, anahtar: str) -> KaynakTanimi:
        return self.tablo[anahtar]

    def uclar(self, anahtar: str) -> Optional[KaynakUclari]:
        """Kaynağın uçlarını döndür; yüklenemiyorsa ``None``."""
        if anahtar in self._onbellek:
            return self._onbellek[anahtar]
        tanim = self.tablo.get(anahtar)
        if tanim is None:
            self._onbellek[anahtar] = None
            return None
        try:
            uclar = tanim.yukleyici()
        except Exception as e:                        # pylint: disable=broad-except
            log.warning("%s kaynağı yüklenemedi: %s", anahtar, e)
            uclar = None
        self._onbellek[anahtar] = uclar
        return uclar


__all__ = [
    "KAYNAKLAR", "KaynakDefteri", "KaynakTanimi", "KaynakUclari", "KosulluSonuc",
]
