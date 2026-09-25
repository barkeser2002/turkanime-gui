"""Anizle: engelsiz ayna önce, katalog diskte, başarısızlıktan sonra geri çekilme.

ESKİ DURUM: tek konak anizm.pro'ydu ve veri merkezi IP'lerine Cloudflare
challenge'ı döndürüyor. Her istek curl → CFSession (5 yöntem × 3 deneme,
65 sn'lik FlareSolverr) → requests zincirini baştan yürüyordu; oysa anizle.co,
anizm.com.tr ve puffytr.com aynı JSON'u (bayt bayt, 8.808.024 B) challenge'sız
veriyor. 8.8 MB'lık katalog yalnızca bellekte tutuluyordu ve başarısız yükleme
hatırlanmıyordu: her arama engelli zinciri yeniden başlatıyordu.

Ağa çıkılmıyor: curl kademesi (`_curl_get`) ve düz `requests` sahte;
`CFSession` kurulursa sayılıyor.
"""
import json
import os
import time
import types
from urllib.parse import urlsplit

import pytest

import turkanime_api.sources.anizle as az
from turkanime_api.common.hatalar import KaynakEngellendi, KaynakHatasi

KATALOG = [{"info_slug": "naruto", "info_title": "Naruto", "lastEpisode": []},
           {"info_slug": "one-piece", "info_title": "One Piece", "lastEpisode": []}]
GOVDE = json.dumps(KATALOG).encode("utf-8")
DB = az.ANIME_LIST_YOLU


class SahteYanit:
    def __init__(self, status_code=200, govde=b""):
        self.status_code = status_code
        self.content = govde
        self.text = govde.decode("utf-8")

    def json(self):
        return json.loads(self.content)


ENGEL = SahteYanit(403, b"<title>Just a moment...</title>")


@pytest.fixture
def ag(monkeypatch):
    """Modül durumunu sıfırla; aynaları konak → yanıt sözlüğüyle sahtele."""
    for ad, deger in (("_anime_database", []), ("_database_loaded", False),
                      ("_db_yuklenme_zamani", 0.0), ("_son_db_hatasi_zamani", 0.0),
                      ("_son_db_hatasi", None), ("_secili_kok", None),
                      ("_cf_session", None)):
        monkeypatch.setattr(az, ad, deger)
    monkeypatch.delenv(az.AYNA_ORTAM_ANAHTARI, raising=False)

    durum = types.SimpleNamespace(istekler=[], yanitlar={}, cf_kurulumu=0,
                                  uzak_istekleri=[])

    def curl(url, timeout, headers):
        durum.istekler.append(url)
        yanit = durum.yanitlar.get(url)
        if yanit is None:
            yanit = durum.yanitlar.get(urlsplit(url).hostname)
        return yanit

    class SayanCFSession:
        def __init__(self, *a, **k):
            durum.cf_kurulumu += 1
            raise RuntimeError("test: CF zinciri kurulmamalıydı")

    def uzak(url, **k):
        durum.uzak_istekleri.append(url)
        raise ConnectionError("uzak sunucu kapalı")

    monkeypatch.setattr(az, "_curl_get", curl)
    monkeypatch.setattr(az, "HAS_CF_BYPASS", True)
    monkeypatch.setattr(az, "CFSession", SayanCFSession)
    monkeypatch.setattr(az, "requests", types.SimpleNamespace(get=uzak, Session=object))
    return durum


def _db_istekleri(durum):
    return [u for u in durum.istekler if u.endswith(DB)]


# ─────────────────────────────────────────────────────────────────────────────
# Ayna seçimi
# ─────────────────────────────────────────────────────────────────────────────

def test_engelli_ayna_atlanip_engelsizi_seciliyor(ag, izole_ev, monkeypatch):
    """anizm.pro önde olsa bile challenge'ı atlanır; CF zinciri hiç kurulmaz."""
    monkeypatch.setattr(az, "AYNALAR", ("https://anizm.pro", "https://anizle.co"))
    ag.yanitlar["anizm.pro"] = ENGEL
    ag.yanitlar["anizle.co"] = SahteYanit(200, GOVDE)

    assert az.load_anime_database() == KATALOG
    assert ag.cf_kurulumu == 0
    assert az._secili_kok == "https://anizle.co"
    assert _db_istekleri(ag) == [f"https://anizm.pro{DB}", f"https://anizle.co{DB}"]


