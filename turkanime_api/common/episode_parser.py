"""
Universal episode + season parser **ve** çok kaynaklı bölüm birleştirici.

Kaynaklar bölüm başlıklarını farklı formatlarda veriyor:
    - "1. Bölüm",  "Bölüm 1",  "Episode 1",  "Ep. 1"
    - "S01E02",  "S1E2",  "s01e02"
    - "1x02",  "01x02"
    - "B01S02",  "b1s2",  "B01-S02"  (Türkçe varyant)
    - "Sezon 1 Bölüm 2",  "Season 2 Episode 13"
    - "2. Sezon 5. Bölüm",  "Sezon 2 - 5. Bölüm"  (Türkçe sıra sayısı)
    - "2nd Season 5. Bölüm",  "3rd Season Bölüm 4"  (İngilizce sıra sayısı)
    - "01_02",  "S01.E02",  "S01 E02"
    - "5.5. Bölüm"  (ara/özel bölüm — 5 ile aynı satıra düşmemeli)
    - Çıplak sayı: "12",  "12 - The Title"
    - "Bölüm 12 Final",  "Movie",  "OVA 1"

Bu modül üç API verir:
    ``parse_episode(text)``       → :class:`EpisodeInfo` (zengin sonuç)
    ``extract_episode_info(text)``→ ``(sezon, bölüm)`` (sade sözleşme)
    ``merge_episodes(data)``      → kaynakları tek listede birleştirir

Kaynakların verdiği başlık ANİME ADINI da içerir ("3x3 Eyes 1. Bölüm",
"86 2nd Season 5. Bölüm"). Addaki rakamların bölüm/sezon sanılmaması için iki
savunma birden var:

1. Çağıran anime adını biliyorsa ``anime_title`` ile geçer, ad başlıktan
   ayıklanır (en kesin çözüm — sezon bilgisi de anime kimliğinin parçası
   olduğu için "86 2nd Season" + "5. Bölüm" ile çıplak "5. Bölüm" aynı
   anahtara düşer, kaynaklar birleşir).
2. Ad bilinmese de kalıp tablosu iki katmanlı: önce işaret sözcüğü taşıyanlar
   (Bölüm/Sezon/Episode/Season), sonra yalnız kod biçimleri (S01E02, 1x02).
   Eşleşme de metnin SONUNDAN aranır; işaret hep sonda, ad hep başta durur.

Parser burada TEK yerde durur; `gui/qt/pages/episodes.py`, `pages/detail.py` ve
`qt/progress_dialog.py` aynı fonksiyonları import eder. İki ayrı regex seti
tutmak, aynı bölümün iki yerde farklı numaralanması demekti — bu modül o
ayrışmayı kapatmak için çıkarıldı.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class EpisodeInfo:
    """Bölüm/sezon parse sonucu.

    ``raw``      — orijinal metin
    ``season``   — sezon numarası (yoksa None)
    ``episode``  — bölüm numarası (yoksa None)
    ``label``    — özel etiket: "movie", "ova", "special", "final" vb (yoksa None)
    ``title``    — bölüm adı kalıntısı (numarayı temizledikten sonra)
    ``score``    — eşleştirme güveni (0..1) — hangi pattern'in eşleştiğine göre
    ``sub``      — ara bölüm ("5.5. Bölüm" → 5); yoksa None
    """
    raw: str
    season: Optional[int]
    episode: Optional[int]
    label: Optional[str]
    title: str
    score: float
    sub: Optional[int] = None

    def normalized(self) -> str:
        """Normalize edilmiş kanonik form üret.

        Örnekler:
            S01E02
            S01E02.5
            S01E02 (Final)
            Movie
            OVA 1
        """
        if self.label and self.episode is None:
            return self.label.upper() if not self.label.endswith("1") else self.label
        if self.season is not None and self.episode is not None:
            base = f"S{self.season:02d}E{self.episode:02d}"
        elif self.episode is not None:
            base = f"E{self.episode:02d}"
        else:
            return self.raw.strip()
        if self.sub:
            base += f".{self.sub}"
        if self.label:
            base += f" ({self.label.title()})"
        return base

    def key(self) -> Tuple[int, int]:
        """Sıralama için (season, episode) tuple — None'lar 0 sayılır."""
        return (self.season or 0, self.episode or 0)


