"""DISCLAIMER'daki adresler koddaki gerçek adreslerle aynı kalsın.

NEDEN ÖNEMLİ: o tablo, hak sahibinin DMCA bildirimini nereye göndereceğini
söylüyor. Yanlış alan adı yazmak, bildirimi hiç göndermemekle aynı kapıya
çıkar — üstelik proje "bildiriminizi ilgili siteye yöneltin" diyerek okuyucuyu
tam olarak o tabloya yönlendiriyor.

Ölçülen uyuşmazlık: tabloda `tranimeizle.co` yazıyordu, `sources/tranime.py`
ise `https://www.tranimeizle.io` kullanıyor. Site alan adı değiştirmiş, belge
geride kalmıştı. AnimeciX'in API konağı (`mangacix.net`) ve Anizle'nin API/
oynatıcı konakları (`anizle.org`, `anizmplayer.com`) da tabloda hiç yoktu;
oysa istek gerçekten oralara gidiyor.

Testler kaynak koddaki `BASE_URL`/`ALT_URL`/`API_BASE_URL`/`PLAYER_BASE_URL`
sabitlerini okuyup tabloda geçtiklerini doğruluyor. Kaynak taşınırsa test
belgeyi güncellemeye zorlar.
"""
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

KOK = Path(__file__).resolve().parent.parent
KAYNAK_DIZIN = KOK / "turkanime_api" / "sources"
BELGELER = [KOK / "DISCLAIMER.md", KOK / "docs" / "DISCLAIMER.md"]

# Tabloya girmesi gereken sabitler. Bunlar kullanıcının isteğinin gerçekten
# gittiği konaklar; "şu siteye erişiyoruz" cümlesinin karşılığı.
SABITLER = ("BASE_URL", "ALT_URL", "API_BASE_URL", "PLAYER_BASE_URL")

# Kaynak sitesi olmayan modüller. `adapter` bir uyarlayıcı, `animedepo`
# GitLab'daki statik arşiv (tabloda ayrı satırı var, ham URL'i değil depo
# adresi yazılı).
HARIC = {"adapter", "__init__", "animedepo"}


def _kaynak_konaklari():
    """`sources/*.py` içindeki sabitlerden (modül, konak) çiftleri."""
    for yol in sorted(KAYNAK_DIZIN.glob("*.py")):
        if yol.stem in HARIC:
            continue
        metin = yol.read_text(encoding="utf-8")
        for sabit in SABITLER:
            m = re.search(rf'^{sabit}\s*=\s*["\']([^"\']+)["\']',
                          metin, re.M)
            if not m:
                continue
            konak = urlsplit(m.group(1)).hostname
            if konak:
                yield yol.stem, sabit, konak


def test_kaynak_konaklari_bulunuyor():
    """Test kendini kandırmasın: hiç konak bulamazsa boşuna yeşil olur."""
    konaklar = list(_kaynak_konaklari())
    assert len(konaklar) >= 5, f"yalnızca {len(konaklar)} konak bulundu: {konaklar}"


@pytest.mark.parametrize("belge", BELGELER, ids=lambda p: p.parent.name or "kok")
def test_her_kaynak_konagi_disclaimerda_geciyor(belge):
    """Koddaki her konak belgede yazılı olmalı."""
    metin = belge.read_text(encoding="utf-8")
    eksik = [f"{mod}.{sabit} → {konak}"
             for mod, sabit, konak in _kaynak_konaklari()
             if konak not in metin]
    assert not eksik, (
        f"{belge.name}: kod bu konaklara istek atıyor ama belge onları "
        "saymıyor; DMCA bildirimi yanlış yere gider:\n  " + "\n  ".join(eksik))


@pytest.mark.parametrize("belge", BELGELER, ids=lambda p: p.parent.name or "kok")
def test_artik_kullanilmayan_alan_adi_belgede_kalmadi(belge):
    """Site taşınınca eski adres belgede kalmamalı.

    Ölçülen örnek: `tranimeizle.co`. Kod `.io` kullanıyor; `.co` başkasının
    eline geçmiş olabilir ve bildirim oraya gider.
    """
    metin = belge.read_text(encoding="utf-8")
    kod_konaklari = {k for _, _, k in _kaynak_konaklari()}
    olu = []
    for eski in ("tranimeizle.co", "anizle.com", "turkanime.tv"):
        if eski in metin and eski not in kod_konaklari:
            # "tranimeizle.co" dizgisi "tranimeizle.com" içinde de geçebilir;
            # sınır kontrolü yap.
            if re.search(rf"{re.escape(eski)}(?![\w.-])", metin):
                olu.append(eski)
    assert not olu, f"{belge.name}: kodda olmayan eski adres duruyor: {olu}"


def test_iki_disclaimer_kopyasi_ayni():
    """README gibi DISCLAIMER de iki yerde; biri güncellenip öteki unutulmasın."""
    a, b = (p.read_text(encoding="utf-8") for p in BELGELER)
    # Tek meşru fark bağıl bağlantı yolları (kökten `docs/`, docs içinden düz).
    a_n = re.sub(r"\]\(docs/", "](", a)
    b_n = re.sub(r"\]\(\.\./", "](", b)
    assert a_n == b_n, "DISCLAIMER kopyaları ayrışmış"
