"""Paketleme yüzeyi: bağımlılık listeleri, gömülü gereksinimler, spec ve yayın adımı.

Bu dosyadaki testlerin ortak teması, "kaynaktan çalıştıranda doğru, dağıtılan
pakette yanlış" sınıfı hatalar. Böyle hatalar hiçbir çalışma zamanı testinde
görünmez: geliştirme ağacında her şey yerindedir, bozulan yalnızca kullanıcının
indirdiği şeydir.

Sınanan dört eski hata:

1. `cloudscraper` yalnızca `requirements.txt`'teydi, `pyproject.toml`'da yoktu —
   PyPI'dan kuran CF zincirini 5 yerine 4 kademeyle çalıştırıyordu.
2. Gereksinim listesi YALNIZCA upstream raw URL'sinden alınıyordu; pakete
   gömülü `gereksinimler.json` hiç okunmuyordu.
3. `release.yml` yt-dlp'yi `curl -L` (`-f` YOK) ile indiriyordu — HTTP hatasında
   hata sayfası `.exe` adıyla pakete giriyordu.
4. PyInstaller `bin/` klasörünün tamamını üç platforma da koyuyordu; gerçek
   ikilileri yalnızca Windows adımı indirdiği için Linux/macOS paketleri
   57-60 baytlık yer tutucu metinleri `.exe` adıyla taşıyordu.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

DEPO_KOKU = Path(__file__).resolve().parent.parent
PYPROJECT = DEPO_KOKU / "pyproject.toml"
REQ_TXT = DEPO_KOKU / "requirements.txt"
REQ_GUI = DEPO_KOKU / "requirements-gui.txt"
SPEC = DEPO_KOKU / "turkanime-gui.spec"
RELEASE_YML = DEPO_KOKU / ".github" / "workflows" / "release.yml"

pytestmark = pytest.mark.skipif(not PYPROJECT.is_file(),
                                reason="kaynak ağacından çalışmıyor")


def _toml_yukle(yol: Path) -> dict:
    """TOML'u stdlib ile, yoksa `toml` paketiyle oku."""
    try:
        import tomllib
        return tomllib.loads(yol.read_text(encoding="utf-8"))
    except ImportError:                      # Python < 3.11
        toml = pytest.importorskip("toml", reason="tomllib/toml yok")
        return toml.loads(yol.read_text(encoding="utf-8"))


def _normal(ad: str) -> str:
    """PEP 503 paket adı normalizasyonu (`PySide6` → `pyside6`)."""
    return re.sub(r"[-_.]+", "-", ad).lower()


def _requirements_adlari(yol: Path) -> set:
    """`requirements*.txt` içindeki paket adları (extras/sürüm sınırı atılır)."""
    adlar = set()
    for ham in yol.read_text(encoding="utf-8").splitlines():
        satir = ham.strip()
        if not satir or satir.startswith("#") or satir.startswith("-"):
            continue                          # yorum ve `-r ...` satırları
        ad = re.split(r"[\[<>=!~;]", satir, maxsplit=1)[0].strip()
        if ad:
            adlar.add(_normal(ad))
    return adlar


def _poetry_adlari() -> set:
    """pyproject'teki tüm bildirilen bağımlılıklar (runtime + dev)."""
    veri = _toml_yukle(PYPROJECT)["tool"]["poetry"]
    adlar = {_normal(a) for a in veri["dependencies"] if a != "python"}
    for grup in veri.get("group", {}).values():
        adlar |= {_normal(a) for a in grup.get("dependencies", {})}
    return adlar


