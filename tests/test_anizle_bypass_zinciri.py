"""Anizle'nin CF bypass kademeleri gerçekten sırasını alıyor mu?

`_http_get`/`_http_post` eskiden şöyleydi:

    try:
        from curl_cffi import requests as curl_requests
        session = curl_requests.Session(impersonate="chrome110")
        return session.get(...)          # <-- try'ın İÇİNDE
    except ImportError:
        pass
    if HAS_CF_BYPASS: ...                # <-- ulaşılamaz
    return requests.get(...)             # <-- ulaşılamaz

curl_cffi zorunlu bağımlılık olduğu için `except ImportError` hiç tetiklenmiyor;
istek ağ hatasıyla düşse dış `except Exception` yakalayıp `None` döndürüyor,
403 challenge dönse de o yanıt olduğu gibi geri veriliyordu. Her iki durumda da
alttaki iki kademe ÖLÜ KODDU — üstelik Anizle, CF arkasındaki kaynak.

Bu testler üç kademenin de gerçekten çalıştığını ve sıranın korunduğunu
doğruluyor. Ağa çıkmıyorlar: her kademe sahte nesnelerle değiştiriliyor.
"""
import sys
import types

import pytest

import turkanime_api.sources.anizle as az


class SahteYanit:
    """`text` ve `status_code` sunan asgari yanıt."""

    def __init__(self, status_code=200, text="<html>içerik</html>"):
        self.status_code = status_code
        self.text = text


def _sahte_curl(davranis):
    """curl_cffi.requests.Session'ı `davranis` ile değiştiren modül üret."""
    class Oturum:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            return davranis()

        def post(self, *a, **k):
            return davranis()

    modul = types.ModuleType("curl_cffi.requests")
    modul.Session = Oturum
    return modul


@pytest.fixture
def kademeler(monkeypatch):
    """Üç kademeyi de izlenebilir kıl; hangisinin çağrıldığını kaydet."""
    izler = []

    def curl_ayarla(davranis):
        paket = types.ModuleType("curl_cffi")
        alt = _sahte_curl(lambda: (izler.append("curl"), davranis())[1])
        paket.requests = alt
        monkeypatch.setitem(sys.modules, "curl_cffi", paket)
        monkeypatch.setitem(sys.modules, "curl_cffi.requests", alt)

    def cf_ayarla(davranis):
        class CFOturum:
            def get(self, *a, **k):
                izler.append("cf")
                return davranis()

            def post(self, *a, **k):
                izler.append("cf")
                return davranis()

        monkeypatch.setattr(az, "HAS_CF_BYPASS", True)
        monkeypatch.setattr(az, "_get_cf_session", CFOturum)

    def requests_ayarla(davranis):
        sahte = types.SimpleNamespace(
            get=lambda *a, **k: (izler.append("requests"), davranis())[1],
            post=lambda *a, **k: (izler.append("requests"), davranis())[1],
        )
        monkeypatch.setattr(az, "requests", sahte)

    return types.SimpleNamespace(izler=izler, curl=curl_ayarla,
                                 cf=cf_ayarla, requests=requests_ayarla)


# ─────────────────────────────────────────────────────────────────────────────
# Kademe sırası
# ─────────────────────────────────────────────────────────────────────────────

def test_curl_calisirsa_digerlerine_dokunulmuyor(kademeler):
    """Mutlu yol: en hızlı kademe yeterse alttakiler boşuna çalışmamalı."""
    kademeler.curl(lambda: SahteYanit(200, "<html>tamam</html>"))
    kademeler.cf(lambda: pytest.fail("CF kademesi gereksiz çalıştı"))
    kademeler.requests(lambda: pytest.fail("requests kademesi gereksiz çalıştı"))

    yanit = az._http_get("https://anizm.pro/x")
    assert yanit.status_code == 200
    assert kademeler.izler == ["curl"]


def test_curl_ag_hatasi_verirse_cf_zinciri_devreye_giriyor(kademeler):
    """ESKİ HATA: ağ hatası dış except'e düşüp None dönüyordu."""
    def patla():
        raise ConnectionError("ağ koptu")

    kademeler.curl(patla)
    kademeler.cf(lambda: SahteYanit(200, "<html>cf verdi</html>"))
    kademeler.requests(lambda: pytest.fail("CF yetmişken requests'e düşüldü"))

    yanit = az._http_get("https://anizm.pro/x")
    assert yanit is not None, "ağ hatasında zincir durdu — eski davranış geri geldi"
    assert yanit.text == "<html>cf verdi</html>"
    assert kademeler.izler == ["curl", "cf"]


