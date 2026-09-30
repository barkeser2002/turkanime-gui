"""Veri bağışı — oynayan bölümün kaydını projenin sunucusuna bağışlamak.

NEDEN: Sunucunun arşivi kaynak siteleri kendi IP'sinden gezerek büyüyor; oysa
pek çok site sunucuyu (veri merkezi IP'si, Cloudflare, bot kontrolü) engelliyor
ama kullanıcının ev bağlantısına açık. Kullanıcı isterse KENDİ oynattığı
bölümün kaydı (kaynak, anime/bölüm kimliği, oynayan video bağlantısı ve diğer
adaylar) sunucuya gider; arşiv, istemcilerin ulaşabildiği yerden büyür.

Kimlik bağışından (`gui.web.katki`) farkı: çerez/jeton GİTMEZ, sunucu kimsenin
hesabıyla istek yapmaz. Yine de bir iz bırakır — sunucuyu işleten, hangi
bölümün hangi IP'den (yaklaşık ne zaman) oynatıldığını görür. Bu yüzden:

1. **Varsayılan KAPALI; açmak açık rıza ister.** "veri bagisi" ayarı yalnızca
   onay penceresinde "okudum" kutusu işaretlenip "Veri bağışını aç"a basılınca
   yazılır (`onay_al` → `katki.onay_cevabi_mi`). Esc, ×, dış tık, Enter,
   sayfanın hiç bağlanmaması: onay YOK. Onaylanan metnin sürümü de saklanır
   (`ONAY_SURUMU`): metin değişirse (ör. yeni bir alan gönderilecekse) eski
   onay geçmez, özellik kendiliğinden kapalı sayılır.
2. **Kapatmak anında.** Ayar kapanınca gönderici bir sonraki adımda durur ve
   gönderilmemiş kuyruk silinir; her gönderimden hemen önce ayar yeniden okunur.
3. **Beyaz liste.** Gövdede `sozlesme/katki_veri.json`'daki alanlardan başka
   hiçbir şey yok; akış sözlüğünden yalnızca adres, oynatıcı, fansub ve etiket
   okunur (çerez, user_agent, referer ASLA). Süreli/imzalı bağlantılar
   (expires, token, signature, X-Amz-* …) gönderilmeden ayıklanır: başka
   makinede işe yaramazlar ve isteyenin IP'sini/oturumunu taşırlar.
4. **Arayüz beklemez.** Kanca yalnızca anlık bir görüntü alıp bırakır; kuyruğa
   yazma ve gönderim tek bir arka plan thread'inde.

Toplama (kanca noktaları):

* Oynatma — `gui.qt.app.MainWindow._play_blocking`: `yedekli_oynat` BAŞARILI
  (mpv 0 ile kapandı) VE mpv'nin konum raporu gerçek oynatma gösteriyor
  (`kutuphane.ASGARI_KONUM` sn ya da dosya sonu; Lua'sız mpv'de rapor yok,
  çıkış kodu tek kanıt). İndirilmiş dosyadan oynatma (`prefs.YerelVideo`)
  bağışlanmaz: kaynağa hiç gidilmedi.
* İndirme — `gui.qt.indirme.DownloadManager.indirildi` (dosya diskte doğrulandı).

Kuyruk (`<veri kökü>/katki_kuyrugu.json`, atomik yazım):

* Aynı kaynak+anime+bölüm `TEKRAR_SURESI` (7 gün) içinde yeniden gönderilmez;
  kuyrukta bekleyen varsa yenisi eklenmez.
* En fazla `KUYRUK_SINIRI` kayıt; taşınca EN ESKİ kayıt günlük satırıyla düşer.
* Bölüm listesi anime başına 7 günde bir gider: her bölümde aynı 1000 satırlık
  listeyi yeniden yollamanın sunucuya bir faydası yok.
* Gönderim tek tek: 2xx → gönderildi; 400 (kaynağı tanımıyor) → düşer, o kaynak
  7 gün sorulmaz; 413/422 → düşer; 401/403, 404/405, 3xx → kalır, uzun bekleme
  (sunucu adresi/anahtarı değişince sıfırlanır); 429 → `Retry-After` kadar
  beklenir; ağ hatası/5xx → kalır, üstel geri çekilme.

Sunucu adresi/anahtarı kimlik bağışıyla ORTAK ("sunucu adresi", "sunucu api
anahtari"); biri boşsa özellik tamamen kapalı (ne toplanır ne gönderilir).
Şifresiz http yalnızca yerel adreste (`katki.tasima_guvenli_mi`).

Modül Qt'siz: soru merkezine yalnızca `onay_al` dokunuyor (o da nesneyi
dışarıdan alıyor); testler PySide6 olmadan da okuyabilsin.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from . import katki

# ── Ayar ve sözleşme ────────────────────────────────────────────────────────
AYAR_ACIK = "veri bagisi"
# Onaylanan metnin sürümü. Metin ya da gönderilen alanlar değişirse ARTIRILIR:
# eski onay yeni metne verilmiş sayılmaz, kullanıcıya yeniden sorulur.
AYAR_ONAY = "veri bagisi onayi"
ONAY_SURUMU = 1

UC_YOLU = "/katki/veri"
KUYRUK_DOSYASI = "katki_kuyrugu.json"
KUYRUK_SURUMU = 1

# Sözleşmenin sınırları (`sozlesme/katki_veri.json`). Gövde bunlara GÖRE
# kuruluyor: sunucunun 422'si kaydı düşürür, yani sınırı aşan tek alan bütün
# bağışı çöpe atardı.
SINIR_KAYNAK = 50
SINIR_KIMLIK = 300
SINIR_BASLIK = 300
SINIR_ADRES = 2048
EN_KISA_ADRES = 8
SINIR_OYNATICI = 50
SINIR_FANSUB = 100
SINIR_ETIKET = 50
SINIR_ISTEMCI = 30
EN_COK_VIDEO = 40
EN_COK_BOLUM = 5000
SEZON_TAVANI = 999
NO_TAVANI = 99999
ARA_TAVANI = 99

# ── Kuyruk ve gönderim ──────────────────────────────────────────────────────
TEKRAR_SURESI = 7 * 24 * 3600
KUYRUK_SINIRI = 200
# Tekrar defterinin (gönderilen/düşen anahtarlar) tavanı: 7 günde bundan fazla
# bölüm izleyen olmaz, ama bozuk bir saat defteri sonsuza büyütmesin.
HATIRLAMA_SINIRI = 5000
ZAMAN_ASIMI = 15
# Art arda iki istek arası: 200 kayıtlık kuyruk sunucuya bir saniyede boşalmasın.
GONDERIM_ARALIGI = 1.0
GERI_CEKILME_TABANI = 30.0
GERI_CEKILME_TAVANI = 6 * 3600.0
# 401/403/404: kullanıcı ya da sunucu bir şey değiştirmeden düzelmez; kuyruk
# bu arada saatte bir aynı reddi toplamasın. Yapılandırma değişince sıfırlanır.
UZUN_BEKLEME = 6 * 3600.0
RETRY_AFTER_VARSAYILAN = 60.0
RETRY_AFTER_TAVANI = 24 * 3600.0

# `GonderimSonucu.tur` değerleri.
TAMAM = "tamam"            # 2xx: sunucu aldı
DUSUR = "dusur"            # 413/422: kayıt geçersiz, tekrar denemek aynı sonucu verir
KAYNAK_REDDI = "kaynak_reddi"   # 400: sunucu bu kaynağı tanımıyor
BEKLE = "bekle"            # 429: Retry-After kadar dur
UZUN = "uzun"              # 401/403/404/405/3xx: uzun bekle, kayıt kalır
HATA = "hata"              # ağ/5xx: üstel geri çekilme, kayıt kalır

# ── Süreli/imzalı bağlantılar ───────────────────────────────────────────────
# Adresi süreli, imzalı ya da isteyene (IP/tarayıcı) bağlı yapan sorgu
# anahtarları (küçük harf). Ölçülen örnekler: anizm/hdplayersystem HLS
# `master.m3u8?md5=…&expires=…`, ok.ru okcdn `?expires=…&srcIp=…&srcAg=…&sig=…`,
# Tranimaci `/1080p.mp4?token=…`, okcdn kapakları `tkn=`; S3 `X-Amz-*`,
# Google `X-Goog-*`, CloudFront `Policy/Signature/Key-Pair-Id`, Akamai `hdnts`.
# "hash" BİLEREK yok: VK gömmelerinin `hash=` değeri kalıcı.
GECICI_ANAHTARLAR = frozenset({
    "md5", "expires", "expire", "expiry", "token", "tkn", "vt", "signature",
    "sig", "srcip", "srcag", "policy", "key-pair-id", "hdnts", "hdnea",
    "auth_key", "wmsauthsign",
})
GECICI_ONEKLER = ("x-amz-", "x-goog-")
# İmza yolun içinde de olabiliyor: ok.ru HLS
# `/expires/179…/srcIp/…/sig/Qa6…/ondemand/hls4_….m3u8`.
GECICI_YOL_ISARETLERI = frozenset({"expires", "sig", "signature", "token", "srcip",
                                   "srcag", "md5"})
# JWT (imzalı HLS'lerin yol/sorgu jetonu): "eyJ…" + iki nokta.
_JWT = re.compile(r"^eyJ[\w-]+\.[\w-]+\.[\w-]+$")
_KAYNAK_ANAHTARI = re.compile(r"^[a-z0-9_]{1,50}$")
_KATKI_ID = re.compile(r"^[0-9a-f]{32}$")

# ── Metinler ────────────────────────────────────────────────────────────────
ONAY_BASLIK = "Veri bağışını aç"

# Büyük harfle vurgu YOK (bkz. `katki.ONAY_METNI`): "İ"nin `str.lower()`
# çıktısı birleşik noktalı "i̇" olduğu için aynı ifadeyi arayan denetimler kaçar.
ONAY_METNI = (
    "Bu özellik açıkken, bir kaynaktan başarıyla oynattığın ya da indirdiğin her "
    "bölümün kaydı projenin sunucusuna (Ayarlar'daki sunucu adresi) gönderilir. "
    "Amaç, sunucunun arşivini kullanıcıların ulaşabildiği kayıtlarla büyütmek. "
    "Açmadan önce ne gönderildiğini okuyun.\n\n"
    "1. Gönderilenler: kaynağın adı; animenin o kaynaktaki kimliği, adı ve kapak "
    "adresi; bölümün kimliği, adı ve numaraları (sezon, bölüm, ara bölüm); oynayan "
    "video bağlantısı ve kaynağın aynı bölüm için verdiği diğer aday bağlantılar "
    "(oynatıcı adı, fansub, etiket ve hangisinin oynadığı bilgisiyle); varsa "
    "animenin bölüm listesi (kimlik ve ad); uygulamanın sürümü.\n\n"
    "2. Gönderilmeyenler: çerezler, parolalar, jetonlar (token) ve oturum "
    "kimlikleri; kullanıcı adın ya da AniList hesabın; bilgisayarındaki dosya "
    "yolları; izleme konumun (bölümün neresinde kaldığın); tarayıcı kimliği "
    "(user-agent) ve referer bilgisi. Süreli ya da imzalı bağlantılar (expires, "
    "token, signature… taşıyanlar) gönderilmeden ayıklanır. İndirilmiş dosyadan "
    "yapılan oynatmalar hiç gönderilmez.\n\n"
    "3. Sunucuyu işleten kişi, hangi bölümlerin hangi IP adresinden oynatıldığını "
    "görebilir: isteği sen yaptığın için IP adresin sunucuya ulaşır. Gönderim "
    "oynatmanın hemen ardından yapıldığı için isteğin saati de bölümü ne zaman "
    "izlediğini yaklaşık gösterir. Sunucunun bunları saklayıp saklamadığı "
    "işletenin elindedir; \"görmez\" diyemeyiz.\n\n"
    "4. Gönderim arka planda yapılır; aynı bölüm 7 gün içinde yeniden gönderilmez. "
    "Gönderilemeyen kayıtlar bu bilgisayarda bir kuyrukta bekler.\n\n"
    "5. İstediğin an kapatabilirsin: kapatınca gönderim hemen durur ve "
    "gönderilmemiş kuyruk silinir. Daha önce gönderilmiş kayıtlar sunucuda kalır; "
    "tek tek geri çekilemezler.\n\n"
    "6. Bağış zorunlu değildir. Vazgeçersen uygulama bugünkü gibi çalışmaya devam "
    "eder."
)

ONAY_KUTUSU = ("Yukarıdakileri okudum; oynattığım bölümlerin kaydının IP adresimle "
               "birlikte sunucuya ulaşacağını kabul ediyorum.")
ONAY_DUGMESI = "Veri bağışını aç"
VAZGEC_DUGMESI = "Vazgeç"

# Ayarlar sayfasındaki kartın açıklaması: (başlık, metin). Onay metninin kısa
# hâli; ikisi aynı şeyi söylemeli (testler ortak ifadeleri arıyor).
ACIKLAMA: Tuple[Tuple[str, str], ...] = (
    ("Ne gönderilir",
     "Kaynağın adı; animenin o kaynaktaki kimliği, adı ve kapak adresi; bölümün "
     "kimliği, adı ve numaraları; oynayan video bağlantısı ve kaynağın verdiği "
     "diğer aday bağlantılar (oynatıcı, fansub, etiket); varsa bölüm listesi; "
     "uygulamanın sürümü."),
    ("Ne gönderilmez",
     "Çerezler, parolalar, jetonlar ve oturum kimlikleri; bilgisayarındaki dosya "
     "yolları; izleme konumun; user-agent ve referer bilgisi. Süreli/imzalı "
     "bağlantılar ayıklanır, indirilmiş dosyadan yapılan oynatmalar gönderilmez."),
    ("Sunucu ne görür",
     "Sunucuyu işleten kişi, hangi bölümlerin hangi IP adresinden (ve yaklaşık ne "
     "zaman) oynatıldığını görebilir. Gönderilmiş kayıtlar geri çekilemez."),
)


# ── Onay ────────────────────────────────────────────────────────────────────
def onay_al(sorular: Any, geri: Callable[[bool], Any]) -> Any:
    """Onay penceresini göster; ``geri(True)`` YALNIZCA açık onayda, diğer her
    sonuçta ``geri(False)`` — tam bir kez.

    Onayın ölçütü kimlik bağışıyla aynı (`katki.onay_cevabi_mi`): yalnızca
    ``{"onay": true, "okudum": true}``. Soru merkezi yoksa (sayfasız kurulum)
    soru sorulamaz, yani onay da yok.
    """
    if sorular is None:
        geri(False)
        return None
    return sorular.sor("veri_bagisi_onayi", {
        "baslik": ONAY_BASLIK,
        "metin": ONAY_METNI,
        "kutu": ONAY_KUTUSU,
        "onay_dugmesi": ONAY_DUGMESI,
        "vazgec_dugmesi": VAZGEC_DUGMESI,
    }, lambda cevap: geri(katki.onay_cevabi_mi(cevap)))


def acik_mi(ayarlar: Optional[Dict[str, Any]]) -> bool:
    """Ayar açık VE bugünkü metne onay verilmiş mi?

    Katı karşılaştırma bilerek: `True == 1` Python'da doğru, yani elle yazılmış
    ``"veri bagisi onayi": true`` sürüm 1 onayı sayılırdı.
    """
    ayarlar = ayarlar or {}
    onay = ayarlar.get(AYAR_ONAY)
    return (ayarlar.get(AYAR_ACIK) is True and type(onay) is int  # noqa: E721
            and onay == ONAY_SURUMU)


def yapilandirma(ayarlar: Optional[Dict[str, Any]]
                 ) -> Tuple[Optional[str], Dict[str, str], str]:
    """``(uç adresi, başlıklar, kapalıysa sebep)``; adres ``None`` = gönderim yok.

    Kural kimlik bağışıyla ortak: adres/anahtar ayarlardan, https zorunlu (yerel
    adres hariç). Farkı, burada istisna yerine sebep dönmesi: kanca her oynatmada
    çağrılıyor ve "kapalı" bir hata değil, olağan durum.
    """
    adres, anahtar = katki.sunucu_yapilandirmasi(ayarlar or {})
    if not adres:
        return None, {}, ("Sunucu adresi ayarlanmamış (Oturum Kimliği Bağışı "
                          "bölümündeki alan); hiçbir şey toplanmıyor.")
    if not anahtar:
        return None, {}, "Sunucu API anahtarı ayarlanmamış; hiçbir şey toplanmıyor."
    if not katki.tasima_guvenli_mi(adres):
        return None, {}, ("Sunucu adresi https:// değil; veri bağışı yalnızca "
                          "şifreli bağlantıyla (ya da bu bilgisayardaki sunucuya) "
                          "gönderilir.")
    return adres + UC_YOLU, {"X-API-Key": anahtar}, ""


def _iz(url: str, basliklar: Dict[str, str]) -> str:
    """Yapılandırmanın parmak izi: geri çekilme hangi adres+anahtar için kuruldu.

    Anahtarın kendisi kuyruk dosyasına yazılmasın diye özet. Kullanıcı adresi ya
    da anahtarı düzeltince iz değişir ve 401'in uzun beklemesi kendiliğinden
    kalkar; 429 ise aynı sunucuya karşı olduğu sürece geçerli kalır.
    """
    ham = f"{url}\n{basliklar.get('X-API-Key', '')}".encode("utf-8")
    return hashlib.sha256(ham).hexdigest()[:16]


# ── Bağlantı ayıklama ───────────────────────────────────────────────────────
def gecici_adres_mi(adres: str) -> bool:
    """Adres süreli/imzalı ya da isteyene bağlı mı? (Ağa çıkmaz.)"""
    parca = urlsplit(str(adres or ""))
    for anahtar, deger in parse_qsl(parca.query, keep_blank_values=True):
        kucuk = anahtar.strip().lower()
        if (kucuk in GECICI_ANAHTARLAR or kucuk.startswith(GECICI_ONEKLER)
                or _JWT.match(deger.strip())):
            return True
    parcalar = [p for p in parca.path.split("/") if p]
    if any(p.lower() in GECICI_YOL_ISARETLERI for p in parcalar[:-1]):
        return True
    return any(_JWT.match(p) for p in parcalar)


def adres_temizle(adres: Any) -> Optional[str]:
    """Bağışlanabilir biçim ya da None.

    Yalnızca http(s), host'lu, kullanıcı bilgisi (``kullanici:parola@``)
    taşımayan ve süreli olmayan adres geçer; parça (``#…``) atılır — oynatıcı
    konumu (``#t=…``) gibi şeyler taşıyabilir ve sunucuya zaten gitmez.
    """
    if not isinstance(adres, str):
        return None
    adres = adres.strip()
    if not adres or any(c.isspace() for c in adres):
        return None
    try:
        parca = urlsplit(adres)
        host = parca.hostname
        kullanici = parca.username or parca.password
    except ValueError:                  # bozuk port, köşeli parantez…
        return None
    if parca.scheme.lower() not in ("http", "https") or not host or kullanici:
        return None
    if gecici_adres_mi(adres):
        return None
    temiz = urlunsplit((parca.scheme.lower(), parca.netloc, parca.path, parca.query, ""))
    if not EN_KISA_ADRES <= len(temiz) <= SINIR_ADRES:
        return None
    return temiz


def kararli_adres(akis: Dict[str, Any]) -> Optional[str]:
    """Akışın bağışlanacak adresi: önce kaynağın verdiği KALICI sayfa.

    Kaynak, çözdüğü süreli adresin yanına kalıcı gömme sayfasını ``gomme``
    anahtarıyla koyabilir (ör. ok.ru'nun `videoembed/<id>`'si, okcdn adresi
    yerine); varsa o tercih edilir. Yoksa akışın kendi adresi — o da süreliyse
    akış bağışa hiç girmez.
    """
    return adres_temizle(akis.get("gomme")) or adres_temizle(akis.get("url"))


# ── Gövde ───────────────────────────────────────────────────────────────────
def _metin(deger: Any, sinir: int) -> str:
    """Tek satır, yazdırılabilir, en çok ``sinir`` karakter."""
    if deger is None:
        return ""
    metin = " ".join(str(deger).split())
    metin = "".join(c for c in metin if c.isprintable())
    return metin[:sinir].strip()


def _metin_ya_da_bos(deger: Any, sinir: int) -> Optional[str]:
    return _metin(deger, sinir) or None


def _aralikta(deger: Any, tavan: int) -> Optional[int]:
    if isinstance(deger, bool) or not isinstance(deger, int):
        return None
    return deger if 0 <= deger <= tavan else None


def istemci_adi() -> str:
    from ...version import __version__
    return f"turkanime-gui/{__version__}"[:SINIR_ISTEMCI]


def gercekten_oynadi_mi(rapor: Optional[Dict[str, Any]]) -> bool:
    """mpv'nin konum raporu gerçek bir oynatma gösteriyor mu?

    Çıkış kodu 0 tek başına zayıf kanıt: kullanıcı akış daha yüklenirken
    pencereyi kapatınca da 0. Rapor varsa en az `kutuphane.ASGARI_KONUM`
    saniye (kaldığın yerin de eşiği) ya da dosya sonu aranıyor. Rapor yoksa
    (Lua'sız mpv) oynatmanın başarılı sayılmasının tek dayanağı çıkış kodu;
    izleme geçmişi de o durumda aynı kuralla yazılıyor.
    """
    if rapor is None:
        return True
    from ...common.kutuphane import ASGARI_KONUM
    if str(rapor.get("sebep") or "") == "eof":
        return True
    try:
        return float(rapor.get("konum") or 0) >= ASGARI_KONUM
    except (TypeError, ValueError):
        return False


def anlik_al(entry: Optional[Dict[str, Any]], video: Any) -> Optional[Dict[str, Any]]:
    """Kancanın aldığı anlık görüntü; bağışlanamayacaksa None. Ağa çıkmaz.

    Bölüm nesnesi hemen sonra yeniden oynatılabilir (`best_video` akış
    listesini ezer); gövde arka planda kurulduğu için gereken her şey burada
    KOPYALANIR.

    Bağışlanmayanlar: kaydı bilinmeyen ya da yalnızca bilgi (AniList) kaynağı,
    yerel dosya (adres http(s) değil), kaynağın kendi bölüm kimliği olmayan
    bölüm (sunucu arşivi o kimlikle anahtarlıyor; tahmin etmek arşivi kirletir),
    anime kimliği olmayan kayıt (aynı sebep).
    """
    from ...sources import kayit

    entry = entry or {}
    kaynak = kayit.bul(entry.get("kaynak"))
    if kaynak is None or not kaynak.oynatilabilir:
        return None
    modul = str(kaynak.modul or "").lower()
    if not _KAYNAK_ANAHTARI.match(modul):
        return None
    adres = str(getattr(video, "url", "") or "")
    if urlsplit(adres).scheme.lower() not in ("http", "https"):
        return None
    bolum = entry.get("obj")
    bolum_kimlik = str(getattr(bolum, "kimlik", "") or "")
    anime_kimlik = str(entry.get("kimlik") or "")
    if not (bolum_kimlik and anime_kimlik):
        return None
    try:
        anime = getattr(bolum, "anime", None)
    except Exception:                   # eski `Bolum.anime` bir property
        anime = None
    anime_adi = str(entry.get("seri_adi") or "") or str(getattr(anime, "title", "") or "")
    akislar = [dict(a) for a in (getattr(bolum, "son_akislar", None) or [])
               if isinstance(a, dict)]
    liste = entry.get("bolum_listesi")
    return {
        "kaynak": modul,
        "varsayilan_oynatici": kaynak.oynatici,
        "anime_kimlik": anime_kimlik,
        "anime_baslik": anime_adi or anime_kimlik,
        "kapak": str(entry.get("kapak") or ""),
        "bolum_kimlik": bolum_kimlik,
        "bolum_baslik": str(entry.get("title") or getattr(bolum, "title", "") or ""),
        "akislar": akislar,
        "oynayan": {"url": adres, "player": getattr(video, "player", None),
                    "label": getattr(video, "label", None)},
        "bolum_listesi": list(liste) if isinstance(liste, (list, tuple)) else None,
    }


def _video(akis: Dict[str, Any], adres: str, calisti: bool,
           varsayilan_oynatici: str) -> Dict[str, Any]:
    return {
        "url": adres,
        "oynatici": _metin_ya_da_bos(akis.get("player") or varsayilan_oynatici,
                                     SINIR_OYNATICI),
        "fansub": _metin_ya_da_bos(akis.get("fansub"), SINIR_FANSUB),
        "etiket": _metin_ya_da_bos(akis.get("label"), SINIR_ETIKET),
        "calisti": bool(calisti),
    }


def _videolar(anlik: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Oynayan akış (``calisti: true``) başta, sonra diğer adaylar.

    Oynayanın akış sözlüğü adres eşleşmesiyle bulunuyor (fansub ve `gomme`
    orada); bulunamazsa videonun kendisi. Aynı kalıcı adres iki kez girmez.
    """
    varsayilan = str(anlik.get("varsayilan_oynatici") or "")
    oynayan = dict(anlik.get("oynayan") or {})
    akislar = list(anlik.get("akislar") or [])
    eslesen = next((a for a in akislar if a.get("url") == oynayan.get("url")), None)
    if eslesen is not None:
        oynayan = {**oynayan, **{k: v for k, v in eslesen.items() if v}}
    out: List[Dict[str, Any]] = []
    gorulen = set()
    adres = kararli_adres(oynayan)
    if adres:
        out.append(_video(oynayan, adres, True, varsayilan))
        gorulen.add(adres)
    for akis in akislar:
        if len(out) >= EN_COK_VIDEO:
            break
        if akis is eslesen:
            continue
        adres = kararli_adres(akis)
        if not adres or adres in gorulen:
            continue
        out.append(_video(akis, adres, False, varsayilan))
        gorulen.add(adres)
    return out


def _bolum_listesi(ham: Optional[Sequence[Any]]) -> Optional[List[List[str]]]:
    if not ham:
        return None
    out: List[List[str]] = []
    for cift in ham:
        if not isinstance(cift, (list, tuple)) or len(cift) != 2:
            continue
        kimlik = _metin(cift[0], SINIR_KIMLIK)
        if kimlik:
            out.append([kimlik, _metin(cift[1], SINIR_BASLIK)])
        if len(out) >= EN_COK_BOLUM:
            break
    return out or None


def govde_kur(anlik: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Anlık görüntü → `POST /katki/veri` gövdesi; bağışlanacak adres yoksa None.

    Alanlar sözleşmenin sınırlarında KIRPILIR, reddedilecek değer (aralık dışı
    numara, süreli kapak) boş bırakılır: tek bir taşan alan yüzünden sunucunun
    422'si bütün kaydı düşürürdü.
    """
    from ...common.episode_parser import parse_episode

    kaynak = str(anlik.get("kaynak") or "")
    anime_kimlik = _metin(anlik.get("anime_kimlik"), SINIR_KIMLIK)
    anime_baslik = _metin(anlik.get("anime_baslik"), SINIR_BASLIK) or anime_kimlik
    bolum_kimlik = _metin(anlik.get("bolum_kimlik"), SINIR_KIMLIK)
    if not (_KAYNAK_ANAHTARI.match(kaynak) and anime_kimlik and bolum_kimlik):
        return None
    videolar = _videolar(anlik)
    if not videolar:
        return None
    bolum_baslik = _metin(anlik.get("bolum_baslik"), SINIR_BASLIK)
    # Numara bilinmiyorsa null: `extract_episode_info`'nun "sezon yoksa 1"
    # varsayımı arşive yanlış bilgi yazardı; sunucu bilmediğini bilmeli.
    bilgi = parse_episode(bolum_baslik, anime_baslik)
    return {
        "kaynak": kaynak,
        "anime": {"kimlik": anime_kimlik, "baslik": anime_baslik,
                  "kapak": adres_temizle(anlik.get("kapak"))},
        "bolum": {"kimlik": bolum_kimlik, "baslik": bolum_baslik,
                  "sezon": _aralikta(bilgi.season, SEZON_TAVANI),
                  "no": _aralikta(bilgi.episode, NO_TAVANI),
                  "ara": _aralikta(bilgi.sub, ARA_TAVANI)},
        "videolar": videolar,
        "bolum_listesi": _bolum_listesi(anlik.get("bolum_listesi")),
        "istemci": istemci_adi(),
    }


def anahtarlar(govde: Dict[str, Any]) -> Tuple[str, str]:
    """``(bölüm anahtarı, anime anahtarı)`` — tekrar denetimi için.

    JSON dizisi biçiminde: kimliklerde "/" ya da ":" olabiliyor, ayraçlı bir
    birleştirme iki farklı bölümü aynı anahtara düşürebilirdi.
    """
    anime = [govde["kaynak"], govde["anime"]["kimlik"]]
    return (json.dumps(anime + [govde["bolum"]["kimlik"]], ensure_ascii=False),
            json.dumps(anime, ensure_ascii=False))


# ── Sunucu çağrısı ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GonderimSonucu:
    tur: str
    mesaj: str = ""
    bekleme: float = 0.0          # BEKLE: Retry-After (sn)
    katki_id: str = ""


def _detay(yanit: Any) -> str:
    try:
        govde = yanit.json()
    except Exception:
        return ""
    detay = govde.get("detail") if isinstance(govde, dict) else None
    return f": {_metin(detay, 200)}" if detay else ""


def retry_after(yanit: Any, simdi: float) -> float:
    """``Retry-After`` (saniye ya da HTTP tarihi) → saniye; yoksa varsayılan."""
    from email.utils import parsedate_to_datetime

    basliklar = getattr(yanit, "headers", None) or {}
    ham = basliklar.get("Retry-After") or basliklar.get("retry-after")
    sure: Optional[float] = None
    if ham:
        try:
            sure = float(str(ham).strip())
        except ValueError:
            try:
                sure = parsedate_to_datetime(str(ham)).timestamp() - simdi
            except (TypeError, ValueError, OverflowError):
                sure = None
    if sure is None or not math.isfinite(sure):
        sure = RETRY_AFTER_VARSAYILAN
    return max(1.0, min(RETRY_AFTER_TAVANI, sure))


def yaniti_yorumla(yanit: Any, simdi: float) -> GonderimSonucu:
    """HTTP yanıtı → ne yapılacağı (bkz. modül başlığındaki tablo)."""
    kod = int(getattr(yanit, "status_code", 0) or 0)
    if 200 <= kod < 300:
        try:
            veri = yanit.json()
        except Exception:
            veri = None
        katki_id = str(veri.get("katki_id") or "") if isinstance(veri, dict) else ""
        if isinstance(veri, dict) and (veri.get("durum") == "alindi"
                                       or _KATKI_ID.match(katki_id)):
            return GonderimSonucu(TAMAM, katki_id=katki_id)
        # Her şeye 200 diyen başka bir sunucu (yanlış adres) kaydı "gönderildi"
        # saydırmamalı: kayıt kalır, kullanıcı hatayı Ayarlar'da görür.
        return GonderimSonucu(HATA, f"Sunucu beklenmeyen bir yanıt döndürdü ({kod}); "
                                    "adresin veri bağışını kabul eden sunucu "
                                    "olduğunu doğrulayın.")
    detay = _detay(yanit)
    if kod == 429:
        bekleme = retry_after(yanit, simdi)
        return GonderimSonucu(BEKLE, f"Sunucu hız sınırına takıldı (429); "
                                     f"{int(bekleme)} sn sonra yeniden denenecek.",
                              bekleme=bekleme)
    if kod == 400:
        return GonderimSonucu(KAYNAK_REDDI, f"Sunucu bu kaynağı kabul etmiyor (400){detay}.")
    if kod in (413, 422):
        return GonderimSonucu(DUSUR, f"Sunucu kaydı geçersiz saydı ({kod}){detay}.")
    if kod in (401, 403):
        return GonderimSonucu(UZUN, f"Sunucu API anahtarını reddetti ({kod}); "
                                    "Ayarlar'daki anahtarı denetleyin.")
    if kod in (404, 405):
        return GonderimSonucu(UZUN, f"Sunucu veri bağışı ucunu tanımıyor ({kod}); "
                                    "sunucu bu özelliği henüz desteklemiyor olabilir.")
    if 300 <= kod < 400:
        return GonderimSonucu(UZUN, f"Sunucu isteği başka adrese yönlendirdi ({kod}); "
                                    "API anahtarı oraya gönderilmedi. Sunucu "
                                    "adresini denetleyin.")
    return GonderimSonucu(HATA, f"Sunucu hatası ({kod}){detay}.")


def gonder(url: str, basliklar: Dict[str, str], govde: Dict[str, Any],
           zaman_asimi: int = ZAMAN_ASIMI) -> GonderimSonucu:
    """Tek kaydı gönder. Hiçbir zaman fırlatmaz.

    Yönlendirme İZLENMEZ: `requests` başka konağa yönlendirmede yalnızca
    ``Authorization``'ı düşürüyor, ``X-API-Key`` gibi özel başlıkları yeni
    konağa da taşıyor.
    """
    import requests
    try:
        yanit = requests.post(url, json=govde, headers=basliklar,
                              timeout=zaman_asimi, allow_redirects=False)
    except Exception as hata:                # ağ, DNS, TLS, zaman aşımı
        return GonderimSonucu(HATA, f"Sunucuya ulaşılamadı ({type(hata).__name__}).")
    return yaniti_yorumla(yanit, time.time())


# ── Kalıcı kuyruk ───────────────────────────────────────────────────────────
def _bos_kuyruk() -> Dict[str, Any]:
    return {"surum": KUYRUK_SURUMU, "bekleyen": [], "gorulen": {}, "listeler": {},
            "reddedilen": {}, "bekleme": {"kadar": 0.0, "ardisik": 0, "iz": ""},
            "sayac": {"gonderilen": 0, "dusurulen": 0}, "son_hata": None,
            "son_gonderim": None}


def _zaman_sozlugu(ham: Any) -> Dict[str, float]:
    if not isinstance(ham, dict):
        return {}
    out = {}
    for anahtar, zaman in ham.items():
        if isinstance(anahtar, str) and isinstance(zaman, (int, float)) \
                and not isinstance(zaman, bool) and math.isfinite(zaman):
            out[anahtar] = float(zaman)
    return out


def _duzelt(ham: Dict[str, Any]) -> Dict[str, Any]:
    """Diskten okunanı beklenen biçime getir; elle bozulmuş alan düşer.

    Kuyruk dosyası kullanıcının makinesinde, düz JSON: yarım yazılmış ya da
    elle düzenlenmiş bir alan göndericiyi her açılışta düşürmemeli.
    """
    v = _bos_kuyruk()
    v["bekleyen"] = [k for k in (ham.get("bekleyen") or [])
                     if isinstance(k, dict) and isinstance(k.get("anahtar"), str)
                     and isinstance(k.get("govde"), dict)
                     and isinstance(k["govde"].get("kaynak"), str)][-KUYRUK_SINIRI:]
    for alan in ("gorulen", "listeler", "reddedilen"):
        v[alan] = _zaman_sozlugu(ham.get(alan))
    bekleme = ham.get("bekleme") if isinstance(ham.get("bekleme"), dict) else {}
    kadar, ardisik = bekleme.get("kadar"), bekleme.get("ardisik")
    v["bekleme"] = {
        "kadar": float(kadar) if isinstance(kadar, (int, float)) and math.isfinite(kadar) else 0.0,
        "ardisik": int(ardisik) if isinstance(ardisik, int) and ardisik > 0 else 0,
        "iz": str(bekleme.get("iz") or ""),
    }
    sayac = ham.get("sayac") if isinstance(ham.get("sayac"), dict) else {}
    for alan in ("gonderilen", "dusurulen"):
        deger = sayac.get(alan)
        v["sayac"][alan] = deger if isinstance(deger, int) and deger > 0 else 0
    son_hata = ham.get("son_hata")
    if isinstance(son_hata, dict) and isinstance(son_hata.get("metin"), str):
        v["son_hata"] = {"zaman": float(son_hata.get("zaman") or 0.0),
                         "metin": son_hata["metin"]}
    if isinstance(ham.get("son_gonderim"), (int, float)):
        v["son_gonderim"] = float(ham["son_gonderim"])
    return v


class KatkiKuyrugu:
    """`katki_kuyrugu.json`: bekleyen kayıtlar, tekrar defteri, sayaçlar.

    Her değişiklik diske atomik yazılır (`cli.dosyalar.atomik_json_yaz`):
    uygulama gönderim ortasında kapansa ya da çökse de kuyruk kaybolmaz.
    Kilit süreç-içi; gönderici thread'i ve ayar sayfası (temizle, durum) aynı
    nesneyi paylaşıyor.
    """

    def __init__(self, yol: str):
        self.yol = yol
        self._kilit = threading.RLock()
        self._veri: Optional[Dict[str, Any]] = None

    # ── İç ──────────────────────────────────────────────────────────────────
    def _v(self) -> Dict[str, Any]:
        if self._veri is None:
            from ...cli.dosyalar import _json_oku
            try:
                ham = _json_oku(self.yol, _bos_kuyruk())
            except Exception as exc:     # okunamayan dosya göndericiyi durdurmasın
                print(f"[Veri bağışı] kuyruk okunamadı: {exc}")
                ham = _bos_kuyruk()
            self._veri = _duzelt(ham)
        return self._veri

    def _yaz(self) -> None:
        from ...cli.dosyalar import atomik_json_yaz
        try:
            atomik_json_yaz(self.yol, self._veri)
        except Exception as exc:         # kuyruğu yazamamak oynatmayı durdurmasın
            print(f"[Veri bağışı] kuyruk yazılamadı: {exc}")

    @staticmethod
    def _buda(v: Dict[str, Any], simdi: float) -> None:
        """Süresi dolan tekrar kayıtlarını at; saat geri alındıysa da temizle."""
        for alan in ("gorulen", "listeler", "reddedilen"):
            defter = v[alan]
            for anahtar in [a for a, z in defter.items()
                            if not simdi - TEKRAR_SURESI < z <= simdi + TEKRAR_SURESI]:
                del defter[anahtar]
        gorulen = v["gorulen"]
        if len(gorulen) > HATIRLAMA_SINIRI:
            for anahtar in sorted(gorulen, key=gorulen.get)[:len(gorulen) - HATIRLAMA_SINIRI]:
                del gorulen[anahtar]

    @staticmethod
    def _hata_yaz(v: Dict[str, Any], simdi: float, metin: str) -> None:
        v["son_hata"] = {"zaman": simdi, "metin": metin}

    # ── Dış ─────────────────────────────────────────────────────────────────
    def ekle(self, govde: Dict[str, Any], simdi: float) -> str:
        """Kaydı kuyruğa al. Dönüş: "eklendi" | "tekrar" | "bekliyor" | "reddedilen"."""
        anahtar, anime = anahtarlar(govde)
        with self._kilit:
            v = self._v()
            self._buda(v, simdi)
            if govde["kaynak"] in v["reddedilen"]:
                return "reddedilen"
            if anahtar in v["gorulen"]:
                return "tekrar"
            if any(k["anahtar"] == anahtar for k in v["bekleyen"]):
                return "bekliyor"
            if govde.get("bolum_listesi") and (
                    anime in v["listeler"]
                    or any(k.get("anime") == anime and k["govde"].get("bolum_listesi")
                           for k in v["bekleyen"])):
                govde = dict(govde, bolum_listesi=None)
            v["bekleyen"].append({"anahtar": anahtar, "anime": anime, "govde": govde,
                                  "eklendi": simdi, "deneme": 0})
            while len(v["bekleyen"]) > KUYRUK_SINIRI:
                eski = v["bekleyen"].pop(0)
                v["sayac"]["dusurulen"] += 1
                print(f"[Veri bağışı] kuyruk dolu ({KUYRUK_SINIRI}); en eski kayıt "
                      f"düştü: {eski['anahtar']}")
            self._yaz()
            return "eklendi"

    def siradaki(self, simdi: float, iz: str) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
        """``(gönderilecek kayıt, None)``, ``(None, beklenecek sn)`` ya da
        kuyruk boşsa ``(None, None)``."""
        with self._kilit:
            v = self._v()
            if not v["bekleyen"]:
                return None, None
            bekleme = v["bekleme"]
            if bekleme["iz"] != iz and (bekleme["kadar"] or bekleme["ardisik"]):
                # Adres/anahtar değişti: eski sunucunun cezası yenisine taşınmaz.
                v["bekleme"] = {"kadar": 0.0, "ardisik": 0, "iz": iz}
                self._yaz()
            elif bekleme["kadar"] > simdi:
                return None, bekleme["kadar"] - simdi
            return json.loads(json.dumps(v["bekleyen"][0])), None

    def isle(self, kayit: Dict[str, Any], sonuc: GonderimSonucu, simdi: float,
             iz: str) -> None:
        """Gönderim sonucunu kuyruğa işle (bkz. modül başlığındaki tablo)."""
        anahtar = kayit["anahtar"]
        with self._kilit:
            v = self._v()
            kuyrukta = [k for k in v["bekleyen"] if k["anahtar"] == anahtar]
            if sonuc.tur == TAMAM:
                v["bekleyen"] = [k for k in v["bekleyen"] if k["anahtar"] != anahtar]
                v["gorulen"][anahtar] = simdi
                if kayit["govde"].get("bolum_listesi"):
                    v["listeler"][kayit.get("anime") or ""] = simdi
                v["sayac"]["gonderilen"] += 1
                v["son_gonderim"] = simdi
                v["bekleme"] = {"kadar": 0.0, "ardisik": 0, "iz": iz}
            elif sonuc.tur == DUSUR:
                v["bekleyen"] = [k for k in v["bekleyen"] if k["anahtar"] != anahtar]
                # Aynı bölüm aynı gövdeyi üretir: 7 gün içinde yeniden denemek
                # aynı reddi alırdı.
                v["gorulen"][anahtar] = simdi
                v["sayac"]["dusurulen"] += len(kuyrukta)
                self._hata_yaz(v, simdi, sonuc.mesaj)
            elif sonuc.tur == KAYNAK_REDDI:
                kaynak = kayit["govde"].get("kaynak")
                dusen = [k for k in v["bekleyen"] if k["govde"].get("kaynak") == kaynak]
                v["bekleyen"] = [k for k in v["bekleyen"] if k["govde"].get("kaynak") != kaynak]
                v["reddedilen"][str(kaynak)] = simdi
                v["sayac"]["dusurulen"] += len(dusen)
                self._hata_yaz(v, simdi, sonuc.mesaj)
            else:
                for k in kuyrukta:
                    k["deneme"] = int(k.get("deneme") or 0) + 1
                bekleme = v["bekleme"]
                if sonuc.tur == BEKLE:
                    sure = sonuc.bekleme or RETRY_AFTER_VARSAYILAN
                elif sonuc.tur == UZUN:
                    sure = UZUN_BEKLEME
                else:
                    bekleme["ardisik"] = int(bekleme["ardisik"]) + 1
                    sure = min(GERI_CEKILME_TAVANI,
                               GERI_CEKILME_TABANI * 2 ** min(bekleme["ardisik"] - 1, 20))
                bekleme["kadar"] = simdi + sure
                bekleme["iz"] = iz
                self._hata_yaz(v, simdi, sonuc.mesaj)
            self._yaz()

    def temizle(self) -> int:
        """Gönderilmemiş kayıtları sil; silinen sayısı. Dosya yoksa yaratılmaz."""
        with self._kilit:
            v = self._v()
            adet = len(v["bekleyen"])
            if adet:
                v["bekleyen"] = []
                self._yaz()
            return adet

    def bekleyen_sayisi(self) -> int:
        with self._kilit:
            return len(self._v()["bekleyen"])

    def durum(self) -> Dict[str, Any]:
        with self._kilit:
            v = self._v()
            return {"bekleyen": len(v["bekleyen"]),
                    "gonderilen": v["sayac"]["gonderilen"],
                    "dusurulen": v["sayac"]["dusurulen"],
                    "son_hata": dict(v["son_hata"]) if v["son_hata"] else None,
                    "son_gonderim": v["son_gonderim"],
                    "bekleme_kadar": v["bekleme"]["kadar"]}


def kuyruk_yolu() -> str:
    """`katki_kuyrugu.json`: veri kökü, `ayarlar.json`'un yanı.

    Modül üzerinden çağrılıyor (testler `dosyalar.veri_koku`'nu geçici köke
    bağlıyor; fonksiyon import anında bağlansaydı bu görünmezdi).
    """
    from ...cli import dosyalar
    return str(dosyalar.veri_koku() / KUYRUK_DOSYASI)


def _ayarlari_oku() -> Dict[str, Any]:
    """Ayarları YAZMADAN oku (gönderici thread'i `Dosyalar()` kurmasın)."""
    from ...cli import dosyalar
    return dosyalar.salt_okunur_ayarlar()


def _zaman_metni(zaman: Optional[float]) -> str:
    if not zaman:
        return ""
    try:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(float(zaman)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


# ── Servis ──────────────────────────────────────────────────────────────────
class VeriBagisi:
    """Kanca → kuyruk → sunucu. Kancalar her thread'den çağrılabilir.

    Tek arka plan thread'i: iş varken yaşar, kuyruk boşalınca (ya da özellik
    kapalıysa) kendiliğinden biter; yeni kayıt gelince yeniden kurulur. Uygulama
    açıkken boşta bekleyen bir thread yok.

    ``ayar_oku``, ``gonderici``, ``saat``, ``kuyruk_dosyasi``: testlerin
    sahtelediği bağımlılıklar; ``otomatik=False`` thread kurmaz, adımları
    çağıran (`adim`) atar. ``bildir``: durum değişince (thread'den)
    çağrılır; ayar sayfası kendi olayını buna bağlıyor.
    """

    def __init__(self, *, kuyruk_dosyasi: Optional[str] = None,
                 ayar_oku: Optional[Callable[[], Dict[str, Any]]] = None,
                 gonderici: Optional[Callable[[str, Dict[str, str], Dict[str, Any]],
                                              GonderimSonucu]] = None,
                 saat: Callable[[], float] = time.time,
                 bildir: Optional[Callable[[], Any]] = None,
                 otomatik: bool = True):
        # Yol KURULUŞTA çözülüyor: kapanıştan sonra işini bitiren bir gönderim
        # veri kökü değişmiş olsa bile kendi dosyasına yazsın.
        self.kuyruk = KatkiKuyrugu(kuyruk_dosyasi or kuyruk_yolu())
        self._ayar_oku = ayar_oku or _ayarlari_oku
        self._gonderici = gonderici or gonder
        self._saat = saat
        self.bildir = bildir
        self._otomatik = otomatik
        self._kosul = threading.Condition()
        self._gelen: List[Dict[str, Any]] = []
        self._thread: Optional[threading.Thread] = None
        self._dur = False

    # ── Kancalar ────────────────────────────────────────────────────────────
    def oynatildi(self, entry: Optional[Dict[str, Any]], video: Any,
                  rapor: Optional[Dict[str, Any]] = None) -> bool:
        """Oynatma BAŞARILI bitti (mpv 0). Kayıt kuyruğa gidecekse True.

        Hiçbir zaman fırlatmaz: bağış yan iş, oynatmanın sonucunu değiştiremez.
        """
        try:
            if not gercekten_oynadi_mi(rapor):
                return False
            return self._topla(entry, video)
        except Exception:
            traceback.print_exc()
            return False

    def indirildi(self, entry: Optional[Dict[str, Any]], video: Any) -> bool:
        """İndirme bitti ve dosya diskte (bkz. `DownloadManager.indirildi`)."""
        try:
            return self._topla(entry, video)
        except Exception:
            traceback.print_exc()
            return False

    def _topla(self, entry: Optional[Dict[str, Any]], video: Any) -> bool:
        # Kapı kancada: kapalıyken anlık görüntü bile alınmıyor.
        ayarlar = self._ayar_oku()
        if not acik_mi(ayarlar) or yapilandirma(ayarlar)[0] is None:
            return False
        anlik = anlik_al(entry, video)
        if anlik is None:
            return False
        with self._kosul:
            self._gelen.append(anlik)
        self._uyandir()
        return True

    # ── Ayar sayfası ────────────────────────────────────────────────────────
    def ayar_degisti(self) -> None:
        """Ayarlar yazıldı (açıldı, adres/anahtar değişti, açılış): bekleyen
        varsa göndericiyi uyandır."""
        self._uyandir()

    def kapat(self) -> int:
        """Özellik kapandı: gönderilmemiş kuyruk silinir. Silinen sayısı.

        Ayarı yazmak çağıranın işi; gönderici her gönderimden önce ayarı yeniden
        okuduğu için yoldaki istek dışında hiçbir şey gitmez.
        """
        with self._kosul:
            self._gelen.clear()
            self._kosul.notify_all()
        adet = self.kuyruk.temizle()
        if adet:
            print(f"[Veri bağışı] kapatıldı; gönderilmemiş {adet} kayıt silindi.")
        self._haber()
        return adet

    def temizle(self) -> int:
        """"Kuyruğu temizle": bekleyenler silinir, özellik açık kalır."""
        with self._kosul:
            self._gelen.clear()
        adet = self.kuyruk.temizle()
        self._haber()
        return adet

    def durum(self, ayarlar: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Ayar sayfasının gösterdiği her şey (ağa çıkmaz)."""
        ayarlar = ayarlar if ayarlar is not None else self._ayar_oku()
        acik = acik_mi(ayarlar)
        sebep = yapilandirma(ayarlar)[2] if acik else ""
        s = self.kuyruk.durum()
        son_hata = s["son_hata"] or {}
        if not acik:
            metin = "Kapalı — hiçbir şey toplanmıyor ya da gönderilmiyor."
        elif sebep:
            metin = f"Açık, ama gönderim yok: {sebep}"
        else:
            metin = "Açık — oynattığın bölümlerin kaydı arka planda gönderiliyor."
        return {
            "acik": acik, "sebep": sebep, "metin": metin,
            "gonderilen": s["gonderilen"], "bekleyen": s["bekleyen"],
            "dusurulen": s["dusurulen"],
            "son_gonderim": _zaman_metni(s["son_gonderim"]),
            "son_hata": str(son_hata.get("metin") or ""),
            "son_hata_zamani": _zaman_metni(son_hata.get("zaman")),
            "bekleme": _zaman_metni(s["bekleme_kadar"]) if s["bekleme_kadar"] > self._saat() else "",
        }

    def durdur(self, bekle: float = 0.5) -> None:
        """Uygulama kapanıyor: thread bir sonraki adımda çıkar.

        Uzun beklenmiyor: yoldaki bir istek `ZAMAN_ASIMI`'na kadar sürebilir ve
        thread `daemon`; kuyruk zaten diskte, yarım kalan kayıt bir sonraki
        açılışta yeniden denenir.
        """
        with self._kosul:
            self._dur = True
            thread = self._thread
            self._kosul.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(bekle)

    # ── Gönderici ───────────────────────────────────────────────────────────
    def _haber(self) -> None:
        if self.bildir is not None:
            try:
                self.bildir()
            except Exception:
                traceback.print_exc()

    def _uyandir(self) -> None:
        """İş varsa thread'i kur (yoksa) ya da uyandır."""
        with self._kosul:
            if self._dur or not self._otomatik:
                return
            if self._thread is not None:
                self._kosul.notify_all()
                return
            if not self._gelen and not self.kuyruk.bekleyen_sayisi():
                return
            self._thread = threading.Thread(target=self._calis, name="veri-bagisi",
                                            daemon=True)
            self._thread.start()

    def _calis(self) -> None:
        while True:
            try:
                bekle = self.adim()
            except Exception:
                traceback.print_exc()
                bekle = GERI_CEKILME_TABANI
            with self._kosul:
                if self._dur:
                    self._thread = None
                    return
                if self._gelen:
                    continue
                if bekle is None:
                    self._thread = None
                    return
                if bekle > 0:
                    self._kosul.wait(bekle)
                if self._dur:
                    self._thread = None
                    return

    def adim(self) -> Optional[float]:
        """Bir iş birimi: gelenleri kuyruğa al, sıradaki kaydı gönder.

        Dönüş: sonraki adıma kadar beklenecek sn; ``None`` = yapacak iş yok
        (kuyruk boş, özellik kapalı ya da sunucu yapılandırılmamış).
        """
        with self._kosul:
            gelenler, self._gelen = self._gelen, []
        ayarlar = self._ayar_oku()
        if not acik_mi(ayarlar):
            if self.kuyruk.bekleyen_sayisi():
                self.kapat()
            return None
        url, basliklar, _sebep = yapilandirma(ayarlar)
        if url is None:
            return None                 # tamamen kapalı: gelenler de atıldı
        simdi = self._saat()
        degisti = False
        for anlik in gelenler:
            govde = govde_kur(anlik)
            if govde is None:
                print(f"[Veri bağışı] {anlik.get('kaynak')}/{anlik.get('bolum_kimlik')}: "
                      "bağışlanabilir kalıcı bağlantı yok, atlandı.")
                continue
            degisti |= self.kuyruk.ekle(govde, simdi) == "eklendi"
        if degisti:
            self._haber()
        iz = _iz(url, basliklar)
        kayit, bekle = self.kuyruk.siradaki(simdi, iz)
        if kayit is None:
            return bekle
        # Kapı gönderimden HEMEN önce yeniden: kullanıcı az önce kapattıysa
        # (ya da ayar sayfası kuyruğu sildiyse) bu kayıt gitmemeli.
        ayarlar = self._ayar_oku()
        if not acik_mi(ayarlar) or yapilandirma(ayarlar)[0] != url:
            return 0.0
        sonuc = self._gonderici(url, basliklar, kayit["govde"])
        self.kuyruk.isle(kayit, sonuc, self._saat(), iz)
        if sonuc.tur in (DUSUR, KAYNAK_REDDI):
            print(f"[Veri bağışı] kayıt düştü ({kayit['anahtar']}): {sonuc.mesaj}")
        elif sonuc.tur != TAMAM:
            print(f"[Veri bağışı] gönderilemedi, yeniden denenecek: {sonuc.mesaj}")
        self._haber()
        return GONDERIM_ARALIGI


__all__ = [
    "AYAR_ACIK", "AYAR_ONAY", "ONAY_SURUMU", "UC_YOLU", "KUYRUK_DOSYASI",
    "ONAY_BASLIK", "ONAY_METNI", "ONAY_KUTUSU", "ONAY_DUGMESI", "VAZGEC_DUGMESI",
    "ACIKLAMA", "TEKRAR_SURESI", "KUYRUK_SINIRI", "GonderimSonucu", "KatkiKuyrugu",
    "VeriBagisi", "onay_al", "acik_mi", "yapilandirma", "gecici_adres_mi",
    "adres_temizle", "kararli_adres", "gercekten_oynadi_mi", "anlik_al", "govde_kur",
    "anahtarlar", "yaniti_yorumla", "retry_after", "gonder", "kuyruk_yolu",
    "istemci_adi",
]
