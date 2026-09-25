"""Ayarlar → "Çevrimdışı arşiv (TürkAnime)" bölümü.

turkanime.tv kapandı; "TürkAnime" kaynağı sitenin statik arşivinden okunuyor.
Bu bölüm kullanıcıya arşivin NEREDEN okunduğunu gösteriyor ve tam arşivi
indirme / güncelleme / silme ile elle klasör gösterme işlerini yapıyor.

Testlerin derdi:

* Sayfa kurulurken ne ağa ne diske gidilmeli: ana pencere bütün sayfaları
  açılışta kuruyor, megabaytlık `dizin.json` her açılışta ayrıştırılırdı.
* Uzun işler (indirme, klasör doğrulama, silme) GUI thread'inde koşmamalı ve
  her sonuç — ilerleme, iptal, hata — kullanıcıya Türkçe ve sebebiyle ulaşmalı.
* Silme YALNIZCA indirilen kopyayı silmeli; depoda veri kökü depo kökü olduğu
  için commit'lenmiş `arsiv/` hemen yanında duruyor.

Hiçbir test ağa çıkmaz ve gerçek 500 MB'lık `arsiv/`'i okumaz: arşivler
`tmp_path` altında birkaç dosyalık sahteler, `tam_arsiv_indir` sahteleniyor
(gerçek indirme yolu `tests/test_cevrimdisi_arsiv.py`'de, sahte HTTP ile).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pytest
from PySide6.QtCore import QThreadPool

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.common import arsiv_paketi as paket
from turkanime_api.gui.qt.pages import settings as settings_mod
from turkanime_api.sources import animedepo

BEKLE = 5000
SON_GUNCELLEME = 1700000000


# ── Yardımcılar ──────────────────────────────────────────────────────────────
def arsiv_yaz(kok: Path, adet: int = 3, son: int = SON_GUNCELLEME) -> Path:
    """Konum çözümünün "geçerli arşiv" saydığı en küçük ağaç: bir dizin.json."""
    kok.mkdir(parents=True, exist_ok=True)
    index = {"N": {f"anime-{i}": {"title": f"Anime {i}"} for i in range(adet)}}
    (kok / "dizin.json").write_text(
        json.dumps({"last_update": son, "index": index}), encoding="utf-8")
    return kok


def tarih(zaman: int = SON_GUNCELLEME) -> str:
    # Sayfa yerel saat dilimiyle biçimliyor; beklenen değer de öyle hesaplanmalı.
    return datetime.fromtimestamp(zaman).strftime("%d.%m.%Y")


class Konumlar:
    """Testin kontrol ettiği arşiv yolları (conftest'in yalıtımının üstüne)."""

    def __init__(self, kok: Path):
        self.indirilen = kok / "veri" / animedepo.CEVRIMDISI_KLASOR
        self.depo = kok / "veri" / "arsiv"
        self.secilen = kok / "baska-disk" / "arsivim"


@pytest.fixture
def konumlar(tmp_path, monkeypatch) -> Konumlar:
    k = Konumlar(tmp_path)
    monkeypatch.setattr(animedepo, "indirilen_arsiv_dizini", lambda: k.indirilen)
    monkeypatch.setattr(animedepo, "DEPO_ARSIVI", k.depo)
    animedepo.sifirla()
    return k


@pytest.fixture
def sayfa(qtbot, izole_ev, konumlar):
    """Tek başına kurulmuş, GÖSTERİLMEMİŞ `SettingsPage`.

    `izole_ev` şart: sayfa `ayarlar.json`'a yazıyor ve animedepo ayar klasörünü
    aynı veri kökünden okuyor.
    """
    sf = settings_mod.SettingsPage()
    qtbot.addWidget(sf)
    yield sf
    # Arka plan işi sayfa yok edilirken sürmesin (sahte indirme bekliyor olabilir).
    sf.arsiv_indirmeyi_durdur()
    QThreadPool.globalInstance().waitForDone(BEKLE)


def durumu_bekle(qtbot, sf) -> None:
    """Durum okumasını başlat ve panele yazılmasını bekle."""
    with qtbot.waitSignal(sf.arsiv_durumu_yenilendi, timeout=BEKLE):
        sf.arsiv_durumunu_tazele()


def is_bitti(sf) -> bool:
    return sf._arsiv_mesgul is None


def gorunur(widget) -> bool:
    """Sayfa gösterilmediği için `isVisible()` hep False; gizlenmiş mi ona bak."""
    return not widget.isHidden()


# ── Kurulum: ağ yok, disk yok ────────────────────────────────────────────────
def test_kurulumda_arsive_dokunulmuyor(qtbot, izole_ev, konumlar, monkeypatch):
    """ESKİ RİSK: durum kurulumda okunsaydı her açılışta (kullanıcı Ayarlar'a
    hiç girmese bile) dizin.json ayrıştırılır, uzak konumda ağa çıkılırdı."""
    cagrilar: List[str] = []
    for ad in ("arsiv_durumu", "arsiv_konumu", "dizin", "fetch_json",
               "tam_arsiv_indir", "indirilen_arsivi_sil"):
        monkeypatch.setattr(animedepo, ad,
                            lambda *a, _ad=ad, **k: cagrilar.append(_ad))
    monkeypatch.setattr(paket, "arsivi_dogrula",
                        lambda *a, **k: cagrilar.append("arsivi_dogrula"))

    sf = settings_mod.SettingsPage()
    qtbot.addWidget(sf)
    qtbot.wait(50)
    QThreadPool.globalInstance().waitForDone(BEKLE)

    assert cagrilar == []
    assert sf.btnArsivIndir.text() == settings_mod.TAM_ARSIV_DUGMESI
    assert not sf.btnArsivSil.isEnabled(), "durum bilinmeden silme açılmamalı"
    assert not gorunur(sf.prgArsiv) and not gorunur(sf.btnArsivIptal)


def test_sayfa_gorununce_durum_arka_planda_okunuyor(qtbot, sayfa, konumlar, monkeypatch):
    arsiv_yaz(konumlar.depo, adet=4)
    asil = animedepo.arsiv_durumu
    threadler: List[threading.Thread] = []

    def izle():
        threadler.append(threading.current_thread())
        return asil()

    monkeypatch.setattr(animedepo, "arsiv_durumu", izle)

    sayfa.show()

    qtbot.waitUntil(lambda: "4 anime" in sayfa.lblArsivIcerik.text(), timeout=BEKLE)
    assert threadler and threadler[0] is not threading.main_thread(), \
        "durum GUI thread'inde okunmamalı"
    assert sayfa.lblArsivKonum.text() == settings_mod.ARSIV_KONUM_ADLARI["depo"]


# ── Konum gösterimi ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("kaynak", ["ortam", "ayar", "indirilen", "depo"])
def test_etkin_yerel_konum_yolu_sayisi_ve_tarihi(qtbot, sayfa, konumlar, monkeypatch,
                                                 kaynak):
    yollar = {"ortam": konumlar.secilen, "ayar": konumlar.secilen,
              "indirilen": konumlar.indirilen, "depo": konumlar.depo}
    arsiv_yaz(yollar[kaynak], adet=1234)
    if kaynak == "ortam":
        monkeypatch.setenv(animedepo.DIZIN_ORTAM_ANAHTARI, str(konumlar.secilen))
    elif kaynak == "ayar":
        Dosyalar().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, str(konumlar.secilen))

    durumu_bekle(qtbot, sayfa)

    assert sayfa.lblArsivKonum.text() == settings_mod.ARSIV_KONUM_ADLARI[kaynak]
    assert sayfa.lblArsivYer.text() == str(yollar[kaynak])
    assert sayfa.lblArsivIcerik.text() == f"1.234 anime · son güncelleme {tarih()}"
    assert not gorunur(sayfa.lblArsivUyari)


