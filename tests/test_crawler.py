"""Arka plan arşiv tarayıcısı testleri (Faz 11).

Hiçbiri ağa çıkmaz: kaynaklar sahte adaptörlerle temsil edilir, gecikmeler
enjekte edilmiş saat/uyku ile ölçülür. Gerçek siteye giden tek test yoktur;
olsaydı `@pytest.mark.network` ile işaretlenirdi.

En değerli test `test_arsiv_animedepo_ile_okunuyor`: üretilen ağacı istemcinin
gerçek `sources/animedepo.py` modülüne okutur. Şema kayarsa burası kırmızı olur.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from turkanime_server.crawler.ayarlar import NezaketAyarlari, TarayiciAyarlari
from turkanime_server.crawler.durum import DurumDeposu
from turkanime_server.crawler.eslestirme import bolum_slugu, slugla
from turkanime_server.crawler.kaynaklar import (
    KaynakDefteri, KaynakTanimi, KaynakUclari, KosulluSonuc,
)
from turkanime_server.crawler.nezaket import (
    GunlukTavanAsildi, KaynakDinlenmede, NezaketBekcisi,
)
from turkanime_server.crawler.tarayici import Tarayici
from turkanime_server.crawler.yazici import ArsivYazici, atomik_yaz


# ─────────────────────────────────────────────────────────────────────────────
# Sahte kaynak
# ─────────────────────────────────────────────────────────────────────────────
class SahteKaynak:
    """Bellek içi katalog sunan sahte adaptör; her çağrıyı sayar.

    Katalog biçimi:
        {anime_id: {"baslik": str,
                    "bolumler": [(ep_id, başlık), ...],
                    "akislar": {ep_id: [{"url", "label"}, ...]}}}
    """

    def __init__(self, katalog: Dict[str, Dict[str, Any]]):
        self.katalog = katalog
        self.cagri: Dict[str, int] = {"ara": 0, "bolumler": 0, "akislar": 0}
        self.hata_ver = False

    def ara(self, sorgu: str) -> List[Tuple[str, str]]:
        self.cagri["ara"] += 1
        if self.hata_ver:
            raise RuntimeError("sahte ağ hatası")
        q = (sorgu or "").lower()
        return [(aid, kayit["baslik"]) for aid, kayit in self.katalog.items()
                if q in kayit["baslik"].lower()]

    def bolumler(self, anime_id: str) -> List[Tuple[str, str]]:
        self.cagri["bolumler"] += 1
        return list(self.katalog.get(anime_id, {}).get("bolumler", []))

    def akislar(self, ep_id: str) -> List[Dict[str, str]]:
        self.cagri["akislar"] += 1
        for kayit in self.katalog.values():
            if ep_id in kayit.get("akislar", {}):
                return [dict(s) for s in kayit["akislar"][ep_id]]
        return []

    def uclar(self) -> KaynakUclari:
        return KaynakUclari(self.ara, self.bolumler, self.akislar)


def _katalog(baslik: str, anime_id: str = "1", bolum_sayisi: int = 2,
             ep_bicim: str = "{n}. Bölüm", ek_kalite: str = "720p") -> Dict[str, Any]:
    bolumler = [(f"{anime_id}-ep{n}", ep_bicim.format(n=n))
                for n in range(1, bolum_sayisi + 1)]
    akislar = {
        ep_id: [{"url": f"https://ornek.test/{ep_id}.mp4", "label": ek_kalite,
                 "type": "direct"}]
        for ep_id, _ in bolumler
    }
    return {anime_id: {"baslik": baslik, "bolumler": bolumler, "akislar": akislar}}


def _defter(**kaynaklar: SahteKaynak) -> KaynakDefteri:
    tablo = {
        ad: KaynakTanimi(ad, ad.title(), ad.upper(), (lambda k=k: k.uclar()))
        for ad, k in kaynaklar.items()
    }
    return KaynakDefteri(tablo)


def _ayarlar(tmp_path: Path, **ek) -> TarayiciAyarlari:
    varsayilan = dict(
        cikti=tmp_path / "arsiv",
        durum=tmp_path / "durum" / "tarayici.sqlite3",
        tohumlar=("naruto",),
        nezaket=NezaketAyarlari(gecikme=0.0, jitter=0.0, gunluk_tavan=10_000),
    )
    varsayilan.update(ek)
    return TarayiciAyarlari(**varsayilan)


# ─────────────────────────────────────────────────────────────────────────────
# 1) Çıktı şeması — istemcinin animedepo modülü ağacı gerçekten okuyabiliyor mu?
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def animedepo_yerel(monkeypatch):
    """`sources.animedepo`'yu yerel dosya sisteminden okuyacak hâle getir."""
    from turkanime_api.sources import animedepo

    def bagla(kok: Path):
        def _fetch(path: str):
            return json.loads((kok / path.lstrip("/")).read_text(encoding="utf-8"))
        monkeypatch.setattr(animedepo, "fetch_json", _fetch)
        monkeypatch.setattr(animedepo, "_dizin_cache", None)
        return animedepo

    return bagla


