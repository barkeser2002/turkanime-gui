"""Çevrimdışı arşiv: konum çözümü, aynalar, disk önbelleği, tam arşiv indirme,
bakımcı eşitleme aracı ve akış (stream) kalitesi.

**Hiçbiri ağa çıkmaz ve 500 MB'lık `arsiv/`'i okumaz.** Arşivler `tmp_path`
altında birkaç dosyalık sahte ağaçlar; HTTP `animedepo._session` sahtesiyle
veriliyor (curl_cffi ağ mandalını atlattığı için sahteleme şart — bkz.
`conftest._arsiv_yalitimi`). Tek istisna en sondaki duman testi: gerçek
`arsiv/dizin.json` ile `arsiv/KAYNAK.json`'ın birbirini tuttuğunu ucuzca denetler.
"""
from __future__ import annotations

import errno
import io
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from turkanime_api.common import arsiv_paketi as paket
from turkanime_api.common import arsiv_senkron as senkron
from turkanime_api.common.oynatici_onceligi import (
    DESTEKLENEN_OYNATICILAR, oncelik_anahtari,
)
from turkanime_api.sources import animedepo
from turkanime_api.sources.adapter import AdapterAnime, AdapterBolum

DEPO = Path(__file__).resolve().parent.parent

# conftest bu iki fonksiyonu test başına yalıtılmış yollarla değiştiriyor;
# varsayılan davranışlarını (veri köküne göre yol) sınamak için asılları
# toplama anında, hiçbir fixture çalışmadan önce saklanıyor.
_ASIL_INDIRILEN = animedepo.indirilen_arsiv_dizini
_ASIL_ONBELLEK = animedepo.onbellek_dizini


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────
def arsiv_yaz(kok: Path, animeler: Optional[Dict[str, Dict[str, Any]]] = None,
              etiket: str = "") -> Path:
    """AnimeDepo şemasında küçük bir arşiv.

    ``animeler``: {slug: {"title": str, "bolumler": {bolum_slug: [kayit, ...]}}}
    ``etiket``: başlıklara eklenir; hangi arşivin okunduğunu ayırt etmek için.
    """
    if animeler is None:
        animeler = {"naruto": {"title": "Naruto", "bolumler": {
            "naruto-1-bolum": [{"player": "SIBNET", "fansub": "TAÇE",
                                "url": "https://video.sibnet.ru/shell.php?videoid=1"}],
        }}}
    kok.mkdir(parents=True, exist_ok=True)
    index: Dict[str, Dict[str, Any]] = {}
    for slug, anime in animeler.items():
        baslik = anime["title"] + etiket
        index.setdefault(slug[0].upper(), {})[slug] = {"title": baslik, "status": 1}
        klasor = kok / "animeler" / slug
        klasor.mkdir(parents=True, exist_ok=True)
        (klasor / "info.json").write_text(json.dumps({"Kategori": "TV"}), "utf-8")
        bolumler = anime.get("bolumler", {})
        (klasor / "bolumler.json").write_text(json.dumps(
            [[b, f"{baslik} {i}. Bölüm"] for i, b in enumerate(bolumler, 1)]), "utf-8")
        for bolum, kayitlar in bolumler.items():
            (klasor / f"{bolum}.json").write_text(json.dumps(kayitlar), "utf-8")
    (kok / "dizin.json").write_text(
        json.dumps({"last_update": 1700000000, "index": index}), "utf-8")
    return kok


class SahteYanit:
    """Hem düz JSON hem akış (tar.gz) yanıtı."""

    def __init__(self, durum: int = 200, veri: Any = None, govde: bytes = b"",
                 etag: Optional[str] = None, uzunluk: bool = True, parca: int = 4096):
        self.status_code = durum
        self._veri = veri
        self._govde = govde
        self._parca = parca
        self.headers: Dict[str, str] = {}
        if etag:
            self.headers["ETag"] = etag
        if govde and uzunluk:
            self.headers["Content-Length"] = str(len(govde))
        self.kapandi = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._veri, Exception):
            raise self._veri
        return self._veri

    def iter_content(self, chunk_size=None):
        adim = chunk_size or self._parca
        for i in range(0, len(self._govde), min(adim, self._parca)):
            yield self._govde[i:i + min(adim, self._parca)]

    def close(self):
        self.kapandi = True


class SahteOturum:
    """URL → yanıt (ya da istisna / çağrılabilir) eşlemesi; istekleri kaydeder."""

    def __init__(self, yollar: Dict[str, Any], kayit: List[Tuple[str, dict]]):
        self.yollar, self.kayit = yollar, kayit

    def get(self, url, timeout=None, headers=None, stream=False):  # pylint: disable=unused-argument
        self.kayit.append((url, dict(headers or {})))
        cevap = self.yollar.get(url)
        if callable(cevap):
            cevap = cevap(url, dict(headers or {}))
        if isinstance(cevap, BaseException):
            raise cevap
        if cevap is None:
            return SahteYanit(404)
        return cevap


@pytest.fixture
def http(monkeypatch):
    """``http(yollar)`` → istek kaydı. `_session` her çağrıda aynı sahteyi verir."""
    kayit: List[Tuple[str, dict]] = []

    def kur(yollar: Dict[str, Any]) -> List[Tuple[str, dict]]:
        monkeypatch.setattr(animedepo, "_session", lambda: SahteOturum(yollar, kayit))
        return kayit

    return kur


@pytest.fixture
def ag_yasak(monkeypatch):
    """HTTP oturumu açma girişimlerini kaydeder; test sonunda boş olmalı.

    Yalnızca istisna fırlatmak yetmez: okuma zinciri ayna hatalarını bilerek
    yutup sıradakine/önbelleğe geçiyor, yani fırlatılan hata testi düşürmez.
    Kayıt teardown'da denetleniyor ki fixture'ı kullanan her test korunsun.
    """
    denemeler: List[str] = []

    def _yasak():
        denemeler.append("oturum")
        raise AssertionError("yerel arşiv varken HTTP oturumu açıldı")

    monkeypatch.setattr(animedepo, "_session", _yasak)
    yield denemeler
    assert denemeler == [], "yerel arşiv varken HTTP oturumu açıldı"


@pytest.fixture
def veri_koku(tmp_path, monkeypatch):
    """Çalışma dizinini `.git`'li geçici bir klasöre al → veri kökü orası."""
    kok = tmp_path / "veri"
    (kok / ".git").mkdir(parents=True)
    monkeypatch.chdir(kok)
    return kok


def ayar_yaz(kok: Path, **ayarlar) -> None:
    (kok / "ayarlar.json").write_text(json.dumps(ayarlar), encoding="utf-8")


GITLAB = animedepo.BASE_URL
GITHUB = animedepo.GITHUB_AYNA_URL


# ─────────────────────────────────────────────────────────────────────────────
# 1) Konum çözümü
# ─────────────────────────────────────────────────────────────────────────────
def test_konum_sirasi_her_kademe(tmp_path, veri_koku, monkeypatch):
    """ortam → ayar → indirilen → depo → uzak; üstteki kalkınca alttaki gelir."""
    ortam = arsiv_yaz(tmp_path / "ortam")
    ayar = arsiv_yaz(tmp_path / "ayar")
    indirilen = arsiv_yaz(tmp_path / "indirilen")
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setenv(animedepo.DIZIN_ORTAM_ANAHTARI, str(ortam))
    ayar_yaz(veri_koku, **{animedepo.DIZIN_AYAR_ANAHTARI: str(ayar)})
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: indirilen)
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)

    beklenen = [("ortam", ortam), ("ayar", ayar), ("indirilen", indirilen),
                ("depo", depo), ("uzak", None)]
    for kaynak, dizin_yolu in beklenen:
        animedepo.sifirla()
        konum = animedepo.arsiv_konumu()
        assert (konum.kaynak, konum.dizin) == (kaynak, dizin_yolu)
        assert konum.yerel is (dizin_yolu is not None)
        # Bir sonraki kademeyi açığa çıkar
        if kaynak == "ortam":
            monkeypatch.delenv(animedepo.DIZIN_ORTAM_ANAHTARI)
        elif kaynak == "ayar":
            ayar_yaz(veri_koku)
        elif kaynak == "indirilen":
            shutil.rmtree(indirilen)
        elif kaynak == "depo":
            shutil.rmtree(depo)


def test_gecersiz_yerel_klasorler_atlaniyor(tmp_path, veri_koku, monkeypatch):
    """dizin.json yok / bozuk / şema dışı → o kademe sayılmaz, sıradakine geçilir."""
    bos = tmp_path / "bos"
    bos.mkdir()
    bozuk = tmp_path / "bozuk"
    bozuk.mkdir()
    (bozuk / "dizin.json").write_text("{yarim", encoding="utf-8")
    semasiz = tmp_path / "semasiz"
    semasiz.mkdir()
    (semasiz / "dizin.json").write_text(json.dumps({"baska": 1}), encoding="utf-8")
    depo = arsiv_yaz(tmp_path / "depo")

    monkeypatch.setenv(animedepo.DIZIN_ORTAM_ANAHTARI, str(bos))
    ayar_yaz(veri_koku, **{animedepo.DIZIN_AYAR_ANAHTARI: str(bozuk)})
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: semasiz)
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    assert animedepo.arsiv_konumu() == animedepo.ArsivKonumu("depo", depo)


def test_konum_onbellekte_sifirla_ile_yenileniyor(tmp_path, monkeypatch):
    """Konum süreç boyunca bir kez çözülür; tam arşiv inince `sifirla` şart."""
    assert animedepo.arsiv_konumu().kaynak == "uzak"
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    assert animedepo.arsiv_konumu().kaynak == "uzak", "çözüm önbelleklenmeliydi"
    animedepo.sifirla()
    assert animedepo.arsiv_konumu().kaynak == "depo"


def test_varsayilan_yollar_veri_kokunde(veri_koku, monkeypatch):
    """İndirilen arşiv ve önbellek veri kökünde; depoda asla `arsiv/` değil."""
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", _ASIL_INDIRILEN)
    monkeypatch.setattr(animedepo, "onbellek_dizini", _ASIL_ONBELLEK)
    assert animedepo.veri_koku() == veri_koku
    assert animedepo.indirilen_arsiv_dizini() == veri_koku / "cevrimdisi_arsiv"
    assert animedepo.onbellek_dizini() == veri_koku / "arsiv_onbellek"
    assert animedepo.indirilen_arsiv_dizini().name != "arsiv"


def test_git_disinda_veri_koku_ev_klasoru(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "ev"))
    assert animedepo.veri_koku() == tmp_path / "ev" / "Turkanime"


def test_depo_aynasi_yolu_depodaki_arsiv_klasoru():
    """Kademe 4'ün yolu gerçekten bu deponun `arsiv/` klasörü (import anı değeri)."""
    import importlib.util
    spec = importlib.util.find_spec("turkanime_api.sources.animedepo")
    assert Path(spec.origin).resolve().parents[2] / "arsiv" == DEPO / "arsiv"


# ─────────────────────────────────────────────────────────────────────────────
# 2) Yerel okuma ağa çıkmaz
# ─────────────────────────────────────────────────────────────────────────────
def test_yerel_arsivde_hic_http_yok(tmp_path, monkeypatch, ag_yasak):
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()

    assert animedepo.search_animedepo("naruto") == [("naruto", "Naruto")]
    bolumler = animedepo.get_anime_episodes("naruto")
    assert bolumler == [("naruto/naruto-1-bolum", "Naruto 1. Bölüm")]
    akislar = animedepo.get_episode_streams(bolumler[0][0])
    assert [a["url"] for a in akislar] == ["https://video.sibnet.ru/shell.php?videoid=1"]
    assert animedepo.fetch_json("/dizin.json")["index"], "baştaki / eskisi gibi tolere"
    assert ag_yasak == [], "yerel arşiv varken oturum açıldı"


def test_yerel_dizin_tazele_degismeyeni_yeniden_ayristirmiyor(tmp_path, monkeypatch, ag_yasak):
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()

    ilk = animedepo.dizin()
    assert animedepo.dizin(tazele=True) is ilk, "dosya değişmedi, aynı nesne dönmeli"

    arsiv_yaz(depo, {"bleach": {"title": "Bleach", "bolumler": {}}})
    yeni = animedepo.dizin(tazele=True)
    assert "bleach" in yeni["index"]["B"], "değişen yerel dizin yeniden okunmalı"


def test_yerelde_eksik_dosya_bos_liste(tmp_path, monkeypatch, ag_yasak):
    """Dizinde olup klasörü olmayan anime (gerçek arşivde de var) çökertmemeli."""
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    assert animedepo.get_anime_episodes("olmayan-anime") == []
    assert animedepo.get_episode_streams("olmayan-anime/x") == []


