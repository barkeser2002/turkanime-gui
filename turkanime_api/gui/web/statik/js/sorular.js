/* Python'un soruları: Qt diyaloglarının yerini alan pencereler.
 *
 * Python `SoruMerkezi.sor` ile ``soru`` olayı yayar ({id, tur, veri}); burası
 * türün çizicisini (`TA.soruTurleri[tur]`, pencereler.js) çağırır. Cevap
 * ``soru_cevapla``, pencere içi eylem ``soru_eylem`` ile döner. Python açık
 * pencereyi ``soru_guncelle`` ile tazeler, ``soru_kapat`` ile kapatır.
 * Sözleşmenin tamamı: gui/web/sorular.py.
 *
 * Sayfa köprüye bağlanmadan yayılan olaylar kayboluyor; bu yüzden bağlanınca
 * (baslat.js) `TA.sorulariAl` açık soruların hepsini çekiyor. Aynı soru olayla
 * ve çekişle iki kez gelebilir: kimliğe göre tekilleşiyor.
 *
 * Çizici: function (veri, api) -> {kapat(), guncelle(degisen)?}
 *   api.cevapla(cevap) → Promise; Python kabul ederse pencere kapanır,
 *                        reddederse (`UcHatasi`) hata mesajıyla reddeder —
 *                        pencere açık kalır, mesajı çizici gösterir.
 *   api.vazgec(cevap)  → pencereyi HEMEN kapatır ve "vazgeç" cevabını yollar
 *                        (Esc, ×, "Vazgeç"; başarısız olamaz).
 *   api.eylem(ad, veri)→ Promise (pencere açık kalır).
 */
(function () {
  "use strict";

  var TA = window.TA;

  TA.soruTurleri = TA.soruTurleri || {};
  var acik = new Map();          // soru kimliği → {pencere: {kapat, guncelle}}

  function kapat(kimlik) {
    var kayit = acik.get(kimlik);
    if (!kayit) return;
    acik.delete(kimlik);
    if (kayit.pencere && kayit.pencere.kapat) kayit.pencere.kapat();
  }

  function yolla(kimlik, cevap, varsayilanla) {
    return TA.cagir("soru_cevapla", {
      kimlik: kimlik, cevap: cevap === undefined ? null : cevap, varsayilanla: !!varsayilanla
    });
  }

  function goster(soru) {
    if (!soru || !soru.id || acik.has(soru.id)) return;
    var ciz = TA.soruTurleri[soru.tur];
    if (!ciz) {
      // Çizilemeyen soru Python'da sonsuza dek açık kalmasın: varsayılanla
      // bitsin (bağış onayında "onay yok", kapanışta "Evet").
      console.error("bilinmeyen soru türü:", soru.tur);
      yolla(soru.id, null, true).catch(function () {});
      return;
    }
    var kayit = { pencere: null };
    acik.set(soru.id, kayit);
    var api = {
      cevapla: function (cevap) {
        return yolla(soru.id, cevap).then(function () { kapat(soru.id); });
      },
      vazgec: function (cevap) {
        kapat(soru.id);
        return yolla(soru.id, cevap).catch(function (e) { console.error("soru cevabı", e); });
      },
      eylem: function (ad, veri) {
        return TA.cagir("soru_eylem", { kimlik: soru.id, ad: ad, veri: veri || {} });
      }
    };
    try {
      kayit.pencere = ciz(soru.veri || {}, api) || {};
    } catch (e) {
      console.error("soru penceresi çizilemedi:", soru.tur, e);
      acik.delete(soru.id);
      yolla(soru.id, null, true).catch(function () {});
    }
  }

  TA.dinle("soru", goster);
  TA.dinle("soru_guncelle", function (v) {
    var kayit = acik.get(v.id);
    if (kayit && kayit.pencere && kayit.pencere.guncelle) kayit.pencere.guncelle(v.veri || {});
  });
  TA.dinle("soru_kapat", function (v) { kapat(v.id); });

  // Bağlanınca bir kez (baslat.js): kaçan olaylar dahil açık sorular.
  TA.sorulariAl = function () {
    return TA.cagir("bekleyen_sorular").then(function (liste) {
      (liste || []).forEach(goster);
    }, function (e) { console.error("bekleyen sorular alınamadı", e); });
  };

  // Testler ve hata ayıklama: açık soru pencereleri.
  TA.acikSorular = function () {
    return Array.from(acik.keys());
  };
})();
