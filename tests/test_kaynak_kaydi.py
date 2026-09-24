"""Tek kaynak kaydı (`sources/kayit.py`) ve TürkAnime = arşiv kablolaması.

Hiçbir test ağa çıkmaz. Üç katman sınanıyor:

1. **Kayıt ve türetilen listeler.** Kaynak listesi eskiden altı yerde elle
   tutuluyordu ve ayrışıyordu (OpenAnime/Tranimaci aramada yoktu, AnimeDepo
   CLI menüsünde yoktu). Artık hepsi kayıttan türüyor; burada kayıttaki HER
   kaynağın aranabildiği, köprüde bölümlerinin açıldığı ve CLI menüsünde
   seçilebildiği sahte uçlarla uçtan uca doğrulanıyor. Testler kaydı
   parametre olarak okuyor: yeni kaynak eklendiğinde ayrıca test yazmadan
   buraya girer.
2. **TürkAnime = arşiv.** turkanime.tv kapandı; kaynak sitenin statik arşivi.
   `tmp_path`'te küçük bir arşiv kurulup arama motoru, köprü ve CLI onun
   üzerinden koşturuluyor. Eski "AnimeDepo" adı takma ad olarak okunuyor ama
   hiçbir listede ikinci kez görünmüyor.
3. **Kapanan siteye giden yol kalmadı.** Üretim kodunda `bypass`'a, canlı
   `objects.Anime(...)` kurulumuna ya da `arama_yap`'a çağrı olmadığı statik
   olarak denetleniyor; adaptör fabrikaları ağsız çalışıyor.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import subprocess
import sys
import threading
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from turkanime_api.sources import animedepo, kayit
from turkanime_api.sources.kayit import Kaynak, KaynakUclari

KOK = Path(__file__).resolve().parent.parent

TUM_KAYNAKLAR = [k.ad for k in kayit.KAYNAKLAR]
OYNATILABILIR = [k.ad for k in kayit.KAYNAKLAR if k.oynatilabilir]
METADATA = [k.ad for k in kayit.KAYNAKLAR if k.yalnizca_metadata]


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────
def _sahte_uclar(kaynak: Kaynak) -> KaynakUclari:
    """Kaynağa özgü, ağsız uçlar. Sayısal kimlik: AnimeciX de kabul etsin."""
    def ara(sorgu: str, limit: int = 10):
        return [("17", f"{kaynak.ad} Sahte Dizi")][:limit]

    def zengin_ara(sorgu: str, limit: int = 10):
        return [{"slug": "17", "title": f"{kaynak.ad} Sahte Dizi",
                 "image": "http://kapak/17.jpg"}][:limit]

    def bolumler(kimlik: str):
        return [(f"{kimlik}/b1", "1. Bölüm"), (f"{kimlik}/b2", "2. Bölüm"),
                (f"{kimlik}/b1", "1. Bölüm")]          # yinelenen kimlik ayıklanmalı

    def akislar(bolum_id: str):
        return [{"url": f"https://cdn.example/{kaynak.modul}/{bolum_id}.mp4",
                 "label": "720p", "fansub": "Grup"}]

    if kaynak.yalnizca_metadata:
        return KaynakUclari(ara, zengin_ara=zengin_ara)
    return KaynakUclari(ara, bolumler, akislar)


@pytest.fixture
def sahte_kayit(monkeypatch):
    """Kayıttaki her kaynağın yükleyicisini sahte uçlarla değiştir.

    Kimlik, etiket, bayraklar gerçek kayıttan geliyor; yalnızca siteyle
    konuşan uçlar sahte. Hazırlık da kapatılıyor (TürkAnime'ninki arşivi
    okurdu).
    """
    yeni = tuple(dataclasses.replace(k, yukleyici=(lambda k=k: _sahte_uclar(k)),
                                     hazirlik=None)
                 for k in kayit.KAYNAKLAR)
    monkeypatch.setattr(kayit, "KAYNAKLAR", yeni)
    return yeni


class _Soru:
    """questionary sorusu yerine: `.ask()` hazır cevabı döndürür."""

    def __init__(self, cevap):
        self.cevap = cevap

    def ask(self, *_a, **_k):
        return self.cevap


def _sirali_cevaplar(cevaplar: List[Any]):
    """Her çağrıda sıradaki cevabı veren soru fabrikası.

    Cevap çağrılabilir ise soru argümanlarıyla çağrılır (seçenekten değer
    seçmek için: `lambda *a, **k: k["choices"][0].value`).
    """
    kuyruk = list(cevaplar)

    def fabrika(*a, **k):
        cevap = kuyruk.pop(0) if kuyruk else None
        return _Soru(cevap(*a, **k) if callable(cevap) else cevap)
    return fabrika


def _ilk_secenek(*_a, **k):
    secenekler = k.get("choices") or (_a[1] if len(_a) > 1 else [])
    ilk = secenekler[0]
    return getattr(ilk, "value", ilk)


@pytest.fixture
def cli(izole_ev, monkeypatch):
    """CLI modülü: ekranı silmeden, beklemeden. `izole_ev` şart — modül import
    anında `Dosyalar()` kuruyor, gerçek `ayarlar.json`'a dokunmamalı."""
    from turkanime_api.cli import __main__ as ana
    monkeypatch.setattr(ana, "sleep", lambda *_a: None)
    monkeypatch.setattr(ana, "clear", lambda: None)
    return ana


