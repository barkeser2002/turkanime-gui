"""Arama sonuçları alakaya göre sıralansın — ilk sıra doğru anime olsun.

Kaynakların çoğu kendi iç sırasını döndürüyor ve bu sıra sorguyla ilgisiz
olabiliyor. Canlı ölçüm ("one piece", düzeltmeden önce):

    AnimeciX     ONE PIECE (Live Action)  ← ilk sıra yanlış
    TürkAnime    Koisuru One Piece        ← gerçek One Piece 4. sırada
    Tranimaci    One Piece Fan Letter     ← ilk sıra yanlış

Bölüm çekme yolu ilk sonucu aldığı için bu doğrudan "yanlış animenin
bölümleri" demek. Düzeltmeden sonra 8 kaynağın 8'i de doğru kaydı başa koydu.

Mevcut `fuzzy_score` bu iş için kullanılamıyor: `partial_ratio` /
`token_set_ratio` kullandığı için sorgu adayın *içinde* geçtiğinde 1.0
veriyor — yukarıdaki dört başlık da 1.00 alıyor, yani sıralama bilgisi sıfır.
`siralama_skoru` tam dizgi benzerliğine bakıp seri adıyla başlayan adaylara
bonus veriyor.
"""
import pytest

from turkanime_api.common.adapters import _alakaya_gore_sirala
from turkanime_api.common.title_match import fuzzy_score, siralama_skoru


# ─────────────────────────────────────────────────────────────────────────────
# siralama_skoru
# ─────────────────────────────────────────────────────────────────────────────

def test_birebir_eslesme_en_yuksek():
    assert siralama_skoru("one piece", "One Piece") == 1.0


def test_birebir_eslesme_her_zaman_tepede():
    """Bonuslar birebir eşleşmeyi geçmemeli."""
    tam = siralama_skoru("one piece", "One Piece")
    for aday in ("One Piece Film: Z", "One Piece Fan Letter", "One Piece: Heroines"):
        assert siralama_skoru("one piece", aday) < tam


def test_onek_eslesmesi_iceriden_ustun():
    """"One Piece Film: Z" aynı seri; "Koisuru One Piece" değil."""
    devam = siralama_skoru("one piece", "One Piece Film: Z")
    baska = siralama_skoru("one piece", "Koisuru One Piece")
    assert devam > baska, "devam filmi, adı içinde geçen farklı seriden geride kaldı"


@pytest.mark.parametrize("devam", [
    "One Piece Fan Letter",     # ham ratio 0.62 — çıplak benzerlikte kaybeder
    "One Piece: Heroines",      # ham ratio 0.67
    "One Piece Movie 1",        # ham ratio 0.69
])
def test_onek_bonusu_ham_benzerligin_yetmedigi_yerde_calisiyor(devam):
    """Önek bonusunun ASIL gerekçesi: ham benzerlik yanlış sıralıyor.

    "Koisuru One Piece" uzunluk olarak sorguya daha yakın (ratio 0.69), yani
    bonussuz bir sıralamada gerçek One Piece kayıtlarının önüne geçiyor —
    farklı bir seri, One Piece filmlerinden üstte. Bonus tam bu farkı kapatıyor.

    (İlk yazdığım test "One Piece Film: Z" (0.72) ile karşılaştırıyordu; orada
    ham benzerlik zaten doğru cevabı veriyor, dolayısıyla bonusu hiç
    sınamıyordu — mutasyon testi bunu yakaladı.)
    """
    assert siralama_skoru("one piece", devam) > \
        siralama_skoru("one piece", "Koisuru One Piece")


def test_buyuk_kucuk_harf_ve_aksan_onemsiz():
    assert siralama_skoru("one piece", "ONE PIECE") == 1.0
    assert siralama_skoru("shingeki no kyojin", "Shingeki no Kyojin") == 1.0


def test_noktalama_onemsiz():
    assert siralama_skoru("steins gate", "Steins;Gate") == 1.0


def test_bos_girdi_sifir():
    assert siralama_skoru("", "One Piece") == 0.0
    assert siralama_skoru("one piece", "") == 0.0


def test_alakasiz_aday_dusuk():
    assert siralama_skoru("one piece", "Naruto") < 0.5


