"""Sunucu API'si (`turkanime_server/app.py`) — Flask test istemcisiyle, ağsız.

ESKİ HATALAR:

- `/search` `threshold`'u doğrudan `float()`a veriyordu. "abc" ya da boş değer
  yakalanmayan ValueError → HTTP 500; 1.5, -1, nan, inf ise sessizce kabul
  ediliyordu. İstemci hatası 400 ile söylenmeli.
- Kaynak tablosu elle yazılmış ikinci bir listeydi (`_safe_import`): AnimeciX
  için bölüm/akış uçları `None` → `/animecix/episodes/..` 405 dönüyordu, oysa
  istemcide ikisi de çalışıyordu. Kayda eklenen kaynak API'de görünmüyordu.
- `CORS(app)` her kökene açıktı; yazma uçlarında hız sınırı, alan sınırı ve
  kaynak adı denetimi yoktu; MySQL hata metni istemciye aynen dönüyordu.
- `/health` veritabanına hiç bakmadan "healthy" diyordu.

Kaynaklar sahte uçlarla değiştiriliyor ya da `SOURCES` boşaltılıyor: hiçbir
test siteye (dolayısıyla ağa) gitmez. Veritabanı sahte bir bağlantı.
"""
from __future__ import annotations

import dataclasses
import importlib
import re
from pathlib import Path
from typing import Any, List, Tuple

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_cors")

from turkanime_api.sources import kayit  # noqa: E402
from turkanime_server import app as api  # noqa: E402


@pytest.fixture
def istemci(monkeypatch):
    monkeypatch.setattr(api, "SOURCES", {})
    monkeypatch.setitem(api.app.config, "TESTING", True)
    return api.app.test_client()


# ─────────────────────────────────────────────────────────────────────────────
# /search threshold
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Kaynak tablosu kayıttan türetiliyor
# ─────────────────────────────────────────────────────────────────────────────
def _sahte_uclar(kaynak: kayit.Kaynak) -> kayit.KaynakUclari:
    def ara(sorgu: str, limit: int = 10) -> List[Tuple[str, str]]:
        return [(f"{kaynak.modul}-1", f"{sorgu} ({kaynak.ad})")][:limit]

    def bolumler(kimlik: str) -> List[Tuple[str, str]]:
        return [(f"{kimlik}/b2", "2. Bölüm"), (f"{kimlik}/b1", "1. Bölüm")]

    def akislar(bolum_id: str):
        return [{"url": f"https://cdn.example/{bolum_id}.mp4", "label": "720p"}]

    return kayit.KaynakUclari(ara, bolumler, akislar)


@pytest.fixture
def sahte_api(monkeypatch):
    """Kayıttaki kaynakların uçları sahte; API tablosu bu kayıttan yeniden kurulur."""
    yeni = tuple(dataclasses.replace(k, yukleyici=(lambda k=k: _sahte_uclar(k)),
                                     hazirlik=None)
                 for k in kayit.KAYNAKLAR)
    monkeypatch.setattr(kayit, "KAYNAKLAR", yeni)
    monkeypatch.setattr(api, "SOURCES", api.kaynak_tablosu())
    monkeypatch.setitem(api.app.config, "TESTING", True)
    return api.app.test_client()


def test_tablo_kayittaki_tarayici_kaynaklariyla_ayni():
    """Elle tutulan ikinci liste yok: API = sunucu tarayıcısının listesi."""
    assert set(api.SOURCES) == {k.modul for k in kayit.tarayici_kaynaklari()}


def test_elle_yazilmis_kaynak_listesi_geri_gelmedi():
    """app.py kaynak modüllerini tek tek import etmemeli; yalnızca kayıt."""
    kod = Path(api.__file__).read_text(encoding="utf-8")
    assert "def _safe_import" not in kod
    assert re.findall(r"from turkanime_api\.sources\.(\w+) import", kod) == []


def test_kayda_eklenen_kaynak_apide_gorunuyor(monkeypatch):
    yeni = kayit.Kaynak("Deneme", "Deneme", "DN", "#000000", "DENEME",
                        lambda: _sahte_uclar(yeni), modul="deneme",
                        taranabilir=True)
    monkeypatch.setattr(kayit, "KAYNAKLAR", kayit.KAYNAKLAR + (yeni,))
    assert "deneme" in api.kaynak_tablosu()


