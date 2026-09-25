/* Arama: bütün kaynaklarda artımlı arama, kaynak hapları, gruplu sonuçlar.
 *
 * Akış Python'dan olaylarla geliyor (gui/web/uclar_arama.py):
 *   arama_kaynaklar → her kaynak için "aranıyor" hapı
 *   arama_kaynak    → o kaynağın kartları (ya da hatası)
 *   arama_bitti     → yetişemeyenler ve son özet
 * İstek numarasını sayfa üretiyor; eski aramanın geç olayları atılıyor.
 */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;
  var istekSayaci = 0;

  function sonucAc(veri) {
    TA.ac("sonuc", { kaynak: veri.kaynak, slug: veri.slug, baslik: veri.baslik, kayit: veri.kayit });
  }

  var arama = {
    istek: 0,
    sorgu: "",
    kaynaklar: [],     // sıralı kaynak bilgileri
    durumlar: {},      // ad → {durum: "bekliyor"|"tamam"|"hata", kartlar, hata}
    filtre: "",
    suruyor: false,

    kur: function (kap) {
      var self = this;
      this.girdi = h("input", {
        type: "search",
        placeholder: "Anime adı… (ör. Frieren, Jujutsu Kaisen)",
        "aria-label": "Anime ara",
        autocomplete: "off"
      });
      this.dugme = h("button.dugme.birincil", { type: "submit" }, TA.ikon("ara"), "Ara");
      this.ozet = h("p", null, "Bütün kaynaklarda aynı anda aranır; sonuçlar geldikçe görünür.");
      this.cipler = h("div.kaynak-cipleri");
      this.uyari = h("p.arama-uyari");
      this.sonuclar = h("div.arama-sonuclari");
      this.bos = h("div.arama-bos");

      TA.ekle(kap, [
        h("header.sayfa-baslik", null,
          h("div", null, h("h1", null, "Arama"), this.ozet)),
        h("form.arama-cubugu", {
          onsubmit: function (e) {
            e.preventDefault();
            var sorgu = self.girdi.value.trim();
            if (sorgu) TA.ac("arama", { sorgu: sorgu });
          }
        }, TA.ikon("ara"), this.girdi, this.dugme),
        this.cipler,
        this.uyari,
        this.bos,
        this.sonuclar
      ]);
      this.baslangicDurumu();

      TA.dinle("arama_kaynaklar", this.kaynaklarGeldi.bind(this));
      TA.dinle("arama_kaynak", this.kaynakGeldi.bind(this));
      TA.dinle("arama_bitti", this.bitti.bind(this));
    },

    goster: function (p) {
      if (p.sorgu && p.sorgu !== this.sorgu) {
        this.baslat(p.sorgu);
      } else if (!this.sorgu) {
        this.girdi.focus();
      }
    },

    baslangicDurumu: function () {
      TA.bosalt(this.bos).appendChild(TA.bosDurum({
        ikon: "ara",
        baslik: "Ne izlemek istersin?",
        metin: "Anime adını yaz; TürkAnime arşivi ve bütün Türkçe kaynaklar birlikte aranır."
      }));
    },

    baslat: function (sorgu) {
      var self = this;
      this.sorgu = sorgu;
      this.girdi.value = sorgu;
      this.istek = ++istekSayaci;
      this.kaynaklar = [];
      this.durumlar = {};
      this.filtre = "";
      this.suruyor = true;
      this.dugme.disabled = true;
      TA.bosalt(this.cipler);
      TA.bosalt(this.sonuclar);
      TA.bosalt(this.bos);
      TA.bosalt(this.uyari);
      this.ozetYaz();
      var istek = this.istek;
      TA.cagir("ara", { sorgu: sorgu, istek: istek }).catch(function (e) {
        if (istek !== self.istek) return;
        self.suruyor = false;
        self.dugme.disabled = false;
        self.bos.appendChild(TA.bosDurum({ hata: true, baslik: "Aranamadı", metin: e.message }));
        self.ozetYaz();
      });
    },

    // ── Olaylar ─────────────────────────────────────────────────────────────
    kaynaklarGeldi: function (v) {
      if (v.istek !== this.istek) return;
      this.kaynaklar = v.kaynaklar;
      v.kaynaklar.forEach(function (k) {
        if (!this.durumlar[k.ad]) this.durumlar[k.ad] = { durum: "bekliyor", kartlar: [], hata: "" };
      }, this);
      this.cipleriCiz();
      this.ozetYaz();
    },

    kaynakGeldi: function (v) {
      if (v.istek !== this.istek) return;
      var ad = v.kaynak.ad;
      if (!this.kaynaklar.some(function (k) { return k.ad === ad; })) this.kaynaklar.push(v.kaynak);
      this.durumlar[ad] = { durum: v.hata ? "hata" : "tamam", kartlar: v.kartlar, hata: v.hata };
      this.cipleriCiz();
      this.gruplariCiz();
      this.ozetYaz();
    },

    bitti: function (v) {
      if (v.istek !== this.istek) return;
      this.suruyor = false;
      this.dugme.disabled = false;
      Object.keys(v.hatalar || {}).forEach(function (ad) {
        var d = this.durumlar[ad] || (this.durumlar[ad] = { kartlar: [] });
        if (!d.kartlar.length) {
          d.durum = "hata";
          d.hata = v.hatalar[ad];
        }
      }, this);
      Object.keys(this.durumlar).forEach(function (ad) {
        if (this.durumlar[ad].durum === "bekliyor") this.durumlar[ad].durum = "tamam";
      }, this);
      this.cipleriCiz();
      this.gruplariCiz();
      this.ozetYaz(v.hata);
    },

    // ── Çizim ───────────────────────────────────────────────────────────────
    toplam: function () {
      var self = this;
      return Object.keys(this.durumlar).reduce(function (t, ad) {
        return t + self.durumlar[ad].kartlar.length;
      }, 0);
    },

    ozetYaz: function (genelHata) {
      var toplam = this.toplam();
      var bekleyen = Object.keys(this.durumlar).filter(function (ad) {
        return this.durumlar[ad].durum === "bekliyor";
      }, this).length;
      var metin;
      if (!this.sorgu) {
        return;
      } else if (this.suruyor) {
        metin = "“" + this.sorgu + "” aranıyor… " +
          (toplam ? toplam + " sonuç" + (bekleyen ? " · " + bekleyen + " kaynak bekleniyor" : "")
                  : bekleyen ? bekleyen + " kaynak bekleniyor" : "");
      } else {
        var kaynakli = Object.keys(this.durumlar).filter(function (ad) {
          return this.durumlar[ad].kartlar.length;
        }, this).length;
        metin = toplam ? "“" + this.sorgu + "” için " + toplam + " sonuç · " + kaynakli + " kaynakta"
                       : "“" + this.sorgu + "” için sonuç bulunamadı";
      }
      this.ozet.textContent = metin;
      this.uyariYaz(toplam);
      if (!this.suruyor) {
        TA.bosalt(this.bos);
        if (genelHata) {
          this.bos.appendChild(TA.bosDurum({ hata: true, baslik: "Arama hatası", metin: genelHata }));
        } else if (!toplam) {
          this.bos.appendChild(this.sonucYok());
        }
      }
    },

    // Sonuç varken aranamayan kaynaklar tek satırda (sonuç yoksa boş durum
    // kutusu sebepleri zaten listeliyor).
    uyariYaz: function (toplam) {
      TA.bosalt(this.uyari);
      if (!toplam) return;
      var hatalilar = this.kaynaklar.filter(function (k) {
        return this.durumlar[k.ad] && this.durumlar[k.ad].durum === "hata";
      }, this);
      if (!hatalilar.length) return;
      TA.ekle(this.uyari, [TA.ikon("uyari"), "Aranamayan: "].concat(hatalilar.map(function (k, i) {
        return [i ? " · " : "", h("b", null, k.etiket), " — " + this.durumlar[k.ad].hata];
      }, this)));
    },

    sonucYok: function () {
      var hatalilar = this.kaynaklar.filter(function (k) {
        return this.durumlar[k.ad] && this.durumlar[k.ad].durum === "hata";
      }, this);
      var liste = hatalilar.length ? h("ul.hata-listesi", null, hatalilar.map(function (k) {
        return h("li", null, h("b", null, k.etiket), " — ", this.durumlar[k.ad].hata);
      }, this)) : null;
      var kutu = TA.bosDurum({
        ikon: "ara",
        baslik: "Sonuç bulunamadı",
        metin: hatalilar.length ? "Bazı kaynaklar aranamadı:" : "Farklı bir yazım ya da İngilizce/Japonca adı deneyin."
      });
      if (liste) kutu.appendChild(liste);
      return kutu;
    },

    cipleriCiz: function () {
      var self = this;
      TA.bosalt(this.cipler);
      if (!this.kaynaklar.length) return;
      var toplam = this.toplam();
      this.cipler.appendChild(h("button.cip" + (this.filtre ? "" : ".secili"), {
        onclick: function () { self.filtrele(""); }
      }, "Tümü", h("span.cip-sayi", null, String(toplam))));
      this.kaynaklar.forEach(function (k) {
        var d = this.durumlar[k.ad] || { durum: "bekliyor", kartlar: [] };
        var ek;
        if (d.durum === "bekliyor") ek = h("span.donen");
        else if (d.durum === "hata") ek = TA.ikon("uyari", "cip-uyari");
        else ek = h("span.cip-sayi", null, String(d.kartlar.length));
        var bos = d.durum === "tamam" && !d.kartlar.length;
        this.cipler.appendChild(h("button.cip" + (this.filtre === k.ad ? ".secili" : "") + (bos ? ".sonucsuz" : "") + (d.durum === "hata" ? ".hatali" : ""), {
          style: { "--renk": k.renk || "var(--soluk)" },
          title: d.durum === "hata" ? k.etiket + ": " + d.hata : k.etiket,
          disabled: bos,
          onclick: function () { if (d.kartlar.length) self.filtrele(self.filtre === k.ad ? "" : k.ad); }
        }, h("i.cip-nokta"), k.etiket, ek));
      }, this);
    },

    filtrele: function (ad) {
      this.filtre = ad;
      this.cipleriCiz();
      this.sonuclar.querySelectorAll(".sonuc-grubu").forEach(function (g) {
        g.hidden = !!ad && g.dataset.kaynak !== ad;
      });
    },

    gruplariCiz: function () {
      // Mevcut grupların DOM'u korunuyor (görseller yeniden yüklenmesin);
      // yalnızca yeni gelen grup ekleniyor ve sıra kayıt sırasına göre.
      var mevcut = {};
      this.sonuclar.querySelectorAll(".sonuc-grubu").forEach(function (g) { mevcut[g.dataset.kaynak] = g; });
      this.kaynaklar.forEach(function (k) {
        var d = this.durumlar[k.ad];
        if (!d || !d.kartlar.length) return;
        var grup = mevcut[k.ad];
        if (!grup) {
          grup = h("section.sonuc-grubu", { dataset: { kaynak: k.ad } },
            h("header.grup-baslik", null,
              h("span.kaynak-hap.buyuk", { style: { "--renk": k.renk || "var(--soluk)" } }, k.etiket),
              h("span.soluk", null, d.kartlar.length + " sonuç"),
              k.metadata ? h("span.hap", { title: "Bu kaynak yalnızca bilgi verir; bölümler eşleştirilen kaynaklardan gelir." }, TA.ikon("bilgi"), "yalnızca bilgi") : null),
            h("div.izgara", null, d.kartlar.map(function (veri) {
              return TA.kart(veri, { tikla: sonucAc, rozetRenk: k.renk });
            })));
          grup.hidden = !!this.filtre && this.filtre !== k.ad;
        }
        this.sonuclar.appendChild(grup);     // appendChild taşır: sıra korunur
      }, this);
    }
  };

  TA.sayfa("search", arama);
})();