def arsiv_yaz(kok: Path) -> Path:
    """AnimeDepo şemasında küçük bir arşiv (turkanime.tv slug'larıyla)."""
    animeler = {
        "naruto": ("Naruto", {"naruto-1-bolum": [
            {"player": "SIBNET", "fansub": "TAÇE",
             "url": "https://video.sibnet.ru/shell.php?videoid=1"},
            {"player": "MAIL", "fansub": "AnimeOU",
             "url": "https://my.mail.ru/video/embed/1"},
            {"player": "DEAD_ALUCARD", "fansub": "TAÇE", "url": "https://x/y"},
        ]}),
        "naruto-shippuuden": ("Naruto: Shippuuden", {}),
        "dr-stone": ("Dr. Stone", {}),
        "shingeki-no-kyojin": ("Shingeki no Kyojin", {}),
        "koisuru-one-piece": ("Koisuru One Piece", {}),
        "one-piece": ("One Piece", {}),
    }
    index: Dict[str, Dict[str, Any]] = {}
    for slug, (baslik, bolumler) in animeler.items():
        index.setdefault(slug[0].upper(), {})[slug] = {"title": baslik}
        klasor = kok / "animeler" / slug
        klasor.mkdir(parents=True, exist_ok=True)
        (klasor / "info.json").write_text(json.dumps({"Kategori": "TV"}), "utf-8")
        (klasor / "bolumler.json").write_text(json.dumps(
            [[b, f"{baslik} {i}. Bölüm"] for i, b in enumerate(bolumler, 1)]), "utf-8")
        for bolum, kayitlar in bolumler.items():
            (klasor / f"{bolum}.json").write_text(json.dumps(kayitlar), "utf-8")
    (kok / "dizin.json").write_text(json.dumps({"last_update": 1, "index": index}), "utf-8")
    return kok


@pytest.fixture
def yerel_arsiv(tmp_path, monkeypatch):
    """İstemci `tmp_path`'teki küçük arşivi "depodaki arşiv" sansın."""
    arsiv = arsiv_yaz(tmp_path / "arsiv")
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", arsiv)
    animedepo.sifirla()
    yield arsiv
    animedepo.sifirla()


# ─────────────────────────────────────────────────────────────────────────────
# 1) Kayıt: adlar, takma adlar, çakışma
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ad", TUM_KAYNAKLAR)
def test_her_ad_ayni_kayda_cozuluyor(ad):
    kaynak = kayit.bul(ad)
    adlar = [kaynak.ad, kaynak.etiket, kaynak.cli_etiketi, kaynak.modul,
             kaynak.cli_kodu, *kaynak.takma_adlar]
    for deger in filter(None, adlar):
        assert kayit.bul(deger) is kaynak, deger
        assert kayit.bul(deger.upper()) is kaynak, "büyük/küçük harf duyarsız olmalı"


def test_kanonik_adlar_tekil():
    adlar = [k.ad for k in kayit.KAYNAKLAR]
    assert len(adlar) == len(set(adlar))
    kodlar = [k.cli_kodu for k in kayit.KAYNAKLAR if k.cli_kodu]
    assert len(kodlar) == len(set(kodlar))


def test_cakisan_ad_kayda_girmiyor(monkeypatch):
    """İki kaynak aynı adı iddia ederse ayar hangisini kastediyor belirsizleşir."""
    monkeypatch.setattr(kayit, "KAYNAKLAR", kayit.KAYNAKLAR)   # kaydet geri alınsın
    once = kayit.KAYNAKLAR
    sahte = Kaynak("Yeni", "TürkAnime (arşiv)", "YN", "#000", "YENI",
                   lambda: KaynakUclari(lambda q, limit=10: []))
    with pytest.raises(ValueError, match="çakışıyor"):
        kayit.kaydet(sahte)
    assert kayit.KAYNAKLAR is once


