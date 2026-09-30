"""Python'dan sayfaya soru — Qt diyaloglarının yerini alan web pencereleri.

NEDEN BÖYLE: Qt diyalogları ``dialog.exec()`` ile iç içe bir olay döngüsü
açıp cevabı eşzamanlı döndürüyordu; çağıran (oynatma, indirme, açılış
denetimi, kapanış) cevabı bir sonraki satırda okuyordu. Pencere artık
QtWebEngine'deki sayfanın içinde bir modal: cevap QWebChannel'dan, GUI
thread'inde ve SONRADAN geliyor. GUI thread'ini beklemeye almak (ya da iç içe
`QEventLoop`) köprünün kendisini kilitlerdi; bu yüzden çağıranlar geri
çağrıya çevrildi ve soru-cevap tek bir yerde, burada toplanıyor.

Akış (hepsi GUI thread'inde):

1. Python ``merkez.sor(tur, veri, geri)`` der ve bir `Soru` alır. Soru
   bekleyenler listesine girer; sayfa bağlıysa ``soru`` olayıyla
   (``{"id", "tur", "veri"}``) hemen gönderilir.
2. Sayfa ``TA.soruTurleri[tur]`` ile pencereyi çizer (``statik/js/sorular.js``,
   ``pencereler.js``). Kullanıcının cevabı ``soru_cevapla(kimlik, cevap)`` ucuyla
   döner ve ``geri(cevap)`` TAM OLARAK BİR KEZ çağrılır.
3. Pencere içi eylemler (güncellemeyi indir, araçları kur, klasörü aç) soruyu
   kapatmaz: ``soru_eylem(kimlik, ad, veri)`` → sorunun ``eylem`` işleyicisi.
   Python açık pencereyi ``soru.guncelle(...)`` ile tazeler (``soru_guncelle``
   olayı; değişiklik ``veri``ye de işlenir, yeniden çizimde kaybolmaz).
4. ``dogrula`` işleyicisi `UcHatasi` fırlatırsa soru AÇIK kalır ve mesaj
   pencerede gösterilir ("İlerleme kaydedilemedi."): Qt diyaloğunun
   `accept()` etmeden kalması gibi.

Kenar durumlar:

* **Sayfa henüz yüklenmedi / yeniden yüklendi.** Bağlanmadan yayılan olay
  kaybolur; bu yüzden sayfa köprüye bağlanınca ``bekleyen_sorular`` ile
  açık soruların hepsini ÇEKİYOR (kimliğe göre tekilleşiyor, iki kez açılmaz).
  Sayfa ilk çekişe kadar "bağlı değil" sayılır.
* **Teslim mühleti.** Sayfa ``teslim_muhleti`` (ms) içinde bağlanmazsa soru
  ``varsayilan`` ile biter; ``0`` "bağlı değilse hiç bekleme" demek (kapanış
  sorusu: bozuk sayfa pencereyi kapatılamaz hâle getirmesin). Cevap için ayrı
  bir mühlet YOK, bilerek: soru kullanıcının kararı, Qt diyalogları da
  beklerken zaman aşımına uğramıyordu.
* **Esc / dış tık.** Sayfa türün "vazgeç" cevabını yollar (çoğunda ``null``,
  kapanışta ``false``) — Qt'deki `reject()`/Esc karşılığı.
* **Render süreci öldü.** `sayfa_gitti` bekleyen her soruyu ``varsayilan``
  ile bitirir: sayfasız bir soruyu kimse cevaplayamaz, ``_playing`` gibi
  bayraklar sonsuza dek açık kalırdı.
* **Uygulama kapanışı.** `hepsini_bitir` aynısını yapar (``closeEvent``).

``varsayilan`` "cevap alınamadı" demek ve güvenli taraf olmalı: fansub'da
iptal, bağış onayında onay YOK, güncellemede "daha sonra".

Uçlar (``bekleyen_sorular``, ``soru_cevapla``, ``soru_eylem``) GUI
thread'inde koşuyor (``arka`` değil): işleyiciler Qt nesnelerine dokunuyor.
"""
from __future__ import annotations

import itertools
import traceback
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, QTimer

from .kopru import UcHatasi, uc

# Sayfa bu kadar sürede köprüye bağlanmazsa soru varsayılan cevapla biter.
# Açılışta sayfa 1-2 sn'de bağlanıyor; yavaş makinede bile 30 sn bol.
TESLIM_MUHLETI = 30_000