# ─────────────────────────────────────────────────────────────────────────────
# Pattern tablosu — sıra önemli, iki katman hâlinde:
#   1) İşaret sözcüğü taşıyanlar (Bölüm/Sezon/Episode/Season) — anime adının
#      içindeki rakamlara takılmazlar, bu yüzden HEPSİ önce denenir.
#   2) Yalnız kod/sayı biçimleri (S01E02, 1x02, 01_02, çıplak sayı) — bunlar
#      "3x3 Eyes" ya da "86" gibi adları bölüm numarası sanabilir, o yüzden
#      ancak birinci katman hiç tutmadığında söz sahibi olurlar.
# Her katmanda kendi içinde en spesifik kalıp önce gelir.
# ─────────────────────────────────────────────────────────────────────────────

# Bir sayıyı yakalayan grup: en az 1, en fazla 4 hane
_N = r"(\d{1,4})"
# Ara bölüm ("5.5") kesiri — en fazla 2 hane
_ALT = r"(\d{1,2})"
# İngilizce sıra sayısı eki: 1st / 2nd / 3rd / 4th
_SIRA = r"(?:st|nd|rd|th)"
_BOLUM = r"(?:Bölüm|Bolum|Episode|Ep\.?)"
_SEZON = r"(?:Sezon|Season)"

