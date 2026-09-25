"""Kaynak → bölüm/stream köprüsü.

Eski `gui/main.py`, her kaynak için ~200 satırlık inline `if/elif` bloklarıyla
`AdapterAnime`/`AdapterBolum` kuruyordu. Sonra bu iş burada tablo sürücülü
yapıldı; ama tablo (`FUNCTION_SOURCES` + kaynağa özgü `BUILDERS`) yine elle
tutuluyordu ve `SearchEngine`/CLI'daki kopyalarıyla ayrışıyordu. Artık kaynak
listesinin tek yeri `sources/kayit.py`; buradaki sözlükler ondan TÜRETİLİYOR
ve yeni kaynak eklemek bu dosyada değişiklik gerektirmiyor.

Dönen bölüm nesneleri `best_video(...)` arayüzünü sağlar; böylece indirme/oynatma
boru hattı (yt-dlp + mpv) değişmeden çalışır.

"TürkAnime" = turkanime.tv'nin statik arşivi (site kapandı). Eski "AnimeDepo"
adıyla gelen istekler (kayıtlı bağlantılar, eski eşleşmeler) aynı kayda düşer.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Callable, Dict, List

from ...sources import kayit

# Kaynak adı -> playback/indirme desteği yok (yalnızca arama/metadata)
METADATA_ONLY = {k.ad for k in kayit.kaynaklar() if k.yalnizca_metadata}


class UnsupportedSource(Exception):
    """Bu kaynak Qt GUI'sinde henüz oynatma/indirme için bağlanmadı."""


class _TakmaAdliTablo(dict):
    """Eski adla (``["AnimeDepo"]``) okumayı da kabul eden sözlük.

    Yalnızca köşeli parantezle okuma takma adı çözer (`__missing__`); yineleme,
    `in` ve `.get` kanonik anahtarlarla sınırlı kalır. Böylece
    `supported_sources()` gibi listeler "AnimeDepo"yu ikinci kez saymaz, ama
    eski adı bilen kod (kayıtlı bağlantılar, eski testler) kırılmaz.
    """

    def __missing__(self, anahtar):
        kanonik = kayit.kanonik_ad(anahtar)
        if kanonik != anahtar and dict.__contains__(self, kanonik):
            return self[kanonik]
        raise KeyError(anahtar)


def _yukleyici(ad: str) -> Callable[[], tuple]:
    """Eski sözleşme: ``loader() -> (episodes_fn, streams_fn)``.

    Kaynak kayda ÇAĞRI ANINDA bakılarak bulunur (yükleme anında değil): kayıt
    çalışma anında değişirse (`kayit.kaydet`, test sahtelemesi) köprü de onu
    görür.
    """
    def loader():
        uclar = kayit.bul(ad).uclar()
        return uclar.bolumler, uclar.akislar
    return loader


def _tablo_kur() -> Dict[str, Dict[str, Any]]:
    tablo = _TakmaAdliTablo()
    for kaynak in kayit.kaynaklar(metadata=False):
        tablo[kaynak.ad] = {
            "loader": _yukleyici(kaynak.ad),
            "player": kaynak.oynatici,
            "ep_url": kaynak.bolum_adresi,
        }
    return tablo


# Fonksiyon-stili kaynaklar (search/episodes/streams üçlüsü). Artık BÜTÜN
# oynatılabilir kaynaklar bu stilde: eski kurucu-stili kaynaklar (TRAnimeİzle,
# AnimeciX, Anizle) kayıtta üç uca indirgendi.
FUNCTION_SOURCES: Dict[str, Dict[str, Any]] = _tablo_kur()


def _make_provider(streams_fn: Callable, ep_id: str) -> Callable[[str], List[Dict[str, str]]]:
    """Bölüm kimliğini kapatan stream sağlayıcı (url argümanı yok sayılır)."""
    return kayit.akis_saglayici(streams_fn, ep_id)


def _build_function_source(source: str, slug: str, title: str) -> List[Dict[str, Any]]:
    """Kayıttaki kaynaktan `[{"title", "obj"}, ...]` kur."""
    from ...sources.adapter import kayittan_bolumler

    kaynak = kayit.bul(source)
    if kaynak is None:
        raise UnsupportedSource(f"{source} kaynağı kayıtlı değil.")
    hata = kaynak.kimlik_denetle(slug)
    if hata:
        raise UnsupportedSource(hata)
    return [{"title": b.title, "obj": b} for b in kayittan_bolumler(kaynak, slug, title)]


# Kaynak → kurucu. Kayıttaki her oynatılabilir kaynak için genel kurucu;
# kayıt dışı, kendine özgü nesne kuran bir kaynak gerekirse buraya eklenebilir.
BUILDERS: Dict[str, Callable[[str, str], List[Dict[str, Any]]]] = _TakmaAdliTablo(
    {ad: partial(_build_function_source, ad) for ad in FUNCTION_SOURCES})


def supported_sources() -> List[str]:
    """Oynatma/indirme için bağlanmış kaynaklar."""
    return sorted(set(BUILDERS) | set(FUNCTION_SOURCES))


def _eski_adlar() -> str:
    """Hata mesajı için "AnimeDepo → TürkAnime" listesi."""
    return ", ".join(f"{takma} → {k.ad}" for k in kayit.kaynaklar(metadata=False)
                     for takma in k.takma_adlar)


def fetch_episodes(source: str, slug: str, title: str) -> List[Dict[str, Any]]:
    """Kaynağa göre bölüm listesini döndür.

    ``source`` kanonik ad ya da eski ad olabilir ("AnimeDepo" → TürkAnime).

    Returns: [{"title": str, "obj": <best_video() sağlayan nesne>}, ...]
    Raises:  UnsupportedSource — kaynak henüz Qt GUI'sinde bağlanmadıysa.
    """
    ad = kayit.kanonik_ad(source)
    if ad in METADATA_ONLY:
        raise UnsupportedSource(
            f"{source} yalnızca arama/metadata kaynağı; oynatma için başka bir kaynak seçin."
        )
    builder = BUILDERS.get(ad)
    if builder is None:
        eski = _eski_adlar()
        raise UnsupportedSource(
            f"{source} kaynağı Qt arayüzünde henüz bağlanmadı "
            f"(desteklenenler: {', '.join(supported_sources())}"
            + (f"; eski adlar: {eski}" if eski else "") + ")."
        )
    return builder(slug, title)


__all__ = ["fetch_episodes", "supported_sources", "UnsupportedSource", "METADATA_ONLY",
           "FUNCTION_SOURCES", "BUILDERS"]
