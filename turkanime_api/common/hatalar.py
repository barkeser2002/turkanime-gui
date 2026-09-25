"""Kaynak hataları: kullanıcıya NEDEN'i söyleyen hafif tip ailesi.

NEDEN VAR: Akış sağlayıcısı (`sources.kayit.akis_saglayici`) arşiv dışındaki
her hatayı boş listeye çeviriyordu. `best_video` boş listeyi "hiçbiri
çalışmıyor" diye raporluyor, arayüz de "çalışan video bulunamadı" diyordu:
süresi dolmuş TRAnimeİzle çerezi, Cloudflare engeli, zaman aşımına uğrayan
CDN ve arşivde gerçekten kaydı olmayan bölüm kullanıcıya AYNI cümleyle
görünüyordu. Kullanıcı hangisinin kendi elinde olduğunu (çerezi yenile, başka
kaynak seç, ağı düzelt) bilemiyordu.

Kural: `KaynakHatasi`'nın metni kullanıcıya yazılmış bir Türkçe cümledir;
arayüz ve CLI onu olduğu gibi gösterir. Kaynağın ham istisnası (requests,
yt-dlp, curl) `kaynak_hatasi` ile sınıflandırılıp bu aileye çevrilir, asıl
istisna ``__cause__``'da (ayrıntı/araç ipucu için) durur.

`common.arsiv_paketi.ArsivHatasi` bu ailenin alt sınıfı: arşiv mesajları
zaten kullanıcıya yazılmış cümleler ve eskiden yalnızca onlar gösteriliyordu.

BU MODÜL YALNIZCA STANDART KÜTÜPHANE kullanır: `arsiv_paketi` sunucu imajında
da yükleniyor (orada requests/yt-dlp yok) ve `kayit` bilerek hafif. Bu yüzden
requests/yt-dlp istisnaları sınıf ADLARIYLA ve metinleriyle tanınıyor,
import edilerek değil.
"""
from __future__ import annotations

import errno
import re
from typing import Iterator, Optional, Tuple, Type


class KaynakHatasi(RuntimeError):
    """Kaynak okunamadı; mesaj kullanıcıya gösterilecek Türkçe cümle.

    `RuntimeError` alt sınıfı: eski `except RuntimeError` yakalayıcıları
    (ve `ArsivHatasi`'nın eski ata sınıfı) aynen çalışmaya devam eder.
    """


class OturumGerekli(KaynakHatasi):
    """Kaynak çerez/giriş istiyor ya da kayıtlı çerezin süresi dolmuş."""


class KaynakEngellendi(KaynakHatasi):
    """Cloudflare / bot koruması / HTTP 401-403: istek reddedildi."""


class KaynakYanitVermedi(KaynakHatasi):
    """Zaman aşımı, bağlantı/DNS/vekil hatası ya da sunucu tarafı 5xx."""


class VideoYok(KaynakHatasi):
    """Kaynak okundu ama bu bölüm için oynatılabilir kayıt yok.

    "Hiçbiri çalışmıyor"dan farkı: denenecek aday hiç yoktu. Arşivin tam
    taramasında 71 bin bölümün 140'ı böyle; kullanıcı yeniden denemekle
    değil başka kaynak seçerek çözebilir.
    """


# Cloudflare/WAF sayfalarının imzaları (küçük harf). `common.cf_bypass`'taki
# listeyle aynı aile; oradan import edilmiyor çünkü o modül requests çekiyor.
_ENGEL_IMZALARI = (
    "just a moment", "attention required", "cf-chl", "cf_chl",
    "challenge-platform", "cloudflare", "enable javascript and cookies",
)

# Zincirde en çok bu kadar istisna yürünür (`__cause__`/`__context__`
# döngüsüne karşı da sınır).
_ZINCIR_SINIRI = 6


def _zincir(exc: BaseException) -> Iterator[BaseException]:
    """İstisna + sebepleri: yt-dlp asıl hatayı `exc_info`'da, requests
    `__context__`'te taşıyor; sınıflandırma en içteki gerçek sebebi görmeli."""
    gorulen = set()
    sira: list = [exc]
    while sira and len(gorulen) < _ZINCIR_SINIRI:
        e = sira.pop(0)
        if e is None or id(e) in gorulen:
            continue
        gorulen.add(id(e))
        yield e
        bilgi = getattr(e, "exc_info", None)
        if isinstance(bilgi, tuple) and len(bilgi) > 1 and isinstance(bilgi[1], BaseException):
            sira.append(bilgi[1])
        sira.append(e.__cause__)
        sira.append(e.__context__)


