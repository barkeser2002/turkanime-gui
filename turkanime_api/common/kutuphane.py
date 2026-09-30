"""Yerel kitaplık: izlemeye devam, favoriler, izleme geçmişi, kaldığın yer.

AniList'e giriş yapmayan kullanıcının elinde izlediği hiçbir şeyin kaydı
yoktu. `gecmis.json` yalnızca ``{"izlendi": {seri_slug: [bölüm_slug]}}``
tutuyor: kaynak, okunabilir ad, zaman yok. İzlenen bir seri oradan yeniden
AÇILAMIYORDU bile — slug'ın hangi kaynağa ait olduğu bilinmiyor.

Anahtar ``"<kaynak>:<kimlik>"`` ve kimlik KAYNAĞIN KENDİ anime kimliği:
bölüm listesini çekerken kullanılan değer (detay sayfasının bağı). Bölüm
nesnesindeki `anime.slug` KULLANILAMAZ: `AdapterAnime` sayısal kimlikleri
(AnimeciX "1234") başlıktan türetilmiş bir slug'la değiştiriyor, o slug'la
kaynak seriyi bir daha bulamaz.

Veri AYRI bir dosyada (`kutuphane.json`, `ayarlar.json`'un yanında):
`gecmis.json`'un biçimini CLI ve eski sürümler okuyor; oraya yeni bölüm
eklemek her okuyucuyu yeni anahtarlara karşı sınamayı gerektirirdi. Yazım
kuralları `cli.dosyalar`'ınkiyle aynı (süreç-içi kilit, atomik yazım, bozuk
dosyayı yedeğe alıp varsayılana dönme) ve aynı yardımcılardan geçiyor.

Modül Qt'siz: CLI de aynı geçmişi okuyabilsin, testler pencere kurmadan
koşabilsin.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..cli.dosyalar import _json_oku, _kilit, atomik_json_yaz

DOSYA_ADI = "kutuphane.json"

# Geçmiş listesi sınırsız büyümesin; 200 kayıt aylarca izlemeye yetiyor ve
# dosya her oynatmada baştan yazıldığı için boyut doğrudan gecikme demek.
GECMIS_SINIRI = 200

# Bu oranı geçen izleme "bitti" sayılır: jenerik ve sonraki bölüm fragmanı
# çoğu zaman son %5-10'u kaplıyor, kullanıcı orada kapatınca bölüm
# izlenmemiş sayılmamalı.
BITTI_ORANI = 0.9

# Bundan kısa konum kaydedilmez: bölümü açıp hemen kapatan kullanıcıya bir
# sonraki açılışta "0:07'den devam" demek anlamsız.
ASGARI_KONUM = 10.0

VARSAYILAN: Dict[str, Any] = {"surum": 1, "seriler": {}, "gecmis": [], "konum": {}}


def kutuphane_yolu() -> str:
    """`kutuphane.json`'un yolu: veri kökü, `ayarlar.json`'un yanı.

    Kök `Dosyalar`'dan okunuyor (depodan çalışınca depo kökü, aksi hâlde
    ``~/Turkanime``); iki dosyanın ayrı köklere düşmesi "ayarlarım duruyor
    ama kitaplığım yok" demek olurdu.
    """
    from ..cli.dosyalar import Dosyalar
    return os.path.join(Dosyalar().ta_path, DOSYA_ADI)


def anahtar(kaynak: str, kimlik: str) -> str:
    return f"{kaynak}:{kimlik}"


def _duzelt(veri: Dict[str, Any]) -> Dict[str, Any]:
    """Elle bozulmuş/eski dosyada eksik ya da yanlış tipli bölümleri tamamla.

    `_json_oku` yalnızca kökün sözlük olduğunu garanti ediyor; "seriler"in
    liste yazıldığı bir dosya `setdefault` zincirinde AttributeError'la
    oynatma sonrası yazımı düşürürdü.
    """
    for alan, bos in (("seriler", {}), ("gecmis", []), ("konum", {})):
        if not isinstance(veri.get(alan), type(bos)):
            veri[alan] = type(bos)()
    veri["gecmis"] = [k for k in veri["gecmis"] if isinstance(k, dict)]
    return veri


def oku(yol: Optional[str] = None) -> Dict[str, Any]:
    """Kitaplığın tamamı; dosya yoksa ya da bozuksa boş kitaplık."""
    try:
        return _duzelt(_json_oku(yol or kutuphane_yolu(), VARSAYILAN))
    except Exception:
        return _duzelt({})


def _guncelle(degistir, yol: Optional[str] = None) -> bool:
    """Oku-değiştir-yaz'ı kilit altında tek parça çalıştır.

    Hata yutulur ve False döner: kitaplık yazımı oynatmayı/indirmeyi hiçbir
    zaman kesmemeli (bkz. `prefs.gecmis_kaydet`).
    """
    try:
        yol = yol or kutuphane_yolu()
        with _kilit(yol):
            veri = _duzelt(_json_oku(yol, VARSAYILAN))
            if degistir(veri) is False:
                return False
            atomik_json_yaz(yol, veri)
        return True
    except Exception:
        return False


def _seri(veri: Dict[str, Any], kaynak: str, kimlik: str, baslik: str,
          kapak: str, zaman: float) -> Dict[str, Any]:
    """Seri kaydını getir ya da kur; ad/kapak boş gelirse eskisi korunur."""
    seri = veri["seriler"].get(anahtar(kaynak, kimlik))
    if not isinstance(seri, dict):
        seri = veri["seriler"][anahtar(kaynak, kimlik)] = {
            "kaynak": kaynak, "kimlik": kimlik, "favori": False, "eklendi": zaman}
    seri["baslik"] = baslik or seri.get("baslik") or kimlik
    if kapak:
        seri["kapak"] = kapak
    return seri


# ── Yazma ────────────────────────────────────────────────────────────────────
def izleme_kaydet(kaynak: str, kimlik: str, baslik: str, bolum_slug: str,
                  bolum_baslik: str = "", kapak: str = "",
                  zaman: Optional[float] = None) -> bool:
    """Başarılı bir oynatmayı kaydet: "izlemeye devam et" + geçmiş.

    Aynı bölüm yeniden izlenince geçmişte ÖNE taşınır, ikinci kopya olmaz:
    `gecmis.json`'daki izlendi listesi yeniden izlemede sırayı değiştirmediği
    için "son izlenen" aslında son İLK izlemeydi.
    """
    if not (kaynak and kimlik and bolum_slug):
        return False
    zaman = time.time() if zaman is None else float(zaman)

    def degistir(veri):
        seri = _seri(veri, kaynak, kimlik, baslik, kapak, zaman)
        seri["son"] = {"bolum_slug": bolum_slug,
                       "bolum_baslik": bolum_baslik or bolum_slug,
                       "zaman": zaman}
        kayit = {"kaynak": kaynak, "kimlik": kimlik, "baslik": seri["baslik"],
                 "bolum_slug": bolum_slug,
                 "bolum_baslik": bolum_baslik or bolum_slug, "zaman": zaman}
        eski = [k for k in veri["gecmis"]
                if (k.get("kaynak"), k.get("kimlik"), k.get("bolum_slug"))
                != (kaynak, kimlik, bolum_slug)]
        veri["gecmis"] = [kayit] + eski[:GECMIS_SINIRI - 1]

    return _guncelle(degistir)


def favori_ayarla(kaynak: str, kimlik: str, favori: bool, baslik: str = "",
                  kapak: str = "", zaman: Optional[float] = None) -> bool:
    """Seriyi favorilere ekle/çıkar.

    Favoriden çıkarılan ve hiç izlenmemiş seri kaydı tamamen silinir; aksi
    hâlde "Favoriler"den kaldırılan seri kitaplıkta görünmez bir artık olarak
    kalırdı.
    """
    if not (kaynak and kimlik):
        return False
    zaman = time.time() if zaman is None else float(zaman)

    def degistir(veri):
        if not favori:
            seri = veri["seriler"].get(anahtar(kaynak, kimlik))
            if not isinstance(seri, dict):
                return False
            seri["favori"] = False
            if not seri.get("son"):
                del veri["seriler"][anahtar(kaynak, kimlik)]
            return True
        seri = _seri(veri, kaynak, kimlik, baslik, kapak, zaman)
        seri["favori"] = True
        seri["favori_zaman"] = zaman
        return True

    return _guncelle(degistir)


def konum_kaydet(kaynak: str, kimlik: str, bolum_slug: str, konum: float,
                 sure: Optional[float] = None,
                 zaman: Optional[float] = None) -> bool:
    """Bölümün kaldığı saniyeyi yaz.

    Anahtar oynatılan ADRES değil (kaynak, kimlik, bölüm): mpv'nin kendi
    `--save-position-on-quit`'i adrese bağlı ve adresler kaynak başına her
    istekte değişiyor (Tranimaci `?token=`, Anizle HLS `?md5=`) ya da
    `best_video` başka bir aday seçiyor; konum her seferinde kayboluyordu.
    """
    if not (kaynak and kimlik and bolum_slug):
        return False
    try:
        konum = float(konum)
    except (TypeError, ValueError):
        return False
    zaman = time.time() if zaman is None else float(zaman)

    def degistir(veri):
        seri = veri["konum"].setdefault(anahtar(kaynak, kimlik), {})
        if not isinstance(seri, dict):
            seri = veri["konum"][anahtar(kaynak, kimlik)] = {}
        seri[bolum_slug] = {"konum": round(konum, 1), "zaman": zaman,
                            "sure": round(float(sure), 1) if sure else None}

    return _guncelle(degistir)


def konum_sil(kaynak: str, kimlik: str, bolum_slug: str) -> bool:
    """Bölüm bittiyse konumu unut: bir sonraki açılış baştan başlamalı."""
    def degistir(veri):
        seri = veri["konum"].get(anahtar(kaynak, kimlik))
        if not isinstance(seri, dict) or bolum_slug not in seri:
            return False
        del seri[bolum_slug]
        if not seri:
            del veri["konum"][anahtar(kaynak, kimlik)]
        return True

    return _guncelle(degistir)


# ── Okuma ────────────────────────────────────────────────────────────────────
def _zaman(kayit: Any, *yol: str) -> float:
    for alan in yol:
        kayit = kayit.get(alan) if isinstance(kayit, dict) else None
    try:
        return float(kayit or 0)
    except (TypeError, ValueError):
        return 0.0


def _seriler(veri: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in veri["seriler"].values()
            if isinstance(s, dict) and s.get("kaynak") and s.get("kimlik")]


def seri_sayisi(veri: Optional[Dict[str, Any]] = None) -> int:
    """Kitaplıktaki seri sayısı (izlenmiş ya da favori; ana sayfa hero'su)."""
    return len(_seriler(veri if veri is not None else oku()))


def devam_listesi(veri: Optional[Dict[str, Any]] = None,
                  sinir: Optional[int] = None) -> List[Dict[str, Any]]:
    """İzlenmiş seriler, en son izlenen başta."""
    veri = veri if veri is not None else oku()
    liste = sorted((s for s in _seriler(veri) if isinstance(s.get("son"), dict)),
                   key=lambda s: _zaman(s, "son", "zaman"), reverse=True)
    return liste[:sinir] if sinir else liste


def favoriler(veri: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Favori seriler, en son eklenen başta."""
    veri = veri if veri is not None else oku()
    return sorted((s for s in _seriler(veri) if s.get("favori")),
                  key=lambda s: _zaman(s, "favori_zaman"), reverse=True)


def favori_mi(kaynak: str, kimlik: str,
              veri: Optional[Dict[str, Any]] = None) -> bool:
    veri = veri if veri is not None else oku()
    seri = veri["seriler"].get(anahtar(kaynak, kimlik))
    return bool(isinstance(seri, dict) and seri.get("favori"))


def gecmis_listesi(veri: Optional[Dict[str, Any]] = None,
                   sinir: Optional[int] = None) -> List[Dict[str, Any]]:
    """Bölüm bazında izleme geçmişi, en yeni başta."""
    veri = veri if veri is not None else oku()
    liste = [k for k in veri["gecmis"] if k.get("kaynak") and k.get("kimlik")]
    return liste[:sinir] if sinir else liste


def konum_getir(kaynak: str, kimlik: str, bolum_slug: str,
                veri: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Bölümün kayıtlı konumu (``{"konum", "sure", "zaman"}``) ya da None."""
    veri = veri if veri is not None else oku()
    seri = veri["konum"].get(anahtar(kaynak, kimlik))
    kayit = seri.get(bolum_slug) if isinstance(seri, dict) else None
    if not isinstance(kayit, dict) or _zaman(kayit, "konum") <= 0:
        return None
    return kayit


# ── Saf yardımcılar ──────────────────────────────────────────────────────────
def sure_metni(saniye: Any) -> str:
    """``734.2`` → ``"12:14"``; saat varsa ``"1:02:03"``."""
    try:
        toplam = max(0, int(float(saniye)))
    except (TypeError, ValueError):
        return "0:00"
    saat, kalan = divmod(toplam, 3600)
    dakika, sn = divmod(kalan, 60)
    return f"{saat}:{dakika:02d}:{sn:02d}" if saat else f"{dakika}:{sn:02d}"


def sonraki_bolum(bolumler: Sequence[Any], izlenenler: Iterable[Any]) -> Optional[int]:
    """Sıradaki bölümün İNDEKSİ; seri bitmişse None.

    Hiç izlenmemişse ilk bölüm. İzlemeler sırasız olabilir (kullanıcı 3'ü
    sonra 1'i izledi): "sıradaki" en İLERİDEKİ izlenen bölümün ardından gelir,
    listenin başındaki boşluk kullanıcının atladığı bölümdür, ona geri
    dönülmez.
    """
    izlenen = set(izlenenler)
    if not bolumler:
        return None
    en_ileri = -1
    for sira, bolum in enumerate(bolumler):
        if bolum in izlenen:
            en_ileri = sira
    sira = en_ileri + 1
    return sira if sira < len(bolumler) else None


def bitti_mi(konum: Any, sure: Any, sebep: str = "") -> bool:
    """Oynatma raporuna göre bölüm sonuna gelindi mi?"""
    if sebep == "eof":
        return True
    try:
        return float(sure) > 0 and float(konum) / float(sure) >= BITTI_ORANI
    except (TypeError, ValueError, ZeroDivisionError):
        return False


__all__ = ["DOSYA_ADI", "GECMIS_SINIRI", "BITTI_ORANI", "ASGARI_KONUM",
           "kutuphane_yolu", "anahtar", "oku", "izleme_kaydet", "favori_ayarla",
           "konum_kaydet", "konum_sil", "seri_sayisi", "devam_listesi", "favoriler",
           "favori_mi", "gecmis_listesi", "konum_getir", "sure_metni",
           "sonraki_bolum", "bitti_mi"]
