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

Cookie alındıktan sonra **oturum kimliği bağışı** teklif edilebiliyor. Buradaki
sıralama bilinçli: çerez ÖNCE diske yazılır, bağış SONRA sorulur. Bağış
diyaloğu ya da sunucu ne yaparsa yapsın kullanıcının kendi çerezi elinde kalır;
bağışın başarısızlığı "kontrolü boşuna çözdüm" demek olmaz. Teklifin kendisi de
iki kapıdan geçer: "kimlik paylas" ayarı açık olacak (varsayılan kapalı) VE
kullanıcı diyaloğu onaylayacak — ayar tek başına hiçbir şey göndertmez.

**Çevrimdışı arşiv (TürkAnime)**: turkanime.tv kapandı; "TürkAnime" kaynağı
sitenin statik arşivinden okunuyor (`sources/animedepo.py`). Bölüm hangi
konumun etkin olduğunu (ortam değişkeni / seçilen klasör / indirilen tam arşiv /
depodaki `arsiv/` / uzak ayna), kaç anime olduğunu ve dizinin tarihini
gösterir; tam arşivi indirir, günceller, siler ya da elle bir klasör
gösterilmesini sağlar. Kural: sayfa KURULURKEN ne ağa çıkar ne diske yüklenir —
ana pencere açılışta bütün sayfaları kuruyor ve megabaytlık `dizin.json`
ayrıştırmak açılışı geciktirirdi. Durum sayfa ilk GÖSTERİLDİĞİNDE arka planda
hesaplanır (`showEvent` → `arsiv_durumunu_tazele`); indirme/silme/klasör
denetimi de arka planda koşar, sonuç `UiBridge` ile GUI thread'ine taşınır.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
)

from ....common import arsiv_paketi as paket
from ....sources import animedepo
from .. import prefs
from ..anilist import AniListService
from ..widgets import StatusLabel
from ..workers import UiBridge, run_bg

# Etkin arşiv konumunun (`animedepo.ArsivKonumu.kaynak`) kullanıcıya görünen adı.
ARSIV_KONUM_ADLARI = {
    "ortam": f"Ortam değişkeni ({animedepo.DIZIN_ORTAM_ANAHTARI})",
    "ayar": "Ayarlarda seçilen klasör",
    "indirilen": "İndirilmiş tam arşiv",
    "depo": "Depodaki arşiv klasörü (arsiv/)",
    "uzak": "Uzak ayna (internet gerekir)",
}
# Boyut ölçüldü: `arsiv/`'in tar.gz'si 230,8 MB (açılınca ~0,5 GB). GitLab
# paketi anında üretildiği için boyut başlığı gelmiyor; kullanıcı neye
# başladığını düğmeden bilsin.
TAM_ARSIV_DUGMESI = "Tüm arşivi indir (~230 MB)"
ARSIV_GUNCELLE_DUGMESI = "Arşivi güncelle"
TAM_ARSIV_BOYUTU_MB = 230
# Paket 64 KB'lık parçalarla akıyor: ~3600 ilerleme çağrısı. Her birini GUI'ye
# taşımak olay kuyruğunu boğar; saniyede ~10 güncelleme göze yetiyor.
ILERLEME_ARALIGI = 0.1
UYARI_RENGI = "#e17055"


