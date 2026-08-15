"""OpenAnime slug probe'u sayfanın gerçekten var olduğuna baksın.

`_probe_slug` eskiden şunu doğruluyordu: istenen slug, sayfadaki
`const data = [...]` bloğunda geçiyor mu? O blok sayfaya özel değil — sitenin
**her** sayfasında duran 46 kayıtlık "son eklenenler" katalogu. Doğrulama iki
yönlü yanlıştı:

* kataloğun içindeki bir slug, hangi URL istenirse istensin "bulundu" sayılır
  (yanlış pozitif);
* kataloğun dışındaki her anime, sitede gerçekten dursa bile bulunamaz
  (yanlış negatif).

Canlı ölçüm: naruto / bleach / jujutsu-kaisen / death-note /
shingeki-no-kyojin — beşi de sitede var, hiçbiri eski kontrolden geçmiyordu.
Arama pratikte 46 animeye kilitliydi.

Doğru sinyal sayfa başlığı: gerçek anime "One Piece | OpenAnime", olmayan slug
HTTP 500 + "undefined | OpenAnime" veriyor.

Testler ağa çıkmıyor: `_light_get` sahte sayfalarla değiştiriliyor.
"""
import pytest

from turkanime_api.sources.openani import OpenAniAdapter

# Sitenin her sayfasında duran katalog bloğu — eski doğrulamanın yanıldığı yer.
KATALOG = (
    'const data = [{english:"One Piece",slug:"one-piece"},'
    '{english:"Youjo Senki",slug:"youjo-senki"}];'
)


def sayfa(baslik: str, katalog: str = KATALOG) -> str:
    return f"<html><head><title>{baslik}</title></head><body>{katalog}</body></html>"


class SahteYanit:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def adaptor(monkeypatch):
    """`_light_get`'i slug→yanıt tablosuyla değiştir; istenen URL'leri kaydet."""
    a = OpenAniAdapter()
    a.last_request = 0

    def kur(tablo, varsayilan=None):
        istenen = []

        def sahte(url, headers=None):
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            istenen.append(slug)
            if slug in tablo:
                return tablo[slug]
            # Site olmayan slug'a HTTP 500 + "undefined" veriyor.
            return varsayilan or SahteYanit(500, sayfa("undefined | OpenAnime"))

        monkeypatch.setattr(a, "_light_get", sahte)
        monkeypatch.setattr(a, "_rate_limit_wait", lambda: None)
        return istenen

    a.kur = kur
    return a


# ─────────────────────────────────────────────────────────────────────────────
# _probe_slug
# ─────────────────────────────────────────────────────────────────────────────

def test_katalog_disindaki_anime_bulunuyor(adaptor):
    """ESKİ HATA: sitede olduğu hâlde katalogda geçmeyen anime bulunamıyordu."""
    adaptor.kur({"naruto": SahteYanit(200, sayfa("Naruto | OpenAnime"))})
    sonuc = adaptor._probe_slug("naruto")
    assert sonuc is not None, "katalogda yok diye gerçek anime elendi"
    assert sonuc["title"] == "Naruto"
    assert sonuc["provider_data"]["item_id"] == "naruto"


def test_olmayan_slug_elenmeye_devam_ediyor(adaptor):
    """Gevşetme yanlış pozitife dönüşmemeli."""
    adaptor.kur({})
    assert adaptor._probe_slug("zzz-yok-boyle-bir-anime") is None


def test_200_ama_undefined_baslik_kabul_edilmiyor(adaptor):
    """Site bazen 500 yerine 200 + "undefined" verebiliyor; başlık karar versin."""
    adaptor.kur({"hayalet": SahteYanit(200, sayfa("undefined | OpenAnime"))})
    assert adaptor._probe_slug("hayalet") is None


def test_katalogdaki_slug_yanlis_url_de_dogrulanmiyor(adaptor):
    """ESKİ HATA: katalogdaki slug, sayfa geçersizken bile "bulundu" sayılıyordu.

    Katalog gövdede duruyor ama başlık "undefined" — sayfa yok demektir.
    """
    adaptor.kur({"one-piece": SahteYanit(200, sayfa("undefined | OpenAnime"))})
    assert adaptor._probe_slug("one-piece") is None


def test_baslik_katalogdan_degil_sayfadan_aliniyor(adaptor):
    """Başlık ±2000 karakterlik pencere tahmini yerine <title>'dan gelmeli."""
    adaptor.kur({"shingeki-no-kyojin":
                 SahteYanit(200, sayfa("Attack on Titan | OpenAnime"))})
    sonuc = adaptor._probe_slug("shingeki-no-kyojin")
    assert sonuc["title"] == "Attack on Titan", "slug'dan türetilmiş ad kullanılmış"


def test_http_hatasi_none(adaptor):
    adaptor.kur({"x": SahteYanit(503, "")})
    assert adaptor._probe_slug("x") is None


# ─────────────────────────────────────────────────────────────────────────────
# _sayfa_basligi
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html,beklenen", [
    ("<title>One Piece | OpenAnime</title>", "One Piece"),
    ("<title>  Naruto Shippuuden | OpenAnime  </title>", "Naruto Shippuuden"),
    ("<TITLE>Bleach | OpenAnime</TITLE>", "Bleach"),
    ("<title>Steins;Gate | OpenAnime</title>", "Steins;Gate"),
    ("<title>undefined | OpenAnime</title>", None),
    ("<title>null | OpenAnime</title>", None),
    ("<title>OpenAnime</title>", None),
    ("<title></title>", None),
    ("<html>başlıksız</html>", None),
])
def test_sayfa_basligi(html, beklenen):
    assert OpenAniAdapter._sayfa_basligi(html) == beklenen


# ─────────────────────────────────────────────────────────────────────────────
# _slugify
# ─────────────────────────────────────────────────────────────────────────────

def test_shippuden_her_sorguya_eklenmiyor():
    """"-shippuden" Naruto'ya özgü ve yazımı yanlıştı ("shippuuden").

    Her sorguya eklenince "one-piece-shippuden" gibi anlamsız probe'lar
    üretiyordu — her biri bir ağ isteği.
    """
    varyantlar = OpenAniAdapter()._slugify("one piece")
    assert not any(v.endswith("-shippuden") for v in varyantlar)


def test_uzun_adin_kisaltilmis_slugu_deneniyor():
    """Site uzun adlara sık sık kısaltılmış slug veriyor."""
    varyantlar = OpenAniAdapter()._slugify("shingeki no kyojin the final season")
    assert "shingeki-no" in varyantlar


def test_kisa_sorguda_kopya_varyant_uretilmiyor():
    varyantlar = OpenAniAdapter()._slugify("bleach")
    assert len(varyantlar) == len(set(varyantlar))
    assert varyantlar[0] == "bleach"


def test_turkce_ve_noktalama_sadelesiyor():
    varyantlar = OpenAniAdapter()._slugify("Şingeki: no Kyojin!")
    assert varyantlar[0] == "singeki-no-kyojin"


def test_bos_sorgu_bos_liste():
    assert OpenAniAdapter()._slugify("!!!") == []
