# TurkAnime GUI - Modernized Architecture

Anime keşif, izleme ve indirme uygulaması. Python backend ve Next.js frontend ile yeniden yapılandırılmış mimari.

## 🏗️ Mimari

### Backend (Python)
- **FastAPI** REST API sunucusu
- **LiveChart.me** entegrasyonu (güncel sezon anime'leri)
- **AniList** entegrasyonu (sadece kayıt ve ilerleme takibi için)
- Gelişmiş başlık eşleştirme (Romaji/English/Japanese)

### Frontend (Next.js)
- **Next.js 16** ile modern React uygulaması
- **TypeScript** tip güvenliği
- **Tailwind CSS** responsive tasarım
- Backend API ile entegrasyon

## 🚀 Kurulum ve Çalıştırma

### Backend

1. Backend bağımlılıklarını yükleyin:
```bash
cd backend
pip install -r requirements.txt
```

2. Backend sunucusunu başlatın:
```bash
python server.py
```

Sunucu `http://localhost:8000` adresinde çalışacaktır.

### Frontend

1. Frontend bağımlılıklarını yükleyin:
```bash
cd frontend
npm install
```

2. Geliştirme sunucusunu başlatın:
```bash
npm run dev
```

Frontend `http://localhost:3000` adresinde çalışacaktır.

## 📋 Özellikler

### Backend API Endpoints

- `GET /api/anime/current-season` - Güncel sezon anime listesi (LiveChart.me)
- `GET /api/anime/news` - Anime haberleri
- `GET /api/anime/recently-aired` - Son yayınlanan bölümler
- `GET /api/anime/search?q=<query>` - Anime arama
- `POST /api/titles/match` - Başlık eşleştirme
- `GET /api/anilist/user` - AniList kullanıcı bilgisi
- `POST /api/anilist/progress` - İzleme ilerlemesi güncelleme

### Frontend Özellikleri

- Güncel sezon anime'lerini görüntüleme
- Anime arama (LiveChart.me ve AniList)
- Başlık eşleştirme (Japonca, Romaji, İngilizce)
- Responsive tasarım
- Dark mode arayüz

## 🔧 Teknoloji Stack

### Backend
- Python 3.9+
- FastAPI
- BeautifulSoup4
- Requests
- Feedparser

### Frontend
- Next.js 16
- React 18+
- TypeScript
- Tailwind CSS

## 📝 Değişiklikler

### Yeni Özellikler
- ✅ Python backend ve Next.js frontend ayrımı
- ✅ LiveChart.me entegrasyonu (trend için)
- ✅ AniList sadece kayıt ve ilerleme takibi için kullanılıyor
- ✅ Geliştirilmiş başlık eşleştirme algoritması
- ✅ Romaji, English, Japanese başlık desteği
- ✅ Modern REST API yapısı

### Eski Özellikler (Korundu)
- ✅ Çoklu kaynak desteği (Anizle, AnimeCix, TürkAnime)
- ✅ Video oynatma ve indirme
- ✅ AniList OAuth2 entegrasyonu
- ✅ İlerleme takibi

## 🧪 Geliştirme

### Backend Geliştirme

```bash
cd backend
pip install -r requirements.txt
python server.py
```

API dokümantasyonu: `http://localhost:8000/docs`

### Frontend Geliştirme

```bash
cd frontend
npm run dev
```

## 📦 Deployment

### Backend Deployment

Backend FastAPI uygulaması herhangi bir Python hosting servisinde çalıştırılabilir:
- Heroku
- Railway
- DigitalOcean App Platform
- AWS Lambda (with Mangum)

### Frontend Deployment

Next.js uygulaması Vercel'de kolayca deploy edilebilir:
```bash
cd frontend
vercel deploy
```

## 🤝 Katkıda Bulunma

1. Fork edin
2. Feature branch oluşturun (`git checkout -b feature/amazing`)
3. Değişikliklerinizi commit edin (`git commit -m 'Add amazing feature'`)
4. Branch'i push edin (`git push origin feature/amazing`)
5. Pull Request açın

## 📄 Lisans

CC-BY-NC-ND-4.0

## 👨‍💻 Geliştiriciler

- barkeser2002
- Junicchi
