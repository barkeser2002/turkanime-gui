"""Detay sayfası performansı: kuyruk rozetleri GUI thread'ini tıkamamalı.

Ölçülen sorun (v10.3.1): detay sayfası açıkken HER indirme durum değişiminde
(`kuyruk_degisti`) bütün `bolum_durumlari` yeniden koşuyordu. Bu da GUI
thread'inde:

* satır başına `prefs.indirme_dizini()` (disk okuma + `isdir`) — 1000 bölümlük
  seride ~110 ms (ölçüldü),
* ağır `gecmis.json` (2,3 MB kullanıcıda ~24 ms) ve kitaplık JSON'unun
  değişmediği hâlde yeniden okunması.

Düzeltme: toplu kuyruk denetimi indirme klasörünü BİR KEZ çözer ve hafif
`kuyruk_durumlari` ucu diske hiç çıkmaz. Ağ yok (conftest soketleri kapatır).
"""
from __future__ import annotations

import pytest

from turkanime_api.common import kutuphane
from turkanime_api.gui.qt import prefs
from turkanime_api.gui.web.uclar_detay import DetayUclari


class _Anime:
    def __init__(self, slug):
        self.slug = slug


class _Bolum:
    def __init__(self, seri, slug):
        self.anime = _Anime(seri)
        self.slug = slug


def _bolumler(seri, adet):
    return [{"title": f"{i}. Bölüm", "obj": _Bolum(seri, f"{seri}-{i}"),
             "kaynak": "TürkAnime", "kimlik": seri}
            for i in range(1, adet + 1)]


def _patla(mesaj):
    raise AssertionError(mesaj)


@pytest.fixture
def izole_ev(tmp_path, monkeypatch):
    monkeypatch.setattr(kutuphane, "kutuphane_yolu",
                        lambda: str(tmp_path / "kutuphane.json"))
    return tmp_path


def _uclar(**kw):
    return DetayUclari(None, oynat=lambda e: None, indir=lambda e: None, **kw)


# ── kuyruk_durumlari: hafif uç ────────────────────────────────────────────────
def test_kuyruk_durumlari_diske_cikmaz(izole_ev, monkeypatch):
    """`kuyruk_degisti` yolu geçmiş/kitaplık JSON'unu HİÇ okumamalı."""
    monkeypatch.setattr(prefs.Gecmis, "yukle",
                        classmethod(lambda cls: _patla("geçmiş okundu")))
    monkeypatch.setattr(kutuphane, "oku",
                        lambda *a, **k: _patla("kitaplık okundu"))
    cagri = {"n": 0, "boyut": []}

    def toplu(entries):
        cagri["n"] += 1
        cagri["boyut"].append(len(entries))
        return [False] * len(entries)

    uclar = _uclar(kuyrukta=lambda e: _patla("satır başına çağrıldı"),
                   kuyrukta_toplu=toplu)
    rid = uclar.ac_sonuc("TürkAnime", "naruto", "Naruto")
    uclar.oturum.bolumler["TürkAnime"] = _bolumler("naruto", 500)

    sonuc = uclar.kuyruk_durumlari(rid)

    assert sonuc["durumlar"]["TürkAnime"] == [False] * 500
    # Kaynak başına TEK toplu çağrı (satır başına 500 değil).
    assert cagri["n"] == 1
    assert cagri["boyut"] == [500]


def test_kuyruk_durumlari_toplu_yoksa_satir_basina_duser(izole_ev):
    """Toplu denetim verilmezse (sayfasız kurulum) tek bölüm tahminine düşülür."""
    uclar = _uclar(kuyrukta=lambda e: e["obj"].slug == "naruto-3")
    rid = uclar.ac_sonuc("TürkAnime", "naruto", "Naruto")
    uclar.oturum.bolumler["TürkAnime"] = _bolumler("naruto", 5)

    sonuc = uclar.kuyruk_durumlari(rid)

    assert sonuc["durumlar"]["TürkAnime"] == [False, False, True, False, False]


# ── bolum_durumlari: toplu kuyruk denetimini kullanmalı ───────────────────────
def test_bolum_durumlari_toplu_kuyruk_kullanir(izole_ev, monkeypatch):
    """`bolum_durumlari` satır başına değil, kaynak başına BİR KEZ denetler."""
    class Gecmis:
        def durum(self, _bolum):
            return (False, False)

    monkeypatch.setattr(prefs.Gecmis, "yukle", classmethod(lambda cls: Gecmis()))
    cagri = {"n": 0}

    def toplu(entries):
        cagri["n"] += 1
        return [False] * len(entries)

    uclar = _uclar(kuyrukta=lambda e: _patla("satır başına çağrıldı"),
                   kuyrukta_toplu=toplu)
    rid = uclar.ac_sonuc("TürkAnime", "naruto", "Naruto")
    uclar.oturum.bolumler["TürkAnime"] = _bolumler("naruto", 100)

    durum = uclar.bolum_durumlari(rid)

    assert len(durum["durumlar"]["TürkAnime"]) == 100
    assert cagri["n"] == 1


# ── DownloadManager.kuyruktaki_hedefler: yalnızca bitmemiş işler ───────────────
def test_kuyruktaki_hedefler_yalniz_bitmemis_isler(izole_ev):
    from turkanime_api.gui.qt.indirme import (
        DURUM_INDIRILIYOR, DURUM_TAMAMLANDI, DownloadManager, _Is,
    )

    mgr = DownloadManager()
    ini = _Is("dl1", {"obj": _Bolum("naruto", "naruto-1")}, "1", "/out")
    ini.hedef = "/out/a.mp4"
    ini.durum = DURUM_INDIRILIYOR
    bitmis = _Is("dl2", {"obj": _Bolum("naruto", "naruto-2")}, "2", "/out")
    bitmis.hedef = "/out/b.mp4"
    bitmis.durum = DURUM_TAMAMLANDI
    mgr._jobs = {"dl1": ini, "dl2": bitmis}

    assert mgr.kuyruktaki_hedefler() == {"/out/a.mp4"}


# ── Pencere yardımcısı: indirme klasörünü BİR KEZ çözmeli ─────────────────────
def test_web_kuyrukta_toplu_indirme_dizinini_bir_kez_cozer(main_window, monkeypatch):
    """1000 bölümlük seri + süren indirme: klasör satır başına değil, bir kez."""
    sayac = {"n": 0}
    orig = prefs.indirme_dizini

    def sayan(*a, **k):
        sayac["n"] += 1
        return orig(*a, **k)

    # Aktif hedef kümesi boş olmasın ki per-satır yol hesabı yoluna girilsin.
    monkeypatch.setattr(main_window.downloads, "kuyruktaki_hedefler",
                        lambda: {"/bir/hedef.mp4"})
    monkeypatch.setattr(prefs, "indirme_dizini", sayan)

    entries = [{"obj": _Bolum("naruto", f"naruto-{i}")} for i in range(1000)]
    bayraklar = main_window._web_kuyrukta_toplu(entries)

    assert len(bayraklar) == 1000
    # Eski kod satır başına okurdu (1000); artık refresh başına bir kez.
    assert sayac["n"] == 1
