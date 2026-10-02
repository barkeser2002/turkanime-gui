"""“Erişimi aç” — bot doğrulamasını kullanıcının çözdüğü pencere ve oturumlar.

Bir kaynak bot korumasına takılınca (Cloudflare "Just a moment…"/Turnstile,
LiteSpeed bot doğrulaması; bkz. `common.oturumlar.erisim_engeli_mi`) sayfa
hatanın yanında "Erişimi aç" gösteriyor. Düğme ``erisim_ac`` ucunu çağırıyor:
kaynağın sitesi gömülü tarayıcıda açılıyor (`gui.qt.erisim_penceresi`),
kullanıcı doğrulamayı çözüyor, oturum `oturumlar.json`'a yazılıyor ve sonuç
``erisim_sonuc`` olayıyla dönüyor; sayfa o kaynağı (arama, bölümler)
kendiliğinden yeniden deniyor. TRAnimeİzle'de pencere eskisi gibi çerez
toplayıcı (`cookie_browser`): oturum Ayarlar'ın yolundan `ayarlar.json`'a.

Sonuç olayla, çünkü pencere dakikalarca açık kalabiliyor ve köprünün uçları
eşzamanlı. İstek numarasını SAYFA veriyor (arama sayfasındaki gibi): sayfa
dinleyiciyi çağrıdan önce kuruyor, olay yanıttan önce gelse de kaybolmuyor.
Aynı kaynağın penceresi zaten açıksa yeni pencere açılmıyor, öne geliyor ve
sonuç bekleyen bütün isteklere gidiyor.

Uçlar GUI thread'inde (pencere Qt nesnesi). ``engel_bildir`` her thread'den
çağrılabilir: oynatma arka planda bot doğrulamasına takılınca sayfaya
``erisim_gerekli`` olayı gidiyor, "Erişimi aç" başarılı olunca sayfa
``erisim_yeniden`` ile oynatmayı yeniden başlatıyor.

Olaylar: ``erisim_sonuc`` {istek, kaynak, etiket, basarili, iptal, mesaj},
``erisim_degisti`` {kaynak} (Ayarlar listesi tazelensin),
``erisim_gerekli`` {kaynak, etiket, mesaj, anahtar}.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from ...common import oturumlar
from ...sources import kayit as kaynak_kaydi
from .kopru import Kopru, UcHatasi, uc

# Yeniden denenmeyi bekleyen engellenmiş işler (oynatma) en çok bu kadar;
# eskileri düşer (kullanıcı düğmeye hiç basmamış olabilir).
AZAMI_BEKLEYEN = 20


# ── Qt'siz yardımcılar ───────────────────────────────────────────────────────
def yas_metni(saniye: float) -> str:
    """Kaydın yaşı: "az önce", "12 dk önce", "3 sa önce", "2 gün önce"."""
    saniye = max(0, int(saniye))
    if saniye < 60:
        return "az önce"
    if saniye < 3600:
        return f"{saniye // 60} dk önce"
    if saniye < 86400:
        return f"{saniye // 3600} sa önce"
    return f"{saniye // 86400} gün önce"


def gecerlilik_metni(kayit: Dict[str, Any]) -> str:
    """Oturumun ne zamana kadar geçerli olduğu (Cloudflare çerezine göre).

    `cf_clearance` varsa onun bitişi; yoksa çerezlerin EN GEÇ bitişi.
    Hepsi oturum çerezi ise tarih yok: kayıt, "Temizle"ye kadar duruyor.
    """
    cerezler = kayit.get("cerezler") or []
    clearance = [int(c.get("expiry") or 0) for c in cerezler
                 if c.get("name") == "cf_clearance" and c.get("expiry")]
    bitisler = clearance or [int(c.get("expiry") or 0) for c in cerezler if c.get("expiry")]
    if not bitisler:
        return "süresiz (Temizle'ye kadar)"
    bitis = min(bitisler) if clearance else max(bitisler)
    try:
        return datetime.fromtimestamp(bitis).strftime("%d.%m.%Y %H:%M") + "'e kadar"
    except (OverflowError, OSError, ValueError):
        return ""


def oturum_satirlari(simdi: Optional[float] = None) -> List[Dict[str, Any]]:
    """Ayarlar'daki liste: kayıtlı erişim oturumları, en yenisi başta."""
    simdi = time.time() if simdi is None else simdi
    satirlar = []
    for ad, kayit in oturumlar.kayitlar().items():
        kaynak = kaynak_kaydi.bul(ad)
        zaman = int(kayit.get("kaydedildi") or 0)
        satirlar.append({
            "kaynak": ad,
            "etiket": kaynak_kaydi.gorunen_ad(ad),
            "renk": kaynak.renk if kaynak is not None else "",
            "kaydedildi": zaman,
            "yas": yas_metni(simdi - zaman),
            "cerez": len(kayit.get("cerezler") or []),
            "gecerlilik": gecerlilik_metni(kayit),
            "alanlar": list(kayit.get("alanlar") or []),
        })
    satirlar.sort(key=lambda s: -s["kaydedildi"])
    return satirlar


