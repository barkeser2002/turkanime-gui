@echo off
CHCP 1252 >NUL
:: Windows için PyInstaller ile build: terminal tabanlı tek dosya exe
:: Script'i bu dizinden çalıştırdığınızı varsayıyorum
findstr /R /C:"__build__ = .exe." ..\turkanime_api\cli\version.py 1>NUL 2>NUL || (
	echo Derlemeden once version.py dosyasindaki "build" degiskenini exe olarak degistirmelisin
	goto :EOF
)

echo Herseyin guncel oldugundan emin olunuyor..
pip install -U pyinstaller 1>NUL
pip install pyinstaller_versionfile 1>NUL
pip install -r ..\requirements.txt 1>NUL

echo Surum dosyasi yaratiliyor..
(
    echo import pyinstaller_versionfile
    echo from turkanime_api.cli.version import __version__
    echo.
    echo pyinstaller_versionfile.create_versionfile^(
    echo     output_file="versionfile.txt",
    echo     version=__version__,
    echo     company_name="TurkAnime Dev",
    echo     file_description="Anime İndirici & Oynatıcı",
    echo     internal_name="TurkAnime",
    echo     legal_copyright="© KebabLord, All rights reserved.",
    echo     original_filename="TurkAnime.exe",
    echo     product_name="TurkAnime İndirici"
    echo ^)
) > ..\version_generator.py
cd ..
py version_generator.py

echo compiled.py yaratiliyor..
(
    echo from turkanime_api.cli.__main__ import main
    echo if __name__ == "__main__":
    echo     main^(^)
) > ..\compiled.py

echo EXE derleniyor..
pyinstaller --noconfirm --onefile --console --icon "docs\Turkanime.ico" --name "Turkanime" --version-file versionfile.txt "compiled.py" && (
  echo Hersey yolunda gitti, calistirilabilir dosya: dist/Turkanime.exe
  for %%F in (dist\*.exe) do (
    certutil -hashfile "%%F" MD5 > "%%F.md5"
    echo MD5 olusturuldu: %%F.md5
  )
)

echo.
echo (KAPATMAK ICIN ENTER'A BASIN)
set /p input=
