# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — PySide6 + QtWebEngine GUI'si (turkanime-qt).

ÖNEMLİ: Bu spec **onedir** üretir (tek klasör), onefile değil. QtWebEngine bir
Chromium runtime'ı taşır (`QtWebEngineProcess`, `resources/*.pak`, `icudtl.dat`,
`qtwebengine_locales/`) ve onefile modunda bu alt-süreç çalışma anında
güvenilir biçimde bulunamıyor. Dağıtım için `dist/turkanime-qt/` klasörünü
zip'leyin.

Boyut: QtWebEngine nedeniyle ~130-250 MB. CLI build'i bundan etkilenmez.
"""
import os

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


def _keep(entry):
    name = str(entry[0]).replace("\\", "/").rsplit("/", 1)[-1]
    return not any(tok.lower() in name.lower() for tok in _DROP)


pyside_binaries = [b for b in pyside_binaries if _keep(b)]
pyside_datas = [d for d in pyside_datas if _keep(d)]
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

bin_data = [('bin', 'bin')] if os.path.isdir('bin') else []

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
    # Artık kullanılmayan eski GUI yığını + gereksiz ağır bağımlılıklar
    excludes=[
        'customtkinter', 'tkinter', 'selenium', 'undetected_chromedriver',
        'matplotlib', 'numpy', 'scipy', 'pandas', 'PIL',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir: EXE yalnızca başlatıcıyı içerir, ikili/veriler COLLECT ile klasöre gider.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='turkanime-qt',
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
    name='turkanime-qt',
)
