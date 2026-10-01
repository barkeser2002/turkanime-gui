"""Erişim penceresi — kaynağın bot doğrulamasını KULLANICININ çözdüğü tarayıcı.

"Erişimi aç" düğmesinin arkası. Kaynağın sitesi uygulamanın içindeki gerçek
bir tarayıcıda (QtWebEngine) açılıyor; Cloudflare "Just a moment…"/Turnstile,
LiteSpeed bot doğrulaması gibi bir sayfa çıkarsa kullanıcı onu bu pencerede
kendisi çözüyor. Uygulama hiçbir şeye tıklamıyor, betik çalıştırıp doğrulamayı
çözmüyor; yalnızca sayfanın doğrulama olmaktan çıktığını izliyor ve o anda
tarayıcının çerezlerini + kimliğini (User-Agent, istemci ipuçları) alıyor.
Kaydı `common.oturumlar` tutuyor, kaynakların HTTP katmanı oradan okuyor.

"Geçildi" ölçütü (`_yokla`): sayfa kaynağın alanında, yüklenmiş, doğrulama
izi taşımıyor (`oturumlar.dogrulama_sayfasi_mi`) ve bu hâl ardışık iki
yoklamada sürüyor (Cloudflare geçişten sonra sayfayı yeniden yüklüyor; arada
bir an boş sayfa görünebiliyor). Hedef belirli çerezler istiyorsa
(TRAnimeİzle: ".AitrWeb.Session") ölçüt o çerezlerin gelmesi.

KALICI PROFİL, KAYNAK BAŞINA: `<veri kökü>/erisim_profilleri/<modül>`.
Doğrulamanın çerezi uygulama kapanınca kaybolmasın (Cloudflare'ın süresi
boyunca geçerli). Ama Qt 6'da çerez deposunun OKUMA yolu yok: diskten
yüklenen çerezler `cookieAdded` yaymıyor (`loadAllCookies()` da yaymıyor,
Qt 6.11'de ölçüldü) ve sunucu çerezi yeniden göndermiyorsa pencere onu hiç
görmüyor. Bu yüzden:

* Doğru kaynak `oturumlar.json`: çerez GELDİĞİ AN (cookieAdded) yakalanıp
  oraya yazılıyor; pencere açılırken kayıttaki çerezler profile geri ekleniyor
  (profil ile kayıt aynı şeyi bilsin).
* Kaydı olmayan kaynağın profili SIFIRLANIYOR (klasör siliniyor ya da
  çerezleri temizleniyor): profilde bizim bilmediğimiz geçerli bir
  `cf_clearance` kalırsa site doğrulama göstermez, çerez de gelmez; pencere
  "geçildi" deyip boş oturum kaydederdi.
* Profil kaynak başına: "Temizle" o kaynağın profilini (başkalarınınkine
  dokunmadan) tümüyle silebiliyor.

TRAnimeİzle'nin eski akışı (`cookie_browser`) bu pencerenin bir yapılandırması:
geçici (off-the-record) profil, gerekli çerez, Netscape çıktısı.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ...common import oturumlar
from ...common.oturumlar import ErisimHedefi

AZAMI_BEKLEME = 300          # sn; kullanıcı doğrulamayla uğraşırken acele yok
YOKLAMA_MS = 700
KARARLI_YOKLAMA = 2           # "doğrulama yok" bu kadar ardışık yoklamada sürmeli
ASGARI_SAYFA = 200            # bundan kısa HTML (about:blank) sayfa sayılmaz

# Chromium'un kendi hata sayfası (ERR_CERT_*, DNS, bağlantı): başlığı alan
# adı olduğu için "yüklendi" sanılmasın.
_HATA_SAYFASI_IZLERI = ('id="main-frame-error"', "neterror")

# Gömülü tarayıcının istemci ipuçları: sayfanın izole dünyasında (sayfanın
# kendi betiklerinden ayrı) salt okunur navigator bilgisi. Doğrulamaya
# dokunmuyor; yalnızca HTTP isteklerinde aynı başlıkları göndermek için.
_IPUCU_BETIGI = ("(function(){var d=navigator.userAgentData;return d?JSON.stringify("
                 "{b:d.brands,m:d.mobile,p:d.platform}):'';})()")
_IZOLE_DUNYA = 1              # QWebEngineScript.ApplicationWorld


def is_available() -> bool:
    """QtWebEngine kullanılabilir mi?"""
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except ImportError:
        return False


# ── Çerez dönüşümü ─────────────────────────────────────────────────────────
def qcookie_sozluk(cookie: QNetworkCookie) -> Dict[str, Any]:
    """QNetworkCookie → `oturumlar` çerez sözlüğü (eski Selenium biçiminin üst kümesi)."""
    expiry = 0
    exp = cookie.expirationDate()
    if exp.isValid():
        expiry = max(0, exp.toSecsSinceEpoch())
    return {
        "domain": cookie.domain(),
        "path": cookie.path() or "/",
        "secure": bool(cookie.isSecure()),
        "httponly": bool(cookie.isHttpOnly()),
        "expiry": expiry,
        "name": bytes(cookie.name()).decode("utf-8", "replace"),
        "value": bytes(cookie.value()).decode("utf-8", "replace"),
    }


def sozluk_qcookie(cerez: Dict[str, Any]) -> Tuple[QNetworkCookie, QUrl]:
    """Kayıttaki çerez → (QNetworkCookie, köken adresi) — profile geri eklemek için.

    Noktasız alan yalnızca-konak çerezi: alan boş bırakılıp köken adresi
    verilince Chromium da onu yalnızca-konak kuruyor.
    """
    from PySide6.QtCore import QDateTime
    c = QNetworkCookie(str(cerez.get("name") or "").encode("utf-8"),
                       str(cerez.get("value") or "").encode("utf-8"))
    alan = str(cerez.get("domain") or "")
    if alan.startswith("."):
        c.setDomain(alan)
    c.setPath(str(cerez.get("path") or "/"))
    c.setSecure(bool(cerez.get("secure")))
    c.setHttpOnly(bool(cerez.get("httponly")))
    bitis = int(cerez.get("expiry") or 0)
    if bitis:
        c.setExpirationDate(QDateTime.fromSecsSinceEpoch(bitis))
    return c, QUrl(f"https://{alan.lstrip('.')}/")


def _anahtar(cerez: Dict[str, Any]) -> Tuple[str, str, str]:
    return (str(cerez.get("domain") or ""), str(cerez.get("name") or ""),
            str(cerez.get("path") or "/"))


def alana_ait(cerez: Dict[str, Any], alanlar) -> bool:
    """Çerez hedefin alanlarından birine mi ait? (Cloudflare'ın üçüncü taraf
    challenges.cloudflare.com çerezleri kaynağın oturumuna karışmasın.)"""
    konak = str(cerez.get("domain") or "").lstrip(".").lower()
    return any(oturumlar.konak_alanda_mi(konak, alan) for alan in alanlar)


def ipuclari_basliklari(ham: Any) -> Dict[str, str]:
    """`navigator.userAgentData` özeti → sec-ch-ua* başlıkları (okunamazsa {})."""
    try:
        veri = json.loads(ham) if isinstance(ham, str) else ham
        markalar = ", ".join(f'"{m["brand"]}";v="{m["version"]}"' for m in veri["b"])
    except (ValueError, TypeError, KeyError):
        return {}
    if not markalar:
        return {}
    return {"sec-ch-ua": markalar, "sec-ch-ua-mobile": "?1" if veri.get("m") else "?0",
            "sec-ch-ua-platform": f'"{veri.get("p") or ""}"'}


# ── Kaynak başına kalıcı profil ─────────────────────────────────────────────
class _Profil:
    """Kalıcı profil + uygulama boyunca gördüğü çerezler (Qt okuma yolu yok)."""

    def __init__(self, profil):
        self.profil = profil
        self.cerezler: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        depo = profil.cookieStore()
        depo.cookieAdded.connect(self._eklendi)
        depo.cookieRemoved.connect(self._silindi)

    def _eklendi(self, cookie: QNetworkCookie) -> None:
        cerez = qcookie_sozluk(cookie)
        self.cerezler[_anahtar(cerez)] = cerez

    def _silindi(self, cookie: QNetworkCookie) -> None:
        self.cerezler.pop(_anahtar(qcookie_sozluk(cookie)), None)

    def temizle(self) -> None:
        """Çerezleri sil. HTTP önbelleği SİLİNMİYOR: Qt, temizlik sürerken
        yeni gezinti başlatılmamasını şart koşuyor (`clearHttpCache`) ve
        pencere hemen ardından sayfayı açıyor — sayfa takılıyordu. Oturum
        çerezlerde; önbellekte doğrulama durumu yok."""
        self.cerezler.clear()
        try:
            self.profil.cookieStore().deleteAllCookies()
        except Exception:
            pass


_PROFILLER: Dict[str, _Profil] = {}


def profil_dizini(hedef: ErisimHedefi) -> Path:
    from ...cli.dosyalar import veri_koku
    return Path(veri_koku()) / oturumlar.PROFIL_KLASORU / hedef.profil_adi


def profil_yuklu_mu(hedef: ErisimHedefi) -> bool:
    return str(profil_dizini(hedef)) in _PROFILLER


def kalici_profil(hedef: ErisimHedefi) -> _Profil:
    """Kaynağın kalıcı profili (süreç boyunca tek).

    Ebeveyn QApplication: Qt profilin sayfalardan UZUN yaşamasını şart koşuyor
    (bkz. `cookie_browser`'daki yaşam süresi notu). Depolama adı klasörün
    özetini taşıyor: aynı süreçte iki farklı veri kökü (testler) aynı adlı iki
    profil kurmasın.
    """
    dizin = profil_dizini(hedef)
    anahtar = str(dizin)
    kayit = _PROFILLER.get(anahtar)
    if kayit is None:
        from PySide6.QtWebEngineCore import QWebEngineProfile
        from PySide6.QtWidgets import QApplication
        ozet = hashlib.sha1(anahtar.encode("utf-8")).hexdigest()[:10]
        profil = QWebEngineProfile(f"erisim-{hedef.profil_adi}-{ozet}",
                                   QApplication.instance())
        profil.setPersistentStoragePath(str(dizin))
        profil.setCachePath(str(dizin / "onbellek"))
        profil.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        kayit = _PROFILLER[anahtar] = _Profil(profil)
    return kayit


def profili_sifirla(hedef: ErisimHedefi) -> None:
    """Kaynağın profilini temizle: yüklüyse çerezleri/önbelleği, değilse klasörü.

    Yüklü değilken klasörü silmek şart: yüklenmemiş profile verilen
    "çerezleri sil" komutu Qt'de ağ bağlamı kurulana kadar bekliyor ve ilk
    sayfa isteği eski çerezle gidebiliyor.
    """
    kayit = _PROFILLER.get(str(profil_dizini(hedef)))
    if kayit is not None:
        kayit.temizle()
        return
    shutil.rmtree(profil_dizini(hedef), ignore_errors=True)


# ── Pencere ────────────────────────────────────────────────────────────────
class ErisimPenceresi(QDialog):
    """İçinde gerçek bir tarayıcı olan erişim penceresi.

    Sinyaller: ``acildi(sonuc)`` — doğrulama geçildi; ``sonuc`` =
    ``{"cerezler", "user_agent", "basliklar"}``. ``failed(str)`` — süre doldu.
    İptal (× / Esc / "İptal") yalnızca `finished` ile anlaşılır.
    """

    acildi = Signal(object)
    failed = Signal(str)
    status = Signal(str)

    # Metinler sınıf özniteliği: TRAnimeİzle yapılandırması (`cookie_browser`)
    # eski cümleleri AYNEN korumak için eziyor.
    BASLIK = "{etiket} — Erişimi aç"
    METIN_HAZIRLANIYOR = "Tarayıcı hazırlanıyor…"
    METIN_YUKLENIYOR = ("{etiket} açılıyor… Bot doğrulaması çıkarsa bu pencerede "
                        "kendiniz tamamlayın.")
    METIN_DOGRULAMA = ("{etiket} bot doğrulaması istiyor. Doğrulamayı bu pencerede "
                       "tamamlayın (ör. “Doğrulayın/Verify you are human” kutusunu "
                       "işaretleyin); geçince pencere kendiliğinden kapanır.")
    METIN_BEKLENIYOR = ("Sayfa açıldı. Bot doğrulaması çıkarsa bu pencerede "
                        "tamamlayın; oturum alınınca pencere kendiliğinden kapanacak.")
    METIN_YUKLENEMEDI = "Sayfa yüklenemedi; bağlantınızı kontrol edip “Yenile”ye basın."
    METIN_TAMAM = "Erişim açıldı; oturum kaydedildi."
    METIN_ZAMAN_ASIMI = ("Süre doldu ({dakika} dk): doğrulama tamamlanmadı. "
                         "Yeniden denemek için “Erişimi aç”a basın.")

    def __init__(self, hedef: ErisimHedefi, parent: Optional[QWidget] = None, *,
                 azami_bekleme: Optional[int] = None):
        super().__init__(parent)
        self.hedef = hedef
        self.azami_bekleme = int(azami_bekleme if azami_bekleme is not None
                                 else AZAMI_BEKLEME)
        self.setWindowTitle(self.BASLIK.format(etiket=hedef.etiket))
        self.resize(1000, 760)
        self.setSizeGripEnabled(True)

        self._cerezler: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self._finished = False
        self._basarili = False
        self._temiz_sayac = 0
        self._dogrulama_goruldu = False
        self._yuklendi = False
        self._ipuclari: Dict[str, str] = {}
        self._yoklama_suruyor = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.lblStatus = QLabel(self.METIN_HAZIRLANIYOR)
        self.lblStatus.setObjectName("Muted")
        self.lblStatus.setWordWrap(True)
        layout.addWidget(self.lblStatus)

        self.view = self._build_view()
        layout.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.btnReload = QPushButton("Yenile")
        self.btnReload.clicked.connect(self._yenile)
        row.addWidget(self.btnReload)
        row.addStretch(1)
        self.btnCancel = QPushButton("İptal")
        self.btnCancel.clicked.connect(self.reject)
        row.addWidget(self.btnCancel)
        layout.addLayout(row)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._timeout.start(self.azami_bekleme * 1000)

        self._yoklayici = QTimer(self)
        self._yoklayici.setInterval(YOKLAMA_MS)
        self._yoklayici.timeout.connect(self._yokla)

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _profil_kur(self):
        """Kaynağın kalıcı profili; kaydı yoksa önce sıfırlanıyor (modül notu)."""
        if oturumlar.kayit(self.hedef.kaynak) is None:
            profili_sifirla(self.hedef)
        self._profil_kaydi = kalici_profil(self.hedef)
        # Bu süreçte daha önce görülenler (profil pencereden uzun yaşıyor).
        self._cerezler.update(self._profil_kaydi.cerezler)
        return self._profil_kaydi.profil

    def _build_view(self):
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self._profile = self._profil_kur()
        store = self._profile.cookieStore()
        store.cookieAdded.connect(self._on_cookie_added)
        store.cookieRemoved.connect(self._on_cookie_removed)

        view = QWebEngineView(self)
        view.setPage(QWebEnginePage(self._profile, view))
        view.loadFinished.connect(self._on_load_finished)
        return view

    def _tohumla(self, store) -> None:
        """Kayıttaki çerezleri profile geri koy (kalıcı profil, bkz. modül notu)."""
        kayit = oturumlar.kayit(self.hedef.kaynak) or {}
        for cerez in kayit.get("cerezler") or []:
            try:
                qc, koken = sozluk_qcookie(cerez)
                store.setCookie(qc, koken)
            except Exception:
                pass

    # ── Akış ────────────────────────────────────────────────────────────────
    def begin(self) -> None:
        """Yüklemeyi başlat. ÖNCE `show()`: gösterilmemiş görünüm yüklemez."""
        self._set_status(self.METIN_YUKLENIYOR.format(etiket=self.hedef.etiket))
        self._tohumla(self._profile.cookieStore())
        self.view.load(QUrl(self.hedef.adres))
        self._yoklayici.start()

    def _yenile(self) -> None:
        if self._finished:
            return
        self._temiz_sayac = 0
        self.view.reload()

    def _set_status(self, msg: str) -> None:
        self.lblStatus.setText(msg)
        self.status.emit(msg)

    def _on_cookie_added(self, cookie: QNetworkCookie) -> None:
        cerez = qcookie_sozluk(cookie)
        self._cerezler[_anahtar(cerez)] = cerez
        self._check_done()

    def _on_cookie_removed(self, cookie: QNetworkCookie) -> None:
        self._cerezler.pop(_anahtar(qcookie_sozluk(cookie)), None)

    def _on_load_finished(self, ok: bool) -> None:
        if self._finished:
            return
        # `ok=False` burada "yüklenemedi" DEMEK DEĞİL: Cloudflare'ın doğrulama
        # sayfası kendini `?__cf_chl_rt_tk=` ile yeniden yüklerken de `False`
        # geliyor (deokwave.com'da ölçüldü; pencere bir an "yüklenemedi"
        # yazıyordu). Asıl karar yoklamada: Chromium'un hata sayfası izi.
        self._yuklendi = self._yuklendi or ok
        if ok:
            self._ipuclarini_oku()
        if not self._check_done() and ok and not self._dogrulama_goruldu:
            self._set_status(self.METIN_BEKLENIYOR)
        # Yüklenir yüklenmez bak: etkileşimsiz doğrulama saniyeler içinde
        # kendini yönlendirebiliyor, "doğrulama görüldü" kaçmasın.
        self._yokla()

    def _ipuclarini_oku(self) -> None:
        try:
            self.view.page().runJavaScript(_IPUCU_BETIGI, _IZOLE_DUNYA, self._ipuclari_geldi)
        except Exception:
            pass

    def _ipuclari_geldi(self, ham: Any) -> None:
        basliklar = ipuclari_basliklari(ham)
        if basliklar:
            self._ipuclari = basliklar

    def _hedef_cerezleri(self) -> List[Dict[str, Any]]:
        return [c for c in self._cerezler.values() if alana_ait(c, self.hedef.alanlar)]

    def _check_done(self) -> bool:
        """Gerekli çerezleri olan hedefte (TRAnimeİzle) ölçüt çerezlerin gelmesi."""
        if self._finished or not self.hedef.gerekli_cerezler:
            return self._finished and self._basarili
        adlar = {c.get("name", "") for c in self._hedef_cerezleri()}
        if not set(self.hedef.gerekli_cerezler).issubset(adlar):
            return False
        self._bitir()
        return True

    def _yokla(self) -> None:
        """Sayfa doğrulama olmaktan çıktı mı? (gerekli çerezi olmayan hedef)"""
        if self._finished or self.hedef.gerekli_cerezler or self._yoklama_suruyor:
            return
        self._yoklama_suruyor = True
        try:
            self.view.page().toHtml(self._html_geldi)
        except Exception:
            self._yoklama_suruyor = False

    def _html_geldi(self, html: str) -> None:
        self._yoklama_suruyor = False
        if self._finished:
            return
        html = html or ""
        konak = self.view.url().host()
        if oturumlar.dogrulama_sayfasi_mi(html):
            self._temiz_sayac = 0
            if not self._dogrulama_goruldu:
                self._dogrulama_goruldu = True
                self._set_status(self.METIN_DOGRULAMA.format(etiket=self.hedef.etiket))
            return
        hata_sayfasi = any(iz in html for iz in _HATA_SAYFASI_IZLERI)
        alanda = any(oturumlar.konak_alanda_mi(konak, a) for a in self.hedef.alanlar)
        if hata_sayfasi or not alanda or len(html) < ASGARI_SAYFA:
            self._temiz_sayac = 0
            if hata_sayfasi:
                self._set_status(self.METIN_YUKLENEMEDI)
            return
        self._temiz_sayac += 1
        if self._temiz_sayac >= KARARLI_YOKLAMA:
            self._bitir()

    def _kimlik(self) -> Tuple[str, Dict[str, str]]:
        """(User-Agent, istemci ipuçları): sayfadan okunan > Qt'nin bildirdiği > UA'dan."""
        ua = self._profile.httpUserAgent()
        if self._ipuclari:
            return ua, dict(self._ipuclari)
        try:
            ch = self._profile.clientHints()          # Qt 6.8+
            surumler = dict(ch.fullVersionList() or {})
            if surumler:
                markalar = ", ".join(f'"{m}";v="{str(v).split(".")[0]}"'
                                     for m, v in surumler.items())
                return ua, {"sec-ch-ua": markalar,
                            "sec-ch-ua-mobile": "?1" if ch.isMobile() else "?0",
                            "sec-ch-ua-platform": f'"{ch.platform()}"'}
        except Exception:
            pass
        return ua, oturumlar.istemci_ipuclari(ua)

    def _bitir(self) -> None:
        self._finished = True
        self._basarili = True
        self._timeout.stop()
        self._yoklayici.stop()
        ua, basliklar = self._kimlik()
        self._set_status(self.METIN_TAMAM)
        self._basari_yay({"cerezler": self._hedef_cerezleri(), "user_agent": ua,
                          "basliklar": basliklar,
                          "dogrulama_goruldu": self._dogrulama_goruldu})
        QTimer.singleShot(400, self.accept)

    def _basari_yay(self, sonuc: Dict[str, Any]) -> None:
        self.acildi.emit(sonuc)

    def _on_timeout(self) -> None:
        if self._finished:
            return
        self.failed.emit(self.METIN_ZAMAN_ASIMI.format(
            dakika=max(1, self.azami_bekleme // 60), saniye=self.azami_bekleme))
        self.reject()

    @property
    def basarili(self) -> bool:
        return self._basarili

    def reject(self):  # noqa: D102 (Qt imzası)
        if not self._finished:
            self._finished = True
            self._timeout.stop()
            self._yoklayici.stop()
        super().reject()


# ── Pencereyi süren denetleyici ─────────────────────────────────────────────
class ErisimIsci(QObject):
    """Pencereyi aç, sonucunu geri çağrılarla bildir, yıkımını düzgün yap.

    ``on_success(sonuc)`` doğrulama geçildi; ``on_error(mesaj)`` süre doldu;
    ``on_cancel()`` kullanıcı kapattı. Tam olarak biri, bir kez çağrılır.
    QtWebEngine GUI thread'inde çalışmak zorunda; bu sınıf da oradan kullanılır.
    """

    def __init__(self, hedef: Optional[ErisimHedefi] = None, *,
                 on_status: Optional[Callable[[str], None]] = None,
                 on_success: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.hedef = hedef
        self.on_status = on_status or (lambda m: None)
        self.on_success = on_success or (lambda s: None)
        self.on_error = on_error or (lambda m: None)
        self.on_cancel = on_cancel or (lambda: None)
        self._parent_widget = parent
        self._dialog: Optional[ErisimPenceresi] = None
        self._done = False

    @property
    def is_running(self) -> bool:
        return self._dialog is not None and self._dialog.isVisible()

    def _pencere_kur(self) -> ErisimPenceresi:
        return ErisimPenceresi(self.hedef, self._parent_widget)

    def start(self) -> bool:
        """Pencereyi aç; zaten açıksa öne getir. QtWebEngine yoksa False."""
        if self.is_running:
            self.one_getir()
            return True
        if not is_available():
            self._safe(self.on_error, "QtWebEngine kullanılamıyor (PySide6-Addons kurulu mu?).")
            return False
        self._done = False
        dlg = self._pencere_kur()
        dlg.status.connect(lambda m: self._safe(self.on_status, m))
        dlg.acildi.connect(self._acildi)
        dlg.failed.connect(self._basarisiz)
        dlg.finished.connect(self._on_dialog_finished)
        self._dialog = dlg
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        dlg.show()
        dlg.begin()
        return True

    def one_getir(self) -> None:
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()

    def stop(self) -> None:
        if self._dialog is not None:
            self._dialog.reject()

    def _acildi(self, sonuc: Dict[str, Any]) -> None:
        if self._done:
            return
        self._done = True
        self._safe(self.on_success, sonuc)

    def _basarisiz(self, mesaj: str) -> None:
        if self._done:
            return
        self._done = True
        self._safe(self.on_error, mesaj)

    def _on_dialog_finished(self, _result: int) -> None:
        if not self._done:
            self._done = True
            self._safe(self.on_cancel)
        # Referans bu slot'un içinde DÜŞÜRÜLMÜYOR gibi davranmak yetmez: dialog
        # hâlâ `finished` yayıyor; yıkımı olay döngüsüne bırakıyoruz.
        dlg, self._dialog = self._dialog, None
        if dlg is not None:
            dlg.deleteLater()

    @staticmethod
    def _safe(cb: Callable, *args) -> None:
        try:
            cb(*args)
        except Exception:
            import traceback
            traceback.print_exc()


__all__ = [
    "ErisimPenceresi", "ErisimIsci", "is_available", "qcookie_sozluk",
    "sozluk_qcookie", "alana_ait", "ipuclari_basliklari", "kalici_profil",
    "profili_sifirla", "profil_dizini", "profil_yuklu_mu",
    "AZAMI_BEKLEME", "YOKLAMA_MS", "KARARLI_YOKLAMA",
]
