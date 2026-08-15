"""AniList OAuth: sırsız (Implicit) akış ve gizli anahtarın kaynaktan çıkışı.

Hiçbir test ağa çıkmaz ve **gerçek OAuth akışı asla açılmaz**: `requests`
sahtelenir, tarayıcı hiç açılmaz. Fragment köprüsü gerçek bir tarayıcıyla değil,
yerel geri dönüş sunucusunun *servis ettiği HTML* okunarak sınanır.

Buradaki asıl güvence şu: `client_secret` kaynak kodda yok. Depo herkese açık
ve değer derlenmiş EXE'ye de gömülürdü; artık yalnızca çalışma anında
(ortam değişkeni → kullanıcı yapılandırması → boş) çözülüyor.
"""
from __future__ import annotations

import ast
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

import pytest

import turkanime_api.anilist_client as anilist_mod
from turkanime_api.anilist_client import (
    AniListAuthServer, AniListClient, SECRET_ORTAM_DEGISKENI,
    VARSAYILAN_CLIENT_ID, VARSAYILAN_REDIRECT_URI, fragment_koprusu_html,
    sizan_secret_mi,
)

# Testlerde kullanılan sahte sır. Gerçek anahtar HİÇBİR yere yazılmaz.
SAHTE_SECRET = "TEST-SECRET-DEGERI-0123456789"

# "Sızmış" sırın test ikizi. Gerçek değer bu depoya geri sokulmadığı için
# tanıma ölçütü (uzunluk + özet öneki) bu ikize göre sahteleniyor; sınanan şey
# değerin kendisi değil, **mekanizma**.
SAHTE_SIZAN_SECRET = "SIZMIS-ESKI-ANAHTAR-0123456789-ABCDEFGH"

PAKET_KOKU = Path(anilist_mod.__file__).resolve().parent


# ── Yalıtım ─────────────────────────────────────────────────────────────────
@pytest.fixture
def izole(tmp_path, monkeypatch):
    """Diskten ve ortamdan yalıtılmış `AniListClient` fabrikası.

    Yalnızca `_tokens_path` sahteleniyor: `_config_path` zaten onun dizinini
    türetiyor, ikisini ayrı ayrı sahtelemek yolların ayrışmasına açık olurdu.
    Yalıtım şart — aksi hâlde testler geliştiricinin gerçek jetonunu ve
    yapılandırmasını okurdu.
    """
    monkeypatch.delenv(SECRET_ORTAM_DEGISKENI, raising=False)
    monkeypatch.setattr(AniListClient, "_tokens_path",
                        lambda self: str(tmp_path / "anilist_tokens.json"))

    def _kur(secret: str = "") -> AniListClient:
        return AniListClient(client_id=VARSAYILAN_CLIENT_ID,
                             client_secret=secret,
                             redirect_uri=VARSAYILAN_REDIRECT_URI)

    _kur.dizin = tmp_path                       # type: ignore[attr-defined]
    _kur.config = tmp_path / "anilist_config.json"   # type: ignore[attr-defined]
    _kur.tokens = tmp_path / "anilist_tokens.json"   # type: ignore[attr-defined]
    return _kur


def yapilandirma_yaz(izole, **alanlar) -> None:
    """Kullanıcının `anilist_config.json`'ını taklit et."""
    veri = {"client_id": VARSAYILAN_CLIENT_ID,
            "redirect_uri": VARSAYILAN_REDIRECT_URI}
    veri.update(alanlar)
    izole.config.write_text(json.dumps(veri), encoding="utf-8")


# ── Kaynakta sabit sır kalmadı ──────────────────────────────────────────────
def _secret_atamalari(yol: Path):
    """Dosyadaki `client_secret` atamalarının/anahtarlarının AST değerleri."""
    agac = ast.parse(yol.read_text(encoding="utf-8"))
    degerler = []
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.keyword) and dugum.arg == "client_secret":
            degerler.append(dugum.value)
        elif isinstance(dugum, ast.Assign):
            for hedef in dugum.targets:
                ad = getattr(hedef, "attr", None) or getattr(hedef, "id", None)
                if ad == "client_secret":
                    degerler.append(dugum.value)
        elif isinstance(dugum, ast.Dict):
            for anahtar, deger in zip(dugum.keys, dugum.values):
                if isinstance(anahtar, ast.Constant) and anahtar.value == "client_secret":
                    degerler.append(deger)
    return degerler


