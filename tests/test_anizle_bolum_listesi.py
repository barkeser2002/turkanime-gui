"""Anizle bölüm listesi TAM gelsin: `lastEpisode` tam liste değil.

ESKİ HATA: `get_anime_episodes`, slug katalogda bulununca kaydın
`lastEpisode` alanını "bütün bölümler" diye döndürüyordu. O alan yalnızca en
yeni 1-3 bölümü tutuyor (yeniden eskiye). Canlı katalogda 4.848 animenin
4.701'i 1-3 bölümle görünüyordu; One Piece 1179, 1178, 1177. Sayfa
ayrıştırıcısına yalnızca katalogda olmayan slug'larda ulaşılıyordu. Aynı
fonksiyon sunucu tarayıcısını ve yedek sunucuyu da beslediği için arşive
yazılan listeler de kesikti.

Ağa çıkılmıyor: aynalar (`_curl_get`), CF zinciri (`_http_get`) ve uzak
sunucu (`requests`) sahte. curl_cffi test soket bekçisini atlattığı için
sahtelemek şart.
"""
import types

import pytest

import turkanime_api.sources.anizle as az
from turkanime_api.common.hatalar import KaynakEngellendi, KaynakYanitVermedi

KOK = "https://anizle.co"

# Canlı katalogdaki biçim: yalnızca en yeni üç bölüm, yeniden eskiye.
KATALOG = [{
    "info_slug": "one-piece",
    "info_title": "One Piece",
    "lastEpisode": [
        {"episode_slug": f"one-piece-{n}-bolum", "episode_title": f"{n}. Bölüm"}
        for n in (1179, 1178, 1177)
    ],
}]


class SahteYanit:
    def __init__(self, status_code=200, text="", veri=None):
        self.status_code = status_code
        self.text = text
        self._veri = veri

    def json(self):
        return self._veri

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ConnectionError(f"HTTP {self.status_code}")


def anime_sayfasi(son: int, kok: str = KOK, slug: str = "one-piece") -> str:
    """Gerçek sayfanın iskeleti: "İlk bölümü izle" butonu + yeniden eskiye liste."""
    satirlar = [f'<a href="{kok}/{slug}-1-bolum" class="anizmEpisodeButton">']
    satirlar += [f'<a href="{kok}/{slug}-{n}-bolum-izle">{n}. Bölüm</a>'
                 for n in range(son, 0, -1)]
    return "\n".join(satirlar)


@pytest.fixture
def ag(monkeypatch):
    """Aynaları, CF zincirini ve uzak sunucuyu sahtele; istekleri kaydet."""
    durum = types.SimpleNamespace(istekler=[], sayfalar={}, uzak=None)

    def curl(url, timeout, headers):
        durum.istekler.append(url)
        return durum.sayfalar.get(url)

    def cf_zinciri(url, *a, **k):
        durum.istekler.append("CF " + url)
        return durum.sayfalar.get(url)

    def uzak(url, **k):
        durum.istekler.append(url)
        if isinstance(durum.uzak, Exception):
            raise durum.uzak
        return SahteYanit(veri=durum.uzak)

    monkeypatch.setattr(az, "_curl_get", curl)
    monkeypatch.setattr(az, "_http_get", cf_zinciri)
    monkeypatch.setattr(az, "requests", types.SimpleNamespace(get=uzak))
    # Katalog `lastEpisode`'lu kaydı taşıyor; eski kod bunu döndürürdü.
    monkeypatch.setattr(az, "load_anime_database", lambda *a, **k: KATALOG)
    monkeypatch.setattr(az, "_secili_kok", None)
    monkeypatch.delenv(az.AYNA_ORTAM_ANAHTARI, raising=False)
    return durum


