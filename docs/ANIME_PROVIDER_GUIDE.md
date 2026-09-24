# TürkAnime GUI — Anime Sağlayıcı Ekleme Rehberi

Bu rehber, TürkAnime GUI'ye yeni bir anime kaynağı eklemeyi anlatır.

> **Bu rehber kaynak kaydıyla (`turkanime_api/sources/kayit.py`) yeniden
> yazıldı.** Kaynak listesi eskiden altı yerde elle tutuluyordu —
> `SearchEngine.adapters`, `sources_bridge.py`'deki `FUNCTION_SOURCES` /
> `BUILDERS`, CLI'daki `SOURCE_TITLES`, bölüm sayfasının rozet renkleri,
> `sources/__init__.py`'deki `PROVIDERS` ve sunucu tarayıcısının tablosu. Birini
> unutan kaynak yarım kalıyordu: aramada görünüp bölümleri açılmıyor ya da CLI
> menüsünde hiç çıkmıyordu. **Artık hepsi kayıttan türetiliyor;** yukarıdaki
> listelerin hiçbirine elle dokunma.

## Gereksinimler

- **Python 3.9+** (`pyproject.toml`: `>=3.9,<4`; test edilen: 3.9 – 3.13)
- `requests`, `curl_cffi`, `beautifulsoup4` — hepsi `requirements.txt`'te

## Mimariye kısa bakış

Bir kaynak iki parçadan oluşur:

| Ne | Nerede | Ne yapar |
|----|--------|----------|
| **Kaynak modülü** | `turkanime_api/sources/<modul>.py` | Siteyle konuşan üç uç: arama, bölümler, akışlar |
| **Kayıt satırı** | `turkanime_api/sources/kayit.py` → `KAYNAKLAR` | Kaynağın adı, etiketi, rozeti, CLI kodu, bayrakları ve uçlarının tembel yükleyicisi |

Kayıttan türetilenler (hiçbirine elle ekleme yapılmaz):

| Türetilen | Dosya |
|-----------|-------|
| Paralel arama (`SearchEngine.adapters`) | `turkanime_api/common/adapters.py` |
| Bölüm + oynatma/indirme (`FUNCTION_SOURCES`, `BUILDERS`, `METADATA_ONLY`) | `turkanime_api/gui/qt/sources_bridge.py` |
| Bölüm satırı rozeti (`SOURCE_COLORS`, `SOURCE_SHORT`) | `turkanime_api/gui/qt/pages/episodes.py` |
| CLI "Kaynak seç" menüsü, `SOURCE_TITLES` | `turkanime_api/cli/__main__.py` |
| `PROVIDERS` | `turkanime_api/sources/__init__.py` |
| Sunucu tarayıcısının tablosu (`taranabilir=True` olanlar) | `turkanime_server/crawler/kaynaklar.py` |

Bölüm nesneleri (`AdapterBolum`) her kaynak için aynı yoldan kurulur:
`sources/adapter.py::kayittan_bolumler`. Oynatma/indirme boru hattı (yt-dlp +
mpv) kaynaktan bağımsızdır.

> **TürkAnime = arşiv.** turkanime.tv kapandı. "TürkAnime" kaynağı artık
> sitenin statik JSON arşivi (`sources/animedepo.py`, depoda `arsiv/`) ve
> ağsız çalışır. Aynı arşiv bir süre "AnimeDepo" adıyla ayrıca listelendi; iki
> kez görünmesin diye tek kayıtta birleşti, "AnimeDepo" artık **takma ad**
> (eski eşleşmeler ve ayarlar okunmaya devam ediyor). `objects.Anime` /
> `bypass.fetch` kapanan siteye gider — yeni kodda kullanma; adaptörün
> `Anime`/`Bolum` nesnesi üretmesi gerekiyorsa `Anime.cevrimdisi(...)` /
> `Bolum.cevrimdisi(...)`.

> `sources/adapter_template.py` dosyasını kopyalama. Sınıf tabanlı eski bir
> şablon; içindeki çıplak `except:` bloğu kopyalayan her yeni kaynağa taşınır.
> Örnek olarak gerçekten kullanılan bir kaynağı okuyun.

---

## Adımlar

### 1. Kaynak modülünü yaz

`turkanime_api/sources/my_provider.py` oluştur. Üç uç yeter:

