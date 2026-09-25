"""Anime detay sayfasının köprü uçları: künye, kaynak eşleştirme, bölümler.

Eski Qt'de iki sayfaydı (`DetailPage` künye + eşleştirme, `EpisodePage`
bölüm listesi). Web sayfası eski ekran görüntüsündeki düzende tek sayfa:
künye üstte, altında "Kaynaklar ve Bölümler" — kaynak başına bir akordiyon.

Durum Python'da, OTURUM olarak tutuluyor (`Oturum`): bölüm nesneleri
(`best_video()` sağlayan nesneler) JSON'a çevrilemez; sayfa bölümü
``(kaynak, sıra)`` ile anıyor, oynat/indir burada nesneye çevriliyor. Her
yeni anime yeni bir oturum numarası (``rid``) alıyor; eski oturumun geç
dönen işi `EskiIstek` fırlatıyor ve sayfa onu sessizce atıyor.

Eşleştirme kuralları `eslestirme` modülünde: başlık varyantları, eşik
(0.95), elle seçimin otomatiği ezmesi, AniList'in (yalnızca bilgi) hiç
bağlanmaması.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...common import kutuphane
from ...sources import kayit as kaynak_kaydi
from .eslestirme import eslesme_basliklari, kaynaklari_esle
from .kopru import Kopru, UcHatasi, uc
from .kunye import (
    STATUS_LABELS, anime_title, clean_html, cover_url, genre_names,
    kunye_birlestir, meta_line, score_of, studio_names,
)

# Arşiv kaynağı: künyesi arşivin kendisinde, yerel arşivde eşleştirme ağsız.
ARSIV_KAYNAGI = "TürkAnime"


class EskiIstek(RuntimeError):
    """Sayfa başka animeye geçti; bu isteğin sonucu artık geçersiz."""

    sessiz = True          # köprü konsola basmasın (bkz. `Kopru._calistir`)


@dataclass
class Oturum:
    rid: int
    anime: Dict[str, Any]
    baslik: str                                   # eşleştirme başlığı
    baglar: Dict[str, str] = field(default_factory=dict)       # kaynak → kimlik
    elle: Dict[str, str] = field(default_factory=dict)         # kullanıcının seçtiği
    oto: Dict[str, str] = field(default_factory=dict)          # kaynak → eşleşen başlık
    bolumler: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    ilk_kaynak: str = ""
    eslestirildi: bool = False                    # "tüm kaynaklar" turu yapıldı mı


# ── Qt'siz yardımcılar ───────────────────────────────────────────────────────
def dis_baglanti(anime: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Künyenin dış sayfası: AniList ``siteUrl`` ya da MyAnimeList kimliği."""
    adres = anime.get("siteUrl")
    if isinstance(adres, str) and adres.startswith("https://"):
        return {"ad": "AniList", "adres": adres}
    mal = anime.get("mal_id") or anime.get("idMal")
    if mal:
        return {"ad": "MyAnimeList", "adres": f"https://myanimelist.net/anime/{int(mal)}"}
    return None


def kunye_verisi(anime: Dict[str, Any]) -> Dict[str, Any]:
    """Sayfanın künye bölümü: gösterime hazır alanlar."""
    from .veri import tur_adi

    basliklar = anime.get("title") if isinstance(anime.get("title"), dict) else {}
    ana = anime_title(anime)
    diger = [b for b in (basliklar.get("english"), basliklar.get("native"))
             if isinstance(b, str) and b and b != ana]
    puan = score_of(anime)
    return {
        "baslik": ana,
        "alt_baslik": " · ".join(diger),
        "meta": meta_line(anime),
        "kapak": cover_url(anime) or "",
        "banner": str(anime.get("bannerImage") or ""),
        "puan": round(puan) if puan else None,
        # MyAnimeList'te `popularity` SIRA (#123), AniList'te listesine ekleyen
        # KİŞİ sayısı (483.340). Eski arayüz ikisini de "#" ile gösteriyordu.
        "populerlik": anime.get("popularity"),
        "populerlik_sira": bool(anime.get("mal_id")),
        "bolum_sayisi": anime.get("episodes"),
        "durum": STATUS_LABELS.get(str(anime.get("status") or "").upper(), ""),
        "ozet": clean_html(anime.get("description")),
        "turler": [tur_adi(t) for t in genre_names(anime)],
        "studyolar": studio_names(anime),
        "dis": dis_baglanti(anime),
    }


