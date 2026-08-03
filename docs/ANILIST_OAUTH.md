# AniList Girişi (OAuth2)

TürkAnime GUI, AniList hesabına **gizli anahtar (client secret) olmadan**
bağlanır. Bu not, akışın neden böyle olduğunu ve kendi AniList uygulamanı
tanımlamak istersen ne yapman gerektiğini anlatır.

## 🔐 Neden client secret yok?

OAuth2'de `client_id` **public**'tir — yetkilendirme URL'inin içinde zaten
kullanıcının adres çubuğunda taşınır, sır değildir. `client_secret` ise sırdır
ve **masaüstü uygulaması sır saklayamaz**: kaynak kodu herkese açık bir depoda
durur, derlenmiş `.exe` de birkaç dakikada açılıp içindeki dizeler okunabilir.
"Gömülü sır" pratikte sır değil, yalnızca gecikmeli bir sızıntıdır.

Bu yüzden giriş varsayılan olarak **Implicit akış** (`response_type=token`)
kullanır: AniList jetonu doğrudan geri dönüş adresine, URL'in `#` fragment'ında
verir; secret hiç devreye girmez.

> **Geçmişteki sızıntı hakkında.** Bu deponun eski sürümlerinde `client_secret`
> kaynak koda gömülüydü. Değer artık koddan çıkarıldı ama **git geçmişinde
> duruyor** ve geçmiş bilerek yeniden yazılmadı: depo çoktan klonlanmış,
> forklanmış ve paketlenmiş durumda — history rewrite sırrı geri getirmez,
> yalnızca herkesin geçmişini bozar. Doğru çözüm **rotasyon**: sızan anahtarın
> sahibi AniList geliştirici panelinden yeni bir secret üretmeli (ya da
> uygulamayı silip yeniden oluşturmalı). Uygulama artık o değere hiç
> ihtiyaç duymadığı için rotasyon kimsenin girişini bozmaz.

## ✅ Normal kullanım

**Hiçbir şey yapmana gerek yok.** Ayarlar → AniList → **"AniList'e Giriş Yap"**
düğmesine bas; tarayıcı açılır, AniList'te izin verirsin, uygulama jetonu alır.

Perde arkasında: uygulama `http://localhost:9921/anilist-login` adresinde küçük
bir yerel sunucu açar. Jeton fragment'ta geldiği ve fragment **sunucuya hiç
gönderilmediği** için, geri dönüş adresinde sunulan sayfadaki JavaScript
`location.hash`'i okuyup jetonu aynı yerel sunucuya geri gönderir. Sayfa "Giriş
başarılı, bu sekmeyi kapatabilirsiniz." dediğinde iş bitmiştir.

Jeton **1 yıl** geçerlidir (AniList refresh token vermiyor); süresi dolunca
aynı düğmeyle tekrar giriş yapman yeterli.

## 🛠️ Kendi AniList uygulamanı tanımlamak

Uygulamanın ortak `client_id`'si yerine kendi kaydını kullanmak istersen:

1. [AniList → Settings → Developer](https://anilist.co/settings/developer)
   sayfasında **Create New Client**.
2. **Name:** istediğin ad (ör. `TurkAnime GUI`).
3. **Redirect URL:** birebir şu değer:
   ```
   http://localhost:9921/anilist-login
   ```
   Başka bir port kullanacaksan burada da, Ayarlar'daki alanda da aynı adresi
   yaz — yerel giriş sunucusu portu bu adresten çözer.
4. Oluşan **Client ID**'yi Ayarlar → AniList → **Client ID** alanına yapıştır.
5. **Client Secret alanını boş bırak.** Implicit akış onu istemez.
6. Kaydet, sonra "AniList'e Giriş Yap".

## ⚙️ Authorization Code akışı (opsiyonel)

Kendi uygulamanı `client_secret` ile, yani klasik Authorization Code akışıyla
kullanmak istersen iki yol var. Secret dolu olduğu anda uygulama otomatik
olarak o akışa geçer:

- **Ortam değişkeni (önerilen):** secret hiçbir dosyaya yazılmaz, süreç
  ömrüyle sınırlı kalır.
  ```powershell
  $env:ANILIST_CLIENT_SECRET = "..."   # kendi secret'ın
  turkanime-qt
  ```
- **Ayarlar sayfası:** Client Secret alanına yaz. Değer, jetonlarla aynı
  klasördeki `anilist_config.json` dosyasına düz metin kaydedilir.

Çözümleme sırası: **ortam değişkeni → `anilist_config.json` → boş (Implicit)**.
Ortam değişkeni etkinken "Kaydet"e basmak secret'ı diske kopyalamaz.

## 📁 Dosyalar

Her ikisi de kullanıcı veri klasöründedir
(`%LOCALAPPDATA%\Barkeser\TurkAnime\` — Linux/macOS'ta `appdirs` karşılığı):

| Dosya | İçerik |
|-------|--------|
| `anilist_tokens.json` | Erişim jetonu (giriş yaptıysan) |
| `anilist_config.json` | Client ID, redirect URI ve — girdiysen — client secret |

Yollar değişmedi: eskiden giriş yapmış kullanıcılar bu değişiklikten
etkilenmez, jetonları olduğu yerde okunmaya devam eder.

## 🩺 Sorun giderme

| Belirti | Sebep |
|---------|-------|
| AniList "Invalid redirect URI" diyor | Ayarlar'daki adres, geliştirici panelindekiyle birebir aynı değil (`http`/`https`, port, sondaki `/` dahil) |
| Tarayıcı sekmesi "Giriş tamamlanamadı" diyor | İzin ekranında reddedildi ya da jeton fragment'ta gelmedi; uygulamadan tekrar dene |
| Sekme açıldı ama uygulama giriş görmüyor | Yerel sunucunun portu (redirect URI'deki port) başka bir uygulama tarafından tutuluyor olabilir |
| "Token geçersiz veya süresi dolmuş" | 1 yıllık jeton doldu; yeniden giriş yap |
</content>
