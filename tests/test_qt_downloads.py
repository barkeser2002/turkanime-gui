"""İndirme kuyruğu: ayarların gerçekten okunması, iptal, yeniden deneme, geçmiş.

Buradaki testlerin çoğu bir hatayı kilitliyor: Qt tarafı `ayarlar.json`'daki
"aria2c kullan", "max resolution", "paralel indirme sayisi" gibi ayarları hiç
okumuyordu — kullanıcı ayarı değiştiriyor, hiçbir şey olmuyordu.

Ağa çıkılmaz, gerçek yt-dlp/aria2c çalıştırılmaz: `Bolum`/`Video` sahteleri
`best_video`/`indir` çağrılarını kaydeder.
"""
from __future__ import annotations

import threading
import time

import pytest

from turkanime_api.gui.qt.pages.downloads import (
    BITMIS_DURUMLAR, DURUM_BEKLIYOR, DURUM_HATA, DURUM_INDIRILIYOR, DURUM_IPTAL,
    DURUM_TAMAMLANDI, MAX_DENEME, DownloadManager, DownloadRow, DownloadsPage,
)


# ── Sahteler ─────────────────────────────────────────────────────────────────
class SahteAnime:
    def __init__(self, slug="naruto-test", title="Naruto Test"):
        self.slug = slug
        self.title = title


class SahteVideo:
    """`objects.Video` yerine: indirme çağrılarını kaydeder, ağa çıkmaz."""

    def __init__(self, bolum, player="GDRIVE", hata: Exception | None = None):
        self.bolum = bolum
        self.player = player
        self.ydl_opts = {}
        self._hata = hata
        self.deneme = 0
        self.cikti = []

    def indir(self, callback=None, output=""):
        self.deneme += 1
        self.cikti.append(output)
        if self._hata is not None:
            raise self._hata
        if callback:
            callback({"status": "downloading", "downloaded_bytes": 512,
                      "total_bytes": 1024, "speed": 256})
            callback({"status": "finished"})

    def duzelt(self):
        """Yeniden deneme testinde işi başarıya çevir."""
        self._hata = None


class BlokeVideo:
    """İptal gelene kadar ilerleme yayan sahte indirme."""

    def __init__(self, bolum):
        self.bolum = bolum
        self.player = "GDRIVE"
        self.ydl_opts = {}
        self.basladi = threading.Event()
        self.hook_sayisi = 0

    def indir(self, callback=None, output=""):
        self.basladi.set()
        for i in range(20000):
            callback({"status": "downloading", "downloaded_bytes": i,
                      "total_bytes": 20000})
            # Hook istisna fırlatırsa sayaç ARTMAZ: "hook durdu mu?" ölçümü.
            self.hook_sayisi += 1
            time.sleep(0.005)
        callback({"status": "finished"})


class SahteBolum:
    def __init__(self, video=None, slug="naruto-test-1-bolum", anime=None):
        self.slug = slug
        self.anime = anime if anime is not None else SahteAnime()
        self.video = video if video is not None else SahteVideo(self)
        if getattr(self.video, "bolum", None) is None:
            self.video.bolum = self
        self.best_kwargs = None

    def best_video(self, **kwargs):
        self.best_kwargs = kwargs
        return self.video


def _entry(bolum, title="Naruto Test 1. Bölüm"):
    return {"title": title, "obj": bolum}


@pytest.fixture
def manager(qtbot, preserved_gecmis):
    """Her test kendi yöneticisiyle çalışsın; işler testler arasında sızmasın.

    `preserved_gecmis` şart: başarılı indirme gerçek `gecmis.json`'a yazıyor,
    izole edilmezse testler kullanıcının geçmişini kirletir.
    """
    mgr = DownloadManager()
    yield mgr
    mgr.cancel_all()


def _bekle(qtbot, manager, task_id, timeout=10000):
    qtbot.waitUntil(lambda: manager.durum(task_id) in BITMIS_DURUMLAR,
                    timeout=timeout)


