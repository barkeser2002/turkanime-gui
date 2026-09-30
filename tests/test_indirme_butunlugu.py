"""İndirme bütünlüğü: HTTP hatası "tamamlandı" sayılmıyor, dosya diskte doğrulanıyor.

ESKİ HATA: `get_ydl_opts` 'ignoreerrors': 'only_download' veriyor; o ayarla
yt-dlp medya adresi 403 dönünce istisna fırlatmıyor, `download_with_info_file`
yalnızca 1 döndürüyordu. `AdapterVideo.indir` dönüşü okumuyordu: arayüz işi
"tamamlandı" yazıyor, geçmişe "indirildi" düşüyor, ⬇ rozeti çıkıyordu — klasör
BOŞKEN. CLI de aynı yalancı başarıyı veriyordu. Ayrıca OpenAnime'nin sahte
info'sunda `extractor` yoktu; her OpenAnime indirmesi "hata: 'extractor'" ile
düşüyordu.

Testler GERÇEK yt-dlp'yi yerel bir HTTP sunucusuna (127.0.0.1, conftest'in ağ
mandalından geçer) karşı koşturuyor: sahte bir `YoutubeDL` tam olarak bu
hatayı gizlerdi. Yalnızca `info` yoklaması sahte (medya adresini gösteren
hazır sözlük); indirme adımının kendisi gerçek.
"""
from __future__ import annotations

import hashlib
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

import pytest

from turkanime_api.common import dosya_adi
from turkanime_api.sources import adapter as adapter_mod
from turkanime_api.sources.adapter import AdapterAnime, AdapterBolum, AdapterVideo

GOVDE = os.urandom(64 * 1024)
SERI, BOLUM = "naruto-test", "naruto-test-1-bolum"


class _Medya(BaseHTTPRequestHandler):
    durum = 200

    def do_GET(self):  # noqa: N802
        if self.durum != 200:
            self.send_response(self.durum)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        bas = 0
        aralik = self.headers.get("Range") or ""
        if aralik.startswith("bytes="):
            bas = int(aralik[6:].split("-")[0] or 0)
        parca = GOVDE[bas:]
        self.send_response(206 if bas else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(parca)))
        if bas:
            self.send_header("Content-Range",
                             f"bytes {bas}-{len(GOVDE) - 1}/{len(GOVDE)}")
        self.end_headers()
        self.wfile.write(parca)

    def log_message(self, *_a):
        pass


