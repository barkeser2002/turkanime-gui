"""CLI giriş noktası (`turkanime_api/cli/__main__.py`) ve sürüm denetimi testleri.

CLI, Qt arayüzünün gölgesinde kalıp test edilmemişti; `pyproject.toml` hâlâ
`turkanime` ve `turkanime-cli` konsol betiklerini tanımlıyor, yani kullanıcı
bulabildiği bir yüzey. Buradaki testler açılış yolunu (güncelleme denetimi,
gereksinim denetimi, TürkAnime bağlantısı) ve tembel kaynak import'larını
korur.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parent.parent
ANA_DOSYA = KOK / "turkanime_api" / "cli" / "__main__.py"


# ── Tembel kaynak import'ları ────────────────────────────────────────────────
def _kaynak_import_listesi():
    """`__main__.py`'deki tüm `from ..sources... import ...` ifadeleri.

    `ast` kullanılıyor çünkü bu import'ların çoğu FONKSİYON GÖVDESİNDE: modülü
    import etmek onları çalıştırmıyor, dolayısıyla bir yazım hatası ancak o dal
    ilk kez çalıştığında ortaya çıkıyor.
    """
    agac = ast.parse(ANA_DOSYA.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.ImportFrom) or dugum.level != 2:
            continue
        modul = dugum.module or ""
        if modul == "sources" or modul.startswith("sources."):
            yield modul, [ad.name for ad in dugum.names]


def test_kaynak_import_adlari_gercekten_var():
    """ESKİ HATA: `from ..sources.tranime import get_tranime_episodes` (:177) ve
    `get_tranime_episode_details` (:307) — bu iki ad `tranime.py`'de hiç
    olmadı (gerçekleri `get_anime_episodes` ve `get_episode_details`). Import
    fonksiyon gövdesinde olduğu için hata ancak TRAnimeİzle seçili kullanıcı
    menüye girince patlıyor; oradaki `except (KeyError, IndexError)`
    ImportError'ı yakalamadığından CLI en dıştaki `sys.exit(1)`e düşüyor,
    yani kaynak seçilir seçilmez komple çöküyordu.
    """
    ifadeler = list(_kaynak_import_listesi())
    assert ifadeler, "kaynak import'ları bulunamadı — tarama bozulmuş olabilir"

    eksikler = []
    for modul, adlar in ifadeler:
        hedef = importlib.import_module(f"turkanime_api.{modul}")
        eksikler += [f"{modul}.{ad}" for ad in adlar if not hasattr(hedef, ad)]
    assert eksikler == [], f"__main__ var olmayan adları import ediyor: {eksikler}"


# ── Sürüm / güncelleme denetimi ──────────────────────────────────────────────
def test_update_type_surum_yoksa_cokmuyor():
    """ESKİ HATA: `update_type(None)` → AttributeError ('NoneType' object has no
    attribute 'split'). `guncel_surum()` sürüm bilgisi eksik geldiğinde None
    döndürüyor ve açılıştaki denetim bunu doğrudan `.split(".")` ediyordu.
    """
    from turkanime_api.cli.version import update_type

    assert update_type(None) is None


def test_update_type_etiketli_surumde_cokmuyor():
    """ESKİ HATA: eski gövde parçaları `int()` ile karşılaştırıyordu; "10.1.0-rc1"
    gibi etiketli bir sürüm ValueError yükseltiyordu.
    """
    from turkanime_api.cli.version import update_type

    assert update_type("999.0.0-rc1") == "Radikal"


@pytest.mark.parametrize("uzak,beklenen", [
    ("9.4.0", None),        # eski sürüm: duyuru yok
    ("10.0.0", None),       # aynı sürüm
    ("10.0.0.0", None),     # "10.0.0" ile "10.0.0.0" aynı sürüm
    ("11.0.0", "Radikal"),
    ("10.1.0", "Özellik"),
    ("10.0.1", "Onarım"),
    ("10.0.0.1", "Onarım"),  # 3'ten uzun sürümde kuyruk farkı yamadır
])
def test_update_type_farkin_derecesini_dogru_adlandiriyor(monkeypatch, uzak, beklenen):
    """ESKİ HATA: eski döngü `break` sonrası `i`yi kullanıyordu; sürüm eşitken
    `i` son turdan kalma değeri taşıyordu ve dallanma okunaksızdı.
    """
    from turkanime_api.cli import version as surum_modulu

    monkeypatch.setattr(surum_modulu, "__version__", "10.0.0")
    assert surum_modulu.update_type(uzak) == beklenen


def test_guncel_surum_ortak_kanaldan_okuyor(monkeypatch):
    """ESKİ HATA: üç ayrı uç vardı ve üçü de yanlış hedefteydi — kaynak kurulumda
    olmayan `master` dalındaki `pyproject.toml`, pip kurulumunda BAŞKASININ
    PyPI paketi ("turkanime-cli"; bu paketin adı "turkanime-gui"), exe'de ise
    Qt'nin okuduğundan farklı bir uç. Artık tek uç `common/updater.py`.
    """
    from turkanime_api.cli import version as surum_modulu
    from turkanime_api.common import updater

    monkeypatch.setattr(updater, "surum_bilgisi_getir",
                        lambda **_: {"version": "12.3.4"})
    assert surum_modulu.guncel_surum() == "12.3.4"


def test_guncel_surum_eksik_veride_none_donuyor(monkeypatch):
    """ESKİ HATA: `version.json` sürüm alanı olmadan gelince eski kod boş bir
    regex sonucundan `None` üretip onu `update_type`a veriyordu (yukarıdaki
    AttributeError'ın kaynağı). Artık `None` üretiliyor ve zinciri kırmıyor.
    """
    from turkanime_api.cli import version as surum_modulu
    from turkanime_api.common import updater

    monkeypatch.setattr(updater, "surum_bilgisi_getir", lambda **_: {})
    assert surum_modulu.guncel_surum() is None
    assert surum_modulu.update_type(surum_modulu.guncel_surum()) is None


def test_version_modulu_olu_uclari_barindirmiyor():
    """ESKİ HATA: `master` dalı ve `turkanime-cli` PyPI adı kaynakta sabit
    yazılıydı. Bu test o iki dizgenin KOD'a geri sızmasını engeller (belge
    niteliğindeki açıklama satırları hariç).
    """
    kaynak = (KOK / "turkanime_api" / "cli" / "version.py").read_text(encoding="utf-8")
    govde = kaynak.split('"""', 2)[-1]        # modül docstring'i anlatım, kod değil
    assert "master/" not in govde
    assert "turkanime-cli" not in govde
    assert "pypi.org" not in govde


