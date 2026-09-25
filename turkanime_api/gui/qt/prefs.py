"""Ayar ve geçmiş okumanın TEK yeri.

Qt tarafı uzun süre ayarları hiç okumadı: kullanıcı "aria2c kullan"ı açıyor,
"paralel indirme sayisi"nı 6 yapıyor, hiçbir şey değişmiyordu. Ayarları her
sayfanın kendi başına `Dosyalar().ayarlar` ile okuması da çözüm değil — aynı
ayarın varsayılanı iki yerde farklı yazılırsa davranış sayfaya göre değişir.
Bu yüzden okuma burada toplanır, sayfalar `Tercihler` nesnesini alır.

Ayrıca `oynat()`/`indir()` çağrılarının kaynak farklarını burada soğuruyoruz:
`objects.Video.oynat` üç argüman alırken `sources.adapter.AdapterVideo.oynat`
yalnızca `dakika_hatirla` alıyor.
"""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

# Ayar okunamadığında düşülecek değerler (Dosyalar'ın varsayılanlarıyla aynı).
VARSAYILAN_PARALEL = 3
VARSAYILAN_ADAY = 8

# aria2c bu player'da devre dışı: linkleri yt-dlp'nin impersonate oturumuna
# bağlı, harici indirici o oturumu taşıyamıyor (CLI'da da aynı istisna var).
ARIA2C_DISI_PLAYER = "ALUCARD(BETA)"


@dataclass(frozen=True)
class Tercihler:
    """`ayarlar.json`'ın oynatma/indirme ve kaynak kimliklerini ilgilendiren kesiti."""

    indirilenler: str = ""
    paralel: int = VARSAYILAN_PARALEL
    max_res: bool = True
    aday_sayisi: int = VARSAYILAN_ADAY
    aria2c: bool = False
    dakika_hatirla: bool = True
    izlerken_kaydet: bool = False
    # Kapalıyken ilerleme bölüm sonunda KENDİLİĞİNDEN yazılır; açıkken eski
    # "Kaçıncı bölümü tamamladınız?" diyaloğu her bölümden sonra açılır.
    ilerlemeyi_sor: bool = False
    izlendi_ikonu: bool = True
    manuel_fansub: bool = False
    discord: bool = True
    gereksinim_atlandi: bool = False
    # Kaynak kimlikleri: diskte duruyor ama süreç içine ancak
    # `kaynak_kimliklerini_uygula` ile giriyor.
    tranime_cookie: str = ""
    openani_token: str = ""
    openani_refresh: str = ""


def _dosya():
    """`Dosyalar` örneği.

    Import fonksiyon içinde: modül seviyesinde bağlansaydı testlerin
    `dosyalar.Dosyalar`'ı sahtelemesi bu modülü etkilemezdi.
    """
    from ...cli.dosyalar import Dosyalar
    return Dosyalar()


def _pozitif(deger: Any, varsayilan: int) -> int:
    """Ayar dosyasına elle "3 " ya da "" yazılmış olabilir; sayı değilse yedeğe düş."""
    try:
        sayi = int(deger)
    except (TypeError, ValueError):
        return varsayilan
    return sayi if sayi > 0 else varsayilan


def _aday_sayisi(ayarlar: Dict[str, Any]) -> int:
    """1080p aday sayısı — ASCII ad, eski Türkçe ad yedek.

    Kanonik ad `dosyalar.ESKI_AYAR_ADLARI` göçüyle "1080p aday sayisi" oldu.
    Yedek okuma yine de duruyor: `Dosyalar` örneklenmeden elde edilmiş bir
    ayar sözlüğü (ya da göç yazamadan düşen bir çalıştırma) eski adı hâlâ
    taşıyabilir ve kullanıcının seçtiği sayı sessizce varsayılana dönmemeli.
    """
    if "1080p aday sayisi" in ayarlar:
        return _pozitif(ayarlar.get("1080p aday sayisi"), VARSAYILAN_ADAY)
    return _pozitif(ayarlar.get("1080p aday sayısı"), VARSAYILAN_ADAY)


