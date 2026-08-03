"""Güncelleme servisi: sürüm karşılaştırma, SHA-256 doğrulama, indirme konumu.

Ağa ÇIKILMAZ: `version.json` sahtelenir, paket yerel HTTP sunucusundan servis
edilir (`local_server` fixture'ı) ve hiçbir gerçek sürüm indirilmez.
"""
from __future__ import annotations

import hashlib
import os

import pytest

from turkanime_api.common import updater
from turkanime_api.common.utils import get_os
from turkanime_api.gui.qt.updates import UpdateDialog, UpdateService

PAKET = b"sahte-guncelleme-paketi" * 64
OZET = hashlib.sha256(PAKET).hexdigest()


def _surum_kaydi(url: str, checksum: str = "", surum: str = "99.0.0") -> dict:
    """`version.json` şemasında sahte kayıt."""
    return {
        "version": surum,
        "release_date": "2030-01-01T00:00:00",
        "changelog": "Test sürümü",
        "platforms": {get_os(): {"url": url, "checksum": checksum}},
    }


# ── Sürüm karşılaştırma ──────────────────────────────────────────────────────
@pytest.mark.parametrize("uzak, yerel, beklenen", [
    ("9.5.0", "9.4.12", True),
    ("10.0.0", "9.5.0", True),
    ("9.4.12", "9.4.9", True),        # metin karşılaştırmada "12" < "9" olurdu
    ("9.4.12.2", "9.4.12", True),
    ("9.4.12", "9.5.0", False),
    ("9.5.0", "10.0.0", False),
    ("9.4.12.2", "9.4.12.2", False),  # eşitlik güncelleme değildir
    ("9.5", "9.5.0", False),          # eksik parça sıfır sayılır
    ("9.5.0", "9.5", False),
    ("9.6.0-beta", "9.5.0", True),    # etiketli sürüm int()'i patlatıyordu
    ("", "9.5.0", False),
    ("bozuk", "9.5.0", False),
    ("9.5.0", "", False),
])
def test_surum_karsilastirma(uzak, yerel, beklenen):
    assert updater.yeni_mi(uzak, yerel) is beklenen


def test_surum_parcala():
    assert updater.surum_parcala("v9.4.12.2") == (9, 4, 12, 2)
    assert updater.surum_parcala("9.6.0-beta") == (9, 6, 0)
    assert updater.surum_parcala("sürüm yok") is None
    assert updater.surum_parcala(None) is None


def test_platform_paketi_sade_anahtarla_bulunuyor():
    """Anahtar "windows"; eski kod `get_platform()` ("windows_x64") arıyordu."""
    veri = _surum_kaydi("https://ornek/paket.exe", OZET)
    paket = updater.platform_paketi(veri)
    assert paket == {"url": "https://ornek/paket.exe", "checksum": OZET}
    assert updater.platform_paketi(veri, "haiku") is None


# ── Yeni sürüm tespiti ───────────────────────────────────────────────────────
def test_yeni_surum_tespiti(qtbot, monkeypatch):
    servis = UpdateService()
    monkeypatch.setattr(updater, "surum_bilgisi_getir",
                        lambda *a, **k: _surum_kaydi("https://ornek/p.exe"))
    with qtbot.waitSignal(servis.update_available, timeout=5000) as blocker:
        servis.kontrol_et(sessiz=True)
    assert blocker.args[0]["version"] == "99.0.0"


def test_ayni_surumde_guncelleme_yok(qtbot, monkeypatch):
    servis = UpdateService()
    monkeypatch.setattr(
        updater, "surum_bilgisi_getir",
        lambda *a, **k: _surum_kaydi("https://ornek/p.exe",
                                     surum=servis.mevcut_surum))
    with qtbot.waitSignal(servis.up_to_date, timeout=5000):
        servis.kontrol_et(sessiz=True)


def test_sessiz_kontrol_hata_yaymiyor(qtbot, monkeypatch):
    """Açılış denetimi ağ hatasında kullanıcıyı rahatsız etmemeli."""
    def patla(*_a, **_k):
        raise OSError("ağ yok")

    servis = UpdateService()
    monkeypatch.setattr(updater, "surum_bilgisi_getir", patla)

    hatalar = []
    servis.check_failed.connect(hatalar.append)
    servis.kontrol_et(sessiz=True)
    qtbot.wait(300)
    assert hatalar == []

    with qtbot.waitSignal(servis.check_failed, timeout=5000):
        servis.kontrol_et(sessiz=False)   # elle denetimde hata görünmeli


# ── Çekirdek: indirme + SHA-256 ──────────────────────────────────────────────
def test_checksum_uyusmazsa_dosya_siliniyor(tmp_path, local_server):
    url = local_server(body=PAKET) + "turkanime-gui-windows.exe"
    with pytest.raises(updater.IndirmeHatasi):
        updater.indir_ve_dogrula(url, str(tmp_path), "0" * 64)
    assert os.listdir(tmp_path) == [], "bozuk paket diskte bırakılmamalı"


def test_dogru_checksum_kabul_ediliyor(tmp_path, local_server):
    url = local_server(body=PAKET) + "turkanime-gui-windows.exe"
    yol = updater.indir_ve_dogrula(url, str(tmp_path), OZET)
    assert os.path.basename(yol) == "turkanime-gui-windows.exe"
    with open(yol, "rb") as fp:
        assert fp.read() == PAKET


