# -*- mode: python ; coding: utf-8 -*-
import os
import platform
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Hidden imports - collect all submodules for better compatibility
hiddenimports = (
    collect_submodules('yt_dlp') +
    collect_submodules('curl_cffi') +
    collect_submodules('Crypto') +
    collect_submodules('customtkinter') +
    collect_submodules('fastapi') +
    collect_submodules('uvicorn') +
    collect_submodules('pydantic') +
    ['yt_dlp', 'curl_cffi', 'Crypto', 'customtkinter', 'toml', 'fastapi', 'uvicorn', 'pydantic']
)

# Include bin directory if it exists (only for Windows)
if platform.system() == 'Windows':
    bin_data = [('bin', 'bin')] if os.path.isdir('bin') else []
else:
    bin_data = []

# Include frontend build if it exists
frontend_build = []
if os.path.isdir('frontend_build'):
    frontend_build = [('frontend_build', 'frontend_build')]

a = Analysis(
    ['backend/unified_server.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('docs/TurkAnime.ico', 'docs'),
        ('docs/TurkAnime.png', 'docs'),
        ('gereksinimler.json', '.'),
        ('backend', 'backend'),
        ('turkanime_api', 'turkanime_api'),
    ] + bin_data + frontend_build,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='turkanime-gui-unified',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # Show console for now
    icon='docs/TurkAnime.ico'
)
