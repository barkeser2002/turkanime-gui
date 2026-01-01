
<div align="center">

![TürkAnime Logo](https://i.imgur.com/Dw8sv30.png)

[![GitHub all releases](https://img.shields.io/github/downloads/barkeser2002/turkanime-gui/total?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Downloads](https://static.pepy.tech/personalized-badge/turkanime-gui?period=total&units=international_system&left_color=grey&right_color=orange&left_text=Pip%20Installs)](https://pepy.tech/project/turkanime-gui)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/barkeser2002/turkanime-gui?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest/download/turkanime-gui-windows.exe)
[![Pypi version](https://img.shields.io/pypi/v/turkanime-gui?style=flat-square)](https://pypi.org/project/turkanime-gui/)

</div>

# TürkAnime GUI

TürkAnime artık **modern web teknolojileri** ile yeniden yapılandırıldı. Python backend ve Next.js frontend ile anime keşif, izleme ve indirme deneyimi sunuyor.

## 🏗️ Yeni Mimari

### Python Backend (FastAPI)
- LiveChart.me entegrasyonu (güncel sezon anime'leri)
- Gelişmiş başlık eşleştirme (Japonca, Romaji, İngilizce)
- AniList OAuth2 entegrasyonu (sadece kayıt ve ilerleme takibi)
- RESTful API endpoints

### Next.js Frontend
- Modern React uygulaması
- TypeScript tip güvenliği
- Tailwind CSS responsive tasarım
- Backend API entegrasyonu

## 🚀 Hızlı Başlangıç

### Backend
```bash
cd backend
pip install -r requirements.txt
python start_server.py
```
Backend `http://localhost:8000` adresinde çalışır.

### Frontend
```bash
cd frontend
npm install
npm run dev
```
Frontend `http://localhost:3000` adresinde çalışır.

**Detaylı kurulum için:** [QUICKSTART.md](QUICKSTART.md)

## ✨ Öne Çıkan Özellikler

### Yeni Özellikler (v10+)
- **🌐 Modern Web Stack:** Python backend (FastAPI) + Next.js frontend
- **📊 LiveChart.me Entegrasyonu:** Güncel sezon anime'leri ve trendler
- **🔤 Gelişmiş Başlık Eşleştirme:** Japonca, Romaji ve İngilizce başlık desteği
- **🎯 AniList Sadece İlerleme:** AniList artık sadece kayıt ve ilerleme takibi için
- **⚡ RESTful API:** Temiz ve belgelenmiş API endpoints

### Mevcut Özellikler
- **Çoklu kaynak desteği:** Anizle, AnimeCix ve TürkAnime'den tek arayüzle erişim
- **Hızlı stream çekme:** Paralel işleme ile 8 kat hızlı video link alma
- **Tek tıkla indirme ve oynatma:** Bölümleri sıra bekletmeden indir, izlerken otomatik kaydet
- **AniList OAuth2:** Güvenli hesap bağlantısı ve liste senkronizasyonu
- **Fansub ve kalite seçimi:** Desteklenen kaynaklardan en temiz sürümü bulur
- **Discord Rich Presence:** O anda ne izlediğini arkadaşlarınla paylaş
- **Çoklu platform:** Windows, macOS, Linux desteği

## 🧭 Uygulama Akışı

### Modern Web Uygulaması (Yeni)
1. **Backend Başlat:** FastAPI sunucusu ile anime verilerine erişim
2. **Frontend Aç:** Next.js uygulaması ile modern arayüz
3. **Keşfet:** LiveChart.me'den güncel sezon anime'leri
4. **Ara:** Gelişmiş başlık eşleştirme ile arama
5. **İzle & Takip Et:** AniList ile ilerleme kaydet

### Desktop Uygulaması (Mevcut)
1. **Keşfet:** Trend listeler ve kişisel öneriler tek ekranda
2. **Ara:** Yerel kaynaklarla AniList veritabanını aynı anda gez
3. **İndir & Oynat:** mpv entegrasyonu sayesinde indirme ve izleme tek pencerede
4. **İlerleme Takibi:** İzlediklerin otomatik tutulur, AniList'e anında yansır

## 📺 Ekran Görüntüleri

### Desktop Uygulaması

#### Anasayfa Ekranı
![anasayfa.png](https://i.imgur.com/Mh353OU.png)

#### Anime Ekranı
![animesayfası.png](https://i.imgur.com/9D4yUdn.png)

## 💬 Discord Rich Presence

TürkAnime GUI, Discord profilinde canlı durum gösterebilir:

- Ana sayfa gezinme
- Trend veya arama ekranları
- İndirme süreci
- İzlenilen anime ve bölüm

> **İpucu:** Ayarlar → Discord Rich Presence bölümünden tek tuşla aç/kapat. Özellik isteğe bağlıdır; `pypresence` yoksa uygulama normal çalışmaya devam eder.

## 📥 Kurulum

### Web Uygulaması (Yeni - Önerilen)

1. **Backend'i başlatın:**
```bash
cd backend
pip install -r requirements.txt
python start_server.py
```

2. **Frontend'i başlatın:**
```bash
cd frontend
npm install
npm run dev
```

3. **Tarayıcınızda açın:** `http://localhost:3000`

**Detaylı kurulum:** [QUICKSTART.md](QUICKSTART.md) | **Mimari:** [ARCHITECTURE.md](ARCHITECTURE.md)

### Desktop Uygulaması (Mevcut)

#### 1. Hazır Paket
- [Releases](https://github.com/barkeser2002/turkanime-gui/releases/latest) sayfasından en güncel `.exe` dosyasını indir
- Çalıştır ve kurulum sihirbazını tamamla

#### 2. PyPI Üzerinden
```bash
pip install turkanime-gui
turkanime-gui
```

#### 3. Kaynak Koddan
```bash
git clone https://github.com/barkeser2002/turkanime-gui.git
cd turkanime-indirici
pip install -r requirements-gui.txt
python -m turkanime_api.gui.main
```

## 🚀 Kullanım

1. **İlk açılışta** ffmpeg/mpv bin klasörü otomatik hazırlanır.
2. **Keşfet veya Ara sekmesinden** anime seç.
3. **Bölümü oynat** ya da **indir**; ilerlemen otomatik tutulur.

## 📺 Desteklenen Kaynaklar

### Birincil Kaynaklar
| Kaynak | Açıklama |
|--------|----------|
| **Anizle** | 4500+ anime, paralel stream çekme, HLS desteği |
| **AnimeCix** | Geniş fansub seçenekleri |
| **TürkAnime** | Klasik Türk anime kaynağı |

### Video Sunucuları
```
Sibnet  Odnoklassniki  HDVID  Myvi  Sendvid  Mail
Amaterasu  Alucard  PixelDrain  VK  MP4upload
Vidmoly  Dailymotion  Yandisk  Uqload  Drive
FirePlayer (Anizle)  HLS Streams
```

## 🔧 Sistem Gereksinimleri

- **Python:** 3.9+
- **FFmpeg & yt-dlp:** Uygulama ilk açılışta otomatik indirir.
- **mpv:** Bin klasörü içinde paketle birlikte gelir (GUI).
- **İnternet bağlantısı:** Kaynaklara erişim ve AniList senkronu için.

## 👨‍💻 Katkıda Bulun

- Hata bildirimi veya feature isteği için [Issues](https://github.com/barkeser2002/turkanime-gui/issues) sekmesini kullan.
- PR göndermeden önce kısa bir açıklama ve ekran görüntüsü eklemek incelemeyi hızlandırır.
- Dokümantasyon ve çeviri katkıları da memnuniyetle kabul edilir.


> CI yayınlarında `.md5` dosyaları otomatik eklenir.



