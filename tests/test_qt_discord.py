"""Discord Rich Presence: opsiyonellik, ayar anahtarı, durum geçişleri.

Gerçek Discord'a bağlanılmaz — `pypresence.Presence` sahte bir sınıfla
değiştirilir. Testlerin biri paketi tamamen yok sayarak modülün import
edilebilirliğini de sınar.
"""
from __future__ import annotations

import builtins
import importlib
import sys
import threading
import time

import pytest

import turkanime_api.gui.qt.discord as discord_mod
from turkanime_api.gui.qt import prefs
from turkanime_api.gui.qt.discord import SAYFA_DURUMU, DiscordService


class SahtePresence:
    """`pypresence.Presence` yerine geçen kayıt tutucu."""

    ornekler: list = []
    # Sınıf düzeyi: bir sonraki örneğin el sıkışması (Discord açılırken
    # saniyeler sürebiliyor) ve başarısızlığı.
    gecikme = 0.0
    baglanma_hatasi = None

    def __init__(self, app_id):
        self.app_id = app_id
        self.baglandi = False
        self.kapandi = False
        self.guncellemeler: list = []
        self.patlat = False
        self.threadler: set = {threading.current_thread()}
        SahtePresence.ornekler.append(self)

    def connect(self):
        self.threadler.add(threading.current_thread())
        if SahtePresence.gecikme:
            time.sleep(SahtePresence.gecikme)
        if SahtePresence.baglanma_hatasi:
            raise SahtePresence.baglanma_hatasi
        self.baglandi = True

    def update(self, **kwargs):
        self.threadler.add(threading.current_thread())
        if self.patlat:
            raise RuntimeError("boru kapandı")
        self.guncellemeler.append(kwargs)

    def clear(self):
        pass

    def close(self):
        self.kapandi = True


@pytest.fixture
def sahte_rpc(monkeypatch):
    """`pypresence` kuruluymuş gibi davran; örnekleri döndür."""
    SahtePresence.ornekler = []
    SahtePresence.gecikme = 0.0
    SahtePresence.baglanma_hatasi = None
    monkeypatch.setattr(discord_mod, "KULLANILABILIR", True)
    monkeypatch.setattr(discord_mod, "Presence", SahtePresence)
    return SahtePresence.ornekler


