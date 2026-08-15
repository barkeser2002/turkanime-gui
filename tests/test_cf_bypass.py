"""CF bypass zinciri: FlareSolverr ayarı ve "200 dışı = başarısız" varsayımı.

Hiçbir test ağa çıkmaz: zincirin basamakları tek tek sahtelenip **hangisinin
çağrıldığı** kaydedilir. Ölçülen şey "bypass çalıştı mı" değil, zincirin ne
zaman ilerlediği — asıl hata buradaydı.
"""
from __future__ import annotations

import pytest
import requests

from turkanime_api.common import cf_bypass as cf


BASAMAKLAR = ("_try_curl_cffi", "_try_cloudscraper", "_try_flaresolverr",
              "_try_qtwebengine", "_try_requests_fallback")


def yanit(status: int = 200, govde: str = "<html>icerik</html>",
          url: str = "https://ornek.test/") -> requests.Response:
    """Gerçek `requests.Response` — sahte nesne `_is_challenge`'ı yanıltırdı."""
    resp = requests.Response()
    resp.status_code = status
    resp._content = govde.encode("utf-8")
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.url = url
    return resp


@pytest.fixture
def zincir(monkeypatch):
    """Tüm basamakları sahteleyip çağrı sırasını kaydeden fabrika.

    `cevaplar`: basamak adı → dönecek yanıt (verilmeyen basamak `None` döner).
    """
    def _kur(session, **cevaplar):
        cagrilar: list = []

        def sahte(ad):
            def _f(*_a, **_kw):
                cagrilar.append(ad)
                return cevaplar.get(ad)
            return _f

        for ad in BASAMAKLAR:
            monkeypatch.setattr(session, ad, sahte(ad))
        return cagrilar

    return _kur


def oturum(**kwargs) -> cf.CFSession:
    """Ayardan bağımsız oturum (FlareSolverr açıkça kapalı, bekleme yok)."""
    kwargs.setdefault("flaresolverr_url", "")
    kwargs.setdefault("retry_delay", 0)
    return cf.CFSession(**kwargs)


# ── 404 meşru yanıt: zincir dönmesin ────────────────────────────────────────
@pytest.fixture
def sahte_curl(monkeypatch):
    """`curl_cffi` taşımasını sahtele; ATILAN İSTEKLERİ say.

    Basamak metodunu değil taşımayı sahtelemek şart: hata `_try_curl_cffi`'nin
    içindeydi (yalnızca HTTP 200'ü "cevap" sayıyor, 404'te 11 impersonate'i
    tek tek deniyordu).
    """
    istekler: list = []

    def _kur(cevap: requests.Response):
        class SahteSession:
            def __init__(self, impersonate=None, allow_redirects=True):
                self.impersonate = impersonate
                self.cookies: dict = {}

            def get(self, url, **_kw):
                istekler.append((self.impersonate, url))
                return cevap

            post = get

        monkeypatch.setattr(cf, "HAS_CURL_CFFI", True)
        monkeypatch.setattr(cf, "curl_requests",
                            type("M", (), {"Session": SahteSession}))
        return istekler

    return _kur


# ── timeout kwarg'ı: requests uyumluluğu ────────────────────────────────────
@pytest.fixture
def kwarg_yakalayici(monkeypatch):
    """Her basamağın taşımasını sahtele; İLETİLEN kwarg'ları kaydet.

    Basamak metodunu değil taşımayı sahtelemek şart: hata basamakların
    içindeydi (`timeout=self.timeout, **kwargs` → çağıran `timeout=` verince
    "got multiple values for keyword argument").
    """
    gorulen: dict = {}

    def _kur(cevap: requests.Response):
        class SahteSession:
            def __init__(self, *_a, **_kw):
                self.cookies: dict = {}

            def get(self, url, **kw):
                gorulen.setdefault("curl", kw)
                return cevap

            post = get

        monkeypatch.setattr(cf, "HAS_CURL_CFFI", True)
        monkeypatch.setattr(cf, "curl_requests",
                            type("M", (), {"Session": SahteSession}))

        def sahte_requests_get(url, **kw):
            gorulen.setdefault("requests", kw)
            return cevap

        monkeypatch.setattr(cf.requests, "get", sahte_requests_get)
        monkeypatch.setattr(cf.requests, "post", sahte_requests_get)
        return gorulen

    return _kur