# ─────────────────────────────────────────────────────────────────────────────
# 3) Uzak aynalar ve disk önbelleği
# ─────────────────────────────────────────────────────────────────────────────
def test_aynalar_sirayla_deneniyor(http, monkeypatch):
    """özel → GitLab → GitHub: hata, 404 ve bozuk JSON bir sonrakine geçirir."""
    monkeypatch.setenv(animedepo.ORTAM_ANAHTARI, "https://ozel.test/arsiv/")
    animedepo.sifirla()
    assert animedepo.uzak_aynalar() == ["https://ozel.test/arsiv", GITLAB, GITHUB]

    kayit = http({
        "https://ozel.test/arsiv/dizin.json": SahteYanit(200, veri=ValueError("HTML")),
        f"{GITLAB}/dizin.json": ConnectionError("gitlab düştü"),
        f"{GITHUB}/dizin.json": SahteYanit(200, veri={"index": {"N": {"naruto": {}}}}),
    })
    veri = animedepo.fetch_json("dizin.json")
    assert veri["index"]["N"] == {"naruto": {}}
    assert [u for u, _ in kayit] == ["https://ozel.test/arsiv/dizin.json",
                                    f"{GITLAB}/dizin.json", f"{GITHUB}/dizin.json"]


def test_ozel_adres_yoksa_gitlab_sonra_github(http):
    kayit = http({f"{GITHUB}/animeler/a/bolumler.json": SahteYanit(200, veri=[["a-1", "A 1"]])})
    assert animedepo.get_anime_episodes("a") == [("a/a-1", "A 1")]
    assert [u for u, _ in kayit] == [f"{GITLAB}/animeler/a/bolumler.json",
                                    f"{GITHUB}/animeler/a/bolumler.json"]


def test_basarili_yanit_onbellege_yaziliyor_cevrimdisi_oradan(http, tmp_path, monkeypatch):
    onbellek = tmp_path / "onbellek"
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: onbellek)
    http({f"{GITLAB}/animeler/a/bolumler.json": SahteYanit(200, veri=[["a-1", "A 1"]])})
    assert animedepo.get_anime_episodes("a") == [("a/a-1", "A 1")]
    dosya = onbellek / "animeler" / "a" / "bolumler.json"
    assert json.loads(dosya.read_text("utf-8")) == [["a-1", "A 1"]]
    assert not [p for p in dosya.parent.iterdir() if p.name.endswith(".tmp")], \
        "atomik yazım geçici dosya bıraktı"

    # Bütün aynalar düştü → önbellek
    kayit = http({})
    once = len(kayit)
    animedepo.sifirla()
    assert animedepo.get_anime_episodes("a") == [("a/a-1", "A 1")]
    assert len(kayit) - once == 2, "önbelleğe düşmeden önce iki ayna da denenmeli"


def test_ayna_da_onbellek_de_yoksa_hata_yutulur(http):
    http({})
    assert animedepo.get_anime_episodes("hic-yok") == []
    with pytest.raises(RuntimeError):
        animedepo.fetch_json("animeler/hic-yok/bolumler.json")


def test_dizin_cevrimdisi_onbellekten_aciliyor(http, tmp_path, monkeypatch):
    """Uygulama çevrimdışı açıldığında arama önceki oturumun dizinini kullanır."""
    onbellek = tmp_path / "onbellek"
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: onbellek)
    (onbellek).mkdir()
    (onbellek / "dizin.json").write_text(
        json.dumps({"index": {"B": {"bleach": {"title": "Bleach"}}}}), "utf-8")
    http({})
    assert animedepo.search_animedepo("bleach") == [("bleach", "Bleach")]


@pytest.mark.parametrize("yol", ["../disari.json", "animeler/../../x.json",
                                 "C:/Windows/x.json", "animeler\\..\\x.json",
                                 "a/b\x00.json", ""])
def test_guvensiz_yol_reddediliyor_ag_ve_disk_yok(yol, http, tmp_path, monkeypatch):
    onbellek = tmp_path / "ic" / "onbellek"
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: onbellek)
    kayit = http({})
    with pytest.raises(ValueError):
        animedepo.fetch_json(yol)
    assert kayit == [], "güvensiz yol için istek atıldı"
    assert not (tmp_path / "disari.json").exists()


def test_onbellek_yazimi_klasor_disina_cikamiyor(tmp_path, monkeypatch):
    onbellek = tmp_path / "ic" / "onbellek"
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: onbellek)
    animedepo._onbellege_yaz("../../kacak.json", {"x": 1})       # sessizce reddedilir
    animedepo._onbellege_yaz("/mutlak.json", {"x": 1})
    assert not (tmp_path / "kacak.json").exists()
    assert not list(tmp_path.rglob("kacak.json"))
    assert not list(tmp_path.rglob("mutlak.json"))


@pytest.mark.parametrize("yol,beklenen", [
    ("dizin.json", ("dizin.json",)),
    ("animeler//a/./b.json", ("animeler", "a", "b.json")),
    ("animeler/x/One Piece Movie 6: Adası.json",
     ("animeler", "x", "One Piece Movie 6: Adası.json")),
])
def test_goreli_parcalar_mesru_yollar(yol, beklenen):
    """Gerçek arşivde `:` içeren bir bölüm dosyası var; reddedilmemeli."""
    assert paket.goreli_parcalar(yol) == beklenen


def test_etag_veren_aynaya_kosullu_istek(http):
    """GitLab düşük → ETag GitHub'dan; koşullu istek GitHub'a gitmeli."""
    etag = 'W/"gh1"'

    def github(_url, basliklar):
        if basliklar.get("If-None-Match") == etag:
            return SahteYanit(304, etag=etag)
        return SahteYanit(200, veri={"index": {"N": {"naruto": {}}}}, etag=etag)

    kayit = http({f"{GITLAB}/dizin.json": SahteYanit(503), f"{GITHUB}/dizin.json": github})
    ilk = animedepo.dizin()
    assert ilk["index"]
    ikinci = animedepo.dizin(tazele=True)
    assert ikinci is ilk
    assert kayit[-1] == (f"{GITHUB}/dizin.json", {"If-None-Match": etag})


def test_etag_aynasi_duserse_digerleri_deneniyor(http):
    etag = 'W/"gl1"'
    durum = {"gitlab_ayakta": True}

    def gitlab(_url, _b):
        if durum["gitlab_ayakta"]:
            return SahteYanit(200, veri={"index": {"A": {}}}, etag=etag)
        return ConnectionError("gitlab düştü")

    kayit = http({f"{GITLAB}/dizin.json": gitlab,
                  f"{GITHUB}/dizin.json": SahteYanit(200, veri={"index": {"B": {}}})})
    assert "A" in animedepo.dizin()["index"]
    durum["gitlab_ayakta"] = False
    assert "B" in animedepo.dizin(tazele=True)["index"]
    assert [u for u, _ in kayit][-1] == f"{GITHUB}/dizin.json"
    assert [u for u, _ in kayit].count(f"{GITLAB}/dizin.json") == 2, \
        "düşen ETag aynası tekrar denenmemeli"


# ─────────────────────────────────────────────────────────────────────────────
# 4) Tam arşiv indirme: tar güvenliği, iptal, takas
# ─────────────────────────────────────────────────────────────────────────────
# Tar üyesi: (başlık, içerik). TarInfo `__slots__` kullandığı için içerik
# ayrı taşınıyor.
Uye = Tuple[tarfile.TarInfo, Optional[bytes]]


def _dosya(ad: str, veri: bytes) -> Uye:
    ti = tarfile.TarInfo(ad)
    ti.size = len(veri)
    ti.mtime = 1789378418
    return ti, veri


def _tur(ad: str, tur: bytes, bag: str = "") -> Uye:
    ti = tarfile.TarInfo(ad)
    ti.type = tur
    ti.linkname = bag
    ti.mtime = 1789378418
    return ti, None


def tar_gz(uyeler: List[Uye], yorum: Optional[str] = None) -> bytes:
    """Bellekte tar.gz; ``yorum`` git arşivlerindeki pax global başlığı gibi."""
    tampon = io.BytesIO()
    ek = {"pax_headers": {"comment": yorum}} if yorum else {}
    with tarfile.open(fileobj=tampon, mode="w:gz", format=tarfile.PAX_FORMAT, **ek) as tar:
        for ti, veri in uyeler:
            tar.addfile(ti, io.BytesIO(veri) if veri is not None else None)
    return tampon.getvalue()


def arsiv_uyeleri(ust: str, etiket: str = "yeni") -> List[Uye]:
    dizin_json = json.dumps({"last_update": 1, "index": {
        "N": {"naruto": {"title": f"Naruto {etiket}"}}}}).encode()
    return [
        _tur(f"{ust}", tarfile.DIRTYPE),
        _dosya(f"{ust}/dizin.json", dizin_json),
        _tur(f"{ust}/animeler", tarfile.DIRTYPE),
        _dosya(f"{ust}/animeler/naruto/bolumler.json", b'[["naruto-1", "1"]]'),
        _dosya(f"{ust}/DISCLAIMER.md", b"uyari"),
    ]


GITLAB_PAKET = animedepo.TAM_ARSIV_KAYNAKLARI[0].url
GITHUB_PAKET = animedepo.TAM_ARSIV_KAYNAKLARI[1].url
SHA = "790d9e827e019b89416c688562947add707b368a"


def _artiklar(klasor: Path) -> List[str]:
    return sorted(p.name for p in klasor.iterdir() if "-indirme-" in p.name or "-eski-" in p.name)


def test_gitlab_paketi_kuruluyor_ve_konum_yenileniyor(http, tmp_path, monkeypatch):
    hedef = tmp_path / "veri" / "cevrimdisi_arsiv"
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: hedef)
    assert animedepo.arsiv_konumu().kaynak == "uzak"      # önbelleğe girsin

    govde = tar_gz(arsiv_uyeleri("animedepo-master-" + SHA[:8]), yorum=SHA)
    yanit = SahteYanit(200, govde=govde)
    kayit = http({GITLAB_PAKET: yanit})
    ilerleme: List[Tuple[int, Optional[int]]] = []

    yol = animedepo.tam_arsiv_indir(ilerleme=lambda n, t: ilerleme.append((n, t)))

    assert yol == hedef
    assert [u for u, _ in kayit] == [GITLAB_PAKET], "GitLab başarılıyken yedeğe gidilmemeli"
    assert yanit.kapandi, "akış yanıtı kapatılmalı"
    assert json.loads((hedef / "dizin.json").read_text("utf-8"))["index"]["N"]
    assert (hedef / "animeler" / "naruto" / "bolumler.json").is_file()
    manifest = json.loads((hedef / "KAYNAK.json").read_text("utf-8"))
    assert tuple(manifest) == paket.MANIFEST_ANAHTARLARI
    assert manifest["commit"] == SHA
    assert manifest["kaynak"] == "https://gitlab.com/AnimeDepo/animedepo"
    assert manifest["anime_sayisi"] == 1
    assert manifest["commit_tarihi"].startswith("2026-09-")
    assert ilerleme[0] == (0, len(govde)) and ilerleme[-1] == (len(govde), len(govde))
    assert _artiklar(hedef.parent) == []
    assert animedepo.arsiv_konumu() == animedepo.ArsivKonumu("indirilen", hedef), \
        "indirmeden sonra modül cache'leri sıfırlanmalı"


def test_gitlab_duserse_github_paketinden_yalnizca_arsiv_aciliyor(http, tmp_path):
    hedef = tmp_path / "cevrimdisi_arsiv"
    ust = "turkanime-gui-main"
    uyeler = [
        _tur(ust, tarfile.DIRTYPE),
        _dosya(f"{ust}/README.md", b"depo readme"),
        _dosya(f"{ust}/turkanime_api/x.py", b"print()"),
        # arsiv/ DIŞINDAKİ meşru bir bağ paketi reddettirmemeli (hiç yazılmıyor)
        _tur(f"{ust}/docs/bag", tarfile.SYMTYPE, "../README.md"),
        *[_dosya(ti.name.replace(ust, f"{ust}/arsiv", 1), veri or b"")
          for ti, veri in arsiv_uyeleri(ust) if ti.isfile()],
        _dosya(f"{ust}/arsiv/KAYNAK.json", json.dumps({"commit": "depodaki"}).encode()),
    ]
    kayit = http({GITLAB_PAKET: SahteYanit(502),
                  GITHUB_PAKET: SahteYanit(200, govde=tar_gz(uyeler), uzunluk=False)})
    ilerleme: List[Tuple[int, Optional[int]]] = []

    animedepo.tam_arsiv_indir(ilerleme=lambda n, t: ilerleme.append((n, t)), hedef=hedef)

    assert [u for u, _ in kayit] == [GITLAB_PAKET, GITHUB_PAKET]
    assert sorted(p.name for p in hedef.iterdir()) == \
        ["DISCLAIMER.md", "KAYNAK.json", "animeler", "dizin.json"]
    assert not (hedef / "turkanime_api").exists() and not (hedef / "README.md").exists()
    assert json.loads((hedef / "KAYNAK.json").read_text("utf-8")) == {"commit": "depodaki"}, \
        "paketin kendi KAYNAK.json'ı korunmalı"
    assert all(t is None for _, t in ilerleme), "Content-Length yoksa toplam None"


