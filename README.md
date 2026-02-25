
<div align="center">

![TürkAnime Logo](https://i.imgur.com/Dw8sv30.png)

[![GitHub all releases](https://img.shields.io/github/downloads/barkeser2002/turkanime-gui/total?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Downloads](https://static.pepy.tech/personalized-badge/turkanime-gui?period=total&units=international_system&left_color=grey&right_color=orange&left_text=Pip%20Installs)](https://pepy.tech/project/turkanime-gui)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/barkeser2002/turkanime-gui?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest/download/turkanime-gui-windows.exe)
[![Pypi version](https://img.shields.io/pypi/v/turkanime-gui?style=flat-square)](https://pypi.org/project/turkanime-gui/)

</div>

# TürkAnime GUI

TürkAnime artık **tamamen GUI odaklı** bir anime keşif, izleme ve indirme deneyimi sunuyor. Terminal (CLI) sürümü destek dışı bırakıldı; tüm geliştirme modern masaüstü uygulamasına odaklanıyor.

## ✨ Öne Çıkan Özellikler

- **4 kaynak, tek arayüz:** Anizle, AnimeCix, TürkAnime ve TRAnimeİzle'den paralel erişim.
- **Jikan + AniList arama:** MyAnimeList (Jikan) birincil, AniList fallback — geniş anime kataloguna erişim.
- **FlareSolverr CF bypass:** Cloudflare korumalı sitelere uzak headless browser ile otomatik erişim. Sunucu adresi ayarlardan özelleştirilebilir.
- **Hızlı stream çekme:** Paralel işleme ile 8 kat hızlı video link alma.
- **Paralel kaynak arama:** Tüm kaynaklar aynı anda aranır (ThreadPoolExecutor).
- **Gelişmiş indirme sistemi:** Bölüm başına ilerleme çubukları, otomatik yeniden deneme (2 deneme), tek tuşla iptal, renkli durum göstergesi.
- **Tek tıkla indirme ve oynatma:** Bölümleri sıra bekletmeden indir, izlerken otomatik kaydet.
- **AniList entegrasyonu:** OAuth2 ile hesabına bağlan, listelerini senkron tut (1 yıllık token).
- **Fansub ve kalite seçimi:** Desteklenen kaynaklardan en temiz sürümü bulur.
- **Netflix benzeri arayüz:** Hover efektli kartlar, batch rendering, poster galerileri, akıcı animasyonlar.
- **Discord Rich Presence:** O anda ne izlediğini arkadaşlarınla paylaş.
- **TRAnimeİzle cookie desteği:** İlk açılışta otomatik cookie toplama teklifi, entegre tarayıcı ile tek tıkla cookie alma (Selenium), Netscape format desteği, manuel rehber.
- **Çoklu platform:** Windows için hazır paket, Python 3.9+ olan her platformdan pip ile çalıştır.
- **Adaptör testleri:** Tüm kaynakları tek komutla test eden kapsamlı test suite'i.

## 🧭 Uygulama Akışı

1. **Keşfet:** Jikan/AniList trend listeler ve kişisel öneriler tek ekranda.
2. **Ara:** 4 kaynakta paralel arama, Jikan+AniList veritabanını aynı anda gez.
3. **İndir & Oynat:** mpv entegrasyonu sayesinde indirme ve izleme tek pencerede.
4. **İlerleme Takibi:** İzlediklerin otomatik tutulur, AniList'e anında yansır.

## 📺 Ekran Görüntüleri

### Anasayfa Ekranı
![anasayfa.png](https://i.imgur.com/Mh353OU.png)

### Anime Ekranı
![animesayfası.png](https://i.imgur.com/9D4yUdn.png)

## 🎮 Discord Rich Presence

TürkAnime GUI, Discord profilinde canlı durum gösterebilir:

- Ana sayfa gezinme
- Trend veya arama ekranları
- İndirme süreci
- İzlenilen anime ve bölüm

> **İpucu:** Ayarlar → Discord Rich Presence bölümünden tek tuşla aç/kapat. Özellik isteğe bağlıdır; `pypresence` yoksa uygulama normal çalışmaya devam eder.

## 📥 Kurulum

### 1. Hazır Paket (Önerilen)
- [Releases](https://github.com/barkeser2002/turkanime-gui/releases/latest) sayfasından en güncel `.exe` dosyasını indir.
- Çalıştır ve kurulum sihirbazını tamamla.

### 2. PyPI Üzerinden
```bash
pip install turkanime-gui
turkanime-gui
&
turkanime-cli
```

### 3. Kaynak Koddan
```bash
git clone https://github.com/barkeser2002/turkanime-gui.git
cd turkanime-indirici
pip install -r requirements-gui.txt
python -m turkanime_api.gui.main
```

## 🚀 Kullanım

1. **İlk açılışta** ffmpeg/mpv bin klasörü otomatik hazırlanır.
2. **TRAnimeİzle** kullanmak istiyorsan ilk açılışta çıkan "Otomatik Cookie Al" teklifini kabul et — tarayıcı açılır, captcha çöz, cookie'ler otomatik kaydedilir. Ayarlardan da her zaman tekrar alabilirsin.
3. **FlareSolverr** kullanmak istiyorsan Ayarlar → FlareSolverr URL bölümünden sunucu adresini gir.
4. **Keşfet veya Ara sekmesinden** anime seç.
5. **Bölümü oynat** ya da **indir**; her bölüm için ayrı ilerleme çubuğu, yeniden deneme ve iptal desteği mevcut.

## 📺 Desteklenen Kaynaklar

### Birincil Kaynaklar
| Kaynak | Açıklama |
|--------|----------|
| **Anizle** | 4500+ anime, paralel stream çekme, HLS desteği, FirePlayer pipeline |
| **AnimeCix** | Dinamik video ID, geniş fansub seçenekleri |
| **TürkAnime** | Klasik Türk anime kaynağı |
| **TRAnimeİzle** | Cookie tabanlı oturum, fuzzy + doğrudan arama, geniş arşiv |

### Arama Motorları
| Motor | Rol |
|-------|-----|
| **Jikan (MAL)** | Birincil arama — MyAnimeList veritabanı |
| **AniList** | Fallback arama + kullanıcı listesi + OAuth2 |

### Cloudflare Bypass Zinciri
```
1. curl_cffi      (TLS fingerprint taklidi)
2. cloudscraper   (JS Challenge çözümü)
3. FlareSolverr   (Uzak headless browser)
4. undetected-chromedriver (Selenium bypass)
5. requests       (Fallback)
```

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
- **FlareSolverr:** Opsiyonel — varsayılan sunucu adresi ayarlardan değiştirilebilir.
- **İnternet bağlantısı:** Kaynaklara erişim ve AniList senkronu için.

## 🧪 Testler

Tüm kaynak adaptörleri tek komutla test edilebilir:

```bash
# Tüm testler
python tests/adapters-test-all.py

# Tek kaynak
python tests/adapters-test-all.py --source animecix
python tests/adapters-test-all.py --source anizle
python tests/adapters-test-all.py --source tranime

# Stream testlerini atla (hızlı)
python tests/adapters-test-all.py --skip-streams

# Detaylı çıktı
python tests/adapters-test-all.py --verbose

# JSON formatında
python tests/adapters-test-all.py --json
```

### Test Kapsamı
| Kaynak | Testler |
|--------|--------|
| **Genel** | Import kontrolü, curl_cffi, Provider registry |
| **AnimeCix** | Arama, sezon/bölüm listeleme, video stream |
| **Anizle** | Veritabanı, arama, bölüm, URL pattern, translator, video pipeline |
| **TRAnimeİzle** | Cookie, harf/fuzzy/doğrudan arama, anime/bölüm detay, fansub, iframe |

> **Not:** TRAnimeİzle doğrudan arama ve stream testleri geçerli bir cookie gerektirir. Cookie süresi dolmuşsa bu testler beklenen şekilde başarısız olur.

## 👨‍💻 Katkıda Bulun

- Hata bildirimi veya feature isteği için [Issues](https://github.com/barkeser2002/turkanime-gui/issues) sekmesini kullan.
- PR göndermeden önce kısa bir açıklama ve ekran görüntüsü eklemek incelemeyi hızlandırır.
- Dokümantasyon ve çeviri katkıları da memnuniyetle kabul edilir.

> CI yayınlarında `.md5` dosyaları otomatik eklenir.

## 📧 İletişim

Eğer sitenizi kullanmamamı, kaldırmamı veya istekleriniz için bana ulaşın:
- **E-mail:** info@bariskeser.com
- **Discord:** bariskeser