Geri = Callable[[Any], Any]


class Soru:
    """Sayfadaki tek bir pencere. `SoruMerkezi.sor` üretir."""

    def __init__(self, merkez: "SoruMerkezi", kimlik: str, tur: str,
                 veri: Dict[str, Any], geri: Optional[Geri],
                 dogrula: Optional[Geri],
                 eylem: Optional[Callable[[str, Dict[str, Any]], Any]],
                 varsayilan: Any):
        self._merkez = merkez
        self.kimlik = kimlik
        self.tur = tur
        self.veri = dict(veri)
        self.varsayilan = varsayilan
        self._geri = geri
        self._dogrula = dogrula
        self._eylem = eylem
        self._bitti = False
        self.teslim = False             # sayfaya ulaştı mı (olay ya da çekiş)
        self._zamanlayici: Optional[QTimer] = None

    @property
    def acik(self) -> bool:
        return not self._bitti

    def paket(self) -> Dict[str, Any]:
        """Sayfaya giden biçim."""
        return {"id": self.kimlik, "tur": self.tur, "veri": self.veri}

    def guncelle(self, **veri: Any) -> None:
        """Açık pencereyi tazele; değişiklik yeniden çizimde de geçerli."""
        if self._bitti:
            return
        self.veri.update(veri)
        self._merkez._yay("soru_guncelle", {"id": self.kimlik, "veri": veri})

    def bitir(self, cevap: Any = None, *, varsayilanla: bool = False) -> None:
        """Soruyu Python tarafından kapat (pencere de kapanır).

        ``varsayilanla=True`` → ``geri``ye sorunun varsayılan cevabı gider.
        """
        self._son(self.varsayilan if varsayilanla else cevap, bildir=True)

    # ── İç ──────────────────────────────────────────────────────────────────
    def _son(self, cevap: Any, *, bildir: bool) -> None:
        """Tek bitiş yolu: `geri` en fazla bir kez çağrılır."""
        if self._bitti:
            return
        self._bitti = True
        if self._zamanlayici is not None:
            self._zamanlayici.stop()
            self._zamanlayici.deleteLater()
            self._zamanlayici = None
        self._merkez._birak(self)
        if bildir:
            self._merkez._yay("soru_kapat", {"id": self.kimlik})
        geri = self._geri
        # İşleyiciler bırakılıyor: çoğu, soruyu tutan denetleyiciye (QObject)
        # geri dönen bir kapanış. Döngü kalırsa nesneyi Python'un döngü
        # toplayıcısı siliyor — o da HANGİ thread'de tetiklenirse orada;
        # QObject'i kendi thread'i dışında yıkmak süreci düşürüyordu (test
        # paketinde ölçüldü: sonraki pencere kurulurken segfault).
        self._geri = self._dogrula = self._eylem = None
        if geri is not None:
            try:
                geri(cevap)
            except Exception:           # çağıranın hatası pencereyi kilitlemesin
                traceback.print_exc()