def test_kaydet_ile_eklenen_kaynak_cozuluyor(monkeypatch):
    monkeypatch.setattr(kayit, "KAYNAKLAR", kayit.KAYNAKLAR)
    yeni = Kaynak("Deneme Kaynak", "Deneme Kaynak", "DK", "#123456", "DENEME",
                  lambda: KaynakUclari(lambda q, limit=10: [("d", "D")]),
                  modul="deneme", cli_kodu="deneme")
    kayit.kaydet(yeni)
    assert kayit.bul("deneme") is yeni
    assert yeni in kayit.cli_kaynaklari()


def test_animedepo_eski_ad_olarak_turkanimeye_dusuyor():
    turkanime = kayit.bul("TürkAnime")
    for eski in ("AnimeDepo", "animedepo", "ANIMEDEPO", "AnimeDepo (arşiv)"):
        assert kayit.bul(eski) is turkanime, eski
        assert kayit.kanonik_ad(eski) == "TürkAnime"
    assert kayit.cli_kaynagi("animedepo").cli_kodu == "turkanime"


def test_gorunen_ad_yalnizca_kanonik_anahtari_cevirir():
    assert kayit.gorunen_ad("TürkAnime") == "TürkAnime (arşiv)"
    assert kayit.gorunen_ad("AnimeciX") == "AnimeciX"
    # Takma ad/bilinmeyen ad olduğu gibi: o dizgeyi anahtar olarak kullanan kod
    # ekranda da aynı adı görmeli.
    assert kayit.gorunen_ad("AnimeDepo") == "AnimeDepo"
    assert kayit.gorunen_ad("YokBoyle") == "YokBoyle"


@pytest.mark.parametrize("deger,kod", [
    ("turkanime", "turkanime"),
    ("", "turkanime"),
    (None, "turkanime"),
    ("yokboyle", "turkanime"),        # eski `_norm_source` da bilinmeyeni TürkAnime sayıyordu
    ("AniList", "turkanime"),         # CLI'da olmayan kaynak
    ("AnimeciX (deneysel)", "animecix"),
    ("TRAnimeİzle", "tranimeizle"),
    ("tranimeizle", "tranimeizle"),
    ("openanime", "openanime"),
    ("TürkAnime (arşiv)", "turkanime"),
])
def test_cli_ayar_degerleri_cozuluyor(deger, kod):
    assert kayit.cli_kaynagi(deger).cli_kodu == kod


def test_kayit_modulu_hafif_kaliyor():
    """Sunucu tarayıcısı kaydı okuyor ve imajında yt-dlp yok: kayıt modülü
    `sources.adapter`'ı (→ yt_dlp) fonksiyon içinde bile import etmemeli."""
    agac = ast.parse((KOK / "turkanime_api" / "sources" / "kayit.py").read_text("utf-8"))
    hedefler = {d.module for d in ast.walk(agac) if isinstance(d, ast.ImportFrom)}
    assert "adapter" not in hedefler and "objects" not in {h and h.split(".")[-1] for h in hedefler}


# ─────────────────────────────────────────────────────────────────────────────
# 2) Türetilen listeler kayıtla aynı; "AnimeDepo" ikinci kez görünmüyor
# ─────────────────────────────────────────────────────────────────────────────
def test_turetilen_listeler_kayittan_geliyor(izole_ev):
    from turkanime_api.cli import __main__ as ana
    from turkanime_api.common.adapters import SearchEngine
    from turkanime_api.gui.qt import sources_bridge as sb
    from turkanime_api.gui.qt.pages import episodes
    from turkanime_api.sources import PROVIDERS
    from turkanime_server.crawler.kaynaklar import KAYNAKLAR as TARAYICI

    assert set(SearchEngine().adapters) == set(TUM_KAYNAKLAR)
    assert set(sb.supported_sources()) == set(OYNATILABILIR)
    assert set(sb.FUNCTION_SOURCES) == set(sb.BUILDERS) == set(OYNATILABILIR)
    assert sb.METADATA_ONLY == set(METADATA)
    assert set(ana.SOURCE_TITLES) == {k.cli_kodu for k in kayit.cli_kaynaklari()}
    assert set(episodes.SOURCE_COLORS) == set(episodes.SOURCE_SHORT) == set(TUM_KAYNAKLAR)
    assert {v["name"] for v in PROVIDERS.values()} == set(OYNATILABILIR)
    assert set(TARAYICI) == {k.modul for k in kayit.tarayici_kaynaklari()}


