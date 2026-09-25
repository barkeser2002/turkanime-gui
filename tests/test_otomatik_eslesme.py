"""Otomatik kaynak eşleştirme (eşik + başlık varyantları): `gui/web/eslestirme`
ve detay sayfasının `eslestir` ucu.

ESKİ HATA: `_do_resolve` her kaynaktan TEK aday istiyor ve onu skorsuz
bağlıyordu. `Kaynak.ara` listeyi alaka sıralamasından önce kestiği için bu,
kaynağın ham ilk sonucu demekti ("One Piece" → "Koisuru One Piece").

Ağa çıkılmaz: ya `SearchEngine` sahtelenir ya da gerçek motor yalnızca
`tmp_path`'teki yerel arşivi arar (`arama_motoru` kaynak kısıtı).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from turkanime_api.gui.qt.sources_bridge import METADATA_ONLY, supported_sources
from turkanime_api.gui.web import eslestirme
from turkanime_api.gui.web.eslestirme import (
    en_iyi_aday, en_iyi_slug, eslesme_basliklari, kaynaklari_esle,
)
from turkanime_api.gui.web.uclar_detay import DetayUclari
from turkanime_api.sources import animedepo


# ── Yardımcılar ─────────────────────────────────────────────────────────────
def arsiv_kur(kok: Path, animeler: dict) -> Path:
    """slug → başlık; her animenin tek bölümü var."""
    index: dict = {}
    for slug, baslik in animeler.items():
        index.setdefault(slug[0].upper(), {})[slug] = {"title": baslik}
        klasor = kok / "animeler" / slug
        klasor.mkdir(parents=True, exist_ok=True)
        (klasor / "bolumler.json").write_text(
            json.dumps([[f"{slug}-1-bolum", "1. Bölüm"]]), "utf-8")
    (kok / "dizin.json").write_text(
        json.dumps({"last_update": 1, "index": index}), "utf-8")
    return kok


@pytest.fixture
def yerel_arsiv(tmp_path, monkeypatch):
    def _kur(animeler: dict) -> Path:
        kok = arsiv_kur(tmp_path / "arsiv", animeler)
        monkeypatch.setattr(animedepo, "DEPO_ARSIVI", kok)
        animedepo.sifirla()
        return kok

    yield _kur
    animedepo.sifirla()


@pytest.fixture(autouse=True)
def _kayit_yok(monkeypatch):
    monkeypatch.setattr(eslestirme, "save_match", lambda *a: True)


@pytest.fixture
def uclar():
    return DetayUclari(None, oynat=lambda e: None, indir=lambda e: None)


@pytest.fixture
def sahte_motor(monkeypatch):
    """`SearchEngine`'i sabit sonuçlu sahteyle değiştir; sorguları kaydet."""
    import turkanime_api.common.adapters as adapters_mod

    sorgular: list = []

    def _kur(sonuc):
        class SahteMotor:
            def search_all_sources_rich(self, query, limit_per_source=10):
                sorgular.append(query)
                return sonuc(query) if callable(sonuc) else sonuc

        monkeypatch.setattr(adapters_mod, "SearchEngine", SahteMotor)
        return sorgular

    return _kur


def kayitlar(*ciftler):
    return [{"slug": s, "title": t, "image": None} for s, t in ciftler]


# ── Qt'siz seçim ────────────────────────────────────────────────────────────
def test_birebir_baslik_ham_ilk_sonucu_yeniyor():
    items = kayitlar(("1", "Koisuru One Piece"), ("2", "One Piece"))
    assert en_iyi_slug(items, ["One Piece"]) == "2"


def test_esigi_gecmeyen_tek_aday_baglanmiyor():
    assert en_iyi_slug(kayitlar(("1", "Koisuru One Piece")), ["One Piece"]) == ""
    # Denetimde ölçülen tuzak: "[Oshi no Ko]" arşivde "Hoshi no Koe"ye 0.91.
    assert en_iyi_slug(kayitlar(("hk", "Hoshi no Koe")), ["[Oshi no Ko]"]) == ""
    assert en_iyi_slug([], ["One Piece"]) == ""
    assert en_iyi_slug([None, {"title": "One Piece"}], ["One Piece"]) == ""