# ── Ayarlar gerçekten okunuyor mu? ───────────────────────────────────────────
def test_max_resolution_ve_aday_sayisi_best_video_a_gidiyor(qtbot, manager, ayarla,
                                                            tmp_path):
    """"max resolution" ve "1080p aday sayısı" `best_video`'ya paslanmalı."""
    ayarla(**{"max resolution": False, "1080p aday sayısı": 3})
    bolum = SahteBolum()

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert bolum.best_kwargs == {"by_res": False, "early_subset": 3}


def test_max_resolution_acikken_by_res_true(qtbot, manager, ayarla, tmp_path):
    ayarla(**{"max resolution": True, "1080p aday sayısı": 12})
    bolum = SahteBolum()

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert bolum.best_kwargs == {"by_res": True, "early_subset": 12}


def test_aria2c_ayari_aria2c_yolunu_seciyor(qtbot, manager, ayarla, monkeypatch,
                                            tmp_path):
    """"aria2c kullan" açıkken `cli_tools.indir_aria2c` kullanılmalı."""
    import turkanime_api.cli.cli_tools as cli_tools

    cagrilar = []
    monkeypatch.setattr(cli_tools, "indir_aria2c",
                        lambda video, callback, output: cagrilar.append(output) or True)

    ayarla(**{"aria2c kullan": True})
    bolum = SahteBolum()

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert manager.durum(task_id) == DURUM_TAMAMLANDI
    assert cagrilar == [str(tmp_path)]
    assert bolum.video.deneme == 0, "aria2c seçilince yt-dlp yolu çalışmamalı"


def test_aria2c_kapaliyken_yt_dlp_kullaniliyor(qtbot, manager, ayarla, monkeypatch,
                                               tmp_path):
    import turkanime_api.cli.cli_tools as cli_tools

    cagrilar = []
    monkeypatch.setattr(cli_tools, "indir_aria2c",
                        lambda *a, **k: cagrilar.append(k) or True)

    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum()

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert cagrilar == []
    assert bolum.video.deneme == 1


def test_aria2c_alucard_player_icin_atlaniyor(qtbot, manager, ayarla, monkeypatch,
                                              tmp_path):
    """ALUCARD linkleri yt-dlp impersonate oturumuna bağlı; aria2c taşıyamıyor."""
    import turkanime_api.cli.cli_tools as cli_tools

    cagrilar = []
    monkeypatch.setattr(cli_tools, "indir_aria2c",
                        lambda *a, **k: cagrilar.append(k) or True)

    ayarla(**{"aria2c kullan": True})
    bolum = SahteBolum()
    bolum.video.player = "ALUCARD(BETA)"

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert cagrilar == []
    assert bolum.video.deneme == 1


def test_aria2c_basarisiz_donerse_hata(qtbot, manager, ayarla, monkeypatch, tmp_path):
    """`indir_aria2c` False dönerse başarı sayılmamalı."""
    import turkanime_api.cli.cli_tools as cli_tools

    monkeypatch.setattr(cli_tools, "indir_aria2c", lambda *a, **k: False)
    ayarla(**{"aria2c kullan": True})

    task_id = manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert manager.durum(task_id) == DURUM_HATA


def test_paralel_indirme_sayisi_havuz_boyutunu_belirliyor(manager, ayarla,
                                                          monkeypatch, tmp_path):
    """"paralel indirme sayisi" sabit 3 yerine havuz boyutunu belirlemeli."""
    import turkanime_api.gui.qt.pages.downloads as dl_mod
    from turkanime_api.gui.qt.workers import long_task_pool

    monkeypatch.setattr(dl_mod, "run_bg", lambda *a, **k: None)  # iş başlamasın

    ayarla(**{"paralel indirme sayisi": 6})
    manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
    assert long_task_pool().maxThreadCount() == 6

    ayarla(**{"paralel indirme sayisi": 2})
    manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
    assert long_task_pool().maxThreadCount() == 2


def test_bozuk_paralel_ayari_varsayilana_dusuyor(ayarla):
    from turkanime_api.gui.qt import prefs

    ayarla(**{"paralel indirme sayisi": 0})
    assert prefs.oku().paralel == prefs.VARSAYILAN_PARALEL
    ayarla(**{"paralel indirme sayisi": "abc"})
    assert prefs.oku().paralel == prefs.VARSAYILAN_PARALEL


