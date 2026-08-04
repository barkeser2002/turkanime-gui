"""Release workflow'u — `version.json`'a yalnızca gerçek bir etiket yazılır.

Denetim bulgusu: bakımcı Actions arayüzünden Release workflow'unu `main`
dalında elle çalıştırdığında (`workflow_dispatch`) `GITHUB_REF_NAME` dal adıdır
ve `version.json`'a `"version": "main"` yazılıp main'e commit'leniyordu. O
andan sonra TÜM istemcilerin güncelleme kanalı ölüyor:
`updater.surum_parcala("main")` `None` döner, karşılaştırma sessizce
"güncelsin" der ve kimse bir daha güncelleme görmez.

Test workflow'u `yaml.safe_load` ile ayrıştırıp adımın içindeki gömülü Python'u
**gerçekten çalıştırır** — YAML'e bakıp "herhâlde doğrudur" demez. Ağ yok,
`git` yok; betik geçici bir dizinde koşar.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML kurulu değil")

DEPO_KOKU = Path(__file__).resolve().parent.parent
RELEASE_YML = DEPO_KOKU / ".github" / "workflows" / "release.yml"
SURUM_ADIMI = "Generate SHA-256 + version.json"


def _workflow() -> dict:
    """`release.yml`i ayrıştır.

    `on:` anahtarı YAML 1.1'de `True` boolean'ına çözülüyor; ikisini de kabul
    edip normalize ediyoruz ki test PyYAML sürümüne bağlı olmasın.
    """
    veri = yaml.safe_load(RELEASE_YML.read_text(encoding="utf-8"))
    if True in veri and "on" not in veri:
        veri["on"] = veri.pop(True)
    return veri


def _adim(job: str, ad: str) -> dict:
    for adim in _workflow()["jobs"][job]["steps"]:
        if adim.get("name") == ad:
            return adim
    raise AssertionError(f"{job}/{ad} adımı bulunamadı")


def _gomulu_betik() -> str:
    kabuk = _adim("release", SURUM_ADIMI)["run"]
    eslesme = re.search(r"python - <<'PY'\n(.*?)\nPY", kabuk, re.S)
    assert eslesme, "sürüm adımındaki gömülü Python bulunamadı"
    return eslesme.group(1)


def _kos(tmp_path: Path, ref_name: str, ref_type: str) -> dict:
    """Gömülü betiği izole bir dizinde çalıştır ve sonucu topla."""
    kok = tmp_path / f"kos-{ref_type}-{re.sub(r'[^a-zA-Z0-9]', '_', ref_name)}"
    kok.mkdir(parents=True)
    betik = kok / "gen.py"
    betik.write_text(_gomulu_betik(), encoding="utf-8")
    cikti = kok / "github_output.txt"
    cikti.touch()

    sonuc = subprocess.run(
        [sys.executable, str(betik)], cwd=str(kok), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env={**os.environ,
             "GITHUB_REF_NAME": ref_name,
             "GITHUB_REF_TYPE": ref_type,
             "GITHUB_OUTPUT": str(cikti),
             "GITHUB_REPOSITORY": "barkeser2002/turkanime-gui"},
    )

    version_json = kok / "docs" / "version.json"
    ciktilar = dict(
        satir.split("=", 1)
        for satir in cikti.read_text(encoding="utf-8").splitlines() if "=" in satir
    )
    return {
        "kod": sonuc.returncode,
        "stdout": sonuc.stdout,
        "stderr": sonuc.stderr,
        "ciktilar": ciktilar,
        "payload": (json.loads(version_json.read_text(encoding="utf-8"))
                    if version_json.is_file() else None),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1) Sürüm yalnızca etiketten
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("etiket,beklenen", [
    ("v10.0.0", "10.0.0"),
    ("10.0.0", "10.0.0"),
    ("v9.4.12.2", "9.4.12.2"),
    ("v9.5.0-beta1", "9.5.0-beta1"),
])
def test_etiketten_surum_turetiliyor(tmp_path, etiket, beklenen):
    """Mevcut tag akışı aynen çalışmaya devam etmeli."""
    sonuc = _kos(tmp_path, etiket, "tag")
    assert sonuc["kod"] == 0, sonuc["stderr"]
    assert sonuc["payload"]["version"] == beklenen
    assert sonuc["ciktilar"]["yayin"] == "true"


@pytest.mark.parametrize("dal", ["main", "feat/qt-migration-phase1", "master"])
def test_workflow_dispatch_dal_adini_yazmiyor(tmp_path, dal):
    """Denetim senaryosu: elle çalıştırma güncelleme kanalına dokunmamalı."""
    sonuc = _kos(tmp_path, dal, "branch")
    assert sonuc["kod"] == 0, sonuc["stderr"]
    assert sonuc["payload"] is None, \
        f"etiket dışı koşuda version.json yazıldı: {sonuc['payload']}"
    assert sonuc["ciktilar"]["yayin"] == "false"
    assert sonuc["ciktilar"]["surum"] == ""


def test_semver_olmayan_etiket_sessizce_gecmiyor(tmp_path):
    """`on.push.tags` kalıbı "v*" — "vnightly" de bu job'ı tetikleyebiliyor."""
    sonuc = _kos(tmp_path, "vnightly", "tag")
    assert sonuc["kod"] != 0, "çöp sürüm sessizce yazıldı"
    assert sonuc["payload"] is None
    assert "::error::" in (sonuc["stdout"] + sonuc["stderr"])