def test_arsiv_tek_kaynak_olarak_listeleniyor(izole_ev):
    """Aynı arşiv "TürkAnime" ve "AnimeDepo" diye iki kez listeleniyordu."""
    from turkanime_api.cli import __main__ as ana
    from turkanime_api.common.adapters import SearchEngine
    from turkanime_api.gui.qt import sources_bridge as sb

    assert "AnimeDepo" not in SearchEngine().adapters
    assert "AnimeDepo" not in sb.supported_sources()
    assert "animedepo" not in ana.SOURCE_TITLES
    assert ana.SOURCE_TITLES["turkanime"] == "TürkAnime (arşiv)"
    # Arşiv taranmaz: tarayıcının ÜRETTİĞİ şema o.
    assert not kayit.bul("TürkAnime").taranabilir


# ─────────────────────────────────────────────────────────────────────────────
# 3) Her kaynak: aranabilir, köprüde açılabilir, CLI menüsünde
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ad", TUM_KAYNAKLAR)
def test_her_kaynak_aranabiliyor(sahte_kayit, ad):
    from turkanime_api.common.adapters import SearchEngine

    sonuc = SearchEngine().search_all_sources_rich("sahte", limit_per_source=5)
    assert sonuc[ad], f"{ad} aramada sonuç vermedi"
    kayit_ = sonuc[ad][0]
    assert kayit_["slug"] == "17"
    assert kayit_["title"] == f"{ad} Sahte Dizi"
    assert "image" in kayit_
    if ad in METADATA:
        assert kayit_["image"], "görsel verebilen kaynağın görseli kaybolmamalı"


@pytest.mark.parametrize("ad", OYNATILABILIR)
def test_her_oynatilabilir_kaynak_koprude_aciliyor(sahte_kayit, ad):
    from turkanime_api.gui.qt import sources_bridge as sb

    kaynak = kayit.bul(ad)
    bolumler = sb.fetch_episodes(ad, "17", "Sahte Dizi")
    assert [b["title"] for b in bolumler] == ["1. Bölüm", "2. Bölüm"], \
        "yinelenen bölüm kimliği ayıklanmalı"

    nesne = bolumler[0]["obj"]
    assert nesne.url == kaynak.bolum_adresi("17/b1")
    assert nesne._player_name == kaynak.oynatici
    akislar = nesne._saglayici()(nesne.url)
    assert akislar[0]["url"] == f"https://cdn.example/{kaynak.modul}/17/b1.mp4"
    assert nesne.fansubs == ["Grup"]
    if kaynak.bolum_slugu:
        assert nesne.slug == kaynak.bolum_slugu("17/b1")


@pytest.mark.parametrize("ad", METADATA)
def test_metadata_kaynagi_koprude_acik_hata_veriyor(sahte_kayit, ad):
    from turkanime_api.gui.qt import sources_bridge as sb

    with pytest.raises(sb.UnsupportedSource, match="metadata"):
        sb.fetch_episodes(ad, "17", "Sahte Dizi")


@pytest.mark.parametrize("ad", OYNATILABILIR)
def test_her_oynatilabilir_kaynak_cli_menusunde(sahte_kayit, cli, monkeypatch, ad):
    """Menüden seçilir, ayara kodu yazılır, arama ve bölüm akışı çalışır."""
    from turkanime_api.cli.dosyalar import Dosyalar

    ana = cli
    kaynak = kayit.bul(ad)
    assert kaynak.cli_kodu, f"{ad} CLI'da yok"
    assert ana._kaynak_basliklari()[kaynak.cli_etiketi] is kaynak

    # "Kaynak seç" → bu kaynak → (menüden çık)
    monkeypatch.setattr(ana.qa, "select",
                        _sirali_cevaplar(["Kaynak seç", kaynak.cli_etiketi, None]))
    ana.menu_loop()
    assert Dosyalar().ayarlar["kaynak"] == kaynak.cli_kodu
    assert ana.secili_kaynak().ad == ad

    # Arama → ilk sonuç → bölümler
    monkeypatch.setattr(ana.qa, "text", _sirali_cevaplar(["sahte"]))
    monkeypatch.setattr(ana.qa, "select", _sirali_cevaplar([_ilk_secenek]))
    secim = ana._anime_sec(ana.secili_kaynak())
    assert secim == ("17", f"{ad} Sahte Dizi")
    bolumler = ana._bolumleri_getir(kaynak, *secim)
    assert [b.title for b in bolumler] == ["1. Bölüm", "2. Bölüm"]
    assert bolumler[0].url == kaynak.bolum_adresi("17/b1")


def test_cli_arama_hatasi_cliyi_kapatmiyor(sahte_kayit, cli, monkeypatch):
    """ESKİ HATA: yalnızca TürkAnime dalı arama hatasını yakalıyordu; diğer
    kaynaklarda bir ağ hatası en dıştaki `sys.exit(1)`e kadar çıkıyordu."""
    ana = cli
    patlayan = dataclasses.replace(
        kayit.bul("AnimeciX"),
        yukleyici=lambda: KaynakUclari(lambda q, limit=10: 1 / 0))
    monkeypatch.setattr(ana.qa, "text", _sirali_cevaplar(["naruto"]))
    assert ana._anime_sec(patlayan) is None