def test_ingilizce_ad_da_eslesiyor():
    items = kayitlar(("fr", "Frieren: Beyond Journey's End"))
    assert en_iyi_slug(items, ["Sousou no Frieren"]) == ""
    assert en_iyi_slug(items, ["Sousou no Frieren",
                               "Frieren: Beyond Journey's End"]) == "fr"
    slug, baslik, skor = en_iyi_aday(items, ["Frieren: Beyond Journey's End"])
    assert (slug, baslik, skor) == ("fr", "Frieren: Beyond Journey's End", 1.0)


def test_ek_almis_baslik_kabul_ediliyor():
    """"One Piece (TV)" ve "One Piece İzle" aynı seri (0.99)."""
    assert en_iyi_slug(kayitlar(("tv", "One Piece (TV)")), ["One Piece"]) == "tv"


def test_eslesme_basliklari_sirali_ve_tekrarsiz():
    anime = {"title": {"romaji": "Sousou no Frieren",
                       "english": "Frieren: Beyond Journey's End",
                       "native": "葬送のフリーレン"},
             "synonyms": ["Frieren", "sousou no frieren", "", None]}
    # Japonca ad normalize edilince boş kalıyor (skor zaten 0): sorgu turu
    # harcanmasın diye listeye girmiyor.
    assert eslesme_basliklari(anime, "Sousou no Frieren") == [
        "Sousou no Frieren", "Frieren: Beyond Journey's End", "Frieren"]
    assert eslesme_basliklari({}, "Naruto") == ["Naruto"]
    assert eslesme_basliklari({"title": "Düz"}, "") == ["Düz"]


# ── Arşivle (gerçek motor, ağsız) ───────────────────────────────────────────
def test_kesif_karti_arsive_kendiliginden_baglaniyor(uclar, yerel_arsiv):
    """Keşif kartı: arşiv birebir eşleşiyor, ikinci sezon bağlanmıyor."""
    yerel_arsiv({"sousou-no-frieren": "Sousou no Frieren",
                 "sousou-no-frieren-2": "Sousou no Frieren 2nd Season"})
    rid = uclar.ac_kesif({"title": {"romaji": "Sousou no Frieren"}})
    sonuc = uclar.eslestir(rid, ["TürkAnime"])
    assert uclar.oturum.baglar == {"TürkAnime": "sousou-no-frieren"}
    assert sonuc["yeni"] == ["TürkAnime"]
    (kaynak,) = sonuc["kaynaklar"]
    assert kaynak["eslesme"] == "Sousou no Frieren"     # sayfa "↳ Eşleşme: …" gösterir


def test_romaji_tutmazsa_ingilizce_adla_baglaniyor(uclar, yerel_arsiv):
    yerel_arsiv({"the-apothecary-diaries": "The Apothecary Diaries",
                 "kusuriya": "Kusuriya Tenshi"})
    rid = uclar.ac_kesif({"title": {"romaji": "Kusuriya no Hitorigoto",
                                    "english": "The Apothecary Diaries"}})
    uclar.eslestir(rid, ["TürkAnime"])
    assert uclar.oturum.baglar == {"TürkAnime": "the-apothecary-diaries"}


def test_yalnizca_benzer_aday_varsa_baglanmiyor(uclar, sahte_motor):
    """"[Oshi no Ko]" → yalnızca "Hoshi no Koe" (0.91): bağlama yok; sayfa
    "Otomatik eşleşme bulunamadı" deyip seçim penceresini öneriyor."""
    sorgular = sahte_motor({"TürkAnime": kayitlar(("hoshi-no-koe", "Hoshi no Koe"))})
    rid = uclar.ac_kesif({"title": {"romaji": "[Oshi no Ko]"}})
    sonuc = uclar.eslestir(rid, ["TürkAnime"])
    assert uclar.oturum.baglar == {}
    assert sonuc["yeni"] == [] and sonuc["eslesmeyen"] == ["TürkAnime"]
    assert sorgular[0] == "[Oshi no Ko]"


# ── "Tüm kaynaklar" ─────────────────────────────────────────────────────────
def test_tum_kaynaklar_dogru_adayi_baglayip_eslesmeyeni_soyluyor(uclar, sahte_motor):
    sahte_motor({
        "TürkAnime": kayitlar(("one-piece", "One Piece")),
        "AnimeciX": kayitlar(("111", "Koisuru One Piece"), ("222", "One Piece")),
        "Anizle": kayitlar(("fan", "One Piece Fan Letter")),
        "AniList": kayitlar(("21", "One Piece")),
    })
    rid = uclar.ac_sonuc("TürkAnime", "one-piece", "One Piece")
    sonuc = uclar.eslestir(rid, None)
    baglar = uclar.oturum.baglar
    assert baglar["AnimeciX"] == "222", "ham ilk sonuç (Koisuru) bağlandı"
    assert "Anizle" not in baglar, "eşiği geçmeyen aday bağlandı"
    assert "AniList" not in baglar
    assert "Anizle" in sonuc["eslesmeyen"]