# ─────────────────────────────────────────────────────────────────────────────
# 2) Yazılan sürümün istemci tarafında gerçekten işe yaraması
# ─────────────────────────────────────────────────────────────────────────────
def test_yazilan_surum_guncelleyici_tarafindan_ayristirilabiliyor(tmp_path):
    """Zincirin öteki ucu: `updater` bu değeri karşılaştırabilmeli."""
    from turkanime_api.common import updater

    surum = _kos(tmp_path, "v10.1.0", "tag")["payload"]["version"]
    assert updater.surum_parcala(surum) is not None
    assert updater.yeni_mi(surum, "10.0.0") is True

    # Bulgunun neden ölümcül olduğunun kanıtı: dal adı hiç ayrıştırılamıyor,
    # yani "güncelleme yok" sonucu sessizce kalıcı hâle geliyordu.
    assert updater.surum_parcala("main") is None
    assert updater.yeni_mi("main", "10.0.0") is False


# ─────────────────────────────────────────────────────────────────────────────
# 3) YAML yapısı — etiket dışı koşu yayın adımlarına hiç girmemeli
# ─────────────────────────────────────────────────────────────────────────────
def test_version_json_yayin_adimi_bayraga_bagli():
    kosul = _adim("release", "Publish version.json to main").get("if", "")
    assert "yayin" in kosul, f"main'e commit adımı koşulsuz: {kosul!r}"


@pytest.mark.parametrize("ad", [
    "Create GitHub Release",
    "Prune old releases (en son 5'i koru)",
])
def test_release_adimlari_yalnizca_etikette(ad):
    kosul = _adim("release", ad).get("if", "")
    assert "ref_type" in kosul and "tag" in kosul, f"{ad!r} koşulsuz: {kosul!r}"


def test_pypi_isi_yalnizca_etikette():
    """`poetry version main` PEP 440'a uymaz; dal adı sürüm yerine geçemez."""
    kosul = _workflow()["jobs"]["pypi"].get("if", "")
    assert "ref_type" in kosul and "tag" in kosul, kosul


def test_mevcut_tag_tetikleyicileri_korundu():
    """Düzeltme yayın akışını daraltmamalı: tag desenleri yerinde kalmalı."""
    tetik = _workflow()["on"]
    assert "v*" in tetik["push"]["tags"]
    assert any(t.startswith("[0-9]") for t in tetik["push"]["tags"])
    assert "workflow_dispatch" in tetik


def test_build_isi_elle_calistirmada_da_kosuyor():
    """Elle çalıştırma bir derleme denemesi olarak kullanılabilmeli."""
    assert "if" not in _workflow()["jobs"]["build"]
