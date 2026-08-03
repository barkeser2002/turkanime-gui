"""AniList köprüsü — OAuth girişi, izleme listesi ve ilerleme senkronu.

Eski CustomTkinter GUI'de bu iş ~400 satıra dağılmıştı: her parça kendi
`threading.Thread`'ini açıyor, sonucu `after(0, ...)` ile widget'a yazıyordu.
Burada tek bir servis var; ağ işleri `run_bg` ile havuza gider, sonuç Qt
sinyaliyle GUI thread'ine taşınır. Sayfalar (`WatchlistPage`, `SettingsPage`)
yalnızca sinyal dinler, ağ görmez.

`turkanime_api.anilist_client` singleton'ı OLDUĞU GİBİ kullanılır: token ve
OAuth yapılandırması onun kendi dosyalarında duruyor, yolları değiştirmek
kullanıcının kayıtlı girişini geçersiz kılardı.
"""
from __future__ import annotations

import threading
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from . import prefs
from .workers import run_bg

# AniList `MediaListStatus` -> (Türkçe etiket). Sıra, filtre kutusundaki sıradır.
DURUMLAR: List[Tuple[str, str]] = [
    ("CURRENT", "İzliyorum"),
    ("PLANNING", "İzleyeceğim"),
    ("COMPLETED", "Tamamladım"),
    ("PAUSED", "Duraklattım"),
    ("DROPPED", "Bıraktım"),
]
DURUM_ETIKETI: Dict[str, str] = dict(DURUMLAR)

# Rozet renkleri eski GUI'deki `status_colors` ile birebir aynı: kullanıcı aynı
# renk kodlamasını tanıyor.
DURUM_RENGI: Dict[str, str] = {
    "CURRENT": "#4CAF50",
    "PLANNING": "#2196F3",
    "COMPLETED": "#9C27B0",
    "PAUSED": "#FF9800",
    "DROPPED": "#F44336",
}

# Bulanık eşleşme bu eşiğin altındaysa AniList'e HİÇBİR ŞEY yazılmaz: yanlış
# animenin ilerlemesini ezmek, hiç yazmamaktan çok daha kötü. Eski GUI arama
# sonucunun ilkini körlemesine alıyordu ve sık sık yanlış sezona yazıyordu.
ESLESME_ESIGI = 0.55

# Aday havuzu küçük tutulur: doğru kayıt neredeyse her zaman ilk birkaçtadır,
# fazlası yalnızca yanlış eşleşme riskini artırır.
ARAMA_ADAY_SAYISI = 5

VARSAYILAN_PORT = 9921


# ── İstemciye erişim ────────────────────────────────────────────────────────
def _modul():
    """`turkanime_api.anilist_client` modülü.

    Import gövdede ve *modül üzerinden*: testlerin hem singleton'ı hem
    `AniListAuthServer` sınıfını sahteleyebilmesi için nesne değil modül
    tutuluyor.
    """
    from ... import anilist_client as modul
    return modul


def istemci():
    """AniList istemci singleton'ı."""
    return _modul().anilist_client


def giris_var_mi() -> bool:
    """Kayıtlı bir erişim jetonu var mı?"""
    try:
        return bool(getattr(istemci(), "access_token", None))
    except Exception:
        return False


# ── Saf yardımcılar (Qt'siz; doğrudan test edilebilir) ──────────────────────
def deslug(seri: str) -> str:
    """``naruto-shippuuden`` → ``naruto shippuuden``.

    İlerleme sinyali seri *slug*'ı taşıyor; AniList ise yalnızca başlık biliyor.
    Aradaki tek köprü slug'ı okunabilir hâle getirip bulanık eşleştirmek.
    """
    return " ".join(str(seri or "").replace("_", "-").split("-")).strip()


def basliklar(media: Dict[str, Any]) -> List[str]:
    """Bir AniList kaydının tüm başlık varyantları (romaji/english/native)."""
    ham = media.get("title") if isinstance(media, dict) else None
    if not isinstance(ham, dict):
        return [str(ham)] if ham else []
    return [str(v) for v in (ham.get("romaji"), ham.get("english"),
                             ham.get("native")) if v]