@pytest.mark.parametrize("kotu", [
    _dosya("animedepo-master/../../kacak.json", b"x"),
    _dosya("/etc/kacak.json", b"x"),
    _dosya("animedepo-master/animeler/..\\..\\kacak.json", b"x"),
    _tur("animedepo-master/animeler/bag", tarfile.SYMTYPE, "/etc/passwd"),
    _tur("animedepo-master/animeler/sert", tarfile.LNKTYPE, "animedepo-master/dizin.json"),
    _tur("animedepo-master/animeler/fifo", tarfile.FIFOTYPE),
    _tur("animedepo-master/animeler/aygit", tarfile.CHRTYPE),
    _dosya("baska-ust/dizin.json", b"{}"),
], ids=["nokta-nokta", "mutlak", "ters-bolu", "sembolik", "sert-bag", "fifo",
        "aygit", "yanlis-ust"])
def test_guvensiz_tar_uyesi_paketi_reddettiriyor(kotu, tmp_path):
    paket_yolu = tmp_path / "p.tar.gz"
    paket_yolu.write_bytes(tar_gz(arsiv_uyeleri("animedepo-master") + [kotu]))
    acilan = tmp_path / "kutu" / "acilan"
    with pytest.raises(paket.GuvensizUye):
        paket.guvenli_ac(paket_yolu, acilan, ust_desen="animedepo-master*")
    assert not list(tmp_path.rglob("kacak.json"))
    assert not (acilan / "animeler" / "bag").exists()


def test_guvensiz_paket_eski_arsive_dokunmuyor(http, tmp_path):
    hedef = arsiv_yaz(tmp_path / "cevrimdisi_arsiv", etiket=" ESKI")
    kotu = tar_gz(arsiv_uyeleri("animedepo-master")
                  + [_tur("animedepo-master/animeler/bag", tarfile.SYMTYPE, "/etc")])
    http({GITLAB_PAKET: SahteYanit(200, govde=kotu)})
    with pytest.raises(paket.ArsivHatasi):
        animedepo.tam_arsiv_indir(hedef=hedef)
    assert "ESKI" in (hedef / "dizin.json").read_text("utf-8")
    assert _artiklar(tmp_path) == []


def test_gecersiz_dizin_eski_arsivi_koruyor(http, tmp_path):
    """Paket açıldı ama dizin.json şema dışı → eski arşiv yerinde, artık yok."""
    hedef = arsiv_yaz(tmp_path / "cevrimdisi_arsiv", etiket=" ESKI")
    uyeler = [_dosya("animedepo-master/dizin.json", b'{"index": "bozuk"}')]
    http({GITLAB_PAKET: SahteYanit(200, govde=tar_gz(uyeler)), GITHUB_PAKET: SahteYanit(404)})
    with pytest.raises(paket.ArsivHatasi, match="GitLab.*GitHub"):
        animedepo.tam_arsiv_indir(hedef=hedef)
    assert "ESKI" in (hedef / "dizin.json").read_text("utf-8")
    assert _artiklar(tmp_path) == []


def test_kesik_indirme_reddediliyor(http, tmp_path):
    """Content-Length'ten az bayt geldiyse yarım paket açılmaya çalışılmaz."""
    govde = tar_gz(arsiv_uyeleri("animedepo-master"))
    yanit = SahteYanit(200, govde=govde)
    yanit.headers["Content-Length"] = str(len(govde) + 100)
    http({GITLAB_PAKET: yanit})
    hedef = tmp_path / "cevrimdisi_arsiv"
    with pytest.raises(paket.ArsivHatasi, match="yarım"):
        animedepo.tam_arsiv_indir(hedef=hedef)
    assert not hedef.exists() and _artiklar(tmp_path) == []


def test_iptal_gecicileri_temizliyor_yedege_gecmiyor(http, tmp_path):
    hedef = arsiv_yaz(tmp_path / "cevrimdisi_arsiv", etiket=" ESKI")
    govde = tar_gz(arsiv_uyeleri("animedepo-master"))
    kayit = http({GITLAB_PAKET: SahteYanit(200, govde=govde, parca=64),
                  GITHUB_PAKET: SahteYanit(200, govde=govde)})
    iptal = threading.Event()

    def ilerleme(indirilen, _toplam):
        if indirilen >= 128:
            iptal.set()                  # indirmenin ortasında vazgeç

    with pytest.raises(paket.IptalEdildi):
        animedepo.tam_arsiv_indir(ilerleme=ilerleme, iptal=iptal, hedef=hedef)
    assert [u for u, _ in kayit] == [GITLAB_PAKET], "iptal sonrası yedek denenmemeli"
    assert "ESKI" in (hedef / "dizin.json").read_text("utf-8")
    assert _artiklar(tmp_path) == []


def test_bastan_iptal_hic_istek_atmiyor(http, tmp_path):
    kayit = http({})
    iptal = threading.Event()
    iptal.set()
    with pytest.raises(paket.IptalEdildi):
        animedepo.tam_arsiv_indir(iptal=iptal, hedef=tmp_path / "h")
    assert kayit == []


def test_takas_yarida_kalirsa_eski_arsiv_geri_geliyor(tmp_path, monkeypatch):
    hedef = arsiv_yaz(tmp_path / "hedef", etiket=" ESKI")
    yeni = arsiv_yaz(tmp_path / "yeni", etiket=" YENI")
    asil_replace = os.replace
    cagri = {"n": 0}

    def bozuk_replace(a, b):
        cagri["n"] += 1
        if cagri["n"] == 2:              # eski kenara alındı, yeni yerine konurken
            raise OSError("disk dolu")
        return asil_replace(a, b)

    monkeypatch.setattr(paket.os, "replace", bozuk_replace)
    with pytest.raises(OSError, match="disk dolu"):
        paket.yerine_koy(yeni, hedef)
    assert "ESKI" in (hedef / "dizin.json").read_text("utf-8")
    assert (yeni / "dizin.json").is_file()
    assert _artiklar(tmp_path) == []


def test_takas_basarida_eskiyi_siliyor(tmp_path):
    hedef = arsiv_yaz(tmp_path / "hedef", etiket=" ESKI")
    yeni = arsiv_yaz(tmp_path / "yeni", etiket=" YENI")
    paket.yerine_koy(yeni, hedef)
    assert "YENI" in (hedef / "dizin.json").read_text("utf-8")
    assert not yeni.exists()
    assert _artiklar(tmp_path) == []


# ─────────────────────────────────────────────────────────────────────────────
# 4b) Ayarlar sayfasının kullandığı uçlar: aşama bildirimi, durum özeti, silme
# ─────────────────────────────────────────────────────────────────────────────
def test_asamalar_kaynak_adiyla_sirayla_bildiriliyor(http, tmp_path):
    """Açma aşamasında bayt ilerlemesi akmıyor; arayüz aşamayı bilmezse çubuk
    %100'de donmuş görünür. Düşen kaynak da "bağlanılıyor" olarak görünmeli."""
    ust = "turkanime-gui-main"
    uyeler = [_tur(ust, tarfile.DIRTYPE),
              *[_dosya(ti.name.replace(ust, f"{ust}/arsiv", 1), veri or b"")
                for ti, veri in arsiv_uyeleri(ust) if ti.isfile()]]
    http({GITLAB_PAKET: SahteYanit(502),
          GITHUB_PAKET: SahteYanit(200, govde=tar_gz(uyeler))})
    asamalar: List[Tuple[str, str]] = []

    animedepo.tam_arsiv_indir(hedef=tmp_path / "cevrimdisi_arsiv",
                              asama=lambda ad, kaynak: asamalar.append((ad, kaynak)))

    assert asamalar == [
        (paket.ASAMA_BAGLANMA, "GitLab"),
        (paket.ASAMA_BAGLANMA, "GitHub"),
        (paket.ASAMA_INDIRME, "GitHub"),
        (paket.ASAMA_ACMA, "GitHub"),
        (paket.ASAMA_YERLESTIRME, "GitHub"),
    ]


def test_asama_verilmezse_eski_cagri_bicimi_calisiyor(http, tmp_path):
    """`asama` isteğe bağlı: CLI ve bakımcı yolları onu hiç vermiyor."""
    http({GITLAB_PAKET: SahteYanit(200, govde=tar_gz(arsiv_uyeleri("animedepo-master")))})
    yol = animedepo.tam_arsiv_indir(hedef=tmp_path / "cevrimdisi_arsiv")
    assert (yol / "dizin.json").is_file()


def test_durum_yerel_arsivin_sayisini_ve_tarihini_veriyor(tmp_path, monkeypatch, ag_yasak):
    hedef = arsiv_yaz(tmp_path / "cevrimdisi_arsiv", animeler={
        "naruto": {"title": "Naruto"}, "bleach": {"title": "Bleach"}})
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: hedef)

    durum = animedepo.arsiv_durumu()

    assert durum.kaynak == "indirilen" and durum.konum.dizin == hedef
    assert durum.adres == str(hedef)
    assert (durum.anime_sayisi, durum.son_guncelleme) == (2, 1700000000)
    assert durum.indirilen_var and not durum.onbellekten


def test_durum_uzakta_aga_cikmiyor_bilinmeyeni_none_birakiyor(ag_yasak):
    """Ayarlar sayfasını açmak kullanıcıyı internete çıkarmamalı."""
    durum = animedepo.arsiv_durumu()

    assert durum.kaynak == "uzak" and durum.konum.dizin is None
    assert durum.adres == animedepo.uzak_aynalar()[0]
    assert durum.anime_sayisi is None and durum.son_guncelleme is None
    assert not durum.indirilen_var


def test_durum_uzakta_disk_onbellegindeki_dizini_kullaniyor(ag_yasak):
    kopya = animedepo.onbellek_dizini() / "dizin.json"
    kopya.parent.mkdir(parents=True)
    kopya.write_text(json.dumps({"last_update": 5, "index": {"N": {"naruto": {}}}}), "utf-8")

    durum = animedepo.arsiv_durumu()

    assert (durum.anime_sayisi, durum.son_guncelleme) == (1, 5)
    assert durum.onbellekten, "sayının önbellekteki kopyadan geldiği söylenmeli"


def test_durum_gecersiz_klasorlerin_ham_degerini_tasiyor(tmp_path, veri_koku, monkeypatch):
    """Geçersiz klasör konum çözümünde sessizce atlanıyor; arayüz bunu
    kullanıcıya söyleyebilsin diye ham değerler özette."""
    ayar_yaz(veri_koku, animedepo_dizin=str(tmp_path / "bos-klasor"))
    monkeypatch.setenv(animedepo.DIZIN_ORTAM_ANAHTARI, str(tmp_path / "olmayan"))

    durum = animedepo.arsiv_durumu()

    assert durum.kaynak == "uzak"
    assert durum.ayar_dizini == str(tmp_path / "bos-klasor")
    assert durum.ortam_dizini == str(tmp_path / "olmayan")


def test_indirileni_sil_yalnizca_indirileni_siliyor(tmp_path, monkeypatch):
    """Depoda veri kökü depo KÖKÜ: `arsiv/` (commit'lenmiş ayna) ile
    `cevrimdisi_arsiv/` yan yana. Silme ikincisiyle sınırlı kalmalı."""
    kok = tmp_path / "veri"
    indirilen = arsiv_yaz(kok / animedepo.CEVRIMDISI_KLASOR, etiket=" INDIRILEN")
    depo = arsiv_yaz(kok / "arsiv", etiket=" DEPO")
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: indirilen)
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    assert animedepo.arsiv_konumu().kaynak == "indirilen"     # önbelleğe girsin

    assert animedepo.indirilen_arsivi_sil() is True

    assert not indirilen.exists()
    assert "DEPO" in (depo / "dizin.json").read_text("utf-8")
    assert _artiklar(kok) == [], "kenara alınan kopya da silinmeli"
    assert animedepo.arsiv_konumu() == animedepo.ArsivKonumu("depo", depo), \
        "silmeden sonra konum önbelleği sıfırlanmalı"


def test_indirileni_sil_yoksa_false(tmp_path, monkeypatch):
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini",
                        lambda: tmp_path / animedepo.CEVRIMDISI_KLASOR)
    assert animedepo.indirilen_arsivi_sil() is False


