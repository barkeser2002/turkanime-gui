/* Ortak bileşenler: anime kartı, şerit, iskelet, boş durum. */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  // Poster: görsel yüklenince yumuşak geçiş, yüklenemezse baş harf.
  TA.poster = function (url, baslik, sinif) {
    var kap = h("div." + (sinif || "kart-poster"));
    var harf = h("span.bas-harf", null, (String(baslik || "?").trim()[0] || "?").toUpperCase());
    kap.appendChild(harf);
    if (url) {
      var img = h("img", { alt: "", loading: "lazy", decoding: "async", draggable: false });
      img.addEventListener("load", function () {
        img.classList.add("yuklu");
        harf.remove();
      });
      img.addEventListener("error", function () { img.remove(); });
      img.src = TA.gorsel(url);
      kap.appendChild(img);
    }
    return kap;
  };

  // Keşif/arama kartı. `veri`: Python'daki `veri.kart` çıktısı.
  //   {baslik, kapak, puan (0-10|null), rozet, alt}
  TA.kart = function (veri, secenek) {
    secenek = secenek || {};
    var poster = TA.poster(veri.kapak, veri.baslik);
    if (veri.puan != null) {
      poster.appendChild(h("span.kart-puan", { style: { "--renk": TA.puanRengi(veri.puan) } },
        TA.ikon("yildiz"), veri.puan.toFixed(1)));
    }
    if (veri.rozet) {
      var renk = veri.rozet_renk || secenek.rozetRenk;
      poster.appendChild(h("span.kart-rozet" + (renk ? ".renkli" : ""),
        renk ? { style: { "--renk": renk } } : null, veri.rozet));
    }
    if (secenek.sira) poster.appendChild(h("span.kart-sira", null, String(secenek.sira)));
    poster.appendChild(h("div.kart-ortu", null, h("span.oynat-dairesi", null, TA.ikon("oynat"))));

    var kart = h("article.kart", {
      tabindex: 0,
      title: veri.ipucu || veri.baslik,
      onclick: function () { if (secenek.tikla) secenek.tikla(veri); },
      onkeydown: function (e) {
        if ((e.key === "Enter" || e.key === " ") && secenek.tikla) {
          e.preventDefault();
          secenek.tikla(veri);
        }
      }
    },
      poster,
      h("div.kart-govde", null,
        h("h3.kart-baslik", null, veri.baslik),
        veri.alt ? h("p.kart-alt", null, veri.alt) : null));
    return kart;
  };

  TA.iskeletKartlar = function (adet) {
    var liste = [];
    for (var i = 0; i < adet; i++) {
      liste.push(h("div.kart.iskelet-kart", { "aria-hidden": "true" },
        h("div.kart-poster.iskelet"),
        h("div.kart-govde", null,
          h("div.iskelet.iskelet-satir", { style: { width: "86%" } }),
          h("div.iskelet.iskelet-satir", { style: { width: "54%", "margin-top": "6px" } }))));
    }
    return liste;
  };

  TA.bosDurum = function (secenek) {
    secenek = secenek || {};
    return h("div.bos-durum" + (secenek.hata ? ".hata" : ""), null,
      h("span.bos-ikon", null, TA.ikon(secenek.ikon || (secenek.hata ? "uyari" : "bilgi"))),
      secenek.baslik ? h("b", null, secenek.baslik) : null,
      secenek.metin ? h("p", null, secenek.metin) : null,
      secenek.eylem ? h("button.dugme.cerceve.kucuk", { onclick: secenek.eylem.fn },
        TA.ikon(secenek.eylem.ikon || "yenile"), secenek.eylem.etiket) : null);
  };

  // Yatay kaydırmalı şerit: başlık + oklar + "Tümünü Gör".
  // Dönen nesne: {el, kaydirma, doldur(dugumler)}
  TA.serit = function (secenek) {
    var kaydirma = h("div.serit-kaydirma" + (secenek.sinif ? "." + secenek.sinif : ""));
    var geri = h("button.ikon-dugme", { title: "Geri", "aria-label": "Geri", onclick: function () { kaydir(-1); } }, TA.ikon("geri"));
    var ileri = h("button.ikon-dugme", { title: "İleri", "aria-label": "İleri", onclick: function () { kaydir(1); } }, TA.ikon("ileri"));

    function kaydir(yon) {
      kaydirma.scrollBy({ left: yon * Math.max(200, kaydirma.clientWidth * 0.85) });
    }

    function oklariGuncelle() {
      var son = kaydirma.scrollWidth - kaydirma.clientWidth - 4;
      geri.disabled = kaydirma.scrollLeft <= 4;
      ileri.disabled = kaydirma.scrollLeft >= son;
    }

    kaydirma.addEventListener("scroll", oklariGuncelle, { passive: true });
    window.addEventListener("resize", oklariGuncelle);

    var el = h("section.serit", secenek.id ? { id: secenek.id } : null,
      h("header.serit-baslik", null,
        h("div.sol", null,
          h("h2", null, secenek.baslik),
          secenek.alt ? h("span.soluk.serit-alt", null, secenek.alt) : null),
        h("div.sag", null, geri, ileri,
          secenek.tumu ? h("button.baglanti", { onclick: secenek.tumu }, "Tümünü Gör", TA.ikon("sag")) : null)),
      kaydirma);

    return {
      el: el,
      kaydirma: kaydirma,
      doldur: function (dugumler) {
        TA.bosalt(kaydirma);
        TA.ekle(kaydirma, dugumler);
        kaydirma.scrollLeft = 0;
        requestAnimationFrame(oklariGuncelle);
      },
      altBaslik: function (metin) {
        var alt = el.querySelector(".serit-alt");
        if (alt) alt.textContent = metin;
      }
    };
  };
})();
