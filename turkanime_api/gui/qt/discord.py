"""Discord Rich Presence — tamamen opsiyonel.

`pypresence` bir bağımlılık olarak zorunlu değil (pyproject'te yok, kullanıcı
istersen kurar). Bu yüzden import gövdenin en başında korumalı: paket yoksa
`KULLANILABILIR` False olur ve servis her çağrıda sessizce hiçbir şey yapmaz.
Discord kapalıyken de aynı: `connect()` patlar, uygulama etkilenmez.

Zamanlayıcılar ve hız sınırı GUI thread'inde (`QTimer`), RPC çağrıları ise
TEK bir adanmış iş parçacığında (`_RpcIsci`). RPC nesnesi kendi asyncio
döngüsünü taşıyor; ona iki farklı thread'den dokunmak kırılgandı (eski GUI'nin
`after` + `Thread` karışımı) — bu yüzden nesneyi yaratan, bağlayan,
güncelleyen ve kapatan hep AYNI iş parçacığı.

NEDEN GUI thread'inde değil: pypresence her çağrıda Discord'un IPC cevabını
bekliyor (bağlanma el sıkışması 30 sn'ye, `update` 10 sn'ye kadar). Discord
açılırken, güncellenirken ya da meşgulken bu bekleme GUI thread'inde olunca
pencere o süre DONUYORDU — sayfa geçişleri ve 15 sn'lik tazeleme de buna
dahil. Sonuçlar iş parçacığından Qt sinyalleriyle (kuyruklu) geri geliyor.
"""
from __future__ import annotations

import queue
import threading
import time
import weakref
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from . import prefs

try:                                  # opsiyonel bağımlılık
    from pypresence import Presence
except Exception:                     # ImportError ve paketin kendi hataları
    Presence = None                   # type: ignore[assignment]

KULLANILABILIR = Presence is not None

# Eski GUI ile aynı uygulama kimliği: kullanıcının Discord profilinde aynı
# uygulama adı ve görseli görünsün.
UYGULAMA_ID = "1115609536552771595"
BUYUK_GORSEL = "Turkanime"
INDIRME_BAGLANTISI = "https://github.com/barkeser2002/turkanime-gui/releases"

# Discord aynı istemciden ~15 saniyede bir güncelleme kabul ediyor; daha sık
# göndermek sessizce düşürülür.
GUNCELLEME_ARALIGI = 15000
YENIDEN_BAGLANMA_ARALIGI = 30000

SAYFA_DURUMU: Dict[str, str] = {
    "home": "Ana sayfada",
    "search": "Anime arıyor",
    "season": "Bu sezona bakıyor",
    "trending": "Trend animelere bakıyor",
    "watchlist": "İzleme listesine bakıyor",
    "downloads": "İndirilenlere bakıyor",
    "settings": "Ayarlarda",
    "detail": "Anime detaylarını inceliyor",
    "episodes": "Bölüm listesine bakıyor",
}

VARSAYILAN_DURUM = "TürkAnime GUI"


def kullanilabilir() -> bool:
    """`pypresence` kurulu mu? (testler bu bayrağı sahteleyebilsin diye ayrı)"""
    return KULLANILABILIR


class _RpcIsci:
    """`Presence` nesnesinin TEK sahibi: bütün pypresence çağrıları burada.

    Komutlar sırayla işlenir (``baglan``, ``guncelle``, ``kapat``); her biri
    servisin o anki nesline (``nesil``) bağlı. Sonuçlar ``bildir(olay,
    nesil, ayrinti)`` ile döner — servis onu bir Qt sinyaline bağlıyor, yani
    GUI thread'ine kuyruklu ulaşır.
    """

    def __init__(self, bildir):
        self._bildir = bildir
        self._kuyruk: "queue.Queue[tuple]" = queue.Queue()
        self._rpc: Any = None
        self._is = threading.Thread(target=self._dongu, daemon=True,
                                    name="discord-rpc")
        self._is.start()

    def gonder(self, komut: str, nesil: int, veri: Any = None) -> None:
        self._kuyruk.put((komut, nesil, veri))

    def _dongu(self) -> None:
        while True:
            komut, nesil, veri = self._kuyruk.get()
            if komut == "son":               # servis toplandı: kapat ve çık
                self._kapat()
                return
            try:
                if komut == "baglan":
                    self._baglan(nesil)
                elif komut == "guncelle":
                    self._guncelle(nesil, veri)
                elif komut == "kapat":
                    self._kapat()
            except Exception as exc:    # iş parçacığı asla ölmemeli
                print(f"[Discord] beklenmeyen hata ({komut}): {exc}")

    def _baglan(self, nesil: int) -> None:
        self._kapat()
        try:
            rpc = Presence(UYGULAMA_ID)
            rpc.connect()
        except Exception as exc:      # Discord kapalı, eski sürüm, izin yok…
            print(f"[Discord] Bağlanılamadı: {exc}")
            self._bildir("baglanamadi", nesil, str(exc))
            return
        self._rpc = rpc
        self._bildir("baglandi", nesil, None)

    def _guncelle(self, nesil: int, veri: Dict[str, Any]) -> None:
        if self._rpc is None:
            return
        try:
            self._rpc.update(**veri)
        except Exception as exc:
            print(f"[Discord] Güncellenemedi: {exc}")
            self._rpc = None
            self._bildir("koptu", nesil, str(exc))

    def _kapat(self) -> None:
        rpc, self._rpc = self._rpc, None
        if rpc is None:
            return
        try:
            rpc.clear()
            rpc.close()
        except Exception as exc:
            print(f"[Discord] Kapatma hatası: {exc}")