# ─────────────────────────────────────────────────────────────────────────────
# 1) Bağımlılık listeleri
# ─────────────────────────────────────────────────────────────────────────────
def test_cloudscraper_pypi_paketinde_de_var():
    """CF zincirinin bir kademesi PyPI kurulumunda sessizce kayboluyordu.

    `common/cf_bypass.py` `import cloudscraper`ı `try/except ImportError` ile
    sarıyor ve `HAS_CLOUDSCRAPER=False` olduğunda kademe `_available_methods`
    listesine hiç girmiyor — hata yok, uyarı yok. `requirements.txt`'te olması
    yalnızca depoyu klonlayanı kurtarıyordu.
    """
    dep = _toml_yukle(PYPROJECT)["tool"]["poetry"]["dependencies"]
    kayit = {_normal(a): v for a, v in dep.items()}.get("cloudscraper")
    assert kayit is not None, "cloudscraper pyproject bağımlılıklarında yok"
    # Opsiyonel/extras'a konursa `pip install turkanime-gui` yine getirmez.
    assert not (isinstance(kayit, dict) and kayit.get("optional")), \
        "cloudscraper opsiyonel — düz kurulumda yine gelmez"


def test_cloudscraper_gercekten_kullaniliyor():
    """Bağımlılığı eklemeden önceki soru: ölü kod mu? Değil.

    Kaldırılabilir bir paketi bağımlılığa eklemek de bir hata olurdu; kanıt
    kaynakta duruyor.
    """
    kaynak = (DEPO_KOKU / "turkanime_api" / "common" / "cf_bypass.py") \
        .read_text(encoding="utf-8")
    assert "cloudscraper.create_scraper(" in kaynak
    assert "_try_cloudscraper" in kaynak


@pytest.mark.parametrize("dosya", ["requirements.txt", "requirements-gui.txt"])
def test_requirements_dosyalari_pyproject_ile_ortusuyor(dosya):
    """İki liste ayrışırsa kaynaktan kuranla PyPI'dan kuran farklı davranır.

    Tam da cloudscraper'ın başına gelen buydu; bu kontrol bir sonraki
    ayrışmayı ilk eklendiği anda yakalar.
    """
    eksikler = _requirements_adlari(DEPO_KOKU / dosya) - _poetry_adlari()
    assert not eksikler, f"{dosya} pyproject'te olmayan paket istiyor: {eksikler}"


# ─────────────────────────────────────────────────────────────────────────────
# 2) Gömülü gereksinimler.json
# ─────────────────────────────────────────────────────────────────────────────
def test_liste_indirilemezse_gomulu_kopyaya_dusuyor(monkeypatch):
    """Upstream erişilemezse sihirbaz tamamen ölüyordu.

    Liste tek kaynaktan (KebabLord raw URL) geliyordu; o depo silinse ya da
    kullanıcı ağsız olsa "liste alınamadı" deyip hiçbir araç kurulamıyordu —
    oysa aynı dosyanın gömülü kopyası paketin içinde duruyor.
    """
    from turkanime_api.common import requirements as req

    def _ag_yok(*_a, **_k):
        raise OSError("ağ yok")

    monkeypatch.setattr(req.requests, "get", _ag_yok)

    liste = req.gereksinim_listesi_getir()
    assert liste, "gömülü kopyaya düşülmedi"
    assert {k["name"] for k in liste} >= {"mpv", "aria2c", "yt-dlp"}