def test_arsiv_animedepo_ile_okunuyor(tmp_path, animedepo_yerel):
    kaynak = SahteKaynak(_katalog("Naruto", "n1", bolum_sayisi=3))
    ayarlar = _ayarlar(tmp_path)
    tarayici = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    ozet = tarayici.calistir()
    tarayici.kapat()

    assert ozet.basarili > 0
    depo = animedepo_yerel(ayarlar.cikti)

    # dizin.json → arama
    sonuclar = depo.search_animedepo("Naruto")
    assert sonuclar, "AnimeDepo istemcisi üretilen dizin.json'da anime bulamadı"
    slug, baslik = sonuclar[0]
    assert baslik == "Naruto"
    assert slug == slugla("Naruto")

    # bolumler.json → bölüm listesi ("anime/bolum" bileşik kimliği)
    bolumler = depo.get_anime_episodes(slug)
    assert len(bolumler) == 3
    ep_id, ep_baslik = bolumler[0]
    assert ep_id == f"{slug}/s01e01"
    assert "1" in ep_baslik

    # {bolum}.json → stream listesi
    akislar = depo.get_episode_streams(ep_id)
    assert akislar, "AnimeDepo istemcisi bölüm JSON'undan stream çıkaramadı"
    assert akislar[0]["url"].endswith(".mp4")
    # İstemci etiketi "Player Fansub" biçiminde kuruyor
    assert "Anizle" in akislar[0]["label"] and "720p" in akislar[0]["label"]


def test_arsivin_referer_bilgisi_istemciye_geciyor(tmp_path, animedepo_yerel):
    """Kaynağın CDN referer'ı arşivden istemciye kadar korunmalı.

    Kayıt kendi referer'ını taşımazsa istemci turkanime.co'yu varsayıyor; başka
    bir kaynağın CDN'i bu referer ile 403 döndürür.
    """
    katalog = _katalog("Naruto", "n1", bolum_sayisi=1)
    for akis in katalog["n1"]["akislar"].values():
        akis[0]["referer"] = "https://tranimaci.test/"

    ayarlar = _ayarlar(tmp_path)
    tarayici = Tarayici(ayarlar, defter=_defter(tranimaci=SahteKaynak(katalog)))
    tarayici.calistir()
    tarayici.kapat()

    depo = animedepo_yerel(ayarlar.cikti)
    ep_id = depo.get_anime_episodes("naruto")[0][0]
    akis = depo.get_episode_streams(ep_id)[0]
    assert akis["referer"] == "https://tranimaci.test/"

    # Referer taşımayan kayıtlarda eski davranış korunuyor
    ham = json.loads((ayarlar.cikti / "animeler" / "naruto" / "s01e01.json")
                     .read_text("utf-8"))
    ham[0].pop("referer")
    (ayarlar.cikti / "animeler" / "naruto" / "s01e01.json").write_text(
        json.dumps(ham), encoding="utf-8")
    assert depo.get_episode_streams(ep_id)[0]["referer"] == "https://www.turkanime.co/"


def test_info_json_kaynak_ve_takma_adlari_tasiyor(tmp_path):
    kaynak = SahteKaynak(_katalog("One Piece", "op"))
    ayarlar = _ayarlar(tmp_path, tohumlar=("one",))
    tarayici = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    tarayici.calistir()
    tarayici.kapat()

    info = json.loads((ayarlar.cikti / "animeler" / "one-piece" / "info.json")
                      .read_text(encoding="utf-8"))
    assert info["title"] == "One Piece"
    assert info["sources"] == {"anizle": "op"}
    assert info["aliases"] == ["One Piece"]
    assert info["bolum_sayisi"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2) Nezaket