@pytest.mark.parametrize("kod", [403, 429, 503])
def test_curl_engel_kodu_donerse_cf_zinciri_devreye_giriyor(kademeler, kod):
    """ESKİ HATA: 403 challenge sayfası "başarılı yanıt" sayılıp geri veriliyordu."""
    kademeler.curl(lambda: SahteYanit(kod, "engellendi"))
    kademeler.cf(lambda: SahteYanit(200, "<html>cf verdi</html>"))
    kademeler.requests(lambda: pytest.fail("CF yetmişken requests'e düşüldü"))

    yanit = az._http_get("https://anizm.pro/x")
    assert yanit.status_code == 200
    assert kademeler.izler == ["curl", "cf"]


def test_challenge_govdesi_200_ile_gelse_bile_engel_sayiliyor(kademeler):
    """Cloudflare interstitial HTTP 200 döner; gövdeye bakmak şart."""
    kademeler.curl(lambda: SahteYanit(200, "<title>Just a moment...</title>"))
    kademeler.cf(lambda: SahteYanit(200, "<html>gerçek içerik</html>"))
    kademeler.requests(lambda: pytest.fail("CF yetmişken requests'e düşüldü"))

    yanit = az._http_get("https://anizm.pro/x")
    assert yanit.text == "<html>gerçek içerik</html>"
    assert kademeler.izler == ["curl", "cf"]


def test_cf_de_engellenirse_duz_requests_son_care(kademeler):
    """Üçüncü kademe de ulaşılabilir olmalı."""
    kademeler.curl(lambda: SahteYanit(403, "engellendi"))
    kademeler.cf(lambda: SahteYanit(503, "hâlâ engel"))
    kademeler.requests(lambda: SahteYanit(200, "<html>son çare</html>"))

    yanit = az._http_get("https://anizm.pro/x")
    assert yanit.text == "<html>son çare</html>"
    assert kademeler.izler == ["curl", "cf", "requests"]


def test_post_da_ayni_kademelerden_geciyor(kademeler):
    """POST yolu GET ile aynı hatayı taşıyordu; birlikte düzelmeli."""
    kademeler.curl(lambda: SahteYanit(403, "engellendi"))
    kademeler.cf(lambda: SahteYanit(200, '{"ok":1}'))
    kademeler.requests(lambda: pytest.fail("CF yetmişken requests'e düşüldü"))

    yanit = az._http_post("https://anizm.pro/api", data={"a": 1})
    assert yanit.text == '{"ok":1}'
    assert kademeler.izler == ["curl", "cf"]


def test_hepsi_duserse_none(kademeler):
    """Tüm kademeler patlarsa sessizce None — çağıran bunu bekliyor."""
    def patla():
        raise ConnectionError("kapalı")

    kademeler.curl(patla)
    kademeler.cf(patla)
    kademeler.requests(patla)
    assert az._http_get("https://anizm.pro/x") is None


# ─────────────────────────────────────────────────────────────────────────────
# `_engellenmis` ayrıntıları
# ─────────────────────────────────────────────────────────────────────────────

def test_engellenmis_none_i_engel_sayiyor():
    assert az._engellenmis(None) is True


def test_engellenmis_normal_yaniti_gecirmiyor():
    assert az._engellenmis(SahteYanit(200, "<html>normal sayfa</html>")) is False


def test_engellenmis_bos_govdede_patlamiyor():
    assert az._engellenmis(SahteYanit(200, "")) is False


def test_engellenmis_text_okunamayan_yanitta_patlamiyor():
    """Bazı yanıt nesnelerinde `.text` erişimi hata fırlatabiliyor."""
    class Huysuz:
        status_code = 200

        @property
        def text(self):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "bozuk")

    assert az._engellenmis(Huysuz()) is False


def test_challenge_izleri_cf_bypass_ile_ayni_kaynak():
    """İz listesi ayrışmamalı — nezaket.py'daki aynı ders."""
    from turkanime_api.common.cf_bypass import CHALLENGE_MARKERS
    assert az.CHALLENGE_MARKERS is CHALLENGE_MARKERS


def test_engel_durumlari_cf_bypass_ile_ayni_kaynak():
    from turkanime_api.common.cf_bypass import ENGEL_DURUMLARI
    assert az.ENGEL_DURUMLARI is ENGEL_DURUMLARI
