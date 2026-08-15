"""Bölüm parser'ı + çok kaynaklı birleştirme (Faz 5).

Bu dosyadaki hiçbir test Qt ya da ağ gerektirmez: `common/episode_parser.py`
saf Python ve hem CLI hem Qt arayüzünün ORTAK kaynağı. Buradaki beklentiler
bozulursa bölüm listesi her iki tarafta birden bozulur.
"""
from __future__ import annotations

import pytest

from turkanime_api.common.episode_parser import (
    episode_key, extract_episode_info, extract_episode_number, merge_episodes,
    normalize_episode_title, parse_episode,
)


def ep(title: str, **extra):
    """Kaynak adapter'larının ürettiği kayıt şekli: başlık + oynatma nesnesi."""
    entry = {"title": title, "obj": object()}
    entry.update(extra)
    return entry


# ── Parser ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("title, expected", [
    ("S02E05", (2, 5)),
    ("s02e05", (2, 5)),
    ("S1E2", (1, 2)),
    ("S01.E02", (1, 2)),
    ("2x05", (2, 5)),
    ("1x10", (1, 10)),
    ("2. Sezon 5. Bölüm", (2, 5)),
    ("2.Sezon 5.Bolum", (2, 5)),
    ("Sezon 2 Bölüm 5", (2, 5)),
    ("Sezon 3 - 12. Bölüm", (3, 12)),
    ("2. Sezon Bölüm 7", (2, 7)),
    ("Bölüm 5", (1, 5)),
    ("5. Bölüm", (1, 5)),
    ("Cowboy Bebop 12. Bölüm", (1, 12)),
    ("Episode 9", (1, 9)),
    ("EP 4", (1, 4)),
    ("01", (1, 1)),
    ("12 - The Real Folk Blues", (1, 12)),
])
def test_extract_episode_info_formats(title, expected):
    assert extract_episode_info(title) == expected


# ── Anime adı başlığın içinde ───────────────────────────────────────────────
# Kaynaklar başlığa anime adını da yazıyor ("3x3 Eyes 1. Bölüm"). Addaki
# rakamlar bölüm/sezon sanılınca bölümler aynı anahtara düşüp birbirini yiyordu.
@pytest.mark.parametrize("title, expected", [
    ("3x3 Eyes 1. Bölüm", (1, 1)),
    ("3x3 Eyes 4. Bölüm Final", (1, 4)),
    ("3x3 Eyes: Seima Densetsu 2. Bölüm", (1, 2)),
    ("5-toubun no Hanayome 3. Bölüm", (1, 3)),
    ("100-man no Inochi no Ue ni Ore wa Tatteiru 7. Bölüm", (1, 7)),
    ("12-sai: Chicchana Mune no Tokimeki 2. Bölüm", (1, 2)),
    ("7 Seeds Bölüm 9", (1, 9)),
    ("Ranma 1/2 10. Bölüm", (1, 10)),
])
def test_addaki_rakam_bolum_sanilmiyor(title, expected):
    assert extract_episode_info(title) == expected


@pytest.mark.parametrize("title, expected", [
    ("86 2nd Season 5. Bölüm", (2, 5)),
    ("Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season 4. Bölüm", (2, 4)),
    ("Aggressive Retsuko (ONA) 3rd Season 6. Bölüm", (3, 6)),
    ("Shingeki no Kyojin 4th Season Bölüm 11", (4, 11)),
    ("Arcane Season 2 - 3. Bölüm", (2, 3)),
    ("1st Season 8. Bölüm", (1, 8)),
])
def test_ingilizce_sira_sayili_sezon(title, expected):
    """"2nd/3rd/4th Season" hiçbir kalıpta yoktu; sezon yerine bölüm okunuyordu."""
    assert extract_episode_info(title) == expected