# (regex, season_group, episode_group, sub_group, score) — indeksler 1-tabanlı
_PATTERNS: List[Tuple[re.Pattern, Optional[int], Optional[int], Optional[int], float]] = [
    # ── 1. katman: işaret sözcüğü taşıyanlar ────────────────────────────────
    # "Sezon 1 Bölüm 2" / "Season 2 Episode 13"
    (re.compile(rf"(?i){_SEZON}\s*{_N}\s*(?:Episode|Bölüm|Bolum)\s*{_N}"),
     1, 2, None, 0.95),
    # "Bölüm 2 Sezon 1" / "Episode 13 Season 2"
    (re.compile(rf"(?i)(?:Episode|Bölüm|Bolum)\s*{_N}\s*{_SEZON}\s*{_N}"),
     2, 1, None, 0.95),
    # ── Sıra sayılı sezon varyantları ───────────────────────────────────────
    # Bunlar "yalnız sezon" kalıbından ÖNCE denenmeli: "2. Sezon 5. Bölüm"
    # metninde "Sezon 5" alt dizesi vardır ve sezon-yalnız kalıbı sezonu 5
    # sanıp bölümü hiç bulamaz.
    # "2. Sezon 5. Bölüm" / "2.Sezon 5.Bolum"
    (re.compile(rf"(?i)\b{_N}\s*\.\s*{_SEZON}\b.*?\b{_N}\s*\.\s*"
                r"(?:Bölüm|Bolum|Episode)\b"), 1, 2, None, 0.95),
    # "2nd Season 5. Bölüm" — AnimeDepo başlıklarının baskın biçimi; anime adı
    # sezonu İngilizce taşıyor ("86 2nd Season 5. Bölüm").
    (re.compile(rf"(?i)\b{_N}{_SIRA}\s*{_SEZON}\b.*?\b{_N}\s*\.\s*"
                r"(?:Bölüm|Bolum|Episode)\b"), 1, 2, None, 0.95),
    # "2. Sezon Bölüm 5"
    (re.compile(rf"(?i)\b{_N}\s*\.\s*{_SEZON}\b.*?"
                rf"(?:Bölüm|Bolum|Episode)\s*{_N}\b"), 1, 2, None, 0.95),
    # "2nd Season Bölüm 5"
    (re.compile(rf"(?i)\b{_N}{_SIRA}\s*{_SEZON}\b.*?"
                rf"(?:Bölüm|Bolum|Episode)\s*{_N}\b"), 1, 2, None, 0.95),
    # "Sezon 2 - 5. Bölüm"
    (re.compile(rf"(?i){_SEZON}\s*{_N}\b.*?\b{_N}\s*\.\s*"
                r"(?:Bölüm|Bolum|Episode)\b"), 1, 2, None, 0.95),
    # ── Ara bölüm: "5.5. Bölüm" / "Bölüm 5.5" ───────────────────────────────
    # "12. Bölüm"den ÖNCE denenir: aksi hâlde "12.5. Bölüm" metninde yalnız
    # "5. Bölüm" eşleşir, ara bölüm 5. bölümün anahtarına düşüp onu yer.
    (re.compile(rf"(?i)\b{_N}\s*\.\s*{_ALT}\s*\.\s*{_BOLUM}\b"), None, 1, 2, 0.92),
    (re.compile(rf"(?i)\b{_BOLUM}\s*{_N}\s*\.\s*{_ALT}\b"), None, 1, 2, 0.92),
    # "12. Bölüm" / "12.Bolum" / "12. Episode" — Türkçe kaynakların baskın
    # biçimi, bu yüzden "Bölüm 12"den ÖNCE denenir: "5. Bölüm 2. Kısım"
    # metninde ters sırada "Bölüm 2" eşleşir ve bölüm yanlış numaralanır.
    (re.compile(rf"(?i)\b{_N}\s*\.\s*{_BOLUM}\b"), None, 1, None, 0.9),
    # "Bölüm 12" / "Bolum 12" / "Episode 12" / "Ep. 12" / "Ep 12"
    (re.compile(rf"(?i)\b{_BOLUM}\s*{_N}\b"), None, 1, None, 0.9),
    # Yalnız sezon: "Sezon 2" / "Season 2" / "2. Sezon" / "2nd Season"
    # → episode None. Bölüm kalıplarından SONRA: "86 2nd Season 5. Bölüm"
    # başlığında sezonu yakalayıp bölümü hiç aramamak eski hatanın ta kendisi.
    (re.compile(rf"(?i){_SEZON}\s*{_N}\b"), 1, None, None, 0.7),
    (re.compile(rf"(?i)\b{_N}\s*\.\s*{_SEZON}\b"), 1, None, None, 0.7),
    (re.compile(rf"(?i)\b{_N}{_SIRA}\s*{_SEZON}\b"), 1, None, None, 0.7),
    # ── 2. katman: yalnız kod/sayı biçimleri ────────────────────────────────
    # S01E02, S1E2, S01.E02, S01 E02, S01-E02
    (re.compile(rf"(?i)\bS\s*{_N}[\s._-]*E\s*{_N}\b"), 1, 2, None, 1.0),
    # 1x02, 01x02
    (re.compile(rf"\b{_N}\s*[xX]\s*{_N}\b"), 1, 2, None, 0.95),
    # B01S02 / b1s2 / B01-S02  (Türkçe: B=Bölüm, S=Sezon — tersi varyant)
    (re.compile(rf"(?i)\bB\s*{_N}[\s._-]*S\s*{_N}\b"), 2, 1, None, 0.95),
    # S01B02 (S=Sezon, B=Bölüm — yine Türkçe)
    (re.compile(rf"(?i)\bS\s*{_N}[\s._-]*B\s*{_N}\b"), 1, 2, None, 0.95),
    # "S01" yalnız sezon
    (re.compile(rf"(?i)\bS\s*{_N}\b"), 1, None, None, 0.6),
    # "E12" / "E 12"
    (re.compile(rf"(?i)\bE\s*{_N}\b"), None, 1, None, 0.7),
    # "01_02"  (sezon_bolum)
    (re.compile(rf"\b{_N}_{_N}\b"), 1, 2, None, 0.6),
    # Çıplak: tek sayı (en zayıf eşleşme) — string'in başında
    (re.compile(rf"^\s*{_N}(?:\s*[-–:|.]|\s*$)"), None, 1, None, 0.5),
]

