"""Discord Rich Presence: opsiyonellik, ayar anahtarı, durum geçişleri.

Gerçek Discord'a bağlanılmaz — `pypresence.Presence` sahte bir sınıfla
değiştirilir. Testlerin biri paketi tamamen yok sayarak modülün import
edilebilirliğini de sınar.
"""
from __future__ import annotations

import builtins
import importlib
import sys

import pytest

import turkanime_api.gui.qt.discord as discord_mod
from turkanime_api.gui.qt import prefs
from turkanime_api.gui.qt.discord import SAYFA_DURUMU, DiscordService


class SahtePresence:
    """`pypresence.Presence` yerine geçen kayıt tutucu."""

    ornekler: list = []

    def __init__(self, app_id):
        self.app_id = app_id
        self.baglandi = False
        self.kapandi = False
        self.guncellemeler: list = []
        self.patlat = False
        SahtePresence.ornekler.append(self)

    def connect(self):
        self.baglandi = True

    def update(self, **kwargs):
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
def test_baglanti_ilk_durumu_gonderiyor(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    assert servis.baslat() is True
    assert servis.bagli is True

    rpc = sahte_rpc[0]
    assert rpc.app_id == discord_mod.UYGULAMA_ID
    assert rpc.guncellemeler[0]["details"] == SAYFA_DURUMU["home"]
    assert rpc.guncellemeler[0]["large_image"] == discord_mod.BUYUK_GORSEL
    servis.durdur()


def test_sayfa_gecisi_durumu_degistiriyor(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    servis.sayfa("downloads")

    assert servis._istek["details"] == SAYFA_DURUMU["downloads"]
    servis._dongu()      # periyodik tazeleme hız sınırını aşar
    assert sahte_rpc[0].guncellemeler[-1]["details"] == SAYFA_DURUMU["downloads"]
    servis.durdur()


def test_hiz_siniri_ard_arda_gondermiyor(sahte_rpc, ayarla):
    """Discord ~15 sn'de bir kabul ediyor; fazlası boşuna trafik."""
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    for _ in range(5):
        servis.sayfa("search")
    assert len(sahte_rpc[0].guncellemeler) == 1
    servis.durdur()


def test_izlerken_baslangic_zamani_ekleniyor(sahte_rpc, ayarla):
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
def test_ayar_kapatilinca_aninda_kopuyor(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    rpc = sahte_rpc[0]

    ayarla(discord_rich_presence=False)
    servis.ayar_uygula()

    assert servis.bagli is False
    assert rpc.kapandi is True


def test_ayar_acilinca_aninda_baglaniyor(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=False)
    servis = DiscordService()
    servis.baslat()
    assert sahte_rpc == []

    ayarla(discord_rich_presence=True)
    servis.ayar_uygula()
    assert servis.bagli is True and len(sahte_rpc) == 1
    servis.durdur()


def test_ayar_sayfasi_anahtari_servisi_tetikliyor(qtbot, sahte_rpc, ayarla):
    """Ayarlar sayfasındaki kutu: hem diske yazmalı hem servisi uygulamalı."""
    from turkanime_api.gui.qt.pages.settings import SettingsPage

    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    sayfa = SettingsPage(discord=servis)
    qtbot.addWidget(sayfa)
    assert sayfa.chkDiscord.isChecked() is True

    sayfa.chkDiscord.setChecked(False)
    assert prefs.oku().discord is False
    assert servis.bagli is False
    assert sahte_rpc[0].kapandi is True

    sayfa.chkDiscord.setChecked(True)
    assert prefs.oku().discord is True
    assert servis.bagli is True


# ── Kopma / yeniden bağlanma ─────────────────────────────────────────────────
def test_guncelleme_hatasi_baglantiyi_dusuruyor(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    durumlar = []
    servis.state_changed.connect(durumlar.append)

    sahte_rpc[0].patlat = True
    servis._dongu()

    assert servis.bagli is False
    assert durumlar == [False]
    assert servis._yeniden.isActive(), "yeniden bağlanma planlanmalı"
    servis.durdur()


def test_durdur_ikinci_kez_cagrilabiliyor(sahte_rpc, ayarla):
    ayarla(discord_rich_presence=True)
    servis = DiscordService()
    servis.baslat()
    servis.durdur()
    servis.durdur()          # kapanışta iki kez çağrılabiliyor
    assert servis.bagli is False


# ── Ana pencere ──────────────────────────────────────────────────────────────
def test_ana_pencere_sayfa_gecisini_bildiriyor(main_window):
    """Discord kapalı olsa da istenen durum güncellenmeli (bağlanma denenmez)."""
    main_window.show_page("watchlist")
    assert main_window.discord.bagli is False
    assert main_window.discord._istek["details"] == SAYFA_DURUMU["watchlist"]
