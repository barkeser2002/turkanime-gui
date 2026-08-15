# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — TürkAnime GUI (PySide6 + QtWebEngine).

ÖNEMLİ: Bu spec **onedir** üretir (tek klasör), onefile değil. QtWebEngine bir
Chromium runtime'ı taşır (`QtWebEngineProcess`, `resources/*.pak`, `icudtl.dat`,
`qtwebengine_locales/`) ve onefile modunda bu alt-süreç çalışma anında
güvenilir biçimde bulunamıyor. Dağıtım için `dist/turkanime-gui/` klasörünü
zip'leyin.

Ad neden `turkanime-qt` değil: Faz 9'dan sonra tek arayüz kaldı, "qt" artık
ayırt edici bir bilgi taşımıyor. Depo, PyPI paketi, giriş noktası ve release
artefaktı hep `turkanime-gui`; paketlenen klasör de aynı adı taşısın ki
`version.json`'daki indirme bağlantısıyla birebir eşleşsin.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# QtWebEngine'in çalışması için PySide6 toplanmalı:
# binaries (QtWebEngineProcess), datas (resources/, translations/) ve alt modüller.
pyside_datas, pyside_binaries, pyside_hidden = collect_all('PySide6')

# --- Boyut kırpma -----------------------------------------------------------
# collect_all() PySide6'nın TAMAMINI getiriyor (~634 MB). Kullanmadığımız Qt
# modüllerini eliyoruz.
# DİKKAT: QtWebEngine içeride Quick/Qml/WebChannel/Positioning/OpenGL kullanır —
# bunlar ELENMEZ, aksi hâlde WebEngine çalışmaz. Yalnızca tamamen ilgisiz
# modüller çıkarılıyor.
_DROP = (
    "Qt63D", "Quick3D", "Charts", "DataVisualization", "Designer",
    "Bluetooth", "Nfc", "SerialPort", "SerialBus", "Sensors",
    "TextToSpeech", "Scxml", "RemoteObjects", "Help", "UiTools",
    "SpatialAudio", "Qt6Sql", "Qt6Test", "Qt6Pdf",
)

# Chromium'un dil paketleri (`translations/qtwebengine_locales/*.pak`): 53 dosya,
# 43,6 MB. Arayüz Türkçe; Chromium istediği dilin .pak'ini bulamazsa en-US'a
# düşer, bu yüzden bu ikisi bırakılıp gerisi atılıyor. Klasörün TAMAMINI atmak
# olmaz — locale dizini boşsa WebEngine "locales directory not found" deyip
# metinsiz açılır.
_LOCALE_KEEP = ("tr.pak", "en-us.pak")

# Qt'nin kendi arayüz çevirileri (`translations/*.qm`): 304 dosya, 15,1 MB.
# Uygulama QTranslator kurmuyor, yani şu an hiçbiri okunmuyor; tr/en yine de
# kalsın ki çeviri açıldığı gün dosya elde olsun.
_QM_KEEP = ("_tr.qm", "_en.qm")

# `*.debug.pak` / `*.debug.bin`: yalnızca debug derlenmiş bir WebEngine okur —
# tek başlarına 80,9 MB (devtools debug paketi 75,8 MB). Doğrulandı: release
# `Qt6WebEngineCore.dll` ikilisinde "debug.pak" dizgesi hiç geçmiyor, buna
# karşılık "qtwebengine_resources.pak" geçiyor.
_DEBUG_EK = (".debug.pak", ".debug.bin")


def _budanacak(dest, src):
    """Bu veri dosyası pakete girmesin mi?

    İki ayrı biçimle çağrılıyor: `collect_all()` çıktısı `(kaynak, hedef_dizin)`,
    Analysis sonrası TOC ise `(hedef_yol, kaynak, tip)`. İkisinde de dosya adı
    `src`'nin son parçasından, dizin bağlamı `dest`'ten okunuyor.
    """
    ad = os.path.basename(str(src)).lower()
    hedef = str(dest).replace("\\", "/").lower()
    if any(ad.endswith(ek) for ek in _DEBUG_EK):
        return True
    if "qtwebengine_locales" in hedef:
        return ad not in _LOCALE_KEEP
    if ad.endswith(".qm"):
        return not any(ad.endswith(ek) for ek in _QM_KEEP)
    return False