def test_lastepisode_tam_liste_sayilmiyor(ag):
    """ESKİ HATA: 1179 bölümlük seride 3 bölüm (1179, 1178, 1177) dönüyordu."""
    ag.sayfalar[f"{KOK}/one-piece"] = SahteYanit(200, anime_sayfasi(1179))

    bolumler = az.get_anime_episodes("one-piece")

    assert len(bolumler) == 1179
    assert bolumler[0] == ("one-piece-1-bolum", "1. Bölüm")
    assert bolumler[-1][0].startswith("one-piece-1179-bolum")
    numaralar = [int(s.split("-")[2]) for s, _ in bolumler]
    assert numaralar == sorted(numaralar) == list(range(1, 1180)), \
        "artan sırada ve bölüm numarasına göre tekil olmalı"


def test_sayfa_okunamazsa_uzak_sunucunun_listesi(ag):
    """Sayfa hiç gelmezse (ağ/engel) uzak sunucunun yanıtı olduğu gibi döner."""
    ag.uzak = [{"id": f"one-piece-{n}-bolum", "title": f"{n}. Bölüm"}
               for n in range(1, 6)]

    bolumler = az.get_anime_episodes("one-piece")

    assert bolumler == [(f"one-piece-{n}-bolum", f"{n}. Bölüm") for n in range(1, 6)]


@pytest.mark.parametrize("sayfa, sinif", [
    (None, KaynakYanitVermedi),                                  # bağlantı yok
    (SahteYanit(403, "<title>Just a moment...</title>"), KaynakEngellendi),
    (SahteYanit(500, "sunucu hatası"), KaynakYanitVermedi),
])
def test_sayfa_ve_uzak_duserse_tipli_hata(ag, sayfa, sinif):
    """Kesik liste de boş liste de değil: kullanıcı SEBEBİ görmeli."""
    for kok in az.AYNALAR:
        ag.sayfalar[f"{kok}/one-piece"] = sayfa
    ag.uzak = ConnectionError("yedek sunucu kapalı")

    with pytest.raises(sinif) as hata:
        az.get_anime_episodes("one-piece")

    assert "Anizle" in str(hata.value) and "yedek sunucu" in str(hata.value)
    assert isinstance(hata.value.__cause__, ConnectionError)


def test_sayfa_acik_ama_bolumsuzse_bos_liste(ag):
    """Sayfa okundu, bölüm yok (yayınlanmamış anime): ağ hatası değil, boş liste."""
    ag.sayfalar[f"{KOK}/yeni-anime"] = SahteYanit(200, "<html>yakında</html>")
    ag.uzak = ConnectionError("yedek sunucu kapalı")

    assert az.get_anime_episodes("yeni-anime") == []


def test_hicbir_yolda_lastepisode_donmuyor(ag):
    """Hangi dal çalışırsa çalışsın katalogdaki üç bölüm "tam liste" olmamalı."""
    ag.uzak = []
    for kok in az.AYNALAR:
        ag.sayfalar[f"{kok}/one-piece"] = SahteYanit(200, "<html></html>")

    assert az.get_anime_episodes("one-piece") == []


def test_aynadan_gelen_sayfanin_baglantilari_taniniyor(ag):
    """Sayfa anizm.com.tr'den gelirse bağlantılar da o konağı taşıyor."""
    ag.sayfalar[f"{KOK}/naruto"] = None                          # ilk ayna düşük
    ag.sayfalar["https://anizm.com.tr/naruto"] = SahteYanit(
        200, anime_sayfasi(220, kok="https://anizm.com.tr", slug="naruto"))

    bolumler = az.get_anime_episodes("naruto")

    assert len(bolumler) == 220
    assert bolumler[0][0] == "naruto-1-bolum"


def test_kayit_uzerinden_uctan_uca(ag):
    """Arayüzün ve CLI'ın yolu: kayıt → `kayittan_bolumler` → nesneler."""
    from turkanime_api.sources import kayit
    from turkanime_api.sources.adapter import kayittan_bolumler

    ag.sayfalar[f"{KOK}/one-piece"] = SahteYanit(200, anime_sayfasi(1179))

    bolumler = kayittan_bolumler(kayit.bul("Anizle"), "one-piece", "One Piece")

    assert len(bolumler) == 1179