class SettingsPage(QWidget):
    """İndirme klasörü, TRAnime cookie'si, bypass, AniList ve çevresel servisler."""

    # Arşiv durumu okunup panele yazıldı (`animedepo.ArsivDurumu`). Başka
    # sayfalar arşiv konumu değişince tazelenmek isterse buna bağlanır.
    arsiv_durumu_yenilendi = Signal(object)

    def __init__(self, servis: Optional[AniListService] = None,
                 parent: Optional[QWidget] = None, discord=None, updates=None,
                 requirements=None):
        super().__init__(parent)
        self._cookie_worker = None
        # Arşiv bölümünün durumu. `_arsiv_nesil`: her durum isteği bir numara
        # alır, geç dönen eski sonuç yenisinin üstüne yazamaz. `_arsiv_mesgul`:
        # None | "indirme" | "islem" (klasör denetimi, silme) — aynı anda tek iş.
        self._ui = UiBridge(self)
        self._arsiv_nesil = 0
        self._arsiv_durumu: Optional[animedepo.ArsivDurumu] = None
        self._arsiv_mesgul: Optional[str] = None
        self._arsiv_iptal: Optional[threading.Event] = None
        self._arsiv_kaynak_adi = ""
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
        dis = QVBoxLayout(self)
        dis.setContentsMargins(24, 20, 24, 20)
        dis.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Ayarlar")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)
        self.lblStatus = StatusLabel()
        head.addWidget(self.lblStatus)
        dis.addLayout(head)

        # Bölümler kaydırılabilir alanda, başlık (ve durum satırı) dışında:
        # dokuz panel küçük ekranda pencereye sığmıyor, kaydırma olmadan sayfa
        # pencerenin asgari yüksekliğini ekrandan büyük yapardı. Durum satırı
        # sabit kalıyor ki "Kaydet"in sonucu her zaman görünsün.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        icerik = QWidget()
        layout = QVBoxLayout(icerik)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        self.scroll.setWidget(icerik)
        dis.addWidget(self.scroll, 1)

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

        # ── Çevrimdışı arşiv (TürkAnime) ────────────────────────────────────
        layout.addWidget(self._build_arsiv())

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

        # ── Oturum kimliği bağışı ───────────────────────────────────────────
        kbox = QFrame(); kbox.setObjectName("Panel")
        kl = QVBoxLayout(kbox)
        kl.setContentsMargins(16, 14, 16, 14)
        kl.setSpacing(8)

        kl.addWidget(QLabel("Oturum kimliği bağışı"))
        khint = QLabel(
            "Kapalıyken hiçbir kimlik gönderilmez. Açarsanız, çerez her "
            "alındığında ne bağışladığınızı anlatan bir onay penceresi çıkar; "
            "gönderim yalnızca o pencereyi onaylarsanız yapılır.")
        khint.setObjectName("Muted")
        khint.setWordWrap(True)
        kl.addWidget(khint)

        self.chkKimlikPaylas = QCheckBox(
            "Çerez aldığımda oturum kimliğimi bağışlamayı sor")
        kl.addWidget(self.chkKimlikPaylas)

        kform = QFormLayout()
        kform.setSpacing(8)
        self.txtSunucu = QLineEdit()
        self.txtSunucu.setPlaceholderText("https://sunucu.example (boş = kapalı)")
        kform.addRow("Sunucu adresi", self.txtSunucu)
        self.txtSunucuAnahtar = QLineEdit()
        # Anahtar omuz üstünden okunmasın; adres/anahtar ikisi de boşken bağış
        # ucu istemci tarafında zaten kapalı (bkz. `katki_dialog._uc`).
        self.txtSunucuAnahtar.setEchoMode(QLineEdit.EchoMode.Password)
        self.txtSunucuAnahtar.setPlaceholderText("Sunucu API anahtarı")
        kform.addRow("API anahtarı", self.txtSunucuAnahtar)
        kl.addLayout(kform)

        self.lblKimlik = QLabel()
        self.lblKimlik.setObjectName("Muted")
        self.lblKimlik.setWordWrap(True)
        kl.addWidget(self.lblKimlik)

        krow = QHBoxLayout()
        self.btnBagisGeriCek = QPushButton("Bağışımı geri çek")
        self.btnBagisGeriCek.clicked.connect(self._bagis_geri_cek)
        krow.addWidget(self.btnBagisGeriCek)
        krow.addStretch(1)
        kl.addLayout(krow)
        layout.addWidget(kbox)

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

    def _build_arsiv(self) -> QFrame:
        """"Çevrimdışı arşiv (TürkAnime)" paneli — yalnızca widget'lar.

        Burada disk/ağ YOK: durum, sayfa gösterilince arka planda dolar.
        """
        rbox = QFrame(); rbox.setObjectName("Panel")
        rl = QVBoxLayout(rbox)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(8)

        rl.addWidget(QLabel("Çevrimdışı arşiv (TürkAnime)"))
        aciklama = QLabel(
            "turkanime.tv kapandı; TürkAnime kaynağı sitenin arşivinden okunur. "
            "Tüm arşivi indirirseniz TürkAnime araması ve bölüm listeleri "
            "internetsiz çalışır. Videolar yine üçüncü parti sunuculardan "
            "(ok.ru, Sibnet, Mail.ru…) gelir.")
        aciklama.setObjectName("Muted")
        aciklama.setWordWrap(True)
        rl.addWidget(aciklama)

        rform = QFormLayout()
        rform.setSpacing(6)
        self.lblArsivKonum = QLabel("—")
        rform.addRow("Etkin konum", self.lblArsivKonum)
        self.lblArsivYer = QLabel("—")
        self.lblArsivYer.setWordWrap(True)
        # Yol/adres kopyalanabilsin: kullanıcı onu dosya yöneticisinde açmak
        # ya da hata bildirirken yapıştırmak isteyecek.
        self.lblArsivYer.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        rform.addRow("Yer", self.lblArsivYer)
        self.lblArsivIcerik = QLabel("Ayarlar açılınca okunur.")
        self.lblArsivIcerik.setWordWrap(True)
        rform.addRow("İçerik", self.lblArsivIcerik)
        rl.addLayout(rform)

        self.lblArsivUyari = QLabel()
        self.lblArsivUyari.setWordWrap(True)
        self.lblArsivUyari.setStyleSheet(f"color: {UYARI_RENGI};")
        self.lblArsivUyari.setVisible(False)
        rl.addWidget(self.lblArsivUyari)

        prow = QHBoxLayout()
        self.prgArsiv = QProgressBar()
        self.prgArsiv.setTextVisible(False)   # metin ayrı etikette (belirsiz kipte görünmüyor)
        prow.addWidget(self.prgArsiv, 1)
        self.btnArsivIptal = QPushButton("İptal")
        self.btnArsivIptal.clicked.connect(self._arsiv_iptal_et)
        prow.addWidget(self.btnArsivIptal)
        rl.addLayout(prow)
        self.lblArsivIlerleme = QLabel()
        self.lblArsivIlerleme.setObjectName("Muted")
        self.lblArsivIlerleme.setWordWrap(True)
        rl.addWidget(self.lblArsivIlerleme)

        rrow = QHBoxLayout()
        self.btnArsivIndir = QPushButton(TAM_ARSIV_DUGMESI)
        self.btnArsivIndir.setObjectName("Primary")
        self.btnArsivIndir.clicked.connect(self._arsiv_indir)
        rrow.addWidget(self.btnArsivIndir)
        self.btnArsivKlasor = QPushButton("Klasör seç…")
        self.btnArsivKlasor.setToolTip(
            "Elinizdeki bir arşiv kopyasını gösterin (içinde dizin.json olmalı).")
        self.btnArsivKlasor.clicked.connect(self._arsiv_klasor_sec)
        rrow.addWidget(self.btnArsivKlasor)
        self.btnArsivVarsayilan = QPushButton("Varsayılana dön")
        self.btnArsivVarsayilan.setToolTip(
            "Seçilen klasörü unut; arşiv varsayılan sırayla aransın.")
        self.btnArsivVarsayilan.clicked.connect(self._arsiv_varsayilana_don)
        rrow.addWidget(self.btnArsivVarsayilan)
        self.btnArsivSil = QPushButton("İndirilen arşivi sil")
        self.btnArsivSil.clicked.connect(self._arsiv_sil)
        rrow.addWidget(self.btnArsivSil)
        rrow.addStretch(1)
        rl.addLayout(rrow)

        # Arşiv işlemlerinin sonucu panelin içinde: sayfa kaydırılabilir ve
        # kullanıcı düğmeye bastığı yere bakıyor.
        self.lblArsivDurum = StatusLabel()
        rl.addWidget(self.lblArsivDurum)

        self._arsiv_dugmelerini_guncelle()
        return rbox

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
        # Bağış anahtarının varsayılanı KAPALI: ayar dosyasında hiç yoksa
        # (eski kurulum) açık görünmemeli.
        self.chkKimlikPaylas.setChecked(bool(ayarlar.get("kimlik paylas", False)))
        self.txtSunucu.setText(str(ayarlar.get("sunucu adresi") or ""))
        self.txtSunucuAnahtar.setText(str(ayarlar.get("sunucu api anahtari") or ""))
        self._show_kimlik_state(self._bagis_kimlikleri(ayarlar))
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
                "kimlik paylas": self.chkKimlikPaylas.isChecked(),
                "sunucu adresi": self.txtSunucu.text().strip(),
                "sunucu api anahtari": self.txtSunucuAnahtar.text().strip(),
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
        # Çerez ZATEN kaydedildi; bağış bundan sonra ve tamamen ayrı bir karar.
        self._kimlik_bagisi_teklif(netscape)

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

    # ── Oturum kimliği bağışı ───────────────────────────────────────────────
    @staticmethod
    def _katki():
        """`katki_dialog` modülü.

        Modül olarak alınıyor (fonksiyon olarak değil): testler `bagis_gonder`/
        `onay_al`'ı modül üzerinde değiştirebilsin, sayfa da her çağrıda güncel
        hâlini görsün. Import fonksiyon içinde çünkü modül QtWidgets çekiyor.
        """
        from .. import katki_dialog
        return katki_dialog

    @staticmethod
    def _bagis_kimlikleri(ayarlar: Dict[str, Any]) -> List[str]:
        """Ayardaki bağış numaralarını LİSTE olarak ver.

        Ayar neden liste: çerezin süresi dolduğunda kullanıcı yeniden bağış
        yapar — zaten bu özelliğin var olma sebebi bu. Numara tek bir dizgede
        tutulunca ikinci bağış birincinin üstüne yazıyordu ve eski numara yok
        oluyordu. O numara geri çekmenin TEK anahtarı (sunucu bağışçıyı
        tanımıyor, "bağışlarımı listele" ucu yok ve olamaz), dolayısıyla ilk
        bağış 30 günlük ömrünü doldurana kadar geri çekilemez kalıyordu —
        oysa onay metni "istediğin an geri çekebilirsin" diye söz veriyor.

        Eski kurulumlarda değer düz dizgi; okurken listeye çevriliyor ki
        10.0.0'dan yükselen kullanıcının numarası kaybolmasın.
        """
        ham = ayarlar.get("kimlik bagis id") or []
        if isinstance(ham, str):
            ham = [ham] if ham.strip() else []
        return [str(x).strip() for x in ham if str(x).strip()]

    def _show_kimlik_state(self, kimlikler: Any) -> None:
        """Bağış durumunu yaz ve geri çekme düğmesini ona göre aç/kapa."""
        if isinstance(kimlikler, str):
            kimlikler = [kimlikler] if kimlikler.strip() else []
        kimlikler = [str(k).strip() for k in (kimlikler or []) if str(k).strip()]
        self.btnBagisGeriCek.setEnabled(bool(kimlikler))
        if len(kimlikler) == 1:
            self.lblKimlik.setText(
                f"Bağış yapıldı — numara: {kimlikler[0]}. "
                "İstediğiniz an geri çekebilirsiniz.")
            self.lblKimlik.setStyleSheet("color: #00b894;")
        elif kimlikler:
            # Çoğul hâl gizlenmiyor: kullanıcı kaç kaydı olduğunu bilmeli,
            # "geri çek" düğmesi hepsini birden siliyor.
            self.lblKimlik.setText(
                f"{len(kimlikler)} bağış kaydı var — numaralar: "
                + ", ".join(kimlikler)
                + ". \"Bağışımı geri çek\" hepsini birden siler.")
            self.lblKimlik.setStyleSheet("color: #00b894;")
        else:
            self.lblKimlik.setText("Bağışlanmış oturum kimliği yok.")
            self.lblKimlik.setStyleSheet("")

    def _kimlik_bagisi_teklif(self, netscape: str) -> None:
        """Ayar açıksa onay diyaloğunu göster; onay yoksa HİÇBİR ŞEY gönderme.

        Erken dönüşlerin sırası önemli: ağa çıkan tek satır (`bagis_gonder`)
        hem ayar hem onay kapısının ardındadır ve arada başka bir çıkış yolu
        yoktur.
        """
        if not netscape:
            return
        try:
            ayarlar: Dict[str, Any] = self._dosya().ayarlar or {}
        except Exception:
            return                      # ayar okunamıyorsa teklif de etme
        if not bool(ayarlar.get("kimlik paylas", False)):
            return                      # kapalı: diyalog bile açılmaz
        katki = self._katki()
        if not katki.onay_al(katki.KAYNAK_TRANIME, self):
            self.lblStatus.info("Oturum kimliği bağışlanmadı.")
            return
        try:
            bagis_id = katki.bagis_gonder(netscape, katki.KAYNAK_TRANIME, ayarlar)
        except Exception as exc:
            self.lblStatus.error(f"Kimlik bağışı gönderilemedi: {exc}")
            return

        # Gönderim ile kaydetme AYRI try blokları. Eskiden aynı bloktaydılar:
        # `bagis_gonder` başarılı olup `set_ayar` düşerse kullanıcıya
        # "gönderilemedi" deniyordu — yalan. Bağış sunucudaydı, numarası ise
        # hiçbir yerde. Artık kaydetme düşerse numara EKRANA yazılıyor;
        # kullanıcının onu bir yere not edip sonra geri çekme şansı olsun.
        kimlikler = self._bagis_kimlikleri(ayarlar)
        if bagis_id not in kimlikler:
            kimlikler.append(bagis_id)
        try:
            self._dosya().set_ayar("kimlik bagis id", kimlikler)
        except Exception as exc:
            self.lblStatus.error(
                f"Bağış SUNUCUYA ULAŞTI ama numarası kaydedilemedi ({exc}). "
                f"Geri çekebilmek için bu numarayı saklayın: {bagis_id}")
            self._show_kimlik_state(kimlikler)
            return
        self._show_kimlik_state(kimlikler)
        self.lblStatus.ok("Oturum kimliği bağışlandı. "
                          "Geri çekmek için “Bağışımı geri çek”.")

    def _bagis_geri_cek(self) -> None:
        """Bağışı sunucudan sil, sonra numarayı ayardan düş.

        Sıra tersine dönemez: numara önce silinseydi ve silme isteği düşseydi
        kayıt sunucuda kalır, kullanıcının onu silecek anahtarı ise kaybolurdu.
        """
        try:
            ayarlar: Dict[str, Any] = self._dosya().ayarlar or {}
        except Exception as exc:
            self.lblStatus.error(f"Ayarlar okunamadı: {exc}")
            return
        kimlikler = self._bagis_kimlikleri(ayarlar)
        if not kimlikler:
            self.lblStatus.info("Geri çekilecek bağış yok.")
            return

        # Hepsi tek tek deneniyor; biri düşerse ötekiler yine de silinsin.
        # Silinemeyen numara ayarda KALIR — atılırsa o kayıt bir daha geri
        # çekilemez, çünkü numara geri çekmenin tek anahtarı.
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
            self.lblStatus.error(
                f"Bağış(lar) sunucudan silindi ama numara yerelde kaldı: {exc}")
            return
        self._show_kimlik_state(kalan)
        if hatalar:
            silinen = len(kimlikler) - len(kalan)
            # Hiçbiri silinemediyse "0/1 bağış geri çekildi" demek anlamsız;
            # kullanıcının duyması gereken şey işlemin olmadığı.
            bas = ("Bağış geri çekilemedi." if silinen == 0 else
                   f"{silinen}/{len(kimlikler)} bağış geri çekildi.")
            self.lblStatus.error(
                bas + " Geri çekilemeyenlerin numarası saklandı, tekrar "
                "deneyebilirsiniz: " + "; ".join(hatalar))
            return
        self.lblStatus.ok("Bağışınız geri çekildi ve sunucudan silindi."
                          if len(kimlikler) == 1 else
                          f"{len(kimlikler)} bağış geri çekildi ve sunucudan silindi.")

    # ── Çevrimdışı arşiv (TürkAnime) ────────────────────────────────────────
    def showEvent(self, event) -> None:  # noqa: N802 (Qt imzası)
        """Arşiv durumunu sayfa GÖRÜNÜNCE tazele (kurulumda değil).

        Ana pencere bütün sayfaları açılışta kuruyor; durumu kurulumda okumak
        her açılışta megabaytlık `dizin.json`'ı ayrıştırmak demekti — kullanıcı
        Ayarlar'a hiç girmese bile. Her gösterimde yeniden okunuyor çünkü
        konum arada değişebilir (başka pencereden indirme, elle silinen klasör);
        yerel dizin önbellekte olduğundan ikinci okuma ucuz.
        """
        super().showEvent(event)
        self.arsiv_durumunu_tazele()

    def _gui(self, fn: Callable[[], Any]) -> None:
        """Arka plan thread'inden GUI thread'ine iş gönder (`after(0, fn)`).

        Sayfa (ve köprüsü) iş bitmeden yok edilmişse `emit` RuntimeError
        fırlatır. Gösterecek pencere kalmadığı için yutmak doğru; yutulmazsa
        kapanışta thread yığın izi basar.
        """
        try:
            self._ui.post(fn)
        except RuntimeError:
            pass

    def arsiv_durumunu_tazele(self) -> None:
        """Etkin arşiv konumunu ve içeriğini arka planda oku, panele yaz."""
        self._arsiv_nesil += 1
        nesil = self._arsiv_nesil
        if self._arsiv_durumu is None:
            self.lblArsivIcerik.setText("Okunuyor…")
        run_bg(self._arsiv_durumu_is, nesil)

    def _arsiv_durumu_is(self, nesil: int) -> None:
        """ARKA PLAN: `animedepo.arsiv_durumu` ağa çıkmaz ama diske yüklenir."""
        try:
            durum = animedepo.arsiv_durumu()
        except Exception as exc:          # kullanıcıya gösteriliyor (sessiz değil)
            mesaj = str(exc) or type(exc).__name__
            self._gui(lambda: self._arsiv_durumu_hatasi(nesil, mesaj))
            return
        self._gui(lambda: self._arsiv_durumu_geldi(nesil, durum))

    def _arsiv_durumu_hatasi(self, nesil: int, mesaj: str) -> None:
        if nesil != self._arsiv_nesil:
            return
        self.lblArsivIcerik.setText("Okunamadı.")
        self.lblArsivDurum.error(f"Arşiv durumu okunamadı: {mesaj}")

    def _arsiv_durumu_geldi(self, nesil: int, durum: Any) -> None:
        if nesil != self._arsiv_nesil:
            return                       # geç dönen eski istek — yenisini ezmesin
        self._arsiv_durumu = durum
        self.lblArsivKonum.setText(ARSIV_KONUM_ADLARI.get(durum.kaynak, durum.kaynak))
        self.lblArsivYer.setText(durum.adres)
        self.lblArsivIcerik.setText(self._arsiv_icerik_metni(durum))
        uyarilar = self._arsiv_uyarilari(durum)
        self.lblArsivUyari.setText("\n".join(uyarilar))
        self.lblArsivUyari.setVisible(bool(uyarilar))
        self._arsiv_dugmelerini_guncelle()
        self.arsiv_durumu_yenilendi.emit(durum)

    @staticmethod
    def _arsiv_icerik_metni(durum: Any) -> str:
        """"6.098 anime · son güncelleme 14.09.2026" biçiminde özet."""
        if durum.anime_sayisi is None:
            if durum.konum.yerel:
                return "dizin.json okunamadı."
            # Durum göstermek için ağa çıkılmıyor (bkz. `animedepo.arsiv_durumu`).
            return "Henüz okunmadı — ilk TürkAnime aramasında aynadan gelecek."
        sayi = f"{durum.anime_sayisi:,}".replace(",", ".")
        tarih = _tarih_metni(durum.son_guncelleme)
        metin = f"{sayi} anime · " + (f"son güncelleme {tarih}" if tarih
                                     else "güncelleme tarihi bilinmiyor")
        if durum.onbellekten:
            metin += " (disk önbelleğindeki kopya)"
        return metin

    @staticmethod
    def _arsiv_uyarilari(durum: Any) -> List[str]:
        """Kullanıcının bilmesi gereken sessiz geçişler.

        Geçersiz bir klasör konum çözümünde SESSİZCE atlanıyor (yanlış ayar
        uygulamayı arşivsiz bırakmasın diye); ama kullanıcı gösterdiği klasörün
        kullanılmadığını buradan öğrenmeli, yoksa "seçtim ama olmadı" kalır.
        """
        uyarilar: List[str] = []
        ortam_adi = animedepo.DIZIN_ORTAM_ANAHTARI
        if durum.ortam_dizini and durum.kaynak != "ortam":
            uyarilar.append(
                f"{ortam_adi} ({durum.ortam_dizini}) geçerli bir arşiv değil "
                "(dizin.json yok ya da okunamıyor); atlandı.")
        if durum.ayar_dizini and durum.kaynak not in ("ortam", "ayar"):
            uyarilar.append(
                f"Seçtiğiniz klasör ({durum.ayar_dizini}) geçerli bir arşiv değil "
                "(dizin.json yok ya da okunamıyor); atlandı. “Klasör seç…” ile "
                "yenisini gösterin ya da “Varsayılana dön”e basın.")
        elif durum.ayar_dizini and durum.kaynak == "ortam":
            uyarilar.append(
                f"Seçtiğiniz klasör kayıtlı ama {ortam_adi} ortam değişkeni önce geliyor.")
        if durum.indirilen_var and durum.kaynak in ("ortam", "ayar"):
            uyarilar.append(
                "İndirilmiş tam arşiv de var, ama gösterilen klasör önce geliyor.")
        if durum.kaynak == "uzak":
            uyarilar.append(
                "Yerel arşiv yok: TürkAnime araması ve bölüm listeleri internetten "
                "gelir. Çevrimdışı kullanmak için tüm arşivi indirin.")
        # Güncelleme ya da silme eski kopyayı gizli bir ada taşıyıp siliyor;
        # silme yarıda kaldıysa (kilitli dosya, izin) ~0,5 GB'lık gizli klasör
        # kalıyor. Eskiden bu hata yutuluyordu, kullanıcı hiç bilmiyordu.
        kalintilar = list(getattr(durum, "kalintilar", ()) or ())
        if kalintilar:
            uyarilar.append(
                "Eski arşivin silinemeyen kopyası var (uygulama kapalıyken elle "
                "silebilirsiniz): " + ", ".join(str(k) for k in kalintilar))
        return uyarilar

    def _arsiv_dugmelerini_guncelle(self) -> None:
        """Düğmeleri işe ve bilinen duruma göre aç/kapa, metinleri ayarla."""
        mesgul = self._arsiv_mesgul is not None
        indirilen_var = bool(self._arsiv_durumu and self._arsiv_durumu.indirilen_var)
        # Aynı eylem: indirme eskiyi ancak yenisi doğrulanınca değiştiriyor.
        self.btnArsivIndir.setText(ARSIV_GUNCELLE_DUGMESI if indirilen_var
                                   else TAM_ARSIV_DUGMESI)
        self.btnArsivIndir.setEnabled(not mesgul)
        self.btnArsivKlasor.setEnabled(not mesgul)
        self.btnArsivVarsayilan.setEnabled(not mesgul)
        self.btnArsivSil.setEnabled(not mesgul and indirilen_var)
        indiriyor = self._arsiv_mesgul == "indirme"
        self.prgArsiv.setVisible(indiriyor)
        self.lblArsivIlerleme.setVisible(indiriyor)
        self.btnArsivIptal.setVisible(indiriyor)
        iptal = self._arsiv_iptal
        self.btnArsivIptal.setEnabled(indiriyor and not (iptal and iptal.is_set()))

    def _arsiv_isi_bitti(self) -> None:
        self._arsiv_mesgul = None
        self._arsiv_iptal = None
        self._arsiv_dugmelerini_guncelle()

    # İndirme ────────────────────────────────────────────────────────────────
    def _arsiv_indir(self) -> None:
        """"Tüm arşivi indir" / "Arşivi güncelle" — ikisi aynı eylem."""
        if self._arsiv_mesgul is not None:
            self.lblArsivDurum.info("Bir arşiv işlemi zaten sürüyor.")
            return
        iptal = threading.Event()
        self._arsiv_iptal = iptal
        self._arsiv_mesgul = "indirme"
        self._arsiv_kaynak_adi = ""
        self.prgArsiv.setRange(0, 0)     # belirsiz: ilk yanıta kadar boyut yok
        self.lblArsivIlerleme.setText("Bağlanılıyor…")
        self.lblArsivDurum.info("Tam arşiv indiriliyor…")
        self._arsiv_dugmelerini_guncelle()
        # Genel havuz, bilinçli: uzun işler için ayrılmış havuzun sınırı
        # "paralel indirme sayısı" ve bölüm indirmeleriyle dolu olabilir; arşiv
        # o kuyruğun sonunda dakikalarca "bağlanılıyor"da beklerdi. Aynı anda tek
        # arşiv işi var (`_arsiv_mesgul`), kısa görevleri aç bırakmaz.
        run_bg(self._arsiv_indir_is, iptal)

    def _arsiv_indir_is(self, iptal: threading.Event) -> None:
        """ARKA PLAN: tam arşivi indir; her sonuç GUI'ye taşınır."""
        son = [0.0]

        def ilerleme(indirilen: int, toplam: Optional[int]) -> None:
            simdi = time.monotonic()
            bitti = toplam is not None and indirilen >= toplam
            if indirilen and not bitti and simdi - son[0] < ILERLEME_ARALIGI:
                return
            son[0] = simdi
            self._gui(lambda: self._arsiv_ilerleme(indirilen, toplam))

        def asama(ad: str, kaynak_adi: str) -> None:
            self._gui(lambda: self._arsiv_asama(ad, kaynak_adi))

        try:
            yol = animedepo.tam_arsiv_indir(ilerleme=ilerleme, iptal=iptal, asama=asama)
        except paket.IptalEdildi:
            self._gui(self._arsiv_indirme_iptal_edildi)
            return
        except Exception as exc:          # kullanıcıya gösteriliyor (sessiz değil)
            mesaj = str(exc) or type(exc).__name__
            self._gui(lambda: self._arsiv_indirme_hatasi(mesaj))
            return
        self._gui(lambda: self._arsiv_indirildi(yol))

    def _arsiv_asama(self, ad: str, kaynak_adi: str) -> None:
        if self._arsiv_mesgul != "indirme":
            return
        self._arsiv_kaynak_adi = kaynak_adi
        if ad == paket.ASAMA_BAGLANMA:
            self.prgArsiv.setRange(0, 0)
            self.lblArsivIlerleme.setText(f"Bağlanılıyor: {kaynak_adi}…")
        elif ad == paket.ASAMA_INDIRME:
            self.prgArsiv.setRange(0, 0)
            self.lblArsivIlerleme.setText(f"{kaynak_adi} paketi indiriliyor…")
        elif ad == paket.ASAMA_ACMA:
            # Açarken bayt ilerlemesi akmıyor: çubuk belirsiz kipe dönmezse
            # %100'de donmuş görünür ve kullanıcı "takıldı" sanar.
            self.prgArsiv.setRange(0, 0)
            self.lblArsivIlerleme.setText(
                "Paket açılıyor (83 bin dosya; bir dakika kadar sürebilir)…")
        elif ad == paket.ASAMA_YERLESTIRME:
            self.lblArsivIlerleme.setText("Yeni arşiv yerine konuyor…")

    def _arsiv_ilerleme(self, indirilen: int, toplam: Optional[int]) -> None:
        if self._arsiv_mesgul != "indirme":
            return                       # geç gelen ilerleme, iş bitmiş
        kaynak = f"{self._arsiv_kaynak_adi}: " if self._arsiv_kaynak_adi else ""
        if toplam:
            # Binde bir çözünürlük: QProgressBar int alıyor, bayt sayısı
            # büyük dosyada taşabilir.
            self.prgArsiv.setRange(0, 1000)
            self.prgArsiv.setValue(min(1000, int(indirilen * 1000 / toplam)))
            self.lblArsivIlerleme.setText(
                f"{kaynak}{_mb(indirilen)} / {_mb(toplam)} MB indirildi")
        else:
            # GitLab paketi anında üretiyor, boyut başlığı yok: yüzde verilemez.
            self.prgArsiv.setRange(0, 0)
            self.lblArsivIlerleme.setText(
                f"{kaynak}{_mb(indirilen)} MB indirildi (toplam ~{TAM_ARSIV_BOYUTU_MB} MB)")

    def _arsiv_iptal_et(self) -> None:
        """"İptal": indirme bir sonraki parçada (ya da tar üyesinde) durur."""
        if self._arsiv_iptal is None:
            return
        self._arsiv_iptal.set()
        self.btnArsivIptal.setEnabled(False)
        self.lblArsivIlerleme.setText("İptal ediliyor…")

    def arsiv_indirmeyi_durdur(self) -> None:
        """Pencere kapanırken çağrılır: süren arşiv indirmesini iptal et.

        Yalnızca havuzu beklemek yetmez; iptal edilmezse indirme sonuna kadar
        sürer ve süreç kapanmaz (bkz. `MainWindow.closeEvent`).
        """
        if self._arsiv_iptal is not None:
            self._arsiv_iptal.set()

    def _arsiv_indirildi(self, yol: Any) -> None:
        self._arsiv_isi_bitti()
        self.lblArsivDurum.ok(
            f"Tam arşiv indirildi: {yol}. TürkAnime araması ve bölüm listeleri "
            "artık internetsiz çalışır.")
        self.arsiv_durumunu_tazele()

    def _arsiv_indirme_iptal_edildi(self) -> None:
        self._arsiv_isi_bitti()
        self.lblArsivDurum.info(
            "Arşiv indirmesi iptal edildi; önceki arşiv (varsa) yerinde duruyor.")

    def _arsiv_indirme_hatasi(self, mesaj: str) -> None:
        self._arsiv_isi_bitti()
        # `tam_arsiv_indir` kaynak başına sebepleri "tam arşiv indirilemedi —
        # GitLab: …; GitHub: …" diye topluyor; başlığı tekrar etmeyelim.
        onek = "tam arşiv indirilemedi — "
        sebep = mesaj[len(onek):] if mesaj.startswith(onek) else mesaj
        self.lblArsivDurum.error(
            f"Arşiv indirilemedi. Sebep: {sebep}. Önceki arşiv (varsa) yerinde duruyor.")

    # Klasör seç / varsayılana dön ────────────────────────────────────────────
    def _arsiv_klasor_sec(self) -> None:
        """Kullanıcının elindeki bir arşiv kopyasını göster (`animedepo_dizin`)."""
        if self._arsiv_mesgul is not None:
            return
        durum = self._arsiv_durumu
        baslangic = (durum.ayar_dizini if durum else "") or str(Path.home())
        secilen = QFileDialog.getExistingDirectory(
            self, "Arşiv klasörünü seç (içinde dizin.json olmalı)", baslangic)
        if not secilen:
            return
        self._arsiv_mesgul = "islem"
        self._arsiv_dugmelerini_guncelle()
        self.lblArsivDurum.info("Klasör denetleniyor…")
        # Doğrulama `dizin.json`'ı ayrıştırıyor (~1 MB); klasör ağ sürücüsünde
        # de olabilir — GUI thread'inde yapılmaz.
        run_bg(self._arsiv_klasor_is, secilen)

    def _arsiv_klasor_is(self, secilen: str) -> None:
        """ARKA PLAN: seçilen klasör gerçekten bir arşiv mi?"""
        try:
            veri = paket.arsivi_dogrula(Path(secilen))
        except Exception as exc:          # kullanıcıya gösteriliyor (sessiz değil)
            mesaj = str(exc) or type(exc).__name__
            self._gui(lambda: self._arsiv_klasor_reddedildi(mesaj))
            return
        sayi = paket.anime_sayisi(veri)
        self._gui(lambda: self._arsiv_klasor_kabul(secilen, sayi))

    def _arsiv_klasor_reddedildi(self, mesaj: str) -> None:
        self._arsiv_isi_bitti()
        self.lblArsivDurum.error(
            f"Bu klasör arşiv olarak kullanılamaz: {mesaj}. dizin.json'ın "
            "bulunduğu klasörü seçin (ör. indirilen arşivin kendisi ya da "
            "depodaki arsiv/). Ayar değiştirilmedi.")

    def _arsiv_klasor_kabul(self, secilen: str, sayi: int) -> None:
        self._arsiv_isi_bitti()
        try:
            self._dosya().set_ayar(animedepo.DIZIN_AYAR_ANAHTARI, secilen)
        except Exception as exc:
            self.lblArsivDurum.error(f"Arşiv klasörü kaydedilemedi: {exc}")
            return
        # Konum süreç boyunca bir kez çözülüp önbellekleniyor; sıfırlanmazsa
        # yeni klasör ancak yeniden başlatınca kullanılırdı. GUI thread'inde
        # güvenli: `sifirla` hiçbir G/Ç'yi beklemiyor (arka planda yavaş
        # aynalara takılmış bir arama pencereyi dondurmaz).
        animedepo.sifirla()
        adet = f"{sayi:,}".replace(",", ".")
        self.lblArsivDurum.ok(f"Arşiv klasörü ayarlandı ({adet} anime): {secilen}")
        self.arsiv_durumunu_tazele()

    def _arsiv_varsayilana_don(self) -> None:
        """Seçilen klasörü unut; konum varsayılan sırayla çözülsün."""
        if self._arsiv_mesgul is not None:
            return
        try:
            silindi = self._dosya().ayar_sil(animedepo.DIZIN_AYAR_ANAHTARI)
        except Exception as exc:
            self.lblArsivDurum.error(f"Arşiv klasörü ayarı silinemedi: {exc}")
            return
        animedepo.sifirla()
        if silindi:
            self.lblArsivDurum.ok(
                "Seçilen arşiv klasörü unutuldu; arşiv varsayılan sırayla aranıyor.")
        else:
            self.lblArsivDurum.info("Zaten varsayılan sıra kullanılıyor.")
        self.arsiv_durumunu_tazele()

    # Silme ──────────────────────────────────────────────────────────────────
    def _onay_al(self, baslik: str, metin: str) -> bool:
        """Evet/Hayır sorusu; varsayılan Hayır (testlerde sahtelenir)."""
        cevap = QMessageBox.question(
            self, baslik, metin,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return cevap == QMessageBox.StandardButton.Yes

    def _arsiv_sil(self) -> None:
        """İndirilen tam arşivi sil — YALNIZCA `<veri kökü>/cevrimdisi_arsiv`.

        Seçilen klasör ve depodaki `arsiv/` hiçbir koşulda silinmez
        (bkz. `animedepo.indirilen_arsivi_sil`).
        """
        if self._arsiv_mesgul is not None:
            return
        hedef = animedepo.indirilen_arsiv_dizini()
        if not self._onay_al(
                "İndirilen arşivi sil",
                f"{hedef}\n\nklasörü ve içindeki bütün dosyalar silinecek. "
                "TürkAnime bundan sonra (varsa) depodaki arşivden ya da "
                "internetten okunur. Devam edilsin mi?"):
            self.lblArsivDurum.info("Silme iptal edildi.")
            return
        self._arsiv_mesgul = "islem"
        self._arsiv_dugmelerini_guncelle()
        self.lblArsivDurum.info("İndirilen arşiv siliniyor…")
        # 83 bin dosya: silmek saniyeler sürer, GUI thread'inde yapılmaz.
        run_bg(self._arsiv_sil_is)

    def _arsiv_sil_is(self) -> None:
        """ARKA PLAN: indirilen arşivi sil."""
        try:
            silindi = animedepo.indirilen_arsivi_sil()
        except Exception as exc:          # kullanıcıya gösteriliyor (sessiz değil)
            mesaj = str(exc) or type(exc).__name__
            self._gui(lambda: self._arsiv_silme_hatasi(mesaj))
            return
        self._gui(lambda: self._arsiv_silindi(silindi))

    def _arsiv_silindi(self, silindi: bool) -> None:
        self._arsiv_isi_bitti()
        if silindi:
            self.lblArsivDurum.ok("İndirilen arşiv silindi.")
        else:
            self.lblArsivDurum.info("Silinecek indirilmiş arşiv yok.")
        self.arsiv_durumunu_tazele()

    def _arsiv_silme_hatasi(self, mesaj: str) -> None:
        self._arsiv_isi_bitti()
        self.lblArsivDurum.error(f"İndirilen arşiv silinemedi: {mesaj}")
        self.arsiv_durumunu_tazele()

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



def _mb(bayt: int) -> str:
    """Bayt → "45,2" (ondalık MB; düğmedeki ~230 MB ile aynı birim)."""
    return f"{bayt / 1_000_000:.1f}".replace(".", ",")


def _tarih_metni(zaman: Optional[int]) -> str:
    """dizin.json `last_update` (unix) → "14.09.2026"; geçersizse boş."""
    if zaman is None:
        return ""
    try:
        return datetime.fromtimestamp(int(zaman)).strftime("%d.%m.%Y")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


__all__ = ["SettingsPage"]
