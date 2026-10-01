"""Yerel FlareSolverr: kurulum, süreç yaşam döngüsü ve CF zincirinde adres seçimi.

Ağa çıkılmıyor. Gerçek FlareSolverr (~260-380 MB) yerine:

* kurulum testlerinde küçük sahte arşivler yerel HTTP sunucusundan iniyor ve
  `VARLIKLAR` onların GERÇEK boyut + SHA-256'sıyla değiştiriliyor — yani
  doğrulama kodu sahte değil, yalnızca beklenen değerler;
* süreç testlerinde `SAHTE` adlı Python betiği FlareSolverr gibi davranıyor:
  HOST/PORT'u ortamdan okuyor, `GET /` → "FlareSolverr is ready!", `POST /v1`
  → çözüm. İstenirse gerçek FlareSolverr'ın yaptığını yapıp Chrome yerine bir
  "torun"u setsid + çift fork ile KOPARARAK başlatıyor — yetim sürecin asıl
  kaynağı buydu.

Süreç testleri Linux'a özgü (bekçi kabuğu, PR_SET_PDEATHSIG); Windows'ta iş
nesnesi yolu CI'da koşamıyor.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import textwrap
import threading
import time
import zipfile
from pathlib import Path

import pytest
import requests

from turkanime_api.common import cf_bypass as cf
from turkanime_api.common import flaresolverr as fs
from turkanime_api.common import requirements as req

# conftest `kurulumu_bul`/`onerilen_eksikler`i susturuyor; burada gerçekleri de lazım.
_GERCEK_KURULUMU_BUL = fs.kurulumu_bul
_GERCEK_ONERILEN = req.onerilen_eksikler
DEPO_KOKU = Path(__file__).resolve().parent.parent

linux = pytest.mark.skipif(not sys.platform.startswith("linux"),
                           reason="bekçi kabuğu ve PR_SET_PDEATHSIG Linux'a özgü")


# ── Yardımcılar ──────────────────────────────────────────────────────────────
def _arsiv(icerik: dict, bicim: str = "tar.gz") -> bytes:
    """``{"flaresolverr/flaresolverr": b"...", ...}`` → arşiv baytları."""
    tampon = io.BytesIO()
    if bicim == "zip":
        with zipfile.ZipFile(tampon, "w") as zf:
            for ad, veri in icerik.items():
                zf.writestr(ad, veri)
    else:
        with tarfile.open(fileobj=tampon, mode="w:gz") as tf:
            for ad, veri in icerik.items():
                bilgi = tarfile.TarInfo(ad)
                bilgi.size = len(veri)
                bilgi.mode = 0o644        # kip bilerek düşük: çalıştırılabilir yapılmalı
                tf.addfile(bilgi, io.BytesIO(veri))
    return tampon.getvalue()


PAKET_JSON = json.dumps({"name": "flaresolverr", "version": fs.SURUM}).encode()


def _gercek_gibi(bicim: str = "tar.gz") -> bytes:
    exe = "flaresolverr/flaresolverr" + (".exe" if bicim == "zip" else "")
    return _arsiv({
        exe: b"ikili",
        "flaresolverr/_internal/package.json": PAKET_JSON,
        "flaresolverr/_internal/chrome/chrome": b"chrome",
        "flaresolverr/_internal/chrome/interactive_ui_tests.exe": b"x" * 4096,
    }, bicim)


@pytest.fixture
def sahte_varlik(monkeypatch):
    """`VARLIKLAR[platform]`ı verilen baytların gerçek boyut/özetine çevir."""
    def _kur(veri: bytes, anahtar: str = "linux_x64", ozet: str = "") -> fs.Varlik:
        ad = "flaresolverr_windows_x64.zip" if anahtar == "windows_x64" else \
            "flaresolverr_linux_x64.tar.gz"
        varlik = fs.Varlik(anahtar, ad, len(veri),
                           ozet or hashlib.sha256(veri).hexdigest(), len(veri) * 4)
        monkeypatch.setitem(fs.VARLIKLAR, anahtar, varlik)
        return varlik
    return _kur


def _sunucu(local_server, veri: bytes, ad: str) -> str:
    return local_server(body=veri) + ad


# ── Platform ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sistem,makine,beklenen", [
    ("Windows", "AMD64", "windows_x64"),
    ("Linux", "x86_64", "linux_x64"),
    ("Darwin", "arm64", None),
    ("Darwin", "x86_64", None),
    ("Linux", "aarch64", None),
    ("Windows", "ARM64", None),
    ("FreeBSD", "amd64", None),
])
def test_platform_anahtari(sistem, makine, beklenen):
    """ARM'a x64 ikili kurmak yok: `utils.get_arch` gibi x64'e düşülmüyor."""
    assert fs.platform_anahtari(sistem, makine) == beklenen


def test_desteklenmeyen_platform_nedenini_ve_yerine_kullanilani_anlatiyor():
    mac = fs.desteklenmeme_sebebi("Darwin", "arm64")
    assert "macOS" in mac and "QtWebEngine" in mac
    arm = fs.desteklenmeme_sebebi("Linux", "aarch64")
    assert "aarch64" in arm and "x64" in arm and "QtWebEngine" in arm
    assert fs.desteklenmeme_sebebi("Linux", "x86_64") == ""


def test_desteklenmeyen_platformda_kurulum_reddediliyor(monkeypatch, tmp_path):
    monkeypatch.setattr(fs, "platform_anahtari", lambda *a, **k: None)
    with pytest.raises(fs.KurulumHatasi):
        fs.kur(hedef=tmp_path / "flaresolverr")
    assert not (tmp_path / "flaresolverr").exists()
    assert fs.kurulum_oner() is False
    assert _GERCEK_ONERILEN() == [], "desteklenmeyen platformda öneri yapılmamalı"
    y = fs.Yonetici()
    ozet = y.durum_ozeti()
    assert ozet["durum"] == "desteklenmiyor" and ozet["kurulabilir"] is False
    assert y.baslat(bekle=False) == ""
    assert y.durum_ozeti()["durum"] == "desteklenmiyor"


def test_linuxta_xvfb_yoksa_eksik_bagimlilik(monkeypatch):
    """Paket Xvfb taşımıyor; yoksa FlareSolverr 'Could not find Xvfb' ile ölüyor."""
    monkeypatch.setattr(fs.shutil, "which", lambda ad: None)
    assert fs.eksik_bagimliliklar("linux_x64") == ["Xvfb"]
    assert fs.eksik_bagimliliklar("windows_x64") == []
    monkeypatch.setattr(fs.shutil, "which", lambda ad: "/usr/bin/" + ad)
    assert fs.eksik_bagimliliklar("linux_x64") == []


def test_sihirbaz_kurulu_degilken_oneriyor(monkeypatch):
    monkeypatch.setattr(fs, "platform_anahtari", lambda *a, **k: "linux_x64")
    monkeypatch.setattr(fs, "kurulumu_bul", lambda *a, **k: None)
    assert _GERCEK_ONERILEN() == ["flaresolverr"]
    bilgi = fs.sihirbaz_bilgisi(onerildi=True)
    assert bilgi["goster"] and bilgi["kurulabilir"] and bilgi["sec"]
    assert bilgi["boyut_mb"] == round(fs.VARLIKLAR["linux_x64"].boyut / 1e6)
    assert "SHA-256" in bilgi["metin"]


# ── Kurulum ─────────────────────────────────────────────────────────────────
def test_sabit_varliklar_resmi_adreste():
    """Sürüm sabit, adres resmî depo, özet 64 hex — "latest" yok."""
    for anahtar, varlik in fs.VARLIKLAR.items():
        assert varlik.url.startswith(
            "https://github.com/FlareSolverr/FlareSolverr/releases/download/v")
        assert f"/v{fs.SURUM}/" in varlik.url and "latest" not in varlik.url
        assert len(varlik.sha256) == 64 and int(varlik.sha256, 16) >= 0
        assert varlik.boyut > 100_000_000 and varlik.acik_boyut > 100_000_000
        assert varlik.platform == anahtar


@pytest.mark.parametrize("bicim,anahtar", [("tar.gz", "linux_x64"), ("zip", "windows_x64")])
def test_indir_dogrula_ac_buda(tmp_path, local_server, sahte_varlik, bicim, anahtar):
    veri = _gercek_gibi(bicim)
    varlik = sahte_varlik(veri, anahtar)
    hedef = tmp_path / "kok" / "flaresolverr"
    ilerleme = []
    kurulum = fs.kur(anahtar, hedef=hedef, url=_sunucu(local_server, veri, varlik.ad),
                     ilerleme=lambda i, t: ilerleme.append((i, t)))

    exe = hedef / fs.exe_adi(anahtar)
    assert kurulum.exe == exe and exe.read_bytes() == b"ikili"
    assert kurulum.surum == fs.SURUM and kurulum.gomulu is False
    assert (hedef / "_internal" / "chrome" / "chrome").is_file()
    assert not (hedef / "_internal" / "chrome" / "interactive_ui_tests.exe").exists(), \
        "Chromium test ikilisi budanmadı"
    assert ilerleme and ilerleme[-1] == (len(veri), len(veri))
    if anahtar == "linux_x64" and os.name != "nt":
        assert exe.stat().st_mode & stat.S_IXUSR, "çalıştırılabilir yapılmadı"
        assert (hedef / "_internal/chrome/chrome").stat().st_mode & stat.S_IXUSR
    # Geçici kurulum klasörü kalmamalı.
    assert [p.name for p in hedef.parent.iterdir()] == ["flaresolverr"]


def test_ozet_tutmazsa_hicbir_sey_yerlesmiyor(tmp_path, local_server, sahte_varlik):
    veri = _gercek_gibi()
    varlik = sahte_varlik(veri, ozet="0" * 64)
    hedef = tmp_path / "kok" / "flaresolverr"
    with pytest.raises(fs.KurulumHatasi, match="SHA-256"):
        fs.kur("linux_x64", hedef=hedef, url=_sunucu(local_server, veri, varlik.ad))
    assert not hedef.exists()
    assert list(hedef.parent.iterdir()) == [], "geçici dosya kaldı"


def test_ozet_tutmazsa_eski_kurulum_bozulmuyor(tmp_path, local_server, sahte_varlik):
    hedef = tmp_path / "flaresolverr"
    hedef.mkdir()
    (hedef / "flaresolverr").write_bytes(b"eski")
    veri = _gercek_gibi()
    varlik = sahte_varlik(veri, ozet="f" * 64)
    with pytest.raises(fs.KurulumHatasi):
        fs.kur("linux_x64", hedef=hedef, url=_sunucu(local_server, veri, varlik.ad))
    assert (hedef / "flaresolverr").read_bytes() == b"eski"


def test_boyut_tutmazsa_indirme_hemen_kesiliyor(tmp_path, local_server, sahte_varlik):
    """Sunucu başka boyut beyan ediyorsa (yanlış dosya) indirilmiyor bile."""
    veri = _gercek_gibi()
    varlik = sahte_varlik(veri)
    fazla = veri + b"ek"
    with pytest.raises(fs.KurulumHatasi, match="boyut"):
        fs.kur("linux_x64", hedef=tmp_path / "flaresolverr",
               url=_sunucu(local_server, fazla, varlik.ad))


def test_yeni_surum_eskisinin_yerine_atomik_geciyor(tmp_path, local_server, sahte_varlik):
    hedef = tmp_path / "flaresolverr"
    hedef.mkdir()
    (hedef / "eski-surumden-kalan").write_text("x")
    veri = _gercek_gibi()
    varlik = sahte_varlik(veri)
    fs.kur("linux_x64", hedef=hedef, url=_sunucu(local_server, veri, varlik.ad))
    assert (hedef / "flaresolverr").read_bytes() == b"ikili"
    assert not (hedef / "eski-surumden-kalan").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["flaresolverr"]


def test_iptal_edilen_indirme_yarim_dosya_birakmiyor(tmp_path, local_server, sahte_varlik):
    veri = _gercek_gibi()
    varlik = sahte_varlik(veri)
    iptal = threading.Event()
    iptal.set()
    with pytest.raises(fs.IptalEdildi):
        fs.kur("linux_x64", hedef=tmp_path / "k" / "flaresolverr", iptal=iptal,
               url=_sunucu(local_server, veri, varlik.ad))
    assert list((tmp_path / "k").iterdir()) == []


@pytest.mark.parametrize("kotu", ["../disari", "/mutlak/yol", "flaresolverr/../../disari"])
def test_arsivden_disari_yazilamiyor(tmp_path, sahte_varlik, kotu):
    """Özet tutsa bile çıkarma kodu arşive güvenmemeli (zip-slip)."""
    veri = _arsiv({"flaresolverr/flaresolverr": b"ikili", kotu: b"kotu"})
    sahte_varlik(veri)
    arsiv = tmp_path / "a.tar.gz"
    arsiv.write_bytes(veri)
    with pytest.raises(fs.KurulumHatasi, match="güvensiz"):
        fs.kur("linux_x64", hedef=tmp_path / "k" / "flaresolverr", arsiv=arsiv)
    assert not (tmp_path / "disari").exists()
    assert not (tmp_path / "k" / "flaresolverr").exists()


def test_paket_bicimi_degismisse_reddediliyor(tmp_path, sahte_varlik):
    veri = _arsiv({"baska-klasor/flaresolverr": b"ikili"})
    sahte_varlik(veri)
    arsiv = tmp_path / "a.tar.gz"
    arsiv.write_bytes(veri)
    with pytest.raises(fs.KurulumHatasi, match="biçimi"):
        fs.kur("linux_x64", hedef=tmp_path / "flaresolverr", arsiv=arsiv)


def test_disk_alani_yetmezse_indirmeye_baslamiyor(tmp_path, monkeypatch, sahte_varlik):
    sahte_varlik(_gercek_gibi())
    monkeypatch.setattr(fs.shutil, "disk_usage",
                        lambda _y: type("D", (), {"free": 1024})())
    monkeypatch.setattr(fs.requests, "get",
                        lambda *a, **k: pytest.fail("yer yokken indirme başladı"))
    with pytest.raises(fs.KurulumHatasi, match="disk alanı"):
        fs.kur("linux_x64", hedef=tmp_path / "flaresolverr")


def test_komut_satiri_kur_ve_hata_kodu(tmp_path, sahte_varlik, capsys):
    """Yayın hattının çağırdığı yol: başarıda 0, doğrulama düşerse 1 + ::error::."""
    veri = _gercek_gibi("zip")
    sahte_varlik(veri, "windows_x64")
    arsiv = tmp_path / "flaresolverr_windows_x64.zip"
    arsiv.write_bytes(veri)
    hedef = tmp_path / "dist" / "paket" / "flaresolverr"
    assert fs.main(["kur", "--platform", "windows_x64", "--hedef", str(hedef),
                    "--arsiv", str(arsiv)]) == 0
    assert (hedef / "flaresolverr.exe").is_file()
    assert "doğrulandı" in capsys.readouterr().out

    arsiv.write_bytes(veri + b"!")          # tek bayt fark
    assert fs.main(["kur", "--platform", "windows_x64", "--hedef",
                    str(tmp_path / "baska"), "--arsiv", str(arsiv)]) == 1
    assert "::error::" in capsys.readouterr().out


def test_komut_satiri_windows_borusunda_dusmuyor(tmp_path, sahte_varlik, monkeypatch):
    """ESKİ HATA (v10.3.1 Windows derlemesi): GitHub Actions'ta Python çıktıyı
    boruya cp1252 ile yazıyor; "←" ve ş/ı/ğ `UnicodeEncodeError` ile süreci
    düşürüyor, FlareSolverr paketlenmiyordu. Çıktı UTF-8'e çevrilmeli."""
    import sys
    veri = _gercek_gibi("zip")
    sahte_varlik(veri, "windows_x64")
    arsiv = tmp_path / "flaresolverr_windows_x64.zip"
    arsiv.write_bytes(veri)
    ham = io.BytesIO()
    boru = io.TextIOWrapper(ham, encoding="cp1252")     # Windows runner'ındaki gibi
    monkeypatch.setattr(sys, "stdout", boru)
    assert fs.main(["kur", "--platform", "windows_x64", "--hedef",
                    str(tmp_path / "flaresolverr"), "--arsiv", str(arsiv)]) == 0
    sys.stdout.flush()
    metin = ham.getvalue().decode("utf-8")
    assert "←" in metin and "doğrulandı" in metin