class SoruMerkezi(QObject):
    """Açık soruları tutar, sayfaya taşır, cevapları çağırana döndürür.

    GUI thread'inde yaşar ve `sor` yalnızca oradan çağrılır (zamanlayıcı ve
    işleyiciler Qt nesnelerine dokunuyor). Köprüye ``kopru.bagla(merkez)``
    ile bağlanır.
    """

    def __init__(self, kopru: Any, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._kopru = kopru
        self._sorular: Dict[str, Soru] = {}
        self._sayac = itertools.count(1)
        self.bagli = False             # sayfa `bekleyen_sorular`ı çekti mi

    # ── Python tarafı ───────────────────────────────────────────────────────
    def sor(self, tur: str, veri: Optional[Dict[str, Any]] = None,
            geri: Optional[Geri] = None, *, dogrula: Optional[Geri] = None,
            eylem: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
            varsayilan: Any = None,
            teslim_muhleti: Optional[int] = TESLIM_MUHLETI) -> Soru:
        """Sayfada pencere aç; cevap gelince ``geri(cevap)``.

        DİKKAT: sayfa bağlı değilse ve ``teslim_muhleti == 0`` ise ``geri``
        daha bu çağrı dönmeden (varsayılanla) çağrılır. ``None`` mühlet:
        sayfa bağlanana kadar süresiz bekle.
        """
        if QThread.currentThread() is not self.thread():
            raise RuntimeError("soru yalnızca GUI thread'inden sorulabilir")
        soru = Soru(self, f"s{next(self._sayac)}", tur, veri or {}, geri,
                    dogrula, eylem, varsayilan)
        self._sorular[soru.kimlik] = soru
        if self.bagli:
            soru.teslim = True
            self._yay("soru", soru.paket())
        elif teslim_muhleti is not None:
            if teslim_muhleti <= 0:
                soru._son(varsayilan, bildir=False)
            else:
                zamanlayici = QTimer(self)
                zamanlayici.setSingleShot(True)
                zamanlayici.timeout.connect(lambda s=soru: self._teslim_edilemedi(s))
                zamanlayici.start(int(teslim_muhleti))
                soru._zamanlayici = zamanlayici
        return soru

    def bekleyenler(self, tur: Optional[str] = None) -> List[Soru]:
        """Açık sorular (sorulma sırasıyla), istenirse yalnızca bir türden."""
        return [s for s in self._sorular.values() if tur is None or s.tur == tur]

    def sayfa_gitti(self, *_sebep: Any) -> None:
        """Render süreci öldü: sayfasız soruyu kimse cevaplayamaz.

        ``renderProcessTerminated(durum, kod)``'a doğrudan bağlanıyor.
        """
        self.bagli = False
        self.hepsini_bitir()

    def hepsini_bitir(self) -> None:
        """Açık her soruyu varsayılan cevapla bitir (kapanış)."""
        for soru in list(self._sorular.values()):
            soru._son(soru.varsayilan, bildir=True)

    # ── Sayfa tarafı (uçlar) ────────────────────────────────────────────────
    @uc()
    def bekleyen_sorular(self) -> List[Dict[str, Any]]:
        """Sayfa köprüye bağlandı: açık soruların hepsi (kaçan olaylar dahil)."""
        self.bagli = True
        paketler = []
        for soru in list(self._sorular.values()):
            soru.teslim = True
            if soru._zamanlayici is not None:
                soru._zamanlayici.stop()
            paketler.append(soru.paket())
        return paketler

    @uc()
    def soru_cevapla(self, kimlik: str, cevap: Any = None,
                     varsayilanla: bool = False) -> bool:
        """Pencerenin cevabı. Soru artık yoksa (mühlet doldu, Python kapattı)
        ``False``: sayfa pencereyi yine kapatır, hata göstermez.

        ``varsayilanla``: sayfa pencereyi ÇİZEMEDİ (bilinmeyen tür, betik
        hatası) — soru sorulamamış sayılır ve varsayılanla biter. Aksi hâlde
        bozuk bir çizici kapanış sorusunu ("Hayır" sayılıp) sonsuz döngüye
        sokardı.
        """
        if varsayilanla:
            soru = self._sorular.get(str(kimlik))
            if soru is None:
                return False
            soru._son(soru.varsayilan, bildir=False)
            return True
        return self.cevapla(kimlik, cevap)

    @uc()
    def soru_eylem(self, kimlik: str, ad: str,
                   veri: Optional[Dict[str, Any]] = None) -> Any:
        """Pencere içi eylem; soru açık kalır."""
        soru = self._sorular.get(str(kimlik))
        if soru is None:
            raise UcHatasi("Bu pencere artık geçerli değil.")
        if soru._eylem is None:
            raise UcHatasi(f"bilinmeyen eylem: {ad}")
        return soru._eylem(str(ad), dict(veri or {}))

    def cevapla(self, kimlik: str, cevap: Any) -> bool:
        """`soru_cevapla`'nın gövdesi (testler de sayfasız buradan cevaplar)."""
        soru = self._sorular.get(str(kimlik))
        if soru is None:
            return False
        if soru._dogrula is not None:
            soru._dogrula(cevap)        # UcHatasi → soru açık, mesaj pencerede
        soru._son(cevap, bildir=False)  # pencereyi sayfa kendisi kapatıyor
        return True

    # ── İç ──────────────────────────────────────────────────────────────────
    def _teslim_edilemedi(self, soru: Soru) -> None:
        if soru.acik and not soru.teslim:
            print(f"[Soru] sayfa bağlanmadı, {soru.tur!r} varsayılanla kapandı")
            soru._son(soru.varsayilan, bildir=False)

    def _birak(self, soru: Soru) -> None:
        self._sorular.pop(soru.kimlik, None)

    def _yay(self, ad: str, veri: Any) -> None:
        self._kopru.yay(ad, veri)


__all__ = ["Soru", "SoruMerkezi", "TESLIM_MUHLETI"]
