"""Ortak mpv komutu: kaldığın yer, konum raporu, "İzlerken kaydet", paketleme.

ESKİ HATALAR:
* "İzlerken kaydet" hiçbir kaynakta çalışmıyordu: bütün kaynaklar
  `AdapterVideo.oynat`'tan geçiyor, o da yalnızca `dakika_hatirla` alıyor;
  `prefs.oynat` imzada olmayan ayarı sessizce atlıyordu.
* Kaldığın yer mpv'nin adrese bağlı kaydına kalmıştı; adresler token'lı
  (Tranimaci `?token=`, Anizle `?md5=`) ya da `best_video` başka aday seçiyor.

Gerçek mpv yok: `subprocess.Popen` sahte, argümanlar okunuyor.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from turkanime_api.common import mpv_oynatici
from turkanime_api.common.dosya_adi import kayit_hedefi, oynatilabilir_dosya
from turkanime_api.gui.qt import prefs
from turkanime_api.sources.adapter import AdapterVideo

DEPO = Path(__file__).resolve().parent.parent


class Anime:
    def __init__(self, slug="naruto"):
        self.slug = slug


class Bolum:
    def __init__(self, slug="naruto-5-bolum", seri="naruto"):
        self.slug = slug
        self.anime = Anime(seri)


@pytest.fixture
def popen(monkeypatch):
    kayit = {"argv": []}

    class Proc:
        returncode = 0

        def __init__(self, argv):
            kayit["argv"].append(list(argv))

        def wait(self):
            return 0

    monkeypatch.setattr(mpv_oynatici, "mpv_bul", lambda: "/opt/sahte/mpv")
    monkeypatch.setattr(mpv_oynatici.sp, "Popen", Proc)
    return kayit


# ── Saf komut ────────────────────────────────────────────────────────────────
def test_komut_konum_betik_ve_baslangic(tmp_path):
    rapor = str(tmp_path / "r, 1.json")        # virgül: script-opts'u bölmemeli
    cmd = mpv_oynatici.mpv_komutu("https://cdn/v.m3u8", referer="https://ref/",
                                  baslangic=734.0, konum_dosyasi=rapor)
    assert "--start=734" in cmd
    assert f"--script={mpv_oynatici.LUA_BETIGI}" in cmd
    assert f"--script-opts-append=turkanime-konum={rapor}" in cmd
    assert "--referrer=https://ref/" in cmd
    assert any(a.startswith("--user-agent=") for a in cmd)

    yalin = mpv_oynatici.mpv_komutu("u", baslangic=None)
    assert not any(a.startswith(("--start", "--script", "--stream-record")) for a in yalin)


def test_konum_raporu_okunuyor_eksik_ya_da_bozuk_none(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"konum": 734.2, "sure": 1420, "sebep": "quit"}))
    assert mpv_oynatici.konum_oku(str(p)) == {"konum": 734.2, "sure": 1420.0,
                                              "sebep": "quit"}
    assert mpv_oynatici.konum_oku(str(p), sil=True)["sebep"] == "quit"
    assert not p.exists(), "sil=True rapor dosyasını kaldırmalı"
    assert mpv_oynatici.konum_oku(str(p)) is None
    p.write_text("{bozuk")
    assert mpv_oynatici.konum_oku(str(p)) is None
    p.write_text("[1, 2]")
    assert mpv_oynatici.konum_oku(str(p)) is None
    assert mpv_oynatici.konum_oku(None) is None

    ayrilan = mpv_oynatici.konum_dosyasi_ayir()
    assert not os.path.exists(ayrilan), "rapor yoksa 'mpv raporlamadı' demek"


def test_lua_betigi_pakette():
    betik = Path(mpv_oynatici.LUA_BETIGI)
    assert betik.is_file()
    # Wheel paket dizinini olduğu gibi taşıyor; PyInstaller'a datas'la giriyor.
    assert betik.parent == DEPO / "turkanime_api" / "common"
    icerik = betik.read_text(encoding="utf-8")
    assert mpv_oynatici.KONUM_ANAHTARI in icerik and "end-file" in icerik
    spec = (DEPO / "turkanime-gui.spec").read_text(encoding="utf-8")
    assert "('turkanime_api/common/mpv_konum.lua', 'turkanime_api/common')" in spec


def test_gomulu_placeholder_linuxta_secilmiyor(tmp_path, monkeypatch):
    """Depodaki `bin/mpv.exe` yer tutucusu Linux'ta "Permission denied"
    veriyordu; sistemdeki mpv hiç denenmiyordu."""
    from turkanime_api.common import utils
    (tmp_path / "mpv.exe").write_text("yer tutucu")
    (tmp_path / "mpv").write_text("çalıştırılamaz")
    monkeypatch.setattr(utils, "BIN_PATH", str(tmp_path))
    monkeypatch.setattr(mpv_oynatici.shutil, "which", lambda _ad: "/usr/bin/mpv")
    # Windows'ta gömülü `mpv.exe` doğru seçim; diğerlerinde PATH'teki mpv.
    beklenen = str(tmp_path / "mpv.exe") if os.name == "nt" else "/usr/bin/mpv"
    assert mpv_oynatici.mpv_bul() == beklenen


# ── İzlerken kaydet ──────────────────────────────────────────────────────────
def test_kayit_hedefi_kok_icinde_kaliyor(tmp_path):
    assert kayit_hedefi(str(tmp_path), Bolum()) == str(tmp_path / "naruto" / "naruto-5-bolum.mkv")
    kotu = kayit_hedefi(str(tmp_path), Bolum(slug="../../x", seri="../y"))
    assert Path(kotu).resolve().is_relative_to(tmp_path.resolve())


def _video(url="https://cdn/v.m3u8?token=1", referer="https://ref/"):
    return AdapterVideo(Bolum(), url, player="SIBNET", referer=referer)


def test_izlerken_kaydet_kaynak_videosuna_ulasiyor(tmp_path, popen):
    tercih = prefs.Tercihler(indirilenler=str(tmp_path), izlerken_kaydet=True)
    prefs.oynat(_video(), tercih, bolum=Bolum())
    argv = popen["argv"][0]
    assert argv[:2] == ["/opt/sahte/mpv", "https://cdn/v.m3u8?token=1"]
    assert f"--stream-record={tmp_path / 'naruto' / 'naruto-5-bolum.mkv'}" in argv
    assert "--referrer=https://ref/" in argv

    prefs.oynat(_video(), prefs.Tercihler(indirilenler=str(tmp_path)), bolum=Bolum())
    assert not any(a.startswith("--stream-record") for a in popen["argv"][1])


def test_dakika_hatirla_kapaliysa_baslangic_verilmiyor(tmp_path, popen):
    prefs.oynat(_video(), prefs.Tercihler(indirilenler=str(tmp_path)),
                baslangic=734.0, bolum=Bolum())
    assert "--start=734" in popen["argv"][0]
    prefs.oynat(_video(), prefs.Tercihler(indirilenler=str(tmp_path),
                                          dakika_hatirla=False),
                baslangic=734.0, bolum=Bolum())
    assert not any(a.startswith("--start") for a in popen["argv"][1])
    assert "--save-position-on-quit" not in popen["argv"][1]


def test_yarim_kayit_indirilmis_sayilmiyor(tmp_path):
    hedef = kayit_hedefi(str(tmp_path), Bolum())
    Path(hedef).write_bytes(b"\x00" * 64)
    yarim = mpv_oynatici.kaydi_sonlandir(hedef, tam=False)
    assert yarim == str(tmp_path / "naruto" / "naruto-5-bolum.yarim.mkv")
    assert oynatilabilir_dosya(hedef[:-len(".mkv")]) is None

    Path(hedef).write_bytes(b"\x00" * 64)
    assert mpv_oynatici.kaydi_sonlandir(hedef, tam=True) == hedef
    assert oynatilabilir_dosya(hedef[:-len(".mkv")]) == hedef
    assert mpv_oynatici.kaydi_sonlandir(str(tmp_path / "yok.mkv"), tam=False) is None
