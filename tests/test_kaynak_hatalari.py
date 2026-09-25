"""Kaynak hataları NEDEN'i söylüyor (`common.hatalar`).

ESKİ HATA: `kayit.akis_saglayici` arşiv dışındaki her hatayı boş listeye
çeviriyordu; `best_video` onu "hiçbiri çalışmıyor", arayüz "çalışan video
bulunamadı" diye raporluyordu. Süresi dolmuş çerez, Cloudflare engeli, zaman
aşımı ve arşivde kaydı olmayan bölüm kullanıcıya aynı cümleyle görünüyordu.

Ağa çıkılmaz: sahte kaynak uçları, sahte istisnalar; mpv hiç başlatılmaz.
"""
from __future__ import annotations

import ast
import errno
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from turkanime_api.common import hatalar
from turkanime_api.common.hatalar import (
    KaynakEngellendi, KaynakHatasi, KaynakYanitVermedi, OturumGerekli, VideoYok,
    insanlastir, kaynak_hatasi, sebep_metni,
)
from turkanime_api.sources import kayit


# ── Tip ailesi ───────────────────────────────────────────────────────────────
def test_arsiv_hatasi_kaynak_hatasi_ailesinde():
    from turkanime_api.common.arsiv_paketi import ArsivHatasi
    from turkanime_api.sources.animedepo import ArsivOkunamadi
    assert issubclass(ArsivHatasi, KaynakHatasi)
    assert issubclass(ArsivOkunamadi, KaynakHatasi)
    assert issubclass(KaynakHatasi, RuntimeError), "eski yakalayıcılar bozulmasın"


def test_hatalar_modulu_yalnizca_standart_kutuphane():
    """`arsiv_paketi` sunucu imajında da yükleniyor; `hatalar` onu ağırlaştırmamalı."""
    kaynak = Path(hatalar.__file__).read_text("utf-8")
    kokler = set()
    for dugum in ast.walk(ast.parse(kaynak)):
        if isinstance(dugum, ast.Import):
            kokler |= {a.name.split(".")[0] for a in dugum.names}
        elif isinstance(dugum, ast.ImportFrom) and not dugum.level:
            kokler.add((dugum.module or "").split(".")[0])
    assert kokler <= set(sys.stdlib_module_names) | {"__future__"}, kokler


# ── insanlastir / sebep_metni ────────────────────────────────────────────────
def _http_hatasi(kod: int, govde: str = "") -> requests.HTTPError:
    yanit = requests.Response()
    yanit.status_code = kod
    yanit._content = govde.encode()
    return requests.HTTPError(f"{kod} Client Error: x", response=yanit)


def test_ytdlp_403_kisa_metin_ve_tavsiye():
    from yt_dlp.utils import DownloadError
    kisa, ayrinti = insanlastir(DownloadError(
        "ERROR: unable to download video data: HTTP Error 403: Forbidden"))
    assert "403" in kisa and "deneyin" in kisa
    assert "HTTP Error 403: Forbidden" in ayrinti


@pytest.mark.parametrize("istisna,beklenen", [
    (requests.ConnectionError("Max retries exceeded"), "sunucuya ulaşılamadı"),
    (requests.exceptions.ProxyError("Unable to connect to proxy"), "sunucuya ulaşılamadı"),
    (requests.exceptions.ReadTimeout("read timed out"), "zaman aşımı"),
    (OSError(errno.ENOSPC, "No space left on device"), "diskte yer yok"),
    (PermissionError(13, "Permission denied"), "indirme klasörüne yazılamıyor"),
])
def test_bilinen_hatalar_turkce_sebebe_donuyor(istisna, beklenen):
    kisa, ayrinti = insanlastir(istisna)
    assert beklenen in kisa
    assert type(istisna).__name__ in ayrinti


def test_cloudflare_sayfasi_taniniyor():
    from turkanime_api.common.cf_bypass import CFBypassError
    assert "Cloudflare" in insanlastir(
        _http_hatasi(403, "<title>Just a moment...</title>"))[0]
    assert "Cloudflare" in insanlastir(CFBypassError("çözülemedi"))[0]
    # Cloudflare imzası olmayan 403 yine 403 (engel türü aynı).
    assert "HTTP 403" in insanlastir(_http_hatasi(403, "yasak"))[0]


def test_kaynak_hatasi_metni_aynen_geciyor():
    from turkanime_api.sources.animedepo import ArsivOkunamadi
    mesaj = "TürkAnime arşivi okunamadı: uzak aynalar yanıt vermedi"
    assert insanlastir(ArsivOkunamadi(mesaj))[0] == mesaj
    assert sebep_metni(OturumGerekli("çerez gir")) == "çerez gir"


