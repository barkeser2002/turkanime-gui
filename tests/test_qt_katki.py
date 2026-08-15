"""Oturum kimliği bağışı — ayar, onay ve geri çekme.

Buradaki testlerin ağırlık merkezi tek bir cümle: **onay verilmeden hiçbir şey
gönderilmez.** Bu yüzden gönderim fonksiyonu sahtelenirken "çağrılmadı" kontrolü
her kapı için ayrı ayrı yapılıyor (ayar kapalı, ayar açık ama onay yok, onay
diyaloğu kaza sonucu onaylanamaz). Kalan testler metnin dürüstlüğünü ve geri
çekmenin numarayı temizlediğini bekliyor.

Ağa çıkılmıyor: `katki_dialog.bagis_gonder` / `bagis_geri_cek` sahteleniyor,
gerçek HTTP yolunun test edildiği yerde de yapılandırma boş bırakılıp isteğin
hiç başlamadığı doğrulanıyor.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QLineEdit

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.gui.qt import katki_dialog
from turkanime_api.gui.qt.pages.settings import SettingsPage

CEREZ = (
    "# Netscape HTTP Cookie File\n"
    ".tranimeizle.io\tTRUE\t/\tTRUE\t0\t.AitrWeb.Session\tSAHTE-DEGER\n"
)


@pytest.fixture
def sayfa(qtbot, izole_ev):
    """Geçici bir ev dizinine bağlı ayarlar sayfası (gerçek ayarlara dokunmaz)."""
    page = SettingsPage()
    qtbot.addWidget(page)
    return page


@pytest.fixture
def casus(monkeypatch):
    """`bagis_gonder`/`bagis_geri_cek`/`onay_al` çağrılarını kaydeden sahte.

    Varsayılan olarak onay VERİLMEZ: bir testin gönderim görmesi için onayı
    açıkça açması gerekir, tersi değil.
    """
    kayit = {"gonderim": [], "geri_cekme": [], "onay_sorusu": 0}
    ayar = {"onay": False, "gonderim_hatasi": None, "geri_cekme_hatasi": None}

    def _onay_al(kaynak=katki_dialog.KAYNAK_TRANIME, parent=None):
        kayit["onay_sorusu"] += 1
        return ayar["onay"]

    def _gonder(deger, kaynak=katki_dialog.KAYNAK_TRANIME, ayarlar=None,
                zaman_asimi=15):
        kayit["gonderim"].append((deger, kaynak))
        if ayar["gonderim_hatasi"]:
            raise katki_dialog.KatkiHatasi(ayar["gonderim_hatasi"])
        return "BAGIS-1234"

    def _geri_cek(bagis_id, ayarlar=None, zaman_asimi=15):
        kayit["geri_cekme"].append(bagis_id)
        if ayar["geri_cekme_hatasi"]:
            raise katki_dialog.KatkiHatasi(ayar["geri_cekme_hatasi"])
        return True

    monkeypatch.setattr(katki_dialog, "onay_al", _onay_al)
    monkeypatch.setattr(katki_dialog, "bagis_gonder", _gonder)
    monkeypatch.setattr(katki_dialog, "bagis_geri_cek", _geri_cek)
    return kayit, ayar


# ── Ayarın varsayılanı ──────────────────────────────────────────────────────
def test_kimlik_paylasimi_varsayilan_kapali(izole_ev):
    """Yeni kurulumda bağış kapalı olmalı; açmak kullanıcının kararı."""
    ayarlar = Dosyalar().ayarlar
    assert ayarlar["kimlik paylas"] is False
    assert ayarlar["kimlik bagis id"] == ""
    # Sunucu adresi/anahtarı koda gömülü değil.
    assert ayarlar["sunucu adresi"] == ""
    assert ayarlar["sunucu api anahtari"] == ""


def test_sayfa_kapali_ayari_yansitiyor(sayfa):
    assert sayfa.chkKimlikPaylas.isChecked() is False
    assert sayfa.btnBagisGeriCek.isEnabled() is False
    assert sayfa.txtSunucuAnahtar.echoMode() == QLineEdit.EchoMode.Password


# ── Onay verilmeden gönderim yok (asıl mesele) ──────────────────────────────
def test_ayar_kapaliyken_onay_bile_sorulmuyor(sayfa, casus):
    """Kapalı ayar: diyalog açılmaz, gönderim olmaz."""
    kayit, _ = casus
    sayfa._on_cookie_ready(CEREZ)

    assert kayit["onay_sorusu"] == 0
    assert kayit["gonderim"] == []
    assert Dosyalar().ayarlar.get("kimlik bagis id") == ""


def test_onay_verilmezse_hicbir_sey_gonderilmiyor(sayfa, casus):
    """Ayar açık ama kullanıcı vazgeçti: çerez yerelde kalır, ağa çıkmaz."""
    kayit, ayar = casus
    ayar["onay"] = False
    Dosyalar().set_ayar("kimlik paylas", True)
    sayfa.reload()

    sayfa._on_cookie_ready(CEREZ)

    assert kayit["onay_sorusu"] == 1        # soruldu
    assert kayit["gonderim"] == []          # ama gönderilmedi
    assert Dosyalar().ayarlar.get("kimlik bagis id") == ""
    # Çerez yine de kaydedilmiş olmalı: bağışın reddi kontrolü boşa çıkarmaz.
    assert ".AitrWeb.Session" in Dosyalar().ayarlar.get("tranime_cookie", "")


def test_onay_verilirse_gonderiliyor_ve_numara_saklaniyor(sayfa, casus):
    kayit, ayar = casus
    ayar["onay"] = True
    Dosyalar().set_ayar("kimlik paylas", True)
    sayfa.reload()

    sayfa._on_cookie_ready(CEREZ)

    assert [d for d, _ in kayit["gonderim"]] == [CEREZ]
    assert kayit["gonderim"][0][1] == katki_dialog.KAYNAK_TRANIME
    assert Dosyalar().ayarlar["kimlik bagis id"] == "BAGIS-1234"
    assert sayfa.btnBagisGeriCek.isEnabled() is True


def test_gonderim_basarisizsa_numara_yazilmiyor(sayfa, casus):
    """Sunucu reddettiyse "bağış var" yalanı ayarlara yazılmamalı."""
    kayit, ayar = casus
    ayar["onay"] = True
    ayar["gonderim_hatasi"] = "Sunucu hatası (503)."
    Dosyalar().set_ayar("kimlik paylas", True)
    sayfa.reload()

    sayfa._on_cookie_ready(CEREZ)

    assert len(kayit["gonderim"]) == 1
    assert Dosyalar().ayarlar.get("kimlik bagis id") == ""
    assert "gönderilemedi" in sayfa.lblStatus.text()


def test_bos_cerez_teklif_bile_edilmiyor(sayfa, casus):
    kayit, ayar = casus
    ayar["onay"] = True
    Dosyalar().set_ayar("kimlik paylas", True)
    sayfa.reload()

    sayfa._kimlik_bagisi_teklif("")

    assert kayit["onay_sorusu"] == 0
    assert kayit["gonderim"] == []


# ── Onay metni ──────────────────────────────────────────────────────────────
def test_onay_metni_uc_kritik_ifadeyi_iceriyor():
    """Metin ne verildiğini, riskini ve geri dönüşü açıkça söylemeli."""
    metin = katki_dialog.onay_metni().lower()
    assert "senin adına giriş" in metin
    assert "hesabın kapanabilir" in metin
    assert "geri çekebilirsin" in metin


def test_onay_metni_pazarlama_dili_kullanmiyor():
    metin = katki_dialog.onay_metni()
    for yasak in ("destek ol", "bağış yaparak projeye", "teşekkür ederiz"):
        assert yasak not in metin.lower()
    # Site adı somut olmalı: "bir siteye" değil, hangi site olduğu yazılı.
    # `lower()` uygulanmıyor: "İ" küçüldüğünde birleşik noktalı "i̇" olur ve
    # düz "tranimeizle" araması bu yüzden tutmaz.
    assert "TRAnimeİzle" in metin


def test_diyalog_metni_gosteriyor(qtbot):
    dialog = katki_dialog.KimlikBagisDialog()
    qtbot.addWidget(dialog)
    assert dialog.lblMetin.text() == katki_dialog.onay_metni()


def test_onay_tek_tikla_verilemiyor(qtbot):
    """Onay düğmesi, risk kutusu işaretlenmeden etkin olmamalı."""
    dialog = katki_dialog.KimlikBagisDialog()
    qtbot.addWidget(dialog)

    assert dialog.btnOnay.isEnabled() is False
    dialog.chkAnladim.setChecked(True)
    assert dialog.btnOnay.isEnabled() is True
    dialog.chkAnladim.setChecked(False)
    assert dialog.btnOnay.isEnabled() is False


def test_varsayilan_dugme_vazgec(qtbot):
    """Enter'a basmak bağış yapmamalı."""
    dialog = katki_dialog.KimlikBagisDialog()
    qtbot.addWidget(dialog)

    assert dialog.btnIptal.isDefault() is True
    assert dialog.btnOnay.isDefault() is False
    assert dialog.btnOnay.autoDefault() is False


