"""Keşif sayfalarının (Ana Sayfa, Trend, Bu Sezon) köprü uçları ve verisi.

Veri MyAnimeList'ten (Jikan), düşerse AniList'ten. Üç kip ayrı soru soruyor
(bkz. `fetch_discover`). "İzlemeye devam et" şeridi yerel kitaplıktan:
Jikan/AniList düştüğünde ya da çevrimdışıyken de dolu. Ağ ve disk işleri
``arka=True``: GUI thread'i beklemiyor.
"""
from __future__ import annotations

from datetime import datetime
from itertools import zip_longest
from typing import Any, Dict, List, Optional

from ...common import kutuphane
from ...sources import kayit as kaynak_kaydi
from .kopru import UcHatasi, uc
from .kunye import anime_title
from .veri import kartlar

MODES = ("home", "trending", "season")


# Ana sayfa bir "şerit": tek ekranda bitsin diye daha az kart gösterir.
HOME_LIMIT = 12


LIST_LIMIT = 24


# Jikan sezon adlarını İngilizce döndürür; başlık kullanıcıya gösterildiği için
# ay numarasından Türkçe etiketi burada üretiyoruz.
_SEASON_NAMES = {1: "Kış", 2: "İlkbahar", 3: "Yaz", 4: "Sonbahar"}


# Boş sonuç mesajı kipe göre değişir: "Bu Sezon" boş kaldığında kullanıcının
# elinde hâlâ bir sonraki adım olmalı (yenile / Trend sekmesi).
_BOS_MESAJ = {
    "season": ("Sezon verisi alınamadı (MyAnimeList yanıt vermedi). "
               "Yenileyin ya da Trend sekmesine bakın."),
}


_BOS_VARSAYILAN = "İçerik alınamadı. Bağlantınızı kontrol edip yenileyin."


# ── Veri yardımcıları (Qt'siz; doğrudan test edilebilir) ────────────────────
def season_label(now: Optional[datetime] = None) -> str:
    """Başlıkta gösterilen "Yaz 2026" biçimindeki etiket."""
    now = now or datetime.now()
    return f"{_SEASON_NAMES[(now.month - 1) // 3 + 1]} {now.year}"


# İçe aktarımlar gövdede: bu modül GUI açılışında yükleniyor, ağ istemcilerini
# o anda import etmeye gerek yok. Üçü de hata/boş durumda [] döndürür ki çağıran
# taraf "veri var mı" sorusunu tek bir şekilde sorabilsin.
def _jikan_sezon() -> List[Dict[str, Any]]:
    """Jikan'ın "şu anki sezon" listesi."""
    try:
        from ...jikan_client import get_seasonal_anime_list

        # year/season BİLEREK verilmiyor: parametresiz çağrı Jikan'ın
        # `/seasons/now` ucuna gider; "şu anki sezon" tanımını yerel saatten
        # tahmin etmek yerine sunucuya bırakmak daha doğru.
        return list(get_seasonal_anime_list() or [])
    except Exception as exc:  # ağ/parse hatası sayfayı düşürmemeli
        print(f"[Keşif] Jikan sezon hatası: {exc}")
        return []


def _jikan_trend(limit: int) -> List[Dict[str, Any]]:
    """Jikan trend listesi."""
    try:
        from ...jikan_client import get_trending_anime_list

        return list(get_trending_anime_list(limit=limit) or [])
    except Exception as exc:
        print(f"[Keşif] Jikan trend hatası: {exc}")
        return []


def _anilist_trend(limit: int) -> List[Dict[str, Any]]:
    """AniList trend listesi — trend kipinin yedeği."""
    try:
        from ...anilist_client import anilist_client

        return list(anilist_client.get_trending_anime(page=1, per_page=limit) or [])
    except Exception as exc:
        print(f"[Keşif] AniList hatası: {exc}")
        return []


def _kimlik(item: Dict[str, Any]) -> str:
    """Harmanda yinelenen kaydı ayıklamak için anahtar.

    Aynı anime hem sezon hem trend listesinde çıkabiliyor; `id` iki istemcide de
    var ama yoksa ada düşüyoruz (kartta zaten ad görünüyor, iki özdeş kart
    kullanıcıya hata gibi görünür).
    """
    ident = item.get("id")
    return f"id:{ident}" if ident is not None else f"ad:{anime_title(item).casefold()}"