# ── Açılış akışı ─────────────────────────────────────────────────────────────
@pytest.fixture
def cli(monkeypatch, izole_ev):
    """`main()`'i ağsız, etkileşimsiz ve ekranı silmeden koşturulabilir yap.

    `izole_ev` şart: `main()` artık `Dosyalar().ayarlar`ı okuyor ve test
    kullanıcının gerçek `ayarlar.json`'una dokunmamalı.
    """
    from turkanime_api.cli import __main__ as ana

    durum = {"menu": 0, "fetch": 0, "cikti": []}

    monkeypatch.setattr(sys, "argv", ["turkanime"])
    monkeypatch.setattr(ana, "menu_loop",
                        lambda: durum.__setitem__("menu", durum["menu"] + 1))
    monkeypatch.setattr(ana, "guncel_surum", lambda *a, **k: None)
    monkeypatch.setattr(ana, "update_type", lambda _s: None)
    monkeypatch.setattr(ana, "sleep", lambda *_a: None)
    monkeypatch.setattr(ana, "clear", lambda: None)          # `system('cls')` çağırır
    monkeypatch.setattr(ana, "rprint", lambda *a, **k: durum["cikti"].append(" ".join(map(str, a))))
    monkeypatch.setattr(ana.atexit, "register", lambda *a, **k: None)
    monkeypatch.setattr(ana.gereksinim, "path_hazirla", lambda: None)
    monkeypatch.setattr(ana.gereksinim, "eksik_araclar", lambda *a, **k: [])

    def _fetch(*_a, **_k):
        durum["fetch"] += 1
        return ""
    monkeypatch.setattr(ana, "fetch", _fetch)
    return ana, durum


def _kaynagi_ayarla(kaynak):
    from turkanime_api.cli.dosyalar import Dosyalar
    Dosyalar().set_ayar("kaynak", kaynak)


