"""İndirilen bölümler: ağsız yerel oynatma, "Oynat"/"Klasörü Aç", bildirim.

ESKİ EKSİK: "Oynat" her zaman akıştan oynatıyordu (`best_video` → ağ);
indirme klasöründeki bölüm uygulamadan çevrimdışı izlenemiyordu. Biten
indirmede ne dosyayı ne klasörü açan bir şey vardı; satırda yalnızca bölüm
başlığı ("3. Bölüm") duruyordu ve toplu indirmenin bittiği 6 saniyelik bir
durum çubuğu mesajıyla duyuruluyordu.

Gerçek mpv/ağ yok: `subprocess.Popen` ve `QDesktopServices` sahte.
"""
from __future__ import annotations

import os

import pytest

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.common import mpv_oynatici
from turkanime_api.common.dosya_adi import bolum_hedefi, oynatilabilir_dosya
from turkanime_api.gui.qt import indirme as downloads_mod
from turkanime_api.gui.qt.indirme import (
    BITMIS_DURUMLAR, DURUM_HATA, DURUM_IPTAL, DURUM_TAMAMLANDI, DownloadManager,
    satir_basligi,
)
from turkanime_api.gui.qt.progress_dialog import ProgressDialog


class Anime:
    def __init__(self, slug="naruto-test", title="Naruto Test"):
        self.slug = slug
        self.title = title


class Surec:
    def __init__(self, kod=0):
        self.returncode = kod


class AkisVideo:
    def __init__(self, kod=0):
        self.url = "https://ornek/akis.m3u8"
        self.player = "SIBNET"
        self.kod = kod
        self.oynatildi = 0

    def oynat(self, dakika_hatirla=False):
        self.oynatildi += 1
        return Surec(self.kod)


class Bolum:
    def __init__(self, slug="naruto-test-1-bolum", video=None, anime=None):
        self.slug = slug
        self.anime = anime or Anime()
        self.video = video
        self.best_cagri = 0

    def best_video(self, **_kwargs):
        self.best_cagri += 1
        return self.video


@pytest.fixture
def dl_klasoru(izole_ev, tmp_path):
    hedef = tmp_path / "dl"
    Dosyalar().set_ayar("indirilenler", str(hedef))
    (hedef / "naruto-test").mkdir(parents=True)
    return hedef / "naruto-test"


@pytest.fixture
def sahte_mpv(monkeypatch):
    """`Popen` argümanlarını kaydeder; dönüş kodu `kodlar`dan sırayla."""
    kayit = {"argv": [], "kodlar": []}

    class Proc:
        def __init__(self, argv):
            kayit["argv"].append(list(argv))
            self.returncode = kayit["kodlar"].pop(0) if kayit["kodlar"] else 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(mpv_oynatici, "mpv_bul", lambda: "/opt/sahte/mpv")
    monkeypatch.setattr(mpv_oynatici.sp, "Popen", Proc)
    monkeypatch.setattr(ProgressDialog, "exec", lambda self: 0)
    return kayit


def _oynat(main_window, qtbot, entry):
    main_window._on_play(entry)
    qtbot.waitUntil(lambda: main_window._playing is False, timeout=10000)
    qtbot.wait(50)


# ── Diskte ne sayılır? ───────────────────────────────────────────────────────
def test_oynatilabilir_dosya_yan_dosyalari_ve_yarimlari_eliyor(tmp_path):
    hedef = str(tmp_path / "b")
    for ad, icerik in (("b.mp4.part", b"x" * 50), ("b.ytdl", b"x"),
                       ("b.info.json", b"{}" * 90), ("b.tr.vtt", b"x" * 80),
                       ("b.mkv", b"")):
        (tmp_path / ad).write_bytes(icerik)
    assert oynatilabilir_dosya(hedef) is None, "yalnızca yarım/yan/boş dosya var"

    (tmp_path / "b.mp4").write_bytes(b"x" * 10)
    (tmp_path / "b.webm").write_bytes(b"x" * 20)
    assert oynatilabilir_dosya(hedef) == str(tmp_path / "b.webm"), "en büyüğü"


# ── Yerel oynatma ────────────────────────────────────────────────────────────
def test_indirilmis_bolum_agsiz_yerel_dosyadan_oynatiliyor(
        dl_klasoru, main_window, qtbot, sahte_mpv):
    dosya = dl_klasoru / "naruto-test-1-bolum.mp4"
    dosya.write_bytes(b"\x00" * 64)
    bolum = Bolum(video=AkisVideo())

    _oynat(main_window, qtbot, {"title": "Naruto Test 1. Bölüm", "obj": bolum})

    assert bolum.best_cagri == 0, "yerel dosya varken ağa çıkılmamalı"
    assert sahte_mpv["argv"][0][:2] == ["/opt/sahte/mpv", str(dosya.resolve())]
    assert "naruto-test-1-bolum" in Dosyalar().gecmis["izlendi"]["naruto-test"]