def _vitrin(sezon: List[Dict[str, Any]], trend: List[Dict[str, Any]],
            limit: int) -> List[Dict[str, Any]]:
    """Sezon ve trend listelerini dönüşümlü ör (sezon başta).

    Uç uca eklemek (önce tüm sezon, sonra tüm trend) ana sayfayı ilk ekranda
    yine tek kaynaklı bir listeye çevirirdi; dönüşümlü örgü her iki kaynağın da
    kaydırma çizgisinin üstünde görünmesini garanti eder.
    """
    harman: List[Dict[str, Any]] = []
    gorulen = set()
    for cift in zip_longest(sezon, trend):
        for item in cift:
            if not isinstance(item, dict):
                continue
            anahtar = _kimlik(item)
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            harman.append(item)
            if len(harman) >= limit:
                return harman
    return harman


def fetch_discover(mode: str, limit: int = LIST_LIMIT) -> List[Dict[str, Any]]:
    """Seçilen kip için anime listesini getir (arka plan thread'inde çağrılır).

    Kipe göre üç ayrı davranış:

    ``season``
        YEDEĞİ YOK. AniList istemcisinde sezon ucu bulunmuyor (yalnızca
        `get_trending_anime` / `search_anime` var), dolayısıyla düşülecek yedek
        "bu sezon" değil "trend" listesi olurdu. Eskiden tam da bu yapılıyordu:
        başlık "… sezonu • MyAnimeList", durum "{n} anime" derken ekranda
        AniList trendi duruyordu — kullanıcı sezon listesi sandığı şeye bakıyor,
        yanlış animeyi "bu sezon çıkmış" sanıyordu. İki seçenekten (uyarı
        göstererek ikame / boş durum) **boş durum** seçildi: uyarı seçeneği
        başlığı, alt başlığı, durum etiketini ve ipuçlarını sonsuza dek senkron
        tutmayı gerektirir ve bunlardan biri unutulduğunda yalan geri gelir;
        üstelik kullanıcının bir tık ötesinde zaten bir "Trend" sekmesi var.
        Boş durum yanlış bilgi veremez.

    ``home``
        Sezon + trend harmanı (bkz. `_vitrin`). Trend sekmesiyle aynı veriyi
        gösteren ikinci bir gezinti yüzeyi olmasın diye ana sayfanın kendi
        derlemesi var. İki kaynak da boşsa AniList trendine düşülür — vitrin
        "şu an ilginç olan" vaat ediyor, hangi listeden geldiği bir iddia değil.

    ``trending``
        Jikan trendi; boş/hatalıysa AniList trendi. İkisi de aynı soruyu
        yanıtladığı için bu ikame kullanıcıyı yanıltmaz.
    """
    if mode == "season":
        return _jikan_sezon()[:limit]

    if mode == "home":
        sezon = _jikan_sezon()
        trend = _jikan_trend(limit)
        if not sezon and not trend:
            return _anilist_trend(limit)[:limit]
        return _vitrin(sezon, trend, limit)

    return (_jikan_trend(limit) or _anilist_trend(limit))[:limit]



# ── İzlemeye devam et (yerel kitaplık) ───────────────────────────────────────
# Ana sayfa şeridinde en fazla kaç seri: şerit bir kısayol, kitaplığın
# tamamı "Kitaplığım"da.
SERIT_SINIRI = 8


def devam_kayitlari(veri: Optional[Dict[str, Any]] = None,
                    sinir: Optional[int] = None) -> List[Dict[str, Any]]:
    """İzlemeye devam listesi, son bölümün kayıtlı konumuyla birlikte.

    Konum ayrı bir bölümde duruyor (bkz. `kutuphane.konum_kaydet`); kart
    "5. Bölüm · 12:14" diyebilsin diye burada birleştiriliyor.
    """
    veri = veri if veri is not None else kutuphane.oku()
    sonuc = []
    for seri in kutuphane.devam_listesi(veri, sinir):
        kayit = dict(seri)
        son = seri.get("son") or {}
        kayit["konum"] = kutuphane.konum_getir(
            seri["kaynak"], seri["kimlik"], str(son.get("bolum_slug") or ""), veri)
        sonuc.append(kayit)
    return sonuc


