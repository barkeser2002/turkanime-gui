"""Kaynak eşleştirme (Qt'siz): bir animeyi Türkçe kaynaklardaki kaydına bağlamak.

Keşif/izleme listesi kartı MyAnimeList/AniList kaydı taşıyor; oynatmak için
aynı animenin kaynaktaki kimliği (slug) gerekiyor. Otomatik bağlama yalnızca
başlık benzerliği eşiği GEÇERSE yapılıyor; geçmeyen kaynak bağlanmıyor ve
kullanıcı "İstediğin anime değil mi?" penceresinden doğru kaydı seçiyor.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...common.title_match import baslik_normalize, siralama_skoru
from ...sources import kayit as kaynak_kaydi

# Eşleşme diyaloğunda kaynak başına gösterilecek aday sayısı. Amaç doğru kaydı
# bulmak, tam listeyi taramak değil.
MATCH_LIMIT_PER_SOURCE = 8


# Otomatik eşleştirmede kaynak başına aday sayısı. ESKİDEN 1'di ve bu, kaynağın
# HAM ilk sonucunun bağlanması demekti: `Kaynak.ara` listeyi alaka sırasından
# (`adapters._alakaya_gore_sirala`) ÖNCE kesiyor, sıralama tek elemanlı listeyi
# sıralıyordu. "One Piece" araması AnimeciX'te "Koisuru One Piece"e bağlanıyordu.
# Diyalogla aynı sayı: kaynakların çoğu zaten tek sayfa çekiyor, fazlası yalnızca
# kesilmiyor; birebir başlık aday listesine girip sıralamayı kazanıyor.
AUTO_MATCH_LIMIT = MATCH_LIMIT_PER_SOURCE


# Otomatik bağlama eşiği (`siralama_skoru`, 0..1). Ölçümler (bkz. denetim):
#   birebir / "One Piece (TV)" / "One Piece İzle"    1.00 / 0.99 / 0.99
#   "[Oshi no Ko]" → arşivde "Hoshi no Koe"           0.91   ← başka anime
#   "One Piece Fan Letter" / "ONE PIECE (Live Action)" 0.87 / 0.85
#   "Koisuru One Piece" / "Naruto: Shippuuden"         0.74 / 0.77
# Yanlış ve doğru adaylar 0.79-0.91 bandında iç içe; o bantta sessizce bağlamak
# kullanıcıya YANLIŞ animenin bölümlerini oynatmak demek. 0.95 yalnızca
# birebir ve ek-almış ("(TV)", "İzle") başlıkları geçiriyor; geçmeyen kaynak
# bağlanmıyor, gerekirse diyalog açılıyor (bir tık, yanlış anime değil).
OTOMATIK_ESLESME_ESIGI = 0.95


# Eşleşme bulunamayınca sıradaki başlık varyantıyla (İngilizce, eş anlamlı…)
# en çok kaç sorgu atılır. Ağ kaynağında her tur 25 sn'ye kadar sürebilir.
ESLESME_SORGU_SINIRI = 3


def eslesme_basliklari(anime: Dict[str, Any], yedek: str = "") -> List[str]:
    """Otomatik eşleştirmede denenecek başlıklar (sıralı, tekrarsız).

    Sıra: ekrandaki başlık (arama sorgusu/`_match_title`), romaji, İngilizce,
    eş anlamlılar (AniList `synonyms`), Japonca. Arşiv başlıkları çoğunlukla
    MAL/AniList romajisi; diğer siteler İngilizce ya da Türkçe adı
    kullanabiliyor ("Frieren: Beyond Journey's End" romajiye karşı 0.31).
    Normalize biçimi aynı olanlar ("Dr. Stone" / "Dr Stone") bir kez sayılır;
    normalize edilince boş kalanlar (Japonca yazı: `siralama_skoru` onlara
    zaten 0 veriyor) hiç girmez — ağ kaynağında boşa bir sorgu turu olurdu.
    """
    adaylar: List[Any] = [yedek]
    basliklar = anime.get("title") if isinstance(anime, dict) else None
    if isinstance(basliklar, dict):
        adaylar += [basliklar.get("romaji"), basliklar.get("english")]
    elif isinstance(basliklar, str):
        adaylar.append(basliklar)
    esler = anime.get("synonyms") if isinstance(anime, dict) else None
    if isinstance(esler, list):
        adaylar += esler
    if isinstance(basliklar, dict):
        adaylar.append(basliklar.get("native"))

    out: List[str] = []
    gorulen = set()
    for aday in adaylar:
        if not isinstance(aday, str) or not aday.strip():
            continue
        anahtar = baslik_normalize(aday)
        if anahtar and anahtar not in gorulen:
            gorulen.add(anahtar)
            out.append(aday.strip())
    return out


def en_iyi_aday(items: Any, basliklar: List[str],
                esik: float = OTOMATIK_ESLESME_ESIGI) -> Tuple[str, str, float]:
    """Eşiği geçen en iyi aday: ``(slug, başlık, skor)``; yoksa ``("", "", skor)``.

    Aday skoru, başlık varyantlarının EN İYİSİ (romajiyle değil İngilizce
    adla birebir eşleşen de bağlanır). Eşit skorda kaynağın kendi sırası
    korunur (ilk gelen kazanır).
    """
    en_iyi: Tuple[str, str, float] = ("", "", 0.0)
    for item in (items or []):
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        baslik = str(item.get("title") or "")
        skor = max((siralama_skoru(b, baslik) for b in basliklar if b), default=0.0)
        if skor > en_iyi[2]:
            en_iyi = (str(item["slug"]), baslik, skor)
    if en_iyi[2] >= esik:
        return en_iyi
    return "", "", en_iyi[2]


def en_iyi_slug(items: Any, basliklar: List[str],
                esik: float = OTOMATIK_ESLESME_ESIGI) -> str:
    """`en_iyi_aday`'ın yalnızca slug'ı: eşiği geçen aday yoksa ``""``."""
    return en_iyi_aday(items, basliklar, esik)[0]


def save_match(source: str, slug: str, title: str) -> bool:
    """Kullanıcının seçtiği eşleşmeyi API'ye kaydet.

    Kaydetmek "nice to have": API kapalıysa ya da kullanıcı çevrimdışıysa
    detay sayfası çalışmaya devam etmeli, bu yüzden her hata yutulur.
    """
    try:
        from ...common.db import APIManager

        return bool(APIManager().save_anime_match(source, str(slug), title))
    except Exception as exc:            # ağ/import hatası akışı kesmemeli
        print(f"[Detay] Eşleşme kaydedilemedi: {exc}")
        return False



def kaynaklari_esle(basliklar: List[str], hedefler: List[str],
                    bagli: Optional[set] = None, limit: Optional[int] = None
                    ) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
    """Kaynak başına EŞİĞİ GEÇEN en iyi adayı bul.

    Dönüş ``(bağlar, eşleşen başlıklar, eşleşmeyenler)``. Yalnızca istenen
    kaynaklar aranır (`arama_motoru`): arşiv tek başına istendiğinde arama ağa
    hiç çıkmıyor. Eşleşme çıkmayan ama CEVAP veren kaynaklar sıradaki başlık
    varyantıyla (İngilizce, eş anlamlı...) yeniden aranır; hata veren kaynağa
    ikinci tur yalnızca bekleme demek. İlk tur tümden patlarsa istisna
    çağırana gider.
    """
    from ...common.adapters import arama_motoru
    limit = limit or AUTO_MATCH_LIMIT
    bagli = set(bagli or ())
    baglar: Dict[str, str] = {}
    eslesen: Dict[str, str] = {}
    aranacak = [h for h in hedefler if kaynak_kaydi.kanonik_ad(h) not in bagli]
    for tur, sorgu in enumerate([b for b in basliklar if b][:ESLESME_SORGU_SINIRI]):
        if not aranacak:
            break
        try:
            sonuc = arama_motoru(aranacak).search_all_sources_rich(
                sorgu, limit_per_source=limit)
        except Exception:
            if tur == 0:
                raise
            break
        hatalar = getattr(sonuc, "hatalar", None) or {}
        cevaplayan = set()
        for kaynak, kayitlar in (sonuc or {}).items():
            if kaynak not in aranacak or kaynak_kaydi.kanonik_ad(kaynak) in bagli:
                continue
            if kaynak not in hatalar:
                cevaplayan.add(kaynak)
            slug, bulunan, _skor = en_iyi_aday(kayitlar, basliklar)
            if slug:
                baglar[kaynak] = slug
                eslesen[kaynak] = bulunan
                bagli.add(kaynak_kaydi.kanonik_ad(kaynak))
        aranacak = [k for k in aranacak if k in cevaplayan and k not in baglar]
    eslesmeyen = [h for h in hedefler if h not in baglar
                  and kaynak_kaydi.kanonik_ad(h) not in bagli]
    return baglar, eslesen, eslesmeyen


__all__ = ["eslesme_basliklari", "en_iyi_aday", "en_iyi_slug", "save_match",
           "kaynaklari_esle", "MATCH_LIMIT_PER_SOURCE", "AUTO_MATCH_LIMIT",
           "OTOMATIK_ESLESME_ESIGI", "ESLESME_SORGU_SINIRI"]