def oku() -> Tercihler:
    """Ayarları tek seferde oku; dosya yoksa/bozuksa varsayılanlara düş."""
    try:
        ayarlar: Dict[str, Any] = _dosya().ayarlar or {}
    except Exception:
        return Tercihler()
    return Tercihler(
        indirilenler=str(ayarlar.get("indirilenler") or ""),
        paralel=_pozitif(ayarlar.get("paralel indirme sayisi"), VARSAYILAN_PARALEL),
        max_res=bool(ayarlar.get("max resolution", True)),
        aday_sayisi=_aday_sayisi(ayarlar),
        aria2c=bool(ayarlar.get("aria2c kullan", False)),
        dakika_hatirla=bool(ayarlar.get("dakika hatirla", True)),
        izlerken_kaydet=bool(ayarlar.get("izlerken kaydet", False)),
        ilerlemeyi_sor=bool(ayarlar.get("ilerlemeyi sor", False)),
        izlendi_ikonu=bool(ayarlar.get("izlendi ikonu", True)),
        manuel_fansub=bool(ayarlar.get("manuel fansub", False)),
        discord=bool(ayarlar.get("discord_rich_presence", True)),
        gereksinim_atlandi=bool(ayarlar.get("gereksinim_atlandi", False)),
        tranime_cookie=str(ayarlar.get("tranime_cookie") or ""),
        openani_token=str(ayarlar.get("openani_token") or ""),
        openani_refresh=str(ayarlar.get("openani_refresh_token") or ""),
    )


# ── Kaynak kimlikleri (çerez / jeton) ───────────────────────────────────────
def kaynak_kimliklerini_uygula(tercih: Optional[Tercihler] = None) -> bool:
    """Diskteki çerez ve jetonları kaynak modüllerinin süreç-içi global'lerine bas.

    `sources.tranime.SESSION_COOKIE` her süreçte None başlıyor ve
    `search_tranime` çerez yoksa hiç istek yapmadan boş liste dönüyor. Ayar
    sayfası çerezi diske yazıyordu ama açılışta kimse geri yüklemiyordu:
    kullanıcı çerezi bir kez alıyor, uygulamayı kapatıp açınca kaynak yine
    "0 bölüm" diyordu. OpenAnime jetonlarında durum daha kötüydü — kaynak ölü
    CDN uçlarında kullanıcıya "Ayarlar'dan token'ını girin" diyordu ama ne alan
    ne de aktarım vardı.

    Gövde Qt'siz `common.kimlikler`'de: CLI de aynı aktarımı yapıyor ve
    `gui.qt`'yi (PySide6) import edemez. Burası yalnızca `Tercihler`'i
    ayar adlarına geri çeviriyor.

    Hata yutulur ve `False` döner: kimlik yükleyememek açılışı engellememeli.
    """
    from ...common.kimlikler import kaynak_kimliklerini_uygula as _uygula
    tercih = tercih or oku()
    return _uygula({
        "tranime_cookie": tercih.tranime_cookie,
        "openani_token": tercih.openani_token,
        "openani_refresh_token": tercih.openani_refresh,
    })


def ayar_yaz(**degerler: Any) -> bool:
    """Ayarları diske yaz (`ayarlar.json`).

    Servisler `Dosyalar`'ı doğrudan tanımasın diye buradan geçiyor: okuma zaten
    burada toplu, yazma da aynı kapıdan geçmezse anahtar adları iki yerde
    ayrışır (ör. "discord_rich_presence" vs "discord").
    """
    if not degerler:
        return False
    try:
        _dosya().set_ayar(ayar_list=dict(degerler))
    except Exception:
        return False
    return True