def test_turkanime_denetimi_baska_kaynakta_kosulmuyor(cli):
    """ESKİ HATA: TürkAnime bağlantı denetimi `menu_loop()` öncesinde KOŞULSUZ
    koşuyordu (:669-675). SOURCE_TITLES yedi kaynak listeliyor ama TürkAnime
    erişilemezse `sys.exit(1)` ile hiçbiri kullanılamıyordu — oysa diğer altı
    kaynağın TürkAnime ile ilgisi yok.
    """
    ana, durum = cli
    _kaynagi_ayarla("animecix")

    ana.main()

    assert durum["fetch"] == 0, "TürkAnime dışı kaynakta bağlantı denetimi koşmamalı"
    assert durum["menu"] == 1


def test_turkanime_denetimi_kendi_kaynaginda_kosuyor(cli):
    """Denetimin koşullu hâle gelmesi onu tamamen devre dışı bırakmamalı:
    kaynak TürkAnime iken oturum yine kuruluyor.
    """
    ana, durum = cli
    _kaynagi_ayarla("turkanime")

    ana.main()

    assert durum["fetch"] == 1
    assert durum["menu"] == 1


def test_turkanime_erisilemezse_menu_yine_de_aciliyor(cli, monkeypatch):
    """ESKİ HATA: bağlantı hatasında `sys.exit(1)` çağrılıyordu. Bu, kullanıcıyı
    "Kaynak seç" menüsünden de mahrum bırakıyordu — yani kaynağını başka bir
    siteye çevirmesinin hiçbir yolu kalmıyordu.
    """
    ana, durum = cli
    _kaynagi_ayarla("turkanime")

    def _patla(*_a, **_k):
        raise ConnectionError("site kapalı")
    monkeypatch.setattr(ana, "fetch", _patla)

    ana.main()                             # SystemExit yükselmemeli

    assert durum["menu"] == 1


def test_turkanime_denetimi_timeout_hatasini_da_yutuyor(cli, monkeypatch):
    """ESKİ HATA: yakalama `(ConnectionError, AssertionError)` ile sınırlıydı;
    requests'in `Timeout`/`SSLError` gibi istisnaları buradan kaçıp en dıştaki
    genel `except`e, yani yine `sys.exit(1)`e düşüyordu.
    """
    import requests

    ana, durum = cli
    _kaynagi_ayarla("turkanime")

    def _patla(*_a, **_k):
        raise requests.exceptions.Timeout("zaman aşımı")
    monkeypatch.setattr(ana, "fetch", _patla)

    ana.main()

    assert durum["menu"] == 1


def test_gereksinim_denetimi_aciliste_kosuyor(cli, monkeypatch):
    """ESKİ HATA: gereksinim denetimi :659-660'ta yorum satırındaydı; gerekçe
    "embed edilmiş araçlar" idi, oysa gömülü kopya mekanizması
    (`common/requirements.py`: `sys._MEIPASS/bin`) yalnızca PyInstaller
    paketinde geçerli. pip ile kurulan CLI'da mpv/yt-dlp/aria2c/ffmpeg hiç
    denetlenmiyordu.
    """
    ana, durum = cli
    _kaynagi_ayarla("animecix")
    monkeypatch.setattr(ana.gereksinim, "eksik_araclar", lambda *a, **k: ["mpv", "aria2c"])

    ana.main()

    uyari = "\n".join(durum["cikti"])
    assert "mpv" in uyari and "aria2c" in uyari


def test_eksik_gereksinim_cli_yi_kapatmiyor(cli, monkeypatch):
    """Denetim geri getirilirken çıkışa bağlanmadı: aria2c/ffmpeg olmadan da
    izlenebiliyor ve CLI bugüne dek hiç denetlemiyordu — eksiklikte çıkmak
    düpedüz gerileme olurdu.
    """
    ana, durum = cli
    _kaynagi_ayarla("animecix")
    monkeypatch.setattr(ana.gereksinim, "eksik_araclar", lambda *a, **k: ["mpv"])

    ana.main()

    assert durum["menu"] == 1


def test_gereksinim_denetimi_ortak_cekirdegi_kullaniyor():
    """ESKİ HATA: `cli/gereksinimler.py` (256 satır) `common/requirements.py` ile
    aynı işi ikinci kez yapıyordu, hiçbir yerden import edilmiyordu ve upstream
    çatalının `gereksinimler.json`'una bakıyordu. Silindi; CLI artık Qt ile
    aynı çekirdeği kullanıyor.
    """
    from turkanime_api.cli import __main__ as ana
    from turkanime_api.common import requirements

    assert ana.gereksinim is requirements
    assert not (KOK / "turkanime_api" / "cli" / "gereksinimler.py").exists()