def test_indirileni_sil_baska_adli_klasoru_reddediyor(tmp_path, monkeypatch):
    """Savunma: yol hesabı bir gün yanlışlıkla `arsiv/`'i gösterirse silinmesin."""
    ayna = arsiv_yaz(tmp_path / "arsiv")
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: ayna)
    with pytest.raises(paket.ArsivHatasi, match="indirilen arşiv klasörü değil"):
        animedepo.indirilen_arsivi_sil()
    assert (ayna / "dizin.json").is_file()


def test_indirileni_sil_depo_aynasini_reddediyor(tmp_path, monkeypatch):
    hedef = arsiv_yaz(tmp_path / animedepo.CEVRIMDISI_KLASOR)
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: hedef)
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", hedef)
    with pytest.raises(paket.ArsivHatasi, match="depodaki arşiv"):
        animedepo.indirilen_arsivi_sil()
    assert (hedef / "dizin.json").is_file()


def test_indirileni_sil_sembolik_bagda_yalnizca_bagi_kaldiriyor(tmp_path, monkeypatch):
    """Kullanıcı arşivini başka diske koyup bağ vermiş olabilir; o kopya onun."""
    asil = arsiv_yaz(tmp_path / "harici-disk" / "arsivim")
    bag = tmp_path / "veri" / animedepo.CEVRIMDISI_KLASOR
    bag.parent.mkdir(parents=True)
    try:
        os.symlink(asil, bag, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("sembolik bağ oluşturulamıyor")
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: bag)

    assert animedepo.indirilen_arsivi_sil() is True

    assert not bag.exists() and not bag.is_symlink()
    assert (asil / "dizin.json").is_file()


def test_indirileni_sil_yarim_kalirsa_hata_ama_konum_dusuyor(tmp_path, monkeypatch):
    """Kilitli dosya yüzünden silme yarım kalsa bile klasör konum çözümüne bir
    daha görünmemeli (önce kenara taşınıyor) ve hata sebebiyle söylenmeli."""
    indirilen = arsiv_yaz(tmp_path / "veri" / animedepo.CEVRIMDISI_KLASOR)
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: indirilen)
    assert animedepo.arsiv_konumu().kaynak == "indirilen"

    def kilitli_rmtree(yol, onerror=None, onexc=None, **_):
        (onexc or onerror)(os.unlink, os.path.join(yol, "dizin.json"),
                           PermissionError("kilitli"))

    # Silme `paket.agaci_sil`'de (uzun yol güvenli); `shutil` aynı modül.
    monkeypatch.setattr(paket.shutil, "rmtree", kilitli_rmtree)
    with pytest.raises(paket.ArsivHatasi, match="1 dosya silinemedi"):
        animedepo.indirilen_arsivi_sil()

    assert not indirilen.exists()
    assert animedepo.arsiv_konumu().kaynak == "uzak"


# ─────────────────────────────────────────────────────────────────────────────
# 5) Bakımcı eşitleme aracı
# ─────────────────────────────────────────────────────────────────────────────
def _yaz(kok: Path, goreli: str, metin: str) -> None:
    yol = kok / goreli
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text(metin, encoding="utf-8")


@pytest.fixture
def iki_agac(tmp_path):
    """Kaynak (upstream) ve hedef (depodaki ayna) — farkları bilinen iki ağaç."""
    kaynak = arsiv_yaz(tmp_path / "kaynak")
    _yaz(kaynak, "animeler/naruto/naruto-1-bolum.json", "[\"yeni icerik\"]")
    _yaz(kaynak, "animeler/bleach/b-1.json", "[]")
    _yaz(kaynak, "README.md", "upstream readme")
    _yaz(kaynak, ".git/config", "[core]")

    hedef = arsiv_yaz(tmp_path / "hedef")
    _yaz(hedef, "animeler/naruto/naruto-1-bolum.json", "[\"eski icerik\"]")
    _yaz(hedef, "animeler/silinen/s-1.json", "[]")
    _yaz(hedef, "README.md", "BIZIM README")
    _yaz(hedef, "KAYNAK.json", "{\"commit\": \"eski\"}")
    return kaynak, hedef


def test_ayna_plani_farklari_buluyor(iki_agac):
    kaynak, hedef = iki_agac
    plan = senkron.ayna_plani(kaynak, hedef)
    assert plan.eklenen == ["animeler/bleach/b-1.json"]
    assert plan.degisen == ["animeler/naruto/naruto-1-bolum.json"]
    assert plan.silinen == ["animeler/silinen/s-1.json"]
    hepsi = plan.eklenen + plan.degisen + plan.silinen
    assert not any(p.startswith(".git") or p in ("README.md", "KAYNAK.json") for p in hepsi)


def test_plan_uygulaninca_ayna_birebir_readme_korunuyor(iki_agac):
    kaynak, hedef = iki_agac
    senkron.plani_uygula(senkron.ayna_plani(kaynak, hedef), kaynak, hedef)
    assert senkron.ayna_plani(kaynak, hedef).bos, "ikinci plan boş olmalı"
    assert not (hedef / "animeler" / "silinen").exists(), "boş klasör kalmamalı"
    assert (hedef / "README.md").read_text("utf-8") == "BIZIM README"
    assert not (hedef / ".git").exists()


def test_senkronla_manifesti_ayni_anahtarlarla_yaziyor(iki_agac):
    kaynak, hedef = iki_agac

    def sahte_klon(url, dal, klon):
        assert (url, dal) == ("https://gitlab.test/AnimeDepo/animedepo.git", "master")
        shutil.copytree(kaynak, klon)
        return SHA, "2026-09-21T14:20:25+03:00"

    ozet = senkron.senkronla(hedef, "https://gitlab.test/AnimeDepo/animedepo.git", "master",
                             klonlayici=sahte_klon, cekilme_tarihi="2026-09-24")
    assert (ozet["eklenen"], ozet["degisen"], ozet["silinen"]) == (1, 1, 1)
    manifest = json.loads((hedef / "KAYNAK.json").read_text("utf-8"))
    gercek_anahtarlar = tuple(json.loads((DEPO / "arsiv" / "KAYNAK.json").read_text("utf-8")))
    assert tuple(manifest) == gercek_anahtarlar == paket.MANIFEST_ANAHTARLARI
    assert manifest["kaynak"] == "https://gitlab.test/AnimeDepo/animedepo"
    assert manifest["commit"] == SHA and manifest["cekilme_tarihi"] == "2026-09-24"
    assert manifest["anime_sayisi"] == 1 and manifest["dizin_last_update"] == 1700000000
    dosyalar = [p for p in hedef.rglob("*") if p.is_file()]
    assert manifest["dosya_sayisi"] == len(dosyalar), "README ve KAYNAK dahil sayılmalı"
    assert (hedef / "README.md").read_text("utf-8") == "BIZIM README"


def test_gecersiz_klon_hedefe_dokunmuyor(iki_agac):
    _kaynak, hedef = iki_agac
    once = sorted(p.relative_to(hedef).as_posix() for p in hedef.rglob("*"))

    def bos_klon(_url, _dal, klon):
        klon.mkdir()
        return None, None

    with pytest.raises(paket.ArsivHatasi):
        senkron.senkronla(hedef, klonlayici=bos_klon)
    assert sorted(p.relative_to(hedef).as_posix() for p in hedef.rglob("*")) == once


def test_komut_satiri_sayilari_yaziyor(iki_agac, monkeypatch, capsys):
    kaynak, hedef = iki_agac
    monkeypatch.setattr(senkron, "git_klonla",
                        lambda _u, _d, klon: (shutil.copytree(kaynak, klon), (SHA, None))[1])
    assert senkron.main(["--hedef", str(hedef)]) == 0
    cikti = capsys.readouterr().out
    assert "eklenen : 1" in cikti and "degisen : 1" in cikti and "silinen : 1" in cikti


@pytest.mark.skipif(shutil.which("git") is None, reason="`git` kurulu değil")
def test_gercek_git_klonu_yerel_depodan(tmp_path):
    """`git_klonla` gerçekten sığ klonlayıp commit bilgisini okuyor (ağ yok)."""
    upstream = arsiv_yaz(tmp_path / "upstream")

    def git(*argv):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "-c", "commit.gpgsign=false", *argv],
                       cwd=upstream, check=True, capture_output=True)

    git("init", "--quiet")
    git("symbolic-ref", "HEAD", "refs/heads/master")   # varsayılan dal adı ne olursa
    git("add", "-A")
    git("commit", "--quiet", "-m", "ilk")
    beklenen = subprocess.run(["git", "rev-parse", "HEAD"], cwd=upstream, check=True,
                              capture_output=True, text=True).stdout.strip()

    hedef = tmp_path / "ayna"
    ozet = senkron.senkronla(hedef, kaynak=upstream.as_uri(), dal="master")
    assert ozet["manifest"]["commit"] == beklenen
    assert (hedef / "dizin.json").read_bytes() == (upstream / "dizin.json").read_bytes()
    assert not (hedef / ".git").exists()


# ─────────────────────────────────────────────────────────────────────────────
# 6) Akış kalitesi: süzme, sıralama, adres düzeltme
# ─────────────────────────────────────────────────────────────────────────────
def _akislar(tmp_path, monkeypatch, kayitlar) -> List[Dict[str, str]]:
    depo = arsiv_yaz(tmp_path / "depo", {"a": {"title": "A", "bolumler": {"a-1": kayitlar}}})
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    return animedepo.get_episode_streams("a/a-1")


def test_olu_maskeli_ve_turkanime_kayitlari_agsiz_atlaniyor(tmp_path, monkeypatch, ag_yasak):
    kayitlar = [
        {"player": "DEAD_ALUCARD", "fansub": "F", "url": "https://olu.test/1"},
        {"player": "DEAD_AMATERASU", "fansub": "F", "mask": "/player/x", "path": "ajax/y"},
        {"player": "SIBNET", "fansub": "F", "url": "https://video.sibnet.ru/1", "alive": False},
        {"player": "MAIL", "fansub": "F", "mask": "/player/embed/html/abc"},
        {"player": "VK", "fansub": "F", "path": "ajax/videosec&b=x"},
        {"player": "GDRIVE", "fansub": "F", "url": "https://www.turkanime.co/player/abc"},
        {"player": "YADISK", "fansub": "F", "url": "https://cdn.turkanime.tv/v.mp4"},
        {"player": "SENDVID", "fansub": "F", "url": "https://sendvid.com/embed/ok"},
    ]
    akislar = _akislar(tmp_path, monkeypatch, kayitlar)
    assert [a["url"] for a in akislar] == ["https://sendvid.com/embed/ok"]
    assert ag_yasak == [], "maske çözümü için ağa çıkıldı"


def test_mask_cozumu_kodu_kaldirildi():
    """turkanime.tv kapandı; `bypass`'a (mask çözümü) giden yol geri gelmesin.

    Metin araması değil AST: yorumlarda eski davranış anlatılıyor.
    """
    import ast
    agac = ast.parse((DEPO / "turkanime_api" / "sources" / "animedepo.py").read_text("utf-8"))
    importlar = []
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.ImportFrom):
            importlar += [dugum.module or ""] + [a.name for a in dugum.names]
        elif isinstance(dugum, ast.Import):
            importlar += [a.name for a in dugum.names]
    assert not [i for i in importlar if "bypass" in i or i == "objects"], importlar


@pytest.mark.parametrize("ham,beklenen", [
    # Gerçek arşivdeki ~22 bin VK kaydının biçimi
    ("https:https://href.li/?https://vk.com/video_ext.php?oid=246903127&id=167933218&hash=fa1&hd=1",
     "https://vk.com/video_ext.php?oid=246903127&id=167933218&hash=fa1&hd=1"),
    ("https:https://my.mail.ru/mail/kurama/video/embed/_myvideo/4106",
     "https://my.mail.ru/mail/kurama/video/embed/_myvideo/4106"),
    ("https://href.li/?https://vk.com/video_ext.php?oid=-1&id=2", "https://vk.com/video_ext.php?oid=-1&id=2"),
    ("https://href.li/?https%3A%2F%2Fvk.com%2Fv", "https://vk.com/v"),
    ("//ok.ru/videoembed/123", "https://ok.ru/videoembed/123"),
    ("https://www.dailymotion.com/embed/video/xqkvjf ", "https://www.dailymotion.com/embed/video/xqkvjf"),
    ("https://www.turkanime.tv/html/pixeldrain.php?id=jCK6eW9k", "https://pixeldrain.com/u/jCK6eW9k"),
    # Dosya ADINDA "Turkanime" geçmesi konak değil; atılmamalı
    ("https://savefileway.com/video/embed/4CnA/660x425/Turkanime.net_x.mp4",
     "https://savefileway.com/video/embed/4CnA/660x425/Turkanime.net_x.mp4"),
    ("https:https://href.li/?https:&hd=1", None),        # gerçek arşivdeki bozuk kayıt
    ("https://www.turkanime.co/video/naruto-1", None),
    ("https://turkanime.com.tr/x", None),
    ("ftp://dosya.test/x", None),
    ("http://[bozuk", None),
    ("", None),
    (None, None),
])
def test_adres_duzeltme(ham, beklenen):
    assert animedepo._url_duzelt(ham) == beklenen


