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

Geçiş sayfa sayfa yapılıyor: taşınan sayfalar tek bir `WebGorunum`'da
rota olarak açılıyor, taşınmayanlar eski Qt sayfası olarak kalıyor.
"""
