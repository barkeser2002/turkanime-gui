/* Açılış: köprüye bağlan, Python olaylarını dinle, ilk sayfayı aç.
 *
 * Python rotayı `TA.git` ile değiştiriyor (bkz. gui/web/gorunum.py). Sayfa
 * yüklenmeden gelen istek Python'da bekletiliyor; burada yalnızca adresteki
 * `#/rota` (geliştirme/önizleme) ya da varsayılan ana sayfa açılıyor.
 */
(function () {
  "use strict";

  var TA = window.TA;

  TA.dinle("bildirim", function (v) {
    TA.bildir(v.mesaj, v.tur, v.sure);
  });

  TA.baglan().then(function () {
    var rota = (window.location.hash || "").replace(/^#\/?/, "");
    if (!TA.aktif) TA.git(rota || "home");
  });
})();
