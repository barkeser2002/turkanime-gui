
<div align="center">

![TürkAnime Logo](https://i.imgur.com/Dw8sv30.png)

[![GitHub all releases](https://img.shields.io/github/downloads/barkeser2002/turkanime-gui/total?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Downloads](https://static.pepy.tech/personalized-badge/turkanime-gui?period=total&units=international_system&left_color=grey&right_color=orange&left_text=Pip%20Installs)](https://pepy.tech/project/turkanime-gui)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/barkeser2002/turkanime-gui?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Pypi version](https://img.shields.io/pypi/v/turkanime-gui?style=flat-square)](https://pypi.org/project/turkanime-gui/)

</div>

# TürkAnime GUI

**Sürüm notları:** [V10.1.0](docs/V10.1.0.md) · [V10.0.0](docs/V10.0.0.md)

TürkAnime GUI **tamamen arayüz odaklı** bir anime keşif, izleme ve indirme
uygulaması. Arayüz **PySide6 + QtWebEngine** üzerine kurulu; V10.0.0 ile
CustomTkinter yığını kaldırıldı ve tek arayüz kaldı. Terminal (CLI) sürümü
çalışmaya devam ediyor ama geliştirme masaüstü uygulamasına odaklı.

## ✨ Öne Çıkan Özellikler

- **8 kaynakta paralel arama:** TürkAnime, AnimeciX, Anizle, TRAnimeİzle,
  OpenAnime, Tranimaci, AnimeDepo ve AniList aynı anda aranır. Bunlardan
  **7'si video sunar**; AniList yalnızca meta veri ve kullanıcı listesi sağlar.
- **Alakaya göre sıralama:** Sonuçlar sorguya yakınlığa göre dizilir. "one piece"
  aramasında ilk sıra One Piece olur — "Koisuru One Piece" değil.
- **Gömülü Cloudflare atlatma:** Uzak bir FlareSolverr'a bağımlı kalmadan,
  uygulamanın içindeki Chromium (QtWebEngine) ayrı süreçte challenge çözüyor.
  FlareSolverr adresi isteyen için ayarlarda duruyor.
- **Çok kaynaklı bölüm birleştirme:** Aynı anime birden çok kaynakta varsa
  bölümler `(sezon, bölüm)` anahtarıyla tek listede birleşir.
- **Gelişmiş indirme sistemi:** Bölüm başına ilerleme çubukları, otomatik
  yeniden deneme, tek tuşla iptal, renkli durum göstergesi.
- **Tek tıkla indirme ve oynatma:** Bölümleri sıra bekletmeden indir, izlerken
  otomatik kaydet.
- **AniList entegrasyonu:** OAuth2 ile hesabına bağlan, listelerini senkron tut.
  Gizli anahtar (client secret) gerekmez — bkz. [AniList Girişi](docs/ANILIST_OAUTH.md).
- **Fansub ve kalite seçimi:** Desteklenen kaynaklardan en temiz sürümü bulur.
- **Kart tabanlı arayüz:** Hover efektli kartlar, batch rendering, poster
  galerileri.
- **Discord Rich Presence:** O anda ne izlediğini arkadaşlarınla paylaş.
- **Çoklu platform:** Windows/Linux/macOS için hazır paket, Python 3.9+ olan
  her platformdan pip ile çalıştır.
- **Testler:** 988 otomatik test (pytest + pytest-qt), ağa çıkmaz.

## 🧭 Uygulama Akışı

1. **Keşfet:** Jikan (MyAnimeList) trend ve sezon listeleri; Jikan erişilemezse
   AniList trendlerine düşülür.
2. **Ara:** 8 kaynakta paralel arama; yavaş kaynak aramayı çökertmez, zaman
   aşımına uğrayan kaynak boş döner.
3. **İndir & Oynat:** mpv entegrasyonu sayesinde indirme ve izleme tek pencerede.
4. **İlerleme Takibi:** İzlediklerin otomatik tutulur, AniList'e yansır.

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

> **İpucu:** Ayarlar → Discord Rich Presence bölümünden tek tuşla aç/kapat.
> Özellik isteğe bağlıdır; `pypresence` yoksa uygulama normal çalışmaya devam eder.

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

Arayüz paketi **zip**'tir ama içinde **tek bir çalıştırılabilir dosya** vardır
(v10.1.0'dan itibaren; `_internal/` klasörü yok). Zip'i aç, `turkanime-gui.exe`
ile başlat. 10.0.0'dan yükseltiyorsan eski kurulumun yanındaki `_internal/`
klasörü artık gereksizdir, silebilirsin.

Zip kullanılmasının sebebi dosyanın bölünmesi değil, indirme sayfasının ve
otomatik güncelleyicinin `.zip` adlarına bağlı olması.

> Burada eskiden "QtWebEngine tek dosyaya sıkıştırıldığında alt-sürecini
> bulamıyor" yazıyordu. Ölçüldü ve doğru çıkmadı: tek dosya paketinde
> `QtWebEngineProcess.exe` açılım dizininde yerinde duruyor ve arayüz açılıyor.
> Tek dosyanın gerçek bedeli başka: her açılışta ~9 sn arşiv açılımı, ve
> Cloudflare çözücü alt-süreci kendi açılımını yaptığı için her duvarda ~9 sn
> daha. Ayrıntı: [V10.1.0 sürüm notları](docs/V10.1.0.md).

### 2. PyPI Üzerinden
```bash
pip install "turkanime-gui[gui]"
```

```bash
turkanime-gui
```

```bash
turkanime-cli
```

> `[gui]` ekstrası PySide6'yı (ve opsiyonel `pypresence`'ı) kurar. Sade
> `pip install turkanime-gui` yalnızca terminal sürümünü çalıştırır.
>
> **Not (düzeltildi):** Burada eskiden "pip ile kurulan sürümde `cloudscraper`
> gelmez, Cloudflare zinciri 5 yerine 4 kademeyle çalışır" yazıyordu. Doğru
> değil: `cloudscraper` hem `pyproject.toml` hem `requirements.txt` içinde
> **zorunlu** bağımlılık. Her kurulum biçimi beş kademenin tamamını taşıyor
> (bkz. [Cloudflare Bypass Zinciri](#cloudflare-bypass-zinciri)).

### 3. Kaynak Koddan
```bash
git clone https://github.com/barkeser2002/turkanime-gui.git
```

```bash
cd turkanime-gui
```

```bash
pip install -r requirements-gui.txt
```

```bash
python -m turkanime_api.gui.qt
```

## 🚀 Kullanım

1. **İlk açılışta** ffmpeg/mpv/aria2c/yt-dlp denetlenir; eksik varsa kurulum
   sihirbazı açılır (hazır pakette hepsi gömülü gelir).
2. **TRAnimeİzle** kullanmak istiyorsan Ayarlar → TRAnimeİzle Cookie →
   **"Tarayıcıdan Al"** düğmesine bas. Uygulama içindeki tarayıcı açılır, bot
   kontrolünü çözersin, çerez kaydedilir.
3. **FlareSolverr** kullanmak istiyorsan Ayarlar → FlareSolverr URL bölümünden
   sunucu adresini gir (zorunlu değil).
4. **Keşfet veya Ara sekmesinden** anime seç.
5. **Bölümü oynat** ya da **indir**; her bölüm için ayrı ilerleme çubuğu,
   yeniden deneme ve iptal desteği mevcut.
6. **AniList'e bağlanmak** istersen Ayarlar → AniList → "AniList'e Giriş Yap";
   gizli anahtar (client secret) gerekmez, ayrıntı için
   [AniList Girişi](docs/ANILIST_OAUTH.md).

## 📺 Desteklenen Kaynaklar

### Video Kaynakları
| Kaynak | Açıklama |
|--------|----------|
| **TürkAnime** | Klasik Türk anime kaynağı (şifreli embed çözümü) |
| **AnimeciX** | Dinamik video ID, geniş fansub seçenekleri |
| **Anizle** | Geniş arşiv (`anizm.pro`). Site video.js/HLS'e geçtiği için bölüm başına sınırlı kaynak dönebiliyor |
| **TRAnimeİzle** | Cookie tabanlı oturum — Ayarlar'dan gömülü tarayıcıyla çerez alınmalı |
| **OpenAnime** | SvelteKit SSR JSON çıkarımı + CF bypass. Arama ve bölüm listesi çalışıyor; **stream uçları şu an 404 dönüyor** (bkz. [Bilinen Kısıtlar](#-bilinen-kısıtlar)) |
| **Tranimaci** | SHA-256 proof-of-work WAF + JS kapısı (QtWebEngine ile aşılır), multi-CDN mp4 |
| **AnimeDepo** | GitLab üzerinde barındırılan statik arşiv; gerçek arama ucu yok, dizin indirilip yerel fuzzy arama yapılır |

### Meta Veri ve Keşif
| Servis | Rol |
|--------|-----|
| **AniList** | Arama sonuçlarına katılır, kullanıcı listesi ve OAuth2 girişi sağlar. Video sunmaz. |
| **Jikan (MyAnimeList)** | Yalnızca Keşfet sekmesindeki trend/sezon listeleri. Arama motoru **değildir**. |

### Cloudflare Bypass Zinciri
```
1. curl_cffi      (TLS fingerprint taklidi)
2. cloudscraper   (JS Challenge çözümü — zorunlu bağımlılık, her kurulumda var)
3. FlareSolverr   (Uzak headless browser — opsiyonel, tanımlıysa)
4. QtWebEngine    (Yerel gömülü Chromium, ayrı süreçte)
5. requests       (Son çare)
```
> Zincir, HTTP 200 dönen *challenge sayfalarını* da tanır ve başarı saymaz;
> aksi hâlde ilk adımda kısa devre olup gerçek tarayıcıya hiç ulaşılmıyordu.
> Selenium/undetected-chromedriver bağımlılıkları V10.0.0 ile tamamen kaldırıldı.

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

## ⚠️ Bilinen Kısıtlar

Bunlar uygulamanın hataları değil, kaynak sitelerin getirdiği sınırlar:

| Kısıt | Ne oluyor |
|-------|-----------|
| **TRAnimeİzle çerez istiyor** | Çerez alınmadan bu kaynak bölüm döndürmez. Ayarlar → "Tarayıcıdan Al" ile bir kez alınır. |
| **OpenAnime stream 404** | Arama ve bölüm listesi çalışıyor, ama CDN uçları `not_found` dönüyor. `api.openani.me` kimlik doğrulama ("Vanguard") istiyor. Uygulama bu durumda sessiz kalmaz, sebebi yazar. |
| ~~**pip kurulumunda 4 kademeli CF zinciri**~~ | Geçersiz: `cloudscraper` zorunlu bağımlılık (`pyproject.toml`, `requirements.txt`); her kurulum 5 kademenin tamamını taşır. |
| **Anizle bölüm başına sınırlı kaynak** | Site video.js/HLS'e geçti; bazı bölümlerde tek stream dönebiliyor. |

## 🔧 Sistem Gereksinimleri

- **Python:** 3.9+ (kaynaktan/pip ile çalıştırmak için; hazır pakette gerekmez).
  Test edilen sürümler: 3.9 – 3.13.
- **FFmpeg, mpv, aria2c, yt-dlp:** Hazır Windows paketinde gömülü gelir;
  kaynaktan çalıştırıyorsan uygulama içindeki sihirbaz indirip kurar.
- **FlareSolverr:** Opsiyonel — tanımlı değilse zincir gömülü QtWebEngine'e düşer.
- **İnternet bağlantısı:** Kaynaklara erişim ve AniList senkronu için.

## 🧪 Testler

Otomatik test paketi (ağa çıkmaz, Qt offscreen koşar):

```bash
pip install -r requirements-gui.txt
```

```bash
pip install pytest pytest-qt PyYAML
```

```bash
python -m pytest tests/
```

> **`PyYAML` neden gerekli:** `tests/test_release_workflow.py` yayın
> workflow'unu ayrıştırıyor ve PyYAML yoksa `importorskip` ile **sessizce
> atlanıyor**. Kurmadan koşarsan o dosyanın tamamı hiç çalışmaz ama paket yeşil
> görünür. PyYAML hiçbir `requirements` dosyasında yer almıyor, elle kurulmalı.

Kaynak adaptörleri gerçek ağa çıkan ayrı bir betikle sınanır. Bu betik
`pytest`'e dahil **değildir**:

```bash
python tests/adapters-test-all.py
```

```bash
python tests/adapters-test-all.py --source animecix
```

```bash
python tests/adapters-test-all.py --skip-streams
```

> Betik şu an **4 kaynağı** kapsıyor: `animecix`, `anizle`, `tranime`,
> `animedepo`. OpenAnime, Tranimaci ve TürkAnime bu betikte yok.

### Test Kapsamı
| Alan | Testler |
|------|---------|
| **Arayüz (pytest-qt)** | Keşif/arama/detay/bölüm/indirme sayfaları, oynatma, izleme listesi, güncelleme servisi, gereksinim sihirbazı, Discord RPC, çerez tarayıcısı, worker havuzu |
| **Arama** | Alakaya göre sıralama, çok kaynaklı arama zaman aşımı, başlık eşleştirme |
| **Kaynaklar** | Anizle CF bypass zinciri, OpenAnime arama ve stream doğrulama, çerez yönetimi |
| **Cloudflare** | Kademe sırası, challenge tanıma, timeout davranışı, çözücü giriş noktası |
| **Çekirdek** | Bölüm birleştirme ve ayrıştırma, indirme yolu güvenliği, atomik JSON yazımı, ağ izolasyonu |
| **Yayın** | `release.yml` sürüm türetme, test kapısı, `version.json` şeması, PyPI sırrı |
| **Adaptörler (ağ, ayrı betik)** | AnimeciX, Anizle, TRAnimeİzle, AnimeDepo — arama, bölüm listesi, stream |

> **Not:** TRAnimeİzle ağ testleri geçerli bir cookie gerektirir. Cookie süresi
> dolmuşsa bu testler beklenen şekilde başarısız olur.

## 🗂️ İlgili Depolar

| Depo | Ne |
|------|-----|
| [turkanime-gui](https://github.com/barkeser2002/turkanime-gui) | Bu depo — masaüstü uygulaması, terminal sürümü ve kaynak adaptörleri |
| [turkanime-server](https://github.com/barkeser2002/turkanime-server) | Arşiv sunucusu: kaynak tarayıcısı, yayıncı ve Flask API (**private**) |

Sunucu tarafı V10.0.0 ile ayrı bir depoya taşındı. Kaynak adaptörlerini bu
depodan yeniden kullanır; ikinci bir kazıyıcı yazılmadı.

## 👨‍💻 Katkıda Bulun

- Hata bildirimi veya feature isteği için
  [Issues](https://github.com/barkeser2002/turkanime-gui/issues) sekmesini kullan.
- PR göndermeden önce kısa bir açıklama ve ekran görüntüsü eklemek incelemeyi
  hızlandırır.
- Dokümantasyon ve çeviri katkıları da memnuniyetle kabul edilir.

> Yayımlanan her dosyanın yanına `.sha256` özeti eklenir; uygulama içi
> güncelleme de indirdiği paketi aynı özetle doğrular, uyuşmazsa dosyayı siler.

## 📄 Lisans

Bu proje **[Creative Commons Attribution-NonCommercial-NoDerivatives 4.0
International](LICENSE)** (CC BY-NC-ND 4.0) ile lisanslanmıştır.

| İzin var | İzin yok |
|----------|----------|
| Paylaşmak ve kopyalamak | Ticari kullanım |
| Kaynak göstererek atıfta bulunmak | Değiştirilmiş sürüm dağıtmak |

Kullanım ve sorumluluk sınırları için [DISCLAIMER](DISCLAIMER.md) dosyasını oku.

## 📧 İletişim

Sitenizi kullanmamamı, kaldırmamı istiyorsanız veya başka talepleriniz için:
- **E-posta:** info@bariskeser.com
- **Discord:** bariskeser
