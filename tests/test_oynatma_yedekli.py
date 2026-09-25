"""`common.oynatma.yedekli_oynat`: CLI ile Qt'nin ORTAK aday döngüsü.

ESKİ HATA: Qt'nin `_play_blocking`'i `best_video`'yu bir kez çağırıyor, mpv
2 ile (dosya oynatılamadı) çıksa bile bölümü "izlendi" yazıyordu; sıradaki
aday hiç denenmiyordu. CLI döngüyü doğru kuruyordu, iki kopya yerine döngü
ortak modüle taşındı. Buradaki testler çıkış kodu politikasını korur.

Gerçek mpv/yt-dlp yok: sahte bölüm `best_video`'nun `atla` sözleşmesine uyar.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from turkanime_api.common import oynatma


class Surec:
    def __init__(self, kod: Optional[int]):
        self.returncode = kod


class Video:
    def __init__(self, url: str, player: str):
        self.url = url
        self.player = player
        self.is_working = True


class SahteBolum:
    """`AdapterBolum.best_video` gibi: `atla`daki adresleri geçer."""

    def __init__(self, videolar: List[Video], durumlar=()):
        self.videolar = videolar
        self.durumlar = list(durumlar)      # callback'e basılacak hook'lar
        self.cagrilar: List[Dict[str, Any]] = []

    def best_video(self, callback=lambda _h: None, atla=None, **kwargs):
        self.cagrilar.append({"atla": set(atla or ()), **kwargs})
        for hook in self.durumlar:
            callback(hook)
        for video in self.videolar:
            if video.url not in (atla or ()):
                return video
        return None


def _bul(bolum):
    return lambda atla, cb: bolum.best_video(callback=cb, atla=atla)


def _kodlarla(kodlar: Dict[str, Optional[int]], oynatilan: List[str]):
    def oynat(video):
        oynatilan.append(video.url)
        kod = kodlar[video.url]
        return None if kod == "yok" else Surec(kod)
    return oynat


def test_oynatilamayan_aday_atlanip_siradaki_oynaniyor():
    a, b = Video("u1", "SIBNET"), Video("u2", "MAIL")
    bolum = SahteBolum([a, b])
    oynatilan: List[str] = []
    bildirimler: List[str] = []

    sonuc = oynatma.yedekli_oynat(_bul(bolum), _kodlarla({"u1": 2, "u2": 0}, oynatilan),
                                  bildir=bildirimler.append)

    assert sonuc.basarili and sonuc.video is b
    assert sonuc.denenen == ["u1"]
    assert oynatilan == ["u1", "u2"]
    assert bolum.cagrilar[1]["atla"] == {"u1"}, "başarısız adres atla ile geri verilmeli"
    assert a.is_working is False
    assert any("2. aday deneniyor (MAIL)" in m for m in bildirimler)


def test_hepsi_oynatilamazsa_deneme_butcesiyle_duruyor():
    videolar = [Video(f"u{i}", f"P{i}") for i in range(5)]
    bolum = SahteBolum(videolar)
    oynatilan: List[str] = []

    sonuc = oynatma.yedekli_oynat(
        _bul(bolum), _kodlarla({v.url: 2 for v in videolar}, oynatilan))

    assert not sonuc.basarili
    assert len(oynatilan) == oynatma.VARSAYILAN_DENEME == 3
    assert "oynatılamadı" in sonuc.sebep and "3" in sonuc.sebep
    assert sonuc.returncode == 2


def test_adaylar_tukenirse_oynatilamadi_deniyor():
    """İki aday da mpv'de düştü, üçüncü çağrıda `best_video` None döndü."""
    bolum = SahteBolum([Video("u1", "SIBNET"), Video("u2", "MAIL")])
    sonuc = oynatma.yedekli_oynat(_bul(bolum), _kodlarla({"u1": 3, "u2": 2}, []))
    assert not sonuc.basarili
    assert "oynatılamadı" in sonuc.sebep
    assert "SIBNET" in sonuc.sebep and "MAIL" in sonuc.sebep
    assert len(bolum.cagrilar) == 3


