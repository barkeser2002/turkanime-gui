/* "Erişimi aç": bot doğrulamasına takılan kaynağın düğmesi, sonucu ve oturumları.
 *
 * Bir kaynak Cloudflare "Just a moment…"/Turnstile ya da LiteSpeed bot
 * doğrulamasına takılınca sayfa hatanın yanına `TA.erisimDugmesi` koyuyor.
 * Düğme Python'daki pencereyi açıyor (gui/web/uclar_erisim.py): kaynağın
 * sitesi uygulamanın içindeki tarayıcıda açılır, doğrulamayı KULLANICI çözer,
 * oturum kaydedilir. Sonuç `erisim_sonuc` olayıyla geliyor; düğme başarıda
 * sayfanın verdiği `sonra()`yı (kaynağı yeniden dene) çağırıyor.
 *
 *   TA.erisimAc(kaynak)            → Promise<{basarili, iptal, mesaj, kaynak}>
 *   TA.erisimDugmesi(kaynak, sonra) → <button>
 *   TA.erisimOturumlari(kap)        → Ayarlar'daki liste (tazeleme fonksiyonu döner)
 *
 * Oynatma arka planda doğrulamaya takılırsa Python `erisim_gerekli` yayıyor;
 * burada kalıcı bir bildirim (düğme + ×) çıkıyor, erişim açılınca bölüm
 * `erisim_yeniden` ile yeniden oynatılıyor.
 */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;

  // Kalkan simgesi: çekirdeğin simge tablosuyla aynı çizgi stili.
  function kalkan() {
    var sablon = document.createElement("template");
    sablon.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M12 3 5 6v5.5c0 4.3 2.9 8 7 9.5 4.1-1.5 7-5.2 7-9.5V6z"/>' +
      '<path d="m9 12 2 2 4-4"/></svg>';
    return sablon.content.firstChild;
  }

  // İstek numarası → sözün çözücüsü. Numarayı sayfa veriyor ve dinleyici
  // çağrıdan ÖNCE kuruluyor: olay yanıttan önce gelse de kaybolmuyor.
  var bekleyen = new Map();
  TA.dinle("erisim_sonuc", function (v) {
    var coz = bekleyen.get(v.istek);
    if (!coz) return;
    bekleyen.delete(v.istek);
    coz(v);
  });

  TA.erisimAc = function (kaynak) {
    var istek = TA.yeniIstek();
    var sonuc = new Promise(function (coz) { bekleyen.set(istek, coz); });
    return TA.cagir("erisim_ac", { kaynak: kaynak, istek: istek }).then(function () {
      return sonuc;
    }, function (e) {
      bekleyen.delete(istek);
      throw e;
    });
  };

  TA.erisimDugmesi = function (kaynak, sonra) {
    var etiket = h("span", null, "Erişimi aç");
    var dugme = h("button.dugme.kucuk.erisim-dugme", {
      type: "button",
      dataset: { erisim: kaynak },
      title: "Sitenin bot doğrulamasını uygulamanın içindeki tarayıcıda kendin geç; " +
        "oturum kaydedilir ve kaynak yeniden denenir.",
      onclick: function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (dugme.disabled) return;
        dugme.disabled = true;
        dugme.classList.add("bekliyor");
        etiket.textContent = "Doğrulama penceresi açık…";
        function birak() {
          dugme.disabled = false;
          dugme.classList.remove("bekliyor");
          etiket.textContent = "Erişimi aç";
        }
        TA.erisimAc(kaynak).then(function (s) {
          birak();
          TA.bildir(s.mesaj, s.basarili ? "tamam" : s.iptal ? "bilgi" : "hata");
          if (s.basarili && sonra) sonra(s);
        }, function (hata) {
          birak();
          TA.bildir(hata.message, "hata");
        });
      }
    }, kalkan(), etiket);
    return dugme;
  };

  // ── Oynatma engeli: kalıcı bildirim ──────────────────────────────────────
  var acikBildirimler = {};

  function engelBildirimi(v) {
    var kap = document.getElementById("bildirimler");
    if (!kap || !v || !v.kaynak) return;
    if (acikBildirimler[v.kaynak]) acikBildirimler[v.kaynak].remove();
    var el;
    function kapat() {
      if (acikBildirimler[v.kaynak] === el) delete acikBildirimler[v.kaynak];
      el.classList.add("cikiyor");
      setTimeout(function () { el.remove(); }, 220);
    }
    var dugme = TA.erisimDugmesi(v.kaynak, function () {
      kapat();
      if (v.anahtar) {
        TA.cagir("erisim_yeniden", { anahtar: v.anahtar }).catch(function (e) {
          TA.bildir(e.message, "hata");
        });
      }
    });
    el = h("div.bildirim.hata.erisim-bildirimi", { role: "alert", dataset: { kaynak: v.kaynak } },
      TA.ikon("uyari"),
      h("div.erisim-bildirim-govde", null, h("span", null, v.mesaj), dugme),
      h("button.ikon-dugme.bildirim-kapat", {
        type: "button", title: "Kapat", "aria-label": "Kapat", onclick: kapat
      }, TA.ikon("kapat")));
    kap.appendChild(el);
    acikBildirimler[v.kaynak] = el;
  }

  TA.dinle("erisim_gerekli", engelBildirimi);

  // ── Ayarlar: kayıtlı erişim oturumları ───────────────────────────────────
  TA.erisimOturumlari = function (kap) {
    function ciz(liste) {
      TA.bosalt(kap);
      if (!liste || !liste.length) {
        kap.appendChild(h("p.ipucu.erisim-bos", null,
          "Kayıtlı erişim oturumu yok. Bir kaynak bot doğrulamasına takılınca " +
          "hatanın yanında “Erişimi aç” çıkar."));
        return;
      }
      liste.forEach(function (s) {
        var bilgi = s.yas + " kaydedildi · " + s.cerez + " çerez" +
          (s.gecerlilik ? " · " + s.gecerlilik : "");
        kap.appendChild(h("div.erisim-satiri", { dataset: { kaynak: s.kaynak } },
          h("span.kaynak-hap", { style: { "--renk": s.renk || "var(--soluk)" } }, s.etiket),
          h("span.erisim-bilgi", { title: (s.alanlar || []).join(", ") }, bilgi),
          h("span.bosluk"),
          h("button.dugme.kucuk.cerceve.erisim-temizle", {
            type: "button",
            title: "Kaydedilen çerezleri ve gömülü tarayıcının bu siteye ait verisini sil",
            onclick: function () { temizle(s); }
          }, TA.ikon("kapat"), "Temizle")));
      });
    }
    function temizle(s) {
      TA.cagir("erisim_temizle", { kaynak: s.kaynak }).then(function (liste) {
        ciz(liste);
        TA.bildir(s.etiket + " erişim oturumu temizlendi.", "bilgi");
      }, function (e) { TA.bildir("Temizlenemedi: " + e.message, "hata"); });
    }
    function tazele() {
      return TA.cagir("erisim_oturumlari").then(ciz, function (e) {
        TA.bosalt(kap).appendChild(h("p.ipucu.uyari", null,
          "Erişim oturumları okunamadı: " + e.message));
      });
    }
    kap.classList.add("erisim-oturumlari");
    kap._erisimTazele = tazele;
    tazele();
    return tazele;
  };

  // Bir kaynağın oturumu değişti (açıldı/temizlendi): açık listeler tazelensin.
  TA.dinle("erisim_degisti", function () {
    document.querySelectorAll(".erisim-oturumlari").forEach(function (kap) {
      if (kap._erisimTazele) kap._erisimTazele();
    });
  });
})();
