"""Ayarlar sayfası: yazılan ayar gerçekten diske gidiyor mu, kimlikler kaynağa
ulaşıyor mu?

Buradaki testlerin ortak derdi "ayar var gibi görünüyor ama hiçbir işe
yaramıyor" sınıfı hatalar:

* TRAnimeİzle çerezi diske yazılıyordu ama süreç açılışında kimse
  `sources.tranime.set_session_cookie`'ye vermiyordu; global None kalıyor,
  `search_tranime` daha isteği kurmadan boş liste dönüyordu.
* Dört ayar (`1080p aday sayisi`, `izlerken kaydet`, `izlendi ikonu`,
  `manuel fansub`) davranışı belirliyordu ama Qt tarafında yazacak kontrol
  yoktu — yalnızca okunuyorlardı.
* OpenAnime kaynağı kullanıcıya "Ayarlar'dan token'ını girin" diyordu; öyle bir
  alan hiç olmadı.

`prefs` SAHTELENMİYOR: soru "ayar okunuyor mu?" değil, "diske yazılıp diskten
geri okunuyor mu?". Sahte bir `prefs` yalnızca sahteyi sınardı. Bu yüzden her
test `izole_ev` ile geçici bir yapılandırma köküne bağlanıyor.
"""
from __future__ import annotations

import pytest

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.gui.qt import prefs

# Gerçek "Tarayıcıdan Al" çıktısıyla aynı biçim: 7 alan, tab ile ayrılmış.
CEREZ = (".tranimeizle.co\tTRUE\t/\tTRUE\t2000000000\t"
         ".AitrWeb.Session\tSAHTE-OTURUM-DEGERI")


@pytest.fixture
def sayfa(qtbot, izole_ev):
    """Tek başına açılmış `SettingsPage` (ana pencere kurmadan).

    `izole_ev` şart: sayfa gerçek `ayarlar.json`'a yazıyor, izole edilmezse
    test kullanıcının yapılandırmasını bozar.
    """
    from turkanime_api.gui.qt.pages.settings import SettingsPage

    sf = SettingsPage()
    qtbot.addWidget(sf)
    return sf


@pytest.fixture
def temiz_kaynak_global(monkeypatch):
    """Kaynak modüllerinin süreç-içi kimlik global'lerini sıfırla.

    Bu global'ler modül düzeyinde; başka bir test onları doldurmuş olsaydı
    "açılışta yüklendi" iddiası yalancı yeşil verirdi. `monkeypatch` test
    sonunda eski değerleri geri koyar.
    """
    from turkanime_api.sources import openani, tranime

    monkeypatch.setattr(tranime, "SESSION_COOKIE", None)
    monkeypatch.setattr(tranime, "_EXTRA_COOKIES", {})
    monkeypatch.setattr(openani, "OPENANI_TOKEN", None)
    monkeypatch.setattr(openani, "OPENANI_REFRESH_TOKEN", None)
    return tranime, openani


# ── Eski Türkçe ayar adının göçü ─────────────────────────────────────────────
def test_eski_turkce_ayar_adi_ascii_adina_gocuyor(izole_ev):
    """ESKİ HATA: `prefs` "1080p aday sayısı" (ı) adını okuyordu, `dosyalar`
    "ayar isimleri ascii olmalı" diyordu ve anahtar varsayılanlarda hiç yoktu.
    Aynı ayar iki adla yaşayınca hangisinin kazandığı okuyucuya kalıyordu."""
    Dosyalar().set_ayar("1080p aday sayısı", 17)

    Dosyalar()                       # göç açılışta yapılır

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["1080p aday sayisi"] == 17, "değer ASCII ada taşınmalı"
    assert "1080p aday sayısı" not in ayarlar, "eski ad dosyada kalmamalı"


def test_gocen_deger_varsayilani_ezmeli(izole_ev):
    """ESKİ HATA adayı: eksik anahtar tamamlaması göçten sonra çalışsaydı
    kullanıcının seçtiği sayı sessizce varsayılan 8'e dönerdi."""
    Dosyalar().set_ayar("1080p aday sayısı", 2)

    assert prefs.oku().aday_sayisi == 2