def indirme_dizini(tercih: Optional[Tercihler] = None) -> str:
    """İndirme klasörü — asla boş string dönmez.

    Boş string'i yt-dlp çalışma dizini sayar; paketlenmiş uygulamada bu
    `Program Files` altı olur (ya yazma izni yok ya da dosyalar kaybolur).
    """
    tercih = tercih or oku()
    hedef = tercih.indirilenler
    if hedef:
        if os.path.isdir(hedef):
            return hedef
        try:                       # ayarlı ama yok: yaratmayı dene
            os.makedirs(hedef, exist_ok=True)
            return hedef
        except OSError:
            pass
    yedek = os.path.join(os.path.expanduser("~"), "Downloads")
    try:
        os.makedirs(yedek, exist_ok=True)
    except OSError:
        return os.path.expanduser("~")
    return yedek


# ── Oynatma / indirme köprüleri ─────────────────────────────────────────────
def _kabul_ediyor(fn: Callable, isim: str) -> bool:
    """`fn` bu adda bir argüman alıyor mu?

    Kaynaklar aynı arayüzü tam uygulamıyor; desteklenmeyen argümanı geçmek
    TypeError demek, hiç geçmemek de ayarı sessizce yok saymak demek olurdu.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if isim in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class YerelVideo:
    """İndirme klasöründeki dosya, `best_video` adayı gibi davranan kılıkta.

    Yedekli oynatma döngüsü (`common.oynatma.yedekli_oynat`) aday nesnesi
    bekliyor; yerel dosyayı İLK aday yapmak şunu sağlıyor: dosya bozuksa
    (mpv 2 ile döner) adresi `atla`ya girer ve döngü kendiliğinden akışa
    geçer. Ayrı bir "yerel mi, akış mı" dalı bu yedeği ikinci kez yazardı.
    """

    player = "YEREL"

    def __init__(self, yol: str):
        self.url = os.path.abspath(yol)
        self.is_working = True


def yerel_dosya(bolum, tercih: Optional[Tercihler] = None,
                kayitli: str = "") -> Optional[str]:
    """Bölümün indirilmiş dosyası (oynatılabilir, tamamlanmış) ya da None.

    Önce indirme bittiğinde kaydedilen yol (``kayitli``; kullanıcı klasör
    ayarını sonradan değiştirmiş olabilir), sonra ayarlı klasördeki hedef.
    Geçmişteki "indirildi" kaydına bakılmıyor: dosya silinmiş ya da taşınmış
    olabilir, diskte ne varsa o.
    """
    from ...common.dosya_adi import bolum_hedefi, oynatilabilir_dosya
    if kayitli:
        try:
            if os.path.isfile(kayitli) and os.path.getsize(kayitli) > 0:
                return kayitli
        except OSError:
            pass
    try:
        return oynatilabilir_dosya(bolum_hedefi(indirme_dizini(tercih), bolum))
    except (ValueError, TypeError, OSError):
        return None


def oynat(video, tercih: Optional[Tercihler] = None, *,
          baslangic: Optional[float] = None, konum_dosyasi: Optional[str] = None,
          bolum: Any = None):
    """Videoyu kullanıcının ayarlarıyla oynat.

    Kaynak videosu (`AdapterVideo`) ve yerel dosya ortak mpv komutuyla
    (`common.mpv_oynatici`) açılıyor: bölümün kaldığı saniye (``baslangic``),
    konum raporu (``konum_dosyasi``) ve "İzlerken kaydet" ancak orada
    verilebiliyor. `AdapterVideo.oynat` yalnızca `dakika_hatirla` alıyordu;
    ayar sayfasındaki "İzlerken aynı anda kaydet" hiçbir kaynakta çalışmıyordu.
    Diğer nesneler (eski `objects.Video`, sahteler) kendi `oynat`'larıyla.
    """
    from ...common import mpv_oynatici
    tercih = tercih or oku()
    if not tercih.dakika_hatirla:
        baslangic = None
    if isinstance(video, YerelVideo):
        return mpv_oynatici.yerel_oynat(video.url,
                                        dakika_hatirla=tercih.dakika_hatirla,
                                        baslangic=baslangic,
                                        konum_dosyasi=konum_dosyasi)
    if mpv_oynatici.kaynak_videosu_mu(video):
        kayit = None
        if tercih.izlerken_kaydet and bolum is not None:
            kayit = mpv_oynatici.kayit_hedefi_kur(indirme_dizini(tercih), bolum)
        return mpv_oynatici.video_oynat(video, dakika_hatirla=tercih.dakika_hatirla,
                                        baslangic=baslangic,
                                        konum_dosyasi=konum_dosyasi, kayit=kayit)
    kwargs: Dict[str, Any] = {}
    if _kabul_ediyor(video.oynat, "dakika_hatirla"):
        kwargs["dakika_hatirla"] = tercih.dakika_hatirla
    if _kabul_ediyor(video.oynat, "izlerken_kaydet"):
        kwargs["izlerken_kaydet"] = tercih.izlerken_kaydet
    return video.oynat(**kwargs)


def indir(video, callback: Callable, output: str,
          tercih: Optional[Tercihler] = None) -> None:
    """`video.indir()` — ayar açıksa aria2c hızlandırıcısıyla.

    `indir_aria2c` kendi içinde yt-dlp'ye düşebiliyor ve sonucu bool döndürüyor;
    False dönerse burada istisnaya çeviriyoruz ki çağıran taraf başarısızlığı
    başarı sanmasın.

    UYARI: aria2c yolunda yt-dlp ilerleme hook'unu yalnızca sonda ateşler
    (indirmeyi harici süreç yapıyor). Yani hook'tan iptal, aria2c'yi ortasında
    kesemez; çağıran taraf iş bitince iptal bayrağını yeniden kontrol etmeli.
    """
    tercih = tercih or oku()
    if tercih.aria2c and getattr(video, "player", None) != ARIA2C_DISI_PLAYER:
        from ...cli.cli_tools import indir_aria2c
        if indir_aria2c(video, callback=callback, output=output):
            return
        raise RuntimeError("aria2c ile indirilemedi")
    video.indir(callback=callback, output=output)


# ── Geçmiş ──────────────────────────────────────────────────────────────────
def bolum_kimligi(bolum) -> Tuple[str, str]:
    """Geçmiş kaydının anahtarı: ``(seri slug, bölüm slug)``.

    `bolum.title` bilerek okunmuyor: `objects.Bolum.title` gerekirse ağa çıkar,
    geçmiş yazmak uğruna istek göndermek istemiyoruz.
    """
    try:
        anime = getattr(bolum, "anime", None)
    except Exception:               # Bolum.anime bir property; patlayabilir
        anime = None
    seri = str(getattr(anime, "slug", "") or "")
    return seri, str(getattr(bolum, "slug", "") or "")


def gecmis_kaydet(bolum, islem: str) -> bool:
    """`izlendi`/`indirildi` kaydı at.

    Geçmiş yazımı hiçbir zaman oynatma/indirme akışını kesmemeli; bu yüzden
    hata yutulur, yalnızca sonuç döndürülür.
    """
    seri, slug = bolum_kimligi(bolum)
    if not slug:
        return False
    try:
        _dosya().set_gecmis(seri, slug, islem)
    except Exception:
        return False
    return True


def seri_adi(bolum, yedek: str = "") -> str:
    """Okunabilir seri adı — ağa çıkabilecek alanlara dokunmadan.

    `objects.Anime.title` gerekirse sayfayı indirir; önce `_title` bakılıyor
    (bkz. `progress_dialog.anime_adi`, aynı sıra).
    """
    try:
        anime = getattr(bolum, "anime", None)
    except Exception:
        anime = None
    for alan in ("_title", "title", "slug"):
        deger = getattr(anime, alan, None)
        if isinstance(deger, str) and deger:
            return deger
    return yedek


def kitaplik_kimligi(entry: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Bölüm kaydından kitaplık anahtarı: kaynak, kimlik, seri adı, kapak.

    Kaynak ve kimlik `EpisodePage._kimlik_damgala`'dan gelir. Kaynağı
    bilinmeyen kayıt (eski akışlar, testlerin çıplak `{"obj": ...}`'i)
    kitaplığa YAZILMAZ: kaynaksız bir kayıt yeniden açılamaz, yani "izlemeye
    devam et"te tıklanınca hiçbir yere gitmeyen bir kart olurdu.
    """
    entry = entry or {}
    bolum = entry.get("obj")
    seri, bolum_slug = bolum_kimligi(bolum)
    kaynak = str(entry.get("kaynak") or "")
    kimlik = str(entry.get("kimlik") or "") or (seri if kaynak else "")
    return {
        "kaynak": kaynak, "kimlik": kimlik, "bolum_slug": bolum_slug,
        "baslik": str(entry.get("seri_adi") or "") or seri_adi(bolum, kimlik),
        "kapak": str(entry.get("kapak") or ""),
    }