# ─────────────────────────────────────────────────────────────────────────────
class Saat:
    """Elle ilerletilen monotonik saat."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_istekler_arasi_gecikme_uygulaniyor(tmp_path):
    saat, uyunan = Saat(), []

    def uyku(sn):
        uyunan.append(sn)
        saat.t += sn

    depo = DurumDeposu(tmp_path / "d.sqlite3")
    bekci = NezaketBekcisi(NezaketAyarlari(gecikme=8.0, jitter=0.0), depo,
                           uyku=uyku, simdi=saat)
    with bekci.kapi("anizle"):
        pass
    assert uyunan == [], "ilk istek beklememeli"
    with bekci.kapi("anizle"):
        pass
    assert uyunan == [8.0], "ikinci istek taban gecikme kadar beklemeli"

    # Farklı kaynak, kendi ritmi — birinin gecikmesi diğerini bağlamaz
    with bekci.kapi("openani"):
        pass
    assert uyunan == [8.0]
    depo.kapat()


def test_jitter_gecikmeyi_dalgalandiriyor(tmp_path):
    saat, uyunan = Saat(), []

    def uyku(sn):
        uyunan.append(sn)
        saat.t += sn

    depo = DurumDeposu(tmp_path / "d.sqlite3")
    # rastgele=1.0 → tavan uç: gecikme * (1 + jitter)
    bekci = NezaketBekcisi(NezaketAyarlari(gecikme=10.0, jitter=0.5), depo,
                           uyku=uyku, simdi=saat, rastgele=lambda: 1.0)
    with bekci.kapi("x"):
        pass
    with bekci.kapi("x"):
        pass
    assert uyunan == [15.0]
    depo.kapat()


def test_kaynak_basina_eszamanlilik_bir(tmp_path):
    depo = DurumDeposu(tmp_path / "d.sqlite3")
    bekci = NezaketBekcisi(NezaketAyarlari(gecikme=0.0, jitter=0.0), depo)

    icerde = 0
    azami = 0
    kilit = threading.Lock()
    basla = threading.Event()

    def calis():
        nonlocal icerde, azami
        basla.wait(2)
        for _ in range(20):
            with bekci.kapi("anizle"):
                with kilit:
                    icerde += 1
                    azami = max(azami, icerde)
                time.sleep(0.001)
                with kilit:
                    icerde -= 1

    isler = [threading.Thread(target=calis) for _ in range(4)]
    for i in isler:
        i.start()
    basla.set()
    for i in isler:
        i.join(10)

    assert azami == 1, f"kaynak başına eşzamanlılık 1 olmalı, {azami} görüldü"
    depo.kapat()


def test_gunluk_tavan_asilinca_kapi_kapaniyor(tmp_path):
    depo = DurumDeposu(tmp_path / "d.sqlite3")
    bekci = NezaketBekcisi(NezaketAyarlari(gecikme=0.0, gunluk_tavan=2), depo)
    for _ in range(2):
        with bekci.kapi("anizle"):
            pass
    with pytest.raises(GunlukTavanAsildi):
        with bekci.kapi("anizle"):
            pass
    # Sayaç kalıcı: süreç yeniden başlasa da tavan sıfırlanmaz
    depo.kapat()
    depo2 = DurumDeposu(tmp_path / "d.sqlite3")
    bekci2 = NezaketBekcisi(NezaketAyarlari(gecikme=0.0, gunluk_tavan=2), depo2)
    assert bekci2.kalan_kota("anizle") == 0
    depo2.kapat()


def test_ustel_geri_cekilme(tmp_path):
    saat = Saat()
    depo = DurumDeposu(tmp_path / "d.sqlite3")
    bekci = NezaketBekcisi(
        NezaketAyarlari(geri_cekilme_tabani=30.0, azami_geri_cekilme=1800.0,
                        ardisik_hata_siniri=99),
        depo, simdi=saat)
    assert bekci.hata_bildir("x") == 30.0
    assert bekci.hata_bildir("x") == 60.0
    assert bekci.hata_bildir("x") == 120.0
    # Dinlenme sürerken kapı kapalı
    with pytest.raises(KaynakDinlenmede):
        with bekci.kapi("x"):
            pass
    bekci.basari_bildir("x")
    with bekci.kapi("x"):
        pass
    assert bekci.hata_bildir("x") == 30.0, "başarı sayacı sıfırlamalı"
    depo.kapat()


def test_hata_alan_kaynak_geri_cekiliyor(tmp_path):
    kaynak = SahteKaynak(_katalog("Bleach", "b1"))
    kaynak.hata_ver = True
    ayarlar = _ayarlar(tmp_path, tohumlar=("bleach",))
    tarayici = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    ozet = tarayici.calistir()
    assert ozet.hatali == 1
    assert tarayici.bekci.dinlenme_kalani("anizle") > 0
    tarayici.kapat()


# ─────────────────────────────────────────────────────────────────────────────
# 3) Koşullu istek
# ─────────────────────────────────────────────────────────────────────────────
def test_degismeyen_kayit_ikinci_turda_yeniden_indirilmiyor(tmp_path):
    kaynak = SahteKaynak(_katalog("Naruto", "n1"))
    ayarlar = _ayarlar(tmp_path)

    t1 = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    t1.calistir()
    t1.kapat()
    ilk = dict(kaynak.cagri)
    assert ilk["ara"] == 1 and ilk["bolumler"] == 1 and ilk["akislar"] == 2

    # İkinci koşu hemen ardından: her kayıt hâlâ taze → tek bir istek bile yok.
    # Görevler tazelik süresi kadar ileriye ertelendiği için kuyruktan bile
    # çıkmazlar; bu koşullu isteğin en ucuz biçimi.
    t2 = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    ozet = t2.calistir()
    t2.kapat()
    assert kaynak.cagri == ilk, "taze kayıtlar için ağa çıkılmamalıydı"
    assert ozet.islenen == 0


def test_taze_kayit_kuyruga_dusse_bile_cekilmiyor(tmp_path):
    """Görev zamanından önce kuyruğa düşerse ikinci savunma hattı devreye girer.

    Süreç `kayit_yaz` ile `gorev_ertele` arasında ölürse görev geçmiş bir
    `hazir_ts` ile kurtarılır. Kayıt defteri hâlâ taze olduğu için istek yine
    yapılmamalı.
    """
    kaynak = SahteKaynak(_katalog("Naruto", "n1"))
    ayarlar = _ayarlar(tmp_path)

    t1 = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    t1.calistir()
    ilk = dict(kaynak.cagri)
    # Tüm görevleri "şimdi hazır" hâline getir (çökme kurtarmasını taklit)
    t1.depo._db.execute("UPDATE kuyruk SET hazir_ts=0")   # pylint: disable=protected-access
    t1.depo._db.commit()                                  # pylint: disable=protected-access
    t1.kapat()

    t2 = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    ozet = t2.calistir()
    t2.kapat()

    assert kaynak.cagri == ilk, "kayıt defteri taze; ağa çıkılmamalıydı"
    assert ozet.taze_atlanan == 4
    assert ozet.islenen == 0


def test_tazelik_dolunca_yeniden_cekiliyor_ve_aralik_buyuyor(tmp_path):
    kaynak = SahteKaynak(_katalog("Naruto", "n1"))
    saat = Saat(1_000_000.0)
    ayarlar = _ayarlar(tmp_path, tazelik=100.0, tazelik_carpani=2.0,
                       azami_tazelik=10_000.0)

    t1 = Tarayici(ayarlar, defter=_defter(anizle=kaynak), simdi=saat)
    t1.calistir()
    kayit = t1.depo.kayit_oku("anizle", "arama", "naruto")
    assert kayit.tazelik == 100.0
    t1.kapat()

    saat.t += 200.0                      # tazelik doldu
    t2 = Tarayici(ayarlar, defter=_defter(anizle=kaynak), simdi=saat)
    ozet = t2.calistir()
    assert ozet.degismeyen > 0, "içerik aynı; 'değişmedi' sayılmalı"
    kayit2 = t2.depo.kayit_oku("anizle", "arama", "naruto")
    assert kayit2.tazelik == 200.0, "değişmeyen kaydın ziyaret aralığı büyümeli"
    t2.kapat()

    # İçerik değişirse aralık tabana döner
    kaynak.katalog["n1"]["bolumler"].append(("n1-ep3", "3. Bölüm"))
    saat.t += 500.0
    t3 = Tarayici(ayarlar, defter=_defter(anizle=kaynak), simdi=saat)
    t3.calistir()
    kayit3 = t3.depo.kayit_oku("anizle", "bolumler", "n1")
    assert kayit3.tazelik == 100.0
    t3.kapat()


def test_etag_304_veriyi_hic_islemiyor(tmp_path):
    """ETag/Last-Modified destekleyen uç 304 dönerse veri işlenmez."""
    gonderilen: List[Dict[str, Any]] = []

    def ara(sorgu, kosullu=None):
        gonderilen.append(kosullu)
        if kosullu and kosullu.get("etag") == "v1":
            return KosulluSonuc(veri=None, etag="v1", degismedi=True)
        return KosulluSonuc(veri=[("a1", "Akira")], etag="v1")

    ara.kosullu = True
    islenen_bolum: List[str] = []

    def bolumler(anime_id):
        islenen_bolum.append(anime_id)
        return [("e1", "1. Bölüm")]

    tanim = KaynakTanimi("sahte", "Sahte", "SAHTE",
                         lambda: KaynakUclari(ara, bolumler, lambda _s: []))
    saat = Saat(500_000.0)
    ayarlar = _ayarlar(tmp_path, tohumlar=("akira",), tazelik=10.0)

    t1 = Tarayici(ayarlar, defter=KaynakDefteri({"sahte": tanim}), simdi=saat)
    t1.calistir()
    ilk_kayit = t1.depo.kayit_oku("sahte", "arama", "akira")
    assert ilk_kayit.etag == "v1"
    assert ilk_kayit.tazelik == 10.0
    t1.kapat()

    saat.t += 100.0
    t2 = Tarayici(ayarlar, defter=KaynakDefteri({"sahte": tanim}), simdi=saat)
    ozet = t2.calistir()
    ikinci_kayit = t2.depo.kayit_oku("sahte", "arama", "akira")
    t2.kapat()

    assert gonderilen[-1] == {"etag": "v1", "last_modified": None}, \
        "saklanan ETag ikinci turda geri gönderilmeli"
    assert ozet.degismeyen >= 1
    assert ikinci_kayit.etag == "v1"
    assert ikinci_kayit.tazelik == 20.0, "304 sonrası ziyaret aralığı büyümeli"


# ─────────────────────────────────────────────────────────────────────────────
# 4) Devam edebilirlik
# ─────────────────────────────────────────────────────────────────────────────
def test_yarida_kesilen_kuyruk_kaldigi_yerden_devam_ediyor(tmp_path):
    kaynak = SahteKaynak(_katalog("Naruto", "n1", bolum_sayisi=4))
    ayarlar = _ayarlar(tmp_path)

    # 1. koşu: yalnızca 2 görev işlensin, sonra süreç "ölsün"
    kesik = Tarayici(_ayarlar(tmp_path, limit=2), defter=_defter(anizle=kaynak))
    ozet1 = kesik.calistir(arsiv_yaz=False)
    kesik.kapat()
    assert ozet1.islenen == 2
    assert kaynak.cagri["akislar"] < 4, "tüm bölümler daha işlenmemeliydi"

    # 2. koşu: aynı durum dosyası — baştan başlamamalı, kaldığı yerden sürmeli
    devam = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    devam.calistir()
    devam.kapat()

    assert kaynak.cagri["ara"] == 1, "tamamlanmış arama tekrar çekilmemeli"
    assert kaynak.cagri["bolumler"] == 1
    assert kaynak.cagri["akislar"] == 4
    bolumler = json.loads(
        (ayarlar.cikti / "animeler" / "naruto" / "bolumler.json").read_text("utf-8"))
    assert len(bolumler) == 4


def test_islemde_kalan_gorev_yeniden_acilista_kurtariliyor(tmp_path):
    yol = tmp_path / "d.sqlite3"
    depo = DurumDeposu(yol)
    depo.kuyruga_ekle("anizle", "arama", "naruto")
    gorev = depo.sirada_bekleyen()
    assert gorev is not None
    assert depo.kuyruk_sayimi() == {"islemde": 1}
    depo.kapat()                          # süreç burada öldü — görev sahipsiz

    yeniden = DurumDeposu(yol)
    assert yeniden.kuyruk_sayimi() == {"bekliyor": 1}
    assert yeniden.sirada_bekleyen() is not None
    yeniden.kapat()


def test_ayni_gorev_iki_kez_kuyruga_girmiyor(tmp_path):
    depo = DurumDeposu(tmp_path / "d.sqlite3")
    assert depo.kuyruga_ekle("anizle", "arama", "naruto") is True
    assert depo.kuyruga_ekle("anizle", "arama", "naruto") is False
    assert depo.kuyruk_sayimi() == {"bekliyor": 1}
    depo.kapat()


# ─────────────────────────────────────────────────────────────────────────────
# 5) Atomik yazım
# ─────────────────────────────────────────────────────────────────────────────
def test_atomik_yazim_yarim_dosya_birakmiyor(tmp_path, monkeypatch):
    from turkanime_server.crawler import yazici as yazici_mod

    hedef = tmp_path / "alt" / "dizin.json"
    assert atomik_yaz(hedef, "eski içerik") is True
    assert hedef.read_text(encoding="utf-8") == "eski içerik"

    def patlayan_replace(*_a, **_k):
        raise OSError("disk doldu")

    monkeypatch.setattr(yazici_mod.os, "replace", patlayan_replace)
    with pytest.raises(OSError):
        atomik_yaz(hedef, "yeni içerik" * 1000)

    # Hedef dokunulmadan kaldı ve geçici dosya temizlendi
    assert hedef.read_text(encoding="utf-8") == "eski içerik"
    artiklar = [p.name for p in hedef.parent.iterdir() if p.name != hedef.name]
    assert artiklar == [], f"geçici dosya artığı kaldı: {artiklar}"


def test_yeni_dosya_yaziminda_da_artik_kalmiyor(tmp_path, monkeypatch):
    from turkanime_server.crawler import yazici as yazici_mod

    hedef = tmp_path / "yeni.json"
    monkeypatch.setattr(yazici_mod.os, "replace",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("kes")))
    with pytest.raises(OSError):
        atomik_yaz(hedef, "veri")
    assert not hedef.exists(), "yarım dosya hedefte görünmemeli"
    assert list(tmp_path.iterdir()) == []


def test_bos_akis_dosyasi_yazilmiyor(tmp_path):
    """Henüz taranmamış bölüm için boş `[]` dosyası arşive girmez."""
    yazici = ArsivYazici(tmp_path)
    yazici.akis_yaz("naruto", "s01e01", [])
    assert not (tmp_path / "animeler" / "naruto" / "s01e01.json").exists()
    assert yazici.atlanan == 1

    yazici.akis_yaz("naruto", "s01e01", [{"url": "https://x.test/a.mp4"}])
    assert (tmp_path / "animeler" / "naruto" / "s01e01.json").exists()

    # Bir kez dolan dosya boşalabilir: tüm kaynaklar öldüyse bunu bilmek gerek
    yazici.akis_yaz("naruto", "s01e01", [])
    icerik = json.loads(
        (tmp_path / "animeler" / "naruto" / "s01e01.json").read_text("utf-8"))
    assert icerik == []


def test_dolu_kayit_bosalinca_hata_sayiliyor(tmp_path):
    """Adaptörlerin yuttuğu ağ hatası arşivi boşaltmamalı.

    Kaynak modüllerinin çoğu `except Exception: return []` yapıyor; dün 4 bölümü
    olan animenin bugün 0 dönmesi "kaldırıldı" değil "site erişilemiyor" demek.
    """
    kaynak = SahteKaynak(_katalog("Naruto", "n1", bolum_sayisi=4))
    saat = Saat(700_000.0)
    ayarlar = _ayarlar(tmp_path, tazelik=50.0)

    t1 = Tarayici(ayarlar, defter=_defter(anizle=kaynak), simdi=saat)
    t1.calistir()
    ilk_imza = t1.depo.kayit_oku("anizle", "bolumler", "n1").imza
    t1.kapat()

    kaynak.katalog["n1"]["bolumler"] = []          # "site çöktü"
    saat.t += 100.0
    t2 = Tarayici(ayarlar, defter=_defter(anizle=kaynak), simdi=saat)
    ozet = t2.calistir()
    assert ozet.hatali >= 1, "boşalan kayıt hata sayılmalıydı"
    assert t2.depo.kayit_oku("anizle", "bolumler", "n1").imza == ilk_imza, \
        "şüpheli boş sonuç kayıt defterine yazılmamalı"
    assert len(t2.depo.bolumler("naruto")) == 4, "eski bölümler silinmemeli"
    assert t2.bekci.dinlenme_kalani("anizle") > 0
    t2.kapat()

    bolumler = json.loads(
        (ayarlar.cikti / "animeler" / "naruto" / "bolumler.json").read_text("utf-8"))
    assert len(bolumler) == 4


def test_bastan_bos_kayit_hata_sayilmiyor(tmp_path):
    """Hiç bölümü olmayan anime normaldir; her turda hata üretmemeli."""
    kaynak = SahteKaynak({"n1": {"baslik": "Naruto", "bolumler": [], "akislar": {}}})
    saat = Saat(800_000.0)
    ayarlar = _ayarlar(tmp_path, tazelik=50.0)

    t1 = Tarayici(ayarlar, defter=_defter(anizle=kaynak), simdi=saat)
    t1.calistir()
    t1.kapat()
    saat.t += 100.0
    t2 = Tarayici(ayarlar, defter=_defter(anizle=kaynak), simdi=saat)
    ozet = t2.calistir()
    t2.kapat()
    assert ozet.hatali == 0


def test_ayni_icerik_tekrar_yazilmiyor(tmp_path):
    """Değişmeyen dosya yeniden yazılmaz — Faz 12'de boş commit üretmesin."""
    yazici = ArsivYazici(tmp_path)
    yazici.bolumler_yaz("naruto", [("s01e01", "1. Bölüm")])
    yazici.dizin_yaz([("naruto", "Naruto")])
    assert (yazici.yazilan, yazici.atlanan) == (2, 0)

    yazici.bolumler_yaz("naruto", [("s01e01", "1. Bölüm")])
    yazici.dizin_yaz([("naruto", "Naruto")])
    assert (yazici.yazilan, yazici.atlanan) == (2, 2), "aynı içerik tekrar yazıldı"

    # İçerik değişince yazılır
    yazici.dizin_yaz([("naruto", "Naruto"), ("bleach", "Bleach")])
    assert yazici.yazilan == 3