@pytest.mark.parametrize("ayarlar, beklenen", [
    ({"1080p aday sayisi": 4, "1080p aday sayısı": 9}, 4),   # ASCII kanonik
    ({"1080p aday sayısı": 9}, 9),                           # eski ad yedek
    ({}, prefs.VARSAYILAN_ADAY),
    ({"1080p aday sayisi": "abc"}, prefs.VARSAYILAN_ADAY),   # elle bozulmuş
])
def test_aday_sayisi_cozumleme_sirasi(ayarlar, beklenen):
    """Göç yazamadan düşen bir çalıştırmada dosyada eski ad kalabilir; o zaman
    bile kullanıcının seçtiği sayı sessizce varsayılana dönmemeli. ASCII ad
    varsa kanonik odur — iki ad birden varsa yeni olan kazanır."""
    assert prefs._aday_sayisi(ayarlar) == beklenen


def test_ascii_ad_varsayilanlarda_var(izole_ev):
    """Varsayılansız ayar, "kimse yazmadıysa ne olacak?" sorusunu her okuyucuya
    ayrı sordurur; anahtar artık `default_ayarlar`da."""
    assert Dosyalar().ayarlar["1080p aday sayisi"] == prefs.VARSAYILAN_ADAY


# ── Dört eksik kontrol ───────────────────────────────────────────────────────
def test_dort_ayar_kaydet_ile_diske_yaziliyor(sayfa):
    """ESKİ HATA: `save()` yalnızca 6 anahtar yazıyordu; aday sayısı, izlerken
    kaydet, izlendi ikonu ve manuel fansub'un Qt'de yazıcısı yoktu."""
    sayfa.spnAday.setValue(11)
    sayfa.chkWhileWatching.setChecked(True)
    sayfa.chkWatchedIcon.setChecked(False)
    sayfa.chkManualFansub.setChecked(True)

    sayfa.save()

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["1080p aday sayisi"] == 11
    assert ayarlar["izlerken kaydet"] is True
    assert ayarlar["izlendi ikonu"] is False
    assert ayarlar["manuel fansub"] is True


def test_kaydedilen_dort_ayar_prefs_uzerinden_geri_okunuyor(sayfa):
    """Yazım ile okuma aynı anahtar adında buluşmalı: `prefs.oku` ayar
    adlarını kendi listesinden çözüyor, sayfa başka bir ad yazsa fark
    edilmezdi."""
    sayfa.spnAday.setValue(11)
    sayfa.chkWhileWatching.setChecked(True)
    sayfa.chkWatchedIcon.setChecked(False)
    sayfa.chkManualFansub.setChecked(True)

    sayfa.save()

    tercih = prefs.oku()
    assert tercih.aday_sayisi == 11
    assert tercih.izlerken_kaydet is True
    assert tercih.izlendi_ikonu is False
    assert tercih.manuel_fansub is True


def test_reload_dort_ayari_diskten_kontrollere_yaziyor(sayfa):
    """Kaydedilen değer forma dönmezse kullanıcı bir sonraki açılışta eski
    değeri görür ve farkında olmadan geri yazar."""
    Dosyalar().set_ayar(ayar_list={
        "1080p aday sayisi": 5, "izlerken kaydet": True,
        "izlendi ikonu": False, "manuel fansub": True})

    sayfa.reload()

    assert sayfa.spnAday.value() == 5
    assert sayfa.chkWhileWatching.isChecked() is True
    assert sayfa.chkWatchedIcon.isChecked() is False
    assert sayfa.chkManualFansub.isChecked() is True


def test_kaydet_onceki_ayarlari_bozmuyor(sayfa):
    """Yeni anahtarlar eklenirken eski altı anahtarın yazımı düşmemeli."""
    hedef = sayfa._dosya().ta_path
    sayfa.txtDir.setText(hedef)
    sayfa.spnParallel.setValue(7)
    sayfa.chkMaxRes.setChecked(False)
    sayfa.chkRemember.setChecked(False)
    sayfa.chkAria.setChecked(True)

    sayfa.save()

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["indirilenler"] == hedef
    assert ayarlar["paralel indirme sayisi"] == 7
    assert ayarlar["max resolution"] is False
    assert ayarlar["dakika hatirla"] is False
    assert ayarlar["aria2c kullan"] is True


# ── TRAnimeİzle çerezi süreç içine giriyor mu? ───────────────────────────────
def test_acilista_diskteki_cerez_kaynaga_ulasiyor(qtbot, izole_ev,
                                                  temiz_kaynak_global):
    """ESKİ HATA: çerez diske yazılıyor ama `set_session_cookie` üretimde hiç
    çağrılmıyordu; `SESSION_COOKIE` None kalıyor ve `search_tranime` daha
    isteği kurmadan boş liste dönüyordu — kullanıcı her açılışta 0 bölüm."""
    from turkanime_api.gui.qt.pages.settings import SettingsPage
    tranime, _ = temiz_kaynak_global

    Dosyalar().set_ayar("tranime_cookie", CEREZ)
    assert tranime.SESSION_COOKIE is None, "ön koşul: süreçte çerez yok"

    qtbot.addWidget(SettingsPage())   # açılışta kurulan sayfa

    assert tranime.SESSION_COOKIE == "SAHTE-OTURUM-DEGERI"


