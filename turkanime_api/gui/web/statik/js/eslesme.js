/* "İstediğin anime değil mi?" penceresi: kaynaklarda arayıp doğru kaydı seç.
 *
 * Arama sayfasıyla aynı `ara` ucunu ve olayları kullanıyor; kendi istek
 * numarasıyla dinlediği için sayfadaki aramayla karışmıyor. Yalnızca bilgi
 * kaynakları (AniList) listelenmiyor: bölüm onlardan gelmez.
 */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  TA.kaynakBilgileri = {};
  TA.kaynakEtiketi = function (ad) {
    return (TA.kaynakBilgileri[ad] || {}).etiket || ad;
  };

  var acik = null;

  TA.eslesmePenceresi = function (secenek) {
    if (acik) acik.kapat();
    var istek = 0;
    var gruplar = {};
    var kaynakSirasi = [];
    var filtre = secenek.kaynak || "";
    var cozuculer = [];

    var girdi = h("input", { type: "search", value: secenek.sorgu || "", "aria-label": "Anime adı", autocomplete: "off" });
    var dugme = h("button.dugme.birincil.kucuk", { type: "submit" }, "Ara");
    var durum = h("p.modal-durum.soluk");
    var cipler = h("div.kaynak-cipleri.modal-cipler");
    var sonuclar = h("div.modal-sonuclar");

    function kapat() {
      cozuculer.forEach(function (c) { c(); });
      ortu.remove();
      document.removeEventListener("keydown", tus);
      acik = null;
    }

    function tus(e) {
      if (e.key === "Escape") kapat();
    }

    function ara() {
      var sorgu = girdi.value.trim();
      if (!sorgu) return;
      istek = TA.yeniIstek();
      gruplar = {};
      kaynakSirasi = [];
      TA.bosalt(sonuclar);
      TA.bosalt(cipler);
      durum.textContent = "Kaynaklarda aranıyor…";
      dugme.disabled = true;
      TA.cagir("ara", { sorgu: sorgu, istek: istek }).catch(function (e) {
        durum.textContent = e.message;
        dugme.disabled = false;
      });
    }

    function ciz() {
      TA.bosalt(cipler);
      TA.bosalt(sonuclar);
      var dolu = kaynakSirasi.filter(function (k) { return (gruplar[k.ad] || []).length; });
      if (dolu.length > 1) {
        cipler.appendChild(h("button.cip" + (filtre ? "" : ".secili"), { onclick: function () { filtre = ""; ciz(); } }, "Tümü"));
        dolu.forEach(function (k) {
          cipler.appendChild(h("button.cip" + (filtre === k.ad ? ".secili" : ""), {
            style: { "--renk": k.renk || "var(--soluk)" },
            onclick: function () { filtre = k.ad; ciz(); }
          }, h("i.cip-nokta"), k.etiket, h("span.cip-sayi", null, String(gruplar[k.ad].length))));
        });
      }
      dolu.forEach(function (k) {
        if (filtre && filtre !== k.ad && dolu.some(function (x) { return x.ad === filtre; })) return;
        sonuclar.appendChild(h("section.modal-grup", null,
          h("h4", null, h("span.kaynak-hap", { style: { "--renk": k.renk || "var(--soluk)" } }, k.etiket)),
          h("ul", null, gruplar[k.ad].map(function (s) {
            return h("li", null, h("button.modal-sonuc", {
              onclick: function (e) { sec(e.currentTarget, s); }
            }, TA.poster(s.kapak, s.baslik, "modal-kapak"), h("span", null, s.baslik), TA.ikon("sag")));
          }))));
      });
    }

    function sec(dugmeEl, s) {
      dugmeEl.disabled = true;
      Promise.resolve(secenek.sec({ kaynak: s.kaynak, slug: s.slug, baslik: s.baslik })).then(function () {
        kapat();
      }, function (e) {
        dugmeEl.disabled = false;
        TA.bildir(e.message, "hata");
      });
    }

    cozuculer.push(TA.dinle("arama_kaynaklar", function (v) {
      if (v.istek !== istek) return;
      kaynakSirasi = v.kaynaklar.filter(function (k) { return !k.metadata; });
    }));
    cozuculer.push(TA.dinle("arama_kaynak", function (v) {
      if (v.istek !== istek || v.kaynak.metadata) return;
      if (!kaynakSirasi.some(function (k) { return k.ad === v.kaynak.ad; })) kaynakSirasi.push(v.kaynak);
      gruplar[v.kaynak.ad] = v.kartlar;
      ciz();
    }));
    cozuculer.push(TA.dinle("arama_bitti", function (v) {
      if (v.istek !== istek) return;
      dugme.disabled = false;
      var toplam = Object.keys(gruplar).reduce(function (t, ad) { return t + gruplar[ad].length; }, 0);
      durum.textContent = v.hata ? "Arama hatası: " + v.hata
        : toplam ? toplam + " aday — doğru olanı seç." : "Hiçbir kaynakta sonuç bulunamadı.";
    }));

    var pencere = h("div.modal", { role: "dialog", "aria-modal": "true", "aria-label": "İstediğin anime değil mi?" },
      h("header.modal-baslik", null,
        h("div", null, h("h3", null, "İstediğin anime değil mi?"),
          h("p.soluk", null, "Doğru kaydı seç; bölümler o kaynaktan gelir.")),
        h("button.ikon-dugme", { onclick: kapat, "aria-label": "Kapat", title: "Kapat" }, TA.ikon("kapat"))),
      h("form.arama-cubugu.modal-arama", { onsubmit: function (e) { e.preventDefault(); ara(); } },
        TA.ikon("ara"), girdi, dugme),
      durum, cipler, sonuclar);
    var ortu = h("div.modal-ortu", { onclick: function (e) { if (e.target === ortu) kapat(); } }, pencere);
    document.body.appendChild(ortu);
    document.addEventListener("keydown", tus);
    acik = { kapat: kapat };
    girdi.focus();
    girdi.select();
    ara();
    return acik;
  };
})();