class _Aktarici(QObject):
    """İş parçacığının sonuçlarını GUI thread'ine taşıyan ÇOCUK nesne.

    Servisin çocuğu: C++ nesnesi hep ebeveyniyle, GUI thread'inde yıkılıyor.
    İş parçacığı yalnızca bunun `emit`ini tutuyor; servis gittiyse yayım
    `RuntimeError` verir ve yutulur. İş parçacığı bir QObject'in SON
    referansını hiç tutmuyor (o nesneyi kendi thread'inde yıkmak süreci
    düşürüyordu; bkz. `gui.web.sorular`).
    """

    sonuc = Signal(str, int, object)

    def yay(self, olay: str, nesil: int, ayrinti: Any) -> None:
        try:
            self.sonuc.emit(olay, nesil, ayrinti)
        except RuntimeError:
            pass                      # servis (ve bu çocuk) kapanışta silindi


class DiscordService(QObject):
    """Rich Presence bağlantısı, periyodik güncelleme ve yeniden bağlanma."""

    state_changed = Signal(bool)      # bağlı mı

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._isci: Optional[_RpcIsci] = None
        self._nesil = 0
        self._baglaniyor = False
        self._bagli = False
        # Gösterilmek İSTENEN durum; gerçek gönderim hız sınırına takılabilir.
        self._istek: Dict[str, Any] = {"details": SAYFA_DURUMU["home"],
                                       "state": VARSAYILAN_DURUM}
        self._son_gonderim = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(GUNCELLEME_ARALIGI)
        self._timer.timeout.connect(self._dongu)

        # Tek atışlık: bağlantı koptuğunda bir kez daha dener, başarısızsa
        # kendini yeniden kurar. Aralıklı timer olsaydı Discord kapalı olan
        # kullanıcıda sonsuza dek her 30 sn'de bir denemeye devam ederdi.
        self._yeniden = QTimer(self)
        self._yeniden.setSingleShot(True)
        self._yeniden.setInterval(YENIDEN_BAGLANMA_ARALIGI)
        self._yeniden.timeout.connect(self._baglan)
        self._aktarici = _Aktarici(self)
        self._aktarici.sonuc.connect(self._sonuc_geldi)

    # ── Durum ───────────────────────────────────────────────────────────────
    @property
    def bagli(self) -> bool:
        return self._bagli

    @property
    def baglaniyor(self) -> bool:
        """El sıkışması sürüyor (Discord açılırken saniyeler sürebilir)."""
        return self._baglaniyor

    @staticmethod
    def etkin_mi() -> bool:
        """Özellik kullanılabilir VE kullanıcı ayarı açık mı?"""
        return kullanilabilir() and prefs.oku().discord

    def _rpc_isci(self) -> _RpcIsci:
        if self._isci is None:
            # Aktarıcı GUI thread'inde yaşıyor; iş parçacığından yayılan sinyal
            # kuyruklu gelir, yuva (`_sonuc_geldi`) hep GUI thread'inde koşar.
            self._isci = _RpcIsci(self._aktarici.yay)
            # Servis toplanınca iş parçacığı bağlantıyı kapatıp çıksın (her
            # örnek için kuyrukta bekleyen bir thread birikmesin). Geri çağrı
            # servise referans TUTMUYOR (yalnızca işçinin `gonder`i).
            weakref.finalize(self, self._isci.gonder, "son", 0)
        return self._isci

    # ── Bağlantı ────────────────────────────────────────────────────────────
    def baslat(self) -> bool:
        """Açılışta çağrılır; ayar kapalıysa ya da paket yoksa hiçbir şey yapmaz.

        True: bağlanma BAŞLATILDI (sonuç `state_changed` ile gelir). GUI
        thread'i Discord'un el sıkışmasını beklemez.
        """
        if not self.etkin_mi():
            return False
        return self._baglan()

    def _baglan(self) -> bool:
        if self._bagli or self._baglaniyor or not self.etkin_mi():
            return False
        self._baglaniyor = True
        self._rpc_isci().gonder("baglan", self._nesil)
        return True

    def durdur(self) -> None:
        """Bağlantıyı kapat ve zamanlayıcıları durdur (kapanış / ayar kapatma).

        Yeni nesil: o an süren bir el sıkışmasının sonucu artık yok sayılır
        (bağlanırsa iş parçacığı sıradaki ``kapat``la onu hemen kapatır).
        """
        self._timer.stop()
        self._yeniden.stop()
        self._nesil += 1
        self._baglaniyor = False
        if self._isci is not None:
            self._isci.gonder("kapat", self._nesil)
        if self._bagli:
            self._bagli = False
            self.state_changed.emit(False)

    def ayar_uygula(self) -> None:
        """Ayar sayfasındaki anahtar değişti: anında bağlan ya da kop."""
        if self.etkin_mi():
            self._baglan()
        else:
            self.durdur()

    # ── Durum bildirimleri ──────────────────────────────────────────────────
    def sayfa(self, anahtar: str) -> None:
        """Sayfa geçişi."""
        self.durum_ayarla(SAYFA_DURUMU.get(anahtar, "Anime arıyor"),
                          VARSAYILAN_DURUM)

    def izliyor(self, anime: str, bolum: str = "") -> None:
        """Oynatma başladı — geçen süre sayacıyla."""
        self.durum_ayarla(f"{anime} izliyor", bolum or "Anime izliyor",
                          baslangic=time.time(), buyuk_metin=anime)

    def indiriyor(self, baslik: str, yuzde: Optional[int] = None) -> None:
        self.durum_ayarla(f"{baslik} indiriyor",
                          f"İlerleme: %{yuzde}" if yuzde is not None
                          else "Anime indiriyor")

    def durum_ayarla(self, details: str, state: str,
                     baslangic: Optional[float] = None,
                     buyuk_metin: str = "TürkAnime") -> None:
        """İstenen durumu kaydet ve hız sınırı izin veriyorsa hemen gönder."""
        self._istek = {"details": details, "state": state,
                       "large_text": buyuk_metin}
        if baslangic is not None:
            self._istek["start"] = int(baslangic)
        self._gonder()

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def _dongu(self) -> None:
        """Periyodik tazeleme: Discord uzun süre güncellenmeyen durumu düşürür."""
        if self._bagli:
            self._gonder(zorla=True)

    def _gonder(self, zorla: bool = False) -> None:
        if not self._bagli or self._isci is None:
            return
        simdi = time.monotonic()
        if not zorla and (simdi - self._son_gonderim) * 1000 < GUNCELLEME_ARALIGI:
            return                    # sınır aşılırsa gönderim sessizce düşer
        veri = dict(self._istek)
        veri.setdefault("large_image", BUYUK_GORSEL)
        veri["buttons"] = [{"label": "Uygulamayı Edin",
                            "url": INDIRME_BAGLANTISI}]
        self._isci.gonder("guncelle", self._nesil, veri)
        self._son_gonderim = simdi

    def _sonuc_geldi(self, olay: str, nesil: int, _ayrinti: Any) -> None:
        """İş parçacığının sonucu (GUI thread'inde). Eski neslinki yok sayılır:
        arada ``durdur`` çağrıldıysa o bağlantı zaten kapatılıyor."""
        if nesil != self._nesil:
            return
        if olay == "baglandi":
            self._baglaniyor = False
            if not self.etkin_mi():          # bu arada ayar kapandı
                self.durdur()
                return
            self._bagli = True
            self.state_changed.emit(True)
            self._timer.start()
            self._gonder(zorla=True)
        elif olay == "baglanamadi":
            self._baglaniyor = False
            # Discord kapalıysa sonsuza dek denemiyoruz (bkz. `_yeniden`);
            # durum yine de bildiriliyor ki ayar sayfası "bağlanıyor"da kalmasın.
            self.state_changed.emit(False)
        elif olay == "koptu":
            self._koptu()

    def _koptu(self) -> None:
        """Bağlantı düştü: durumu sıfırla ve bir kez yeniden denemeyi planla."""
        self._timer.stop()
        self._baglaniyor = False
        if self._bagli:
            self._bagli = False
            self.state_changed.emit(False)
        if self.etkin_mi() and not self._yeniden.isActive():
            self._yeniden.start()


__all__ = ["DiscordService", "KULLANILABILIR", "kullanilabilir",
           "SAYFA_DURUMU", "UYGULAMA_ID"]