def test_cli_animecix_sayisal_olmayan_kimligi_soyluyor(sahte_kayit, cli, capsys):
    ana = cli
    assert ana._bolumleri_getir(kayit.bul("AnimeciX"), "naruto", "Naruto") is None
    assert "sayısal" in capsys.readouterr().out


# ─────────────────────────────────────────────────────────────────────────────
# 4) TürkAnime = arşiv (küçük yerel arşivle, ağsız)
# ─────────────────────────────────────────────────────────────────────────────
def test_turkanime_arama_motorunda_arsivden_geliyor(yerel_arsiv):
    from turkanime_api.common.adapters import SearchEngine

    motor = SearchEngine()
    # Diğer kaynaklar gerçek siteye gider (curl_cffi ağ mandalını atlatıyor).
    motor.adapters = {"TürkAnime": motor.adapters["TürkAnime"]}
    sonuc = motor.search_all_sources_rich("naruto")
    assert sonuc["TürkAnime"][0] == {"slug": "naruto", "title": "Naruto", "image": None}
    assert [k["slug"] for k in sonuc["TürkAnime"]] == ["naruto", "naruto-shippuuden"]


def test_turkanime_koprusu_arsiv_bolumlerini_kuruyor(yerel_arsiv):
    from turkanime_api.gui.qt import sources_bridge as sb

    bolumler = sb.fetch_episodes("TürkAnime", "naruto", "Naruto")
    assert [b["title"] for b in bolumler] == ["Naruto 1. Bölüm"]
    nesne = bolumler[0]["obj"]
    assert nesne.url == "naruto/naruto-1-bolum"
    # turkanime.tv'nin kendi bölüm slug'ı: izleme geçmişi ve eski indirmeler
    # bu slug'la duruyor.
    assert nesne.slug == "naruto-1-bolum"
    assert nesne.anime.slug == "naruto"
    # Sıra oynatıcı önceliğinden (MAIL, SIBNET'ten önce denenir).
    assert nesne.fansubs == ["AnimeOU", "TAÇE"]
    oynaticilar = [a["player"] for a in nesne._saglayici()(nesne.url)]
    assert oynaticilar == ["MAIL", "SIBNET"], "ölü oynatıcı (DEAD_) elenmeli"


def test_eski_animedepo_adi_kopruden_ayni_bolumleri_aciyor(yerel_arsiv):
    """Kayıtlı bağlantı/eşleşme "AnimeDepo" diyor olabilir."""
    from turkanime_api.gui.qt import sources_bridge as sb

    eski = sb.fetch_episodes("AnimeDepo", "naruto", "Naruto")
    yeni = sb.fetch_episodes("TürkAnime", "naruto", "Naruto")
    assert [b["obj"].url for b in eski] == [b["obj"].url for b in yeni]
    assert sb.FUNCTION_SOURCES["AnimeDepo"] is sb.FUNCTION_SOURCES["TürkAnime"]
    assert "AnimeDepo" not in sb.FUNCTION_SOURCES, "yinelemede ikinci kez sayılmamalı"


def test_cli_turkanime_akisi_arsivden(yerel_arsiv, cli, monkeypatch):
    ana = cli
    kaynak = ana.secili_kaynak()
    assert kaynak.ad == "TürkAnime"                   # yeni kurulumun varsayılanı
    assert ana.fetch(kaynak)["index"], "açılış hazırlığı arşivi yüklemeli"

    monkeypatch.setattr(ana.qa, "text", _sirali_cevaplar(["Dr Stone"]))
    monkeypatch.setattr(ana.qa, "select", _sirali_cevaplar([_ilk_secenek]))
    assert ana._anime_sec(kaynak) == ("dr-stone", "Dr. Stone")

    bolumler = ana._bolumleri_getir(kaynak, "naruto", "Naruto")
    assert [b.slug for b in bolumler] == ["naruto-1-bolum"]


def test_cli_arsiv_okunamazsa_uyarip_menuyu_aciyor(cli, monkeypatch):
    """Arşiv yok + aynalar kapalı (conftest ağı kesiyor): çıkış değil, uyarı."""
    ana = cli
    durum = {"menu": 0, "cikti": []}
    monkeypatch.setattr(sys, "argv", ["turkanime"])
    monkeypatch.setattr(ana, "menu_loop", lambda: durum.__setitem__("menu", 1))
    monkeypatch.setattr(ana, "guncel_surum", lambda *a, **k: None)
    monkeypatch.setattr(ana.atexit, "register", lambda *a, **k: None)
    monkeypatch.setattr(ana.gereksinim, "path_hazirla", lambda: None)
    monkeypatch.setattr(ana, "rprint",
                        lambda *a, **k: durum["cikti"].append(" ".join(map(str, a))))

    ana.main()

    assert durum["menu"] == 1
    assert "okunamıyor" in "\n".join(durum["cikti"])


