/* Ayarlar: oynatma/indirme, bölüm listesi, çevrimdışı arşiv, kaynak
 * oturumları, kimlik bağışı, bağlantı, AniList, Discord ve bakım.
 *
 * Form alanları "Kaydet" ile yazılıyor (değişiklik olunca alttaki çubuk
 * beliriyor). Discord anahtarı ve arşiv/çerez/bağış eylemleri anlık.
 */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  var BOLUMLER = [
    ["oynatma", "Oynatma ve İndirme", "indir"],
    ["liste", "Bölüm Listesi", "film"],
    ["arsiv", "Çevrimdışı Arşiv", "kutuphane"],
    ["oturum", "Kaynak Oturumları", "tamam"],
    ["bagis", "Kimlik Bağışı", "kalp"],
    ["baglanti", "Bağlantı", "dis"],
    ["anilist", "AniList", "yildiz"],
    ["bakim", "Discord ve Bakım", "ayar"]
  ];

  var ayarlar = {
    kur: function (kap) {
      var self = this;
      this.alanlar = {};         // alan → input
      this.durumEl = h("span.durum");
      this.menu = h("nav.ayar-menu");
      this.govde = h("div.ayar-govde");
      this.kaydetCubugu = h("div.kaydet-cubugu", { hidden: true },
        h("span", null, TA.ikon("bilgi"), "Kaydedilmemiş değişiklikler var"),
        h("span.bosluk"),
        h("button.dugme.hayalet", { onclick: function () { self.yukle(); } }, "Vazgeç"),
        h("button.dugme.birincil", { onclick: function () { self.kaydet(); } }, TA.ikon("tamam"), "Kaydet"));
      TA.ekle(kap, [
        h("header.sayfa-baslik", null,
          h("div", null, h("h1", null, "Ayarlar"),
            h("p", null, "Tercihler CLI ile ortak ayarlar.json'da saklanır.")),
          h("div.sayfa-eylem", null, this.durumEl)),
        h("div.ayar-duzen", null, this.menu, this.govde),
        this.kaydetCubugu
      ]);
      BOLUMLER.forEach(function (b) {
        self.menu.appendChild(h("button.ayar-menu-ogesi", {
          dataset: { bolum: b[0] },
          onclick: function () {
            var hedef = self.govde.querySelector("[data-bolum='" + b[0] + "']");
            if (hedef) hedef.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        }, TA.ikon(b[2]), b[1]));
      });

      TA.dinle("ayar_durum", function (v) { self.durumYaz(v.mesaj, v.tur); });
      TA.dinle("ayar_cerez", function (v) { self.cerezGoster(v); });
      TA.dinle("ayar_bagis", function (v) { self.bagisGoster(v); });
      TA.dinle("ayar_anilist", function (v) { self.anilistGoster(v); });
      TA.dinle("arsiv_ilerleme", function (v) { self.arsivIlerleme(v); });
      TA.dinle("arsiv_sonuc", function (v) {
        self.arsivMesgul(false);
        self.arsivSonucYaz(v.mesaj, v.tur);
        self.arsivTazele();
      });
    },

    goster: function () {
      this.yukle();
      this.arsivTazele();       // konum arada değişmiş olabilir; yerelde ucuz
    },

    durumYaz: function (mesaj, tur) {
      this.durumEl.className = "durum" + (tur && tur !== "bilgi" ? " " + tur : "");
      this.durumEl.textContent = mesaj || "";
      if (mesaj && TA.aktif !== "settings") TA.bildir(mesaj, tur);
    },

    // ── Yükleme ve form ─────────────────────────────────────────────────────
    yukle: function () {
      var self = this;
      TA.cagir("ayarlar").then(function (v) {
        self.veri = v;
        self.ciz(v);
        self.kirli(false);
      }, function (e) {
        self.durumYaz("Ayarlar okunamadı: " + e.message, "hata");
      });
    },

    kirli: function (deger) {
      this.degisti = deger;
      this.kaydetCubugu.hidden = !deger;
    },

    girdi: function (alan, secenek) {
      var self = this;
      secenek = secenek || {};
      var el = h("input.girdi", {
        type: secenek.tip || "text",
        placeholder: secenek.yer || "",
        autocomplete: "off",
        spellcheck: false,
        oninput: function () { self.kirli(true); }
      });
      if (secenek.tip === "number") {
        el.min = secenek.min;
        el.max = secenek.max;
      }
      this.alanlar[alan] = el;
      if (secenek.tip === "password") {
        var goz = h("button.ikon-dugme.goz-dugme", {
          type: "button", title: "Göster/gizle",
          onclick: function () { el.type = el.type === "password" ? "text" : "password"; }
        }, TA.ikon("goz"));
        return h("div.girdi-kap", null, el, goz);
      }
      return el;
    },

    anahtar: function (alan, baslik, aciklama, aninda) {
      var self = this;
      var kutu = h("input", { type: "checkbox", role: "switch" });
      kutu.addEventListener("change", function () {
        if (aninda) aninda(kutu.checked);
        else self.kirli(true);
      });
      if (alan) this.alanlar[alan] = kutu;
      return h("label.anahtar-satiri", null,
        h("span.anahtar-metin", null, h("b", null, baslik), aciklama ? h("span", null, aciklama) : null),
        h("span.anahtar", null, kutu, h("i")));
    },

    satir: function (etiket, icerik, ipucu) {
      return h("div.ayar-satiri", null,
        h("label.ayar-etiket", null, etiket),
        h("div.ayar-deger", null, icerik, ipucu ? h("p.ipucu", null, ipucu) : null));
    },

    kart: function (anahtar, baslik, aciklama, cocuklar) {
      var b = BOLUMLER.filter(function (x) { return x[0] === anahtar; })[0];
      return h("section.ayar-karti", { dataset: { bolum: anahtar } },
        h("header", null, h("span.ayar-ikon", null, TA.ikon(b ? b[2] : "ayar")),
          h("div", null, h("h2", null, baslik), aciklama ? h("p", null, aciklama) : null)),
        h("div.ayar-icerik", null, cocuklar));
    },

    dugme: function (etiket, ikon, fn, sinif) {
      return h("button.dugme.kucuk." + (sinif || "cerceve"), { type: "button", onclick: fn }, ikon ? TA.ikon(ikon) : null, etiket);
    },

    ciz: function (v) {
      var self = this;
      var d = v.degerler;
      this.alanlar = {};
      var dizin = this.girdi("indirilenler", { yer: "İndirilenler klasörü" });
      var paralel = this.girdi("paralel", { tip: "number", min: 1, max: 10 });
      var aday = this.girdi("aday", { tip: "number", min: 1, max: 30 });

      this.cerezEl = h("div.oturum-durumu");
      this.bagisEl = h("p.ipucu");
      this.bagisDugme = this.dugme("Bağışımı geri çek", "kapat", function () { self.bagisGeriCek(); });
      this.anilistEl = h("div.oturum-durumu");
      this.secretIpucu = h("p.ipucu");
      this.discordEl = h("p.ipucu");
      this.arsivKur();

      TA.bosalt(this.govde);
      TA.ekle(this.govde, [
        this.kart("oynatma", "Oynatma ve İndirme", "Video kalitesi, indirme klasörü ve oynatma davranışı.", [
          this.satir("İndirme klasörü", h("div.girdi-grup", null, dizin,
            this.dugme("Gözat", "kutuphane", function () {
              TA.cagir("klasor_sec", { baslangic: dizin.value, baslik: "İndirme klasörü seç" }).then(function (yol) {
                if (yol) { dizin.value = yol; self.kirli(true); }
              });
            }))),
          this.satir("Paralel indirme", paralel, "Aynı anda inen bölüm sayısı (1-10)."),
          this.satir("1080p aday sayısı", aday, "Kaç video linki denenip en iyisinin seçileceği. Yükseltmek kaliteyi artırabilir, aramayı yavaşlatır."),
          this.anahtar("max_res", "En yüksek çözünürlüğü tercih et"),
          this.anahtar("dakika_hatirla", "Kaldığım dakikayı hatırla", "Yarıda kapattığın bölüm bir dahaki sefere oradan açılır."),
          this.anahtar("izlerken_kaydet", "İzlerken aynı anda kaydet", "Baştan sona izlenen bölüm indirilmiş sayılır."),
          this.anahtar("ilerlemeyi_sor", "Her bölümden sonra izleme ilerlemesini sor", "Kapalıyken biten bölüm ilerlemeye kendiliğinden yazılır."),
          this.anahtar("aria2c", "aria2c ile indir", "Çok bağlantılı indirme (aria2c kurulu olmalı).")
        ]),
        this.kart("liste", "Bölüm Listesi", "Detay sayfasındaki bölüm satırları.", [
          this.anahtar("izlendi_ikonu", "İzlendi/indirildi rozetleri", "Satırlarda ✓ İzlendi ve İndirildi etiketleri."),
          this.anahtar("manuel_fansub", "Fansub'u kendim seçeyim", "Birden çok çeviri grubu varsa oynatmadan/indirmeden önce sorulur; seçim seri boyunca hatırlanır. Kapalıyken en iyi çalışan video otomatik seçilir.")
        ]),
        this.kart("arsiv", "Çevrimdışı Arşiv (TürkAnime)", "turkanime.tv kapandı; TürkAnime kaynağı sitenin arşivinden okunur. Tüm arşivi indirirsen TürkAnime araması ve bölüm listeleri internetsiz çalışır. Videolar yine üçüncü parti sunuculardan (ok.ru, Sibnet, Mail.ru…) gelir.", [this.arsivEl]),
        this.kart("oturum", "Kaynak Oturumları", "Bazı kaynaklar oturum çerezi olmadan bölüm döndürmüyor.", [
          h("div.alt-baslik", null, "TRAnimeİzle oturum çerezi"),
          this.cerezEl,
          h("div.dugme-satiri", null,
            this.dugme("Tarayıcıdan Al", "dis", function () {
              TA.cagir("cerez_al").catch(function (e) { self.durumYaz(e.message, "hata"); });
            }, "birincil"),
            this.dugme("Temizle", "kapat", function () {
              TA.cagir("cerez_temizle").then(function (c) { self.cerezGoster(c); self.durumYaz("Çerez temizlendi."); },
                function (e) { self.durumYaz("Temizlenemedi: " + e.message, "hata"); });
            })),
          h("div.alt-baslik", null, "OpenAnime oturumu"),
          this.satir("Token", this.girdi("openani_token", { tip: "password", yer: "token çerezi (opsiyonel)" })),
          this.satir("Refresh Token", this.girdi("openani_refresh", { tip: "password", yer: "refreshToken çerezi (opsiyonel)" }),
            "Boş bırakılabilir. OpenAnime bazı bölümlerde giriş yapmış oturum istiyor; stream uçlarının hepsi 404 dönüyorsa tarayıcındaki openani.me çerezlerini gir.")
        ]),
        this.kart("bagis", "Oturum Kimliği Bağışı", "Kapalıyken hiçbir kimlik gönderilmez. Açarsan, çerez her alındığında ne bağışladığını anlatan bir onay penceresi çıkar; gönderim yalnızca onu onaylarsan yapılır.", [
          this.anahtar("kimlik_paylas", "Çerez aldığımda oturum kimliğimi bağışlamayı sor"),
          this.satir("Sunucu adresi", this.girdi("sunucu_adresi", { yer: "https://sunucu.example (boş = kapalı)" })),
          this.satir("API anahtarı", this.girdi("sunucu_anahtari", { tip: "password", yer: "Sunucu API anahtarı" })),
          this.bagisEl,
          h("div.dugme-satiri", null, this.bagisDugme)
        ]),
        this.kart("baglanti", "Bağlantı", "Cloudflare korumalı siteler için.", [
          this.satir("FlareSolverr", this.girdi("flaresolverr", { yer: "http://host:8191 (boş bırakılabilir)" }),
            "Boş bırakılırsa yalnızca yerel QtWebEngine çözücü kullanılır.")
        ]),
        this.kart("anilist", "AniList Hesabı", "İzleme Listesi ve ilerleme senkronu için.", [
          this.anilistEl,
          this.satir("Client ID", this.girdi("anilist_id", { yer: "AniList uygulama Client ID" })),
          this.satir("Client Secret", h("div", null, this.girdi("anilist_secret", { tip: "password", yer: "Client Secret (opsiyonel)" }), this.secretIpucu)),
          this.satir("Redirect URI", this.girdi("anilist_redirect", { yer: "http://localhost:9921/anilist-login" }),
            "AniList geliştirici panelindekiyle birebir aynı olmalı; portu yerel giriş sunucusu dinler."),
          h("div.dugme-satiri", null,
            this.dugme("AniList'e Giriş Yap", "dis", function () { self.anilistGiris(); }, "birincil"),
            this.dugme("Çıkış Yap", "kapat", function () {
              TA.cagir("anilist_cikis").then(function (s) { self.anilistGoster(s); self.durumYaz("AniList oturumu kapatıldı."); });
            }))
        ]),
        this.kart("bakim", "Discord ve Bakım", null, [
          this.anahtar("", "Discord Rich Presence", "Ne izlediğin Discord profilinde görünür.", function (acik) {
            TA.cagir("discord_ayarla", { acik: acik }).then(function (s) {
              self.discordEl.textContent = s.metin;
              self.durumYaz(s.mesaj);
            }, function (e) { self.durumYaz(e.message, "hata"); });
          }),
          this.discordEl,
          h("div.dugme-satiri", null,
            v.servisler.guncelleme ? this.dugme("Güncellemeleri Denetle", "yenile", function () { TA.cagir("guncelleme_denetle"); }) : null,
            v.servisler.gereksinim ? this.dugme("Gereksinimleri Denetle", "tamam", function () { TA.cagir("gereksinim_denetle"); }) : null)
        ])
      ]);

      // Değerler
      Object.keys(this.alanlar).forEach(function (alan) {
        var el = self.alanlar[alan];
        if (!(alan in d)) return;
        if (el.type === "checkbox") el.checked = !!d[alan];
        else el.value = d[alan];
      });
      this.govde.querySelector("[data-bolum=bakim] input[type=checkbox]").checked = !!d.discord;
      this.alanlar.anilist_id.value = v.anilist.client_id || "";
      this.alanlar.anilist_secret.value = v.anilist.client_secret || "";
      this.alanlar.anilist_redirect.value = v.anilist.redirect_uri || "";
      this.secretIpucu.textContent = v.anilist.sizan_uyari ||
        "Client Secret opsiyoneldir: boş bırakılırsa giriş, secret gerektirmeyen Implicit akışla yapılır. Yalnızca kendi AniList uygulamanı Authorization Code akışıyla kullanacaksan doldur.";
      this.secretIpucu.classList.toggle("uyari", !!v.anilist.sizan_uyari);
      this.discordEl.textContent = v.discord_metni;
      this.cerezGoster(v.cerez);
      this.bagisGoster(v.bagis);
      this.anilistGoster(v.anilist);
    },

    formDegerleri: function () {
      var self = this;
      var out = {};
      ["indirilenler", "paralel", "aday", "max_res", "dakika_hatirla", "izlerken_kaydet",
        "ilerlemeyi_sor", "aria2c", "izlendi_ikonu", "manuel_fansub", "flaresolverr",
        "openani_token", "openani_refresh", "kimlik_paylas", "sunucu_adresi", "sunucu_anahtari"
      ].forEach(function (alan) {
        var el = self.alanlar[alan];
        if (!el) return;
        out[alan] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
      });
      return out;
    },

    anilistDegerleri: function () {
      return {
        client_id: this.alanlar.anilist_id.value,
        client_secret: this.alanlar.anilist_secret.value,
        redirect_uri: this.alanlar.anilist_redirect.value
      };
    },

    kaydet: function () {
      var self = this;
      return TA.cagir("ayarlari_kaydet", { degerler: this.formDegerleri(), anilist: this.anilistDegerleri() }).then(function (s) {
        self.kirli(false);
        self.durumYaz(s.mesaj, "tamam");
        TA.bildir(s.mesaj, "tamam");
      }, function (e) {
        self.durumYaz("Kaydedilemedi: " + e.message, "hata");
      });
    },

    // ── Durum kutuları ──────────────────────────────────────────────────────
    cerezGoster: function (c) {
      if (!this.cerezEl) return;
      TA.bosalt(this.cerezEl);
      TA.ekle(this.cerezEl, [h("span.rozet" + (c.var ? ".yesil" : ""), null, TA.ikon(c.var ? "tamam" : "uyari"), c.var ? "Kayıtlı" : "Yok"),
        h("span", null, c.metin)]);
    },

    bagisGoster: function (b) {
      if (!this.bagisEl) return;
      this.bagisEl.textContent = b.metin;
      this.bagisEl.classList.toggle("tamam", !!b.kimlikler.length);
      this.bagisDugme.disabled = !b.kimlikler.length;
    },

    bagisGeriCek: function () {
      var self = this;
      TA.cagir("bagis_geri_cek").then(function (s) {
        self.bagisGoster(s);
        self.durumYaz(s.mesaj, s.tur);
      }, function (e) { self.durumYaz(e.message, "hata"); });
    },

    anilistGoster: function (a) {
      if (!this.anilistEl) return;
      TA.bosalt(this.anilistEl);
      TA.ekle(this.anilistEl, [h("span.rozet" + (a.giris ? ".yesil" : ""), null, TA.ikon(a.giris ? "tamam" : "bilgi"), a.giris ? "Bağlı" : "Bağlı değil"),
        h("span", null, a.metin)]);
    },

    anilistGiris: function () {
      var self = this;
      TA.cagir("anilist_giris", this.anilistDegerleri()).catch(function (e) { self.durumYaz(e.message, "hata"); });
    },

    // ── Arşiv ───────────────────────────────────────────────────────────────
    arsivKur: function () {
      var self = this;
      this.arsivKonum = h("dd", null, "—");
      this.arsivYer = h("dd.secilebilir", null, "—");
      this.arsivIcerik = h("dd", null, "Okunuyor…");
      this.arsivUyari = h("div.arsiv-uyarilari");
      this.arsivCubuk = h("i");
      this.arsivIlerlemeMetni = h("span");
      this.arsivIptal = this.dugme("İptal", "kapat", function () { TA.cagir("arsiv_iptal"); });
      this.arsivIlerlemeEl = h("div.arsiv-ilerleme", { hidden: true },
        h("div.arsiv-cubuk", null, this.arsivCubuk), h("div.arsiv-ilerleme-satir", null, this.arsivIlerlemeMetni, this.arsivIptal));
      this.arsivIndir = this.dugme("Tüm arşivi indir (~230 MB)", "indir", function () {
        TA.cagir("arsiv_indir").then(function () { self.arsivMesgul(true, "indirme"); self.arsivSonucYaz("Tam arşiv indiriliyor…"); },
          function (e) { self.arsivSonucYaz(e.message, "hata"); });
      }, "birincil");
      this.arsivKlasor = this.dugme("Klasör seç", "kutuphane", function () {
        TA.cagir("arsiv_klasor_sec").then(function (secildi) {
          if (secildi) { self.arsivMesgul(true, "islem"); self.arsivSonucYaz("Klasör denetleniyor…"); }
        }, function (e) { self.arsivSonucYaz(e.message, "hata"); });
      });
      this.arsivVarsayilan = this.dugme("Varsayılana dön", "yenile", function () {
        TA.cagir("arsiv_varsayilan").then(function (s) { self.arsivSonucYaz(s.mesaj, s.tur); self.arsivTazele(); },
          function (e) { self.arsivSonucYaz(e.message, "hata"); });
      });
      this.arsivSil = this.dugme("İndirilen arşivi sil", "kapat", function () { self.arsivSilOnay(); });
      this.arsivSonuc = h("p.durum.arsiv-sonuc");
      this.arsivEl = h("div.arsiv-paneli", null,
        h("dl.bilgi-tablosu", null, h("dt", null, "Etkin konum"), this.arsivKonum,
          h("dt", null, "Yer"), this.arsivYer, h("dt", null, "İçerik"), this.arsivIcerik),
        this.arsivUyari, this.arsivIlerlemeEl,
        h("div.dugme-satiri", null, this.arsivIndir, this.arsivKlasor, this.arsivVarsayilan, this.arsivSil),
        this.arsivSonuc);
      if (this.arsivSon) this.arsivGoster(this.arsivSon);
    },

    arsivTazele: function () {
      var self = this;
      TA.cagir("arsiv_durumu").then(function (s) { self.arsivSon = s; self.arsivGoster(s); },
        function (e) { self.arsivIcerik && (self.arsivIcerik.textContent = "Okunamadı."); self.arsivSonucYaz("Arşiv durumu okunamadı: " + e.message, "hata"); });
    },

    arsivGoster: function (s) {
      if (!this.arsivKonum) return;
      this.arsivKonum.textContent = s.konum;
      this.arsivYer.textContent = s.adres;
      this.arsivIcerik.textContent = s.icerik;
      TA.bosalt(this.arsivUyari);
      s.uyarilar.forEach(function (u) { this.arsivUyari.appendChild(h("p", null, TA.ikon("uyari"), u)); }, this);
      this.indirilenVar = s.indirilen_var;
      this.silinecek = s.indirilen_dizini;
      TA.bosalt(this.arsivIndir);
      TA.ekle(this.arsivIndir, [TA.ikon("indir"), s.indirilen_var ? "Arşivi güncelle" : "Tüm arşivi indir (~" + s.boyut_mb + " MB)"]);
      this.arsivMesgul(!!s.mesgul, s.mesgul);
    },

    arsivMesgul: function (mesgul, tur) {
      this.mesgul = mesgul;
      [this.arsivIndir, this.arsivKlasor, this.arsivVarsayilan].forEach(function (d) { d.disabled = mesgul; });
      this.arsivSil.disabled = mesgul || !this.indirilenVar;
      this.arsivIlerlemeEl.hidden = !(mesgul && tur === "indirme");
      this.arsivIptal.disabled = false;
    },

    arsivIlerleme: function (v) {
      if (!this.arsivIlerlemeEl) return;
      this.arsivIlerlemeEl.hidden = false;
      this.arsivIlerlemeMetni.textContent = v.metin;
      var cubuk = this.arsivIlerlemeEl.querySelector(".arsiv-cubuk");
      cubuk.classList.toggle("belirsiz", v.oran == null);
      this.arsivCubuk.style.width = v.oran == null ? "" : Math.round(v.oran * 100) + "%";
      if (v.metin === "İptal ediliyor…") this.arsivIptal.disabled = true;
    },

    arsivSonucYaz: function (mesaj, tur) {
      this.arsivSonuc.className = "durum arsiv-sonuc" + (tur && tur !== "bilgi" ? " " + tur : "");
      this.arsivSonuc.textContent = mesaj || "";
    },

    arsivSilOnay: function () {
      var self = this;
      TA.onayla({
        baslik: "İndirilen arşivi sil",
        metin: (this.silinecek || "İndirilen arşiv") + " klasörü ve içindeki bütün dosyalar silinecek. TürkAnime bundan sonra (varsa) depodaki arşivden ya da internetten okunur.",
        evet: "Sil", tehlikeli: true
      }).then(function (evet) {
        if (!evet) { self.arsivSonucYaz("Silme iptal edildi."); return; }
        TA.cagir("arsiv_sil", { onay: true }).then(function () {
          self.arsivMesgul(true, "islem");
          self.arsivSonucYaz("İndirilen arşiv siliniyor…");
        }, function (e) { self.arsivSonucYaz(e.message, "hata"); });
      });
    }
  };

  TA.sayfa("settings", ayarlar);
})();