def test_cagiranin_timeout_u_zinciri_cokertmiyor(kwarg_yakalayici):
    """ESKİ HATA: `get(url, timeout=5)` her basamakta TypeError'a düşüyordu.

    Sonuç ağ hatası gibi görünüyordu: 11 curl_cffi profili + cloudscraper +
    FlareSolverr sırayla "hata" verip düz requests'e düşülüyordu — tek bir
    kwarg yüzünden tüm CF bypass kaybediliyordu.
    """
    gorulen = kwarg_yakalayici(yanit(200))
    s = oturum(timeout=30)
    resp = s.get("https://ornek.test/", timeout=5)

    assert resp.status_code == 200, "çağıranın timeout'u zinciri kırdı"
    assert gorulen["curl"]["timeout"] == 5, "çağıranın değeri kazanmalı"


def test_timeout_verilmezse_oturum_varsayilani(kwarg_yakalayici):
    gorulen = kwarg_yakalayici(yanit(200))
    oturum(timeout=17).get("https://ornek.test/")
    assert gorulen["curl"]["timeout"] == 17


def test_post_da_timeout_u_kabul_ediyor(kwarg_yakalayici):
    gorulen = kwarg_yakalayici(yanit(200))
    resp = oturum(timeout=30).post("https://ornek.test/", timeout=9, data={"a": 1})
    assert resp.status_code == 200
    assert gorulen["curl"]["timeout"] == 9


def test_requests_yedegi_de_timeout_u_kabul_ediyor(monkeypatch):
    """Son çare basamağı aynı hatayı taşıyordu; o da düzelmeli."""
    gorulen: dict = {}

    def sahte_get(url, **kw):
        gorulen.update(kw)
        return yanit(200)

    monkeypatch.setattr(cf, "HAS_CURL_CFFI", False)
    monkeypatch.setattr(cf, "HAS_CLOUDSCRAPER", False)
    monkeypatch.setattr(cf, "HAS_QTWEBENGINE", False)
    monkeypatch.setattr(cf.requests, "get", sahte_get)

    resp = oturum().get("https://ornek.test/", timeout=3)
    assert resp.status_code == 200
    assert gorulen["timeout"] == 3


def test_404_tek_istekte_donuyor(sahte_curl, monkeypatch):
    """Var olmayan bölüm: tüm zincir + 3 retry dönüp istisna fırlıyordu."""
    istekler = sahte_curl(yanit(404, "Sayfa bulunamadi"))
    ses = oturum()
    # Zincirin geri kalanı çağrılırsa test patlasın: 404 buradan dönmeli.
    for ad in BASAMAKLAR[1:]:
        monkeypatch.setattr(ses, ad,
                            lambda *a, _ad=ad, **k: pytest.fail(
                                f"404 için zincir ilerledi: {_ad}"))

    resp = ses.get("https://ornek.test/yok")

    assert resp.status_code == 404
    assert len(istekler) == 1, f"404 için {len(istekler)} istek atıldı"


def test_403_parmak_izini_degistirip_yeniden_deniyor(sahte_curl):
    """403 CF sinyali: impersonate döngüsü ve zincir korunmalı."""
    istekler = sahte_curl(yanit(403, "Access denied"))
    ses = oturum(max_retries=1)
    with pytest.raises(cf.CFBypassError):
        ses.get("https://ornek.test/x")
    assert len(istekler) > 1, "403'te parmak izi rotasyonu kayboldu"
    assert len({imp for imp, _ in istekler}) > 1


def test_requests_yedegi_de_404_donduruyor(monkeypatch):
    """Son çare basamağı da "yalnızca 200" diyordu; 404 orada da yutuluyordu."""
    ses = oturum()
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: yanit(404))
    resp = ses._try_requests_fallback("https://ornek.test/yok", {}, "GET")
    assert resp is not None and resp.status_code == 404


@pytest.mark.parametrize("kod", [200, 301, 401, 404, 410, 418])
def test_mesru_yanitlar_cagirana_donuyor(kod, zincir):
    ses = oturum()
    cagrilar = zincir(ses, _try_curl_cffi=yanit(kod))
    assert ses.get("https://ornek.test/x").status_code == kod
    assert cagrilar == ["_try_curl_cffi"]


