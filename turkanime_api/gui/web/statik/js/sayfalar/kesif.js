/* Keşif sayfaları: Ana Sayfa (hero + şeritler), Trend ve Bu Sezon (ızgara). */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  function animeAc(veri) {
    TA.ac("anime", { kayit: veri.kayit });
  }

  function kartlar(liste, secenek) {
    return (liste || []).map(function (veri, i) {
      return TA.kart(veri, { tikla: animeAc, sira: secenek && secenek.sirali ? i + 1 : 0 });
    });
  }

  // ── Ana Sayfa ────────────────────────────────────────────────────────────
  var ana = {
    istatistikEl: null,
    trend: null,
    sezon: null,
    devam: null,
    heroBulanik: null,
    heroSahne: null,
    yuklendi: false,

    kur: function (kap) {
      var girdi = h("input", {
        type: "search",
        placeholder: "Anime adı yazın… (ör. Frieren, One Piece)",
        "aria-label": "Anime ara",
        autocomplete: "off"
      });
      var form = h("form.hero-ara", {
        onsubmit: function (e) {
          e.preventDefault();
          var sorgu = girdi.value.trim();
          if (sorgu) TA.ac("arama", { sorgu: sorgu });
        }
      }, TA.ikon("ara"), girdi, h("button.dugme.birincil", { type: "submit" }, "Ara"));

      this.istatistikEl = h("div.hero-sayilar");
      this.heroBulanik = h("div.hero-bulanik");
      this.heroSahne = h("div.hero-sahne", null,
        h("img.hero-maskot", { src: "img/maskot.png", alt: "", draggable: false }));

      var hero = h("section.hero", null,
        this.heroBulanik,
        h("div.hero-ic", null,
          h("div.hero-metin", null,
            h("span.hap.vurgu", null, TA.ikon("kivilcim"), "Türkçe altyazılı anime platformu"),
            h("h1", null, "Anime Dünyasını ", h("em", null, "Keşfet")),
            h("p.hero-alt", null,
              "Onlarca Türkçe kaynağı tek aramada topla, bölümleri izle ya da indir. " +
              "turkanime.tv arşivi çevrimdışı olarak her zaman elinin altında."),
            form,
            this.istatistikEl),
          this.heroSahne));

      this.devam = TA.serit({ baslik: "İzlemeye Devam Et", sinif: "devam-kaydirma", id: "devam" });
      this.devam.el.hidden = true;
      this.trend = TA.serit({
        baslik: "Bu Hafta Trend",
        alt: "En çok izlenenler",
        tumu: function () { TA.ac("sayfa", { ad: "trending" }); }
      });
      this.sezon = TA.serit({
        baslik: "Bu Sezon",
        tumu: function () { TA.ac("sayfa", { ad: "season" }); }
      });
      TA.ekle(kap, [hero, this.devam.el, this.trend.el, this.sezon.el]);
      this.istatistik({});
    },

    goster: function () {
      // Şerit ve sayılar her ziyarette (yerel, ucuz): az önce izlenen bölüm
      // ana sayfaya dönüldüğünde orada olmalı. Ağdan gelen şeritler bir kez.
      this.devamYukle();
      TA.cagir("istatistik").then(this.istatistik.bind(this), function () {});
      if (!this.yuklendi) this.yenile();
    },

    yenile: function () {
      this.yuklendi = true;
      this.seritYukle(this.trend, "trending", true);
      this.seritYukle(this.sezon, "season", false);
    },

    istatistik: function (s) {
      TA.bosalt(this.istatistikEl);
      var ogeler = [
        [s.arsiv, "arşivdeki anime"],
        [s.kaynak, "kaynak"],
        [s.kitaplik, "kitaplığında"]
      ];
      ogeler.forEach(function (o) {
        this.istatistikEl.appendChild(h("div", null, h("b", null, o[0] == null ? "—" : TA.sayi(o[0])), h("span", null, o[1])));
      }, this);
    },

    seritYukle: function (serit, mod, trend) {
      var self = this;
      serit.doldur(TA.iskeletKartlar(8));
      TA.cagir("kesif", { mod: mod, limit: 18 }).then(function (sonuc) {
        if (sonuc.altbaslik && mod === "season") serit.altBaslik(sonuc.altbaslik);
        if (!sonuc.kartlar.length) {
          serit.doldur(TA.bosDurum({ metin: sonuc.bos_mesaj, eylem: { etiket: "Yenile", fn: function () { self.seritYukle(serit, mod, trend); } } }));
          return;
        }
        serit.doldur(kartlar(sonuc.kartlar, { sirali: trend }));
        if (trend) self.heroDoldur(sonuc.kartlar);
      }, function (hata) {
        serit.doldur(TA.bosDurum({ hata: true, metin: hata.message, eylem: { etiket: "Tekrar dene", fn: function () { self.seritYukle(serit, mod, trend); } } }));
      });
    },

    // Hero sahnesi: haftanın ilk üç animesi maskotun arkasında yelpaze.
    heroDoldur: function (liste) {
      var sahne = this.heroSahne;
      sahne.querySelectorAll(".hero-poster").forEach(function (el) { el.remove(); });
      var ilk = liste.filter(function (v) { return v.kapak; }).slice(0, 3);
      ilk.forEach(function (veri) {
        var img = h("img", { alt: "", draggable: false });
        var kutu = h("div.hero-poster", { title: veri.baslik, onclick: function () { animeAc(veri); } }, img);
        img.addEventListener("load", function () { kutu.classList.add("yuklu"); });
        img.src = TA.gorsel(veri.kapak);
        sahne.insertBefore(kutu, sahne.firstChild);
      });
      if (ilk[0]) {
        var bulanik = this.heroBulanik;
        var on = new Image();
        on.onload = function () {
          bulanik.style.backgroundImage = "url(\"" + on.src + "\")";
          bulanik.classList.add("yuklu");
        };
        on.src = TA.gorsel(ilk[0].kapak);
      }
    },

    devamYukle: function () {
      var serit = this.devam;
      TA.cagir("devam_listesi").then(function (liste) {
        serit.el.hidden = !liste.length;
        serit.doldur(liste.map(devamKarti));
      }, function () {
        serit.el.hidden = true;
      });
    }
  };

  function devamKarti(kayit) {
    var kapak = h("div.devam-kapak", null,
      kayit.kapak ? h("img", { src: TA.gorsel(kayit.kapak), alt: "", loading: "lazy", onerror: function () { this.remove(); } }) : null,
      h("span.oynat-mini", null, TA.ikon("oynat")));
    var ilerleme = kayit.ilerleme ? h("div.ilerleme", null, h("i", { style: { width: Math.round(kayit.ilerleme * 100) + "%" } })) : null;
    return h("article.devam-kart", {
      tabindex: 0,
      title: kayit.baslik + " — " + kayit.kaynak_adi,
      onclick: function () { TA.ac("kitaplik", { kayit: kayit.kayit }); },
      onkeydown: function (e) { if (e.key === "Enter") TA.ac("kitaplik", { kayit: kayit.kayit }); }
    },
      kapak,
      h("div.devam-bilgi", null,
        h("b", null, kayit.baslik),
        h("span.bolum", null, kayit.bolum_metni || "—"),
        h("span.kaynak-hap", { style: { "--renk": kayit.kaynak_renk || "var(--soluk)" } }, kayit.kaynak_adi)),
      ilerleme);
  }

  TA.sayfa("home", ana);

  // ── Trend / Bu Sezon (ızgara) ────────────────────────────────────────────
  function izgaraSayfasi(mod, baslik, altbaslik) {
    return {
      yuklendi: false,
      kur: function (kap) {
        this.baslikEl = h("h1", null, baslik);
        this.altEl = h("p", null, altbaslik);
        this.durum = h("span.durum");
        this.yenileDugme = h("button.dugme.cerceve", { onclick: this.yenile.bind(this) }, TA.ikon("yenile"), "Yenile");
        this.izgara = h("div.izgara");
        this.bos = h("div.kesif-bos");
        TA.ekle(kap, [
          h("header.sayfa-baslik", null,
            h("div", null, this.baslikEl, this.altEl),
            h("div.sayfa-eylem", null, this.durum, this.yenileDugme)),
          this.bos,
          this.izgara
        ]);
      },
      goster: function () {
        if (!this.yuklendi) this.yenile();
      },
      yenile: function () {
        var self = this;
        this.yuklendi = true;
        this.yenileDugme.disabled = true;
        TA.bosalt(this.bos);
        TA.bosalt(this.izgara);
        TA.ekle(this.izgara, TA.iskeletKartlar(18));
        this.durum.className = "durum";
        TA.bosalt(this.durum).appendChild(h("span.donen"));
        this.durum.appendChild(document.createTextNode("Yükleniyor…"));
        TA.cagir("kesif", { mod: mod, limit: 30 }).then(function (sonuc) {
          self.yenileDugme.disabled = false;
          TA.bosalt(self.izgara);
          if (sonuc.altbaslik) self.altEl.textContent = sonuc.altbaslik;
          if (!sonuc.kartlar.length) {
            TA.bosalt(self.durum);
            self.bos.appendChild(TA.bosDurum({ baslik: "İçerik yok", metin: sonuc.bos_mesaj, eylem: { etiket: "Yenile", fn: self.yenile.bind(self) } }));
            return;
          }
          TA.ekle(self.izgara, kartlar(sonuc.kartlar, { sirali: mod === "trending" }));
          self.durum.className = "durum tamam";
          self.durum.textContent = sonuc.kartlar.length + " anime";
        }, function (hata) {
          self.yenileDugme.disabled = false;
          TA.bosalt(self.izgara);
          TA.bosalt(self.durum);
          self.bos.appendChild(TA.bosDurum({ hata: true, baslik: "Yüklenemedi", metin: hata.message, eylem: { etiket: "Tekrar dene", fn: self.yenile.bind(self) } }));
        });
      }
    };
  }

  TA.sayfa("trending", izgaraSayfasi("trending", "Trend", "Şu anda yayında ve en çok izlenenler"));
  TA.sayfa("season", izgaraSayfasi("season", "Bu Sezon", "MyAnimeList sezon listesi"));
})();