# ── Uçlar ────────────────────────────────────────────────────────────────────
class ErisimUclari:
    """``erisim_ac``, ``erisim_oturumlari``, ``erisim_temizle``, ``erisim_yeniden``.

    QObject DEĞİL, bilerek: pencereyle döngüde (pencere → köprü → uçlar →
    pencere) duruyor ve döngüyü Python'un toplayıcısı hangi thread'de
    tetiklenirse orada siliyor; ebeveynsiz bir QObject'i kendi thread'i
    dışında yıkmak süreci düşürüyordu (test paketinde "pure virtual method
    called"; bkz. `gui.web.sorular`). Qt nesneleri (pencere denetleyicileri)
    pencerenin çocuğu.

    ``tranime_cerez(netscape)``: TRAnimeİzle çerezini Ayarlar'ın yolundan
    kaydet (ayar dosyası, kaynağa uygulama, bağış teklifi) — "Tarayıcıdan
    Al" ile birebir aynı sonuç.
    """

    def __init__(self, kopru: Kopru, *, pencere=None,
                 tranime_cerez: Optional[Callable[[str], Any]] = None):
        self._kopru = kopru
        self._pencere = pencere
        self._tranime_cerez = tranime_cerez
        self._acik: Dict[str, Dict[str, Any]] = {}     # kaynak → {isci, istekler}
        self._bekleyen: "OrderedDict[str, Callable[[], Any]]" = OrderedDict()
        self._kilit = threading.Lock()
        self._sayac = itertools.count(1)

    # ── Pencere ─────────────────────────────────────────────────────────────
    @uc()
    def erisim_ac(self, kaynak: str, istek: Optional[int] = None) -> Dict[str, Any]:
        """Kaynağın erişim penceresini aç; sonuç ``erisim_sonuc`` olayıyla."""
        hedef = oturumlar.erisim_hedefi(kaynak)
        if hedef is None:
            raise UcHatasi(f"{kaynak_kaydi.gorunen_ad(str(kaynak))} için erişim "
                           "penceresi yok (site bot doğrulaması kullanmıyor)")
        istek = int(istek) if istek is not None else next(self._sayac)
        acik = self._acik.get(hedef.kaynak)
        if acik is not None:
            acik["istekler"].append(istek)
            acik["isci"].one_getir()
            return {"istek": istek, "kaynak": hedef.kaynak, "zaten_acik": True}
        self._acik[hedef.kaynak] = kayit = {"isci": None, "istekler": [istek]}
        # Netscape çerez akışı (TRAnimeİzle) her zaman gömülü; diğerlerinde
        # ayardan motoru seç. Gerçek tarayıcı motoru `start()` False dönerse
        # (ör. selenium/tarayıcı o an hazır değil) sessizce gömülüye düşüyoruz.
        motor = "gomulu" if hedef.cerez_akisi else self._erisim_motoru()
        kayit["isci"] = isci = self._isci_kur(hedef, motor=motor)
        if isci.start() is False and motor == "chrome":
            kayit["isci"] = isci = self._isci_kur(hedef, motor="gomulu")
            isci.start()
        return {"istek": istek, "kaynak": hedef.kaynak}

    @staticmethod
    def _erisim_motoru() -> str:
        """Ayardan seçilen erişim motoru ("chrome" / "gomulu").

        İçe aktarma tembel ve her şey geri sarılı: selenium kurulu olmayan
        normal pakette (ve test paketinde) hep "gomulu" dönüp davranışı
        bugünküyle aynı tutuyor.
        """
        try:
            from ...common import tarayici_oturum
            from ...cli.dosyalar import Dosyalar
            return tarayici_oturum.erisim_motoru(Dosyalar().ayarlar)
        except Exception:
            return "gomulu"

    def _isci_kur(self, hedef: oturumlar.ErisimHedefi, *, motor: str = "gomulu"):
        if hedef.cerez_akisi:
            from ..qt.cookie_browser import CookieBrowserWorker
            return CookieBrowserWorker(
                on_cookies=lambda netscape: self._tranime_geldi(hedef, netscape),
                on_error=lambda m: self._bitti(hedef, False, m),
                on_cancel=lambda: self._iptal(hedef),
                parent=self._pencere)
        if motor == "chrome":
            from ..qt.tarayici_penceresi import TarayiciIsci
            return TarayiciIsci(
                hedef,
                on_success=lambda sonuc: self._kaydet(hedef, sonuc),
                on_error=lambda m: self._bitti(hedef, False, m),
                on_cancel=lambda: self._iptal(hedef),
                parent=self._pencere)
        from ..qt.erisim_penceresi import ErisimIsci
        return ErisimIsci(
            hedef,
            on_success=lambda sonuc: self._kaydet(hedef, sonuc),
            on_error=lambda m: self._bitti(hedef, False, m),
            on_cancel=lambda: self._iptal(hedef),
            parent=self._pencere)

    def _tranime_geldi(self, hedef, netscape: str) -> None:
        try:
            if self._tranime_cerez is not None:
                self._tranime_cerez(netscape)
            else:
                from ...cli.dosyalar import Dosyalar
                from ..qt import prefs
                Dosyalar().set_ayar("tranime_cookie", netscape)
                prefs.kaynak_kimliklerini_uygula()
        except Exception as exc:
            self._bitti(hedef, False, f"{hedef.etiket} çerezi kaydedilemedi: {exc}")
            return
        oturumlar.dogrulama_gecildi(hedef.kaynak)
        self._bitti(hedef, True, f"{hedef.etiket} oturum çerezi alındı; yeniden deneniyor.")

    def _kaydet(self, hedef, sonuc: Dict[str, Any]) -> None:
        cerezler = list(sonuc.get("cerezler") or [])
        try:
            if cerezler:
                oturumlar.kaydet(hedef.kaynak, cerezler=cerezler,
                                 user_agent=str(sonuc.get("user_agent") or ""),
                                 alanlar=hedef.alanlar,
                                 basliklar=sonuc.get("basliklar") or {})
        except Exception as exc:
            self._bitti(hedef, False, f"{hedef.etiket} oturumu kaydedilemedi: {exc}")
            return
        oturumlar.dogrulama_gecildi(hedef.kaynak)
        self._kopru.yay("erisim_degisti", {"kaynak": hedef.kaynak})
        if cerezler:
            mesaj = f"{hedef.etiket} için erişim açıldı; yeniden deneniyor."
        else:
            # Tarayıcı doğrulamasız girdi: site şu an doğrulama istemiyor.
            mesaj = (f"{hedef.etiket} tarayıcıda doğrulama istemedi; "
                     "yeniden deneniyor.")
        self._bitti(hedef, True, mesaj)

    def _iptal(self, hedef) -> None:
        self._bitti(hedef, False, "Erişim penceresi kapatıldı; doğrulama tamamlanmadı.",
                    iptal=True)

    def _bitti(self, hedef, basarili: bool, mesaj: str, *, iptal: bool = False) -> None:
        kayit = self._acik.pop(hedef.kaynak, None)
        for istek in (kayit or {}).get("istekler") or []:
            self._kopru.yay("erisim_sonuc", {
                "istek": istek, "kaynak": hedef.kaynak, "etiket": hedef.etiket,
                "basarili": bool(basarili), "iptal": bool(iptal), "mesaj": mesaj})

    def acik_mi(self, kaynak: str) -> bool:
        hedef = oturumlar.erisim_hedefi(kaynak)
        return hedef is not None and hedef.kaynak in self._acik

    # ── Ayarlar ─────────────────────────────────────────────────────────────
    @uc()
    def erisim_oturumlari(self) -> List[Dict[str, Any]]:
        """Kayıtlı erişim oturumları (Ayarlar > Kaynak Oturumları)."""
        return oturum_satirlari()

    @uc()
    def erisim_temizle(self, kaynak: str) -> List[Dict[str, Any]]:
        """Oturumu sil; gömülü tarayıcının o kaynağa ait profili de sıfırlanır.

        Profil de gitmeli: içinde bizim artık bilmediğimiz geçerli bir çerez
        kalırsa bir sonraki "Erişimi aç" doğrulama görmez, çerezi de yakalayamaz.
        """
        oturumlar.sil(kaynak)
        hedef = oturumlar.erisim_hedefi(kaynak)
        if hedef is not None and not hedef.cerez_akisi and hedef.kaynak not in self._acik:
            try:
                from ..qt.erisim_penceresi import profili_sifirla
                profili_sifirla(hedef)
            except Exception as exc:
                print(f"[Erişim] {hedef.etiket} profili temizlenemedi: {exc}")
            # Gerçek-tarayıcı motorunun ayrı profilini de sil (aynı gerekçe).
            try:
                from ...common import tarayici_oturum
                tarayici_oturum.profili_sil(hedef)
            except Exception as exc:
                print(f"[Erişim] {hedef.etiket} tarayıcı profili temizlenemedi: {exc}")
        self._kopru.yay("erisim_degisti", {"kaynak": kaynak_kaydi.kanonik_ad(str(kaynak))})
        return oturum_satirlari()

    # ── Engellenen işler ────────────────────────────────────────────────────
    def engel_bildir(self, hata: Any, kaynak: str, *, baslik: str = "",
                     yeniden: Optional[Callable[[], Any]] = None) -> bool:
        """Hata bot doğrulamasıysa sayfaya "Erişimi aç" teklifini gönder.

        Her thread'den çağrılabilir. ``yeniden`` (GUI thread'inde koşar)
        erişim açıldıktan sonra sayfanın ``erisim_yeniden`` çağrısıyla
        çalıştırılır. Doğrulama değilse hiçbir şey yapmaz, False döner.
        """
        try:
            if not kaynak or not oturumlar.erisim_engeli_mi(hata, kaynak):
                return False
        except Exception:                # bildirim asla çağıranı düşürmesin
            return False
        ad = kaynak_kaydi.kanonik_ad(str(kaynak))
        anahtar = ""
        if yeniden is not None:
            with self._kilit:
                anahtar = f"e{next(self._sayac)}"
                self._bekleyen[anahtar] = yeniden
                while len(self._bekleyen) > AZAMI_BEKLEYEN:
                    self._bekleyen.popitem(last=False)
        etiket = kaynak_kaydi.gorunen_ad(ad)
        from ...common.hatalar import insanlastir
        kisa = insanlastir(hata)[0] if isinstance(hata, BaseException) else str(hata)
        self._kopru.yay("erisim_gerekli", {
            "kaynak": ad, "etiket": etiket, "anahtar": anahtar,
            "mesaj": f"{baslik} — {kisa}" if baslik else kisa})
        return True

    @uc()
    def erisim_yeniden(self, anahtar: str) -> bool:
        """Erişim açıldı: bekleyen işi (oynatma) yeniden başlat."""
        with self._kilit:
            yeniden = self._bekleyen.pop(str(anahtar), None)
        if yeniden is None:
            raise UcHatasi("Yeniden denenecek iş bulunamadı (süresi geçmiş olabilir).")
        yeniden()
        return True


__all__ = ["ErisimUclari", "yas_metni", "gecerlilik_metni", "oturum_satirlari",
           "AZAMI_BEKLEYEN"]
