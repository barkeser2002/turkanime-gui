"""Gereksinim sihirbazı: eksik araç tespiti, gömülü araç, "Atla" tercihi.

Gerçek indirme YOK: `gereksinimler.json` ve paketler yerel HTTP sunucusundan
gelir. `subprocess` çağrıları da sahtelenir — testler makinede mpv olup
olmamasına göre değişmemeli.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

import pytest

from turkanime_api.common import requirements as core
from turkanime_api.gui.qt import prefs
from turkanime_api.gui.qt.requirements import RequirementsDialog, RequirementsService

ARAC = core.ARACLAR[1]          # "mpv"
EXE = core._calistirilabilir(ARAC)

# conftest açılış denetimini susturmak için `eksik_araclar`ı sahteliyor; tespit
# testleri gerçek gövdeyi çağırmalı, bu yüzden import anında saklanıyor.
_GERCEK_EKSIK = core.eksik_araclar


class _Sonuc:
    """`subprocess.run` dönüşünün gereken tek alanı."""

    def __init__(self, returncode):
        self.returncode = returncode


@pytest.fixture
def gercek_tespit(monkeypatch):
    monkeypatch.setattr(core, "eksik_araclar", _GERCEK_EKSIK)


# ── Tespit ───────────────────────────────────────────────────────────────────
def test_eksik_araclar_tespit_ediliyor(monkeypatch, gercek_tespit):
    kurulu = {"yt-dlp", "ffmpeg"}
    monkeypatch.setattr(core, "arac_var_mi", lambda ad: ad in kurulu)
    assert core.eksik_araclar() == ["mpv", "aria2c"]

    monkeypatch.setattr(core, "arac_var_mi", lambda _ad: True)
    assert core.eksik_araclar() == []


def test_gomulu_arac_varsa_path_taranmiyor(tmp_path, monkeypatch):
    """Paketlenmiş EXE'de araç `_MEIPASS/bin` altında; kontrol atlanmalı."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / EXE).write_bytes(b"MZ gercek ikili")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(core.subprocess, "run",
                        lambda *a, **k: pytest.fail("gömülü araç varken çağrılmamalı"))

    assert core.gomulu_arac_yolu(ARAC) == str(tmp_path / "bin" / EXE)
    assert core.arac_var_mi(ARAC) is True


def test_placeholder_gomulu_sayilmiyor(tmp_path, monkeypatch):
    """Depodaki yer tutucular (BOM'lu "# Placeholder…") araç sayılmamalı."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / EXE).write_text(
        "﻿# Placeholder for mpv - will be downloaded at runtime\r\n",
        encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert core.gomulu_arac_yolu(ARAC) is None

    cagrilar = []
    monkeypatch.setattr(core.subprocess, "run",
                        lambda cmd, **k: cagrilar.append(cmd) or _Sonuc(1))
    assert core.arac_var_mi(ARAC) is False
    assert cagrilar, "yer tutucu varken PATH taranmalı"


def test_depodaki_yer_tutucular_arac_sayilmiyor():
    """Bu depodaki `bin/*.exe` dosyaları 60 baytlık metin yer tutucular."""
    for ad in core.ARACLAR:
        assert core.gomulu_arac_yolu(ad) is None


def test_path_taramasi_exit_koduna_bakiyor(monkeypatch):
    monkeypatch.setattr(core, "gomulu_arac_yolu", lambda _ad: None)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: _Sonuc(0))
    assert core.arac_var_mi(ARAC) is True

    monkeypatch.setattr(core.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert core.arac_var_mi(ARAC) is False


# ── Paket adresi ─────────────────────────────────────────────────────────────
LISTE = [{
    "name": "mpv", "type": "7z",
    "platforms": {"windows": {"x64": "https://ornek/mpv-x64.7z",
                              "x32": "https://ornek/mpv-x32.7z"},
                  "linux": {"x64": "https://ornek/mpv-linux.tar.xz"}},
}]


def test_paket_url_platform_ve_mimariye_gore():
    assert core.paket_url(LISTE, "mpv", "windows", "x32") == "https://ornek/mpv-x32.7z"
    assert core.paket_url(LISTE, "mpv", "linux", "x64") == "https://ornek/mpv-linux.tar.xz"
    # arm64 yayımlanmamış: x64'e düş, "desteklenmiyor" deme.
    assert core.paket_url(LISTE, "mpv", "windows", "arm64") == "https://ornek/mpv-x64.7z"
    assert core.paket_url(LISTE, "mpv", "haiku", "x64") == ""
    assert core.paket_url(LISTE, "ffmpeg", "windows", "x64") == ""


# ── Kurulum ──────────────────────────────────────────────────────────────────
def _zip_bytes() -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as arsiv:
        arsiv.writestr("mpv-surum/README.txt", "belge")
        arsiv.writestr(f"mpv-surum/{EXE}", "MZ ikili")
    return tampon.getvalue()


def test_arsivden_kurulum(tmp_path, local_server):
    url = local_server(body=_zip_bytes()) + "mpv.zip"
    yol = core.indir_ve_kur(ARAC, url, str(tmp_path))
    assert yol == str(tmp_path / EXE)
    with open(yol, encoding="utf-8") as fp:
        assert fp.read() == "MZ ikili"


def test_arsivsiz_dosya_dogrudan_kuruluyor(tmp_path, local_server):
    url = local_server(body=b"MZ ikili") + EXE
    assert core.indir_ve_kur(ARAC, url, str(tmp_path)) == str(tmp_path / EXE)


def test_path_hazirla_kurulum_dizinini_ekliyor(monkeypatch, tmp_path):
    """Kurulan araç PATH'te olmalı; yoksa `sp.run(["mpv"…])` yine bulamaz."""
    monkeypatch.setattr(core, "arama_yollari", lambda: [str(tmp_path)])
    monkeypatch.setenv("PATH", "C:\\onceden")
    core.path_hazirla()
    assert str(tmp_path) in os.environ["PATH"].split(os.pathsep)

    core.path_hazirla()          # ikinci çağrı tekrar eklememeli
    assert os.environ["PATH"].split(os.pathsep).count(str(tmp_path)) == 1