def test_uzak_ayna_adresi_ve_aga_cikmadan_bilinmiyor(qtbot, sayfa):
    """Yerel arşiv yokken durum için ağa çıkılmaz (conftest `_session`'ı
    patlatıyor; çıkılsaydı durum okunamadı hatası görünürdü)."""
    durumu_bekle(qtbot, sayfa)

    assert sayfa.lblArsivKonum.text() == settings_mod.ARSIV_KONUM_ADLARI["uzak"]
    assert sayfa.lblArsivYer.text() == animedepo.uzak_aynalar()[0]
    assert "Henüz okunmadı" in sayfa.lblArsivIcerik.text()
    assert "Çevrimdışı kullanmak için tüm arşivi indirin" in sayfa.lblArsivUyari.text()
    assert "okunamadı" not in sayfa.lblArsivDurum.text()


def test_uzakta_onbellekteki_dizin_sayiliyor(qtbot, sayfa):
    kopya = animedepo.onbellek_dizini() / "dizin.json"
    arsiv_yaz(kopya.parent, adet=2)

    durumu_bekle(qtbot, sayfa)

    assert sayfa.lblArsivIcerik.text() == (
        f"2 anime · son güncelleme {tarih()} (disk önbelleğindeki kopya)")


def test_gecersiz_secilen_klasor_uyarisi(qtbot, sayfa, konumlar):
    """Geçersiz klasör konum çözümünde sessizce atlanıyor; kullanıcı
    gösterdiği klasörün KULLANILMADIĞINI buradan öğrenmeli."""
    arsiv_yaz(konumlar.depo)
    konumlar.secilen.mkdir(parents=True)          # dizin.json yok
    Dosyalar().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, str(konumlar.secilen))

    durumu_bekle(qtbot, sayfa)

    assert sayfa.lblArsivKonum.text() == settings_mod.ARSIV_KONUM_ADLARI["depo"]
    assert gorunur(sayfa.lblArsivUyari)
    uyari = sayfa.lblArsivUyari.text()
    assert str(konumlar.secilen) in uyari and "geçerli bir arşiv değil" in uyari