# ─────────────────────────────────────────────────────────────────────────────
# 6) Çapraz kaynak eşleştirme
# ─────────────────────────────────────────────────────────────────────────────
def test_iki_kaynaktaki_ayni_anime_tek_kayda_bagleniyor(tmp_path, animedepo_yerel):
    a = SahteKaynak(_katalog("Naruto Shippuuden", "a1", ep_bicim="{n}. Bölüm",
                             ek_kalite="1080p"))
    b = SahteKaynak(_katalog("Naruto Shippuden", "b1", ep_bicim="Bölüm {n}",
                             ek_kalite="720p"))
    ayarlar = _ayarlar(tmp_path, tohumlar=("naruto",))
    tarayici = Tarayici(ayarlar, defter=_defter(anizle=a, openani=b))
    tarayici.calistir()
    tarayici.kapat()

    anime_kok = ayarlar.cikti / "animeler"
    klasorler = sorted(p.name for p in anime_kok.iterdir())
    assert len(klasorler) == 1, f"aynı anime tek klasöre düşmeliydi: {klasorler}"

    info = json.loads((anime_kok / klasorler[0] / "info.json").read_text("utf-8"))
    assert set(info["sources"]) == {"anizle", "openani"}
    assert set(info["aliases"]) == {"Naruto Shippuuden", "Naruto Shippuden"}

    # "1. Bölüm" ve "Bölüm 1" tek satırda birleşti, iki kaynağın da akışı var
    depo = animedepo_yerel(ayarlar.cikti)
    bolumler = depo.get_anime_episodes(klasorler[0])
    assert len(bolumler) == 2, "aynı bölüm iki kez listelenmemeli"
    akislar = depo.get_episode_streams(bolumler[0][0])
    etiketler = {s["label"] for s in akislar}
    assert any("1080p" in e for e in etiketler)
    assert any("720p" in e for e in etiketler)