def test_yuklenemeyen_kaynak_digerlerini_dusurmuyor(monkeypatch):
    def patla():
        raise ImportError("bozuk modül")

    yeni = tuple(dataclasses.replace(k, yukleyici=patla) if k.modul == "anizle"
                 else dataclasses.replace(k, yukleyici=(lambda k=k: _sahte_uclar(k)))
                 for k in kayit.KAYNAKLAR)
    monkeypatch.setattr(kayit, "KAYNAKLAR", yeni)
    tablo = api.kaynak_tablosu()
    assert "anizle" not in tablo
    assert {"animecix", "openani"} <= set(tablo)


def test_sources_animecix_bolum_ve_akis_veriyor(sahte_api):
    satirlar = {s["key"]: s for s in sahte_api.get("/sources").get_json()}
    assert satirlar["animecix"]["has_episodes"] is True
    assert satirlar["animecix"]["has_streams"] is True
    assert satirlar["tranime"]["name"] == "TRAnimeİzle"


@pytest.mark.parametrize("modul", sorted(k.modul for k in kayit.tarayici_kaynaklari()))
def test_her_kaynagin_uc_ucu_calisiyor(sahte_api, modul):
    kimlik = "17"                         # AnimeciX sayısal kimlik ister
    arama = sahte_api.get(f"/{modul}/search", query_string={"q": "naruto"})
    assert arama.status_code == 200, arama.get_json()
    assert arama.get_json()[0]["id"] == f"{modul}-1"

    bolumler = sahte_api.get(f"/{modul}/episodes/{kimlik}")
    assert bolumler.status_code == 200, bolumler.get_json()
    assert [b["id"] for b in bolumler.get_json()] == ["17/b1", "17/b2"]

    akislar = sahte_api.get(f"/{modul}/streams/17/b1")
    assert akislar.status_code == 200, akislar.get_json()
    assert akislar.get_json()[0]["url"] == "https://cdn.example/17/b1.mp4"


def test_animecix_sayisal_olmayan_kimlik_400(sahte_api):
    yanit = sahte_api.get("/animecix/episodes/abc")
    assert yanit.status_code == 400
    assert yanit.get_json()["error"] == kayit.bul("animecix").kimlik_denetle("abc")


def test_kaynak_etiketiyle_de_cagrilabiliyor(sahte_api):
    """URL'de modül adı yerine kayıttaki etiket: "TRAnimeİzle" → tranime."""
    for ad in ("TRAnimeİzle", "tranimeizle", "AnimeciX", "TRANIME"):
        yanit = sahte_api.get(f"/{ad}/search", query_string={"q": "x"})
        assert yanit.status_code == 200, ad


@pytest.mark.parametrize("ad", ["animedepo", "TürkAnime", "AniList", "yok"])
def test_apide_olmayan_kaynak_404(sahte_api, ad):
    """Arşiv imajda yok, AniList oynatılamaz: ikisi de tabloda değil."""
    assert sahte_api.get(f"/{ad}/search", query_string={"q": "x"}).status_code == 404


def test_genel_aramada_kaynak_etiketi_cozuluyor(sahte_api):
    yanit = sahte_api.get("/search", query_string={"q": "naruto",
                                                   "source": "TRAnimeİzle",
                                                   "fuzzy": "true"})
    assert yanit.status_code == 200
    assert list(yanit.get_json()["results"]) == ["tranime"]


# ─────────────────────────────────────────────────────────────────────────────
# Sahte veritabanı
# ─────────────────────────────────────────────────────────────────────────────
class SahteImlec:
    def __init__(self, baglanti: "SahteBaglanti"):
        self.b = baglanti

    def execute(self, sql: str, params: Any = None) -> None:
        if self.b.hata is not None:
            raise self.b.hata
        self.b.sorgular.append((" ".join(sql.split()), params))

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return []


class SahteBaglanti:
    def __init__(self, hata: Exception | None = None):
        self.hata = hata
        self.sorgular: List[Tuple[str, Any]] = []
        self.kapandi = False

    def cursor(self, dictionary: bool = False):
        return SahteImlec(self)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self.kapandi = True