@pytest.mark.parametrize("sorgu,ilk", [
    ("naruto", "naruto"),                 # birebir, uzun devamdan önce
    ("Dr Stone", "dr-stone"),             # noktalama duyarsız
    ("dr. stone", "dr-stone"),
    ("narto", "naruto"),                  # yazım hatası
    ("Şingeki", "shingeki-no-kyojin"),    # Türkçe klavyeyle romaji
    ("shingeki kyojin", "shingeki-no-kyojin"),   # kelimeler sırasız/eksik
    ("one piece", "one-piece"),           # "Koisuru One Piece" önce gelmesin
    ("shingeki-no-kyojin", "shingeki-no-kyojin"),  # slug ile
])
def test_arsiv_aramasi(yerel_arsiv, sorgu, ilk):
    sonuc = animedepo.search_animedepo(sorgu)
    assert sonuc and sonuc[0][0] == ilk, sonuc


def test_arsiv_aramasi_alakasizi_getirmiyor(yerel_arsiv):
    """Eski `SequenceMatcher >= 0.55` eşiği arşivde olmayan seri için alakasız
    kısa başlıklar getiriyordu."""
    assert animedepo.search_animedepo("attack on titan") == []
    assert animedepo.search_animedepo("   ") == []
    assert len(animedepo.search_animedepo("n", limit=1)) == 1


def test_arsiv_arama_tablosu_onbellekte_ve_sifirlaniyor(yerel_arsiv):
    ilk = animedepo._arama_tablosu()
    assert animedepo._arama_tablosu() is ilk, "her aramada yeniden kurulmamalı"
    animedepo.sifirla()
    assert animedepo._arama_tablosu() is not ilk


# ─────────────────────────────────────────────────────────────────────────────
# 5) Arayüzde etiket, anahtar kanonik
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_karti_etiketi_gosteriyor(qtbot):
    from turkanime_api.gui.qt.pages.search import SearchPage

    sayfa = SearchPage()
    qtbot.addWidget(sayfa)
    sayfa._on_results({"TürkAnime": [{"slug": "naruto", "title": "Naruto",
                                      "image": None}]})
    kart = sayfa.cards()[0]
    assert kart.lblSource.text() == "TürkAnime (arşiv)"
    assert kart.payload == ("TürkAnime", "naruto", "Naruto")


def test_detay_kaynak_kutusu_etiket_gosterip_ad_tasiyor(qtbot):
    from turkanime_api.gui.qt.pages.detail import DetailPage

    sayfa = DetailPage()
    qtbot.addWidget(sayfa)
    sayfa.show_match("TürkAnime", "naruto", "Naruto")
    assert sayfa.cmbSource.currentText() == "TürkAnime (arşiv)"
    assert sayfa.current_source() == "TürkAnime"
    assert sayfa.cmbSource.findText("AnimeDepo") < 0


def test_rozet_eski_adda_da_ayni():
    from turkanime_api.gui.qt.pages.episodes import source_color, source_short

    assert source_short("AnimeDepo") == source_short("TürkAnime") == "TA"
    assert source_color("AnimeDepo") == source_color("TürkAnime")


def test_detay_eski_ad_bagliyken_arsivi_ikinci_kez_baglamiyor(qtbot, monkeypatch):
    """Eski "AnimeDepo" bağlantısı varken aramadan gelen "TürkAnime" aynı arşiv."""
    import turkanime_api.common.adapters as adapters_mod
    from turkanime_api.gui.qt.pages.detail import DetailPage

    class Motor:
        def search_all_sources_rich(self, query, limit_per_source=10):
            return {"TürkAnime": [{"slug": "naruto", "title": "Naruto"}]}

    monkeypatch.setattr(adapters_mod, "SearchEngine", Motor)
    sayfa = DetailPage()
    qtbot.addWidget(sayfa)
    rid = sayfa.show_anime({"title": {"romaji": "Naruto"}}, source="AnimeDepo",
                           slug="naruto")
    yayilan = []
    sayfa.sources_resolved.disconnect()
    sayfa.sources_resolved.connect(yayilan.append)
    sayfa._do_resolve(rid, "Naruto", {"AnimeDepo": "naruto"}, True, "AnimeDepo")
    assert yayilan[0][1] == {"AnimeDepo": "naruto"}