def _keep(entry):
    name = str(entry[0]).replace("\\", "/").rsplit("/", 1)[-1]
    return not any(tok.lower() in name.lower() for tok in _DROP)


pyside_binaries = [b for b in pyside_binaries if _keep(b)]
pyside_datas = [d for d in pyside_datas
                if _keep(d) and not _budanacak(d[1], d[0])]
pyside_hidden = [h for h in pyside_hidden
                 if not any(tok.lower() in h.lower() for tok in _DROP)]

hiddenimports = (
    pyside_hidden
    + collect_submodules('yt_dlp')
    + collect_submodules('curl_cffi')
    + collect_submodules('Crypto')
    + [
        'toml',
        # QtWebEngine modülleri dinamik yüklendiği için açıkça belirtiliyor
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtNetwork',
        # CF çözücü alt-süreci uygulamanın kendisi tarafından çağrılıyor
        'turkanime_api.common.cf_qt_solver',
    ]
)

def _yer_tutucu_mu(yol):
    """İlk satırı "#" ile başlayan metin dosyası mı?

    Depoda `bin/*.exe` yerine 57-60 baytlık yer tutucu metinler duruyor
    (git-lfs'siz kopyalar). Kanonik kontrol
    `turkanime_api/common/requirements._placeholder_mi` — burada kopyalanıyor
    ki spec ayrıştırılırken projeyi import etmek gerekmesin. `utf-8-sig` şart:
    yer tutucular BOM ile yazılmış.
    """
    try:
        with open(yol, "r", encoding="utf-8-sig", errors="ignore") as fp:
            return fp.readline().strip().startswith("#")
    except OSError:
        return False


def _bin_verileri(hedef=None):
    """Pakete girecek `bin/` dosyaları — klasörün tamamı DEĞİL.

    Eskiden `[('bin', 'bin')]` idi: gerçek ikilileri indiren CI adımı
    `if: matrix.os == 'windows-latest'` ile korumalıyken PyInstaller klasörü
    üç platformda da kopyalıyordu. Sonuç, Linux/macOS zip'lerinin içinde
    Windows'a ait, üstelik yer tutucu olan `.exe` dosyaları taşımak.
    Çalışma anını bozmuyordu (`common/requirements._placeholder_mi` onları
    "kurulu" saymıyor) ama pakete hiç girmemeleri gerekiyor.
    """
    if not os.path.isdir('bin'):
        return []
    windows = (hedef or sys.platform).startswith('win')
    girdiler = []
    for ad in sorted(os.listdir('bin')):
        yol = os.path.join('bin', ad)
        if not os.path.isfile(yol):
            continue
        if not windows and ad.lower().endswith('.exe'):
            continue
        if _yer_tutucu_mu(yol):
            continue
        girdiler.append((yol, 'bin'))
    return girdiler


bin_data = _bin_verileri()

a = Analysis(
    ['turkanime_api/gui/qt/__main__.py'],
    pathex=[],
    binaries=pyside_binaries,
    datas=[
        ('docs/TurkAnime.ico', 'docs'),
        ('docs/TurkAnime.png', 'docs'),
        ('gereksinimler.json', '.'),
    ] + pyside_datas + bin_data,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # DİKKAT: burası import listesi DEĞİL, dışlama listesi. Faz 9'da CTk yığını
    # silindiği için customtkinter/tkinter/selenium/PIL projede artık hiç
    # geçmiyor; yine de burada bırakılıyorlar ki transitif bir bağımlılık
    # (ör. bir paketin tkinter'a dokunması) onları sessizce pakete sokmasın.
    excludes=[
        'customtkinter', 'tkinter', 'selenium', 'undetected_chromedriver',
        'matplotlib', 'numpy', 'scipy', 'pandas', 'PIL',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Budama Analysis'ten SONRA bir kez daha uygulanıyor: Qt verilerini yalnızca
# yukarıdaki `collect_all` getirmiyor, PyInstaller'ın kendi PySide6 hook'u da
# ekliyor. Sadece girdi listesini süzmek, locale/debug dosyalarının arka
# kapıdan geri gelmesine izin verirdi.
a.datas = [t for t in a.datas if not _budanacak(t[0], t[1])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir: EXE yalnızca başlatıcıyı içerir, ikili/veriler COLLECT ile klasöre gider.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='turkanime-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='docs/TurkAnime.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='turkanime-gui',
)
