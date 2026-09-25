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


@pytest.mark.parametrize("yol", [KOK_README, DOCS_README])
def test_flaresolverr_varsayilani_dogru_anlatiliyor(yol):
    """Varsayılan dolu mu boş mu — belge koda uymalı.

    GİZLİLİK MESELESİ: `flaresolverr_url` varsayılanı BOŞ DEĞİL, projenin
    sunucusu yazılı geliyor. Yani taze bir kurulum, Cloudflare zincirinin
    3. kademesinde istekleri uzak bir sunucudan geçiriyor. README ise
    "Opsiyonel — tanımlı değilse zincir gömülü QtWebEngine'e düşer" diyerek
    varsayılanın boş olduğunu ima ediyordu. Kullanıcı, trafiğinin nereye
    gittiğini belgeden öğrenebilmeli.

    Test iki yönlü: varsayılan doluysa belge bunu söylemeli; biri varsayılanı
    boşaltırsa bu sefer "varsayılan dolu" cümlesi yalan olur ve test onu da
    yakalar.
    """
    import re as _re

    kaynak = (KOK / "turkanime_api" / "cli" / "dosyalar.py").read_text(encoding="utf-8")
    m = _re.search(r'"flaresolverr_url":\s*"([^"]*)"', kaynak)
    assert m, "flaresolverr_url varsayılanı okunamadı"
    varsayilan = m.group(1).strip()

    metin = yol.read_text(encoding="utf-8")
    if varsayilan:
        assert "VARSAYILAN OLARAK DOLU" in metin, (
            f"{yol.name}: varsayılan {varsayilan!r} (boş değil) ama belge "
            "opsiyonel/boş gibi anlatıyor")
        konak = varsayilan.split("//")[-1].split("/")[0]
        assert konak in metin, (
            f"{yol.name}: isteklerin gittiği konak ({konak}) belgede yazmıyor")
    else:
        assert "VARSAYILAN OLARAK DOLU" not in metin, (
            f"{yol.name}: varsayılan artık boş; belge hâlâ dolu olduğunu söylüyor")


# ─────────────────────────────────────────────────────────────────────────────
# Sağlayıcı rehberi: iki kopya, silinen şablon
# ─────────────────────────────────────────────────────────────────────────────
KOK_REHBER = KOK / "ANIME_PROVIDER_GUIDE.md"
DOCS_REHBER = KOK / "docs" / "ANIME_PROVIDER_GUIDE.md"


def test_iki_rehber_kopyasi_ayni():
    """`docs/index.html` docs kopyasını yüklüyor; biri güncellenip öteki unutulmasın."""
    kok = KOK_REHBER.read_text(encoding="utf-8")
    docs = re.sub(r"\]\(\.\./", "](", DOCS_REHBER.read_text(encoding="utf-8"))
    assert kok == docs, "ANIME_PROVIDER_GUIDE.md kopyaları ayrışmış"


def test_eski_adaptor_sablonu_geri_gelmedi():
    """Hiçbir yerden kullanılmayan sınıf tabanlı şablon silindi; rehber kayda yönlendiriyor."""
    assert not (KOK / "turkanime_api" / "sources" / "adapter_template.py").exists()
    rehber = KOK_REHBER.read_text(encoding="utf-8")
    assert "kayit.py" in rehber
    assert "dosyasını kopyala" not in rehber


# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare kademe sayısı kurulum biçimine göre
# ─────────────────────────────────────────────────────────────────────────────
def _pyside_istege_bagli() -> bool:
    pyproject = (KOK / "pyproject.toml").read_text(encoding="utf-8")
    return bool(re.search(r"(?im)^pyside6\s*=\s*\{[^}]*optional\s*=\s*true", pyproject))


@pytest.mark.parametrize("yol", [KOK_README, DOCS_README])
def test_cf_kademe_iddiasi_kurulum_yoluna_gore(yol):
    """"Her kurulum 5 kademe" yanlıştı: sade pip kurulumunda PySide6 yok.

    `cf_bypass` QtWebEngine kademesini `PySide6.QtWebEngineCore` bulunursa
    ekliyor; PySide6 `[gui]` ekstrasında. Sade `pip install turkanime-gui`'de
    zincir 4 kademe (FlareSolverr adresi boşsa 3). Eski iddia bir düzeltme
    notunun (`>` satırı) içindeydi, bu yüzden alıntılar da taranıyor.
    """
    assert _pyside_istege_bagli(), (
        "PySide6 artık zorunlu: her kurulum QtWebEngine taşıyor. README'deki "
        "kurulum biçimine göre kademe açıklamasını ve bu testi yeniden yaz.")
    metin = yol.read_text(encoding="utf-8")
    for yanlis in ("beş kademenin tamamını", "5 kademenin tamamını"):
        assert yanlis not in metin, f"{yol.name}: '{yanlis}' kurulum biçimine bakmıyor"
    zincir = metin.split("### Cloudflare Bypass Zinciri", 1)[1].split("\n### ", 1)[0]
    assert "[gui]" in zincir, f"{yol.name}: zincirde QtWebEngine'in [gui] şartı yazmıyor"


# ─────────────────────────────────────────────────────────────────────────────
# Python sürümü
# ─────────────────────────────────────────────────────────────────────────────
PYTHON_BELGELERI = [KOK_README, DOCS_README, KOK_REHBER, DOCS_REHBER,
                    KOK / "turkanime_api" / "gui" / "README.md"]


@pytest.mark.parametrize("yol", PYTHON_BELGELERI, ids=lambda p: str(p.relative_to(KOK)))
def test_python_surumu_pyproject_ile_tutuyor(yol):
    """Taban sürüm pyproject'le aynı; "test edilen" denmiyor.

    Belgeler "test edilen: 3.9 – 3.13" diyordu; yayın kapısı yalnızca 3.12'de
    koşuyor. Sınıflandırıcıları "test edildi" diye sunmak yanlış güven verir.
    """
    pyproject = (KOK / "pyproject.toml").read_text(encoding="utf-8")
    taban = re.search(r'(?m)^python\s*=\s*">=(\d+\.\d+)', pyproject).group(1)
    metin = yol.read_text(encoding="utf-8")
    beyanlar = set(re.findall(r"Python(?::\*\*)?\s*\**\s*(\d+\.\d+)\+", metin))
    assert beyanlar == {taban}, f"{yol.name}: {beyanlar} ≠ pyproject {taban}"
    assert "test edilen" not in metin.lower()