def test_checksum_yoksa_dogrulama_atlaniyor(tmp_path, local_server):
    """`version.json` bazı sürümlerde boş özet yayımlıyor; indirme yine de geçer."""
    url = local_server(body=PAKET) + "paket.bin"
    assert os.path.isfile(updater.indir_ve_dogrula(url, str(tmp_path), ""))


# ── Servis: indirme konumu ve kurulum akışı ──────────────────────────────────
def test_indirme_ayardaki_klasore_gidiyor(qtbot, tmp_path, local_server, ayarla):
    """Paket `indirilenler` ayarına iner, `~/Downloads` sabitine değil."""
    hedef = tmp_path / "indirmelerim"
    ayarla(indirilenler=str(hedef))
    url = local_server(body=PAKET) + "turkanime-gui.exe"

    servis = UpdateService()
    with qtbot.waitSignal(servis.download_ready, timeout=10000) as blocker:
        assert servis.indir(_surum_kaydi(url, OZET)) is True
    inen = blocker.args[0]
    assert os.path.dirname(inen) == str(hedef)
    assert os.path.dirname(inen) != os.path.join(os.path.expanduser("~"),
                                                 "Downloads")


def test_konumu_ac_indirilen_dosyanin_klasorunu_aciyor(tmp_path, monkeypatch, ayarla):
    """Eski hata: dosya nereye inerse insin hep `~/Downloads` açılıyordu."""
    ayarla(indirilenler=str(tmp_path / "indirmelerim"))
    acilan = []
    monkeypatch.setattr(updater, "konumu_ac",
                        lambda dizin, *a, **k: acilan.append(dizin) or True)
    UpdateService.konumu_ac(str(tmp_path / "indirmelerim" / "paket.exe"))
    assert acilan == [str(tmp_path / "indirmelerim")]


def test_bozuk_paket_kurulum_baslatmiyor(qtbot, tmp_path, local_server, ayarla):
    """Özet uyuşmazsa diyalog kurulum talimatına GEÇMEMELİ."""
    ayarla(indirilenler=str(tmp_path))
    url = local_server(body=PAKET) + "turkanime-gui.exe"

    servis = UpdateService()
    dialog = UpdateDialog(servis, _surum_kaydi(url, "f" * 64))
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(servis.download_failed, timeout=10000):
        dialog.btnDownload.click()
    qtbot.wait(100)

    assert dialog.indirilen == "", "kurulum talimatı gösterilmemeli"
    assert not dialog.btnOpen.isVisibleTo(dialog)
    assert "SHA-256" in dialog.lblStatus.text()
    assert os.listdir(tmp_path) == []


def test_diyalog_indirme_sonrasi_talimat_gosteriyor(qtbot, tmp_path, local_server,
                                                    ayarla):
    ayarla(indirilenler=str(tmp_path))
    url = local_server(body=PAKET) + "turkanime-gui.exe"

    servis = UpdateService()
    dialog = UpdateDialog(servis, _surum_kaydi(url, OZET))
    qtbot.addWidget(dialog)
    assert "99.0.0" in dialog.lblInfo.text()
    assert "Test sürümü" in dialog.txtChangelog.toPlainText()

    with qtbot.waitSignal(servis.download_ready, timeout=10000):
        dialog.btnDownload.click()
    qtbot.wait(100)

    assert dialog.indirilen.endswith("turkanime-gui.exe")
    assert dialog.btnOpen.isVisibleTo(dialog)
    assert dialog.indirilen in dialog.lblStatus.text()


def test_desteklenmeyen_platform_hata_yayiyor(qtbot):
    servis = UpdateService()
    with qtbot.waitSignal(servis.download_failed, timeout=3000):
        assert servis.indir({"version": "99.0.0", "platforms": {}}) is False


# ── Ana pencere ──────────────────────────────────────────────────────────────
def test_acilis_denetimleri_sessiz(main_window, qtbot, monkeypatch, ayarla):
    """Açılış denetimi: sürüme bakar, "Atla" tercihine saygı duyar, uyarı yok."""
    ayarla(gereksinim_atlandi=True)
    cagrilar = []
    monkeypatch.setattr(updater, "surum_bilgisi_getir",
                        lambda *a, **k: cagrilar.append("surum") or {})

    main_window._acilis_denetimleri()
    qtbot.wait(300)

    assert cagrilar == ["surum"]
    assert main_window._update_dialog is None      # güncel: diyalog yok
    assert main_window._req_dialog is None         # atlandı: sihirbaz yok
    assert main_window.discord.bagli is False      # pypresence yok sayıldı


def test_ana_pencere_guncelleme_diyalogu_aciyor(main_window, qtbot):
    main_window._on_update_available(_surum_kaydi("https://ornek/p.exe"))
    assert isinstance(main_window._update_dialog, UpdateDialog)

    # İkinci sinyal ikinci pencere açmamalı (açılış + elle denetim çakışabilir).
    ilk = main_window._update_dialog
    main_window._on_update_available(_surum_kaydi("https://ornek/p.exe"))
    assert main_window._update_dialog is ilk

    ilk.reject()
    qtbot.wait(50)
    assert main_window._update_dialog is None