def test_pakette_sabit_kodlu_secret_yok():
    """Hiçbir kaynak dosyada `client_secret`'a dolu bir dize sabiti atanmıyor."""
    suclular = []
    for yol in PAKET_KOKU.rglob("*.py"):
        for deger in _secret_atamalari(yol):
            if isinstance(deger, ast.Constant) and isinstance(deger.value, str) \
                    and deger.value.strip():
                suclular.append(f"{yol.relative_to(PAKET_KOKU)}:{deger.lineno}")
    assert not suclular, f"kaynakta sabit kodlu secret: {suclular}"


def test_anilist_client_uzun_sabit_dize_icermiyor():
    """API anahtarı biçimli (uzun, boşluksuz) hiçbir dize sabiti kalmamalı.

    Eşik 24: en uzun meşru sabit `ANILIST_CLIENT_SECRET` (21) ve gerçek AniList
    anahtarları 40 karakter; aradaki boşluk yanlış alarm vermeden yakalıyor.
    """
    kaynak = (PAKET_KOKU / "anilist_client.py").read_text(encoding="utf-8")
    kalip = re.compile(r"^[A-Za-z0-9_-]{24,}$")
    suclular = [d.value for d in ast.walk(ast.parse(kaynak))
                if isinstance(d, ast.Constant) and isinstance(d.value, str)
                and kalip.match(d.value)]
    assert not suclular, "anahtar biçimli sabit dize bulundu"


# ── Akış seçimi ─────────────────────────────────────────────────────────────
def test_secret_yoksa_implicit_akis(izole):
    """Sırsız masaüstü uygulaması: jeton fragment'la gelmeli."""
    ist = izole()
    assert ist.secret_var_mi() is False
    assert ist.akis_turu() == "token"
    url = ist.get_auth_url()
    assert "response_type=token" in url
    assert "response_type=code" not in url


def test_secret_varsa_authorization_code_akisi(izole):
    """Kendi uygulamasını tanımlamış kullanıcı eski akışta kalır."""
    ist = izole(secret=SAHTE_SECRET)
    assert ist.akis_turu() == "code"
    assert "response_type=code" in ist.get_auth_url()


def test_response_type_acikca_verilebiliyor(izole):
    ist = izole()
    assert "response_type=code" in ist.get_auth_url(response_type="code")


def test_auth_url_client_id_ve_redirect_tasiyor(izole):
    url = izole().get_auth_url()
    assert f"client_id={VARSAYILAN_CLIENT_ID}" in url
    assert VARSAYILAN_REDIRECT_URI in url


# ── Secret çözümleme sırası: ortam → dosya → boş ────────────────────────────
def test_ortam_degiskeni_yapilandirma_dosyasini_eziyor(izole, monkeypatch):
    yapilandirma_yaz(izole, client_secret="dosyadaki-deger")
    monkeypatch.setenv(SECRET_ORTAM_DEGISKENI, SAHTE_SECRET)
    assert izole().client_secret == SAHTE_SECRET


def test_yapilandirma_dosyasi_ortam_yokken_kullaniliyor(izole):
    yapilandirma_yaz(izole, client_secret=SAHTE_SECRET)
    ist = izole()
    assert ist.client_secret == SAHTE_SECRET
    assert ist.akis_turu() == "code"


def test_ikisi_de_yoksa_secret_bos(izole):
    yapilandirma_yaz(izole)                 # secret alanı hiç yok
    ist = izole()
    assert ist.client_secret == ""
    assert ist.akis_turu() == "token"


def test_bos_ortam_degiskeni_dosyayi_ezmiyor(izole, monkeypatch):
    """Boş tanımlanmış değişken "secret yok" demektir, dosyayı silmez."""
    yapilandirma_yaz(izole, client_secret=SAHTE_SECRET)
    monkeypatch.setenv(SECRET_ORTAM_DEGISKENI, "   ")
    assert izole().client_secret == SAHTE_SECRET


