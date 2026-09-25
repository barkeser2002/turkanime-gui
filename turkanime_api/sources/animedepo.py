"""
AnimeDepo sağlayıcı (KebabLord upstream V10 driver'ından uyarlandı).

turkanime.tv kapandı; sitenin anime/bölüm/video kayıtlarının yaşayan tek kopyası
AnimeDepo adlı statik JSON arşivi. Uygulamada bu modül "TürkAnime" kaynağıdır
(`sources/kayit.py`; eski "AnimeDepo" adı takma ad olarak okunur). Gerçek bir
arama endpoint'i yoktur
(gözat-tabanlı). Bu modül fork'un fonksiyon-stili kaynak sözleşmesine
(search / episodes / streams) uyar ve `dizin.json` üzerinde **yerel fuzzy
arama** yapar.

Arşiv yapısı:
- dizin.json                              -> {"index": {grup: {slug: {"title": ...}}}}
- animeler/{slug}/info.json               -> anime metadata
- animeler/{slug}/bolumler.json           -> [[bolum_slug, baslik], ...]
- animeler/{slug}/{bolum_slug}.json       -> [{url|mask|path, player, fansub, alive}, ...]

Bölüm kimliği bu modülde "anime_slug/bolum_slug" bileşik biçiminde taşınır;
böylece `get_episode_streams` doğru JSON yolunu kurabilir.

Arşiv NEREDEN okunur (`arsiv_konumu`)? Önce yerel, sonra uzak:

1. `TURKANIME_ARSIV_DIZIN` ortam değişkeni, sonra `ayarlar.json` →
   `animedepo_dizin` (kullanıcının gösterdiği klasör)
2. Kullanıcının indirdiği tam arşiv: `<veri kökü>/cevrimdisi_arsiv`
3. Depodan çalıştırılıyorsa depoya alınmış ayna: `<depo>/arsiv`
4. Uzak aynalar sırayla: özel adres (`TURKANIME_ARSIV_URL` / `animedepo_url`)
   → GitLab (`BASE_URL`) → bu projenin GitHub kopyası (`GITHUB_AYNA_URL`)

Yerel bir klasör ancak içinde okunabilir bir `dizin.json` varsa sayılır. Yerel
okumada tek bir HTTP isteği bile atılmaz. Uzaktan başarıyla gelen her dosya
`<veri kökü>/arsiv_onbellek/` altına aynı göreli yolla yazılır; bütün aynalar
düştüğünde oradan okunur (çevrimdışı).

"Veri kökü" `cli.dosyalar.Dosyalar` ile aynı kural: `~/Turkanime`, çalışma
dizini bir git deposuysa (.git) orası. DİKKAT: depoda veri kökü depo KÖKÜDÜR;
indirilenler bu yüzden `arsiv/` değil `cevrimdisi_arsiv/` adıyla duruyor —
`arsiv/` commit'lenmiş aynadır, üstüne yazılmamalı.

Ayarlar sayfasının ("Çevrimdışı arşiv (TürkAnime)") kullandığı uçlar:
`arsiv_durumu` (ağsız özet; silinemeyen eski kopyalar dahil),
`tam_arsiv_indir(ilerleme, iptal, asama)` ve `indirilen_arsivi_sil`
(yalnızca `cevrimdisi_arsiv/`). `sifirla` GUI thread'inden çağrılabilir:
hiçbir ağ/disk bekleyişini beklemez (bkz. kilitlerin açıklaması).

Okunamama sessiz değil: `search_animedepo` dizin hiçbir yerden okunamazsa
`ArsivOkunamadi` fırlatır (boş arşiv hata değildir); `dizin()` eski
sözleşmesiyle `{}` döndürmeye devam eder. Bölüm listesi ve akışlar da aynı
kural: kayıt arşivde YOKSA boş liste (yerelde dosya yok / bütün aynalar
404), arşive ULAŞILAMADIYSA `ArsivOkunamadi` (bkz. `_kaydi_oku`).

Windows: arşivde göreli yolu 311 karaktere varan dosyalar var; diske giden
her yol `arsiv_paketi.disk_yolu`'ndan (`\\\\?\\` öneki) geçiyor.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

try:
    from curl_cffi import requests as _requests  # type: ignore
    _HAS_CURL = True
except ImportError:  # pragma: no cover - kütüphane yoksa düz requests'e düş
    import requests as _requests  # type: ignore[no-redef]
    _HAS_CURL = False

from ..common import arsiv_paketi as paket
from ..common.oynatici_onceligi import oncelik_anahtari
from ..common.title_match import baslik_normalize, siralama_skoru

try:  # arama kalitesi/hızı için; yoksa difflib'e düşülür (title_match ile aynı)
    from rapidfuzz import fuzz as _rf_fuzz  # type: ignore
except ImportError:  # pragma: no cover
    _rf_fuzz = None


BASE_URL = "https://gitlab.com/AnimeDepo/animedepo/-/raw/master"
# Arşivin bu depodaki kopyası (`arsiv/`). GitLab deposu kaybolursa veri buradan
# gelmeye devam etsin diye depoya alındı; aynı dosya düzeni, aynı göreli yollar.
GITHUB_AYNA_URL = "https://raw.githubusercontent.com/barkeser2002/turkanime-gui/main/arsiv"

ORTAM_ANAHTARI = "TURKANIME_ARSIV_URL"
AYAR_ANAHTARI = "animedepo_url"
DIZIN_ORTAM_ANAHTARI = "TURKANIME_ARSIV_DIZIN"
DIZIN_AYAR_ANAHTARI = "animedepo_dizin"

HTTP_TIMEOUT = 10
# Tam arşiv ~230 MB (sıkıştırılmış; açılınca ~0,5 GB): toplam süre sınırı
# koymak yavaş bağlantıda indirmeyi yarıda keser. (bağlanma, okuma) çifti akış
# kipinde "60 sn hiç veri gelmezse vazgeç" demek (curl_cffi bunu
# LOW_SPEED_TIME ile uyguluyor).
INDIRME_ZAMAN_ASIMI = (15, 60)

CEVRIMDISI_KLASOR = "cevrimdisi_arsiv"
ONBELLEK_KLASOR = "arsiv_onbellek"
# Depodan çalıştırılırken commit'lenmiş ayna. Paketlenmiş (PyInstaller) sürümde
# bu yol geçici açma klasörünün dışına düşer, orada `arsiv/` yok → atlanır.
DEPO_ARSIVI = Path(__file__).resolve().parents[2] / "arsiv"


@dataclass(frozen=True)
class TamArsivKaynagi:
    """Tam arşivin indirilebileceği bir tar.gz paketi."""

    ad: str
    url: str
    ust_desen: str               # paketin tek üst klasörü (fnmatch)
    alt_klasor: Optional[str]    # yalnızca bu alt ağacı aç (None: hepsi)
    depo: str                    # KAYNAK.json'a yazılacak depo adresi
    dal: str


TAM_ARSIV_KAYNAKLARI: Tuple[TamArsivKaynagi, ...] = (
    TamArsivKaynagi(
        "GitLab",
        "https://gitlab.com/AnimeDepo/animedepo/-/archive/master/animedepo-master.tar.gz",
        "animedepo-master*", None,
        "https://gitlab.com/AnimeDepo/animedepo", "master"),
    # Yedek: bu deponun tamamı geliyor ama yalnızca `arsiv/` açılıyor.
    TamArsivKaynagi(
        "GitHub",
        "https://codeload.github.com/barkeser2002/turkanime-gui/tar.gz/refs/heads/main",
        "turkanime-gui-*", "arsiv",
        "https://github.com/barkeser2002/turkanime-gui", "main"),
)


@dataclass(frozen=True)
class ArsivKonumu:
    """Arşivin okunduğu yer.

    ``kaynak``: "ortam" | "ayar" | "indirilen" | "depo" | "uzak".
    ``dizin``: yerel klasör; uzaktan okunuyorsa ``None``.
    """

    kaynak: str
    dizin: Optional[Path] = None

    @property
    def yerel(self) -> bool:
        return self.dizin is not None


@dataclass(frozen=True)
class ArsivDurumu:
    """Etkin arşivin özeti — Ayarlar sayfası bunu gösteriyor (bkz. `arsiv_durumu`).

    ``anime_sayisi``/``son_guncelleme`` bilinmiyorsa ``None``: uzak aynadan
    okunuyor ve dizin henüz hiç inmemiş olabilir; durum göstermek için ağa
    çıkılmaz. ``son_guncelleme`` dizin.json'daki ``last_update`` (unix zamanı).
    ``ortam_dizini``/``ayar_dizini`` kullanıcının gösterdiği klasörlerin HAM
    değerleri: geçersiz olduklarında konum sessizce sıradakine geçiyor ve
    arayüz bunu kullanıcıya söyleyebilsin diye taşınıyorlar.
    """

    konum: ArsivKonumu
    adres: str
    anime_sayisi: Optional[int]
    son_guncelleme: Optional[int]
    indirilen_dizini: Path
    indirilen_var: bool
    ortam_dizini: str = ""
    ayar_dizini: str = ""
    onbellekten: bool = False    # uzakta sayılar disk önbelleğindeki kopyadan
    # Önceki bir güncellemenin silinemeyen eski kopyaları (`.cevrimdisi_arsiv-
    # eski-*`). Gizli adlı ve her biri ~0,5 GB; kullanıcı görmezse bilemez.
    kalintilar: Tuple[Path, ...] = ()

    @property
    def kaynak(self) -> str:
        return self.konum.kaynak


# Kilitler. KURAL: ağ ya da disk bekleyişi boyunca yalnızca YÜKLEYİCİ
# kilitleri tutulur; `sifirla`nın aldığı `_durum_kilidi` hiçbir G/Ç boyunca
# tutulmaz. Neden: `sifirla` GUI thread'inden çağrılıyor ("Varsayılana dön",
# "Klasör seç"). Eskiden `dizin()` tek kilidi uzak okumanın TAMAMI boyunca
# (3 ayna × 10 sn + önbellek) tutuyordu ve `sifirla` aynı kilidi bekliyordu:
# arka planda bir arama yavaş aynalara takılmışken pencere ~30 sn donuyordu.
#
# - `_dizin_yukleme_kilidi`: dizin yükleyicilerini sıraya koyar (aynı anda on
#   arama on kez indirmesin). Yalnızca `_dizin_getir` alır.
# - `_konum_cozme_kilidi`: konum çözücülerini sıraya koyar (her aday
#   klasörde megabaytlık dizin.json ayrıştırılıyor). Yalnızca `arsiv_konumu`.
# - `_durum_kilidi`: önbellekleri ve `_nesil`'i okuyup yazmak için; içinde
#   hiçbir G/Ç yok, en içteki kilit.
#
# Sıra (iç içe alındıklarında): yükleme → çözme → durum.
#
# `_nesil`: `sifirla` her çağrıda bir artırır. Kilitsiz yürüyen bir okuma
# başlarken nesli not eder; sonucu ancak nesil HÂLÂ aynıysa önbelleğe yazar.
# Böylece okuma sürerken yapılan sıfırlama (yeni klasör seçildi) eski
# konumdan gelen sonucla ezilmez.
_dizin_yukleme_kilidi = Lock()
_konum_cozme_kilidi = Lock()
_durum_kilidi = Lock()
_nesil = 0
_dizin_cache: Optional[Dict[str, Any]] = None
_konum_cache: Optional[ArsivKonumu] = None
_taban_cache: Optional[str] = None
# path → (ayna, ETag). Koşullu istek için: dizin.json arşivin en çok indirilen
# dosyası ve turların çoğunda değişmiyor. ETag'i hangi ayna verdiyse koşullu
# istek ona gider; başka aynanın ETag'i anlamsızdır.
_etag_defteri: Dict[str, Tuple[str, str]] = {}
# path → (mtime_ns, boyut). Yerel arşivde `dizin(tazele=True)` megabaytlık
# dosyayı ancak değiştiyse yeniden ayrıştırsın diye (ETag'in yerel karşılığı).
_yerel_imzalar: Dict[str, Tuple[int, int]] = {}
# Arama tablosu: (dizin nesnesi, [(slug, başlık, normalize başlık, normalize
# slug), ...]). Dizin nesnesinin KENDİSİ tutuluyor (id değil): tutulan nesne
# çöpe gitmez, dolayısıyla aynı kimliği yeni bir sözlük alamaz. `dizin()`
# değişince (tazele, sifirla, tam arşiv indirme) tablo yeniden kurulur.
_arama_onbellek: Optional[Tuple[Any, List[Tuple[str, str, str, str]]]] = None

_YOK = object()


# ─────────────────────────────────────────────────────────────────────────────
# Veri kökü ve ayarlar
# ─────────────────────────────────────────────────────────────────────────────
def veri_koku() -> Path:
    """Kullanıcı verisinin kökü — `Dosyalar` ile aynı kural, ama salt okunur.

    `Dosyalar()` örneklemiyoruz: yapıcısı dosya yaratıyor, eksik ayarları
    yazıyor ve gerekirse `user_id` üretiyor. Bir yol çözmek için kullanıcının
    ayar dosyasına yazmak yanlış olurdu (sunucu/CI ortamında da istenmez).
    """
    cwd = Path.cwd()
    return cwd if (cwd / ".git").is_dir() else Path.home() / "Turkanime"


def indirilen_arsiv_dizini() -> Path:
    """`tam_arsiv_indir`'in varsayılan hedefi."""
    return veri_koku() / CEVRIMDISI_KLASOR


