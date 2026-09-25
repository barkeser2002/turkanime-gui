/* Uygulama kabuğu: üst çubuk (marka, menü, arama, AniList, ayarlar) ve alt
 * durum çubuğu. Eski ekran görüntüsündeki düzen: logo + kırmızı marka,
 * menü, kaynak seçmeli arama kutusu, dişli.
 *
 * Gezinti Python'dan geçiyor (`TA.ac("sayfa")`): menü, Discord durumu ve
 * detaydaki "Geri" hedefi orada tutuluyor. Seçili menü öğesi `TA.git`'te
 * `[data-git]` üzerinden işaretleniyor.
 */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  var MENU = [
    ["home", "Ana Sayfa"],
    ["trending", "Trend"],
    ["season", "Bu Sezon"],
    ["library", "Kitaplığım"],
    ["watchlist", "İzleme Listesi"],
    ["downloads", "İndirilenler"]
  ];

  function git(ad) {
    return function (e) {
      if (e) e.preventDefault();
      TA.ac("sayfa", { ad: ad });
    };
  }

  TA.kabukKur = function () {
    var ust = document.getElementById("ust-bar");
    if (!ust) return;
    var sayac = h("span.menu-sayac", { hidden: true });
    var menu = h("nav.ust-menu", { "aria-label": "Ana menü" }, MENU.map(function (m) {
      return h("a.menu-ogesi", { href: "#", dataset: { git: m[0] }, onclick: git(m[0]) },
        m[1], m[0] === "downloads" ? sayac : null);
    }));

    var girdi = h("input", { type: "search", placeholder: "Anime ara…", "aria-label": "Anime ara", autocomplete: "off" });
    var kaynak = h("select.ust-kaynak", { "aria-label": "Kaynak", title: "Hangi kaynakta aransın" },
      h("option", { value: "" }, "Tüm kaynaklar"));
    var ara = h("form.ust-ara", {
      role: "search",
      onsubmit: function (e) {
        e.preventDefault();
        var sorgu = girdi.value.trim();
        if (sorgu) TA.ac("arama", { sorgu: sorgu, kaynak: kaynak.value });
      }
    }, TA.ikon("ara"), girdi, kaynak);

    var avatar = h("img.avatar", { alt: "", hidden: true });
    var kullanici = h("button.kullanici-cipi", { type: "button", onclick: function () {
      TA.ac("sayfa", { ad: kullanici.dataset.giris === "1" ? "watchlist" : "settings" });
    } }, avatar, TA.ikon("yildiz", "kullanici-ikon"), h("span", null, "AniList"));
    var ayar = h("a.ikon-dugme.ust-ayar", { href: "#", title: "Ayarlar", "aria-label": "Ayarlar", dataset: { git: "settings" }, onclick: git("settings") }, TA.ikon("ayar"));

    TA.ekle(ust, [
      h("a.marka", { href: "#", onclick: git("home"), title: "Ana Sayfa" },
        h("img", { src: "img/logo.png", alt: "" }),
        h("span", null, "Türk", h("em", null, "Anime"))),
      menu,
      h("span.bosluk"),
      ara,
      kullanici,
      ayar
    ]);

    TA.kabuk_ = { girdi: girdi, kaynak: kaynak, sayac: sayac, kullanici: kullanici, avatar: avatar };

    TA.dinle("indirme_sayaci", function (v) {
      sayac.hidden = !v.sayi;
      sayac.textContent = String(v.sayi || "");
    });
    TA.dinle("anilist_kullanici", TA.kullaniciGoster);
    TA.dinle("arama_metni", function (v) { girdi.value = v.sorgu || ""; });

    // Kaynak listesi (arama kutusundaki seçim).
    TA.cagir("kaynaklar").then(function (liste) {
      liste.forEach(function (k) {
        if (k.oynatilabilir) kaynak.appendChild(h("option", { value: k.ad }, k.etiket));
      });
    }, function () {});
    TA.cagir("kabuk_durumu").then(function (d) {
      TA.kullaniciGoster(d.kullanici);
      sayac.hidden = !d.indirme;
      sayac.textContent = String(d.indirme || "");
      if (TA.durum_) TA.durum_.surum.textContent = d.surum || "";
      if (d.durum) TA.durumYaz(d.durum);
    }, function () {});

    // Alt durum çubuğu (eski arayüzdeki gibi).
    var durumMetni = h("span.durum-metni");
    var surum = h("span.surum");
    var alt = h("footer.durum-cubugu", null, durumMetni, h("span.bosluk"), surum);
    document.body.appendChild(alt);
    TA.durum_ = { metin: durumMetni, surum: surum };
    TA.dinle("durum_mesaji", TA.durumYaz);
  };

  TA.kullaniciGoster = function (k) {
    var ks = TA.kabuk_;
    if (!ks) return;
    k = k || {};
    ks.kullanici.dataset.giris = k.ad ? "1" : "0";
    ks.kullanici.lastChild.textContent = k.ad || "Giriş yap";
    ks.kullanici.title = k.ad ? "AniList: " + k.ad + " — İzleme Listem" : "AniList'e giriş için Ayarlar";
    ks.kullanici.classList.toggle("girisli", !!k.ad);
    if (k.avatar) {
      ks.avatar.src = TA.gorsel(k.avatar);
      ks.avatar.hidden = false;
    } else {
      ks.avatar.hidden = true;
    }
    ks.kullanici.querySelector(".kullanici-ikon").style.display = k.avatar ? "none" : "";
  };

  TA.durumYaz = function (v) {
    var d = TA.durum_;
    if (!d) return;
    d.metin.textContent = v.mesaj || "";
    d.metin.className = "durum-metni" + (v.tur && v.tur !== "bilgi" ? " " + v.tur : "");
    // Hata kalıcı satırda kalıyor; kullanıcı başka yere bakıyorsa diye ayrıca bildirim.
    if (v.mesaj && v.tur === "hata") TA.bildir(v.mesaj, "hata");
  };
})();