_LABEL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bmovie\b|\bfilm\b"), "movie"),
    (re.compile(r"(?i)\bOVA\s*(\d*)\b"), "ova"),
    (re.compile(r"(?i)\bspecial\s*(\d*)\b|\bözel\s*(\d*)\b"), "special"),
    (re.compile(r"(?i)\bfinal\b|\bson bölüm\b"), "final"),
    (re.compile(r"(?i)\brecap\b"), "recap"),
    (re.compile(r"(?i)\bopening\b|\bOP\b"), "opening"),
    (re.compile(r"(?i)\bending\b|\bED\b"), "ending"),
]


def _detect_label(text: str) -> Optional[str]:
    for pat, name in _LABEL_PATTERNS:
        if pat.search(text):
            return name
    return None


# Anime adı → önek regex'i. Ad bir kere kullanılıp atılmıyor (aynı liste için
# yüzlerce başlık parse ediliyor), bu yüzden derlenmiş hâli saklanıyor.
_AD_CACHE: Dict[str, Optional[re.Pattern]] = {}
_KIRPMA = " \t-–—:|.,_"


def _ad_deseni(anime_adi: str) -> Optional[re.Pattern]:
    """Anime adını "araya ne girerse girsin" eşleşen bir desene çevir.

    Kaynaklar aynı adı farklı noktalamayla yazıyor ("Gun Gale Online" vs
    "gun-gale-online"); sözcükler arasına `[\\W_]*` koymak slug ile insan
    okunur adı aynı desene indirger.
    """
    if anime_adi in _AD_CACHE:
        return _AD_CACHE[anime_adi]
    parcalar = re.findall(r"\w+", anime_adi, re.UNICODE)
    desen = None
    if parcalar:
        desen = re.compile(r"[\W_]*".join(re.escape(p) for p in parcalar), re.IGNORECASE)
    if len(_AD_CACHE) > 512:
        # Tarayıcı binlerce anime geziyor; cache sınırsız büyümesin.
        _AD_CACHE.clear()
    _AD_CACHE[anime_adi] = desen
    return desen


def _ad_ayikla(baslik: str, anime_adi: str) -> str:
    """Bölüm başlığından anime adını at ("3x3 Eyes 1. Bölüm" → "1. Bölüm").

    Addaki rakamlar bölüm/sezon sanıldığı için veri kaybı yaşanıyordu; ad
    biliniyorsa onu metinden çıkarmak en kesin çözüm. Ad başta aranmıyor,
    metnin herhangi bir yerinde: bazı kaynaklar başlığın önüne fansub etiketi
    koyuyor. Geriye hiçbir şey kalmıyorsa dokunulmaz — ad ile bölüm işareti
    aynı metin olabilir ("86" animesinin "86. Bölüm"ü).
    """
    if not anime_adi or not baslik:
        return baslik
    desen = _ad_deseni(anime_adi)
    if desen is None:
        return baslik
    m = desen.search(baslik)
    if m is None:
        return baslik
    kalan = (baslik[:m.start()] + " " + baslik[m.end():]).strip(_KIRPMA)
    return kalan or baslik


def _kaliplari_uygula(metin: str, raw: str, label: Optional[str]) -> Optional[EpisodeInfo]:
    """Kalıp tablosunu sırayla dene; ilk tutan kazanır."""
    for pat, sgrp, egrp, subgrp, score in _PATTERNS:
        m = _son_eslesme(pat, metin)
        if not m:
            continue
        try:
            season = int(m.group(sgrp)) if sgrp else None
            episode = int(m.group(egrp)) if egrp else None
            sub = int(m.group(subgrp)) if subgrp else None
        except (ValueError, IndexError):
            continue
        # Numara mantıklı mı? (4 hane max, 0 değil)
        if episode is not None and (episode > 9999 or episode == 0):
            # season 0 olabilir ama episode 0 nadir
            if score < 0.7:
                continue
        # Numarayı sil, kalan başlık
        title = (metin[:m.start()] + metin[m.end():]).strip(" -–:|.")
        return EpisodeInfo(
            raw=raw, season=season, episode=episode,
            label=label, title=title, score=score, sub=sub,
        )
    return None


