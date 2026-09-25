/* TürkAnime web arayüzü — çekirdek.
 *
 * TA ad alanı: köprü (Python çağrıları + olaylar), yönlendirici, DOM
 * yardımcıları, simgeler, biçimlendirme. Derleme adımı yok; betikler
 * index.html'deki sırayla yükleniyor.
 */
(function () {
  "use strict";

  var TA = (window.TA = window.TA || {});

  // ── Ortam ────────────────────────────────────────────────────────────────
  var parametreler = new URLSearchParams(window.location.search);
  TA.kabuk = parametreler.get("kabuk") || "web";
  document.documentElement.dataset.kabuk = TA.kabuk;

  // ── DOM yardımcısı ───────────────────────────────────────────────────────
  // TA.h("div.kart.aktif", {onclick: fn, title: "..."}, cocuk, [cocuklar], "metin")
  TA.h = function (secici, ozellikler) {
    var parcalar = String(secici).split(".");
    var el = document.createElement(parcalar[0] || "div");
    if (parcalar.length > 1) el.className = parcalar.slice(1).join(" ");
    var baslangic = 2;
    if (ozellikler == null || typeof ozellikler !== "object" || ozellikler instanceof Node ||
        Array.isArray(ozellikler)) {
      baslangic = 1;
    } else {
      Object.keys(ozellikler).forEach(function (ad) {
        var deger = ozellikler[ad];
        if (deger == null || deger === false) return;
        if (ad.slice(0, 2) === "on" && typeof deger === "function") {
          el.addEventListener(ad.slice(2), deger);
        } else if (ad === "class") {
          el.className += (el.className ? " " : "") + deger;
        } else if (ad === "style" && typeof deger === "object") {
          Object.keys(deger).forEach(function (k) { el.style.setProperty(k, deger[k]); });
        } else if (ad === "dataset") {
          Object.keys(deger).forEach(function (k) { el.dataset[k] = deger[k]; });
        } else if (ad in el && ad !== "list" && ad !== "form") {
          el[ad] = deger;
        } else {
          el.setAttribute(ad, deger === true ? "" : deger);
        }
      });
    }
    TA.ekle(el, Array.prototype.slice.call(arguments, baslangic));
    return el;
  };

  TA.ekle = function (ebeveyn, cocuklar) {
    (Array.isArray(cocuklar) ? cocuklar : [cocuklar]).forEach(function ekle(c) {
      if (c == null || c === false) return;
      if (Array.isArray(c)) return c.forEach(ekle);
      ebeveyn.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    });
    return ebeveyn;
  };

  TA.bosalt = function (el) {
    while (el.firstChild) el.removeChild(el.firstChild);
    return el;
  };

  // ── Simgeler (çizgi, 24'lük ızgara) ──────────────────────────────────────
  var YOLLAR = {
    ara: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    oynat: '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.4-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z" fill="currentColor" stroke="none"/>',
    indir: '<path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/>',
    yildiz: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" fill="currentColor" stroke="none"/>',
    sag: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    ileri: '<path d="m9 6 6 6-6 6"/>',
    geri: '<path d="m15 6-6 6 6 6"/>',
    yenile: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    kalp: '<path d="M12 20s-7-4.4-9.2-8.6C1.2 8.3 3 4.5 6.6 4.5c2.1 0 3.4 1.1 4.4 2.6 1-1.5 2.3-2.6 4.4-2.6 3.6 0 5.4 3.8 3.8 6.9C19 15.6 12 20 12 20z"/>',
    ayar: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    kivilcim: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><path d="m6.3 6.3 2.1 2.1M15.6 15.6l2.1 2.1M6.3 17.7l2.1-2.1M15.6 8.4l2.1-2.1"/>',
    ates: '<path d="M12 21c4 0 7-2.7 7-6.6 0-3.7-2.6-5.8-3.9-8.4-.5 1.9-1.6 3-2.9 3.6C12.6 6.8 11 4.6 8.8 3c.2 3.1-1.6 5-2.9 6.8C4.8 11.3 5 12.6 5 14.4 5 18.3 8 21 12 21z"/>',
    takvim: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    saat: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    uyari: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.3"/>',
    tamam: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    bilgi: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.3"/>',
    kutuphane: '<path d="M5 4.5v15M9.5 4.5v15"/><path d="m14 5.2 4.3 14.3"/>',
    film: '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M7 4.5v15M17 4.5v15M3 9.5h4M3 14.5h4M17 9.5h4M17 14.5h4"/>',
    dis: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    kapat: '<path d="M6 6l12 12M18 6 6 18"/>',
    arti: '<path d="M12 5v14M5 12h14"/>'
  };

  TA.ikon = function (ad, sinif) {
    var sablon = document.createElement("template");
    sablon.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' +
      (sinif ? ' class="' + sinif + '"' : "") + ">" + (YOLLAR[ad] || "") + "</svg>";
    return sablon.content.firstChild;
  };

  // ── Biçimlendirme ────────────────────────────────────────────────────────
  TA.sayi = function (n) {
    return n == null ? "—" : Number(n).toLocaleString("tr-TR");
  };

  // 0-10 puanın rengi (Qt temasındaki score_color ile aynı eşikler).
  TA.puanRengi = function (puan) {
    if (puan == null) return "var(--soluk)";
    if (puan >= 7.5) return "var(--yesil)";
    if (puan >= 4.5) return "var(--sari)";
    return "var(--kirmizi)";
  };

  TA.gorsel = function (url) {
    if (!url) return "";
    if (TA.demo && TA.demo.gorsel) return TA.demo.gorsel(url);
    return "ta://gorsel/?u=" + encodeURIComponent(url);
  };

  // ── Köprü ────────────────────────────────────────────────────────────────
  var kopru = null;
  var bekleyen = new Map();
  var kuyruk = [];
  var sayac = 0;
  var dinleyiciler = new Map();

  function yanitGeldi(istek, basarili, json) {
    var kayit = bekleyen.get(istek);
    if (!kayit) return;
    bekleyen.delete(istek);
    var veri = null;
    try {
      veri = JSON.parse(json);
    } catch (e) {
      basarili = false;
      veri = { mesaj: "yanıt çözülemedi" };
    }
    if (basarili) kayit.ok(veri);
    else kayit.red(Object.assign(new Error((veri && veri.mesaj) || "bilinmeyen hata"), { veri: veri }));
  }

  function olayGeldi(ad, json) {
    var veri = null;
    try {
      veri = JSON.parse(json);
    } catch (e) {
      return;
    }
    (dinleyiciler.get(ad) || []).slice().forEach(function (fn) {
      try {
        fn(veri);
      } catch (e) {
        console.error("olay dinleyicisi", ad, e);
      }
    });
  }

  function gonder(id, yontem, args) {
    if (kopru) {
      kopru.cagir(id, yontem, JSON.stringify(args || {}));
    } else if (TA.demo) {
      Promise.resolve()
        .then(function () { return TA.demo.cagir(yontem, args || {}); })
        .then(function (sonuc) { yanitGeldi(id, true, JSON.stringify(sonuc === undefined ? null : sonuc)); },
              function (hata) { yanitGeldi(id, false, JSON.stringify({ mesaj: String(hata && hata.message || hata) })); });
    } else {
      kuyruk.push([id, yontem, args]);
    }
  }

  // Python ucunu çağır: Promise döner; hata `err.message` Türkçe cümle.
  TA.cagir = function (yontem, args) {
    return new Promise(function (ok, red) {
      var id = String(++sayac);
      bekleyen.set(id, { ok: ok, red: red });
      gonder(id, yontem, args);
    });
  };

  // Olaylarla akan işlerin (arama) istek numarası: sayfalar ve pencereler
  // aynı olayları dinliyor, numara uygulama genelinde tekil olmalı.
  var istekNo = 0;
  TA.yeniIstek = function () {
    return ++istekNo;
  };

  TA.dinle = function (ad, fn) {
    if (!dinleyiciler.has(ad)) dinleyiciler.set(ad, []);
    dinleyiciler.get(ad).push(fn);
    return function () {
      var liste = dinleyiciler.get(ad) || [];
      var i = liste.indexOf(fn);
      if (i >= 0) liste.splice(i, 1);
    };
  };

  // Test/demo için: Python'dan gelmiş gibi olay yay.
  TA.yay = function (ad, veri) {
    olayGeldi(ad, JSON.stringify(veri));
  };

  TA.baglan = function () {
    return new Promise(function (ok) {
      if (typeof qt === "undefined" || !window.QWebChannel) {
        ok(false);        // tarayıcıda önizleme (demo) — köprü yok
        return;
      }
      new QWebChannel(qt.webChannelTransport, function (kanal) {
        kopru = kanal.objects.kopru;
        kopru.yanit.connect(yanitGeldi);
        kopru.olay.connect(olayGeldi);
        kuyruk.splice(0).forEach(function (is) { gonder(is[0], is[1], is[2]); });
        ok(true);
      });
    });
  };

  // Python'a gezinti/eylem isteği (sayfa aç, anime aç, ara ...). Gezinti
  // Python'dan geçiyor: menü, Discord durumu ve "Geri" hedefi orada tutuluyor.
  TA.ac = function (hedef, veri) {
    return TA.cagir("ac", { hedef: hedef, veri: veri || {} }).catch(function (e) {
      TA.bildir(e.message, "hata");
    });
  };

  // ── Yönlendirici ─────────────────────────────────────────────────────────
  // Sayfa tanımı: {kur(kap) -> void, goster(parametreler) -> void, gizle()?}
  // `kur` ilk ziyarette bir kez; `goster` her ziyarette. Sayfanın DOM'u
  // ziyaretler arasında korunuyor (kaydırma konumu, yüklenmiş kartlar).
  var sayfalar = {};
  var kaplar = {};
  TA.aktif = null;

  TA.sayfa = function (ad, tanim) {
    sayfalar[ad] = tanim;
  };

  TA.git = function (ad, parametreler) {
    var tanim = sayfalar[ad];
    if (!tanim) {
      console.warn("bilinmeyen sayfa:", ad);
      return false;
    }
    var icerik = document.getElementById("icerik");
    if (TA.aktif && TA.aktif !== ad && kaplar[TA.aktif]) {
      kaplar[TA.aktif].hidden = true;
      var eski = sayfalar[TA.aktif];
      if (eski && eski.gizle) eski.gizle();
    }
    var kap = kaplar[ad];
    var ilk = !kap;
    if (ilk) {
      kap = kaplar[ad] = TA.h("section.sayfa", { dataset: { sayfa: ad } });
      icerik.appendChild(kap);
      tanim.kur(kap);
    }
    kap.hidden = false;
    var onceki = TA.aktif;
    TA.aktif = ad;
    document.documentElement.dataset.aktifSayfa = ad;
    if (onceki !== ad) window.scrollTo(0, tanim.kaydirma || 0);
    document.querySelectorAll("[data-git]").forEach(function (a) {
      a.classList.toggle("aktif", a.dataset.git === ad);
    });
    if (tanim.goster) tanim.goster(parametreler || {}, ilk);
    return true;
  };

  // ── Bildirim ─────────────────────────────────────────────────────────────
  TA.bildir = function (mesaj, tur, sure) {
    tur = tur || "bilgi";
    var kap = document.getElementById("bildirimler");
    if (!kap || !mesaj) return;
    var ikon = { hata: "uyari", tamam: "tamam", bilgi: "bilgi" }[tur] || "bilgi";
    var el = TA.h("div.bildirim." + tur, { role: "status" }, TA.ikon(ikon), TA.h("span", null, mesaj));
    kap.appendChild(el);
    setTimeout(function () {
      el.classList.add("cikiyor");
      setTimeout(function () { el.remove(); }, 220);
    }, sure || (tur === "hata" ? 7000 : 4000));
  };
})();
