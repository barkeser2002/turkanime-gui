/* Kitaplığım: İzlemeye Devam Et, Favoriler, Geçmiş (yerel, AniList'siz). */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  function ac(kayit) {
    TA.ac("kitaplik", { kayit: kayit });
  }

  var BOS = {
    devam: ["Henüz izlenen bir şey yok", "İzlediğin bölümler burada görünür; kaldığın yerden devam edersin.", "oynat"],
    favori: ["Favori yok", "Detay sayfasındaki “Kitaplığa Ekle” ile ekleyebilirsin.", "kalp"],
    gecmis: ["İzleme geçmişi boş", "Oynattığın bölümler burada sıralanır.", "saat"]
  };

  var kitaplik = {
    sekme: "devam",
    veri: null,

    kur: function (kap) {
      var self = this;
      this.ozet = h("p", null, "Yükleniyor…");
      this.sekmeler = h("div.sekmeler", { role: "tablist" });
      this.govde = h("div.kitaplik-govde");
      TA.ekle(kap, [
        h("header.sayfa-baslik", null,
          h("div", null, h("h1", null, "Kitaplığım"), this.ozet),
          h("div.sayfa-eylem", null,
            h("button.dugme.cerceve", { onclick: function () { self.yenile(); } }, TA.ikon("yenile"), "Yenile"))),
        this.sekmeler,
        this.govde
      ]);
      TA.dinle("gecmis_degisti", function () { if (TA.aktif === "library") self.yenile(); });
    },

    goster: function () {
      this.yenile();                 // her ziyarette: az önce izlenen görünmeli
    },

    yenile: function () {
      var self = this;
      if (!this.veri) {
        TA.bosalt(this.govde).appendChild(h("div.izgara", null, TA.iskeletKartlar(12)));
      }
      TA.cagir("kitaplik").then(function (veri) {
        self.veri = veri;
        self.ozet.textContent = veri.devam.length + " seri izleniyor • " + veri.favori.length + " favori";
        self.sekmeleriCiz();
        self.ciz();
      }, function (e) {
        TA.bosalt(self.govde).appendChild(TA.bosDurum({ hata: true, baslik: "Kitaplık okunamadı", metin: e.message }));
      });
    },

    sekmeleriCiz: function () {
      var self = this;
      TA.bosalt(this.sekmeler);
      [["devam", "İzlemeye Devam Et"], ["favori", "Favoriler"], ["gecmis", "Geçmiş"]].forEach(function (s) {
        self.sekmeler.appendChild(h("button.sekme" + (self.sekme === s[0] ? ".secili" : ""), {
          role: "tab", "aria-selected": self.sekme === s[0] ? "true" : "false",
          dataset: { sekme: s[0] },
          onclick: function () { self.sekme = s[0]; self.sekmeleriCiz(); self.ciz(); }
        }, s[1], h("span.cip-sayi", null, String(self.veri[s[0]].length))));
      });
    },

    ciz: function () {
      var liste = this.veri[this.sekme];
      TA.bosalt(this.govde);
      if (!liste.length) {
        var b = BOS[this.sekme];
        this.govde.appendChild(TA.bosDurum({ ikon: b[2], baslik: b[0], metin: b[1] }));
        return;
      }
      if (this.sekme === "gecmis") {
        this.govde.appendChild(h("ul.gecmis-listesi", null, liste.map(function (g) {
          return h("li", null, h("button.gecmis-satiri", { onclick: function () { ac(g.kayit); }, title: g.baslik + " — " + g.kaynak_adi },
            TA.poster(g.kapak, g.baslik, "gecmis-kapak"),
            h("span.gecmis-metin", null, h("b", null, g.baslik), h("span", null, g.bolum)),
            h("span.kaynak-hap", { style: { "--renk": g.kaynak_renk || "var(--soluk)" } }, g.kaynak_adi),
            h("span.gecmis-zaman", null, g.zaman)));
        })));
        return;
      }
      this.govde.appendChild(h("div.izgara", null, liste.map(function (k) {
        var veri = Object.assign({}, k, { alt: k.bolum_metni || k.alt, rozet_renk: k.kaynak_renk || k.rozet_renk });
        return TA.kart(veri, { tikla: function () { ac(k.kayit); } });
      })));
    }
  };

  TA.sayfa("library", kitaplik);
})();
