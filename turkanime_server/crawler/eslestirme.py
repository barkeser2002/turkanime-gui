"""Çapraz kaynak eşleştirme — aynı anime beş kaynakta tek kayda bağlanır.

Kaynaklar aynı seriyi farklı yazar: "Kimetsu no Yaiba" / "Demon Slayer" /
"Kimetsu no Yaiba 1. Sezon". Arşivde her seri **tek** klasör olmalı, yoksa
istemci aynı animeyi beş kez listeler.

Eşleştirme `turkanime_api.common.title_match.fuzzy_score` ile yapılır —
rapidfuzz varsa onu, yoksa stdlib'i kullanan, **ağa çıkmayan** skorlayıcı.
`get_title_aliases` bilinçli olarak kullanılmıyor: AniList/Jikan'a çıkar ve
tarayıcının "yalnızca hedef kaynaklara istek" garantisini bozardı.

Bölüm kimlikleri `common.episode_parser` ile normalize edilir; böylece
"Bölüm 5", "5. Bölüm" ve "S01E05" arşivde aynı dosyaya düşer.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from turkanime_api.common.episode_parser import extract_episode_info, parse_episode
from turkanime_api.common.title_match import fuzzy_score

from .durum import DurumDeposu

_TR_HARITA = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


def slugla(metin: str, yedek: str = "anime") -> str:
    """Başlığı dosya sistemi güvenli slug'a çevir.

    Türkçe harfler önce elle eşlenir: NFKD "ı" harfini "i"ye indirgemez ve
    "Ölümsüz" gibi başlıklar aksi hâlde "l-ms-z" olurdu.
    """
    if not metin:
        return yedek
    s = metin.translate(_TR_HARITA)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or yedek


def bolum_slugu(baslik: str) -> str:
    """Bölüm başlığından kanonik dosya adı üret: ``s01e05`` / ``movie`` / ...

    Sezon/bölüm çıkarılamazsa başlığın slug'ına düşer; numarasız özel bölümler
    aksi hâlde hepsi aynı dosyaya yazılıp birbirini silerdi.
    """
    bilgi = parse_episode(baslik or "")
    if bilgi.episode is None and bilgi.label:
        return slugla(bilgi.label, "bolum")
    sezon, bolum = extract_episode_info(baslik or "")
    if bolum:
        return f"s{sezon:02d}e{bolum:02d}"
    return slugla(baslik, "bolum")


class KumeDefteri:
    """Kaynak kayıtlarını küme (arşiv slug'ı) altında toplar.

    Açgözlü kümeleme: her yeni başlık mevcut küme başlıklarına karşı skorlanır,
    eşik üstündeki en iyi kümeye bağlanır, yoksa yeni küme açılır. Küme
    başlıkları `DurumDeposu`dan yüklenir; süreç yeniden başlasa da aynı anime
    aynı slug'a düşer (slug arşiv yolunun parçası, kayması kırık link demek).
    """

    def __init__(self, depo: DurumDeposu, esik: float = 0.90):
        self.depo = depo
        self.esik = esik
        self._basliklar: Dict[str, str] = dict(depo.kumeler())
        self._bulgu_kume: Dict[Tuple[str, str], str] = {
            (b["kaynak"], b["kaynak_id"]): b["kume"] for b in depo.bulgular()
        }

    # ── Sorgu ───────────────────────────────────────────────────────────────
    def kume_bul(self, baslik: str) -> Optional[str]:
        """Başlığa uyan mevcut kümeyi döndür (yoksa ``None``)."""
        if not baslik:
            return None
        aday = slugla(baslik)
        if aday in self._basliklar:
            return aday
        en_iyi: Optional[str] = None
        en_iyi_skor = 0.0
        for slug, mevcut in self._basliklar.items():
            skor = fuzzy_score(baslik, mevcut)
            if skor > en_iyi_skor:
                en_iyi_skor, en_iyi = skor, slug
        return en_iyi if en_iyi_skor >= self.esik else None

    def _bos_slug(self, baslik: str) -> str:
        """Kullanılmayan bir slug üret (çakışmada sayısal son ek)."""
        taban = slugla(baslik)
        if taban not in self._basliklar:
            return taban
        n = 2
        while f"{taban}-{n}" in self._basliklar:
            n += 1
        return f"{taban}-{n}"

    # ── Yazma ───────────────────────────────────────────────────────────────
    def bagla(self, kaynak: str, kaynak_id: str, baslik: str) -> str:
        """(kaynak, id, başlık) üçlüsünü bir kümeye bağla; küme slug'ını döndür."""
        anahtar = (kaynak, str(kaynak_id))
        mevcut = self._bulgu_kume.get(anahtar)
        if mevcut is not None:
            # Kaynak kimliği bir kez bağlandıktan sonra taşınmaz: slug arşiv
            # yolunun parçası ve her turda oynaması istemcide kırık link demek.
            self.depo.bulgu_yaz(kaynak, str(kaynak_id), baslik, mevcut)
            return mevcut

        slug = self.kume_bul(baslik)
        if slug is None:
            slug = self._bos_slug(baslik)
            self._basliklar[slug] = baslik
            self.depo.kume_yaz(slug, baslik)
        self._bulgu_kume[anahtar] = slug
        self.depo.bulgu_yaz(kaynak, str(kaynak_id), baslik, slug)
        return slug

    def kume_basligi(self, slug: str) -> str:
        return self._basliklar.get(slug, slug.replace("-", " ").title())

    def kume_listesi(self) -> List[Tuple[str, str]]:
        return sorted(self._basliklar.items())


__all__ = ["KumeDefteri", "slugla", "bolum_slugu"]