# ── İptal ────────────────────────────────────────────────────────────────────
def test_calisan_indirme_iptal_edilebiliyor(qtbot, manager, ayarla, tmp_path):
    """İptal gerçekten durdurmalı: hook `return` etseydi indirme sürerdi."""
    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum()
    video = BlokeVideo(bolum)
    bolum.video = video

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    assert video.basladi.wait(10), "indirme hiç başlamadı"
    manager.cancel(task_id)
    _bekle(qtbot, manager, task_id)

    assert manager.durum(task_id) == DURUM_IPTAL
    sayac = video.hook_sayisi
    time.sleep(0.15)
    assert video.hook_sayisi == sayac, "hook iptalden sonra da çalışıyor"


def test_bekleyen_is_aninda_iptal_ediliyor(manager, ayarla, monkeypatch, tmp_path):
    """Havuz doluysa iş dakikalarca başlamaz; iptal geri bildirimi beklememeli."""
    import turkanime_api.gui.qt.pages.downloads as dl_mod

    monkeypatch.setattr(dl_mod, "run_bg", lambda *a, **k: None)
    ayarla(**{"aria2c kullan": False})

    task_id = manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
    assert manager.durum(task_id) == DURUM_BEKLIYOR
    assert manager.cancel(task_id) is True
    assert manager.durum(task_id) == DURUM_IPTAL
    # İkinci iptal yok sayılmalı (çift "bitti" raporu üretmemeli).
    assert manager.cancel(task_id) is False


def test_cancel_all_bekleyen_isleri_kapatiyor(manager, ayarla, monkeypatch, tmp_path):
    import turkanime_api.gui.qt.pages.downloads as dl_mod

    monkeypatch.setattr(dl_mod, "run_bg", lambda *a, **k: None)
    ayarla(**{"aria2c kullan": False})

    ids = [manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
           for _ in range(3)]
    assert manager.cancel_all() == 3
    assert all(manager.durum(i) == DURUM_IPTAL for i in ids)
    assert manager.active_ids() == []


# ── Yeniden deneme ───────────────────────────────────────────────────────────
def test_basarisiz_is_otomatik_bir_kez_tekrarlaniyor(qtbot, manager, ayarla, tmp_path):
    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum(video=None)
    bolum.video = SahteVideo(bolum, hata=RuntimeError("bağlantı koptu"))

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert manager.durum(task_id) == DURUM_HATA
    assert bolum.video.deneme == MAX_DENEME


def test_yeniden_dene_basarisiz_isi_kuyruga_aliyor(qtbot, manager, ayarla, tmp_path):
    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum(video=None)
    bolum.video = SahteVideo(bolum, hata=RuntimeError("bağlantı koptu"))

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)
    assert manager.durum(task_id) == DURUM_HATA

    bolum.video.duzelt()
    assert manager.retry(task_id) == task_id
    _bekle(qtbot, manager, task_id)

    assert manager.durum(task_id) == DURUM_TAMAMLANDI
    assert bolum.video.deneme == MAX_DENEME + 1


def test_tamamlanmis_is_yeniden_denenmiyor(qtbot, manager, ayarla, tmp_path):
    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum()

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert manager.retry(task_id) is None
    assert bolum.video.deneme == 1


def test_video_bulunamazsa_hata(qtbot, manager, ayarla, tmp_path):
    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum()
    bolum.best_video = lambda **k: None

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    assert manager.durum(task_id) == DURUM_HATA


# ── Durum akışı ve geçmiş ────────────────────────────────────────────────────
def test_durum_akisi_bekliyor_indiriliyor_tamamlandi(qtbot, manager, ayarla, tmp_path):
    ayarla(**{"aria2c kullan": False})
    goruldu = []
    manager.state.connect(lambda _id, durum: goruldu.append(durum))

    task_id = manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)
    qtbot.waitUntil(lambda: DURUM_TAMAMLANDI in goruldu, timeout=5000)

    assert goruldu[:3] == [DURUM_BEKLIYOR, DURUM_INDIRILIYOR, DURUM_TAMAMLANDI]


