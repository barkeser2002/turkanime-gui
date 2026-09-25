"""Ayarlar sayfasının köprü uçları.

Ayarlar `cli.dosyalar.Dosyalar` üzerinden okunup yazılıyor (CLI ile ortak
`ayarlar.json`). Kurallar eski Qt sayfasıyla aynı; en önemlileri:

* **Kaynak kimlikleri**: TRAnimeİzle çerezi ve OpenAnime jetonları diskte
  durmakla iş bitmiyor, kaynak modülleri onları süreç-içi global'de tutuyor.
  Açılışta (bu sınıf kurulurken) ve her kayıt/temizlemede
  `prefs.kaynak_kimliklerini_uygula()` çağrılıyor; çağrılmazsa TRAnimeİzle
  her açılışta 0 bölüm döndürür.
* **Kimlik bağışı** iki kapılı: "kimlik paylas" ayarı açık VE onay penceresi
  onaylanmış. Çerez ÖNCE diske yazılır, bağış SONRA sorulur. Bağış numarası
  geri çekmenin tek anahtarı; liste olarak saklanır, silinemeyen numara
  ayarda kalır.
* **Çevrimdışı arşiv**: durum sayfa AÇILINCA arka planda okunur (kurulumda
  değil: megabaytlık dizin.json). İndirme/silme/klasör denetimi arka planda;
  aynı anda tek arşiv işi. Silme yalnızca `<veri kökü>/cevrimdisi_arsiv`.

Olaylar: ``ayar_durum`` (sayfanın üst satırı), ``ayar_cerez``,
``ayar_anilist``, ``arsiv_ilerleme``, ``arsiv_sonuc``.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject

from .kopru import Kopru, UcHatasi, uc

# Etkin arşiv konumunun (`animedepo.ArsivKonumu.kaynak`) kullanıcıya görünen adı.
ARSIV_KONUM_ADLARI = {
    "ortam": "Ortam değişkeni (TURKANIME_ARSIV_DIZIN)",
    "ayar": "Ayarlarda seçilen klasör",
    "indirilen": "İndirilmiş tam arşiv",
    "depo": "Depodaki arşiv klasörü (arsiv/)",
    "uzak": "Uzak ayna (internet gerekir)",
}
# Ölçüldü: `arsiv/`'in tar.gz'si 230,8 MB (açılınca ~0,5 GB).
TAM_ARSIV_BOYUTU_MB = 230
# Paket 64 KB'lık parçalarla akıyor (~3600 çağrı); saniyede ~10 güncelleme yeter.
ILERLEME_ARALIGI = 0.1

SIZAN_SECRET_UYARISI = (
    "Eski sürümlerden kalan, herkese açık depoya sızmış Client Secret "
    "yapılandırmanızdan silindi. Giriş artık secret gerektirmeyen Implicit "
    "akışla yapılıyor; bir şey yapmanıza gerek yok.")

# Sayfa alanı → `ayarlar.json` anahtarı. Eski Türkçe adlar `Dosyalar`
# açılışında ASCII'ye göç ediyor (bkz. `dosyalar.ESKI_AYAR_ADLARI`).
ALANLAR = {
    "indirilenler": "indirilenler",
    "paralel": "paralel indirme sayisi",
    "aday": "1080p aday sayisi",
    "max_res": "max resolution",
    "dakika_hatirla": "dakika hatirla",
    "izlerken_kaydet": "izlerken kaydet",
    "ilerlemeyi_sor": "ilerlemeyi sor",
    "aria2c": "aria2c kullan",
    "izlendi_ikonu": "izlendi ikonu",
    "manuel_fansub": "manuel fansub",
    "flaresolverr": "flaresolverr_url",
    "openani_token": "openani_token",
    "openani_refresh": "openani_refresh_token",
    "kimlik_paylas": "kimlik paylas",
    "sunucu_adresi": "sunucu adresi",
    "sunucu_anahtari": "sunucu api anahtari",
}
_METIN = ("indirilenler", "flaresolverr", "openani_token", "openani_refresh",
          "sunucu_adresi", "sunucu_anahtari")
_MANTIKSAL = ("max_res", "dakika_hatirla", "izlerken_kaydet", "ilerlemeyi_sor",
              "aria2c", "izlendi_ikonu", "manuel_fansub", "kimlik_paylas")
_SAYI = {"paralel": (1, 10), "aday": (1, 30)}


# ── Qt'siz yardımcılar ───────────────────────────────────────────────────────
def mb(bayt: int) -> str:
    """Bayt → "45,2" (ondalık MB)."""
    return f"{bayt / 1_000_000:.1f}".replace(".", ",")


def tarih_metni(zaman: Optional[int]) -> str:
    """dizin.json `last_update` (unix) → "14.09.2026"; geçersizse boş."""
    if zaman is None:
        return ""
    try:
        return datetime.fromtimestamp(int(zaman)).strftime("%d.%m.%Y")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def bagis_kimlikleri(ayarlar: Dict[str, Any]) -> List[str]:
    """Ayardaki bağış numaraları LİSTE olarak (eski kurulumda düz dizgi).

    Liste: çerezin süresi dolunca kullanıcı yeniden bağışlıyor; tek dizgede
    ikinci bağış birincinin numarasını siliyordu ve o numara geri çekmenin
    TEK anahtarı.
    """
    ham = ayarlar.get("kimlik bagis id") or []
    if isinstance(ham, str):
        ham = [ham] if ham.strip() else []
    return [str(x).strip() for x in ham if str(x).strip()]


def bagis_metni(kimlikler: List[str]) -> str:
    if len(kimlikler) == 1:
        return f"Bağış yapıldı — numara: {kimlikler[0]}. İstediğiniz an geri çekebilirsiniz."
    if kimlikler:
        return (f"{len(kimlikler)} bağış kaydı var — numaralar: " + ", ".join(kimlikler)
                + ". “Bağışımı geri çek” hepsini birden siler.")
    return "Bağışlanmış oturum kimliği yok."


def cerez_durumu(netscape: str) -> Dict[str, Any]:
    var = bool(netscape and ".AitrWeb.Session" in netscape)
    return {"var": var, "metin": "Oturum çerezi kayıtlı" if var else (
        "Çerez yok — TRAnimeİzle bot kontrolü nedeniyle bölüm döndürmez. "
        "“Tarayıcıdan Al” ile kontrolü çözün.")}


def arsiv_icerik_metni(durum: Any) -> str:
    """"6.098 anime · son güncelleme 14.09.2026" biçiminde özet."""
    if durum.anime_sayisi is None:
        if durum.konum.yerel:
            return "dizin.json okunamadı."
        return "Henüz okunmadı — ilk TürkAnime aramasında aynadan gelecek."
    sayi = f"{durum.anime_sayisi:,}".replace(",", ".")
    tarih = tarih_metni(durum.son_guncelleme)
    metin = f"{sayi} anime · " + (f"son güncelleme {tarih}" if tarih
                                 else "güncelleme tarihi bilinmiyor")
    if durum.onbellekten:
        metin += " (disk önbelleğindeki kopya)"
    return metin


def arsiv_uyarilari(durum: Any) -> List[str]:
    """Kullanıcının bilmesi gereken sessiz geçişler (geçersiz klasör atlandı...)."""
    from ...sources import animedepo
    uyarilar: List[str] = []
    ortam_adi = animedepo.DIZIN_ORTAM_ANAHTARI
    if durum.ortam_dizini and durum.kaynak != "ortam":
        uyarilar.append(
            f"{ortam_adi} ({durum.ortam_dizini}) geçerli bir arşiv değil "
            "(dizin.json yok ya da okunamıyor); atlandı.")
    if durum.ayar_dizini and durum.kaynak not in ("ortam", "ayar"):
        uyarilar.append(
            f"Seçtiğiniz klasör ({durum.ayar_dizini}) geçerli bir arşiv değil "
            "(dizin.json yok ya da okunamıyor); atlandı. “Klasör seç” ile "
            "yenisini gösterin ya da “Varsayılana dön”e basın.")
    elif durum.ayar_dizini and durum.kaynak == "ortam":
        uyarilar.append(
            f"Seçtiğiniz klasör kayıtlı ama {ortam_adi} ortam değişkeni önce geliyor.")
    if durum.indirilen_var and durum.kaynak in ("ortam", "ayar"):
        uyarilar.append("İndirilmiş tam arşiv de var, ama gösterilen klasör önce geliyor.")
    if durum.kaynak == "uzak":
        uyarilar.append(
            "Yerel arşiv yok: TürkAnime araması ve bölüm listeleri internetten "
            "gelir. Çevrimdışı kullanmak için tüm arşivi indirin.")
    kalintilar = list(getattr(durum, "kalintilar", ()) or ())
    if kalintilar:
        uyarilar.append(
            "Eski arşivin silinemeyen kopyası var (uygulama kapalıyken elle "
            "silebilirsiniz): " + ", ".join(str(k) for k in kalintilar))
    return uyarilar


# ── Uçlar ────────────────────────────────────────────────────────────────────
class AyarlarUclari(QObject):
    """Ayarlar sayfasının Python tarafı (GUI thread'inde yaşar)."""

    def __init__(self, kopru: Kopru, *, anilist, discord=None, updates=None,
                 requirements=None, pencere=None):
        super().__init__()
        from ..qt.workers import UiBridge
        self._kopru = kopru
        self._ui = UiBridge(self)
        self.anilist = anilist
        self.discord = discord
        self.updates = updates
        self.requirements = requirements
        self._pencere = pencere
        self._cerez_isci = None
        self._arsiv_mesgul: Optional[str] = None        # None | "indirme" | "islem"
        self._arsiv_iptal: Optional[threading.Event] = None
        self._arsiv_kaynak_adi = ""
        self._arsiv_indirilen_var = False

        anilist.auth_changed.connect(
            lambda user: kopru.yay("ayar_anilist", self._anilist_durumu(user)))
        if updates is not None:
            updates.up_to_date.connect(lambda: self._durum("Uygulamanız güncel.", "tamam"))
            updates.check_failed.connect(lambda m: self._durum(m, "hata"))
        if requirements is not None:
            requirements.all_present.connect(
                lambda: self._durum("Tüm gereksinimler kurulu.", "tamam"))
        # Diskteki çerez/jetonlar süreç-içi kopyaya (açılış).
        if not self._kimlikleri_uygula():
            print("[Ayarlar] Kaynak çerez/jetonları uygulanamadı.")

    # ── Yardımcılar ─────────────────────────────────────────────────────────
    @staticmethod
    def _dosya():
        from ...cli.dosyalar import Dosyalar
        return Dosyalar()

    @staticmethod
    def _kimlikleri_uygula() -> bool:
        from ..qt import prefs
        return prefs.kaynak_kimliklerini_uygula()

    def _durum(self, mesaj: str, tur: str = "bilgi") -> None:
        """Sayfanın durum satırı (ve bildirim)."""
        self._kopru.yay("ayar_durum", {"mesaj": mesaj, "tur": tur})

    def _gui(self, fn) -> None:
        try:
            self._ui.post(fn)
        except RuntimeError:
            pass

    def _anilist_durumu(self, user: Any = None) -> Dict[str, Any]:
        user = user if user is not None else self.anilist.kullanici
        if isinstance(user, dict) and user.get("name"):
            return {"giris": True, "ad": str(user["name"]),
                    "metin": f"Giriş yapıldı: {user['name']}"}
        if self.anilist.giris_var_mi():
            return {"giris": True, "ad": "",
                    "metin": "Jeton kayıtlı, kullanıcı bilgisi bekleniyor…"}
        return {"giris": False, "ad": "",
                "metin": "Giriş yapılmamış — İzleme Listesi ve ilerleme senkronu için giriş yapın."}

    def _discord_metni(self) -> str:
        from ..qt.discord import kullanilabilir
        if not kullanilabilir():
            return "pypresence kurulu değil — özellik kapalı (pip install pypresence)."
        if self.discord is not None and self.discord.bagli:
            return "Discord'a bağlı"
        return "Discord açık değilse bağlantı kurulmaz; uygulama etkilenmez."

    # ── Okuma / kaydetme ────────────────────────────────────────────────────
    @uc()
    def ayarlar(self) -> Dict[str, Any]:
        from ..qt import prefs
        ayarlar: Dict[str, Any] = self._dosya().ayarlar or {}
        deger = {alan: ayarlar.get(anahtar) for alan, anahtar in ALANLAR.items()}
        for alan in _METIN:
            deger[alan] = str(deger[alan] or "")
        varsayilan = {"max_res": True, "dakika_hatirla": True, "izlendi_ikonu": True}
        for alan in _MANTIKSAL:
            ham = ayarlar.get(ALANLAR[alan], varsayilan.get(alan, False))
            deger[alan] = bool(ham)
        deger["paralel"] = int(ayarlar.get(ALANLAR["paralel"]) or 3)
        # Eski Türkçe ada düşme kuralı `prefs`te (iki yerde ayrışmasın).
        deger["aday"] = prefs.oku().aday_sayisi
        deger["discord"] = bool(ayarlar.get("discord_rich_presence", True))
        anilist = prefs.anilist_oku()
        return {
            "degerler": deger,
            "cerez": cerez_durumu(str(ayarlar.get("tranime_cookie") or "")),
            "bagis": self._bagis_durumu(bagis_kimlikleri(ayarlar)),
            "discord_metni": self._discord_metni(),
            "anilist": dict(self._anilist_durumu(),
                            client_id=anilist.client_id,
                            client_secret=anilist.client_secret,
                            redirect_uri=anilist.redirect_uri,
                            sizan_uyari=SIZAN_SECRET_UYARISI if anilist.sizan_secret_temizlendi else ""),
            "servisler": {"guncelleme": self.updates is not None,
                          "gereksinim": self.requirements is not None},
        }

    @staticmethod
    def _bagis_durumu(kimlikler: List[str]) -> Dict[str, Any]:
        return {"kimlikler": kimlikler, "metin": bagis_metni(kimlikler)}

    @uc()
    def ayarlari_kaydet(self, degerler: Dict[str, Any],
                        anilist: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Formu diske yaz; bypass oturumunu ve kaynak kimliklerini tazele."""
        yazilacak: Dict[str, Any] = {}
        for alan, anahtar in ALANLAR.items():
            if alan not in degerler:
                continue
            ham = degerler[alan]
            if alan in _METIN:
                yazilacak[anahtar] = str(ham or "").strip()
            elif alan in _MANTIKSAL:
                yazilacak[anahtar] = bool(ham)
            else:
                alt, ust = _SAYI[alan]
                try:
                    yazilacak[anahtar] = max(alt, min(ust, int(ham)))
                except (TypeError, ValueError):
                    raise UcHatasi(f"geçersiz sayı: {ham!r}") from None
        self._dosya().set_ayar(ayar_list=yazilacak)
        # Bypass oturumu adresi kurulumda okuyor; sıfırlanmazsa değişiklik
        # ancak yeniden başlatınca etkili olurdu.
        try:
            from ...common.cf_bypass import reset_cf_session
            reset_cf_session()
        except Exception:
            pass
        if anilist is not None:
            from ..qt import prefs
            if not prefs.anilist_yaz(anilist.get("client_id", ""),
                                     anilist.get("client_secret", ""),
                                     anilist.get("redirect_uri", "")):
                raise UcHatasi("Ayarlar kaydedildi ama AniList yapılandırması yazılamadı")
        if not self._kimlikleri_uygula():
            raise UcHatasi("Ayarlar kaydedildi ama kaynak çerez/jetonları uygulanamadı")
        return {"mesaj": "Ayarlar kaydedildi."}

    @uc()
    def klasor_sec(self, baslangic: str = "", baslik: str = "Klasör seç") -> str:
        """Sistem klasör seçicisi (iptal → boş)."""
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getExistingDirectory(self._pencere, baslik, baslangic or "") or ""

    # ── TRAnimeİzle çerezi ──────────────────────────────────────────────────
    @uc()
    def cerez_al(self) -> bool:
        from ..qt.cookie_browser import CookieBrowserWorker, is_available
        if not is_available():
            raise UcHatasi("QtWebEngine yok (PySide6-Addons kurulu mu?)")
        if self._cerez_isci is not None and self._cerez_isci.is_running:
            self._durum("Tarayıcı zaten açık.")
            return False
        self._durum("Tarayıcı açılıyor…")
        self._cerez_isci = CookieBrowserWorker(
            on_status=lambda m: self._durum(m),
            on_cookies=self._cerez_geldi,
            on_error=lambda m: self._durum(m, "hata"),
            parent=self._pencere)
        self._cerez_isci.start()
        return True

    def _cerez_geldi(self, netscape: str) -> None:
        try:
            self._dosya().set_ayar("tranime_cookie", netscape)
            self._kimlikleri_uygula()
        except Exception as exc:
            self._durum(f"Çerez kaydedilemedi: {exc}", "hata")
            return
        self._kopru.yay("ayar_cerez", cerez_durumu(netscape))
        self._durum("TRAnimeİzle çerezi alındı ve kaydedildi.", "tamam")
        # Çerez ZATEN kaydedildi; bağış bundan sonra ve tamamen ayrı bir karar.
        self.kimlik_bagisi_teklif(netscape)

    @uc()
    def cerez_temizle(self) -> Dict[str, Any]:
        self._dosya().set_ayar("tranime_cookie", "")
        # Kaynak modülü çerezi süreç-içi global'de tutuyor.
        self._kimlikleri_uygula()
        return cerez_durumu("")

    # ── Oturum kimliği bağışı ───────────────────────────────────────────────
    @staticmethod
    def _katki():
        from ..qt import katki_dialog
        return katki_dialog

    def kimlik_bagisi_teklif(self, netscape: str) -> None:
        """Ayar açıksa onay penceresi; onay yoksa HİÇBİR ŞEY gönderilmez."""
        if not netscape:
            return
        try:
            ayarlar: Dict[str, Any] = self._dosya().ayarlar or {}
        except Exception:
            return
        if not bool(ayarlar.get("kimlik paylas", False)):
            return
        katki = self._katki()
        if not katki.onay_al(katki.KAYNAK_TRANIME, self._pencere):
            self._durum("Oturum kimliği bağışlanmadı.")
            return
        try:
            bagis_id = katki.bagis_gonder(netscape, katki.KAYNAK_TRANIME, ayarlar)
        except Exception as exc:
            self._durum(f"Kimlik bağışı gönderilemedi: {exc}", "hata")
            return
        # Gönderim ile kaydetme AYRI: kaydetme düşerse numara ekrana yazılıyor.
        kimlikler = bagis_kimlikleri(ayarlar)
        if bagis_id not in kimlikler:
            kimlikler.append(bagis_id)
        try:
            self._dosya().set_ayar("kimlik bagis id", kimlikler)
        except Exception as exc:
            self._kopru.yay("ayar_bagis", self._bagis_durumu(kimlikler))
            self._durum(f"Bağış SUNUCUYA ULAŞTI ama numarası kaydedilemedi ({exc}). "
                        f"Geri çekebilmek için bu numarayı saklayın: {bagis_id}", "hata")
            return
        self._kopru.yay("ayar_bagis", self._bagis_durumu(kimlikler))
        self._durum("Oturum kimliği bağışlandı. Geri çekmek için “Bağışımı geri çek”.", "tamam")

    @uc()
    def bagis_geri_cek(self) -> Dict[str, Any]:
        """Önce sunucudan sil, sonra numarayı düş (sıra tersine dönemez)."""
        ayarlar: Dict[str, Any] = self._dosya().ayarlar or {}
        kimlikler = bagis_kimlikleri(ayarlar)
        if not kimlikler:
            return dict(self._bagis_durumu([]), mesaj="Geri çekilecek bağış yok.", tur="bilgi")
        kalan, hatalar = [], []
        katki = self._katki()
        for bid in kimlikler:
            try:
                katki.bagis_geri_cek(bid, ayarlar)
            except Exception as exc:
                kalan.append(bid)
                hatalar.append(f"{bid}: {exc}")
        try:
            self._dosya().set_ayar("kimlik bagis id", kalan)
        except Exception as exc:
            return dict(self._bagis_durumu(kimlikler), tur="hata",
                        mesaj=f"Bağış(lar) sunucudan silindi ama numara yerelde kaldı: {exc}")
        if hatalar:
            silinen = len(kimlikler) - len(kalan)
            bas = ("Bağış geri çekilemedi." if silinen == 0 else
                   f"{silinen}/{len(kimlikler)} bağış geri çekildi.")
            return dict(self._bagis_durumu(kalan), tur="hata",
                        mesaj=bas + " Geri çekilemeyenlerin numarası saklandı, tekrar "
                        "deneyebilirsiniz: " + "; ".join(hatalar))
        return dict(self._bagis_durumu(kalan), tur="tamam",
                    mesaj="Bağışınız geri çekildi ve sunucudan silindi." if len(kimlikler) == 1
                    else f"{len(kimlikler)} bağış geri çekildi ve sunucudan silindi.")

    # ── Discord / bakım ─────────────────────────────────────────────────────
    @uc()
    def discord_ayarla(self, acik: bool) -> Dict[str, Any]:
        """Anlık etkili ("Kaydet"i beklemez)."""
        from ..qt import prefs
        if not prefs.ayar_yaz(discord_rich_presence=bool(acik)):
            raise UcHatasi("Discord ayarı kaydedilemedi")
        if self.discord is not None:
            self.discord.ayar_uygula()
        return {"metin": self._discord_metni(),
                "mesaj": "Discord Rich Presence " + ("açıldı." if acik else "kapatıldı.")}

    @uc()
    def guncelleme_denetle(self) -> bool:
        if self.updates is None:
            return False
        self._durum("Güncellemeler denetleniyor…")
        self.updates.kontrol_et(sessiz=False)
        return True

    @uc()
    def gereksinim_denetle(self) -> bool:
        """Elle denetim: "Atla" tercihini de geri alır."""
        if self.requirements is None:
            return False
        self.requirements.atlandi_yaz(False)
        self._durum("Gereksinimler denetleniyor…")
        self.requirements.denetle(kullanici_istegi=True)
        return True

    # ── AniList ─────────────────────────────────────────────────────────────
    @uc()
    def anilist_giris(self, client_id: str = "", client_secret: str = "",
                      redirect_uri: str = "") -> bool:
        """Önce ekrandaki OAuth bilgilerini kaydet, sonra tarayıcıyı aç."""
        from ..qt import prefs
        if not prefs.anilist_yaz(client_id, client_secret, redirect_uri):
            raise UcHatasi("AniList yapılandırması kaydedilemedi")
        if self.anilist.giris_yap():
            self._durum("Tarayıcıda AniList girişini tamamlayın…")
            return True
        return False

    @uc()
    def anilist_cikis(self) -> Dict[str, Any]:
        self.anilist.cikis_yap()
        return self._anilist_durumu()

    # ── Çevrimdışı arşiv ────────────────────────────────────────────────────
    @uc("arsiv_durumu", arka=True)
    def arsiv_durumu(self) -> Dict[str, Any]:
        """Etkin konum ve içerik (ağa ÇIKMAZ; dizin.json'ı diske yükleyebilir)."""
        from ...sources import animedepo
        try:
            durum = animedepo.arsiv_durumu()
        except Exception as exc:
            # Sebep olduğu gibi: genel çevirici `PermissionError`ı "indirme
            # klasörüne yazılamıyor" diye anlatıyor, burada yanlış olurdu.
            raise UcHatasi(str(exc) or type(exc).__name__) from exc
        self._arsiv_indirilen_var = bool(durum.indirilen_var)
        return {
            "konum": ARSIV_KONUM_ADLARI.get(durum.kaynak, durum.kaynak),
            "kaynak": durum.kaynak,
            "adres": str(durum.adres),
            "icerik": arsiv_icerik_metni(durum),
            "uyarilar": arsiv_uyarilari(durum),
            "indirilen_var": bool(durum.indirilen_var),
            "indirilen_dizini": str(durum.indirilen_dizini),
            "mesgul": self._arsiv_mesgul,
            "boyut_mb": TAM_ARSIV_BOYUTU_MB,
        }

    def _arsiv_sonuc(self, tur: str, mesaj: str) -> None:
        self._arsiv_mesgul = None
        self._arsiv_iptal = None
        self._kopru.yay("arsiv_sonuc", {"tur": tur, "mesaj": mesaj})

    def _mesgul_mu(self) -> None:
        if self._arsiv_mesgul is not None:
            raise UcHatasi("Bir arşiv işlemi zaten sürüyor.")

    @uc()
    def arsiv_indir(self) -> bool:
        """"Tüm arşivi indir" / "Arşivi güncelle" (aynı eylem)."""
        self._mesgul_mu()
        iptal = threading.Event()
        self._arsiv_iptal = iptal
        self._arsiv_mesgul = "indirme"
        self._arsiv_kaynak_adi = ""
        self._kopru.yay("arsiv_ilerleme", {"metin": "Bağlanılıyor…", "oran": None})
        from ..qt.workers import run_bg
        # Genel havuz, bilinçli: uzun iş havuzu bölüm indirmeleriyle dolu
        # olabilir; arşiv o kuyruğun sonunda "bağlanılıyor"da beklerdi.
        run_bg(self._arsiv_indir_is, iptal)
        return True

    def _arsiv_indir_is(self, iptal: threading.Event) -> None:
        from ...common import arsiv_paketi as paket
        from ...sources import animedepo
        son = [0.0]
        yay = self._kopru.yay

        def ilerleme(indirilen: int, toplam: Optional[int]) -> None:
            simdi = time.monotonic()
            bitti = toplam is not None and indirilen >= toplam
            if indirilen and not bitti and simdi - son[0] < ILERLEME_ARALIGI:
                return
            son[0] = simdi
            kaynak = f"{self._arsiv_kaynak_adi}: " if self._arsiv_kaynak_adi else ""
            if toplam:
                yay("arsiv_ilerleme", {"oran": min(1.0, indirilen / toplam),
                                       "metin": f"{kaynak}{mb(indirilen)} / {mb(toplam)} MB indirildi"})
            else:
                # GitLab paketi anında üretiyor, boyut başlığı yok.
                yay("arsiv_ilerleme", {"oran": None, "metin":
                                       f"{kaynak}{mb(indirilen)} MB indirildi (toplam ~{TAM_ARSIV_BOYUTU_MB} MB)"})

        def asama(ad: str, kaynak_adi: str) -> None:
            self._arsiv_kaynak_adi = kaynak_adi
            metin = {
                paket.ASAMA_BAGLANMA: f"Bağlanılıyor: {kaynak_adi}…",
                paket.ASAMA_INDIRME: f"{kaynak_adi} paketi indiriliyor…",
                paket.ASAMA_ACMA: "Paket açılıyor (83 bin dosya; bir dakika kadar sürebilir)…",
                paket.ASAMA_YERLESTIRME: "Yeni arşiv yerine konuyor…",
            }.get(ad)
            if metin:
                yay("arsiv_ilerleme", {"oran": None, "metin": metin})

        try:
            yol = animedepo.tam_arsiv_indir(ilerleme=ilerleme, iptal=iptal, asama=asama)
        except paket.IptalEdildi:
            self._gui(lambda: self._arsiv_sonuc(
                "bilgi", "Arşiv indirmesi iptal edildi; önceki arşiv (varsa) yerinde duruyor."))
            return
        except Exception as exc:
            mesaj = str(exc) or type(exc).__name__
            onek = "tam arşiv indirilemedi — "
            sebep = mesaj[len(onek):] if mesaj.startswith(onek) else mesaj
            self._gui(lambda: self._arsiv_sonuc(
                "hata", f"Arşiv indirilemedi. Sebep: {sebep}. Önceki arşiv (varsa) yerinde duruyor."))
            return
        self._gui(lambda: self._arsiv_sonuc(
            "tamam", f"Tam arşiv indirildi: {yol}. TürkAnime araması ve bölüm listeleri "
            "artık internetsiz çalışır."))

    @uc()
    def arsiv_iptal(self) -> bool:
        if self._arsiv_iptal is None:
            return False
        self._arsiv_iptal.set()
        self._kopru.yay("arsiv_ilerleme", {"oran": None, "metin": "İptal ediliyor…"})
        return True

    def arsiv_indirmeyi_durdur(self) -> None:
        """Pencere kapanırken: süren arşiv indirmesini iptal et."""
        if self._arsiv_iptal is not None:
            self._arsiv_iptal.set()

    @uc()
    def arsiv_klasor_sec(self) -> bool:
        """Elinizdeki arşiv kopyasını göster (içinde dizin.json olmalı)."""
        self._mesgul_mu()
        from ...sources import animedepo
        ayarlar = self._dosya().ayarlar or {}
        baslangic = str(ayarlar.get(animedepo.DIZIN_AYAR_ANAHTARI) or "") or str(Path.home())
        secilen = self.klasor_sec(baslangic, "Arşiv klasörünü seç (içinde dizin.json olmalı)")
        if not secilen:
            return False
        self._arsiv_mesgul = "islem"
        from ..qt.workers import run_bg
        run_bg(self._arsiv_klasor_is, secilen)
        return True

    def _arsiv_klasor_is(self, secilen: str) -> None:
        from ...common import arsiv_paketi as paket
        try:
            veri = paket.arsivi_dogrula(Path(secilen))
        except Exception as exc:
            mesaj = str(exc) or type(exc).__name__
            self._gui(lambda: self._arsiv_sonuc(
                "hata", f"Bu klasör arşiv olarak kullanılamaz: {mesaj}. dizin.json'ın "
                "bulunduğu klasörü seçin (ör. indirilen arşivin kendisi ya da depodaki "
                "arsiv/). Ayar değiştirilmedi."))
            return
        sayi = paket.anime_sayisi(veri)
        self._gui(lambda: self._arsiv_klasor_kabul(secilen, sayi))

    def _arsiv_klasor_kabul(self, secilen: str, sayi: int) -> None:
        from ...sources import animedepo
        try:
            self._dosya().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, secilen)
        except Exception as exc:
            self._arsiv_sonuc("hata", f"Arşiv klasörü kaydedilemedi: {exc}")
            return
        # Konum süreç boyunca bir kez çözülüp önbellekleniyor.
        animedepo.sifirla()
        adet = f"{sayi:,}".replace(",", ".")
        self._arsiv_sonuc("tamam", f"Arşiv klasörü ayarlandı ({adet} anime): {secilen}")

    @uc()
    def arsiv_varsayilan(self) -> Dict[str, str]:
        self._mesgul_mu()
        from ...sources import animedepo
        silindi = self._dosya().ayar_sil(animedepo.DIZIN_AYAR_ANAHTARI)
        animedepo.sifirla()
        if silindi:
            return {"tur": "tamam", "mesaj":
                    "Seçilen arşiv klasörü unutuldu; arşiv varsayılan sırayla aranıyor."}
        return {"tur": "bilgi", "mesaj": "Zaten varsayılan sıra kullanılıyor."}

    @uc()
    def arsiv_sil(self, onay: bool = False) -> bool:
        """İndirilen tam arşivi sil — YALNIZCA `<veri kökü>/cevrimdisi_arsiv`.

        Onayı sayfa soruyor (kendi penceresiyle); ``onay`` olmadan silinmez.
        """
        if not onay:
            raise UcHatasi("silme onaylanmadı")
        self._mesgul_mu()
        self._arsiv_mesgul = "islem"
        from ..qt.workers import run_bg
        run_bg(self._arsiv_sil_is)
        return True

    def _arsiv_sil_is(self) -> None:
        from ...sources import animedepo
        try:
            silindi = animedepo.indirilen_arsivi_sil()
        except Exception as exc:
            mesaj = str(exc) or type(exc).__name__
            self._gui(lambda: self._arsiv_sonuc("hata", f"İndirilen arşiv silinemedi: {mesaj}"))
            return
        self._gui(lambda: self._arsiv_sonuc(
            "tamam" if silindi else "bilgi",
            "İndirilen arşiv silindi." if silindi else "Silinecek indirilmiş arşiv yok."))


__all__ = ["AyarlarUclari", "ALANLAR", "ARSIV_KONUM_ADLARI", "SIZAN_SECRET_UYARISI",
           "bagis_kimlikleri", "bagis_metni", "cerez_durumu", "arsiv_icerik_metni",
           "arsiv_uyarilari", "mb", "tarih_metni", "TAM_ARSIV_BOYUTU_MB"]