def onbellek_dizini() -> Path:
    """Uzaktan gelen dosyaların disk önbelleği."""
    return veri_koku() / ONBELLEK_KLASOR


def _ayar_dosyasi() -> Optional[Path]:
    yol = veri_koku() / "ayarlar.json"
    return yol if yol.is_file() else None


def _ayar_oku(anahtar: str) -> str:
    """`ayarlar.json`'dan dizgi ayar; yok/bozuk/dizgi değilse boş dizgi."""
    yol = _ayar_dosyasi()
    if yol is None:
        return ""
    try:
        deger = json.loads(yol.read_text(encoding="utf-8")).get(anahtar)
    except Exception:
        return ""           # bozuk/okunamayan ayar varsayılanı bozmasın
    return deger.strip() if isinstance(deger, str) else ""


# ─────────────────────────────────────────────────────────────────────────────
# Arşiv konumu
# ─────────────────────────────────────────────────────────────────────────────
def _konumu_coz() -> ArsivKonumu:
    adaylar: List[Tuple[str, Path]] = []
    ortam = (os.environ.get(DIZIN_ORTAM_ANAHTARI) or "").strip()
    if ortam:
        adaylar.append(("ortam", Path(ortam).expanduser()))
    ayar = _ayar_oku(DIZIN_AYAR_ANAHTARI)
    if ayar:
        adaylar.append(("ayar", Path(ayar).expanduser()))
    adaylar.append(("indirilen", indirilen_arsiv_dizini()))
    adaylar.append(("depo", DEPO_ARSIVI))
    for kaynak, dizin_yolu in adaylar:
        # Gösterilen klasör geçersizse (silinmiş, yarım kopya, yanlış klasör)
        # sessizce sıradakine geçilir: yanlış bir ayar uygulamayı arşivsiz
        # bırakmamalı, uzak ayna hâlâ orada.
        if paket.arsiv_gecerli_mi(dizin_yolu):
            return ArsivKonumu(kaynak, dizin_yolu)
    return ArsivKonumu("uzak", None)


def arsiv_konumu() -> ArsivKonumu:
    """Arşivin okunacağı yer (süreç boyunca bir kez çözülür; bkz. `sifirla`).

    Çözüm (dört aday klasöre kadar dizin.json ayrıştırma) `_durum_kilidi`
    DIŞINDA yapılır; bkz. kilitlerin açıklaması.
    """
    global _konum_cache
    with _durum_kilidi:
        if _konum_cache is not None:
            return _konum_cache
    with _konum_cozme_kilidi:
        with _durum_kilidi:
            if _konum_cache is not None:  # sırada beklerken başkası çözdü
                return _konum_cache
            nesil = _nesil
        konum = _konumu_coz()
        with _durum_kilidi:
            if _nesil == nesil:
                _konum_cache = konum
        # Arada sıfırlandıysa önbelleğe girmedi; bu çağrı yine de çözüldüğü
        # andaki konumu alır (sıfırlamadan ÖNCE başlamış bir okuma).
        return konum


def taban_url() -> str:
    """İlk denenecek uzak arşiv kökü: özel adres, yoksa GitLab (`BASE_URL`)."""
    global _taban_cache
    if _taban_cache:
        return _taban_cache
    aday = (os.environ.get(ORTAM_ANAHTARI) or "").strip() or _ayar_oku(AYAR_ANAHTARI)
    _taban_cache = (aday or BASE_URL).rstrip("/")
    return _taban_cache


