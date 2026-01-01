# TurkAnime GUI - Unified Build

Bu sürüm, Python backend ve Next.js frontend'i tek bir executable dosyada birleştirir.

## Özellikler

- ✅ Tek executable dosya (backend + frontend birlikte)
- ✅ Otomatik GitHub Release
- ✅ Python içinde Next.js frontend
- ✅ LiveChart.me entegrasyonu
- ✅ AniList ilerleme takibi
- ✅ Gelişmiş başlık eşleştirme

## Kurulum

### İndirme

[Releases](https://github.com/barkeser2002/turkanime-gui/releases/latest) sayfasından işletim sisteminize uygun dosyayı indirin:

- **Windows**: `turkanime-gui-unified-windows.exe`
- **Linux**: `turkanime-gui-unified-linux`
- **macOS**: `turkanime-gui-unified-macos`

### Çalıştırma

#### Windows
```cmd
turkanime-gui-unified-windows.exe
```

#### Linux/macOS
```bash
chmod +x turkanime-gui-unified-linux  # veya -macos
./turkanime-gui-unified-linux         # veya -macos
```

Uygulama başladığında, tarayıcınızda `http://localhost:8000` adresini açın.

## Geliştirme

### Build Süreci

1. **Frontend Build**
   ```bash
   cd frontend
   npm install
   npm run build
   ```

2. **Python Paketleme**
   ```bash
   python build_unified.py
   ```

3. **Manuel Build**
   ```bash
   # Frontend'i build et
   cd frontend && npm run build && cd ..
   
   # Build output'u kopyala
   cp -r frontend/out frontend_build
   
   # PyInstaller ile paketле
   pyinstaller turkanime-gui-unified.spec
   ```

### Otomatik Build (GitHub Actions)

Her tag push'unda otomatik olarak build edilir:

```bash
git tag v10.0.0
git push origin v10.0.0
```

## Mimari

```
turkanime-gui-unified.exe
├── Python Backend (FastAPI)
│   ├── API Endpoints (/api/*)
│   ├── LiveChart.me Client
│   ├── Title Matcher
│   └── AniList Integration
├── Next.js Frontend (Static)
│   ├── HTML/CSS/JS
│   ├── React Components
│   └── API Client
└── Embedded Resources
    ├── mpv/ffmpeg (Windows)
    └── Icons/Assets
```

## API Endpoints

Backend aynı process içinde çalışır ve şu endpoint'leri sağlar:

- `GET /` - Frontend (Next.js app)
- `GET /api/anime/current-season` - Güncel sezon
- `GET /api/anime/search` - Anime arama
- `POST /api/titles/match` - Başlık eşleştirme
- `POST /api/anilist/progress` - İlerleme güncelleme

Swagger dokümantasyonu: `http://localhost:8000/docs`

## Avantajlar

1. **Tek Dosya**: Tüm uygulama tek executable'da
2. **Kolay Dağıtım**: Sadece bir dosya paylaş
3. **Hızlı Başlatma**: Node.js runtime gerektirmez
4. **Güvenlik**: Kaynak kodu executable içinde
5. **Otomatik Release**: GitHub Actions ile otomatik build

## Sorun Giderme

### Port Kulımda
Eğer 8000 portu kullanımdaysa:
```bash
# Windows
turkanime-gui-unified-windows.exe --port 8080

# Linux/macOS
./turkanime-gui-unified-linux --port 8080
```

### Frontend Görünmüyor
- Executable ile aynı dizinde `frontend_build` klasörü olmalı
- PyInstaller spec dosyası frontend_build'i include ediyor mu kontrol edin

### API Çalışmıyor
- Console çıktılarını kontrol edin
- `http://localhost:8000/api/health` endpoint'ini test edin

## Lisans

CC-BY-NC-ND-4.0