def _son_eslesme(pat: re.Pattern, metin: str) -> Optional[re.Match]:
    """Kalıbın metindeki SON eşleşmesi.

    "Anime Adı 5. Bölüm" biçiminde işaret hep sonda, ad hep başta durur; ilk
    eşleşmeyi almak addaki rakamları bölüm numarası saymak demektir.
    """
    son = None
    for m in pat.finditer(metin):
        son = m
    return son


def parse_episode(text: str, anime_title: str = "") -> EpisodeInfo:
    """Bir bölüm başlığını parse et.

    Args:
        text: Kaynak adapter'ın verdiği ham başlık (ör. "Bölüm 12 - Yeni Dünya")
        anime_title: Biliniyorsa anime adı — başlıktan ayıklanır, böylece
            addaki rakamlar ("3x3 Eyes", "86 2nd Season") bölüm sanılmaz.

    Returns:
        EpisodeInfo - hiçbir şey eşleşmezse season=episode=None ve score=0.
    """
    if not text:
        return EpisodeInfo(raw="", season=None, episode=None, label=None, title="", score=0.0)

    raw = text.strip()
    label = _detect_label(raw)
    aday = _ad_ayikla(raw, anime_title)

    bilgi = _kaliplari_uygula(aday, raw, label)
    if bilgi is not None and bilgi.episode is not None:
        return bilgi
    if aday != raw:
        # Ad temizliği bölüm işaretini de yemiş olabilir; ham başlık son söz.
        ham = _kaliplari_uygula(raw, raw, label)
        if ham is not None and ham.episode is not None:
            return ham
    if bilgi is not None:
        return bilgi

    # Hiçbir pattern eşleşmedi
    if label:
        return EpisodeInfo(raw=raw, season=None, episode=None, label=label, title=aday, score=0.5)
    return EpisodeInfo(raw=raw, season=None, episode=None, label=None, title=aday, score=0.0)


def normalize_title(text: str, anime_title: str = "") -> str:
    """Bir bölüm başlığını kanonik forma çevir.

    Kısa yol — sadece normalize edilmiş string ister."""
    return parse_episode(text, anime_title).normalized()


def sort_episodes(titles: List[str]) -> List[Tuple[str, EpisodeInfo]]:
    """Bir başlık listesini (season, episode) sırasına göre sırala.

    Returns:
        [(original_title, EpisodeInfo), ...]
    """
    parsed = [(t, parse_episode(t)) for t in titles]
    return sorted(parsed, key=lambda x: x[1].key())


# ─────────────────────────────────────────────────────────────────────────────
# Sade (sezon, bölüm) sözleşmesi — Qt sayfaları bunları buradan import eder
# ─────────────────────────────────────────────────────────────────────────────
_SAYI_RE = re.compile(r"\d+")


def extract_episode_info(title: str, anime_title: str = "") -> Tuple[int, int]:
    """Bölüm başlığından ``(sezon, bölüm)`` çıkar — her zaman sayı döndürür.

    `parse_episode` "bilmiyorum" diyebilmek için ``None`` döndürür; birleştirme
    sözlüğünün anahtarı ise sayı olmak zorunda. Bu sarmalayıcı eski GUI'nin
    varsayımlarını korur:

        - sezon bulunamazsa 1
        - bölüm bulunamazsa başlıktaki SON sayı (çoğu kaynak "Anime Adı 5"
          gibi başlıklar veriyor; ilk sayıyı almak "3x3 Eyes 1" başlığında
          anime adını okumak demekti), o da yoksa 0

    Örnekler:
        "S02E05" → (2, 5) · "2. Sezon 5. Bölüm" → (2, 5) · "Bölüm 5" → (1, 5)
        "01" → (1, 1) · "Movie" → (1, 0) · "86 2nd Season 5. Bölüm" → (2, 5)
    """
    if not title:
        return (1, 0)
    return _sezon_bolum(parse_episode(title, anime_title))[:2]