def kitapliga_yaz(entry: Optional[Dict[str, Any]], bolum_baslik: str = "") -> bool:
    """Başarılı oynatmayı kitaplığa (izlemeye devam + geçmiş) yaz.

    `gecmis_kaydet` gibi hata yutar: kitaplık yazılamadı diye oynatmanın
    "başarılı" sonucu değişmemeli.
    """
    from ...common import kutuphane
    k = kitaplik_kimligi(entry)
    if not (k["kaynak"] and k["kimlik"] and k["bolum_slug"]):
        return False
    return kutuphane.izleme_kaydet(
        k["kaynak"], k["kimlik"], k["baslik"], k["bolum_slug"],
        bolum_baslik=bolum_baslik or str((entry or {}).get("title") or ""),
        kapak=k["kapak"])


def konum_getir(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Bölümün kitaplıktaki konumu (``{"konum", "sure", ...}``) ya da None.

    Anahtar kitaplığınki (kaynak + kaynağın kimliği + bölüm slug'ı): mpv'nin
    adrese bağlı kaydı token'lı/değişen adreslerde kayboluyordu.
    """
    from ...common import kutuphane
    k = kitaplik_kimligi(entry)
    if not (k["kaynak"] and k["kimlik"] and k["bolum_slug"]):
        return None
    try:
        return kutuphane.konum_getir(k["kaynak"], k["kimlik"], k["bolum_slug"])
    except Exception:
        return None


def konum_yaz(entry: Optional[Dict[str, Any]], konum: Optional[float],
              sure: Optional[float] = None) -> bool:
    """Konumu yaz; ``konum`` None ise sil (bölüm bitti, baştan başlasın)."""
    from ...common import kutuphane
    k = kitaplik_kimligi(entry)
    if not (k["kaynak"] and k["kimlik"] and k["bolum_slug"]):
        return False
    if konum is None:
        return kutuphane.konum_sil(k["kaynak"], k["kimlik"], k["bolum_slug"])
    return kutuphane.konum_kaydet(k["kaynak"], k["kimlik"], k["bolum_slug"],
                                  konum, sure)


def ilerleme_kaydet(seri: str, bolum_no: int) -> bool:
    """Serinin yerel izleme ilerlemesini yaz.

    AniList yazımı buradan YAPILMAZ: yerel kayıt her kullanıcı için çalışmalı,
    ağ işi ise `gui.qt.anilist.AniListService` üzerinden arka plana gider.
    """
    if not seri:
        return False
    try:
        _dosya().set_ilerleme(seri, int(bolum_no))
    except Exception:
        return False
    return True


def yerel_ilerleme() -> Dict[str, int]:
    """`gecmis.json`'daki ``seri slug -> son tamamlanan bölüm`` eşlemesi.

    AniList→yerel senkronunun girdisi: yalnızca burada kaydı olan seriler
    güncellenir (bkz. `anilist.senkron_guncellemeleri`).
    """
    try:
        data = _dosya().gecmis or {}
    except Exception:
        return {}
    sonuc: Dict[str, int] = {}
    for seri, no in (data.get("ilerleme") or {}).items():
        try:
            sonuc[str(seri)] = int(no)
        except (TypeError, ValueError):
            continue          # elle bozulmuş kayıt senkronu düşürmemeli
    return sonuc


# ── AniList OAuth yapılandırması ────────────────────────────────────────────
@dataclass(frozen=True)
class AniListAyar:
    """AniList OAuth istemci bilgileri (ayar sayfasının doldurduğu alanlar).

    `client_secret` opsiyoneldir: boş kalırsa istemci sır gerektirmeyen
    Implicit akışı seçer (bkz. `anilist_client.AniListClient.akis_turu`).
    """

    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    # Diskte kalmış sızmış secret bu oturumda temizlendi mi (arayüz duyurur).
    sizan_secret_temizlendi: bool = False


def _anilist_modulu():
    """`turkanime_api.anilist_client` modülü.

    Modül üzerinden erişim şart: testler singleton'ı komple değiştirebilsin
    diye nesne değil, modül tutuluyor (bkz. `_dosya`).
    """
    from ... import anilist_client as modul
    return modul


def anilist_oku() -> AniListAyar:
    """AniList OAuth ayarlarını oku.

    Bu üçlü `ayarlar.json`'da DEĞİL, istemcinin kendi dosyasında duruyor; yolu
    taşımak kullanıcının kayıtlı girişini geçersiz kılardı. Okuma yine de
    buradan geçiyor ki sayfalar istemci nesnesini doğrudan tanımasın.
    """
    try:
        ist = _anilist_modulu().anilist_client
        return AniListAyar(
            client_id=str(getattr(ist, "client_id", "") or ""),
            client_secret=str(getattr(ist, "client_secret", "") or ""),
            redirect_uri=str(getattr(ist, "redirect_uri", "") or ""),
            sizan_secret_temizlendi=bool(
                getattr(ist, "sizan_secret_temizlendi", False)),
        )
    except Exception:
        return AniListAyar()


def anilist_yaz(client_id: str, client_secret: str, redirect_uri: str) -> bool:
    """OAuth ayarlarını istemciye yaz ve kalıcılaştır."""
    try:
        ist = _anilist_modulu().anilist_client
        ist.set_oauth_config(str(client_id or "").strip(),
                             str(client_secret or "").strip(),
                             str(redirect_uri or "").strip())
    except Exception:
        return False
    return True


class Gecmis:
    """`gecmis.json`'un tek seferde okunmuş hâli.

    Bölüm listesi birkaç yüz satır olabiliyor; satır başına dosya açmak listeyi
    gözle görülür yavaşlatır, üstelik liste çizilirken geçmiş zaten değişmez.
    """

    def __init__(self, izlendi: Optional[Dict[str, Any]] = None,
                 indirildi: Optional[Dict[str, Any]] = None):
        self.izlendi = izlendi or {}
        self.indirildi = indirildi or {}

    @classmethod
    def yukle(cls) -> "Gecmis":
        try:
            data = _dosya().gecmis or {}
        except Exception:
            return cls()
        return cls(data.get("izlendi") or {}, data.get("indirildi") or {})

    def durum(self, bolum) -> Tuple[bool, bool]:
        """``(izlendi mi, indirildi mi)``."""
        seri, slug = bolum_kimligi(bolum)
        if not slug:
            return (False, False)
        return (slug in (self.izlendi.get(seri) or []),
                slug in (self.indirildi.get(seri) or []))


__all__ = ["Tercihler", "Gecmis", "AniListAyar", "oku", "ayar_yaz",
           "kaynak_kimliklerini_uygula",
           "indirme_dizini", "oynat", "indir", "bolum_kimligi", "gecmis_kaydet",
           "seri_adi", "kitaplik_kimligi", "kitapliga_yaz", "YerelVideo",
           "yerel_dosya", "konum_getir", "konum_yaz",
           "ilerleme_kaydet", "yerel_ilerleme", "anilist_oku", "anilist_yaz",
           "VARSAYILAN_PARALEL", "VARSAYILAN_ADAY"]