@pytest.mark.parametrize("ad,icerik", [("naruto-test-1-bolum.mp4.part", b"x" * 64),
                                       ("naruto-test-1-bolum.ytdl", b"x" * 64),
                                       ("naruto-test-1-bolum.mp4", b"")])
def test_yarim_ya_da_bos_dosya_varsa_akistan_oynatiliyor(
        dl_klasoru, main_window, qtbot, sahte_mpv, ad, icerik):
    (dl_klasoru / ad).write_bytes(icerik)
    video = AkisVideo()
    bolum = Bolum(video=video)
    _oynat(main_window, qtbot, {"title": "1. Bölüm", "obj": bolum})
    assert bolum.best_cagri == 1 and video.oynatildi == 1
    assert sahte_mpv["argv"] == []


def test_bozuk_yerel_dosyada_akisa_geciliyor(dl_klasoru, main_window, qtbot, sahte_mpv):
    (dl_klasoru / "naruto-test-1-bolum.mp4").write_bytes(b"\x00" * 64)
    sahte_mpv["kodlar"] = [2]             # mpv: dosya oynatılamadı
    video = AkisVideo()
    bolum = Bolum(video=video)
    _oynat(main_window, qtbot, {"title": "1. Bölüm", "obj": bolum})
    assert len(sahte_mpv["argv"]) == 1 and video.oynatildi == 1


def test_kayitli_yol_ayar_degisse_de_bulunuyor(izole_ev, tmp_path, main_window,
                                              qtbot, sahte_mpv):
    """İndirme bittiğinde kaydedilen yol, klasör ayarı sonradan değişse de geçerli."""
    eski = tmp_path / "eski" / "naruto-test-1-bolum.mkv"
    eski.parent.mkdir()
    eski.write_bytes(b"\x00" * 64)
    Dosyalar().set_ayar("indirilenler", str(tmp_path / "yeni"))
    bolum = Bolum(video=AkisVideo())
    _oynat(main_window, qtbot, {"title": "1. Bölüm", "obj": bolum,
                                "yerel_dosya": str(eski)})
    assert bolum.best_cagri == 0
    assert sahte_mpv["argv"][0][1] == str(eski)


# ── İndirme satırı (web sayfası) ─────────────────────────────────────────────
@pytest.mark.parametrize("durum,gorunur", [(DURUM_TAMAMLANDI, True),
                                           (DURUM_HATA, False), (DURUM_IPTAL, False)])
def test_oynat_ve_klasor_yalnizca_tamamlananda(main_window, web, durum, gorunur):
    y = main_window.downloads
    main_window.show_page("downloads")
    y.added.emit("dl1", "Naruto — 1. Bölüm")
    y.state.emit("dl1", durum)
    satir = "document.querySelector('.indirme-satiri[data-id=dl1]')"
    web.bekle(f"!!{satir} && {satir}.querySelector('.durum-cipi').textContent.length > 0")
    dugmeler = web.js(f"Array.from({satir}.querySelectorAll('button')).map(b => b.textContent)")
    assert ("Oynat" in dugmeler) == gorunur
    assert ("Klasörü Aç" in dugmeler) == gorunur


def test_satir_basligi_seri_adini_tasiyor():
    tranimaci = {"title": "3. Bölüm", "obj": Bolum(anime=Anime("jjk", "Jujutsu Kaisen"))}
    assert satir_basligi(tranimaci) == "Jujutsu Kaisen — 3. Bölüm"
    damgali = {"title": "3. Bölüm", "seri_adi": "JJK 2", "obj": Bolum()}
    assert satir_basligi(damgali) == "JJK 2 — 3. Bölüm"
    # Arşiv başlıkları adı zaten içeriyor: iki kez yazılmaz.
    arsiv = {"title": "Naruto Test 3. Bölüm", "obj": Bolum()}
    assert satir_basligi(arsiv) == "Naruto Test 3. Bölüm"


class DosyaYazanVideo:
    player = "GDRIVE"

    def __init__(self, bolum):
        self.bolum = bolum
        self.url = "https://ornek/v.mp4"

    def indir(self, callback=None, output=""):
        hedef = bolum_hedefi(output, self.bolum)
        os.makedirs(os.path.dirname(hedef), exist_ok=True)   # yt-dlp de kuruyor
        with open(hedef + ".mp4", "wb") as fp:
            fp.write(b"\x00" * 128)
        callback({"status": "finished"})