def test_anime_adi_verilince_ayikliniyor():
    """Ad biliniyorsa sezon da anime kimliğinin parçası: anahtar (1, 5) olur.

    Aksi hâlde "86 2nd Season 5. Bölüm" (2,5), çıplak "5. Bölüm" (1,5) anahtarına
    düşer ve aynı bölüm iki kaynakta iki ayrı satır olur.
    """
    assert extract_episode_info("86 2nd Season 5. Bölüm", "86 2nd Season") == (1, 5)
    assert extract_episode_info("3x3 Eyes 1. Bölüm", "3x3 Eyes") == (1, 1)
    # Slug da kabul: sözcükler arası noktalama esnek eşleşiyor
    assert extract_episode_info("3x3 Eyes 1. Bölüm", "3x3-eyes") == (1, 1)
    assert extract_episode_info(
        "Sword Art Online Alternative: Gun Gale Online 12. Bölüm Final",
        "sword-art-online-alternative-gun-gale-online") == (1, 12)


def test_anime_adi_bolum_isaretini_yemiyor():
    """Ad ile işaret aynı metin olabilir: "86" animesinin "86. Bölüm"ü."""
    assert extract_episode_info("86. Bölüm", "86") == (1, 86)
    assert extract_episode_info("Bölüm 3", "Bölüm") == (1, 3)


def test_alakasiz_ad_baslige_dokunmuyor():
    assert extract_episode_info("12. Bölüm", "Cowboy Bebop") == (1, 12)


# ── Ara bölüm ("5.5. Bölüm") ────────────────────────────────────────────────
def test_ara_bolum_parse_ediliyor():
    bilgi = parse_episode("12.5. Bölüm")
    assert (bilgi.episode, bilgi.sub) == (12, 5)
    assert parse_episode("Bölüm 5.5").sub == 5
    assert parse_episode("5. Bölüm").sub is None


def test_sezonsuz_basliklar_birinci_sezona_dusuyor():
    """Sezon bilgisi yoksa 1 varsayılır; aksi hâlde anahtar hiç oluşmazdı."""
    assert extract_episode_info("Bölüm 3")[0] == 1
    assert extract_episode_info("3. Bölüm")[0] == 1


def test_numarasiz_basliklar_sifir_dondurur():
    """Numara yoksa 0: birleştirme bunu görüp index'e düşer."""
    assert extract_episode_info("Movie") == (1, 0)
    assert extract_episode_info("") == (1, 0)
    assert extract_episode_info(None) == (1, 0)


def test_numarasiz_basliktaki_ilk_sayi_bolum_sayilir():
    """Eski GUI davranışı: "OVA 2" gibi başlıklarda ilk sayı bölüm sayılır."""
    assert extract_episode_info("OVA 2") == (1, 2)


def test_extract_episode_number_sarmalayicisi():
    assert extract_episode_number("S02E05") == 5
    assert extract_episode_number("Movie") == 0


def test_parse_episode_etiketleri_koruyor():
    """Zengin API kaybolmadı: server tarafı `label`/`score` kullanıyor."""
    assert parse_episode("Movie").label == "movie"
    assert parse_episode("Bölüm 12 Final").label == "final"
    assert parse_episode("S02E05").normalized() == "S02E05"


def test_normalize_episode_title():
    assert normalize_episode_title("Cowboy Bebop 12. Bölüm", 12, 1) == "12. Bölüm"
    assert normalize_episode_title("Bölüm 5", 5, 1) == "Bölüm 5"
    assert normalize_episode_title("2. Sezon 5. Bölüm", 5, 2) == "S02E05"
    assert normalize_episode_title("", 3, 1) == "3. Bölüm"
    assert normalize_episode_title("3x3 Eyes 4. Bölüm Final", 4, 1) == "4. Bölüm Final"


def test_normalize_episode_title_ara_bolum_kesri_koruyor():
    """Etiket "5. Bölüm" olursa kullanıcı iki satırı birbirinden ayıramaz."""
    assert normalize_episode_title("GGO 5.5. Bölüm", 5, 1, 5) == "5.5. Bölüm"
    assert normalize_episode_title("", 5, 1, 5) == "5.5. Bölüm"
    assert normalize_episode_title("2. Sezon 5.5. Bölüm", 5, 2, 5) == "S02E05.5"


