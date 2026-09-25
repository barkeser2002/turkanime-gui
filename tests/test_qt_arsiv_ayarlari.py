"""Ayarlar → "Çevrimdışı arşiv (TürkAnime)" bölümü.

turkanime.tv kapandı; "TürkAnime" kaynağı sitenin statik arşivinden okunuyor.
Bu bölüm kullanıcıya arşivin NEREDEN okunduğunu gösteriyor ve tam arşivi
indirme / güncelleme / silme ile elle klasör gösterme işlerini yapıyor.

Testlerin derdi:

* Uçlar kurulurken ne ağa ne diske gidilmeli: ana pencere `AyarlarUclari`nı
  açılışta kuruyor, megabaytlık `dizin.json` her açılışta ayrıştırılırdı.
  Durum ancak Ayarlar sayfası açılınca, arka planda okunuyor.
* Uzun işler (indirme, klasör doğrulama, silme) GUI thread'inde koşmamalı ve
  her sonuç — ilerleme, iptal, hata — kullanıcıya Türkçe ve sebebiyle ulaşmalı.
* Silme YALNIZCA indirilen kopyayı silmeli; depoda veri kökü depo kökü olduğu
  için commit'lenmiş `arsiv/` hemen yanında duruyor.

Ayarlar sayfası web'de: mantık `gui.web.uclar_ayarlar.AyarlarUclari`
uçlarında, düğme metni/etkinliği `ayarlar.js`'te (web testleri).

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
from PySide6.QtWidgets import QFileDialog

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.common import arsiv_paketi as paket
from turkanime_api.gui.web import uclar_ayarlar as ayar_mod
from turkanime_api.gui.web.kopru import Kopru, UcHatasi
from turkanime_api.sources import animedepo

BEKLE = 5000
SON_GUNCELLEME = 1700000000
KONUM = ayar_mod.ARSIV_KONUM_ADLARI
AYARLAR_JS = (Path(ayar_mod.__file__).parent / "statik" / "js" / "sayfalar"
              / "ayarlar.js")
TAM_ARSIV_DUGMESI = f"Tüm arşivi indir (~{ayar_mod.TAM_ARSIV_BOYUTU_MB} MB)"
BOLUM_BASLIGI = "Çevrimdışı Arşiv (TürkAnime)"


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
def uclar(ayar_uclari, izole_ev, konumlar):
    """`izole_ev` şart: uçlar `ayarlar.json`'a yazıyor ve animedepo ayar
    klasörünü aynı veri kökünden okuyor."""
    return ayar_uclari()


def is_bitti(u) -> bool:
    return u._arsiv_mesgul is None                           # noqa: SLF001


def sonucu_bekle(qtbot, u) -> dict:
    """Arka plan işinin `arsiv_sonuc` olayını bekle."""
    qtbot.waitUntil(lambda: u.kopru.son("arsiv_sonuc") is not None and is_bitti(u),
                    timeout=BEKLE)
    return u.kopru.son("arsiv_sonuc")


def ilerleme_metinleri(u) -> List[str]:
    return [v["metin"] for v in u.kopru.hepsi("arsiv_ilerleme")]


# ── Kurulum: ağ yok, disk yok ────────────────────────────────────────────────
def test_kurulumda_arsive_dokunulmuyor(qtbot, ayar_uclari, izole_ev, konumlar, monkeypatch):
    """ESKİ RİSK: durum kurulumda okunsaydı her açılışta (kullanıcı Ayarlar'a
    hiç girmese bile) dizin.json ayrıştırılır, uzak konumda ağa çıkılırdı."""
    cagrilar: List[str] = []
    for ad in ("arsiv_durumu", "arsiv_konumu", "dizin", "fetch_json",
               "tam_arsiv_indir", "indirilen_arsivi_sil"):
        monkeypatch.setattr(animedepo, ad,
                            lambda *a, _ad=ad, **k: cagrilar.append(_ad))
    monkeypatch.setattr(paket, "arsivi_dogrula",
                        lambda *a, **k: cagrilar.append("arsivi_dogrula"))

    u = ayar_uclari()
    qtbot.wait(50)
    QThreadPool.globalInstance().waitForDone(BEKLE)

    assert cagrilar == []
    assert is_bitti(u)
    assert u.kopru.olaylar == []


def test_durum_arka_planda_okunuyor(qtbot, uclar, konumlar, monkeypatch):
    arsiv_yaz(konumlar.depo, adet=4)
    asil = animedepo.arsiv_durumu
    threadler: List[threading.Thread] = []

    def izle():
        threadler.append(threading.current_thread())
        return asil()

    monkeypatch.setattr(animedepo, "arsiv_durumu", izle)
    kopru = Kopru()
    kopru.bagla(uclar)
    yanitlar: list = []
    kopru.yanit.connect(lambda istek, ok, veri: yanitlar.append((ok, json.loads(veri))))

    kopru.cagir("1", "arsiv_durumu", "{}")

    qtbot.waitUntil(lambda: bool(yanitlar), timeout=BEKLE)
    ok, durum = yanitlar[0]
    assert ok and "4 anime" in durum["icerik"]
    assert durum["konum"] == KONUM["depo"]
    assert threadler and threadler[0] is not threading.main_thread(), \
        "durum GUI thread'inde okunmamalı"


def test_sayfa_acilinca_durum_gorunuyor(main_window, web, konumlar):
    arsiv_yaz(konumlar.depo, adet=4)
    main_window.show_page("settings")
    web.bekle("[...document.querySelectorAll('.arsiv-paneli dd')]"
              ".some(e => e.textContent.includes('4 anime'))", timeout=8000)
    assert web.js("document.querySelector('.arsiv-paneli dd').textContent") \
        == KONUM["depo"]


# ── Konum gösterimi ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("kaynak", ["ortam", "ayar", "indirilen", "depo"])
def test_etkin_yerel_konum_yolu_sayisi_ve_tarihi(uclar, konumlar, monkeypatch, kaynak):
    yollar = {"ortam": konumlar.secilen, "ayar": konumlar.secilen,
              "indirilen": konumlar.indirilen, "depo": konumlar.depo}
    arsiv_yaz(yollar[kaynak], adet=1234)
    if kaynak == "ortam":
        monkeypatch.setenv(animedepo.DIZIN_ORTAM_ANAHTARI, str(konumlar.secilen))
    elif kaynak == "ayar":
        Dosyalar().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, str(konumlar.secilen))

    durum = uclar.arsiv_durumu()

    assert durum["konum"] == KONUM[kaynak]
    assert durum["adres"] == str(yollar[kaynak])
    assert durum["icerik"] == f"1.234 anime · son güncelleme {tarih()}"
    assert durum["uyarilar"] == []


def test_uzak_ayna_adresi_ve_aga_cikmadan_bilinmiyor(uclar):
    """Yerel arşiv yokken durum için ağa çıkılmaz (conftest `_session`'ı
    patlatıyor; çıkılsaydı durum okunamadı hatası fırlardı)."""
    durum = uclar.arsiv_durumu()

    assert durum["konum"] == KONUM["uzak"]
    assert durum["adres"] == animedepo.uzak_aynalar()[0]
    assert "Henüz okunmadı" in durum["icerik"]
    assert any("Çevrimdışı kullanmak için tüm arşivi indirin" in u
               for u in durum["uyarilar"])


def test_uzakta_onbellekteki_dizin_sayiliyor(uclar):
    kopya = animedepo.onbellek_dizini() / "dizin.json"
    arsiv_yaz(kopya.parent, adet=2)

    assert uclar.arsiv_durumu()["icerik"] == (
        f"2 anime · son güncelleme {tarih()} (disk önbelleğindeki kopya)")


def test_gecersiz_secilen_klasor_uyarisi(uclar, konumlar):
    """Geçersiz klasör konum çözümünde sessizce atlanıyor; kullanıcı
    gösterdiği klasörün KULLANILMADIĞINI buradan öğrenmeli."""
    arsiv_yaz(konumlar.depo)
    konumlar.secilen.mkdir(parents=True)          # dizin.json yok
    Dosyalar().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, str(konumlar.secilen))

    durum = uclar.arsiv_durumu()

    assert durum["konum"] == KONUM["depo"]
    uyari = " ".join(durum["uyarilar"])
    assert str(konumlar.secilen) in uyari and "geçerli bir arşiv değil" in uyari


def test_silinemeyen_eski_kopya_uyarida_gorunuyor(uclar, konumlar):
    """ESKİ HATA: güncellemede eski kopya silinemezse (`rmtree(ignore_errors=
    True)`) gizli adlı ~0,5 GB klasör sessizce kalıyordu."""
    arsiv_yaz(konumlar.indirilen)
    kalinti = konumlar.indirilen.with_name(".cevrimdisi_arsiv-eski-1a2b3c4d")
    arsiv_yaz(kalinti)

    uyari = " ".join(uclar.arsiv_durumu()["uyarilar"])
    assert "silinemeyen" in uyari and str(kalinti) in uyari


def test_varsayilana_don_suren_arsiv_okumasini_beklemiyor(uclar, konumlar, monkeypatch):
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
        sonuc = uclar.arsiv_varsayilan()
        sure = time.monotonic() - bas
    finally:
        birak.set()
        arka.join(BEKLE / 1000)

    assert sure < 0.5, f"GUI thread'i {sure:.2f} sn dondu"
    assert "Zaten varsayılan" in sonuc["mesaj"]


@pytest.mark.parametrize("indirilen, dugme, sil_acik", [
    (True, "Arşivi güncelle", True),
    (False, TAM_ARSIV_DUGMESI, False),
])
def test_dugmeler_indirilen_arsive_gore(main_window, web, konumlar, indirilen, dugme,
                                        sil_acik):
    arsiv_yaz(konumlar.indirilen if indirilen else konumlar.depo)
    assert main_window.ayarlar_uclari.arsiv_durumu()["indirilen_var"] is indirilen
    main_window.show_page("settings")
    dugmeler = "[...document.querySelectorAll('.arsiv-paneli .dugme-satiri button')]"
    web.bekle(f"{dugmeler}.length === 4 && {dugmeler}[0].textContent.trim() === "
              f"{json.dumps(dugme)}", timeout=8000)
    assert web.js(f"{dugmeler}[3].disabled") is (not sil_acik)
    if indirilen:
        assert main_window.ayarlar_uclari.arsiv_durumu()["indirilen_dizini"] \
            == str(konumlar.indirilen)


def test_durum_okunamazsa_sebebiyle_soyleniyor(qtbot, uclar, monkeypatch):
    def patla():
        raise PermissionError("izin yok: /veri")

    monkeypatch.setattr(animedepo, "arsiv_durumu", patla)
    kopru = Kopru()
    kopru.bagla(uclar)
    yanitlar: list = []
    kopru.yanit.connect(lambda istek, ok, veri: yanitlar.append((ok, json.loads(veri))))

    kopru.cagir("1", "arsiv_durumu", "{}")

    qtbot.waitUntil(lambda: bool(yanitlar), timeout=BEKLE)
    ok, veri = yanitlar[0]
    assert ok is False and "izin yok: /veri" in veri["mesaj"]


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
    monkeypatch.setattr(ayar_mod, "ILERLEME_ARALIGI", 0.0)
    yield sahte
    sahte.devam.set()


def test_indirme_ilerlemesi_ve_basari(qtbot, uclar, sahte_indirme):
    sahte_indirme.adimlar = [
        ("asama", (paket.ASAMA_BAGLANMA, "GitLab")),
        ("asama", (paket.ASAMA_INDIRME, "GitLab")),
        ("ilerleme", (0, 2_000_000)),
        ("ilerleme", (1_000_000, 2_000_000)),
    ]

    assert uclar.arsiv_indir() is True

    qtbot.waitUntil(lambda: any("1,0 / 2,0 MB" in m for m in ilerleme_metinleri(uclar)),
                    timeout=BEKLE)
    assert sahte_indirme.thread is not threading.main_thread()
    son = uclar.kopru.son("arsiv_ilerleme")
    assert "GitLab" in son["metin"] and son["oran"] == 0.5
    assert uclar.arsiv_durumu()["mesgul"] == "indirme"
    for is_ in (uclar.arsiv_indir, uclar.arsiv_klasor_sec, uclar.arsiv_varsayilan,
                lambda: uclar.arsiv_sil(onay=True)):
        with pytest.raises(UcHatasi, match="zaten sürüyor"):
            is_()                  # iş sürerken ikinci bir arşiv işi başlamamalı

    sahte_indirme.devam.set()

    sonuc = sonucu_bekle(qtbot, uclar)
    assert sonuc["tur"] == "tamam" and "Tam arşiv indirildi" in sonuc["mesaj"]
    durum = uclar.arsiv_durumu()
    assert durum["konum"] == KONUM["indirilen"]
    assert durum["icerik"].startswith("6.098 anime")
    assert durum["indirilen_var"] is True and durum["mesgul"] is None


def test_boyut_bilinmeyen_indirme_belirsiz_cubuk(qtbot, uclar, sahte_indirme):
    """GitLab paketi anında üretiyor, Content-Length yok: yüzde uydurulmamalı."""
    sahte_indirme.adimlar = [("ilerleme", (5_300_000, None))]

    uclar.arsiv_indir()

    qtbot.waitUntil(lambda: any("5,3 MB indirildi" in m for m in ilerleme_metinleri(uclar)),
                    timeout=BEKLE)
    son = uclar.kopru.son("arsiv_ilerleme")
    assert "~230 MB" in son["metin"]
    assert son["oran"] is None, "belirsiz çubuk bekleniyordu"


def test_acma_asamasi_gosteriliyor(qtbot, uclar, sahte_indirme):
    """Açarken bayt ilerlemesi akmıyor; aşama gösterilmezse çubuk donmuş görünür."""
    sahte_indirme.adimlar = [("ilerleme", (2_000_000, 2_000_000)),
                             ("asama", (paket.ASAMA_ACMA, "GitHub"))]

    uclar.arsiv_indir()

    qtbot.waitUntil(lambda: any("açılıyor" in m for m in ilerleme_metinleri(uclar)),
                    timeout=BEKLE)
    assert uclar.kopru.son("arsiv_ilerleme")["oran"] is None


def test_iptal_indirmeyi_durduruyor(qtbot, uclar, sahte_indirme):
    uclar.arsiv_indir()
    assert sahte_indirme.basladi.wait(BEKLE / 1000)

    assert uclar.arsiv_iptal() is True

    sonuc = sonucu_bekle(qtbot, uclar)
    assert sahte_indirme.iptal is not None and sahte_indirme.iptal.is_set()
    assert "iptal edildi" in sonuc["mesaj"]
    assert uclar.arsiv_iptal() is False, "bitmiş işte iptal bir şey yapmamalı"
    assert not (sahte_indirme.hedef / "dizin.json").exists()


@pytest.mark.parametrize("hata, beklenen", [
    (paket.ArsivHatasi("tam arşiv indirilemedi — GitLab: HTTP 503; "
                       "GitHub: zaman aşımı"),
     "Arşiv indirilemedi. Sebep: GitLab: HTTP 503; GitHub: zaman aşımı"),
    (PermissionError("[Errno 13] izin yok: '/veri'"),
     "Arşiv indirilemedi. Sebep: [Errno 13] izin yok: '/veri'"),
])
def test_indirme_hatasi_sebebiyle_gosteriliyor(qtbot, uclar, sahte_indirme, hata, beklenen):
    sahte_indirme.hata = hata
    sahte_indirme.devam.set()

    uclar.arsiv_indir()

    sonuc = sonucu_bekle(qtbot, uclar)
    assert sonuc["tur"] == "hata", "hata rengiyle gösterilmeli"
    assert beklenen in sonuc["mesaj"]


def test_ilerleme_seyreltiliyor(qtbot, uclar, monkeypatch, konumlar):
    """Paket 64 KB'lık parçalarla akıyor (~3600 çağrı); hepsi sayfaya taşınırsa
    olay kuyruğu boğulur. Seyreltme GERÇEK aralıkla (0,1 sn) sınanıyor."""
    def sahte(ilerleme=None, iptal=None, hedef=None, asama=None):
        for i in range(2000):
            ilerleme(i * 65536, None)
        raise paket.IptalEdildi("bitti")

    monkeypatch.setattr(animedepo, "tam_arsiv_indir", sahte)

    uclar.arsiv_indir()

    sonucu_bekle(qtbot, uclar)
    metinler = ilerleme_metinleri(uclar)
    assert metinler[:2] == ["Bağlanılıyor…", "0,0 MB indirildi (toplam ~230 MB)"], \
        "ilk değer hemen gösterilmeli"
    assert len(metinler) < 100, f"{len(metinler)} ilerleme sayfaya taşındı"


def test_kapanista_suren_indirme_iptal_ediliyor(qtbot, main_window, monkeypatch, konumlar):
    """Pencere kapanınca indirme iptal edilmeli; yoksa süreç ~230 MB bitene
    kadar kapanmaz (bkz. `MainWindow.closeEvent`)."""
    sahte = SahteIndirme(konumlar.indirilen)
    monkeypatch.setattr(animedepo, "tam_arsiv_indir", sahte)
    main_window.ayarlar_uclari.arsiv_indir()
    assert sahte.basladi.wait(BEKLE / 1000)

    main_window.close()

    assert sahte.iptal is not None and sahte.iptal.is_set()
    QThreadPool.globalInstance().waitForDone(BEKLE)


# ── Klasör seç / varsayılana dön ─────────────────────────────────────────────
def test_klasor_sec_gecerli_arsivi_kaydediyor_ve_onbellegi_sifirliyor(
        qtbot, uclar, konumlar, monkeypatch):
    arsiv_yaz(konumlar.secilen, adet=7)
    assert animedepo.arsiv_konumu().kaynak == "uzak"     # önbelleğe girsin
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(konumlar.secilen)))

    assert uclar.arsiv_klasor_sec() is True

    sonuc = sonucu_bekle(qtbot, uclar)
    assert sonuc["tur"] == "tamam" and "7 anime" in sonuc["mesaj"]
    assert Dosyalar().ayarlar[animedepo.DIZIN_AYAR_ANAHTARI] == str(konumlar.secilen)
    assert animedepo.arsiv_konumu() == animedepo.ArsivKonumu("ayar", konumlar.secilen), \
        "modül önbelleği sıfırlanmazsa yeni klasör yeniden başlatana kadar kullanılmazdı"
    assert uclar.arsiv_durumu()["konum"] == KONUM["ayar"]


def test_klasor_sec_dizin_json_yoksa_reddediyor(qtbot, uclar, konumlar, monkeypatch):
    konumlar.secilen.mkdir(parents=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(konumlar.secilen)))

    uclar.arsiv_klasor_sec()

    sonuc = sonucu_bekle(qtbot, uclar)
    assert sonuc["tur"] == "hata"
    assert "arşiv olarak kullanılamaz" in sonuc["mesaj"] and "dizin.json" in sonuc["mesaj"]
    assert animedepo.DIZIN_AYAR_ANAHTARI not in Dosyalar().ayarlar


def test_klasor_sec_vazgecilirse_hicbir_sey_olmuyor(uclar, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: ""))
    assert uclar.arsiv_klasor_sec() is False
    assert is_bitti(uclar)
    assert animedepo.DIZIN_AYAR_ANAHTARI not in Dosyalar().ayarlar


def test_varsayilana_don_ayari_siliyor(uclar, konumlar):
    arsiv_yaz(konumlar.secilen)
    arsiv_yaz(konumlar.depo)
    Dosyalar().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, str(konumlar.secilen))
    assert animedepo.arsiv_konumu().kaynak == "ayar"

    sonuc = uclar.arsiv_varsayilan()

    assert animedepo.DIZIN_AYAR_ANAHTARI not in Dosyalar().ayarlar
    assert animedepo.arsiv_konumu().kaynak == "depo"
    assert sonuc["tur"] == "tamam" and "unutuldu" in sonuc["mesaj"]
    assert uclar.arsiv_durumu()["konum"] == KONUM["depo"]


def test_varsayilana_don_ayar_yoksa_bilgi(uclar):
    sonuc = uclar.arsiv_varsayilan()
    assert sonuc["tur"] == "bilgi" and "Zaten varsayılan" in sonuc["mesaj"]


# ── İndirilen arşivi sil ─────────────────────────────────────────────────────
def test_sil_yalnizca_indirileni_siliyor(qtbot, uclar, konumlar):
    """Depoda veri kökü depo KÖKÜ: `arsiv/` hemen yanında ve silinmemeli."""
    arsiv_yaz(konumlar.indirilen)
    arsiv_yaz(konumlar.depo)
    assert uclar.arsiv_durumu()["indirilen_var"]

    assert uclar.arsiv_sil(onay=True) is True

    sonuc = sonucu_bekle(qtbot, uclar)
    assert sonuc["tur"] == "tamam" and "silindi" in sonuc["mesaj"]
    assert not konumlar.indirilen.exists()
    assert (konumlar.depo / "dizin.json").is_file()
    durum = uclar.arsiv_durumu()
    assert durum["konum"] == KONUM["depo"] and durum["indirilen_var"] is False


def test_sil_onaysiz_hicbir_sey_silinmiyor(uclar, konumlar):
    arsiv_yaz(konumlar.indirilen)
    with pytest.raises(UcHatasi, match="onaylanmadı"):
        uclar.arsiv_sil()
    assert (konumlar.indirilen / "dizin.json").is_file()
    assert is_bitti(uclar)


def test_silme_onayi_sayfada_yolu_soyluyor(main_window, web, konumlar):
    """Sayfanın onay penceresi NEYİN silineceğini söylemeli; vazgeçince silinmez."""
    arsiv_yaz(konumlar.indirilen)
    main_window.show_page("settings")
    sil = ("[...document.querySelectorAll('.arsiv-paneli .dugme-satiri button')]"
           ".find(b => b.textContent.includes('arşivi sil'))")
    web.bekle(f"!!{sil} && !{sil}.disabled", timeout=8000)
    web.js(f"{sil}.click()")
    web.bekle("!!document.querySelector('.modal')")
    metin = web.js("document.querySelector('.modal').innerText")
    assert str(konumlar.indirilen) in metin, "onay metni neyin silineceğini söylemeli"
    web.js("[...document.querySelectorAll('.modal button')]"
           ".find(b => b.textContent.includes('Vazgeç')).click()")
    web.bekle("document.querySelector('.arsiv-sonuc').textContent.includes('iptal')")
    assert (konumlar.indirilen / "dizin.json").is_file()


def test_silme_hatasi_sebebiyle_gosteriliyor(qtbot, uclar, konumlar, monkeypatch):
    arsiv_yaz(konumlar.indirilen)

    def patla():
        raise paket.ArsivHatasi("3 dosya silinemedi; ilki kilitli.json")

    monkeypatch.setattr(animedepo, "indirilen_arsivi_sil", patla)

    uclar.arsiv_sil(onay=True)

    sonuc = sonucu_bekle(qtbot, uclar)
    assert sonuc["tur"] == "hata"
    assert "3 dosya silinemedi; ilki kilitli.json" in sonuc["mesaj"]


def test_belge_ayarlar_bolumunu_anlatiyor():
    """README'ler arşivin Ayarlar'dan nasıl indirileceğini söylemeli ve
    düğme adları sayfayla aynı olmalı (kullanıcı belgede okuduğunu arar)."""
    sayfa = AYARLAR_JS.read_text(encoding="utf-8")
    assert BOLUM_BASLIGI in sayfa and "Tüm arşivi indir (~" in sayfa
    kok = Path(__file__).resolve().parent.parent
    for yol in (kok / "README.md", kok / "docs" / "README.md"):
        metin = yol.read_text(encoding="utf-8")
        assert BOLUM_BASLIGI in metin, yol
        assert TAM_ARSIV_DUGMESI in metin, yol
        assert animedepo.CEVRIMDISI_KLASOR in metin, yol