def test_biten_indirmeden_oynat_ve_klasor_ac(izole_ev, qtbot, tmp_path, monkeypatch):
    from turkanime_api.gui.web.kopru import Kopru
    from turkanime_api.gui.web.uclar_indirme import IndirmeUclari
    acilan = []

    class Masaustu:
        @staticmethod
        def openUrl(url):          # noqa: N802 (Qt imzası)
            acilan.append(url)
            return True

    monkeypatch.setattr(downloads_mod, "QDesktopServices", Masaustu)
    mgr = DownloadManager()
    istenen = []
    uclar = IndirmeUclari(Kopru(), mgr, oynat=istenen.append,
                          indirme_dizini=lambda: str(tmp_path))

    bolum = Bolum(slug="naruto-test-3-bolum", anime=Anime("jjk", "Jujutsu Kaisen"))
    bolum.video = DosyaYazanVideo(bolum)
    entry = {"title": "3. Bölüm", "obj": bolum}
    tid = mgr.enqueue(entry, output=str(tmp_path))
    qtbot.waitUntil(lambda: mgr.durum(tid) in BITMIS_DURUMLAR, timeout=10000)
    assert mgr.durum(tid) == DURUM_TAMAMLANDI

    (satir,) = uclar.indirmeler()["satirlar"]
    assert satir["baslik"] == "Jujutsu Kaisen — 3. Bölüm"
    assert mgr.dosya(tid) == str(tmp_path / "jjk" / "naruto-test-3-bolum.mp4")
    assert entry["yerel_dosya"] == mgr.dosya(tid)

    uclar.indirme_eylem(tid, "klasor")
    assert [u.toLocalFile() for u in acilan] == [str(tmp_path / "jjk")]
    uclar.indirme_eylem(tid, "oynat")
    assert istenen == [entry]

    uclar.indirme_toplu("klasor")
    assert len(acilan) == 2, "üst düğme indirme klasörünü açar"


def test_kuyruk_bitince_arka_plandaysa_bir_kez_bildiriliyor(main_window, monkeypatch):
    bildirim = []
    kalan = [["dl2", "dl3"], ["dl3"], []]
    durumlar = {"dl1": DURUM_TAMAMLANDI, "dl2": DURUM_TAMAMLANDI, "dl3": DURUM_HATA}
    monkeypatch.setattr(main_window, "_bildir", lambda b, m: bildirim.append(m))
    monkeypatch.setattr(main_window, "isActiveWindow", lambda: False)
    monkeypatch.setattr(main_window.downloads, "active_ids", lambda: kalan.pop(0))
    monkeypatch.setattr(main_window.downloads, "durum", durumlar.get)

    main_window._on_download_finished("dl1", True, "tamam")
    main_window._on_download_finished("dl2", True, "tamam")
    assert bildirim == []
    main_window._on_download_finished("dl3", False, "hata")
    assert bildirim == ["2 bölüm indirildi, 1 hata"]


def test_pencere_ondeyse_ve_hepsi_iptalse_bildirim_yok(main_window, monkeypatch):
    bildirim = []
    monkeypatch.setattr(main_window, "_bildir", lambda b, m: bildirim.append(m))
    monkeypatch.setattr(main_window.downloads, "active_ids", lambda: [])
    monkeypatch.setattr(main_window, "isActiveWindow", lambda: True)
    monkeypatch.setattr(main_window.downloads, "durum", lambda _t: DURUM_TAMAMLANDI)
    main_window._on_download_finished("dl1", True, "tamam")
    assert bildirim == []

    monkeypatch.setattr(main_window, "isActiveWindow", lambda: False)
    monkeypatch.setattr(main_window.downloads, "durum", lambda _t: DURUM_IPTAL)
    main_window._on_download_finished("dl2", False, DURUM_IPTAL)
    assert bildirim == [], "yalnızca iptal: bildirilecek sonuç yok"


def test_bildirim_tepsi_yoksa_gorev_cubugunu_uyariyor(main_window, monkeypatch):
    from turkanime_api.gui.qt import app as app_mod
    uyarilan = []
    monkeypatch.setattr(app_mod.QSystemTrayIcon, "isSystemTrayAvailable",
                        staticmethod(lambda: False))
    monkeypatch.setattr(app_mod.QApplication, "alert",
                        staticmethod(lambda w, *a: uyarilan.append(w)))
    main_window._bildir("İndirmeler bitti", "1 bölüm indirildi")
    assert uyarilan == [main_window]