def test_episode_key_alan_degerine_guveniyor():
    """Adapter numarayı alan olarak veriyorsa başlığı parse etmeye gerek yok."""
    assert episode_key({"title": "Karışık başlık", "number": 7}) == (1, 7)
    assert episode_key({"title": "x", "episode_number": 3, "season_number": 2}) == (2, 3)
    # Numara alanı var ama sezon yok: sezon başlıktan tamamlanmalı
    assert episode_key({"title": "2. Sezon 4. Bölüm", "number": 4}) == (2, 4)


def test_episode_key_anime_adini_kabul_ediyor():
    assert episode_key({"title": "86 2nd Season 5. Bölüm"}, 0, "86 2nd Season") == (1, 5)
    assert episode_key({"title": "86 2nd Season 5. Bölüm"}) == (2, 5)


# ── Birleştirme ─────────────────────────────────────────────────────────────
def test_ayni_bolum_uc_kaynakta_tek_satir():
    merged = merge_episodes({
        "TürkAnime": [ep("Cowboy Bebop 1. Bölüm"), ep("Cowboy Bebop 2. Bölüm")],
        "AnimeciX": [ep("1. Bölüm"), ep("2. Bölüm")],
        "Anizle": [ep("Bölüm 1"), ep("Bölüm 2")],
    })
    assert len(merged) == 2
    assert set(merged[0]["sources"]) == {"TürkAnime", "AnimeciX", "Anizle"}
    assert set(merged[1]["sources"]) == {"TürkAnime", "AnimeciX", "Anizle"}
    assert (merged[0]["season"], merged[0]["number"]) == (1, 1)


def test_farkli_numaralandirma_semalari_hizaliniyor():
    """S02E05 / 2x05 / "2. Sezon 5. Bölüm" aynı bölümdür."""
    merged = merge_episodes({
        "TürkAnime": [ep("Anime 2. Sezon 5. Bölüm")],
        "AnimeciX": [ep("S02E05")],
        "Anizle": [ep("2x05")],
    })
    assert len(merged) == 1
    assert (merged[0]["season"], merged[0]["number"]) == (2, 5)
    assert len(merged[0]["sources"]) == 3


def test_farkli_sezonlar_ayri_satir():
    merged = merge_episodes({
        "TürkAnime": [ep("S01E05"), ep("S02E05")],
    })
    assert [(m["season"], m["number"]) for m in merged] == [(1, 5), (2, 5)]


def test_numarasizlar_kaybolmuyor():
    """Parse edilemeyen başlıklar index'e düşer ama listeden SİLİNMEZ."""
    merged = merge_episodes({"OpenAnime": [ep("Movie"), ep("Special")]})
    assert len(merged) == 2
    assert [m["number"] for m in merged] == [1, 2]
    assert [next(iter(m["sources"].values()))["title"] for m in merged] == \
        ["Movie", "Special"]


def test_siralama_sayisal():
    """10 vs 2: metinsel sıralama hatası olmamalı."""
    merged = merge_episodes({
        "TürkAnime": [ep("10. Bölüm"), ep("2. Bölüm"), ep("1. Bölüm"),
                      ep("21. Bölüm"), ep("3. Bölüm")],
    })
    assert [m["number"] for m in merged] == [1, 2, 3, 10, 21]


def test_siralama_once_sezon_sonra_bolum():
    merged = merge_episodes({
        "TürkAnime": [ep("S02E01"), ep("S01E10"), ep("S01E02")],
    })
    assert [(m["season"], m["number"]) for m in merged] == [(1, 2), (1, 10), (2, 1)]


def test_ayni_kaynakta_cakisan_anahtarda_ilk_kayit_korunur():
    """"5. Bölüm" ve "5. Bölüm 2. Kısım" aynı anahtara düşer; asıl bölüm kalmalı."""
    merged = merge_episodes({
        "TürkAnime": [ep("5. Bölüm"), ep("5. Bölüm 2. Kısım")],
    })
    assert len(merged) == 1
    assert merged[0]["sources"]["TürkAnime"]["title"] == "5. Bölüm"