@pytest.fixture
def db(monkeypatch):
    baglanti = SahteBaglanti()
    monkeypatch.setattr(api, "_db", lambda: baglanti)
    monkeypatch.setattr(api, "yazma_siniri", api.HizSiniri(0))   # sınırsız
    return baglanti


def _eslesme(istemci, **alanlar):
    govde = {"source": "AnimeciX", "anime_id": "17", "anime_title": "Naruto", **alanlar}
    return istemci.post("/anime-matches", json=govde)


# ─────────────────────────────────────────────────────────────────────────────
# POST /anime-matches: anahtarsız ama denetimli
# ─────────────────────────────────────────────────────────────────────────────
def test_eslesme_anahtarsiz_kaydediliyor(istemci, db, monkeypatch):
    """Masaüstü istemcisi anahtar göndermiyor; anahtar ayarlı olsa da çalışmalı."""
    monkeypatch.setattr(api, "YAZMA_ANAHTARI", "gizli")
    yanit = _eslesme(istemci)
    assert yanit.status_code == 200
    assert db.sorgular[0][1] == ("AnimeciX", "17", "Naruto", "")
    assert db.kapandi


def test_eslesme_eski_kaynak_adi_kanonik_yaziliyor(istemci, db):
    assert _eslesme(istemci, source="AnimeDepo").status_code == 200
    assert db.sorgular[0][1][0] == "TürkAnime"


@pytest.mark.parametrize("kaynak", ["YokBoyle", "x" * 5000])
def test_eslesme_bilinmeyen_kaynak_400(istemci, db, kaynak):
    yanit = _eslesme(istemci, source=kaynak)
    assert yanit.status_code == 400
    assert db.sorgular == []


@pytest.mark.parametrize("alan, uzunluk", [("anime_id", 151), ("anime_title", 501),
                                           ("aliases", 2001)])
def test_eslesme_sema_sinirini_asan_alan_400(istemci, db, alan, uzunluk):
    yanit = _eslesme(istemci, **{alan: "a" * uzunluk})
    assert yanit.status_code == 400
    assert alan in yanit.get_json()["error"]
    assert db.sorgular == []


@pytest.mark.parametrize("govde", [{"anime_id": {"x": 1}}, {"anime_title": True},
                                   {"aliases": [1, 2]}, {"source": ""}])
def test_eslesme_bicimsiz_alan_400(istemci, db, govde):
    assert _eslesme(istemci, **govde).status_code == 400


def test_eslesme_alias_listesi_birlestiriliyor(istemci, db):
    assert _eslesme(istemci, aliases=["Naruto", "ナルト"]).status_code == 200
    assert db.sorgular[0][1][3] == "Naruto|ナルト"


def test_db_hatasi_metni_disari_sizmiyor(istemci, db):
    db.hata = api.MySQLError("Access denied for user 'gizli'@'db.ic.ag'")
    yanit = _eslesme(istemci)
    assert yanit.status_code == 500
    assert yanit.get_json() == {"error": api.DB_HATASI}
    assert "gizli" not in yanit.get_data(as_text=True)


def test_eslesme_listesi_limiti_tavanli(istemci, db):
    assert istemci.get("/anime-matches", query_string={"limit": 99999}).status_code == 200
    assert istemci.get("/anime-matches", query_string={"limit": -5}).status_code == 200
    assert [p for _, p in db.sorgular] == [(api.AZAMI_LIMIT,), (1,)]


# ─────────────────────────────────────────────────────────────────────────────
# POST /user/episode-status: eski uç, isteğe bağlı anahtar
# ─────────────────────────────────────────────────────────────────────────────
DURUM = {"user_id": "u-1", "episode_id": "naruto-1", "watched": True}


def test_durum_anahtar_ayarliysa_anahtarsiz_401(istemci, db, monkeypatch):
    monkeypatch.setattr(api, "YAZMA_ANAHTARI", "gizli")
    assert istemci.post("/user/episode-status", json=DURUM).status_code == 401
    yanlis = istemci.post("/user/episode-status", json=DURUM,
                          headers={"X-API-Key": "tahmin"})
    assert yanlis.status_code == 401
    assert db.sorgular == []
    dogru = istemci.post("/user/episode-status", json=DURUM,
                         headers={"X-API-Key": "gizli"})
    assert dogru.status_code == 200