# ─────────────────────────────────────────────────────────────────────────────
# 6) Kayıtlı eşleşmeler: eski ad okunurken/yazılırken kanoniğe çevriliyor
# ─────────────────────────────────────────────────────────────────────────────
def test_eslesme_okunurken_eski_ad_cevriliyor(monkeypatch):
    from turkanime_api.common import db

    api = db.APIManager.__new__(db.APIManager)
    monkeypatch.setattr(api, "_make_request", lambda *a, **k: [
        {"source": "AnimeDepo", "anime_id": "naruto"},
        {"source": "AnimeciX", "anime_id": "17"},
        {"source": "YeniKaynak", "anime_id": "x"},
    ], raising=False)
    assert [e["source"] for e in api.get_anime_matches()] == \
        ["TürkAnime", "AnimeciX", "YeniKaynak"]
    assert api.search_anime_matches("n")[0]["source"] == "TürkAnime"


def test_eslesme_kanonik_adla_kaydediliyor(monkeypatch):
    from turkanime_api.common import db

    api = db.APIManager.__new__(db.APIManager)
    giden, bitti = [], threading.Event()

    def istek(yontem, uc, data=None):
        giden.append(data)
        bitti.set()
        return {"ok": True}

    monkeypatch.setattr(api, "_make_request", istek, raising=False)
    api.save_anime_match("AnimeDepo", "naruto", "Naruto")
    assert bitti.wait(5)
    assert giden[0]["source"] == "TürkAnime"


# ─────────────────────────────────────────────────────────────────────────────
# 7) Kapanan siteye giden yol kalmadı
# ─────────────────────────────────────────────────────────────────────────────
# Eski nesne modeli ve onun şifre çözücüsü: siteye giden kodun kendisi, geriye
# uyum için duruyor. Paket `__init__`'i yalnızca TYPE_CHECKING altında ve
# tembel ad tablosuyla (dizgeler) anıyor.
_MUAF = {"turkanime_api/objects.py", "turkanime_api/bypass.py"}


def _uretim_dosyalari():
    for kok in ("turkanime_api", "turkanime_server"):
        for yol in sorted((KOK / kok).rglob("*.py")):
            bagil = yol.relative_to(KOK).as_posix()
            if bagil not in _MUAF:
                yield bagil, yol


def _type_checking_disi(agac):
    """TYPE_CHECKING bloğu dışındaki düğümler."""
    atla = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.If) and "TYPE_CHECKING" in ast.dump(dugum.test):
            atla.update(id(c) for b in dugum.body for c in ast.walk(b))
    return [d for d in ast.walk(agac) if id(d) not in atla]


def test_uretim_kodu_kapanan_siteye_gitmiyor():
    """`bypass` import'u, canlı `Anime(...)`/`Bolum(...)` kurulumu ve
    `Anime.arama_yap`/`get_anime_listesi` çağrısı üretim kodunda olmamalı.
    Adaptörler `Anime.cevrimdisi`/`Bolum.cevrimdisi` kullanır."""
    ihlaller = []
    for bagil, yol in _uretim_dosyalari():
        agac = ast.parse(yol.read_text(encoding="utf-8"), filename=bagil)
        dugumler = _type_checking_disi(agac)
        nesne_adlari = set()
        for d in dugumler:
            if isinstance(d, ast.ImportFrom):
                modul = d.module or ""
                if modul.split(".")[-1] == "bypass":
                    ihlaller.append(f"{bagil}:{d.lineno} bypass import ediyor")
                if modul.split(".")[-1] == "objects":
                    nesne_adlari.update(a.asname or a.name for a in d.names)
            if isinstance(d, ast.Import) and any(a.name.endswith(".bypass") for a in d.names):
                ihlaller.append(f"{bagil}:{d.lineno} bypass import ediyor")
        for d in dugumler:
            if not isinstance(d, ast.Call):
                continue
            if isinstance(d.func, ast.Name) and d.func.id in nesne_adlari & {"Anime", "Bolum"}:
                ihlaller.append(f"{bagil}:{d.lineno} canlı {d.func.id}(...) kuruyor")
            if isinstance(d.func, ast.Attribute) and d.func.attr in ("arama_yap",) :
                ihlaller.append(f"{bagil}:{d.lineno} .arama_yap() çağırıyor")
            if (isinstance(d.func, ast.Attribute) and d.func.attr == "get_anime_listesi"
                    and isinstance(d.func.value, ast.Name) and d.func.value.id == "Anime"):
                ihlaller.append(f"{bagil}:{d.lineno} Anime.get_anime_listesi() çağırıyor")
    assert ihlaller == []


