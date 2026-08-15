"""Çok kaynaklı aramanın toplam zaman aşımı GERÇEKTEN sınırlıyor mu?

Ağa çıkılmaz: `SearchEngine.adapters` tümüyle sahte adapterlerle değiştirilir.
Ölçülen şey "sonuç doğru mu" değil, **çağrının ne kadar sürdüğü**: eski kod
`with ThreadPoolExecutor(...)` bağlamından çıkarken `shutdown(wait=True)`
çalıştırıyor ve yavaş kaynağı sonuna kadar bekliyordu; konsola "zaman aşımı"
yazılsa da arama bitmiyor, kullanıcı 45 sn "aranıyor…" görüyordu.
"""
from __future__ import annotations

import threading
import time

import pytest

from turkanime_api.common import adapters as adapters_mod
from turkanime_api.common.adapters import SearchEngine

SINIR = 0.6          # testteki toplam zaman aşımı (sn)
PAY = 3.0            # ölçüm payı: CI yavaş olabilir ama 5 sn'ye asla ulaşmamalı


class HizliAdapter:
    def __init__(self, sonuc):
        self.sonuc = sonuc

    def search_anime(self, query, limit=10):
        return list(self.sonuc)


class YavasAdapter:
    """ISS'in engellediği kaynağı taklit eder: kapı açılana dek geri dönmez."""

    def __init__(self, kapi: threading.Event):
        self.kapi = kapi
        self.basladi = threading.Event()

    def search_anime(self, query, limit=10):
        self.basladi.set()
        self.kapi.wait(5)
        return [("gec", "Geç Kalan")]


@pytest.fixture
def motor(monkeypatch):
    """Sahte adapterli `SearchEngine` + kısaltılmış toplam süre."""
    monkeypatch.setattr(adapters_mod, "OVERALL_SEARCH_TIMEOUT", SINIR)
    kapi = threading.Event()
    yavas = YavasAdapter(kapi)
    motor = SearchEngine()
    motor.adapters = {
        "Hizli": HizliAdapter([("cb", "Cowboy Bebop")]),
        "Yavas": yavas,
    }
    try:
        yield motor, yavas
    finally:
        kapi.set()          # sızan thread'i serbest bırak


def _sure_olc(cagri):
    basla = time.monotonic()
    sonuc = cagri()
    return sonuc, time.monotonic() - basla


def test_arama_toplam_sureyi_gercekten_siniriyor(motor):
    """Yavaş kaynak 5 sn sürüyor; arama 0.6 sn'lik sınırda dönmeli."""
    engine, yavas = motor
    sonuc, gecen = _sure_olc(lambda: engine.search_all_sources("cowboy"))

    assert yavas.basladi.wait(1), "yavaş kaynak hiç çalışmadı, ölçüm anlamsız"
    assert gecen < PAY, f"arama sınırda dönmedi ({gecen:.1f} sn)"
    assert sonuc["Yavas"] == [], "yetişemeyen kaynak boş sayılmalı"


def test_zaman_asiminda_toplanan_sonuclar_kaybolmuyor(motor):
    """Yavaş kaynak yüzünden hızlı kaynağın sonucu atılmamalı."""
    engine, _yavas = motor
    sonuc = engine.search_all_sources("cowboy")
    assert sonuc["Hizli"] == [("cb", "Cowboy Bebop")]
    assert set(sonuc) == {"Hizli", "Yavas"}


def test_zengin_arama_da_sinirda_donuyor(motor):
    """Qt arayüzü `search_all_sources_rich` çağırıyor; asıl senaryo bu."""
    engine, yavas = motor
    sonuc, gecen = _sure_olc(lambda: engine.search_all_sources_rich("cowboy"))

    assert yavas.basladi.wait(1)
    assert gecen < PAY, f"zengin arama sınırda dönmedi ({gecen:.1f} sn)"
    assert sonuc["Hizli"] == [{"slug": "cb", "title": "Cowboy Bebop",
                               "image": None}]
    assert sonuc["Yavas"] == []


def test_yavas_kaynak_yokken_hemen_donuyor(monkeypatch):
    """Sınır bir alt sınır DEĞİL: hepsi hızlıysa beklemeden dönmeli."""
    monkeypatch.setattr(adapters_mod, "OVERALL_SEARCH_TIMEOUT", 5)
    engine = SearchEngine()
    engine.adapters = {"Hizli": HizliAdapter([("cb", "Cowboy Bebop")])}

    sonuc, gecen = _sure_olc(lambda: engine.search_all_sources("cowboy"))
    assert gecen < 1.0
    assert sonuc == {"Hizli": [("cb", "Cowboy Bebop")]}


def test_patlayan_kaynak_digerlerini_dusurmuyor(monkeypatch):
    class Patlayan:
        def search_anime(self, query, limit=10):
            raise RuntimeError("kaynak öldü")

    monkeypatch.setattr(adapters_mod, "OVERALL_SEARCH_TIMEOUT", 5)
    engine = SearchEngine()
    engine.adapters = {"Patlayan": Patlayan(),
                       "Hizli": HizliAdapter([("cb", "Cowboy Bebop")])}

    sonuc = engine.search_all_sources("cowboy")
    assert sonuc["Patlayan"] == []
    assert sonuc["Hizli"] == [("cb", "Cowboy Bebop")]
