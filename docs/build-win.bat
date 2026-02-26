@echo off
SETLOCAL ENABLEDELAYEDEXPANSION
REM Pure-Batch Windows build helper (no PowerShell)

echo Preparing build environment...
if not exist bin mkdir bin

echo Downloading 7zr (7-Zip) ...
curl -L -o 7zr.exe https://www.7-zip.org/a/7zr.exe || (
  echo Failed to download 7zr.exe & pause & exit /b 1
)

echo Downloading yt-dlp...
curl -L -o bin\yt-dlp.exe "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" || echo warn: yt-dlp download failed

echo Downloading aria2...
curl -L -o aria2.zip "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip" || (echo Failed to download aria2.zip & pause & exit /b 1)
echo Extracting aria2...
7zr.exe x aria2.zip -otmp_aria2 -y >nul 2>&1 || (
  echo 7z extraction failed, trying PowerShell Expand-Archive...
  powershell -NoProfile -Command "Expand-Archive -LiteralPath 'aria2.zip' -DestinationPath 'tmp_aria2' -Force" >nul 2>&1 || (
    echo Failed to extract aria2.zip & pause & exit /b 1
  )
)
echo Copying aria2 binaries to bin\ ...
set "ARIA2_DIR="
for /r tmp_aria2 %%f in (aria2c.exe) do (
  set "ARIA2_DIR=%%~dpf"
  goto :after_aria2_find
)
:after_aria2_find
if not defined ARIA2_DIR (
  echo aria2c.exe not found in extracted archive & pause & exit /b 1
) else (
  xcopy /E /I "%ARIA2_DIR%*" bin\ >nul 2>&1 || echo warn: aria2 copy failed
)

echo Downloading mpv (may be large)...
curl -L -o mpv.7z "https://sourceforge.net/projects/mpv-player-windows/files/64bit/mpv-x86_64-20231231-git-abc2a74.7z/download" || (echo Failed to download mpv.7z & pause & exit /b 1)
echo Extracting mpv...
7zr.exe x mpv.7z -obin -y >nul 2>&1 || (echo Failed to extract mpv.7z & pause & exit /b 1)

echo Locating mpv.exe...
set "MPV_FOUND="
for /r bin %%f in (mpv.exe) do (
  set "MPV_FOUND=%%~f"
  goto :after_mpv_find
)
:after_mpv_find
if not defined MPV_FOUND (
  echo mpv.exe not found after extraction & pause & exit /b 1
) else (
  echo mpv found at %MPV_FOUND%
)

echo Downloading ffmpeg...
curl -L -o ffmpeg.zip "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" || (echo Failed to download ffmpeg.zip & pause & exit /b 1)
echo Extracting ffmpeg...
7zr.exe x ffmpeg.zip -otmp_ffmpeg -y >nul 2>&1 || (
  echo 7z extraction failed, trying PowerShell Expand-Archive...
  powershell -NoProfile -Command "Expand-Archive -LiteralPath 'ffmpeg.zip' -DestinationPath 'tmp_ffmpeg' -Force" >nul 2>&1 || (
    echo Failed to extract ffmpeg.zip & pause & exit /b 1
  )
)

echo Locating ffmpeg.exe...
set "FFMPEG_FOUND="
for /r tmp_ffmpeg %%f in (ffmpeg.exe) do (
  set "FFMPEG_FOUND=%%~f"
  goto :after_ffmpeg_find
)
:after_ffmpeg_find
if not defined FFMPEG_FOUND (
  echo ffmpeg.exe not found after extraction & pause & exit /b 1
) else (
  echo Copying ffmpeg to bin\
  copy /Y "%FFMPEG_FOUND%" bin\ >nul
)
for /r tmp_ffmpeg %%g in (ffprobe.exe) do (
  copy /Y "%%~g" bin\ >nul
  goto :after_ffprobe_copy
)
:after_ffprobe_copy

echo Embedded runtime prepared.

echo Installing python deps and building GUI with PyInstaller...
python -m pip install --upgrade pip || (echo pip upgrade failed & pause & exit /b 1)
pip install -r requirements-gui.txt || echo warn: requirements-gui install failed
pip install pyinstaller || echo warn: pyinstaller install failed
python -m PyInstaller pyinstaller.spec || echo warn: PyInstaller GUI build failed

echo Building CLI (Windows)...
python -m pip install --upgrade pip
pip install -r requirements.txt || echo warn: requirements install failed
pip install pyinstaller pyinstaller_versionfile || echo warn: pyinstaller_versionfile install failed

echo Generating version_generator.py
>version_generator.py echo import pyinstaller_versionfile
>>version_generator.py echo from turkanime_api.cli.version import __version__
>>version_generator.py echo.
>>version_generator.py echo pyinstaller_versionfile.create_versionfile(
>>version_generator.py echo     output_file='versionfile.txt',
>>version_generator.py echo     version=__version__,
>>version_generator.py echo     company_name='TurkAnime Dev',
>>version_generator.py echo     file_description='Anime İndirici ^& Oynatıcı',
>>version_generator.py echo     internal_name='TurkAnime',
>>version_generator.py echo     legal_copyright='© Barkeser2002, All rights reserved.',
>>version_generator.py echo     original_filename='TurkAnime.exe',
>>version_generator.py echo     product_name='TurkAnime İndirici'
>>version_generator.py echo )

echo Generating compiled.py
>compiled.py echo from turkanime_api.cli.__main__ import main
>>compiled.py echo if __name__ == "__main__":
>>compiled.py echo     main()

echo Running PyInstaller for CLI
pyinstaller --noconfirm --onefile --console --icon "docs\TurkAnime.ico" --name "TurkAnime" --version-file versionfile.txt --hidden-import yt_dlp --hidden-import curl_cffi --hidden-import Crypto --hidden-import selenium --add-data "gereksinimler.json;." compiled.py || echo warn: PyInstaller CLI build failed

echo Build complete. Artifacts are in the dist\ directory.

ENDLOCAL
pause
