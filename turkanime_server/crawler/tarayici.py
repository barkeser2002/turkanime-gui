"""Tarayıcı çekirdeği — kuyruk döngüsü ve arşiv projeksiyonu.

Akış üç görev türünden ibaret; her biri bir sonrakini kuyruğa besler:

    arama(tohum)   -> (anime_id, başlık)      -> bolumler görevleri
    bolumler(id)   -> (bolum_id, başlık)      -> akislar görevleri
    akislar(bolum) -> [{url, label, ...}]     -> arşive yazılacak veri

Kuyruk asla "bitmez": başarıyla işlenen görev, kaydın tazelik aralığı kadar
ileriye ertelenir. Koşu, o an **hazır** görev kalmayınca (ya da `--limit`
dolunca) sona erer; cron/systemd bir sonraki turu başlatır. Böylece tam tarama
doğal olarak günlere yayılır ve süregelen sezonlar bitmiş serilerden daha sık
ziyaret edilir.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from turkanime_api.common.episode_parser import merge_episodes

from .ayarlar import TarayiciAyarlari
from .durum import DurumDeposu, Gorev
from .eslestirme import KumeDefteri, bolum_slugu
from .kaynaklar import KaynakDefteri, KosulluSonuc
from .nezaket import NezaketBekcisi, NezaketEngeli
from .yazici import ArsivYazici

log = logging.getLogger("turkanime.crawler")

ARAMA, BOLUMLER, AKISLAR = "arama", "bolumler", "akislar"


@dataclass
class Ozet:
    """Bir koşunun sonucu."""

    islenen: int = 0
    basarili: int = 0
    degismeyen: int = 0
    hatali: int = 0
    taze_atlanan: int = 0
    engellenen: List[str] = field(default_factory=list)
    yeni_gorev: int = 0
    sure: float = 0.0
    arsiv: Dict[str, Any] = field(default_factory=dict)

    def sozluk(self) -> Dict[str, Any]:
        return {
            "islenen": self.islenen, "basarili": self.basarili,
            "degismeyen": self.degismeyen, "hatali": self.hatali,
            "taze_atlanan": self.taze_atlanan,
            "engellenen": sorted(set(self.engellenen)),
            "yeni_gorev": self.yeni_gorev,
            "sure": round(self.sure, 2), "arsiv": self.arsiv,
        }


def imzala(veri: Any) -> str:
    """İçerik imzası — koşullu isteğin ETag'siz hâli.

    Kanonik JSON (sıralı anahtar) üzerinden sha256; aynı veri her koşuda aynı
    imzayı verir, sözlük sırası değişse bile.
    """
    ham = json.dumps(veri, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


# "Bu kayıt hep boştu" ile "doluydu, şimdi boşaldı" ayrımı için sabit imza.
BOS_IMZA = imzala([])


class Tarayici:
    """Nazik, devam edebilir, çok kaynaklı arşiv tarayıcısı."""

    def __init__(
        self,
        ayarlar: TarayiciAyarlari,
        *,
        defter: Optional[KaynakDefteri] = None,
        depo: Optional[DurumDeposu] = None,
        bekci: Optional[NezaketBekcisi] = None,
        simdi: Callable[[], float] = time.time,
    ):
        self.ayarlar = ayarlar
        self.defter = defter if defter is not None else KaynakDefteri()
        self._simdi = simdi
        self.depo = depo if depo is not None else DurumDeposu(ayarlar.durum, simdi=simdi)
        self.bekci = bekci if bekci is not None else NezaketBekcisi(ayarlar.nezaket, self.depo)
        self.kume_defteri = KumeDefteri(self.depo, esik=ayarlar.eslesme_esigi)

    # ── Kaynak seçimi ───────────────────────────────────────────────────────
    def hedef_kaynaklar(self) -> List[str]:
        istenen = list(self.ayarlar.kaynaklar) or self.defter.anahtarlar()
        return [k for k in istenen if k in self.defter.tablo]

    # ── Tohumlama ───────────────────────────────────────────────────────────
    def tohumla(self) -> int:
        """Keşif görevlerini kuyruğa koy; eklenen yeni görev sayısını döndür.

        Kaynaklarda "hepsini listele" ucu yok, yalnızca arama var; bu yüzden
        alfabe/rakam tohumlarıyla dolaşıyoruz. Zaten kuyrukta olan tohum tekrar
        eklenmez (INSERT OR IGNORE).
        """
        yeni = 0
        for kaynak in self.hedef_kaynaklar():
            for tohum in self.ayarlar.tohumlar:
                if self.depo.kuyruga_ekle(kaynak, ARAMA, tohum, oncelik=0):
                    yeni += 1
        return yeni

    # ── Ana döngü ───────────────────────────────────────────────────────────
    def calistir(self, arsiv_yaz: bool = True) -> Ozet:
        baslangic = time.monotonic()
        ozet = Ozet()
        ozet.yeni_gorev = self.tohumla()

        if self.ayarlar.kuru_calistir:
            # Kuru koşu ağa hiç çıkmaz: yalnızca kuyruğu tohumlar ve planı
            # raporlar. Tek bir adaptör fonksiyonu çağrılmaz.
            ozet.arsiv = self.plan()
            ozet.sure = time.monotonic() - baslangic
            return ozet

        engelli: Set[str] = set()
        limit = self.ayarlar.limit
        while limit is None or ozet.islenen < limit:
            uygun = [k for k in self.hedef_kaynaklar() if k not in engelli]
            if not uygun:
                break
            gorev = self.depo.sirada_bekleyen(uygun)
            if gorev is None:
                break
            sonuc = self._gorev_isle(gorev, ozet)
            if sonuc is not None:
                engelli.add(sonuc)
                ozet.engellenen.append(sonuc)

        if arsiv_yaz:
            ozet.arsiv = self.arsivi_yaz().ozet()
        ozet.sure = time.monotonic() - baslangic
        return ozet

    def _gorev_isle(self, gorev: Gorev, ozet: Ozet) -> Optional[str]:
        """Tek görevi işle. Kaynak bu koşuda engellendiyse adını döndürür."""
        simdi = self._simdi()

        # ── Koşullu istek: kayıt hâlâ taze mi? ──────────────────────────────
        kayit = self.depo.kayit_oku(gorev.kaynak, gorev.tur, gorev.anahtar)
        if kayit is not None and simdi < kayit.gecerli_ts:
            # Ağa hiç çıkmadan ertele — koşullu isteğin en ucuz hâli.
            self.depo.gorev_ertele(gorev, kayit.gecerli_ts, deneme_artir=False)
            ozet.taze_atlanan += 1
            return None

        uclar = self.defter.uclar(gorev.kaynak)
        if uclar is None:
            # Modül import edilemedi: görev kuyrukta kalsın (bağımlılık
            # düzeltilince bir sonraki koşuda çalışır), kaynak bu tur atlansın.
            self.depo.gorev_birak(gorev)
            return gorev.kaynak

        fn = {ARAMA: uclar.ara, BOLUMLER: uclar.bolumler, AKISLAR: uclar.akislar}[gorev.tur]
        if fn is None:
            # Kaynak bu ucu hiç desteklemiyor — kota harcamadan kapat. Tekrar
            # denemek her turda aynı hatayı verirdi.
            self.depo.gorev_bitti(gorev)
            ozet.islenen += 1
            ozet.hatali += 1
            log.warning("%s '%s' ucunu desteklemiyor; görev kapatıldı",
                        gorev.kaynak, gorev.tur)
            return None

        try:
            with self.bekci.kapi(gorev.kaynak):
                ham = self._getir(fn, gorev, kayit)
        except NezaketEngeli as e:
            # Kota/dinlenme: görev denemesi artmadan kuyruğa döner.
            self.depo.gorev_birak(gorev)
            log.info("%s engellendi: %s", gorev.kaynak, e)
            return gorev.kaynak
        except Exception as e:                        # pylint: disable=broad-except
            bekle = self.bekci.hata_bildir(gorev.kaynak)
            self.depo.gorev_ertele(gorev, simdi + bekle)
            ozet.hatali += 1
            ozet.islenen += 1
            log.warning("%s/%s/%s hata: %s (%.0f sn geri çekilme)",
                        gorev.kaynak, gorev.tur, gorev.anahtar, e, bekle)
            return None

        degismedi = isinstance(ham, KosulluSonuc) and ham.degismedi
        veri = ham.veri if isinstance(ham, KosulluSonuc) else ham
        etag = ham.etag if isinstance(ham, KosulluSonuc) else None
        son_degisiklik = ham.last_modified if isinstance(ham, KosulluSonuc) else None

        if self._bos_gerileme(kayit, degismedi, veri):
            # Kaynak adaptörlerinin çoğu ağ hatasını yutup boş liste döndürüyor
            # (`except Exception: return []`). Dün 200 bölümü olan animenin
            # bugün 0 bölüm dönmesi "yayından kalktı" değil, "site şu an
            # erişilemiyor" demek. Bunu geçerli içerik sayarsak arşivi kendi
            # elimizle boşaltırız; hata muamelesi yapıp geri çekiliyoruz.
            bekle = self.bekci.hata_bildir(gorev.kaynak)
            self.depo.gorev_ertele(gorev, simdi + bekle)
            ozet.hatali += 1
            ozet.islenen += 1
            log.warning("%s/%s/%s dolu kayıt boşaldı — hata sayıldı (%.0f sn)",
                        gorev.kaynak, gorev.tur, gorev.anahtar, bekle)
            return None

        self.bekci.basari_bildir(gorev.kaynak)
        ozet.islenen += 1
        ozet.basarili += 1

        imza = kayit.imza if degismedi else imzala(veri)
        if kayit is not None and kayit.imza == imza:
            degismedi = True

        if degismedi:
            ozet.degismeyen += 1
            # İçerik aynıysa ziyaret aralığını büyüt: bitmiş seriler seyrek,
            # süregelen sezonlar sık ziyaret alsın.
            tazelik = min(max(kayit.tazelik if kayit else 0.0, self.ayarlar.tazelik)
                          * self.ayarlar.tazelik_carpani, self.ayarlar.azami_tazelik)
        else:
            tazelik = self.ayarlar.tazelik

        if not (isinstance(ham, KosulluSonuc) and ham.degismedi):
            # 304 dışındaki her durumda türetilmiş satırları tazele: işlem
            # yereldir, idempotenttir ve önceki koşu yarıda kesilmişse eksik
            # kalan çocuk görevleri tamamlar.
            ozet.yeni_gorev += self._sonucu_isle(gorev, veri)

        gecerli_ts = simdi + tazelik
        self.depo.kayit_yaz(
            gorev.kaynak, gorev.tur, gorev.anahtar,
            imza=imza, tazelik=tazelik, gecerli_ts=gecerli_ts,
            guncel_ts=simdi, etag=etag, last_modified=son_degisiklik,
        )
        # Görev silinmiyor, ileriye erteleniyor: arşiv canlı kalmalı.
        self.depo.gorev_ertele(gorev, gecerli_ts, deneme_artir=False)
        return None

    @staticmethod
    def _bos_gerileme(kayit, degismedi: bool, veri: Any) -> bool:
        """Daha önce dolu olan kayıt boş döndüyse ``True`` (şüpheli sonuç)."""
        if degismedi or kayit is None or kayit.imza is None:
            return False
        return not veri and kayit.imza != BOS_IMZA

    # ── Getirme ─────────────────────────────────────────────────────────────
    @staticmethod
    def _getir(fn: Callable, gorev: Gorev, kayit) -> Any:
        """Adaptör ucunu çağır.

        ETag/Last-Modified destekleyen uçlar ``kosullu=`` argümanını kabul eder;
        etmeyenler (çoğu) düz liste döndürür ve içerik hash'ine düşeriz.
        """
        if getattr(fn, "kosullu", False):
            return fn(gorev.anahtar, kosullu={
                "etag": kayit.etag if kayit else None,
                "last_modified": kayit.last_modified if kayit else None,
            })
        return fn(gorev.anahtar)

    # ── Sonuç işleme ────────────────────────────────────────────────────────
    def _sonucu_isle(self, gorev: Gorev, veri: Any) -> int:
        if gorev.tur == ARAMA:
            return self._arama_sonucu(gorev, veri)
        if gorev.tur == BOLUMLER:
            return self._bolum_sonucu(gorev, veri)
        return self._akis_sonucu(gorev, veri)

    def _arama_sonucu(self, gorev: Gorev, veri: Any) -> int:
        yeni = 0
        for kayit in veri or []:
            if not isinstance(kayit, (list, tuple)) or len(kayit) < 2:
                continue
            anime_id, baslik = str(kayit[0]), str(kayit[1])
            if not anime_id or not baslik:
                continue
            kume = self.kume_defteri.bagla(gorev.kaynak, anime_id, baslik)
            if self.depo.kuyruga_ekle(
                gorev.kaynak, BOLUMLER, anime_id,
                ek={"kume": kume, "baslik": baslik}, oncelik=1,
            ):
                yeni += 1
        return yeni

    def _bolum_sonucu(self, gorev: Gorev, veri: Any) -> int:
        kume = gorev.ek.get("kume")
        if not kume:
            return 0
        yeni = 0
        for sira, kayit in enumerate(veri or []):
            if not isinstance(kayit, (list, tuple)) or len(kayit) < 2:
                continue
            ep_id, baslik = str(kayit[0]), str(kayit[1])
            if not ep_id:
                continue
            self.depo.bolum_yaz(kume, gorev.kaynak, gorev.anahtar, ep_id,
                                baslik or ep_id, sira)
            if self.depo.kuyruga_ekle(
                gorev.kaynak, AKISLAR, ep_id,
                ek={"kume": kume, "anime": gorev.anahtar}, oncelik=2,
            ):
                yeni += 1
        return yeni

    def _akis_sonucu(self, gorev: Gorev, veri: Any) -> int:
        kume = gorev.ek.get("kume")
        if not kume:
            return 0
        temiz = [s for s in (veri or []) if isinstance(s, dict) and s.get("url")]
        self.depo.akis_yaz(kume, gorev.kaynak, gorev.anahtar, temiz)
        return 0

    # ── Arşiv projeksiyonu ──────────────────────────────────────────────────
    def arsivi_yaz(self, yazici: Optional[ArsivYazici] = None) -> ArsivYazici:
        """Biriken durumu AnimeDepo şemasında dosya ağacına dök."""
        yazici = yazici or ArsivYazici(self.ayarlar.cikti, kuru=self.ayarlar.kuru_calistir)
        dizin: List[Tuple[str, str]] = []

        for slug, baslik in self.kume_defteri.kume_listesi():
            bulgular = self.depo.bulgular(slug)
            bolum_satirlari = self.depo.bolumler(slug)
            if not bolum_satirlari:
                # Bölümü olmayan küme arşive girmez: istemci boş klasörü
                # "0 bölüm" diye gösterir, kullanıcı için gürültüden ibaret.
                continue

            akis_haritasi = {
                (a["kaynak"], a["ep_id"]): a["veri"] for a in self.depo.akislar(slug)
            }
            bolumler, akislar = self._bolumleri_birlestir(bolum_satirlari, akis_haritasi)
            if not bolumler:
                continue

            yazici.anime_yaz(
                slug,
                {
                    "slug": slug,
                    "title": baslik,
                    "aliases": sorted({b["baslik"] for b in bulgular}),
                    "sources": {b["kaynak"]: b["kaynak_id"] for b in bulgular},
                    "bolum_sayisi": len(bolumler),
                },
                bolumler,
                akislar,
            )
            dizin.append((slug, baslik))

        yazici.dizin_yaz(dizin)
        return yazici

    def _bolumleri_birlestir(
        self,
        satirlar: Sequence[Dict[str, Any]],
        akis_haritasi: Dict[Tuple[str, str], List[Dict[str, Any]]],
    ) -> Tuple[List[Tuple[str, str]], Dict[str, List[Dict[str, Any]]]]:
        """Kaynak bölüm listelerini tek sıralı listeye indir + akışları topla.

        Birleştirme anahtarı başlık değil ``(sezon, bölüm)``; bunu
        `common.episode_parser.merge_episodes` yapıyor — istemcideki çoklu
        kaynak birleştirmesiyle birebir aynı mantık.
        """
        kaynak_verisi: Dict[str, List[Dict[str, Any]]] = {}
        for r in satirlar:
            kaynak_verisi.setdefault(r["kaynak"], []).append(
                {"title": r["baslik"], "ep_id": r["ep_id"]})

        bolumler: List[Tuple[str, str]] = []
        akislar: Dict[str, List[Dict[str, Any]]] = {}
        kullanilan: Dict[str, int] = {}

        for satir in merge_episodes(kaynak_verisi):
            slug = bolum_slugu(satir["title"]) or f"e{satir['number']:02d}"
            if satir.get("season") and satir.get("number"):
                slug = f"s{int(satir['season']):02d}e{int(satir['number']):02d}"
            if slug in kullanilan:
                kullanilan[slug] += 1
                slug = f"{slug}-{kullanilan[slug]}"
            else:
                kullanilan[slug] = 1

            kayitlar: List[Dict[str, Any]] = []
            for kaynak, girdi in sorted(satir["sources"].items()):
                oynatici = self.defter.tablo[kaynak].oynatici if kaynak in self.defter.tablo \
                    else kaynak.upper()
                for akis in akis_haritasi.get((kaynak, girdi["ep_id"]), []):
                    kayit = {
                        "url": akis.get("url"),
                        "player": oynatici,
                        "fansub": akis.get("label") or "",
                        "alive": True,
                    }
                    for ek in ("type", "referer"):
                        if akis.get(ek):
                            kayit[ek] = akis[ek]
                    kayitlar.append(kayit)

            bolumler.append((slug, satir["title"]))
            akislar[slug] = kayitlar
        return bolumler, akislar

    # ── Kuru koşu planı ─────────────────────────────────────────────────────
    def plan(self) -> Dict[str, Any]:
        """Ağa çıkmadan "ne yapılacaktı" raporu."""
        sayim = self.depo.kuyruk_sayimi()
        return {
            "kuru": True,
            "cikti": str(self.ayarlar.cikti),
            "durum": str(self.ayarlar.durum),
            "kaynaklar": self.hedef_kaynaklar(),
            "tohum_adedi": len(self.ayarlar.tohumlar),
            "kuyruk": sayim,
            "bekleyen": sayim.get("bekliyor", 0),
            "kume_adedi": len(self.kume_defteri.kume_listesi()),
            "kalan_kota": {k: self.bekci.kalan_kota(k) for k in self.hedef_kaynaklar()},
            "gecikme": self.ayarlar.nezaket.gecikme,
        }

    def kapat(self) -> None:
        self.depo.kapat()


__all__ = ["Tarayici", "Ozet", "imzala", "ARAMA", "BOLUMLER", "AKISLAR"]
