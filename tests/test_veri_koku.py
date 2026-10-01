"""Veri kökü kuralı: ortam değişkeni > depo (klasör ya da worktree) > ~/Turkanime.

ESKİ HATA: kural iki yerde (`Dosyalar` ve `animedepo.veri_koku`) ayrı ayrı
yazılmıştı ve ikisi de yalnızca `.git` KLASÖRÜNE bakıyordu. `git worktree`'de
`.git` bir dosya: worktree'den açılan uygulama ve testler sessizce
`~/Turkanime`'ye, yani kullanıcının gerçek ayarlarına düşüyordu.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from turkanime_api.cli import dosyalar
from turkanime_api.sources import animedepo

# Modül yüklenirken alınan ASIL fonksiyon: conftest'in yalıtımı
# `dosyalar.veri_koku`'yu her test için sarıyor.
ASIL = dosyalar.veri_koku


@pytest.fixture
def ortamsiz(monkeypatch):
    monkeypatch.delenv(dosyalar.VERI_DIZINI_ORTAM, raising=False)


def test_worktree_git_dosyasi_depo_sayiliyor(tmp_path, monkeypatch, ortamsiz):
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /bir/yer/.git/worktrees/wt\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    assert ASIL() == wt


def test_git_klasoru_depo_sayiliyor(tmp_path, monkeypatch, ortamsiz):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    assert ASIL() == tmp_path


def test_git_yoksa_ev_klasoru(tmp_path, monkeypatch, ortamsiz):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "ev"))
    assert ASIL() == tmp_path / "ev" / "Turkanime"


def test_ortam_degiskeni_depodan_once_geliyor(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    hedef = tmp_path / "tasinabilir" / "veri"
    monkeypatch.setenv(dosyalar.VERI_DIZINI_ORTAM, str(hedef))
    assert ASIL() == hedef


def test_bos_ortam_degiskeni_yok_sayiliyor(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(dosyalar.VERI_DIZINI_ORTAM, "   ")
    assert ASIL() == tmp_path


def test_dosyalar_ve_arsiv_ayni_koku_kullaniyor(tmp_path, monkeypatch):
    """Tek kural: ayarlar ile arşiv önbelleği aynı köke düşmeli."""
    hedef = tmp_path / "ic" / "ice"                 # yok: Dosyalar yaratmalı
    monkeypatch.setenv(dosyalar.VERI_DIZINI_ORTAM, str(hedef))
    d = dosyalar.Dosyalar()
    assert Path(d.ta_path) == hedef
    assert Path(d.ayar_path).is_file()
    assert animedepo.veri_koku() == hedef


def test_yalitim_gercek_koke_yazdirmiyor(monkeypatch, tmp_path_factory):
    """Depo kökünden koşan test bile geçici köke yazıyor (conftest yalıtımı)."""
    monkeypatch.delenv(dosyalar.VERI_DIZINI_ORTAM, raising=False)
    gecici = tmp_path_factory.getbasetemp().resolve()
    d = dosyalar.Dosyalar()
    Path(d.ayar_path).resolve().relative_to(gecici)
    animedepo.veri_koku().resolve().relative_to(gecici)