def test_alakasiz_animeler_ayri_kaliyor(tmp_path):
    a = SahteKaynak(_katalog("Naruto", "a1"))
    b = SahteKaynak(_katalog("Nanatsu no Taizai", "b1"))
    ayarlar = _ayarlar(tmp_path, tohumlar=("na",))
    tarayici = Tarayici(ayarlar, defter=_defter(anizle=a, openani=b))
    tarayici.calistir()
    tarayici.kapat()

    klasorler = sorted(p.name for p in (ayarlar.cikti / "animeler").iterdir())
    assert klasorler == ["nanatsu-no-taizai", "naruto"]


def test_slug_ve_bolum_slugu_normalizasyonu():
    assert slugla("Şeytan Öldüren: Kimetsu no Yaiba") == "seytan-olduren-kimetsu-no-yaiba"
    assert slugla("") == "anime"
    assert bolum_slugu("1. Bölüm") == "s01e01"
    assert bolum_slugu("Bölüm 1") == "s01e01"
    assert bolum_slugu("S02E05") == "s02e05"
    assert bolum_slugu("2. Sezon 5. Bölüm") == "s02e05"
    assert bolum_slugu("Movie") == "movie"


# ─────────────────────────────────────────────────────────────────────────────
# 7) Kuru koşu — ağa çıkmama garantisi
# ─────────────────────────────────────────────────────────────────────────────
def test_kuru_calistir_hicbir_adaptor_cagirmiyor(tmp_path):
    kaynak = SahteKaynak(_katalog("Naruto", "n1"))
    ayarlar = _ayarlar(tmp_path, kuru_calistir=True)
    tarayici = Tarayici(ayarlar, defter=_defter(anizle=kaynak))
    ozet = tarayici.calistir()
    tarayici.kapat()

    assert kaynak.cagri == {"ara": 0, "bolumler": 0, "akislar": 0}
    assert not ayarlar.cikti.exists(), "kuru koşu diske yazmamalı"
    assert ozet.arsiv["kuru"] is True
    assert ozet.arsiv["bekleyen"] == 1