def test_ortamdan_gelen_secret_diske_yazilmiyor(izole, monkeypatch):
    """Ayarlar'da "Kaydet", ortam sırrını sessizce dosyaya kopyalamamalı."""
    monkeypatch.setenv(SECRET_ORTAM_DEGISKENI, SAHTE_SECRET)
    ist = izole()
    ist.set_oauth_config(VARSAYILAN_CLIENT_ID, ist.client_secret,
                         VARSAYILAN_REDIRECT_URI)
    assert SAHTE_SECRET not in izole.config.read_text(encoding="utf-8")
    assert ist.client_secret == SAHTE_SECRET      # etkin değer korunur


def test_kullanicinin_girdigi_secret_kaliciligini_koruyor(izole):
    ist = izole()
    ist.set_oauth_config(VARSAYILAN_CLIENT_ID, SAHTE_SECRET,
                         VARSAYILAN_REDIRECT_URI)
    assert json.loads(izole.config.read_text(encoding="utf-8"))["client_secret"] \
        == SAHTE_SECRET
    assert izole().akis_turu() == "code"


def test_secret_silinince_implicit_akisa_donuyor(izole):
    yapilandirma_yaz(izole, client_secret=SAHTE_SECRET)
    ist = izole()
    ist.set_oauth_config(VARSAYILAN_CLIENT_ID, "", VARSAYILAN_REDIRECT_URI)
    assert ist.akis_turu() == "token"
    assert izole().client_secret == ""


# ── Diskte kalmış sızmış sır ────────────────────────────────────────────────
@pytest.fixture
def sizan_isareti(monkeypatch):
    """Tanıma ölçütünü `SAHTE_SIZAN_SECRET`'e göre kur.

    Gerçek sırrın değeri koda/teste yazılmadığı için mekanizma bir ikizle
    sınanıyor; `sizan_secret_mi` sabitleri çağrı anında okuduğu için yeterli.
    """
    import hashlib
    monkeypatch.setattr(anilist_mod, "SIZAN_SECRET_UZUNLUK",
                        len(SAHTE_SIZAN_SECRET))
    monkeypatch.setattr(
        anilist_mod, "SIZAN_SECRET_OZET_ONEKI",
        hashlib.sha256(SAHTE_SIZAN_SECRET.encode("utf-8")).hexdigest()[:16])


def test_sizan_secret_diskten_temizleniyor(izole, sizan_isareti, capsys):
    """V10 öncesinden kalan sır kullanılmamalı, dosyada da bırakılmamalı."""
    yapilandirma_yaz(izole, client_secret=SAHTE_SIZAN_SECRET)
    ist = izole()

    assert ist.client_secret == ""
    assert ist.akis_turu() == "token", "sızmış sırla Authorization Code denenirdi"
    assert ist.sizan_secret_temizlendi is True

    diskteki = json.loads(izole.config.read_text(encoding="utf-8"))
    assert diskteki["client_secret"] == ""
    assert SAHTE_SIZAN_SECRET not in izole.config.read_text(encoding="utf-8")
    # Sessiz temizlik kullanıcıyı karanlıkta bırakırdı.
    assert "client secret" in capsys.readouterr().out.lower()


def test_sizan_secret_temizligi_diger_alanlari_korumali(izole, sizan_isareti):
    izole.config.write_text(json.dumps({
        "client_id": "31337",
        "client_secret": SAHTE_SIZAN_SECRET,
        "redirect_uri": "http://localhost:9999/anilist-login",
    }), encoding="utf-8")
    ist = izole()

    assert ist.client_id == "31337"
    assert ist.redirect_uri == "http://localhost:9999/anilist-login"
    diskteki = json.loads(izole.config.read_text(encoding="utf-8"))
    assert diskteki["client_id"] == "31337"
    assert diskteki["redirect_uri"] == "http://localhost:9999/anilist-login"


def test_kullanicinin_kendi_secreti_temizlenmiyor(izole, sizan_isareti):
    """Kendi uygulamasını tanımlamış kullanıcı bundan etkilenmemeli."""
    yapilandirma_yaz(izole, client_secret=SAHTE_SECRET)
    ist = izole()
    assert ist.client_secret == SAHTE_SECRET
    assert ist.sizan_secret_temizlendi is False
    assert ist.akis_turu() == "code"