def _adlar(exc: BaseException) -> set:
    return {c.__name__ for c in type(exc).__mro__}


def _durum_kodu(exc: BaseException, metin: str) -> Optional[int]:
    yanit = getattr(exc, "response", None)
    kod = getattr(yanit, "status_code", None) or getattr(exc, "status_code", None)
    if isinstance(kod, int):
        return kod
    # yt-dlp: "HTTP Error 403: Forbidden"; requests: "403 Client Error: ...".
    eslesme = (re.search(r"HTTP Error (\d{3})", metin)
               or re.search(r"\b(\d{3}) (?:Client|Server) Error", metin)
               or re.search(r"\bHTTP (\d{3})\b", metin))
    return int(eslesme.group(1)) if eslesme else None


def _yanit_metni(exc: BaseException) -> str:
    """Yanıt gövdesinin başı (Cloudflare sayfasını tanımak için)."""
    yanit = getattr(exc, "response", None)
    try:
        govde = getattr(yanit, "text", "") or ""
    except Exception:            # bazı yanıtlar gövdeyi tembel çözüyor
        govde = ""
    return str(govde)[:4000]


def _siniflandir(exc: BaseException) -> Tuple[Type[KaynakHatasi], str]:
    """(hata sınıfı, kısa Türkçe sebep). Bilinmeyen: (KaynakHatasi, "")."""
    for e in _zincir(exc):
        adlar = _adlar(e)
        metin = str(e) or ""
        kucuk = (metin + " " + _yanit_metni(e)).casefold()
        kod = _durum_kodu(e, metin)
        if "CFBypassError" in adlar or any(i in kucuk for i in _ENGEL_IMZALARI):
            return KaynakEngellendi, ("Cloudflare engeli: site bot doğrulaması "
                                      "istiyor; biraz sonra yeniden deneyin ya "
                                      "da başka kaynak seçin")
        if adlar & {"Timeout", "ReadTimeout", "ConnectTimeout", "TimeoutError",
                    "timeout", "ReadTimeoutError", "ConnectTimeoutError"} \
                or "timed out" in kucuk or "zaman aşımı" in kucuk:
            return KaynakYanitVermedi, "zaman aşımı: sunucu zamanında yanıt vermedi"
        if "ProxyError" in adlar or "proxyerror" in kucuk:
            return KaynakYanitVermedi, ("sunucuya ulaşılamadı: vekil sunucu "
                                        "(proxy) bağlantıyı reddetti")
        if ("NameResolutionError" in adlar or "gaierror" in adlar
                or "name or service not known" in kucuk
                or "getaddrinfo failed" in kucuk or "failed to resolve" in kucuk
                or "could not resolve host" in kucuk):
            return KaynakYanitVermedi, ("sunucuya ulaşılamadı: adres çözülemedi "
                                        "(DNS / internet bağlantısı)")
        if "SSLError" in adlar or "CERTIFICATE_VERIFY_FAILED" in metin:
            return KaynakYanitVermedi, "güvenli bağlantı (SSL) kurulamadı"
        if kod in (401, 403):
            return KaynakEngellendi, (f"HTTP {kod}: sunucu erişimi reddetti; "
                                      "başka fansub/kaynak deneyin")
        if kod in (404, 410):
            return VideoYok, (f"HTTP {kod}: video sunucuda artık yok; "
                              "başka fansub/kaynak deneyin")
        if kod == 429:
            return KaynakEngellendi, ("HTTP 429: çok fazla istek; birkaç dakika "
                                      "bekleyip yeniden deneyin")
        if kod is not None and 500 <= kod < 600:
            return KaynakYanitVermedi, f"HTTP {kod}: kaynağın sunucusu hata verdi"
        if adlar & {"ConnectionError", "ConnectionRefusedError",
                    "ConnectionResetError", "ConnectionAbortedError",
                    "MaxRetryError", "NewConnectionError", "RemoteDisconnected",
                    "ChunkedEncodingError"}:
            return KaynakYanitVermedi, "sunucuya ulaşılamadı: bağlantı kurulamadı ya da koptu"
        if "unsupported url" in kucuk:
            return KaynakHatasi, "yt-dlp bu video adresini desteklemiyor"
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            return KaynakHatasi, "diskte yer yok: indirme klasörünün diskini boşaltın"
        if isinstance(e, PermissionError):
            return KaynakHatasi, ("indirme klasörüne yazılamıyor (izin yok); "
                                  "Ayarlar'dan başka klasör seçin")
    return KaynakHatasi, ""


