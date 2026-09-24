"""Bakımcı aracı: depodaki `arsiv/` aynasını AnimeDepo'nun son hâliyle eşitle.

Kullanım (depo kökünden):

    python -m turkanime_api.common.arsiv_senkron --hedef arsiv
    python -m turkanime_api.common.arsiv_senkron --hedef arsiv --kaynak URL --dal master

Ne yapar:
1. Kaynağı geçici bir klasöre `git clone --depth 1` ile çeker (geçmiş gerekmez;
   yalnızca son commit'in ağacı aynalanıyor).
2. Klonun geçerli bir AnimeDepo arşivi olduğunu doğrular — değilse hedefe HİÇ
   dokunmadan çıkar (yarım/bozuk bir klon aynayı silip boşaltmasın).
3. Hedefi klonla eşitler: yeni ve değişen dosyalar kopyalanır, kaynakta artık
   olmayanlar silinir. Hedefin kök klasöründeki `README.md` ve `KAYNAK.json`
   BİZİM dosyalarımız; kaynakta karşılıkları olsa bile ellenmez.
4. `KAYNAK.json`'ı yeniden yazar (hangi commit, ne zaman, kaç anime/dosya).

Hesap (hangi dosya eklenecek/silinecek) ile uygulama ayrı saf fonksiyonlar:
testler git'e ve ağa çıkmadan `tmp_path` altında iki küçük ağaçla sınıyor.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import arsiv_paketi as paket

VARSAYILAN_KAYNAK = "https://gitlab.com/AnimeDepo/animedepo"
VARSAYILAN_DAL = "master"
# Hedefin KÖKÜNDEKİ bizim dosyalarımız. Alt klasörlerdeki aynı adlı dosyalar
# arşivin parçasıdır, normal aynalanır.
KORUNANLAR = frozenset({"README.md", paket.MANIFEST_DOSYASI})
# Her iki tarafta da yok sayılan kök klasörler (klonun kendi git verisi).
HARIC_KLASORLER = frozenset({".git"})

Klonlayici = Callable[[str, str, Path], Tuple[Optional[str], Optional[str]]]


@dataclass
class AynaPlani:
    """Hedefi kaynağa eşitlemek için gereken değişiklikler (göreli POSIX yollar)."""

    eklenen: List[str] = field(default_factory=list)
    degisen: List[str] = field(default_factory=list)
    silinen: List[str] = field(default_factory=list)

    @property
    def bos(self) -> bool:
        return not (self.eklenen or self.degisen or self.silinen)


# ─────────────────────────────────────────────────────────────────────────────
# Saf kısım: ağaç karşılaştırma ve uygulama
# ─────────────────────────────────────────────────────────────────────────────
def dosyalari_listele(kok: Path, korunanlar: Iterable[str] = KORUNANLAR,
                      haric_klasorler: Iterable[str] = HARIC_KLASORLER) -> Dict[str, Path]:
    """``kok`` altındaki düz dosyalar: {göreli POSIX yol: mutlak yol}.

    Sembolik bağlar izlenmez ve listelenmez: AnimeDepo'da yok, olursa da bir
    bağın hedefini depoya kopyalamak istemeyiz.
    """
    kok = Path(kok)
    korunanlar, haric = set(korunanlar), set(haric_klasorler)
    sonuc: Dict[str, Path] = {}
    if not kok.is_dir():
        return sonuc
    for klasor, alt_klasorler, dosyalar in os.walk(kok):
        goreli_klasor = Path(klasor).relative_to(kok)
        if goreli_klasor == Path("."):
            alt_klasorler[:] = [d for d in alt_klasorler if d not in haric]
        alt_klasorler[:] = [d for d in alt_klasorler
                            if not (Path(klasor) / d).is_symlink()]
        for ad in dosyalar:
            yol = Path(klasor) / ad
            if yol.is_symlink() or not yol.is_file():
                continue
            goreli = (goreli_klasor / ad).as_posix()
            if goreli_klasor == Path(".") and ad in korunanlar:
                continue
            sonuc[goreli] = yol
    return sonuc


def _ayni_mi(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return filecmp.cmp(a, b, shallow=False)


def ayna_plani(kaynak: Path, hedef: Path,
               korunanlar: Iterable[str] = KORUNANLAR) -> AynaPlani:
    """Hedefi kaynağın birebir aynası yapmak için ne değişmeli?

    Karşılaştırma içerikle yapılır (önce boyut, eşitse bayt bayt): klonun
    dosya zamanları checkout anıdır, mtime'a bakmak her dosyayı "değişmiş"
    gösterirdi.
    """
    korunanlar = set(korunanlar)
    k = dosyalari_listele(kaynak, korunanlar)
    h = dosyalari_listele(hedef, korunanlar)
    return AynaPlani(
        eklenen=sorted(set(k) - set(h)),
        degisen=sorted(p for p in set(k) & set(h) if not _ayni_mi(k[p], h[p])),
        silinen=sorted(set(h) - set(k)),
    )


def _bos_klasorleri_sil(kok: Path) -> None:
    """Silinen dosyalardan geriye kalan boş klasörleri temizle (kök hariç)."""
    for klasor, _alt, _dosyalar in os.walk(kok, topdown=False):
        yol = Path(klasor)
        if yol == Path(kok):
            continue
        try:
            yol.rmdir()                  # yalnızca boşsa başarır
        except OSError:
            pass


def plani_uygula(plan: AynaPlani, kaynak: Path, hedef: Path) -> None:
    """Planı hedefe uygula. Yollar yine de doğrulanır: plan elle de kurulabilir.

    Kopyalar atomik (geçici dosya + `os.replace`): araç yarıda kesilirse hiçbir
    dosya yarım kalmaz; yeniden çalıştırmak kalanını tamamlar.

    Silme ÖNCE yapılır: kaynakta `a` dosyası `a/` klasörüne dönüştüyse (ya da
    tersi) eski dosya/klasör yerinde dururken yenisi yazılamaz.
    """
    kaynak, hedef = Path(kaynak), Path(hedef)
    for goreli in plan.silinen:
        try:
            paket.guvenli_birlestir(hedef, goreli).unlink()
        except FileNotFoundError:
            pass
    if plan.silinen:
        _bos_klasorleri_sil(hedef)
    for goreli in list(plan.eklenen) + list(plan.degisen):
        veri = paket.guvenli_birlestir(kaynak, goreli).read_bytes()
        paket.atomik_bayt_yaz(paket.guvenli_birlestir(hedef, goreli), veri)


# ─────────────────────────────────────────────────────────────────────────────
# git ve komut satırı
# ─────────────────────────────────────────────────────────────────────────────
def depo_adresi(kaynak: str) -> str:
    """`KAYNAK.json`'a yazılacak biçim: sonda "/" ve ".git" yok."""
    adres = kaynak.strip().rstrip("/")
    return adres[:-4] if adres.endswith(".git") else adres


