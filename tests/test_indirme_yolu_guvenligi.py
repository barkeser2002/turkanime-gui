"""İndirme yolu güvenliği — kaynaktan gelen slug dizin dolaşımı yapamaz.

Saldırı senaryosu (denetimden): AnimeDepo arşivinin `dizin.json`'ına ya da ele
geçirilmiş bir kaynak siteye `"../../../../evil-dir"` gibi bir anime slug'ı
konur. Kullanıcı o animenin bir bölümünü indirdiğinde yt-dlp'ye verilen
`outtmpl` indirme klasörünün DIŞINA çözülür ve dosya oraya yazılır.

Testler yt-dlp'yi sahteliyor ama **davranışını taklit ediyor**: verilen
`outtmpl`'in klasörlerini yaratıp dosyayı açıyor. Yani "yol güvenli mi"
sorusunu yolun metnine bakarak değil, diske gerçekten yazarak yanıtlıyoruz.

Hiçbiri gerçek kullanıcı dosyasına dokunmaz — hepsi `tmp_path` altında.
"""
from __future__ import annotations

import os

import pytest

from turkanime_api.common.dosya_adi import (
    AYRILMIS_ADLAR, alt_yolda_mi, guvenli_ad, guvenli_alt_yol,
)


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcı: yt-dlp'nin outtmpl davranışını taklit eden sahte
# ─────────────────────────────────────────────────────────────────────────────
class SahteYDL:
    """`outtmpl` neredeyse oraya yazar — gerçek yt-dlp de bunu yapıyor."""

    yazilanlar: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def download_with_info_file(self, _info_yolu):
        hedef = self.opts["outtmpl"]["default"].replace(".%(ext)s", ".mp4")
        os.makedirs(os.path.dirname(hedef) or ".", exist_ok=True)
        with open(hedef, "w", encoding="utf-8") as fp:
            fp.write("video")
        SahteYDL.yazilanlar.append(os.path.abspath(hedef))


@pytest.fixture
def sahte_ydl(monkeypatch):
    """`adapter` ve `objects` modüllerindeki YoutubeDL'i sahteyle değiştir."""
    from turkanime_api import objects as objects_mod
    from turkanime_api.sources import adapter as adapter_mod

    SahteYDL.yazilanlar = []
    monkeypatch.setattr(adapter_mod, "YoutubeDL", SahteYDL)
    monkeypatch.setattr(objects_mod, "YoutubeDL", SahteYDL)
    return SahteYDL


def _dosyalar(kok) -> list:
    return [os.path.join(k, d) for k, _, ds in os.walk(str(kok)) for d in ds]


# ─────────────────────────────────────────────────────────────────────────────
# 1) Dosya adı temizliği
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ham", [
    "../../../../evil-dir",
    "..\\..\\..\\evil-dir",
    "/etc/passwd",
    "C:\\Windows\\System32",
    "..",
    ".",
    "   ",
    "",
    None,
])
def test_guvenli_ad_ayirici_ve_dolasim_birakmaz(ham):
    ad = guvenli_ad(ham, yedek="yedek")
    assert ad, "ad boş kalamaz"
    assert "/" not in ad and "\\" not in ad
    assert ad not in (".", "..")
    assert os.path.basename(ad) == ad, "ad tek bir bileşen olmalı"
    assert not os.path.isabs(ad)


@pytest.mark.parametrize("ayrilmis", sorted(AYRILMIS_ADLAR))
def test_windows_ayrilmis_adlari_kacisliyor(ayrilmis):
    """CON/NUL/COM1... Windows'ta dosya değil aygıt adıdır; uzantılısı da."""
    assert guvenli_ad(ayrilmis) != ayrilmis
    assert guvenli_ad(ayrilmis.lower() + ".mp4") != ayrilmis.lower() + ".mp4"