@pytest.fixture
def medya():
    """``medya(durum)`` → ``http://127.0.0.1:<port>/video.mp4``."""
    sunucular = []

    def _baslat(durum: int = 200) -> str:
        srv = HTTPServer(("127.0.0.1", 0), type("H", (_Medya,), {"durum": durum}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        sunucular.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}/video.mp4"

    yield _baslat
    for srv in sunucular:
        srv.shutdown()
        srv.server_close()


def _bolum():
    return AdapterBolum(f"{SERI}/{BOLUM}", "1. Bölüm", AdapterAnime(SERI, "Naruto Test"),
                        stream_provider=lambda _u: [], player_name="ANIMEDEPO",
                        slug=BOLUM)


def _video(adres: str, player: str = "SIBNET", monkeypatch=None) -> AdapterVideo:
    """`info` yoklaması "başarılı": generic çıkarıcının döndüreceği biçim."""
    if monkeypatch is not None:
        monkeypatch.setattr(adapter_mod, "extract_video_info", lambda url, _o: {
            "id": "video", "title": "video", "url": url, "ext": "mp4",
            "extractor": "generic", "extractor_key": "Generic",
            "webpage_url": url})
    video = AdapterVideo(_bolum(), adres, player=player)
    # Ortamdaki proxy değişkenleri yerel sunucuyu dışarı yönlendirmesin.
    video.ydl_opts["proxy"] = ""
    video.ydl_opts["retries"] = 0
    assert video.is_working
    return video


def _hedef(kok) -> str:
    return os.path.join(str(kok), SERI, BOLUM)


# ── AdapterVideo.indir ──────────────────────────────────────────────────────
def test_403_indirmesi_hata_firlatiyor_dosya_yok(medya, tmp_path, monkeypatch):
    from yt_dlp.utils import DownloadError

    video = _video(medya(403), monkeypatch=monkeypatch)
    with pytest.raises(DownloadError, match="403"):
        video.indir(output=str(tmp_path))
    assert dosya_adi.indirilen_dosya(_hedef(tmp_path)) is None


def test_200_indirmesi_sunulan_baytlari_yaziyor(medya, tmp_path, monkeypatch):
    video = _video(medya(200), monkeypatch=monkeypatch)
    video.indir(output=str(tmp_path))
    yol = dosya_adi.indirilen_dosya(_hedef(tmp_path))
    assert yol and yol.endswith(".mp4")
    with open(yol, "rb") as fp:
        assert hashlib.sha1(fp.read()).digest() == hashlib.sha1(GOVDE).digest()


def test_openani_indirmesi_extractor_hatasi_vermiyor(medya, tmp_path):
    """ESKİ HATA: sahte info'da `id`/`extractor` yoktu → KeyError('extractor')."""
    video = AdapterVideo(_bolum(), medya(200), player="OPENANI")
    video.ydl_opts["proxy"] = ""
    video.indir(output=str(tmp_path))
    yol = dosya_adi.indirilen_dosya(_hedef(tmp_path))
    assert yol is not None
    with open(yol, "rb") as fp:
        assert fp.read() == GOVDE


def test_cikis_kodu_sifir_ama_dosya_yoksa_hata(tmp_path, monkeypatch):
    """İkinci güvence: yt-dlp 0 dönse de yalnızca `.part` kaldıysa bitmemiştir."""
    from yt_dlp.utils import DownloadError

    class YarimBirakan:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def download_with_info_file(self, _yol):
            hedef = self.opts["outtmpl"]["default"].replace(".%(ext)s", ".mp4.part")
            os.makedirs(os.path.dirname(hedef), exist_ok=True)
            with open(hedef, "wb") as fp:
                fp.write(b"yarim")
            return 0

    monkeypatch.setattr(adapter_mod, "YoutubeDL", YarimBirakan)
    video = _video("https://cdn.test/v.mp4", monkeypatch=monkeypatch)
    with pytest.raises(DownloadError, match="diskte yok"):
        video.indir(output=str(tmp_path))


# ── Qt indirme kuyruğu ──────────────────────────────────────────────────────
class _TekVideoluBolum:
    def __init__(self, video):
        self.video = video
        self.slug = BOLUM
        self.anime = AdapterAnime(SERI, "Naruto Test")
        self.cagrilar: List[dict] = []

    def best_video(self, **kwargs):
        self.cagrilar.append(kwargs)
        return self.video


def _indirildi():
    from turkanime_api.cli.dosyalar import Dosyalar
    return Dosyalar().gecmis["indirildi"].get(SERI, [])


def _kuyrukta_indir(qtbot, bolum, output):
    from turkanime_api.gui.qt.indirme import BITMIS_DURUMLAR, DownloadManager

    mgr = DownloadManager()
    biten: list = []
    mgr.finished.connect(lambda tid, ok, mesaj: biten.append((tid, ok, mesaj)))
    task_id = mgr.enqueue({"title": "Naruto Test 1. Bölüm", "obj": bolum},
                          output=str(output))
    qtbot.waitUntil(lambda: mgr.durum(task_id) in BITMIS_DURUMLAR, timeout=20000)
    qtbot.waitUntil(lambda: bool(biten), timeout=5000)
    return mgr, task_id, biten


def test_kuyruk_403te_hata_diyor_gecmise_yazmiyor(
        qtbot, izole_ev, ayarla, medya, tmp_path, monkeypatch):
    from turkanime_api.gui.qt.indirme import DURUM_HATA
    from turkanime_api.gui.qt import prefs

    ayarla(**{"aria2c kullan": False})
    bolum = _TekVideoluBolum(_video(medya(403), monkeypatch=monkeypatch))

    mgr, task_id, biten = _kuyrukta_indir(qtbot, bolum, tmp_path / "out")

    assert mgr.durum(task_id) == DURUM_HATA
    _, ok, mesaj = biten[-1]
    assert ok is False and "403" in mesaj
    assert BOLUM not in _indirildi()
    assert prefs.Gecmis.yukle().durum(bolum) == (False, False), "⬇ rozeti çıkmamalı"


def test_kuyruk_200de_dosyayi_yaziyor_gecmise_bir_kez(
        qtbot, izole_ev, ayarla, medya, tmp_path, monkeypatch):
    from turkanime_api.gui.qt.indirme import DURUM_TAMAMLANDI

    ayarla(**{"aria2c kullan": False})
    bolum = _TekVideoluBolum(_video(medya(200), monkeypatch=monkeypatch))

    mgr, task_id, _ = _kuyrukta_indir(qtbot, bolum, tmp_path / "out")

    assert mgr.durum(task_id) == DURUM_TAMAMLANDI
    yol = dosya_adi.indirilen_dosya(_hedef(tmp_path / "out"))
    with open(yol, "rb") as fp:
        assert fp.read() == GOVDE
    assert _indirildi().count(BOLUM) == 1


# ── CLI ─────────────────────────────────────────────────────────────────────
def test_cli_403_indirmesi_gecmise_yazmiyor(izole_ev, medya, tmp_path, monkeypatch, capsys):
    from rich.table import Table

    from turkanime_api.cli import cli_tools
    from turkanime_api.cli.dosyalar import Dosyalar

    dosya = Dosyalar()
    dosya.set_ayar(ayar_list={"aria2c kullan": False,
                              "indirilenler": str(tmp_path / "out")})
    bolum = _TekVideoluBolum(_video(medya(403), monkeypatch=monkeypatch))

    cli_tools.indirme_task_cli(bolum, Table.grid(), Dosyalar())

    assert Dosyalar().gecmis["indirildi"] == {}
    assert "indirilemedi" in capsys.readouterr().out


# ── Yardımcılar ─────────────────────────────────────────────────────────────
def test_indirilen_dosya_yarim_dosyalari_saymiyor(tmp_path):
    hedef = str(tmp_path / "bolum")
    for ad in ("bolum.mp4.part", "bolum.mp4.ytdl", "bolum.mp4.part.aria2",
               "bolum.temp.mp4", "bolum.mp4.part-Frag3", "bolum-2.mp4"):
        (tmp_path / ad).write_bytes(b"x")
    assert dosya_adi.indirilen_dosya(hedef) is None
    (tmp_path / "bolum.mp4").write_bytes(b"tam")
    assert dosya_adi.indirilen_dosya(hedef) == str(tmp_path / "bolum.mp4")


def test_yarim_dosyalari_sil_yalnizca_yarimlari_siliyor(tmp_path):
    hedef = str(tmp_path / "bolum")
    for ad in ("bolum.mp4.part", "bolum.mp4.ytdl", "bolum.mp4", "bolum-2.mp4.part"):
        (tmp_path / ad).write_bytes(b"x")
    assert dosya_adi.yarim_dosyalari_sil(hedef) == 2
    assert sorted(os.listdir(tmp_path)) == ["bolum-2.mp4.part", "bolum.mp4"]