def test_kuru_calistirmada_arsiv_yazici_dosya_uretmiyor(tmp_path):
    yazici = ArsivYazici(tmp_path / "arsiv", kuru=True)
    yazici.anime_yaz("naruto", {"slug": "naruto"}, [("s01e01", "1. Bölüm")],
                     {"s01e01": [{"url": "x"}]})
    yazici.dizin_yaz([("naruto", "Naruto")])
    assert not (tmp_path / "arsiv").exists()
    assert yazici.ozet()["planlanan"] == 4


def test_cli_kuru_calistir(tmp_path, capsys, monkeypatch):
    """CLI kuru koşusu: gerçek kaynak tablosuyla, tek bir istek yapmadan."""
    from turkanime_server.crawler import __main__ as cli

    kod = cli.main([
        "--kuru-calistir",
        "--kaynak", "anizle",
        "--cikti", str(tmp_path / "arsiv"),
        "--durum", str(tmp_path / "durum.sqlite3"),
        "--tohum", "naruto",
    ])
    assert kod == 0
    rapor = json.loads(capsys.readouterr().out)
    assert rapor["arsiv"]["kaynaklar"] == ["anizle"]
    assert rapor["arsiv"]["bekleyen"] == 1
    assert not (tmp_path / "arsiv").exists()


