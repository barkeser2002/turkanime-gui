"""Arşiv dosya adları her işletim sisteminde geçerli olmalı.

Arşivde ``One Piece Movie 6: Omatsuri….json`` adlı bir bölüm dosyası vardı.
`:` Windows'ta (NTFS) geçersiz; 10.2.0 etiketinin Windows derlemesi
checkout'ta "invalid path" ile düştü ve depo Windows'ta klonlanamıyordu.
Ayna artık bu adları ``%XX`` ile tutuyor (`arsiv_paketi.disk_adi`); istemci
özgün adla istenen dosyayı disk adıyla buluyor.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

from turkanime_api.common import arsiv_paketi as paket
from turkanime_api.common import arsiv_senkron as senkron
from turkanime_api.sources import animedepo

DEPO = Path(__file__).resolve().parents[1]
OZGUN = "animeler/x/One Piece Movie 6: Ada.json"
DISK = "animeler/x/One Piece Movie 6%3A Ada.json"

# Windows'ta geçersiz: yasak karakterler, kontrol karakterleri, sondaki
# nokta/boşluk ve ayrılmış aygıt adları.
_YASAK = re.compile(r'[<>:"|?*\x00-\x1f]|[ .]$')
_AYGIT = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
          *(f"LPT{i}" for i in range(1, 10))}


@pytest.mark.parametrize("ad, beklenen", [
    ("One Piece Movie 6: Ada.json", "One Piece Movie 6%3A Ada.json"),
    ('a<b>c"d|e?f*g.json', "a%3Cb%3Ec%22d%7Ce%3Ff%2Ag.json"),
    ("sonda nokta.", "sonda nokta%2E"),
    ("sonda bosluk ", "sonda bosluk%20"),
    ("naruto-1-bolum.json", "naruto-1-bolum.json"),
    ("Bölüm 1 — Türkçe.json", "Bölüm 1 — Türkçe.json"),
])
def test_disk_adi(ad, beklenen):
    assert paket.disk_adi(ad) == beklenen
    # İdempotent: ayna/GitHub paketi zaten disk adlı; ikinci geçiş değiştirmemeli.
    assert paket.disk_adi(beklenen) == beklenen


def test_depodaki_arsivde_windows_da_gecersiz_ad_yok():
    """Depoya Windows'ta klonlanamayan bir yol bir daha girmesin."""
    try:
        cikti = subprocess.run(["git", "ls-files", "-z", "arsiv"], cwd=DEPO,
                               capture_output=True, check=True).stdout
        yollar = [y for y in cikti.decode("utf-8").split("\0") if y]
    except (OSError, subprocess.CalledProcessError):
        yollar = [p.relative_to(DEPO).as_posix()
                  for p in (DEPO / "arsiv").rglob("*") if p.is_file()]
    assert yollar, "arşiv dosyaları listelenemedi"
    gecersiz = [y for y in yollar
                if any(_YASAK.search(p) or p.split(".")[0].upper() in _AYGIT
                       for p in y.split("/"))]
    assert gecersiz == []


def test_ozgun_adla_istenen_dosya_disk_adiyla_okunuyor(tmp_path):
    (tmp_path / "animeler" / "x").mkdir(parents=True)
    (tmp_path / DISK).write_text(json.dumps([{"player": "SIBNET"}]), encoding="utf-8")
    assert animedepo._yerelden_oku(tmp_path, OZGUN) == [{"player": "SIBNET"}]


@pytest.mark.skipif(os.name == "nt", reason="özgün `:`'li ad Windows'ta yazılamaz")
def test_eski_indirilmis_arsivdeki_ozgun_ad_da_bulunuyor(tmp_path):
    """Bu düzeltmeden önce Linux/macOS'ta indirilen arşivde ad özgün hâliyle."""
    (tmp_path / "animeler" / "x").mkdir(parents=True)
    (tmp_path / OZGUN).write_text("[1]", encoding="utf-8")
    assert animedepo._yerelden_oku(tmp_path, OZGUN) == [1]


def test_github_aynasi_disk_adini_gitlab_ozgun_adi_istiyor():
    github = animedepo._uzak_url(animedepo.GITHUB_AYNA_URL, OZGUN)
    gitlab = animedepo._uzak_url(animedepo.BASE_URL, OZGUN)
    # GitHub'daki dosya adı "%3A" taşıyor; "%" kodlanmazsa sunucu onu ":"
    # diye çözer ve dosyayı bulamaz.
    assert github.endswith("/animeler/x/One%20Piece%20Movie%206%253A%20Ada.json")
    assert gitlab.endswith("/animeler/x/One%20Piece%20Movie%206%3A%20Ada.json")
    assert animedepo._uzak_url(animedepo.BASE_URL, "dizin.json").endswith("/dizin.json")


def test_onbellek_disk_adini_kullaniyor(tmp_path, monkeypatch):
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: tmp_path)
    animedepo._onbellege_yaz(OZGUN, [1, 2])
    assert (tmp_path / DISK).is_file()
    assert animedepo._onbellekten_oku(OZGUN) == [1, 2]


def _tar(uyeler):
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as tar:
        for ad, veri in uyeler.items():
            bilgi = tarfile.TarInfo(ad)
            bilgi.size = len(veri)
            tar.addfile(bilgi, io.BytesIO(veri))
    tampon.seek(0)
    return tampon


def test_paketteki_iki_noktali_uye_disk_adiyla_aciliyor():
    """Eskiden Windows'ta bu üye atlanıyordu (o bölüm orada hiç açılmıyordu)."""
    with tarfile.open(fileobj=_tar({"animedepo-master/" + OZGUN: b"[]"})) as tar:
        uye = tar.getmembers()[0]
        assert paket._uye_yolu(uye, "animedepo-*", None) == tuple(DISK.split("/"))


def test_senkron_ozgun_adi_disk_adiyla_aynaliyor(tmp_path):
    kaynak, hedef = tmp_path / "kaynak", tmp_path / "hedef"
    (kaynak / "animeler" / "x").mkdir(parents=True)
    (kaynak / OZGUN).write_bytes(b"[1]")
    (kaynak / "dizin.json").write_bytes(b"{}")

    plan = senkron.ayna_plani(kaynak, hedef)
    assert DISK in plan.eklenen and OZGUN not in plan.eklenen
    senkron.plani_uygula(plan, kaynak, hedef)
    assert (hedef / DISK).read_bytes() == b"[1]"

    # İkinci tur: hiçbir şey değişmemeli (`:`'li dosya her turda silinip
    # yeniden eklenmemeli).
    ikinci = senkron.ayna_plani(kaynak, hedef)
    assert (ikinci.eklenen, ikinci.degisen, ikinci.silinen) == ([], [], [])
