"""Anizle uzak sunucu yedeği, tüketicilerin beklediği sözleşmeyi versin.

`SERVER_URL` (turkanimeapi.bariskeser.com) JSON **sözlüğü** döndürüyor:

    /anizle/search           -> [{"id": "naruto", "title": "Naruto"}, …]
    /anizle/episodes/<slug>  -> [{"episode": 218, "id": "naruto-218-bolum",
                                  "title": "218. Bölüm (Filler)", …}, …]

Tüketiciler ise `(slug, başlık)` ikilisi bekliyor — imza da öyle diyor
(`List[Tuple[str, str]]`). Yanıt olduğu gibi döndürüldüğünde sözlük ikiye
açılamıyor ve `ValueError: too many values to unpack` fırlıyor. Uzak yol tam
da ana site engellendiğinde devreye girdiği için hata en kötü anda çıkıyordu.

Hata uzun süre görünmedi çünkü sunucudaki kazıyıcı bozuktu ve boş liste
dönüyordu (canlı ölçüm: her iki uç da `[]`). Sunucu güncellenip gerçek veri
dönmeye başlayınca hata canlıya çıktı — bu testler o dönüşü sabitliyor.

Ağa çıkılmıyor: `requests.get` sahteleniyor.
"""
import pytest

import turkanime_api.sources.anizle as az


# Canlı sunucudan alınmış gerçek biçimler.
SUNUCU_ARAMA = [
    {"id": "naruto", "title": "Naruto"},
    {"id": "naruto-shippuuden", "title": "Naruto Shippuuden"},
]
SUNUCU_BOLUMLER = [
    {"episode": 218, "id": "naruto-218-bolum-filler", "label": None,
     "normalized": "E218", "parse_score": 0.9, "season": None,
     "title": "218. Bölüm (Filler)"},
    {"episode": 219, "id": "naruto-219-bolum-filler", "label": None,
     "normalized": "E219", "parse_score": 0.9, "season": None,
     "title": "219. Bölüm (Filler)"},
]


class SahteYanit:
    def __init__(self, veri):
        self._veri = veri

    def raise_for_status(self):
        return None

    def json(self):
        return self._veri


@pytest.fixture
def sunucu(monkeypatch):
    """`requests.get`'i sabit bir gövdeyle değiştir."""
    def kur(veri):
        monkeypatch.setattr(az.requests, "get",
                            lambda *a, **k: SahteYanit(veri))
    return kur


# ─────────────────────────────────────────────────────────────────────────────
# Sözleşme
# ─────────────────────────────────────────────────────────────────────────────

def test_uzak_bolumler_ikiliye_cevriliyor(sunucu):
    """ESKİ HATA: sözlük listesi olduğu gibi dönüyordu."""
    sunucu(SUNUCU_BOLUMLER)
    sonuc = az._get_episodes_remote("naruto")

    assert sonuc == [("naruto-218-bolum-filler", "218. Bölüm (Filler)"),
                     ("naruto-219-bolum-filler", "219. Bölüm (Filler)")]
    # Asıl kırılma noktası: tüketici bunu ikiye açıyor.
    for slug, baslik in sonuc:
        assert isinstance(slug, str) and isinstance(baslik, str)


def test_uzak_arama_ikiliye_cevriliyor(sunucu):
    sunucu(SUNUCU_ARAMA)
    sonuc = az._search_remote("naruto")
    assert sonuc == [("naruto", "Naruto"),
                     ("naruto-shippuuden", "Naruto Shippuuden")]


def test_arama_limiti_uygulaniyor(sunucu):
    sunucu(SUNUCU_ARAMA)
    assert len(az._search_remote("naruto", limit=1)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# `_ikiliye_cevir` ayrıntıları
# ─────────────────────────────────────────────────────────────────────────────

def test_zaten_ikili_olan_veri_bozulmuyor():
    """Sunucu ileride ikili dönmeye başlarsa da çalışmalı."""
    assert az._ikiliye_cevir([("a", "A"), ["b", "B"]]) == [("a", "A"), ("b", "B")]


@pytest.mark.parametrize("anahtar", ["id", "slug", "episode_slug"])
def test_slug_alan_adi_varyantlari(anahtar):
    assert az._ikiliye_cevir([{anahtar: "x", "title": "X"}]) == [("x", "X")]


@pytest.mark.parametrize("anahtar", ["title", "label", "episode_title"])
def test_baslik_alan_adi_varyantlari(anahtar):
    assert az._ikiliye_cevir([{"id": "x", anahtar: "X"}]) == [("x", "X")]


def test_baslik_yoksa_slug_kullaniliyor():
    """Başlıksız kayıt elenmemeli; slug hiç yoksa boş başlıktan iyidir."""
    assert az._ikiliye_cevir([{"id": "naruto"}]) == [("naruto", "naruto")]


def test_null_baslik_slug_a_dusuyor():
    """Sunucu `label: null` gönderiyor; None başlık olarak yazılmamalı."""
    assert az._ikiliye_cevir([{"id": "x", "title": None}]) == [("x", "x")]


def test_slugsuz_kayit_eleniyor():
    assert az._ikiliye_cevir([{"title": "slugsuz"}, {"id": "x", "title": "X"}]) \
        == [("x", "X")]


@pytest.mark.parametrize("bozuk", [None, {}, "metin", 42, [None], ["tek"]])
def test_bozuk_yanit_bos_liste(bozuk):
    """Sunucu beklenmedik bir şey dönerse çökme, boş dön."""
    assert az._ikiliye_cevir(bozuk) == []


def test_sayisal_alanlar_metne_cevriliyor():
    """`episode` gibi sayısal alanlar başlık yerine geçerse tip bozulmasın."""
    sonuc = az._ikiliye_cevir([{"id": 218, "title": 219}])
    assert sonuc == [("218", "219")]
    for slug, baslik in sonuc:
        assert isinstance(slug, str) and isinstance(baslik, str)


def test_ag_hatasi_bos_liste(monkeypatch):
    def patla(*_a, **_k):
        raise ConnectionError("sunucuya ulaşılamadı")

    monkeypatch.setattr(az.requests, "get", patla)
    assert az._get_episodes_remote("naruto") == []
    assert az._search_remote("naruto") == []
