"""Yedekli oynatma: mpv bir adayı oynatamazsa sıradakine geç.

CLI ile Qt arayüzünün ORTAK oynatma döngüsü. Qt'nin `_play_blocking`'i eskiden
`best_video`'yu bir kez çağırıyor, mpv nasıl kapanırsa kapansın bölümü
"izlendi" yazıp ilerleme diyaloğunu açıyordu: mpv 2 ile çıksa bile (dosya
oynatılamadı) kullanıcıya "oynatma bitti" deniyordu. "Oynat"a yeniden basmak
da işe yaramıyordu; `AdapterBolum.best_video` videoları her çağrıda yeniden
kurduğu için yt-dlp yoklamasını geçen İLK aday, yani aynı bozuk akış yine
seçiliyordu. CLI bu döngüyü doğru kuruyordu (başarısız adres `atla` ile geri
veriliyor); Qt'de ikinci bir kopya yazmak yerine döngü buraya taşındı.

Modül Qt'siz ve yt-dlp'siz: CLI `gui.qt`'yi import edemez (PySide6 gerekmesin),
testler de sahte bölüm/video ile koşabilsin.

mpv çıkış kodları (mpv kılavuzu, "EXIT CODES"):

* 0 — normal çıkış ya da dosya sonu; kullanıcının `q` ile kapatması da 0.
* 1 — başlatma/seçenek hatası. Seçenekler her adayda aynı: yeniden denemek
  aynı hatayı yeniden üretir.
* 2 — dosya oynatılamadı (ağ hatası, 403, bozuk akış) → SIRADAKİ ADAY.
* 3 — bazı dosyalar oynatılamadı (çalma listesi) → sıradaki aday.
* 4 — sinyal ya da video penceresinde Ctrl+C: kullanıcı kesti. Yeniden
  denemek kullanıcının kapattığı pencerenin yerine yenisini açmak olurdu.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional

# Aday bütçesi CLI'daki eski döngüyle aynı. Her deneme yt-dlp yoklaması
# demek; daha fazlası kullanıcıyı dakikalarca bekletebilir.
VARSAYILAN_DENEME = 3

# Başka adaya geçmeyi haklı çıkaran çıkış kodları (bkz. modül belgesi).
YENIDEN_DENENECEK_KODLAR = frozenset({2, 3})

# Kodu bilinip yeniden denenmeyen başarısızlıkların kullanıcıya söylenişi.
_KOD_SEBEBI = {
    1: "oynatıcı başlatılamadı (mpv çıkış kodu 1: seçenek/başlatma hatası).",
    4: "oynatma kesildi (mpv çıkış kodu 4); izlendi işaretlenmedi.",
}

# `prefs.oynat`/`AdapterVideo.oynat` None döndü: mpv hiç bulunamadı ya da
# çalıştırılamadı. Bu adaya değil oynatıcıya ait bir sorun, sıradaki aday da
# aynı sonucu verir; bu yüzden yeniden denenmez.
OYNATICI_YOK = "oynatıcı başlatılamadı (mpv kurulu mu?)."


@dataclass
class OynatmaSonucu:
    """`yedekli_oynat`'ın özeti; çağıran geçmişi YALNIZCA `basarili` ise yazar."""

    basarili: bool
    sebep: str = ""
    video: Any = None
    returncode: Optional[int] = None
    # Oynatılamayıp atlanan adresler, deneme sırasıyla.
    denenen: List[str] = field(default_factory=list)


def _oynatici_adi(video: Any) -> str:
    return str(getattr(video, "player", "") or "?")


class _AdayGunlugu:
    """`best_video` callback'lerinden hangi oynatıcıların çalışmadığını topla.

    Hiç aday çalışmadığında "çalışan video bulunamadı" tek başına yetersiz:
    kullanıcı kaç adayın denendiğini, hangi oynatıcıların düştüğünü görmeli.
    """

    def __init__(self, ilet: Optional[Callable[[Dict[str, Any]], None]] = None):
        self._ilet = ilet
        self.calismayan: List[str] = []
        self.yoklanan = 0

    def __call__(self, hook: Dict[str, Any]) -> None:
        if isinstance(hook, dict) and hook.get("status") == "çalışmıyor":
            self.yoklanan += 1
            ad = str(hook.get("player") or "").strip()
            if ad and ad not in self.calismayan:
                self.calismayan.append(ad)
        if self._ilet is not None:
            self._ilet(hook)

    def ozet(self) -> str:
        if not self.calismayan:
            return ""
        return (f"{self.yoklanan} aday denendi: "
                f"{', '.join(self.calismayan)} çalışmıyor")


