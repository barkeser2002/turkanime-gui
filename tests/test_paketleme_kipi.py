"""Spec iki paketleme kipini de üretebilmeli, yayın akışı ikisini de tanımalı.

BAĞLAM: kullanıcı `_internal/` klasörünün kaybolmasını istedi (tek dosya).
Spec varsayılanı onefile oldu. Bu, yayın akışında sessiz bir kırılma riski
taşıyor: eski adım `dist/turkanime-gui` KLASÖRÜNÜ arıyordu ve yoksa
`exit 1` ile duruyordu. Yani spec'i çevirmek, tek başına, bütün release'i
GUI'siz bırakırdı.

Buradaki testler ağır derlemeyi çalıştırmıyor (dakikalar sürer, CI'da zaten
yapılıyor); iki ucu sınıyorlar:

* spec'in kip anahtarı gerçekten iki dalı da kuruyor mu,
* release.yml'deki paketleme adımı tek dosyayı da klasörü de tanıyor mu.
"""
import os
import re
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parent.parent
SPEC = KOK / "turkanime-gui.spec"
AKIS = KOK / ".github" / "workflows" / "release.yml"


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_spec_varsayilani_onefile():
    """Ayar verilmeden derlenirse tek dosya çıkmalı — istenen bu."""
    metin = _spec()
    m = re.search(r"TURKANIME_ONEFILE['\"],\s*['\"]([^'\"]+)['\"]", metin)
    assert m, "kip anahtarının varsayılanı okunamadı"
    assert m.group(1) == "1", f"varsayılan onefile değil: {m.group(1)!r}"


@pytest.mark.parametrize("deger,beklenen", [
    ("1", True), ("", True), ("evet", True),
    ("0", False), ("false", False), ("FALSE", False), ("no", False),
])
def test_kip_anahtari_beklenen_degerleri_cozuyor(deger, beklenen, monkeypatch):
    """Anahtarın çözümü spec'ten AYNEN alınıp doğrulanıyor.

    Dizgiyi teste kopyalamak yerine spec'teki ifadeyi çıkarıp çalıştırıyoruz;
    spec değişirse test onunla birlikte değişir, sessizce ayrışmaz.
    """
    metin = _spec()
    m = re.search(r"TEK_DOSYA = (os\.environ\.get\(.+\))\n", metin)
    assert m, "TEK_DOSYA ifadesi bulunamadı"
    monkeypatch.setenv("TURKANIME_ONEFILE", deger)
    # `deger` boşsa environ'da boş dizgi var; spec'in `or`suz `get(ad, '1')`
    # varsayılanı devreye girmez, boş dizgi 'false' listesinde de yok -> True.
    assert eval(m.group(1), {"os": os}) is beklenen  # noqa: S307 - spec'in kendi ifadesi


def test_spec_iki_dali_da_kuruyor():
    """onefile dalı EXE'ye veriyi gömüyor, onedir dalı COLLECT kullanıyor."""
    metin = _spec()
    assert "if TEK_DOSYA:" in metin
    assert "runtime_tmpdir=None" in metin, "onefile dalı eksik"
    assert "exclude_binaries=True" in metin, "onedir dalı eksik"
    assert "coll = COLLECT(" in metin, "onedir COLLECT'i silinmiş"


def test_onefile_dalinda_veriler_exeye_giriyor():
    """`a.binaries`/`a.datas` EXE çağrısına verilmezse exe boş kabuk olur."""
    metin = _spec()
    bas = metin.index("if TEK_DOSYA:")
    son = metin.index("else:", bas)
    dal = metin[bas:son]
    for ad in ("a.scripts", "a.binaries", "a.zipfiles", "a.datas"):
        assert ad in dal, f"onefile EXE'sine {ad} verilmemiş"


# ── Yayın akışı ─────────────────────────────────────────────────────────────
def _paketleme_adimi() -> str:
    metin = AKIS.read_text(encoding="utf-8")
    bas = metin.index("- name: Package GUI (zip)")
    son = metin.index("- name: Build CLI", bas)
    return metin[bas:son]


def test_akis_tek_dosyayi_taniyor():
    """ESKİ HATA olurdu: adım yalnızca klasörü arıyordu, onefile'da exit 1."""
    adim = _paketleme_adimi()
    assert "dist/turkanime-gui.exe" in adim, "Windows tek dosya adı geçmiyor"
    assert '-f "$EXE"' in adim, "dosya varlığı sınanmıyor"


def test_akis_klasoru_de_taniyor():
    """`TURKANIME_ONEFILE=0` ile derlenen paket de yayımlanabilmeli."""
    adim = _paketleme_adimi()
    assert "-d dist/turkanime-gui" in adim


def test_akis_ciktisiz_derlemede_duruyor():
    """Sessiz GUI'siz release bir kez yaşandı; kapı yerinde kalmalı."""
    adim = _paketleme_adimi()
    assert "::error::" in adim and "exit 1" in adim


def test_akis_zip_adi_degismedi():
    """`docs/version.json` ve güncelleyici `.zip` adlarına bağlı.

    Çıplak exe yayımlamaya geçmek indirme sayfasını ve otomatik güncellemeyi
    sessizce bozardı; tek dosya da zip içinde dağıtılıyor.
    """
    adim = _paketleme_adimi()
    assert "turkanime-gui-{sys.argv[1]}" in adim
    assert "'zip'" in adim


@pytest.mark.parametrize("os_adi,beklenen", [
    ("windows-latest", "dist/turkanime-gui.exe"),
    ("ubuntu-latest", "dist/turkanime-gui"),
    ("macos-latest", "dist/turkanime-gui"),
])
def test_her_platform_icin_dogru_ikili_adi(os_adi, beklenen):
    """Linux/macOS'ta ikili uzantısız; `.exe` aramak orada hep boş dönerdi."""
    adim = _paketleme_adimi()
    satir = next((s for s in adim.splitlines() if os_adi in s), "")
    assert satir, f"{os_adi} dalı yok"
    assert f"EXE={beklenen} " in satir + " ", f"{os_adi}: {satir.strip()}"