def son_bolum_metni(kayit: Dict[str, Any]) -> str:
    """``"5. Bölüm · 12:14"`` — konum yoksa yalnızca bölüm adı."""
    son = kayit.get("son") or {}
    metin = str(son.get("bolum_baslik") or son.get("bolum_slug") or "")
    konum = kayit.get("konum")
    if isinstance(konum, dict) and konum.get("konum"):
        metin += f" · {kutuphane.sure_metni(konum['konum'])}"
    return metin


_ALT_BASLIKLAR = {
    "trending": "Şu anda yayında ve en çok izlenenler",
}


def kaynak_rengi(ad: str) -> str:
    kaynak = kaynak_kaydi.bul(ad)
    return kaynak.renk if kaynak is not None else ""


def devam_karti(kayit: Dict[str, Any]) -> Dict[str, Any]:
    """Kitaplık "devam" kaydından şerit kartı (konum → ilerleme oranı)."""
    konum = kayit.get("konum") if isinstance(kayit.get("konum"), dict) else {}
    ilerleme = None
    try:
        sure = float(konum.get("sure") or 0)
        if sure > 0:
            ilerleme = max(0.0, min(1.0, float(konum.get("konum") or 0) / sure))
    except (TypeError, ValueError):
        ilerleme = None
    kaynak = str(kayit.get("kaynak") or "")
    return {
        "baslik": str(kayit.get("baslik") or kayit.get("kimlik") or ""),
        "kapak": str(kayit.get("kapak") or ""),
        "bolum_metni": son_bolum_metni(kayit),
        "kaynak_adi": kaynak_kaydi.gorunen_ad(kaynak),
        "kaynak_renk": kaynak_rengi(kaynak),
        "ilerleme": ilerleme,
        "kayit": kayit,
    }


class KesifUclari:
    """``kesif``, ``devam_listesi``, ``istatistik``."""

    @uc(arka=True)
    def kesif(self, mod: str = "home", limit: int = 24) -> Dict[str, Any]:
        if mod not in MODES:
            raise UcHatasi(f"bilinmeyen keşif kipi: {mod}")
        limit = max(1, min(int(limit or 24), 50))
        items = fetch_discover(mod, limit)
        altbaslik = _ALT_BASLIKLAR.get(mod, "")
        if mod == "season":
            altbaslik = f"{season_label()} sezonu • MyAnimeList"
        return {
            "mod": mod,
            "altbaslik": altbaslik,
            "kartlar": kartlar(items),
            "bos_mesaj": _BOS_MESAJ.get(mod, _BOS_VARSAYILAN),
        }

    @uc(arka=True)
    def devam_listesi(self, sinir: int = SERIT_SINIRI) -> List[Dict[str, Any]]:
        return [devam_karti(k) for k in devam_kayitlari(sinir=int(sinir or SERIT_SINIRI))]

    @uc(arka=True)
    def istatistik(self) -> Dict[str, Any]:
        """Hero sayıları — hepsi gerçek veri (eski sabit "10.000+" yok).

        Arşiv sayısı `arsiv_durumu`'ndan: ağa ÇIKMAZ; uzak aynada dizin
        henüz inmemişse ``None`` ve sayfa "—" gösteriyor.
        """
        arsiv = None
        try:
            from ...sources import animedepo
            arsiv = animedepo.arsiv_durumu().anime_sayisi
        except Exception as exc:
            print(f"[Web] arşiv durumu okunamadı: {exc}")
        return {
            "arsiv": arsiv,
            "kaynak": len(kaynak_kaydi.kaynaklar(metadata=False)),
            "kitaplik": kutuphane.seri_sayisi(),
        }


__all__ = ["KesifUclari", "devam_karti", "devam_kayitlari", "son_bolum_metni",
           "fetch_discover", "season_label", "MODES", "SERIT_SINIRI"]
