"""Test paketi gerçekten ağa çıkıyor muydu?

Ölçüm (`socket.connect`/`getaddrinfo` kancalanarak, tüm paket koşturularak):
paket başına 3 dış istek. İkisi `test_cf_bypass.py`'nin sahtelenmemiş
cloudscraper/requests basamaklarından, biri `raw.githubusercontent.com`'a giden
GERÇEK bir güncelleme denetiminden geliyordu.

Sonuncusu bir yarış: `MainWindow` açılıştan 1500 ms sonra `UpdateService`'i
tetikliyor, o da işi arka plan havuzuna atıyor. İşçi thread fonksiyona, testin
`monkeypatch`'i çoktan geri alındıktan sonra ulaşıp gerçek fonksiyonu buluyordu.

Buradaki testler `conftest.py`'deki iki mandalı kilitliyor:
  1. `_ag_mandali`     — loopback dışına çıkan her soket çağrısı kesiliyor.
  2. `_cevresel_taban` — sahtelerin OTURUM BOYU tabanı; teardown gerçek
                         fonksiyonu geri koymuyor.
"""
from __future__ import annotations

import socket

import pytest

from conftest import AgEngellendi


def test_dis_baglanti_engelleniyor():
    with pytest.raises(AgEngellendi):
        socket.create_connection(("example.com", 80), timeout=1)


def test_dns_sorgusu_engelleniyor():
    with pytest.raises(AgEngellendi):
        socket.getaddrinfo("raw.githubusercontent.com", 443)


def test_loopback_serbest(local_server):
    """Mandal yerel test sunucusunu kesmemeli."""
    import urllib.request

    url = local_server()
    with urllib.request.urlopen(url, timeout=5) as yanit:
        assert yanit.status == 200


def test_guncelleme_ucunun_tabani_sahte():
    """Arka plan thread'i per-test sahtenin yanından dolaşabiliyor.

    Bu yüzden asıl güvence tabanda: `monkeypatch` geri aldığında ortaya çıkan
    değer de ağa çıkmayan bir sahte olmalı.
    """
    from turkanime_api.common import updater

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(updater, "surum_bilgisi_getir", lambda *a, **k: {"v": "1"})
        assert updater.surum_bilgisi_getir()["v"] == "1"

    # Bağlam kapandı: geri gelen değer gerçek ağ fonksiyonu OLMAMALI.
    assert updater.surum_bilgisi_getir() == {}


def test_guncelleme_servisi_arka_planda_aga_cikmiyor(qtbot):
    """`UpdateService.kontrol_et` gerçek yolundan geçirilir; ağa çıkarsa yakalanır."""
    from turkanime_api.gui.qt.updates import UpdateService
    from turkanime_api.gui.qt.workers import shutdown_pools

    servis = UpdateService()
    with qtbot.waitSignal(servis.up_to_date, timeout=5000):
        servis.kontrol_et(sessiz=True)
    shutdown_pools(2000)