@pytest.mark.parametrize("kod", [403, 429, 503, 500])
def test_engel_ve_sunucu_hatasi_zinciri_ilerletiyor(kod, zincir):
    """CF engeli/limit ve 5xx'te sıradaki yöntem denenmeli."""
    ses = oturum(max_retries=1)
    cagrilar = zincir(ses, _try_curl_cffi=yanit(kod),
                      _try_qtwebengine=yanit(200))

    resp = ses.get("https://ornek.test/x")
    assert resp.status_code == 200
    assert cagrilar[:4] == list(BASAMAKLAR[:4])


def test_challenge_sayfasi_hala_zinciri_ilerletiyor(zincir):
    """`_is_challenge` bozulmamalı: HTTP 200'lük challenge "başarı" sayılmaz."""
    ses = oturum(max_retries=1)
    cagrilar = zincir(
        ses,
        _try_curl_cffi=yanit(200, "<title>Just a moment...</title>"),
        _try_qtwebengine=yanit(200, "<html>gercek icerik</html>"))

    resp = ses.get("https://ornek.test/x")
    assert "gercek icerik" in resp.text
    assert "_try_qtwebengine" in cagrilar


def test_is_challenge_isaretleri_taniyor():
    assert cf.CFSession._is_challenge(yanit(200, "Checking your browser")) is True
    assert cf.CFSession._is_challenge(yanit(202, "bos")) is True
    assert cf.CFSession._is_challenge(yanit(200, "<html>normal</html>")) is False
    assert cf.CFSession._is_challenge(None) is False


def test_mesru_yanit_karari():
    assert cf.CFSession._mesru_yanit(None) is False
    assert cf.CFSession._mesru_yanit(yanit(404)) is True
    assert cf.CFSession._mesru_yanit(yanit(403)) is False
    assert cf.CFSession._mesru_yanit(yanit(502)) is False
    assert cf.CFSession._mesru_yanit(yanit(200, "Just a moment")) is False


def test_hicbir_basamak_cevap_vermezse_hata(zincir):
    """Gerçek başarısızlıkta davranış değişmemeli: `CFBypassError`."""
    ses = oturum(max_retries=1)
    zincir(ses)                                   # hepsi None
    with pytest.raises(cf.CFBypassError):
        ses.get("https://ornek.test/x")


# ── FlareSolverr ayarı ──────────────────────────────────────────────────────
def test_bos_adres_flaresolverr_i_kapatiyor(monkeypatch):
    """Ayarda kutu boşsa hiçbir istek uzak sunucuya GİTMEMELİ."""
    ses = cf.CFSession(flaresolverr_url="")
    monkeypatch.setattr(cf.requests, "post",
                        lambda *a, **k: pytest.fail("FlareSolverr'a istek atıldı"))

    assert ses.flaresolverr_url == ""
    assert ses._try_flaresolverr("https://ornek.test/") is None
    assert "flaresolverr" not in ses._available_methods


def test_bos_ayar_varsayilan_adresi_ezmiyor(ayarla):
    """Asıl hata: `or` ile boş değer varsayılana düşüyor, ayar yalan söylüyordu."""
    ayarla(flaresolverr_url="")
    assert cf.flaresolverr_ayari() == ""
    assert cf.CFSession().flaresolverr_url == ""


def test_dolu_ayar_kullaniliyor(ayarla):
    ayarla(flaresolverr_url="http://127.0.0.1:8191")
    assert cf.CFSession().flaresolverr_url == "http://127.0.0.1:8191"


def test_acik_arguman_ayardan_ustun(ayarla):
    ayarla(flaresolverr_url="http://127.0.0.1:8191")
    assert cf.CFSession(flaresolverr_url="http://baska:8191").flaresolverr_url \
        == "http://baska:8191"


def test_global_oturum_ayari_okuyor_ve_sifirlanabiliyor(ayarla):
    """Kaydet'ten sonra değişiklik yeniden başlatma beklemeden etkili olmalı."""
    ayarla(flaresolverr_url="http://127.0.0.1:8191")
    cf.reset_cf_session()
    assert cf.get_cf_session().flaresolverr_url == "http://127.0.0.1:8191"

    ayarla(flaresolverr_url="")
    assert cf.get_cf_session().flaresolverr_url == "http://127.0.0.1:8191", \
        "singleton beklenmedik biçimde kendiliğinden yenilendi"
    cf.reset_cf_session()
    try:
        assert cf.get_cf_session().flaresolverr_url == ""
    finally:
        cf.reset_cf_session()      # sonraki testlere kirli singleton bırakma
