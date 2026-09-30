"""Bağış numarası kaybolmasın — "istediğin an geri çekebilirsin" sözü tutulsun.

Onay metni madde 4 açıkça söz veriyor: *"İstediğin an geri çekebilirsin.
Bağış numarası ayarlarında saklanır."* Bu söz yalnızca numara ayarda DURDUĞU
sürece tutulabilir; sunucu bağışçıyı tanımıyor, "bağışlarımı listele" ucu yok
ve olamaz. Numara giderse bağış 30 günlük ömrünü doldurana kadar geri
çekilemez.

Yayından önce iki yoldan kaybolabiliyordu:

1. İKİNCİ BAĞIŞ BİRİNCİYİ EZİYORDU. Ayar tek dizgi tutuyordu ve
   `_kimlik_bagisi_teklif` mevcut numaraya hiç bakmıyordu. Çerezin süresi
   dolunca kullanıcı yeniden bağışlıyor (özelliğin var olma sebebi tam da bu)
   ve eski numara sessizce siliniyordu. Her yenileme bir yetim kayıt.
2. KAYDETME HATASI BAŞARILI BAĞIŞI YOK EDİYORDU. `bagis_gonder` ile
   `set_ayar` aynı `try` bloğundaydı: gönderim başarılı olup kaydetme düşerse
   kullanıcıya "gönderilemedi" deniyordu. Yalan — bağış sunucudaydı, numarası
   hiçbir yerde.
"""
from typing import Any, Dict, List

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from turkanime_api.gui.web.uclar_ayarlar import (  # noqa: E402
    AyarlarUclari, bagis_kimlikleri, bagis_metni,
)


class SahteDosya:
    """`Dosyalar` yerine geçen asgari ikame."""

    def __init__(self, ayarlar: Dict[str, Any], yazma_hatasi: bool = False):
        self.ayarlar = ayarlar
        self.yazma_hatasi = yazma_hatasi
        self.yazilanlar: List[Any] = []

    def set_ayar(self, ad, deger):
        if self.yazma_hatasi:
            raise OSError("disk dolu")
        self.yazilanlar.append((ad, deger))
        self.ayarlar[ad] = deger


class SahteKopru:
    def __init__(self):
        self.olaylar: List[Any] = []

    def yay(self, ad, veri=None):
        self.olaylar.append((ad, veri))

    def durumlar(self):
        return [(v["tur"], v["mesaj"]) for ad, v in self.olaylar if ad == "ayar_durum"]


class SahteAniList(QObject):
    auth_changed = Signal(object)
    kullanici = None

    def giris_var_mi(self):
        return False


def _sayfa(ayarlar, gonderilen=None, geri_cekilen=None, yazma_hatasi=False,
           gonderim_hatasi=None, geri_cekme_hatasi=None):
    """`AyarlarUclari`nın bağış yollarını sahte dosya/katkı ile kur."""
    kopru = SahteKopru()
    s = AyarlarUclari(kopru, anilist=SahteAniList())
    dosya = SahteDosya(ayarlar, yazma_hatasi)
    s._dosya = lambda: dosya                                  # noqa: SLF001

    class SahteKatki:
        KAYNAK_TRANIME = "tranime"

        @staticmethod
        def onay_al(*_a, **_k):
            return True

        @staticmethod
        def bagis_gonder(*_a, **_k):
            if gonderim_hatasi:
                raise gonderim_hatasi
            return (gonderilen or ["yeni"]).pop(0)

        @staticmethod
        def bagis_geri_cek(bid, *_a, **_k):
            if geri_cekme_hatasi and bid in geri_cekme_hatasi:
                raise RuntimeError(geri_cekme_hatasi[bid])
            if geri_cekilen is not None:
                geri_cekilen.append(bid)
            return True

    s._katki = lambda: SahteKatki                             # noqa: SLF001
    s.kopru = kopru
    return s, dosya


@pytest.fixture(autouse=True)
def _kimlik_uygulama(monkeypatch):
    """Kurulumdaki `prefs.kaynak_kimliklerini_uygula` gerçek ayar okumasın."""
    monkeypatch.setattr(AyarlarUclari, "_kimlikleri_uygula", staticmethod(lambda: True))


