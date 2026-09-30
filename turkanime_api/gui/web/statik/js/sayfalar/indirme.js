/* İndirilenler: kuyruk, ilerleme, duraklat/sürdür/iptal/yeniden dene, oynat. */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  var DURUM = {
    "bekliyor": ["Bekliyor", "gri", "saat"],
    "indiriliyor": ["İndiriliyor", "mavi", "indir"],
    "duraklatıldı": ["Duraklatıldı", "sari", "saat"],
    "tamamlandı": ["Tamamlandı", "yesil", "tamam"],
    "hata": ["Hata", "kirmizi", "uyari"],
    "iptal edildi": ["İptal edildi", "turuncu", "kapat"]
  };
  var BITMIS = ["tamamlandı", "hata", "iptal edildi"];

  var indirme = {
    satirlar: {},      // id → veri
    elemanlar: {},     // id → {kok, bar, detay, cip, eylem}
    sira: [],

    kur: function (kap) {
      var self = this;
      this.ozet = h("p", null, "Henüz indirme yok.");
      this.eylemler = h("div.sayfa-eylem.sarili");
      this.liste = h("div.indirme-listesi");
      this.bos = h("div");
      TA.ekle(kap, [
        h("header.sayfa-baslik", null, h("div", null, h("h1", null, "İndirilenler"), this.ozet), this.eylemler),
        this.bos, this.liste
      ]);
      TA.dinle("indirme_satir", function (v) { self.satirGuncelle(v.satir); });
      TA.dinle("indirme_ilerleme", function (v) {
        v.satirlar.forEach(function (s) {
          var satir = self.satirlar[s.id];
          if (!satir) return;
          satir.yuzde = s.yuzde;
          satir.detay = s.detay;
          self.ilerlemeYaz(s.id);
        });
      });
      TA.dinle("indirme_silindi", function (v) {
        v.idler.forEach(function (id) {
          if (self.elemanlar[id]) self.elemanlar[id].kok.remove();
          delete self.elemanlar[id];
          delete self.satirlar[id];
          self.sira = self.sira.filter(function (x) { return x !== id; });
        });
        self.ozetYaz();
      });
    },

    goster: function () {
      var self = this;
      TA.cagir("indirmeler").then(function (v) {
        self.klasor = v.klasor;
        v.satirlar.forEach(function (s) { self.satirGuncelle(s); });
        self.ozetYaz();
      });
    },

    satirGuncelle: function (s) {
      var yeni = !this.satirlar[s.id];
      this.satirlar[s.id] = s;
      if (yeni) {
        this.sira.push(s.id);
        this.satirKur(s.id);
      }
      this.satirYaz(s.id);
      this.ozetYaz();
    },

    satirKur: function (id) {
      var el = {};
      el.cip = h("span.durum-cipi");
      el.baslik = h("b.indirme-baslik");
      el.detay = h("span.indirme-detay");
      el.yuzde = h("span.indirme-yuzde");
      el.bar = h("i");
      el.eylem = h("div.indirme-eylem");
      el.kok = h("article.indirme-satiri", { dataset: { id: id } },
        h("div.indirme-ust", null,
          h("div.indirme-bilgi", null, el.baslik, h("div.indirme-alt", null, el.cip, el.detay)),
          el.yuzde, el.eylem),
        h("div.indirme-cubuk", null, el.bar));
      this.elemanlar[id] = el;
      this.liste.insertBefore(el.kok, this.liste.firstChild);   // en yeni üstte
    },

    ilerlemeYaz: function (id) {
      var s = this.satirlar[id], el = this.elemanlar[id];
      if (!s || !el) return;
      el.bar.style.width = (s.yuzde || 0) + "%";
      el.yuzde.textContent = s.durum === "indiriliyor" || s.yuzde ? (s.yuzde || 0) + "%" : "";
      if (!s.mesaj || s.durum === "indiriliyor") el.detay.textContent = s.detay || "";
    },

    satirYaz: function (id) {
      var self = this;
      var s = this.satirlar[id], el = this.elemanlar[id];
      var d = DURUM[s.durum] || [s.durum, "gri", "bilgi"];
      el.kok.className = "indirme-satiri " + d[1];
      el.baslik.textContent = s.baslik;
      el.baslik.title = s.baslik;
      TA.bosalt(el.cip);
      TA.ekle(el.cip, [s.durum === "indiriliyor" ? h("span.donen") : TA.ikon(d[2]), d[0]]);
      el.detay.textContent = BITMIS.indexOf(s.durum) >= 0 ? s.mesaj
        : s.durum === "duraklatıldı" ? "“Devam et” kaldığı yerden sürdürür" : (s.detay || "");
      el.detay.title = s.ayrinti || "";
      this.ilerlemeYaz(id);

      function dugme(etiket, ikon, eylem, sinif) {
        return h("button.dugme.kucuk." + (sinif || "hayalet"), {
          onclick: function () {
            TA.cagir("indirme_eylem", { id: id, eylem: eylem }).catch(function (e) { TA.bildir(e.message, "hata"); });
          }
        }, TA.ikon(ikon), etiket);
      }
      TA.bosalt(el.eylem);
      if (s.durum === "bekliyor" || s.durum === "indiriliyor") el.eylem.appendChild(dugme("Duraklat", "saat", "duraklat"));
      if (s.durum === "duraklatıldı") el.eylem.appendChild(dugme("Devam et", "oynat", "devam", "birincil"));
      if (BITMIS.indexOf(s.durum) < 0) el.eylem.appendChild(dugme("İptal", "kapat", "iptal"));
      if (s.durum === "hata" || s.durum === "iptal edildi") el.eylem.appendChild(dugme("Yeniden Dene", "yenile", "tekrar", "cerceve"));
      if (s.durum === "tamamlandı") {
        el.eylem.appendChild(dugme("Oynat", "oynat", "oynat", "birincil"));
        el.eylem.appendChild(dugme("Klasörü Aç", "kutuphane", "klasor"));
      }
      void self;
    },

    ozetYaz: function () {
      var self = this;
      var hepsi = this.sira.map(function (id) { return self.satirlar[id]; }).filter(Boolean);
      var duran = hepsi.filter(function (s) { return s.durum === "duraklatıldı"; }).length;
      var aktif = hepsi.filter(function (s) { return s.durum === "bekliyor" || s.durum === "indiriliyor"; }).length;
      var biten = hepsi.filter(function (s) { return BITMIS.indexOf(s.durum) >= 0; });
      var hatali = biten.filter(function (s) { return !s.ok; }).length;
      var metin;
      if (duran) metin = aktif + " aktif, " + duran + " duraklatıldı / " + hepsi.length + " toplam";
      else if (aktif) metin = aktif + " aktif / " + hepsi.length + " toplam";
      else if (hepsi.length) metin = hatali ? (hepsi.length - hatali) + " tamamlandı, " + hatali + " başarısız" : hepsi.length + " iş tamamlandı";
      else metin = "Henüz indirme yok.";
      this.ozet.textContent = metin;

      function toplu(etiket, ikon, eylem, sinif) {
        return h("button.dugme." + (sinif || "cerceve"), {
          onclick: function () {
            TA.cagir("indirme_toplu", { eylem: eylem }).catch(function (e) { TA.bildir(e.message, "hata"); });
          }
        }, TA.ikon(ikon), etiket);
      }
      TA.bosalt(this.eylemler);
      if (duran) this.eylemler.appendChild(toplu("Tümünü Sürdür", "oynat", "surdur", "birincil"));
      if (aktif) this.eylemler.appendChild(toplu("Tümünü Duraklat", "saat", "duraklat"));
      if (aktif || duran) this.eylemler.appendChild(toplu("Tümünü İptal", "kapat", "iptal"));
      if (biten.length) this.eylemler.appendChild(toplu("Tamamlananları Temizle", "tamam", "temizle"));
      this.eylemler.appendChild(toplu("İndirme Klasörü", "kutuphane", "klasor"));

      TA.bosalt(this.bos);
      if (!hepsi.length) {
        this.bos.appendChild(TA.bosDurum({
          ikon: "indir", baslik: "Henüz indirme yok",
          metin: "Detay sayfasında bölüm seçip “Seçilenleri İndir”e bastığında indirmeler burada görünür."
        }));
      }
    }
  };

  TA.sayfa("downloads", indirme);
})();
