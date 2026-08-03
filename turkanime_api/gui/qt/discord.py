"""Discord Rich Presence — tamamen opsiyonel.

`pypresence` bir bağımlılık olarak zorunlu değil (pyproject'te yok, kullanıcı
istersen kurar). Bu yüzden import gövdenin en başında korumalı: paket yoksa
`KULLANILABILIR` False olur ve servis her çağrıda sessizce hiçbir şey yapmaz.
Discord kapalıyken de aynı: `connect()` patlar, uygulama etkilenmez.

Zamanlayıcılar `QTimer` — eski GUI `self.after(...)` + `threading.Thread`
karışımı kullanıyordu; RPC nesnesi kendi asyncio döngüsünü taşıdığı için ona
iki farklı thread'den dokunmak kırılgandı. Burada tüm RPC çağrıları GUI
thread'inde.
"""
from __future__ import annotations

import time
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


class DiscordService(QObject):
    """Rich Presence bağlantısı, periyodik güncelleme ve yeniden bağlanma."""

    state_changed = Signal(bool)      # bağlı mı

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._rpc: Any = None
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

    # ── Durum ───────────────────────────────────────────────────────────────
    @property
    def bagli(self) -> bool:
        return self._bagli

    @staticmethod
    def etkin_mi() -> bool:
        """Özellik kullanılabilir VE kullanıcı ayarı açık mı?"""
        return kullanilabilir() and prefs.oku().discord

    # ── Bağlantı ────────────────────────────────────────────────────────────
    def baslat(self) -> bool:
        """Açılışta çağrılır; ayar kapalıysa ya da paket yoksa hiçbir şey yapmaz."""
        if not self.etkin_mi():
            return False
        return self._baglan()

    def _baglan(self) -> bool:
        if self._bagli or not self.etkin_mi():
            return False
        try:
            self._rpc = Presence(UYGULAMA_ID)
            self._rpc.connect()
        except Exception as exc:      # Discord kapalı, eski sürüm, izin yok…
            print(f"[Discord] Bağlanılamadı: {exc}")
            self._rpc = None
            self._bagli = False
            return False
        self._bagli = True
        self.state_changed.emit(True)
        self._timer.start()
        self._gonder(zorla=True)
        return True

    def durdur(self) -> None:
        """Bağlantıyı kapat ve zamanlayıcıları durdur (kapanış / ayar kapatma)."""
        self._timer.stop()
        self._yeniden.stop()
        rpc, self._rpc = self._rpc, None
        if rpc is not None:
            try:
                rpc.clear()
                rpc.close()
            except Exception as exc:
                print(f"[Discord] Kapatma hatası: {exc}")
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
        if not self._bagli or self._rpc is None:
            return
        simdi = time.monotonic()
        if not zorla and (simdi - self._son_gonderim) * 1000 < GUNCELLEME_ARALIGI:
            return                    # sınır aşılırsa gönderim sessizce düşer
        veri = dict(self._istek)
        veri.setdefault("large_image", BUYUK_GORSEL)
        veri["buttons"] = [{"label": "Uygulamayı Edin",
                            "url": INDIRME_BAGLANTISI}]
        try:
            self._rpc.update(**veri)
        except Exception as exc:
            print(f"[Discord] Güncellenemedi: {exc}")
            self._koptu()
            return
        self._son_gonderim = simdi

    def _koptu(self) -> None:
        """Bağlantı düştü: durumu sıfırla ve bir kez yeniden denemeyi planla."""
        self._rpc = None
        self._timer.stop()
        if self._bagli:
            self._bagli = False
            self.state_changed.emit(False)
        if self.etkin_mi() and not self._yeniden.isActive():
            self._yeniden.start()


__all__ = ["DiscordService", "KULLANILABILIR", "kullanilabilir",
           "SAYFA_DURUMU", "UYGULAMA_ID"]
