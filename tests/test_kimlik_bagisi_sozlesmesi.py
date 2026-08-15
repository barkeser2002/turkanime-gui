"""İstemcinin gönderdiği bağış isteği, sunucunun kabul ettiği şeyle aynı mı?

Bu dosyadaki testler hep aynı sınıf hatayı hedefliyor: iki taraf ayrı ayrı
"doğru" ama BİRBİRİNE göre yanlış. Böyle bir hata her iki tarafın kendi
testlerinde görünmez — yalnızca sözleşmeyi karşılıklı sınayınca çıkar.

Bulunan üç somut kusur:

* `tur` alanı hiç gönderilmiyordu. Sunucuda `Literal["cookie","token"]` ve
  ZORUNLU; eksikse FastAPI gövdeye hiç bakmadan 422 döner. Yani hiçbir bağış
  hiçbir zaman kaydedilmiyordu.
* Kaynak adı "tranimeizle" gönderiliyordu; sunucunun beyaz listesi
  `KAYNAK_CEREZLERI` anahtarları, yani "tranime". `tur` düzelse bile 400.
* `DELETE` 200 + `{"silindi": false}` dönerken istemci başarı sayıyordu.
  Çağıran taraf bunun üzerine ayarlardaki bağış numarasını siliyor; o numara
  geri çekmenin TEK anahtarı olduğu için bağış 30 gün geri çekilemez kalıyordu.
"""
import pytest

from turkanime_api.gui.qt import katki_dialog as kd


# Sunucudaki beyaz liste (`api.KAYNAK_CEREZLERI` anahtarları, `_refresh` hariç).
# Sunucu ayrı bir depoda olduğu için buraya sabitleniyor; ikisi ayrışırsa
# testin kendisi yanlış olur, o yüzden değiştirmeden önce sunucuya bak.
SUNUCU_KAYNAKLARI = frozenset({"tranime", "openani"})
SUNUCU_TURLERI = frozenset({"cookie", "token"})


class SahteYanit:
    def __init__(self, kod=200, govde=None):
        self.status_code = kod
        self._govde = govde

    def json(self):
        if self._govde is None:
            raise ValueError("gövde yok")
        return self._govde


AYAR = {"sunucu adresi": "https://ornek.test", "sunucu api anahtari": "k"}


def _yakala(monkeypatch):
    """`requests.post`'u yakala, gönderilen gövdeyi döndür."""
    kutu = {}

    def sahte_post(url, json=None, headers=None, timeout=None):
        kutu["url"] = url
        kutu["json"] = json
        kutu["headers"] = headers
        return SahteYanit(200, {"bagis_id": "a" * 32})

    import requests
    monkeypatch.setattr(requests, "post", sahte_post)
    return kutu


# ── Sözleşme ────────────────────────────────────────────────────────────────
def test_tur_alani_gonderiliyor(monkeypatch):
    """ESKİ HATA: `tur` yoktu -> sunucu 422, hiçbir bağış kaydedilmiyordu."""
    kutu = _yakala(monkeypatch)
    kd.bagis_gonder("cerez", kd.KAYNAK_TRANIME, AYAR)
    assert "tur" in kutu["json"], f"`tur` gönderilmedi: {kutu['json']}"
    assert kutu["json"]["tur"] in SUNUCU_TURLERI


def test_kaynak_adi_sunucunun_beyaz_listesinde(monkeypatch):
    """ESKİ HATA: "tranimeizle" gönderiliyordu, sunucu "tranime" bekliyor."""
    kutu = _yakala(monkeypatch)
    kd.bagis_gonder("cerez", kd.KAYNAK_TRANIME, AYAR)
    assert kutu["json"]["kaynak"] in SUNUCU_KAYNAKLARI, (
        f"sunucu bu adı 400 ile reddeder: {kutu['json']['kaynak']!r}")


@pytest.mark.parametrize("kaynak", sorted(kd.KAYNAK_TURLERI))
def test_her_tanimli_kaynak_sunucuda_gecerli(kaynak):
    """Modüldeki HER kaynak adı sunucuda kabul edilebilir olmalı."""
    assert kaynak in SUNUCU_KAYNAKLARI
    assert kd.KAYNAK_TURLERI[kaynak] in SUNUCU_TURLERI


def test_deger_gonderiliyor_ve_bagis_id_dönüyor(monkeypatch):
    kutu = _yakala(monkeypatch)
    sonuc = kd.bagis_gonder("  cerez-degeri  ", kd.KAYNAK_TRANIME, AYAR)
    assert kutu["json"]["deger"] == "cerez-degeri"   # kırpılmış
    assert sonuc == "a" * 32