def test_bosluk_ve_nokta_ile_biten_ad_duzeltiliyor():
    """Windows "ad." ve "ad " dosyasını yaratamaz/açamaz."""
    for ham in ("bolum.", "bolum ", "bolum..", "bolum . "):
        ad = guvenli_ad(ham)
        assert not ad.endswith((".", " ")), ad


def test_yasak_karakterler_temizleniyor():
    ad = guvenli_ad('bo<lum>:"1"|?*\x00')
    assert not set(ad) & set('<>:"/\\|?*')
    assert all(ord(c) >= 32 for c in ad)


def test_uzun_ad_kirpiliyor():
    assert len(guvenli_ad("a" * 500)) <= 120


# ─────────────────────────────────────────────────────────────────────────────
# 2) Sınır denetimi (temizliğe ek ikinci katman)
# ─────────────────────────────────────────────────────────────────────────────
def test_guvenli_alt_yol_daima_kokun_altinda(tmp_path):
    for slug in ("../../evil", "..\\..\\evil", "/etc", "C:\\Windows", ".."):
        yol = guvenli_alt_yol(tmp_path, slug, "bolum-1")
        assert alt_yolda_mi(tmp_path, yol), f"{slug!r} kökten kaçtı: {yol}"


def test_alt_yolda_mi_farkli_surucude_false(tmp_path):
    """Windows'ta farklı sürücüler ortak kök paylaşmaz; `commonpath` patlar."""
    assert alt_yolda_mi(tmp_path, tmp_path / "alt") is True
    assert alt_yolda_mi(tmp_path, os.path.join(str(tmp_path) + "-komsu", "x")) is False


def test_bos_kok_calisma_dizinine_cozuluyor(tmp_path, monkeypatch):
    """`Video.indir(output="")` varsayılanı: eskiden göreli yol üretiyordu."""
    monkeypatch.chdir(tmp_path)
    yol = guvenli_alt_yol("", "naruto", "naruto-1-bolum")
    assert os.path.isabs(yol)
    assert alt_yolda_mi(tmp_path, yol)


# ─────────────────────────────────────────────────────────────────────────────
# 3) Uçtan uca: AdapterVideo.indir (AnimeDepo/AnimeciX yolu)
# ─────────────────────────────────────────────────────────────────────────────
def _adapter_video(slug: str, indirilenler):
    from turkanime_api.sources.adapter import (
        AdapterAnime, AdapterBolum, AdapterVideo,
    )
    anime = AdapterAnime(slug=slug, title="Naruto")
    bolum = AdapterBolum(url="https://x.test/1", title="1. Bölüm", anime=anime)
    video = AdapterVideo(bolum, "https://x.test/v.mp4")
    video.is_working = True
    video._info = {"url": "https://x.test/v.mp4", "ext": "mp4"}
    return video


def test_adapter_indir_kotu_slugla_klasor_disina_yazmiyor(tmp_path, sahte_ydl):
    """Denetim senaryosunun birebir kendisi."""
    indirilenler = tmp_path / "a" / "b" / "c" / "d" / "Downloads"
    indirilenler.mkdir(parents=True)

    _adapter_video("../../../../evil-dir", indirilenler).indir(
        output=str(indirilenler))

    assert sahte_ydl.yazilanlar, "hiç indirme yapılmadı, test anlamsız"
    for yazilan in sahte_ydl.yazilanlar:
        assert alt_yolda_mi(indirilenler, yazilan), f"klasör dışına yazıldı: {yazilan}"
    assert not (tmp_path / "evil-dir").exists()
    assert not (tmp_path / "a" / "evil-dir").exists()
    assert len(_dosyalar(indirilenler)) == 1


def test_adapter_indir_normal_slug_davranisi_bozulmadi(tmp_path, sahte_ydl):
    """Temizlik meşru slug'ları değiştirmemeli — dosya adları kaymasın."""
    indirilenler = tmp_path / "Downloads"
    indirilenler.mkdir()

    _adapter_video("naruto-shippuuden", indirilenler).indir(output=str(indirilenler))

    beklenen = indirilenler / "naruto-shippuuden" / "naruto-1-bolum.mp4"
    assert beklenen.is_file(), _dosyalar(indirilenler)