```python
"""My Provider kaynağı."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..common.cf_bypass import get_cf_session

BASE_URL = "https://myprovider.com"
ZAMAN_ASIMI = 15


def search_my_provider(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    """(slug, başlık) ikilileri döndür."""
    oturum = get_cf_session()
    yanit = oturum.get(f"{BASE_URL}/search?q={query}", timeout=ZAMAN_ASIMI)
    if yanit.status_code != 200:
        return []
    # ... ayrıştır ...
    return [("anime-slug", "Anime Adı")][:limit]


def get_anime_episodes(slug: str) -> List[Tuple[str, str]]:
    """(bölüm_id, bölüm_başlığı) ikilileri döndür."""
    return [(f"{slug}/1", "1. Bölüm")]


def get_episode_streams(episode_id: str) -> List[Dict[str, str]]:
    """Oynatılabilir uçlar."""
    return [{
        "url": "https://cdn.example/video.mp4",
        "label": "1080p",
        "type": "direct",          # ya da "hls"
        "referer": BASE_URL + "/", # CDN referer istiyorsa ŞART
        "fansub": "Grup Adı",      # varsa: kullanıcı fansub seçebilir
    }]
```

**Dikkat edilecekler:**

- **Arama `limit` anahtar argümanını kabul etmeli** (`ara(sorgu, limit=...)`
  diye çağrılır). Sitenin ucu limit almıyorsa kayıttaki yükleyicide sarmala
  (bkz. `kayit.py::_animecix`).
- **`get_cf_session()` kullan.** Kendi `requests.Session`'ını kurma; CF zinciri
  (curl_cffi → cloudscraper → FlareSolverr → QtWebEngine → requests) bu oturumun
  içinde.
- **`timeout` geç.** `CFSession.get(url, timeout=15)` destekleniyor; vermezsen
  oturumun varsayılanı kullanılır.
- **Engeli sessizce yutma.** HTTP 403/429/503 veya gövdede "Just a moment" gibi
  bir iz varsa bu bir *engellenme*dir, boş sonuç değil. `cf_bypass` içindeki
  `ENGEL_DURUMLARI` ve `CHALLENGE_MARKERS` sabitlerini kullan — kendi listeni
  tutma, iki liste ayrıştığında hata sinsi oluyor.
- **`referer` alanını doldur.** Birçok CDN kendi sitesi dışından gelen isteğe
  403 döner.
- **Akışları iyiden kötüye sırala.** `best_video` yalnızca ilk birkaç adayı
  yokluyor.

### 2. Kayda tek satır ekle

`turkanime_api/sources/kayit.py` içinde tembel bir yükleyici yaz ve
`KAYNAKLAR` demetine bir `Kaynak(...)` ekle:

```python
def _my_provider() -> KaynakUclari:
    from .my_provider import (
        get_anime_episodes, get_episode_streams, search_my_provider,
    )
    return KaynakUclari(search_my_provider, get_anime_episodes, get_episode_streams)
```

```python
KAYNAKLAR: Tuple[Kaynak, ...] = (
    ...
    Kaynak("My Provider", "My Provider", "MP", "#16a085", "MYPROVIDER",
           _my_provider, modul="my_provider", cli_kodu="myprovider",
           bolum_adresi=lambda ep: f"https://myprovider.com/izle/{ep}"),
)
```

Alanlar:

| Alan | Anlamı |
|------|--------|
| `ad` | Kanonik anahtar: arama sonucu sözlüğü, köprü ve API'ye kaydedilen eşleşme bu adı kullanır. Sonradan **değiştirme**; değiştirmen gerekirse eskisini `takma_adlar`'a yaz. |
| `etiket` | İnsana gösterilen ad (arama kartı, detay sayfasının kaynak kutusu, CLI menüsü) |
| `kisaltma`, `renk` | Bölüm satırındaki iki harfli rozet ve rengi |
| `oynatici` | `AdapterBolum` ilerleme etiketi ve tarayıcının arşive yazdığı `player` |
| `yukleyici` | Uçları döndüren tembel fonksiyon |
| `modul` | `sources` altındaki modül adı; `PROVIDERS` ve sunucu tarayıcısı bu adla anahtarlar |
| `cli_kodu` | `ayarlar.json` → `"kaynak"` değeri; `None` ise CLI menüsünde yok |
| `takma_adlar` | Eski adlar; okunurken bu kayda düşer, hiçbir listede görünmez |
| `bolum_adresi` | Bölüm kimliği → `AdapterBolum.url` (varsayılan: kimliğin kendisi) |
| `bolum_slugu` | Bölüm kimliği → geçmiş/dosya adı slug'ı (varsayılan: başlıktan üretilir) |
| `kimlik_hatasi` | Kaynak kimliği bu kaynakta açılamıyorsa kullanıcıya gösterilecek mesaj (AnimeciX: sayısal olmalı) |
| `yalnizca_metadata` | Aramaya katılır, video sunmaz (AniList) |
| `cerez_gerekir` | Oturum çerezi olmadan sonuç vermiyor (TRAnimeİzle) |
| `taranabilir` | Sunucu tarayıcısı gezsin mi |
| `hazirlik` | CLI açılışında bir kez çağrılır (TürkAnime: arşiv dizinini yükler); kaynak kullanılamıyorsa hata fırlatır, CLI uyarır ama menüyü yine açar |
| `deneysel` | CLI menüsünde "(deneysel)" notu |

