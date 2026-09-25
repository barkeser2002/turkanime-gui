"""Adapter katmanı: referer aktarımı ve CDN yedeklerinin denenmesi.

Hiçbir test ağa çıkmaz, mpv/yt-dlp çalıştırmaz: `extract_video_info`,
`YoutubeDL` ve `subprocess.Popen` sahtelenip **kendilerine ne verildiği**
kaydedilir. Amaç "indirme başarılı mı" değil, "referer gerçekten iletiliyor mu".
"""
from __future__ import annotations

import os
import shutil

from pathlib import Path

import pytest

from turkanime_api.sources import adapter as adapter_mod
from turkanime_api.sources.adapter import AdapterAnime, AdapterBolum, AdapterVideo

REFERER = "https://tranimaci.test/"


def bolum(stream_provider=None, url="https://kaynak.test/bolum-1") -> AdapterBolum:
    anime = AdapterAnime(slug="naruto", title="Naruto")
    return AdapterBolum(url=url, title="1. Bölüm", anime=anime,
                        stream_provider=stream_provider, player_name="TRANIMACI")


@pytest.fixture
def yakalanan_opts(monkeypatch):
    """`extract_video_info`e giden (url, opts) çiftlerini kaydeder."""
    kayit: list = []

    def sahte(url, opts):
        kayit.append((url, dict(opts)))
        return {"url": url, "ext": "mp4"}

    monkeypatch.setattr(adapter_mod, "extract_video_info", sahte)
    return kayit


# ── Referer → yt-dlp ────────────────────────────────────────────────────────
def test_referer_yt_dlp_opts_una_giriyor(yakalanan_opts):
    """Kaynak referer veriyor ama yt-dlp'ye hiç iletilmiyordu; CDN 403 dönüyordu."""
    video = AdapterVideo(bolum(), "https://cdn.test/v.mp4", referer=REFERER)
    assert video.info                       # extract_video_info tetiklensin
    _, opts = yakalanan_opts[0]
    assert opts["http_headers"]["Referer"] == REFERER


def test_referersiz_video_baslik_uydurmuyor(yakalanan_opts):
    """Referer'ı olmayan kaynağa (ör. OpenAni mp4) uydurma başlık eklenmemeli."""
    video = AdapterVideo(bolum(), "https://cdn.test/v.mp4")
    assert video.info
    _, opts = yakalanan_opts[0]
    assert "Referer" not in (opts.get("http_headers") or {})


def test_referer_indirmede_yt_dlp_ye_geciyor(monkeypatch, tmp_path):
    """`indir()` kendi opts kopyasını kuruyor; referer orada da durmalı."""
    kayit: list = []

    class SahteYDL:
        def __init__(self, opts):
            kayit.append(dict(opts))
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def download_with_info_file(self, _yol):
            # Gerçek yt-dlp gibi dosyayı yazar: `indir` artık diskteki sonucu
            # doğruluyor (bkz. test_indirme_butunlugu).
            hedef = Path(self.opts["outtmpl"]["default"].replace(".%(ext)s", ".mp4"))
            hedef.parent.mkdir(parents=True, exist_ok=True)
            hedef.write_bytes(b"video")
            return 0

    monkeypatch.setattr(adapter_mod, "YoutubeDL", SahteYDL)
    monkeypatch.setattr(adapter_mod, "extract_video_info",
                        lambda url, opts: {"url": url, "ext": "mp4"})

    video = AdapterVideo(bolum(), "https://cdn.test/v.mp4", referer=REFERER)
    video.indir(output=str(tmp_path))

    assert kayit, "YoutubeDL hiç kurulmadı"
    assert kayit[0]["http_headers"]["Referer"] == REFERER