def ham_metin(exc: BaseException) -> str:
    """Araç ipucu / konsol için ham metin: ``Sınıf: mesaj``."""
    metin = str(exc)
    return f"{type(exc).__name__}: {metin}" if metin else type(exc).__name__


def insanlastir(exc: BaseException) -> Tuple[str, str]:
    """``(kisa, ayrinti)``: kullanıcıya gösterilecek cümle + ham metin.

    `KaynakHatasi` (arşiv hataları dahil) metni kullanıcıya yazılmış olduğu
    için AYNEN geçer. Tanınan ağ/HTTP/disk hataları Türkçe sebebe çevrilir;
    tanınmayan hata "beklenmeyen hata: <ilk satır>" olur ve ham metnin
    tamamı (sınıf adıyla) ``ayrinti``'da kalır: araç ipucunda/konsolda
    durur, kopyalanıp hata bildirimine yapıştırılabilir.
    """
    ayrinti = ham_metin(exc)
    if isinstance(exc, KaynakHatasi):
        return (str(exc) or ayrinti), ayrinti
    _sinif, kisa = _siniflandir(exc)
    if not kisa:
        # İlk satır, kısaltılmış: kendi hatalarımızın bir kısmı zaten Türkçe
        # ("indirme bitti ama dosya diskte yok"), yabancı olanlar da ipucu.
        ilk = (str(exc).strip().splitlines() or [""])[0]
        if len(ilk) > 90:
            ilk = ilk[:89] + "…"
        kisa = (f"beklenmeyen hata: {ilk}" if ilk
                else f"beklenmeyen hata ({type(exc).__name__})")
    return kisa, ayrinti


def sebep_metni(exc: BaseException) -> str:
    """Tek satırlık sebep: tanınan hata Türkçe, tanınmayan kendi metniyle.

    `insanlastir`'dan farkı yalnızca tanınmayan hatada: arama sayfasının
    "aranamayan: X — …" satırı kaynak başına tek cümle ve orada "beklenmeyen
    hata (RuntimeError)" ham metinden (ör. "site değişti") daha az şey söyler.
    """
    if isinstance(exc, KaynakHatasi):
        return str(exc) or ham_metin(exc)
    _sinif, kisa = _siniflandir(exc)
    return kisa or str(exc) or type(exc).__name__


def kaynak_hatasi(exc: BaseException, etiket: str = "",
                  is_adi: str = "video listesi alınamadı") -> KaynakHatasi:
    """Ham istisnayı etiketli, tipli `KaynakHatasi`'na çevir (zaten öyleyse aynısı).

    Çağıran ``raise kaynak_hatasi(e, "AnimeciX") from e`` yazar; asıl istisna
    ``__cause__``'da kalır. Metin: "AnimeciX: video listesi alınamadı —
    zaman aşımı: …". Tanınmayan hatada sınıf adı ve kısaltılmış ham metin
    cümlenin içinde: kaynak koduna bakan kullanıcı/geliştirici neyin
    bozulduğunu görsün (ör. "AttributeError: 'NoneType' …" = site değişti).
    """
    if isinstance(exc, KaynakHatasi):
        return exc
    sinif, kisa = _siniflandir(exc)
    if not kisa:
        ham = ham_metin(exc)
        kisa = ham if len(ham) <= 160 else ham[:157] + "…"
    onek = f"{etiket}: " if etiket else ""
    return sinif(f"{onek}{is_adi} — {kisa}")


__all__ = ["KaynakHatasi", "OturumGerekli", "KaynakEngellendi",
           "KaynakYanitVermedi", "VideoYok", "insanlastir", "kaynak_hatasi",
           "ham_metin", "sebep_metni"]
