"""Kaynak tablosu — `turkanime_api.sources.*` üzerine ince bir sarmalayıcı.

**İkinci bir kazıyıcı yazılmaz.** İstemcinin kullandığı adaptörlerin aynısı
burada da kullanılır; aksi hâlde aynı siteyi iki farklı regex setiyle kazıyıp
site değiştiğinde birini düzeltip diğerini unutmuş oluruz (bu depoda bir kez
oldu: `turkanime_server/anizle_scraper.py`, Selenium'lu ve yarım bir
`sources/anizle.py` kopyasıydı, Faz 11'de silindi).

Tablo `gui/qt/sources_bridge.py` desenini izler: yeni kaynak eklemek
`KAYNAKLAR` sözlüğüne bir satır yazmaktır. İmport'lar tembel; bir modül
patlarsa yalnızca o kaynak devre dışı kalır.

Üç uç sözleşmesi (hepsi ``turkanime_api.sources`` biçiminde):
    ara(sorgu)       -> [(kaynak_id, başlık), ...]
    bolumler(id)     -> [(bolum_id, başlık), ...]
    akislar(bolum_id)-> [{"url", "label", "type"?, "referer"?}, ...]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("turkanime.crawler.kaynaklar")

Arama = Callable[[str], List[Tuple[str, str]]]
Bolumler = Callable[[str], List[Tuple[str, str]]]
Akislar = Callable[[str], List[Dict[str, Any]]]


@dataclass(frozen=True)
class KaynakUclari:
    """Bir kaynağın üç ucu."""

    ara: Arama
    bolumler: Optional[Bolumler] = None
    akislar: Optional[Akislar] = None


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


# ── Yükleyiciler ────────────────────────────────────────────────────────────
def _anizle() -> KaynakUclari:
    from turkanime_api.sources.anizle import (
        search_anizle, get_anime_episodes, get_episode_streams,
    )
    return KaynakUclari(search_anizle, get_anime_episodes, get_episode_streams)


def _openani() -> KaynakUclari:
    from turkanime_api.sources.openani import (
        search_openani, get_anime_episodes, get_episode_streams,
    )
    return KaynakUclari(search_openani, get_anime_episodes, get_episode_streams)


def _tranimaci() -> KaynakUclari:
    from turkanime_api.sources.tranimaci import (
        search_tranimaci, get_anime_episodes, get_episode_streams,
    )
    return KaynakUclari(search_tranimaci, get_anime_episodes, get_episode_streams)


def _tranime() -> KaynakUclari:
    from turkanime_api.sources.tranime import (
        search_tranime, get_anime_episodes, get_episode_details,
    )

    def bolumler(slug: str) -> List[Tuple[str, str]]:
        return [(e.slug, e.title) for e in (get_anime_episodes(slug) or [])]

    def akislar(ep_slug: str) -> List[Dict[str, Any]]:
        detay = get_episode_details(ep_slug)
        if not detay:
            return []
        out: List[Dict[str, Any]] = []
        for s in detay.get_sources():
            iframe = s.get_iframe()
            if iframe:
                out.append({"url": iframe, "label": s.name, "type": "iframe"})
        return out

    return KaynakUclari(search_tranime, bolumler, akislar)


def _animecix() -> KaynakUclari:
    from turkanime_api.sources.animecix import (
        search_animecix, CixAnime, _video_streams,
    )

    def bolumler(anime_id: str) -> List[Tuple[str, str]]:
        # Bölüm kimliği olarak embed yolunu taşıyoruz: `_video_streams` zaten
        # onu bekliyor, ayrıca ikinci bir çözümleme isteği gerekmiyor.
        return [(e.url, e.title) for e in CixAnime(id=str(anime_id), title="").episodes
                if e.url]

    return KaynakUclari(search_animecix, bolumler, _video_streams)


# AnimeDepo bilerek yok: ürettiğimiz arşivin ta kendisi o şemada, onu taramak
# kendi çıktımızı geri okumak olurdu.
KAYNAKLAR: Dict[str, KaynakTanimi] = {
    "anizle": KaynakTanimi("anizle", "Anizle", "ANIZLE", _anizle),
    "openani": KaynakTanimi("openani", "OpenAnime", "OPENANI", _openani),
    "tranimaci": KaynakTanimi("tranimaci", "Tranimaci", "TRANIMACI", _tranimaci),
    "tranime": KaynakTanimi("tranime", "TRAnimeİzle", "TRANIME", _tranime),
    "animecix": KaynakTanimi("animecix", "AnimeciX", "ANIMECIX", _animecix),
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