def test_secilen_ayna_sayfa_ve_translator_adreslerinde(ag, monkeypatch):
    """Katalogla seçilen ayna bölüm sayfasında ve translator isteğinde de kullanılır."""
    monkeypatch.setattr(az, "_secili_kok", "https://anizle.co")
    monkeypatch.setattr(az, "_get_episodes_remote", lambda *a, **k: [])
    monkeypatch.setattr(az, "_uzak_bolumler", lambda *a, **k: [])
    ag.yanitlar["anizle.co"] = SahteYanit(200, b"<html></html>")

    az._fetch_all_episodes_from_page("naruto")
    az._get_episode_translators("naruto-1-bolum")
    # Eski kayıtlarda tam anizm.pro adresi duruyor; o da seçili aynaya gider.
    az._get_episode_translators("https://anizm.pro/naruto-2-bolum")

    assert ag.istekler == ["https://anizle.co/naruto",
                           "https://anizle.co/naruto-1-bolum",
                           "https://anizle.co/naruto-2-bolum"]


def test_ortam_degiskenindeki_ayna_once_deneniyor(ag, izole_ev, monkeypatch):
    monkeypatch.setenv(az.AYNA_ORTAM_ANAHTARI, "https://yeni-ayna.example/")
    ag.yanitlar["yeni-ayna.example"] = SahteYanit(200, GOVDE)

    assert az.load_anime_database() == KATALOG
    assert ag.istekler == [f"https://yeni-ayna.example{DB}"]


def test_hepsi_engelliyse_cf_zinciri_bir_kez_ve_en_sonda(ag, izole_ev):
    """Pahalı zincir yalnızca bütün aynalar engelliyken, tek sefer."""
    for kok in az.AYNALAR:
        ag.yanitlar[urlsplit(kok).hostname] = ENGEL

    assert az.load_anime_database() == []
    assert len(_db_istekleri(ag)) == len(az.AYNALAR)
    assert ag.cf_kurulumu == 1


def test_poster_secili_aynadan(ag, monkeypatch):
    monkeypatch.setattr(az, "_secili_kok", "https://anizm.com.tr")
    anime = az.AnizleAnime(slug="x", title="X", poster="kapak.jpg")
    assert anime.poster_url == "https://anizm.com.tr/uploads/img/kapak.jpg"


# ─────────────────────────────────────────────────────────────────────────────
# Disk önbelleği
# ─────────────────────────────────────────────────────────────────────────────

def _bellegi_sifirla():
    az._anime_database, az._database_loaded = [], False
    az._secili_kok = None


def test_ilk_yukleme_diske_yaziliyor_ikincisi_aga_cikmiyor(ag, izole_ev):
    ag.yanitlar["anizle.co"] = SahteYanit(200, GOVDE)
    az.load_anime_database()

    dosya = izole_ev / "onbellek" / "anizle_db.json"
    assert dosya.read_bytes() == GOVDE, "sunucunun gövdesi bayt bayt yazılmalı"

    _bellegi_sifirla()
    ag.istekler.clear()
    assert az.load_anime_database() == KATALOG
    assert ag.istekler == [], "taze önbellek varken ağa çıkıldı"


def test_eski_onbellek_ag_varken_yenileniyor(ag, izole_ev):
    dosya = izole_ev / "onbellek" / "anizle_db.json"
    dosya.parent.mkdir(parents=True)
    dosya.write_bytes(json.dumps([{"info_slug": "eski"}]).encode())
    iki_gun_once = time.time() - 2 * 86400
    os.utime(dosya, (iki_gun_once, iki_gun_once))
    ag.yanitlar["anizle.co"] = SahteYanit(200, GOVDE)

    assert az.load_anime_database() == KATALOG
    assert dosya.read_bytes() == GOVDE


def test_hicbir_ayna_yoksa_eski_onbellek_donuyor(ag, izole_ev):
    """Aynalar düşükken arama eski katalogla ayakta kalmalı."""
    eski = [{"info_slug": "naruto", "info_title": "Naruto"}]
    dosya = izole_ev / "onbellek" / "anizle_db.json"
    dosya.parent.mkdir(parents=True)
    dosya.write_bytes(json.dumps(eski).encode())
    iki_gun_once = time.time() - 2 * 86400
    os.utime(dosya, (iki_gun_once, iki_gun_once))

    assert az.load_anime_database() == eski
    assert az.search_anizle("naruto") == [("naruto", "Naruto")]