def test_silinemeyen_eski_kopya_uyarida_gorunuyor(qtbot, sayfa, konumlar):
    """ESKİ HATA: güncellemede eski kopya silinemezse (`rmtree(ignore_errors=
    True)`) gizli adlı ~0,5 GB klasör sessizce kalıyordu."""
    arsiv_yaz(konumlar.indirilen)
    kalinti = konumlar.indirilen.with_name(".cevrimdisi_arsiv-eski-1a2b3c4d")
    arsiv_yaz(kalinti)

    durumu_bekle(qtbot, sayfa)

    assert gorunur(sayfa.lblArsivUyari)
    uyari = sayfa.lblArsivUyari.text()
    assert "silinemeyen" in uyari and str(kalinti) in uyari


def test_varsayilana_don_suren_arsiv_okumasini_beklemiyor(qtbot, sayfa, konumlar, monkeypatch):
    """ESKİ HATA: "Varsayılana dön" GUI thread'inde `animedepo.sifirla()`
    çağırıyor ve `sifirla`, arka planda aynalardan dizin okuyan aramanın
    tuttuğu kilidi bekliyordu: yavaş aynada pencere ~30 sn donuyordu."""
    girdi, birak = threading.Event(), threading.Event()

    class YavasOturum:
        def get(self, *_a, **_k):
            girdi.set()
            birak.wait(10)
            raise ConnectionError("ayna yanıt vermedi")

    monkeypatch.setattr(animedepo, "_session", YavasOturum)
    arka = threading.Thread(target=animedepo.dizin, daemon=True)
    arka.start()
    assert girdi.wait(BEKLE / 1000), "arka plan okuması aynaya ulaşmadı"
    try:
        bas = time.monotonic()
        sayfa.btnArsivVarsayilan.click()
        sure = time.monotonic() - bas
    finally:
        birak.set()
        arka.join(BEKLE / 1000)

    assert sure < 0.5, f"GUI thread'i {sure:.2f} sn dondu"
    assert "Zaten varsayılan" in sayfa.lblArsivDurum.text()


def test_indirilen_varken_dugmeler_guncelle_ve_sil(qtbot, sayfa, konumlar):
    arsiv_yaz(konumlar.indirilen)
    durumu_bekle(qtbot, sayfa)

    assert sayfa.btnArsivIndir.text() == settings_mod.ARSIV_GUNCELLE_DUGMESI
    assert sayfa.btnArsivSil.isEnabled()


