"""Sunucu import zinciri ağır istemci bağımlılıklarını sürüklemesin.

`turkanime_api/__init__.py` eskiden koşulsuz `objects` (→ yt_dlp, 71 modül) ve
`bypass` (→ appdirs, Crypto) çekiyordu. Python paket import ederken önce
`__init__.py`'yi koşturduğu için, sunucu tarayıcısının kullandığı
`turkanime_api.common.episode_parser` gibi saf bir modülü import etmek bile tüm
indirme yığınını yüklüyordu. Sonuç: video indirmeyen tarayıcı konteyneri
yt-dlp/appdirs/pycryptodome kurmak zorundaydı ve her süreç ~0.5 sn bunu ödüyordu.

Bu testler zincirin tembel kaldığını doğruluyor. `sys.modules` içeriğine
bakmak zorunlu: paket kurulu olduğu için `import` çalışır — asıl soru
*çalışıp çalışmadığı* değil, *yüklenip yüklenmediği*.

Her kontrol ayrı bir alt süreçte koşuyor; aynı yorumlayıcıda test sırası
`sys.modules`'u kirletir ve sonuç sıraya bağlı hâle gelirdi.
"""
import subprocess
import sys
from pathlib import Path

import pytest

DEPO = Path(__file__).resolve().parent.parent

# Sunucunun istemciden miras almaması gereken paketler.
YASAKLI = ("yt_dlp", "appdirs", "Crypto")

# `app.py` listede yok: flask_cors/mysql-connector gerektiriyor, bunlar test
# ortamında kurulu değil. Kapsamı `turkanime_api` uçlarıyla dolaylı koruyoruz.
SUNUCU_MODULLERI = [
    "turkanime_server.crawler.tarayici",
    "turkanime_server.crawler.kaynaklar",
    "turkanime_server.crawler.nezaket",
    "turkanime_server.crawler.eslestirme",
    "turkanime_server.crawler.yazici",
    "turkanime_server.crawler.__main__",
    "turkanime_server.yayinci.yayinci",
    "turkanime_server.yayinci.depo",
    "turkanime_server.yayinci.__main__",
]

# Sunucu bunları doğrudan import ediyor (app.py + kaynaklar.py).
ISTEMCI_MODULLERI = [
    "turkanime_api",
    "turkanime_api.sources",
    "turkanime_api.sources.anizle",
    "turkanime_api.sources.openani",
    "turkanime_api.sources.animecix",
    "turkanime_api.sources.tranime",
    "turkanime_api.sources.tranimaci",
    "turkanime_api.sources.animedepo",
    "turkanime_api.common.episode_parser",
    "turkanime_api.common.title_match",
]


def _yuklu_paketler(modul: str) -> set:
    """`modul` import edildikten sonra sys.modules'a giren yasaklı kökler."""
    kod = (
        "import sys\n"
        f"import {modul}\n"
        "print(' '.join(sorted({m.split('.')[0] for m in sys.modules}"
        f" & set({YASAKLI!r}))))\n"
    )
    r = subprocess.run([sys.executable, "-c", kod], cwd=DEPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"{modul} import edilemedi:\n{r.stderr}"
    return set(r.stdout.split())


@pytest.mark.parametrize("modul", SUNUCU_MODULLERI)
def test_sunucu_modulu_indirme_yiginini_cekmiyor(modul):
    """Sunucu tarafı hiçbir modül yt-dlp/appdirs/Crypto yüklememeli."""
    sizinti = _yuklu_paketler(modul)
    assert not sizinti, (
        f"{modul} şunları sürükledi: {sorted(sizinti)}. "
        "turkanime_server/requirements.txt bunları kurmuyor — konteyner "
        "ImportError ile çıkar. Paketi geri eklemek yerine sızıntıyı bul."
    )


@pytest.mark.parametrize("modul", ISTEMCI_MODULLERI)
def test_kaynak_adaptorleri_indirme_yiginini_cekmiyor(modul):
    """Kaynak adaptörleri arama/bölüm için yt-dlp'ye ihtiyaç duymamalı."""
    sizinti = _yuklu_paketler(modul)
    assert not sizinti, f"{modul} şunları sürükledi: {sorted(sizinti)}"


def test_paket_yuzeyi_korunuyor():
    """Tembelleştirme `from turkanime_api import ...` sözleşmesini bozmamalı."""
    kod = (
        "import sys, turkanime_api\n"
        "once = any(m.startswith('yt_dlp') for m in sys.modules)\n"
        "from turkanime_api import Anime, Bolum, Video, session\n"
        "sonra = any(m.startswith('yt_dlp') for m in sys.modules)\n"
        "adlar = all(a in dir(turkanime_api) for a in "
        "('Anime','Bolum','Video','session'))\n"
        "print(int(once), int(sonra), int(adlar), Anime.__name__, Bolum.__name__)\n"
    )
    r = subprocess.run([sys.executable, "-c", kod], cwd=DEPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    once, sonra, adlar, anime_ad, bolum_ad = r.stdout.split()
    assert once == "0", "paketi import etmek hâlâ yt_dlp çekiyor"
    assert sonra == "1", "isim erişilince yt_dlp yüklenmeli (tembel, kayıp değil)"
    assert adlar == "1", "dir() eski yüzeyi göstermeli"
    assert (anime_ad, bolum_ad) == ("Anime", "Bolum")


def test_bilinmeyen_ad_attributeerror():
    """Tembel `__getattr__` her adı yutup ImportError'a çevirmemeli."""
    import turkanime_api
    with pytest.raises(AttributeError, match="YokBoyleBirSey"):
        turkanime_api.YokBoyleBirSey  # pylint: disable=pointless-statement


def test_openani_fabrikalari_hala_calisiyor():
    """`Anime`/`Bolum` metot içine taşındı — üretim hâlâ çalışmalı."""
    from turkanime_api.sources.openani import OpenAniAdapter
    adaptor = OpenAniAdapter()
    anime = adaptor.create_anime_object({
        "provider_data": {"slug": "one-piece"},
        "title": "One Piece", "description": "özet", "episodes": 1100,
    })
    assert type(anime).__name__ == "Anime"
    assert anime.title == "One Piece"
    assert anime.info["Bölüm Sayısı"] == 1100

    bolum = adaptor.create_episode_object(
        {"provider_data": {"episode_id": "b1"}, "title": "Bölüm 1",
         "episode_number": 1}, anime)
    assert type(bolum).__name__ == "Bolum"
    assert bolum.title == "Bölüm 1"


def test_requirements_yasakli_paketleri_geri_getirmedi():
    """requirements.txt sessizce eski hâline dönmesin."""
    metin = (DEPO / "turkanime_server" / "requirements.txt").read_text(encoding="utf-8")
    satirlar = [s.strip().lower() for s in metin.splitlines()
                if s.strip() and not s.strip().startswith("#")]
    for paket in ("yt-dlp", "yt_dlp", "appdirs", "pycryptodome"):
        assert not any(s.startswith(paket) for s in satirlar), (
            f"{paket} requirements.txt'e geri eklenmiş; import zinciri yeniden "
            "sızmış olabilir — önce testleri koştur."
        )