AYAR_TABAN = {"kimlik paylas": True, "sunucu adresi": "https://x.test",
              "sunucu api anahtari": "k"}


# ── Numara ezilmesi ─────────────────────────────────────────────────────────
def test_ikinci_bagis_birinciyi_ezmiyor():
    """ESKİ HATA: ikinci bağış eski numarayı siliyordu → yetim kayıt."""
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": ["a" * 32]})
    sayfa, dosya = _sayfa(ayarlar, gonderilen=["b" * 32])
    sayfa.kimlik_bagisi_teklif("cerez")
    assert dosya.ayarlar["kimlik bagis id"] == ["a" * 32, "b" * 32]


def test_ucuncu_bagis_da_birikiyor():
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": ["a" * 32, "b" * 32]})
    sayfa, dosya = _sayfa(ayarlar, gonderilen=["c" * 32])
    sayfa.kimlik_bagisi_teklif("cerez")
    assert len(dosya.ayarlar["kimlik bagis id"]) == 3


def test_ayni_numara_iki_kez_eklenmiyor():
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": ["a" * 32]})
    sayfa, dosya = _sayfa(ayarlar, gonderilen=["a" * 32])
    sayfa.kimlik_bagisi_teklif("cerez")
    assert dosya.ayarlar["kimlik bagis id"] == ["a" * 32]


# ── Eski kurulumdan göç ─────────────────────────────────────────────────────
@pytest.mark.parametrize("eski,beklenen", [
    ("a" * 32, ["a" * 32]),          # 10.0.0 biçimi: düz dizgi
    ("", []),                        # boş dizgi
    ([], []),
    (["a" * 32, "b" * 32], ["a" * 32, "b" * 32]),
    (None, []),
    (["  ", "a" * 32], ["a" * 32]),  # boşluklu girdi ayıklanıyor
])
def test_eski_biçim_okunuyor(eski, beklenen):
    """10.0.0'dan yükselen kullanıcının numarası kaybolmamalı."""
    assert bagis_kimlikleri({"kimlik bagis id": eski}) == beklenen


def test_dizgi_bicimindeki_eski_numara_yeni_bagista_korunuyor():
    """Göç yolu: düz dizgi + yeni bağış = iki kayıt, kayıp yok."""
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": "a" * 32})
    sayfa, dosya = _sayfa(ayarlar, gonderilen=["b" * 32])
    sayfa.kimlik_bagisi_teklif("cerez")
    assert dosya.ayarlar["kimlik bagis id"] == ["a" * 32, "b" * 32]


# ── Kaydetme hatası ─────────────────────────────────────────────────────────
def test_kaydetme_hatasinda_numara_kullaniciya_gosteriliyor():
    """ESKİ HATA: "gönderilemedi" deniyordu — yalan; bağış sunucudaydı."""
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": []})
    sayfa, _ = _sayfa(ayarlar, gonderilen=["b" * 32], yazma_hatasi=True)
    sayfa.kimlik_bagisi_teklif("cerez")
    tur, mesaj = sayfa.kopru.durumlar()[-1]
    assert tur == "hata"
    assert "b" * 32 in mesaj, f"numara gösterilmiyor: {mesaj}"
    assert "gönderilemedi" not in mesaj, f"hâlâ yalan söylüyor: {mesaj}"
    assert "ULAŞTI" in mesaj


def test_gercek_gonderim_hatasinda_hala_gonderilemedi_deniyor():
    """Gönderim GERÇEKTEN düştüyse mesaj doğru; ayırt edilebilmeli."""
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": []})
    sayfa, dosya = _sayfa(ayarlar, gonderim_hatasi=RuntimeError("ağ yok"))
    sayfa.kimlik_bagisi_teklif("cerez")
    tur, mesaj = sayfa.kopru.durumlar()[-1]
    assert tur == "hata" and "gönderilemedi" in mesaj
    assert not dosya.yazilanlar, "başarısız gönderim ayara yazdı"


