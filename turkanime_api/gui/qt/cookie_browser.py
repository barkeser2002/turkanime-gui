"""TRAnimeİzle cookie toplayıcı — QtWebEngine tabanlı (Selenium'un yerine).

Eski akış harici bir Selenium tarayıcısı (Chrome→Edge→Firefox→Chromium) açıp
`driver.get_cookies()`'i 2 sn'de bir yokluyordu; bu hem ağır (gerekirse onlarca
MB Chromium indiriyordu) hem de paketlenmiş EXE'de kırılgandı (Chrome yolu
bulma, registry taraması).

Yeni akış: uygulamanın **içine gömülü** bir `QWebEngineView`. Kullanıcı bot
kontrolünü/captcha'yı aynı pencerede çözer; cookie'ler `QWebEngineCookieStore`
üzerinden **olay tabanlı** (`cookieAdded`) toplanır — yoklama yok.

Pencere artık genel "Erişimi aç" penceresinin (`erisim_penceresi`) bir
yapılandırması: gerekli çerez (".AitrWeb.Session"), geçici profil, Netscape
çıktısı ve eski cümleler burada. Bütün kaynaklar aynı pencereyi kullanıyor;
TRAnimeİzle'nin farkı oturumun `ayarlar.json`'a (`tranime_cookie`) Netscape
metni olarak yazılması.

Public kontrat eski modülle birebir aynıdır (`on_status` / `on_cookies` /
`on_error`, `start()` / `stop()` / `is_running`) ve üretilen Netscape metni de
aynı formatta olduğu için `tranime.set_session_cookie()` değişmeden çalışır.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import QWidget

from ...common.oturumlar import ErisimHedefi
from .erisim_penceresi import ErisimPenceresi, is_available, qcookie_sozluk

# ── Eski modülle aynı sabitler ──────────────────────────────────────────────
TRANIME_BASE = "https://www.tranimeizle.io"
TARGET_ANIME = f"{TRANIME_BASE}/anime/naruto-izle"
COOKIE_DOMAIN = "tranimeizle.io"
REQUIRED_COOKIES = {".AitrWeb.Session"}
MAX_WAIT_SECONDS = 300  # 5 dakika


# ── Cookie dönüşümü ─────────────────────────────────────────────────────────
def _qcookie_to_dict(cookie: QNetworkCookie) -> Dict:
    """QNetworkCookie -> eski Selenium cookie sözlüğüyle aynı şekil (+ httponly)."""
    return qcookie_sozluk(cookie)


def _cookies_to_netscape(cookies: List[Dict]) -> str:
    """Cookie listesini Netscape HTTP Cookie File formatına çevir.

    Çıktı eski Selenium sürümüyle birebir aynı şekildedir; `set_session_cookie`
    bu metni tab ile ayırıp 7 alan bekler.
    """
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# TürkAnime GUI tarafından otomatik oluşturuldu.",
        "",
    ]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        expiry = str(int(c.get("expiry", 0)))
        name = c.get("name", "")
        value = c.get("value", "")
        if not name:
            continue
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def _has_required_cookies(cookies: List[Dict]) -> bool:
    names = {c.get("name", "") for c in cookies}
    return REQUIRED_COOKIES.issubset(names)


def _filter_tranime_cookies(cookies: List[Dict]) -> List[Dict]:
    return [c for c in cookies if COOKIE_DOMAIN in c.get("domain", "")]


def tranime_hedefi() -> ErisimHedefi:
    """Modül sabitlerinden hedef — ÇAĞRI anında okunuyor (testler sahteliyor)."""
    return ErisimHedefi(kaynak="TRAnimeİzle", etiket="TRAnimeİzle",
                        adres=TARGET_ANIME, alanlar=(COOKIE_DOMAIN,),
                        profil_adi="tranime",
                        gerekli_cerezler=frozenset(REQUIRED_COOKIES),
                        cerez_akisi=True)


# ── Gömülü tarayıcı diyaloğu ────────────────────────────────────────────────
class CookieBrowserDialog(ErisimPenceresi):
    """İçinde gerçek bir tarayıcı olan cookie toplama penceresi."""

    cookies_ready = Signal(str)   # Netscape metni

    BASLIK = "TRAnimeİzle — Bot kontrolünü çözün"
    METIN_YUKLENIYOR = "Sayfa yükleniyor… Bot kontrolü çıkarsa pencerede çözün."
    METIN_BEKLENIYOR = ("Bot kontrolünü/captcha'yı bu pencerede çözün. "
                        "Oturum çerezi alınınca pencere kendiliğinden kapanacak.")
    METIN_YUKLENEMEDI = "Sayfa yüklenemedi; bağlantınızı kontrol edin."
    METIN_TAMAM = "Oturum çerezi alındı."
    METIN_ZAMAN_ASIMI = "Süre doldu ({saniye} sn): oturum çerezi alınamadı."

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(tranime_hedefi(), parent, azami_bekleme=MAX_WAIT_SECONDS)

    def _profil_kur(self):
        from PySide6.QtWebEngineCore import QWebEngineProfile
        from PySide6.QtWidgets import QApplication

        # GEÇİCİ (off-the-record) profil — bilinçli tercih.
        #
        # Kalıcı profil kullanmak gerçek bir hataya yol açıyordu: çerez diske
        # yazıldıktan sonra sunucu çerezi YENİDEN GÖNDERMİYOR (oturum zaten
        # var) ve Chromium diskten yüklediği çerez için `cookieAdded` yaymıyor.
        # Qt6'da cookie store'un okuma API'si de yok (`loadAllCookies()`
        # mevcut çerezleri yeniden yayınlamıyor; Qt 6.11'de de ölçüldü),
        # dolayısıyla çerezi daha önce almış kullanıcı sonsuza dek "bot
        # kontrolünü çözün" ekranında kalıyordu.
        #
        # Kalıcılığa zaten ihtiyacımız yok: çerez `ayarlar.json`'a
        # (`tranime_cookie`) bizim tarafımızdan kaydediliyor. Her oturumu temiz
        # başlatmak `cookieAdded`'in daima tetiklenmesini garantiler. (Diğer
        # kaynaklar kalıcı profil kullanıyor; o sorunu `erisim_penceresi`
        # kayıttan geri besleyerek çözüyor.)
        #
        # YAŞAM SÜRESİ: Qt, profilin sayfadan **uzun yaşamasını** şart koşar.
        # Profil dialog'a çocuk yapılırsa yıkım sırası profili sayfadan önce
        # silebiliyor; ebeveynsiz bırakılıp yalnızca Python referansıyla
        # tutulursa da dialog GC edilince aynı yarış oluşuyor (test paketinde
        # Windows erişim ihlaliyle çökme olarak yakalandı).
        # Çözüm: profili QApplication'a bağla — uygulama boyunca yaşar, yani
        # her zaman sayfadan sonra yıkılır. Ebeveynli `QWebEngineProfile(parent)`
        # yine **off-the-record**'dur (kalıcı profil ancak isim verilince olur).
        profile = QWebEngineProfile(QApplication.instance())
        self._seed_age_cookie(profile.cookieStore())
        return profile

    @staticmethod
    def _seed_age_cookie(store) -> None:
        """Yaş doğrulamasını önden set et (eski sürümdeki davranış)."""
        try:
            cookie = QNetworkCookie(b"age_verified", b"true")
            cookie.setDomain("." + COOKIE_DOMAIN)
            cookie.setPath("/")
            store.setCookie(cookie, QUrl(TRANIME_BASE))
        except Exception:
            pass

    def _tohumla(self, store) -> None:
        # Yükleme başlamadan hemen önce tekrar tohumla: WebEngine çekirdeği ilk
        # yüklemeyle ayağa kalktığı için bu, çerezin kesinlikle uygulanmasını garantiler.
        self._seed_age_cookie(store)

    def _on_load_finished(self, ok: bool) -> None:
        if self._finished:
            return
        if not ok:
            self._set_status(self.METIN_YUKLENEMEDI)
            return
        if not self._check_done():
            self._set_status(self.METIN_BEKLENIYOR)

    def _hedef_cerezleri(self) -> List[Dict]:
        return _filter_tranime_cookies(list(self._cerezler.values()))

    def _basari_yay(self, sonuc: Dict) -> None:
        self.cookies_ready.emit(_cookies_to_netscape(sonuc["cerezler"]))


# ── Eski API ile uyumlu sarmalayıcı ─────────────────────────────────────────
class CookieBrowserWorker(QObject):
    """Selenium tabanlı `CookieBrowserWorker` ile aynı yüzey, QtWebEngine gövdesi.

    Fark: QtWebEngine ana (GUI) thread'inde çalışmak zorunda olduğu için artık
    ayrı bir thread yok; bu yüzden `dispatch` parametresine gerek kalmadı.
    `dispatch` ve `allow_download` yalnızca çağıran kodu bozmamak için kabul
    edilir ve yok sayılır (Chromium indirme mekanizması tamamen kalktı).

    ``on_cancel``: kullanıcı pencereyi çerez almadan kapattı ("Erişimi aç"
    sonucu bekleyen sayfa bununla haber alıyor; eski çağıranlar vermiyor).
    """

    def __init__(
        self,
        on_status: Optional[Callable[[str], None]] = None,
        on_cookies: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        dispatch: Optional[Callable] = None,      # geriye dönük uyumluluk
        allow_download: Optional[Callable] = None,  # geriye dönük uyumluluk
        parent: Optional[QWidget] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.on_status = on_status or (lambda m: None)
        self.on_error = on_error or (lambda m: None)
        self.on_cookies = on_cookies or (lambda m: None)
        self.on_cancel = on_cancel or (lambda: None)
        self._parent_widget = parent
        self._dialog: Optional[CookieBrowserDialog] = None
        self._done = False

    @property
    def is_running(self) -> bool:
        return self._dialog is not None and self._dialog.isVisible()

    def start(self) -> None:
        """Gömülü tarayıcı penceresini aç."""
        if self.is_running:
            return
        if not is_available():
            self._safe(self.on_error, "QtWebEngine kullanılamıyor (PySide6-Addons kurulu mu?).")
            return

        self._done = False
        dlg = CookieBrowserDialog(self._parent_widget)
        dlg.status.connect(lambda m: self._safe(self.on_status, m))
        dlg.cookies_ready.connect(self._on_cookies_ready)
        dlg.failed.connect(self._on_failed)
        dlg.finished.connect(self._on_dialog_finished)
        self._dialog = dlg

        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dlg.show()
        dlg.begin()

    def one_getir(self) -> None:
        """Açık pencereyi öne getir ("Erişimi aç"a ikinci kez basıldı)."""
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()

    def stop(self) -> None:
        """İptal et ve pencereyi kapat."""
        if self._dialog is not None:
            self._dialog.reject()

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def _on_cookies_ready(self, netscape: str) -> None:
        if self._done:
            return
        self._done = True
        self._safe(self.on_cookies, netscape)

    def _on_failed(self, mesaj: str) -> None:
        if self._done:
            return
        self._done = True
        self._safe(self.on_error, mesaj)

    def _on_dialog_finished(self, _result: int) -> None:
        if not self._done:
            self._done = True
            self._safe(self.on_status, "Cookie toplama iptal edildi.")
            self._safe(self.on_cancel)
        # Referansı bu slot'un içinde DÜŞÜRMÜYORUZ: dialog hâlâ `finished`
        # sinyalini yayıyor; son referansı burada bırakmak nesneyi yayın
        # sırasında yok eder (use-after-free). Yıkımı olay döngüsüne bırakıyoruz.
        dlg, self._dialog = self._dialog, None
        if dlg is not None:
            dlg.deleteLater()

    @staticmethod
    def _safe(cb: Callable, *args) -> None:
        try:
            cb(*args)
        except Exception:
            pass


__all__ = [
    "CookieBrowserWorker",
    "CookieBrowserDialog",
    "is_available",
    "tranime_hedefi",
    "TRANIME_BASE",
    "TARGET_ANIME",
    "COOKIE_DOMAIN",
    "REQUIRED_COOKIES",
    "MAX_WAIT_SECONDS",
]