def _sezon_bolum(info: EpisodeInfo) -> Tuple[int, int, int]:
    """`EpisodeInfo` → ``(sezon, bölüm, ara bölüm)`` — hepsi sayı."""
    if info.episode is not None:
        return (info.season or 1, info.episode, info.sub or 0)
    # Numara işaretsiz kaldı: kalan metindeki son sayıya bak. `info.title`
    # kullanılıyor, ham başlık değil — anime adı ayıklandıysa oradaki
    # rakamlar bu aşamada da bölüm sanılmamalı.
    sayilar = _SAYI_RE.findall(info.title or "")
    return (info.season or 1, int(sayilar[-1]) if sayilar else 0, 0)


def extract_episode_number(title: str, anime_title: str = "") -> int:
    """Yalnızca bölüm numarası (geriye uyumluluk sarmalayıcısı)."""
    return extract_episode_info(title, anime_title)[1]


def normalize_episode_title(title: str, episode_num: int, season_num: int = 1,
                            sub: int = 0) -> str:
    """Bölüm başlığını normalize et — anime ismini at, sezon bilgisini koru.

    "Anime İsmi 1. Bölüm" → "1. Bölüm" · "2. Sezon 5. Bölüm" → "S02E05"
    Ara bölüm ("5.5. Bölüm") etiketinde kesir korunur; aksi hâlde satır
    5. bölümle aynı adı taşır ve kullanıcı hangisi olduğunu ayırt edemez.

    Amaç, aynı bölümü farklı kaynakların farklı adlandırmasına rağmen tek
    satırda gösterebilmek: satır etiketi hangi kaynağın önce geldiğine göre
    değişmemeli.
    """
    numara = f"{episode_num}.{sub}" if sub else f"{episode_num}"

    # Sezon bilgisi varsa S0XE0Y formatında döndür
    if season_num > 1:
        temel = f"S{season_num:02d}E{episode_num:02d}"
        return f"{temel}.{sub}" if sub else temel

    if not title:
        return f"{numara}. Bölüm"

    stripped = title.strip()
    # Zaten normalize edilmişse (sadece bölüm bilgisi varsa) olduğu gibi bırak
    if re.match(r'^\d+(?:\.\d+)?\s*\.\s*[Bb][öÖ]l[üÜ]m', stripped):
        return stripped
    if re.match(r'^[Bb][öÖ]l[üÜ]m\s*\d+', stripped):
        return stripped
    if re.match(r'^[Ee]pisode\s*\d+', stripped, re.IGNORECASE):
        return stripped
    if re.match(r'^[Ss]\d+[Ee]\d+', stripped):
        return stripped

    # "Anime İsmi X. Bölüm" formatından "X. Bölüm"ü çıkar
    match = re.search(r'(\d+(?:\.\d+)?\s*\.\s*[Bb][öÖ]l[üÜ]m.*?)$', title)
    if match:
        return match.group(1).strip()

    return f"{numara}. Bölüm"


# ─────────────────────────────────────────────────────────────────────────────
# Çok kaynaklı birleştirme
# ─────────────────────────────────────────────────────────────────────────────
def _kayit_anahtari(entry: Dict[str, Any], index: int = 0,
                    anime_title: str = "") -> Tuple[int, int, int]:
    """Tek bir kaynak kaydı için ``(sezon, bölüm, ara bölüm)`` kimliği üret."""
    number = entry.get('episode_number', entry.get('number', 0))
    season = entry.get('season_number', entry.get('season', 1)) or 1
    title = entry.get('title', '') or ''
    sub = 0

    if not number:
        season, number, sub = _sezon_bolum(parse_episode(title, anime_title))
    elif season == 1:
        # Numara alanı var ama sezon yok: sezonu yine de başlıktan deneyelim,
        # yoksa 2. sezonun 1. bölümü 1. sezonunkiyle aynı satıra düşer.
        title_season, _, _ = _sezon_bolum(parse_episode(title, anime_title))
        if title_season > 1:
            season = title_season

    if not number:
        number = index + 1
    return (int(season), int(number), int(sub))


