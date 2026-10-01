"""Grup 1 kaynakları (OpenAnime, Tranimaci, AnimeciX, TRAnimeİzle) engeli
SESSİZCE boş listeye çevirmesin — tipli `KaynakHatasi` yükseltsin.

NEDEN: canlı ölçüm (bu sandbox, TLS-yeniden-sonlandıran vekil arkasında):

    OpenAnime  "one piece" -> 0 sonuç, HATA YOK   (CF "Attention Required")
    Tranimaci  "one piece" -> 0 sonuç, HATA YOK   (CF "Just a moment")
    AnimeciX   "one piece" -> ham HTTPError 403, 69.6 sn (ağır CF zinciri)
    TRAnimeİzle "one piece" -> OturumGerekli 0.3 sn (zaten doğru)

Boş liste "aradım, bulamadım" demek; engel "soramadım" demek. İkisi
karıştığında kullanıcı çalışan bir kaynağı "sonuç yok" sanıyordu. Düzeltme
sonrası (aynı ölçüm):

    OpenAnime  -> KaynakEngellendi 1.0 sn
    Tranimaci  -> BotDogrulamasi   0.6 sn
    AnimeciX   -> KaynakEngellendi 0.4 sn

Fikstürler GERÇEK Cloudflare yanıtlarından kırpıldı (tests/fixtures/kaynak_grup1).
Testler ağa çıkmaz: alt-katman istek fonksiyonları sahtelenir.
"""
from pathlib import Path

import pytest

from turkanime_api.common.hatalar import (
    BotDogrulamasi, KaynakEngellendi, KaynakHatasi, OturumGerekli,
)
import turkanime_api.sources.openani as oa
import turkanime_api.sources.tranimaci as tc
import turkanime_api.sources.animecix as ac

_FIX = Path(__file__).parent / "fixtures" / "kaynak_grup1"
CF_JUST_A_MOMENT = (_FIX / "cf_just_a_moment.html").read_text(encoding="utf-8")
CF_ATTENTION = (_FIX / "cf_attention_required.html").read_text(encoding="utf-8")


class SahteYanit:
    """status_code + text/content taşıyan minimal yanıt."""

    def __init__(self, status_code=200, text="", content=None):
        self.status_code = status_code
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP Error {self.status_code}")


# ═════════════════════════════════════════════════════════════════════════════
# OpenAnime
# ═════════════════════════════════════════════════════════════════════════════

def test_openani_cf_engeli_yukseliyor(monkeypatch):
    """Düz CF engeli ("Attention Required") -> KaynakEngellendi, boş liste değil."""
    a = oa.OpenAniAdapter()
    monkeypatch.setattr(a, "_rate_limit_wait", lambda: None)
    monkeypatch.setattr(a, "_light_get",
                        lambda url, headers=None: SahteYanit(403, CF_ATTENTION))
    with pytest.raises(KaynakEngellendi):
        a.search_anime("one piece")


def test_openani_cf_dogrulamasi_botdogrulamasi(monkeypatch):
    """CF JS doğrulaması ("Just a moment") -> BotDogrulamasi ("Erişimi aç")."""
    a = oa.OpenAniAdapter()
    monkeypatch.setattr(a, "_rate_limit_wait", lambda: None)
    monkeypatch.setattr(a, "_light_get",
                        lambda url, headers=None: SahteYanit(403, CF_JUST_A_MOMENT))
    with pytest.raises(BotDogrulamasi):
        a.search_anime("one piece")


def test_openani_gercek_bos_sonuc_yukseltmiyor(monkeypatch):
    """Site 200 ama eşleşme yok: bu gerçek "bulunamadı", engel değil -> []."""
    a = oa.OpenAniAdapter()
    monkeypatch.setattr(a, "_rate_limit_wait", lambda: None)
    # Probe'lar "undefined" (yok), explore 200 ama katalogda eşleşme yok.
    explore = ('<html><head><title>OpenAnime</title></head><body>'
               'const data = [{english:"Youjo Senki",slug:"youjo-senki"}];'
               '</body></html>')

    def sahte(url, headers=None):
        if "/explore" in url:
            return SahteYanit(200, explore)
        return SahteYanit(500, "<title>undefined | OpenAnime</title>")

    monkeypatch.setattr(a, "_light_get", sahte)
    assert a.search_anime("bulunmayan xyzzy") == []


# ═════════════════════════════════════════════════════════════════════════════
# Tranimaci
# ═════════════════════════════════════════════════════════════════════════════

def test_tranimaci_cf_dogrulamasi_botdogrulamasi(monkeypatch):
    """CF "Just a moment" -> BotDogrulamasi, boş liste değil."""
    monkeypatch.setattr(tc, "_request",
                        lambda method, path, **k: SahteYanit(403, CF_JUST_A_MOMENT))
    with pytest.raises(BotDogrulamasi):
        tc.search_tranimaci("one piece")


def test_tranimaci_cf_engeli_kaynakengellendi(monkeypatch):
    """Düz CF engeli -> KaynakEngellendi."""
    monkeypatch.setattr(tc, "_request",
                        lambda method, path, **k: SahteYanit(403, CF_ATTENTION))
    with pytest.raises(KaynakEngellendi):
        tc.search_tranimaci("one piece")