def test_akislar_oynatici_onceligine_gore_sirali(tmp_path, monkeypatch):
    oyuncular = ["MP4UPLOAD", "BILINMEYEN1", "SIBNET", "GDRIVE", "CLONE", "OK.RU",
                 "STREAMSB", "BILINMEYEN2", "YADISK", "XDVID"]
    kayitlar = [{"player": p, "fansub": "F", "url": f"https://{p.lower().replace('.', '')}.test/v"}
                for p in oyuncular]
    akislar = _akislar(tmp_path, monkeypatch, kayitlar)
    assert [a["player"] for a in akislar] == [
        "YADISK", "GDRIVE", "XDVID", "OK.RU", "SIBNET",   # desteklenenler kendi sırasıyla
        "BILINMEYEN1", "BILINMEYEN2",                      # bilinmeyenler arşiv sırasıyla
        "MP4UPLOAD", "CLONE", "STREAMSB",                  # false-positive'ler en sonda
    ]


def test_her_akis_player_ve_fansub_tasiyor_tekrarlar_atiliyor(tmp_path, monkeypatch):
    kayitlar = [
        {"player": "sibnet", "fansub": " TAÇE ", "url": "https://video.sibnet.ru/1"},
        {"player": "SIBNET", "fansub": "TAÇE", "url": "https://video.sibnet.ru/1"},
        {"player": "SIBNET", "fansub": "AnimeOU", "url": "https://video.sibnet.ru/1"},
        {"url": "https://cdn.test/v.mp4"},
    ]
    akislar = _akislar(tmp_path, monkeypatch, kayitlar)
    assert len(akislar) == 3, "aynı fansub'un aynı adresi bir kez"
    for akis in akislar:
        assert {"url", "label", "type", "player", "fansub"} <= set(akis)
    assert akislar[0]["label"] == "Sibnet TAÇE"
    sahipsiz = [a for a in akislar if a["player"] == "ANIMEDEPO"][0]
    assert sahipsiz["fansub"] == "" and sahipsiz["referer"] == "https://www.turkanime.co/"


def test_oncelik_listesi_objects_ile_ayni():
    """`objects.SUPPORTED` ortak listeden türüyor; iki kopya kayamaz."""
    from turkanime_api import objects
    assert objects.SUPPORTED == list(DESTEKLENEN_OYNATICILAR)
    assert isinstance(objects.SUPPORTED, list)
    assert oncelik_anahtari("ok.ru") == oncelik_anahtari("ODNOKLASSNIKI")


# ─────────────────────────────────────────────────────────────────────────────
# 7) AdapterBolum: fansub seçimi
# ─────────────────────────────────────────────────────────────────────────────
def _fansublu_akislar():
    return [
        {"url": "https://yadi.sk/a", "label": "Yadisk A", "player": "YADISK", "fansub": "A"},
        {"url": "https://sibnet.test/b", "label": "Sibnet B", "player": "SIBNET", "fansub": "B"},
        {"url": "https://mail.test/a", "label": "Mail A", "player": "MAIL", "fansub": "A"},
        {"url": "https://ok.test/b", "label": "Ok B", "player": "ODNOKLASSNIKI", "fansub": "B"},
    ]


@pytest.fixture
def denenen(monkeypatch):
    """`extract_video_info` sahtesi: hiçbir adres çalışmıyor, denenenler kayıtlı."""
    from turkanime_api.sources import adapter as adapter_mod
    kayit: List[str] = []
    monkeypatch.setattr(adapter_mod, "extract_video_info",
                        lambda url, _opts: kayit.append(url) or {})
    return kayit


def _bolum(saglayici: Callable[[str], List[Dict[str, str]]]) -> AdapterBolum:
    return AdapterBolum(url="a/a-1", title="1. Bölüm",
                        anime=AdapterAnime(slug="a", title="A"),
                        stream_provider=saglayici, player_name="ANIMEDEPO")


def test_fansublar_akislardan_tek_getirmeyle(denenen):
    cagri = {"n": 0}

    def saglayici(_url):
        cagri["n"] += 1
        return _fansublu_akislar()

    bolum = _bolum(saglayici)
    assert bolum.fansubs == ["A", "B"]
    assert bolum.fansubs == ["A", "B"]
    bolum.best_video(by_res=False)
    assert cagri["n"] == 1, "fansubs + best_video aynı getirmeyi paylaşmalı"
    bolum.best_video(by_res=False)
    assert cagri["n"] == 2, "sonraki oynatma taze liste almalı (süreli adresler)"


def test_best_video_secilen_fansubla_sinirli(denenen):
    bolum = _bolum(lambda _u: _fansublu_akislar())
    durumlar: List[dict] = []
    assert bolum.best_video(by_res=False, by_fansub="B", callback=durumlar.append) is None
    assert denenen == ["https://sibnet.test/b", "https://ok.test/b"]
    assert {d["player"] for d in durumlar if d["status"] == "çalışmıyor"} == \
        {"SIBNET", "ODNOKLASSNIKI"}, "ilerlemede akışın kendi oynatıcısı görünmeli"


def test_best_video_eslesmeyen_fansubda_hepsine_dusuyor(denenen):
    bolum = _bolum(lambda _u: _fansublu_akislar())
    bolum.best_video(by_res=False, by_fansub="Olmayan Grup")
    assert denenen == [a["url"] for a in _fansublu_akislar()]


def test_fansubsuz_kaynak_eskisi_gibi(denenen):
    """Fansub anahtarı taşımayan sağlayıcıda davranış değişmemeli."""
    akislar = [{"url": "https://cdn.test/1080.mp4", "label": "1080p"},
               {"url": "https://cdn.test/720.mp4", "label": "720p"}]
    bolum = AdapterBolum(url="https://kaynak.test/b", title="1",
                         anime=AdapterAnime(slug="x", title="X"),
                         stream_provider=lambda _u: akislar, player_name="TRANIMACI")
    assert bolum.fansubs == []
    durumlar: List[dict] = []
    bolum.best_video(by_fansub="Herhangi", callback=durumlar.append)
    assert denenen == ["https://cdn.test/1080.mp4", "https://cdn.test/720.mp4"]
    assert {d["player"] for d in durumlar} == {"TRANIMACI"}


def test_fansubs_saglayici_hatasini_yutuyor():
    def patlayan(_url):
        raise RuntimeError("ağ yok")

    assert _bolum(patlayan).fansubs == []


def test_kopru_animedepo_akislarini_fansubla_tasiyor(tmp_path, monkeypatch, denenen):
    """Qt köprüsünden kurulan bölüm gerçek AnimeDepo akışlarıyla fansub verir."""
    from turkanime_api.gui.qt.sources_bridge import _build_function_source

    depo = arsiv_yaz(tmp_path / "depo", {"a": {"title": "A", "bolumler": {"a-1": [
        {"player": "SIBNET", "fansub": "TAÇE", "url": "https://video.sibnet.ru/1"},
        {"player": "MAIL", "fansub": "AnimeOU", "url": "https://my.mail.ru/2"},
    ]}}})
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    bolum = _build_function_source("AnimeDepo", "a", "A")[0]["obj"]
    assert bolum.fansubs == ["AnimeOU", "TAÇE"], "MAIL (öncelikli) önce gelir"
    bolum.best_video(by_res=False, by_fansub="TAÇE")
    assert denenen == ["https://video.sibnet.ru/1"]


# ─────────────────────────────────────────────────────────────────────────────
# 8) Depo hijyeni ve gerçek arşiv dumanı
# ─────────────────────────────────────────────────────────────────────────────
def test_indirilenler_gitignore_da():
    """Depoda veri kökü depo kökü: indirilen arşiv ve önbellek commit'lenmesin."""
    satirlar = (DEPO / ".gitignore").read_text("utf-8").splitlines()
    for klasor in ("cevrimdisi_arsiv", "arsiv_onbellek"):
        assert any(s.strip().strip("/") == klasor for s in satirlar), klasor


def test_arsiv_readme_uygulamayi_anlatiyor():
    """README'deki sıra ve adlar koddakilerle aynı kalsın."""
    import re
    metin = (DEPO / "arsiv" / "README.md").read_text("utf-8")
    bolum = metin.split("## Uygulama bu klasörü nasıl kullanıyor", 1)[1].split("\n## ", 1)[0]
    maddeler = re.split(r"^\d\. ", bolum, flags=re.M)[1:]
    assert len(maddeler) == 4, "dört kademe bekleniyordu"
    assert animedepo.DIZIN_ORTAM_ANAHTARI in maddeler[0]
    assert animedepo.DIZIN_AYAR_ANAHTARI in maddeler[0]
    assert animedepo.CEVRIMDISI_KLASOR in maddeler[1]
    assert "`arsiv/`" in maddeler[2]
    uzak = maddeler[3]
    sira = [uzak.index(p) for p in (animedepo.ORTAM_ANAHTARI, "GitLab", "GitHub")]
    assert sira == sorted(sira), "uzak aynaların sırası kodla aynı olmalı"
    assert animedepo.AYAR_ANAHTARI in uzak
    assert animedepo.ONBELLEK_KLASOR in bolum
    assert "python -m turkanime_api.common.arsiv_senkron --hedef arsiv" in metin


def test_gercek_arsiv_dizini_ve_manifest_tutarli():
    """Duman testi: depodaki ayna okunabiliyor ve KAYNAK.json onu anlatıyor."""
    manifest = json.loads((DEPO / "arsiv" / "KAYNAK.json").read_text("utf-8"))
    dizin = paket.arsivi_dogrula(DEPO / "arsiv")
    assert tuple(manifest) == paket.MANIFEST_ANAHTARLARI
    assert manifest["anime_sayisi"] == paket.anime_sayisi(dizin)
    assert manifest["dizin_last_update"] == dizin["last_update"]
    assert manifest["anime_sayisi"] > 1000


# ─────────────────────────────────────────────────────────────────────────────
# 9) Denetim düzeltmeleri: GUI donması, disk hatası, iptal, sembolik bağ,
#    sessiz okuma hatası, test yalıtımı, eşitleme hedefi
# ─────────────────────────────────────────────────────────────────────────────
def _bekle(kosul: Callable[[], bool], sure: float = 5.0) -> bool:
    son = time.monotonic() + sure
    while time.monotonic() < son:
        if kosul():
            return True
        time.sleep(0.01)
    return kosul()


def _dizin_verisi(etiket: str) -> Dict[str, Any]:
    return {"last_update": 1, "index": {"N": {"naruto": {"title": f"Naruto {etiket}"}}}}


class BekleyenOturum:
    """İlk `get` testin izin vermesini bekler (yavaş ya da takılmış ayna).

    ``yanitlar[i]`` i. isteğin yanıtı (sonuncusu tekrar eder). Beklemenin
    süresi değil KENDİSİ sınanıyor: gerçek zaman aşımı 10-75 sn.
    """

    def __init__(self, yanitlar: List[Any]):
        self.yanitlar = yanitlar
        self.cagri: List[str] = []
        self.girdi = threading.Event()
        self.birak = threading.Event()

    def get(self, url, timeout=None, headers=None, stream=False):  # pylint: disable=unused-argument
        self.cagri.append(url)
        if len(self.cagri) == 1:
            self.girdi.set()
            self.birak.wait(10)
        return self.yanitlar[min(len(self.cagri), len(self.yanitlar)) - 1]


# 9a) `sifirla` G/Ç beklemez ────────────────────────────────────────────────────
def test_sifirla_suren_uzak_okumayi_beklemiyor(monkeypatch):
    """ESKİ HATA: `dizin()` tek kilidi uzak okumanın TAMAMI boyunca (3 ayna ×
    10 sn) tutuyordu; `sifirla` aynı kilidi bekliyordu ve GUI thread'inden
    çağrılıyor ("Varsayılana dön"). Arka planda bir arama yavaş aynalara
    takılmışken pencere ~30 sn donuyordu."""
    oturum = BekleyenOturum([SahteYanit(200, veri=_dizin_verisi("ESKI")),
                             SahteYanit(200, veri=_dizin_verisi("YENI"))])
    monkeypatch.setattr(animedepo, "_session", lambda: oturum)
    arka = threading.Thread(target=animedepo.dizin, daemon=True)
    arka.start()
    assert oturum.girdi.wait(5), "arka plan okuması aynaya ulaşmadı"

    bas = time.monotonic()
    animedepo.sifirla()
    sure = time.monotonic() - bas
    oturum.birak.set()
    arka.join(5)

    assert sure < 0.5, f"sifirla sürmekte olan okumayı {sure:.2f} sn bekledi"
    assert animedepo.dizin()["index"]["N"]["naruto"]["title"] == "Naruto YENI", \
        "sıfırlamadan ÖNCE başlamış okumanın sonucu önbelleğe girmemeli"
    assert len(oturum.cagri) == 2


