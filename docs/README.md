
<div align="center">

![TürkAnime Logo](https://i.imgur.com/Dw8sv30.png)

[![GitHub all releases](https://img.shields.io/github/downloads/barkeser2002/turkanime-gui/total?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Downloads](https://static.pepy.tech/personalized-badge/turkanime-gui?period=total&units=international_system&left_color=grey&right_color=orange&left_text=Pip%20Installs)](https://pepy.tech/project/turkanime-gui)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/barkeser2002/turkanime-gui?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Pypi version](https://img.shields.io/pypi/v/turkanime-gui?style=flat-square)](https://pypi.org/project/turkanime-gui/)

</div>

# TürkAnime GUI

**Sürüm notları:** [V10.2.0](V10.2.0.md) · [V10.1.0](V10.1.0.md) · [V10.0.0](V10.0.0.md)

TürkAnime GUI **tamamen arayüz odaklı** bir anime keşif, izleme ve indirme
uygulaması. Arayüz **PySide6 + QtWebEngine** üzerine kurulu; V10.0.0 ile
CustomTkinter yığını kaldırıldı ve tek arayüz kaldı. Terminal (CLI) sürümü
çalışmaya devam ediyor ama geliştirme masaüstü uygulamasına odaklı.

## ✨ Öne Çıkan Özellikler

- **13 kaynakta paralel arama:** TürkAnime (arşiv), AnimeciX, Anizle,
  TRAnimeİzle, OpenAnime, Tranimaci, Animexe, AnimPow, Deokwave, Asya
  Animeleri, Animeler.pw, One Pace TR ve AniList aynı anda aranır. Bunlardan
  **12'si video sunar**; AniList yalnızca meta veri ve kullanıcı listesi sağlar.
  turkanime.tv kapandı: TürkAnime kaynağı artık sitenin statik arşivi ve
  yerel kopyadan **ağsız** aranır. Kaynak listesi tek yerde tutulur
  (`turkanime_api/sources/kayit.py`).
- **Artımlı arama:** Her kaynağın sonucu o kaynak bittiği an görünür; yeni
  sorgu süren aramayı beklemez.
- **Çevrimdışı TürkAnime arşivi:** Ayarlar'dan tek tıkla indirilir
  (~230 MB); sonra TürkAnime araması ve bölüm listeleri internetsiz çalışır
  (bkz. [Çevrimdışı Arşiv](#-çevrimdışı-arşiv-türkanime)). Arşiv kaydında
  detay sayfası Türkçe özeti, türü, stüdyoyu ve puanı gösterir.
- **Alakaya göre sıralama:** Sonuçlar sorguya yakınlığa göre dizilir. "one piece"
  aramasında ilk sıra One Piece olur — "Koisuru One Piece" değil. Otomatik
  eşleşme eşiklidir (0,95): eşiği geçen aday yoksa kaynak bağlanmaz, eşleştirme
  penceresi açılır.
- **Yerel kitaplık:** AniList hesabı olmadan "Kitaplığım" → İzlemeye devam et,
  Favoriler, Geçmiş.
- **Oynatma:** Video oynatılamazsa sıradaki aday denenir ve bölüm "izlendi"
  sayılmaz; kaldığın yerden devam, "Sıradaki" bölüm, bölüm bitince izleme
  ilerlemesi kendiliğinden yazılır. İndirilmiş bölüm ağsız oynar.
- **Gömülü Cloudflare atlatma:** Uygulamanın içindeki Chromium (QtWebEngine)
  ayrı süreçte challenge çözüyor; uzak bir FlareSolverr olmadan da çalışır.
  **Dikkat:** yine de kutudan çıktığı hâlde `flaresolverr_url` ayarı BOŞ
  DEĞİL, projenin sunucusu (`node-kyb.bariskeser.com:8191`) yazılı gelir ve
  zincirin 3. kademesi oraya uğrar. İstemiyorsanız Ayarlar → FlareSolverr
  URL alanını boşaltın; zincir gömülü çözücüyle çalışmaya devam eder.
- **Çok kaynaklı bölüm birleştirme:** Aynı anime birden çok kaynakta varsa
  bölümler `(sezon, bölüm)` anahtarıyla tek listede birleşir.
- **Gelişmiş indirme sistemi:** Bölüm başına ilerleme çubukları, başka adayla
  yeniden deneme, tek tuşla iptal. Kuyruk diske yazılır ve açılışta geri
  gelir; Duraklat/Devam et, "Tümünü Duraklat". Başarısız indirme "tamamlandı"
  sayılmaz (dosya diskte doğrulanır), aynı bölüm iki kez kuyruğa girmez. Biten
  satırda "Oynat" ve "Klasörü Aç"; kuyruk bitince masaüstü bildirimi.
- **Tek tıkla indirme ve oynatma:** Bölümleri sıra bekletmeden indir, izlerken
  kaydet ("İzlerken kaydet"). Toplu seçim aralıkla ("1-12, 20-"),
  izlenmemişler, indirilmemişler ya da Shift+tık.
- **Hata sebebi:** Oynatma, indirme ve arama hataları "çalışmıyor" yerine
  sebebi söyler (çerez süresi, Cloudflare engeli, 403/404/429, zaman aşımı,
  disk dolu…).
- **AniList entegrasyonu:** OAuth2 ile hesabına bağlan, listelerini senkron tut.
  Gizli anahtar (client secret) gerekmez — bkz. [AniList Girişi](ANILIST_OAUTH.md).
- **Fansub ve kalite seçimi:** Desteklenen kaynaklardan en temiz sürümü bulur.
  "Fansub'u kendim seçeyim" açıksa birden çok grup olan bölümde sorar; seçim
  seri için hatırlanır.
- **Kart tabanlı arayüz:** Hover efektli kartlar, batch rendering, poster
  galerileri.
- **Discord Rich Presence:** O anda ne izlediğini arkadaşlarınla paylaş.
- **Çoklu platform:** Windows/Linux/macOS için hazır paket, Python 3.9+ olan
  her platformdan pip ile çalıştır.
- **Terminal sürümü tkinter istemez:** klasör seçici yoksa yol terminalden
  sorulur; kayıtlı TRAnimeİzle çerezi ve OpenAnime jetonları CLI'da da yüklenir.
- **Testler:** 1.936 otomatik test (pytest + pytest-qt), ağa çıkmaz.

## 🧭 Uygulama Akışı

1. **Keşfet:** Jikan (MyAnimeList) trend ve sezon listeleri; Jikan erişilemezse
   AniList trendlerine düşülür.
2. **Ara:** 13 kaynakta paralel arama; sonuçlar kaynak kaynak gelir, yavaş
   kaynak aramayı çökertmez, zaman aşımına uğrayan kaynak adıyla yazılır.
3. **İndir & Oynat:** mpv entegrasyonu sayesinde indirme ve izleme tek pencerede.
4. **İlerleme Takibi:** İzlediklerin otomatik tutulur, "Kitaplığım"da görünür
   ve AniList'e bağlıysan oraya yansır.

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
> daha. Ayrıntı: [V10.1.0 sürüm notları](V10.1.0.md).

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
> **Cloudflare zinciri kurulum biçimine göre değişir.** Hazır paket, kaynak
> koddan kurulum (`requirements-gui.txt`) ve `pip install "turkanime-gui[gui]"`
> beş kademeyle çalışır. Sade `pip install turkanime-gui` PySide6 kurmaz, yani
> QtWebEngine kademesi de yoktur: zincir 4 kademe. Her durumda FlareSolverr
> kademesi yalnızca `flaresolverr_url` doluyken vardır
> (bkz. [Cloudflare Bypass Zinciri](#cloudflare-bypass-zinciri)).
>
> **Not (düzeltildi):** Burada önce "pip'te `cloudscraper` gelmez", sonra "her
> kurulum beş kademeyi de taşır" yazıyordu; ikisi de yanlıştı. `cloudscraper`
> zorunlu bağımlılık, her kurulumda var; sade pip kurulumunda eksik olan
> QtWebEngine.

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
2. **TürkAnime'yi internetsiz** kullanmak istiyorsan Ayarlar →
   **Çevrimdışı arşiv (TürkAnime)** → **"Tüm arşivi indir (~230 MB)"**.
   Ayrıntı: [Çevrimdışı Arşiv](#-çevrimdışı-arşiv-türkanime).
3. **TRAnimeİzle** kullanmak istiyorsan Ayarlar → TRAnimeİzle Cookie →
   **"Tarayıcıdan Al"** düğmesine bas. Uygulama içindeki tarayıcı açılır, bot
   kontrolünü çözersin, çerez kaydedilir.
   **OpenAnime** akışları 404 dönüyorsa Ayarlar → OpenAnime oturumu →
   Token / Refresh Token alanlarına tarayıcındaki `openani.me` çerezlerini gir
   (isteğe bağlı). Diğer kaynaklar giriş istemez.
4. **FlareSolverr** kullanmak istiyorsan Ayarlar → FlareSolverr URL bölümünden
   sunucu adresini gir (zorunlu değil).
5. **Keşfet veya Ara sekmesinden** anime seç.
6. **Bölümü oynat** ya da **indir**; her bölüm için ayrı ilerleme çubuğu,
   yeniden deneme ve iptal desteği mevcut.
7. **AniList'e bağlanmak** istersen Ayarlar → AniList → "AniList'e Giriş Yap";
   gizli anahtar (client secret) gerekmez, ayrıntı için
   [AniList Girişi](ANILIST_OAUTH.md).

## 📦 Çevrimdışı Arşiv (TürkAnime)

turkanime.tv kapandı (sitenin görselleri bile 503 dönüyor). Sitenin anime,
bölüm ve video kayıtları [AnimeDepo](https://gitlab.com/AnimeDepo/animedepo)
adlı statik JSON arşivinde yaşıyor ve uygulamadaki **"TürkAnime (arşiv)"**
kaynağı artık bu arşiv: 6.098 anime, ~83 bin dosya. Arama, bölüm listesi ve
video bağlantıları arşivden okunur; videoların kendisi yine üçüncü parti
sunuculardan (ok.ru, Sibnet, Mail.ru, Google Drive…) gelir, yani izlemek ve
indirmek için internet gerekir.

Arşiv bu depoda [`arsiv/`](../arsiv/README.md) altında durur (GitLab'daki
AnimeDepo'nun `790d9e8` commit'inin birebir kopyası). **Hazır paket ve pip
kurulumu arşivi içermez;** internetsiz kullanmak için Ayarlar'dan indirin
(aşağıda), indirmezseniz uzak aynalardan okunur.

**Arşiv nereden okunur?** Sırayla, içinde okunabilir bir `dizin.json` olan
ilk konum kullanılır; geçersiz klasör atlanır ve Ayarlar'da uyarı çıkar:

1. `TURKANIME_ARSIV_DIZIN` ortam değişkeni, sonra Ayarlar'da **"Klasör seç…"**
   ile gösterilen klasör
2. Ayarlar'dan indirilen tam arşiv: `<veri kökü>/cevrimdisi_arsiv`
3. Depodan çalıştırılıyorsa depodaki [`arsiv/`](../arsiv/README.md) aynası
4. Uzak aynalar: varsa özel adres (`TURKANIME_ARSIV_URL`), sonra GitLab,
   olmazsa bu deponun GitHub kopyası. Gelen her dosya
   `<veri kökü>/arsiv_onbellek` altında saklanır; aynalar düşerse oradan okunur.
   **GitHub aynası bu deponun `main` dalını okur** (10.2.0 ile birleşti,
   çalışıyor); GitLab düşerse yedek odur.
   Tek bir aynanın 404'ü "anime yok" sayılmaz, yalnızca bütün aynalarınki.

"Veri kökü" `~/Turkanime` klasörü; uygulama bir git deposunun içinden
çalıştırılıyorsa depo kökü (indirilenler bu yüzden `arsiv/` değil
`cevrimdisi_arsiv/` adıyla durur ve `.gitignore`'dadır).

**Ayarlar'dan indirme:** Ayarlar → **Çevrimdışı arşiv (TürkAnime)** bölümü
etkin konumu, yolunu ya da adresini, anime sayısını ve dizinin son güncelleme
tarihini gösterir. Bu bilgi sayfa açılınca arka planda okunur; ağa çıkılmaz.

- **"Tüm arşivi indir (~230 MB)"** arşivin tamamını indirir (açılınca
  ~0,5 GB). İlerleme çubuğu ve **"İptal"** düğmesi var; GitLab'a
  ulaşılamazsa GitHub paketine geçilir. İndirilmiş bir kopya varsa düğmenin
  adı **"Arşivi güncelle"** olur: eski kopya, yenisi doğrulanıp yerine konana
  kadar silinmez; iptal ya da hata olursa yerinde kalır. İptal bağlanırken de
  hemen işler. Disk dolarsa ya da klasöre yazılamazsa GitHub'a geçilmez
  (aynı diske aynı boyutta paket inecekti); hata diski anlatır.
  `cevrimdisi_arsiv` başka bir diske sembolik bağsa arşiv bağın gösterdiği
  yere kurulur, bağ korunur. Eski kopya silinemezse (kilitli dosya) gizli
  `.cevrimdisi_arsiv-eski-*` klasörü burada uyarı olarak görünür.
- **"Klasör seç…"** elinizdeki bir kopyayı gösterir (içinde `dizin.json`
  olmalı, seçerken denetlenir). **"Varsayılana dön"** bu seçimi unutur.
- **"İndirilen arşivi sil"** onay sorar ve yalnızca `cevrimdisi_arsiv`
  klasörünü siler; seçtiğiniz klasöre ve depodaki `arsiv/`'e dokunmaz.

Arşiv hiçbir yerden okunamazsa (yerel kopya yok, aynalar yanıt vermiyor,
önbellek boş) arama "bulunamadı" demez: arama sayfası ve CLI, TürkAnime'nin
aranamadığını sebebiyle söyler.

Arşivdeki video kayıtlarının yaklaşık dörtte biri yalnızca turkanime.tv'nin
kendi oynatıcısıyla açılabiliyordu; site kapandığı için bunlar ve `DEAD_`
işaretli oynatıcılar atlanır. Arşivde İngilizce adlar yok, romaji ile arayın
("attack on titan" değil "shingeki no kyojin").

## 📺 Desteklenen Kaynaklar

### Video Kaynakları
| Kaynak | Açıklama |
|--------|----------|
| **AnimeciX** | Dinamik video ID, geniş fansub seçenekleri |
| **Anizle** | Geniş arşiv (`anizm.pro`). Site video.js/HLS'e geçtiği için bölüm başına sınırlı kaynak dönebiliyor |
| **TRAnimeİzle** | Cookie tabanlı oturum — Ayarlar'dan gömülü tarayıcıyla çerez alınmalı |
| **OpenAnime** | SvelteKit SSR JSON çıkarımı + CF bypass. Arama ve bölüm listesi çalışıyor; **stream uçları jetonsuz 404 dönebiliyor** — Ayarlar → OpenAnime oturumu'na `openani.me` çerezleri (isteğe bağlı) (bkz. [Bilinen Kısıtlar](#-bilinen-kısıtlar)) |
| **Tranimaci** | SHA-256 proof-of-work WAF + JS kapısı (QtWebEngine ile aşılır), multi-CDN mp4 |
| **TürkAnime (arşiv)** | turkanime.tv kapandı; kaynak artık sitenin statik JSON arşivi (AnimeDepo). Önce yerel kopyadan okunur (indirilen tam arşiv ya da depodaki [`arsiv/`](../arsiv/README.md)), yoksa GitLab → GitHub aynalarından. Gerçek arama ucu yok; dizin üzerinde yerel arama yapılır (aksan/noktalamadan bağımsız, yazım hatasına toleranslı, "Şingeki" → Shingeki). Arşivde İngilizce adlar yok, romaji ile arayın. Eskiden ayrı listelenen "AnimeDepo" kaynağı bununla birleşti; eski ad hâlâ tanınır |
| **Animexe** | `animexe.com`; fansub akışları, giriş gerekmez. Ücretli abonelik servisi **Anizium**'un aktarımları bilerek alınmaz; yalnızca onları taşıyan bölümlerde (canlı kontrolde One Piece) akış dönmez. Ölü CDN'ler önceden elenir |
| **AnimPow** | `animpow.com`; Türk fansub gruplarını toplayan site, iki arka uç (şifreli eski API + QuadroGG HLS) birleştirilir. Türkçe adla arama QuadroGG'de çalışır ("iblis" → Demon Slayer) |
| **Deokwave** | `deokwave.com`; ~3.100 başlık, bölüm başına çoğu zaman birden çok fansub, altyazısı gömülü MP4 (1080/720/480p). Art arda isteklerde site 1–3 dk engellediği için istekler seyreltilir |
| **Asya Animeleri** | `asyaanimeleri.top`; donghua (Çin animasyonu) ağırlıklı, Japon anime listesi kısmi |
| **Animeler.pw** | Eski animeler.me; anime + donghua, çok sayıda fansub ve oynatıcı. Akış listesi yavaş gelir (site 12–30 sn düşünüyor) |
| **One Pace TR** | `onepacetr.net`; One Pace (One Piece'in dolgusuz kurgusu) Türkçe altyazılı, 35 ark / 443 bölüm. Google Drive ve Sibnet |

### Meta Veri ve Keşif
| Servis | Rol |
|--------|-----|
| **AniList** | Arama sonuçlarına katılır, kullanıcı listesi ve OAuth2 girişi sağlar. Video sunmaz. |
| **Jikan (MyAnimeList)** | Yalnızca Keşfet sekmesindeki trend/sezon listeleri. Arama motoru **değildir**. |

### Cloudflare Bypass Zinciri
```
1. curl_cffi      (TLS fingerprint taklidi)
2. cloudscraper   (JS Challenge çözümü — zorunlu bağımlılık, her kurulumda var)
3. FlareSolverr   (Uzak headless browser — VARSAYILAN OLARAK DOLU gelir)
4. QtWebEngine    (Yerel gömülü Chromium, ayrı süreçte — yalnızca [gui] kurulumlarında)
5. requests       (Son çare)
```
> Zincir, HTTP 200 dönen *challenge sayfalarını* da tanır ve başarı saymaz;
> aksi hâlde ilk adımda kısa devre olup gerçek tarayıcıya hiç ulaşılmıyordu.
> Selenium/undetected-chromedriver bağımlılıkları V10.0.0 ile tamamen kaldırıldı.

### Video Sunucuları

TürkAnime arşivindeki bölüm kayıtlarının oynatıcıları (öncelik sırasıyla,
`turkanime_api/common/oynatici_onceligi.py`):

```
Yandisk  Alucard  GDrive  Mail  PixelDrain  Amaterasu  HDVID
Odnoklassniki  Dailymotion  Sibnet  VK  Vidmoly  YourUpload
Sendvid  Myvi  Uqload
```
> MP4upload listeden çıkarıldı: çözümlenmiş gibi görünüp oynatılamayan
> bağlantılar üretiyordu. Anizle, Tranimaci ve OpenAnime doğrudan mp4/HLS
> bağlantısı döndürür, bu listeden geçmez. Gömülü oynatıcı döndüren Asya
> Animeleri ve One Pace TR aynı listeyle sıralar; AnimPow ve Animeler.pw kendi
> sitelerinde ölçülen çalışma oranına göre sıralar.

## ⚠️ Bilinen Kısıtlar

Bunlar uygulamanın hataları değil, kaynak sitelerin getirdiği sınırlar:

| Kısıt | Ne oluyor |
|-------|-----------|
| **TürkAnime kapandı** | Kaynak artık sitenin arşivi: içerik sitenin kapanmadan önceki kaydı (dizinin tarihi Ayarlar'da görünür). Video kayıtlarının yaklaşık dörtte biri turkanime.tv'nin kendi oynatıcısına bağlıydı ve oynatılamıyor; arama yalnızca romaji adlarla bulur. |
| **TRAnimeİzle çerez istiyor** | Çerez alınmadan bu kaynak bölüm döndürmez. Ayarlar → "Tarayıcıdan Al" ile bir kez alınır. |
| **TürkAnime arşivinde bir dosya adı** | `One Piece Movie 6: …` adındaki `:` Windows'ta geçersiz; aynada `%3A` ile duruyor (`:` → `%3A`). İstemci özgün adla da buluyor; içerik aynı. |
| **OpenAnime stream 404** | Arama ve bölüm listesi çalışıyor, ama CDN uçları `not_found` dönüyor. `api.openani.me` kimlik doğrulama ("Vanguard") istiyor; Ayarlar → OpenAnime oturumu'na jeton girilebilir. Uygulama bu durumda sessiz kalmaz, sebebi yazar. |
| **Animexe'de Anizium aktarımları yok** | Ücretli servisin aktarımları bilerek alınmıyor; bir başlıkta yalnızca onlar varsa Animexe akış döndürmez. |
| **Animeler.pw yavaş** | Sitenin bölüm sayfası sunucuda 12–30 sn düşünüyor. Mugen HLS imzası isteği yapan IP'ye bağlı; çıkış IP'si değişen ağlarda 403 alınabilir. |
| **Deokwave engeli** | Site art arda isteklerde 1–3 dk 403 veriyor; uygulama istekleri seyreltiyor, kaynak bu yüzden yavaş. |
| **One Pace TR API anahtarı** | Anahtar sitenin JS paketinden çalışma anında okunuyor; site yapısını değiştirirse kaynak kırılır. |
| **Sade pip kurulumunda 4 kademeli CF zinciri** | `cloudscraper` zorunlu, her kurulumda var; ama sade `pip install turkanime-gui` PySide6 kurmadığı için QtWebEngine kademesi yok. 5 kademe için `[gui]` ekstrası, hazır paket ya da `requirements-gui.txt`. |
| **Anizle bölüm başına sınırlı kaynak** | Site video.js/HLS'e geçti; bazı bölümlerde tek stream dönebiliyor. |

## 🔧 Sistem Gereksinimleri

- **Python:** 3.9+ (kaynaktan/pip ile çalıştırmak için; hazır pakette gerekmez).
  Sınıflandırıcılarda 3.9 – 3.13; yayın kapısındaki testler 3.12 ile koşuyor.
  3.11+ önerilir: yt-dlp, curl-cffi ve PySide6'nın güncel sürümleri 3.10+,
  rapidfuzz'unki 3.11+ istiyor; 3.9'da pip bunların eski sürümlerinde kalır.
- **FFmpeg, mpv, aria2c, yt-dlp:** Hazır Windows paketinde gömülü gelir;
  kaynaktan çalıştırıyorsan uygulama içindeki sihirbaz indirip kurar.
- **FlareSolverr:** Varsayılan ayarda projenin sunucusu yazılıdır, yani
  kurulumdan sonra 3. kademe uzak bir sunucuya gider. Alanı boşaltırsanız
  zincir doğrudan gömülü QtWebEngine'e düşer ve hiçbir istek dışarı çıkmaz.
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
> `animedepo` (TürkAnime arşivi). OpenAnime ve Tranimaci bu betikte yok.

Gerçek ağa çıkan küçük duman testleri de `pytest` içinde:
`tests/test_ag_canli.py` (arşiv aynaları ve AniList) ve altı yeni kaynağın
birer uçtan uca testi (arama → bölümler → akışlar). `network` işaretli,
varsayılan koşuda atlanır; ağ mandalını kaldırıp yalnızca onları koşmak için:

```bash
python -m pytest --network -m network
```

### Test Kapsamı
| Alan | Testler |
|------|---------|
| **Arayüz (pytest-qt)** | Keşif/arama/detay/bölüm/indirme sayfaları, oynatma, izleme listesi, güncelleme servisi, gereksinim sihirbazı, Discord RPC, çerez tarayıcısı, worker havuzu |
| **Arama** | Alakaya göre sıralama, çok kaynaklı arama zaman aşımı, başlık eşleştirme |
| **Çevrimdışı arşiv** | Konum sırası, aynalar ve disk önbelleği, tam arşiv indirme (tar güvenliği, bağlanırken de işleyen iptal, eskiyi koruyan takas, disk hatasında yedeğe geçmeme, sembolik bağlı hedef), sıfırlamanın GUI'yi dondurmaması, okunamayan arşivin aramada, bölüm listesinde ve oynatmada söylenmesi ("yok" ile "ulaşılamadı" ayrı), Windows uzun yolları (MAX_PATH taklidiyle), eşitleme aracının yanlış hedefi reddetmesi, Ayarlar bölümü (ilerleme, iptal, hata mesajı, klasör seçimi, yalnızca indirileni silme, silinemeyen eski kopya uyarısı) |
| **Kaynak kaydı** | Her kaynak aranabilir, bölümleri açılabilir ve CLI menüsünde; eski "AnimeDepo" adı; ad çakışması import anında hata; uzun bölüm slug'ları kesilmeden ayrık (geçmiş anahtarı, dosya adı); CLI yeniden denemede oynatılamayan videoyu atlıyor; üretim kodunda kapanan turkanime.tv'ye giden yol kalmadı |
| **Kaynaklar** | Anizle CF bypass zinciri, OpenAnime arama ve stream doğrulama, çerez yönetimi; Animexe, AnimPow, Deokwave, Asya Animeleri, Animeler.pw ve One Pace TR için sitelerin gerçek yanıtlarından kırpılmış fikstürlerle ağsız testler |
| **Oynatma ve indirme** | Sıradaki adaya geçme ve mpv çıkış kodları, kaldığın yer ve otomatik ilerleme, "İzlerken kaydet", yerel dosyadan oynatma, indirme bütünlüğü (gerçek yt-dlp ile yerel HTTP sunucusuna karşı 403/200), çift kuyruk engeli, kalıcı kuyruk ve duraklat/sürdür, hata sebepleri, yerel kitaplık |
| **Cloudflare** | Kademe sırası, challenge tanıma, timeout davranışı, çözücü giriş noktası |
| **Çekirdek** | Bölüm birleştirme ve ayrıştırma, indirme yolu güvenliği, atomik JSON yazımı, ağ izolasyonu |
| **Yayın** | `release.yml` sürüm türetme, test kapısı, `version.json` şeması, PyPI sırrı |
| **Adaptörler (ağ, ayrı betik)** | AnimeciX, Anizle, TRAnimeİzle, TürkAnime arşivi — arama, bölüm listesi, stream |

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
International](../LICENSE)** (CC BY-NC-ND 4.0) ile lisanslanmıştır.

| İzin var | İzin yok |
|----------|----------|
| Paylaşmak ve kopyalamak | Ticari kullanım |
| Kaynak göstererek atıfta bulunmak | Değiştirilmiş sürüm dağıtmak |

Kullanım ve sorumluluk sınırları için [DISCLAIMER](DISCLAIMER.md) dosyasını oku.

## 📧 İletişim

Sitenizi kullanmamamı, kaldırmamı istiyorsanız veya başka talepleriniz için:
- **E-posta:** info@bariskeser.com
- **Discord:** bariskeser