Adların hepsi (kanonik, etiket, CLI kodu, modül, takma adlar) büyük/küçük harf,
aksan ve parantez içi eklerden bağımsız çözülür; iki kaynak aynı adı iddia
ederse modül import anında `ValueError` verir.

> Yükleyici neden fonksiyon içinde import ediyor? Kayıt modülü sunucu
> tarayıcısı tarafından da okunuyor ve imajında yt-dlp yok; modül düzeyinde
> import, arayüz açılışında da bütün kaynakları (ve bağımlılıklarını)
> yüklerdi. Aynı sebeple `kayit.py`'den `sources.adapter` import **edilmez**.

> Sonuçlar `common/title_match.siralama_skoru` ile alakaya göre sıralanır;
> kaynağın kendi sırası eşit skorda korunur. Ek bir şey yapman gerekmiyor.

### 3. Sunucu tarafı (isteğe bağlı)

Kaynağın arşiv tarayıcısında da gezilmesini istiyorsan kayıtta
`taranabilir=True` yeter; `turkanime_server/crawler/kaynaklar.py` tablosunu
kayıttan türetiyor. Tarayıcı bu depodaki adaptörleri yeniden kullanır; ikinci
bir kazıyıcı yazılmaz.

### 4. Test et

Önce hızlı bir elle deneme:

```bash
python -c "from turkanime_api.sources.my_provider import search_my_provider; print(search_my_provider('one piece')[:5])"
```

Sonra otomatik testler (ağa çıkmaz):

```bash
python -m pytest tests/
```

`tests/test_kaynak_kaydi.py` kayıttaki **her** kaynağın aramada göründüğünü,
köprüde bölümlerinin açıldığını ve CLI menüsünde seçilebildiğini sahte uçlarla
denetler — yeni kaynak için ayrıca bir şey yazmana gerek yok.

Ağa çıkan adaptör betiğine de eklemen önerilir:

```bash
python tests/adapters-test-all.py --source my_provider
```

> Betik şu an yalnızca `animecix`, `anizle`, `tranime`, `animedepo` kaynaklarını
> tanıyor. Yeni kaynağı eklemek isteyenler `tests/adapters-test-all.py`
> içindeki takım tablosunu genişletmeli.

---

## İpuçları

1. **Hız sınırına uy.** Kaynak başına ardışık istekler arasına gecikme koy;
   sabit ritim bot imzasıdır, araya jitter ekle.
2. **Hatayı sınıflandır.** Geçici (ağ), kalıcı (404) ve engellenme (403/429/503
   veya challenge sayfası) farklı davranış ister. Hepsini `except: return []`
   ile yutmak, kullanıcıya "kaynak çalışmıyor" demekten başka bir şey bırakmaz.
3. **Gerçekçi User-Agent** kullan; `cf_bypass.USER_AGENTS` listesi hazır.
4. **Zaman aşımı ver.** Timeout'suz `urlopen`/`get` çağrısı soket varsayılanına
   düşer ve süresiz asılabilir.
5. **Farklı formatları destekle** — `mp4` (`"type": "direct"`) ve `m3u8`
   (`"type": "hls"`).
6. **Ölü ucu sessizce döndürme.** Uç 404 veriyorsa kullanıcıya sebebini söyle;
   `sources/openani.py` içindeki `_uc_calisiyor()` bu deseni gösteriyor.
7. **Sabit kodlanmış CDN'den kaçın.** Site düğüm değiştirdiğinde kod
   değişikliği gerekmesin; adresi sayfadan çıkar ya da ortam değişkeniyle
   geçersiz kılınabilir yap (`sources/animedepo.py::ORTAM_ANAHTARI` örneği).

## Örnek olarak okunacak kaynaklar

| Dosya | Neden iyi örnek |
|-------|-----------------|
| `turkanime_api/sources/kayit.py` | Kaynak kaydı; her kaynağın yükleyicisi ve bayrakları |
| `turkanime_api/sources/animedepo.py` | TürkAnime arşivi: yerel-önce statik arşiv okuma, ağsız arama |
| `turkanime_api/sources/openani.py` | HTML'den JSON çıkarımı, uç doğrulama, teşhis mesajı |
| `turkanime_api/sources/tranimaci.py` | Proof-of-work WAF ve JS kapısını aşma |
| `turkanime_api/sources/anizle.py` | Çok kademeli CF bypass kullanımı |

## Destek

Sorular ve öneriler için
[GitHub Issues](https://github.com/barkeser2002/turkanime-gui/issues).

> Bu depo **CC BY-NC-ND 4.0** ile lisanslıdır; katkı göndermeden önce
> [LICENSE](../LICENSE) dosyasını okuyun.
