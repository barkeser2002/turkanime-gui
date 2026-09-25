"""Canlı ağ duman testleri — varsayılan koşuda ATLANIR.

    python -m pytest --network -m network

`conftest.py`'deki `--network` seçeneği, ağ mandalı ve `network` işareti
hazırdı ama işaretli tek bir test yoktu: `--collect-only -m network` "no tests
collected" diyordu. Ağsız paket sahte yanıtlarla koşuyor; uzaktaki bir aynanın
ölmesini (yol değişti, depo taşındı) yalnızca bu testler görür.

Ölçülmüş örnek: GitHub aynası (`GITHUB_AYNA_URL`, `main` dalındaki `arsiv/`)
arşiv `main`'e birleşene kadar 404 döndü; GitLab aynası 200. Bu dosya o
durumda GitHub parametresinde KIRMIZI olur — olması gereken de bu.

Kaynak siteleri (Anizle, AnimeciX…) burada değil: onlar için elle koşulan
`tests/adapters-test-all.py` var. Burası yalnızca uygulamanın ağsız
çalışmasını sağlayan arşiv aynaları ile meta veri servisi.
"""
from __future__ import annotations

import pytest

from turkanime_api.sources import animedepo

pytestmark = pytest.mark.network

AYNALAR = {
    "gitlab": animedepo.BASE_URL,
    "github": animedepo.GITHUB_AYNA_URL,
}


def test_uzak_aynalar_iki_yedegi_de_iceriyor():
    """Özel adres ne olursa olsun GitLab ve GitHub yedekleri sırada kalmalı."""
    aynalar = animedepo.uzak_aynalar()
    assert set(AYNALAR.values()) <= set(aynalar), aynalar


@pytest.mark.parametrize("kok", list(AYNALAR.values()), ids=list(AYNALAR))
def test_arsiv_aynasi_dizini_veriyor(kok):
    """Ayna `dizin.json`'u 200 ile ve binlerce animeyle vermeli."""
    yanit = animedepo._session().get(f"{kok}/dizin.json", timeout=20)
    assert yanit.status_code == 200, f"{kok}/dizin.json → HTTP {yanit.status_code}"
    ciftler = animedepo._anime_ciftleri(yanit.json())
    assert len(ciftler) > 1000, f"{kok}: yalnızca {len(ciftler)} anime"


def test_anilist_aramasi_sonuc_veriyor():
    from turkanime_api.anilist_client import anilist_client

    sonuclar = anilist_client.search_anime("Cowboy Bebop", per_page=3)
    assert sonuclar, "AniList araması boş döndü"