# ── Opsiyonellik ─────────────────────────────────────────────────────────────
def test_pypresence_yokken_modul_calisiyor(monkeypatch):
    """Paket hiç kurulu değilken import da servis de çökmemeli."""
    gercek_import = builtins.__import__

    def engelle(ad, *args, **kwargs):
        if ad.split(".")[0] == "pypresence":
            raise ImportError("pypresence kurulu değil")
        return gercek_import(ad, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", engelle)
    sys.modules.pop("pypresence", None)
    try:
        modul = importlib.reload(discord_mod)
        assert modul.KULLANILABILIR is False
        assert modul.Presence is None

        servis = modul.DiscordService()
        assert servis.baslat() is False
        assert servis.bagli is False
        # Hiçbiri istisna atmamalı: özellik sessizce kapalı.
        servis.sayfa("home")
        servis.izliyor("Naruto", "1. Bölüm")
        servis.indiriyor("Naruto", 40)
        servis.ayar_uygula()
        servis.durdur()
    finally:
        monkeypatch.undo()
        importlib.reload(discord_mod)


def test_paket_varken_bile_ayar_kapaliysa_baglanmiyor(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=False)
    servis = DiscordService()
    assert servis.baslat() is False
    assert servis.bagli is False
    assert sahte_rpc == [], "ayar kapalıyken Presence hiç yaratılmamalı"


def test_ayar_okunuyor(ayarla):
    ayarla(discord_rich_presence=False)
    assert prefs.oku().discord is False
    ayarla(discord_rich_presence=True)
    assert prefs.oku().discord is True


# ── Bağlantı ve durum ────────────────────────────────────────────────────────
def bagli_bekle(qtbot, servis, rpc_listesi=None):
    qtbot.waitUntil(lambda: servis.bagli, timeout=3000)
    if rpc_listesi is not None:
        qtbot.waitUntil(lambda: bool(rpc_listesi and rpc_listesi[-1].guncellemeler),
                        timeout=3000)


def test_baglanti_ilk_durumu_gonderiyor(sahte_rpc, ayarla, qtbot):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    assert servis.baslat() is True
    bagli_bekle(qtbot, servis, sahte_rpc)

    rpc = sahte_rpc[0]
    assert rpc.app_id == discord_mod.UYGULAMA_ID
    assert rpc.guncellemeler[0]["details"] == SAYFA_DURUMU["home"]
    assert rpc.guncellemeler[0]["large_image"] == discord_mod.BUYUK_GORSEL
    servis.durdur()


def test_sayfa_gecisi_durumu_degistiriyor(sahte_rpc, ayarla, qtbot):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    bagli_bekle(qtbot, servis, sahte_rpc)
    servis.sayfa("downloads")

    assert servis._istek["details"] == SAYFA_DURUMU["downloads"]
    servis._dongu()      # periyodik tazeleme hız sınırını aşar
    qtbot.waitUntil(lambda: sahte_rpc[0].guncellemeler[-1]["details"]
                    == SAYFA_DURUMU["downloads"], timeout=3000)
    servis.durdur()


def test_hiz_siniri_ard_arda_gondermiyor(sahte_rpc, ayarla, qtbot):
    """Discord ~15 sn'de bir kabul ediyor; fazlası boşuna trafik."""
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    bagli_bekle(qtbot, servis, sahte_rpc)
    for _ in range(5):
        servis.sayfa("search")
    qtbot.wait(200)                 # iş parçacığına fırsat: yine de gönderilmemeli
    assert len(sahte_rpc[0].guncellemeler) == 1
    servis.durdur()


def test_izlerken_baslangic_zamani_ekleniyor(sahte_rpc, ayarla, qtbot):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    servis.izliyor("Naruto", "3. Bölüm")

    assert servis._istek["details"] == "Naruto izliyor"
    assert servis._istek["state"] == "3. Bölüm"
    assert servis._istek["start"] > 0
    servis.durdur()


def test_indirme_durumu(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    servis.indiriyor("Naruto 5. Bölüm", 42)
    assert servis._istek["state"] == "İlerleme: %42"
    servis.durdur()


# ── Ayarın anlık etkisi ──────────────────────────────────────────────────────
def test_ayar_kapatilinca_aninda_kopuyor(sahte_rpc, ayarla, qtbot):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    bagli_bekle(qtbot, servis)
    rpc = sahte_rpc[0]

    ayarla(discord_rich_presence=False)
    servis.ayar_uygula()

    assert servis.bagli is False            # GUI tarafı anında
    qtbot.waitUntil(lambda: rpc.kapandi, timeout=3000)


def test_ayar_acilinca_aninda_baglaniyor(sahte_rpc, ayarla, qtbot):
    ayarla(discord_rich_presence=False)
    servis = DiscordService()
    servis.baslat()
    assert sahte_rpc == []

    ayarla(discord_rich_presence=True)
    servis.ayar_uygula()
    bagli_bekle(qtbot, servis)
    assert len(sahte_rpc) == 1
    servis.durdur()


def test_ayar_sayfasi_anahtari_servisi_tetikliyor(sahte_rpc, ayarla, ayar_uclari, qtbot):
    """Ayarlar sayfasındaki anahtar: hem diske yazmalı hem servisi uygulamalı."""
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    bagli_bekle(qtbot, servis)
    sayfa = ayar_uclari(discord=servis)
    assert sayfa.ayarlar()["degerler"]["discord"] is True

    assert "kapatıldı" in sayfa.discord_ayarla(False)["mesaj"]
    assert prefs.oku().discord is False
    assert servis.bagli is False
    qtbot.waitUntil(lambda: sahte_rpc[0].kapandi, timeout=3000)

    sonuc = sayfa.discord_ayarla(True)
    assert prefs.oku().discord is True
    # El sıkışması arka planda: önce "bağlanılıyor", bitince sayfaya olay.
    assert sonuc["metin"] in ("Discord'a bağlanılıyor…", "Discord'a bağlı")
    bagli_bekle(qtbot, servis)
    qtbot.waitUntil(lambda: (sayfa.kopru.son("discord_durum") or {}).get("metin")
                    == "Discord'a bağlı", timeout=3000)


# ── Kopma / yeniden bağlanma ─────────────────────────────────────────────────
def test_guncelleme_hatasi_baglantiyi_dusuruyor(sahte_rpc, ayarla, qtbot):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    bagli_bekle(qtbot, servis, sahte_rpc)
    durumlar = []
    servis.state_changed.connect(durumlar.append)

    sahte_rpc[0].patlat = True
    servis._dongu()

    qtbot.waitUntil(lambda: not servis.bagli, timeout=3000)
    assert durumlar == [False]
    assert servis._yeniden.isActive(), "yeniden bağlanma planlanmalı"
    servis.durdur()


def test_durdur_ikinci_kez_cagrilabiliyor(sahte_rpc, ayarla, qtbot):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    bagli_bekle(qtbot, servis)
    servis.durdur()
    servis.durdur()          # kapanışta iki kez çağrılabiliyor
    assert servis.bagli is False


# ── Ana pencere ──────────────────────────────────────────────────────────────
def test_ana_pencere_sayfa_gecisini_bildiriyor(main_window):
    """Discord kapalı olsa da istenen durum güncellenmeli (bağlanma denenmez)."""
    main_window.show_page("watchlist")
    assert main_window.discord.bagli is False
    assert main_window.discord._istek["details"] == SAYFA_DURUMU["watchlist"]


# ── GUI thread'i Discord'u beklemiyor (pencere donmasın) ────────────────────
def test_rpc_cagrilari_tek_is_parcaciginda_ve_gui_threadinde_degil(sahte_rpc, ayarla,
                                                                     qtbot):
    """pypresence her çağrıda Discord'un IPC cevabını bekliyor. GUI
    thread'inde olunca Discord meşgulken pencere donuyordu. Nesne yine de
    TEK iş parçacığına ait (asyncio döngüsü iki thread'e bölünmesin)."""
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    bagli_bekle(qtbot, servis, sahte_rpc)
    servis._dongu()
    qtbot.waitUntil(lambda: len(sahte_rpc[0].guncellemeler) >= 2, timeout=3000)
    threadler = sahte_rpc[0].threadler
    assert threading.main_thread() not in threadler
    assert len(threadler) == 1, f"RPC birden çok thread'den çağrıldı: {threadler}"
    servis.durdur()


def test_yavas_el_sikismasi_baslati_bekletmiyor(sahte_rpc, ayarla, qtbot):
    SahtePresence.gecikme = 0.6
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    bas = time.monotonic()
    assert servis.baslat() is True
    assert time.monotonic() - bas < 0.2, "baslat el sıkışmasını bekledi"
    assert servis.bagli is False and servis.baglaniyor is True
    bagli_bekle(qtbot, servis)
    assert servis.baglaniyor is False
    servis.durdur()


def test_baglanirken_kapatilirsa_gec_gelen_baglanti_kapaniyor(sahte_rpc, ayarla, qtbot):
    """Kullanıcı el sıkışması sürerken kapattı: geç gelen "bağlandı" yok
    sayılmalı ve o bağlantı kapatılmalı (Discord'da hayalet durum kalmasın)."""
    SahtePresence.gecikme = 0.4
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    durumlar = []
    servis.state_changed.connect(durumlar.append)
    servis.baslat()
    qtbot.waitUntil(lambda: bool(sahte_rpc), timeout=3000)
    ayarla(discord_rich_presence=False)
    servis.ayar_uygula()
    qtbot.waitUntil(lambda: sahte_rpc[0].kapandi, timeout=3000)
    qtbot.wait(100)
    assert servis.bagli is False and True not in durumlar
    assert sahte_rpc[0].guncellemeler == [], "kapatılan bağlantıya durum gönderildi"


def test_discord_kapaliyken_bagliyor_metninde_kalmiyor(sahte_rpc, ayarla, ayar_uclari,
                                                         qtbot):
    SahtePresence.baglanma_hatasi = ConnectionRefusedError("Discord çalışmıyor")
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    sayfa = ayar_uclari(discord=servis)
    servis.baslat()
    qtbot.waitUntil(lambda: not servis.baglaniyor, timeout=3000)
    assert servis.bagli is False
    qtbot.waitUntil(lambda: bool(sayfa.kopru.son("discord_durum")), timeout=3000)
    assert "bağlanılıyor" not in sayfa.kopru.son("discord_durum")["metin"]