def test_sifirla_suren_konum_cozumunu_beklemiyor(tmp_path, monkeypatch):
    """Konum çözümü aday klasörlerde megabaytlık dizin.json ayrıştırıyor;
    eskiden bu da `sifirla`nın aldığı kilidin içindeydi."""
    girdi, birak = threading.Event(), threading.Event()
    asil = paket.arsiv_gecerli_mi
    sayac = {"n": 0}

    def yavas(kok):
        sayac["n"] += 1
        if sayac["n"] == 1:
            girdi.set()
            birak.wait(10)
        return asil(kok)

    monkeypatch.setattr(paket, "arsiv_gecerli_mi", yavas)
    arka = threading.Thread(target=animedepo.arsiv_konumu, daemon=True)
    arka.start()
    assert girdi.wait(5)

    bas = time.monotonic()
    animedepo.sifirla()
    sure = time.monotonic() - bas
    # Sıfırlamanın sebebi: bu arada yeni bir arşiv klasörü gösterildi.
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    birak.set()
    arka.join(5)

    assert sure < 0.5, f"sifirla konum çözümünü {sure:.2f} sn bekledi"
    assert animedepo.arsiv_konumu() == animedepo.ArsivKonumu("depo", depo), \
        "sıfırlamadan önce çözülen (eski) konum önbellekte kalmamalı"


# 9b) Yerel disk hatası yedeğe geçirmez, "paket bozuk" denmez ────────────────────
class _DoluDosya:
    """Yazınca ENOSPC veren dosya (disk dolu)."""

    def write(self, _veri):
        raise OSError(errno.ENOSPC, "No space left on device")

    def close(self):
        pass


def test_takasta_disk_dolarsa_yedek_indirilmiyor(http, tmp_path, monkeypatch):
    """ESKİ HATA: her hata "bu kaynak olmadı" sayılıyordu; GitLab'dan ~230 MB
    indikten sonra disk dolunca GitHub'ın ~231 MB'lık paketi de indiriliyordu."""
    hedef = arsiv_yaz(tmp_path / "cevrimdisi_arsiv", etiket=" ESKI")
    govde = tar_gz(arsiv_uyeleri("animedepo-master"))
    kayit = http({GITLAB_PAKET: SahteYanit(200, govde=govde),
                  GITHUB_PAKET: SahteYanit(200, govde=govde)})

    def dolu(_yeni, _hedef):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(paket, "yerine_koy", dolu)
    with pytest.raises(paket.YerelDiskHatasi, match="diskte yer kalmadı"):
        animedepo.tam_arsiv_indir(hedef=hedef)
    assert [u for u, _ in kayit] == [GITLAB_PAKET], "disk hatasında yedek indirilmemeli"
    assert "ESKI" in (hedef / "dizin.json").read_text("utf-8")
    assert _artiklar(tmp_path) == []


def test_indirirken_disk_dolarsa_disk_hatasi(http, tmp_path, monkeypatch):
    govde = tar_gz(arsiv_uyeleri("animedepo-master"))
    kayit = http({GITLAB_PAKET: SahteYanit(200, govde=govde),
                  GITHUB_PAKET: SahteYanit(200, govde=govde)})
    monkeypatch.setattr(paket, "open", lambda *a, **k: _DoluDosya(), raising=False)
    with pytest.raises(paket.YerelDiskHatasi) as bilgi:
        animedepo.tam_arsiv_indir(hedef=tmp_path / "cevrimdisi_arsiv")
    assert "paket.tar.gz" in str(bilgi.value) and "diskte yer kalmadı" in str(bilgi.value)
    assert [u for u, _ in kayit] == [GITLAB_PAKET]


def test_acarken_disk_dolarsa_paket_bozuk_denmiyor(tmp_path, monkeypatch):
    """ESKİ HATA: `guvenli_ac` her OSError'u "paket bozuk" diye sarıyordu."""
    paket_yolu = tmp_path / "p.tar.gz"
    paket_yolu.write_bytes(tar_gz(arsiv_uyeleri("animedepo-master")))
    monkeypatch.setattr(paket, "open", lambda *a, **k: _DoluDosya(), raising=False)
    with pytest.raises(paket.YerelDiskHatasi, match="diskte yer kalmadı") as bilgi:
        paket.guvenli_ac(paket_yolu, tmp_path / "acilan", ust_desen="animedepo-master*")
    assert "bozuk" not in str(bilgi.value)


