"""OpenAnime oturum kurulumu — token ayarlanınca çökmemeli.

`set_openani_tokens()` çağrılır çağrılmaz `_get_cf_session()` AttributeError
atıyordu: `CFSession.cookies` bir dict property (üstelik kopya döndürüyor) ve
dict'te `.set()` yok. Hata bugüne dek gizliydi çünkü depoda `set_openani_tokens`
çağıran kimse yoktu — yani ilk çağıran anında çökerdi.
"""
import pytest

from turkanime_api.common.cf_bypass import CFSession
from turkanime_api.sources import openani


@pytest.fixture
def tokensiz(monkeypatch):
    """Tokenları ve tekil adaptörün oturumlarını test sonrası eski hâline döndür.

    `set_openani_tokens` artık açık adaptörlerin oturumlarını yeniden kuruyor;
    geri alınmazsa sonraki testler sahte tokenlı oturumla çalışırdı.
    """
    onceki = (openani.OPENANI_TOKEN, openani.OPENANI_REFRESH_TOKEN)
    monkeypatch.setattr(openani.adapter, "session", openani.adapter.session)
    monkeypatch.setattr(openani.adapter, "_light_session", None, raising=False)
    monkeypatch.setattr(openani, "_ozel_adaptorler", {})
    yield
    openani.OPENANI_TOKEN, openani.OPENANI_REFRESH_TOKEN = onceki


def test_cfsession_cerez_yazma_yolu_var():
    """`cookies` kopya döndürdüğü için ayrı bir yazma metodu şart."""
    s = CFSession(timeout=5)
    assert isinstance(s.cookies, dict)
    assert s.cookies is not s.cookies, "cookies kopya döndürmeli"
    assert hasattr(s, "set_cookie"), "yazma yolu yok — kopyaya yazmak sessizce kaybolur"

    s.set_cookie("token", "abc")
    assert s.cookies["token"] == "abc"


def test_token_ayarlaninca_oturum_kurulabiliyor(tokensiz):
    """Asıl regresyon: eskiden burada AttributeError atıyordu."""
    openani.set_openani_tokens("SAHTE-TOKEN", "SAHTE-REFRESH")

    session = openani._get_cf_session()

    assert session is not None
    # Çerezler gerçekten oturuma yazılmış olmalı — sessizce düşmemeli.
    assert session.cookies.get("token") == "SAHTE-TOKEN"
    assert session.cookies.get("refreshToken") == "SAHTE-REFRESH"


def test_tokensiz_oturum_da_kuruluyor(tokensiz):
    """Token yokken çerez eklenmemeli ama oturum yine kurulmalı."""
    openani.set_openani_tokens("", "")

    session = openani._get_cf_session()

    assert session is not None
    assert "token" not in session.cookies


def test_requests_kosulsuz_iceri_aktarilmis():
    """Yedek dal `requests`e bakıyor; ad her koşulda tanımlı olmalı."""
    assert getattr(openani, "requests", None) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Token, modül yüklenirken kurulmuş tekil adaptöre de ulaşmalı
# ─────────────────────────────────────────────────────────────────────────────
# ESKİ HATA: `adapter = OpenAniAdapter()` modül yüklenirken oturum kuruyor ve
# tokenları yalnızca o anda okuyordu; `prefs` modülü import edip tokenları
# SONRA basıyor. Ölçüldü: set_openani_tokens('TOK', 'REF') sonrası
# `adapter.session.cookies == {}`. `_light_get` (arama yoklamaları, /explore)
# kendi curl_cffi oturumunu kuruyor ve hiç çerez göndermiyordu.

def test_token_tekil_adaptorun_oturumuna_ulasiyor(tokensiz):
    openani.set_openani_tokens("T", "R")

    assert openani.adapter.session.cookies.get("token") == "T"
    assert openani.adapter.session.cookies.get("refreshToken") == "R"


def test_ozel_sureli_adaptorler_de_tazeleniyor(tokensiz):
    ozel = openani._adaptor(openani.adapter.timeout + 7)
    assert "token" not in ozel.session.cookies

    openani.set_openani_tokens("T", "R")

    assert ozel.session.cookies.get("token") == "T"


def test_token_silinince_oturumdan_da_gidiyor(tokensiz):
    openani.set_openani_tokens("T", "R")
    openani.set_openani_tokens("", "")

    assert "token" not in openani.adapter.session.cookies
    assert openani._token_cerezleri() == {}


def test_hafif_istek_tokeni_cerez_olarak_gonderiyor(tokensiz, monkeypatch):
    """Asıl HTTP yolu: `_light_get` kendi curl_cffi oturumunu kuruyor."""
    import curl_cffi.requests as curl_requests

    cagrilar = []

    class SahteOturum:
        def __init__(self, *a, **k):
            pass

        def get(self, url, **kw):
            cagrilar.append(kw)
            return object()

    monkeypatch.setattr(curl_requests, "Session", SahteOturum)
    openani.set_openani_tokens("T", "R")

    openani.adapter._light_get("https://openani.me/anime/x", {})

    assert cagrilar and cagrilar[0]["cookies"] == {"token": "T", "refreshToken": "R"}


def test_cfsession_istekleri_de_tokeni_tasiyor(tokensiz, monkeypatch):
    """`CFSession` `set_cookie` çerezlerini curl kademesinde göndermiyor;
    bölüm/detay istekleri tokenı istek başına vermeli."""
    cagrilar = []

    class SahteYanit:
        status_code = 500
        text = ""

    class KayitliOturum:
        def get(self, url, **kw):
            cagrilar.append(kw)
            return SahteYanit()

    openani.set_openani_tokens("T", "R")
    monkeypatch.setattr(openani.adapter, "session", KayitliOturum())

    openani.adapter.get_anime_details("https://openani.me/anime/x")

    assert cagrilar and cagrilar[0]["cookies"]["token"] == "T"
