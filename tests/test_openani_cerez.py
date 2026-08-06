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
def tokensiz():
    """Modül seviyesindeki tokenları test sonrası eski hâline döndür."""
    onceki = (openani.OPENANI_TOKEN, openani.OPENANI_REFRESH_TOKEN)
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