def test_url_yoksa_hata(tmp_path):
    with pytest.raises(RuntimeError):
        core.indir_ve_kur(ARAC, "", str(tmp_path))


def test_arsivde_arac_yoksa_hata(tmp_path, local_server):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as arsiv:
        arsiv.writestr("baska/dosya.txt", "yok")
    url = local_server(body=tampon.getvalue()) + "mpv.zip"
    with pytest.raises(RuntimeError):
        core.indir_ve_kur(ARAC, url, str(tmp_path))


# ── Servis ───────────────────────────────────────────────────────────────────
def test_servis_eksikleri_yayiyor(qtbot, monkeypatch, ayarla):
    ayarla(gereksinim_atlandi=False)
    monkeypatch.setattr(core, "eksik_araclar", lambda *a, **k: ["mpv"])
    servis = RequirementsService()
    with qtbot.waitSignal(servis.missing_found, timeout=5000) as blocker:
        assert servis.denetle() is True
    assert blocker.args[0] == ["mpv"]


def test_servis_eksik_yoksa_sessiz(qtbot, monkeypatch, ayarla):
    ayarla(gereksinim_atlandi=False)
    monkeypatch.setattr(core, "eksik_araclar", lambda *a, **k: [])
    servis = RequirementsService()
    with qtbot.waitSignal(servis.all_present, timeout=5000):
        servis.denetle()


def test_atla_tercihi_hatirlaniyor(qtbot, monkeypatch, ayarla):
    """"Atla" dendikten sonra açılış denetimi bir daha çalışmamalı."""
    monkeypatch.setattr(core, "eksik_araclar",
                        lambda *a, **k: pytest.fail("atlanmışken denetim olmamalı"))
    servis = RequirementsService()
    assert servis.atlandi_yaz(True) is True
    assert prefs.oku().gereksinim_atlandi is True
    assert servis.denetle() is False

    # Ayarlar'dan elle istenirse tercih yok sayılır.
    monkeypatch.setattr(core, "eksik_araclar", lambda *a, **k: ["mpv"])
    with qtbot.waitSignal(servis.missing_found, timeout=5000):
        assert servis.denetle(kullanici_istegi=True) is True


def test_diyalogdaki_atla_ayara_yaziyor(qtbot, ayarla):
    ayarla(gereksinim_atlandi=False)
    servis = RequirementsService()
    dialog = RequirementsDialog(servis, ["mpv", "ffmpeg"])
    qtbot.addWidget(dialog)
    assert "mpv" in dialog.lblList.text() and "ffmpeg" in dialog.lblList.text()

    dialog.btnSkip.click()
    assert dialog.atlandi is True
    assert prefs.oku().gereksinim_atlandi is True


def test_kurulum_sonucu_diyaloga_yansiyor(qtbot, monkeypatch, ayarla):
    ayarla(gereksinim_atlandi=False)
    monkeypatch.setattr(core, "gereksinim_listesi_getir", lambda *a, **k: LISTE)
    monkeypatch.setattr(core, "paket_url", lambda *a, **k: "https://ornek/mpv.zip")

    def sahte_kur(ad, *_a, **_k):
        if ad == "ffmpeg":
            raise RuntimeError("arşiv bozuk")
        return "/kurulan/mpv"

    monkeypatch.setattr(core, "indir_ve_kur", sahte_kur)

    servis = RequirementsService()
    dialog = RequirementsDialog(servis, ["mpv", "ffmpeg"])
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(servis.install_done, timeout=10000) as blocker:
        dialog.btnInstall.click()
    qtbot.wait(100)

    assert blocker.args[0] == [("mpv", True, ""), ("ffmpeg", False, "arşiv bozuk")]
    assert "ffmpeg" in dialog.lblStatus.text()
    assert dialog.btnInstall.text() == "Tekrar Dene"


def test_liste_alinamazsa_hepsi_basarisiz(qtbot, monkeypatch, ayarla):
    ayarla(gereksinim_atlandi=False)
    def patla(*_a, **_k):
        raise OSError("ağ yok")
    monkeypatch.setattr(core, "gereksinim_listesi_getir", patla)

    servis = RequirementsService()
    with qtbot.waitSignal(servis.install_done, timeout=5000) as blocker:
        assert servis.kur(["mpv"]) is True
    assert blocker.args[0][0][0] == "mpv"
    assert blocker.args[0][0][1] is False


# ── Ana pencere ──────────────────────────────────────────────────────────────
def test_ana_pencere_sihirbazi_aciyor(main_window, qtbot):
    main_window._on_requirements_missing(["mpv"])
    dialog = main_window._req_dialog
    assert isinstance(dialog, RequirementsDialog)

    main_window._on_requirements_missing(["mpv"])       # ikinci pencere yok
    assert main_window._req_dialog is dialog

    dialog.reject()
    qtbot.wait(50)
    assert main_window._req_dialog is None


def test_eksik_yoksa_sihirbaz_acilmiyor(main_window):
    main_window._on_requirements_missing([])
    assert main_window._req_dialog is None