def test_reddedilen_diyalog_onay_dondurmuyor(qtbot, monkeypatch):
    """`onay_al` yalnızca Accepted'ı onay sayar."""
    for kod, beklenen in ((QDialog.DialogCode.Rejected, False),
                          (QDialog.DialogCode.Accepted, True)):
        monkeypatch.setattr(katki_dialog.KimlikBagisDialog, "exec",
                            lambda self, _k=kod: _k)
        assert katki_dialog.onay_al() is beklenen


# ── Geri çekme ──────────────────────────────────────────────────────────────
def test_geri_cekme_numarayi_temizliyor(sayfa, casus):
    kayit, _ = casus
    Dosyalar().set_ayar("kimlik bagis id", "BAGIS-1234")
    sayfa.reload()
    assert sayfa.btnBagisGeriCek.isEnabled() is True

    sayfa._bagis_geri_cek()

    assert kayit["geri_cekme"] == ["BAGIS-1234"]
    assert Dosyalar().ayarlar["kimlik bagis id"] == ""
    assert sayfa.btnBagisGeriCek.isEnabled() is False
    assert "geri çekildi" in sayfa.lblStatus.text()


def test_geri_cekme_basarisizsa_numara_kaliyor(sayfa, casus):
    """Sunucudan silinemediyse yerel numara durmalı: tek silme anahtarı o."""
    kayit, ayar = casus
    ayar["geri_cekme_hatasi"] = "Sunucuya ulaşılamadı."
    Dosyalar().set_ayar("kimlik bagis id", "BAGIS-1234")
    sayfa.reload()

    sayfa._bagis_geri_cek()

    assert kayit["geri_cekme"] == ["BAGIS-1234"]
    assert Dosyalar().ayarlar["kimlik bagis id"] == "BAGIS-1234"
    assert "geri çekilemedi" in sayfa.lblStatus.text()


