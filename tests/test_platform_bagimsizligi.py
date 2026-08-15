"""Windows'a özgü öznitelikler diğer platformlarda kodu düşürmesin.

`OSError.winerror` YALNIZCA Windows'ta var. `adapter.oynat()` onu doğrudan
okuyordu: Linux/macOS'ta gömülü mpv çalıştırılamadığında `except OSError`
bloğunun kendisi `AttributeError` fırlatıyor, asıl hatayı maskeliyor ve
"sistem mpv'sine düş" yedeği hiç çalışmıyordu — yani yedek tam olarak
Windows dışı platformlarda ölü koddu.

Bu sınıf hata geliştirme makinesinde GÖRÜNMEZ: Windows'ta koşan pylint
`winerror`'ı tanır ve şikâyet etmez. CI (Linux) yakaladı.

Testler platformdan bağımsız: `winerror` taşımayan bir `OSError` üretip
kodu o yoldan geçiriyorlar, yani Windows'ta da anlamlılar.
"""
import ast
import errno
import shutil
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parent.parent

# Yalnızca Windows'ta bulunan öznitelikler. Doğrudan erişim taşınabilir değil.
WINDOWS_OZNITELIKLERI = {"winerror"}


def _dogrudan_erisimler(yol: Path):
    """`<isim>.winerror` biçiminde DOĞRUDAN erişimleri bul.

    `getattr(e, "winerror", None)` güvenli ve sayılmıyor — aranan şey
    özniteliğin varlığını varsayan erişim.
    """
    agac = ast.parse(yol.read_text(encoding="utf-8"), filename=str(yol))
    bulunan = []
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Attribute) and dugum.attr in WINDOWS_OZNITELIKLERI:
            bulunan.append((dugum.lineno, dugum.attr))
    return bulunan


def test_kaynak_agacinda_dogrudan_winerror_erisimi_yok():
    """Tüm pakette `x.winerror` doğrudan okunmamalı."""
    suclu = []
    for yol in (KOK / "turkanime_api").rglob("*.py"):
        for satir, ad in _dogrudan_erisimler(yol):
            suclu.append(f"{yol.relative_to(KOK)}:{satir} → .{ad}")
    assert not suclu, (
        "Windows'a özgü öznitelik doğrudan okunuyor; Linux/macOS'ta "
        f"AttributeError olur. `getattr(e, 'winerror', None)` kullan:\n  "
        + "\n  ".join(suclu)
    )


def test_winerrorsuz_oserror_yedege_dusuruyor(monkeypatch, tmp_path):
    """Linux/macOS senaryosu: ENOENT'li OSError yedeği tetiklemeli.

    ESKİ HATA: bu yol `AttributeError: 'OSError' object has no attribute
    'winerror'` ile patlıyordu, yani sistem mpv'si hiç denenmiyordu.
    """
    from turkanime_api.sources import adapter as adapter_mod

    cagrilan = []

    def sahte_popen(cmd, *a, **k):
        cagrilan.append(cmd[0])
        if len(cagrilan) == 1:
            # Gömülü ikili: Linux'ta tipik olarak ENOENT/ENOEXEC.
            raise OSError(errno.ENOENT, "No such file or directory")

        class Surec:
            def wait(self):
                return 0
        return Surec()

    monkeypatch.setattr(adapter_mod.sp, "Popen", sahte_popen)
    monkeypatch.setattr(shutil, "which", lambda ad: "/usr/bin/mpv")

    video = adapter_mod.AdapterVideo.__new__(adapter_mod.AdapterVideo)
    video._url = "https://ornek.test/x.mp4"
    video.referer = None

    # Patlamamalı ve sistem mpv'sine düşmeli.
    video.oynat()
    assert len(cagrilan) == 2, f"yedek denenmedi: {cagrilan}"
    assert cagrilan[1] == "/usr/bin/mpv"


