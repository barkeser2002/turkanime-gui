"""Jikan istemcisi — geçici sunucu hatalarında yeniden deneme.

Canlıda gözlendi: Jikan 504 verince trend listesi tek denemede boş dönüyordu.
Bu testler ağa çıkmaz; `session.get` ve modül düzeyindeki `time` sahtelenir
(gerçek geri çekilme beklemesi test süresine yansımasın diye).
"""
from __future__ import annotations

import pytest
import requests

import turkanime_api.jikan_client as jikan_mod
from turkanime_api.jikan_client import JikanClient


class FakeResponse:
    """`requests.Response`'un testte kullanılan yüzeyi."""

    def __init__(self, status_code: int, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": []}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self)


class FakeClock:
    """Modül düzeyindeki `time` yerine geçer; uyumaz, kaydeder."""

    def __init__(self):
        self.sleeps: list[float] = []
        self._now = 0.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds

    def time(self) -> float:
        return self._now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(jikan_mod, "time", fake)
    return fake


@pytest.fixture
def client():
    """Önbelleksiz kullanılacak taze istemci (disk'e dokunulmasın diye)."""
    return JikanClient()


def make_session(responses):
    """`responses` sırasını dönen sahte session.get; çağrıları kaydeder."""
    calls: list = []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params})
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    _get.calls = calls
    return _get


def test_retries_until_success_after_504(client, clock, monkeypatch):
    """504 → 504 → 200: sonuç dönmeli, aradaki bekleme üstel olmalı."""
    payload = {"data": [{"mal_id": 1, "title": "Naruto"}]}
    get = make_session([FakeResponse(504), FakeResponse(504),
                        FakeResponse(200, payload)])
    monkeypatch.setattr(client.session, "get", get)

    result = client._make_request("/top/anime", use_cache=False)

    assert result == payload
    assert len(get.calls) == 3
    # Geri çekilme: ~1sn sonra 1., ~2sn sonra 2. yeniden deneme
    backoffs = [s for s in clock.sleeps if s >= 1.0]
    assert len(backoffs) == 2
    assert 1.0 <= backoffs[0] < 1.5
    assert 2.0 <= backoffs[1] < 2.5


def test_gives_up_after_max_attempts(client, clock, monkeypatch):
    get = make_session([FakeResponse(503)])
    monkeypatch.setattr(client.session, "get", get)

    assert client._make_request("/top/anime", use_cache=False) is None
    assert len(get.calls) == JikanClient.RETRY_ATTEMPTS


def test_no_retry_on_client_error(client, clock, monkeypatch):
    """404 kalıcı hatadır; tekrar denemek yalnızca kullanıcıyı bekletir."""
    get = make_session([FakeResponse(404)])
    monkeypatch.setattr(client.session, "get", get)

    assert client._make_request("/anime/0", use_cache=False) is None
    assert len(get.calls) == 1


def test_retries_on_timeout_and_connection_error(client, clock, monkeypatch):
    payload = {"data": [1]}
    get = make_session([
        requests.exceptions.ConnectionError("bağlantı koptu"),
        requests.exceptions.Timeout("zaman aşımı"),
        FakeResponse(200, payload),
    ])
    monkeypatch.setattr(client.session, "get", get)

    assert client._make_request("/seasons/now", use_cache=False) == payload
    assert len(get.calls) == 3


def test_rate_limit_429_is_retried_and_honours_retry_after(client, clock, monkeypatch):
    payload = {"data": []}
    get = make_session([
        FakeResponse(429, headers={"Retry-After": "5"}),
        FakeResponse(200, payload),
    ])
    monkeypatch.setattr(client.session, "get", get)

    assert client._make_request("/top/anime", use_cache=False) == payload
    assert 5.0 in clock.sleeps


def test_absurd_retry_after_is_capped(client, clock, monkeypatch):
    """Bozuk/kötü niyetli Retry-After arayüzü dakikalarca dondurmamalı."""
    get = make_session([FakeResponse(503, headers={"Retry-After": "99999"}),
                        FakeResponse(200, {"data": []})])
    monkeypatch.setattr(client.session, "get", get)

    client._make_request("/top/anime", use_cache=False)
    assert max(clock.sleeps) <= JikanClient.RETRY_MAX_DELAY


def test_cache_fallback_survives_retry_exhaustion(client, clock, monkeypatch):
    """Tüm denemeler tükenirse eski davranış korunur: önbellek döner."""
    cached = {"data": [{"mal_id": 7}]}
    monkeypatch.setattr(client.cache, "get", lambda key: (cached, "etag-1", True))
    get = make_session([FakeResponse(502)])
    monkeypatch.setattr(client.session, "get", get)

    assert client._make_request("/top/anime") == cached
    assert len(get.calls) == JikanClient.RETRY_ATTEMPTS


def test_fresh_cache_skips_network(client, clock, monkeypatch):
    cached = {"data": []}
    monkeypatch.setattr(client.cache, "get", lambda key: (cached, "etag-1", False))

    def boom(*_a, **_kw):
        raise AssertionError("taze önbellek varken ağa çıkılmamalı")

    monkeypatch.setattr(client.session, "get", boom)
    assert client._make_request("/top/anime") == cached


def test_304_returns_cached_data(client, clock, monkeypatch):
    cached = {"data": [{"mal_id": 3}]}
    monkeypatch.setattr(client.cache, "get", lambda key: (cached, "etag-1", True))
    monkeypatch.setattr(client.cache, "update_validated", lambda key: None)
    get = make_session([FakeResponse(304)])
    monkeypatch.setattr(client.session, "get", get)

    assert client._make_request("/top/anime") == cached
    assert len(get.calls) == 1, "304 yeniden deneme tetiklememeli"
