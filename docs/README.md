
<div align="center">

![TürkAnime Logo](https://i.imgur.com/Dw8sv30.png)

[![GitHub all releases](https://img.shields.io/github/downloads/barkeser2002/turkanime-gui/total?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Downloads](https://static.pepy.tech/personalized-badge/turkanime-gui?period=total&units=international_system&left_color=grey&right_color=orange&left_text=Pip%20Installs)](https://pepy.tech/project/turkanime-gui)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/barkeser2002/turkanime-gui?style=flat-square)](https://github.com/barkeser2002/turkanime-gui/releases/latest)
[![Pypi version](https://img.shields.io/pypi/v/turkanime-gui?style=flat-square)](https://pypi.org/project/turkanime-gui/)

</div>

# TürkAnime GUI

**Sürüm notları:** [V10.3.1](V10.3.1.md) · [V10.3.0](V10.3.0.md) · [V10.2.0](V10.2.0.md) · [V10.1.0](V10.1.0.md) · [V10.0.0](V10.0.0.md)

TürkAnime GUI **tamamen arayüz odaklı** bir anime keşif, izleme ve indirme
uygulaması. Arayüz **PySide6 + QtWebEngine** üzerine kurulu; V10.0.0 ile
CustomTkinter yığını kaldırıldı ve tek arayüz kaldı. Sayfalar **HTML/CSS/JS**
ile yazılmış bir web arayüzü: Qt penceresinin içindeki gömülü Chromium
(QtWebEngine) gösteriyor, Python tarafıyla QWebChannel üzerinden konuşuyor.
Ek bağımlılık, derleme adımı ya da internetten yüklenen arayüz dosyası yok
(bkz. [Arayüz Mimarisi](#-arayüz-mimarisi)). Terminal (CLI) sürümü çalışmaya
devam ediyor ama geliştirme masaüstü uygulamasına odaklı.

## ✨ Öne Çıkan Özellikler

- **18 kaynakta paralel arama:** TürkAnime (arşiv), AnimeciX, Anizle,
  TRAnimeİzle, OpenAnime, Tranimaci, Animexe, AnimPow, Deokwave, Asya
  Animeleri, Animeler.pw, One Pace TR, AnimeTR, Animezer, AniMOM, BuguiTR,
  SeiCode ve AniList aynı anda aranır. Bunlardan **17'si video sunar**; AniList
  yalnızca meta veri ve kullanıcı listesi sağlar.
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
- **Cloudflare ve bot doğrulamaları:** Zincir önce otomatik yolları dener:
  curl_cffi, cloudscraper, FlareSolverr, gömülü Chromium (QtWebEngine).
  FlareSolverr pakette gelmez (zip ~230-265 MB küçük); Windows/Linux'ta ilk
  Cloudflare engelinde arka planda kendiliğinden iner (sabit sürüm, SHA-256
  denetimli), sonra bu bilgisayarda (yalnızca 127.0.0.1) kendiliğinden başlar,
  uygulama kapanınca durur. Otomatik yollar yetmezse hatanın yanında
  **"Erişimi aç"** düğmesi çıkar: sitenin doğrulamasını uygulamanın içindeki
  tarayıcıda sen çözersin, oturum o kaynağa kaydedilir ve istek kaldığı
  yerden sürer (bkz. [Erişimi Aç](#-erişimi-aç)).
  **Dikkat:** `flaresolverr_url` ayarında projenin sunucusu
  (`node-kyb.bariskeser.com:8191`) yazılı gelir; yerel FlareSolverr
  kullanılamazken zincir oraya uğrar. İstemiyorsanız Ayarlar → Bağlantı →
  FlareSolverr adresi alanını boşaltın; yerel FlareSolverr ve gömülü çözücü
  yine çalışır.
- **Bağış (isteğe bağlı, varsayılan kapalı):** Oynattığın bölümlerin kaydını
  (kaynak, anime, bölüm, çalışan video bağlantısı) projenin sunucusuna
  bağışlayabilirsin; arşiv, kullanıcıların ulaşabildiği kaynaklardan büyür.
  Çerez, jeton, hesap bilgisi ya da yerel dosya yolu gönderilmez
  (bkz. [Bağış](#-bağış)).
- **Kaynak başına bölüm listesi:** Anime birden çok kaynakta varsa detay
  sayfasında her kaynak kendi akordiyonunda listelenir. Otomatik eşleşen
  başlık yanında yazar, yanlışsa "Değiştir" ile doğru kayıt seçilir. Oynat ve
  İndir, tıklanan satırın kaynağından çalışır.
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
- **Web arayüzü:** Eski ekran görüntülerindeki düzen modernleştirilerek
  HTML/CSS/JS ile yeniden yazıldı. Üst menü ve arama çubuğu; hero ve kaydırmalı
  şeritli ana sayfa; kart ızgaraları; künye kartlı detay sayfası; bölüm
  akordiyonları; bildirimler ve onay pencereleri. Veri beklenirken iskelet
  kartlar görünür, düzen pencere daralınca uyum sağlar.
- **Discord Rich Presence:** O anda ne izlediğini arkadaşlarınla paylaş.
- **Çoklu platform:** Windows/Linux/macOS için hazır paket, Python 3.9+ olan
  her platformdan pip ile çalıştır.
- **Terminal sürümü tkinter istemez:** klasör seçici yoksa yol terminalden
  sorulur; kayıtlı TRAnimeİzle çerezi ve OpenAnime jetonları CLI'da da yüklenir.
- **Testler:** 2.726 otomatik test (pytest + pytest-qt; web sayfaları gerçek
  QtWebEngine'de), ağa çıkmaz.

## 🧭 Uygulama Akışı

1. **Keşfet:** Jikan (MyAnimeList) trend ve sezon listeleri; Jikan erişilemezse
   AniList trendlerine düşülür.
2. **Ara:** 18 kaynakta paralel arama; sonuçlar kaynak kaynak gelir, yavaş
   kaynak aramayı çökertmez, zaman aşımına uğrayan kaynak adıyla yazılır.
3. **İndir & Oynat:** mpv entegrasyonu sayesinde indirme ve izleme tek pencerede.
4. **İlerleme Takibi:** İzlediklerin otomatik tutulur, "Kitaplığım"da görünür
   ve AniList'e bağlıysan oraya yansır.

## 🧩 Arayüz Mimarisi

Arayüz tek bir Qt penceresi, içinde tek bir web görünümü (`QWebEngineView`):

| Katman | Yer | Görev |
|--------|-----|-------|
| Sayfalar | `turkanime_api/gui/web/statik/` (`index.html`, `css/`, `js/sayfalar/`) | HTML/CSS/JS. Derleme adımı yok; bütün dosyalar yerel, CDN yok |
| Köprü | `turkanime_api/gui/web/kopru.py` | QWebChannel: JS → Python çağrıları (`TA.cagir`), Python → JS olayları (`kopru.yay`) |
| Uçlar | `turkanime_api/gui/web/uclar_*.py` | Sayfa başına Python tarafı: keşif, arama, detay, kitaplık, izleme listesi, indirmeler, ayarlar |
| Şema | `turkanime_api/gui/web/sema.py` | `ta://uygulama/…` statik dosyaları, `ta://gorsel/…` kapak önbelleğini sunar; dış adresler sistem tarayıcısında açılır |
| Pencereler | `turkanime_api/gui/web/sorular.py`, `pencereler.py`, `katki.py`; `js/sorular.js`, `js/pencereler.js` | Soru-cevap düzeni ve sayfa içi pencereler: fansub seçimi, ilerleme sorusu, güncelleme, kurulum sihirbazı, bağış onayı, kapanış onayı |
| Erişimi aç | `turkanime_api/gui/qt/erisim_penceresi.py`, `common/oturumlar.py`, `gui/web/uclar_erisim.py`, `js/erisim.js` | Bot doğrulamasını kullanıcının çözdüğü tarayıcı penceresi; kaynak başına kalıcı profil, oturum deposu (`oturumlar.json`) |
| Bağış | `turkanime_api/gui/web/katki.py`, `veri_bagisi.py`; `sozlesme/katki_veri.json` | Oturum kimliği bağışı ve veri bağışı: onay, gövde kurma, diskteki kuyruk, arka planda gönderim |
| Servisler | `turkanime_api/gui/qt/`, `common/flaresolverr.py` | İndirme yöneticisi, oynatma (mpv), AniList, Discord, güncelleme, kurulum sihirbazı, yerel FlareSolverr |

Ağ ve disk işleri arka plan havuzunda koşar; sayfa donmaz. Sayfalar veriyi
JSON olarak alır. Bölüm nesneleri Python'da kalır, sayfa onları `(kaynak, sıra)`
ile anar. Soru pencereleri de sayfanın içinde çizilir: Python soruyu olay
olarak yollar, cevap köprüden döner, ana iş parçacığı beklemez. Sayfa kapanır
ya da çökerse açık her soru güvenli varsayılanıyla biter (bağış onayı: hayır).
Tek ayrı Qt penceresi "Erişimi aç" tarayıcısı (TRAnimeİzle çerez penceresi de
onun bir yapılandırması): sitenin bot doğrulamasını çözmek gerçek bir tarayıcı
istiyor.

## 📺 Ekran Görüntüleri

### Ana Sayfa
![Ana sayfa: hero, arama, trend şeridi](https://raw.githubusercontent.com/barkeser2002/turkanime-gui/main/docs/ekran/anasayfa.webp)

### Anime Detayı
![Anime detayı: künye, skor ve izleyici kartları, türler, stüdyo](https://raw.githubusercontent.com/barkeser2002/turkanime-gui/main/docs/ekran/detay.webp)

### Kaynaklar ve Bölümler
![Kaynak akordiyonu: bölüm arama, aralık seçimi, izlendi/indirildi rozetleri](https://raw.githubusercontent.com/barkeser2002/turkanime-gui/main/docs/ekran/bolumler.webp)

### Arama
![Arama: kaynak filtreleri, aranamayan kaynakların sebebi, kaynak başına sonuçlar](https://raw.githubusercontent.com/barkeser2002/turkanime-gui/main/docs/ekran/arama.webp)

### Ayarlar
![Ayarlar: bölüm menüsü ve anahtarlar](https://raw.githubusercontent.com/barkeser2002/turkanime-gui/main/docs/ekran/ayarlar.webp)

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

Zip'lerde FlareSolverr **yok**: 10.3.x'te exe'nin yanında `flaresolverr/`
klasörü olarak geliyor ve zip'i Windows'ta ~230 MB, Linux'ta ~265 MB
büyütüyordu — Cloudflare'e hiç takılmayan kullanıcı da indiriyordu. Artık
uygulama onu ilk Cloudflare engelinde veri klasörüne kendisi indirip açıyor
(FlareSolverr v3.5.2, boyut + SHA-256 denetimli); istemiyorsan Ayarlar →
Bağlantı → "Gerekince kendiliğinden indir"i kapat. Eski zip'ten kalan
`flaresolverr/` klasörü exe'nin yanında duruyorsa o kullanılır, yeniden inmez.

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
> QtWebEngine kademesi de yoktur: zincir 4 kademe. FlareSolverr kademesi,
> yerel FlareSolverr kuruluysa (ilk ihtiyaçta kendiliğinden iner; Ayarlar →
> Bağlantı → Kur) ya da
> `flaresolverr_url` doluysa vardır
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

> **Windows:** Çevrimdışı arşivde (`arsiv/`) tam yolu 260 karakteri aşan
> dosyalar var; git'in uzun yol desteği kapalıysa klon "Filename too long" ile
> yarım kalır. Klonlarken açın:
> `git clone -c core.longpaths=true https://github.com/barkeser2002/turkanime-gui.git`
> Arşive ihtiyacınız yoksa onu hiç yazmadan da klonlayabilirsiniz (uygulama
> arşivi uzak aynalardan okur):
> `git clone --no-checkout https://github.com/barkeser2002/turkanime-gui.git`,
> sonra klasörde `git sparse-checkout set --no-cone '/*' '!/arsiv/'` ve
> `git checkout main`.

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
   **Çevrimdışı Arşiv (TürkAnime)** → **"Tüm arşivi indir (~230 MB)"**.
   Ayrıntı: [Çevrimdışı Arşiv](#-çevrimdışı-arşiv-türkanime).
3. **TRAnimeİzle** kullanmak istiyorsan Ayarlar → TRAnimeİzle Cookie →
   **"Tarayıcıdan Al"** düğmesine bas. Uygulama içindeki tarayıcı açılır, bot
   kontrolünü çözersin, çerez kaydedilir.
   **OpenAnime** akışları 404 dönüyorsa Ayarlar → OpenAnime oturumu →
   Token / Refresh Token alanlarına tarayıcındaki `openani.me` çerezlerini gir
   (isteğe bağlı). Diğer kaynaklar giriş istemez.
4. **FlareSolverr** pakette gelmez: Windows/Linux'ta ilk Cloudflare
   engelinde arka planda kendiliğinden iner. Kurulum sihirbazı da önerir
   (işareti kaldırırsan kendiliğinden inmez), Ayarlar → Bağlantı → **Kur**
   ile elle de kurulur. Kendi sunucun varsa adresini aynı karttaki alana
   yaz; o zaman yerel yerine o kullanılır.
   Bir kaynak **bot doğrulamasına** takılırsa hatanın yanındaki
   **"Erişimi aç"** düğmesine bas (bkz. [Erişimi Aç](#-erişimi-aç)).
5. **Ana Sayfa, Trend, Bu Sezon** ya da üst çubuktaki **arama kutusundan**
   anime seç. Kutunun yanındaki listeden tek bir kaynakta da arayabilirsin.
6. Detay sayfasında kaynağın akordiyonundan **bölümü oynat** ya da **indir**.
   Toplu seçim için aralık ("1-12, 20-"), İzlenmemişler / İndirilmemişler ya
   da Shift+tık. İndirmeler **İndirilenler** sayfasında: bölüm başına
   ilerleme çubuğu, yeniden deneme, duraklatma ve iptal.
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

**Ayarlar'dan indirme:** Ayarlar → **Çevrimdışı Arşiv (TürkAnime)** bölümü
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

## 🔓 Erişimi Aç

Bazı siteler her isteğe bot doğrulaması koyuyor (Deokwave'de Cloudflare'in
"Verify you are human" kutusu). Uygulama bu doğrulamaları otomatik geçmeye
**çalışmaz**; onun yerine hatanın yanında **"Erişimi aç"** düğmesi çıkar:

1. Düğme, o kaynağın sitesini uygulamanın içindeki tarayıcıda açar.
2. Doğrulamayı sen çözersin. Pencere, sayfa doğrulama olmaktan çıkınca
   kendiliğinden kapanır; "Yenile" ve "İptal" de var, 5 dakikada zaman aşımı.
3. Çerezler, tarayıcının kimliği (user-agent) ve istemci ipucu başlıkları o
   kaynağa kaydedilir. Arama o kaynakta yeniden yapılır, bölüm listesi
   yenilenir, oynatma yeniden başlar ya da indirme aynı satırda kuyruğa girer.

Düğme yalnızca gerçek bir doğrulamada çıkar; düz 403 ya da zaman aşımında
çıkmaz. Kayıtlı oturumlar Ayarlar → Kaynak Oturumları'nda görünür (yaşı,
çerez sayısı, geçerli mi); "Temizle" oturumu ve o kaynağın tarayıcı profilini
siler. Oturumlar veri klasöründe `oturumlar.json` ve `erisim_profilleri/`
altında durur ve bu bilgisayardan çıkmaz. Terminal sürümü GUI'nin kaydettiği
oturumu kullanır ama pencereyi kendisi açamaz.

**Erişim tarayıcısı (gömülü ya da gerçek tarayıcı).** Bazı siteler gömülü
QtWebEngine'in parmak izini tanıyıp doğrulamayı hiç çözdürmüyor; makinedeki
gerçek bir Chrome ailesi tarayıcısı (Chrome, Chromium, Brave, Edge, **Chrome
Beta**, **ungoogled-chromium**) çözdürüyor. Ayarlar → Kaynak Oturumları →
*Erişim tarayıcısı*'ndan motoru seçersin:

- **Otomatik** (varsayılan): `undetected-chromedriver` ve makinede bir Chrome
  ailesi tarayıcısı **varsa** onu görünür açar, yoksa gömülü QtWebEngine
  penceresine düşer. `selenium`/`undetected-chromedriver` kurulu olmayan normal
  pakette davranış eskisiyle aynıdır (hep gömülü).
- **Her zaman gömülü**: hep QtWebEngine penceresi.
- **Gerçek tarayıcı**: hazır değilse yine gömülüye düşer.

Gerçek tarayıcı motoru **isteğe bağlıdır** ve bir tarayıcı **paketlenmez** (yer
kazanmak için senin kurulu tarayıcın kullanılır): `pip install
"turkanime-gui[tarayici]"` ile `selenium` + `undetected-chromedriver` kurulur.
Belirli bir tarayıcıyı zorlamak için *Tarayıcı yolu* alanına yolunu yaz
(ya da `TURKANIME_TARAYICI` ortam değişkenine); boşsa PATH ve bilinen kurulum
yolları otomatik taranır. İlke değişmez: doğrulamayı yine **sen** çözersin,
`undetected-chromedriver` yalnızca gerçek tarayıcının kendi parmak izini
korur; uygulama otomatik tık/çözme yapmaz.

## 🤝 Bağış

İki ayrı bağış var, ikisi de **varsayılan kapalı**. Açarken ne gönderildiğini
anlatan bir onay penceresi çıkar; kutu işaretlenmeden onay düğmesi pasif,
Enter/Esc/× her zaman "hayır" demek.

- **Oturum kimliği bağışı:** TRAnimeİzle çerezi ya da OpenAnime jetonu
  alındığında, sunucunun o kaynağa erişebilmesi için bağışlanır. Cloudflare
  çerezleri (`cf_clearance`) bağışlanmaz: IP adresine ve tarayıcıya bağlılar,
  sunucuda işe yaramazlar.
- **Veri bağışı:** En az 10 sn oynatılan ya da indirmesi biten bölümün kaydı
  gönderilir: kaynak adı, anime ve bölüm kimliği/adı, çalışan video bağlantısı
  ve diğer adaylar (oynatıcı, fansub, hangisinin çalıştığı), varsa bölüm
  listesi, uygulama sürümü. Sunucu kayıtları arşive işler; Deokwave gibi
  sunucunun kendisinin gezemediği kaynaklar arşive bu yoldan girer.
  - **Gönderilmeyenler:** çerez, parola, jeton, oturum kimliği, kullanıcı adı
    ya da AniList hesabı, yerel dosya yolu, izleme konumu, user-agent,
    referer. İmzalı/süreli bağlantılar (md5, expires, token… taşıyanlar)
    atılır. İndirilmiş dosyadan oynatma ve TürkAnime arşivinden oynatma
    gönderilmez.
  - **Sunucu ne görür:** hangi bölümün hangi IP'den, yaklaşık ne zaman
    oynatıldığını. Sunucu IP'yi veritabanına yazmıyor (hız sınırı için
    yalnızca tuzlanmış özetini bellekte tutuyor), ama IP'yi görmediğini vaat
    edemeyiz.
  - **Davranış:** aynı bölüm 7 gün içinde yeniden gönderilmez. Kayıtlar
    diskte bir kuyrukta bekler (en çok 200). Sunucuya ulaşılamazsa arka planda
    yeniden denenir. Kapatınca gönderim hemen durur, gönderilmemiş kuyruk
    silinir; gönderilmiş kayıtlar geri çekilemez.
  - Ayarlar → Veri Bağışı kartında gönderilen, bekleyen ve düşen kayıtların
    sayısı ile son hata görünür.

**Sunucu:** varsayılan olarak projenin sunucusu (`turkanimeapi.bariskeser.com`).
Ayarlar → Oturum Kimliği Bağışı'ndaki alanlardan başka bir sunucu yazılabilir.
İstemcinin içindeki API anahtarı gizli değildir; sunucu onu yetki olarak değil
kapı olarak kullanır (hız sınırı). Bu anahtar yalnızca projenin sunucusuna,
şifreli bağlantıyla gider: kendi sunucunu yazdıysan anahtarını da yazman gerekir.

İstemcinin gönderdiği gövdenin şeması `sozlesme/katki_veri.json`; sunucu
deposundaki kopyayla birebir aynı tutulur.

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
| **Deokwave** | `deokwave.com`; ~3.100 başlık, bölüm başına çoğu zaman birden çok fansub, altyazısı gömülü MP4 (1080/720/480p). **Site şu an tamamen Cloudflare bot doğrulamasının arkasında**; doğrulamayı "Erişimi aç" ile sen çözersin, oturum kaydedilir (bkz. [Bilinen Kısıtlar](#-bilinen-kısıtlar)) |
| **Asya Animeleri** | `asyaanimeleri.top`; donghua (Çin animasyonu) ağırlıklı, Japon anime listesi kısmi |
| **Animeler.pw** | Eski animeler.me; anime + donghua, çok sayıda fansub ve oynatıcı. Akış listesi yavaş gelir (site 12–30 sn düşünüyor) |
| **One Pace TR** | `onepacetr.net`; One Pace (One Piece'in dolgusuz kurgusu) Türkçe altyazılı, 35 ark / 443 bölüm. Google Drive ve Sibnet |
| **AnimeTR** | `animetr.co`; anime ve donghua, bölüm başına birçok ayna (Sibnet, VK, Vidmoly, Google Drive…). Sendvid'in "geçici olarak yok" yer tutucusu ve ölü Sibnet videoları elenir |
| **Animezer** | `animezer.com`; açık JSON API, anime + donghua + çizgi dizi + film. Akışlar çoğunlukla anizmplayer (Anizle ile aynı CDN) ve Sibnet; imzalı HLS açılamazsa sitenin kendi vekiline düşülür. Deneysel |
| **AniMOM** | `animom.org`; 667 dizi + 28 film, çoğu sitenin kendi altyazı gömülü yüklemesi (Anizm kataloğunda olmayan 170 dizi, çoğu donghua ve yeni sezonlar). Üyelere özel videolar atlanır |
| **BuguiTR** | `buguitr.com`; fansub grubunun blogu, ~20 animasyon (BL anime ve donghua), grubun kendi çevirisi. Sitenin canlı çekim dizileri listelenmez |
| **SeiCode** | `seicode.net`; açık JSON API, ~180 başlık, yeni sezonlar ağırlıklı. Arama İngilizce adlarla çalışır. Deneysel |

### Meta Veri ve Keşif
| Servis | Rol |
|--------|-----|
| **AniList** | Arama sonuçlarına katılır, kullanıcı listesi ve OAuth2 girişi sağlar. Video sunmaz. |
| **Jikan (MyAnimeList)** | Yalnızca Keşfet sekmesindeki trend/sezon listeleri. Arama motoru **değildir**. |

### Cloudflare Bypass Zinciri
```
0. Erişimi aç      (Kullanıcının çözdüğü doğrulamanın oturumu — varsa tek deneme)
1. curl_cffi      (TLS fingerprint taklidi)
2. cloudscraper   (JS Challenge çözümü — zorunlu bağımlılık, her kurulumda var)
3. FlareSolverr   (Ayardaki kendi sunucun > bu bilgisayardaki yerel örnek > projenin uzak sunucusu;
                   uzak sunucu adresi VARSAYILAN OLARAK DOLU gelir)
4. QtWebEngine    (Yerel gömülü Chromium, ayrı süreçte — yalnızca [gui] kurulumlarında)
5. requests       (Son çare)
```
> Yerel FlareSolverr uygulama açılırken değil, zincir onu ilk kez gerektirince
> başlar (her açılışta Chrome öz-testi yapıyor ve ~100 MB bellek tutuyor).
> Kurulu değilse o ilk ihtiyaçta arka planda iner (o istek diğer kademelerden
> geçer); başarısız indirme 30 dakika kendiliğinden yeniden denenmez.
> Arayüz iş parçacığı onu hiç beklemez; o istek kademeyi atlar. Başlatma
> başarısız olursa 5 dakika kendiliğinden yeniden denenmez; aynı anda en çok
> 2 istek (her biri kendi Chrome'unu açıyor). Ayarlar → Bağlantı'da durum,
> Kur/Güncelle, Başlat, Durdur, "Yerel FlareSolverr'ı kullan" ve "Gerekince
> kendiliğinden indir" anahtarları var.
>
> Zincir, HTTP 200 dönen *challenge sayfalarını* da tanır ve başarı saymaz;
> aksi hâlde ilk adımda kısa devre olup gerçek tarayıcıya hiç ulaşılmıyordu.
> Selenium/undetected-chromedriver V10.0.0 ile zorunlu bağımlılıklardan
> çıkarıldı; bugün yalnızca "Erişimi aç"ın isteğe bağlı gerçek tarayıcı motoru
> için `[tarayici]` extra'sında (bkz. [Erişimi Aç](#-erişimi-aç)) ve zincirde
> kullanılmıyor.

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
| **Deokwave Cloudflare doğrulaması** | 30 Eylül 2026'dan beri site her isteğe Cloudflare'in etkileşimli doğrulamasını ("Verify you are human") gösteriyor; otomatik yollar geçemiyor. Hatanın yanındaki "Erişimi aç" ile doğrulamayı sen çözersin, oturum kaydedilir. Kaydedilen oturumla uygulamanın kendi isteklerinin kabul edildiği gerçek bir ev bağlantısında henüz doğrulanmadı; kabul edilmezse kaynak bunu söyler ve "Erişimi aç"ı yeniden önerir. |
| **ok.ru videoları (yt-dlp 2026.08.19)** | yt-dlp'nin bu sürümü ok.ru videolarında `TypeError` ile düşüyor. Oynatmada sıradaki aday denenir; yalnızca ok.ru bağlantısı olan bölüm açılmaz. |
| **Yerel FlareSolverr** | Yalnızca Windows ve Linux x64. macOS'ta ayardaki adres ve gömülü çözücü kullanılır. Linux'ta Xvfb gerekir; yoksa Ayarlar'daki durum "Eksik bağımlılık" der, kurulum komutunu yazar ve FlareSolverr kendiliğinden indirilmez. Pakette gelmediği için ilk Cloudflare engelinde ~230-265 MB iner; o indirme bitene kadar istekler diğer kademelerden geçer. İlk açılışta chromedriver'ı Google'dan indirir, bunun için internet gerekir. Windows'ta süreç temizliği (iş nesnesi) henüz gerçek bir Windows makinesinde denenmedi. |
| **İmzalı HLS IP'ye bağlı (Animezer, AniMOM, Animeler.pw)** | Oynatma listesini isteyen IP'ye imzalanıyor; çıkış IP'si bağlantı başına değişen ağlarda (bazı VPN'ler, mobil ağlar) 403 alınabilir. Animezer bu durumda sitenin vekiline düşer. |
| **SeiCode ve tau-video** | Bölümlerin çoğunun tek kopyası tau-video'da; bazı ağları Cloudflare ile engelliyor. O zaman akış dönmez ve sebebi yazılır. Arama yalnızca İngilizce adları bulur. |
| **AnimeTR eski bölümler** | Site eski bölümlerin çoğunu telif gerekçesiyle silmiş; çalışan ayna yoksa uygulama bunu söyler. |
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
- **FlareSolverr:** Pakette gelmez; GUI ilk Cloudflare engelinde kendisi
  indirir, sihirbaz ya da Ayarlar → Bağlantı → Kur ile elle de kurulur (sabit
  sürüm, SHA-256 denetimli, ~263/378 MB). Terminal sürümü kendiliğinden
  indirmez: `python -m turkanime_api.common.flaresolverr kur`. Linux'ta Xvfb gerekir. Yerel örnek
  yoksa ve alan boş değilse 3. kademe projenin uzak sunucusuna gider; alanı
  boşaltırsanız uzak sunucu hiç kullanılmaz.
- **İnternet bağlantısı:** Kaynaklara erişim ve AniList senkronu için.

## 🧪 Testler

Otomatik test paketi (ağa çıkmaz, Qt offscreen koşar). Web sayfası testleri
QtWebEngine'i de offscreen çalıştırır; root olarak (ör. bir container'da)
koşarken Chromium'un korumalı alanı açılamaz, `QTWEBENGINE_DISABLE_SANDBOX=1`
verin:

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
| **Arayüz (pytest-qt + QtWebEngine)** | Web sayfaları gerçek tarayıcıda (offscreen): keşif, arama, detay ve kaynak akordiyonları, kitaplık, izleme listesi, indirmeler, ayarlar; köprü ve `ta://` şeması, ızgara yerleşimi. Oynatma, güncelleme servisi, gereksinim sihirbazı, Discord RPC, çerez tarayıcısı, worker havuzu; sayfa içi soru pencereleri (fansub seçimi, ilerleme sorusu, güncelleme, kurulum sihirbazı, bağış onayı, kapanış onayı) ve soru-cevap düzeni |
| **Arama** | Alakaya göre sıralama, çok kaynaklı arama zaman aşımı, başlık eşleştirme |
| **Çevrimdışı arşiv** | Konum sırası, aynalar ve disk önbelleği, tam arşiv indirme (tar güvenliği, bağlanırken de işleyen iptal, eskiyi koruyan takas, disk hatasında yedeğe geçmeme, sembolik bağlı hedef), sıfırlamanın GUI'yi dondurmaması, okunamayan arşivin aramada, bölüm listesinde ve oynatmada söylenmesi ("yok" ile "ulaşılamadı" ayrı), Windows uzun yolları (MAX_PATH taklidiyle), eşitleme aracının yanlış hedefi reddetmesi, Ayarlar bölümü (ilerleme, iptal, hata mesajı, klasör seçimi, yalnızca indirileni silme, silinemeyen eski kopya uyarısı) |
| **Kaynak kaydı** | Her kaynak aranabilir, bölümleri açılabilir ve CLI menüsünde; eski "AnimeDepo" adı; ad çakışması import anında hata; uzun bölüm slug'ları kesilmeden ayrık (geçmiş anahtarı, dosya adı); CLI yeniden denemede oynatılamayan videoyu atlıyor; üretim kodunda kapanan turkanime.tv'ye giden yol kalmadı |
| **Kaynaklar** | Anizle CF bypass zinciri, OpenAnime arama ve stream doğrulama, çerez yönetimi; Animexe, AnimPow, Deokwave (Cloudflare doğrulaması dahil), Asya Animeleri, Animeler.pw, One Pace TR, AnimeTR, Animezer, AniMOM, BuguiTR ve SeiCode için sitelerin gerçek yanıtlarından kırpılmış fikstürlerle ağsız testler |
| **Oynatma ve indirme** | Sıradaki adaya geçme ve mpv çıkış kodları, kaldığın yer ve otomatik ilerleme, "İzlerken kaydet", yerel dosyadan oynatma, indirme bütünlüğü (gerçek yt-dlp ile yerel HTTP sunucusuna karşı 403/200), çift kuyruk engeli, kalıcı kuyruk ve duraklat/sürdür, hata sebepleri, yerel kitaplık |
| **Cloudflare** | Kademe sırası, challenge tanıma, timeout davranışı, çözücü giriş noktası; yerel FlareSolverr (sabit sürüm kurulumu, ilk ihtiyaçta arka planda otomatik kurulum ve başarısızlıkta bekleme, kapanışta kesilen indirmenin temizliği, bozuk özet/boyut, atomik takas, arşiv yol kaçışı, sahte çalıştırılabilirle yaşam döngüsü, ebeveyn öldürülünce kalan süreç yok, adres önceliği; yayın paketine gömülmediği) |
| **Erişimi aç** | Oturum deposu (süre, alan adı ve yol eşleşmesi, istemci ipucu başlıkları), gerçek QtWebEngine'de kendini temizleyen sahte doğrulama sitesine karşı pencere, iptal ve zaman aşımı; arama, detay, oynatma, indirme ve Ayarlar'daki düğme akışları |
| **Bağış** | Onay (varsayılan kapalı, yalnızca açık "evet"), gövde beyaz listesi, imzalı bağlantıların atılması, kuyruk kuralları, yerel sahte sunucuya karşı gerçek HTTP (429, yönlendirme izlenmemesi), gövdenin sunucu sözleşmesine uyması |
| **Çekirdek** | Bölüm birleştirme ve ayrıştırma (sezonlu ara bölüm dahil), indirme yolu güvenliği, atomik JSON yazımı, ağ izolasyonu, veri kökü kuralı ve testlerin gerçek ayarlardan yalıtımı |
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
