"""Kaldığın yer, otomatik ilerleme ve "Devam et / Sıradaki".

ESKİ DAVRANIŞ: uygulama oynatmanın nerede bittiğini hiç bilmiyordu. Her
bölümden sonra modal "Kaçıncı bölümü tamamladınız?" açılıyor, mpv 10.
saniyede kapatılsa bile bölüm "izlendi" yazılıyordu; konum mpv'nin adrese
bağlı kaydına kalmıştı ve token'lı adreslerde kayboluyordu.

Gerçek mpv yok: sahte `Popen`, mpv betiğinin yazacağı raporu yazıyor.
"""
from __future__ import annotations

import json

import pytest

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.common import kutuphane, mpv_oynatici
from turkanime_api.gui.qt.pages.episodes import EpisodePage
from turkanime_api.gui.qt.progress_dialog import ProgressDialog
from turkanime_api.sources.adapter import AdapterVideo

ANAHTAR = "--script-opts-append=" + mpv_oynatici.KONUM_ANAHTARI + "="


class Anime:
    def __init__(self, slug="naruto", title="Naruto"):
        self.slug = slug
        self.title = title


class Bolum:
    """Her `best_video` çağrısında FARKLI token'lı adres veren kaynak bölümü."""

    def __init__(self, slug="naruto-5-bolum"):
        self.slug = slug
        self.anime = Anime()
        self.cagri = 0

    def best_video(self, **_kwargs):
        self.cagri += 1
        return AdapterVideo(self, f"https://cdn/v.m3u8?token={self.cagri}",
                            player="SIBNET", referer="https://ref/")


@pytest.fixture
def mpv(monkeypatch, izole_ev):
    """Sahte mpv: `raporlar`dan sıradakini betiğin dosyasına yazar."""
    durum = {"argv": [], "raporlar": [], "diyalog": [], "anilist": []}

    class Proc:
        returncode = 0

        def __init__(self, argv):
            durum["argv"].append(list(argv))
            for arg in argv:             # --stream-record: mpv kaydı yazar
                if arg.startswith("--stream-record="):
                    with open(arg.split("=", 1)[1], "wb") as fp:
                        fp.write(b"\x00" * 64)
            rapor = durum["raporlar"].pop(0) if durum["raporlar"] else None
            yol = next((a[len(ANAHTAR):] for a in argv if a.startswith(ANAHTAR)), None)
            if rapor is not None and yol:
                with open(yol, "w", encoding="utf-8") as fp:
                    json.dump(rapor, fp)

        def wait(self):
            return 0

    monkeypatch.setattr(mpv_oynatici, "mpv_bul", lambda: "/opt/sahte/mpv")
    monkeypatch.setattr(mpv_oynatici.sp, "Popen", Proc)
    monkeypatch.setattr(ProgressDialog, "exec",
                        lambda self: durum["diyalog"].append(self) or 0)
    return durum


def _entry(bolum, title="Naruto 5. Bölüm"):
    return {"title": title, "obj": bolum, "kaynak": "TürkAnime",
            "kimlik": "naruto", "seri_adi": "Naruto"}


def _oynat(main_window, qtbot, entry, durum):
    main_window.anilist.ilerleme_yaz = (
        lambda seri, no, ad="": durum["anilist"].append((seri, no, ad)))
    main_window._on_play(entry)
    qtbot.waitUntil(lambda: main_window._playing is False, timeout=10000)
    qtbot.wait(100)          # `ui.post` ile gelen bitiş işleri


def _izlendi():
    return Dosyalar().gecmis["izlendi"].get("naruto", [])


def test_yarida_kapatinca_konum_saklaniyor_izlendi_yazilmiyor(main_window, qtbot, mpv):
    bolum = Bolum()
    mpv["raporlar"] = [{"konum": 734.2, "sure": 1420, "sebep": "quit"}]
    _oynat(main_window, qtbot, _entry(bolum), mpv)

    assert kutuphane.konum_getir("TürkAnime", "naruto", "naruto-5-bolum")["konum"] == 734.2
    assert _izlendi() == []
    assert mpv["diyalog"] == [], "yarıda kalan bölüm için ilerleme sorulmaz"
    assert mpv["anilist"] == []
    assert "12:14" in main_window.statusBar().currentMessage()

    # İkinci açılış: `best_video` başka (token'ı farklı) adres veriyor; konum
    # yine de bölümün anahtarından geliyor.
    mpv["raporlar"] = [{"konum": 800, "sure": 1420, "sebep": "quit"}]
    _oynat(main_window, qtbot, _entry(bolum), mpv)
    assert mpv["argv"][1][1] != mpv["argv"][0][1]
    assert "--start=734" in mpv["argv"][1]


def test_bolum_sonunda_ilerleme_diyalogsuz_yaziliyor(main_window, qtbot, mpv):
    bolum = Bolum()
    kutuphane.konum_kaydet("TürkAnime", "naruto", "naruto-5-bolum", 700, 1420)
    mpv["raporlar"] = [{"konum": 1419.9, "sure": 1420, "sebep": "eof"}]
    _oynat(main_window, qtbot, _entry(bolum), mpv)

    assert _izlendi() == ["naruto-5-bolum"]
    assert Dosyalar().gecmis["ilerleme"] == {"naruto": 5}
    assert mpv["anilist"] == [("naruto", 5, "Naruto")]
    assert mpv["diyalog"] == []
    assert kutuphane.konum_getir("TürkAnime", "naruto", "naruto-5-bolum") is None, \
        "biten bölüm bir dahaki sefere baştan başlamalı"