def test_indirilen_yokken_dugmeler_indir_silme_kapali(qtbot, sayfa, konumlar):
    arsiv_yaz(konumlar.depo)
    durumu_bekle(qtbot, sayfa)

    assert sayfa.btnArsivIndir.text() == "Tüm arşivi indir (~230 MB)"
    assert not sayfa.btnArsivSil.isEnabled()


def test_durum_okunamazsa_sebebiyle_soyleniyor(qtbot, sayfa, monkeypatch):
    def patla():
        raise PermissionError("izin yok: /veri")

    monkeypatch.setattr(animedepo, "arsiv_durumu", patla)
    sayfa.arsiv_durumunu_tazele()

    qtbot.waitUntil(lambda: "okunamadı" in sayfa.lblArsivDurum.text(), timeout=BEKLE)
    assert "izin yok: /veri" in sayfa.lblArsivDurum.text()


# ── Tam arşivi indir ─────────────────────────────────────────────────────────
class SahteIndirme:
    """`tam_arsiv_indir` yerine: verilen adımları koşar, test izin verene kadar bekler."""

    def __init__(self, hedef: Path):
        self.hedef = hedef
        self.devam = threading.Event()
        self.basladi = threading.Event()
        self.iptal: Optional[Any] = None
        self.adimlar: List[Tuple[str, Any]] = []
        self.hata: Optional[BaseException] = None
        self.thread: Optional[threading.Thread] = None

    def __call__(self, ilerleme=None, iptal=None, hedef=None, asama=None):
        self.thread = threading.current_thread()
        self.iptal = iptal
        for tur, deger in self.adimlar:
            if tur == "asama":
                asama(*deger)
            else:
                ilerleme(*deger)
        self.basladi.set()
        # İptal ya da testin izni gelene kadar "indiriyor".
        while not self.devam.wait(0.01):
            if iptal is not None and iptal.is_set():
                raise paket.IptalEdildi("işlem iptal edildi")
        if self.hata is not None:
            raise self.hata
        arsiv_yaz(self.hedef, adet=6098)
        animedepo.sifirla()
        return self.hedef


@pytest.fixture
def sahte_indirme(monkeypatch, konumlar):
    sahte = SahteIndirme(konumlar.indirilen)
    monkeypatch.setattr(animedepo, "tam_arsiv_indir", sahte)
    # Seyreltme testin ara değerleri görmesini engellemesin.
    monkeypatch.setattr(settings_mod, "ILERLEME_ARALIGI", 0.0)
    yield sahte
    sahte.devam.set()


def test_indirme_ilerlemesi_ve_basari(qtbot, sayfa, sahte_indirme):
    sahte_indirme.adimlar = [
        ("asama", (paket.ASAMA_BAGLANMA, "GitLab")),
        ("asama", (paket.ASAMA_INDIRME, "GitLab")),
        ("ilerleme", (0, 2_000_000)),
        ("ilerleme", (1_000_000, 2_000_000)),
    ]
    durumu_bekle(qtbot, sayfa)

    sayfa.btnArsivIndir.click()

    qtbot.waitUntil(lambda: "1,0 / 2,0 MB" in sayfa.lblArsivIlerleme.text(),
                    timeout=BEKLE)
    assert sahte_indirme.thread is not threading.main_thread()
    assert "GitLab" in sayfa.lblArsivIlerleme.text()
    assert gorunur(sayfa.prgArsiv) and gorunur(sayfa.btnArsivIptal)
    assert (sayfa.prgArsiv.maximum(), sayfa.prgArsiv.value()) == (1000, 500)
    assert sayfa.btnArsivIptal.isEnabled()
    for dugme in (sayfa.btnArsivIndir, sayfa.btnArsivKlasor,
                  sayfa.btnArsivVarsayilan, sayfa.btnArsivSil):
        assert not dugme.isEnabled(), "iş sürerken ikinci bir arşiv işi başlamamalı"

    sahte_indirme.devam.set()

    qtbot.waitUntil(lambda: sayfa.lblArsivKonum.text()
                    == settings_mod.ARSIV_KONUM_ADLARI["indirilen"], timeout=BEKLE)
    assert "Tam arşiv indirildi" in sayfa.lblArsivDurum.text()
    assert sayfa.lblArsivIcerik.text().startswith("6.098 anime")
    assert sayfa.btnArsivIndir.text() == settings_mod.ARSIV_GUNCELLE_DUGMESI
    assert sayfa.btnArsivIndir.isEnabled() and sayfa.btnArsivSil.isEnabled()
    assert not gorunur(sayfa.prgArsiv) and not gorunur(sayfa.btnArsivIptal)


