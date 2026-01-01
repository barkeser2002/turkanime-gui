# TurkAnime GUI - Quick Start Guide

Bu proje Python backend ve Next.js frontend kullanarak modern bir anime keşif ve takip uygulaması sağlar.

## 🚀 Hızlı Başlangıç

### 1. Backend'i Başlatın

```bash
# Backend klasörüne gidin
cd backend

# Bağımlılıkları yükleyin
pip install -r requirements.txt

# Sunucuyu başlatın
python start_server.py
```

Backend `http://localhost:8000` adresinde çalışacaktır.

### 2. Frontend'i Başlatın

Yeni bir terminal penceresi açın:

```bash
# Frontend klasörüne gidin
cd frontend

# Bağımlılıkları yükleyin (ilk kez)
npm install

# Development sunucusunu başlatın
npm run dev
```

Frontend `http://localhost:3000` adresinde çalışacaktır.

### 3. Uygulamayı Kullanın

Tarayıcınızda `http://localhost:3000` adresine gidin.

## 📋 Özellikler

### ✅ Tamamlanan
- Python FastAPI backend
- Next.js TypeScript frontend
- LiveChart.me entegrasyonu
- Gelişmiş başlık eşleştirme (Romaji/English/Japanese)
- AniList OAuth2 entegrasyonu
- Anime arama ve listeleme
- Responsive tasarım

### 🔄 AniList Entegrasyonu

AniList ile bağlanmak için:

1. Backend çalışıyor olmalı
2. Frontend'de AniList giriş butonuna tıklayın
3. AniList hesabınızla giriş yapın
4. İzleme ilerlemeniz otomatik senkronize olacak

## 🛠️ Teknoloji Stack

### Backend
- Python 3.9+
- FastAPI
- BeautifulSoup4
- Requests

### Frontend
- Next.js 16
- React 18
- TypeScript
- Tailwind CSS

## 📚 Dokümantasyon

- Backend API: http://localhost:8000/docs
- Backend README: [backend/README.md](backend/README.md)
- Frontend README: [frontend/README.md](frontend/README.md)
- Mimari: [ARCHITECTURE.md](ARCHITECTURE.md)

## 🔧 Geliştirme

### Backend Geliştirme

Backend değişiklikleriniz otomatik olarak yeniden yüklenir (hot reload).

```bash
cd backend
python start_server.py
```

### Frontend Geliştirme

Frontend de otomatik hot reload destekler.

```bash
cd frontend
npm run dev
```

## 📦 Production Build

### Backend

```bash
cd backend
pip install -r requirements.txt
python server.py
```

### Frontend

```bash
cd frontend
npm run build
npm start
```

## ❓ Sorun Giderme

### Backend başlamıyor
- Python 3.9+ yüklü olduğundan emin olun
- Bağımlılıkları yükleyin: `pip install -r requirements.txt`
- Port 8000 kullanımda değil mi kontrol edin

### Frontend başlamıyor
- Node.js yüklü olduğundan emin olun
- `npm install` komutunu çalıştırın
- Port 3000 kullanımda değil mi kontrol edin

### API bağlantı hatası
- Backend'in çalıştığından emin olun
- `.env.local` dosyasının doğru API URL'sini içerdiğini kontrol edin

## 🤝 Katkıda Bulunma

1. Fork yapın
2. Feature branch oluşturun
3. Değişikliklerinizi commit edin
4. Push edin
5. Pull Request açın

## 📄 Lisans

CC-BY-NC-ND-4.0
