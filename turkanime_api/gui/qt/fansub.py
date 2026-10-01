"""Fansub seçimi — ayarlardaki "Fansub'u kendim seçeyim" arayüzde de çalışsın.

NEDEN VAR: Ayar kaydediliyordu ama yalnızca CLI soruyordu; arayüz her bölümde
`best_video`'nun oynatıcı önceliğiyle seçtiği akışı açıyordu. Arşivde her üç
bölümden biri birden çok fansub'la duruyor (tam taramada 71 bin bölümün
%31,7'si), yani bir seri içinde çeviri grubu bölümden bölüme değişebiliyordu.

Akış (hepsi GUI thread'inde, ağ işi arka planda):

1. `FansubSecici.iste(entry, devam)`: seri için hatırlanan seçim varsa
   ``devam(True, fansub)`` hemen çağrılır, soru yok.
2. Yoksa bölümün fansub listesi (`AdapterBolum.fansubs` — akışları getirir,
   yani ağ) ARKA PLANDA okunur. Aynı seri için gelen diğer istekler (toplu
   indirmede 12 bölüm) bekleyen listesine girer; liste bir kez getirilir,
   soru bir kez sorulur ve cevap hepsine uygulanır.
3. Birden çok fansub varsa kullanıcıya oynatıcı/kalite özetiyle sorulur;
   "Otomatik" de bir seçenek. Tek fansub (ya da fansub kavramı olmayan
   kaynak) sorulmaz. İptal: ``devam(False, None)``.

Soru web arayüzünde bir pencere (tür ``fansub``, ``statik/js/pencereler.js``;
soru-cevap düzeni `gui.web.sorular`). Cevap SONRADAN geldiği için seri
anahtarı soru açıkken de "bekleniyor" sayılıyor: bu arada gelen istekler
(toplu indirmenin kalan bölümleri) ikinci bir soru açmıyor, aynı cevabı alıyor.

`fansubs`'un getirdiği akışlar `AdapterBolum`'da bekletiliyor ve ilk
`best_video` onları tüketiyor: soru ağ turunu iki katına çıkarmıyor.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject

from . import prefs
from .workers import UiBridge, run_bg

# "Otomatik" seçimi: `best_video` fansub süzmeden, oynatıcı önceliğiyle seçer.
OTOMATIK = ""
OTOMATIK_ETIKETI = "Otomatik — en iyi çalışan video"

Devam = Callable[[bool, Optional[str]], Any]
Cevap = Optional[Tuple[str, bool]]


def fansub_ozeti(bolum: Any) -> Dict[str, List[str]]:
    """Fansub → ["SIBNET 1080p", "MAIL 720p", …] (akış sırasıyla, tekrarsız).

    `AdapterBolum.fansubs` akışları `_bekleyen_akislar`'da bırakıyor (ilk
    `best_video` onları kullanıyor); özet oradan okunur, ikinci istek yok.
    Kaynak modülüne dokunulmadığı için özel öznitelik okunuyor; yoksa (eski
    nesne, sahte) özet boş kalır ve pencere yalnızca adları gösterir.
    """
    ozet: Dict[str, List[str]] = {}
    for akis in getattr(bolum, "_bekleyen_akislar", None) or []:
        if not isinstance(akis, dict):
            continue
        ad = str(akis.get("fansub") or "").strip()
        if not ad:
            continue
        parca = " ".join(str(x) for x in (akis.get("player"), akis.get("label")) if x)
        satir = ozet.setdefault(ad, [])
        if parca and parca not in satir:
            satir.append(parca)
    return ozet


def fansub_secenekleri(fansubs: List[str],
                       ozet: Optional[Dict[str, List[str]]] = None) -> List[Dict[str, str]]:
    """Penceredeki liste: önce "Otomatik", sonra "Ad — SIBNET 1080p, …".

    Özet en fazla dört oynatıcı/kalite gösteriyor (eski diyalogla aynı).
    """
    secenekler = [{"deger": OTOMATIK, "etiket": OTOMATIK_ETIKETI}]
    for ad in fansubs:
        ayrinti = ", ".join((ozet or {}).get(ad, [])[:4])
        secenekler.append({"deger": ad, "etiket": f"{ad} — {ayrinti}" if ayrinti else ad})
    return secenekler


class FansubSecici(QObject):
    """Seri başına fansub tercihi + tek seferlik soru (bkz. modül belgesi)."""

    def __init__(self, parent: Optional[QObject] = None, sorular: Any = None):
        super().__init__(parent)
        # Soru merkezi (`gui.web.sorular.SoruMerkezi`): pencere sayfada açılıyor.
        self.sorular = sorular
        self.ui = UiBridge(self)
        # Seri anahtarı → seçilen fansub (OTOMATIK dahil). Oturum boyunca:
        # kalıcı saklamak, kaynaktaki grup adı değişince eski tercihi
        # sessizce "otomatik"e düşürürdü; her açılışta bir soru ucuz.
        self.tercih: Dict[str, str] = {}
        self._bekleyen: Dict[str, List[Devam]] = {}

    @staticmethod
    def anahtar(entry: Dict[str, Any]) -> str:
        """Seri kimliği: kaynak + kaynağın anime kimliği (yoksa seri slug'ı)."""
        kimlik = str(entry.get("kimlik") or "")
        if not kimlik:
            kimlik = prefs.bolum_kimligi(entry.get("obj"))[0]
        return f"{entry.get('kaynak') or ''}:{kimlik}"

    def iste(self, entry: Dict[str, Any], devam: Devam) -> None:
        """Bu bölüm için fansub'u çöz, sonra ``devam(tamam, fansub)`` (GUI thread'i)."""
        anahtar = self.anahtar(entry)
        if anahtar in self.tercih:
            devam(True, self.tercih[anahtar] or None)
            return
        bekleyen = self._bekleyen.setdefault(anahtar, [])
        bekleyen.append(devam)
        if len(bekleyen) == 1:
            baslik = str(entry.get("seri_adi") or entry.get("title") or "Bölüm")
            run_bg(self._getir, anahtar, entry.get("obj"), baslik)

    def _getir(self, anahtar: str, bolum: Any, baslik: str) -> None:
        """Arka plan: fansub listesini (ağ) oku, cevabı GUI thread'ine yolla."""
        fansubs: List[str] = []
        ozet: Dict[str, List[str]] = {}
        try:
            fansubs = [str(f) for f in (getattr(bolum, "fansubs", None) or [])]
            ozet = fansub_ozeti(bolum)
        except Exception as exc:          # liste yüzünden oynatma düşmesin
            print(f"[Fansub] liste okunamadı: {exc}")
        finally:
            try:
                self.ui.post(lambda: self._cevapla(anahtar, baslik, fansubs, ozet))
            except RuntimeError:          # pencere kapandı
                pass

    def _cevapla(self, anahtar: str, baslik: str, fansubs: List[str],
                 ozet: Dict[str, List[str]]) -> None:
        if len(fansubs) <= 1:
            # Soru yok ve HATIRLANMAZ: serinin sonraki bölümünde birden çok
            # grup olabilir.
            for devam in self._bekleyen.pop(anahtar, []):
                devam(True, None)
            return

        def secildi(cevap: Cevap) -> None:
            # Liste ŞİMDİ alınıyor: soru açıkken gelen istekler de içinde.
            bekleyenler = self._bekleyen.pop(anahtar, [])
            if cevap is None:
                for devam in bekleyenler:
                    devam(False, None)
                return
            secim, hatirla = cevap
            if hatirla:
                self.tercih[anahtar] = secim
            for devam in bekleyenler:
                devam(True, secim or None)

        self.sor(baslik, fansubs, ozet, secildi)

    def sor(self, baslik: str, fansubs: List[str], ozet: Dict[str, List[str]],
            geri: Callable[[Cevap], Any]) -> None:
        """Pencereyi aç; ``geri((fansub ya da OTOMATIK, hatırla))``, iptalde
        ``geri(None)`` — tam bir kez.

        Sayfa listede olmayan bir değer gönderirse pencere açık kalır: yanlış
        bir ad `best_video`'da hiçbir akışla eşleşmez ve oynatma "video yok"la
        biterdi. Testler bu metodu sahteler (pencereyi sayfasız sınamak için).
        """
        from ..web.kopru import UcHatasi

        if self.sorular is None:            # sayfasız kurulum: soru sorulamaz
            geri(None)
            return
        secenekler = fansub_secenekleri(fansubs, ozet)
        gecerli = {s["deger"] for s in secenekler}

        def dogrula(cevap: Any) -> None:
            if cevap is None:
                return
            if not isinstance(cevap, dict) or cevap.get("secim") not in gecerli:
                raise UcHatasi("Listede olmayan bir seçim yapıldı.")

        def bitti(cevap: Any) -> None:
            if not isinstance(cevap, dict) or cevap.get("secim") not in gecerli:
                geri(None)                   # Vazgeç / Esc / sayfa yok / kapanış
                return
            geri((str(cevap["secim"]), cevap.get("hatirla") is True))

        self.sorular.sor("fansub", {
            "baslik": baslik,
            "secenekler": secenekler,
            # İlk gerçek fansub seçili gelir (eski diyalog da 1. satırı seçiyordu).
            "secili": fansubs[0] if fansubs else OTOMATIK,
            "hatirla": True,
        }, bitti, dogrula=dogrula)


__all__ = ["FansubSecici", "fansub_ozeti", "fansub_secenekleri", "OTOMATIK",
           "OTOMATIK_ETIKETI"]
