"""Çevrimdışı arşiv: konum çözümü, aynalar, disk önbelleği, tam arşiv indirme,
bakımcı eşitleme aracı ve akış (stream) kalitesi.

**Hiçbiri ağa çıkmaz ve 500 MB'lık `arsiv/`'i okumaz.** Arşivler `tmp_path`
altında birkaç dosyalık sahte ağaçlar; HTTP `animedepo._session` sahtesiyle
veriliyor (curl_cffi ağ mandalını atlattığı için sahteleme şart — bkz.
`conftest._arsiv_yalitimi`). Tek istisna en sondaki duman testi: gerçek
`arsiv/dizin.json` ile `arsiv/KAYNAK.json`'ın birbirini tuttuğunu ucuzca denetler.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import threading
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

    monkeypatch.setattr(animedepo.shutil, "rmtree", kilitli_rmtree)
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
