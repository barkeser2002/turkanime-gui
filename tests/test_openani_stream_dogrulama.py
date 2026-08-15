"""OpenAnime ölü stream uçlarını sessizce vermesin.

Canlı ölçüm: `one-piece/1/1` için sayfadan iki uç çıkarılıyor (1080p + 720p) ve
ikisi de CDN'den `HTTP 404 {"code":"not_found","message":"File with such name
does not exist."}` alıyor. Kullanıcı "Oynat"a basınca sebepsiz bir hata
görüyordu; hangi kaynağın neden çalışmadığı hiçbir yerde yazmıyordu.

TRAnimeİzle aynı durumda net konuşuyor ("cookie ayarlanmamış, Ayarlar'dan
girin"); OpenAnime sessizdi. Bu testler sessizliğin geri gelmediğini
doğruluyor.

Uçların *neden* 404 verdiği ayrı bir konu: api.openani.me "Vanguard" auth
istiyor (401) ve CDN yolunun doğru biçimi hesapla bulunamadı — anime id,
storage_cluster_id ve fansub id ile kurulan beş varyantın hepsi 404/403.
Bu yüzden ölü uçlar yine de döndürülüyor (yoklama oynatıcıdan farklı
başlıklar kullanıyor, yanılma payı var) — ama artık sebep yazılıyor.

Testler ağa çıkmıyor: yoklama fonksiyonu sahteleniyor.
"""
import pytest

import turkanime_api.sources.openani as oa


@pytest.fixture
def akislar(monkeypatch):
    """`adapter.get_video_urls`'ü sabitleyip yoklamayı kontrol edilebilir kıl."""
    def kur(urller, yoklama):
        videolar = [
            {"url": u, "quality": f"{720 + i * 360}p", "format": "mp4",
             "provider_data": {"fansub": "AoiSubs"}, "referer": "https://openani.me/"}
            for i, u in enumerate(urller)
        ]
        monkeypatch.setattr(oa.adapter, "get_video_urls", lambda _d: videolar)
        monkeypatch.setattr(oa, "_uc_calisiyor",
                            lambda url, referer=None: yoklama.get(url))
        monkeypatch.setattr(oa, "UCLARI_DOGRULA", True)
    return kur


def test_olu_uclar_icin_sebep_yaziliyor(akislar, capsys):
    """ESKİ DAVRANIŞ: iki ölü uç sessizce dönüyordu."""
    akislar(["https://cdn/a.mp4", "https://cdn/b.mp4"],
            {"https://cdn/a.mp4": False, "https://cdn/b.mp4": False})
    sonuc = oa.get_episode_streams("one-piece/1/1")

    cikti = capsys.readouterr().out
    assert "OpenAnime" in cikti and "404" in cikti, "kullanıcıya sebep söylenmedi"
    assert len(sonuc) == 2, "ölü de olsa uçlar atılmamalı (yoklama yanılabilir)"


def test_calisan_uc_varsa_sessiz(akislar, capsys):
    """Mutlu yolda gürültü yapma."""
    akislar(["https://cdn/a.mp4", "https://cdn/b.mp4"],
            {"https://cdn/a.mp4": True, "https://cdn/b.mp4": True})
    sonuc = oa.get_episode_streams("one-piece/1/1")

    assert len(sonuc) == 2
    assert "OpenAnime" not in capsys.readouterr().out


def test_olu_uclar_eleniyor_calisan_kaliyor(akislar):
    """Karışık durumda ölü uç oynatıcıya hiç gitmemeli."""
    akislar(["https://cdn/olu.mp4", "https://cdn/saglam.mp4"],
            {"https://cdn/olu.mp4": False, "https://cdn/saglam.mp4": True})
    sonuc = oa.get_episode_streams("one-piece/1/1")

    assert [s["url"] for s in sonuc] == ["https://cdn/saglam.mp4"]


def test_karar_verilemeyen_uc_atilmiyor(akislar, capsys):
    """Ağ hatası "ölü" demek değil; uç korunmalı ve uyarı çıkmamalı."""
    akislar(["https://cdn/a.mp4"], {"https://cdn/a.mp4": None})
    sonuc = oa.get_episode_streams("one-piece/1/1")

    assert len(sonuc) == 1
    assert "OpenAnime" not in capsys.readouterr().out


def test_dogrulama_kapatilabiliyor(akislar, monkeypatch):
    """CLI/test için yoklama kapatılabilmeli (ağ istemeyen bağlamlar)."""
    akislar(["https://cdn/a.mp4"], {"https://cdn/a.mp4": False})
    monkeypatch.setattr(oa, "UCLARI_DOGRULA", False)
    cagri = []
    monkeypatch.setattr(oa, "_uc_calisiyor",
                        lambda *a, **k: cagri.append(1))

    sonuc = oa.get_episode_streams("one-piece/1/1")
    assert len(sonuc) == 1
    assert not cagri, "kapalıyken ağa çıkılmamalı"


def test_hic_stream_yoksa_yoklama_yapilmiyor(monkeypatch):
    monkeypatch.setattr(oa.adapter, "get_video_urls", lambda _d: [])
    cagri = []
    monkeypatch.setattr(oa, "_uc_calisiyor", lambda *a, **k: cagri.append(1))
    assert oa.get_episode_streams("one-piece/1/1") == []
    assert not cagri


# ─────────────────────────────────────────────────────────────────────────────
# `_uc_calisiyor` karar tablosu
# ─────────────────────────────────────────────────────────────────────────────

class SahteYanit:
    def __init__(self, status_code, content_type=""):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}


@pytest.mark.parametrize("kod,tur,beklenen", [
    (206, "video/mp4", True),
    (200, "video/mp4", True),
    (200, "application/x-mpegurl", True),
    (200, "application/octet-stream", True),
    (200, "application/json;charset=utf-8", False),   # CDN hata gövdesi
    (200, "text/html; charset=utf-8", False),          # hata/reklam sayfası
    (404, "application/json", False),
    (403, "text/html", False),
    (500, "video/mp4", False),
    (200, "", None),                                   # tür yok → karar verme
])
def test_uc_calisiyor_karar_tablosu(monkeypatch, kod, tur, beklenen):
    sahte = type("M", (), {"get": staticmethod(
        lambda *a, **k: SahteYanit(kod, tur))})
    monkeypatch.setitem(__import__("sys").modules, "curl_cffi",
                        type("P", (), {"requests": sahte}))
    monkeypatch.setitem(__import__("sys").modules, "curl_cffi.requests", sahte)
    assert oa._uc_calisiyor("https://cdn/x.mp4") is beklenen


def test_uc_calisiyor_ag_hatasinda_karar_vermiyor(monkeypatch):
    """Ağ patlarsa "ölü" deme — uç atılır ve kullanıcı stream'siz kalırdı."""
    def patla(*_a, **_k):
        raise ConnectionError("ağ koptu")

    sahte = type("M", (), {"get": staticmethod(patla)})
    monkeypatch.setitem(__import__("sys").modules, "curl_cffi",
                        type("P", (), {"requests": sahte}))
    monkeypatch.setitem(__import__("sys").modules, "curl_cffi.requests", sahte)
    assert oa._uc_calisiyor("https://cdn/x.mp4") is None