def episode_key(entry: Dict[str, Any], index: int = 0,
                anime_title: str = "") -> Tuple[int, int]:
    """Tek bir kaynak kaydı için ``(sezon, bölüm)`` kimliği üret.

    Öncelik sırası kaynakların ne verdiğine göre: adapter numarayı alan olarak
    veriyorsa ona güveniriz, vermiyorsa başlığı parse ederiz, o da tutmazsa
    listedeki sıraya düşeriz. Sıraya düşmek son çare: numarasız bölümler
    (özel/OVA) aksi hâlde tek anahtarda toplanıp birbirini yerdi.

    ``anime_title`` verilirse başlıktaki anime adı ayıklanır — "3x3 Eyes 1.
    Bölüm" gibi başlıklarda addaki rakamlar bölüm sanılmasın diye.
    """
    return _kayit_anahtari(entry, index, anime_title)[:2]


def merge_episodes(
    sources_data: Dict[str, Sequence[Dict[str, Any]]],
    anime_title: str = "",
) -> List[Dict[str, Any]]:
    """Kaynak → bölüm listesi sözlüğünü tek sıralı listeye indir.

    Farklı kaynaklar aynı bölümü farklı adlandırır:
        TürkAnime "Anime İsmi 1. Bölüm" · AnimeciX "1. Bölüm" · Anizle "Bölüm 1"
    Bu yüzden birleştirme anahtarı başlık değil, ``(sezon, bölüm)`` çiftidir.

    ``anime_title`` verilmesi şiddetle önerilir: adı bilmeden "86 2nd Season
    5. Bölüm" ile çıplak "5. Bölüm" farklı anahtarlara düşer ve aynı bölüm iki
    satır olur. Ad ayıklandığında sezon da anime kimliğinin parçası sayılır,
    iki kaynak tek satırda buluşur.

    Returns:
        ``[{"season": int, "number": int, "sub": int, "title": str,
            "sources": {kaynak_adı: kaynak_kaydı}}, ...]``
        (sezon, bölüm, ara bölüm) sırasında — sayısal sıralama, yani 2 < 10.
    """
    merged: Dict[Tuple[int, int, int], Dict[str, Any]] = {}

    for source_name, episodes in (sources_data or {}).items():
        if not episodes:
            continue
        for index, entry in enumerate(episodes):
            if not isinstance(entry, dict):
                continue
            # Ara bölüm anahtarın parçası: "5.5. Bölüm" 5. bölümün satırına
            # düşerse `setdefault` onu sessizce yutar, bölüm listeden silinir.
            key = _kayit_anahtari(entry, index, anime_title)
            season, number, sub = key
            row = merged.get(key)
            if row is None:
                row = {
                    'season': season,
                    'number': number,
                    'sub': sub,
                    'title': normalize_episode_title(
                        entry.get('title', '') or '', number, season, sub),
                    'sources': {},
                }
                merged[key] = row
            # `setdefault`: aynı kaynakta iki başlık aynı anahtara düşerse
            # (ör. "5. Bölüm" ve "5. Bölüm 2. Kısım") ilk kayıt korunur;
            # üzerine yazmak asıl bölümü listeden silmek olurdu.
            row['sources'].setdefault(source_name, entry)

    return sorted(merged.values(),
                  key=lambda row: (row['season'], row['number'], row['sub']))


__all__ = [
    "EpisodeInfo", "parse_episode", "normalize_title", "sort_episodes",
    "extract_episode_info", "extract_episode_number", "normalize_episode_title",
    "episode_key", "merge_episodes",
]