# ── Anime adı başlıkta: veri kaybı regresyonları ────────────────────────────
def test_addaki_rakam_bolumleri_yemiyor():
    """AnimeDepo "3x3-eyes": dört bölümün dördü de (3,3) anahtarına düşüyordu.

    Hayatta kalan tek satır "S03E03" etiketliydi ve 1. bölümü oynatıyordu.
    """
    basliklar = ["3x3 Eyes 1. Bölüm", "3x3 Eyes 2. Bölüm",
                 "3x3 Eyes 3. Bölüm", "3x3 Eyes 4. Bölüm Final"]
    kayitlar = [ep(t) for t in basliklar]

    merged = merge_episodes({"AnimeDepo": kayitlar}, "3x3 Eyes")
    assert [(m["season"], m["number"]) for m in merged] == [(1, n) for n in range(1, 5)]
    assert [m["sources"]["AnimeDepo"]["title"] for m in merged] == basliklar
    # Anime adı bilinmese de kayıp olmamalı: kalıp tablosu işaret sözcüğünü
    # addaki rakamlardan önce sayıyor.
    assert len(merge_episodes({"AnimeDepo": kayitlar})) == 4


def test_ingilizce_sezonlu_ad_kaynaklari_ayirmiyor():
    """AnimeDepo "86 2nd Season …" + Anizle "…" → 24 değil 12 satır olmalı."""
    ad = "86 2nd Season"
    merged = merge_episodes({
        "AnimeDepo": [ep(f"{ad} {n}. Bölüm") for n in range(1, 13)],
        "Anizle": [ep(f"{n}. Bölüm") for n in range(1, 13)],
        "TürkAnime": [ep(f"Bölüm {n}") for n in range(1, 13)],
    }, ad)
    assert len(merged) == 12
    assert [m["number"] for m in merged] == list(range(1, 13))
    assert all(len(m["sources"]) == 3 for m in merged)


def test_ara_bolum_kendi_satirinda_kaliyor():
    """"5.5. Bölüm" 5. bölümün anahtarına düşüp sessizce siliniyordu."""
    ad = "Sword Art Online Alternative: Gun Gale Online"
    basliklar = [f"{ad} 5. Bölüm", f"{ad} 5.5. Bölüm", f"{ad} 6. Bölüm"]
    merged = merge_episodes({"AnimeDepo": [ep(t) for t in basliklar]}, ad)

    assert [(m["number"], m["sub"]) for m in merged] == [(5, 0), (5, 5), (6, 0)]
    assert merged[1]["title"] == "5.5. Bölüm"
    assert merged[1]["sources"]["AnimeDepo"]["title"] == f"{ad} 5.5. Bölüm"


def test_ara_bolum_kaynaklar_arasi_birlesiyor():
    merged = merge_episodes({
        "AnimeDepo": [ep("GGO 5.5. Bölüm")],
        "Anizle": [ep("5.5. Bölüm")],
    }, "GGO")
    assert len(merged) == 1
    assert len(merged[0]["sources"]) == 2


def test_bos_ve_bozuk_girdiler_yutuluyor():
    merged = merge_episodes({
        "TürkAnime": [],
        "AnimeciX": None,
        "Anizle": [ep("1. Bölüm"), "bozuk kayıt", None],
    })
    assert len(merged) == 1
    assert list(merged[0]["sources"]) == ["Anizle"]
    assert merge_episodes({}) == []
    assert merge_episodes(None) == []


def test_baslik_normalize_edilmis_halde_tutuluyor():
    """Satır etiketi hangi kaynağın önce geldiğine göre değişmemeli."""
    merged = merge_episodes({"TürkAnime": [ep("Cowboy Bebop 12. Bölüm")]})
    assert merged[0]["title"] == "12. Bölüm"


def test_kaynak_kayitlari_oldugu_gibi_tasiniyor():
    """`obj` (oynatma nesnesi) birleştirmede kaybolmamalı."""
    entry = ep("1. Bölüm")
    merged = merge_episodes({"TürkAnime": [entry]})
    assert merged[0]["sources"]["TürkAnime"] is entry


# NOT: Burada eskiden `common/ui.py`'nin parser'ı kendi kopyalamadığını
# doğrulayan bir test vardı. Faz 9'da `common/ui.py` (CustomTkinter) silindi;
# artık korunacak bir kopya-şim yok — Qt sayfaları doğrudan
# `common/episode_parser.py`'dan import ediyor. Test konusuz kaldığı için
# kaldırıldı.