def test_durum_anahtar_ayarsizsa_eski_istemci_calisiyor(istemci, db, monkeypatch):
    """v9.4.3 anahtar göndermiyor; işletmeci anahtar ayarlamadıysa uç açık."""
    monkeypatch.setattr(api, "YAZMA_ANAHTARI", "")
    assert istemci.post("/user/episode-status", json=DURUM).status_code == 200


@pytest.mark.parametrize("govde", [{**DURUM, "position_seconds": "abc"},
                                   {**DURUM, "user_id": "u" * 65},
                                   {"episode_id": "x"}])
def test_durum_bicimsiz_govde_400(istemci, db, govde):
    assert istemci.post("/user/episode-status", json=govde).status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Hız sınırı
# ─────────────────────────────────────────────────────────────────────────────
def test_otuz_birinci_yazma_429(istemci, db, monkeypatch):
    monkeypatch.setattr(api, "yazma_siniri", api.HizSiniri(30))
    kodlar = [_eslesme(istemci).status_code for _ in range(31)]
    assert kodlar[:30] == [200] * 30
    assert kodlar[30] == 429
    son = _eslesme(istemci)
    assert son.headers["Retry-After"] == "60"
    # Sınır iki yazma ucunu birlikte sayıyor.
    assert istemci.post("/user/episode-status", json=DURUM).status_code == 429


def test_sinir_anahtardan_once_sayiliyor(istemci, db, monkeypatch):
    """Yanlış anahtarla sınırsız tahmin yapılamasın."""
    monkeypatch.setattr(api, "YAZMA_ANAHTARI", "gizli")
    monkeypatch.setattr(api, "yazma_siniri", api.HizSiniri(2))
    kodlar = [istemci.post("/user/episode-status", json=DURUM,
                           headers={"X-API-Key": str(i)}).status_code for i in range(3)]
    assert kodlar == [401, 401, 429]


def test_vekil_basligina_yalnizca_trust_proxy_ile_guveniliyor(istemci, db, monkeypatch):
    monkeypatch.setattr(api, "yazma_siniri", api.HizSiniri(1))

    def yaz(ip):
        return istemci.post("/anime-matches", headers={"CF-Connecting-IP": ip},
                            json={"source": "AnimeciX", "anime_id": "1",
                                  "anime_title": "x"}).status_code

    monkeypatch.setattr(api, "VEKIL_GUVENILIR", False)
    assert [yaz("1.1.1.1"), yaz("2.2.2.2")] == [200, 429], \
        "TRUST_PROXY kapalıyken başlık sınırı atlatmaya yaramamalı"

    monkeypatch.setattr(api, "yazma_siniri", api.HizSiniri(1))
    monkeypatch.setattr(api, "VEKIL_GUVENILIR", True)
    assert [yaz("1.1.1.1"), yaz("2.2.2.2"), yaz("1.1.1.1")] == [200, 200, 429]


def test_hiz_siniri_penceresi_kayiyor():
    saat = [0.0]
    sinir = api.HizSiniri(2, pencere=60, saat=lambda: saat[0])
    assert [sinir.izin_ver("a"), sinir.izin_ver("a"), sinir.izin_ver("a")] == \
        [True, True, False]
    assert sinir.izin_ver("b"), "adresler birbirini etkilememeli"
    saat[0] = 60.0
    assert sinir.izin_ver("a"), "pencere geçince yeniden izin"


def test_hiz_siniri_bellegi_sinirli():
    saat = [0.0]
    sinir = api.HizSiniri(1, pencere=10, saat=lambda: saat[0])
    sinir.AZAMI_ANAHTAR = 5
    for i in range(6):
        sinir.izin_ver(f"ip{i}")
    saat[0] = 100.0
    sinir.izin_ver("yeni")
    assert list(sinir._kayitlar) == ["yeni"]