def test_boyut_bilinmeyen_indirme_belirsiz_cubuk(qtbot, sayfa, sahte_indirme):
    """GitLab paketi anında üretiyor, Content-Length yok: yüzde uydurulmamalı."""
    sahte_indirme.adimlar = [("ilerleme", (5_300_000, None))]

    sayfa.btnArsivIndir.click()

    qtbot.waitUntil(lambda: "5,3 MB indirildi" in sayfa.lblArsivIlerleme.text(),
                    timeout=BEKLE)
    assert "~230 MB" in sayfa.lblArsivIlerleme.text()
    assert sayfa.prgArsiv.maximum() == 0, "belirsiz kip (0..0) bekleniyordu"


def test_acma_asamasi_gosteriliyor(qtbot, sayfa, sahte_indirme):
    """Açarken bayt ilerlemesi akmıyor; aşama gösterilmezse çubuk donmuş görünür."""
    sahte_indirme.adimlar = [("ilerleme", (2_000_000, 2_000_000)),
                             ("asama", (paket.ASAMA_ACMA, "GitHub"))]

    sayfa.btnArsivIndir.click()

    qtbot.waitUntil(lambda: "açılıyor" in sayfa.lblArsivIlerleme.text(), timeout=BEKLE)
    assert sayfa.prgArsiv.maximum() == 0


def test_iptal_indirmeyi_durduruyor(qtbot, sayfa, sahte_indirme):
    sayfa.btnArsivIndir.click()
    assert sahte_indirme.basladi.wait(BEKLE / 1000)

    sayfa.btnArsivIptal.click()

    qtbot.waitUntil(lambda: is_bitti(sayfa), timeout=BEKLE)
    assert sahte_indirme.iptal is not None and sahte_indirme.iptal.is_set()
    assert "iptal edildi" in sayfa.lblArsivDurum.text()
    assert sayfa.btnArsivIndir.isEnabled()
    assert not gorunur(sayfa.prgArsiv) and not gorunur(sayfa.btnArsivIptal)
    assert not (sahte_indirme.hedef / "dizin.json").exists()


@pytest.mark.parametrize("hata, beklenen", [
    (paket.ArsivHatasi("tam arşiv indirilemedi — GitLab: HTTP 503; "
                       "GitHub: zaman aşımı"),
     "Arşiv indirilemedi. Sebep: GitLab: HTTP 503; GitHub: zaman aşımı"),
    (PermissionError("[Errno 13] izin yok: '/veri'"),
     "Arşiv indirilemedi. Sebep: [Errno 13] izin yok: '/veri'"),
])
def test_indirme_hatasi_sebebiyle_gosteriliyor(qtbot, sayfa, sahte_indirme, hata, beklenen):
    sahte_indirme.hata = hata
    sahte_indirme.devam.set()

    sayfa.btnArsivIndir.click()

    qtbot.waitUntil(lambda: is_bitti(sayfa), timeout=BEKLE)
    assert beklenen in sayfa.lblArsivDurum.text()
    assert "d63031" in sayfa.lblArsivDurum.styleSheet(), "hata rengiyle gösterilmeli"
    assert sayfa.btnArsivIndir.isEnabled()


def test_ilerleme_seyreltiliyor(qtbot, sayfa, monkeypatch, konumlar):
    """Paket 64 KB'lık parçalarla akıyor (~3600 çağrı); hepsi GUI'ye taşınırsa
    olay kuyruğu boğulur. Seyreltme GERÇEK aralıkla (0,1 sn) sınanıyor."""
    gelen: List[Tuple[int, Optional[int]]] = []

    def sahte(ilerleme=None, iptal=None, hedef=None, asama=None):
        for i in range(2000):
            ilerleme(i * 65536, None)
        raise paket.IptalEdildi("bitti")

    monkeypatch.setattr(animedepo, "tam_arsiv_indir", sahte)
    monkeypatch.setattr(sayfa, "_arsiv_ilerleme", lambda n, t: gelen.append((n, t)))

    sayfa.btnArsivIndir.click()

    qtbot.waitUntil(lambda: is_bitti(sayfa), timeout=BEKLE)
    assert gelen and gelen[0] == (0, None), "ilk değer hemen gösterilmeli"
    assert len(gelen) < 100, f"{len(gelen)} ilerleme GUI'ye taşındı"