def uzak_aynalar() -> List[str]:
    """Sırayla denenecek uzak kökler: özel → GitLab → GitHub (tekrarsız).

    Özel adres (`turkanime_server/yayinci`'nın yayınladığı arşiv) başarısız
    olursa bile GitLab/GitHub'a düşülür: kullanıcının yazdığı adresin ölmesi
    uygulamayı arşivsiz bırakmamalı.
    """
    sira: List[str] = []
    for aday in (taban_url(), BASE_URL, GITHUB_AYNA_URL):
        aday = aday.rstrip("/")
        if aday not in sira:
            sira.append(aday)
    return sira


def sifirla() -> None:
    """Çözülmüş konumu/adresi ve ona bağlı cache'leri unut.

    Ayar değişince, tam arşiv indirilince ya da testlerde çağrılır. GUI
    thread'inden çağrılabilir: hiçbir G/Ç'yi beklemez (yalnızca
    `_durum_kilidi`), sürmekte olan okumaların sonucu `_nesil` sayesinde
    önbelleğe giremez.
    """
    global _taban_cache, _dizin_cache, _konum_cache, _arama_onbellek, _nesil
    with _durum_kilidi:
        _nesil += 1
        _taban_cache = None
        _dizin_cache = None
        _konum_cache = None
        _arama_onbellek = None
        _etag_defteri.clear()
        _yerel_imzalar.clear()


def taban_url_sifirla() -> None:
    """Geriye uyum: eski adı. Artık konum dahil her şeyi sıfırlar (`sifirla`)."""
    sifirla()


# ─────────────────────────────────────────────────────────────────────────────
# Dosya okuma: yerel → uzak aynalar → disk önbelleği
# ─────────────────────────────────────────────────────────────────────────────
def _session():
    if _HAS_CURL:
        return _requests.Session(impersonate="chrome131")
    sess = _requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0"
    })
    return sess


def _goreli(path: str) -> str:
    """API'ye verilen yolu normalize et ve güvenliğini denetle.

    Baştaki "/" eskiden de sessizce atılıyordu (`fetch_json("/dizin.json")`
    çalışırdı); o uyum korunuyor. Sonrası `goreli_parcalar`'ın kuralları:
    ``..``, sürücü, ters bölü → ``ValueError``. Slug'lar arşivden geliyor ve
    arşiv bizim kontrolümüzde değil.
    """
    return "/".join(paket.goreli_parcalar(str(path).lstrip("/")))


def _yerel_yol(kok: Path, goreli: str) -> Path:
    """Yerel arşivde dosyanın yolu: önce disk adı, yoksa özgün ad.

    Yerel kopyalar Windows'ta geçersiz karakter taşıyan adları ``%XX`` ile
    tutuyor (`paket.disk_adi`: ``Movie 6: …`` → ``Movie 6%3A …``). Bu
    sürümden ÖNCE Linux/macOS'ta indirilmiş bir tam arşivde ise ad özgün
    hâliyle duruyor; onu da bulmak için Windows dışında özgün ada düşülür
    (Windows'ta `:`'li ad zaten yazılamamıştı).
    """
    disk = paket.guvenli_birlestir(kok, paket.disk_goreli(goreli))
    if os.name == "nt" or os.path.exists(paket.disk_yolu(disk)):
        return disk
    ozgun = paket.guvenli_birlestir(kok, goreli)
    return ozgun if os.path.exists(paket.disk_yolu(ozgun)) else disk


def _uzak_url(taban: str, goreli: str) -> str:
    """Aynadaki dosyanın adresi.

    GitHub aynası bu deponun `arsiv/` klasörü: adları disk biçiminde
    (`paket.disk_adi`). GitLab ve özel adresler özgün adları taşıyor. Yol
    her iki durumda kodlanıyor: ``:`` → ``%3A``, disk adındaki ``%`` →
    ``%25`` (yoksa sunucu ``%3A``'yı `:` diye çözer ve dosyayı bulamaz).
    """
    taban = taban.rstrip("/")
    yol = paket.disk_goreli(goreli) if taban == GITHUB_AYNA_URL.rstrip("/") else goreli
    return f"{taban}/{quote(yol, safe='/')}"