# ── Kopyanın bulunması ──────────────────────────────────────────────────────
def test_paketle_gelen_kopya_exe_yaninda_bulunuyor(tmp_path, monkeypatch):
    """CI onu `<zip>/flaresolverr/`e koyuyor; veri kökündekinden önce gelir."""
    monkeypatch.setattr(fs, "kurulumu_bul", _GERCEK_KURULUMU_BUL)
    monkeypatch.setattr(fs, "platform_anahtari", lambda *a, **k: "linux_x64")
    uygulama = tmp_path / "uygulama"
    (uygulama / "flaresolverr" / "_internal").mkdir(parents=True)
    (uygulama / "flaresolverr" / "flaresolverr").write_bytes(b"ikili")
    (uygulama / "flaresolverr" / "_internal" / "package.json").write_bytes(PAKET_JSON)
    veri = tmp_path / "veri"
    (veri / "flaresolverr").mkdir(parents=True)
    (veri / "flaresolverr" / "flaresolverr").write_bytes(b"kurulan")
    monkeypatch.setenv("TURKANIME_VERI_DIZINI", str(veri))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(uygulama / "turkanime-gui"))

    kurulum = fs.kurulumu_bul()
    assert kurulum.dizin == uygulama / "flaresolverr" and kurulum.gomulu is True
    assert kurulum.surum == fs.SURUM
    ozet = fs.Yonetici().durum_ozeti()
    assert ozet["gomulu"] and ozet["kurulabilir"] is False, \
        "uygulamayla gelen kopya 'Kur' önermemeli"

    (uygulama / "flaresolverr" / "flaresolverr").unlink()
    kurulum = fs.kurulumu_bul()
    assert kurulum.dizin == veri / "flaresolverr" and kurulum.gomulu is False