def test_gomulu_liste_paket_url_ile_uyumlu(monkeypatch):
    """Yedek liste yalnızca "var" olmamalı, aynı biçimde OKUNABİLİR olmalı.

    `paket_url` `platforms.<os>.<mimari>` bekliyor; gömülü dosya farklı bir
    şema kullansaydı yedek sessizce boş adres döndürür ve kurulum
    "bu platform için indirme adresi yok" ile ölürdü.
    """
    from turkanime_api.common import requirements as req

    monkeypatch.setattr(req.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ağ yok")))

    liste = req.gereksinim_listesi_getir()
    url = req.paket_url(liste, "mpv", isletim="windows", mimari="x64")
    assert url.startswith("http"), f"gömülü listeden URL çıkmadı: {url!r}"


def test_gomulu_kopya_da_yoksa_hata_yutulmuyor(monkeypatch):
    """Yedek de yoksa çağıran hatayı GÖRMELİ.

    Sessizce boş liste dönmek, `gui/qt/requirements` tarafında "böyle bir araç
    yok"a çevrilirdi: kullanıcı ağ hatası yerine yanlış bir teşhis görürdü.
    """
    from turkanime_api.common import requirements as req

    def _ag_yok(*_a, **_k):
        raise OSError("ağ yok")

    monkeypatch.setattr(req.requests, "get", _ag_yok)
    monkeypatch.setattr(req, "gomulu_gereksinim_yolu", lambda: None)

    with pytest.raises(OSError):
        req.gereksinim_listesi_getir()


def test_gomulu_dosya_pakete_konuyor():
    """Yedek ancak pakete girerse yedektir — spec onu datas'a koymalı."""
    assert "('gereksinimler.json', '.')" in SPEC.read_text(encoding="utf-8")
    veri = json.loads((DEPO_KOKU / "gereksinimler.json").read_text(encoding="utf-8"))
    assert isinstance(veri, list) and veri


# ─────────────────────────────────────────────────────────────────────────────
# 3) release.yml — yt-dlp indirmesi
# ─────────────────────────────────────────────────────────────────────────────
def _hazirlik_adimi() -> str:
    yaml = pytest.importorskip("yaml", reason="PyYAML kurulu değil")
    veri = yaml.safe_load(RELEASE_YML.read_text(encoding="utf-8"))
    for adim in veri["jobs"]["build"]["steps"]:
        if adim.get("name") == "Prepare embedded runtime (Windows only)":
            return str(adim["run"])
    raise AssertionError("gömülü çalışma zamanı adımı bulunamadı")


def test_yt_dlp_indirmesi_http_hatasinda_duruyor():
    """`curl -L` (`-f` yok) HTTP 404/5xx gövdesini de dosyaya yazıyordu.

    Sonuç: bir hata SAYFASI `bin/yt-dlp.exe` adıyla pakete giriyor, kullanıcı
    çalıştırdığında çözümleme sessizce ölüyordu. mpv/ffmpeg aynı adımda
    `throw` ile korunuyor, aria2 arşivden çıkarıldığı için kazara korunuyor;
    doğrudan indirilen yt-dlp'de hiçbir kapı yoktu.
    """
    satirlar = [s.strip() for s in _hazirlik_adimi().splitlines()
                if s.strip() and not s.strip().startswith("#")]
    # Satırın BAŞI curl olmalı: hata mesajının içinde geçen "curl" kelimesi
    # de aksi hâlde indirme satırı sayılıyor.
    indirme = [s for s in satirlar
               if s.split()[0].startswith("curl") and "yt-dlp.exe" in s]
    assert len(indirme) == 1, f"yt-dlp indirme satırı: {indirme}"

    bayraklar = [p for p in indirme[0].split() if p.startswith("-")]
    assert any("f" in b.lstrip("-") for b in bayraklar), (
        f"curl `-f` almıyor, hata sayfası .exe olarak yazılır: {indirme[0]!r}"
    )

    # Bayrak tek başına yetmez: curl bazı sürümlerde boş dosya bırakabiliyor.
    # Yalnızca BİR SONRAKİ indirmeye kadar olan satırlara bakılıyor; aşağıdaki
    # mpv/ffmpeg `throw`ları bu kontrolü yanlışlıkla yeşile boyamasın.
    sonraki = satirlar[satirlar.index(indirme[0]) + 1:]
    kesme = next((i for i, s in enumerate(sonraki)
                  if s.split()[0].startswith("curl")), len(sonraki))
    kapi = sonraki[:kesme]
    assert any("throw" in s for s in kapi), \
        f"yt-dlp indirmesinden sonra hiçbir doğrulama yok: {kapi}"
    assert any("LASTEXITCODE" in s for s in kapi), \
        "curl çıkış kodu hiç kontrol edilmiyor"


def test_diger_araclarin_korumasi_bozulmadi():
    """Düzeltme yalnızca yt-dlp'yi eklemeli; mevcut kapılar yerinde kalmalı."""
    adim = _hazirlik_adimi()
    assert "mpv.exe not found after extraction" in adim
    assert "ffmpeg.exe not found after extraction" in adim


# ─────────────────────────────────────────────────────────────────────────────
# 4) spec — bin/ yalnızca gerçek ve platforma uygun ikililer
# ─────────────────────────────────────────────────────────────────────────────
def _spec_yardimcilari() -> dict:
    """Spec'ten yalnızca saf fonksiyonları çıkar ve çalıştır.

    Dosyanın tamamı exec edilemez: `Analysis`/`PYZ`/`COLLECT` PyInstaller'ın
    enjekte ettiği global'ler ve `collect_all('PySide6')` gerçek bir toplama
    yapar. AST'ten sadece ilgili fonksiyonları alıp derlemek, spec'in
    GERÇEK kodunu sınamayı sürdürürken bu bağımlılıkları atlar.
    """
    import os
    import sys

    agac = ast.parse(SPEC.read_text(encoding="utf-8"))
    istenen = {"_yer_tutucu_mu", "_bin_verileri"}
    dugumler = [d for d in agac.body
                if isinstance(d, ast.FunctionDef) and d.name in istenen]
    assert {d.name for d in dugumler} == istenen, \
        f"spec'te beklenen yardımcılar yok: {[d.name for d in dugumler]}"

    ad_alani = {"os": os, "sys": sys}
    exec(compile(ast.Module(body=dugumler, type_ignores=[]), str(SPEC), "exec"),
         ad_alani)
    return ad_alani


@pytest.fixture
def bin_dizini(tmp_path, monkeypatch):
    """Geçici bir `bin/` kur ve oraya geç (spec göreli yol kullanıyor)."""
    def _kur(**dosyalar):
        kok = tmp_path / "proje"
        (kok / "bin").mkdir(parents=True)
        for ad, icerik in dosyalar.items():
            (kok / "bin" / ad).write_bytes(icerik)
        monkeypatch.chdir(kok)
        return kok
    return _kur


# Depodaki gerçek yer tutucu biçimi: BOM + "#" ile başlayan tek satır.
YER_TUTUCU = "﻿# Placeholder for yt-dlp - will be downloaded at runtime\r\n".encode()
IKILI = b"MZ" + b"\x00" * 4096


def test_yer_tutucular_pakete_girmiyor(bin_dizini):
    """57-60 baytlık metin dosyaları `.exe` adıyla pakete giriyordu.

    Çalışma anını bozmuyorlardı (`_placeholder_mi` onları "kurulu" saymıyor)
    ama dağıtılan pakette bir aracın var gibi görünmesi, hata ayıklarken
    yanlış yöne bakmak demek.
    """
    bin_dizini(**{"yt-dlp.exe": YER_TUTUCU})
    verilenler = _spec_yardimcilari()["_bin_verileri"]("win32")
    assert verilenler == [], f"yer tutucu pakete girdi: {verilenler}"


def test_windows_hedefinde_gercek_ikili_giriyor(bin_dizini):
    """Düzeltme asıl işi bozmamalı: gerçek ikili Windows paketine girmeli."""
    bin_dizini(**{"mpv.exe": IKILI, "yt-dlp.exe": YER_TUTUCU})
    verilenler = _spec_yardimcilari()["_bin_verileri"]("win32")
    assert [h for _k, h in verilenler] == ["bin"]
    assert [Path(k).name for k, _h in verilenler] == ["mpv.exe"]


@pytest.mark.parametrize("hedef", ["linux", "darwin"])
def test_windows_ikilileri_diger_platformlara_girmiyor(bin_dizini, hedef):
    """Gerçek ikilileri indiren CI adımı `matrix.os == 'windows-latest'`.

    PyInstaller ise `bin/` klasörünün tamamını üç platformda da kopyalıyordu:
    Linux/macOS zip'leri Windows'a ait `.exe` dosyaları taşıyordu.
    """
    bin_dizini(**{"mpv.exe": IKILI, "mpv": IKILI})
    verilenler = _spec_yardimcilari()["_bin_verileri"](hedef)
    assert [Path(k).name for k, _h in verilenler] == ["mpv"]


def test_bin_yoksa_sessizce_geciliyor(tmp_path, monkeypatch):
    """`bin/` hiç yokken (temiz klon) paketleme çökmemeli."""
    monkeypatch.chdir(tmp_path)
    assert _spec_yardimcilari()["_bin_verileri"]("win32") == []