def _yerelden_oku(kok: Path, goreli: str) -> Any:
    """Yerel arşivden oku. Dosya yoksa `FileNotFoundError` ("arşivde yok").

    Yol diske `paket.disk_yolu` ile gidiyor: Windows'ta arşivin uzun adlı
    dosyaları (göreli yolu 311 karaktere varan) MAX_PATH'i aşıyor ve önek
    olmadan "yok" görünüyordu (bkz. `arsiv_paketi`'ndeki uzun yol notu).

    Dosya yoksa ÖNCE arşivin kendisi hâlâ yerinde mi diye bakılır: konum
    süreç başında çözülüyor; sonradan silinen/çıkarılan (USB disk) bir
    arşivde her dosya "yok" olur ve her anime "bölümü yok" görünürdü. Kök
    kaybolduysa bu bir OKUMA hatasıdır (`ArsivHatasi`), "yok" değil.
    """
    yol = _yerel_yol(kok, goreli)
    try:
        with open(paket.disk_yolu(yol), encoding="utf-8") as fp:
            veri = json.load(fp)
    except FileNotFoundError as hata:
        if not os.path.isfile(paket.disk_yolu(Path(kok) / paket.DIZIN_DOSYASI)):
            raise paket.ArsivHatasi(
                f"yerel arşiv klasörü artık yok ya da boşaldı ({kok})") from hata
        raise
    try:
        st = os.stat(paket.disk_yolu(yol))
        _yerel_imzalar[goreli] = (st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    return veri


def _onbellege_yaz(goreli: str, veri: Any) -> None:
    """Uzaktan gelen veriyi diske yaz; hata yutulur (önbellek en iyi çaba).

    Ham yanıt baytı değil yeniden serileştirilmiş JSON yazılıyor: `r.json()`
    zaten ayrıştırıldı, geçerli olduğu kesin — bozuk bir yanıt önbelleğe hiç
    giremez.
    """
    try:
        yol = paket.guvenli_birlestir(onbellek_dizini(), paket.disk_goreli(goreli))
        paket.atomik_bayt_yaz(yol, json.dumps(veri, ensure_ascii=False).encode("utf-8"))
    except (OSError, ValueError, TypeError):
        pass


def _onbellekten_oku(goreli: str) -> Any:
    try:
        yol = paket.guvenli_birlestir(onbellek_dizini(), paket.disk_goreli(goreli))
        with open(paket.disk_yolu(yol), encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return _YOK


def _basarili_yanit(goreli: str, taban: str, yanit: Any, veri: Any) -> None:
    etag = (getattr(yanit, "headers", None) or {}).get("ETag")
    if etag:
        _etag_defteri[goreli] = (taban, etag)
    _onbellege_yaz(goreli, veri)


class ArsivdeYok(paket.ArsivHatasi):
    """İstenen dosya arşivde YOK: yanıt veren her ayna 404/410 dedi.

    "Okunamadı"nın tersi: arşive ulaşıldı, kayıt yok. Dizinde olup klasörü
    olmayan animeler gerçek arşivde de var; onların bölüm listesi boş liste
    olmalı, hata değil. Yerel arşivde aynı durumun karşılığı
    `FileNotFoundError` (bkz. `_arsivde_yok_mu`).
    """


# Aynanın "bu dosya bende yok" dediği HTTP kodları. Diğer her kod (429 hız
# sınırı, 5xx, 403) aynaya ULAŞILAMADI demek: dosyanın var olup olmadığı
# bilinmiyor.
_YOK_KODLARI = (404, 410)


def _uzaktan_getir(goreli: str, atla: Optional[str] = None) -> Any:
    """Aynaları sırayla dene; hepsi düşerse disk önbelleğinden oku.

    Hiçbiri veremezse hangi istisnanın yükseldiği önemli (bkz.
    `get_anime_episodes`): BÜTÜN aynalar 404 dediyse `ArsivdeYok`; en az
    birine ulaşılamadıysa (bağlantı, zaman aşımı, 429/5xx, bozuk yanıt) o
    aynanın hatası. Tek bir 404 yetmez: GitLab'a ulaşılamazken GitHub
    aynası 404 derse (ayna geride kalmış olabilir) dosyanın
    gerçekten olmadığı bilinmiyor — asıl haber GitLab'ın hatası.
    """
    son_hata: Optional[BaseException] = None   # 404 DIŞINDAKİ son hata
    yok_diyen = 0
    oturum = None
    for taban in uzak_aynalar():
        if taban == atla:
            continue
        try:
            if oturum is None:
                oturum = _session()
            yanit = oturum.get(_uzak_url(taban, goreli), timeout=HTTP_TIMEOUT)
            if getattr(yanit, "status_code", None) in _YOK_KODLARI:
                yok_diyen += 1
                continue
            yanit.raise_for_status()
            veri = yanit.json()
        except Exception as hata:          # zaman aşımı, 5xx, HTML hata sayfası…
            son_hata = hata
            continue
        _basarili_yanit(goreli, taban, yanit, veri)
        return veri
    onbellekte = _onbellekten_oku(goreli)
    if onbellekte is not _YOK:
        return onbellekte
    if son_hata is not None:
        raise son_hata
    if yok_diyen:
        raise ArsivdeYok(f"{goreli} arşivde yok ({yok_diyen} ayna 404 döndü)")
    raise paket.ArsivHatasi(f"{goreli} hiçbir aynadan alınamadı")


def fetch_json(path: str) -> Any:
    """AnimeDepo JSON dosyasını getir: yerel arşivden, yoksa uzak aynalardan.

    Yerel arşiv varsa doğrudan dosya okunur, ağa çıkılmaz. Uzakta sırayla
    özel adres → GitLab → GitHub denenir; hepsi düşerse disk önbelleği.
    Dosya alınamazsa istisna: arşivde YOKSA yerelde `FileNotFoundError`,
    uzakta `ArsivdeYok`; ULAŞILAMADIYSA sebebin kendisi (bağlantı hatası,
    HTTP 5xx/429, bozuk JSON…). Ayrımı `_kaydi_oku` kullanıyor.
    """
    goreli = _goreli(path)
    konum = arsiv_konumu()
    if konum.dizin is not None:
        return _yerelden_oku(konum.dizin, goreli)
    return _uzaktan_getir(goreli)


def _kosullu_getir(path: str) -> Tuple[Any, bool]:
    """``(veri, degismedi)`` — değişmediği bilinen dosyayı yeniden indirme.

    Uzakta: ETag biliniyorsa onu veren aynaya `If-None-Match` ile sorar.
    Yerelde: dosyanın (mtime, boyut) imzası aynıysa yeniden ayrıştırmaz.
    Bilinen imza yoksa düz `fetch_json`'a düşer; böylece `fetch_json`'ı
    sahteleyen çağrı yolları (testler) bozulmaz.
    """
    goreli = _goreli(path)
    konum = arsiv_konumu()
    if konum.dizin is not None:
        imza = _yerel_imzalar.get(goreli)
        if imza is not None:
            try:
                st = os.stat(paket.disk_yolu(_yerel_yol(konum.dizin, goreli)))
                if (st.st_mtime_ns, st.st_size) == imza:
                    return None, True
            except OSError:
                pass
        return fetch_json(goreli), False

    kayit = _etag_defteri.get(goreli)
    if not kayit:
        return fetch_json(goreli), False
    taban, etag = kayit
    try:
        yanit = _session().get(_uzak_url(taban, goreli), timeout=HTTP_TIMEOUT,
                               headers={"If-None-Match": etag})
        if yanit.status_code == 304:
            return None, True
        yanit.raise_for_status()
        veri = yanit.json()
    except Exception:
        # ETag'i veren ayna düştü: diğer aynaları (ve önbelleği) dene.
        return _uzaktan_getir(goreli, atla=taban), False
    _basarili_yanit(goreli, taban, yanit, veri)
    return veri, False


class ArsivOkunamadi(paket.ArsivHatasi):
    """Arşivin dizini hiçbir yerden okunamadı (yerel okuma hatası, aynalar
    yanıt vermedi ve disk önbelleği boş).

    "Arşiv boş" ile karıştırılmasın diye ayrı: eskiden ikisi de sessizce 0
    sonuç veriyordu ve kullanıcı "Aradığınız anime bulunamadı" görüyordu —
    oysa arama hiç yapılamamıştı. Paketlenmiş uygulamada yerel arşiv yok,
    yani ağ gidince bu yol olağan durum.
    """


def _okuma_hatasi_metni(hata: BaseException) -> str:
    """Okunamama sebebini konumuyla birlikte anlat."""
    sebep = str(hata) or type(hata).__name__
    konum = arsiv_konumu()
    if konum.dizin is not None:
        return f"TürkAnime arşivi okunamadı: yerel arşiv ({konum.dizin}) okunamadı — {sebep}"
    return ("TürkAnime arşivi okunamadı: yerel arşiv yok, uzak aynalar yanıt vermedi "
            f"ve disk önbelleğinde kopya yok — {sebep}")


def _arsivde_yok_mu(hata: BaseException) -> bool:
    """Hata "dosya arşivde yok" mu (``True``), "arşive ulaşılamadı" mı?

    Yerelde `FileNotFoundError` (kök yerindeyken; bkz. `_yerelden_oku`),
    uzakta `ArsivdeYok` (bütün aynalar 404). Geri kalan her şey — bağlantı,
    zaman aşımı, 429/5xx, bozuk JSON, okunamayan yerel dosya — okunamamadır.
    """
    return isinstance(hata, (ArsivdeYok, FileNotFoundError))


def _kaydi_oku(path: str) -> Any:
    """Bir anime/bölüm kaydını oku: arşivde YOKSA ``None``, okunamadıysa hata.

    Eskiden bölüm ve akış okuyucuları HER hatayı boş listeye çeviriyordu:
    ulaşılamayan bir arşiv "bölümü olmayan anime" gibi görünüyordu.
    Paketlenmiş uygulamada (yerel arşiv yok) çevrimdışıyken arama disk
    önbelleğindeki dizinle çalışıyor, ama tıklanan her sonuç "bölüm
    bulunamadı" diyordu — asıl sebep (aynalar düştü, dosya önbellekte yok)
    hiç görünmüyordu. Artık yalnızca "yok" boş sonuçtur; okunamama
    `ArsivOkunamadi` olarak, sebebi ve konumuyla yükselir.

    Güvensiz kimlik (``../x``) arşivde olamayacak bir kayıttır: ``None``
    (diske/ağa hiç gidilmez). Yol burada AYRICA hesaplanıyor çünkü bozuk bir
    yerel dosyanın `JSONDecodeError`'ı da bir `ValueError` — ikisi
    karışmamalı: biri "yok", öbürü "okunamadı".
    """
    try:
        goreli = _goreli(path)
    except ValueError:
        return None
    try:
        return fetch_json(goreli)
    except Exception as hata:
        if _arsivde_yok_mu(hata):
            return None
        raise ArsivOkunamadi(_okuma_hatasi_metni(hata)) from hata


def _dizin_getir(tazele: bool = False) -> Tuple[Dict[str, Any], Optional[BaseException]]:
    """``(dizin, hata)``: dizin okunamadıysa ``hata`` okuma hatasıdır.

    Okunamayınca eldeki önbellek (yoksa ``{}``) döner ve ``hata`` dolu olur;
    böylece `dizin()` eski sözleşmesini korurken arama "okunamadı" ile "boş"
    arasındaki farkı görebilir. Hata modül değişkeninde değil dönüşte
    taşınıyor: başka bir thread'in hatası bu çağrıya karışmasın.
    """
    global _dizin_cache
    # Uzak okuma 3 ayna × `HTTP_TIMEOUT` sürebilir; bu süre boyunca yalnızca
    # yükleme kilidi tutuluyor (bkz. kilitlerin açıklaması).
    with _dizin_yukleme_kilidi:
        with _durum_kilidi:
            onceki, nesil = _dizin_cache, _nesil
        if onceki and not tazele:
            return onceki, None
        try:
            if onceki and tazele:
                data, degismedi = _kosullu_getir("dizin.json")
                if degismedi:
                    return onceki, None
            else:
                data = fetch_json("dizin.json")
        except Exception as hata:
            return (onceki or {}), hata  # cache'leme: sonraki çağrı tekrar dener
        with _durum_kilidi:
            if data and _nesil == nesil:
                _dizin_cache = data
        return (data or {}), None


def dizin(tazele: bool = False) -> Dict[str, Any]:
    """AnimeDepo dizin.json dosyasını cache'li döndür.

    ``tazele=True`` cache'i koşullu istekle doğrular: sunucu 304 döndürürse
    (arşiv commit'i değişmemişse) megabaytlık dizin yeniden indirilmez.

    NOT: Başarısızlık cache'lenmez. Aksi hâlde tek bir geçici ağ hatası
    AnimeDepo'yu süreç boyunca sessizce devre dışı bırakır (her arama 0 sonuç,
    hiçbir hata görünmez) ve tek çare uygulamayı yeniden başlatmak olur.
    Okunamayınca ``{}`` döner (eski sözleşme); SEBEBİ isteyen
    `dizin_ya_da_hata` kullanır.
    """
    return _dizin_getir(tazele)[0]


def dizin_ya_da_hata(tazele: bool = False) -> Dict[str, Any]:
    """`dizin` gibi, ama dizin hiçbir yerden okunamadıysa `ArsivOkunamadi`.

    Gerçekten boş bir arşiv (``{"index": {}}``) hata değildir, döner.
    Arama ve CLI açılış hazırlığı bunu kullanıyor: kullanıcı "bulunamadı"
    yerine "arşiv okunamadı" ve sebebini görmeli.
    """
    veri, hata = _dizin_getir(tazele)
    if hata is not None and not isinstance((veri or {}).get("index"), dict):
        raise ArsivOkunamadi(_okuma_hatasi_metni(hata)) from hata
    return veri


def get_anime_listesi() -> List[Tuple[str, str]]:
    """AnimeDepo anime listesini [(slug, title), ...] biçiminde döndür."""
    return _anime_ciftleri(dizin())


# ─────────────────────────────────────────────────────────────────────────────
# Tam arşivi indir (çevrimdışı kullanım)
# ─────────────────────────────────────────────────────────────────────────────
def tam_arsiv_indir(ilerleme: Optional[Callable[[int, Optional[int]], Any]] = None,
                    iptal: Any = None, hedef: Optional[Path] = None,
                    asama: Optional[Callable[[str, str], Any]] = None) -> Path:
    """Arşivin tamamını indirip ``hedef``'e (varsayılan `cevrimdisi_arsiv`) kur.

    Kaynaklar sırayla: GitLab paketi → bu deponun GitHub paketi (yalnızca
    `arsiv/`). ``ilerleme(indirilen_bayt, toplam_bayt_ya_da_None)`` indirme
    boyunca çağrılır; ``iptal`` `threading.Event` benzeri bir nesnedir ve
    kurulduğunda işlem `IptalEdildi` ile durur (yedek kaynağa GEÇİLMEZ —
    kullanıcı vazgeçti).

    ``asama(asama_adi, kaynak_adi)`` her kaynağın her aşamasının başında
    çağrılır (``paket.ASAMA_*``; kaynak adı "GitLab"/"GitHub"): önce
    `ASAMA_BAGLANMA` (istekten önce, kaynak düşse bile), yanıt geldiyse
    indirme → açma → yerleştirme. Arayüz böylece hangi kaynağın beklendiğini
    ve açma aşamasını (bayt ilerlemesi akmıyor) gösterebilir.

    Eski arşiv yenisi doğrulanıp yerine konana kadar yerinde kalır; hata ya
    da iptalde geçici dosyalar silinir. Başarıda modül cache'leri sıfırlanır,
    sonraki okuma yeni arşivden yapılır.

    Yedeğe YALNIZCA kaynağın suçu olabilecek hatalarda geçilir (ağ, HTTP,
    bozuk/güvensiz paket, geçersiz dizin). Yerel disk hatası
    (`paket.YerelDiskHatasi`: disk dolu, izin yok, takas yapılamadı) hemen
    yükselir: yedek kaynak aynı diske aynı boyutta paket yazacak ve aynı
    sebeple düşecekti — boşuna ikinci bir ~231 MB indirme.

    İptal bağlanırken ve ilk bayt beklenirken de işler (bkz.
    `paket.iptal_edilebilir`, `paket._parcalar`): istek yardımcı thread'de
    sürer, bu thread en geç `paket.IPTAL_ARALIGI` saniyede bir iptale bakar.
    """
    hedef = Path(hedef) if hedef is not None else indirilen_arsiv_dizini()
    # Kopuk bağ (diski takılı değil) ağa hiç çıkmadan söylensin.
    paket.bag_hedefi(hedef)
    hatalar: List[str] = []
    for kaynak in TAM_ARSIV_KAYNAKLARI:
        paket.iptal_denetle(iptal)
        if asama:
            asama(paket.ASAMA_BAGLANMA, kaynak.ad)
        yanit = None
        try:
            oturum = _session()
            yanit = paket.iptal_edilebilir(
                # Döngü değişkenleri çağrı anında bağlanıyor (varsayılan
                # argüman): lambda yardımcı thread'de sonra da çalışabilir.
                lambda _o=oturum, _u=kaynak.url: _o.get(
                    _u, stream=True, timeout=INDIRME_ZAMAN_ASIMI),
                iptal, birak=_yaniti_kapat)
            yanit.raise_for_status()
            sonuc = paket.paketten_kur(
                yanit, hedef, ust_desen=kaynak.ust_desen, alt_klasor=kaynak.alt_klasor,
                kaynak=kaynak.depo, dal=kaynak.dal, ilerleme=ilerleme, iptal=iptal,
                asama=_asama_bagla(asama, kaynak.ad))
        except (paket.IptalEdildi, paket.YerelDiskHatasi):
            raise
        except Exception as hata:
            hatalar.append(f"{kaynak.ad}: {hata}")
            continue
        finally:
            if yanit is not None:
                if iptal is not None and iptal.is_set():
                    # Takılmış bir akışı kapatmak da bekler (curl, sürmekte
                    # olan aktarımın dönmesini bekliyor); iptal eden kullanıcı
                    # beklemesin.
                    threading.Thread(target=_yaniti_kapat, args=(yanit,),
                                     name="arsiv-kapat", daemon=True).start()
                else:
                    _yaniti_kapat(yanit)
        sifirla()
        return sonuc
    raise paket.ArsivHatasi("tam arşiv indirilemedi — " + "; ".join(hatalar))


def _yaniti_kapat(yanit: Any) -> None:
    """Akış yanıtını kapat; kapatma hatası asıl sonucu gölgelemesin."""
    kapat = getattr(yanit, "close", None)
    if callable(kapat):
        try:
            kapat()
        except Exception:
            pass


def _asama_bagla(asama: Optional[Callable[[str, str], Any]],
                 kaynak_adi: str) -> Optional[Callable[[str], Any]]:
    """`paketten_kur`'un tek argümanlı `asama`sına kaynak adını ekle."""
    if asama is None:
        return None
    return lambda ad: asama(ad, kaynak_adi)


def indirilen_arsivi_sil() -> bool:
    """İndirilmiş tam arşivi (`<veri kökü>/cevrimdisi_arsiv`) sil.

    Silinecek bir şey yoksa ``False``. YALNIZCA `tam_arsiv_indir`'in varsayılan
    hedefi silinir; kullanıcının gösterdiği klasör (ayar/ortam) ve depodaki
    `arsiv/` asla: depodan çalışırken veri kökü depo KÖKÜ, yani yanlış bir yol
    hesabı commit'lenmiş aynayı silebilirdi. Bu yüzden ad ve konum ayrıca
    denetleniyor — savunma, tek satırlık bir hatanın 83 bin dosyaya mal
    olmaması için.

    Önce aynı klasörde gizli bir ada taşınır (`.cevrimdisi_arsiv-eski-*`,
    `.gitignore`'da; `paket.yerine_koy` da eskiyi bu adla bırakıyor). Taşıma
    anlık; silme ise binlerce dosya ve yarıda kesilebilir (kilitli dosya,
    izin). Yarım kalan klasör böylece konum çözümüne bir daha görünmez.
    Sembolik bağsa yalnızca BAĞ kaldırılır, gösterdiği klasöre dokunulmaz.
    """
    hedef = Path(indirilen_arsiv_dizini())
    if hedef.name != CEVRIMDISI_KLASOR:
        raise paket.ArsivHatasi(
            f"{hedef} indirilen arşiv klasörü değil ({CEVRIMDISI_KLASOR} bekleniyordu); "
            "silinmedi")
    if hedef.is_symlink():
        hedef.unlink()
        sifirla()
        return True
    if not hedef.exists():
        return False
    try:
        depo_mu = hedef.resolve() == Path(DEPO_ARSIVI).resolve()
    except OSError:
        depo_mu = False
    if depo_mu:
        raise paket.ArsivHatasi(f"{hedef} depodaki arşiv aynası; silinmedi")

    cop = hedef.with_name(f"{paket.eski_kopya_adi(hedef)}{uuid.uuid4().hex[:8]}")
    try:
        os.replace(paket.disk_yolu(hedef), paket.disk_yolu(cop))
    except OSError as hata:
        raise paket.ArsivHatasi(f"{hedef} kaldırılamadı: {hata}") from hata
    sifirla()                            # konum artık sıradaki yere düşmeli
    # `agaci_sil` uzun yolları da siliyor (Windows `\\?\` öneki) ve
    # silinemeyenleri döndürüyor; burada ayrı bir `rmtree` kopyası vardı.
    silinemeyen = paket.agaci_sil(cop)
    if silinemeyen:
        raise paket.ArsivHatasi(
            f"arşiv devre dışı bırakıldı ama {len(silinemeyen)} dosya silinemedi "
            f"({cop}); elle silebilirsiniz. İlki: {silinemeyen[0]}")
    return True


def _dizin_ozeti(veri: Any) -> Tuple[Optional[int], Optional[int]]:
    """``(anime_sayisi, last_update)``; dizin yok/bozuksa ``(None, None)``."""
    if not isinstance(veri, dict) or not isinstance(veri.get("index"), dict):
        return None, None
    son = veri.get("last_update")
    try:
        son = int(son) if son is not None else None
    except (TypeError, ValueError):
        son = None
    return paket.anime_sayisi(veri), son


def arsiv_durumu() -> ArsivDurumu:
    """Etkin arşiv konumu ve içeriğinin özeti. ASLA ağa çıkmaz.

    Yerel konumda dizin `dizin()` ile okunur — önbelleğe girer, ilk arama
    ayrıca beklemez. Uzak aynada yalnızca eldeki bilgi kullanılır: bellekteki
    dizin, yoksa disk önbelleğindeki kopya; ikisi de yoksa sayılar ``None``.
    Ayarlar sayfasını açmak kullanıcıyı internete çıkarmamalı.

    GUI thread'inde çağrılmamalı: ilk çağrıda megabaytlık dizin.json
    (gerekirse birkaç aday klasörde) ayrıştırılıyor.
    """
    konum = arsiv_konumu()
    onbellekten = False
    if konum.dizin is not None:
        adres = str(konum.dizin)
        sayi, son = _dizin_ozeti(dizin())
    else:
        adres = uzak_aynalar()[0]
        veri: Any = _dizin_cache
        if not veri:
            veri = _onbellekten_oku("dizin.json")
            onbellekten = veri is not _YOK
        sayi, son = _dizin_ozeti(veri if veri is not _YOK else None)
    indirilen = Path(indirilen_arsiv_dizini())
    return ArsivDurumu(
        konum=konum, adres=adres, anime_sayisi=sayi, son_guncelleme=son,
        indirilen_dizini=indirilen,
        indirilen_var=konum.kaynak == "indirilen" or indirilen.exists(),
        ortam_dizini=(os.environ.get(DIZIN_ORTAM_ANAHTARI) or "").strip(),
        ayar_dizini=_ayar_oku(DIZIN_AYAR_ANAHTARI),
        onbellekten=onbellekten and sayi is not None,
        kalintilar=tuple(arsiv_kalintilari()),
    )


def arsiv_kalintilari() -> List[Path]:
    """Silinemeyip kalmış eski arşiv kopyaları (`.cevrimdisi_arsiv-eski-*`).

    `paket.yerine_koy` ve `indirilen_arsivi_sil` eski kopyayı önce gizli bir
    ada taşıyıp sonra siliyor; silme yarıda kalırsa (kilitli dosya, izin)
    klasör orada kalıyor. Eskiden `rmtree(ignore_errors=True)` bunu
    yutuyordu. İndirilen arşiv başka bir diske sembolik bağlıysa takas o
    diskte yapıldığı için bağın gösterdiği klasörün yanına da bakılır.

    Yalnızca `-eski-` kalıntıları: `-indirme-` klasörü sürmekte olan bir
    indirmeye de ait olabilir, onu "kalıntı" diye göstermek yanlış olurdu.
    """
    hedef = Path(indirilen_arsiv_dizini())
    yerler = [hedef]
    try:
        if hedef.is_symlink():
            yerler.append(Path(os.path.realpath(hedef)))
    except OSError:
        pass
    bulunan: List[Path] = []
    for yer in yerler:
        onek = paket.eski_kopya_adi(yer)
        try:
            adaylar = list(yer.parent.iterdir())
        except OSError:
            continue                     # klasör yok/okunamıyor: kalıntı da yok
        # Bağlar da sayılır: bu düzeltmeden önceki sürüm bağlı arşivi
        # güncellerken BAĞIN kendisini bu ada taşıyıp bırakıyordu.
        bulunan.extend(p for p in adaylar if p.name.startswith(onek))
    return sorted(set(bulunan))


# ─────────────────────────────────────────────────────────────────────────────
# Arama (yerel fuzzy — AnimeDepo'da gerçek arama endpoint'i yok)
# ─────────────────────────────────────────────────────────────────────────────
# Bulanık eşleşme eşikleri (0..100, rapidfuzz ölçeği). Gerçek arşivde
# (6098 başlık) ölçülerek seçildi: eski `SequenceMatcher >= 0.55` eşiği "dr
# stone" aramasına "Cat Shit One", "Rain Town"; "narto"ya "Arte", "Arion"
# getiriyordu. 80'de yazım hatası ("narto"/"naurto" → Naruto, 83-91)
# yakalanıyor; arşivde olmayan bir seri arandığında gelen yarı-benzer adlar
# ("oshi no ko" → "Shion no Ou", 76) düşüyor.
BULANIK_ESIK = 80
# Uzun başlığın bir PARÇASINA yakın sorgu ("kimetsu no yaba" → "Kimetsu no
# Yaiba: Mugen Ressha-hen", 93). Kısa sorguda parça eşleşmesi her yerde tutar,
# o yüzden yalnızca yeterince uzun sorgularda ve yüksek eşikle ("oshi no ko"
# → "Bubuki Buranki: Hoshi no Kyojin" 90'da kalıyor).
PARCA_ESIK = 92
PARCA_MIN_UZUNLUK = 6


def _anime_ciftleri(veri: Dict[str, Any]) -> List[Tuple[str, str]]:
    liste: List[Tuple[str, str]] = []
    for grup in (veri or {}).get("index", {}).values():
        if not isinstance(grup, dict):
            continue
        for slug, anime in grup.items():
            title = (anime or {}).get("title") if isinstance(anime, dict) else None
            liste.append((slug, title or slug.replace("-", " ").title()))
    return liste


def _arama_tablosu() -> List[Tuple[str, str, str, str]]:
    """Normalize edilmiş başlık tablosu; dizin başına bir kez kurulur.

    Her aramada 6098 başlığı yeniden normalize etmek (NFKD + regex) aramanın
    kendisinden pahalı olurdu; tablo önbellekli olunca gerçek arşivde bir arama
    ~15-30 ms (eski `SequenceMatcher` taraması 90-190 ms sürüyordu).

    Dizin okunamadıysa `ArsivOkunamadi` yükselir (boş tablo değil): arama
    "sonuç yok" ile "arşive ulaşılamadı"yı ayırt edebilsin.
    """
    global _arama_onbellek
    veri = dizin_ya_da_hata()
    onbellek = _arama_onbellek
    if onbellek is not None and onbellek[0] is veri:
        return onbellek[1]
    tablo = [(slug, title, baslik_normalize(title),
              baslik_normalize(str(slug).replace("-", " ")))
             for slug, title in _anime_ciftleri(veri)]
    _arama_onbellek = (veri, tablo)
    return tablo


def _bulanik(sorgu: str, aday: str) -> float:
    """0..100 benzerlik; eşiği geçemeyen aday için 0."""
    if _rf_fuzz is not None:
        tam = max(_rf_fuzz.ratio(sorgu, aday), _rf_fuzz.token_sort_ratio(sorgu, aday))
        if tam >= BULANIK_ESIK:
            return tam
        if len(sorgu) >= PARCA_MIN_UZUNLUK and len(aday) > len(sorgu):
            parca = _rf_fuzz.partial_ratio(sorgu, aday)
            if parca >= PARCA_ESIK:
                return parca
        return 0.0
    tam = SequenceMatcher(None, sorgu, aday).ratio() * 100   # rapidfuzz yoksa
    return tam if tam >= BULANIK_ESIK else 0.0


def search_animedepo(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """Arşiv dizininde yerel arama — ağsız (yerel arşivde) ve hızlı.

    Kademeler (üstteki her zaman önce): birebir → başlangıç → kelime sınırında
    geçiyor → bütün kelimeler, sırasız ("shingeki kyojin") → herhangi bir
    yerde geçiyor → bulanık (yazım hatası); bkz. `_kademe`. Aynı
    kademede `title_match.siralama_skoru` sıralar: fazladan kelimeyi
    cezalandırır, seri adıyla başlayanı öne alır ("One Piece" >
    "One Piece Film: Z" > "Koisuru One Piece"). Eşitlikte kısa başlık, sonra
    arşivdeki sıra. Karşılaştırma aksan/noktalamadan bağımsız ("Dr. Stone" =
    "dr stone") ve slug da aranıyor ("shingeki-no-kyojin").

    Arşivde İngilizce adlar yok (turkanime.tv romaji kullanıyordu):
    "attack on titan" bulunmaz, "shingeki no kyojin" bulunur.

    Returns: [(slug, başlık), ...]

    Raises: `ArsivOkunamadi` — dizin hiçbir yerden okunamadı. Eskiden boş
    liste dönüyordu ve CLI/arayüz "bulunamadı" diyordu; arama motoru artık
    bunu kaynağın hatası olarak gösteriyor, CLI "arama hatası" diyor.
    """
    sorgular = _sorgu_bicimleri(query)
    if not sorgular:
        return []
    enler: Dict[str, Tuple[float, int, int, str, str]] = {}
    tablo = _arama_tablosu()
    for q in sorgular:
        kelimeler = q.split()
        q_bitisik = q.replace(" ", "")
        for sira, (slug, title, nb, ns) in enumerate(tablo):
            kademe = _kademe(q, kelimeler, q_bitisik, nb, ns)
            if kademe is None:
                continue
            aday = (-(kademe + siralama_skoru(q, nb)), len(title), sira, slug, title)
            if slug not in enler or aday < enler[slug]:
                enler[slug] = aday
    sirali = sorted(enler.values())
    return [(slug, title) for _, _, _, slug, title in sirali[:max(0, int(limit))]]


# Türkçe klavyeyle romaji yazımı: "Şingeki" → "shingeki", "Çainsaw" → "chainsaw".
# Normalizasyon "ş"yi "s"ye indiriyor ("singeki"), arşivdeki romaji ise "sh".
# İki biçim de aranır, aday başına en iyi skor kalır.
_TURKCE_ROMAJI = str.maketrans({"ş": "sh", "Ş": "Sh", "ç": "ch", "Ç": "Ch"})


def _sorgu_bicimleri(sorgu: str) -> List[str]:
    """Sorgunun normalize biçimleri (tekrarsız, boşlar atılır)."""
    out: List[str] = []
    for aday in (sorgu or "", (sorgu or "").translate(_TURKCE_ROMAJI)):
        q = baslik_normalize(aday)
        if q and q not in out:
            out.append(q)
    return out


def _kademe(q: str, kelimeler: List[str], q_bitisik: str,
            nb: str, ns: str) -> Optional[float]:
    """Eşleşme kademesi (yüksek = daha iyi); eşleşme yoksa None.

    4 birebir · 3 başlangıç · 2 kelime sınırında geçiyor · 1.5 bütün
    kelimeler (sırasız, kelime başı) · 1 herhangi bir yerde (boşluksuz da:
    "rezero" → "Re:Zero") · 0 bulanık.
    """
    if q in (nb, ns):
        return 4.0
    if nb.startswith(q) or ns.startswith(q):
        return 3.0
    if f" {q}" in f" {nb}" or f" {q}" in f" {ns}":
        return 2.0
    if len(kelimeler) > 1:
        baslik_kelimeleri = nb.split()
        if all(any(b.startswith(k) for b in baslik_kelimeleri) for k in kelimeler):
            return 1.5
    if q in nb or q in ns:
        return 1.0
    if len(q_bitisik) >= 4 and q_bitisik in nb.replace(" ", ""):
        return 1.0
    if len(q) >= 3 and _bulanik(q, nb):
        return 0.0
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Bölüm listesi
# ─────────────────────────────────────────────────────────────────────────────
def get_anime_episodes(anime_slug: str) -> List[Tuple[str, str]]:
    """Anime'nin bölüm listesini döndür.

    Returns: [(episode_id, başlık), ...]; episode_id = "anime_slug/bolum_slug".
        Anime arşivde yoksa boş liste.

    Raises: `ArsivOkunamadi` — arşive ulaşılamadı ve dosyanın kopyası yok
        (bkz. `_kaydi_oku`). Arayüz/CLI sebebi "bölüm bulunamadı" yerine gösterir.
    """
    data = _kaydi_oku(f"animeler/{anime_slug}/bolumler.json")
    episodes: List[Tuple[str, str]] = []
    for entry in data or []:
        # entry ya [slug, title] ya da {"slug":..,"title":..}
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            ep_slug, title = entry[0], entry[1]
        elif isinstance(entry, dict):
            ep_slug, title = entry.get("slug"), entry.get("title")
        else:
            continue
        if not ep_slug:
            continue
        episodes.append((f"{anime_slug}/{ep_slug}", title or ep_slug))
    return episodes


# ─────────────────────────────────────────────────────────────────────────────
# Künye (info.json)
# ─────────────────────────────────────────────────────────────────────────────
_TR_AYLAR = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5,
             "haziran": 6, "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10,
             "kasım": 11, "aralık": 12}
_TARIH = re.compile(r"(\d{1,2})\s+(\w+)\s+(\d{4})")


def _tarih_coz(ham: Any) -> Optional[Dict[str, int]]:
    """"07 Nisan 2009, Salı" → AniList biçimi ``{"year", "month", "day"}``."""
    m = _TARIH.search(str(ham or ""))
    if not m:
        return None
    ay = _TR_AYLAR.get(m.group(2).replace("I", "ı").lower())
    if not ay:
        return None
    return {"year": int(m.group(3)), "month": ay, "day": int(m.group(1))}


def _bolum_sayisi(ham: Any) -> Optional[int]:
    """"25 / 25" → 25. Toplam bilinmiyorsa ("12 / ?", "12 / 12+") yayınlanan.

    Arşivdeki biçim "yayınlanan / toplam"; süren seride toplam "?" ya da
    "N+" yazıyor. Toplamı sayı değilken göstermek "12 bölüm" demek olur ki
    bu da elde olan bölüm sayısı, yani doğru.
    """
    parcalar = [p.strip() for p in str(ham or "").split("/")]
    for aday in reversed(parcalar):
        if aday.isdigit() and int(aday) > 0:
            return int(aday)
    for aday in parcalar:
        rakam = re.match(r"\d+", aday)
        if rakam and int(rakam.group()) > 0:
            return int(rakam.group())
    return None


def anime_bilgisi(anime_slug: str) -> Dict[str, Any]:
    """Arşivdeki ``animeler/{slug}/info.json``'u AniList biçimine çevir.

    Detay sayfası AniList/Jikan kaydını render ediyor; arşiv kaydı aynı
    anahtarlarla gelince ikinci bir görünüm yazmak gerekmiyor. Okuma
    `_kaydi_oku` üzerinden (yerel arşiv → aynalar → disk önbelleği), yani
    yerel ya da indirilmiş arşivle tamamen çevrimdışı çalışır.

    "Resim" alanı BİLEREK taşınmıyor: adresler kapanan turkanime.co/.tv'ye
    gidiyor, arayüz onu istemeye kalksa her kapak zaman aşımını beklerdi.
    ``averageScore`` yalnızca Puanı > 0 ise: arşivde puansız kayıtlar 0
    taşıyor ve "SKOR 0%" puanı düşük bir anime gibi okunurdu.

    Returns: anahtarları olan alanlar (description, genres, studios,
        averageScore, episodes, format, startDate, endDate, title.native);
        anime ya da info.json arşivde yoksa ``{}``.
    Raises: `ArsivOkunamadi` — arşive ulaşılamadı (bkz. `_kaydi_oku`).
    """
    veri = _kaydi_oku(f"animeler/{anime_slug}/info.json")
    if not isinstance(veri, dict):
        return {}
    out: Dict[str, Any] = {}

    ozet = str(veri.get("Özet") or "").strip()
    if ozet:
        out["description"] = ozet            # <br /> temizliği arayüzde
    turler = veri.get("Anime Türü")
    if isinstance(turler, str):
        turler = turler.split(",")
    if isinstance(turler, list):
        temiz = [str(t).strip() for t in turler if str(t or "").strip()]
        if temiz:
            out["genres"] = temiz
    # 529 kayıtta birden çok stüdyo virgülle yazılmış ("Studio A, Studio B").
    studyolar = [s.strip() for s in str(veri.get("Stüdyo") or "").split(",")]
    studyolar = [s for s in studyolar if s and s != "?"]
    if studyolar:
        out["studios"] = studyolar
    try:
        puan = float(veri.get("Puanı") or 0)
    except (TypeError, ValueError):
        puan = 0.0
    if puan > 0:
        out["averageScore"] = int(round(puan * 10))
    bolum = _bolum_sayisi(veri.get("Bölüm Sayısı"))
    if bolum:
        out["episodes"] = bolum
    kategori = str(veri.get("Kategori") or "").strip()
    if kategori:
        out["format"] = kategori
    for alan, anahtar in (("Başlama Tarihi", "startDate"), ("Bitiş Tarihi", "endDate")):
        tarih = _tarih_coz(veri.get(alan))
        if tarih:
            out[anahtar] = tarih
    japonca = str(veri.get("Japonca") or "").strip()
    if japonca:
        out["title"] = {"native": japonca}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Stream linkleri
# ─────────────────────────────────────────────────────────────────────────────
# turkanime.tv/.co/.net… — site kapandı; oraya giden adres oynatılamaz.
_TURKANIME_KONAGI = re.compile(r"(^|\.)turkanime(\.[a-z]{2,})+$")
# Site VK gömme adreslerini href.li "referer gizleyici"sinden geçiriyordu:
# `https://href.li/?<gerçek adres>`. href.li bir JS/meta yönlendirme sayfası;
# yt-dlp onu izleyemez, gerçek adres soru işaretinden sonrası.
_YONLENDIRICILER = {"href.li", "www.href.li"}
# `https:https://…` → `https://…` (aşağıda neden olduğu yazıyor).
_CIFT_SEMA = re.compile(r"^(?:https?:)+(?=https?://)", re.I)


def _turkanime_sarmalayicisini_ac(parca) -> Optional[str]:
    """turkanime.tv adresinden kurtarılabilecek gerçek adres; yoksa None.

    Tek kurtarılabilen kalıp: `https://www.turkanime.tv/html/pixeldrain.php?id=<ID>`
    (arşivde 102 kayıt). Sayfa pixeldrain dosyasını gömen bir sarmalayıcıydı;
    ``id`` pixeldrain'in kendi dosya kimliği. Arşivdeki diğer 462 PIXELDRAIN
    kaydı zaten doğrudan `https://pixeldrain.com/u/<ID>` biçiminde — aynı biçime
    çevriliyor.
    """
    if parca.path.rstrip("/").endswith("/pixeldrain.php"):
        kimlik = (parse_qs(parca.query).get("id") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9]{4,32}", kimlik):
            return f"https://pixeldrain.com/u/{kimlik}"
    return None


def _url_duzelt(ham: Any) -> Optional[str]:
    """Arşivdeki video adresini oynatılabilir biçime getir; olmuyorsa None.

    Arşivdeki bütün VK kayıtları (~22 bin) şöyle:
        ``https:https://href.li/?https://vk.com/video_ext.php?oid=…``
    `urlsplit` bunu şema=``https``, konak=**boş**, yol=``https://href.li/…``
    diye ayrıştırıyor — "VK kayıtlarının konağı boş" görünmesinin sebebi bu.
    AnimeDepo'nun kazıyıcısı iframe adresini protokol-göreli (``//…``) sanıp
    başına ``https:`` eklemiş; site ise zaten tam adres veriyordu. Düzeltme:
    fazladan şemayı at, href.li sarmalayıcısını soy, sonucu yeniden doğrula.
    Aynı çift şema 13 MAIL kaydında da var.

    Ayrıca: baş/son boşluklar kırpılır (SAVEFILEWAY/MAIL kayıtlarında var),
    gerçekten protokol-göreli (``//konak/…``) adrese ``https:`` eklenir,
    konağı olmayan artıklar (``https:&hd=1``) ve turkanime alan adları atılır.
    """
    if not isinstance(ham, str):
        return None
    url = ham.strip()
    for _ in range(3):                   # sarmalayıcı içinden yine çift şema çıkabilir
        if url.startswith("//"):
            url = "https:" + url
        url = _CIFT_SEMA.sub("", url)
        try:
            konak = (urlsplit(url).hostname or "").lower()
        except ValueError:
            return None
        if konak in _YONLENDIRICILER and "?" in url:
            url = url.split("?", 1)[1].strip()
            if re.match(r"https?%3A", url, re.I):
                url = unquote(url)
            continue
        break
    try:
        parca = urlsplit(url)
        konak = (parca.hostname or "").lower()
    except ValueError:
        return None
    if parca.scheme.lower() not in ("http", "https") or not konak:
        return None
    if _TURKANIME_KONAGI.search(konak):
        return _turkanime_sarmalayicisini_ac(parca)
    return url


def _akis_uret(item: Any) -> Optional[Dict[str, str]]:
    """Tek arşiv kaydından stream sözlüğü; oynatılamayacaksa None.

    Hiçbir koşulda ağa çıkılmaz. Eskiden `url`'siz (yalnızca `mask`/`path`
    taşıyan) kayıtlar `bypass.unmask_real_url` ile turkanime.tv'ye sorularak
    çözülüyordu; site kapandı, o istekler yalnızca zaman aşımı biriktirir.
    """
    if not isinstance(item, dict):
        return None
    player = str(item.get("player") or "AnimeDepo").strip().upper()
    # Arşiv ölü oynatıcıları "DEAD_" önekiyle işaretliyor (DEAD_ALUCARD,
    # DEAD_AMATERASU: turkanime'nin kendi sunucuları).
    if player.startswith("DEAD_"):
        return None
    if item.get("alive") is False:
        return None
    url = _url_duzelt(item.get("url"))
    if not url:
        return None
    fansub = str(item.get("fansub") or "").strip()
    stream: Dict[str, str] = {
        "url": url,
        "label": " ".join(p for p in (player.title(), fansub) if p).strip() or player,
        "type": item.get("type") or "direct",
        "player": player,
        "fansub": fansub,
    }
    # Arşiv kendi referer'ını veriyorsa ona uyulur: sunucu tarayıcısının
    # (turkanime_server/crawler) ürettiği kayıtlarda link tranimaci/openani
    # gibi kaynakların CDN'inden gelir ve turkanime referer'ı ile 403 döner.
    # Referer yoksa eski davranış korunur (GitLab arşivindeki doğrudan dosya
    # linkleri turkanime'nin gömmesi için verilmişti).
    referer = item.get("referer")
    if referer:
        stream["referer"] = referer
    elif re.search(r"\.(mp4|m3u8)(\?|$)", url):
        stream["referer"] = "https://www.turkanime.co/"
    return stream


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Bölümün video stream'lerini oynatıcı önceliğine göre sıralı döndür.

    Args:
        episode_id: "anime_slug/bolum_slug" bileşik kimliği.

    Returns: [{"url", "label", "type", "player", "fansub", "referer"?}, ...]
        Sıra `common.oynatici_onceligi`: çalıştığı bilinen oynatıcılar önce,
        bilinmeyenler ortada, bilinen false-positive'ler en sonda. Sıra önemli:
        `AdapterBolum.best_video` yalnızca ilk birkaç adayı yokluyor.
        Bölüm arşivde yoksa boş liste.

    Raises: `ArsivOkunamadi` — arşive ulaşılamadı (bkz. `_kaydi_oku`).
        `kayit.akis_saglayici` bunu yutmaz; oynatma "çalışan video yok"
        yerine sebebi gösterir.
    """
    if "/" not in episode_id:
        return []
    anime_slug, ep_slug = episode_id.split("/", 1)
    data = _kaydi_oku(f"animeler/{anime_slug}/{ep_slug}.json")
    if not isinstance(data, list):
        return []

    streams: List[Dict[str, str]] = []
    gorulen = set()
    for item in data:
        stream = _akis_uret(item)
        if stream is None:
            continue
        # Aynı fansub'un aynı adresi iki kez: best_video'nun kısıtlı deneme
        # bütçesini boşa harcamasın.
        anahtar = (stream["url"], stream["fansub"])
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        streams.append(stream)
    # `sort` kararlı: aynı öncelikteki kayıtlar arşivdeki sırasını korur.
    streams.sort(key=lambda s: oncelik_anahtari(s["player"]))
    return streams


__all__ = [
    "search_animedepo",
    "get_anime_episodes",
    "anime_bilgisi",
    "get_episode_streams",
    "get_anime_listesi",
    "dizin",
    "dizin_ya_da_hata",
    "ArsivOkunamadi",
    "ArsivdeYok",
    "fetch_json",
    "arsiv_konumu",
    "ArsivKonumu",
    "arsiv_durumu",
    "ArsivDurumu",
    "indirilen_arsivi_sil",
    "arsiv_kalintilari",
    "uzak_aynalar",
    "tam_arsiv_indir",
    "TAM_ARSIV_KAYNAKLARI",
    "veri_koku",
    "indirilen_arsiv_dizini",
    "onbellek_dizini",
    "sifirla",
    "taban_url",
    "taban_url_sifirla",
    "BASE_URL",
    "GITHUB_AYNA_URL",
    "ORTAM_ANAHTARI",
    "AYAR_ANAHTARI",
    "DIZIN_ORTAM_ANAHTARI",
    "DIZIN_AYAR_ANAHTARI",
]
