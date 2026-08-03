
<div align="center">

![TürkAnime Logo](https://i.imgur.com/Dw8sv30.png)

[![GitHub all releases](https://img.shields.io/github/downloads/barkeser2002/turkanime-gui/total?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Downloads](https://static.pepy.tech/personalized-badge/turkanime-gui?period=total&units=international_system&left_color=grey&right_color=orange&left_text=Pip%20Installs)](https://pepy.tech/project/turkanime-gui)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/barkeser2002/turkanime-gui?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Pypi version](https://img.shields.io/pypi/v/turkanime-gui?style=flat-square)](https://pypi.org/project/turkanime-gui/)

</div>

# TürkAnime GUI

**Sürüm notları:** [V10.0.0](V10.0.0.md)

TürkAnime **tamamen GUI odaklı** bir anime keşif, izleme ve indirme deneyimi
sunuyor. Arayüz **PySide6 + QtWebEngine** üzerine kurulu; V10.0.0 ile
CustomTkinter yığını kaldırıldı ve tek arayüz kaldı. Terminal (CLI) sürümü
çalışmaya devam ediyor ama geliştirme masaüstü uygulamasına odaklı.

## ✨ Öne Çıkan Özellikler

- **7 kaynak, tek arayüz:** TürkAnime, AnimeCix, Anizle, TRAnimeİzle, OpenAnime, Tranimaci ve AnimeDepo'dan paralel erişim.
- **Jikan + AniList arama:** MyAnimeList (Jikan) birincil, AniList fallback — geniş anime kataloguna erişim.
- **Gömülü Cloudflare atlatma:** Uzak bir FlareSolverr'a bağımlı kalmadan, uygulamanın içindeki Chromium (QtWebEngine) ayrı süreçte challenge çözüyor. FlareSolverr adresi isteyen için ayarlarda duruyor.
- **Hızlı stream çekme:** Paralel işleme ile 8 kat hızlı video link alma.
- **Paralel kaynak arama:** Tüm kaynaklar aynı anda aranır (ThreadPoolExecutor); yavaş kaynak aramayı çökertmez, zaman aşımına uğrayan kaynak boş döner.
- **Gelişmiş indirme sistemi:** Bölüm başına ilerleme çubukları, otomatik yeniden deneme (2 deneme), tek tuşla iptal, renkli durum göstergesi.
- **Tek tıkla indirme ve oynatma:** Bölümleri sıra bekletmeden indir, izlerken otomatik kaydet.
- **AniList entegrasyonu:** OAuth2 ile hesabına bağlan, listelerini senkron tut (1 yıllık token).
- **Fansub ve kalite seçimi:** Desteklenen kaynaklardan en temiz sürümü bulur.
- **Netflix benzeri arayüz:** Hover efektli kartlar, batch rendering, poster galerileri, akıcı animasyonlar.
- **Discord Rich Presence:** O anda ne izlediğini arkadaşlarınla paylaş.
- **TRAnimeİzle cookie desteği:** İlk açılışta otomatik cookie toplama teklifi, uygulama içine gömülü tarayıcı (QtWebEngine) ile tek tıkla cookie alma, Netscape format desteği, manuel rehber.
- **Çoklu platform:** Windows/Linux/macOS için hazır paket, Python 3.9+ olan her platformdan pip ile çalıştır.
- **Testler:** 313 otomatik test (pytest + pytest-qt) ve tüm kaynakları tek komutla sınayan adaptör betiği.

## 🧭 Uygulama Akışı

1. **Keşfet:** Jikan/AniList trend listeler ve kişisel öneriler tek ekranda.
2. **Ara:** tüm kaynaklarda paralel arama, Jikan+AniList veritabanını aynı anda gez.
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

[Releases](https://github.com/barkeser2002/turkanime-gui/releases/latest)
sayfasından platformuna uyanı indir:

| Dosya | Ne |
|-------|-----|
| `turkanime-gui-windows.zip` | Arayüz — Windows (mpv, ffmpeg, aria2c, yt-dlp gömülü) |
| `turkanime-gui-linux.zip` / `turkanime-gui-macos.zip` | Arayüz — Linux / macOS |
| `turkanime-cli-windows.exe` / `-linux` / `-macos` | Terminal sürümü (tek dosya) |
| `*.sha256` | Yayımlanan dosyanın SHA-256 özeti |

Arayüz paketi **zip**'tir (tek exe değil): QtWebEngine bir Chromium çalışma
zamanı taşıyor ve tek dosyaya sıkıştırıldığında alt-süreci bulamıyor. Zip'i bir
klasöre çıkar, `turkanime-gui.exe` ile başlat.

### 2. PyPI Üzerinden
```bash
pip install "turkanime-gui[gui]"
turkanime-gui        # arayüz
turkanime-cli        # terminal sürümü
```
> `[gui]` ekstrası PySide6'yı (ve opsiyonel `pypresence`'ı) kurar. Sade
> `pip install turkanime-gui` yalnızca CLI'yı çalıştırır.

### 3. Kaynak Koddan
```bash
git clone https://github.com/barkeser2002/turkanime-gui.git
cd turkanime-indirici
pip install -r requirements-gui.txt
python -m turkanime_api.gui.qt
```

## 🚀 Kullanım

1. **İlk açılışta** ffmpeg/mpv/aria2c/yt-dlp denetlenir; eksik varsa kurulum sihirbazı açılır (hazır pakette hepsi gömülü gelir).
2. **TRAnimeİzle** kullanmak istiyorsan ilk açılışta çıkan "Otomatik Cookie Al" teklifini kabul et — uygulama içindeki tarayıcı açılır, bot kontrolünü çöz, çerezler otomatik kaydedilir. Ayarlardan da her zaman tekrar alabilirsin.
3. **FlareSolverr** kullanmak istiyorsan Ayarlar → FlareSolverr URL bölümünden sunucu adresini gir (zorunlu değil).
4. **Keşfet veya Ara sekmesinden** anime seç.
5. **Bölümü oynat** ya da **indir**; her bölüm için ayrı ilerleme çubuğu, yeniden deneme ve iptal desteği mevcut.
6. **AniList'e bağlanmak** istersen Ayarlar → AniList → "AniList'e Giriş Yap"; gizli anahtar (client secret) gerekmez, ayrıntı için [AniList Girişi](ANILIST_OAUTH.md).

## 📺 Desteklenen Kaynaklar

### Birincil Kaynaklar
| Kaynak | Açıklama |
|--------|----------|
| **TürkAnime** | Klasik Türk anime kaynağı (şifreli embed çözümü) |
| **AnimeCix** | Dinamik video ID, geniş fansub seçenekleri |
| **Anizle** | Geniş arşiv; site video.js/HLS'e geçtiği için şu an bölüm başına sınırlı kaynak |
| **TRAnimeİzle** | Cookie tabanlı oturum — Ayarlar'dan gömülü tarayıcıyla çerez alınmalı |
| **OpenAnime** | SvelteKit SSR JSON çıkarımı + CF bypass |
| **Tranimaci** | SHA-256 proof-of-work WAF + JS kapısı (QtWebEngine ile aşılır), multi-CDN mp4 |
| **AnimeDepo** | GitLab üzerinde barındırılan statik arşiv; gerçek arama ucu yok, dizin indirilip yerel fuzzy arama yapılır |

### Arama Motorları
| Motor | Rol |
|-------|-----|
| **Jikan (MAL)** | Birincil arama — MyAnimeList veritabanı |
| **AniList** | Fallback arama + kullanıcı listesi + OAuth2 (secret gerektirmez — bkz. [AniList Girişi](ANILIST_OAUTH.md)) |

### Cloudflare Bypass Zinciri
```
1. curl_cffi      (TLS fingerprint taklidi)
2. cloudscraper   (JS Challenge çözümü)
3. FlareSolverr   (Uzak headless browser — opsiyonel, tanımlıysa)
4. QtWebEngine    (Yerel gömülü Chromium, ayrı süreçte)
5. requests       (Fallback)
```
> Not: Zincir, HTTP 200 dönen *challenge sayfalarını* da tanır ve başarı
> saymaz; aksi hâlde ilk adımda kısa devre olup gerçek tarayıcıya hiç
> ulaşılmıyordu. Selenium/undetected-chromedriver bağımlılıkları V10.0.0 ile
> tamamen kaldırıldı.

### Video Sunucuları

TürkAnime embed'lerinde desteklenen oynatıcılar (öncelik sırasıyla,
`turkanime_api/objects.py::SUPPORTED`):

```
Yandisk  Alucard  GDrive  Mail  PixelDrain  Amaterasu  HDVID
Odnoklassniki  Dailymotion  Sibnet  VK  Vidmoly  YourUpload
Sendvid  Myvi  Uqload
```
> MP4upload listeden çıkarıldı: çözümlenmiş gibi görünüp oynatılamayan
> bağlantılar üretiyordu. Diğer kaynaklar (Anizle, Tranimaci, AnimeDepo,
> OpenAnime) doğrudan mp4/HLS bağlantısı döndürür, bu listeden geçmez.

## 🔧 Sistem Gereksinimleri

- **Python:** 3.9+ (kaynaktan/pip ile çalıştırmak için; hazır pakette gerekmez)
- **FFmpeg, mpv, aria2c, yt-dlp:** Hazır Windows paketinde gömülü gelir; kaynaktan çalıştırıyorsan uygulama içindeki sihirbaz indirip kurar.
- **FlareSolverr:** Opsiyonel — tanımlı değilse zincir gömülü QtWebEngine'e düşer.
- **İnternet bağlantısı:** Kaynaklara erişim ve AniList senkronu için.

## 🧪 Testler

Otomatik test paketi (ağa çıkmaz, Qt offscreen koşar):

```bash
pip install -r requirements-gui.txt
pip install pytest pytest-qt
python -m pytest tests/
```

Kaynak adaptörleri gerçek ağa çıkan ayrı bir betikle sınanır:

```bash
# Tüm kaynaklar
python tests/adapters-test-all.py

# Tek kaynak
python tests/adapters-test-all.py --source animecix
python tests/adapters-test-all.py --source anizle
python tests/adapters-test-all.py --source tranime
python tests/adapters-test-all.py --source animedepo

# Stream testlerini atla (hızlı) / detaylı çıktı / JSON
python tests/adapters-test-all.py --skip-streams
python tests/adapters-test-all.py --verbose
python tests/adapters-test-all.py --json
```

### Test Kapsamı
| Alan | Testler |
|------|--------|
| **Arayüz (pytest-qt)** | Keşif/arama/detay/bölüm/indirme sayfaları, oynatma, izleme listesi, güncelleme servisi, gereksinim sihirbazı, Discord RPC, çerez tarayıcısı, worker havuzu |
| **Çekirdek** | Bölüm birleştirme, başlık eşleştirme, Jikan istemcisi, kaynak köprüsü, sürüm/`version.json` şeması |
| **Adaptörler (ağ)** | AnimeCix, Anizle, TRAnimeİzle, AnimeDepo — arama, bölüm listesi, stream |

> **Not:** TRAnimeİzle doğrudan arama ve stream testleri geçerli bir cookie gerektirir. Cookie süresi dolmuşsa bu testler beklenen şekilde başarısız olur.

## 👨‍💻 Katkıda Bulun

- Hata bildirimi veya feature isteği için [Issues](https://github.com/barkeser2002/turkanime-gui/issues) sekmesini kullan.
- PR göndermeden önce kısa bir açıklama ve ekran görüntüsü eklemek incelemeyi hızlandırır.
- Dokümantasyon ve çeviri katkıları da memnuniyetle kabul edilir.

> Yayımlanan her dosyanın yanına `.sha256` özeti eklenir; uygulama içi
> güncelleme de indirdiği paketi aynı özetle doğrular, uyuşmazsa dosyayı siler.

## 📧 İletişim

Eğer sitenizi kullanmamamı, kaldırmamı veya istekleriniz için bana ulaşın:
- **E-mail:** info@bariskeser.com
- **Discord:** bariskeser
