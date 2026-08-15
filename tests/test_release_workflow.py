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


def _kos(tmp_path: Path, ref_name: str, ref_type: str,
         artefakt: bool = True) -> dict:
    """Gömülü betiği izole bir dizinde çalıştır ve sonucu topla.

    `artefakt=True` gerçek bir yayını modelliyor: `dist/` altında GUI ve CLI
    paketleri var. Betik artık GUI paketi eksikse sürümü hiç yayınlamıyor
    (checksum'suz sürüm = doğrulanamaz güncelleme kanalı), bu yüzden dosyalar
    olmadan sürüm türetme yolları hiç çalışmıyor.
    """
    kok = tmp_path / f"kos-{ref_type}-{re.sub(r'[^a-zA-Z0-9]', '_', ref_name)}"
    kok.mkdir(parents=True)
    betik = kok / "gen.py"
    betik.write_text(_gomulu_betik(), encoding="utf-8")
    cikti = kok / "github_output.txt"
    cikti.touch()

    if artefakt:
        dist = kok / "dist"
        dist.mkdir()
        for ad in ("turkanime-gui-windows.zip", "turkanime-gui-linux.zip",
                   "turkanime-gui-macos.zip", "turkanime-cli-windows.exe",
                   "turkanime-cli-linux", "turkanime-cli-macos"):
            (dist / ad).write_bytes(b"sahte paket " + ad.encode())

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


# ─────────────────────────────────────────────────────────────────────────────
# Test kapısı — hiçbir şey testten geçmeden yayınlanmasın
# ─────────────────────────────────────────────────────────────────────────────
def test_yayin_yolunda_test_kapisi_var():
    """Etiket, testler geçmeden GitHub Release ve PyPI'a gitmemeli.

    Kapı eklenene kadar `grep pytest|pylint release.yml` sıfır sonuç veriyordu:
    709 test yayın akışının hiçbir noktasında koşmuyordu. `main.yml` var ama
    GitHub'da devre dışı, yani ona güvenilemez — kapı yayın yolunun kendi
    üstünde olmak zorunda.
    """
    isler = _workflow()["jobs"]
    assert "test" in isler, "release.yml'de test işi yok"

    def _bagimli(ad: str) -> set:
        g = isler[ad].get("needs") or []
        return {g} if isinstance(g, str) else set(g)

    assert "test" in _bagimli("build"), "build test işine bağlı değil"
    # release ve pypi build üzerinden dolaylı olarak teste bağlı
    for is_adi in ("release", "pypi"):
        assert _bagimli(is_adi) & {"test", "build"}, f"{is_adi} kapısız"


def _komut_satirlari(is_adi: str) -> list:
    """İşin `run` bloklarındaki GERÇEK komut satırları — yorumlar hariç.

    Ham metinde arama yapmak yanıltıcı: `# PyYAML: …` gibi bir yorum ya da
    `pip install … pytest` gibi bir KURULUM satırı, "pytest çalışıyor mu?"
    sorusunu yanlışlıkla olumlu yanıtlıyor. (İlk yazdığım iki kontrol tam
    bu yüzden hiçbir şey sınamıyordu; mutasyon testi yakaladı: pytest adımı
    tamamen silindiğinde bile yeşil kalıyorlardı.)
    """
    satirlar = []
    for adim in _workflow()["jobs"][is_adi]["steps"]:
        for ham in str(adim.get("run", "")).splitlines():
            s = ham.strip()
            if s and not s.startswith("#"):
                satirlar.append(s)
    return satirlar


def test_test_isi_gercekten_pytest_ve_pylint_kosuyor():
    """İşin adı "test" olması yetmez; içinde gerçekten koşan bir şey olmalı."""
    satirlar = _komut_satirlari("test")

    def _calistiriyor(arac: str) -> bool:
        # Kurulum satırını sayma: "pip install … pytest" pytest'i ÇALIŞTIRMAZ.
        return any(s.split()[0] == arac for s in satirlar if s.split())

    assert _calistiriyor("pytest") or any(
        s.startswith("python -m pytest") for s in satirlar
    ), "test işi pytest çalıştırmıyor"
    assert _calistiriyor("pylint") or any(
        s.startswith("python -m pylint") for s in satirlar
    ), "test işi pylint çalıştırmıyor"
    # `|| true` yasağı YALNIZCA kapının kendisi için. Niyet "kapı
    # susturulamasın"dı, "hiçbir yerde || true olmasın" değil: sistem paketi
    # kurulumunda meşru — ayna arızası bir sürümü rehin almamalı (v10.0.0
    # etiketinde `apt-get update` 8+ dakika asılı kaldı). İlk hâli bu ayrımı
    # yapmıyordu ve tam o düzeltmeyi engelledi.
    susturulan = [s for s in satirlar
                  if "|| true" in s and ("pytest" in s or "pylint" in s)]
    assert not susturulan, (
        f"kapı `|| true` ile susturulmuş: {susturulan} — hiçbir zaman "
        "kırmızıya dönemez (main.yml'deki lint adımlarının hatası)"
    )


def test_pytest_paketi_import_edebilecek_sekilde_cagriliyor():
    """CI'da paket import edilemezse kapı 35 toplama hatasıyla düşer.

    ÖLÇÜLDÜ (v10.0.0 etiketi): `pytest tests/` ile koşulduğunda tüm test
    modülleri `ModuleNotFoundError: No module named 'turkanime_api'` verdi,
    oysa yerelde 855 test yeşildi. Fark tek kelimede: `python -m pytest`
    çalışma dizinini `sys.path`'e ekler, konsol betiği eklemez.

    Kabul edilen iki yol var: ya `python -m pytest`, ya paketi kuran bir
    adım (`pip install -e .`). İkisi de yoksa kapı ilk koşuda patlar.
    """
    satirlar = _komut_satirlari("test")
    python_m = any(s.startswith("python -m pytest") for s in satirlar)
    kurulum = any("pip install" in s and (" -e ." in s or s.rstrip().endswith(" ."))
                  for s in satirlar)
    assert python_m or kurulum, (
        "pytest paketi import edemeyecek şekilde çağrılıyor: ya "
        "`python -m pytest` kullan ya da paketi kur (`pip install -e .`). "
        f"Bulunan komutlar: {[s for s in satirlar if 'pytest' in s]}"
    )