def test_bagis_yokken_geri_cekme_aga_cikmiyor(sayfa, casus):
    kayit, _ = casus
    sayfa._bagis_geri_cek()
    assert kayit["geri_cekme"] == []


# ── Yapılandırma kapısı (gerçek HTTP yolu; istek hiç başlamıyor) ────────────
def test_sunucu_adresi_yoksa_gonderim_baslamiyor():
    with pytest.raises(katki_dialog.KatkiHatasi):
        katki_dialog.bagis_gonder(CEREZ, ayarlar={"sunucu api anahtari": "k"})


def test_api_anahtari_yoksa_gonderim_baslamiyor():
    with pytest.raises(katki_dialog.KatkiHatasi):
        katki_dialog.bagis_gonder(
            CEREZ, ayarlar={"sunucu adresi": "http://127.0.0.1:1"})


def test_bos_cerez_gonderilmiyor():
    with pytest.raises(katki_dialog.KatkiHatasi):
        katki_dialog.bagis_gonder("", ayarlar={"sunucu adresi": "http://127.0.0.1:1",
                                               "sunucu api anahtari": "k"})


def test_yapilandirma_ayarlardan_okunuyor(izole_ev):
    Dosyalar().set_ayar(ayar_list={"sunucu adresi": "https://ornek.test/",
                                   "sunucu api anahtari": " gizli "})
    assert katki_dialog.sunucu_yapilandirmasi() == ("https://ornek.test", "gizli")


def test_ayarlar_sayfasi_sunucu_bilgisini_kaydediyor(sayfa):
    sayfa.chkKimlikPaylas.setChecked(True)
    sayfa.txtSunucu.setText("https://ornek.test")
    sayfa.txtSunucuAnahtar.setText("gizli")
    sayfa.save()

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["kimlik paylas"] is True
    assert ayarlar["sunucu adresi"] == "https://ornek.test"
    assert ayarlar["sunucu api anahtari"] == "gizli"