# ── Referer → mpv ───────────────────────────────────────────────────────────
@pytest.fixture
def mpv_komutu(monkeypatch):
    """mpv'yi sahteler; oluşturulan komut satırını döndüren liste."""
    komutlar: list = []

    class SahteProc:
        def wait(self):
            return 0

    # bin/mpv.exe aranmasın: geliştirme makinesinde varsa gerçek yol girerdi.
    monkeypatch.setattr(os.path, "exists", lambda _p: False)
    monkeypatch.setattr(shutil, "which", lambda _ad: "sahte-mpv")
    monkeypatch.setattr(adapter_mod.sp, "Popen",
                        lambda cmd, *a, **k: komutlar.append(list(cmd)) or SahteProc())
    return komutlar


def test_referer_mpv_komutuna_giriyor(mpv_komutu):
    """mpv referer olmadan CDN'den 403 alıp boş ekranla kapanıyordu."""
    AdapterVideo(bolum(), "https://cdn.test/v.m3u8", referer=REFERER).oynat()
    assert mpv_komutu, "mpv çalıştırılmadı"
    assert f"--referrer={REFERER}" in mpv_komutu[0]


def test_referersiz_oynatmada_referrer_bayragi_yok(mpv_komutu):
    AdapterVideo(bolum(), "https://cdn.test/v.m3u8").oynat()
    assert not any(a.startswith("--referrer=") for a in mpv_komutu[0])


# ── CDN yedekleri ───────────────────────────────────────────────────────────
def _akislar():
    """Tranimaci'nin gerçek çıktı şekli: aynı kalite için CDN yedekleri."""
    return [
        {"url": "https://cdn1.test/1080p.mp4", "label": "1080p", "referer": REFERER},
        {"url": "https://cdn2.test/1080p.mp4", "label": "1080p (CDN2)", "referer": REFERER},
        {"url": "https://cdn3.test/1080p.mp4", "label": "1080p (CDN3)", "referer": REFERER},
        {"url": "https://cdn1.test/720p.mp4", "label": "720p", "referer": REFERER},
    ]


@pytest.fixture
def calisanlar(monkeypatch):
    """Yalnızca izin verilen URL'ler için info döndüren `extract_video_info`.

    Dönen liste denenen URL'leri sırasıyla tutar.
    """
    denenen: list = []

    def kur(*calisan_urller: str):
        izinli = set(calisan_urller)

        def sahte(url, _opts):
            denenen.append(url)
            return {"url": url, "ext": "mp4"} if url in izinli else {}

        monkeypatch.setattr(adapter_mod, "extract_video_info", sahte)
        return denenen

    return kur


def test_best_video_calisan_cdn_yedegine_geciyor(calisanlar):
    """İlk CDN 403 verdiğinde tek denemeyle vazgeçilip None dönülüyordu."""
    denenen = calisanlar("https://cdn2.test/1080p.mp4")
    vid = bolum(stream_provider=lambda _u: _akislar()).best_video()

    assert vid is not None, "çalışan CDN2 varken video bulunamadı"
    assert vid.url == "https://cdn2.test/1080p.mp4"
    assert denenen == ["https://cdn1.test/1080p.mp4", "https://cdn2.test/1080p.mp4"]
    assert vid.referer == REFERER


def test_best_video_yuksek_cozunurlugu_once_deniyor(calisanlar):
    """720p'ye ancak tüm 1080p yedekleri düştükten sonra inilmeli."""
    denenen = calisanlar("https://cdn1.test/720p.mp4")
    vid = bolum(stream_provider=lambda _u: _akislar()).best_video()

    assert vid.url == "https://cdn1.test/720p.mp4"
    assert denenen == [s["url"] for s in _akislar()], "sıralama bozuldu"


def test_best_video_deneme_sayisini_siniriyor(calisanlar):
    """Her CDN'i denemek yt-dlp zaman aşımlarıyla dakikalara mal olabilir."""
    denenen = calisanlar()          # hiçbiri çalışmıyor
    vid = bolum(stream_provider=lambda _u: _akislar()).best_video(early_subset=2)

    assert vid is None
    assert len(denenen) == 2