def test_kesik_gzip_hala_paket_bozuk(tmp_path):
    """Okuma tarafı ayrı kalmalı: kesik paket disk hatası sayılmasın (yedeğe geçilsin)."""
    govde = tar_gz(arsiv_uyeleri("animedepo-master"))
    paket_yolu = tmp_path / "p.tar.gz"
    paket_yolu.write_bytes(govde[: len(govde) // 2])
    with pytest.raises(paket.ArsivHatasi, match="paket (bozuk|açılamadı)") as bilgi:
        paket.guvenli_ac(paket_yolu, tmp_path / "acilan", ust_desen="animedepo-master*")
    assert not isinstance(bilgi.value, paket.YerelDiskHatasi)


# 9c) İptal bağlanırken ve ilk bayt beklenirken de işler ─────────────────────────
def _arka_planda_indir(iptal, hedef) -> Tuple[threading.Thread, Dict[str, Any]]:
    sonuc: Dict[str, Any] = {}

    def calis():
        try:
            sonuc["yol"] = animedepo.tam_arsiv_indir(iptal=iptal, hedef=hedef)
        except BaseException as hata:        # pylint: disable=broad-except
            sonuc["hata"] = hata

    arka = threading.Thread(target=calis, daemon=True)
    arka.start()
    return arka, sonuc


def test_iptal_baglanirken_beklemeden_isliyor(tmp_path, monkeypatch):
    """ESKİ HATA: iptal yalnızca parçalar arasında denetleniyordu; `get`
    bağlanırken (15 sn'ye kadar) düğmeler "İptal ediliyor…"da kilitliydi."""
    hedef = arsiv_yaz(tmp_path / "cevrimdisi_arsiv", etiket=" ESKI")
    gec_yanit = SahteYanit(200, govde=tar_gz(arsiv_uyeleri("animedepo-master")))
    oturum = BekleyenOturum([gec_yanit])
    monkeypatch.setattr(animedepo, "_session", lambda: oturum)
    iptal = threading.Event()
    arka, sonuc = _arka_planda_indir(iptal, hedef)
    assert oturum.girdi.wait(5)

    bas = time.monotonic()
    iptal.set()
    arka.join(5)
    sure = time.monotonic() - bas

    assert not arka.is_alive() and sure < 1.0, f"iptal {sure:.2f} sn sonra işlendi"
    assert isinstance(sonuc.get("hata"), paket.IptalEdildi), sonuc
    assert oturum.cagri == [GITLAB_PAKET], "iptalden sonra yedek denenmemeli"
    oturum.birak.set()
    assert _bekle(lambda: gec_yanit.kapandi), "iptalden sonra gelen yanıt kapatılmalı"
    assert "ESKI" in (hedef / "dizin.json").read_text("utf-8")


class TakilanYanit(SahteYanit):
    """Başlıklar geldi, gövdenin ilk baytı gelmiyor (GitLab paketi üretiyor)."""

    def __init__(self):
        super().__init__(200)
        self.basladi = threading.Event()
        self.birak = threading.Event()

    def iter_content(self, chunk_size=None):
        self.basladi.set()
        self.birak.wait(10)
        yield b""


def test_iptal_ilk_bayt_beklenirken_isliyor(http, tmp_path):
    hedef = arsiv_yaz(tmp_path / "cevrimdisi_arsiv", etiket=" ESKI")
    yanit = TakilanYanit()
    kayit = http({GITLAB_PAKET: yanit,
                  GITHUB_PAKET: SahteYanit(200, govde=tar_gz(arsiv_uyeleri("animedepo-master")))})
    iptal = threading.Event()
    arka, sonuc = _arka_planda_indir(iptal, hedef)
    assert yanit.basladi.wait(5)

    bas = time.monotonic()
    iptal.set()
    arka.join(5)
    sure = time.monotonic() - bas

    assert not arka.is_alive() and sure < 1.0, f"iptal {sure:.2f} sn sonra işlendi"
    assert isinstance(sonuc.get("hata"), paket.IptalEdildi), sonuc
    assert [u for u, _ in kayit] == [GITLAB_PAKET]
    assert "ESKI" in (hedef / "dizin.json").read_text("utf-8")
    assert _artiklar(tmp_path) == [], "iptalde geçici klasör kalmamalı"
    yanit.birak.set()
    assert _bekle(lambda: yanit.kapandi), "takılan akış arka planda kapatılmalı"


# 9d) Sembolik bağlı hedef ──────────────────────────────────────────────────────
def _bag_kur(bag: Path, hedef: Path) -> None:
    bag.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(hedef, bag, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("sembolik bağ oluşturulamıyor")


def test_bagli_hedefte_takas_bagin_gosterdigi_diskte(tmp_path):
    """ESKİ HATA: bağın KENDİSİ gizli ada taşınıyor, yeni arşiv veri kökünün
    diskine gerçek klasör olarak iniyor, eski veri öbür diskte kalıyordu."""
    asil = arsiv_yaz(tmp_path / "baska_disk" / "arsiv", etiket=" ESKI")
    bag = tmp_path / "veri" / animedepo.CEVRIMDISI_KLASOR
    _bag_kur(bag, asil)
    yeni = arsiv_yaz(tmp_path / "baska_disk" / "yeni", etiket=" YENI")

    assert paket.yerine_koy(yeni, bag) == []

    assert bag.is_symlink(), "bağ korunmalı"
    assert Path(os.path.realpath(bag)) == asil.resolve()
    assert "YENI" in (asil / "dizin.json").read_text("utf-8")
    assert _artiklar(tmp_path / "veri") == [] and _artiklar(tmp_path / "baska_disk") == []


def test_bagli_hedefe_tam_arsiv_indirme_bagi_koruyor(http, tmp_path):
    asil = arsiv_yaz(tmp_path / "baska_disk" / "arsiv", etiket=" ESKI")
    bag = tmp_path / "veri" / animedepo.CEVRIMDISI_KLASOR
    _bag_kur(bag, asil)
    http({GITLAB_PAKET: SahteYanit(200, govde=tar_gz(arsiv_uyeleri("animedepo-master")))})

    assert animedepo.tam_arsiv_indir(hedef=bag) == bag

    assert bag.is_symlink()
    assert "Naruto yeni" in (asil / "dizin.json").read_text("utf-8")
    assert sorted(p.name for p in (tmp_path / "veri").iterdir()) == [animedepo.CEVRIMDISI_KLASOR]
    assert _artiklar(tmp_path / "baska_disk") == []


def test_kopuk_bag_hedefi_reddediliyor(http, tmp_path):
    """Bağın diski takılı değilse yol kurulmamalı (veri sistem diskine inerdi)."""
    bag = tmp_path / "veri" / animedepo.CEVRIMDISI_KLASOR
    _bag_kur(bag, tmp_path / "takili_degil" / "arsiv")
    with pytest.raises(paket.YerelDiskHatasi, match="disk takılı"):
        paket.yerine_koy(arsiv_yaz(tmp_path / "yeni"), bag)
    kayit = http({})
    with pytest.raises(paket.YerelDiskHatasi, match="disk takılı"):
        animedepo.tam_arsiv_indir(hedef=bag)
    assert kayit == [], "kopuk bağda ağa hiç çıkılmamalı"
    assert not (tmp_path / "takili_degil").exists()


def test_eski_kopya_silinemezse_durumda_gorunuyor(tmp_path, monkeypatch):
    """ESKİ HATA: `rmtree(ignore_errors=True)` silinemeyen ~0,5 GB'ı yutuyordu."""
    kok = tmp_path / "veri"
    hedef = arsiv_yaz(kok / animedepo.CEVRIMDISI_KLASOR, etiket=" ESKI")
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: hedef)
    yeni = arsiv_yaz(kok / "yeni", etiket=" YENI")
    asil_rmtree = shutil.rmtree

    def kilitli_rmtree(yol, onerror=None, onexc=None, **_):
        (onexc or onerror)(os.unlink, os.path.join(yol, "dizin.json"),
                           PermissionError("kilitli"))

    monkeypatch.setattr(paket.shutil, "rmtree", kilitli_rmtree)
    kalan = paket.yerine_koy(yeni, hedef)
    monkeypatch.setattr(paket.shutil, "rmtree", asil_rmtree)

    assert len(kalan) == 1 and "-eski-" in kalan[0]
    assert "YENI" in (hedef / "dizin.json").read_text("utf-8"), "takas yine de başarılı"
    durum = animedepo.arsiv_durumu()
    assert [p.name for p in durum.kalintilar] == [Path(kalan[0]).parent.name]


# 9e) Arşiv okunamayınca arama bunu söylüyor ────────────────────────────────────
def test_arsiv_okunamazsa_arama_sebebiyle_hata_veriyor(http):
    """ESKİ HATA: aynalar kapalı + önbellek boş → `dizin()` sessizce {} →
    arama [] → kullanıcı "Aradığınız anime bulunamadı" görüyordu."""
    http({})
    with pytest.raises(animedepo.ArsivOkunamadi, match="uzak aynalar yanıt vermedi"):
        animedepo.search_animedepo("naruto")
    assert animedepo.dizin() == {}, "dizin() eski sözleşmesini korumalı"


def test_yerel_arsiv_okunamazsa_konumuyla_soyleniyor(tmp_path, monkeypatch, ag_yasak):
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    assert animedepo.arsiv_konumu().kaynak == "depo"
    (depo / "dizin.json").write_text("{yarım kopya", "utf-8")   # sonradan bozuldu
    with pytest.raises(animedepo.ArsivOkunamadi) as bilgi:
        animedepo.search_animedepo("naruto")
    assert "yerel arşiv" in str(bilgi.value) and str(depo) in str(bilgi.value)


def test_bos_arsiv_hata_degil(tmp_path, monkeypatch, ag_yasak):
    """Okundu ama içinde anime yok: bu "bulunamadı"dır, hata değil."""
    depo = arsiv_yaz(tmp_path / "depo", animeler={})
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    assert animedepo.search_animedepo("naruto") == []


# 9f) Geliştiricinin ayarları test paketine sızmıyor ─────────────────────────────
def test_gelistiricinin_ayarlari_testlere_sizmiyor(monkeypatch, tmp_path_factory):
    """ESKİ HATA: `_arsiv_yalitimi` ayarlar.json'daki `animedepo_dizin` ve
    `animedepo_url`'yi yalıtmıyordu. pytest depodan çalışınca veri kökü depo
    kökü; geliştirici "Klasör seç…"e bastıysa kendi arşivi ve aynası bütün
    teste sızıyordu (31 test düşüyordu). Bu test "depo kökü"nü pytest'in
    geçici kökü DIŞINDA kurup oradan koşuyor."""
    taban = tmp_path_factory.getbasetemp().resolve()
    with tempfile.TemporaryDirectory(prefix="gelistirici-") as ham:
        dev = Path(ham).resolve()
        assert taban not in dev.parents, "sınama klasörü pytest'in geçici kökü dışında olmalı"
        (dev / ".git").mkdir()
        benim = arsiv_yaz(dev / "benim_arsivim")
        ayar_yaz(dev, animedepo_dizin=str(benim),
                 animedepo_url="https://benim-aynam.example/arsiv")
        monkeypatch.chdir(dev)
        animedepo.sifirla()
        try:
            assert animedepo.veri_koku() == dev
            assert animedepo.arsiv_konumu().kaynak == "uzak", "seçilen klasör sızdı"
            assert animedepo.uzak_aynalar()[0] == GITLAB, "özel ayna sızdı"
            assert animedepo.arsiv_durumu().ayar_dizini == ""
        finally:
            monkeypatch.chdir(taban)         # geçici klasör silinmeden önce çık


# 9g) Eşitleme aracı yanlış hedefi silmiyor ─────────────────────────────────────
def _agac(kok: Path) -> List[str]:
    return sorted(p.relative_to(kok).as_posix() for p in kok.rglob("*"))


@pytest.fixture
def sahte_depo(tmp_path, monkeypatch):
    """Bu projenin kökü gibi bir klasör; çalışma dizini orası."""
    depo = tmp_path / "depo"
    _yaz(depo, "turkanime_api/onemli.py", "print()")
    _yaz(depo, "pyproject.toml", "[project]")
    _yaz(depo, "ayarlar.json", "{}")
    (depo / ".git").mkdir()
    monkeypatch.chdir(depo)
    return depo


@pytest.mark.parametrize("hedef", ["", ".", "./", "..", "<depo>"])
def test_senkron_calisma_dizinini_silmiyor(sahte_depo, monkeypatch, capsys, hedef):
    """ESKİ HATA: `--hedef ""` (argparse: Path("") == ".") ya da boş kalmış
    `$HEDEF` çalışma dizininde `.git` dışındaki her şeyi siliyordu."""
    once = _agac(sahte_depo)
    klonlar: List[Any] = []
    monkeypatch.setattr(senkron, "git_klonla",
                        lambda *a: klonlar.append(a) or (None, None))
    arg = str(sahte_depo) if hedef == "<depo>" else hedef

    assert senkron.main(["--hedef", arg]) == 1

    assert _agac(sahte_depo) == once
    assert klonlar == [], "yanlış hedefte klonlamaya bile başlanmamalı"
    assert "hata:" in capsys.readouterr().err


def test_senkron_hedef_denetimi(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proje = tmp_path / "baska_proje"
    _yaz(proje, "pyproject.toml", "")
    dolu = tmp_path / "dolu"
    _yaz(dolu, "notlar.txt", "önemli")

    with pytest.raises(paket.ArsivHatasi, match="proje kökü"):
        senkron.hedef_denetle(proje)
    with pytest.raises(paket.ArsivHatasi, match="geçerli bir arşiv de değil"):
        senkron.hedef_denetle(dolu)
    senkron.hedef_denetle(tmp_path / "yok")               # ilk eşitleme
    (tmp_path / "bos").mkdir()
    senkron.hedef_denetle(tmp_path / "bos")
    senkron.hedef_denetle(arsiv_yaz(tmp_path / "ayna"))   # mevcut ayna

    # --zorla: emin olunan durumda denetim atlanır.
    kaynak = arsiv_yaz(tmp_path / "kaynak")
    ozet = senkron.senkronla(
        dolu, klonlayici=lambda _u, _d, klon: (shutil.copytree(kaynak, klon), (SHA, None))[1],
        zorla=True)
    assert ozet["silinen"] == 1 and not (dolu / "notlar.txt").exists()


# ─────────────────────────────────────────────────────────────────────────────
# 10) Denetim turu 2
# ─────────────────────────────────────────────────────────────────────────────
# 10a) Bölüm/akış okuyucuları "arşivde yok" ile "okunamadı"yı ayırıyor ───────
def _cevrimdisi_paketli_uygulama(monkeypatch, tmp_path) -> Path:
    """Paketlenmiş uygulama çevrimdışı: yerel arşiv yok (conftest), disk
    önbelleğinde önceki oturumdan yalnızca dizin.json var, ağ yok."""
    onbellek = tmp_path / "onbellek"
    onbellek.mkdir()
    (onbellek / "dizin.json").write_text(json.dumps(
        {"index": {"N": {"naruto": {"title": "Naruto"}}}}), "utf-8")
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: onbellek)

    class AgYok:
        def get(self, *_a, **_k):
            raise ConnectionError("ağ yok")

    monkeypatch.setattr(animedepo, "_session", AgYok)
    animedepo.sifirla()
    return onbellek


def test_cevrimdisi_bolum_listesi_bos_degil_sebebiyle_hata(tmp_path, monkeypatch):
    """ESKİ HATA: arama önbellekteki dizinle çalışıyor, ama tıklanan her sonuç
    "bölüm bulunamadı" diyordu: bölüm/akış okuyucuları her hatayı boş listeye
    çeviriyordu ve asıl sebep (aynalar düştü, dosya önbellekte yok) hiç
    görünmüyordu."""
    _cevrimdisi_paketli_uygulama(monkeypatch, tmp_path)
    assert animedepo.arsiv_konumu().kaynak == "uzak"
    assert animedepo.search_animedepo("naruto") == [("naruto", "Naruto")]

    with pytest.raises(animedepo.ArsivOkunamadi, match="uzak aynalar yanıt vermedi") as bilgi:
        animedepo.get_anime_episodes("naruto")
    assert "ağ yok" in str(bilgi.value), "asıl sebep mesajda olmalı"
    with pytest.raises(animedepo.ArsivOkunamadi, match="ağ yok"):
        animedepo.get_episode_streams("naruto/naruto-1-bolum")


def test_cevrimdisi_kopru_bolum_hatasini_yukseltiyor(tmp_path, monkeypatch):
    """Qt köprüsü hatayı yükseltmeli: detay sayfası `_do_load` metnini gösteriyor
    (boş liste gelince "kaynağında bölüm bulunamadı" diyordu)."""
    from turkanime_api.gui.qt import sources_bridge
    _cevrimdisi_paketli_uygulama(monkeypatch, tmp_path)
    with pytest.raises(animedepo.ArsivOkunamadi, match="ağ yok"):
        sources_bridge.fetch_episodes("TürkAnime", "naruto", "Naruto")


@pytest.mark.parametrize("gitlab,iz", [
    (ConnectionError("gitlab düştü"), "gitlab düştü"),
    (SahteYanit(429), "HTTP 429"),                       # hız sınırı
    (SahteYanit(503), "HTTP 503"),
    (SahteYanit(200, veri=ValueError("HTML hata sayfası")), "HTML hata sayfası"),
])
def test_bir_ayna_404_oteki_ulasilamazsa_okunamadi(http, gitlab, iz):
    """GitHub aynası bugün her dosyaya 404 dönüyor. GitLab'a ulaşılamazken bu
    404 "anime yok" sayılırsa ağ hatası yine "bölüm bulunamadı" olur."""
    http({f"{GITLAB}/animeler/a/bolumler.json": gitlab,
          f"{GITLAB}/animeler/a/a-1.json": gitlab})              # GitHub: 404
    with pytest.raises(animedepo.ArsivOkunamadi, match=iz):
        animedepo.get_anime_episodes("a")
    with pytest.raises(animedepo.ArsivOkunamadi, match=iz):
        animedepo.get_episode_streams("a/a-1")


def test_butun_aynalar_404_derse_arsivde_yok_bos_liste(http):
    """Arşive ulaşıldı, kayıt yok: bu hata değil (dizinde olup klasörü olmayan
    animeler gerçek arşivde de var)."""
    kayit = http({})
    with pytest.raises(animedepo.ArsivdeYok):
        animedepo.fetch_json("animeler/hic-yok/bolumler.json")
    assert animedepo.get_anime_episodes("hic-yok") == []
    assert animedepo.get_episode_streams("hic-yok/b-1") == []
    assert len(kayit) == 6, "üç okumada da iki ayna sorulmalı"


def test_guvensiz_kimlik_bos_liste_ag_yok(http):
    kayit = http({})
    assert animedepo.get_anime_episodes("../../disari") == []
    assert animedepo.get_episode_streams("../x/y") == []
    assert kayit == [], "güvensiz kimlik için istek atıldı"


def test_yerel_bozuk_bolum_dosyasi_okunamadi(tmp_path, monkeypatch, ag_yasak):
    """Bozuk JSON da bir `ValueError`; güvensiz yolun ValueError'ıyla
    karışıp "yok" sayılmamalı."""
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    (depo / "animeler" / "naruto" / "bolumler.json").write_text("[yarım", "utf-8")
    with pytest.raises(animedepo.ArsivOkunamadi) as bilgi:
        animedepo.get_anime_episodes("naruto")
    assert "yerel arşiv" in str(bilgi.value) and str(depo) in str(bilgi.value)


def test_yerel_arsiv_sonradan_kaybolursa_okunamadi(tmp_path, monkeypatch, ag_yasak):
    """Konum süreç başında çözülüyor. Arşiv sonradan silinir ya da diski
    çıkarılırsa her dosya "yok" olur; her anime "bölümü yok" görünmemeli."""
    depo = arsiv_yaz(tmp_path / "depo")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()
    assert animedepo.arsiv_konumu().kaynak == "depo"
    shutil.rmtree(depo)
    with pytest.raises(animedepo.ArsivOkunamadi, match="artık yok"):
        animedepo.get_anime_episodes("naruto")


def test_akis_saglayici_arsiv_hatasini_gecirir_digerlerini_yutar():
    from turkanime_api.sources import kayit

    def arsiv(_b):
        raise animedepo.ArsivOkunamadi("TürkAnime arşivi okunamadı: ağ yok")

    def bozuk_site(_b):
        raise RuntimeError("site değişti")

    with pytest.raises(animedepo.ArsivOkunamadi):
        kayit.akis_saglayici(arsiv, "a/a-1")("adres")
    assert kayit.akis_saglayici(bozuk_site, "a/a-1")("adres") == []


def test_cevrimdisi_oynatma_sebebi_soyluyor(tmp_path, monkeypatch, denenen):
    """ESKİ HATA: akış okuyucusu VE `akis_saglayici` hatayı yutuyordu; oynatma
    "çalışan video bulunamadı" diyordu. Arayüz `_play_blocking` ve indirme
    işçisi `best_video`'nun hatasını metniyle gösteriyor."""
    from turkanime_api.sources import kayit
    from turkanime_api.sources.adapter import kayittan_bolumler

    onbellek = _cevrimdisi_paketli_uygulama(monkeypatch, tmp_path)
    # Bölüm listesi önceki oturumdan önbellekte; bölüm dosyası değil.
    (onbellek / "animeler" / "naruto").mkdir(parents=True)
    (onbellek / "animeler" / "naruto" / "bolumler.json").write_text(
        '[["naruto-1-bolum", "1. Bölüm"]]', "utf-8")
    bolum = kayittan_bolumler(kayit.bul("TürkAnime"), "naruto", "Naruto")[0]

    assert bolum.fansubs == [], "fansub listesi oynatmayı çökertmemeli"
    durumlar: List[dict] = []
    with pytest.raises(animedepo.ArsivOkunamadi, match="ağ yok"):
        bolum.best_video(callback=durumlar.append)
    assert durumlar[-1]["status"] == "kaynak okunamadı"
    assert denenen == []


# 10b) Yeniden denemede başarısız adres atlanıyor ────────────────────────────
def test_best_video_atla_denenen_adresi_geciyor(monkeypatch):
    """ESKİ HATA: `best_video` her çağrıda videoları akışlardan yeniden kuruyor;
    çağıranın koyduğu `is_working = False` kayboluyor, aynı adres yine
    seçiliyordu. `atla` ile verilen adresler denenmez."""
    from turkanime_api.sources import adapter as adapter_mod
    denenen_: List[str] = []
    monkeypatch.setattr(adapter_mod, "extract_video_info",
                        lambda url, _o: denenen_.append(url) or {"url": url})
    bolum = _bolum(lambda _u: _fansublu_akislar())

    ilk = bolum.best_video(by_res=False)
    ikinci = bolum.best_video(by_res=False, atla={ilk.url})
    assert ikinci is not None and ikinci.url != ilk.url

    hepsi = {a["url"] for a in _fansublu_akislar()}
    durumlar: List[dict] = []
    once = len(denenen_)
    assert bolum.best_video(by_res=False, atla=hepsi, callback=durumlar.append) is None
    assert durumlar[-1]["status"] == "hiçbiri çalışmıyor"
    assert len(denenen_) == once, "hepsi atlanınca hiçbir adres yoklanmamalı"


def test_best_video_atla_secili_fansub_bitince_digerlerine_dusuyor(denenen):
    bolum = _bolum(lambda _u: _fansublu_akislar())
    b_adresleri = {a["url"] for a in _fansublu_akislar() if a["fansub"] == "B"}
    bolum.best_video(by_res=False, by_fansub="B", atla=b_adresleri)
    assert denenen == [a["url"] for a in _fansublu_akislar() if a["fansub"] != "B"]


# 10c) Windows uzun yolları ────────────────────────────────────────────────────
@pytest.mark.parametrize("girdi,beklenen", [
    ("C:\\Users\\K\\Turkanime\\cevrimdisi_arsiv\\dizin.json",
     "\\\\?\\C:\\Users\\K\\Turkanime\\cevrimdisi_arsiv\\dizin.json"),
    # Önek Win32 normalleştirmesini kapatıyor: / ve .. önceden çözülmeli.
    ("C:/Users/K/Turkanime/x/../arsiv_onbellek/a.json",
     "\\\\?\\C:\\Users\\K\\Turkanime\\arsiv_onbellek\\a.json"),
    ("\\\\sunucu\\paylasim\\arsiv\\dizin.json",
     "\\\\?\\UNC\\sunucu\\paylasim\\arsiv\\dizin.json"),
    ("\\\\?\\C:\\zaten\\onekli", "\\\\?\\C:\\zaten\\onekli"),
])
def test_windows_uzun_yol_bicimi(girdi, beklenen):
    assert paket.windows_uzun_yol(girdi) == beklenen
    assert paket.windows_uzun_yol(beklenen) == beklenen, "iki kez önek eklenmemeli"
    assert paket.gorunen_yol(beklenen) == ntpath.normpath(
        paket.gorunen_yol(girdi) if girdi.startswith("\\\\?") else girdi)


def test_windows_uzun_yol_260_ustu():
    """Arşivdeki en uzun göreli yol 311 karakter; 71 karakterlik indirme
    önekiyle 382. Önekli biçim uzunluğu korumalı, kesmemeli."""
    uzun = "C:\\Users\\Kullanici\\Turkanime\\cevrimdisi_arsiv\\" + "a" * 311
    assert len(uzun) > 260
    sonuc = paket.windows_uzun_yol(uzun)
    assert sonuc == "\\\\?\\" + uzun


def test_disk_yolu_windows_disinda_aynen(tmp_path):
    assert os.name != "nt"
    assert paket.disk_yolu(tmp_path / "a" / "b.json") == str(tmp_path / "a" / "b.json")


# Windows taklidi (Linux'ta): MAX_PATH sınırını bir denetim kancası koyuyor.
# `sys.addaudithook` geri alınamıyor; kanca süreçte bir kez kurulur ve yalnızca
# `_WINDOWS_TAKLIDI["onek"]` doluyken (fixture süresince) iş yapar.
_MAX_PATH = 259                  # 260, sondaki NUL dahil
_WINDOWS_TAKLIDI: Dict[str, Any] = {"onek": None}
_KANCA_KURULDU: List[bool] = []
# Yol taşıyan denetim olayları → yol argümanlarının sırası.
_YOL_OLAYLARI = {
    "open": (0,), "os.mkdir": (0,), "os.rename": (0, 1), "os.remove": (0,),
    "os.rmdir": (0,), "os.scandir": (0,), "os.listdir": (0,),
    "shutil.rmtree": (0,), "tempfile.mkdtemp": (0,),
}


def _max_path_kancasi(olay: str, argumanlar: tuple) -> None:
    onek = _WINDOWS_TAKLIDI["onek"]
    if onek is None or olay not in _YOL_OLAYLARI:
        return
    for sira in _YOL_OLAYLARI[olay]:
        if sira >= len(argumanlar) or isinstance(argumanlar[sira], int):
            continue
        try:
            yol = os.fsdecode(os.fspath(argumanlar[sira]))
        except TypeError:
            continue
        # Göreli adlar (dir_fd ile) kısa; Windows'ta öneksiz uzun mutlak yol
        # ENOENT verir — CPython'un orada gördüğü hata bu.
        if os.path.isabs(yol) and len(yol) > _MAX_PATH and not yol.startswith(onek):
            raise FileNotFoundError(errno.ENOENT, "Windows MAX_PATH (taklit)", yol)


@pytest.fixture
def windows_taklidi(tmp_path_factory, monkeypatch):
    """Linux'ta Windows'un yol sınırı ve `\\\\?\\` öneki.

    - Öneksiz, 259 karakterden uzun mutlak yolla yapılan dosya işlemi (open,
      mkdir, rename, remove, rmtree, mkdtemp…) ENOENT ile düşer.
    - `\\\\?\\`'in karşılığı "/"'e giden bir sembolik bağ: önekli yol aynı
      dosyaya çıkar ve kanca onu serbest bırakır. Bağ `tmp_path` dışında,
      kendi klasöründe (testin kendi ağacı onu hiç görmesin).
    - `paket._uzun_yol_gerekli` True; `paket.windows_uzun_yol` bu öneki
      ekler (gerçeği `ntpath` ile önek ekliyor, yukarıda saf sınanıyor).
    - `rmtree` Windows'taki gibi tam yollarla yürür: Linux'un dosya
      tanıtıcılı (dir_fd) yolu uzun yolu hiç görmediği için taklidi atlatırdı.
    """
    bag = tmp_path_factory.mktemp("uzun_onek") / "kok"
    bag.symlink_to("/")
    onek = str(bag)

    def uzun_yol(yol: str) -> str:
        return yol if yol.startswith(onek) else onek + os.path.abspath(yol)

    monkeypatch.setattr(paket, "_uzun_yol_gerekli", lambda: True)
    monkeypatch.setattr(paket, "windows_uzun_yol", uzun_yol)
    monkeypatch.setattr(shutil, "_use_fd_functions", False, raising=False)
    if hasattr(shutil, "_rmtree_impl"):                 # 3.13+
        monkeypatch.setattr(shutil, "_rmtree_impl", shutil._rmtree_unsafe)
    if not _KANCA_KURULDU:
        sys.addaudithook(_max_path_kancasi)
        _KANCA_KURULDU.append(True)
    _WINDOWS_TAKLIDI["onek"] = onek
    try:
        yield uzun_yol
    finally:
        _WINDOWS_TAKLIDI["onek"] = None


def _uzun_goreli() -> str:
    """Gerçek arşivdeki uzun bölüm adları gibi: ~225 karakter göreli yol."""
    return f"animeler/{'uzun-seri-' * 6}/{'cok-uzun-bolum-adi-' * 8}1-bolum.json"


def test_windows_taklidi_gercekten_sinirliyor(tmp_path, windows_taklidi):
    """Taklidin kendisi: öneksiz uzun yol düşer, önekli aynı yol çalışır."""
    klasor = tmp_path / ("k" * 120) / ("d" * 150)
    assert len(str(klasor)) > _MAX_PATH
    with pytest.raises(FileNotFoundError):
        os.makedirs(klasor)
    os.makedirs(windows_taklidi(str(klasor)))
    dosya = klasor / "x.json"
    with pytest.raises(FileNotFoundError):
        open(dosya, "w", encoding="utf-8").close()   # pylint: disable=consider-using-with
    with open(windows_taklidi(str(dosya)), "w", encoding="utf-8") as fp:
        fp.write("tamam")
    assert os.path.isfile(dosya), "önekli yazılan dosya gerçekten orada"


def test_uzun_yollu_tam_arsiv_windows_sinirinda_kuruluyor(tmp_path, windows_taklidi):
    """ESKİ HATA: Windows'ta (LongPathsEnabled kapalı, varsayılan) tam arşiv
    açılırken ilk uzun üyede `open()` ENOENT veriyordu → "yerel disk hatası"
    → "Tüm arşivi indir" hiç bitmiyordu (gerçek arşivde 280 dosya)."""
    goreli = _uzun_goreli()
    kayitlar = [{"player": "SIBNET", "url": "https://video.sibnet.ru/1"}]
    uyeler = arsiv_uyeleri("animedepo-master") + [
        _dosya(f"animedepo-master/{goreli}", json.dumps(kayitlar).encode())]
    hedef = tmp_path / "Users" / "Kullanici" / "Turkanime" / "cevrimdisi_arsiv"
    assert len(str(hedef / goreli)) > _MAX_PATH

    sonuc = paket.paketten_kur(SahteYanit(200, govde=tar_gz(uyeler)), hedef,
                               ust_desen="animedepo-master*")

    assert sonuc == hedef
    with open(paket.disk_yolu(hedef / goreli), encoding="utf-8") as fp:
        assert json.load(fp) == kayitlar
    assert _artiklar(hedef.parent) == [], "geçici klasör uzun yollarıyla silinmeli"

    # Güncelleme: eski kopya (uzun dosyalarıyla) kenara alınıp silinmeli.
    paket.paketten_kur(SahteYanit(200, govde=tar_gz(uyeler)), hedef,
                       ust_desen="animedepo-master*")
    assert _artiklar(hedef.parent) == [], "eski kopya uzun yollarıyla silinmeli"


def test_uzun_yollu_yerel_arsiv_ve_onbellek_windows_sinirinda_okunuyor(
        tmp_path, monkeypatch, windows_taklidi, ag_yasak):
    """Okuma tarafı: yerel arşivin ve disk önbelleğinin uzun adlı dosyaları
    "yok" görünmemeli (eskiden sessizce boş liste oluyordu)."""
    goreli = _uzun_goreli()
    depo = arsiv_yaz(tmp_path / "Users" / "Kullanici" / "Turkanime" / "arsiv")
    kayitlar = [{"player": "SIBNET", "fansub": "TAÇE",
                 "url": "https://video.sibnet.ru/shell.php?videoid=7"}]
    paket.atomik_bayt_yaz(depo / goreli, json.dumps(kayitlar).encode())
    assert len(str(depo / goreli)) > _MAX_PATH
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", depo)
    animedepo.sifirla()

    bolum_id = goreli[len("animeler/"):-len(".json")]
    akislar = animedepo.get_episode_streams(bolum_id)
    assert [a["url"] for a in akislar] == [kayitlar[0]["url"]]

    onbellek = tmp_path / "Users" / "Kullanici" / "Turkanime" / "arsiv_onbellek"
    monkeypatch.setattr(animedepo, "onbellek_dizini", lambda: onbellek)
    animedepo._onbellege_yaz(goreli, kayitlar)
    assert animedepo._onbellekten_oku(goreli) == kayitlar