def test_cocuk_ortami_donmus_uygulamanin_izlerini_tasimiyor(monkeypatch):
    """Onefile açılışı LD_LIBRARY_PATH'i kendi geçici dizinine çeviriyor."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    taban = {"LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": "/usr/lib/ozgun",
             "_PYI_ARCHIVE_FILE": "/app/turkanime-gui", "_MEIPASS2": "/tmp/_MEI123",
             "PYTHONPATH": "/x", "LOG_LEVEL": "verbose", "LANG": "tr_TR.UTF-8",
             "HOST": "0.0.0.0", "PATH": "/usr/bin"}
    ortam = fs.cocuk_ortami(9123, taban)
    assert ortam["LD_LIBRARY_PATH"] == "/usr/lib/ozgun"
    assert not any(k.startswith("_PYI_") for k in ortam)
    assert "_MEIPASS2" not in ortam and "PYTHONPATH" not in ortam
    assert ortam["HOST"] == "127.0.0.1", "0.0.0.0 yerel ağa açmak demek"
    assert ortam["PORT"] == "9123" and ortam["LOG_LEVEL"] == "info"
    assert ortam["LANG"] == "tr-TR", "Chrome'a bozuk Accept-Language gider"
    assert ortam["PATH"] == "/usr/bin" and ortam["HEADLESS"] == "true"

    monkeypatch.setattr(sys, "frozen", False)
    assert fs.cocuk_ortami(1, {"LD_LIBRARY_PATH": "/opt/x", "LANG": "C"})["LD_LIBRARY_PATH"] \
        == "/opt/x"
    assert "LANG" not in fs.cocuk_ortami(1, {"LANG": "C.UTF-8"})


def test_windows_is_nesnesi_yapisi_winnt_ile_ayni():
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION x64'te 144 bayt; yanlış yapı
    SetInformationJobObject'i sessizce düşürür, çöküşte Chrome yetim kalırdı."""
    import ctypes
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("yalnızca 64 bit")
    assert ctypes.sizeof(fs._JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144
    assert fs._JOBOBJECT_EXTENDED_LIMIT_INFORMATION.BasicLimitInformation.offset == 0
    assert fs._JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags.offset == 16


# ── Süreç yaşam döngüsü (sahte FlareSolverr) ────────────────────────────────
SAHTE = textwrap.dedent(r'''
    #!{python}
    import json, os, subprocess, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    kip = os.environ.get("SAHTE_KIP", "normal")
    print("sahte FlareSolverr", os.environ.get("HOST"), os.environ.get("PORT"), flush=True)
    if kip == "cik":
        print("chrome: error while loading shared libraries: libwayland-server.so.0: "
              "cannot open shared object file", flush=True)
        sys.exit(3)
    if kip == "asili":
        time.sleep(3600)
    torun_dosyasi = os.environ.get("SAHTE_TORUN_DOSYASI")
    if torun_dosyasi:
        # undetected-chromedriver'ın `start_detached`'ı gibi: ara süreç setsid'li
        # "Chrome"u başlatıp çıkıyor; torun kimsenin süreç grubunda değil.
        pid = os.fork()
        if pid == 0:
            p = subprocess.Popen(["sleep", "3600"], start_new_session=True)
            with open(torun_dosyasi, "w") as fp:
                fp.write(str(p.pid))
            os._exit(0)
        os.waitpid(pid, 0)

    class H(BaseHTTPRequestHandler):
        def _json(self, veri):
            govde = json.dumps(veri).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(govde)))
            self.end_headers()
            self.wfile.write(govde)

        def do_GET(self):
            self._json({"msg": "FlareSolverr is ready!", "version": "3.5.2",
                        "userAgent": "Sahte/1.0"})

        def do_POST(self):
            istek = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._json({"status": "ok", "message": "Challenge solved!", "solution": {
                "url": istek["url"], "status": 200, "userAgent": "Sahte/1.0",
                "cookies": [{"name": "cf_clearance", "value": "SAHTE-CEREZ"}],
                "response": "<html><body>cozuldu " + istek["url"] + "</body></html>"}})

        def log_message(self, *a):
            pass

    HTTPServer((os.environ["HOST"], int(os.environ["PORT"])), H).serve_forever()
''').lstrip()


@pytest.fixture
def sahte_kurulum(tmp_path, monkeypatch):
    """Veri kökünde "kurulu" sahte FlareSolverr; yönetici onu bulur."""
    dizin = tmp_path / "flaresolverr"
    (dizin / "_internal").mkdir(parents=True)
    exe = dizin / "flaresolverr"
    exe.write_text(SAHTE.replace("{python}", sys.executable), encoding="utf-8")
    exe.chmod(0o755)
    (dizin / "_internal" / "package.json").write_bytes(PAKET_JSON)
    kurulum = fs.Kurulum(dizin, exe, fs.SURUM, False)
    monkeypatch.setattr(fs, "platform_anahtari", lambda *a, **k: "linux_x64")
    monkeypatch.setattr(fs, "kurulumu_bul", lambda *a, **k: kurulum)
    monkeypatch.setattr(fs, "eksik_bagimliliklar", lambda *a, **k: [])
    return kurulum


@pytest.fixture
def yonetici_kur():
    """Test başına yönetici(ler); test bitince ne açıldıysa kapanır."""
    olusan = []

    def _kur(**kw):
        kw.setdefault("tercih_port", 0)
        kw.setdefault("baslangic_zaman_asimi", 20)
        y = fs.Yonetici(**kw)
        olusan.append(y)
        return y

    yield _kur
    for y in olusan:
        y.durdur()


def _yasiyor(pid: int) -> bool:
    return fs._yasiyor_mu(pid)


def _bekle(kosul, sure: float = 8.0) -> bool:
    son = time.monotonic() + sure
    while time.monotonic() < son:
        if kosul():
            return True
        time.sleep(0.05)
    return kosul()


@linux
def test_baslat_saglik_istek_durdur(sahte_kurulum, yonetici_kur):
    y = yonetici_kur()
    olaylar = []
    y.dinle(olaylar.append)
    adres = y.baslat(bekle=True)
    assert adres.startswith("http://127.0.0.1:"), y.durum_ozeti()
    assert y.durum_ozeti()["durum"] == "calisiyor"
    assert fs.saglik(adres) == "3.5.2"
    yanit = requests.post(adres + "/v1", json={"cmd": "request.get", "url": "https://a.test/"},
                          proxies=fs.VEKILSIZ, timeout=5).json()
    assert yanit["solution"]["status"] == 200
    assert "sahte FlareSolverr 127.0.0.1" in fs.gunluk_yolu().read_text(), \
        "çıktı veri kökündeki günlüğe gitmiyor"
    assert {o["durum"] for o in olaylar} >= {"basliyor", "calisiyor"}

    bekci = y._surec.pid
    agac = fs._linux_agac(bekci)
    assert agac, "bekçinin altında FlareSolverr yok"
    assert y.durdur() is True
    assert not _yasiyor(bekci) and not any(_yasiyor(p) for p in agac)
    assert y.adres() == "" and y.durum_ozeti()["durum"] == "durdu"


@linux
def test_varsayilan_port_boşsa_o_doluysa_baskasi(sahte_kurulum, yonetici_kur):
    """8191 başka bir programdaysa oraya bağlanmaya çalışmak iki süreci de bozar."""
    tutucu = socket.socket()
    tutucu.bind(("127.0.0.1", 0))
    tutucu.listen(1)
    dolu = tutucu.getsockname()[1]
    try:
        assert fs.bos_port(dolu) != dolu
        y = yonetici_kur(tercih_port=dolu)
        adres = y.baslat(bekle=True)
        assert adres and not adres.endswith(f":{dolu}")
    finally:
        tutucu.close()
    bos = fs.bos_port(0)
    assert fs.bos_port(bos) == bos, "tercih edilen port boşken o kullanılmalı"


@linux
def test_acilamayan_surec_sebebiyle_hata_veriyor(sahte_kurulum, yonetici_kur, monkeypatch):
    monkeypatch.setenv("SAHTE_KIP", "cik")
    y = yonetici_kur()
    assert y.baslat(bekle=True) == ""
    ozet = y.durum_ozeti()
    assert ozet["durum"] == "hata"
    assert "libwayland-server.so.0" in ozet["hata"], ozet["hata"]
    assert y._surec is None
    # Hemen ardından tembel başlatma yeniden denemez (her deneme Chrome açıyor).
    assert y.kendiliginden_baslayabilir() is False
    assert y.hazir_adres(bekle=0) == ""
    # Elle "Başlat" bekleme süresini tanımaz.
    monkeypatch.setenv("SAHTE_KIP", "normal")
    assert y.baslat(bekle=True, elle=True).startswith("http://")


@linux
def test_hazir_olmayan_surec_zaman_asiminda_olduruluyor(sahte_kurulum, yonetici_kur,
                                                        monkeypatch):
    monkeypatch.setenv("SAHTE_KIP", "asili")
    y = yonetici_kur(baslangic_zaman_asimi=1.5)
    bas = time.monotonic()
    assert y.baslat(bekle=True) == ""
    assert time.monotonic() - bas < 15
    assert "hazır olmadı" in y.durum_ozeti()["hata"]
    assert y._surec is None


@linux
def test_durdurunca_koparilmis_torun_da_oluyor(sahte_kurulum, yonetici_kur, monkeypatch,
                                               tmp_path):
    """Gerçek FlareSolverr Chrome'u setsid + çift fork ile başlatıyor; süreç
    grubunu öldürmek ona ULAŞMIYOR (ölçüldü). Bekçi alt-biçer olduğu için
    torun ona bağlanıyor ve durdurmada o da gidiyor."""
    torun_dosyasi = tmp_path / "torun.pid"
    monkeypatch.setenv("SAHTE_TORUN_DOSYASI", str(torun_dosyasi))
    y = yonetici_kur()
    assert y.baslat(bekle=True)
    assert _bekle(torun_dosyasi.exists)
    torun = int(torun_dosyasi.read_text())
    assert _yasiyor(torun)
    assert torun in fs._linux_agac(y._surec.pid), "torun bekçiye bağlanmadı"
    assert os.getsid(torun) != os.getsid(y._surec.pid), "torun ayrı oturumda olmalı"
    y.durdur()
    assert _bekle(lambda: not _yasiyor(torun)), "torun yetim kaldı"


@linux
def test_beklenmedik_kapanis_fark_ediliyor(sahte_kurulum, yonetici_kur):
    y = yonetici_kur()
    olaylar = []
    y.dinle(olaylar.append)
    assert y.baslat(bekle=True)
    for pid in fs._linux_agac(y._surec.pid):
        os.kill(pid, signal.SIGKILL)            # FlareSolverr kendiliğinden öldü
    assert _bekle(lambda: y.durum_ozeti()["durum"] == "hata")
    assert "beklenmedik" in y.durum_ozeti()["hata"]
    assert y.adres() == ""
    assert olaylar[-1]["durum"] == "hata"


@linux
def test_elle_durdurulan_tembel_baslatilmiyor(sahte_kurulum, yonetici_kur):
    y = yonetici_kur()
    assert y.baslat(bekle=True)
    y.durdur(elle=True)
    assert y.hazir_adres(bekle=5) == "", "kullanıcının 'Durdur'u yok sayıldı"
    assert y._surec is None
    assert "Elle durduruldu" in y.durum_ozeti()["metin"]
    assert y.baslat(bekle=True, elle=True)
    assert y.hazir_adres(bekle=0)


@linux
def test_paralel_ihtiyac_tek_surec_aciyor(sahte_kurulum, yonetici_kur, monkeypatch):
    y = yonetici_kur()
    acilan = []
    asil = fs.Yonetici._surec_ac

    def say(self, *a, **k):
        acilan.append(1)
        return asil(self, *a, **k)

    monkeypatch.setattr(fs.Yonetici, "_surec_ac", say)
    with concurrent.futures.ThreadPoolExecutor(6) as havuz:
        adresler = list(havuz.map(lambda _i: y.hazir_adres(bekle=15), range(6)))
    assert len(set(adresler)) == 1 and adresler[0].startswith("http://")
    assert len(acilan) == 1


@linux
def test_arayuz_threadinde_beklenmiyor(sahte_kurulum, yonetici_kur, monkeypatch):
    """Arayüz thread'i açılışı (saniyeler) beklerse pencere donar."""
    monkeypatch.setattr(fs, "_arayuz_threadi_mi", lambda: True)
    y = yonetici_kur()
    bas = time.monotonic()
    assert y.hazir_adres(bekle=30) == ""
    assert time.monotonic() - bas < 1.0
    assert y.basliyor() or y.adres(), "açılış arka planda başlamadı"
    assert _bekle(lambda: bool(y.adres()), 15)


@linux
def test_ebeveyn_cokunce_hicbir_sey_kalmiyor(sahte_kurulum, tmp_path):
    """Uygulama SIGKILL ile ölse de (çöküş, görev yöneticisi) FlareSolverr ve
    koparılmış torunu ölmeli. Başlatma kısa ömürlü bir işçi thread'inden:
    PR_SET_PDEATHSIG thread'e bağlı ve QThreadPool boşta kalan thread'i 30
    sn'de emekliye ayırıyor — ömürlük başlatıcı thread olmasa süreç o an
    ölürdü."""
    torun_dosyasi = tmp_path / "torun.pid"
    betik = tmp_path / "ebeveyn.py"
    betik.write_text(textwrap.dedent(f'''
        import json, sys, threading, time
        from pathlib import Path
        sys.path.insert(0, {str(DEPO_KOKU)!r})
        from turkanime_api.common import flaresolverr as fs
        d = Path({str(sahte_kurulum.dizin)!r})
        fs.kurulumu_bul = lambda *a, **k: fs.Kurulum(d, d / "flaresolverr", "3.5.2", False)
        fs.eksik_bagimliliklar = lambda *a, **k: []
        fs.platform_anahtari = lambda *a, **k: "linux_x64"
        y = fs.Yonetici(tercih_port=0, baslangic_zaman_asimi=20)
        sonuc = {{}}
        isci = threading.Thread(target=lambda: sonuc.update(adres=y.baslat(bekle=True)))
        isci.start(); isci.join()                       # işçi thread'i bitti
        time.sleep(1.0)
        print(json.dumps({{"adres": sonuc["adres"], "bekci": y._surec.pid,
                          "canli": y._surec.poll() is None}}), flush=True)
        time.sleep(3600)
    '''), encoding="utf-8")
    ortam = dict(os.environ, TURKANIME_VERI_DIZINI=str(tmp_path / "veri"),
                 SAHTE_TORUN_DOSYASI=str(torun_dosyasi))
    ebeveyn = subprocess.Popen([sys.executable, str(betik)], stdout=subprocess.PIPE,
                               text=True, env=ortam)
    try:
        bilgi = json.loads(ebeveyn.stdout.readline())
        assert bilgi["adres"] and bilgi["canli"], \
            "işçi thread'i bitince FlareSolverr da öldü (PDEATHSIG yanlış thread'e bağlı)"
        bekci = bilgi["bekci"]
        assert _bekle(torun_dosyasi.exists)
        torun = int(torun_dosyasi.read_text())
        agac = fs._linux_agac(bekci)
        assert torun in agac
        ebeveyn.kill()                                   # çöküş
        ebeveyn.wait(5)
        assert _bekle(lambda: not _yasiyor(bekci)), "bekçi yaşıyor"
        assert _bekle(lambda: not any(_yasiyor(p) for p in agac)), \
            f"yetim kaldı: {[p for p in agac if _yasiyor(p)]}"
    finally:
        if ebeveyn.poll() is None:
            ebeveyn.kill()


# ── CF zinciri: hangi adres? ────────────────────────────────────────────────
class SahteYonetici:
    """CF zincirinin gördüğü yüzey; çağrıları sayar."""

    def __init__(self, kullanilabilir=True, adres="http://127.0.0.1:9", basliyor=False):
        self._k, self._adres, self._b = kullanilabilir, adres, basliyor
        self.cagri = 0

    def kullanilabilir(self):
        return self._k

    def hazir_adres(self, bekle=fs.TEMBEL_BEKLEME):
        self.cagri += 1
        return self._adres

    def basliyor(self):
        return self._b

    def istek_siniri(self):
        return threading.BoundedSemaphore(1)


@pytest.fixture
def sahte_yonetici(monkeypatch):
    def _kur(**kw):
        y = SahteYonetici(**kw)
        monkeypatch.setattr(fs, "yonetici", lambda: y)
        return y
    return _kur


UZAK = cf.CFSession.DEFAULT_FLARESOLVERR_URL


def test_acik_arguman_yereli_hic_sormuyor(sahte_yonetici, izole_ayarla):
    y = sahte_yonetici()
    ses = cf.CFSession(flaresolverr_url="http://baska:8191")
    assert ses._flaresolverr_adresi() == ("http://baska:8191", False)
    assert cf.CFSession(flaresolverr_url="")._flaresolverr_adresi() == ("", False)
    assert y.cagri == 0


def test_ayardaki_ozel_adres_yerelden_once(sahte_yonetici, izole_ayarla):
    """Kendi sunucusunu yazan kullanıcının seçimi sessizce ezilmemeli."""
    y = sahte_yonetici()
    izole_ayarla(flaresolverr_url="http://nas.ev:8191")
    assert cf.CFSession()._flaresolverr_adresi() == ("http://nas.ev:8191", False)
    assert y.cagri == 0


@pytest.mark.parametrize("ayar", [UZAK, UZAK + "/", None])
def test_varsayilan_uzak_yerine_yerel(sahte_yonetici, izole_ayarla, ayar):
    """Varsayılan (projenin sunucusu) ya da hiç ayar yok → yerel örnek."""
    y = sahte_yonetici(adres="http://127.0.0.1:8191")
    if ayar is None:
        from turkanime_api.cli.dosyalar import Dosyalar
        Dosyalar().ayar_sil("flaresolverr_url")
    else:
        izole_ayarla(flaresolverr_url=ayar)
    assert cf.CFSession()._flaresolverr_adresi() == ("http://127.0.0.1:8191", True)
    assert y.cagri == 1


def test_yerel_yoksa_eski_davranis(sahte_yonetici, izole_ayarla):
    sahte_yonetici(kullanilabilir=False)
    izole_ayarla(flaresolverr_url=UZAK)
    assert cf.CFSession()._flaresolverr_adresi() == (UZAK, False)
    izole_ayarla(flaresolverr_url="")
    assert cf.CFSession()._flaresolverr_adresi() == ("", False)


def test_bos_ayar_uzagi_kapatir_yereli_degil(sahte_yonetici, izole_ayarla):
    """Kutuyu gizlilik için boşaltan kullanıcı: yerel örnek trafiği dışarı
    taşımıyor, o yüzden kullanılıyor."""
    sahte_yonetici(adres="http://127.0.0.1:8191")
    izole_ayarla(flaresolverr_url="")
    ses = cf.CFSession()
    assert ses._flaresolverr_adresi() == ("http://127.0.0.1:8191", True)
    assert "flaresolverr" in ses._available_methods


def test_yerel_kapaliysa_sorulmuyor(sahte_yonetici, izole_ayarla):
    y = sahte_yonetici()
    izole_ayarla(flaresolverr_url=UZAK, flaresolverr_yerel=False)
    assert cf.CFSession()._flaresolverr_adresi() == (UZAK, False)
    assert y.cagri == 0


def test_yerel_aciliyorsa_basamak_atlaniyor_uzaga_gidilmiyor(sahte_yonetici, izole_ayarla,
                                                              monkeypatch):
    sahte_yonetici(adres="", basliyor=True)
    izole_ayarla(flaresolverr_url=UZAK)
    monkeypatch.setattr(cf.requests, "post",
                        lambda *a, **k: pytest.fail("açılırken istek atıldı"))
    ses = cf.CFSession()
    assert ses._flaresolverr_adresi() == ("", True)
    assert ses._try_flaresolverr("https://korumali.test/") is None


def test_yerel_istek_vekile_gitmiyor_ve_devre_kesmiyor(sahte_yonetici, izole_ayarla,
                                                       monkeypatch):
    sahte_yonetici(adres="http://127.0.0.1:8191")
    izole_ayarla(flaresolverr_url=UZAK)
    gorulen = []

    def patla(url, **kw):
        gorulen.append((url, kw))
        raise requests.exceptions.ConnectionError("süreç öldü")

    monkeypatch.setattr(cf.requests, "post", patla)
    ses = cf.CFSession()
    assert ses._try_flaresolverr("https://korumali.test/") is None
    assert gorulen[0][0] == "http://127.0.0.1:8191/v1"
    assert gorulen[0][1]["proxies"] == {"http": None, "https": None}
    assert ses._flaresolverr_down is False, "yerel örnek oturum boyu kapatıldı"

    # Uzak sunucu için kesici eskisi gibi.
    uzak = cf.CFSession(flaresolverr_url="http://uzak.test:8191")
    assert uzak._try_flaresolverr("https://korumali.test/") is None
    assert uzak._flaresolverr_down is True
    assert "proxies" not in gorulen[-1][1], "uzak sunucuda kullanıcının vekili geçerli"


@linux
def test_cf_zinciri_yerel_flaresolverr_ile_cozuyor(sahte_kurulum, yonetici_kur, izole_ayarla,
                                                  monkeypatch):
    """Uçtan uca: ilk iki basamak düşüyor, zincir yerel örneği TEMBEL açıyor ve
    cevabı ondan alıyor. İstek (GUI'deki gibi) bir işçi thread'inden."""
    y = yonetici_kur()
    monkeypatch.setattr(fs, "yonetici", lambda: y)
    izole_ayarla(flaresolverr_url=UZAK)
    for bayrak in ("HAS_CURL_CFFI", "HAS_CLOUDSCRAPER", "HAS_QTWEBENGINE"):
        monkeypatch.setattr(cf, bayrak, False)
    ses = cf.CFSession(max_retries=1, retry_delay=0)
    monkeypatch.setattr(ses, "_try_requests_fallback",
                        lambda *a, **k: pytest.fail("zincir yerel FlareSolverr'ı atladı"))
    assert y._surec is None, "başlatma tembel olmalı"

    with concurrent.futures.ThreadPoolExecutor(1) as havuz:
        yanit = havuz.submit(ses.get, "https://korumali.test/bolum-1").result(timeout=60)
    assert "cozuldu https://korumali.test/bolum-1" in yanit.text
    assert ses.last_method == "flaresolverr"
    assert ses.cookies["cf_clearance"] == "SAHTE-CEREZ"
    assert y.durum_ozeti()["durum"] == "calisiyor"


# ── Ayarlar uçları (sayfasız) ───────────────────────────────────────────────
def test_yerel_kapatilinca_calisan_ornek_durduruluyor(ayar_uclari, izole_ev, qtbot,
                                                      monkeypatch):
    durdurulan = []
    sahte = type("Y", (), {"durdur": lambda self, elle=False: durdurulan.append(elle),
                           "dinle": lambda self, fn: None,
                           "durum_ozeti": lambda self: {"durum": "durdu"}})()
    monkeypatch.setattr(fs, "yonetici", lambda: sahte)
    uclar = ayar_uclari()
    uclar.ayarlari_kaydet({"flaresolverr_yerel": False})
    from turkanime_api.cli.dosyalar import Dosyalar
    assert Dosyalar().ayarlar["flaresolverr_yerel"] is False
    qtbot.waitUntil(lambda: durdurulan == [False], timeout=3000)
    assert cf.yerel_flaresolverr_ayari() is False
    assert uclar.ayarlar()["degerler"]["flaresolverr_yerel"] is False


def test_yerel_ayari_yazilmamissa_acik(izole_ev):
    assert cf.yerel_flaresolverr_ayari() is True


# ── Spec: FlareSolverr onefile arşivine girmiyor ────────────────────────────
def test_spec_bin_flaresolverr_klasorunu_pakete_almiyor(tmp_path, monkeypatch):
    """Geliştiricinin `bin/flaresolverr/`i (800 MB) onefile'a girerse her açılış
    onu da geçici dizine açar. Paket onu exe'nin YANINDA taşıyor."""
    import ast
    spec = DEPO_KOKU / "turkanime-gui.spec"
    agac = ast.parse(spec.read_text(encoding="utf-8"))
    dugumler = [d for d in agac.body if isinstance(d, ast.FunctionDef)
                and d.name in ("_yer_tutucu_mu", "_bin_verileri")]
    ad_alani = {"os": os, "sys": sys}
    exec(compile(ast.Module(body=dugumler, type_ignores=[]), str(spec), "exec"), ad_alani)
    (tmp_path / "bin" / "flaresolverr" / "_internal").mkdir(parents=True)
    (tmp_path / "bin" / "flaresolverr" / "flaresolverr").write_bytes(b"ikili")
    (tmp_path / "bin" / "mpv").write_bytes(b"\x7fELF" + b"\x00" * 64)
    monkeypatch.chdir(tmp_path)
    for hedef in ("linux", "win32"):
        verilenler = ad_alani["_bin_verileri"](hedef)
        assert all("flaresolverr" not in k for k, _h in verilenler), verilenler
    assert [Path(k).name for k, _h in ad_alani["_bin_verileri"]("linux")] == ["mpv"]


# ── Gereksinim sihirbazı (sayfa) ────────────────────────────────────────────
GEREKSINIM = "document.querySelector('[data-soru=gereksinim]')"


def _sihirbaz_dugmesi(etiket: str) -> str:
    return (f"[...{GEREKSINIM}.querySelectorAll('button')]"
            f".find(b => b.textContent === '{etiket}')")


@pytest.fixture
def linux_platformu(monkeypatch):
    """Sonuç çalıştığı makineye bağlı olmasın: Linux x64, Xvfb var."""
    monkeypatch.setattr(fs, "platform_anahtari", lambda *a, **k: "linux_x64")
    monkeypatch.setattr(fs, "eksik_bagimliliklar", lambda *a, **k: [])


@pytest.fixture
def kur_casusu(main_window):
    """Sihirbazın servise verdiği hedefler (gerçek indirme yok)."""
    cagrilar: list = []

    def kur(hedefler):
        cagrilar.append(list(hedefler))
        return True

    main_window.requirements.kur = kur
    return cagrilar


@pytest.mark.parametrize("isaretli,beklenen", [
    (True, ["mpv", "flaresolverr"]),
    (False, ["mpv"]),
])
def test_sihirbaz_flaresolverri_secilebilir_gosteriyor(linux_platformu, main_window, web,
                                                       kur_casusu, isaretli, beklenen):
    main_window._on_requirements_missing(["mpv", "flaresolverr"])
    web.bekle("!!" + GEREKSINIM)
    kutu = f"{GEREKSINIM}.querySelector('.fs-satiri input[type=checkbox]')"
    metin = web.js(GEREKSINIM + ".innerText")
    assert "FlareSolverr'ı da kur (önerilir, ~263 MB)" in metin
    assert "Bazı araçlar bulunamadı" in metin and "mpv" in metin
    assert web.js(kutu + ".checked") is True, "önerilen bileşen varsayılan seçili olmalı"
    if not isaretli:
        web.js(kutu + ".click()")
    web.js(_sihirbaz_dugmesi("İndir ve Kur") + ".click()")
    web.qtbot.waitUntil(lambda: kur_casusu == [beklenen], timeout=5000)


def test_yalnizca_flaresolverr_eksikse_onerilen_bilesen(linux_platformu, main_window, web,
                                                       kur_casusu):
    main_window._on_requirements_missing(["flaresolverr"])
    web.bekle("!!" + GEREKSINIM)
    metin = web.js(GEREKSINIM + ".innerText")
    assert "Önerilen bileşen" in metin
    assert "Bunlar olmadan oynatma" not in metin, "isteğe bağlı bileşen zorunlu gibi anlatıldı"
    web.js(f"{GEREKSINIM}.querySelector('.fs-satiri input').click()")
    assert web.js(_sihirbaz_dugmesi("İndir ve Kur") + ".disabled") is True, \
        "kurulacak bir şey yokken 'İndir ve Kur' basılabiliyor"
    assert kur_casusu == []


def test_desteklenmeyen_platformda_sihirbaz_nedenini_anlatiyor(monkeypatch, main_window, web,
                                                               kur_casusu):
    gercek = fs.desteklenmeme_sebebi
    monkeypatch.setattr(fs, "platform_anahtari", lambda *a, **k: None)
    monkeypatch.setattr(fs, "desteklenmeme_sebebi", lambda *a, **k: gercek("Darwin", "arm64"))
    assert _GERCEK_ONERILEN() == []
    main_window._on_requirements_missing(["mpv"])
    web.bekle("!!" + GEREKSINIM)
    satir = web.js(f"{GEREKSINIM}.querySelector('.fs-satiri').innerText")
    assert "macOS" in satir and "QtWebEngine" in satir
    assert web.js(f"!!{GEREKSINIM}.querySelector('.fs-satiri input')") is False
    web.js(_sihirbaz_dugmesi("İndir ve Kur") + ".click()")
    web.qtbot.waitUntil(lambda: kur_casusu == [["mpv"]], timeout=5000)


def test_servis_flaresolverri_kendi_kurulumuyla_kuruyor(qtbot, monkeypatch, ayarla):
    """`gereksinimler.json` listesi alınamasa bile FlareSolverr kurulur
    (kendi sabit adresi var) ve kurulum yönetici üzerinden yapılır."""
    from turkanime_api.gui.qt.requirements import RequirementsService

    def liste_yok(*_a, **_k):
        raise OSError("ağ yok")

    kurulan = []

    class Y:
        def kur(self, ilerleme=None, iptal=None):
            ilerleme(5, 10)
            ilerleme(10, 10)
            kurulan.append(1)

    monkeypatch.setattr(req, "gereksinim_listesi_getir", liste_yok)
    monkeypatch.setattr(req, "path_hazirla", lambda: None)
    monkeypatch.setattr(fs, "yonetici", lambda: Y())
    servis = RequirementsService()
    ilerleme = []
    servis.progress.connect(lambda y, m: ilerleme.append(m))
    with qtbot.waitSignal(servis.install_done, timeout=5000) as blocker:
        assert servis.kur(["mpv", "flaresolverr"]) is True
    sonuc = blocker.args[0]
    assert sonuc[0][0] == "mpv" and sonuc[0][1] is False and "liste" in sonuc[0][2]
    assert sonuc[1] == ("flaresolverr", True, "")
    assert kurulan == [1]
    assert any(m.startswith("FlareSolverr:") for m in ilerleme)


# ── Ayarlar sayfası ─────────────────────────────────────────────────────────
PANEL = "document.querySelector('.fs-paneli')"


def _panel_dugmesi(bas: str) -> str:
    return (f"[...{PANEL}.querySelectorAll('button')]"
            f".find(b => b.textContent.startsWith('{bas}'))")


@pytest.fixture
def ayar_yoneticisi(sahte_kurulum, monkeypatch):
    """Sayfanın konuştuğu yönetici — pencere kurulmadan ÖNCE (dinleyici
    kurulumda bağlanıyor). Sahte FlareSolverr'ı gerçekten açıp kapatır."""
    y = fs.Yonetici(tercih_port=0, baslangic_zaman_asimi=20)
    monkeypatch.setattr(fs, "yonetici", lambda: y)
    yield y
    y.durdur()


def test_ayarlarda_kurulu_degilken_kur_dugmesi(linux_platformu, main_window, web):
    main_window.show_page("settings")
    web.bekle(f"!!{PANEL} && {PANEL}.dataset.durum === 'kurulu_degil'")
    assert "Kurulu değil" in web.js(f"{PANEL}.querySelector('.rozet').innerText")
    assert web.js(_panel_dugmesi("Kur (~263 MB)") + ".hidden") is False
    assert web.js(_panel_dugmesi("Başlat") + ".hidden") is True
    anahtar = "document.querySelector('[data-bolum=baglanti] input[type=checkbox]')"
    assert web.js(anahtar + ".checked") is True, "yerel FlareSolverr varsayılan açık"


@linux
def test_ayarlardan_baslat_ve_durdur(ayar_yoneticisi, main_window, web):
    main_window.show_page("settings")
    web.bekle(f"!!{PANEL} && {PANEL}.dataset.durum === 'durdu'")
    assert web.js(_panel_dugmesi("Kur") + ".hidden") is True, \
        "kurulu ve güncel kopya için 'Kur' gösterilmemeli"
    web.js(_panel_dugmesi("Başlat") + ".click()")
    web.bekle(f"{PANEL}.dataset.durum === 'calisiyor'", timeout=20000)
    assert web.js(f"{PANEL}.querySelector('.rozet').innerText").strip() == "Çalışıyor"
    assert "127.0.0.1" in web.js(f"{PANEL}.innerText")
    assert web.js(_panel_dugmesi("Başlat") + ".disabled") is True
    web.js(_panel_dugmesi("Durdur") + ".click()")
    web.bekle(f"{PANEL}.dataset.durum === 'durdu'", timeout=15000)
    assert "Elle durduruldu" in web.js(f"{PANEL}.innerText")
    assert ayar_yoneticisi._surec is None


@linux
def test_cf_zincirinin_actigi_ornek_sayfaya_yansiyor(ayar_yoneticisi, main_window, web):
    """Tembel başlatma sayfadan habersiz oluyor; durum olayla gelmeli."""
    main_window.show_page("settings")
    web.bekle(f"!!{PANEL} && {PANEL}.dataset.durum === 'durdu'")
    with concurrent.futures.ThreadPoolExecutor(1) as havuz:
        assert havuz.submit(ayar_yoneticisi.hazir_adres, 15).result(timeout=30)
    web.bekle(f"{PANEL}.dataset.durum === 'calisiyor'", timeout=10000)


def test_ayarlardan_kurulum_ilerleme_ve_sonuc(linux_platformu, monkeypatch, main_window, web):
    def sahte_kur(self, ilerleme=None, iptal=None):
        ilerleme(131_000_000, 262_840_948)
        ilerleme(262_840_948, 262_840_948)
        return fs.Kurulum(Path("/x"), Path("/x/flaresolverr"), fs.SURUM, False)

    monkeypatch.setattr(fs.Yonetici, "kur", sahte_kur)
    main_window.show_page("settings")
    web.bekle(f"!!{PANEL} && {PANEL}.dataset.durum === 'kurulu_degil'")
    web.js(_panel_dugmesi("Kur (~263 MB)") + ".click()")
    web.bekle("document.querySelector('.sayfa-baslik .durum').textContent"
              ".includes('SHA-256 doğrulandı')", timeout=8000)
    assert web.js(f"{PANEL}.querySelector('.fs-ilerleme').hidden") is True
    # Arşiv paneliyle sınıf paylaşılmıyor: arşiv seçicileri FlareSolverr
    # düğmelerini saymamalı (bir kez saydı, arşiv testleri düştü).
    assert web.js("document.querySelectorAll('.arsiv-paneli .fs-paneli, "
                  ".fs-paneli .arsiv-cubuk').length") == 0