def _casus_motor(monkeypatch, cevap=None):
    """Gerçek `SearchEngine` + kaynak başına casus adaptör (ağ yok)."""
    import turkanime_api.common.adapters as adapters_mod
    from turkanime_api.sources import kayit

    cagrilan: list = []

    class Casus:
        def __init__(self, ad):
            self.ad = ad

        def search_anime(self, query, limit=10):
            cagrilan.append(self.ad)
            return list((cevap or {}).get(self.ad, []))

    class CasusMotor(adapters_mod.SearchEngine):
        def __init__(self):             # noqa: D401 - kayıttaki her kaynak
            self.adapters = {k.ad: Casus(k.ad) for k in kayit.kaynaklar()}

    monkeypatch.setattr(adapters_mod, "SearchEngine", CasusMotor)
    return cagrilan


def test_tek_kaynak_eslestirmesi_yalnizca_o_kaynagi_ariyor(uclar, monkeypatch):
    cagrilan = _casus_motor(monkeypatch, {"AnimeciX": [("17", "Cowboy Bebop")]})
    rid = uclar.ac_sonuc("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    uclar.eslestir(rid, ["AnimeciX"])
    assert cagrilan == ["AnimeciX"]
    assert uclar.oturum.baglar["AnimeciX"] == "17"


def test_tum_kaynaklar_oynatilabilirleri_ariyor_anilisti_asla(uclar, monkeypatch):
    cagrilan = _casus_motor(monkeypatch)
    rid = uclar.ac_sonuc("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    uclar.eslestir(rid, None)
    beklenen = {s for s in supported_sources() if s not in METADATA_ONLY} - {"TürkAnime"}
    assert set(cagrilan) == beklenen, "zaten bağlı arşiv de yeniden arandı"
    assert not METADATA_ONLY & set(cagrilan)


# ── AniList arama kartı ─────────────────────────────────────────────────────
def test_anilist_karti_metadata_kaydi_olarak_aciliyor(uclar, sahte_motor):
    sahte_motor({"TürkAnime": kayitlar(("sousou-no-frieren", "Sousou no Frieren"))})
    rid = uclar.ac_sonuc("AniList", "154587", "Sousou no Frieren",
                         {"slug": "154587", "title": "Sousou no Frieren",
                          "image": "https://img/frieren.jpg"})
    assert uclar.oturum.baglar == {}
    assert uclar.oturum.anime["coverImage"] == {"large": "https://img/frieren.jpg"}
    uclar.eslestir(rid, ["TürkAnime"])
    assert uclar.oturum.baglar == {"TürkAnime": "sousou-no-frieren"}


def test_ilgisiz_sonuc_baglanmiyor(sahte_motor):
    sahte_motor({"AnimeciX": kayitlar(("5", "Tamamen Başka Bir Seri"))})
    baglar, _eslesen, eslesmeyen = kaynaklari_esle(["Cowboy Bebop"], ["AnimeciX"])
    assert baglar == {}
    assert eslesmeyen == ["AnimeciX"]


def test_varyant_turu_yalnizca_cevap_veren_eslesmeyenleri_ariyor(sahte_motor):
    """İkinci başlıkla tur: hata veren kaynağa yeniden gidilmiyor."""
    from turkanime_api.common.adapters import AramaSonuclari

    def sonuc(sorgu):
        return AramaSonuclari({"AnimeciX": [], "Anizle": []},
                              hatalar={"Anizle": "kapalı"})

    sorgular = sahte_motor(sonuc)
    _baglar, _eslesen, eslesmeyen = kaynaklari_esle(["A", "B", "C", "D"],
                                                     ["AnimeciX", "Anizle"])
    # 3 tur sınırı; AnimeciX cevap verdi ama bulamadı → sonraki varyantlar denendi.
    assert sorgular == ["A", "B", "C"]
    assert "Anizle" in eslesmeyen
