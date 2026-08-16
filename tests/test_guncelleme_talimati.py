"""Kurulum talimatı, gerçekten yayımlanan paket biçimini anlatmalı.

BAĞLAM: v10.1.0'da paket onedir'den ONEFILE'a geçti. Talimat metni geride
kaldı ve "çıkardığınız KLASÖRÜ eskisinin yerine koyun" demeye devam etti —
oysa zip'in içinde artık klasör değil tek bir dosya var. Kullanıcı tarif
edilen adımı yapamazdı.

Bu, testlerin yakalaması güç bir hata sınıfı: kod çalışıyor, testler yeşil,
yalnızca kullanıcıya SÖYLENEN şey yanlış. Buradaki testler metnin paketleme
biçimiyle birlikte güncellenmesini zorunlu kılıyor.
"""
import pytest

from turkanime_api.common.updater import arsiv_mi, kurulum_talimati

ISLETIMLER = ["windows", "linux", "macos"]


@pytest.mark.parametrize("isletim", ISLETIMLER)
def test_arsiv_talimati_klasor_tasimayi_soylemiyor(isletim):
    """ESKİ HATA: "çıkardığınız klasörü eskisinin yerine koyun".

    Tek dosya paketinde zip'ten bir klasör çıkmıyor; bu adım yapılamazdı.
    """
    metin = kurulum_talimati("/indirilenler/turkanime-gui-windows.zip", isletim)
    dusuk = metin.lower()
    assert "klasörü eskisinin yerine" not in dusuk, metin
    assert "uygulama klasörünü" not in dusuk, metin


@pytest.mark.parametrize("isletim", ISLETIMLER)
def test_arsiv_talimati_tek_dosya_oldugunu_soyluyor(isletim):
    """Kullanıcı zip'i açınca ne bulacağını önceden bilmeli."""
    metin = kurulum_talimati("/indirilenler/turkanime-gui-windows.zip", isletim)
    assert "tek bir uygulama dosyası" in metin, metin


@pytest.mark.parametrize("isletim", ISLETIMLER)
def test_arsiv_talimati_eski_internal_klasorunu_anlatiyor(isletim):
    """10.0.0'dan yükseltenin yanında yarım gigabaytlık ölü klasör kalıyor.

    Tek dosya onu okumaz (zararsız) ama "hangisi güncel" sorusunu doğurur ve
    yer kaplar. Silinebileceği söylenmeli.
    """
    metin = kurulum_talimati("/indirilenler/turkanime-gui-linux.zip", isletim)
    assert "_internal" in metin, metin
    assert "silebilirsiniz" in metin, metin


@pytest.mark.parametrize("isletim", ISLETIMLER)
def test_adim_numaralari_artan_ve_bosluksuz(isletim):
    """Linux/macOS dalında araya adım ekleniyor; numaralar kaymamalı.

    Elle numaralanmış bir listeye ortadan madde eklemek, numaraların
    tekrarlamasının klasik yoludur ("...5. ... 6. ... 6. ... 7. ...").
    """
    metin = kurulum_talimati("/indirilenler/turkanime-gui-linux.zip", isletim)
    numaralar = [int(s.split(".", 1)[0]) for s in metin.splitlines() if s.strip()]
    assert numaralar == list(range(1, len(numaralar) + 1)), metin


@pytest.mark.parametrize("isletim", ["linux", "macos"])
def test_calistirma_izni_dogru_dosyaya_veriliyor(isletim):
    """ESKİ HATA: `chmod +x <klasör>/turkanime-gui` — o klasör artık yok."""
    metin = kurulum_talimati("/indirilenler/turkanime-gui-linux.zip", isletim)
    assert "chmod +x turkanime-gui" in metin, metin
    assert "<klasör>" not in metin, metin


def test_arsiv_olmayan_dosyada_kopyalama_talimati_kaliyor():
    """CLI tek `.exe` olarak yayımlanıyor; onun talimatı değişmedi."""
    metin = kurulum_talimati("/indirilenler/turkanime-cli-windows.exe", "windows")
    assert "kopyalayın" in metin
    assert "arşiv" not in metin.lower()


@pytest.mark.parametrize("yol,beklenen", [
    ("paket.zip", True), ("paket.ZIP", True),
    ("uygulama.exe", False), ("uygulama", False), ("", False),
])
def test_arsiv_tanimi(yol, beklenen):
    assert arsiv_mi(yol) is beklenen


@pytest.mark.parametrize("isletim", ISLETIMLER)
def test_indirilen_dosyanin_gercek_yolu_gosteriliyor(isletim):
    """Eski metin dosyayı her zaman Downloads altında gösteriyordu."""
    yol = "/baska/bir/yer/turkanime-gui-macos.zip"
    assert yol in kurulum_talimati(yol, isletim)
