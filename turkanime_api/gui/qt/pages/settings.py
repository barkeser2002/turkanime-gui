"""Ayarlar sayfası.

Ayarlar `cli.dosyalar.Dosyalar` üzerinden okunur/yazılır (CLI ile ortak dosya),
böylece iki arayüz aynı yapılandırmayı paylaşır.

Buradaki en önemli iş **TRAnimeİzle cookie'si**: o kaynak bot kontrolü nedeniyle
cookie olmadan hiç bölüm döndürmüyor. "Tarayıcıdan Al" düğmesi gömülü
QtWebEngine penceresini açar, kullanıcı kontrolü çözer, oturum çerezi otomatik
kaydedilir.

Çerez ve jetonlar diskte durmakla iş bitmiyor: kaynak modülleri onları süreç-içi
global'lerde tutuyor ve her süreç boş başlıyor. Bu yüzden sayfa `reload()`
sonunda `prefs.kaynak_kimliklerini_uygula()` çağırıyor — sayfa ana pencere
kurulurken (`MainWindow._build_ui`) örnekleniyor, yani bu çağrı pratikte süreç
açılışında ve her kayıt/temizleme sonrasında çalışıyor.

AniList OAuth bilgileri buradan girilir ama `ayarlar.json`'a YAZILMAZ: istemci
onları kendi dosyasında tutuyor (bkz. `prefs.anilist_yaz`). Client Secret alanı
opsiyoneldir — boş bırakılınca giriş, sır gerektirmeyen Implicit akışa düşer.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .. import prefs
from ..anilist import AniListService
from ..widgets import StatusLabel


class SettingsPage(QWidget):
    """İndirme klasörü, TRAnime cookie'si, bypass, AniList ve çevresel servisler."""

    def __init__(self, servis: Optional[AniListService] = None,
                 parent: Optional[QWidget] = None, discord=None, updates=None,
                 requirements=None):
        super().__init__(parent)
        self._cookie_worker = None
        self.servis = servis or AniListService(self)
        # Çevresel servisler ana pencereye ait; sayfa yalnızca düğmelerini
        # bağlar. Yoksa (tek başına açılan sayfa/test) ilgili bölüm pasif olur.
        self.discord = discord
        self.updates = updates
        self.requirements = requirements
        self._build_ui()
        self.servis.auth_changed.connect(self._on_auth)
        if updates is not None:
            updates.up_to_date.connect(
                lambda: self.lblStatus.ok("Uygulamanız güncel."))
            updates.check_failed.connect(self.lblStatus.error)
        if requirements is not None:
            requirements.all_present.connect(
                lambda: self.lblStatus.ok("Tüm gereksinimler kurulu."))
        self.reload()

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Ayarlar")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)
        self.lblStatus = StatusLabel()
        head.addWidget(self.lblStatus)
        layout.addLayout(head)

        # ── İndirme ─────────────────────────────────────────────────────────
        box = QFrame(); box.setObjectName("Panel")
        form = QFormLayout(box)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        row = QHBoxLayout()
        self.txtDir = QLineEdit()
        self.txtDir.setPlaceholderText("İndirilenler klasörü")
        row.addWidget(self.txtDir, 1)
        btnBrowse = QPushButton("Gözat…")
        btnBrowse.clicked.connect(self._pick_dir)
        row.addWidget(btnBrowse)
        holder = QWidget(); holder.setLayout(row)
        form.addRow("İndirme klasörü", holder)

        self.spnParallel = QSpinBox()
        self.spnParallel.setRange(1, 10)
        form.addRow("Paralel indirme", self.spnParallel)

        # Bu ayar `best_video(early_subset=...)`a gidiyor: kaç aday linkin
        # erkenden yoklanacağını belirler. Qt tarafı okuyordu ama yazacak
        # kontrol yoktu — kullanıcı değeri ancak ayarlar.json'ı elle
        # düzenleyerek değiştirebiliyordu.
        self.spnAday = QSpinBox()
        self.spnAday.setRange(1, 30)
        form.addRow("1080p aday sayısı", self.spnAday)
        adayIpucu = QLabel("Kaç video linki denenip en iyisinin seçileceği. "
                           "Yükseltmek kaliteyi artırabilir, aramayı yavaşlatır.")
        adayIpucu.setObjectName("Muted")
        adayIpucu.setWordWrap(True)
        form.addRow("", adayIpucu)

        self.chkMaxRes = QCheckBox("En yüksek çözünürlüğü tercih et")
        form.addRow("", self.chkMaxRes)
        self.chkRemember = QCheckBox("Kaldığım dakikayı hatırla")
        form.addRow("", self.chkRemember)
        self.chkWhileWatching = QCheckBox("İzlerken aynı anda kaydet")
        form.addRow("", self.chkWhileWatching)
        self.chkAria = QCheckBox("aria2c ile indir")
        form.addRow("", self.chkAria)
        layout.addWidget(box)

        # ── Liste görünümü / fansub ─────────────────────────────────────────
        lbox = QFrame(); lbox.setObjectName("Panel")
        lform = QFormLayout(lbox)
        lform.setContentsMargins(16, 14, 16, 14)
        lform.setSpacing(10)
        self.chkWatchedIcon = QCheckBox("Bölüm listesinde izlendi/indirildi rozeti")
        lform.addRow("", self.chkWatchedIcon)
        self.chkManualFansub = QCheckBox("Fansub'u kendim seçeyim")
        lform.addRow("", self.chkManualFansub)
        fansubIpucu = QLabel("Fansub seçimi şimdilik yalnızca komut satırı "
                             "arayüzünde soruluyor; ayar dosyası ikisinde ortak.")
        fansubIpucu.setObjectName("Muted")
        fansubIpucu.setWordWrap(True)
        lform.addRow("", fansubIpucu)
        layout.addWidget(lbox)

        # ── Discord / bakım ─────────────────────────────────────────────────
        dbox = QFrame(); dbox.setObjectName("Panel")
        dl = QVBoxLayout(dbox)
        dl.setContentsMargins(16, 14, 16, 14)
        dl.setSpacing(8)

        self.chkDiscord = QCheckBox("Discord Rich Presence")
        # Anlık etkili: "Kaydet"i beklemek, kullanıcının kapattığı sonra da
        # profilinde görünmeye devam etmesi demek olurdu.
        self.chkDiscord.toggled.connect(self._discord_degisti)
        dl.addWidget(self.chkDiscord)
        self.lblDiscord = QLabel()
        self.lblDiscord.setObjectName("Muted")
        self.lblDiscord.setWordWrap(True)
        dl.addWidget(self.lblDiscord)

        drow = QHBoxLayout()
        self.btnUpdate = QPushButton("Güncellemeleri Denetle")
        self.btnUpdate.clicked.connect(self._guncelleme_denetle)
        drow.addWidget(self.btnUpdate)
        self.btnRequirements = QPushButton("Gereksinimleri Denetle")
        self.btnRequirements.clicked.connect(self._gereksinim_denetle)
        drow.addWidget(self.btnRequirements)
        drow.addStretch(1)
        dl.addLayout(drow)
        layout.addWidget(dbox)

        # ── TRAnimeİzle cookie ──────────────────────────────────────────────
        cbox = QFrame(); cbox.setObjectName("Panel")
        cl = QVBoxLayout(cbox)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(8)

        cl.addWidget(QLabel("TRAnimeİzle oturum çerezi"))
        self.lblCookie = QLabel()
        self.lblCookie.setObjectName("Muted")
        self.lblCookie.setWordWrap(True)
        cl.addWidget(self.lblCookie)

        crow = QHBoxLayout()
        self.btnCookie = QPushButton("Tarayıcıdan Al")
        self.btnCookie.setObjectName("Primary")
        self.btnCookie.clicked.connect(self._fetch_cookie)
        crow.addWidget(self.btnCookie)
        self.btnCookieClear = QPushButton("Temizle")
        self.btnCookieClear.clicked.connect(self._clear_cookie)
        crow.addWidget(self.btnCookieClear)
        crow.addStretch(1)
        cl.addLayout(crow)
        layout.addWidget(cbox)

        # ── OpenAnime jetonları ─────────────────────────────────────────────
        # Kaynak, CDN uçlarının hepsi ölünce kullanıcıya "Ayarlar'dan OpenAnime
        # token'ını girin" diyordu; öyle bir alan yoktu. Var olmayan yere
        # yönlendirmek, hatayı hiç açıklamamaktan kötü.
        obox = QFrame(); obox.setObjectName("Panel")
        ol = QVBoxLayout(obox)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.setSpacing(8)
        ol.addWidget(QLabel("OpenAnime oturumu"))

        oform = QFormLayout()
        oform.setSpacing(8)
        self.txtOpenAniToken = QLineEdit()
        # Jeton hesabın kendisi demek; omuz üstünden okunmasın.
        self.txtOpenAniToken.setEchoMode(QLineEdit.EchoMode.Password)
        self.txtOpenAniToken.setPlaceholderText("token çerezi (opsiyonel)")
        oform.addRow("Token", self.txtOpenAniToken)
        self.txtOpenAniRefresh = QLineEdit()
        self.txtOpenAniRefresh.setEchoMode(QLineEdit.EchoMode.Password)
        self.txtOpenAniRefresh.setPlaceholderText("refreshToken çerezi (opsiyonel)")
        oform.addRow("Refresh Token", self.txtOpenAniRefresh)
        ol.addLayout(oform)

        ohint = QLabel("Boş bırakılabilir. OpenAnime bazı bölümlerde giriş "
                       "yapmış oturum istiyor; stream uçlarının hepsi 404 "
                       "dönüyorsa tarayıcınızdaki openani.me çerezlerini girin.")
        ohint.setObjectName("Muted")
        ohint.setWordWrap(True)
        ol.addWidget(ohint)
        layout.addWidget(obox)

        # ── Bypass ──────────────────────────────────────────────────────────
        bbox = QFrame(); bbox.setObjectName("Panel")
        bform = QFormLayout(bbox)
        bform.setContentsMargins(16, 14, 16, 14)
        self.txtFlare = QLineEdit()
        self.txtFlare.setPlaceholderText("http://host:8191 (boş bırakılabilir)")
        bform.addRow("FlareSolverr", self.txtFlare)
        hint = QLabel("Boş bırakılırsa yalnızca yerel QtWebEngine çözücü kullanılır.")
        hint.setObjectName("Muted")
        bform.addRow("", hint)
        layout.addWidget(bbox)

        # ── AniList ─────────────────────────────────────────────────────────
        abox = QFrame(); abox.setObjectName("Panel")
        al = QVBoxLayout(abox)
        al.setContentsMargins(16, 14, 16, 14)
        al.setSpacing(8)

        al.addWidget(QLabel("AniList hesabı"))
        self.lblAniList = QLabel()
        self.lblAniList.setObjectName("Muted")
        self.lblAniList.setWordWrap(True)
        al.addWidget(self.lblAniList)

        aform = QFormLayout()
        aform.setSpacing(8)
        self.txtAniListId = QLineEdit()
        self.txtAniListId.setPlaceholderText("AniList uygulama Client ID")
        aform.addRow("Client ID", self.txtAniListId)

        self.txtAniListSecret = QLineEdit()
        # Gizli anahtar omuz üstünden okunmasın; hiçbir log/hata satırına da
        # yazılmıyor (bkz. `AniListService.giris_yap`).
        self.txtAniListSecret.setEchoMode(QLineEdit.EchoMode.Password)
        self.txtAniListSecret.setPlaceholderText("Client Secret (opsiyonel)")
        aform.addRow("Client Secret", self.txtAniListSecret)

        self.lblAniListSecretIpucu = QLabel(
            "Client Secret opsiyoneldir: boş bırakılırsa giriş, secret "
            "gerektirmeyen Implicit akışla yapılır. Yalnızca kendi AniList "
            "uygulamanızı Authorization Code akışıyla kullanacaksanız doldurun.")
        self.lblAniListSecretIpucu.setObjectName("Muted")
        self.lblAniListSecretIpucu.setWordWrap(True)
        aform.addRow("", self.lblAniListSecretIpucu)

        self.txtAniListRedirect = QLineEdit()
        self.txtAniListRedirect.setPlaceholderText(
            "http://localhost:9921/anilist-login")
        aform.addRow("Redirect URI", self.txtAniListRedirect)
        al.addLayout(aform)

        ahint = QLabel("Redirect URI, AniList geliştirici panelindekiyle birebir "
                       "aynı olmalı; portu yerel giriş sunucusu dinler.")
        ahint.setObjectName("Muted")
        ahint.setWordWrap(True)
        al.addWidget(ahint)

        arow = QHBoxLayout()
        self.btnAniListLogin = QPushButton("AniList'e Giriş Yap")
        self.btnAniListLogin.setObjectName("Primary")
        self.btnAniListLogin.clicked.connect(self._anilist_login)
        arow.addWidget(self.btnAniListLogin)
        self.btnAniListLogout = QPushButton("Çıkış Yap")
        self.btnAniListLogout.clicked.connect(self._anilist_logout)
        arow.addWidget(self.btnAniListLogout)
        arow.addStretch(1)
        al.addLayout(arow)
        layout.addWidget(abox)

        actions = QHBoxLayout()
        actions.addStretch(1)
        btnSave = QPushButton("Kaydet")
        btnSave.setObjectName("Primary")
        btnSave.clicked.connect(self.save)
        actions.addWidget(btnSave)
        layout.addLayout(actions)

        layout.addStretch(1)

    # ── Ayar okuma/yazma ────────────────────────────────────────────────────
    @staticmethod
    def _dosya():
        from ....cli.dosyalar import Dosyalar
        return Dosyalar()

    def reload(self) -> None:
        """Ayarları diskten oku, forma yerleştir ve kaynak kimliklerini uygula."""
        try:
            ayarlar: Dict[str, Any] = self._dosya().ayarlar or {}
        except Exception as exc:
            self.lblStatus.error(f"Ayarlar okunamadı: {exc}")
            return
        self.txtDir.setText(str(ayarlar.get("indirilenler") or ""))
        self.spnParallel.setValue(int(ayarlar.get("paralel indirme sayisi") or 3))
        # Ayar sözlüğünden değil `prefs`ten: eski Türkçe ada düşme kuralı orada
        # yaşıyor, burada kopyalansa iki yer ayrışırdı (bkz. `prefs._aday_sayisi`).
        self.spnAday.setValue(prefs.oku().aday_sayisi)
        self.chkMaxRes.setChecked(bool(ayarlar.get("max resolution", True)))
        self.chkRemember.setChecked(bool(ayarlar.get("dakika hatirla", True)))
        self.chkWhileWatching.setChecked(bool(ayarlar.get("izlerken kaydet", False)))
        self.chkAria.setChecked(bool(ayarlar.get("aria2c kullan", False)))
        self.chkWatchedIcon.setChecked(bool(ayarlar.get("izlendi ikonu", True)))
        self.chkManualFansub.setChecked(bool(ayarlar.get("manuel fansub", False)))
        self.txtFlare.setText(str(ayarlar.get("flaresolverr_url") or ""))
        self.txtOpenAniToken.setText(str(ayarlar.get("openani_token") or ""))
        self.txtOpenAniRefresh.setText(str(ayarlar.get("openani_refresh_token") or ""))
        self._show_cookie_state(str(ayarlar.get("tranime_cookie") or ""))
        self._reload_discord(bool(ayarlar.get("discord_rich_presence", True)))
        self._reload_anilist()
        # Kaynak modülleri çerez/jetonu süreç-içi global'de tutuyor ve her süreç
        # boş başlıyor; diskteki değer buradan içeri girmezse TRAnimeİzle her
        # açılışta 0 bölüm döndürür (bkz. modül başlığı).
        if not self._kimlikleri_uygula():
            self.lblStatus.error(
                "Kaynak çerez/jetonları uygulanamadı; TRAnimeİzle ve OpenAnime "
                "bölüm döndürmeyebilir.")

    @staticmethod
    def _kimlikleri_uygula() -> bool:
        """Çerez/jetonu kaynak modüllerinin süreç-içi global'lerine bas."""
        return prefs.kaynak_kimliklerini_uygula()

    def save(self) -> None:
        try:
            self._dosya().set_ayar(ayar_list={
                "indirilenler": self.txtDir.text().strip(),
                "paralel indirme sayisi": self.spnParallel.value(),
                # ASCII ad kanonik; eski Türkçe ad `Dosyalar` açılışında göç
                # ediyor (bkz. `dosyalar.ESKI_AYAR_ADLARI`).
                "1080p aday sayisi": self.spnAday.value(),
                "max resolution": self.chkMaxRes.isChecked(),
                "dakika hatirla": self.chkRemember.isChecked(),
                "izlerken kaydet": self.chkWhileWatching.isChecked(),
                "aria2c kullan": self.chkAria.isChecked(),
                "izlendi ikonu": self.chkWatchedIcon.isChecked(),
                "manuel fansub": self.chkManualFansub.isChecked(),
                "flaresolverr_url": self.txtFlare.text().strip(),
                "openani_token": self.txtOpenAniToken.text().strip(),
                "openani_refresh_token": self.txtOpenAniRefresh.text().strip(),
            })
        except Exception as exc:
            self.lblStatus.error(f"Kaydedilemedi: {exc}")
            return
        # Bypass oturumu adresi kurulumda okuyor; sıfırlanmazsa değişiklik
        # (özellikle "boşalt = kullanma") ancak yeniden başlatınca etkili olurdu.
        try:
            from ....common.cf_bypass import reset_cf_session
            reset_cf_session()
        except Exception:
            pass
        if not self._save_anilist():
            self.lblStatus.error("Ayarlar kaydedildi ama AniList yapılandırması yazılamadı.")
            return
        # Jeton diske yazıldı; süreç içindeki kopyası da tazelenmeli, yoksa
        # değişiklik ancak yeniden başlatınca etkili olurdu.
        if not self._kimlikleri_uygula():
            self.lblStatus.error(
                "Ayarlar kaydedildi ama kaynak çerez/jetonları uygulanamadı.")
            return
        self.lblStatus.ok("Ayarlar kaydedildi.")

    def _pick_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "İndirme klasörü seç", self.txtDir.text() or "")
        if chosen:
            self.txtDir.setText(chosen)

    # ── TRAnime cookie ──────────────────────────────────────────────────────
    def _show_cookie_state(self, netscape: str) -> None:
        if netscape and ".AitrWeb.Session" in netscape:
            self.lblCookie.setText("Oturum çerezi kayıtlı ✓")
            self.lblCookie.setStyleSheet("color: #00b894;")
        else:
            self.lblCookie.setText(
                "Çerez yok — TRAnimeİzle bot kontrolü nedeniyle bölüm döndürmez. "
                "“Tarayıcıdan Al” ile kontrolü çözün.")
            self.lblCookie.setStyleSheet("")

    def _fetch_cookie(self) -> None:
        from ..cookie_browser import CookieBrowserWorker, is_available
        if not is_available():
            self.lblStatus.error("QtWebEngine yok (PySide6-Addons kurulu mu?).")
            return
        if self._cookie_worker is not None and self._cookie_worker.is_running:
            self.lblStatus.info("Tarayıcı zaten açık.")
            return

        self.btnCookie.setEnabled(False)
        self.lblStatus.info("Tarayıcı açılıyor…")
        self._cookie_worker = CookieBrowserWorker(
            on_status=self.lblStatus.info,
            on_cookies=self._on_cookie_ready,
            on_error=self._on_cookie_error,
            parent=self,
        )
        self._cookie_worker.start()

    def _on_cookie_ready(self, netscape: str) -> None:
        try:
            self._dosya().set_ayar("tranime_cookie", netscape)  # kalıcı
            # Süreç içi kopya diskten okunarak tazeleniyor: doğrudan `netscape`
            # basmak, disk ile bellek arasında sessiz bir ayrışmaya kapı açardı.
            self._kimlikleri_uygula()
        except Exception as exc:
            self.lblStatus.error(f"Çerez kaydedilemedi: {exc}")
            self.btnCookie.setEnabled(True)
            return
        self._show_cookie_state(netscape)
        self.lblStatus.ok("TRAnimeİzle çerezi alındı ve kaydedildi.")
        self.btnCookie.setEnabled(True)

    def _on_cookie_error(self, message: str) -> None:
        self.lblStatus.error(message)
        self.btnCookie.setEnabled(True)

    def _clear_cookie(self) -> None:
        try:
            self._dosya().set_ayar("tranime_cookie", "")
        except Exception as exc:
            self.lblStatus.error(f"Temizlenemedi: {exc}")
            return
        # Yalnızca diski temizlemek yetmez: kaynak modülü çerezi süreç-içi
        # global'de tutuyor, yeniden başlatana kadar eskisiyle istek atardı.
        self._kimlikleri_uygula()
        self._show_cookie_state("")
        self.lblStatus.info("Çerez temizlendi.")

    # ── Discord / bakım ─────────────────────────────────────────────────────
    def _reload_discord(self, acik: bool) -> None:
        """Anahtarı ayardan doldur (sinyali tetiklemeden) ve durumu yaz."""
        from ..discord import kullanilabilir
        self.chkDiscord.blockSignals(True)
        self.chkDiscord.setChecked(acik)
        self.chkDiscord.blockSignals(False)
        if not kullanilabilir():
            # Anahtar yine de kullanılabilir kalır: kullanıcı pypresence'ı sonra
            # kurabilir, tercihi şimdiden kaydedebilsin.
            self.lblDiscord.setText(
                "pypresence kurulu değil — özellik kapalı (pip install pypresence).")
        elif self.discord is not None and self.discord.bagli:
            self.lblDiscord.setText("Discord'a bağlı ✓")
        else:
            self.lblDiscord.setText(
                "Discord açık değilse bağlantı kurulmaz; uygulama etkilenmez.")

    def _discord_degisti(self, acik: bool) -> None:
        if not prefs.ayar_yaz(discord_rich_presence=bool(acik)):
            self.lblStatus.error("Discord ayarı kaydedilemedi.")
            return
        if self.discord is not None:
            self.discord.ayar_uygula()      # anında bağlan/kop
        self._reload_discord(bool(acik))
        self.lblStatus.info("Discord Rich Presence "
                            + ("açıldı." if acik else "kapatıldı."))

    def _guncelleme_denetle(self) -> None:
        if self.updates is None:
            return
        self.lblStatus.info("Güncellemeler denetleniyor…")
        self.updates.kontrol_et(sessiz=False)

    def _gereksinim_denetle(self) -> None:
        """Elle denetim: "Atla" tercihini de geri alır."""
        if self.requirements is None:
            return
        self.requirements.atlandi_yaz(False)
        self.lblStatus.info("Gereksinimler denetleniyor…")
        self.requirements.denetle(kullanici_istegi=True)

    # ── AniList ─────────────────────────────────────────────────────────────
    # Sızmış secret uyarısının metni; testler de bunu arıyor.
    SIZAN_SECRET_UYARISI = (
        "Eski sürümlerden kalan, herkese açık depoya sızmış Client Secret "
        "yapılandırmanızdan silindi. Giriş artık secret gerektirmeyen Implicit "
        "akışla yapılıyor; bir şey yapmanıza gerek yok.")

    def _reload_anilist(self) -> None:
        ayar = prefs.anilist_oku()
        self.txtAniListId.setText(ayar.client_id)
        self.txtAniListSecret.setText(ayar.client_secret)
        self.txtAniListRedirect.setText(ayar.redirect_uri)
        if ayar.sizan_secret_temizlendi:
            # Sessiz temizlik kullanıcıyı "secret'ım nereye gitti?" sorusuyla
            # baş başa bırakırdı; alanın dibinde açıkça yazıyor.
            self.lblAniListSecretIpucu.setText(self.SIZAN_SECRET_UYARISI)
            self.lblAniListSecretIpucu.setStyleSheet("color: #e17055;")
        self._show_anilist_state(self.servis.kullanici)

    def _save_anilist(self) -> bool:
        """OAuth üçlüsünü istemciye yaz (`ayarlar.json`'a değil)."""
        return prefs.anilist_yaz(self.txtAniListId.text(),
                                 self.txtAniListSecret.text(),
                                 self.txtAniListRedirect.text())

    def _show_anilist_state(self, user: Any) -> None:
        if isinstance(user, dict) and user.get("name"):
            self.lblAniList.setText(f"Giriş yapıldı: {user['name']} ✓")
            self.lblAniList.setStyleSheet("color: #00b894;")
        elif self.servis.giris_var_mi():
            self.lblAniList.setText("Jeton kayıtlı, kullanıcı bilgisi bekleniyor…")
            self.lblAniList.setStyleSheet("")
        else:
            self.lblAniList.setText(
                "Giriş yapılmamış — İzleme Listesi ve ilerleme senkronu için giriş yapın.")
            self.lblAniList.setStyleSheet("")

    def _on_auth(self, user: Any) -> None:
        self._show_anilist_state(user)

    def _anilist_login(self) -> None:
        """Önce ekrandaki OAuth bilgilerini kaydet, sonra tarayıcıyı aç.

        Kaydetmeden başlatmak, kullanıcının az önce yapıştırdığı Client ID'yi
        yok sayıp eski (çoğu zaman boş) yapılandırmayla giriş denemek olurdu.
        """
        if not self._save_anilist():
            self.lblStatus.error("AniList yapılandırması kaydedilemedi.")
            return
        if self.servis.giris_yap():
            self.lblStatus.info("Tarayıcıda AniList girişini tamamlayın…")

    def _anilist_logout(self) -> None:
        self.servis.cikis_yap()
        self._show_anilist_state(None)
        self.lblStatus.info("AniList oturumu kapatıldı.")


__all__ = ["SettingsPage"]