def test_indirme_bitince_gecmise_yaziliyor(qtbot, manager, ayarla, tmp_path):
    """Qt tarafı `set_gecmis`'i hiç çağırmıyordu; rozetler bu kayda bakıyor."""
    from turkanime_api.cli.dosyalar import Dosyalar

    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum(slug="naruto-test-7-bolum")

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    indirildi = Dosyalar().gecmis["indirildi"]
    assert "naruto-test-7-bolum" in indirildi.get("naruto-test", [])


def test_basarisiz_indirme_gecmise_yazilmiyor(qtbot, manager, ayarla, tmp_path):
    from turkanime_api.cli.dosyalar import Dosyalar

    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum(slug="naruto-test-9-bolum", video=None)
    bolum.video = SahteVideo(bolum, hata=RuntimeError("olmadı"))

    task_id = manager.enqueue(_entry(bolum), output=str(tmp_path))
    _bekle(qtbot, manager, task_id)

    indirildi = Dosyalar().gecmis["indirildi"]
    assert "naruto-test-9-bolum" not in indirildi.get("naruto-test", [])


# ── Satır / sayfa ────────────────────────────────────────────────────────────
def test_satir_butonlari_duruma_gore(qtbot):
    row = DownloadRow("dl1", "Naruto 1")
    qtbot.addWidget(row)

    row.set_state(DURUM_INDIRILIYOR)
    assert row.btnCancel.isVisibleTo(row) and not row.btnRetry.isVisibleTo(row)

    row.set_state(DURUM_HATA)
    assert row.is_finished and not row.is_ok
    assert row.btnRetry.isVisibleTo(row) and not row.btnCancel.isVisibleTo(row)

    row.set_state(DURUM_TAMAMLANDI)
    assert row.is_ok
    assert not row.btnRetry.isVisibleTo(row)


def test_satir_yeniden_denemede_temizleniyor(qtbot):
    """Yeniden denenen satırda eski hata metni kalmamalı."""
    row = DownloadRow("dl1", "Naruto 1")
    qtbot.addWidget(row)
    row.set_state(DURUM_HATA)
    row.set_done(False, "hata: bağlantı koptu")

    row.set_state(DURUM_BEKLIYOR)
    assert row.lblDetail.text() == DURUM_BEKLIYOR
    assert row.bar.value() == 0
    assert not row.is_finished


def test_sayfa_satirlari_yonetiyor(qtbot, manager):
    page = DownloadsPage(manager)
    qtbot.addWidget(page)

    manager.added.emit("dl1", "Naruto 1")
    manager.state.emit("dl1", DURUM_INDIRILIYOR)
    assert "1 aktif" in page.lblStatus.text()

    manager.progress.emit("dl1", 40, "yarısı")
    assert page._rows["dl1"].bar.value() == 40

    manager.state.emit("dl1", DURUM_TAMAMLANDI)
    manager.finished.emit("dl1", True, DURUM_TAMAMLANDI)
    assert "tamamlandı" in page.lblStatus.text()

    page._clear_finished()
    assert page._rows == {}


def test_sayfa_iptal_dugmesi_yoneticiye_gidiyor(qtbot, manager, monkeypatch):
    page = DownloadsPage(manager)
    qtbot.addWidget(page)
    cagrildi = []
    monkeypatch.setattr(manager, "cancel_all", lambda: cagrildi.append(True) or 2)

    page.btnCancelAll.click()
    assert cagrildi == [True]


def test_satir_dugmeleri_yoneticiye_bagli(qtbot, manager, monkeypatch, ayarla,
                                          tmp_path):
    import turkanime_api.gui.qt.pages.downloads as dl_mod

    monkeypatch.setattr(dl_mod, "run_bg", lambda *a, **k: None)
    page = DownloadsPage(manager)
    qtbot.addWidget(page)

    task_id = manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
    page._rows[task_id].btnCancel.click()
    assert manager.durum(task_id) == DURUM_IPTAL
    assert page._rows[task_id].btnRetry.isVisibleTo(page._rows[task_id])