def test_yuzde_doksani_gecen_izleme_bitmis_sayiliyor(main_window, qtbot, mpv):
    mpv["raporlar"] = [{"konum": 1300, "sure": 1420, "sebep": "quit"}]
    _oynat(main_window, qtbot, _entry(Bolum()), mpv)
    assert _izlendi() == ["naruto-5-bolum"]
    assert mpv["diyalog"] == []


def test_ilerleme_geri_alinmiyor(main_window, qtbot, mpv):
    Dosyalar().set_ilerleme("naruto", 10)
    mpv["raporlar"] = [{"konum": 1420, "sure": 1420, "sebep": "eof"}]
    _oynat(main_window, qtbot, _entry(Bolum()), mpv)
    assert Dosyalar().gecmis["ilerleme"] == {"naruto": 10}
    assert mpv["anilist"] == []


def test_ilerlemeyi_sor_ayari_diyalogu_geri_getiriyor(main_window, qtbot, mpv):
    Dosyalar().set_ayar("ilerlemeyi sor", True)
    mpv["raporlar"] = [{"konum": 1420, "sure": 1420, "sebep": "eof"}]
    _oynat(main_window, qtbot, _entry(Bolum()), mpv)
    assert len(mpv["diyalog"]) == 1
    assert _izlendi() == ["naruto-5-bolum"]


def test_rapor_yoksa_eski_davranis(main_window, qtbot, mpv):
    """Lua'sız mpv: bölümün bitip bitmediği bilinmiyor → izlendi + soru."""
    _oynat(main_window, qtbot, _entry(Bolum()), mpv)
    assert _izlendi() == ["naruto-5-bolum"]
    assert len(mpv["diyalog"]) == 1


def test_izlerken_kaydet_yalnizca_tam_izlemede_indirilmis_sayiliyor(
        main_window, qtbot, mpv, tmp_path):
    Dosyalar().set_ayar(ayar_list={"izlerken kaydet": True,
                                   "indirilenler": str(tmp_path / "dl")})
    kayit = tmp_path / "dl" / "naruto" / "naruto-5-bolum.mkv"

    mpv["raporlar"] = [{"konum": 300, "sure": 1420, "sebep": "quit"}]
    _oynat(main_window, qtbot, _entry(Bolum()), mpv)
    assert f"--stream-record={kayit}" in mpv["argv"][0]
    assert not kayit.exists(), "yarım kayıt bir sonraki Oynat'ta açılmamalı"
    assert (kayit.parent / "naruto-5-bolum.yarim.mkv").exists()

    # Baştan sona (konum yok, dosya sonu): kayıt indirilmiş bölüm olur.
    kutuphane.konum_sil("TürkAnime", "naruto", "naruto-5-bolum")
    mpv["raporlar"] = [{"konum": 1420, "sure": 1420, "sebep": "eof"}]
    _oynat(main_window, qtbot, _entry(Bolum()), mpv)
    assert kayit.exists()


# ── Bölüm listesi: Devam et / Sıradaki ───────────────────────────────────────
class ListeBolumu:
    def __init__(self, no):
        self.slug = f"naruto-{no}-bolum"
        self.anime = Anime()


def _liste(qtbot):
    page = EpisodePage()
    qtbot.addWidget(page)
    bolumler = [{"title": f"{no}. Bölüm", "obj": ListeBolumu(no)} for no in range(1, 7)]
    page.load("TürkAnime", "naruto", "Naruto", episodes=bolumler)
    return page


def test_devam_et_yarim_kalan_bolume_gidiyor(izole_ev, qtbot):
    d = Dosyalar()
    for no in range(1, 5):
        d.set_gecmis("naruto", f"naruto-{no}-bolum", "izlendi")
    kutuphane.konum_kaydet("TürkAnime", "naruto", "naruto-5-bolum", 734.2, 1420)

    page = _liste(qtbot)
    assert not page.btnDevam.isHidden()
    assert "5" in page.btnDevam.text() and "12:14" in page.btnDevam.text()
    istenen = []
    page.play_requested.connect(istenen.append)
    page.btnDevam.click()
    assert istenen[0]["obj"].slug == "naruto-5-bolum"

    # 5 sonuna kadar izlendi: konum silinir, izlendi yazılır → Sıradaki 6.
    kutuphane.konum_sil("TürkAnime", "naruto", "naruto-5-bolum")
    d.set_gecmis("naruto", "naruto-5-bolum", "izlendi")
    page.refresh_history()
    assert page.btnDevam.text().startswith("▶ Sıradaki: 6. Bölüm")
    page.btnDevam.click()
    assert istenen[1]["obj"].slug == "naruto-6-bolum"


def test_hic_izlenmemis_ya_da_bitmis_seride_dugme_yok(izole_ev, qtbot):
    page = _liste(qtbot)
    assert page.btnDevam.isHidden()
    d = Dosyalar()
    d.set_gecmis("naruto", "naruto-6-bolum", "izlendi")
    page.refresh_history()
    assert page.btnDevam.isHidden(), "son bölüm izlendi: sıradaki yok"


def test_ayar_sayfasi_ilerlemeyi_sor(izole_ev, qtbot):
    from turkanime_api.gui.qt.pages.settings import SettingsPage
    page = SettingsPage()
    qtbot.addWidget(page)
    page.reload()
    assert not page.chkAskProgress.isChecked()
    page.chkAskProgress.setChecked(True)
    page.save()
    assert Dosyalar().ayarlar["ilerlemeyi sor"] is True