def test_test_isi_pyyaml_kuruyor():
    """PyYAML kurulmazsa BU DOSYADAKİ testler sessizce atlanır.

    `pytest.importorskip("yaml")` eksik bağımlılıkta testi "geçti" saymaz ama
    "atladı" sayar — kapı yalancı yeşil verir.
    """
    kurulumlar = [s for s in _komut_satirlari("test") if "pip install" in s]
    assert kurulumlar, "test işinde pip install satırı yok"
    assert any("pyyaml" in s.lower() for s in kurulumlar), (
        f"PyYAML kurulmuyor: {kurulumlar}"
    )


def test_qt_testleri_offscreen_kosuyor():
    """Runner'da ekran yok; `QT_QPA_PLATFORM` verilmezse Qt testleri çöker."""
    adimlar = _workflow()["jobs"]["test"]["steps"]
    ortamlar = [a.get("env", {}) for a in adimlar if a.get("env")]
    assert any(o.get("QT_QPA_PLATFORM") == "offscreen" for o in ortamlar)


# ─────────────────────────────────────────────────────────────────────────────
# PyPI sırrı
# ─────────────────────────────────────────────────────────────────────────────
def test_pypi_sirri_depodaki_adla_eslesiyor():
    """Sır adı tutmazsa yayın adımı sessizce atlanır.

    Depoda tanımlı sır `PYPI_TOKEN`; workflow yalnızca `PYPI_API_TOKEN`
    okuyordu. Sonuç: etiket atılır, release oluşur, iş YEŞİL görünür ve
    PyPI'a hiçbir şey gitmez. En sinsi hata sınıfı — başarı gibi görünen
    başarısızlık.
    """
    adim = _adim("pypi", "Publish to PyPI")
    # DEĞERE bak, sözlüğün tamamına değil: ortam değişkeninin ADI
    # `POETRY_PYPI_TOKEN_PYPI` ve bu ad zaten "PYPI_TOKEN" alt dizesini
    # içeriyor — sözlüğü stringe çevirip aramak her koşulda geçerdi.
    # (Bu kontrolün ilk hâli tam olarak bu yüzden hiçbir şey sınamıyordu;
    # mutasyon testi yakaladı.)
    deger = adim.get("env", {}).get("POETRY_PYPI_TOKEN_PYPI", "")
    assert "secrets.PYPI_TOKEN" in deger, (
        f"workflow depodaki sır adını (PYPI_TOKEN) okumuyor: {deger!r}"
    )


def test_checksumsuz_surum_yayinlanmiyor(tmp_path):
    """GUI paketi üretilmemişse sürüm hiç yayınlanmamalı.

    `ozet()` dosya yoksa "" döndürüyor ve eskiden bu değer version.json'a
    olduğu gibi yazılıyordu. Sonuç: sürüm yayınlanmış görünür ama istemci
    onu doğrulayamaz. Yayındaki version.json tam bu durumda — üç platformda
    da checksum boş. İstemci tarafı artık böyle bir kaydı reddediyor
    (updater.indir_ve_dogrula); yayıncı tarafı da üretmemeli.
    """
    sonuc = _kos(tmp_path, "v10.0.0", "tag", artefakt=False)
    assert sonuc["kod"] != 0, "paket yokken sürüm yayınlandı"
    assert sonuc["payload"] is None, "checksum'suz version.json yazıldı"
    assert "::error::" in (sonuc["stdout"] + sonuc["stderr"])


def test_uretilen_checksumlar_gercek_dosyadan(tmp_path):
    """Özetler sahte değil, diskteki paketin gerçek SHA-256'sı olmalı."""
    import hashlib

    sonuc = _kos(tmp_path, "v10.0.0", "tag")
    assert sonuc["payload"], sonuc["stderr"]
    beklenen = hashlib.sha256(b"sahte paket turkanime-gui-linux.zip").hexdigest()
    assert sonuc["payload"]["platforms"]["linux"]["checksum"] == beklenen


def test_hicbir_platform_bos_checksumla_yayinlanmiyor(tmp_path):
    sonuc = _kos(tmp_path, "v10.0.0", "tag")
    for isim, veri in sonuc["payload"]["platforms"].items():
        assert veri["checksum"], f"{isim} boş checksum ile yayınlandı"


def test_gui_artefakt_adi_istemcinin_bekledigi_bicimde():
    """`arsiv_mi()` `.zip` bekliyor; yayınlanan version.json `.exe` gösteriyordu."""
    betik = _gomulu_betik()
    assert 'turkanime-gui-{isim}.zip' in betik or "turkanime-gui-" in betik
    assert ".zip" in betik, "GUI artefaktı zip olarak adlandırılmıyor"


def test_pypi_sirri_yoksa_sessizce_gecilmiyor():
    """Sır eksikse iş KIRMIZI olmalı, uyarı basıp `exit 0` değil."""
    betik = _adim("pypi", "Publish to PyPI")["run"]
    assert "exit 1" in betik, (
        "sır yokken `exit 0` ile geçiliyor — yayınlanmamış bir sürüm "
        "yayınlanmış gibi görünür"
    )
    assert "::error::" in betik, "eksik sır uyarı değil hata olarak raporlanmalı"
