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

  TA.baglan().then(function (bagli) {
    if (TA.kabuk !== "qt") TA.kabukKur();
    // Python'un bağlanmadan önce sorduğu pencereler (açılış denetimi, ...):
    // o sıradaki olaylar kayboldu, açık soruların hepsi buradan çekiliyor.
    if (bagli) TA.sorulariAl();
    // Kaynak etiketleri/renkleri (hap ve akordiyon başlıkları için).
    TA.cagir("kaynaklar").then(function (liste) {
      liste.forEach(function (k) { TA.kaynakBilgileri[k.ad] = k; });
    }, function () {});
    var rota = (window.location.hash || "").replace(/^#\/?/, "");
    if (!TA.aktif) TA.git(rota || "home");
  });
})();