def test_sizan_secret_elle_yapistirilsa_da_kaydedilmiyor(izole, sizan_isareti):
    ist = izole()
    ist.set_oauth_config(VARSAYILAN_CLIENT_ID, SAHTE_SIZAN_SECRET,
                         VARSAYILAN_REDIRECT_URI)
    assert ist.client_secret == ""
    assert SAHTE_SIZAN_SECRET not in izole.config.read_text(encoding="utf-8")


def test_sizan_secret_isareti_degerin_kendisini_tasimiyor():
    """Depoya sır geri sokulmadı: elde yalnızca uzunluk + kısa özet var."""
    onek = anilist_mod.SIZAN_SECRET_OZET_ONEKI
    assert anilist_mod.SIZAN_SECRET_UZUNLUK == 40
    assert len(onek) < 24, "24+ karakterlik sabit anahtar denetimini bozar"
    assert re.fullmatch(r"[0-9a-f]+", onek), "özet öneki onaltılık olmalı"


def test_sizan_secret_mi_rastgele_degerleri_reddediyor():
    assert sizan_secret_mi("") is False
    assert sizan_secret_mi(None) is False
    assert sizan_secret_mi(SAHTE_SECRET) is False
    assert sizan_secret_mi("x" * anilist_mod.SIZAN_SECRET_UZUNLUK) is False


# ── Sır sızıntısı ───────────────────────────────────────────────────────────
def test_secret_hata_ciktisina_sizmiyor(izole, monkeypatch, capsys):
    """Token takası patladığında basılan satır isteğin gövdesini taşımamalı."""
    ist = izole(secret=SAHTE_SECRET)

    def patla(*_a, **_kw):
        raise RuntimeError("baglanti kurulamadi")

    monkeypatch.setattr(anilist_mod.requests, "post", patla)
    assert ist.exchange_code_for_token("kod") is False
    cikti = capsys.readouterr()
    assert SAHTE_SECRET not in (cikti.out + cikti.err)


def test_secret_auth_urlde_gorunmuyor(izole):
    """Yetkilendirme URL'i tarayıcı geçmişine düşer; sır oraya girmemeli."""
    ist = izole(secret=SAHTE_SECRET)
    assert SAHTE_SECRET not in ist.get_auth_url()


def test_secretsiz_kod_takasi_aga_cikmiyor(izole, monkeypatch, capsys):
    """Secret yokken Authorization Code takası denenmez (boş sırla istek yok)."""
    ist = izole()
    monkeypatch.setattr(anilist_mod.requests, "post",
                        lambda *a, **k: pytest.fail("secret yokken ağa çıkıldı"))
    assert ist.exchange_code_for_token("kod") is False
    assert "client_secret" in capsys.readouterr().out


# ── Fragment köprüsü ────────────────────────────────────────────────────────
class SahteIstemci:
    """Sunucu testleri için ağsız istemci ikizi."""

    def __init__(self):
        self.access_token = None
        self.user_cagrildi = 0

    def set_access_token(self, token, expires_in=None):
        self.access_token = token

    def get_current_user(self):
        self.user_cagrildi += 1
        return {"id": 1, "name": "kullanici"}


@pytest.fixture
def geri_donus_sunucusu():
    """Gerçek `AniListAuthServer`'ı rastgele boş bir portta ayağa kaldır.

    Port 0: sabit 9921'i kullanmak, geliştiricinin açık uygulamasıyla ya da
    paralel testle çakışırdı. Sunucu yalnızca 127.0.0.1'i dinler; dışarı hiç
    istek gitmez.
    """
    sunucular = []

    def _ac(client):
        srv = AniListAuthServer(client)
        threading.Thread(target=srv.start_server, args=(0,), daemon=True).start()
        for _ in range(500):
            if getattr(srv, "server", None) is not None:
                break
            time.sleep(0.01)
        assert srv.server is not None, "sunucu bağlanamadı"
        sunucular.append(srv)
        return f"http://127.0.0.1:{srv.server.server_address[1]}", srv

    yield _ac
    for srv in sunucular:
        try:
            srv.server.shutdown()
            srv.server.server_close()
        except Exception:
            pass