# ─────────────────────────────────────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────────────────────────────────────
def test_cors_varsayilanda_yabanci_kokene_baslik_yok(istemci):
    yanit = istemci.get("/health/live", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in yanit.headers
    on = istemci.options("/anime-matches", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "Access-Control-Allow-Origin" not in on.headers


def test_cors_kokenleri_ayristiriliyor():
    assert api.cors_kokenleri(" https://a.example , ,https://b.example") == \
        ["https://a.example", "https://b.example"]
    assert api.cors_kokenleri("") == []


@pytest.fixture
def cors_ayarli(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://izinli.example")
    importlib.reload(api)
    try:
        yield api.app.test_client()
    finally:
        monkeypatch.delenv("CORS_ORIGINS")
        importlib.reload(api)


def test_cors_izin_listesi_uygulaniyor(cors_ayarli):
    izinli = cors_ayarli.get("/health/live", headers={"Origin": "https://izinli.example"})
    assert izinli.headers.get("Access-Control-Allow-Origin") == "https://izinli.example"
    yabanci = cors_ayarli.get("/health/live", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in yabanci.headers


# ─────────────────────────────────────────────────────────────────────────────
# /health gerçekten yokluyor
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def db_ayarli(monkeypatch):
    monkeypatch.setattr(api, "_HAS_MYSQL", True)
    for alan in ("host", "user", "database"):
        monkeypatch.setitem(api.DB_CONFIG, alan, "x")


def test_health_db_yanit_verirse_200(istemci, db_ayarli, db):
    yanit = istemci.get("/health")
    assert yanit.status_code == 200
    assert yanit.get_json()["status"] == "healthy"
    assert yanit.get_json()["db"] == "ok"
    assert db.sorgular == [("SELECT 1", None)]
    assert db.kapandi


def test_health_db_erisilemezse_503(istemci, db_ayarli, monkeypatch):
    monkeypatch.setattr(api, "_db", lambda: None)
    yanit = istemci.get("/health")
    assert yanit.status_code == 503
    govde = yanit.get_json()
    assert govde["status"] == "degraded"
    assert govde["db"].startswith("erişilemiyor")
    assert "sources" in govde and "features" in govde


def test_health_sorgu_patlarsa_503_ve_metin_sizmiyor(istemci, db_ayarli, db):
    db.hata = RuntimeError("parola=gizli")
    yanit = istemci.get("/health")
    assert yanit.status_code == 503
    assert "gizli" not in yanit.get_data(as_text=True)


def test_health_surucu_yoksa_503(istemci, monkeypatch):
    monkeypatch.setattr(api, "_HAS_MYSQL", False)
    yanit = istemci.get("/health")
    assert yanit.status_code == 503
    assert yanit.get_json()["db"] == "sürücü yok"


def test_health_live_db_olmadan_200(istemci, monkeypatch):
    monkeypatch.setattr(api, "_HAS_MYSQL", False)
    assert istemci.get("/health/live").status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# DB ayarı: varsayılan yok
# ─────────────────────────────────────────────────────────────────────────────
def test_db_ayarsizsa_baglanti_denenmiyor(monkeypatch):
    def patla(**_):
        raise AssertionError("ayarsız DB'ye bağlanılmaya çalışıldı")

    monkeypatch.setattr(api, "_HAS_MYSQL", True)
    monkeypatch.setattr(api, "mysql", type("M", (), {"connector": type(
        "C", (), {"connect": staticmethod(patla)})}), raising=False)
    for alan in ("host", "user", "database"):
        monkeypatch.setitem(api.DB_CONFIG, alan, "")
    assert api._db() is None
    assert api.db_eksikleri() == ["DB_HOST", "DB_USER", "DB_NAME"]


def test_bootstrap_db_ayarsizsa_acilmiyor(monkeypatch):
    monkeypatch.setitem(api.DB_CONFIG, "host", "")
    with pytest.raises(SystemExit, match="DB_HOST"):
        api._bootstrap()


# ─────────────────────────────────────────────────────────────────────────────
# İstemci tarafı: ölü senkron kodu geri gelmesin
# ─────────────────────────────────────────────────────────────────────────────
def test_istemcide_olu_senkron_kodu_yok():
    from turkanime_api.common import db as istemci_db

    assert not hasattr(istemci_db, "api_manager")
    assert not hasattr(istemci_db, "init_database")
    for ad in ("save_user_episode_status", "get_user_episode_status",
               "generate_user_id", "create_tables"):
        assert not hasattr(istemci_db.APIManager, ad), ad
    assert hasattr(istemci_db.APIManager, "save_anime_match"), \
        "detay sayfası eşleşmeyi bununla kaydediyor"