def girisleri_duzlestir(lists: Any, durum: Optional[str] = None) -> List[Dict[str, Any]]:
    """`MediaListCollection.lists` → kart başına tek sözlük.

    Şema eski GUI ile aynı: media alanlarının üstüne `user_progress`,
    `user_score`, `user_status` yazılır.

    `durum` verilirse ayrıca yerelde süzülür. AniList'in özel listeleri
    (custom lists) sorguya durum verilse bile karışık durumlu girişler
    döndürebiliyor; sunucunun süzdüğüne güvenmek kullanıcıya "Tamamladım"
    filtresinde yarım animeler gösterirdi.
    """
    sonuc: List[Dict[str, Any]] = []
    for grup in (lists or []):
        if not isinstance(grup, dict):
            continue
        for entry in (grup.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            media = dict(entry.get("media") or {})
            if not media:
                continue
            entry_durum = str(entry.get("status") or "")
            if durum and entry_durum and entry_durum != durum:
                continue
            media["user_progress"] = int(entry.get("progress") or 0)
            media["user_score"] = entry.get("score") or 0
            media["user_status"] = entry_durum or (durum or "")
            sonuc.append(media)
    return sonuc


def en_iyi_eslesme(adaylar: Any, ad: str,
                   esik: float = ESLESME_ESIGI) -> Optional[Dict[str, Any]]:
    """`ad`'a en yakın AniList kaydı; hiçbiri eşiği geçmezse ``None``."""
    if not ad:
        return None
    from ...common.title_match import fuzzy_score

    en_iyi: Optional[Dict[str, Any]] = None
    en_iyi_skor = 0.0
    for item in (adaylar or []):
        if not isinstance(item, dict):
            continue
        skorlar = [fuzzy_score(ad, baslik) for baslik in basliklar(item)]
        skor = max(skorlar) if skorlar else 0.0
        if skor > en_iyi_skor:
            en_iyi, en_iyi_skor = item, skor
    return en_iyi if en_iyi_skor >= esik else None


def senkron_guncellemeleri(girisler: Any,
                           yerel: Dict[str, int]) -> Dict[str, int]:
    """AniList ilerlemesini yerel kayıtlara eşle; yazılacakları döndür.

    Yalnızca yerelde ZATEN kaydı olan seriler güncellenir: aksi hâlde
    AniList'teki yüzlerce anime `gecmis.json`'a slug'ı olmayan anahtarlarla
    dolardı. Geriye gitme de yok — AniList'te 3, yerelde 7 ise yerel kalır
    (kullanıcı çevrimdışı izlemiş olabilir).
    """
    guncel: Dict[str, int] = {}
    for seri, mevcut in (yerel or {}).items():
        media = en_iyi_eslesme(girisler, deslug(seri))
        if not media:
            continue
        ilerleme = int(media.get("user_progress") or 0)
        try:
            eski = int(mevcut or 0)
        except (TypeError, ValueError):
            eski = 0
        if ilerleme > eski:
            guncel[str(seri)] = ilerleme
    return guncel


def geri_donus_portu(uri: str, varsayilan: int = VARSAYILAN_PORT) -> int:
    """Redirect URI'deki portu çöz — yerel geri dönüş sunucusu oraya bağlanır.

    Sabit 9921 varsaymak, kullanıcı AniList uygulamasında başka bir port
    tanımladıysa sunucunun hiç dinlemediği anlamına gelirdi.
    """
    from urllib.parse import urlparse
    try:
        port = urlparse(str(uri or "")).port
    except ValueError:            # bozuk URI
        port = None
    return int(port or varsayilan)


# ── Servis ──────────────────────────────────────────────────────────────────
class AniListService(QObject):
    """AniList işlerinin tek adresi; ağ arka planda, sonuç sinyalle."""

    auth_changed = Signal(object)        # kullanıcı sözlüğü ya da None
    avatar_ready = Signal(object)        # avatar görsel baytları
    status_changed = Signal(str, bool)   # (mesaj, hata mı)
    list_ready = Signal(str, object)     # (durum, girişler)
    list_failed = Signal(str)
    progress_written = Signal(str, int, bool)   # (seri, bölüm no, başarılı mı)
    sync_done = Signal(int)              # güncellenen seri sayısı

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._user: Optional[Dict[str, Any]] = None
        self._auth_server = None
        self._son_girisler: List[Dict[str, Any]] = []
        # seri slug -> AniList media id. Aynı seriyi her bölümde yeniden
        # aratmamak için; süreç ömrü kadar yaşar (disk kirletmez).
        self._media_ids: Dict[str, int] = {}

    # ── Durum ───────────────────────────────────────────────────────────────
    @property
    def kullanici(self) -> Optional[Dict[str, Any]]:
        """Son bilinen AniList kullanıcı kaydı."""
        return self._user

    def giris_var_mi(self) -> bool:
        """Kayıtlı jeton var mı? (`anilist_client` üzerinden)"""
        return giris_var_mi()

    def baslat(self) -> None:
        """Uygulama açılışında çağrılır: jeton varsa kullanıcıyı tazele."""
        if giris_var_mi():
            self.kullaniciyi_tazele()

    # ── OAuth ───────────────────────────────────────────────────────────────
    def giris_yap(self) -> bool:
        """OAuth akışını başlat: yerel geri dönüş sunucusu + tarayıcı.

        Sunucu `run_bg` ile DEĞİL kendi daemon thread'inde çalışır:
        `serve_forever` kullanıcı girişi tamamlayana kadar dönmüyor ve
        havuzdaki bir thread'i süresiz tutardı (indirme/arama kuyrukta kalırdı).
        """
        ist = istemci()
        if not str(getattr(ist, "client_id", "") or ""):
            self.status_changed.emit(
                "AniList istemci kimliği tanımlı değil. Ayarlar'dan girin.", True)
            return False
        try:
            if self._auth_server is None:
                sunucu = _modul().AniListAuthServer(ist)
                sunucu.register_on_success(self._on_auth_success)
                self._auth_server = sunucu
                port = geri_donus_portu(getattr(ist, "redirect_uri", ""))
                threading.Thread(target=sunucu.start_server, args=(port,),
                                 daemon=True).start()
            # Authorization Code akışı: implicit akış client_secret'sız çalışsa
            # da jetonu tarayıcı adres çubuğunda taşır.
            url = ist.get_auth_url(response_type="code")
            webbrowser.open(url)
        except Exception as exc:
            # Mesaja YALNIZCA istisna metni girer; client_secret hiçbir log ya
            # da arayüz satırına sızmamalı.
            self._auth_server = None
            self.status_changed.emit(f"AniList girişi başlatılamadı: {exc}", True)
            return False
        self.status_changed.emit("Tarayıcıda AniList girişini tamamlayın…", False)
        return True

    def _on_auth_success(self) -> None:
        """OAuth geri dönüşü — **HTTP handler thread'inde** çalışır.

        Burada widget'a dokunulmaz; yalnızca sinyal yayılır ve tazeleme işi
        havuza atılır (bağlantılar queued olduğu için GUI thread'ine geçer).
        """
        self._auth_server = None
        self.status_changed.emit("AniList girişi başarılı.", False)
        self.kullaniciyi_tazele()

    def cikis_yap(self) -> None:
        """Jetonu sil ve arayüzü çıkış durumuna al."""
        try:
            istemci().clear_tokens()
        except Exception as exc:
            self.status_changed.emit(f"Çıkış yapılamadı: {exc}", True)
            return
        self._user = None
        self._son_girisler = []
        self._media_ids.clear()
        self.auth_changed.emit(None)
        self.status_changed.emit("AniList oturumu kapatıldı.", False)

    # ── Kullanıcı ───────────────────────────────────────────────────────────
    def kullaniciyi_tazele(self) -> None:
        """Kullanıcı bilgisini (ve avatarı) arka planda getir."""
        if not giris_var_mi():
            self._kullaniciyi_ata(None)
            return
        run_bg(self._kullanici_getir)

    def _kullanici_getir(self) -> None:
        """Arka plan thread'i: kullanıcıyı çek, avatarı indir."""
        try:
            user = istemci().get_current_user()
        except Exception as exc:
            print(f"[AniList] Kullanıcı bilgisi alınamadı: {exc}")
            user = None
        self._kullaniciyi_ata(user if isinstance(user, dict) else None)
        if isinstance(user, dict):
            url = (user.get("avatar") or {}).get("large")
            if url:
                self._avatar_getir(str(url))

    def _kullaniciyi_ata(self, user: Optional[Dict[str, Any]]) -> None:
        self._user = user
        self.auth_changed.emit(user)

    def _avatar_getir(self, url: str) -> None:
        """Arka plan: avatarı indir, baytları sinyalle taşı (widget'a dokunma)."""
        try:
            import requests
            data = requests.get(url, timeout=10).content
        except Exception:
            return
        if data:
            self.avatar_ready.emit(data)

    # ── İzleme listesi ──────────────────────────────────────────────────────
    def liste_getir(self, durum: str = "CURRENT") -> bool:
        """Kullanıcının `durum` listesini arka planda getir."""
        if not giris_var_mi():
            self.list_failed.emit("AniList girişi yapılmamış.")
            return False
        run_bg(self._liste_getir, str(durum or "CURRENT"))
        return True

    def _liste_getir(self, durum: str) -> None:
        try:
            user = self._user or istemci().get_current_user()
            uid = (user or {}).get("id")
            if not uid:
                self.list_failed.emit("AniList kullanıcı bilgisi alınamadı.")
                return
            self._user = user
            girisler = girisleri_duzlestir(
                istemci().get_user_anime_list(int(uid), durum), durum)
        except Exception as exc:
            self.list_failed.emit(str(exc))
            return
        self._son_girisler = girisler
        self.list_ready.emit(durum, girisler)

    # ── İlerleme yazma ──────────────────────────────────────────────────────
    def ilerleme_yaz(self, seri: str, bolum_no: int, ad: str = "") -> bool:
        """Bölüm ilerlemesini AniList'e yaz. Giriş yoksa **sessizce** atlanır.

        Yerel kayıt zaten yapıldı; AniList'e bağlı olmayan kullanıcıyı her
        bölüm sonunda hata mesajıyla rahatsız etmenin anlamı yok.
        """
        if not giris_var_mi():
            return False
        arama_adi = (ad or "").strip() or deslug(seri)
        if not arama_adi:
            return False
        run_bg(self._ilerleme_yaz, str(seri), int(bolum_no), arama_adi)
        return True

    def _ilerleme_yaz(self, seri: str, bolum_no: int, ad: str) -> None:
        try:
            media_id = self._media_id_bul(seri, ad)
            if not media_id:
                # Hata değil: kullanıcının izlediği her seri AniList'te yok.
                self.status_changed.emit(
                    f"{ad}: AniList'te eşleşme bulunamadı, ilerleme yalnızca yerel.",
                    False)
                self.progress_written.emit(seri, bolum_no, False)
                return
            basarili = bool(istemci().update_anime_progress(int(media_id),
                                                            int(bolum_no)))
        except Exception as exc:
            self.status_changed.emit(f"AniList ilerlemesi yazılamadı: {exc}", True)
            self.progress_written.emit(seri, bolum_no, False)
            return
        self.status_changed.emit(
            f"AniList güncellendi: {ad} — {bolum_no}. bölüm" if basarili
            else f"AniList güncellenemedi: {ad}", not basarili)
        self.progress_written.emit(seri, bolum_no, basarili)

    def _media_id_bul(self, seri: str, ad: str) -> Optional[int]:
        """`seri` için AniList media kimliği (önbellek → liste → arama)."""
        if seri in self._media_ids:
            return self._media_ids[seri]
        # Önce çekilmiş izleme listesi: kullanıcı zaten takip ediyorsa arama
        # isteği atmadan doğru kaydı buluruz.
        media = en_iyi_eslesme(self._son_girisler, ad)
        if media is None:
            media = en_iyi_eslesme(
                istemci().search_anime(ad, per_page=ARAMA_ADAY_SAYISI), ad)
        if not media or not media.get("id"):
            return None
        media_id = int(media["id"])
        if seri:
            self._media_ids[seri] = media_id
        return media_id

    # ── AniList → yerel senkron ─────────────────────────────────────────────
    def yereli_senkronla(self) -> bool:
        """CURRENT listesini çekip yerel ilerlemeyi güncelle (arka planda)."""
        if not giris_var_mi():
            return False
        run_bg(self._senkronla)
        return True

    def _senkronla(self) -> None:
        try:
            user = self._user or istemci().get_current_user()
            uid = (user or {}).get("id")
            if not uid:
                return
            self._user = user
            girisler = girisleri_duzlestir(
                istemci().get_user_anime_list(int(uid), "CURRENT"), "CURRENT")
        except Exception as exc:
            print(f"[AniList] Senkron hatası: {exc}")
            return
        guncel = senkron_guncellemeleri(girisler, prefs.yerel_ilerleme())
        for seri, no in guncel.items():
            prefs.ilerleme_kaydet(seri, no)
        if guncel:
            self.status_changed.emit(
                f"AniList ile senkronize edildi: {len(guncel)} seri.", False)
        self.sync_done.emit(len(guncel))


__all__ = ["AniListService", "DURUMLAR", "DURUM_ETIKETI", "DURUM_RENGI",
           "ESLESME_ESIGI", "istemci", "giris_var_mi", "deslug", "basliklar",
           "girisleri_duzlestir", "en_iyi_eslesme", "senkron_guncellemeleri",
           "geri_donus_portu"]