def test_best_video_urlsiz_akisi_atliyor(calisanlar):
    """En yüksek çözünürlüklü kayıt URL'siz gelince tüm bölüm ölü sayılıyordu."""
    akislar = [{"label": "1080p", "referer": REFERER},
               {"url": "https://cdn1.test/720p.mp4", "label": "720p"}]
    denenen = calisanlar("https://cdn1.test/720p.mp4")
    vid = bolum(stream_provider=lambda _u: akislar).best_video()

    assert vid is not None and vid.url == "https://cdn1.test/720p.mp4"
    assert denenen == ["https://cdn1.test/720p.mp4"]


def test_best_video_hicbiri_calismazsa_durumu_bildiriyor(calisanlar):
    calisanlar()
    durumlar: list = []
    vid = bolum(stream_provider=lambda _u: _akislar()).best_video(
        callback=lambda h: durumlar.append(h["status"]))

    assert vid is None
    assert durumlar[-1] == "hiçbiri çalışmıyor"


# ─────────────────────────────────────────────────────────────────────────────
# yt-dlp'nin baştan reddettiği konaklar bütçeyi yemesin
# ─────────────────────────────────────────────────────────────────────────────
# ESKİ HATA: uqload/yourupload/dood/filemoon adresleri yt-dlp'nin KnownPiracy
# listesinde; hiç oynamıyorlar ama `best_video`'nun ilk 8 adayına giriyorlardı.
# Arşivde 71.137 bölümün %37'sinde ilk 8'den en az biri böyleydi.

REDDEDILENLER = [
    "https://uqload.com/embed-a1.html", "https://www.yourupload.com/embed/a2",
    "https://dood.watch/e/a3", "https://filemoon.sx/e/a4",
    "https://uqload.com/embed-b1.html", "https://www.yourupload.com/embed/b2",
    "https://dood.watch/e/b3", "https://filemoon.sx/e/b4",
]


def test_reddedilen_konaklar_sona_itiliyor(calisanlar):
    normal = "https://video.sibnet.ru/shell.php?videoid=1"
    akislar = [{"url": u, "label": "1080p"} for u in REDDEDILENLER]
    akislar.append({"url": normal, "label": "480p"})
    denenen = calisanlar(normal)

    vid = bolum(stream_provider=lambda _u: akislar).best_video(early_subset=8)

    assert vid is not None and vid.url == normal
    assert denenen == [normal], f"reddedilen adres yoklandı: {denenen}"


def test_reddedilmeyenlerin_sirasi_korunuyor(calisanlar):
    """Kararlı sıralama: çözünürlük ve kaynağın CDN sırası bozulmamalı."""
    akislar = _akislar()
    akislar.insert(1, {"url": REDDEDILENLER[0], "label": "1080p"})
    denenen = calisanlar()

    bolum(stream_provider=lambda _u: akislar).best_video()

    assert denenen == [s["url"] for s in _akislar()] + [REDDEDILENLER[0]]


def test_ytdlp_reddeder_saf_adres_kontrolu():
    assert all(adapter_mod.ytdlp_reddeder(u) for u in REDDEDILENLER)
    assert not adapter_mod.ytdlp_reddeder("https://video.sibnet.ru/shell.php?videoid=1")
    assert not adapter_mod.ytdlp_reddeder(None)


def test_uqload_ve_yourupload_oncelikte_sona():
    """Ad ile de: uqload.io gibi aynalar yt-dlp'nin düzenli ifadesine takılmıyor."""
    from turkanime_api.common.oynatici_onceligi import oncelik_anahtari
    for ad in ("UQLOAD", "YOURUPLOAD"):
        assert oncelik_anahtari(ad) > oncelik_anahtari("SIBNET")
        assert oncelik_anahtari(ad) > oncelik_anahtari("BILINMEYEN")
