Bu klasör, PySide6 + QtWebEngine tabanlı TürkAnime GUI uygulamasını içerir
(`turkanime_api/gui/qt/`). CustomTkinter yığını Faz 9'da kaldırıldı; geri
dönmek gerekirse `pre-ctk-removal` etiketi güvenlik ağıdır.

## Derleme (lokal)

Önkoşullar:
- Python 3.10–3.12
- Bağımlılıklar (GUI):

```
pip install -r requirements-gui.txt
```

PyInstaller ile (spec):

```
python -m PyInstaller turkanime-qt.spec
```

Çıktı `dist/turkanime-qt/` klasörüne üretilir. **onedir**'dir, onefile değil:
QtWebEngine onefile'da kırılgan (`QtWebEngineProcess` çalışma anında
bulunamıyor), bu yüzden klasör zip'lenerek dağıtılır.

Notlar:
- Proje kökünde `bin/` klasörü varsa içeriği paketlenir (mpv, aria2c, ffmpeg,
  yt-dlp vb.). CI yalnızca Windows'ta bu klasörü otomatik hazırlar.
- Uygulama simgesi `docs/TurkAnime.ico` dosyasından yüklenir.
- İsteğe bağlı doğrulama: `dist/` altındaki çıktılar için MD5 özetini
  oluşturmak isterseniz Windows'ta `docs/hash_dist_md5.bat` betiğini
  çalıştırabilirsiniz. CLI build betiği (`docs/build_exe.bat`) MD5 dosyalarını
  otomatik üretir.

## Çalıştırma

Derlenmiş çıktı:
- Windows: `dist/turkanime-qt/turkanime-qt.exe`
- Linux/macOS: `dist/turkanime-qt/turkanime-qt`

Geliştirme modunda çalıştırma:

```
python -m turkanime_api.gui.qt
```

Poetry ile (iki ad da aynı giriş noktasına bağlı):

```
poetry run turkanime-gui
poetry run turkanime-qt
```

macOS ipucu: İndirilen dosya karantinadaysa açılışa izin vermek için Gatekeeper
karantinasını kaldırmanız gerekebilir.

## CI / Release

GitHub Actions, tag (vX.Y.Z) atıldığında üç işletim sistemi için derler ve
release'e ekler:
- Windows: `turkanime-qt-windows.zip`
- Linux: `turkanime-qt-linux.zip`
- macOS: `turkanime-qt-macos.zip`

Ayrıca her platform için konsol tabanlı CLI ikilisi (`turkanime-cli-*`)
üretilir.

Windows derlemelerinde mpv, aria2c, ffmpeg ve yt-dlp, mevcutsa paketlenir.
Linux/macOS'ta sistemde bulunmaları önerilir; lokal derlemede `bin/` altına
koyarsanız paketlenir.