def test_sistem_mpv_de_yoksa_sessizce_donuyor(monkeypatch):
    """Yedek bulunamazsa istisna sızdırmadan None dönmeli."""
    from turkanime_api.sources import adapter as adapter_mod

    def sahte_popen(cmd, *a, **k):
        raise OSError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(adapter_mod.sp, "Popen", sahte_popen)
    monkeypatch.setattr(shutil, "which", lambda ad: None)

    video = adapter_mod.AdapterVideo.__new__(adapter_mod.AdapterVideo)
    video._url = "https://ornek.test/x.mp4"
    video.referer = None

    assert video.oynat() is None


@pytest.mark.parametrize("ikili", ["mpv.exe", "mpv"])
def test_gomulu_mpv_her_platformda_bulunuyor(monkeypatch, tmp_path, ikili):
    """Paketle gelen mpv, uzantısı ne olursa olsun kullanılmalı.

    ESKİ HATA: yol `join(BIN_PATH, "mpv.exe")` olarak SABİTTİ. Yayın Linux
    ve macOS paketi de üretiyor; o platformlarda ikili uzantısız "mpv"
    oluyor, dolayısıyla `exists()` hep False dönüyor ve kutunun içindeki
    mpv hiç kullanılmıyordu. Sistemde mpv kurulu değilse uygulama
    "MPV bulunamadı" diyordu — oysa ikili paketin içindeydi.
    """
    from turkanime_api.sources import adapter as adapter_mod
    from turkanime_api.common import utils as utils_mod

    (tmp_path / ikili).write_text("sahte ikili", encoding="utf-8")
    monkeypatch.setattr(utils_mod, "BIN_PATH", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda ad: None)  # sistemde mpv YOK

    calisan = []

    class Surec:
        def wait(self):
            return 0

    monkeypatch.setattr(adapter_mod.sp, "Popen",
                        lambda cmd, *a, **k: (calisan.append(cmd[0]), Surec())[1])

    video = adapter_mod.AdapterVideo.__new__(adapter_mod.AdapterVideo)
    video._url = "https://ornek.test/x.mp4"
    video.referer = None
    video.oynat()

    assert calisan, "gömülü ikili bulunamadı, hiç çalıştırılmadı"
    assert calisan[0].endswith(ikili), f"yanlış ikili: {calisan[0]}"


@pytest.mark.parametrize("hata", [
    OSError(errno.EACCES, "Permission denied"),
    OSError(errno.EIO, "Input/output error"),
])
def test_ilgisiz_oserror_yedege_dusurmuyor(monkeypatch, hata):
    """Her OSError "uyumsuz ikili" değil; yalnızca ilgili olanlar yedeğe düşer.

    Aksi hâlde izin hatası sessizce sistem mpv'siyle tekrar denenir ve
    kullanıcı sebebi hiç öğrenemez.
    """
    from turkanime_api.sources import adapter as adapter_mod

    cagrilan = []

    def sahte_popen(cmd, *a, **k):
        cagrilan.append(cmd[0])
        raise hata

    monkeypatch.setattr(adapter_mod.sp, "Popen", sahte_popen)
    monkeypatch.setattr(shutil, "which", lambda ad: "/usr/bin/mpv")

    video = adapter_mod.AdapterVideo.__new__(adapter_mod.AdapterVideo)
    video._url = "https://ornek.test/x.mp4"
    video.referer = None

    # Davranış ÖLÇÜLDÜ (varsayılmadı): fonksiyon istisna yükseltmiyor, hatayı
    # basıp None dönüyor — oynatma yolu için makul, çağıran None'a bakıyor.
    # Burada sınanan şey yedeğin GEREKSİZ YERE denenmemesi: izin hatasında
    # sistem mpv'siyle sessizce tekrar denemek, kullanıcıya sebebi hiç
    # göstermeden aynı duvara ikinci kez çarpmak olurdu.
    assert video.oynat() is None
    assert len(cagrilan) == 1, f"ilgisiz hata için yedek denendi: {cagrilan}"
