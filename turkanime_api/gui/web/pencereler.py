"""Qt diyaloglarından taşınan web pencerelerinin Python tarafı.

Her pencere bir `sorular.Soru` (istek/yanıt düzeni orada anlatılıyor); sayfa
tarafı ``statik/js/pencereler.js``. Buradakiler eski diyalogların davranışını
birebir taşıyor, yalnızca "diyalog açık kaldı" yerine "soru açık kaldı":

* `ilerleme_sor`        — oynatma sonrası "kaçıncı bölümü tamamladınız?"
                          (eski ``ProgressDialog``).
* `GuncellemePenceresi` — yeni sürüm, değişiklikler, indirme ilerlemesi
                          (eski ``UpdateDialog``).
* `GereksinimPenceresi` — eksik araçlar, kurulum, "Atla" (eski
                          ``RequirementsDialog``).
* `kapanis_sor`         — süren indirme varken kapanış (eski
                          ``QMessageBox.question``).

Fansub sorusu `gui.qt.fansub.FansubSecici.sor`'da, bağış onayı
`gui.web.katki.onay_al`'da: kuralları kendi modüllerinde duruyor.

Servis sinyalleri (indirme ilerlemesi) arka plan thread'inden geliyor;
pencere denetleyicileri QObject, yani bağlantı kuyruklu ve işleyiciler GUI
thread'inde koşuyor — eski diyaloglarla aynı güvence.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QObject

from .kopru import UcHatasi
from .sorular import Soru, SoruMerkezi

# ── İzleme ilerlemesi ───────────────────────────────────────────────────────
# Qt'deki sayı kutusunun aralığı; sayfadaki girdi de aynı sınırda.
ILERLEME_EN_AZ, ILERLEME_EN_COK = 1, 9999

KIMLIKSIZ_NOTU = "Seri kimliği bulunamadı, ilerleme kaydedilemiyor."


def ilerleme_bilgisi(bolum: Any, title: str = "") -> Dict[str, Any]:
    """Pencerenin gösterdiği her şey — ağa çıkmadan.

    Numara başlıktan çıkarılır; kullanıcı yine de düzeltebilir (kaynaklar
    bölümü sık sık yanlış adlandırıyor). Anime adı da veriliyor: başlık adı
    içerdiği için ("86 2nd Season 5. Bölüm") addaki rakam bölüm sanılıyor ve
    AniList'e 86 yazılıyordu.
    """
    from ...common.episode_parser import extract_episode_info
    from ..qt import prefs

    seri, bolum_slug = prefs.bolum_kimligi(bolum)
    bolum_adi = title or bolum_slug
    # Okunabilir seri adı: AniList araması slug ("naruto-test") yerine bununla
    # yapılır, aksi hâlde eşleşme çok daha zayıf olur.
    ad = prefs.anime_adi(bolum, seri)
    _, no = extract_episode_info(bolum_adi, ad)
    return {"seri": seri, "anime_adi": ad, "bolum_adi": bolum_adi,
            "bolum_no": max(ILERLEME_EN_AZ, min(ILERLEME_EN_COK, int(no or 1)))}


def _bolum_no(cevap: Any) -> int:
    """Sayfanın ``{"no": N}`` cevabı → doğrulanmış tam sayı."""
    ham = cevap.get("no") if isinstance(cevap, dict) else None
    if isinstance(ham, bool) or not isinstance(ham, (int, float)) or int(ham) != ham:
        raise UcHatasi("Bölüm numarası bir tam sayı olmalı.")
    no = int(ham)
    if not ILERLEME_EN_AZ <= no <= ILERLEME_EN_COK:
        raise UcHatasi(f"Bölüm numarası {ILERLEME_EN_AZ}-{ILERLEME_EN_COK} arasında olmalı.")
    return no


def ilerleme_sor(sorular: SoruMerkezi, bolum: Any, title: str,
                 kaydedildi: Callable[[str, int, str], Any]) -> Soru:
    """İlerleme penceresi; "Kaydet" yerel ilerlemeyi yazar, sonra ``kaydedildi``.

    Yerel yazım `dogrula`da: yazılamazsa pencere AÇIK kalır ve "İlerleme
    kaydedilemedi." gösterilir (Qt diyaloğu da `accept` etmeden kalıyordu).
    ``kaydedildi(seri, no, anime_adi)`` AniList yazımına bağlanıyor; okunabilir
    ad oradan AniList aramasına gidiyor.
    """
    from ..qt import prefs

    bilgi = ilerleme_bilgisi(bolum, title)
    seri = bilgi["seri"]

    def dogrula(cevap: Any) -> None:
        if cevap is None:                    # "Atla" / Esc
            return
        no = _bolum_no(cevap)
        if not seri:
            # Kaydediyormuş gibi yapıp sessizce kaybetmektense söylüyoruz.
            raise UcHatasi(KIMLIKSIZ_NOTU)
        if not prefs.ilerleme_kaydet(seri, no):
            raise UcHatasi("İlerleme kaydedilemedi.")

    def geri(cevap: Any) -> None:
        if cevap is not None:
            kaydedildi(seri, _bolum_no(cevap), bilgi["anime_adi"])

    return sorular.sor("ilerleme", {
        "anime_adi": bilgi["anime_adi"], "bolum_adi": bilgi["bolum_adi"],
        "bolum_no": bilgi["bolum_no"], "en_az": ILERLEME_EN_AZ,
        "en_cok": ILERLEME_EN_COK, "kaydedilebilir": bool(seri),
        "not": "" if seri else KIMLIKSIZ_NOTU,
    }, geri, dogrula=dogrula)


# ── Pencere denetleyicisi tabanı ────────────────────────────────────────────
class _ServisPenceresi(QObject):
    """Servis sinyallerini soruya taşıyan pencere; kapanınca bağları koparır."""

    def __init__(self, kapandi: Optional[Callable[[], Any]] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._kapandi = kapandi
        self._baglar: List[Any] = []
        self.soru: Optional[Soru] = None

    def _bagla(self, sinyal: Any, fn: Callable[..., Any]) -> None:
        sinyal.connect(fn)
        self._baglar.append((sinyal, fn))

    def _guncelle(self, **veri: Any) -> None:
        if self.soru is not None:
            self.soru.guncelle(**veri)

    def _son(self) -> None:
        # Diyalog `deleteLater` ile bağlarını kendiliğinden bırakıyordu; soru
        # bir widget değil, bağlar elle kopuyor: kapanmış pencere yeni
        # indirmenin ilerlemesini dinlemeye devam etmesin.
        for sinyal, fn in self._baglar:
            try:
                sinyal.disconnect(fn)
            except (RuntimeError, TypeError):
                pass
        self._baglar.clear()
        if self._kapandi is not None:
            self._kapandi()

    def kapat(self) -> None:
        """Pencereyi Python tarafından kapat ("Daha Sonra" gibi)."""
        if self.soru is not None:
            self.soru.bitir(None)


# ── Güncelleme ──────────────────────────────────────────────────────────────
class GuncellemePenceresi(_ServisPenceresi):
    """Sürüm bilgisi, değişiklikler, indirme ilerlemesi ve "Daha Sonra".

    Pencere hiçbir zaman ağ görmez, servis hiçbir zaman pencereye dokunmaz
    (bkz. `gui.qt.updates`): indirme ``indir`` eylemiyle servise gidiyor,
    ilerleme servis sinyallerinden ``soru.guncelle``'ye.

    Sayfadaki ``durum``: ``hazir`` → ``iniyor`` → ``tamam`` | ``hata``.
    """

    def __init__(self, sorular: SoruMerkezi, servis: Any,
                 version_data: Optional[Dict[str, Any]],
                 kapandi: Optional[Callable[[], Any]] = None,
                 parent: Optional[QObject] = None):
        super().__init__(kapandi, parent)
        self.servis = servis
        self.version_data = version_data or {}
        self.indirilen = ""
        self._bagla(servis.progress, self._on_progress)
        self._bagla(servis.download_ready, self._on_ready)
        self._bagla(servis.download_failed, self._on_failed)
        tarih = str(self.version_data.get("release_date") or "")[:10]
        self.soru = sorular.sor("guncelleme", {
            "mevcut": str(servis.mevcut_surum),
            "yeni": str(self.version_data.get("version", "?")),
            "tarih": tarih,
            "degisiklikler": str(self.version_data.get("changelog")
                                 or "Değişiklik bilgisi yok."),
            "durum": "hazir", "yuzde": 0, "metin": "", "yol": "",
        }, lambda _cevap: self._son(), eylem=self._eylem)

    def _eylem(self, ad: str, _veri: Dict[str, Any]) -> bool:
        if ad == "indir":
            # `indir` False dönerse ya iş zaten sürüyor ya da paket yok; ikinci
            # durumda `download_failed` ZATEN yayıldı ve pencere hatayı gösterdi.
            if not self.servis.indir(self.version_data):
                return False
            self._guncelle(durum="iniyor", yuzde=0, metin="Güncelleme indiriliyor…")
            return True
        if ad == "klasor":
            if self.servis.konumu_ac(self.indirilen):
                return True
            self._guncelle(metin=f"Klasör açılamadı. Dosya: {self.indirilen}")
            return False
        raise UcHatasi(f"bilinmeyen eylem: {ad}")

    def _on_progress(self, yuzde: int, ayrinti: str) -> None:
        self._guncelle(yuzde=max(0, min(100, int(yuzde))), metin=f"İndiriliyor… {ayrinti}")

    def _on_ready(self, yol: str) -> None:
        """İndirme + SHA-256 doğrulaması geçti; kurulum talimatını göster."""
        from ...common import updater
        self.indirilen = yol
        self._guncelle(durum="tamam", yuzde=100, yol=yol,
                       metin="İndirildi ve doğrulandı.\n\n" + updater.kurulum_talimati(yol))

    def _on_failed(self, mesaj: str) -> None:
        self._guncelle(durum="hata", metin=f"Güncelleme indirilemedi: {mesaj}")


# ── Gereksinim sihirbazı ────────────────────────────────────────────────────
class GereksinimPenceresi(_ServisPenceresi):
    """Eksik araçları listeler, indirip kurar, "Atla"yı hatırlar.

    "Atla" pencerenin CEVABI (``{"atla": true}``): tercih yazılır ve pencere
    kapanır. Esc ve kurulum sonrası "Kapat" tercihe dokunmaz — Qt'de
    "Kapat" etiketi aynı "Atla" işleyicisine bağlı kalmıştı ve bütün araçları
    kurmuş kullanıcıya habersizce "bir daha sorma" yazıyordu; bir araç sonradan
    silinirse açılış denetimi onu hiç söylemezdi.

    Sayfadaki ``durum``: ``hazir`` → ``kuruluyor`` → ``tamam`` | ``hata``.
    """

    def __init__(self, sorular: SoruMerkezi, servis: Any, eksikler: Sequence[str],
                 kapandi: Optional[Callable[[], Any]] = None,
                 parent: Optional[QObject] = None):
        super().__init__(kapandi, parent)
        self.servis = servis
        self.eksikler = list(eksikler)
        self.atlandi = False
        self._bagla(servis.progress, self._on_progress)
        self._bagla(servis.install_done, self._on_done)
        self.soru = sorular.sor("gereksinim", {
            "eksikler": self.eksikler, "durum": "hazir", "yuzde": 0, "metin": "",
        }, self._bitti, eylem=self._eylem)

    def _eylem(self, ad: str, _veri: Dict[str, Any]) -> bool:
        if ad != "kur":
            raise UcHatasi(f"bilinmeyen eylem: {ad}")
        if not self.servis.kur(self.eksikler):
            return False
        self._guncelle(durum="kuruluyor", yuzde=0, metin="Gereksinimler indiriliyor…")
        return True

    def _bitti(self, cevap: Any) -> None:
        if isinstance(cevap, dict) and cevap.get("atla") is True:
            # Bir daha sorma: Ayarlar'daki "Gereksinimleri Denetle" geri alır.
            self.atlandi = bool(self.servis.atlandi_yaz(True))
        self._son()

    def _on_progress(self, yuzde: int, ayrinti: str) -> None:
        self._guncelle(yuzde=max(0, min(100, int(yuzde))), metin=ayrinti)

    def _on_done(self, sonuclar: Any) -> None:
        basarisiz = [(ad, hata) for ad, ok, hata in (sonuclar or []) if not ok]
        if not basarisiz:
            self._guncelle(durum="tamam", yuzde=100, metin="Tüm gereksinimler kuruldu.")
            return
        self._guncelle(durum="hata", metin="Kurulamayanlar:\n" + "\n".join(
            f"• {ad}: {hata}" for ad, hata in basarisiz))


# ── Kapanış ─────────────────────────────────────────────────────────────────
KAPANIS_BASLIK = "İndirmeler sürüyor"


def kapanis_metni(adet: int) -> str:
    """Kapanış sorusunun gövdesi (eski QMessageBox metniyle aynı)."""
    return (f"{adet} indirme sürüyor. Duraklatılıp çıkılsın mı?\n\n"
            "Kuyruk kaydedilir; uygulamayı yeniden açınca İndirilenler'den "
            "“Devam et” ile kaldığı yerden sürdürebilirsiniz.")


def kapanis_sor(sorular: SoruMerkezi, adet: int, geri: Callable[[bool], Any]) -> Soru:
    """"Duraklatılıp çıkılsın mı?" — ``geri(True)`` ise pencere kapanır.

    Varsayılan "Evet" (QMessageBox'ta da varsayılan düğme Evet'ti) ve teslim
    mühleti SIFIR: sayfa bağlı değilse (yüklenmedi, render süreci öldü)
    soru hiç beklemeden "Evet" sayılır. Aksi hâlde bozuk bir sayfa pencereyi
    kapatılamaz hâle getirirdi; duraklatıp kaydetmek güvenli taraf, kuyruk
    kaybolmuyor. Esc ve "Hayır" açıkça ``false`` gönderir.
    """
    return sorular.sor("kapanis", {"baslik": KAPANIS_BASLIK, "metin": kapanis_metni(adet),
                                   "adet": int(adet)},
                       lambda cevap: geri(cevap is True),
                       varsayilan=True, teslim_muhleti=0)


__all__ = ["ilerleme_bilgisi", "ilerleme_sor", "GuncellemePenceresi",
           "GereksinimPenceresi", "kapanis_sor", "kapanis_metni", "KAPANIS_BASLIK",
           "KIMLIKSIZ_NOTU", "ILERLEME_EN_AZ", "ILERLEME_EN_COK"]
