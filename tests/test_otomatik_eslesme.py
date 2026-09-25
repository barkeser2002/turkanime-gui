"""Detay sayfasının otomatik kaynak eşleştirmesi (eşik + başlık varyantları).

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

from turkanime_api.gui.qt.pages import detail as detail_mod
from turkanime_api.gui.qt.pages.detail import (
    AnimeMatchDialog, DetailPage, en_iyi_aday, en_iyi_slug, eslesme_basliklari,
)
from turkanime_api.gui.qt.sources_bridge import METADATA_ONLY, supported_sources
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


@pytest.fixture
def page(qtbot):
    widget = DetailPage()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture(autouse=True)
def _kayit_yok(monkeypatch):
    monkeypatch.setattr(detail_mod, "save_match", lambda *a: True)


@pytest.fixture
def diyalog_yasak(monkeypatch, page):
    """Eşleşme bulunması gereken senaryoda diyalog açılırsa test düşsün."""
    def _patla():
        raise AssertionError("otomatik eşleşme varken diyalog açıldı")

    monkeypatch.setattr(page, "open_match_dialog", _patla)


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


@pytest.fixture
def sahte_fetch(monkeypatch):
    cagrilar: list = []

    def fake(source, slug, title):
        cagrilar.append((source, slug))
        return [{"title": "1. Bölüm", "obj": object()}]

    monkeypatch.setattr(detail_mod, "fetch_episodes", fake)
    return cagrilar


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
def test_kesif_karti_arsive_kendiliginden_baglaniyor(qtbot, page, yerel_arsiv,
                                                      diyalog_yasak):
    """Keşif kartı → "Bölümleri Getir": diyalog yok, arşiv birebir eşleşiyor."""
    yerel_arsiv({"sousou-no-frieren": "Sousou no Frieren",
                 "sousou-no-frieren-2": "Sousou no Frieren 2nd Season"})
    page.show_anime({"title": {"romaji": "Sousou no Frieren"}})
    assert page.current_source() == "TürkAnime", "varsayılan kaynak arşiv olmalı"

    with qtbot.waitSignal(page.episodes_ready, timeout=5000) as sinyal:
        page.load_episodes()

    assert page._bindings == {"TürkAnime": "sousou-no-frieren"}
    assert page.current_source() == "TürkAnime"
    assert sinyal.args[:2] == ["TürkAnime", "sousou-no-frieren"]
    assert "Eşleşme: TürkAnime (arşiv) → Sousou no Frieren" in page.lblStatus.text()


def test_romaji_tutmazsa_ingilizce_adla_baglaniyor(qtbot, page, yerel_arsiv,
                                                   diyalog_yasak):
    yerel_arsiv({"the-apothecary-diaries": "The Apothecary Diaries",
                 "kusuriya": "Kusuriya Tenshi"})
    page.show_anime({"title": {"romaji": "Kusuriya no Hitorigoto",
                               "english": "The Apothecary Diaries"}})

    with qtbot.waitSignal(page.episodes_ready, timeout=5000):
        page.load_episodes()
    assert page._bindings == {"TürkAnime": "the-apothecary-diaries"}


def test_yalnizca_benzer_aday_varsa_diyalog_dolu_ve_aramis_aciliyor(
        qtbot, page, sahte_motor, monkeypatch):
    """"[Oshi no Ko]" → yalnızca "Hoshi no Koe": bağlama yok, diyalog açılır."""
    sorgular = sahte_motor({"TürkAnime": kayitlar(("hoshi-no-koe", "Hoshi no Koe"))})
    gorulen: dict = {}

    def sahte_exec(dialog):
        # "Ara"ya BASILMADAN ağaç dolmalı.
        qtbot.waitUntil(lambda: dialog.tree.topLevelItemCount() == 1, timeout=5000)
        gorulen["sorgu"] = dialog.txtQuery.text()
        gorulen["aday"] = dialog.tree.topLevelItem(0).child(0).text(0)
        return 0                            # İptal

    monkeypatch.setattr(AnimeMatchDialog, "exec", sahte_exec)
    page.show_anime({"title": {"romaji": "[Oshi no Ko]"}})
    page.load_episodes()

    qtbot.waitUntil(lambda: "sorgu" in gorulen, timeout=5000)
    assert gorulen == {"sorgu": "[Oshi no Ko]", "aday": "Hoshi no Koe"}
    assert page._bindings == {}
    assert sorgular[0] == "[Oshi no Ko]"
    qtbot.waitUntil(lambda: "bağlı değil" in page.lblStatus.text(), timeout=2000)


def test_diyalogda_secim_yuklemeyi_surduruyor(qtbot, page, sahte_motor,
                                              sahte_fetch, monkeypatch):
    sahte_motor({"TürkAnime": kayitlar(("hoshi-no-koe", "Hoshi no Koe"))})

    def sec(dialog):
        dialog.selection = ("TürkAnime", "oshi-no-ko", "Oshi no Ko")
        return int(AnimeMatchDialog.DialogCode.Accepted)

    monkeypatch.setattr(AnimeMatchDialog, "exec", sec)
    page.show_anime({"title": {"romaji": "[Oshi no Ko]"}})
    with qtbot.waitSignal(page.episodes_ready, timeout=5000):
        page.load_episodes()
    assert sahte_fetch == [("TürkAnime", "oshi-no-ko")]


# ── "Tüm kaynaklar" ─────────────────────────────────────────────────────────
def test_tum_kaynaklar_dogru_adayi_baglayip_eslesmeyeni_soyluyor(
        qtbot, page, sahte_motor, sahte_fetch):
    sahte_motor({
        "TürkAnime": kayitlar(("one-piece", "One Piece")),
        "AnimeciX": kayitlar(("111", "Koisuru One Piece"), ("222", "One Piece")),
        "Anizle": kayitlar(("fan", "One Piece Fan Letter")),
        "AniList": kayitlar(("21", "One Piece")),
    })
    page.show_match("TürkAnime", "one-piece", "One Piece")
    page.chkAllSources.setChecked(True)

    with qtbot.waitSignal(page.episodes_ready, timeout=5000):
        page.load_episodes()

    cekilen = dict(sahte_fetch)
    assert cekilen["AnimeciX"] == "222", "ham ilk sonuç (Koisuru) bağlandı"
    assert "Anizle" not in cekilen, "eşiği geçmeyen aday bağlandı"
    assert "AniList" not in page._bindings
    assert "Eşleşme bulunamayan" in page.lblStatus.text()
    assert "Anizle" in page.lblStatus.text()


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


def test_tek_kaynak_eslestirmesi_yalnizca_o_kaynagi_ariyor(qtbot, page,
                                                           monkeypatch, sahte_fetch):
    cagrilan = _casus_motor(monkeypatch, {"AnimeciX": [("17", "Cowboy Bebop")]})
    page.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    page.cmbSource.setCurrentIndex(page.cmbSource.findData("AnimeciX"))

    with qtbot.waitSignal(page.episodes_ready, timeout=5000):
        page.load_episodes()
    assert cagrilan == ["AnimeciX"]
    assert sahte_fetch == [("AnimeciX", "17")]


def test_tum_kaynaklar_oynatilabilirleri_ariyor_anilisti_asla(qtbot, page,
                                                               monkeypatch, sahte_fetch):
    cagrilan = _casus_motor(monkeypatch)
    page.show_match("TürkAnime", "cowboy-bebop", "Cowboy Bebop")
    page.chkAllSources.setChecked(True)

    with qtbot.waitSignal(page.episodes_ready, timeout=5000):
        page.load_episodes()
    beklenen = {s for s in supported_sources() if s not in METADATA_ONLY} - {"TürkAnime"}
    assert set(cagrilan) == beklenen, "zaten bağlı arşiv de yeniden arandı"
    assert not METADATA_ONLY & set(cagrilan)


# ── AniList arama kartı ─────────────────────────────────────────────────────
def test_anilist_karti_metadata_kaydi_olarak_aciliyor(qtbot, page, sahte_motor,
                                                      sahte_fetch, diyalog_yasak):
    sahte_motor({"TürkAnime": kayitlar(("sousou-no-frieren", "Sousou no Frieren"))})
    page.show_match("AniList", "154587", "Sousou no Frieren",
                    kayit={"slug": "154587", "title": "Sousou no Frieren",
                           "image": "https://img/frieren.jpg"})

    assert "AniList" not in page._bindings
    assert page.cmbSource.findData("AniList") < 0
    assert page._anime["coverImage"] == {"large": "https://img/frieren.jpg"}

    with qtbot.waitSignal(page.episodes_ready, timeout=5000):
        page.load_episodes()
    assert sahte_fetch == [("TürkAnime", "sousou-no-frieren")]
    assert "metadata" not in page.lblStatus.text()


def test_do_resolve_ilgisiz_sonucu_baglamiyor(qtbot, page, sahte_motor):
    sahte_motor({"AnimeciX": kayitlar(("5", "Tamamen Başka Bir Seri"))})
    rid = page.show_anime({"title": {"romaji": "Cowboy Bebop"}})
    yayilan: list = []
    page.sources_resolved.disconnect()
    page.sources_resolved.connect(yayilan.append)

    page._do_resolve(rid, "Cowboy Bebop", {}, False, "AnimeciX", ["Cowboy Bebop"])
    _rid, baglar, _hepsi, _istenen, rapor = yayilan[0]
    assert baglar == {}
    assert rapor["eslesmeyen"] == ["AnimeciX"]


def test_varyant_turu_yalnizca_cevap_veren_eslesmeyenleri_ariyor(page, sahte_motor):
    """İkinci başlıkla tur: hata veren kaynağa yeniden gidilmiyor."""
    from turkanime_api.common.adapters import AramaSonuclari

    def sonuc(sorgu):
        return AramaSonuclari({"AnimeciX": [], "Anizle": []},
                              hatalar={"Anizle": "kapalı"})

    sorgular = sahte_motor(sonuc)
    rid = page.show_anime({"title": {"romaji": "A"}})
    yayilan: list = []
    page.sources_resolved.disconnect()
    page.sources_resolved.connect(yayilan.append)

    page._do_resolve(rid, "A", {}, True, "TürkAnime", ["A", "B", "C", "D"])
    # 3 tur sınırı; AnimeciX cevap verdi ama bulamadı → sonraki varyantlar denendi.
    assert sorgular == ["A", "B", "C"]
    assert "Anizle" in yayilan[0][4]["eslesmeyen"]