def test_openani_fabrikalari_aga_cikmiyor(monkeypatch):
    """Fabrikaların döndürdüğü nesneler de siteye gitmemeli (title, bölümler,
    videolar); eskiden `Anime(slug)` kurucusu `IndexError` ile düşüyordu."""
    from turkanime_api import objects
    from turkanime_api.sources.openani import OpenAniAdapter

    def _yasak(*_a, **_k):
        raise AssertionError("turkanime.tv'ye istek atıldı")
    monkeypatch.setattr(objects, "fetch", _yasak)

    adaptor = OpenAniAdapter()
    anime = adaptor.create_anime_object({"provider_data": {"slug": "bleach"},
                                         "title": "Bleach"})
    assert anime.title == "Bleach"
    assert anime.bolumler == []
    bolum = adaptor.create_episode_object(
        {"provider_data": {"episode_id": "bleach-1"}, "episode_number": 1}, anime)
    assert bolum.title == "Bölüm 1"
    assert bolum.videos == [] and bolum.fansubs == []
    assert bolum.best_video() is None


def test_canli_anime_kurucusu_uyariyor(monkeypatch):
    from turkanime_api import objects

    monkeypatch.setattr(objects.Anime, "fetch_info", lambda self: None)
    with pytest.warns(DeprecationWarning, match="turkanime.tv"):
        objects.Anime("naruto")


# ─────────────────────────────────────────────────────────────────────────────
# 8) CLI tkinter/easygui olmadan
# ─────────────────────────────────────────────────────────────────────────────
def test_cli_easygui_olmadan_aciliyor(tmp_path):
    """ESKİ HATA: `import easygui` modül düzeyindeydi; tkinter'sız kurulumda
    CLI hiç açılmıyordu (easygui'nin yedek import'u da düşüyor)."""
    (tmp_path / ".git").mkdir()      # Dosyalar() gerçek ev klasörüne yazmasın
    kod = ("import sys; sys.modules['easygui'] = None\n"
           "import turkanime_api.cli.__main__ as ana\n"
           "print(callable(ana.main), 'easygui' in sys.modules and "
           "sys.modules['easygui'] is not None)\n")
    ortam = {**__import__("os").environ, "PYTHONPATH": str(KOK)}
    r = subprocess.run([sys.executable, "-c", kod], cwd=tmp_path, env=ortam,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    # Son satır: `Dosyalar()` ilk kurulumda kendi mesajını basıyor.
    assert r.stdout.strip().splitlines()[-1].split() == ["True", "False"]


def test_klasor_secimi_easygui_yoksa_metin_istemine_dusuyor(cli, monkeypatch, tmp_path):
    ana = cli
    monkeypatch.setitem(sys.modules, "easygui", None)      # import → ImportError
    sorulan = {}

    def yol_sor(mesaj, **k):
        sorulan.update(k)
        return _Soru(f"  {tmp_path}/indir  ")
    monkeypatch.setattr(ana.qa, "path", yol_sor)

    assert ana.select_download_folder(str(tmp_path)) == f"{tmp_path}/indir"
    assert sorulan["only_directories"] is True
    assert sorulan["default"] == str(tmp_path)


def test_klasor_secimi_iptalde_eski_yol_kaliyor(cli, monkeypatch):
    ana = cli
    monkeypatch.setitem(sys.modules, "easygui", None)
    monkeypatch.setattr(ana.qa, "path", lambda *a, **k: _Soru(None))
    assert ana.select_download_folder("/eski/yol") == "/eski/yol"


def test_klasor_secimi_pencere_varsa_easygui(cli, monkeypatch):
    ana = cli
    sahte = types.ModuleType("easygui")
    sahte.diropenbox = lambda *a, **k: "/secilen"
    monkeypatch.setitem(sys.modules, "easygui", sahte)
    monkeypatch.setattr(ana.qa, "path",
                        lambda *a, **k: pytest.fail("pencere açılabiliyorken metin sorulmamalı"))
    assert ana.select_download_folder(None) == "/secilen"


def test_klasor_secimi_ekran_yoksa_metin_istemine_dusuyor(cli, monkeypatch):
    """SSH oturumu: easygui import edilir ama pencere açılamaz (TclError)."""
    ana = cli
    sahte = types.ModuleType("easygui")

    def _ekran_yok(*_a, **_k):
        raise RuntimeError("no display name and no $DISPLAY environment variable")
    sahte.diropenbox = _ekran_yok
    monkeypatch.setitem(sys.modules, "easygui", sahte)
    monkeypatch.setattr(ana.qa, "path", lambda *a, **k: _Soru("/metinle"))
    assert ana.select_download_folder(None) == "/metinle"