def test_bozuk_onbellek_yok_sayiliyor(ag, izole_ev):
    dosya = izole_ev / "onbellek" / "anizle_db.json"
    dosya.parent.mkdir(parents=True)
    dosya.write_bytes(b"[{yarim")
    ag.yanitlar["anizle.co"] = SahteYanit(200, GOVDE)

    assert az.load_anime_database() == KATALOG


# ─────────────────────────────────────────────────────────────────────────────
# Geri çekilme
# ─────────────────────────────────────────────────────────────────────────────

def test_basarisizliktan_sonra_aynalara_yeniden_gidilmiyor(ag, izole_ev):
    """Geri çekilme süresinde iki arama: aynalara yalnızca birincisi gider."""
    for kok in az.AYNALAR:
        ag.yanitlar[urlsplit(kok).hostname] = ENGEL

    for _ in range(2):
        with pytest.raises(KaynakEngellendi) as hata:
            az.search_anizle("naruto")
        assert "Cloudflare" in str(hata.value)

    assert len(_db_istekleri(ag)) == len(az.AYNALAR)
    assert ag.cf_kurulumu == 1
    # Uzak sunucu her aramada denenir (ucuz; katalog yükü değil).
    assert len([u for u in ag.uzak_istekleri if u.startswith(az.SERVER_URL)]) == 2

    # Süre dolunca yeniden denenir.
    az._son_db_hatasi_zamani -= az.GERI_CEKILME_SN + 1
    with pytest.raises(KaynakHatasi):
        az.search_anizle("naruto")
    assert len(_db_istekleri(ag)) == 2 * len(az.AYNALAR)


def test_force_reload_geri_cekilmeyi_atliyor(ag, izole_ev):
    for kok in az.AYNALAR:
        ag.yanitlar[urlsplit(kok).hostname] = ENGEL
    az.load_anime_database()
    ag.yanitlar = {"anizle.co": SahteYanit(200, GOVDE)}

    assert az.load_anime_database(force_reload=True) == KATALOG


# ─────────────────────────────────────────────────────────────────────────────
# Belge ile kod aynı şeyi söylesin
# ─────────────────────────────────────────────────────────────────────────────

def test_modul_belgesi_iki_oynatici_yolunu_anlatiyor():
    """Belge yalnızca FirePlayer'ı anlatıyordu; kod çoktan video.js/HLS'e de düşüyor."""
    assert "FirePlayer" in az.__doc__ and "HLS" in az.__doc__


def test_api_konagi_yorumu_sabitle_ayni():
    """Yorum "anizle.org redirect, anizm.pro ana API" diyordu; ikisi de yanlıştı."""
    import inspect
    satirlar = inspect.getsource(az).splitlines()
    i = next(n for n, s in enumerate(satirlar) if s.startswith("API_BASE_URL ="))
    assert urlsplit(az.API_BASE_URL).hostname in satirlar[i - 1]
    assert "anizle.org" not in satirlar[i - 1]


# ─────────────────────────────────────────────────────────────────────────────
# Oynatıcı yönlendirmesi
# ─────────────────────────────────────────────────────────────────────────────

class Yonlendirme:
    def __init__(self, hedef):
        self.status_code = 302
        self.headers = {"location": hedef}
        self.text = ""


def test_ucuncu_taraf_gommeye_gidilmiyor(monkeypatch):
    """`<ayna>/player/<id>` voe.sx'e gidiyorsa takip edilmez, CF zinciri yürümez.

    Canlı ölçüm: 39 videonun yalnızca anizmplayer.com'a gidenleri çözülebiliyor;
    üçüncü taraf sayfaların CF zinciri bir bölümü 285 sn'ye çıkarıyordu.
    """
    yonler = {"https://anizle.co/player/1": Yonlendirme("https://voe.sx/e/abc"),
              "https://anizle.co/player/2": Yonlendirme("https://anizmplayer.com/video/ab")}
    curl_istekleri, zincir = [], []

    def curl(url, timeout, headers, yonlendir=True):
        curl_istekleri.append((url, yonlendir))
        return yonler.get(url)

    monkeypatch.setattr(az, "_curl_get", curl)
    monkeypatch.setattr(az, "_http_get",
                        lambda url, *a, **k: (zincir.append(url), SahteYanit(200))[1])

    assert az._oynatici_sayfasi("https://anizle.co/player/1", {}) is None
    assert zincir == [] and curl_istekleri == [("https://anizle.co/player/1", False)]

    yanit, son_adres = az._oynatici_sayfasi("https://anizle.co/player/2", {})
    assert son_adres == "https://anizmplayer.com/video/ab"
    assert zincir == [son_adres] and yanit.status_code == 200
