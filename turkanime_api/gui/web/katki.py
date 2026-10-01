"""Oturum kimliği bağışı — onay penceresi ve sunucu çağrıları.

Kullanıcı, kaynak siteye ait oturum çerezini projenin sunucusuna bağışlayabilir.
Bu, sunucunun o siteye **kullanıcının hesabıyla** istek yapması demektir; yani
bir "destek ol" düğmesinden çok daha ağır bir karardır. Modül bu yüzden iki şeyi
birden yapar: metni dürüst tutar ve gönderimi mekanik olarak onaya bağlar.

Üç kural:

1. **Onay verilmeden hiçbir şey gönderilmez.** `bagis_gonder` yalnızca çağrılınca
   ağa çıkar; onay ile gönderim arasındaki tek köprü `onay_al`'ın geri
   çağrısına ``True`` gitmesidir, o da YALNIZCA sayfadaki pencere
   ``{"onay": true, "okudum": true}`` cevabını verdiğinde (`onay_cevabi_mi`).
   Başka her sonuç — Esc, dış tık, "Vazgeç", kutusuz onay, bozuk/eksik alan,
   sayfanın hiç bağlanmaması, uygulamanın kapanması — onay DEĞİLDİR. Ayarın
   açık olması TEK BAŞINA yetmez — ayar sadece pencerenin gösterilmesine izin
   verir.
2. **Metin pazarlama yapmaz.** Kullanıcıya ne kaybedebileceği (hesabın
   kapanması) yazılı olarak söylenir. "Projeye destek ol" cümlesi bilerek yok.
3. **Kaza sonucu onay olmaz.** Onay düğmesi, kullanıcı "okudum" kutusunu
   işaretlemeden etkin değildir ve odak *Vazgeç*'tedir; pencere açıkken Enter
   HER ZAMAN vazgeçer (Qt'deki varsayılan düğme gibi), bağış yapmaz. Kutu
   sayfada atlatılsa bile Python ``okudum`` alanını ayrıca istiyor.

Pencere eskiden bir Qt diyaloğuydu (`KimlikBagisDialog`); artık web arayüzünde
(``statik/js/pencereler.js``, tür ``bagis_onayi``) ve soru-cevap düzeni
`gui.web.sorular`'da. Kurallar değişmedi, yalnızca cevap geri çağrıyla geliyor.

Sunucu adresi ve API anahtarı koda gömülü değildir, ayarlardan gelir. Adres ya
da anahtar boşsa uç istemci tarafında da kapalıdır: yanlış yapılandırılmış bir
istemcinin kimliği nereye gönderdiğini bilmemesi kabul edilemez.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

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

# Ağ çağrılarının tavanı: pencere kapandıktan sonra arayüz beklemede kalır,
# yanıtsız bir sunucu yüzünden süresiz donmamalı.
ZAMAN_ASIMI = 15

ONAY_BASLIK = "Oturum kimliğini bağışla"

# Pencerenin gövdesi. Testler buradaki üç kritik ifadeyi arıyor:
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
    """Pencere gövdesi — site adı yerleştirilmiş hâlde (sayfada DÜZ METİN)."""
    return ONAY_METNI.format(site=KAYNAK_ADLARI.get(kaynak, kaynak))


class KatkiHatasi(RuntimeError):
    """Bağış gönderilemedi / geri çekilemedi."""


# ── Onay penceresi ─────────────────────────────────────────────────────────
ONAY_DUGMESI = "Kimliğimi bağışla"
VAZGEC_DUGMESI = "Vazgeç"


def onay_cevabi_mi(cevap: Any) -> bool:
    """Pencerenin cevabı AÇIK onay mı?

    Yalnızca ``{"onay": True, "okudum": True}`` — ikisi de gerçek ``True``
    (``1``, ``"true"`` değil). Sayfadaki düğme kutu işaretlenmeden zaten
    pasif; buradaki ikinci denetim, sayfa tarafında bir hata ya da atlatma
    olsa bile "okudum" demeden onayın geçmemesi için.
    """
    return (isinstance(cevap, dict) and cevap.get("onay") is True
            and cevap.get("okudum") is True)


def onay_al(sorular: Any, kaynak: str, geri: Callable[[bool], Any]) -> Any:
    """Onay penceresini göster; ``geri(True)`` YALNIZCA kullanıcı bilerek
    onaylarsa, diğer her sonuçta ``geri(False)`` — tam bir kez.

    Ayrı fonksiyon olmasının sebebi çağrı yerinin (ayarlar uçları) pencereyi
    değil yalnızca "onay var mı" sorusunu tanıması: gönderim kararı tek bir
    boolean'a bağlı kalır, sayfanın ham cevabını yorumlama fırsatı olmaz.
    Soru merkezi yoksa (sayfasız kurulum) soru sorulamaz: onay yok.
    """
    if sorular is None:
        geri(False)
        return None
    return sorular.sor("bagis_onayi", {
        "kaynak": kaynak,
        "baslik": ONAY_BASLIK,
        "metin": onay_metni(kaynak),
        "kutu": ONAY_KUTUSU,
        "onay_dugmesi": ONAY_DUGMESI,
        "vazgec_dugmesi": VAZGEC_DUGMESI,
    }, lambda cevap: geri(onay_cevabi_mi(cevap)))


# ── Sunucu çağrıları ────────────────────────────────────────────────────────
# Ayarlardaki adres/anahtar boşsa bunlar kullanılır. Anahtar GİZLİ DEĞİL:
# dağıtılan her istemcinin içinde ve bu depoda açıkta duruyor; sunucu onu
# yetki olarak değil kapı olarak kullanıyor (hız sınırı, bekleyen sınırı).
# ayarlar.json'a yazılmıyor: anahtar döndürülünce eski kurulumlar yenisini
# güncellemeyle alır.
VARSAYILAN_SUNUCU_ADRESI = "https://turkanimeapi.bariskeser.com"
# Depo sahibi ekler. Boşken yerleşik anahtar yok: kullanıcı kendi anahtarını
# girmedikçe projenin sunucusuna hiçbir şey gönderilmez.
VARSAYILAN_API_ANAHTARI = ""


def sunucu_yapilandirmasi(ayarlar: Optional[Dict[str, Any]] = None
                          ) -> Tuple[str, str]:
    """``(adres, api anahtarı)``; ayar boşsa projenin sunucusu.

    Yerleşik anahtar YALNIZCA projenin sunucusuna, https ile gider. Kullanıcı
    başka bir adres yazdıysa anahtarını da yazmalı; yazmadıysa anahtar boş
    kalır ve çağrı hiç başlamaz (`_uc`). Projenin anahtarı, adresi ayarlara
    yazılmış üçüncü bir sunucuya sızmamalı.
    """
    if ayarlar is None:
        from ...cli.dosyalar import Dosyalar
        ayarlar = Dosyalar().ayarlar or {}
    adres = str(ayarlar.get("sunucu adresi") or "").strip().rstrip("/")
    anahtar = str(ayarlar.get("sunucu api anahtari") or "").strip()
    if not adres:
        adres = VARSAYILAN_SUNUCU_ADRESI
    if not anahtar and adres.lower() == VARSAYILAN_SUNUCU_ADRESI:
        anahtar = VARSAYILAN_API_ANAHTARI
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


def tasima_guvenli_mi(adres: str) -> bool:
    """Sunucuya giden trafik şifreli mi (ya da makineden hiç çıkmıyor mu)?

    Kural TEK yerde: kimlik bağışı (`_tasimayi_dogrula`) ve veri bağışı
    (`gui.web.veri_bagisi`) aynı kapıdan geçiyor. İkisi ayrı yazılsaydı biri
    sıkılaştırılıp öbürü unutulabilirdi.

    Tek istisna yerel adresler: geliştirme sırasında sunucu aynı makinede
    koşuyor, trafik makineden hiç çıkmıyor. Bunu da ad üzerinden değil
    ayrıştırılmış host üzerinden karara bağlıyoruz — `http://localhost.saldiri`
    gibi bir ad "localhost ile başlıyor" diye muaf sayılmamalı. DNS'e de
    sorulmuyor: saldırganın adı yerele çözdürmesi muafiyet kazandırmamalı.
    """
    from urllib.parse import urlsplit

    parca = urlsplit(str(adres or ""))
    if parca.scheme == "https":
        return True
    if parca.scheme != "http":
        return False
    return (parca.hostname or "").lower() in ("localhost", "127.0.0.1", "::1")


def _tasimayi_dogrula(adres: str) -> None:
    """Şifresiz taşımaya kimlik verme.

    Gönderilen şey bir oturum çerezi: `http://` üzerinden giderse aradaki
    herkes onu okur ve okuyan kişi kullanıcının hesabına girebilir. Bu, bağışın
    sunucuya yaptığından daha ağır bir sonuç — sunucuya bilerek güveniliyor,
    aradaki ağa güvenilmiyor. Kural `tasima_guvenli_mi`'de; burada yalnızca
    reddin kullanıcıya söylenişi var.
    """
    from urllib.parse import urlsplit

    if tasima_guvenli_mi(adres):
        return
    if urlsplit(adres).scheme != "http":
        raise KatkiHatasi(
            f"Sunucu adresi anlaşılmadı ({adres!r}); https:// ile başlamalı.")
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
           "ONAY_KUTUSU", "ONAY_DUGMESI", "VAZGEC_DUGMESI", "KatkiHatasi",
           "onay_metni", "onay_cevabi_mi", "onay_al", "sunucu_yapilandirmasi",
           "VARSAYILAN_SUNUCU_ADRESI", "VARSAYILAN_API_ANAHTARI",
           "tasima_guvenli_mi", "bagis_gonder", "bagis_geri_cek"]
