---
name: Hata raporu
about: Uygulamada karşılaştığınız bir sorunu bildirin
title: ''
labels: bug
assignees: ''

---

**Sorun nedir?**
Ne yapmaya çalıştınız, ne oldu, ne olmasını bekliyordunuz?

**Nasıl tekrarlanır?**
1. …
2. …
3. Hata görünüyor

---

### Ortam

- **Arayüz:** Qt arayüzü (`turkanime-gui`) / terminal (`turkanime-cli`)
- **Kurulum yolu:** hazır paket (zip) / `pip install` / kaynak koddan
- **Sürüm:** (arayüzde Ayarlar → Hakkında, ya da `pip show turkanime-gui`)
- **İşletim sistemi:** (örn. Windows 11, Ubuntu 24.04, macOS 15)
- **Python sürümü:** (yalnızca pip/kaynak kurulumunda; `python --version`)

> **Kurulum yolu neden önemli:** `pip install` ile kurulan sürümde
> `cloudscraper` gelmiyor ve Cloudflare zinciri 5 yerine 4 kademeyle çalışıyor.
> Hazır paket ve kaynaktan kurulum beş kademenin tamamını taşıyor — bu, aynı
> hatanın bir kurulumda görünüp diğerinde görünmemesine yol açabiliyor.

### Hangi kaynak?

Sorun belirli bir kaynakta mı yaşanıyor?

- [ ] TürkAnime
- [ ] AnimeciX
- [ ] Anizle
- [ ] TRAnimeİzle
- [ ] OpenAnime
- [ ] Tranimaci
- [ ] AnimeDepo
- [ ] AniList (yalnızca arama/liste — video sunmaz)
- [ ] Kaynaktan bağımsız

---

### Kontrol listesi

Göndermeden önce şunlara bakın; bilinen kısıtlar rapor gerektirmiyor:

- [ ] **TRAnimeİzle** kullanıyorsam Ayarlar → TRAnimeİzle Cookie →
      "Tarayıcıdan Al" ile çerezi aldım. *(Çerez olmadan bu kaynak bölüm
      döndürmez — beklenen davranıştır.)*
- [ ] **OpenAnime** oynatma sorunu değil. *(Arama ve bölüm listesi çalışıyor
      ama stream uçları şu an 404 dönüyor; bilinen kısıt.)*
- [ ] Sorun sunucu/arşiv tarafında değil. *(Sunucu ayrı bir depoda:
      [turkanime-server](https://github.com/barkeser2002/turkanime-server))*
- [ ] Aynı sorun için açılmış bir issue yok.

### Ekler

- Hata mesajının tam metni veya ekran görüntüsü
- Terminalden çalıştırıyorsanız konsol çıktısı

> Ekran görüntüsü veya log paylaşırken **çerez, token ve AniList giriş
> bilgilerinizi karartın.**
