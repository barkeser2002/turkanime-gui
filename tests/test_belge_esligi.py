"""İki README kopyası birbirinden ayrışmasın.

Depoda aynı metnin iki kopyası var: kök `README.md` (GitHub deposunda görünen)
ve `docs/README.md` (GitHub Pages indirme sayfası). Aralarındaki TEK meşru
fark bağıl bağlantı yolları: kökten `docs/X.md`, docs içinden `X.md`.

NEDEN TEST: v10.1.0'da paketleme onedir'den onefile'a geçti ve iki dosyada da
aynı yanlış cümle duruyordu ("QtWebEngine tek dosyaya sıkıştırıldığında
alt-sürecini bulamıyor" — ölçüldü, doğru değil). Kökteki düzeltildi;
`docs/README.md` yalnızca arama yapıldığı için fark edildi. Kullanıcıların
indirme talimatını okuduğu yer tam olarak o ikinci kopyaydı.

Kopyayı silip birleştirmek daha iyi olurdu ama GitHub Pages kendi dizininde
bir `README.md` bekliyor. Kopya kalıyorsa, ayrışmasını test yakalasın.
"""
import re
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parent.parent
KOK_README = KOK / "README.md"
DOCS_README = KOK / "docs" / "README.md"


def _normalize(metin: str, docs_icinden: bool) -> str:
    """Bağıl yol farklarını sil, kalanı karşılaştırılabilir hâle getir.

    Kökten `docs/ANILIST_OAUTH.md`, docs içinden `ANILIST_OAUTH.md`; kökten
    `LICENSE`, docs içinden `../LICENSE`. İkisini de yalın ada indiriyoruz.
    """
    if docs_icinden:
        metin = re.sub(r"\]\(\.\./", "](", metin)
    else:
        metin = re.sub(r"\]\(docs/", "](", metin)
    return metin


def test_iki_readme_yalnizca_baglanti_yolunda_ayriliyor():
    """Metin ayrışırsa hangi satırda olduğunu söyle."""
    kok = _normalize(KOK_README.read_text(encoding="utf-8"), docs_icinden=False)
    docs = _normalize(DOCS_README.read_text(encoding="utf-8"), docs_icinden=True)

    k_satir, d_satir = kok.splitlines(), docs.splitlines()
    farklar = [
        f"  satır {i + 1}:\n    README.md      : {a!r}\n    docs/README.md : {b!r}"
        for i, (a, b) in enumerate(zip(k_satir, d_satir))
        if a != b
    ]
    if len(k_satir) != len(d_satir):
        farklar.append(f"  satır sayısı farklı: {len(k_satir)} / {len(d_satir)}")

    assert not farklar, (
        "İki README ayrışmış. Birini düzeltip ötekini unutmak, kullanıcının "
        "okuduğu kopyanın yanlış kalması demek:\n" + "\n".join(farklar[:10])
    )


@pytest.mark.parametrize("yol", [KOK_README, DOCS_README])
def test_paketleme_aciklamasi_guncel(yol):
    """Onefile'a geçildi; "tek exe değil" diyen metin artık yanlış.

    Bu iddia bir de GEREKÇE veriyordu ("alt-sürecini bulamıyor") ve o gerekçe
    ölçümle çürütüldü: tek dosya paketinde `QtWebEngineProcess.exe` açılım
    dizininde duruyor ve arayüz açılıyor. Yanlış bir gerekçe, iddianın
    kendisinden daha zararlı — okuyan onu doğru sanıp aktarır.
    """
    metin = yol.read_text(encoding="utf-8")
    # "eskiden şöyle yazıyordu" diyen düzeltme notu meşru; alıntı satırları hariç.
    govde = "\n".join(s for s in metin.splitlines() if not s.lstrip().startswith(">"))
    assert "tek exe değil" not in govde, f"{yol.name}: eski paketleme iddiası duruyor"
    assert "bir klasöre çıkar" not in govde, f"{yol.name}: onedir talimatı duruyor"


@pytest.mark.parametrize("yol", [KOK_README, DOCS_README])
def test_surum_notu_baglantisi_en_yeni_surume_isaret_ediyor(yol):
    """Yeni sürüm çıkınca README'nin bağlantısı da güncellenmiş olmalı."""
    from turkanime_api.version import __version__

    metin = yol.read_text(encoding="utf-8")
    assert f"V{__version__}" in metin, (
        f"{yol.name}: sürüm {__version__} yayımlanıyor ama README hâlâ eski "
        "sürüm notlarına bağlanıyor")


@pytest.mark.parametrize("yol", [KOK_README, DOCS_README])
def test_baglanan_belgeler_gercekten_var(yol):
    """Ölü bağlantı bırakma: `](X.md)` biçimindeki her hedef dosya olmalı."""
    metin = yol.read_text(encoding="utf-8")
    taban = yol.parent
    eksik = []
    for hedef in re.findall(r"\]\(([^)\s#]+\.md)\)", metin):
        if hedef.startswith(("http://", "https://")):
            continue
        if not (taban / hedef).resolve().exists():
            eksik.append(hedef)
    assert not eksik, f"{yol.name}: olmayan belgelere bağlantı: {eksik}"


# ── Sayısal iddialar ────────────────────────────────────────────────────────
@pytest.mark.parametrize("yol", [KOK_README, DOCS_README])
def test_test_sayisi_abartilmiyor(yol):
    """README'nin verdiği test sayısı gerçekten toplanandan FAZLA olmasın.

    "723 otomatik test" yazıyordu; gerçek sayı 988'e çıkmıştı. Sayı sessizce
    eskiyor ve kimse fark etmiyor. Burada eşitlik değil ÜST SINIR aranıyor:
    test eklemek testi kırmasın, ama olduğundan çok göstermek kırsın —
    yanlış olan yön o.
    """
    metin = yol.read_text(encoding="utf-8")
    m = re.search(r"(\d[\d.]*)\s*otomatik test", metin)
    assert m, f"{yol.name}: test sayısı ifadesi bulunamadı"
    iddia = int(m.group(1).replace(".", ""))

    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", str(KOK / "tests")],
        cwd=KOK, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    g = re.search(r"(\d+) tests? collected", r.stdout)
    if not g:
        pytest.skip("toplanan test sayısı okunamadı")
    gercek = int(g.group(1))
    assert iddia <= gercek, (
        f"{yol.name}: {iddia} test olduğu yazıyor ama {gercek} toplanıyor — "
        "README olduğundan fazlasını gösteriyor")


@pytest.mark.parametrize("yol", [KOK_README, DOCS_README])
def test_cloudscraper_iddiasi_pyproject_ile_tutuyor(yol):
    """"pip kurulumunda cloudscraper gelmez" deniyordu; zorunlu bağımlılık.

    İddia yanlış olmakla kalmıyordu, kullanıcıyı gereksiz yere hazır pakete
    yönlendiriyordu. Bağımlılık kaldırılırsa bu test iddianın yeniden
    yazılmasını zorunlu kılar.
    """
    zorunlu = "cloudscraper" in (KOK / "pyproject.toml").read_text(encoding="utf-8")
    govde = "\n".join(
        s for s in yol.read_text(encoding="utf-8").splitlines()
        if not s.lstrip().startswith(">")           # düzeltme notu hariç
    )
    if zorunlu:
        assert "pip kurulumunda gelmez" not in govde, (
            f"{yol.name}: cloudscraper zorunlu bağımlılık ama README gelmediğini "
            "söylüyor")