def git_klonla(kaynak: str, dal: str, hedef: Path) -> Tuple[Optional[str], Optional[str]]:
    """Sığ klon; ``(commit, commit_tarihi_iso)`` döndürür.

    `core.autocrlf=false`: Windows'ta çalışan bir bakımcının git'i JSON'ların
    satır sonlarını değiştirirse her dosya "değişmiş" görünür ve ayna,
    kaynakla bayt bayt aynı olmaktan çıkar.
    """
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "clone", "--quiet", "--depth", "1",
         "--single-branch", "--branch", dal, kaynak, str(hedef)],
        check=True,
    )
    cikti = subprocess.run(
        ["git", "-C", str(hedef), "log", "-1", "--format=%H%n%cI"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.split()
    return (cikti[0] if cikti else None, cikti[1] if len(cikti) > 1 else None)


def senkronla(hedef: Path, kaynak: str = VARSAYILAN_KAYNAK, dal: str = VARSAYILAN_DAL,
              klonlayici: Optional[Klonlayici] = None,
              cekilme_tarihi: Optional[str] = None) -> Dict[str, object]:
    """Klonla → doğrula → eşitle → `KAYNAK.json` yaz; özet sözlüğü döndür.

    ``klonlayici`` testler için değiştirilebilir (git/ağ olmadan sahte ağaç);
    verilmezse `git_klonla` çağrı anında çözülür.
    """
    hedef = Path(hedef)
    with tempfile.TemporaryDirectory(prefix="arsiv-senkron-") as gecici:
        klon = Path(gecici) / "klon"
        commit, commit_tarihi = (klonlayici or git_klonla)(kaynak, dal, klon)
        paket.arsivi_dogrula(klon)       # geçersizse hedefe dokunmadan dur
        plan = ayna_plani(klon, hedef)
        hedef.mkdir(parents=True, exist_ok=True)
        plani_uygula(plan, klon, hedef)
    manifest = paket.manifest_uret(
        hedef, kaynak=depo_adresi(kaynak), dal=dal, commit=commit,
        commit_tarihi=commit_tarihi, cekilme_tarihi=cekilme_tarihi)
    paket.manifest_yaz(hedef, manifest)
    return {"eklenen": len(plan.eklenen), "degisen": len(plan.degisen),
            "silinen": len(plan.silinen), "manifest": manifest}


def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(
        prog="python -m turkanime_api.common.arsiv_senkron",
        description="arsiv/ aynasını AnimeDepo deposunun son commit'iyle eşitle.")
    ayristirici.add_argument("--hedef", required=True, type=Path,
                             help="eşitlenecek klasör (depoda: arsiv)")
    ayristirici.add_argument("--kaynak", default=VARSAYILAN_KAYNAK,
                             help=f"klonlanacak depo (varsayılan: {VARSAYILAN_KAYNAK})")
    ayristirici.add_argument("--dal", default=VARSAYILAN_DAL,
                             help=f"dal (varsayılan: {VARSAYILAN_DAL})")
    secenek = ayristirici.parse_args(argv)
    try:
        ozet = senkronla(secenek.hedef, secenek.kaynak, secenek.dal)
    except (subprocess.CalledProcessError, OSError, paket.ArsivHatasi) as hata:
        print(f"hata: {hata}", file=sys.stderr)
        return 1
    manifest = ozet["manifest"]
    print(f"eklenen : {ozet['eklenen']}")
    print(f"degisen : {ozet['degisen']}")
    print(f"silinen : {ozet['silinen']}")
    print(f"commit  : {manifest['commit']} ({manifest['commit_tarihi']})")  # type: ignore[index]
    print(f"anime   : {manifest['anime_sayisi']}, dosya: {manifest['dosya_sayisi']}")  # type: ignore[index]
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