def test_kapanista_suren_indirme_iptal_ediliyor(qtbot, main_window, monkeypatch, konumlar):
    """Pencere kapanınca indirme iptal edilmeli; yoksa süreç ~230 MB bitene
    kadar kapanmaz (bkz. `MainWindow.closeEvent`)."""
    sahte = SahteIndirme(konumlar.indirilen)
    monkeypatch.setattr(animedepo, "tam_arsiv_indir", sahte)
    # Ayarlar web'de: "Tüm arşivi indir" köprüden `arsiv_indir` ucunu çağırıyor.
    main_window.ayarlar_uclari.arsiv_indir()
    assert sahte.basladi.wait(BEKLE / 1000)

    main_window.close()

    assert sahte.iptal is not None and sahte.iptal.is_set()
    QThreadPool.globalInstance().waitForDone(BEKLE)


# ── Klasör seç / varsayılana dön ─────────────────────────────────────────────
def test_klasor_sec_gecerli_arsivi_kaydediyor_ve_onbellegi_sifirliyor(
        qtbot, sayfa, konumlar, monkeypatch):
    arsiv_yaz(konumlar.secilen, adet=7)
    assert animedepo.arsiv_konumu().kaynak == "uzak"     # önbelleğe girsin
    monkeypatch.setattr(settings_mod.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(konumlar.secilen)))

    sayfa.btnArsivKlasor.click()

    qtbot.waitUntil(lambda: sayfa.lblArsivKonum.text()
                    == settings_mod.ARSIV_KONUM_ADLARI["ayar"], timeout=BEKLE)
    assert Dosyalar().ayarlar[animedepo.DIZIN_AYAR_ANAHTARI] == str(konumlar.secilen)
    assert animedepo.arsiv_konumu() == animedepo.ArsivKonumu("ayar", konumlar.secilen), \
        "modül önbelleği sıfırlanmazsa yeni klasör yeniden başlatana kadar kullanılmazdı"
    assert "7 anime" in sayfa.lblArsivDurum.text()


def test_klasor_sec_dizin_json_yoksa_reddediyor(qtbot, sayfa, konumlar, monkeypatch):
    konumlar.secilen.mkdir(parents=True)
    monkeypatch.setattr(settings_mod.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(konumlar.secilen)))

    sayfa.btnArsivKlasor.click()

    qtbot.waitUntil(lambda: is_bitti(sayfa) and sayfa.lblArsivDurum.text()
                    != "Klasör denetleniyor…", timeout=BEKLE)
    metin = sayfa.lblArsivDurum.text()
    assert "arşiv olarak kullanılamaz" in metin and "dizin.json" in metin
    assert "d63031" in sayfa.lblArsivDurum.styleSheet()
    assert animedepo.DIZIN_AYAR_ANAHTARI not in Dosyalar().ayarlar


def test_klasor_sec_vazgecilirse_hicbir_sey_olmuyor(qtbot, sayfa, monkeypatch):
    monkeypatch.setattr(settings_mod.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: ""))
    sayfa.btnArsivKlasor.click()
    assert is_bitti(sayfa)
    assert animedepo.DIZIN_AYAR_ANAHTARI not in Dosyalar().ayarlar


def test_varsayilana_don_ayari_siliyor(qtbot, sayfa, konumlar):
    arsiv_yaz(konumlar.secilen)
    arsiv_yaz(konumlar.depo)
    Dosyalar().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, str(konumlar.secilen))
    assert animedepo.arsiv_konumu().kaynak == "ayar"

    sayfa.btnArsivVarsayilan.click()

    assert animedepo.DIZIN_AYAR_ANAHTARI not in Dosyalar().ayarlar
    assert animedepo.arsiv_konumu().kaynak == "depo"
    assert "unutuldu" in sayfa.lblArsivDurum.text()
    qtbot.waitUntil(lambda: sayfa.lblArsivKonum.text()
                    == settings_mod.ARSIV_KONUM_ADLARI["depo"], timeout=BEKLE)


