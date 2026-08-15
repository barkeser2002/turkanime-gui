Bu klasör, PySide6 + QtWebEngine tabanlı TürkAnime GUI uygulamasını içerir
(`turkanime_api/gui/qt/`). CustomTkinter yığını Faz 9'da kaldırıldı; geri
dönmek gerekirse `pre-ctk-removal` etiketi güvenlik ağıdır.

## Derleme (lokal)

Önkoşullar:
- Python 3.9+ (`pyproject.toml`: `>=3.9,<4`; sınıflandırıcılarda 3.9 – 3.13)
- Bağımlılıklar (GUI):

```
pip install -r requirements-gui.txt
pip install pyinstaller
```

PyInstaller ile (spec):

```
python -m PyInstaller turkanime-gui.spec --noconfirm
```

Çıktı `dist/turkanime-gui/` klasörüne üretilir. **onedir**'dir, onefile değil:
QtWebEngine onefile'da kırılgan (`QtWebEngineProcess` çalışma anında
bulunamıyor), bu yüzden klasör zip'lenerek dağıtılır.

Spec'in yaptığı boyut budaması (bozarsanız paket ~130 MB şişer):
- Kullanılmayan Qt modülleri (`_DROP`) — 3D, Charts, Designer, Sql, Test…
- QtWebEngine dil paketleri yalnızca **tr + en-US**; klasörü tamamen boşaltmak
  WebEngine'i metinsiz bırakır, o yüzden ikisi mutlaka kalmalı.
- Qt'nin `.qm` çevirilerinden yalnızca `_tr` / `_en`.
- `*.debug.pak` / `*.debug.bin` — release WebEngine bunları hiç okumuyor.

Notlar:
- Proje kökünde `bin/` klasörü varsa içeriği paketlenir (mpv, aria2c, ffmpeg,
  yt-dlp vb.). CI yalnızca Windows'ta bu klasörü otomatik hazırlar.
- Uygulama simgesi `docs/TurkAnime.ico` dosyasından yüklenir.
- Windows'ta hem GUI hem CLI'yı tek seferde derlemek için `docs/build-win.bat`
  (gömülü mpv/ffmpeg/aria2c/yt-dlp indirmesi dahil).
- SHA-256 özetleri elle üretilmiyor: release iş akışı, yayımladığı her dosyanın
  yanına `.sha256` yazıyor ve aynı özeti `version.json`'a koyuyor.

## Çalıştırma

Derlenmiş çıktı:
- Windows: `dist/turkanime-gui/turkanime-gui.exe`
- Linux/macOS: `dist/turkanime-gui/turkanime-gui`

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

GitHub Actions, tag (vX.Y.Z) atıldığında **önce test kapısından geçirir**
(`pytest tests/` + `pylint -E`), sonra üç işletim sistemi için derler ve
release'e ekler. İş grafiği `test → build → {release, pypi}`; testler
kırmızıysa etiket ne Release'e ne PyPI'a gider.
- Windows: `turkanime-gui-windows.zip`
- Linux: `turkanime-gui-linux.zip`
- macOS: `turkanime-gui-macos.zip`

Ayrıca her platform için konsol tabanlı CLI ikilisi (`turkanime-cli-*`), her
dosyanın `.sha256` özeti ve `docs/version.json` üretilir. `version.json`
şeması `turkanime_api/common/updater.platform_paketi` ile hizalı olmak
zorunda (`platforms[os]["url"]` + `["checksum"]`); `tests/test_qt_updates.py`
iki tarafı birden denetliyor.

Windows derlemelerinde mpv, aria2c, ffmpeg ve yt-dlp, mevcutsa paketlenir.
Linux/macOS'ta sistemde bulunmaları önerilir; lokal derlemede `bin/` altına
koyarsanız paketlenir.
