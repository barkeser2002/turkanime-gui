"""Oynatma/indirme geçici dosya temizliği, `--stream-record` ve `Bolum.anime`.

Üç ayrı kusuru birlikte kapatıyor, çünkü üçü de `objects.Video.oynat`/`indir`
yolunda yaşıyor ve aynı sahte altyapıyı paylaşıyor:

1. **`.info.json` sızıntısı.** yt-dlp ve mpv, info dosyasını *adıyla* açtığı
   için `delete=False` şart; silme işi bize kalıyor. `oynat()` bunu hiç
   yapmıyordu, `indir()` ise `try` bloğunun DIŞINDA yapıyordu — hata hâlinde
   dosya diskte kalıyordu. Doğru desen `sources/adapter.py`'de zaten vardı.
2. **Değersiz `--stream-record`.** mpv `--stream-record=<dosya>` istiyor.
   Bayrak komuta `cmd.insert(1,opt)` ile en başa giriyor, yani değersiz hâli
   bir sonraki argümanı yutabiliyordu.
3. **`Bolum.anime`.** Gövdesi `...` olan bir property kalıcı olarak None
   dönüyordu, oysa docstring "erişildiğinde yaratılır" diyordu.

Sızıntı, dosyanın metnine bakılarak değil geçici dizin GERÇEKTEN sayılarak
ölçülüyor: `tempfile.tempdir` boş bir dizine bağlanıyor, test sonunda o dizin
boş olmalı. Hiçbir test ağa çıkmaz ve gerçek kullanıcı dosyalarına dokunmaz.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from turkanime_api import objects as objects_mod
from turkanime_api.common.dosya_adi import alt_yolda_mi


# ─────────────────────────────────────────────────────────────────────────────
# Sahteler
# ─────────────────────────────────────────────────────────────────────────────
class SahteAnime:
    """`Anime` kurucusu ağa çıkıyor (fetch_info); testte yalnız slug gerekli."""

    def __init__(self, slug="naruto"):
        self.slug = slug


class SahteSp:
    """`subprocess` yerine geçer; çalıştırılan komutu saklar.

    Modüldeki `sp` adı komple değiştiriliyor: `subprocess.run`'ı global olarak
    yamamak test süresince başka her şeyi de etkilerdi.
    """

    PIPE = -1
    DEVNULL = -3

    def __init__(self, patlat=False):
        self.komutlar = []
        self._patlat = patlat

    def run(self, cmd, **_kw):
        self.komutlar.append(list(cmd))
        if self._patlat:
            raise RuntimeError("mpv çöktü")
        return "sonuc"

    @property
    def komut(self):
        assert self.komutlar, "mpv hiç çağrılmadı, test anlamsız"
        return self.komutlar[-1]


class SahteYDL:
    """`YoutubeDL` yerine geçer; istenirse indirme sırasında patlar."""

    patlat = False
    okunan_info = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def download_with_info_file(self, info_yolu):
        SahteYDL.okunan_info.append(info_yolu)
        assert os.path.isfile(info_yolu), "info dosyası indirme anında yok"
        if SahteYDL.patlat:
            raise RuntimeError("bağlantı koptu")
        hedef = self.opts["outtmpl"]["default"].replace(".%(ext)s", ".mp4")
        os.makedirs(os.path.dirname(hedef) or ".", exist_ok=True)
        with open(hedef, "w", encoding="utf-8") as fp:
            fp.write("video")


@pytest.fixture
def gecici_kok(tmp_path, monkeypatch):
    """`NamedTemporaryFile`ı boş bir dizine bağla ki sızıntı sayılabilsin."""
    kok = tmp_path / "gecici"
    kok.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(kok))
    return kok


@pytest.fixture
def sahte_sp(monkeypatch):
    """mpv'yi ve platform tespitini sabitle.

    `get_arch`/`get_platform` sabitlenmezse ARM makinede `oynat` Android
    dalına sapar ve geçici dosyaya hiç uğramaz — test sessizce anlamsızlaşırdı.
    """
    def _kur(patlat=False):
        sp = SahteSp(patlat=patlat)
        monkeypatch.setattr(objects_mod, "sp", sp)
        monkeypatch.setattr(objects_mod, "get_platform", lambda: "linux")
        monkeypatch.setattr(objects_mod, "get_arch", lambda: "x86_64")
        return sp
    return _kur


@pytest.fixture
def sahte_ydl(monkeypatch):
    def _kur(patlat=False):
        SahteYDL.patlat = patlat
        SahteYDL.okunan_info = []
        monkeypatch.setattr(objects_mod, "YoutubeDL", SahteYDL)
        return SahteYDL
    return _kur


def _video(anime=None, bolum_slug="naruto-1-bolum", url="https://x.test/v.mp4"):
    """Ağa hiç çıkmayan `Video` — `__init__` fetch/ayar okuyor, atlanıyor."""
    bolum = objects_mod.Bolum(bolum_slug, anime=anime)
    video = objects_mod.Video.__new__(objects_mod.Video)
    video.bolum = bolum
    video.path = "x"
    video.player = "YADISK"
    video.fansub = None
    video.is_supported = True
    video._is_working = True
    video._url = url
    video._info = {"url": url, "ext": "mp4"}
    video._resolution = 1080
    video.ydl_opts = {}
    return video


def _kalanlar(kok):
    return sorted(p.name for p in kok.iterdir())


# ─────────────────────────────────────────────────────────────────────────────
# 1) Geçici dosya sızıntısı
# ─────────────────────────────────────────────────────────────────────────────
def test_oynat_gecici_info_dosyasi_birakmiyor(gecici_kok, sahte_sp):
    """ESKİ HATA: `oynat()` temizlik HİÇ yapmıyordu; her oynatma bir dosya bıraktı."""
    sp = sahte_sp()

    _video(SahteAnime()).oynat()

    assert sp.komutlar, "mpv çağrılmadı, test anlamsız"
    assert _kalanlar(gecici_kok) == []


def test_oynat_mpv_cokse_bile_gecici_dosya_kalmiyor(gecici_kok, sahte_sp):
    """ESKİ HATA: temizlik yoktu; hata yolunda sızıntı kalıcıydı."""
    sahte_sp(patlat=True)

    with pytest.raises(RuntimeError):
        _video(SahteAnime()).oynat()

    assert _kalanlar(gecici_kok) == []


def test_oynat_gecici_dosya_mpv_komutuna_giriyor(gecici_kok, sahte_sp):
    """Temizlik erken silmeye dönüşmemeli: mpv dosyayı adıyla açıyor."""
    sp = sahte_sp()

    _video(SahteAnime()).oynat()

    yollar = [a.split("=", 2)[-1] for a in sp.komut
              if a.startswith("--ytdl-raw-options=load-info-json=")]
    assert len(yollar) == 1, sp.komut
    assert alt_yolda_mi(gecici_kok, yollar[0]), yollar[0]


def test_indir_hata_verse_bile_gecici_dosya_kalmiyor(gecici_kok, tmp_path, sahte_ydl):
    """ESKİ HATA: `remove` try DIŞINDAydı; indirme patlayınca dosya kalıyordu."""
    sahte_ydl(patlat=True)
    hedef = tmp_path / "Downloads"
    hedef.mkdir()

    with pytest.raises(RuntimeError):
        _video(SahteAnime()).indir(output=str(hedef))

    assert _kalanlar(gecici_kok) == []


def test_indir_basarili_yolda_da_temizliyor(gecici_kok, tmp_path, sahte_ydl):
    """Mevcut davranış korunuyor: başarı yolunda zaten siliniyordu."""
    sahte_ydl()
    hedef = tmp_path / "Downloads"
    hedef.mkdir()

    _video(SahteAnime()).indir(output=str(hedef))

    assert (hedef / "naruto" / "naruto-1-bolum.mp4").is_file()
    assert _kalanlar(gecici_kok) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2) --stream-record
# ─────────────────────────────────────────────────────────────────────────────
def _stream_record(komut):
    return [a for a in komut if a.startswith("--stream-record")]


def _indirilenler(izole_ev, slug_klasoru="indirilenler"):
    from turkanime_api.cli.dosyalar import Dosyalar
    kok = izole_ev / slug_klasoru
    Dosyalar().set_ayar("indirilenler", str(kok))
    return kok


def test_izlerken_kaydet_mpv_ye_dosya_adi_veriyor(izole_ev, gecici_kok, sahte_sp):
    """ESKİ HATA: bayrak değersiz geçiliyordu; mpv `--stream-record=<dosya>` ister."""
    sp = sahte_sp()
    kok = _indirilenler(izole_ev)

    _video(SahteAnime()).oynat(izlerken_kaydet=True)

    beklenen = os.path.join(str(kok), "naruto", "naruto-1-bolum.mkv")
    assert _stream_record(sp.komut) == ["--stream-record=" + beklenen]


def test_izlerken_kaydet_degersiz_bayrak_birakmiyor(izole_ev, gecici_kok, sahte_sp):
    """ESKİ HATA: çıplak `--stream-record`, bir sonraki argümanı yutabiliyordu."""
    sp = sahte_sp()
    _indirilenler(izole_ev)

    _video(SahteAnime()).oynat(izlerken_kaydet=True)

    assert "--stream-record" not in sp.komut
    assert "--no-input-terminal" in sp.komut


def test_izlerken_kaydet_hedef_klasoru_aciliyor(izole_ev, gecici_kok, sahte_sp):
    """mpv var olmayan klasöre yazamaz; kaydı sessizce düşürürdü."""
    sp = sahte_sp()
    _indirilenler(izole_ev)

    _video(SahteAnime()).oynat(izlerken_kaydet=True)

    hedef = _stream_record(sp.komut)[0].split("=", 1)[1]
    assert os.path.isdir(os.path.dirname(hedef))


def test_izlerken_kaydet_kapaliyken_bayrak_eklenmiyor(izole_ev, gecici_kok, sahte_sp):
    """Varsayılan kapalı; kayıt istemeyen kullanıcıya disk yazımı olmamalı."""
    sp = sahte_sp()
    _indirilenler(izole_ev)

    _video(SahteAnime()).oynat()

    assert _stream_record(sp.komut) == []


def test_izlerken_kaydet_kotu_slugla_klasor_disina_yazmiyor(izole_ev, gecici_kok,
                                                            sahte_sp):
    """Seri slug'ı sitenin HTML'inden geliyor; kayıt yolu da dolaşıma açıktı."""
    sp = sahte_sp()
    kok = _indirilenler(izole_ev)

    _video(SahteAnime("../../../evil")).oynat(izlerken_kaydet=True)

    hedef = _stream_record(sp.komut)[0].split("=", 1)[1]
    assert alt_yolda_mi(kok, hedef), hedef
    assert not (izole_ev.parent / "evil").exists()


def test_izlerken_kaydet_hedef_kurulamazsa_oynatma_devam_ediyor(izole_ev, gecici_kok,
                                                                sahte_sp, monkeypatch):
    """Kayıt yan işlev: klasör açılamıyorsa oynatmayı düşürmemeli."""
    sp = sahte_sp()
    _indirilenler(izole_ev)
    monkeypatch.setattr(objects_mod, "kayit_hedefi",
                        lambda _b: (_ for _ in ()).throw(OSError("izin yok")))

    _video(SahteAnime()).oynat(izlerken_kaydet=True)

    assert _stream_record(sp.komut) == []
    assert "ytdl://naruto-1-bolum" in sp.komut


# ─────────────────────────────────────────────────────────────────────────────
# 3) Bolum.anime
# ─────────────────────────────────────────────────────────────────────────────
def test_bolum_anime_verilmediginde_none_donuyor():
    """ESKİ HATA: gövde `...` iken docstring "erişildiğinde yaratılır" diyordu."""
    assert objects_mod.Bolum("naruto-1-bolum").anime is None


def test_bolum_anime_verildiginde_ayni_obje_donuyor():
    """Kurucuya verilen seri korunmalı — indirme yolu bunun slug'ını kullanıyor."""
    anime = SahteAnime()
    assert objects_mod.Bolum("naruto-1-bolum", anime=anime).anime is anime


def test_bolum_anime_erisimi_aga_cikmiyor(monkeypatch):
    """Tembel yaratmak `bolum.anime.slug`ı sessiz bir HTTP isteğine çevirirdi."""
    def _patla(*_a, **_k):
        raise AssertionError("Bolum.anime ağa çıktı")

    monkeypatch.setattr(objects_mod, "fetch", _patla)
    assert objects_mod.Bolum("naruto-1-bolum").anime is None


def test_indir_animesiz_bolumde_seri_klasoru_acmiyor(gecici_kok, tmp_path, sahte_ydl):
    """`anime` None iken seri klasörü boş kalıyor — belgelenen davranış."""
    sahte_ydl()
    hedef = tmp_path / "Downloads"
    hedef.mkdir()

    _video(anime=None).indir(output=str(hedef))

    assert (hedef / "naruto-1-bolum.mp4").is_file()
