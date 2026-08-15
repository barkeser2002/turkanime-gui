"""Oturum kimliği bağışı — onay diyaloğu ve sunucu çağrıları.

Kullanıcı, kaynak siteye ait oturum çerezini projenin sunucusuna bağışlayabilir.
Bu, sunucunun o siteye **kullanıcının hesabıyla** istek yapması demektir; yani
bir "destek ol" düğmesinden çok daha ağır bir karardır. Modül bu yüzden iki şeyi
birden yapar: metni dürüst tutar ve gönderimi mekanik olarak onaya bağlar.

Üç kural:

1. **Onay verilmeden hiçbir şey gönderilmez.** `bagis_gonder` yalnızca çağrılınca
   ağa çıkar; onay ile gönderim arasındaki tek köprü `KimlikBagisDialog`'un
   `Accepted` dönmesidir. Ayarın açık olması TEK BAŞINA yetmez — ayar sadece
   diyaloğun gösterilmesine izin verir.
2. **Metin pazarlama yapmaz.** Kullanıcıya ne kaybedebileceği (hesabın
   kapanması) yazılı olarak söylenir. "Projeye destek ol" cümlesi bilerek yok.
3. **Kaza sonucu onay olmaz.** Onay düğmesi, kullanıcı "okudum" kutusunu
   işaretlemeden etkin değildir ve varsayılan düğme *Vazgeç*'tir; Enter'a basmak
   bağış yapmaz.

Sunucu adresi ve API anahtarı koda gömülü değildir, ayarlardan gelir. Adres ya
da anahtar boşsa uç istemci tarafında da kapalıdır: yanlış yapılandırılmış bir
istemcinin kimliği nereye gönderdiğini bilmemesi kabul edilemez.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

# Bağışın SUNUCUDAKİ kaynak adı. Buradaki dizgi sunucunun `BAGIS_KAYNAKLARI`
# beyaz listesiyle harfi harfine aynı olmak zorunda: sunucu `KAYNAK_CEREZLERI`
# sözlüğünün anahtarlarını kullanıyor ve o anahtar "tranime".
#
# Eskiden burada "tranimeizle" yazıyordu — sitenin adı, sunucunun anahtarı
# değil. Sunucu bunu beyaz listede bulamayıp her bağışı 400 ile reddediyordu.
# Site adı kullanıcıya `KAYNAK_ADLARI` üzerinden gösteriliyor; ikisini
# karıştırmamak için ayrı duruyorlar.
KAYNAK_TRANIME = "tranime"
KAYNAK_OPENANI = "openani"

# Bağışlanan değerin cinsi. Sunucu `tur` alanını ZORUNLU istiyor
# (`Literal["cookie", "token"]`); gönderilmezse istek gövdeye hiç bakılmadan
# 422 ile döner — yani eksik `tur` her bağışı sessizce çöpe atardı.
TUR_COOKIE = "cookie"
TUR_TOKEN = "token"

# Kaynak adı -> (kullanıcıya gösterilecek site adı, bağışlanan değerin cinsi).
KAYNAK_ADLARI = {
    KAYNAK_TRANIME: "TRAnimeİzle",
    KAYNAK_OPENANI: "OpenAnime",
}
KAYNAK_TURLERI = {
    KAYNAK_TRANIME: TUR_COOKIE,     # Netscape çerez dosyası
    KAYNAK_OPENANI: TUR_TOKEN,      # Bearer jetonu
}

# Ağ çağrılarının tavanı: diyalog kapandıktan sonra arayüz beklemede kalır,
# yanıtsız bir sunucu yüzünden süresiz donmamalı.
ZAMAN_ASIMI = 15

ONAY_BASLIK = "Oturum kimliğini bağışla"

# Diyaloğun gövdesi. Testler buradaki üç kritik ifadeyi arıyor:
# "senin adına giriş", "hesabın kapanabilir", "geri çek…".
# Büyük harf kullanılmıyor: Türkçe "İ" harfinin `str.lower()` çıktısı
# birleşik noktalı "i̇" olduğu için metni büyük harfle vurgulamak, aynı
# ifadeyi arayan her denetimi (test dâhil) sessizce kaçırtır.
ONAY_METNI = (
    "{site} oturum çerezini projenin sunucusuna göndermek üzeresiniz. "
    "Kabul etmeden önce ne olduğunu okuyun.\n\n"
    "1. Bu çerez bir parola kadar güçlüdür: sunucuya, siteye senin adına giriş "
    "yapma yetkisi verir. Sunucu senin parolanı görmez ama parolanın açtığı "
    "kapıyı kullanır.\n\n"
    "2. Sunucu bu kimlikle senin hesabın üzerinden siteye istek yapar. "
    "Sitenin kayıtlarında bu istekler senin hesabın olarak görünür.\n\n"
    "3. Site bunu hesap paylaşımı sayarsa hesabın kapanabilir. Bu riski sunucu "
    "değil, hesabın sahibi olarak sen taşırsın.\n\n"
    "4. İstediğin an geri çekebilirsin. Bağış numarası ayarlarında saklanır; "
    "Ayarlar sayfasındaki \"Bağışımı geri çek\" düğmesi bağışı sunucudan siler.\n\n"
    "5. Çerez sunucuda şifreli saklanır ve en geç 30 gün sonra kendiliğinden "
    "silinir. Bağış kaydına kullanıcı adın ya da e-postan yazılmaz; kayıtta "
    "bağış numarası dışında seni gösteren hiçbir alan yoktur.\n\n"
    "6. IP adresin bu kayda YAZILMAZ — ama isteği sunucuya sen yaptığın için "
    "sunucu onu istek anında yine de görür. \"Hiç görmez\" diyemeyiz; "
    "\"saklamaz\" diyebiliriz.\n\n"
    "7. Bağışı kesin olarak sonlandırmanın en güçlü yolu sitedeki parolanı "
    "değiştirmektir: bu, bağışlanan çerezi de dâhil olmak üzere bütün açık "
    "oturumları geçersiz kılar ve sunucunun elindeki kopya işe yaramaz hâle "
    "gelir.\n\n"
    "8. Bağış zorunlu değildir. Vazgeçersen uygulama bugünkü gibi çalışmaya "
    "devam eder."
)

ONAY_KUTUSU = "Yukarıdakileri okudum; hesabımın kapanma riskini kabul ediyorum."


def onay_metni(kaynak: str = KAYNAK_TRANIME) -> str:
    """Diyalog gövdesi — site adı yerleştirilmiş hâlde."""
    return ONAY_METNI.format(site=KAYNAK_ADLARI.get(kaynak, kaynak))


class KatkiHatasi(RuntimeError):
    """Bağış gönderilemedi / geri çekilemedi."""


# ── Diyalog ─────────────────────────────────────────────────────────────────
class KimlikBagisDialog(QDialog):
    """Bağış onayı. `exec()` yalnızca kullanıcı bilerek onaylarsa `Accepted`.

    Onay düğmesi başlangıçta PASİF: kullanıcı "okudum" kutusunu işaretlemeden
    etkinleşmez. Varsayılan düğme *Vazgeç*, yani diyalog açıkken Enter'a basmak
    bağış yapmaz — istem dışı onayın iki ayrı yolu da kapalı.
    """

    def __init__(self, kaynak: str = KAYNAK_TRANIME,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.kaynak = kaynak
        self.setWindowTitle(ONAY_BASLIK)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        baslik = QLabel(ONAY_BASLIK)
        baslik.setObjectName("Title")
        layout.addWidget(baslik)

        self.lblMetin = QLabel(onay_metni(kaynak))
        self.lblMetin.setWordWrap(True)
        # Düz metin: gövde kullanıcıya gösterilen tek bilgi kaynağı, HTML olarak
        # yorumlanıp bir kısmı görünmez hâle gelmemeli.
        self.lblMetin.setTextFormat(Qt.TextFormat.PlainText)
        self.lblMetin.setMinimumWidth(520)
        layout.addWidget(self.lblMetin)

        self.chkAnladim = QCheckBox(ONAY_KUTUSU)
        self.chkAnladim.toggled.connect(self._kutu_degisti)
        layout.addWidget(self.chkAnladim)

        satir = QHBoxLayout()
        satir.addStretch(1)
        self.btnIptal = QPushButton("Vazgeç")
        self.btnIptal.setDefault(True)        # Enter = vazgeç
        self.btnIptal.setAutoDefault(True)
        self.btnIptal.clicked.connect(self.reject)
        satir.addWidget(self.btnIptal)

        self.btnOnay = QPushButton("Kimliğimi bağışla")
        self.btnOnay.setEnabled(False)        # kutu işaretlenene dek pasif
        self.btnOnay.setDefault(False)
        self.btnOnay.setAutoDefault(False)
        self.btnOnay.clicked.connect(self.accept)
        satir.addWidget(self.btnOnay)
        layout.addLayout(satir)

    def _kutu_degisti(self, isaretli: bool) -> None:
        self.btnOnay.setEnabled(bool(isaretli))


def onay_al(kaynak: str = KAYNAK_TRANIME,
            parent: Optional[QWidget] = None) -> bool:
    """Diyaloğu göster; kullanıcı onayladıysa True.

    Ayrı fonksiyon olmasının sebebi çağrı yerinin (ayarlar sayfası) diyalog
    sınıfını değil yalnızca "onay var mı" sorusunu tanıması: gönderim kararı tek
    bir boolean'a bağlı kalır, sayfa tarafında yanlışlıkla `exec()` dönüşünü
    yorumlama fırsatı olmaz.
    """
    dialog = KimlikBagisDialog(kaynak, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


# ── Sunucu çağrıları ────────────────────────────────────────────────────────
def sunucu_yapilandirmasi(ayarlar: Optional[Dict[str, Any]] = None
                          ) -> Tuple[str, str]:
    """``(adres, api anahtarı)`` — ayarlardan, koddan değil."""
    if ayarlar is None:
        from ...cli.dosyalar import Dosyalar
        ayarlar = Dosyalar().ayarlar or {}
    adres = str(ayarlar.get("sunucu adresi") or "").strip().rstrip("/")
    anahtar = str(ayarlar.get("sunucu api anahtari") or "").strip()
    return adres, anahtar


def _uc(ayarlar: Optional[Dict[str, Any]], yol: str) -> Tuple[str, Dict[str, str]]:
    """Tam URL + başlıklar; yapılandırma eksikse çağrıyı hiç başlatma.

    Eksik yapılandırmayla ağa çıkmak, kimliği "bir yere" göndermeyi denemek
    demek olurdu; hangi sunucuya gittiği belirsizken çerez trafiğe çıkmamalı.
    """
    adres, anahtar = sunucu_yapilandirmasi(ayarlar)
    if not adres:
        raise KatkiHatasi("Sunucu adresi ayarlanmamış.")
    if not anahtar:
        raise KatkiHatasi("Sunucu API anahtarı ayarlanmamış.")
    _tasimayi_dogrula(adres)
    return f"{adres}{yol}", {"X-API-Key": anahtar}


def _tasimayi_dogrula(adres: str) -> None:
    """Şifresiz taşımaya kimlik verme.

    Gönderilen şey bir oturum çerezi: `http://` üzerinden giderse aradaki
    herkes onu okur ve okuyan kişi kullanıcının hesabına girebilir. Bu, bağışın
    sunucuya yaptığından daha ağır bir sonuç — sunucuya bilerek güveniliyor,
    aradaki ağa güvenilmiyor.

    Tek istisna yerel adresler: geliştirme sırasında sunucu aynı makinede
    koşuyor, trafik makineden hiç çıkmıyor. Bunu da ad üzerinden değil
    ayrıştırılmış host üzerinden karara bağlıyoruz — `http://localhost.saldiri`
    gibi bir ad "localhost ile başlıyor" diye muaf sayılmamalı.
    """
    from urllib.parse import urlsplit

    parca = urlsplit(adres)
    if parca.scheme == "https":
        return
    if parca.scheme != "http":
        raise KatkiHatasi(
            f"Sunucu adresi anlaşılmadı ({adres!r}); https:// ile başlamalı.")
    host = (parca.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return
    raise KatkiHatasi(
        "Sunucu adresi şifresiz (http://). Oturum çerezi şifresiz "
        "gönderilmez; adresi https:// olarak ayarlayın.")


def bagis_gonder(deger: str, kaynak: str = KAYNAK_TRANIME,
                 ayarlar: Optional[Dict[str, Any]] = None,
                 zaman_asimi: int = ZAMAN_ASIMI) -> str:
    """Çerezi sunucuya bağışla; sunucunun verdiği bağış numarasını döndür.

    ÇAĞIRAN TARAFA UYARI: bu fonksiyon onay SORMAZ. Onay `onay_al` ile alınır;
    burada bir daha sorulsaydı iki farklı onay kaynağı olur ve hangisinin
    bağlayıcı olduğu belirsizleşirdi.
    """
    deger = str(deger or "").strip()
    if not deger:
        raise KatkiHatasi("Bağışlanacak çerez boş.")
    # `tur` sunucuda ZORUNLU. Eksikse FastAPI gövdeyi hiç değerlendirmeden 422
    # döndürür — bu alan gönderilmediği sürece hiçbir bağış sunucuya ulaşmaz.
    tur = KAYNAK_TURLERI.get(kaynak, TUR_COOKIE)
    url, basliklar = _uc(ayarlar, "/katki/kimlik")

    import requests
    try:
        yanit = requests.post(
            url,
            json={"kaynak": kaynak, "tur": tur, "deger": deger},
            headers=basliklar, timeout=zaman_asimi)
    except Exception as hata:                       # ağ/DNS/TLS
        raise KatkiHatasi(f"Sunucuya ulaşılamadı: {hata}") from hata
    if yanit.status_code >= 400:
        raise KatkiHatasi(_hata_metni(yanit))
    try:
        veri = yanit.json()
    except ValueError as hata:
        raise KatkiHatasi("Sunucu beklenmeyen bir yanıt döndürdü.") from hata
    bagis_id = str((veri or {}).get("bagis_id") or "").strip()
    if not bagis_id:
        # Numara yoksa bağış geri çekilemez; sessizce "başarılı" saymak
        # kullanıcıyı silemeyeceği bir kayıtla baş başa bırakırdı.
        raise KatkiHatasi("Sunucu bağış numarası vermedi; bağış geri "
                          "çekilemeyeceği için başarısız sayıldı.")
    return bagis_id


def bagis_geri_cek(bagis_id: str, ayarlar: Optional[Dict[str, Any]] = None,
                   zaman_asimi: int = ZAMAN_ASIMI) -> bool:
    """Bağışı sunucudan sil. Gerçekten silindiyse True.

    Sunucu 404 dönerse başarı sayılır: yol yoksa kayıt da yok.

    AMA 200 + ``{"silindi": false}`` BAŞARI DEĞİLDİR. Sunucu bu yanıtı hiçbir
    satır eşleşmediğinde döndürüyor. Çağıran tarafta "silindi" sayılırsa
    ayarlardaki bağış numarası siliniyor — oysa o numara geri çekmenin TEK
    anahtarı; sunucu bağışçının kim olduğunu bilmiyor, "bağışlarımı listele"
    diye bir uç yok ve olamaz. Yani numarayı yanlışlıkla atmak, bağışı süresi
    dolana kadar (30 gün) geri çekilemez hâle getirir.

    Bunun sessiz olmayan gerçek senaryosu şu: kullanıcı sunucu adresini
    değiştirmiş ya da yanlış yazmıştır. DELETE bambaşka bir sunucuya gider,
    orada böyle bir kayıt olmadığı için ``silindi: false`` döner, istemci
    "geri çektim" der ve numarayı siler. Asıl sunucudaki bağış yerinde durur.
    """
    bagis_id = str(bagis_id or "").strip()
    if not bagis_id:
        raise KatkiHatasi("Geri çekilecek bağış numarası yok.")
    url, basliklar = _uc(ayarlar, f"/katki/kimlik/{bagis_id}")

    import requests
    try:
        yanit = requests.delete(url, headers=basliklar, timeout=zaman_asimi)
    except Exception as hata:
        raise KatkiHatasi(f"Sunucuya ulaşılamadı: {hata}") from hata
    if yanit.status_code == 404:
        return True
    if yanit.status_code >= 400:
        raise KatkiHatasi(_hata_metni(yanit))

    try:
        govde = yanit.json()
    except ValueError:
        # Gövdesiz 2xx: sunucu sildim demiş sayılır (eski sürüm uyumluluğu).
        return True
    if isinstance(govde, dict) and govde.get("silindi") is False:
        raise KatkiHatasi(
            "Sunucu bu numaraya ait bir bağış bulamadı, dolayısıyla hiçbir şey "
            "silinmedi. Bağış numarası SAKLANIYOR — atılırsa bağış bir daha "
            "geri çekilemez. Sunucu adresinin bağış yaptığınız sunucu olduğunu "
            "doğrulayın.")
    return True


def _hata_metni(yanit) -> str:
    """Sunucu hatasını okunur cümleye çevir."""
    detay = ""
    try:
        govde = yanit.json()
        if isinstance(govde, dict):
            detay = str(govde.get("detail") or "")
    except Exception:
        detay = ""
    kod = getattr(yanit, "status_code", "?")
    return f"Sunucu hatası ({kod})" + (f": {detay}" if detay else ".")


__all__ = ["KAYNAK_TRANIME", "KAYNAK_OPENANI", "KAYNAK_ADLARI",
           "KAYNAK_TURLERI", "TUR_COOKIE", "TUR_TOKEN",
           "ONAY_BASLIK", "ONAY_METNI",
           "ONAY_KUTUSU", "KatkiHatasi", "KimlikBagisDialog", "onay_metni",
           "onay_al", "sunucu_yapilandirmasi", "bagis_gonder", "bagis_geri_cek"]