def test_cli_bilinmeyen_kaynagi_reddediyor(tmp_path):
    from turkanime_server.crawler import __main__ as cli

    with pytest.raises(SystemExit):
        cli.main(["--kaynak", "yokboyle", "--kuru-calistir",
                  "--durum", str(tmp_path / "d.sqlite3")])


# ─────────────────────────────────────────────────────────────────────────────
# 8) Kaynak tablosu — istemciyle aynı adaptörleri kullanıyor muyuz?
# ─────────────────────────────────────────────────────────────────────────────
def test_kaynak_tablosu_turkanime_api_adaptorlerini_kullaniyor():
    from turkanime_api.sources import anizle, openani, tranimaci
    from turkanime_server.crawler.kaynaklar import KAYNAKLAR

    defter = KaynakDefteri()
    assert set(KAYNAKLAR) == {"anizle", "openani", "tranimaci", "tranime", "animecix"}
    assert defter.uclar("anizle").ara is anizle.search_anizle
    assert defter.uclar("openani").bolumler is openani.get_anime_episodes
    assert defter.uclar("tranimaci").akislar is tranimaci.get_episode_streams
    # AnimeDepo taranmaz: ürettiğimiz arşivin şeması o, taramak döngü olurdu
    assert "animedepo" not in KAYNAKLAR


def test_desteklenmeyen_uc_kaynagi_devre_disi_birakiyor(tmp_path):
    """Bölüm ucu olmayan kaynak koşuyu düşürmez, yalnızca kendini engeller."""
    tanim = KaynakTanimi("yarim", "Yarim", "YARIM",
                         lambda: KaynakUclari(lambda _q: [("a", "Akira")], None, None))
    ayarlar = _ayarlar(tmp_path, tohumlar=("akira",))
    tarayici = Tarayici(ayarlar, defter=KaynakDefteri({"yarim": tanim}))
    ozet = tarayici.calistir()
    tarayici.kapat()
    assert ozet.hatali == 1
    assert ozet.basarili == 1