def test_bilinmeyen_hata_genel_metin_ham_ayrintida():
    kisa, ayrinti = insanlastir(ZeroDivisionError("division by zero"))
    assert kisa.startswith("beklenmeyen hata")
    assert ayrinti == "ZeroDivisionError: division by zero"
    # Arama satırı: tanınmayan hata kendi metniyle (ipucu kaybolmasın).
    assert sebep_metni(RuntimeError("site değişti")) == "site değişti"
    assert sebep_metni(requests.exceptions.ProxyError("x")).startswith(
        "sunucuya ulaşılamadı")


def test_kaynak_hatasi_sinifi_sebebe_gore():
    assert isinstance(kaynak_hatasi(requests.exceptions.ConnectTimeout("t"), "X"),
                      KaynakYanitVermedi)
    assert isinstance(kaynak_hatasi(_http_hatasi(403, "Attention Required! | Cloudflare"),
                                    "X"), KaynakEngellendi)
    hata = kaynak_hatasi(RuntimeError("site değişti"), "Anizle")
    assert type(hata) is KaynakHatasi
    assert str(hata).startswith("Anizle: video listesi alınamadı")
    assert "RuntimeError: site değişti" in str(hata)


# ── Akış sağlayıcıdan best_video'ya ──────────────────────────────────────────
def _sahte_kaynak(akislar, etiket="Sahte Site", **ek) -> "kayit.Kaynak":
    uclar = kayit.KaynakUclari(lambda q, limit=10: [],
                               lambda _s: [("s/1", "1. Bölüm")], akislar)
    return kayit.Kaynak("Sahte", etiket, "SS", "#000000", "SAHTE",
                        lambda: uclar, **ek)


def _ilk_bolum(kaynak):
    from turkanime_api.sources.adapter import kayittan_bolumler
    return kayittan_bolumler(kaynak, "s", "Sahte Seri")[0]


def test_zaman_asimi_best_videodan_etiketiyle_yukseliyor():
    def akislar(_b):
        raise requests.exceptions.Timeout("HTTPSConnectionPool: read timed out")

    bolum = _ilk_bolum(_sahte_kaynak(akislar, etiket="AnimeciX"))
    assert bolum.fansubs == [], "bölüm listesi/fansub sorusu çökmemeli"
    durumlar: list = []
    with pytest.raises(KaynakYanitVermedi) as bilgi:
        bolum.best_video(callback=durumlar.append)
    assert "AnimeciX" in str(bilgi.value) and "zaman aşımı" in str(bilgi.value)
    assert durumlar[-1]["status"] == "kaynak okunamadı"


def test_arsivde_kayit_yoksa_sebebi_bu(monkeypatch):
    from turkanime_api.sources import animedepo
    monkeypatch.setattr(animedepo, "get_anime_episodes",
                        lambda _s: [("naruto/naruto-1-bolum", "1. Bölüm")])
    monkeypatch.setattr(animedepo, "get_episode_streams", lambda _b: [])
    bolum = _ilk_bolum(kayit.bul("TürkAnime"))
    with pytest.raises(VideoYok,
                       match="arşivde bu bölüm için oynatılabilir kayıt yok"):
        bolum.best_video()


def test_canli_kaynakta_bos_liste_eski_davranis_ama_sebepli():
    """Boş listenin anlamı kesin değil (site bozulmuş olabilir): hata değil,
    ama yedekli oynatma "kaynak hiç video vermedi" diyor, "N aday denendi" değil."""
    from turkanime_api.common.oynatma import yedekli_oynat
    bolum = _ilk_bolum(_sahte_kaynak(lambda _b: []))
    sonuc = yedekli_oynat(
        lambda atla, cb: bolum.best_video(callback=cb, atla=atla),
        lambda _v: pytest.fail("oynatılacak video yoktu"))
    assert not sonuc.basarili
    assert "hiç video vermedi" in sonuc.sebep


def test_tranime_cerezsiz_bot_kontrolu_oturum_gerekli(monkeypatch):
    from turkanime_api.sources import tranime
    monkeypatch.setattr(tranime, "get_episode_details", lambda _s: None)
    monkeypatch.setattr(tranime, "SESSION_COOKIE", None)
    akislar = kayit.bul("TRAnimeİzle").uclar().akislar
    with pytest.raises(OturumGerekli) as bilgi:
        akislar("naruto-1-bolum-izle")
    assert "çerez" in str(bilgi.value) and "Ayarlar" in str(bilgi.value)

    monkeypatch.setattr(tranime, "SESSION_COOKIE", "eski-cerez")
    with pytest.raises(KaynakHatasi, match="süresi dolmuş"):
        akislar("naruto-1-bolum-izle")


