"""Sunucu dağıtım (Docker) testleri — imaja ne giriyor, imajda ne eksik.

İki soruyu **imaj kurmadan** yanıtlar:

1. `.dockerignore` build context'in kökünde mi ve `.env` içindeki deploy token
   gerçekten dışarıda mı? `.dockerignore` yalnızca context kökünde okunur;
   alt dizindeki bir kopya hiçbir zaman uygulanmaz. Burada Docker'ın yoksayma
   kuralları taklit edilip context'e hangi dosyaların gireceği hesaplanır.
2. Crawler'ın import ettiği üçüncü parti paketlerin hepsi
   `turkanime_server/requirements.txt` içinde mi? Import grafiği AST ile
   yürünür (modül gerçekten import edilmez: bu test kurulu olmayan bir paketi
   de yakalayabilmeli).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

KOK = Path(__file__).resolve().parents[1]
SUNUCU = KOK / "turkanime_server"


# ─────────────────────────────────────────────────────────────────────────────
# Docker yoksayma kurallarının taklidi
# ─────────────────────────────────────────────────────────────────────────────
def _desen_regex(desen: str) -> re.Pattern:
    """`.dockerignore` desenini regex'e çevir (Go `filepath.Match` + `**`)."""
    out: List[str] = []
    i, n = 0, len(desen)
    while i < n:
        if desen.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif desen.startswith("**", i):
            out.append(".*")
            i += 2
        elif desen[i] == "*":
            out.append("[^/]*")
            i += 1
        elif desen[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(desen[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


class DockerYoksay:
    """Bir `.dockerignore` dosyasının karar mekanizması.

    Docker'da **son eşleşen desen kazanır** ve bir dizini dışarıda bırakmak
    altındaki her şeyi de dışarıda bırakır; ikisi de burada uygulanır.
    """

    def __init__(self, yol: Path):
        self.kurallar: List[Tuple[bool, re.Pattern]] = []
        for satir in yol.read_text(encoding="utf-8").splitlines():
            satir = satir.strip()
            if not satir or satir.startswith("#"):
                continue
            olumsuz = satir.startswith("!")
            desen = satir.lstrip("!").strip().strip("/")
            if desen:
                self.kurallar.append((olumsuz, _desen_regex(desen)))

    def disarida(self, yol: str) -> bool:
        """``yol`` (context köküne göre, posix) build context'e girmiyor mu?"""
        parcalar = yol.split("/")
        adaylar = ["/".join(parcalar[: i + 1]) for i in range(len(parcalar))]
        karar = False
        for olumsuz, kalip in self.kurallar:
            if any(kalip.match(a) for a in adaylar):
                karar = not olumsuz
        return karar


def _compose_contextleri() -> Set[Path]:
    """docker-compose.yml'deki her servisin build context'i (mutlak yol)."""
    ham = (SUNUCU / "docker-compose.yml").read_text(encoding="utf-8")
    return {(SUNUCU / m.group(1).strip()).resolve()
            for m in re.finditer(r"^\s*context:\s*(\S+)", ham, re.M)}


def test_dockerignore_build_context_kokunde_duruyor():
    """`.dockerignore` context kökünde değilse Docker onu hiç okumaz."""
    contextler = _compose_contextleri()
    assert contextler, "docker-compose.yml içinde build context bulunamadı"
    for context in contextler:
        assert (context / ".dockerignore").is_file(), (
            f"{context} build context kökünde .dockerignore yok — alt dizindeki "
            f"bir kopya Docker tarafından okunmaz")


def test_dockerignore_alt_dizinde_yaniltici_kopya_birakmiyor():
    """Aldatıcı kopya: dosya var diye korunduğu sanılır, aslında ölü."""
    olu = SUNUCU / ".dockerignore"
    assert not olu.exists(), (
        "turkanime_server/.dockerignore build context kökünde değil; Docker onu "
        "okumaz. Kurallar depo kökündeki .dockerignore'a taşınmalı")


def test_env_sirri_build_contexte_girmiyor():
    """README `turkanime_server/.env`'e gerçek deploy token yazdırıyor.

    O dosya imaj katmanına kopyalanırsa `docker run --entrypoint cat <imaj>`
    ile okunabilir hâle gelir — imaj paylaşılan bir registry'deyse token da
    paylaşılmış olur.
    """
    yoksay = DockerYoksay(KOK / ".dockerignore")
    for gizli in ("turkanime_server/.env",
                  "turkanime_server/.env.yerel",
                  ".env",
                  "sirlar/arsiv_ed25519",
                  "turkanime_server/arsiv_ed25519.pem"):
        assert yoksay.disarida(gizli), f"{gizli} build context'e giriyor"


def test_kaynak_kodu_build_contextte_kaliyor():
    """Yoksayma fazla geniş olursa imaj çalışmaz; Dockerfile'ın kopyaladığı
    her şey içeride kalmalı."""
    yoksay = DockerYoksay(KOK / ".dockerignore")
    for gerekli in ("turkanime_server/requirements.txt",
                    "turkanime_server/Dockerfile",
                    "turkanime_server/app.py",
                    "turkanime_server/crawler/tarayici.py",
                    "turkanime_server/yayinci/yayinci.py",
                    "turkanime_server/.env.example",
                    "turkanime_api/__init__.py",
                    "turkanime_api/sources/anizle.py",
                    "turkanime_api/common/episode_parser.py"):
        assert not yoksay.disarida(gerekli), \
            f"{gerekli} build context'ten atılıyor — imaj çalışmaz"


def test_depodaki_gercek_dosyalar_taraninca_sir_kalmiyor():
    """Dockerfile'ın `COPY` ettiği ağaçta sızacak bir şey var mı (gerçek dosyalar)."""
    yoksay = DockerYoksay(KOK / ".dockerignore")
    supheli = re.compile(r"(^|/)\.env$|\.pem$|\.key$|(^|/)id_(rsa|ed25519)")
    giren: List[str] = []
    for kok in ("turkanime_api", "turkanime_server"):
        for p in (KOK / kok).rglob("*"):
            if not p.is_file():
                continue
            bagil = p.relative_to(KOK).as_posix()
            if not yoksay.disarida(bagil):
                giren.append(bagil)
    assert giren, "hiçbir dosya context'e girmiyor — yoksayma çok geniş"
    sizan = [y for y in giren if supheli.search(y)]
    assert sizan == [], f"sır niteliğinde dosya imaja giriyor: {sizan}"


# ─────────────────────────────────────────────────────────────────────────────
# Import grafiği — imajda hangi paketler kurulu olmalı
# ─────────────────────────────────────────────────────────────────────────────
# Üçüncü parti modül adı → PyPI dağıtım adı. Yeni bir bağımlılık grafiğe
# girdiğinde bu tablo da büyümeli; bilinmeyen modül testi kırar.
DAGITIM: Dict[str, str] = {
    "appdirs": "appdirs",
    "bs4": "beautifulsoup4",
    "Crypto": "pycryptodome",
    "curl_cffi": "curl-cffi",
    "flask": "flask",
    "flask_cors": "flask-cors",
    "mysql": "mysql-connector-python",
    "rapidfuzz": "rapidfuzz",
    "requests": "requests",
    "yt_dlp": "yt-dlp",
}


def _modul_yolu(ad: str) -> Path | None:
    p = KOK / Path(*ad.split("."))
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    return None


def _zorunlu_importlar(agac: ast.AST) -> List[ast.stmt]:
    """`try:` gövdesindeki ve `if TYPE_CHECKING:` altındaki importlar hariç.

    Fonksiyon içi importlar **dahil**: `kaynaklar.py`'ın tembel yükleyicileri
    koşuda mutlaka çağrılıyor, yani o paketler de imajda bulunmak zorunda.
    """
    out: List[ast.stmt] = []

    def gez(dugum: ast.AST, korumali: bool) -> None:
        for cocuk in ast.iter_child_nodes(dugum):
            alt = korumali
            if isinstance(dugum, ast.Try) and dugum.handlers and cocuk in dugum.body:
                alt = True
            if isinstance(dugum, ast.If) and cocuk in dugum.body \
                    and "TYPE_CHECKING" in ast.dump(dugum.test):
                alt = True
            if isinstance(cocuk, (ast.Import, ast.ImportFrom)) and not alt:
                out.append(cocuk)
            gez(cocuk, alt)

    gez(agac, False)
    return out


def _hedefler(dugum: ast.stmt, ad: str, yol: Path) -> List[str]:
    if isinstance(dugum, ast.Import):
        return [a.name for a in dugum.names]
    if not isinstance(dugum, ast.ImportFrom):
        return []
    if dugum.level:
        parcalar = ad.split(".")
        paket = parcalar if yol.name == "__init__.py" else parcalar[:-1]
        if dugum.level > 1:
            paket = paket[: len(paket) - (dugum.level - 1)]
        tam = ".".join(paket + ([dugum.module] if dugum.module else []))
    else:
        tam = dugum.module or ""
    if not tam:
        return []
    return [tam] + [f"{tam}.{a.name}" for a in dugum.names]


def ucuncu_parti(baslangic: Iterable[str]) -> Dict[str, Set[str]]:
    """Verilen giriş modüllerinden ulaşılan üçüncü parti kökler → kim çekti."""
    gorulen: Set[str] = set()
    yigit = list(baslangic)
    disarisi: Dict[str, Set[str]] = {}
    while yigit:
        ad = yigit.pop()
        if ad in gorulen:
            continue
        gorulen.add(ad)
        yol = _modul_yolu(ad)
        if yol is None:
            continue
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
        for dugum in _zorunlu_importlar(agac):
            for hedef in _hedefler(dugum, ad, yol):
                kok = hedef.split(".")[0]
                if kok in ("turkanime_api", "turkanime_server"):
                    parcalar = hedef.split(".")
                    for i in range(1, len(parcalar) + 1):
                        alt = ".".join(parcalar[:i])
                        if alt not in gorulen and _modul_yolu(alt):
                            yigit.append(alt)
                elif kok not in sys.stdlib_module_names and kok != "__future__":
                    disarisi.setdefault(kok, set()).add(ad)
    return disarisi


def _requirements() -> Set[str]:
    """`requirements.txt`'teki dağıtım adları (sürüm/extra ayıklanmış)."""
    adlar: Set[str] = set()
    for satir in (SUNUCU / "requirements.txt").read_text(encoding="utf-8").splitlines():
        satir = satir.split("#")[0].strip()
        if not satir:
            continue
        ad = re.split(r"[<>=!~\[; ]", satir, maxsplit=1)[0]
        adlar.add(ad.strip().lower().replace("_", "-"))
    return adlar


def test_crawlerin_cektigi_paketler_requirementsta_var():
    """Konteynerde `ImportError` = arşiv hiç üretilmez, yayıncı 'arsiv-eksik' der.

    `turkanime_api/__init__.py` daha ilk satırda `objects` → `yt_dlp`, ardından
    `bypass` → `appdirs`/`Crypto` çekiyor; crawler bu paketi import etmeden tek
    bir kaynak adaptörüne bile ulaşamıyor.
    """
    cekilen = ucuncu_parti(["turkanime_server.crawler",
                            "turkanime_server.crawler.__main__"])
    bilinmeyen = sorted(set(cekilen) - set(DAGITIM))
    assert not bilinmeyen, \
        f"tabloya eklenmemiş üçüncü parti modül: {bilinmeyen} — DAGITIM'i güncelle"

    kurulu = _requirements()
    eksik = sorted({DAGITIM[m] for m in cekilen} - kurulu)
    assert not eksik, (
        "crawler bu paketleri import ediyor ama imajda kurulu değil: "
        + ", ".join(f"{d} (çeken: {sorted(cekilen[m])[0]})"
                    for d in eksik for m in cekilen if DAGITIM[m] == d))


def test_api_sunucusunun_paketleri_de_requirementsta():
    cekilen = ucuncu_parti(["turkanime_server.app"])
    kurulu = _requirements()
    eksik = sorted({DAGITIM[m] for m in cekilen if m in DAGITIM} - kurulu)
    assert not eksik, f"app.py için eksik paket: {eksik}"


def test_dockerfile_git_ikilisini_kuruyor():
    """Yayıncı `git`i doğrudan çağırıyor; yoksa CLI 3 döndürüp hiç yayınlamaz."""
    ham = (SUNUCU / "Dockerfile").read_text(encoding="utf-8")
    kurulum = ham.split("apt-get install", 1)[1].split("rm -rf", 1)[0]
    assert re.search(r"^\s*git\s*\\?$", kurulum, re.M), \
        "Dockerfile git kurmuyor — yayıncı `git` ikilisine muhtaç"