def test_acilista_yuklenen_cerez_istek_basliklarina_giriyor(qtbot, izole_ev,
                                                            temiz_kaynak_global):
    """Global'i doldurmak yetmez: çerez giden isteğe de girmeli.

    `_get_cookies()` her TRAnimeİzle isteğinin çerez sözlüğünü kuruyor; asıl
    kanıt burada. Ağa çıkılmıyor — yalnızca sözlük kuruluyor.
    """
    from turkanime_api.gui.qt.pages.settings import SettingsPage
    tranime, _ = temiz_kaynak_global

    Dosyalar().set_ayar("tranime_cookie", CEREZ)
    assert ".AitrWeb.Session" not in tranime._get_cookies()

    qtbot.addWidget(SettingsPage())

    assert tranime._get_cookies()[".AitrWeb.Session"] == "SAHTE-OTURUM-DEGERI"


def test_yeni_cerez_kaydedilince_aninda_kaynaga_gidiyor(sayfa,
                                                        temiz_kaynak_global):
    """Çerez alındıktan sonra yeniden başlatmak gerekmemeli."""
    tranime, _ = temiz_kaynak_global

    sayfa._on_cookie_ready(CEREZ)

    assert Dosyalar().ayarlar["tranime_cookie"] == CEREZ
    assert tranime.SESSION_COOKIE == "SAHTE-OTURUM-DEGERI"


def test_cerez_temizlenince_surec_ici_kopya_da_dusuyor(sayfa,
                                                       temiz_kaynak_global):
    """ESKİ HATA: "Temizle" yalnızca diski siliyordu; kaynak, uygulama kapanana
    kadar iptal edilmiş çerezle istek atmaya devam ediyordu."""
    tranime, _ = temiz_kaynak_global
    sayfa._on_cookie_ready(CEREZ)
    assert tranime.SESSION_COOKIE

    sayfa._clear_cookie()

    assert Dosyalar().ayarlar["tranime_cookie"] == ""
    assert tranime.SESSION_COOKIE is None


# ── OpenAnime jetonu ─────────────────────────────────────────────────────────
def test_openani_jetonu_kaydedilip_kaynaga_gidiyor(sayfa, temiz_kaynak_global):
    """ESKİ HATA: `sources/openani.py` ölü CDN uçlarında "Ayarlar'dan OpenAnime
    token'ını girmeyi deneyin" diyordu ama böyle bir alan yoktu."""
    _, openani = temiz_kaynak_global

    sayfa.txtOpenAniToken.setText("JETON-123")
    sayfa.txtOpenAniRefresh.setText("TAZELE-456")
    sayfa.save()

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["openani_token"] == "JETON-123"
    assert ayarlar["openani_refresh_token"] == "TAZELE-456"
    assert openani.OPENANI_TOKEN == "JETON-123"
    assert openani.OPENANI_REFRESH_TOKEN == "TAZELE-456"


def test_acilista_diskteki_openani_jetonu_kaynaga_ulasiyor(qtbot, izole_ev,
                                                           temiz_kaynak_global):
    """Jeton da çerez gibi süreç-içi global; açılışta geri yüklenmezse ancak
    kullanıcı Ayarlar'a girip Kaydet'e basınca etkili olurdu."""
    from turkanime_api.gui.qt.pages.settings import SettingsPage
    _, openani = temiz_kaynak_global

    Dosyalar().set_ayar(ayar_list={"openani_token": "JETON-ACILIS",
                                   "openani_refresh_token": "TAZELE-ACILIS"})

    qtbot.addWidget(SettingsPage())

    assert openani.OPENANI_TOKEN == "JETON-ACILIS"
    assert openani.OPENANI_REFRESH_TOKEN == "TAZELE-ACILIS"


def test_openani_jetonu_gizli_yaziliyor(sayfa):
    """Jeton hesabın kendisi demek; ekranda düz metin durmamalı."""
    from PySide6.QtWidgets import QLineEdit

    assert sayfa.txtOpenAniToken.echoMode() == QLineEdit.EchoMode.Password
    assert sayfa.txtOpenAniRefresh.echoMode() == QLineEdit.EchoMode.Password
