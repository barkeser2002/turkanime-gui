"""Sunucu API'si (`turkanime_server/app.py`) — Flask test istemcisiyle, ağsız.

ESKİ HATA: `/search` `threshold`'u doğrudan `float()`a veriyordu. "abc" ya da
boş değer yakalanmayan ValueError → HTTP 500; 1.5, -1, nan, inf ise sessizce
kabul ediliyordu. İstemci hatası 400 ile söylenmeli.

`SOURCES` boşaltılıyor: arama hiçbir kaynağa (dolayısıyla ağa) gitmez.
"""
from __future__ import annotations

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_cors")

from turkanime_server import app as api  # noqa: E402


@pytest.fixture
def istemci(monkeypatch):
    monkeypatch.setattr(api, "SOURCES", {})
    monkeypatch.setitem(api.app.config, "TESTING", True)
    return api.app.test_client()


@pytest.mark.parametrize("deger", ["abc", "", "1.5", "-0.1", "nan", "inf", "-inf"])
def test_search_gecersiz_threshold_400(istemci, deger):
    yanit = istemci.get("/search", query_string={"q": "naruto", "threshold": deger})
    assert yanit.status_code == 400
    assert "threshold" in yanit.get_json()["error"]


@pytest.mark.parametrize("deger", ["0", "0.9", "1", "1.0"])
def test_search_gecerli_threshold_200(istemci, deger):
    yanit = istemci.get("/search", query_string={"q": "naruto", "threshold": deger})
    assert yanit.status_code == 200
    assert yanit.get_json()["query"] == "naruto"


def test_search_threshold_verilmezse_varsayilan(istemci):
    assert istemci.get("/search", query_string={"q": "naruto"}).status_code == 200