def kaynak_bilgisi(ad: str) -> Dict[str, Any]:
    kaynak = kaynak_kaydi.bul(ad)
    return {"ad": ad, "etiket": kaynak_kaydi.gorunen_ad(ad),
            "renk": kaynak.renk if kaynak is not None else "",
            "kisaltma": kaynak.kisaltma if kaynak is not None else ad[:2].upper()}


def oynatilabilir_kaynaklar() -> List[str]:
    from ..qt.sources_bridge import METADATA_ONLY, supported_sources
    return [ad for ad in supported_sources() if ad not in METADATA_ONLY]


def arsiv_yerel_mi() -> bool:
    """Arşiv diskte mi? (Eşleştirme ağa çıkmadan yapılabilir mi?)"""
    try:
        from ...sources import animedepo
        return animedepo.arsiv_konumu().dizin is not None
    except Exception:
        return False


# ── Uçlar ────────────────────────────────────────────────────────────────────
class DetayUclari:
    """Detay sayfasının Python tarafı.

    ``oynat``/``indir``: pencerenin oynatma/indirme yolları (fansub sorusu,
    kuyruk, geçmiş orada). ``kuyrukta``: bölümün bitmemiş indirmesi var mı.
    """

    def __init__(self, kopru: Kopru, *, oynat: Callable[[Dict[str, Any]], Any],
                 indir: Callable[[Dict[str, Any]], Any],
                 kuyrukta: Callable[[Dict[str, Any]], bool] = lambda _e: False):
        self._kopru = kopru
        self._oynat = oynat
        self._indir = indir
        self._kuyrukta = kuyrukta
        self._kilit = threading.RLock()
        self._sayac = 0
        self.oturum: Optional[Oturum] = None

    # ── Pencerenin açtığı oturumlar (GUI thread'i) ──────────────────────────
    def _yeni(self, anime: Dict[str, Any], baslik: str, kaynak: str = "",
              kimlik: str = "") -> int:
        with self._kilit:
            self._sayac += 1
            self.oturum = Oturum(rid=self._sayac, anime=dict(anime or {}),
                                 baslik=baslik)
            if kaynak and kimlik:
                self.oturum.baglar[kaynak] = kimlik
                self.oturum.ilk_kaynak = kaynak
            return self._sayac

    def ac_kesif(self, anime: Dict[str, Any]) -> int:
        """Keşif/izleme listesi kartı: tam künye, kaynağa bağlı değil."""
        return self._yeni(anime, anime_title(anime or {}))

    def ac_sonuc(self, kaynak: str, slug: str, baslik: str,
                 kayit: Optional[Dict[str, Any]] = None) -> int:
        """Arama sonucu: kaynağa bağlı (AniList hariç; o yalnızca bilgi)."""
        from ..qt.sources_bridge import METADATA_ONLY
        anime: Dict[str, Any] = {"title": {"romaji": baslik}}
        gorsel = (kayit or {}).get("image") if isinstance(kayit, dict) else None
        if isinstance(gorsel, str) and gorsel:
            anime["coverImage"] = {"large": gorsel}
        if kaynak_kaydi.kanonik_ad(kaynak) in METADATA_ONLY:
            return self._yeni(anime, baslik)
        return self._yeni(anime, baslik, kaynak, str(slug or ""))

    def ac_kitaplik(self, kayit: Dict[str, Any]) -> int:
        """Kitaplık kaydı: kaynağın kendi kimliğiyle bağlı açılır."""
        kaynak = str((kayit or {}).get("kaynak") or "")
        kimlik = str((kayit or {}).get("kimlik") or "")
        baslik = str((kayit or {}).get("baslik") or kimlik)
        kapak = (kayit or {}).get("kapak") or ""
        return self.ac_sonuc(kaynak, kimlik, baslik,
                             {"image": kapak} if kapak else None)

    # ── Yardımcılar ─────────────────────────────────────────────────────────
    def _oturum(self, rid: Any) -> Oturum:
        oturum = self.oturum
        if oturum is None or oturum.rid != int(rid):
            raise EskiIstek("eski istek")
        return oturum

    def _kaynaklar(self, oturum: Oturum) -> List[Dict[str, Any]]:
        """Bağlı kaynaklar, sayfanın akordiyon sırasıyla (ilk kaynak başta)."""
        sira = [k.ad for k in kaynak_kaydi.kaynaklar()]

        def anahtar(ad: str):
            kanonik = kaynak_kaydi.kanonik_ad(ad)
            return (ad != oturum.ilk_kaynak,
                    sira.index(kanonik) if kanonik in sira else len(sira), ad)

        out = []
        for ad in sorted(oturum.baglar, key=anahtar):
            bilgi = kaynak_bilgisi(ad)
            bilgi["eslesme"] = oturum.oto.get(ad, "") if ad not in oturum.elle else ""
            bilgi["elle"] = ad in oturum.elle
            bilgi["favori"] = kutuphane.favori_mi(ad, oturum.baglar[ad])
            out.append(bilgi)
        return out

    def _bolum_durumlari(self, oturum: Oturum, kaynak: str, gecmis: Any,
                         rozet: bool, veri: Dict[str, Any],
                         kuyruk: bool = True) -> List[Dict[str, Any]]:
        """Satır rozetleri. Kitaplık (``veri``) ve geçmiş BİR KEZ okunup
        veriliyor: 1000 bölümlük seride satır başına dosya okumak saniyeler.

        ``kuyruk``: indirme kuyruğu yalnızca GUI thread'inden okunur
        (yönetici orada değişiyor); arka plandan çağrıda False.
        """
        from ..qt import prefs
        out = []
        for entry in oturum.bolumler.get(kaynak, []):
            izlendi, indirildi = gecmis.durum(entry.get("obj"))
            k = prefs.kitaplik_kimligi(entry)
            konum = (kutuphane.konum_getir(k["kaynak"], k["kimlik"], k["bolum_slug"], veri)
                     if k["kaynak"] and k["kimlik"] and k["bolum_slug"] else None)
            out.append({
                "izlendi": bool(izlendi), "indirildi": bool(indirildi),
                "rozet": rozet,
                "konum": kutuphane.sure_metni(konum["konum"]) if konum else "",
                "kuyrukta": bool(kuyruk and self._kuyrukta(entry)),
            })
        return out

    def _devam_hedefi(self, oturum: Oturum, gecmis: Any,
                      veri: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """"Devam et" düğmesi: yarım bölüm, yoksa en ileri izlenenin ardı.

        Eski bölüm sayfasıyla aynı kural (`EpisodePage.devam_hedefi`), ama
        liste kaynak başına: sıradaki bölüm, izlenen bölümün BULUNDUĞU
        kaynağın listesinden (ilk kaynak öncelikli).
        """
        from ..qt import prefs
        yarimlar = []
        for kaynak, entries in oturum.bolumler.items():
            for sira, entry in enumerate(entries):
                if not entry.get("kimlik"):
                    continue
                _seri, slug = prefs.bolum_kimligi(entry.get("obj"))
                konum = kutuphane.konum_getir(kaynak, entry["kimlik"], slug, veri)
                if konum:
                    yarimlar.append((float(konum.get("zaman") or 0), kaynak, sira,
                                     entry, konum.get("konum")))
        if yarimlar:
            _z, kaynak, sira, entry, saniye = max(yarimlar, key=lambda y: y[0])
            return {"kaynak": kaynak, "sira": sira,
                    "metin": f"Devam et: {entry.get('title') or 'bölüm'} "
                             f"({kutuphane.sure_metni(saniye)})"}
        sirali = sorted(oturum.bolumler, key=lambda k: k != oturum.ilk_kaynak)
        for kaynak in sirali:
            entries = oturum.bolumler[kaynak]
            izlenen = [i for i, e in enumerate(entries) if gecmis.durum(e.get("obj"))[0]]
            if not izlenen:
                continue
            sira = kutuphane.sonraki_bolum(range(len(entries)), izlenen)
            if sira is None:
                return None
            return {"kaynak": kaynak, "sira": sira,
                    "metin": f"Sıradaki: {entries[sira].get('title') or 'bölüm'}"}
        return None

    def _gecmis(self):
        from ..qt import prefs
        rozet = prefs.oku().izlendi_ikonu
        # Rozet kapalıyken de seçim yardımcıları ("izlenmemişler") geçmişe
        # bakmalı; rozet bilgisi ayrıca gidiyor.
        return prefs.Gecmis.yukle(), rozet

    # ── Sayfanın uçları ─────────────────────────────────────────────────────
    @uc("detay", arka=True)
    def detay(self, rid: int) -> Dict[str, Any]:
        """Künye (arşiv kaydıysa arşiv künyesiyle zenginleşmiş) + bağlar."""
        oturum = self._oturum(rid)
        arsiv = oturum.baglar.get(ARSIV_KAYNAGI) or next(
            (k for a, k in oturum.baglar.items()
             if kaynak_kaydi.kanonik_ad(a) == ARSIV_KAYNAGI), "")
        if arsiv:
            try:
                from ...sources import animedepo
                bilgi = animedepo.anime_bilgisi(arsiv)
                if bilgi:
                    with self._kilit:
                        if self.oturum is oturum:
                            oturum.anime = kunye_birlestir(oturum.anime, bilgi)
            except Exception as exc:     # künye süs: okunamazsa sayfa yine açılır
                print(f"[Detay] Arşiv künyesi okunamadı ({arsiv}): {exc}")
        self._oturum(rid)
        return {
            "rid": oturum.rid,
            "kunye": kunye_verisi(oturum.anime),
            "kaynaklar": self._kaynaklar(oturum),
            "arsivde_ara": (not oturum.baglar and ARSIV_KAYNAGI in oynatilabilir_kaynaklar()
                            and arsiv_yerel_mi()),
        }

    @uc("bolumler", arka=True)
    def bolumler(self, rid: int, kaynak: str) -> Dict[str, Any]:
        """Bağlı kaynağın bölümleri (+ geçmiş rozetleri, kaldığın yer)."""
        from ..qt import sources_bridge
        from ...common.episode_parser import extract_episode_info
        oturum = self._oturum(rid)
        kimlik = oturum.baglar.get(kaynak)
        if not kimlik:
            raise UcHatasi(f"{kaynak_kaydi.gorunen_ad(kaynak)} bu animeye bağlı değil")
        try:
            ham = sources_bridge.fetch_episodes(kaynak, kimlik, oturum.baslik)
        except sources_bridge.UnsupportedSource as exc:
            # "AniList yalnızca metadata kaynağı", "AnimeciX sayısal kimlik
            # bekliyor": kullanıcıya yazılmış cümleler, olduğu gibi gitsin.
            raise UcHatasi(str(exc)) from None
        bolumler = [e for e in (ham or []) if isinstance(e, dict)]
        kapak = cover_url(oturum.anime) or ""
        for entry in bolumler:
            # Kitaplık anahtarı (bkz. `prefs.kitaplik_kimligi`): kaynak ve
            # kaynağın KENDİ kimliği; bölüm nesnesinden türetilemiyor.
            entry["kaynak"] = kaynak
            entry["kimlik"] = kimlik
            entry["seri_adi"] = oturum.baslik
            entry["kapak"] = kapak
        with self._kilit:
            self._oturum(rid).bolumler[kaynak] = bolumler
        gecmis, rozet = self._gecmis()
        veri = kutuphane.oku()
        # Kuyruk durumu burada yok (arka plan thread'i); sayfa liste gelince
        # `bolum_durumlari`'nı çağırıp tazeliyor.
        durumlar = self._bolum_durumlari(oturum, kaynak, gecmis, rozet, veri,
                                         kuyruk=False)
        liste = []
        for sira, (entry, durum) in enumerate(zip(bolumler, durumlar)):
            baslik = str(entry.get("title") or f"Bölüm {sira + 1}")
            _sezon, no = extract_episode_info(baslik, oturum.baslik)
            liste.append({"sira": sira, "baslik": baslik, "no": no, **durum})
        return {"kaynak": kaynak, "bolumler": liste,
                "devam": self._devam_hedefi(oturum, gecmis, veri)}

    @uc("bolum_durumlari")
    def bolum_durumlari(self, rid: int) -> Dict[str, Any]:
        """Rozetler, kuyruk ve "Devam et" (GUI thread'i; ağa çıkmaz).

        GUI thread'inde: indirme yöneticisi orada değişiyor. Okunan iki küçük
        JSON (geçmiş, kitaplık) eski bölüm sayfasında da burada okunuyordu.
        """
        oturum = self._oturum(rid)
        gecmis, rozet = self._gecmis()
        veri = kutuphane.oku()
        return {"durumlar": {k: self._bolum_durumlari(oturum, k, gecmis, rozet, veri)
                             for k in list(oturum.bolumler)},
                "devam": self._devam_hedefi(oturum, gecmis, veri)}

    @uc("eslestir", arka=True)
    def eslestir(self, rid: int, kaynaklar: Optional[List[str]] = None) -> Dict[str, Any]:
        """Otomatik eşleştirme; ``kaynaklar`` yoksa bütün oynatılabilir kaynaklar.

        Elle seçilen kaynağa DOKUNULMAZ: arama sürerken kullanıcı "İstediğin
        anime değil mi?"den doğru kaydı seçmiş olabilir.
        """
        oturum = self._oturum(rid)
        tum = kaynaklar is None
        hedefler = [k for k in (kaynaklar if kaynaklar is not None
                                else oynatilabilir_kaynaklar())
                    if k in oynatilabilir_kaynaklar() and k not in oturum.baglar]
        bagli = {kaynak_kaydi.kanonik_ad(k) for k in oturum.baglar}
        basliklar = eslesme_basliklari(oturum.anime, oturum.baslik) or [oturum.baslik]
        baglar, eslesen, eslesmeyen = kaynaklari_esle(basliklar, hedefler, bagli)
        yeni = []
        with self._kilit:
            oturum = self._oturum(rid)
            for kaynak, slug in baglar.items():
                if kaynak in oturum.elle or kaynak in oturum.baglar:
                    continue
                oturum.baglar[kaynak] = slug
                oturum.oto[kaynak] = eslesen.get(kaynak, "")
                yeni.append(kaynak)
            if tum:
                oturum.eslestirildi = True
        return {"yeni": yeni, "eslesmeyen": [k for k in eslesmeyen if k not in oturum.baglar],
                "kaynaklar": self._kaynaklar(oturum)}

    @uc("elle_eslestir")
    def elle_eslestir(self, rid: int, kaynak: str, slug: str, baslik: str = "") -> Dict[str, Any]:
        """"İstediğin anime değil mi?" seçimi: kaynağı bu kayda bağla."""
        from ..qt.sources_bridge import METADATA_ONLY
        oturum = self._oturum(rid)
        if kaynak_kaydi.kanonik_ad(kaynak) in METADATA_ONLY:
            raise UcHatasi(f"{kaynak_kaydi.gorunen_ad(kaynak)} yalnızca bilgi "
                             "kaynağı; bölüm için başka bir kaynaktan seçin")
        if not slug:
            raise UcHatasi("kayıt seçilmedi")
        with self._kilit:
            # Aynı arşivin eski adıyla ikinci bağ kalmasın.
            for eski in [a for a in oturum.baglar
                         if kaynak_kaydi.kanonik_ad(a) == kaynak_kaydi.kanonik_ad(kaynak)]:
                oturum.baglar.pop(eski, None)
                oturum.bolumler.pop(eski, None)
            oturum.baglar[kaynak] = str(slug)
            oturum.elle[kaynak] = str(slug)
            oturum.oto.pop(kaynak, None)
            if not oturum.ilk_kaynak:
                oturum.ilk_kaynak = kaynak
        from ..qt.workers import run_bg
        from . import eslestirme
        # Modülden çağrı anında okunuyor (testler sahteleyebilsin).
        run_bg(eslestirme.save_match, kaynak, str(slug), baslik or oturum.baslik)
        return {"kaynak": kaynak, "kaynaklar": self._kaynaklar(oturum)}

    def _kayit(self, oturum: Oturum, kaynak: str, sira: int) -> Dict[str, Any]:
        try:
            return oturum.bolumler[kaynak][int(sira)]
        except (KeyError, IndexError, TypeError, ValueError):
            raise UcHatasi("bölüm bulunamadı (liste yenilenmiş olabilir)") from None

    @uc("oynat")
    def oynat(self, rid: int, kaynak: str, sira: int) -> bool:
        entry = self._kayit(self._oturum(rid), kaynak, sira)
        self._oynat(entry)
        return True

    @uc("indir")
    def indir(self, rid: int, secim: List[List[Any]]) -> Dict[str, int]:
        """Seçili bölümleri kuyruğa al: ``secim`` = ``[[kaynak, sıra], ...]``.

        Kuyrukta olan tekrar eklenmez; kaç tanesinin atlandığı dönüyor.
        """
        oturum = self._oturum(rid)
        yeni = zaten = 0
        for kaynak, sira in secim or []:
            entry = self._kayit(oturum, str(kaynak), sira)
            if self._kuyrukta(entry):
                zaten += 1
                continue
            self._indir(entry)
            yeni += 1
        return {"yeni": yeni, "zaten": zaten}

    @uc("favori")
    def favori(self, rid: int, kaynak: str, deger: bool) -> bool:
        from ..qt.workers import run_bg
        oturum = self._oturum(rid)
        kimlik = oturum.baglar.get(kaynak)
        if not kimlik:
            raise UcHatasi("kitaplığa eklemek için anime bir kaynağa bağlanmalı")
        run_bg(kutuphane.favori_ayarla, kaynak, kimlik, bool(deger),
               oturum.baslik, cover_url(oturum.anime) or "")
        return bool(deger)


__all__ = ["DetayUclari", "Oturum", "EskiIstek", "kunye_verisi", "dis_baglanti",
           "kaynaklari_esle", "oynatilabilir_kaynaklar", "ARSIV_KAYNAGI"]
