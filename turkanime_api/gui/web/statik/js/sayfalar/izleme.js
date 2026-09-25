/* İzleme Listem: AniList hesabındaki listeler (durum sekmeleriyle). */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  var izleme = {
    durum: "CURRENT",
    yuklendi: false,

    kur: function (kap) {
      var self = this;
      this.alt = h("p", null, "AniList hesabındaki listelerin");
      this.sekmeler = h("div.sekmeler", { role: "tablist" });
      this.durumEl = h("span.durum");
      this.senkronDugme = h("button.dugme.cerceve", {
        onclick: function () { self.senkron(); },
        title: "AniList'teki ilerlemeyi yerel geçmişe uygular"
      }, TA.ikon("yenile"), "Senkronize Et");
      this.yenileDugme = h("button.dugme.cerceve", { onclick: function () { self.yenile(); } }, TA.ikon("yenile"), "Yenile");
      this.giris = h("div.giris-paneli");
      this.izgara = h("div.izgara");
      this.bos = h("div");
      TA.ekle(kap, [
        h("header.sayfa-baslik", null,
          h("div", null, h("h1", null, "İzleme Listem"), this.alt),
          h("div.sayfa-eylem", null, this.durumEl, this.senkronDugme, this.yenileDugme)),
        this.sekmeler, this.giris, this.bos, this.izgara
      ]);

      TA.dinle("izleme_listesi", function (v) {
        if (v.durum !== self.durum) return;         // kullanıcı sekme değiştirdi
        self.yenileDugme.disabled = false;
        TA.bosalt(self.izgara);
        TA.bosalt(self.bos);
        if (!v.kartlar.length) {
          self.durumYaz("");
          self.bos.appendChild(TA.bosDurum({ ikon: "kutuphane", baslik: "Bu listede anime yok" }));
          return;
        }
        TA.ekle(self.izgara, v.kartlar.map(function (k) {
          return TA.kart(k, { tikla: function () { TA.ac("anime", { kayit: k.kayit }); } });
        }));
        self.durumYaz(v.kartlar.length + " anime", "tamam");
      });
      TA.dinle("izleme_hatasi", function (v) {
        self.yenileDugme.disabled = false;
        TA.bosalt(self.izgara);
        self.durumYaz("");
        TA.bosalt(self.bos).appendChild(TA.bosDurum({ hata: true, baslik: "Liste alınamadı", metin: v.mesaj,
          eylem: { etiket: "Tekrar dene", fn: function () { self.yenile(); } } }));
      });
      TA.dinle("izleme_senkron", function (v) {
        var metin = v.sayi ? v.sayi + " serinin ilerlemesi yerele işlendi." : "Yerel ilerleme zaten güncel.";
        if (self.senkronIstendi) {
          self.senkronIstendi = false;
          self.durumYaz(metin, "tamam");
          TA.bildir(metin, "tamam");
        } else if (v.sayi) {
          // Girişten sonraki kendiliğinden senkron: durum satırı ("12 anime")
          // kullanıcının; yalnızca değişiklik olduysa haber ver.
          TA.bildir(metin, "tamam");
        }
      });
      TA.dinle("anilist_giris", function (d) {
        self.durumUygula(d);
        if (d.giris && TA.aktif === "watchlist") self.yenile();
      });
    },

    goster: function () {
      var self = this;
      TA.cagir("izleme_durumu").then(function (d) {
        self.durumUygula(d);
        if (d.giris && !self.yuklendi) self.yenile();
      });
    },

    durumYaz: function (metin, tur) {
      this.durumEl.className = "durum" + (tur ? " " + tur : "");
      TA.bosalt(this.durumEl);
      if (tur === "suruyor") this.durumEl.appendChild(h("span.donen"));
      if (metin) this.durumEl.appendChild(document.createTextNode(metin));
    },

    durumUygula: function (d) {
      var self = this;
      this.girisli = d.giris;
      this.alt.textContent = d.giris && d.ad ? d.ad + " — AniList hesabındaki listelerin" : "AniList hesabındaki listelerin";
      this.senkronDugme.disabled = !d.giris;
      this.yenileDugme.disabled = !d.giris;
      TA.bosalt(this.sekmeler);
      this.sekmeler.hidden = !d.giris;
      d.durumlar.forEach(function (s) {
        self.sekmeler.appendChild(h("button.sekme" + (self.durum === s.kod ? ".secili" : ""), {
          role: "tab", dataset: { durum: s.kod },
          onclick: function () {
            self.durum = s.kod;
            self.durumUygula(d);
            self.yenile();
          }
        }, h("i.cip-nokta", { style: { "--renk": s.renk } }), s.etiket));
      });
      TA.bosalt(this.giris);
      if (!d.giris) {
        TA.bosalt(this.izgara);
        TA.bosalt(this.bos);
        this.yuklendi = false;
        this.durumYaz("Giriş yapılmamış");
        this.giris.appendChild(TA.bosDurum({
          ikon: "kutuphane",
          baslik: "AniList girişi gerekli",
          metin: "İzleme listeni görebilmek için Ayarlar'dan AniList'e giriş yap.",
          eylem: { etiket: "Ayarlar'a Git", ikon: "ayar", fn: function () { TA.ac("sayfa", { ad: "settings" }); } }
        }));
      }
    },

    yenile: function () {
      var self = this;
      if (!this.girisli) return;
      this.yuklendi = true;
      this.yenileDugme.disabled = true;
      TA.bosalt(this.bos);
      TA.bosalt(this.izgara);
      TA.ekle(this.izgara, TA.iskeletKartlar(12));
      this.durumYaz("Yükleniyor…", "suruyor");
      TA.cagir("izleme_listesi", { durum: this.durum }).catch(function (e) {
        self.yenileDugme.disabled = false;
        TA.bosalt(self.izgara);
        self.durumYaz(e.message, "hata");
      });
    },

    senkron: function () {
      var self = this;
      TA.cagir("izleme_senkron").then(function (ok) {
        if (!ok) return;
        self.senkronIstendi = true;
        self.durumYaz("Senkronize ediliyor…", "suruyor");
      });
    }
  };

  TA.sayfa("watchlist", izleme);
})();