# ── Geri çekme ──────────────────────────────────────────────────────────────
def test_geri_cekme_butun_kayitlari_siliyor():
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": ["a" * 32, "b" * 32]})
    cekilen = []
    sayfa, dosya = _sayfa(ayarlar, geri_cekilen=cekilen)
    sonuc = sayfa.bagis_geri_cek()
    assert sonuc["tur"] == "tamam" and sonuc["kimlikler"] == []
    assert cekilen == ["a" * 32, "b" * 32]
    assert dosya.ayarlar["kimlik bagis id"] == []


def test_bir_kayit_dusense_otekiler_yine_siliniyor():
    """Kısmi başarı: silinemeyen numara SAKLANIYOR, atılmıyor.

    Atılsaydı o kayıt bir daha geri çekilemezdi.
    """
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": ["a" * 32, "b" * 32, "c" * 32]})
    sayfa, dosya = _sayfa(ayarlar, geri_cekilen=[],
                          geri_cekme_hatasi={"b" * 32: "sunucu 500"})
    sonuc = sayfa.bagis_geri_cek()
    assert dosya.ayarlar["kimlik bagis id"] == ["b" * 32], "kalan yanlış"
    assert sonuc["kimlikler"] == ["b" * 32]
    assert sonuc["tur"] == "hata" and "2/3" in sonuc["mesaj"]


def test_hicbiri_cekilemezse_mesaj_sayi_saymiyor():
    """"0/1 bağış geri çekildi" anlamsız; kullanıcı işlemin olmadığını duymalı."""
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": ["a" * 32]})
    sayfa, dosya = _sayfa(ayarlar, geri_cekilen=[],
                          geri_cekme_hatasi={"a" * 32: "ağ yok"})
    sonuc = sayfa.bagis_geri_cek()
    tur, mesaj = sonuc["tur"], sonuc["mesaj"]
    assert tur == "hata"
    assert "geri çekilemedi" in mesaj, mesaj
    assert "0/1" not in mesaj, mesaj
    assert dosya.ayarlar["kimlik bagis id"] == ["a" * 32], "numara atıldı!"


def test_kayit_yokken_geri_cekme_bilgilendiriyor():
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": []})
    sayfa, dosya = _sayfa(ayarlar)
    assert sayfa.bagis_geri_cek()["tur"] == "bilgi"
    assert not dosya.yazilanlar


# ── Durum gösterimi ─────────────────────────────────────────────────────────
def test_cogul_kayit_kullanicidan_gizlenmiyor():
    """Kaç kaydı olduğunu ve düğmenin hepsini sildiğini bilmeli."""
    metin = bagis_metni(["a" * 32, "b" * 32])
    assert "2 bağış" in metin and "hepsini" in metin
    assert "a" * 32 in bagis_metni(["a" * 32])
    assert "yok" in bagis_metni([])


def test_teklif_sonrasi_sayfaya_liste_gidiyor():
    """Sayfa düğmeyi `kimlikler` doluluğuna göre açıyor: liste olay ile gitmeli."""
    ayarlar = dict(AYAR_TABAN, **{"kimlik bagis id": ["a" * 32]})
    sayfa, _ = _sayfa(ayarlar, gonderilen=["b" * 32])
    sayfa.kimlik_bagisi_teklif("cerez")
    bagis = [v for ad, v in sayfa.kopru.olaylar if ad == "ayar_bagis"][-1]
    assert bagis["kimlikler"] == ["a" * 32, "b" * 32]
    assert "2 bağış" in bagis["metin"]


def test_kayit_yokken_dugme_pasif(main_window, web):
    main_window.show_page("settings")
    web.bekle("[...document.querySelectorAll('button')].some("
              "b => b.textContent.includes('Bağışımı geri çek'))", timeout=8000)
    dugme = ("[...document.querySelectorAll('button')].find("
             "b => b.textContent.includes('Bağışımı geri çek'))")
    main_window.kopru.yay("ayar_bagis", {"kimlikler": [], "metin": "yok"})
    web.bekle(dugme + ".disabled === true")
    main_window.kopru.yay("ayar_bagis", {"kimlikler": ["a" * 32], "metin": "var"})
    web.bekle(dugme + ".disabled === false")