def test_tranime_cerezsiz_bos_arama_aranamayanlarda(monkeypatch):
    from turkanime_api.common.adapters import SearchEngine, kaynak_adaptoru
    from turkanime_api.sources import tranime
    monkeypatch.setattr(tranime, "search_tranime", lambda q, limit=10: [])
    monkeypatch.setattr(tranime, "SESSION_COOKIE", None)
    motor = SearchEngine()
    motor.adapters = {"TRAnimeİzle": kaynak_adaptoru(kayit.bul("TRAnimeİzle"))}
    sonuc = motor.search_all_sources_rich("naruto")
    assert sonuc["TRAnimeİzle"] == []
    assert "çerez" in sonuc.hatalar["TRAnimeİzle"]

    # Çerez varken boş sonuç gerçekten "bulunamadı": hata yok.
    monkeypatch.setattr(tranime, "SESSION_COOKIE", "cerez")
    assert motor.search_all_sources_rich("naruto").hatalar == {}


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_kaynak_hatasini_yaziyor(capsys):
    from turkanime_api.cli import __main__ as ana
    from turkanime_api.cli import cli_tools

    ana._hata_sebebi(OturumGerekli("TRAnimeİzle çerez gerekli: Ayarlar"))
    assert "çerez gerekli" in capsys.readouterr().out

    class Bolum:
        slug = "x-1"

        def best_video(self, **_k):
            raise KaynakEngellendi("Tranimaci: Cloudflare engeli")

    from rich.table import Table
    cli_tools.indirme_task_cli(Bolum(), Table(),
                               SimpleNamespace(ayarlar={"max resolution": True}))
    assert "Cloudflare engeli" in capsys.readouterr().out


# ── Arayüz ───────────────────────────────────────────────────────────────────
class _PatlayanBolum:
    slug = "sahte-1-bolum"
    anime = SimpleNamespace(slug="sahte", title="Sahte")
    url = "sahte"

    def __init__(self, hata):
        self.hata = hata

    def best_video(self, **_k):
        raise self.hata


def test_oynatma_sebebi_durum_cubugunda_kalici(main_window, qtbot, monkeypatch):
    from PySide6.QtWidgets import QStatusBar
    from turkanime_api.gui.qt import prefs
    monkeypatch.setattr(prefs, "oynat", lambda *a, **k: pytest.fail("mpv açılmamalı"))
    sureler: list = []
    asil = QStatusBar.showMessage
    monkeypatch.setattr(QStatusBar, "showMessage",
                        lambda self, m, t=0: (sureler.append((m, t)), asil(self, m, t)))

    hata = kaynak_hatasi(requests.exceptions.ReadTimeout("read timed out"), "AnimeciX")
    main_window._on_play({"title": "Sahte 1. Bölüm", "obj": _PatlayanBolum(hata)})
    qtbot.waitUntil(lambda: "zaman aşımı" in main_window.statusBar().currentMessage(),
                    timeout=5000)
    mesaj = main_window.statusBar().currentMessage()
    assert "AnimeciX" in mesaj and "oynatılamadı" in mesaj
    assert [t for m, t in sureler if m == mesaj] == [0], \
        "hata 6 sn'de silinmemeli, bir sonraki mesaja kadar kalmalı"


def test_indirme_satiri_kisa_sebep_arac_ipucunda_ham(qtbot, izole_ev):
    from yt_dlp.utils import DownloadError
    from turkanime_api.gui.qt.indirme import BITMIS_DURUMLAR, DownloadManager
    from turkanime_api.gui.web.kopru import Kopru
    from turkanime_api.gui.web.uclar_indirme import IndirmeUclari

    ham = ("ERROR: unable to download video data: HTTP Error 403: Forbidden "
           + "x" * 300)

    class Video:
        player = "SIBNET"
        url = "https://cdn/1.mp4"
        ydl_opts: dict = {}

        def indir(self, callback=None, output=""):
            raise DownloadError(ham)

    class Bolum:
        slug = "sahte-1-bolum"
        anime = SimpleNamespace(slug="sahte", title="Sahte")

        def best_video(self, **_k):
            return Video()

    mgr = DownloadManager()
    uclar = IndirmeUclari(Kopru(), mgr, oynat=lambda e: None)
    tid = mgr.enqueue({"title": "Sahte 1. Bölüm", "obj": Bolum()},
                      output=str(izole_ev / "indir"))
    qtbot.waitUntil(lambda: mgr.durum(tid) in BITMIS_DURUMLAR, timeout=10000)
    satir = lambda: uclar.indirmeler()["satirlar"][0]      # noqa: E731
    qtbot.waitUntil(lambda: "403" in satir()["mesaj"], timeout=5000)
    # Satırda kısa sebep; ham metnin tamamı araç ipucunda (sayfada `title`).
    assert len(satir()["mesaj"]) <= 120
    assert "HTTP Error 403: Forbidden" in satir()["ayrinti"]