def yedekli_oynat(
    bul: Callable[[FrozenSet[str], Callable[[Dict[str, Any]], None]], Any],
    oynat: Callable[[Any], Any],
    *,
    deneme: int = VARSAYILAN_DENEME,
    bildir: Callable[[str], None] = lambda _m: None,
    callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> OynatmaSonucu:
    """En iyi adayı oynat; mpv "oynatılamadı" derse sıradaki adaya geç.

    ``bul(atla, callback)``: bölümün `best_video`'sunu çağırır. `atla`
    oynatılamayan adresleri taşır (bkz. `AdapterBolum.best_video`), callback
    aday durumlarını hem buraya hem ``callback``'e iletir. `bul` fonksiyonu
    çağırana bırakıldı: CLI her denemede kendi ilerleme çubuğunu açıyor.

    ``oynat(video)``: mpv sürecini (ya da None) döndürür. `returncode`'u
    olmayan dönüş (test sahteleri, eski `objects.Video`) başarı sayılır.

    ``bildir``: ara durum metinleri ("2. aday deneniyor (MAIL)…").

    `bul`un hatası YÜKSELİR: arşiv okunamadıysa sebebi çağıran söyler,
    "çalışan video yok" demek yanlış olurdu.
    """
    gunluk = _AdayGunlugu(callback)
    denenen: List[str] = []
    son_kod: Optional[int] = None
    son_video: Any = None
    oynatilamayan: List[str] = []
    for sira in range(1, max(1, int(deneme)) + 1):
        video = bul(frozenset(denenen), gunluk)
        if video is None:
            if oynatilamayan:
                # Önceki adaylar mpv'de düştü, yenisi de yok.
                return OynatmaSonucu(
                    False, _hepsi_dustu(oynatilamayan), son_video, son_kod, denenen)
            ozet = gunluk.ozet()
            sebep = "çalışan video bulunamadı" + (f" — {ozet}." if ozet else ".")
            return OynatmaSonucu(False, sebep, None, None, denenen)
        son_video = video
        if sira > 1:
            bildir(f"{sira}. aday deneniyor ({_oynatici_adi(video)})…")
        proc = oynat(video)
        if proc is None:
            return OynatmaSonucu(False, OYNATICI_YOK, video, None, denenen)
        kod = getattr(proc, "returncode", None)
        son_kod = kod
        if kod is None or kod == 0:
            return OynatmaSonucu(True, "", video, kod, denenen)
        try:
            video.is_working = False
        except AttributeError:
            pass
        if kod not in YENIDEN_DENENECEK_KODLAR:
            sebep = _KOD_SEBEBI.get(
                kod, f"oynatılamadı (mpv çıkış kodu {kod}).")
            return OynatmaSonucu(False, sebep, video, kod, denenen)
        adres = getattr(video, "url", None)
        if adres:
            denenen.append(str(adres))
        oynatilamayan.append(_oynatici_adi(video))
        bildir(f"{_oynatici_adi(video)} oynatılamadı (mpv çıkış kodu {kod}), "
               "başka bir video denenecek…")
    return OynatmaSonucu(False, _hepsi_dustu(oynatilamayan), son_video,
                         son_kod, denenen)


def _hepsi_dustu(oynatilamayan: List[str]) -> str:
    return (f"{len(oynatilamayan)} aday denendi, hiçbiri oynatılamadı "
            f"({', '.join(oynatilamayan)}).")


__all__ = ["OynatmaSonucu", "yedekli_oynat", "VARSAYILAN_DENEME",
           "YENIDEN_DENENECEK_KODLAR", "OYNATICI_YOK"]
