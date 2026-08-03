"""Arşiv yazıcı — AnimeDepo şemasında dosya ağacı üretir.

Şema `turkanime_api/sources/animedepo.py` tarafından tanımlı; istemci hiç
değişmeden okuyabilsin diye birebir uyulur:

    dizin.json                            {"index": {grup: {slug: {"title": ...}}}}
    animeler/{slug}/info.json             anime metadata
    animeler/{slug}/bolumler.json         [[bolum_slug, başlık], ...]
    animeler/{slug}/{bolum_slug}.json     [{url, player, fansub, alive}, ...]

**Atomik yazım.** Faz 12 bu ağacı git'e yayınlayacak ve istemciler onu ham URL
olarak okuyacak; yazımın ortasında ölen bir süreç yarım JSON bırakırsa istemci
tarafında `JSONDecodeError` olur. Bu yüzden her dosya önce aynı dizindeki geçici
bir isme yazılır, fsync'lenir, sonra `os.replace` ile yerine geçer — okuyucu ya
eski ya yeni dosyayı görür, arası yoktur.

İçeriği değişmeyen dosya hiç yazılmaz: git'te gereksiz commit gürültüsü olmaz
ve dosya mtime'ı "bu kayıt gerçekten değişti" bilgisini korur.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


def _simdi_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomik_yaz(yol: Path, icerik: str) -> bool:
    """``yol``a atomik yaz. İçerik aynıysa dokunma. ``True`` = yazıldı."""
    yol = Path(yol)
    veri = icerik.encode("utf-8")
    if yol.exists():
        try:
            if yol.read_bytes() == veri:
                return False
        except OSError:
            pass                      # okunamıyorsa üzerine yaz

    yol.parent.mkdir(parents=True, exist_ok=True)
    # Geçici dosya AYNI dizinde olmalı: `os.replace` yalnızca aynı dosya
    # sisteminde atomik, /tmp farklı bir mount olabilir.
    gecici = yol.with_name(f".{yol.name}.{os.getpid()}.tmp")
    try:
        with open(gecici, "wb") as f:
            f.write(veri)
            f.flush()
            os.fsync(f.fileno())
        os.replace(gecici, yol)
        return True
    except BaseException:
        # Yarım geçici dosya bırakma; hedef dosyaya hiç dokunulmadı.
        try:
            gecici.unlink()
        except OSError:
            pass
        raise


def json_yaz(yol: Path, veri: Any) -> bool:
    """JSON'u kanonik (sıralı anahtar, 2 boşluk) biçimde atomik yaz."""
    metin = json.dumps(veri, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return atomik_yaz(yol, metin)


def _grup(baslik: str) -> str:
    """dizin.json'daki gruplama anahtarı — ilk harf, değilse '#'."""
    for ch in (baslik or "").strip():
        if ch.isalnum():
            return ch.upper()
    return "#"


class ArsivYazici:
    """AnimeDepo ağacını üretir. ``kuru=True`` iken hiçbir şey diske inmez."""

    def __init__(self, kok: Path | str, kuru: bool = False):
        self.kok = Path(kok)
        self.kuru = kuru
        self.yazilan = 0
        self.atlanan = 0
        self.planlanan: List[str] = []

    # ── Alt seviye ──────────────────────────────────────────────────────────
    def _yaz(self, goreli: str, veri: Any) -> None:
        if self.kuru:
            self.planlanan.append(goreli)
            return
        if json_yaz(self.kok / goreli, veri):
            self.yazilan += 1
        else:
            self.atlanan += 1

    # ── Şema ────────────────────────────────────────────────────────────────
    def dizin_yaz(self, kayitlar: Sequence[Tuple[str, str]]) -> None:
        """``dizin.json``: [(slug, başlık), ...] → gruplu indeks.

        ``guncelleme`` damgası yalnızca indeks gerçekten değiştiğinde tazelenir.
        Her koşuda damga atmak, arşivin en çok indirilen dosyasını her turda
        değiştirir: git'te boş commit, istemcide gereksiz indirme demek.
        """
        index: Dict[str, Dict[str, Dict[str, str]]] = {}
        for slug, baslik in kayitlar:
            index.setdefault(_grup(baslik), {})[slug] = {"title": baslik}

        if not self.kuru and self._mevcut_index() == index:
            self.atlanan += 1
            return

        self._yaz("dizin.json", {
            "surum": 1,
            "guncelleme": _simdi_iso(),
            "adet": len(kayitlar),
            "index": index,
        })

    def _mevcut_index(self) -> Any:
        yol = self.kok / "dizin.json"
        try:
            return json.loads(yol.read_text(encoding="utf-8")).get("index")
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def info_yaz(self, slug: str, bilgi: Dict[str, Any]) -> None:
        self._yaz(f"animeler/{slug}/info.json", bilgi)

    def bolumler_yaz(self, slug: str, bolumler: Sequence[Tuple[str, str]]) -> None:
        """``bolumler.json``: istemci [slug, başlık] çiftleri bekliyor."""
        self._yaz(f"animeler/{slug}/bolumler.json",
                  [[ep_slug, baslik] for ep_slug, baslik in bolumler])

    def akis_yaz(self, slug: str, bolum_slug: str,
                 akislar: Iterable[Dict[str, Any]]) -> None:
        """Bölümün stream listesi.

        Henüz taranmamış bölüm için boş dosya yazılmaz: tarama günlere yayıldığı
        için `bolumler.json` her zaman stream'lerden önce dolar ve boş `[]`
        dosyaları arşivi binlerce anlamsız dosyayla şişirirdi. İstemci eksik
        dosyayı zaten "stream yok" diye okuyor. Bir kez dolmuş dosya ise boş
        listeyle güncellenir — kaynakların hepsi ölmüşse bunu bilmek gerekir.
        """
        goreli = f"animeler/{slug}/{bolum_slug}.json"
        kayitlar = list(akislar)
        if not kayitlar and not (self.kok / goreli).exists():
            self.atlanan += 1
            return
        self._yaz(goreli, kayitlar)

    def anime_yaz(
        self,
        slug: str,
        bilgi: Dict[str, Any],
        bolumler: Sequence[Tuple[str, str]],
        akislar: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        """Tek bir anime klasörünü baştan sona yaz."""
        self.info_yaz(slug, bilgi)
        self.bolumler_yaz(slug, bolumler)
        for bolum_slug, kayitlar in akislar.items():
            self.akis_yaz(slug, bolum_slug, kayitlar)

    def ozet(self) -> Dict[str, Any]:
        return {
            "kok": str(self.kok),
            "kuru": self.kuru,
            "yazilan": self.yazilan,
            "atlanan": self.atlanan,
            "planlanan": len(self.planlanan),
        }


__all__ = ["ArsivYazici", "atomik_yaz", "json_yaz"]