def test_varsayilana_don_ayar_yoksa_bilgi(sayfa):
    sayfa.btnArsivVarsayilan.click()
    assert "Zaten varsayılan" in sayfa.lblArsivDurum.text()


# ── İndirilen arşivi sil ─────────────────────────────────────────────────────
def test_sil_onaydan_sonra_yalnizca_indirileni_siliyor(qtbot, sayfa, konumlar, monkeypatch):
    """Depoda veri kökü depo KÖKÜ: `arsiv/` hemen yanında ve silinmemeli."""
    arsiv_yaz(konumlar.indirilen)
    arsiv_yaz(konumlar.depo)
    sorular: List[Tuple[str, str]] = []
    monkeypatch.setattr(sayfa, "_onay_al", lambda b, m: sorular.append((b, m)) or True)
    durumu_bekle(qtbot, sayfa)

    sayfa.btnArsivSil.click()

    qtbot.waitUntil(lambda: sayfa.lblArsivKonum.text()
                    == settings_mod.ARSIV_KONUM_ADLARI["depo"], timeout=BEKLE)
    assert sorular and str(konumlar.indirilen) in sorular[0][1], \
        "onay metni neyin silineceğini söylemeli"
    assert not konumlar.indirilen.exists()
    assert (konumlar.depo / "dizin.json").is_file()
    assert "silindi" in sayfa.lblArsivDurum.text()
    assert not sayfa.btnArsivSil.isEnabled()


def test_sil_onaylanmazsa_hicbir_sey_silinmiyor(qtbot, sayfa, konumlar, monkeypatch):
    arsiv_yaz(konumlar.indirilen)
    monkeypatch.setattr(sayfa, "_onay_al", lambda b, m: False)
    durumu_bekle(qtbot, sayfa)

    sayfa.btnArsivSil.click()

    assert (konumlar.indirilen / "dizin.json").is_file()
    assert "iptal" in sayfa.lblArsivDurum.text()
    assert is_bitti(sayfa)


def test_silme_hatasi_sebebiyle_gosteriliyor(qtbot, sayfa, konumlar, monkeypatch):
    arsiv_yaz(konumlar.indirilen)
    monkeypatch.setattr(sayfa, "_onay_al", lambda b, m: True)

    def patla():
        raise paket.ArsivHatasi("3 dosya silinemedi; ilki kilitli.json")

    monkeypatch.setattr(animedepo, "indirilen_arsivi_sil", patla)
    durumu_bekle(qtbot, sayfa)

    sayfa.btnArsivSil.click()

    qtbot.waitUntil(lambda: "silinemedi" in sayfa.lblArsivDurum.text(), timeout=BEKLE)
    assert "3 dosya silinemedi; ilki kilitli.json" in sayfa.lblArsivDurum.text()
    assert "d63031" in sayfa.lblArsivDurum.styleSheet()


def test_sayfa_kaydirilabilir(sayfa):
    """Dokuz panel küçük ekrana sığmıyor; bölümler kaydırma alanında olmalı."""
    assert sayfa.scroll.widget().isAncestorOf(sayfa.btnArsivIndir)
    assert not sayfa.scroll.widget().isAncestorOf(sayfa.lblStatus), \
        "durum satırı kaymamalı; Kaydet'in sonucu hep görünsün"


def test_belge_ayarlar_bolumunu_anlatiyor():
    """README'ler arşivin Ayarlar'dan nasıl indirileceğini söylemeli ve
    düğme adları kodla aynı olmalı (kullanıcı belgede okuduğunu arar)."""
    kok = Path(__file__).resolve().parent.parent
    for yol in (kok / "README.md", kok / "docs" / "README.md"):
        metin = yol.read_text(encoding="utf-8")
        assert "Çevrimdışı arşiv (TürkAnime)" in metin, yol
        assert settings_mod.TAM_ARSIV_DUGMESI in metin, yol
        assert animedepo.CEVRIMDISI_KLASOR in metin, yol