@pytest.mark.parametrize("kod", [1, 4])
def test_seçenek_hatasi_ve_kullanici_kesmesi_yeniden_denenmiyor(kod):
    """1: seçenekler her adayda aynı; 4: kullanıcı Ctrl+C ile kapattı —
    yeniden denemek istenmeyen yeni bir mpv penceresi açardı."""
    bolum = SahteBolum([Video("u1", "SIBNET"), Video("u2", "MAIL")])
    oynatilan: List[str] = []
    sonuc = oynatma.yedekli_oynat(_bul(bolum), _kodlarla({"u1": kod, "u2": 0}, oynatilan))
    assert not sonuc.basarili
    assert oynatilan == ["u1"]
    assert len(bolum.cagrilar) == 1
    assert str(kod) in sonuc.sebep


def test_oynatici_yoksa_yeniden_denenmiyor():
    """mpv yok (None): sıradaki aday da aynı sonucu verir."""
    bolum = SahteBolum([Video("u1", "SIBNET"), Video("u2", "MAIL")])
    oynatilan: List[str] = []
    sonuc = oynatma.yedekli_oynat(_bul(bolum), _kodlarla({"u1": "yok", "u2": 0}, oynatilan))
    assert not sonuc.basarili
    assert sonuc.sebep == oynatma.OYNATICI_YOK
    assert oynatilan == ["u1"] and len(bolum.cagrilar) == 1


@pytest.mark.parametrize("donus", ["proc", Surec(None), Surec(0)])
def test_returncode_olmayan_donus_basari_sayiliyor(donus):
    bolum = SahteBolum([Video("u1", "SIBNET")])
    sonuc = oynatma.yedekli_oynat(_bul(bolum), lambda _v: donus)
    assert sonuc.basarili


def test_calisan_video_yoksa_sebep_oynaticilari_sayiyor():
    """Eskiden yalnızca "çalışan video bulunamadı" deniyordu."""
    bolum = SahteBolum([], durumlar=[
        {"current": 1, "total": 2, "player": "SIBNET", "status": "çalışmıyor"},
        {"current": 2, "total": 2, "player": "MAIL", "status": "çalışmıyor"},
    ])
    iletilen: List[dict] = []
    sonuc = oynatma.yedekli_oynat(_bul(bolum), lambda _v: Surec(0),
                                  callback=iletilen.append)
    assert not sonuc.basarili
    assert "çalışan video bulunamadı" in sonuc.sebep
    assert "SIBNET" in sonuc.sebep and "MAIL" in sonuc.sebep
    assert "2 aday" in sonuc.sebep
    assert len(iletilen) == 2, "callback çağıranın ilerlemesine de iletilmeli"


def test_bul_hatasi_yukseliyor():
    """Arşiv okunamadıysa sebebi çağıran söyler; "video yok" denmemeli."""
    def bul(_atla, _cb):
        raise OSError("arşiv okunamadı")
    with pytest.raises(OSError, match="arşiv"):
        oynatma.yedekli_oynat(bul, lambda _v: Surec(0))


def test_modul_qt_ve_ytdlp_cekmiyor():
    """CLI bu modülü kullanıyor; PySide6/yt-dlp çekmesi CLI'ı onlara bağlardı."""
    kaynak = Path(oynatma.__file__).read_text(encoding="utf-8")
    moduller = set()
    for dugum in ast.walk(ast.parse(kaynak)):
        if isinstance(dugum, ast.ImportFrom):
            moduller.add(dugum.module or "")
        elif isinstance(dugum, ast.Import):
            moduller.update(a.name for a in dugum.names)
    assert not any(m.startswith(("PySide6", "yt_dlp")) or "gui" in m
                   for m in moduller), moduller