def test_fuzzy_score_bu_is_icin_yetersiz():
    """Neden ayrı bir fonksiyon gerektiğini kayda geçir.

    Bu test `fuzzy_score`'u kusurlu ilan etmiyor — o "aynı seri mi?" sorusu
    için doğru araç. Yalnızca sıralama için kullanılamayacağını sabitliyor;
    biri gelip `siralama_skoru`'yu `fuzzy_score` ile değiştirmesin.
    """
    adaylar = ["One Piece", "Koisuru One Piece", "Noroi no One Piece"]
    assert len({round(fuzzy_score("one piece", a), 2) for a in adaylar}) == 1, \
        "fuzzy_score artık ayırt ediyorsa bu testi ve gerekçeyi gözden geçir"
    assert len({round(siralama_skoru("one piece", a), 2) for a in adaylar}) == 3


# ─────────────────────────────────────────────────────────────────────────────
# _alakaya_gore_sirala
# ─────────────────────────────────────────────────────────────────────────────

def _sozluk(*basliklar):
    return [{"slug": b.lower().replace(" ", "-"), "title": b} for b in basliklar]


def test_gercek_vakasi_turkanime(caplog):
    """Canlı gözlem: gerçek One Piece 4. sıradaydı."""
    kayitlar = _sozluk("Koisuru One Piece",
                       "Koisuru One Piece Zenyasai Tokubetsu-hen",
                       "Noroi no One Piece", "One Piece")
    sirali = _alakaya_gore_sirala("one piece", kayitlar, lambda k: k["title"])
    assert sirali[0]["title"] == "One Piece"


def test_gercek_vakasi_animecix():
    kayitlar = _sozluk("ONE PIECE (Live Action)", "One Piece",
                       "One Piece: Heart of Gold")
    sirali = _alakaya_gore_sirala("one piece", kayitlar, lambda k: k["title"])
    assert sirali[0]["title"] == "One Piece"


def test_tuple_bicimi_de_destekleniyor():
    """`search_all_sources` (slug, title) ikilisi döndürüyor."""
    ciftler = [("fan-letter", "One Piece Fan Letter"), ("one-piece", "One Piece")]
    sirali = _alakaya_gore_sirala("one piece", ciftler, lambda c: c[1])
    assert sirali[0][1] == "One Piece"


def test_siralama_kararli_esit_skorda_kaynak_sirasi_korunuyor():
    """Kaynağın popülerlik bilgisi eşitlikte kaybolmamalı."""
    kayitlar = _sozluk("Naruto Movie 1", "Naruto Movie 2", "Naruto Movie 3")
    sirali = _alakaya_gore_sirala("naruto movie", kayitlar, lambda k: k["title"])
    assert [k["title"] for k in sirali] == \
        ["Naruto Movie 1", "Naruto Movie 2", "Naruto Movie 3"]


def test_bos_liste_bos_doner():
    assert _alakaya_gore_sirala("x", [], lambda k: k) == []


def test_hicbir_kayit_kaybolmuyor():
    kayitlar = _sozluk("A", "One Piece", "B", "Koisuru One Piece")
    sirali = _alakaya_gore_sirala("one piece", kayitlar, lambda k: k["title"])
    assert len(sirali) == 4
    assert {k["title"] for k in sirali} == {k["title"] for k in kayitlar}


def test_baslik_alicisi_patlarsa_arama_dusmuyor():
    """Skorlama hatası tüm aramayı düşürmemeli — sonuçsuz kalmaktansa sırasız."""
    kayitlar = _sozluk("One Piece", "Naruto")

    def patlak(_k):
        raise ValueError("başlık yok")

    sonuc = _alakaya_gore_sirala("one piece", kayitlar, patlak)
    assert sonuc == kayitlar, "hata durumunda özgün liste korunmalı"


def test_baslikisiz_kayit_elenmiyor():
    kayitlar = [{"slug": "x", "title": None}, {"slug": "op", "title": "One Piece"}]
    sirali = _alakaya_gore_sirala("one piece", kayitlar,
                                  lambda k: k.get("title") or "")
    assert len(sirali) == 2
    assert sirali[0]["title"] == "One Piece"


@pytest.mark.parametrize("sorgu,dogru,yanlis", [
    ("naruto", "Naruto", "Boruto Naruto Next Generations"),
    ("bleach", "Bleach", "Bleach: Sennen Kessen-hen"),
    ("death note", "Death Note", "Death Note: Rewrite"),
    ("steins gate", "Steins;Gate", "Steins;Gate 0"),
])
def test_yaygin_sorgularda_dogru_kayit_once(sorgu, dogru, yanlis):
    sirali = _alakaya_gore_sirala(sorgu, _sozluk(yanlis, dogru),
                                  lambda k: k["title"])
    assert sirali[0]["title"] == dogru
