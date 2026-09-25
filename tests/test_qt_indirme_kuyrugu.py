"""Kalıcı indirme kuyruğu, duraklat/sürdür ve kapanış sorusu.

ESKİ HATA: işler yalnızca `DownloadManager._jobs`'taydı; pencereyi kapatmak
sormadan hepsini iptal ediyor, bir sonraki açılışta hiçbiri geri gelmiyordu
(40 bölümlük toplu indirme yanlış bir tıkla ya da çökmede kayboluyordu).
Duraklatma yoktu. Sürdürmede aday değişirse yt-dlp eski `.part`'a yeni akışı
ekliyordu (ölçüldü: A'nın başı + B'nin sonu).

Ağ yok, gerçek yt-dlp yok: sahte kaynak (kayda geçici olarak eklenir) ve
sahte video nesneleri.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

import turkanime_api.gui.qt.pages.downloads as dl_mod
from turkanime_api.common.dosya_adi import bolum_hedefi
from turkanime_api.gui.qt.pages.downloads import (
    BITMIS_DURUMLAR, DURUM_BEKLIYOR, DURUM_DURAKLATILDI, DURUM_INDIRILIYOR,
    DURUM_TAMAMLANDI, DownloadManager, DownloadsPage,
)
from turkanime_api.sources import kayit

from test_qt_downloads import AdresliVideo, BlokeVideo, SahteBolum, _entry


# ── Sahte kaynak ─────────────────────────────────────────────────────────────
@pytest.fixture
def sahte_kaynak(monkeypatch):
    """Kayda eklenen sahte kaynak; uçlarının yüklenmesini sayar."""
    yukleme: list = []

    def akislar(_bolum_id):
        pytest.fail("geri yükleme akış istememeli")

    def yukleyici():
        yukleme.append(1)
        return kayit.KaynakUclari(
            lambda q, limit=10: [],
            lambda _s: [("s1", "1. Bölüm"), ("s2", "2. Bölüm")], akislar)

    kaynak = kayit.Kaynak("SahteKuyruk", "Sahte Kuyruk", "SK", "#000000", "SAHTEK",
                          yukleyici, bolum_adresi=lambda b: f"https://sahte.test/izle/{b}")
    monkeypatch.setattr(kayit, "KAYNAKLAR", kayit.KAYNAKLAR + (kaynak,))
    return kaynak, yukleme


def _kaynak_girisleri(kaynak):
    from turkanime_api.sources.adapter import kayittan_bolumler
    return [{"title": f"Sahte Seri {b.title}", "obj": b, "kaynak": kaynak.ad,
             "kimlik": "seri-42", "seri_adi": "Sahte Seri"}
            for b in kayittan_bolumler(kaynak, "seri-42", "Sahte Seri")]


def _kuyruk():
    with open(dl_mod.kuyruk_yolu(), encoding="utf-8") as fp:
        return json.load(fp)["isler"]


@pytest.fixture
def baslatma(monkeypatch):
    """`run_bg` sahte: işler başlamasın, çağrılar sayılsın. `izin` doluysa gerçek."""
    asil = dl_mod.run_bg
    cagrilar: list = []
    izin: list = []

    def sahte(*a, **k):
        cagrilar.append(a)
        if izin:
            return asil(*a, **k)
        return None
    monkeypatch.setattr(dl_mod, "run_bg", sahte)
    return cagrilar, izin


@pytest.fixture
def manager(qtbot, izole_ev):
    mgr = DownloadManager()
    yield mgr
    mgr.cancel_all()


# ── Kalıcılık ────────────────────────────────────────────────────────────────
def test_kuyruk_diske_yaziliyor_biten_dusuyor(qtbot, manager, sahte_kaynak,
                                              baslatma, izole_ev):
    kaynak, yukleme = sahte_kaynak
    cikti = str(izole_ev / "indir")
    a, b = _kaynak_girisleri(kaynak)
    yukleme.clear()                           # bölüm listesi kurulurken yüklendi
    t1 = manager.enqueue(a, output=cikti)
    t2 = manager.enqueue(b, output=cikti)

    qtbot.waitUntil(lambda: os.path.exists(dl_mod.kuyruk_yolu())
                    and len(_kuyruk()) == 2, timeout=3000)
    isler = _kuyruk()
    assert {i["bolum_id"] for i in isler} == {"s1", "s2"}
    assert all(i["kaynak"] == "SahteKuyruk" and i["anime_kimlik"] == "seri-42"
               and i["output"] == cikti for i in isler)
    assert os.path.samefile(os.path.dirname(dl_mod.kuyruk_yolu()), izole_ev), \
        "kuyruk ayarlar.json'un yanında"

    manager.cancel(t1)
    assert [i["bolum_id"] for i in _kuyruk()] == ["s2"]
    manager._bitir(manager._jobs[t2], True, DURUM_TAMAMLANDI)
    assert _kuyruk() == []


def test_geri_yukleme_agsiz_duraklatilmis_devam_baslatir(qtbot, manager, sahte_kaynak,
                                                          baslatma, izole_ev):
    kaynak, yukleme = sahte_kaynak
    cagrilar, _izin = baslatma
    cikti = str(izole_ev / "indir")
    girisler = _kaynak_girisleri(kaynak)
    for g in girisler:
        manager.enqueue(g, output=cikti, fansub="B")
    manager.kapanista_kaydet()
    assert {i["durum"] for i in _kuyruk()} == {DURUM_DURAKLATILDI}
    hedefler = {j.hedef for j in manager._jobs.values()}

    yukleme.clear()
    cagrilar.clear()
    yeni = DownloadManager()
    assert yeni.geri_yukle() == 2
    assert [yeni.durum(t) for t in yeni._jobs] == [DURUM_DURAKLATILDI] * 2
    assert cagrilar == [], "geri yükleme iş başlatmamalı"
    assert yukleme == [], "geri yükleme kaynağa dokunmamalı (ağ yok)"
    assert {j.hedef for j in yeni._jobs.values()} == hedefler, "aynı dosya hedefi"
    assert {j.fansub for j in yeni._jobs.values()} == {"B"}
    assert yeni.active_ids() == [] and len(yeni.duraklatilan_ids()) == 2

    # Aynı bölüm yeniden kuyruğa girmez; "Devam et" işi başlatır.
    ilk = next(iter(yeni._jobs))
    assert yeni.enqueue(yeni.kayit(ilk), output=cikti) == ilk
    assert yeni.resume(ilk)
    assert yeni.durum(ilk) == DURUM_BEKLIYOR
    assert cagrilar and cagrilar[-1][0] == yeni._run
    yeni.cancel_all()


def test_bozuk_kuyruk_kenara_ayriliyor(manager):
    yol = dl_mod.kuyruk_yolu()
    with open(yol, "w", encoding="utf-8") as fp:
        fp.write("{bozuk")
    assert DownloadManager().geri_yukle() == 0
    klasor = os.path.dirname(yol)
    assert any(ad.startswith(dl_mod.KUYRUK_DOSYASI + ".bozuk-")
               for ad in os.listdir(klasor))
    assert not os.path.exists(yol)


def test_acilista_geri_yuklenen_isler_sayfada(qtbot, sahte_kaynak, monkeypatch,
                                             tmp_path):
    """Ana pencere açılışta kuyruğu okur; satırlar "Devam et" ile gelir."""
    from turkanime_api.gui.qt.app import MainWindow
    kaynak, _yukleme = sahte_kaynak
    kayitlar = [{"kaynak": kaynak.ad, "anime_kimlik": "seri-42", "anime_slug": "seri-42",
                 "anime_baslik": "Sahte Seri", "bolum_id": "s1",
                 "bolum_baslik": "1. Bölüm", "bolum_slug": "sahte-seri-1-bolum",
                 "baslik": "Sahte Seri 1. Bölüm", "output": str(tmp_path),
                 "durum": "indiriliyor"}]
    with open(dl_mod.kuyruk_yolu(), "w", encoding="utf-8") as fp:
        json.dump({"surum": 1, "isler": kayitlar}, fp)
    monkeypatch.setattr(dl_mod, "run_bg", lambda *a, **k: pytest.fail("başlamamalı"))

    win = MainWindow()
    qtbot.addWidget(win)
    try:
        sayfa = win.pages["downloads"]
        assert isinstance(sayfa, DownloadsPage)
        (satir,) = sayfa._rows.values()
        assert satir.durum == DURUM_DURAKLATILDI
        assert satir.btnResume.isVisibleTo(satir) and not satir.btnPause.isVisibleTo(satir)
        assert sayfa.btnResumeAll.isVisibleTo(sayfa)
        assert "geri yüklendi" in win.statusBar().currentMessage()
    finally:
        win._kapanis_onayi = lambda _adet: True
        win.close()


# ── Duraklat / sürdür ────────────────────────────────────────────────────────
def test_inen_is_duraklatiliyor_part_kaliyor_devam_yeniden_calisiyor(
        qtbot, manager, ayarla, tmp_path):
    ayarla(**{"aria2c kullan": False})
    bolum = SahteBolum(video=None)
    bolum.video = BlokeVideo(bolum)
    part = bolum_hedefi(str(tmp_path), bolum) + ".mp4.part"
    os.makedirs(os.path.dirname(part), exist_ok=True)
    with open(part, "wb") as fp:
        fp.write(b"yarim")

    tid = manager.enqueue(_entry(bolum), output=str(tmp_path))
    assert bolum.video.basladi.wait(5)
    assert manager.durum(tid) == DURUM_INDIRILIYOR
    assert manager.pause(tid)
    qtbot.waitUntil(lambda: manager.durum(tid) == DURUM_DURAKLATILDI, timeout=5000)
    assert os.path.exists(part), "duraklatma yarım dosyayı silmemeli"
    assert manager.active_ids() == []

    bolum.video.basladi.clear()
    assert manager.resume(tid)
    assert manager.durum(tid) in (DURUM_BEKLIYOR, DURUM_INDIRILIYOR)
    assert bolum.video.basladi.wait(5), "Devam et işi yeniden çalıştırmalı"


def test_bekleyen_is_duraklatilip_surdurulunce_tek_kez_calisiyor(
        qtbot, manager, ayarla, tmp_path, baslatma):
    """Havuzda sırada kalan ESKİ `_run` çağrısı, sürdürülen işi ikinci kez
    başlatmamalı (iki thread aynı dosyaya yazardı)."""
    ayarla(**{"aria2c kullan": False})
    cagrilar, _izin = baslatma
    bolum = SahteBolum()
    tid = manager.enqueue(_entry(bolum), output=str(tmp_path))
    assert manager.pause(tid) and manager.durum(tid) == DURUM_DURAKLATILDI
    assert manager.resume(tid)
    eski, yeni = cagrilar[0], cagrilar[-1]
    eski[0](*eski[1:])                           # eski nesil: hiçbir şey yapmaz
    assert bolum.video.deneme == 0 and manager.durum(tid) == DURUM_BEKLIYOR
    yeni[0](*yeni[1:])
    assert bolum.video.deneme == 1 and manager.durum(tid) == DURUM_TAMAMLANDI


@pytest.mark.parametrize("ayni_adres", [False, True])
def test_surdurmede_aday_degisirse_yarim_dosya_siliniyor(
        qtbot, manager, ayarla, tmp_path, baslatma, ayni_adres):
    ayarla(**{"aria2c kullan": False})
    _cagrilar, izin = baslatma
    bolum = SahteBolum()
    part = bolum_hedefi(str(tmp_path), bolum) + ".mp4.part"
    os.makedirs(os.path.dirname(part), exist_ok=True)
    with open(part, "wb") as fp:
        fp.write(b"A akisinin basi")
    eski = "https://a.test/v.mp4"
    video = AdresliVideo(bolum, eski if ayni_adres else "https://b.test/v.mp4",
                         part_yolu=part)
    bolum.best_video = lambda **_k: video

    tid = manager.enqueue(_entry(bolum), output=str(tmp_path))
    manager.pause(tid)
    manager._jobs[tid].secilen_url = eski      # önceki çalıştırmanın adayı
    izin.append(True)
    manager.resume(tid)
    qtbot.waitUntil(lambda: manager.durum(tid) in BITMIS_DURUMLAR, timeout=5000)
    assert video.part_vardi is ayni_adres
    assert manager._jobs[tid].secilen_url == video.url


def test_satir_duraklat_ve_devam_dugmeleri(qtbot, manager, baslatma, tmp_path):
    page = DownloadsPage(manager)
    qtbot.addWidget(page)
    tid = manager.enqueue(_entry(SahteBolum()), output=str(tmp_path))
    satir = page._rows[tid]
    assert satir.btnPause.isVisibleTo(satir) and not satir.btnResume.isVisibleTo(satir)
    satir.btnPause.click()
    assert manager.durum(tid) == DURUM_DURAKLATILDI
    assert satir.btnResume.isVisibleTo(satir) and satir.btnCancel.isVisibleTo(satir)
    assert "duraklatıldı" in page.lblStatus.text()
    satir.btnResume.click()
    assert manager.durum(tid) == DURUM_BEKLIYOR
    satir.btnPause.click()
    satir.btnCancel.click()
    assert manager.durum(tid) == "iptal edildi"


# ── Kapanış ──────────────────────────────────────────────────────────────────
def test_kapanista_hayir_pencereyi_ve_isi_birakiyor(main_window, baslatma, tmp_path,
                                                    monkeypatch):
    win = main_window
    sorular: list = []
    monkeypatch.setattr(win, "_kapanis_onayi", lambda adet: sorular.append(adet) or False)
    durduruldu: list = []
    monkeypatch.setattr(win.downloads, "pause_all", lambda: durduruldu.append(1))
    monkeypatch.setattr(win.downloads, "cancel_all", lambda: durduruldu.append(1))
    tid = win.downloads.enqueue(_entry(SahteBolum()), output=str(tmp_path))

    assert win.close() is False
    assert sorular == [1]
    assert win.isVisible()
    assert durduruldu == [] and win.downloads.durum(tid) == DURUM_BEKLIYOR


def test_kapanista_evet_duraklatip_kaydediyor(main_window, baslatma, tmp_path,
                                              monkeypatch):
    win = main_window
    monkeypatch.setattr(win, "_kapanis_onayi", lambda adet: True)
    kaydedildi: list = []
    asil = win.downloads.kapanista_kaydet
    monkeypatch.setattr(win.downloads, "kapanista_kaydet",
                        lambda: kaydedildi.append(1) or asil())
    tid = win.downloads.enqueue(_entry(SahteBolum()), output=str(tmp_path))

    assert win.close() is True
    assert win.downloads.durum(tid) == DURUM_DURAKLATILDI, "iptal değil duraklatma"
    assert kaydedildi == [1]


def test_is_yokken_kapanis_sormuyor(main_window, monkeypatch):
    monkeypatch.setattr(main_window, "_kapanis_onayi",
                        lambda adet: pytest.fail("iş yokken sorulmamalı"))
    assert main_window.close() is True