def _getir(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as yanit:
        return yanit.read().decode("utf-8")


def test_kopru_sayfasi_hash_okuyup_geri_gonderiyor(geri_donus_sunucusu):
    """Servis edilen HTML `location.hash`'i okuyup jetonu yerel uca yollamalı.

    Fragment sunucuya HİÇ gelmediği için tek köprü bu sayfa; tarayıcı açmadan,
    doğrudan servis edilen gövdeyi inceliyoruz.
    """
    kok, _srv = geri_donus_sunucusu(SahteIstemci())
    html = _getir(f"{kok}/anilist-login")

    assert "location.hash" in html
    assert "access_token" in html
    assert anilist_mod.TOKEN_UCU in html
    assert "'POST'" in html or '"POST"' in html
    assert "kapatabilirsiniz" in html


def test_kopru_sayfasi_gecerli_javascript_uretiyor(geri_donus_sunucusu):
    """Metin `<script>` içinde kalırsa JS parse hatası verir ve köprü çöker.

    Önceki sürümde `<p>` etiketi script bloğunun içindeydi: sayfa açılıyor ama
    jeton hiç gönderilmiyordu.
    """
    kok, _srv = geri_donus_sunucusu(SahteIstemci())
    html = _getir(f"{kok}/anilist-login")
    govde = html.split("<script>")[1].split("</script>")[0]
    assert "<" not in govde, "script bloğunda HTML var — JS parse edilemez"
    assert govde.strip().endswith("})();")
    assert govde.count("{") == govde.count("}")
    assert govde.count("(") == govde.count(")")


def test_kopru_post_ettigi_jeton_istemciye_yaziliyor(geri_donus_sunucusu):
    """Sayfanın yaptığı `fetch` taklit edilir; jeton istemciye ulaşmalı."""
    ist = SahteIstemci()
    kok, srv = geri_donus_sunucusu(ist)
    tetiklendi = threading.Event()
    srv.register_on_success(tetiklendi.set)

    istek = urllib.request.Request(
        f"{kok}{anilist_mod.TOKEN_UCU}",
        data=json.dumps({"access_token": "fragment-jetonu"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(istek, timeout=5) as yanit:
        assert yanit.status == 200

    assert ist.access_token == "fragment-jetonu"
    assert ist.user_cagrildi == 1
    assert tetiklendi.wait(5), "on_success çağrılmadı (GUI uyanmazdı)"


def test_kopru_get_yedegi_de_kabul_ediliyor(geri_donus_sunucusu):
    """`fetch` engellenirse sayfa jetonu sorgu dizesiyle yolluyor."""
    ist = SahteIstemci()
    kok, _srv = geri_donus_sunucusu(ist)
    yanit = _getir(f"{kok}{anilist_mod.TOKEN_UCU}?access_token=yedek-jeton")
    assert ist.access_token == "yedek-jeton"
    assert "kapatabilirsiniz" in yanit


def test_fragment_koprusu_html_saf_fonksiyon():
    """HTML üretimi sunucudan bağımsız test edilebilir olmalı."""
    html = fragment_koprusu_html("/ozel-uc")
    assert "/ozel-uc" in html and anilist_mod.TOKEN_UCU not in html


# ── Mevcut kullanıcılar bozulmuyor ──────────────────────────────────────────
def test_kayitli_jeton_secret_olmadan_da_yukleniyor(izole):
    """Girişi olan kullanıcı, secret kaynaktan çıktı diye yeniden giriş yapmaz."""
    izole.tokens.write_text(json.dumps({"access_token": "eski-jeton"}),
                            encoding="utf-8")
    ist = izole()
    assert ist.access_token == "eski-jeton"
    assert ist.secret_var_mi() is False


def test_jeton_dosya_yolu_degismedi():
    """Jeton yolu taşınırsa mevcut girişler sessizce geçersizleşirdi."""
    yol = Path(anilist_mod.anilist_client._tokens_path())
    assert yol.name == "anilist_tokens.json"
    assert Path(anilist_mod.anilist_client._config_path()).name == \
        "anilist_config.json"
    assert yol.parent == Path(anilist_mod.anilist_client._config_path()).parent


def test_singleton_client_id_public_kaliyor():
    """`client_id` sır değil; kaldırmak mevcut kullanıcıların girişini bozardı."""
    assert anilist_mod.anilist_client.client_id == VARSAYILAN_CLIENT_ID
    assert anilist_mod.anilist_client.redirect_uri.endswith("/anilist-login")