# ── Geri çekme ──────────────────────────────────────────────────────────────
def test_silindi_false_basari_sayilmiyor(monkeypatch):
    """ESKİ HATA: 200 + silindi:false başarı sayılıyordu.

    Sonucu: çağıran taraf ayarlardaki bağış numarasını siliyordu. O numara
    geri çekmenin tek anahtarı — sunucu bağışçıyı tanımıyor, "bağışlarımı
    listele" ucu yok. Yani numara atılınca bağış 30 gün geri çekilemez.
    """
    import requests
    monkeypatch.setattr(requests, "delete",
                        lambda *a, **k: SahteYanit(200, {"silindi": False}))
    with pytest.raises(kd.KatkiHatasi) as hata:
        kd.bagis_geri_cek("b" * 32, AYAR)
    assert "silinmedi" in str(hata.value).lower()


def test_silindi_true_basari(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "delete",
                        lambda *a, **k: SahteYanit(200, {"silindi": True}))
    assert kd.bagis_geri_cek("b" * 32, AYAR) is True


def test_404_basari_sayiliyor(monkeypatch):
    """Kayıt zaten yok; kullanıcıyı silinemeyen numarayla kilitleme."""
    import requests
    monkeypatch.setattr(requests, "delete", lambda *a, **k: SahteYanit(404, {}))
    assert kd.bagis_geri_cek("b" * 32, AYAR) is True


def test_govdesiz_2xx_basari(monkeypatch):
    """Eski sunucu sürümü gövdesiz 204 dönebilir; bu başarıdır."""
    import requests
    monkeypatch.setattr(requests, "delete", lambda *a, **k: SahteYanit(204, None))
    assert kd.bagis_geri_cek("b" * 32, AYAR) is True


# ── Taşıma güvenliği ────────────────────────────────────────────────────────
@pytest.mark.parametrize("adres", [
    "http://ornek.test",
    "http://192.168.1.30:16554",
    "http://localhost.saldirgan.test",   # "localhost" ile BAŞLIYOR ama değil
])
def test_sifresiz_adrese_kimlik_gonderilmiyor(monkeypatch, adres):
    """Oturum çerezi düz http üzerinden gitmemeli.

    Ağı dinleyen biri çerezi okur ve okuyan kişi hesaba girer. Sunucuya
    bilerek güveniliyor; aradaki ağa güvenilmiyor.
    """
    patlama = []
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: patlama.append(1) or SahteYanit(200, {}))
    ayar = {"sunucu adresi": adres, "sunucu api anahtari": "k"}
    with pytest.raises(kd.KatkiHatasi) as hata:
        kd.bagis_gonder("cerez", kd.KAYNAK_TRANIME, ayar)
    assert "şifresiz" in str(hata.value) or "https" in str(hata.value)
    assert not patlama, "istek yine de gönderildi — çerez ağa çıktı"


@pytest.mark.parametrize("adres", [
    "https://turkanimeapi.bariskeser.com",
    "http://localhost:16554",
    "http://127.0.0.1:16554",
])
def test_guvenli_ve_yerel_adresler_geciyor(monkeypatch, adres):
    kutu = _yakala(monkeypatch)
    ayar = {"sunucu adresi": adres, "sunucu api anahtari": "k"}
    kd.bagis_gonder("cerez", kd.KAYNAK_TRANIME, ayar)
    assert kutu["url"].startswith(adres)


# ── Rıza metni ──────────────────────────────────────────────────────────────
def test_onay_metni_ip_konusunda_fazla_vaat_etmiyor():
    """ESKİ HATA: "IP adresin gönderilmez" deniyordu.

    IP, isteği yapan taraf kullanıcı olduğu için sunucuya kaçınılmaz olarak
    ulaşır; sunucunun yapabildiği şey onu SAKLAMAMAK. "Gönderilmez" demek,
    tutulamayacak bir söz vermekti.
    """
    metin = kd.onay_metni(kd.KAYNAK_TRANIME)
    assert "IP adresin, kullanıcı adın ya da e-postan gönderilmez" not in metin
    assert "saklamaz" in metin or "YAZILMAZ" in metin


def test_onay_metni_parola_degistirmeyi_soyluyor():
    """Kullanıcının en güçlü iptal kaldıracı bu; metinde geçmeli."""
    assert "parola" in kd.onay_metni(kd.KAYNAK_TRANIME).lower()


@pytest.mark.parametrize("kaynak", sorted(kd.KAYNAK_ADLARI))
def test_onay_metninde_site_adi_yerlesiyor(kaynak):
    metin = kd.onay_metni(kaynak)
    assert kd.KAYNAK_ADLARI[kaynak] in metin
    assert "{site}" not in metin
