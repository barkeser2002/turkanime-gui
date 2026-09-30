/* Anime detayı: künye + "Kaynaklar ve Bölümler" (kaynak başına akordiyon).
 *
 * Eski ekran görüntüsündeki düzen: poster, başlık, SKOR/POPÜLERLİK kartları,
 * eylemler, özet, türler, stüdyo; altta eylem çubuğu (İlk Seçiliyi Oynat,
 * Seçilenleri İndir, İstediğin Anime Değil Mi?) ve kaynak akordiyonları.
 *
 * Bölüm nesneleri Python'da (gui/web/uclar_detay.py, oturum); sayfa bölümü
 * (kaynak, sıra) ile anıyor. Her anime yeni bir oturum numarası (rid); eski
 * oturumun geç yanıtları atılıyor.
 */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;
  var SAYFA = 50;

  // ── Saf yardımcılar (bölüm filtresi ve aralık seçimi) ────────────────────
  // Python'daki `episode_matches` / `aralik_coz` ile aynı kurallar.
  TA.bolumEslesir = function (bolum, igne) {
    if (!igne) return true;
    var baslik = String(bolum.baslik || "").toLocaleLowerCase("tr");
    if (baslik.indexOf(igne) >= 0) return true;
    var no = String(bolum.no || "");
    return /^\d+$/.test(igne) ? no.indexOf(igne) === 0 : no.indexOf(igne) >= 0;
  };

  TA.aralikCoz = function (metin, mevcut) {
    var numaralar = Array.from(new Set(mevcut)).sort(function (a, b) { return a - b; });
    var secilen = new Set();
    var hatali = [];
    var duz = String(metin || "").replace(/–/g, "-").replace(/(\d)\s*-\s*(\d)/g, "$1-$2")
      .replace(/(\d)\s+-(?=[,;\s]|$)/g, "$1-");
    duz.split(/[,;\s]+/).forEach(function (parca) {
      if (!parca) return;
      var m = /^(\d+)?\s*(-)?\s*(\d+)?$/.exec(parca);
      if (!m || !(m[1] || m[3])) {
        hatali.push(parca);
        return;
      }
      var alt, ust;
      if (!m[2]) {
        alt = ust = parseInt(m[1] || m[3], 10);
      } else {
        alt = m[1] ? parseInt(m[1], 10) : (numaralar.length ? numaralar[0] : 0);
        ust = m[3] ? parseInt(m[3], 10) : (numaralar.length ? numaralar[numaralar.length - 1] : alt);
      }
      if (alt > ust) {
        hatali.push(parca);
        return;
      }
      numaralar.forEach(function (n) { if (n >= alt && n <= ust) secilen.add(n); });
    });
    return { secilen: secilen, hatali: hatali };
  };

  // ── Sayfa ────────────────────────────────────────────────────────────────
  var detay = {
    rid: 0,

    kur: function (kap) {
      var self = this;
      this.geriDugme = h("button.dugme.hayalet.kucuk.geri-dugme", {
        onclick: function () { TA.ac("geri"); }
      }, TA.ikon("geri"), "Geri");
      this.ust = h("section.detay-ust");
      this.govde = h("section.detay-govde");
      this.eylemler = h("div.eylem-cubugu");
      this.kaynakDurum = h("span.durum");
      this.eslestirDugme = h("button.dugme.cerceve.kucuk", {
        onclick: function () { self.eslestir(null); },
        title: "Bütün Türkçe kaynaklarda bu animeyi ara ve bağla"
      }, TA.ikon("yenile"), "Tüm Kaynaklarda Eşleştir");
      this.akordiyonlar = h("div.akordiyonlar");
      this.not = h("p.kaynak-notu");
      TA.ekle(kap, [
        this.geriDugme,
        this.ust,
        this.govde,
        this.eylemler,
        h("section.kaynaklar", null,
          h("header.kaynaklar-baslik", null,
            h("h2", null, "Kaynaklar ve Bölümler"),
            this.kaynakDurum,
            h("span.bosluk"),
            this.eslestirDugme),
          this.akordiyonlar,
          this.not)
      ]);
      TA.dinle("gecmis_degisti", function () { self.durumlariTazele(); });
      TA.dinle("kuyruk_degisti", function () { self.durumlariTazele(); });
    },

    goster: function (p) {
      if (p.rid && p.rid !== this.rid) this.yukle(p.rid);
    },

    // ── Yükleme ─────────────────────────────────────────────────────────────
    sifirla: function (rid) {
      this.rid = rid;
      this.veri = null;
      this.kaynaklar = [];
      this.bolumler = {};
      this.durum = {};
      this.hata = {};
      this.secim = {};
      this.gosterilen = {};
      this.igne = {};
      this.sonTik = {};
      this.acik = {};
      this.devam = null;
      this.eslesmeyen = [];
      this.akEl = {};
    },

    yukle: function (rid) {
      var self = this;
      this.sifirla(rid);
      TA.bosalt(this.ust).appendChild(this.ustIskelet());
      TA.bosalt(this.govde);
      TA.bosalt(this.akordiyonlar);
      TA.bosalt(this.not);
      this.kaynakDurumYaz("");
      this.eylemleriCiz();
      TA.cagir("detay", { rid: rid }).then(function (veri) {
        if (rid !== self.rid) return;
        self.veri = veri;
        self.ustCiz(veri.kunye);
        self.govdeCiz(veri.kunye);
        self.kaynaklariKur(veri.kaynaklar);
        if (veri.arsivde_ara) {
          self.eslestir(["TürkAnime"], true);
        } else if (!veri.kaynaklar.length) {
          self.bagsizDurum();
        }
      }, function (e) {
        if (rid !== self.rid || (e.veri && e.veri.eski)) return;
        TA.bosalt(self.ust).appendChild(TA.bosDurum({ hata: true, baslik: "Detay açılamadı", metin: e.message }));
      });
    },

    ustIskelet: function () {
      return h("div.detay-ust-ic", null,
        h("div.detay-poster.iskelet"),
        h("div.detay-bilgi", null,
          h("div.iskelet.iskelet-satir", { style: { width: "55%", height: "34px" } }),
          h("div.iskelet.iskelet-satir", { style: { width: "35%", "margin-top": "14px" } }),
          h("div.iskelet.iskelet-satir", { style: { width: "70%", "margin-top": "26px", height: "64px" } })));
    },

    ustCiz: function (k) {
      var self = this;
      var arka = h("div.detay-arka");
      var gorsel = k.banner || k.kapak;
      if (gorsel) {
        var on = new Image();
        on.onload = function () {
          arka.style.backgroundImage = "url(\"" + on.src + "\")";
          arka.classList.add("yuklu");
        };
        on.src = TA.gorsel(gorsel);
      }
      var istatistik = h("div.istatistikler", null,
        k.puan != null ? this.statKart("Skor", k.puan + "%", TA.puanRengi(k.puan / 10), "yildiz") : null,
        k.populerlik ? (k.populerlik_sira
          ? this.statKart("Popülerlik", "#" + TA.sayi(k.populerlik), "var(--camgobegi)", "ates")
          : this.statKart("İzleyici", TA.sayi(k.populerlik), "var(--camgobegi)", "ates")) : null,
        k.bolum_sayisi ? this.statKart("Bölüm", String(k.bolum_sayisi), "var(--mavi-2)", "film") : null,
        k.durum ? this.statKart("Durum", k.durum, "var(--metin)", "takvim") : null);

      this.favoriDugme = h("button.dugme.marka", { onclick: function () { self.favoriDegistir(); } });
      this.favoriGoster();
      var eylem = h("div.detay-eylemler", null,
        this.favoriDugme,
        h("button.dugme.cerceve", {
          onclick: function () { TA.ac("arama", { sorgu: k.baslik }); },
          title: "Bu adla bütün kaynaklarda ara"
        }, TA.ikon("ara"), "Uygulamada Ara"),
        k.dis ? h("a.dugme.cerceve", { href: k.dis.adres, title: k.dis.adres }, TA.ikon("dis"), k.dis.ad) : null);

      TA.bosalt(this.ust).appendChild(h("div.detay-ust-ic", null,
        arka,
        TA.poster(k.kapak, k.baslik, "detay-poster"),
        h("div.detay-bilgi", null,
          h("h1", null, k.baslik),
          k.alt_baslik ? h("p.detay-alt-baslik", null, k.alt_baslik) : null,
          k.meta ? h("p.detay-meta", null, k.meta) : null,
          istatistik.childNodes.length ? istatistik : null,
          eylem)));
    },

    statKart: function (etiket, deger, renk, ikon) {
      return h("div.stat-kart", { style: { "--renk": renk } },
        h("span.stat-ikon", null, TA.ikon(ikon)),
        h("div", null, h("span.stat-etiket", null, etiket), h("b.stat-deger", null, deger)));
    },

    govdeCiz: function (k) {
      var ozet = h("p.ozet-metin", null, k.ozet || "Özet bulunamadı.");
      var devami = null;
      if ((k.ozet || "").length > 420) {
        ozet.classList.add("kisa");
        devami = h("button.baglanti", {
          onclick: function () {
            var kisa = ozet.classList.toggle("kisa");
            devami.firstChild.textContent = kisa ? "Devamını oku" : "Daha az göster";
          }
        }, "Devamını oku");
      }
      var etiketler = h("div.etiket-bloklari", null,
        k.turler.length ? h("div.etiket-blok", null, h("h4", null, "Türler"),
          h("div.hap-satiri", null, k.turler.map(function (t) { return h("span.hap", null, t); }))) : null,
        k.studyolar.length ? h("div.etiket-blok", null, h("h4", null, "Stüdyo"),
          h("div.hap-satiri", null, k.studyolar.map(function (t) { return h("span.hap", null, t); }))) : null);
      TA.bosalt(this.govde);
      TA.ekle(this.govde, [h("div.ozet", null, h("h3", null, "Özet"), ozet, devami), etiketler]);
    },

    // ── Kitaplık ────────────────────────────────────────────────────────────
    favoriKaynak: function () {
      return this.kaynaklar.length ? this.kaynaklar[0] : null;
    },

    favoriGoster: function () {
      if (!this.favoriDugme) return;
      var k = this.favoriKaynak();
      var var_ = !!(k && k.favori);
      TA.bosalt(this.favoriDugme);
      TA.ekle(this.favoriDugme, [TA.ikon("kalp"), var_ ? "Kitaplıkta" : "Kitaplığa Ekle"]);
      this.favoriDugme.classList.toggle("secili", var_);
      this.favoriDugme.disabled = !k;
      this.favoriDugme.title = k ? (var_ ? "Kitaplıktan çıkar" : "Kitaplığım'a ekle")
        : "Kitaplığa eklemek için anime bir kaynağa bağlanmalı";
    },

    favoriDegistir: function () {
      var self = this;
      var k = this.favoriKaynak();
      if (!k) return;
      var yeni = !k.favori;
      TA.cagir("favori", { rid: this.rid, kaynak: k.ad, deger: yeni }).then(function () {
        k.favori = yeni;
        self.favoriGoster();
        TA.bildir(yeni ? "Kitaplığa eklendi." : "Kitaplıktan çıkarıldı.", "tamam");
      }, function (e) { TA.bildir(e.message, "hata"); });
    },

    // ── Kaynaklar ───────────────────────────────────────────────────────────
    kaynaklariKur: function (liste) {
      var self = this;
      liste.forEach(function (k) {
        var mevcut = self.kaynaklar.filter(function (x) { return x.ad === k.ad; })[0];
        if (mevcut) Object.assign(mevcut, k);
        else self.kaynaklar.push(k);
      });
      // Sıra Python'dan (ilk kaynak başta); yeni gelenler sona.
      var sira = liste.map(function (k) { return k.ad; });
      this.kaynaklar.sort(function (a, b) {
        var i = sira.indexOf(a.ad), j = sira.indexOf(b.ad);
        return (i < 0 ? 999 : i) - (j < 0 ? 999 : j);
      });
      this.favoriGoster();
      this.kaynaklar.forEach(function (k, i) {
        if (!self.akEl[k.ad]) {
          self.acik[k.ad] = i === 0;
          self.akordiyonKur(k);
        } else {
          self.akordiyonBaslik(k);
        }
        if (!self.durum[k.ad]) self.bolumYukle(k.ad);
      });
      this.akordiyonSirala();
    },

    akordiyonSirala: function () {
      this.kaynaklar.forEach(function (k) {
        if (this.akEl[k.ad]) this.akordiyonlar.appendChild(this.akEl[k.ad].kok);
      }, this);
    },

    bagsizDurum: function () {
      var self = this;
      TA.bosalt(this.akordiyonlar).appendChild(TA.bosDurum({
        ikon: "kutuphane",
        baslik: "Bu anime henüz bir kaynağa bağlı değil",
        metin: "Bölümler için Türkçe kaynaklarda eşleşme aranmalı. Yanlış eşleşirse “İstediğin Anime Değil Mi?” ile düzeltebilirsin.",
        eylem: { etiket: "Kaynaklarda Eşleştir", ikon: "ara", fn: function () { self.eslestir(null); } }
      }));
    },

    kaynakDurumYaz: function (metin, tur) {
      this.kaynakDurum.className = "durum" + (tur ? " " + tur : "");
      TA.bosalt(this.kaynakDurum);
      if (tur === "suruyor") this.kaynakDurum.appendChild(h("span.donen"));
      if (metin) this.kaynakDurum.appendChild(document.createTextNode(metin));
    },

    eslestir: function (kaynaklar, sessiz) {
      var self = this;
      var rid = this.rid;
      this.eslestirDugme.disabled = true;
      this.kaynakDurumYaz(kaynaklar ? "Arşivde eşleşme aranıyor…" : "Kaynaklarda eşleşme aranıyor…", "suruyor");
      if (!this.kaynaklar.length && !sessiz) TA.bosalt(this.akordiyonlar);
      TA.cagir("eslestir", { rid: rid, kaynaklar: kaynaklar }).then(function (s) {
        if (rid !== self.rid) return;
        self.eslestirDugme.disabled = false;
        self.eslesmeyen = kaynaklar ? self.eslesmeyen : s.eslesmeyen;
        if (s.yeni.length) {
          if (!self.kaynaklar.length) TA.bosalt(self.akordiyonlar);
          self.kaynaklariKur(s.kaynaklar);
          self.kaynakDurumYaz(s.yeni.length + " kaynak bağlandı", "tamam");
        } else if (!self.kaynaklar.length) {
          self.kaynakDurumYaz(kaynaklar ? "" : "Eşleşme bulunamadı", kaynaklar ? "" : "hata");
          if (kaynaklar && sessiz) self.bagsizDurum();
          else self.eslesmeYok();
        } else {
          self.kaynakDurumYaz("Yeni eşleşme yok");
        }
        self.notYaz();
      }, function (e) {
        if (rid !== self.rid || (e.veri && e.veri.eski)) return;
        self.eslestirDugme.disabled = false;
        self.kaynakDurumYaz(e.message, "hata");
        if (!self.kaynaklar.length) self.bagsizDurum();
      });
    },

    eslesmeYok: function () {
      var self = this;
      TA.bosalt(this.akordiyonlar).appendChild(TA.bosDurum({
        ikon: "uyari",
        baslik: "Otomatik eşleşme bulunamadı",
        metin: "Kaynaklarda bu adla birebir eşleşen kayıt çıkmadı. Doğru kaydı kendin seç.",
        eylem: { etiket: "Doğru Kaydı Seç", ikon: "ara", fn: function () { self.eslesmePenceresi(); } }
      }));
    },

    notYaz: function () {
      TA.bosalt(this.not);
      if (this.eslesmeyen.length) {
        TA.ekle(this.not, [TA.ikon("bilgi"), "Eşleşme bulunamayan: ",
          this.eslesmeyen.map(function (ad) { return TA.kaynakEtiketi(ad); }).join(", ")]);
      }
    },

    // ── Akordiyon ───────────────────────────────────────────────────────────
    akordiyonKur: function (k) {
      var self = this;
      var ad = k.ad;
      var el = {};
      el.chevron = TA.ikon("ileri", "ak-ok");
      el.baslik = h("div.ak-baslik-metin");
      el.sayi = h("span.ak-sayi");
      el.durum = h("span.ak-durum");
      el.baslikSatiri = h("header.ak-baslik", {
        tabindex: 0,
        onclick: function (e) {
          if (e.target.closest(".ak-degistir")) return;
          self.acKapa(ad);
        },
        onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); self.acKapa(ad); } }
      }, el.chevron, el.baslik, el.sayi, el.durum,
        h("button.baglanti.ak-degistir", {
          onclick: function () { self.eslesmePenceresi(ad); },
          title: "Bu kaynakta başka bir kayıt seç"
        }, "Değiştir"));

      el.igne = h("input.girdi.kucuk-girdi", {
        type: "search", placeholder: "Bölüm ara…", "aria-label": "Bölüm ara",
        oninput: function () { self.igne[ad] = el.igne.value.trim().toLocaleLowerCase("tr"); self.listeCiz(ad, true); }
      });
      el.aralik = h("input.girdi.kucuk-girdi.aralik-girdi", {
        type: "text", placeholder: "Aralık: 1-12, 15", "aria-label": "Aralıkla seç",
        onkeydown: function (e) { if (e.key === "Enter") { e.preventDefault(); self.aralikSec(ad); } }
      });
      el.secimSayi = h("span.secim-sayi");
      el.arac = h("div.ak-arac", null,
        h("div.arac-grup", null, TA.ikon("ara", "arac-ikon"), el.igne),
        h("div.arac-grup", null, el.aralik,
          h("button.dugme.hayalet.kucuk", { onclick: function () { self.aralikSec(ad); } }, "Seç")),
        h("span.bosluk"),
        h("button.dugme.hayalet.kucuk", { onclick: function () { self.secHepsi(ad, "izlenmemis"); } }, "İzlenmemişler"),
        h("button.dugme.hayalet.kucuk", { onclick: function () { self.secHepsi(ad, "indirilmemis"); } }, "İndirilmemişler"),
        h("button.dugme.hayalet.kucuk", { onclick: function () { self.secHepsi(ad, "hepsi"); } }, "Tümü"),
        h("button.dugme.hayalet.kucuk", { onclick: function () { self.secHepsi(ad, "hic"); } }, "Temizle"),
        el.secimSayi);
      el.liste = h("ul.bolum-listesi");
      el.daha = h("button.dugme.cerceve.daha-fazla", { onclick: function () { self.dahaFazla(ad); } });
      el.icerik = h("div.ak-govde", null, el.arac, el.liste, el.daha);
      el.kok = h("section.akordiyon", { dataset: { kaynak: ad }, style: { "--renk": k.renk || "var(--soluk)" } },
        el.baslikSatiri, el.icerik);
      this.akEl[ad] = el;
      this.akordiyonBaslik(k);
      this.acKapaUygula(ad);
      this.akordiyonlar.appendChild(el.kok);
    },

    akordiyonBaslik: function (k) {
      var el = this.akEl[k.ad];
      if (!el) return;
      TA.bosalt(el.baslik);
      TA.ekle(el.baslik, [
        h("span.kaynak-hap.buyuk", null, k.etiket),
        k.eslesme ? h("span.ak-eslesme", { title: "Otomatik eşleşme — yanlışsa “Değiştir”" }, "↳ " + k.eslesme) : null,
        k.elle ? h("span.hap", null, TA.ikon("tamam"), "elle seçildi") : null
      ]);
    },

    acKapa: function (ad) {
      this.acik[ad] = !this.acik[ad];
      this.acKapaUygula(ad);
    },

    acKapaUygula: function (ad) {
      var el = this.akEl[ad];
      el.kok.classList.toggle("acik", !!this.acik[ad]);
      el.icerik.hidden = !this.acik[ad];
      el.baslikSatiri.setAttribute("aria-expanded", this.acik[ad] ? "true" : "false");
    },

    bolumYukle: function (ad) {
      var self = this;
      var rid = this.rid;
      var el = this.akEl[ad];
      this.durum[ad] = "yukleniyor";
      TA.bosalt(el.durum).appendChild(h("span.donen"));
      el.sayi.textContent = "";
      TA.bosalt(el.liste);
      for (var i = 0; i < 6; i++) el.liste.appendChild(h("li.bolum-satiri.iskelet-satir-kap", null, h("div.iskelet.iskelet-satir", { style: { width: (40 + (i * 7) % 40) + "%" } })));
      el.daha.hidden = true;
      TA.cagir("bolumler", { rid: rid, kaynak: ad }).then(function (s) {
        if (rid !== self.rid) return;
        self.durum[ad] = "tamam";
        self.bolumler[ad] = s.bolumler;
        self.secim[ad] = self.secim[ad] || new Set();
        self.gosterilen[ad] = 0;
        TA.bosalt(el.durum);
        el.sayi.textContent = s.bolumler.length + " bölüm";
        self.devam = s.devam || self.devam;
        self.listeCiz(ad, true);
        self.eylemleriCiz();
        self.durumlariTazele();          // kuyruk durumu GUI thread'inden
      }, function (e) {
        if (rid !== self.rid || (e.veri && e.veri.eski)) return;
        self.durum[ad] = "hata";
        self.hata[ad] = e.message;
        TA.bosalt(el.durum).appendChild(h("span.ak-hata", { title: e.message }, TA.ikon("uyari"), "hata"));
        TA.bosalt(el.liste).appendChild(h("li.bolum-hata", null,
          h("span", null, e.message),
          h("button.dugme.cerceve.kucuk", { onclick: function () { self.bolumYukle(ad); } }, TA.ikon("yenile"), "Tekrar dene")));
        self.acik[ad] = true;
        self.acKapaUygula(ad);
      });
    },

    filtreli: function (ad) {
      var igne = this.igne[ad] || "";
      return (this.bolumler[ad] || []).filter(function (b) { return TA.bolumEslesir(b, igne); });
    },

    listeCiz: function (ad, bastan) {
      var el = this.akEl[ad];
      if (!el || !this.bolumler[ad]) return;
      var liste = this.filtreli(ad);
      if (bastan) {
        TA.bosalt(el.liste);
        this.gosterilen[ad] = 0;
      }
      if (!liste.length) {
        el.liste.appendChild(h("li.bolum-bos", null, this.bolumler[ad].length ? "Aramaya uyan bölüm yok." : "Bu kaynakta bölüm bulunamadı."));
      }
      var bas = this.gosterilen[ad];
      var parca = liste.slice(bas, bas + SAYFA);
      parca.forEach(function (b) { el.liste.appendChild(this.satir(ad, b)); }, this);
      this.gosterilen[ad] = bas + parca.length;
      var kalan = liste.length - this.gosterilen[ad];
      el.daha.hidden = kalan <= 0;
      el.daha.textContent = "Daha Fazla Yükle (" + kalan + " kaldı)";
      this.secimGoster(ad);
    },

    dahaFazla: function (ad) {
      this.listeCiz(ad, false);
    },

    satir: function (ad, b) {
      var self = this;
      var kutu = h("input", { type: "checkbox", "aria-label": b.baslik + " seç" });
      kutu.checked = this.secim[ad].has(b.sira);
      kutu.addEventListener("click", function (e) { self.kutuTik(ad, b.sira, kutu.checked, e.shiftKey); });
      var rozetler = h("span.bolum-rozetler");
      var li = h("li.bolum-satiri", { dataset: { sira: b.sira } },
        h("label.secim", null, kutu),
        h("span.bolum-no", null, b.no ? String(b.no) : "—"),
        h("span.bolum-ad", { title: b.baslik }, b.baslik),
        rozetler,
        h("div.bolum-eylem", null,
          h("button.ikon-dugme.indir-dugme", {
            title: "İndir", "aria-label": b.baslik + " indir",
            onclick: function () { self.indir([[ad, b.sira]]); }
          }, TA.ikon("indir")),
          h("button.dugme.birincil.kucuk.oynat-dugme", {
            onclick: function () { self.oynat(ad, b.sira); }
          }, TA.ikon("oynat"), "Oynat")));
      this.rozetleriYaz(li, b);
      return li;
    },

    rozetleriYaz: function (li, b) {
      var r = li.querySelector(".bolum-rozetler");
      TA.bosalt(r);
      li.classList.toggle("izlendi", !!(b.rozet && b.izlendi));
      if (b.rozet && b.izlendi) r.appendChild(h("span.rozet.yesil", { title: "İzlendi" }, TA.ikon("tamam"), "İzlendi"));
      if (b.rozet && b.indirildi) r.appendChild(h("span.rozet.mavi", { title: "İndirildi" }, TA.ikon("indir"), "İndirildi"));
      if (b.kuyrukta) r.appendChild(h("span.rozet", { title: "İndirme kuyruğunda" }, h("span.donen"), "Kuyrukta"));
      if (b.konum) r.appendChild(h("span.rozet.turuncu", { title: "Kaldığın yer" }, TA.ikon("saat"), b.konum));
    },

    // ── Seçim ───────────────────────────────────────────────────────────────
    kutuTik: function (ad, sira, isaretli, shift) {
      var secim = this.secim[ad];
      var liste = this.filtreli(ad).map(function (b) { return b.sira; });
      var son = this.sonTik[ad];
      var aralik = [sira];
      if (shift && son != null && liste.indexOf(son) >= 0 && liste.indexOf(sira) >= 0) {
        var i = liste.indexOf(son), j = liste.indexOf(sira);
        aralik = liste.slice(Math.min(i, j), Math.max(i, j) + 1);
      }
      aralik.forEach(function (s) { if (isaretli) secim.add(s); else secim.delete(s); });
      this.sonTik[ad] = sira;
      this.secimGoster(ad);
    },

    secHepsi: function (ad, tur) {
      if (!this.bolumler[ad]) return;
      var liste = this.filtreli(ad);
      var secilecek = liste.filter(function (b) {
        if (tur === "izlenmemis") return !b.izlendi;
        if (tur === "indirilmemis") return !b.indirildi && !b.kuyrukta;
        return tur === "hepsi";
      });
      this.secim[ad] = new Set(secilecek.map(function (b) { return b.sira; }));
      this.sonTik[ad] = null;
      this.secimGoster(ad);
    },

    aralikSec: function (ad) {
      var el = this.akEl[ad];
      var metin = el.aralik.value;
      var liste = this.filtreli(ad);
      var s = TA.aralikCoz(metin, liste.map(function (b) { return b.no || 0; }));
      if (!metin.trim() || (!s.secilen.size && s.hatali.length)) {
        TA.bildir("Aralık anlaşılamadı; örnek: 1-12, 15, 20-", "hata");
        return;
      }
      this.secim[ad] = new Set(liste.filter(function (b) { return s.secilen.has(b.no || 0); })
        .map(function (b) { return b.sira; }));
      this.secimGoster(ad);
      if (s.hatali.length) TA.bildir(this.secim[ad].size + " bölüm seçildi; anlaşılamayan: " + s.hatali.join(", "), "bilgi");
    },

    secimGoster: function (ad) {
      var el = this.akEl[ad];
      var secim = this.secim[ad] || new Set();
      el.liste.querySelectorAll(".bolum-satiri[data-sira]").forEach(function (li) {
        var s = Number(li.dataset.sira);
        var kutu = li.querySelector("input[type=checkbox]");
        kutu.checked = secim.has(s);
        li.classList.toggle("secili", secim.has(s));
      });
      el.secimSayi.textContent = secim.size ? secim.size + " seçili" : "";
      this.eylemleriCiz();
    },

    secililer: function () {
      var out = [];
      this.kaynaklar.forEach(function (k) {
        var secim = this.secim[k.ad];
        if (!secim || !this.bolumler[k.ad]) return;
        this.bolumler[k.ad].forEach(function (b) { if (secim.has(b.sira)) out.push([k.ad, b.sira]); });
      }, this);
      return out;
    },

    // ── Eylem çubuğu ────────────────────────────────────────────────────────
    eylemleriCiz: function () {
      var self = this;
      var secili = this.secililer();
      TA.bosalt(this.eylemler);
      if (this.devam) {
        this.eylemler.appendChild(h("button.dugme.marka", {
          onclick: function () { self.oynat(self.devam.kaynak, self.devam.sira); }
        }, TA.ikon("oynat"), this.devam.metin));
      }
      TA.ekle(this.eylemler, [
        h("button.dugme.birincil", {
          disabled: !secili.length,
          onclick: function () { var s = self.secililer()[0]; if (s) self.oynat(s[0], s[1]); }
        }, TA.ikon("oynat"), "İlk Seçiliyi Oynat"),
        h("button.dugme.camgobegi", {
          disabled: !secili.length,
          onclick: function () { self.indir(self.secililer()); }
        }, TA.ikon("indir"), secili.length ? "Seçilenleri İndir (" + secili.length + ")" : "Seçilenleri İndir"),
        h("span.bosluk"),
        h("button.dugme.cerceve", {
          onclick: function () { self.eslesmePenceresi(); },
          title: "Yanlış anime ya da sezon mu eşleşti? Doğru kaydı seç."
        }, TA.ikon("ara"), "İstediğin Anime Değil Mi?")
      ]);
    },

    oynat: function (ad, sira) {
      TA.cagir("oynat", { rid: this.rid, kaynak: ad, sira: sira }).catch(function (e) {
        TA.bildir(e.message, "hata");
      });
    },

    indir: function (secim) {
      var self = this;
      if (!secim.length) return;
      TA.cagir("indir", { rid: this.rid, secim: secim }).then(function (s) {
        var metin = s.yeni ? s.yeni + " bölüm indirme sırasına alındı." : "";
        if (s.zaten) metin += (metin ? " " : "") + s.zaten + " bölüm zaten kuyrukta.";
        TA.bildir(metin || "İndirilecek bölüm yok.", s.yeni ? "tamam" : "bilgi");
        self.durumlariTazele();
      }, function (e) { TA.bildir(e.message, "hata"); });
    },

    // Olaylar art arda gelebiliyor (30 bölüm kuyruğa girer): birleştir.
    durumlariTazele: function () {
      var self = this;
      clearTimeout(this._tazeleZamanlayici);
      this._tazeleZamanlayici = setTimeout(function () { self._durumlariTazele(); }, 120);
    },

    _durumlariTazele: function () {
      var self = this;
      var rid = this.rid;
      if (!rid || !Object.keys(this.bolumler).length) return;
      TA.cagir("bolum_durumlari", { rid: rid }).then(function (s) {
        if (rid !== self.rid) return;
        Object.keys(s.durumlar).forEach(function (ad) {
          var liste = self.bolumler[ad];
          if (!liste) return;
          s.durumlar[ad].forEach(function (d, i) { if (liste[i]) Object.assign(liste[i], d); });
          var el = self.akEl[ad];
          if (!el) return;
          el.liste.querySelectorAll(".bolum-satiri[data-sira]").forEach(function (li) {
            var b = liste[Number(li.dataset.sira)];
            if (b) self.rozetleriYaz(li, b);
          });
        });
        self.devam = s.devam;
        self.eylemleriCiz();
      }, function () {});
    },

    // ── "İstediğin anime değil mi?" ─────────────────────────────────────────
    eslesmePenceresi: function (kaynak) {
      var self = this;
      if (!this.veri) return;
      TA.eslesmePenceresi({
        sorgu: this.veri.kunye.baslik,
        kaynak: kaynak || "",
        sec: function (s) {
          var rid = self.rid;
          return TA.cagir("elle_eslestir", { rid: rid, kaynak: s.kaynak, slug: s.slug, baslik: s.baslik }).then(function (r) {
            if (rid !== self.rid) return;
            if (!self.kaynaklar.length) TA.bosalt(self.akordiyonlar);
            // Aynı arşivin eski adla bağı düştüyse akordiyonu da kaldır.
            Object.keys(self.akEl).forEach(function (ad) {
              if (!r.kaynaklar.some(function (k) { return k.ad === ad; })) {
                self.akEl[ad].kok.remove();
                delete self.akEl[ad];
                self.kaynaklar = self.kaynaklar.filter(function (k) { return k.ad !== ad; });
              }
            });
            self.durum[s.kaynak] = null;
            self.secim[s.kaynak] = new Set();
            self.kaynaklariKur(r.kaynaklar);
            self.acik[s.kaynak] = true;
            if (self.akEl[s.kaynak]) self.acKapaUygula(s.kaynak);
            self.eslesmeyen = self.eslesmeyen.filter(function (ad) { return ad !== s.kaynak; });
            self.notYaz();
            self.kaynakDurumYaz(TA.kaynakEtiketi(s.kaynak) + " → " + s.baslik + " eşleştirildi", "tamam");
          });
        }
      });
    }
  };

  TA.sayfa("detail", detay);
})();