def test_adapter_indir_mutlak_slug_ile_disari_yazmiyor(tmp_path, sahte_ydl):
    """Sürücü harfi / mutlak yol da bir kaçış vektörü."""
    indirilenler = tmp_path / "Downloads"
    indirilenler.mkdir()
    hedef = tmp_path / "kacak"

    _adapter_video(str(hedef), indirilenler).indir(output=str(indirilenler))

    assert not hedef.exists(), "mutlak slug indirme klasörünü atladı"
    assert all(alt_yolda_mi(indirilenler, y) for y in sahte_ydl.yazilanlar)


# ─────────────────────────────────────────────────────────────────────────────
# 4) Uçtan uca: objects.Video.indir (TürkAnime yolu)
# ─────────────────────────────────────────────────────────────────────────────
def test_objects_video_indir_dolasim_yapmiyor(tmp_path, sahte_ydl):
    """Aynı sınıf kusur: seri/bölüm slug'ı site HTML'inden regex ile geliyor."""
    from turkanime_api.objects import Video

    class SahteAnime:
        slug = "../../../evil"

    class SahteBolum:
        slug = "../../kotu-bolum"
        anime = SahteAnime()

    video = Video.__new__(Video)          # __init__ ağa/ayara dokunuyor
    video.bolum = SahteBolum()
    video.ydl_opts = {}
    video._is_working = True
    video._info = {"url": "https://x.test/v.mp4", "ext": "mp4"}
    video.is_supported = True
    video.player = "YADISK"

    indirilenler = tmp_path / "Downloads"
    indirilenler.mkdir()
    video.indir(output=str(indirilenler))

    assert sahte_ydl.yazilanlar
    for yazilan in sahte_ydl.yazilanlar:
        assert alt_yolda_mi(indirilenler, yazilan), yazilan
    assert not (tmp_path / "evil").exists()


# ─────────────────────────────────────────────────────────────────────────────
# 5) Arşiv yazıcısı (sunucu tarafı) — aynı sınıf kusur
# ─────────────────────────────────────────────────────────────────────────────
def test_arsiv_yazici_kok_disina_yazmiyor(tmp_path):
    """Slug bugün `[a-z0-9-]`; yazıcı yine de çağıranına güvenmemeli."""
    from turkanime_server.crawler.yazici import ArsivYazici

    kok = tmp_path / "arsiv"
    yazici = ArsivYazici(kok)
    yazici.info_yaz("../../evil", {"slug": "x"})
    yazici.bolumler_yaz("../../evil", [("s01e01", "1. Bölüm")])
    yazici.akis_yaz("naruto", "../../../kacak", [{"url": "https://x.test/a.mp4"}])

    for yazilan in _dosyalar(kok):
        assert alt_yolda_mi(kok, yazilan), f"arşiv kökünün dışına yazıldı: {yazilan}"
    assert not (tmp_path / "evil").exists()
    assert not (tmp_path / "kacak").exists()


def test_arsiv_yazici_normal_slug_yolu_degistirmiyor(tmp_path):
    """Mevcut arşiv düzeni kaymamalı: slug yolun parçası, kayması kırık link."""
    from turkanime_server.crawler.yazici import ArsivYazici

    kok = tmp_path / "arsiv"
    yazici = ArsivYazici(kok)
    yazici.anime_yaz("naruto", {"slug": "naruto"}, [("s01e01", "1. Bölüm")],
                     {"s01e01": [{"url": "https://x.test/a.mp4"}]})
    yazici.dizin_yaz([("naruto", "Naruto")])

    assert (kok / "dizin.json").is_file()
    assert (kok / "animeler" / "naruto" / "info.json").is_file()
    assert (kok / "animeler" / "naruto" / "bolumler.json").is_file()
    assert (kok / "animeler" / "naruto" / "s01e01.json").is_file()