def test_tranimaci_202_ara_sayfa_botdogrulamasi(monkeypatch):
    """Next.js 202 "JavaScript'i açın" ara sayfası da doğrulama -> BotDogrulamasi."""
    monkeypatch.setattr(tc, "_request",
                        lambda method, path, **k: SahteYanit(202, "please turn JavaScript on"))
    with pytest.raises(BotDogrulamasi):
        tc.search_tranimaci("one piece")


def test_tranimaci_200_sonuc_parse_ediliyor(monkeypatch):
    """200 gerçek içerik: engel yükseltilmez, kartlar ayrıştırılır."""
    html = ('<a href="/anime/885-one-piece"><img alt="One Piece"></a>'
            '<a href="/anime/21-naruto"><img alt="Naruto"></a>')
    monkeypatch.setattr(tc, "_request",
                        lambda method, path, **k: SahteYanit(200, html))
    sonuc = tc.search_tranimaci("one piece")
    assert ("885-one-piece", "One Piece") in sonuc


def test_tranimaci_arama_tarayici_basamagini_atliyor(monkeypatch):
    """Arama QtWebEngine devrini (yavaş, in-sandbox 60+ sn) KULLANMAMALI."""
    kayit = {}

    def sahte_request(method, path, tarayici=True, **k):
        kayit["tarayici"] = tarayici
        return SahteYanit(200, "")

    monkeypatch.setattr(tc, "_request", sahte_request)
    tc.search_tranimaci("one piece")
    assert kayit["tarayici"] is False


# ═════════════════════════════════════════════════════════════════════════════
# AnimeciX
# ═════════════════════════════════════════════════════════════════════════════

def test_animecix_blok_hatasi_siniflandirma():
    assert isinstance(ac._blok_hatasi(403, CF_JUST_A_MOMENT), BotDogrulamasi)
    assert isinstance(ac._blok_hatasi(403, CF_ATTENTION), KaynakEngellendi)
    assert isinstance(ac._blok_hatasi(200, "cloudflare"), KaynakEngellendi)
    assert ac._blok_hatasi(200, '{"results": []}') is None


class _FakeCurl:
    """`ac._curl_requests` yerine: programlanmış yanıtı döndüren Session."""

    def __init__(self, yanit):
        self._yanit = yanit
        self.istek = {}

    def Session(self, *a, **k):  # noqa: N802 (curl_cffi API adı)
        dis = self

        class _S:
            def get(self, url, headers=None, timeout=None):
                dis.istek["timeout"] = timeout
                return dis._yanit
        return _S()


def _kur_curl(monkeypatch, yanit):
    fake = _FakeCurl(yanit)
    monkeypatch.setattr(ac, "_HAS_CURL", True)
    monkeypatch.setattr(ac, "_curl_requests", fake)
    from turkanime_api.common import oturumlar
    monkeypatch.setattr(oturumlar, "oturumlu", lambda s, **k: s)
    return fake


def test_animecix_http_get_engelde_yukseliyor(monkeypatch):
    """`_http_get` 403/CF'de ham HTTPError değil tipli hata yükseltir."""
    _kur_curl(monkeypatch, SahteYanit(403, CF_ATTENTION))
    with pytest.raises(KaynakEngellendi):
        ac._http_get("https://animecix.tv/secure/search/one-piece")


def test_animecix_http_get_timeout_u_cagirana_uyuyor(monkeypatch):
    """`timeout` artık GERÇEKTEN sokete gidiyor (eskiden CF session yutuyordu)."""
    fake = _kur_curl(monkeypatch, SahteYanit(200, '{"results": []}'))
    ac._http_get("https://animecix.tv/secure/search/x", timeout=3)
    assert fake.istek["timeout"] == 3


def test_animecix_http_get_200_icerik_donuyor(monkeypatch):
    _kur_curl(monkeypatch, SahteYanit(200, '{"results": [{"id": 1, "name": "X"}]}'))
    import json
    data = json.loads(ac._http_get("https://animecix.tv/secure/search/x"))
    assert data["results"][0]["name"] == "X"


def test_animecix_search_engelde_yukseliyor(monkeypatch):
    """search_animecix engeli (sessiz boş liste değil) çağırana taşır."""
    monkeypatch.setattr(ac, "_http_get",
                        lambda url, timeout=8: (_ for _ in ()).throw(
                            KaynakEngellendi("AnimeciX: engel")))
    with pytest.raises(KaynakEngellendi):
        ac.search_animecix("one piece")


# ═════════════════════════════════════════════════════════════════════════════
# TRAnimeİzle — çerezsiz arama çerez akışına yönlendirmeli (ham 403 değil)
# ═════════════════════════════════════════════════════════════════════════════

def _tranime_kaynagi():
    from turkanime_api.sources import kayit
    return next(k for k in kayit.kaynaklar() if k.ad == "TRAnimeİzle")


def test_tranimeizle_cerezsiz_oturumgerekli(monkeypatch):
    """Çerez yokken arama OturumGerekli yükseltir — ham HTTP 403 değil."""
    import turkanime_api.sources.tranime as tr
    monkeypatch.setattr(tr, "SESSION_COOKIE", None)
    # Çerezsiz harf-indeksi bulanık araması (ağ) boş dönsün: ağa çıkmadan.
    monkeypatch.setattr(tr, "search_by_letter", lambda letter, page=1: [])
    monkeypatch.setattr(tr, "_get_cache", lambda key: None)
    with pytest.raises(OturumGerekli):
        _tranime_kaynagi().ara("one piece")
