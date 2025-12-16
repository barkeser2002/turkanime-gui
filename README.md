
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

- **Çoklu kaynak desteği:** Anizle, AnimeCix ve TürkAnime'den tek arayüzle erişim.
- **Hızlı stream çekme:** Paralel işleme ile 8 kat hızlı video link alma.
- **Tek tıkla indirme ve oynatma:** Bölümleri sıra bekletmeden indir, izlerken otomatik kaydet.
- **AniList entegrasyonu:** OAuth2 ile hesabına bağlan, listelerini senkron tut.
- **Fansub ve kalite seçimi:** Desteklenen kaynaklardan en temiz sürümü bulur.
- **Netflix benzeri arayüz:** Hover efektli kartlar, poster galerileri, akıcı animasyonlar.
- **Discord Rich Presence:** O anda ne izlediğini arkadaşlarınla paylaş.
- **Çoklu platform:** Windows için hazır paket, Python 3.9+ olan her platformdan pip ile çalıştır.

## 🧭 Uygulama Akışı

1. **Keşfet:** Trend listeler ve kişisel öneriler tek ekranda.
2. **Ara:** Yerel kaynaklarla AniList veritabanını aynı anda gez.
3. **İndir & Oynat:** mpv entegrasyonu sayesinde indirme ve izleme tek pencerede.
4. **İlerleme Takibi:** İzlediklerin otomatik tutulur, AniList'e anında yansır.

## 📺 Ekran Görüntüleri

### Anasayfa Ekranı
![anasayfa.png](https://i.imgur.com/Mh353OU.png)

### Anime Ekranı
![animesayfası.png](https://i.imgur.com/9D4yUdn.png)

## � Discord Rich Presence

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



