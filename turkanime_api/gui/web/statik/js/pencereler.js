/* Qt diyaloglarından taşınan pencereler — `TA.soruTurleri` çizicileri.
 *
 * Her tür bir Python sorusu (gui/web/sorular.py); verisi ve kuralları
 * gui/web/pencereler.py, gui/web/katki.py ve gui/qt/fansub.py'de. Ortak
 * düzen: Esc, × ve (izin verilen yerde) dış tık "vazgeç" demek; cevabı
 * Python doğruluyor, reddederse mesaj pencerenin içinde gösteriliyor.
 *
 *   fansub       — fansub seçimi + "Bu seri için hatırla"
 *   ilerleme     — "Kaçıncı bölümü tamamladınız?"
 *   guncelleme   — yeni sürüm, değişiklikler, indirme ilerlemesi
 *   gereksinim   — eksik araçlar, kurulum, "Atla"
 *   bagis_onayi  — oturum kimliği bağışı onayı (gizlilik: varsayılan HAYIR)
 *   kapanis      — süren indirme varken kapanış sorusu
 */
(function () {
  "use strict";

  var TA = window.TA;
  var h = TA.h;
  var turler = TA.soruTurleri;

  function dugme(sinif, etiket, fn, ikon) {
    return h("button.dugme." + sinif, { type: "button", onclick: fn }, ikon ? TA.ikon(ikon) : null, etiket);
  }

  function etiketYaz(dugmeEl, etiket, ikon) {
    TA.bosalt(dugmeEl);
    TA.ekle(dugmeEl, [ikon ? TA.ikon(ikon) : null, etiket]);
  }

  // Pencere içindeki hata/uyarı satırı (boşken görünmez).
  function notSatiri(metin) {
    return h("p.modal-not", { role: "status" }, metin || "");
  }

  // İlerleme çubuğu + metni (güncelleme/gereksinim ortak).
  function ilerlemeAlani() {
    var ic = h("i");
    var cubuk = h("div.soru-cubuk", { hidden: true }, ic);
    var metin = h("p.soru-durum");
    return {
      el: [cubuk, metin],
      ciz: function (gorunur, yuzde, yazi, tur) {
        cubuk.hidden = !gorunur;
        ic.style.width = Math.max(0, Math.min(100, Number(yuzde) || 0)) + "%";
        metin.textContent = yazi || "";
        metin.className = "soru-durum" + (tur ? " " + tur : "");
      }
    };
  }

  // ── Fansub ───────────────────────────────────────────────────────────────
  // Enter = Tamam (Qt'de varsayılan düğme Tamam'dı); çift tık da seçer.
  turler.fansub = function (v, api) {
    var ad = "fansub-" + Math.random().toString(36).slice(2);
    var tamamEl;
    var not = notSatiri();
    var liste = h("div.secenek-listesi", { role: "radiogroup", "aria-label": "Fansub" },
      (v.secenekler || []).map(function (s, i) {
        var girdi = h("input", { type: "radio", name: ad, dataset: { sira: String(i) } });
        girdi.checked = s.deger === v.secili;
        return h("label.secenek", { ondblclick: function () { girdi.checked = true; tamam(); } },
          girdi, h("span", null, s.etiket));
      }));
    var hatirla = h("input", { type: "checkbox" });
    hatirla.checked = v.hatirla !== false;

    function secim() {
      var r = liste.querySelector("input:checked");
      return r ? v.secenekler[Number(r.dataset.sira)].deger : "";
    }
    function tamam() {
      if (tamamEl.disabled) return;
      tamamEl.disabled = true;
      not.textContent = "";
      api.cevapla({ secim: secim(), hatirla: hatirla.checked }).catch(function (e) {
        not.textContent = e.message;
        tamamEl.disabled = false;
      });
    }
    function vazgec() { api.vazgec(null); }

    tamamEl = dugme("birincil", "Tamam", tamam, "tamam");
    var pk = TA.pencere({
      sinif: "soru-penceresi", dataset: { soru: "fansub" }, etiket: "Fansub seç",
      vazgec: vazgec,
      enter: function (e) {
        if (e.target && e.target.tagName === "BUTTON") return;   // odaktaki düğme kendi işini yapar
        e.preventDefault();
        tamam();
      },
      icerik: [
        TA.pencereBasligi("Fansub seç", "“" + v.baslik + "” birden çok çeviri grubuyla var. Hangisi oynatılsın/indirilsin?", vazgec),
        liste,
        h("label.onay-kutusu", null, hatirla, h("span", null, "Bu seri için hatırla (bir daha sorma)")),
        not,
        h("div.dugme-satiri.sag", null, dugme("hayalet", "Vazgeç", vazgec), tamamEl)
      ]
    });
    (liste.querySelector("input:checked") || tamamEl).focus();
    return { kapat: pk.kapat };
  };

  // ── İzleme ilerlemesi ────────────────────────────────────────────────────
  // Enter (sayı kutusunda) = Kaydet; kaydedilemezse pencere açık, not görünür.
  turler.ilerleme = function (v, api) {
    var girdi = h("input.girdi.sayi-girdisi", {
      type: "number", min: v.en_az, max: v.en_cok, step: 1, value: String(v.bolum_no),
      "aria-label": "Tamamlanan bölüm", autocomplete: "off"
    });
    var not = notSatiri(v.not);
    var kaydet = h("button.dugme.birincil", { type: "submit", disabled: !v.kaydedilebilir }, TA.ikon("tamam"), "Kaydet");
    function vazgec() { api.vazgec(null); }
    function gonder(e) {
      e.preventDefault();
      if (kaydet.disabled) return;
      kaydet.disabled = true;
      not.textContent = "";
      api.cevapla({ no: Number(girdi.value) }).catch(function (hata) {
        not.textContent = hata.message;
        kaydet.disabled = !v.kaydedilebilir;
      });
    }
    var pk = TA.pencere({
      sinif: "soru-penceresi.dar", dataset: { soru: "ilerleme" }, etiket: "İzleme İlerlemesi",
      vazgec: vazgec,
      icerik: [
        TA.pencereBasligi("İzleme İlerlemesi Kaydet", v.anime_adi + "\n" + v.bolum_adi, vazgec),
        h("form.soru-formu", { onsubmit: gonder },
          h("label.soru-etiket", null, "Kaçıncı bölümü tamamladınız?", girdi),
          not,
          h("div.dugme-satiri.sag", null, dugme("hayalet", "Atla", vazgec), kaydet))
      ]
    });
    if (v.kaydedilebilir) {
      girdi.focus();
      girdi.select();
    }
    return { kapat: pk.kapat };
  };

  // ── Güncelleme ───────────────────────────────────────────────────────────
  // Süren indirme varken dış tık kapatmaz (yanlışlıkla kapanan pencere
  // ilerlemeyi ve "Klasörü Aç"ı götürür); Esc, × ve "Daha Sonra" kapatır.
  turler.guncelleme = function (v, api) {
    var d = Object.assign({}, v);
    var alan = ilerlemeAlani();
    var klasor = dugme("cerceve", "Klasörü Aç", function () {
      api.eylem("klasor").catch(function (e) { alan.ciz(true, d.yuzde, e.message, "hata"); });
    }, "kutuphane");
    var indir = dugme("birincil", "Güncellemeyi İndir", function () {
      indir.disabled = true;
      api.eylem("indir").then(ciz, function (e) {
        d.metin = e.message;
        d.durum = "hata";
        ciz();
      });
    }, "indir");
    var sonra = dugme("hayalet", "Daha Sonra", vazgec);
    function vazgec() { api.vazgec(null); }

    function ciz() {
      var durum = d.durum;
      alan.ciz(durum === "iniyor" || durum === "tamam", d.yuzde, d.metin,
        durum === "hata" ? "hata" : durum === "tamam" ? "tamam" : "");
      indir.hidden = durum === "tamam";
      indir.disabled = durum === "iniyor";
      etiketYaz(indir, durum === "iniyor" ? "İndiriliyor…" : durum === "hata" ? "Tekrar Dene" : "Güncellemeyi İndir", "indir");
      klasor.hidden = durum !== "tamam";
      sonra.textContent = durum === "tamam" ? "Kapat" : "Daha Sonra";
    }

    var bilgi = h("dl.bilgi-tablosu.soru-bilgi", null,
      h("dt", null, "Mevcut sürüm"), h("dd", null, v.mevcut),
      h("dt", null, "Yeni sürüm"), h("dd", null, v.yeni),
      v.tarih ? [h("dt", null, "Yayın tarihi"), h("dd", null, v.tarih)] : null);
    var pk = TA.pencere({
      sinif: "soru-penceresi", dataset: { soru: "guncelleme" }, etiket: "Güncelleme Mevcut",
      vazgec: vazgec, disTik: false,
      icerik: [
        TA.pencereBasligi("Yeni sürüm yayımlandı", null, vazgec),
        bilgi,
        h("div.alt-baslik", null, "Değişiklikler"),
        h("pre.degisiklikler.secilebilir", null, v.degisiklikler),
        alan.el,
        h("div.dugme-satiri.sag", null, klasor, sonra, indir)
      ]
    });
    ciz();
    indir.focus();
    return {
      kapat: pk.kapat,
      guncelle: function (degisen) {
        Object.assign(d, degisen);
        ciz();
      }
    };
  };

  // ── Gereksinim sihirbazı ─────────────────────────────────────────────────
  // "Atla" = bir daha sorma (Python tercihi yazar); Esc/×/"Kapat" yalnızca kapatır.
  turler.gereksinim = function (v, api) {
    var d = Object.assign({}, v);
    var alan = ilerlemeAlani();
    var kur = dugme("birincil", "İndir ve Kur", function () {
      kur.disabled = true;
      api.eylem("kur").then(ciz, function (e) {
        d.metin = e.message;
        d.durum = "hata";
        ciz();
      });
    }, "indir");
    var atla = dugme("hayalet", "Atla", function () {
      if (d.durum === "tamam") vazgec();
      else api.cevapla({ atla: true }).catch(function (e) { alan.ciz(true, d.yuzde, e.message, "hata"); });
    });
    function vazgec() { api.vazgec(null); }

    function ciz() {
      var durum = d.durum;
      alan.ciz(durum !== "hazir", d.yuzde, d.metin,
        durum === "hata" ? "hata" : durum === "tamam" ? "tamam" : "");
      kur.disabled = durum === "kuruluyor" || durum === "tamam";
      etiketYaz(kur, { kuruluyor: "Kuruluyor…", tamam: "Tamamlandı", hata: "Tekrar Dene" }[durum] || "İndir ve Kur",
        durum === "tamam" ? "tamam" : "indir");
      atla.textContent = durum === "tamam" ? "Kapat" : "Atla";
      atla.title = durum === "tamam" ? "" : "Bir daha sorma (Ayarlar'daki “Gereksinimleri Denetle” geri alır)";
    }

    var pk = TA.pencere({
      sinif: "soru-penceresi", dataset: { soru: "gereksinim" }, etiket: "Eksik Gereksinimler",
      vazgec: vazgec, disTik: false,
      icerik: [
        TA.pencereBasligi("Bazı araçlar bulunamadı", null, vazgec),
        h("div.arac-listesi", null, (v.eksikler || []).map(function (a) {
          return h("span.hap", null, TA.ikon("uyari"), a);
        })),
        h("p.soluk", null, "Bunlar olmadan oynatma, birleştirme veya indirme çalışmayabilir. Otomatik kurulumu şimdi yapabilirsiniz."),
        alan.el,
        h("div.dugme-satiri.sag", null, atla, kur)
      ]
    });
    ciz();
    kur.focus();
    return {
      kapat: pk.kapat,
      guncelle: function (degisen) {
        Object.assign(d, degisen);
        ciz();
      }
    };
  };

  // ── Oturum kimliği bağışı ────────────────────────────────────────────────
  // Kaza sonucu onay OLMAZ (gui/web/katki.py, kural 3):
  //  * "Kimliğimi bağışla" kutu işaretlenmeden PASİF; tıklama yine gelse de
  //    kutu işaretli değilse hiçbir şey gönderilmez.
  //  * Odak "Vazgeç"te ve pencere açıkken Enter HER ZAMAN vazgeçer — odak onay
  //    düğmesinde olsa bile (Qt'de Enter varsayılan düğmeye, Vazgeç'e gidiyordu).
  //    Onay yalnızca tıklama ya da onay düğmesindeyken Boşluk ile.
  //  * Esc, ×, dış tık = vazgeç. Metin DÜZ METİN (textContent), HTML değil.
  //  * Python da yalnızca {onay: true, okudum: true}'yu onay sayıyor.
  turler.bagis_onayi = function (v, api) {
    var kutu = h("input", { type: "checkbox" });
    var not = notSatiri();
    var onay;
    function vazgec() { api.vazgec({ onay: false, okudum: kutu.checked === true }); }
    onay = dugme("tehlike", v.onay_dugmesi || "Kimliğimi bağışla", function () {
      if (onay.disabled || kutu.checked !== true) return;
      onay.disabled = true;
      api.cevapla({ onay: true, okudum: kutu.checked === true }).catch(function (e) {
        not.textContent = e.message;
        onay.disabled = kutu.checked !== true;
      });
    });
    onay.disabled = true;
    kutu.addEventListener("change", function () { onay.disabled = kutu.checked !== true; });
    var vazgecEl = dugme("cerceve", v.vazgec_dugmesi || "Vazgeç", vazgec);
    var metin = h("div.bagis-metni.secilebilir", { tabindex: 0 });
    metin.textContent = v.metin || "";

    var pk = TA.pencere({
      sinif: "soru-penceresi.bagis-penceresi", rol: "alertdialog", dataset: { soru: "bagis_onayi" },
      etiket: v.baslik,
      vazgec: vazgec,
      enter: function (e) {
        e.preventDefault();
        vazgec();
      },
      icerik: [
        TA.pencereBasligi(v.baslik, null, vazgec),
        metin,
        h("label.onay-kutusu", null, kutu, h("span", null, v.kutu)),
        not,
        h("div.dugme-satiri.sag", null, vazgecEl, onay)
      ]
    });
    vazgecEl.focus();
    return { kapat: pk.kapat };
  };

  // ── Kapanış ──────────────────────────────────────────────────────────────
  // QMessageBox'taki gibi: Enter = Evet (odak orada), Esc/dış tık = Hayır.
  turler.kapanis = function (v, api) {
    var kapandi = false;
    var soz = TA.onayla({ baslik: v.baslik, metin: v.metin, evet: "Evet", hayir: "Hayır",
      dataset: { soru: "kapanis" } });
    soz.then(function (evet) {
      if (!kapandi) api.vazgec(evet === true);
    });
    return {
      kapat: function () {
        kapandi = true;               // Python kapattı: cevap yollanmaz
        soz.kapat();
      }
    };
  };
})();
