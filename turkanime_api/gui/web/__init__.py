"""Web arayüzü — HTML/CSS/JS sayfaları, PySide6 penceresinin içinde.

Sayfalar QtWebEngine'de (Chromium) çiziliyor; Python ile konuşmaları
QWebChannel üzerinden. Yeni bağımlılık yok: QtWebEngine zaten çerez penceresi
ve Cloudflare çözücü için pakette.

Katmanlar:

* `sema`     — ``ta://`` URL şeması. ``ta://uygulama/...`` arayüz dosyalarını
               (``statik/``), ``ta://gorsel/?u=...`` kapak görsellerini
               (`gui.qt.gorsel` önbelleği) sunuyor.
* `kopru`    — JS → Python çağrıları (``cagir`` → ``yanit``) ve Python → JS
               olayları (``olay``). Uçlar (``@uc``) alan başına sınıflarda.
* `gorunum`  — `WebGorunum`: köprüyü ve şemayı kurulu tek QWebEngineView.
* `uclar_*`  — sayfaların Python tarafı (keşif, arama, ...). Qt'siz iş
               mantığı mevcut modüllerden (`common/`, `sources/`) geliyor.
* `sorular`  — Python'dan sayfaya soru (modal pencere) ve cevabının geri
               çağrıyla dönüşü; eski Qt diyaloglarının ``exec()``'i yerine.
* `pencereler`, `katki` — o pencerelerin Python tarafı (ilerleme,
               güncelleme, gereksinim, kapanış; kimlik bağışı onayı).

Bütün sayfalar ve küçük pencereler tek bir `WebGorunum`'da. Qt'de kalan tek
ayrı pencere "Erişimi aç" tarayıcısı (`gui.qt.erisim_penceresi`; TRAnimeİzle'nin
çerez penceresi `gui.qt.cookie_browser` onun bir yapılandırması): dış sitenin
bot doğrulaması için gerçek bir tarayıcı penceresi.
"""
