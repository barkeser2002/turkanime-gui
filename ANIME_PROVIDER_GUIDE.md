# TürkAnime GUI — Anime Sağlayıcı Ekleme Rehberi

Bu rehber, TürkAnime GUI'ye yeni bir anime kaynağı eklemeyi anlatır.

> **Bu rehber 10.0.0 ile yeniden yazıldı.** Önceki sürümü `sources/__init__.py`
> içindeki `PROVIDERS` sözlüğüne ve `register_provider()` fonksiyonuna
> yönlendiriyordu. **O yol artık kullanılmıyor:** `register_provider` /
> `get_enabled_providers` üretim kodunda hiç çağrılmıyor ve `PROVIDERS`'ın
> girdilerinin çoğu zaten `"adapter": None` diyor. Rehberi harfiyen uygulayan
> biri, kaynağını çalışır sanıp arayüzde `UnsupportedSource` hatası alıyordu.
> Aşağıdaki adımlar **gerçek** kayıt noktalarını kullanır.

## Gereksinimler

- **Python 3.9+** (`pyproject.toml`: `>=3.9,<4`; test edilen: 3.9 – 3.13)
- `requests`, `curl_cffi`, `beautifulsoup4` — hepsi `requirements.txt`'te

## Mimariye kısa bakış

Bir kaynağın uygulamaya bağlanması **iki ayrı yerde** olur:

| Ne | Nerede | Ne yapar |
|----|--------|----------|
| **Arama** | `turkanime_api/common/adapters.py` → `SearchEngine.adapters` | Kaynağı paralel aramaya dâhil eder |
| **Bölüm + stream** | `turkanime_api/gui/qt/sources_bridge.py` | Arayüzün bölüm listesi ve oynatma/indirme yolu |

İkisini de yapmazsan kaynak yarım kalır: aramada görünür ama bölümlerine
tıklayınca `UnsupportedSource` alırsın.

### İki entegrasyon biçimi

`sources_bridge.py` iki stil tanıyor:

- **Fonksiyon stili** (`FUNCTION_SOURCES`) — modül üç fonksiyon dışa verir:
  arama, bölümler, stream'ler. **Yeni kaynaklar için önerilen budur.**
  Örnekler: `sources/openani.py`, `sources/tranimaci.py`, `sources/animedepo.py`
- **Builder stili** (`BUILDERS`) — kaynağa özgü bir kurucu fonksiyon, nesne
  döndürür. Eski kaynaklar böyle: TürkAnime, TRAnimeİzle, AnimeciX, Anizle.

Ayrıca `METADATA_ONLY = {"AniList"}` var: aramada yer alır ama video sunmaz.

> `sources/adapter_template.py` dosyasına **dokunmayın.** 527 satır ve hiçbir
> yerden import edilmiyor; içindeki çıplak `except:` bloğu kopyalayan her yeni
> kaynağa taşınır. Örnek olarak gerçekten kullanılan bir kaynağı okuyun.

---

## Adımlar

### 1. Kaynak modülünü yaz

`turkanime_api/sources/my_provider.py` oluştur. Fonksiyon stili için üç uç yeter:

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
    }]
```

**Dikkat edilecekler:**

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
  403 döner. Alan boşsa istemci `turkanime.co`'yu varsayar ve stream kırılır.

### 2. Aramaya kaydet

`turkanime_api/common/adapters.py` içinde bir adaptör sınıfı yaz ve
`SearchEngine.adapters` sözlüğüne ekle:

```python
class MyProviderAdapter:
    """My Provider arama adaptörü."""

    def search_anime(self, query: str, limit: int = 10):
        from ..sources.my_provider import search_my_provider
        return search_my_provider(query, limit=limit)
```

```python
self.adapters = {
    "AniList": AniListAdapter(),
    "TürkAnime": TurkAnimeAdapter(),
    "AnimeciX": AnimeciXAdapter(),
    "Anizle": AnizleAdapter(),
    "TRAnimeİzle": TRAnimeAdapter(),
    "AnimeDepo": AnimeDepoAdapter(),
    "OpenAnime": OpenAnimeAdapter(),
    "Tranimaci": TranimaciAdapter(),
    "My Provider": MyProviderAdapter(),      # ← yeni
}
```

Sözlükteki **anahtar** arayüzde görünen addır; sonraki adımda da aynı anahtarı
kullanacaksın. İkisi tutmazsa kaynak aramada çıkar, bölümleri gelmez.

> Sonuçlar `common/title_match.siralama_skoru` ile alakaya göre sıralanır;
> kaynağın kendi sırası eşit skorda korunur. Ek bir şey yapman gerekmiyor.

### 3. Bölüm ve stream'e kaydet

`turkanime_api/gui/qt/sources_bridge.py` içinde tembel bir yükleyici ekle ve
`FUNCTION_SOURCES`'a kaydet:

```python
def _my_provider():
    from ...sources.my_provider import (
        get_anime_episodes as episodes, get_episode_streams as streams,
    )
    return episodes, streams
```

```python
FUNCTION_SOURCES = {
    "OpenAnime": {...},
    "Tranimaci": {...},
    "My Provider": {                          # ← Adım 2'deki anahtarın AYNISI
        "loader": _my_provider,
        "player": "MYPROVIDER",
        "ep_url": lambda ep: f"https://myprovider.com/izle/{ep}",
    },
}
```

> Yükleyici neden fonksiyon içinde import ediyor? Modül düzeyinde import,
> arayüz açılışında bütün kaynakları (ve bağımlılıklarını) yüklerdi. Tembel
> yükleme, kaynak gerçekten kullanılana kadar bedeli ödemiyor.

### 4. Sunucu tarafı (isteğe bağlı)

Arşiv sunucusu ayrı ve **private** bir depoda:
[turkanime-server](https://github.com/barkeser2002/turkanime-server). Kaynağın
arşiv tarayıcısında da gezilmesini istiyorsan oradaki `crawler/kaynaklar.py`
tablosuna eklenmesi gerekir. Tarayıcı bu depodaki adaptörleri yeniden kullanır;
ikinci bir kazıyıcı yazılmaz.

### 5. Test et

Önce hızlı bir elle deneme:

```bash
python -c "from turkanime_api.sources.my_provider import search_my_provider; print(search_my_provider('one piece')[:5])"
```

Sonra otomatik testler (ağa çıkmaz):

```bash
python -m pytest tests/
```

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
| `turkanime_api/sources/animedepo.py` | En sade fonksiyon stili; statik arşiv okuma |
| `turkanime_api/sources/openani.py` | HTML'den JSON çıkarımı, uç doğrulama, teşhis mesajı |
| `turkanime_api/sources/tranimaci.py` | Proof-of-work WAF ve JS kapısını aşma |
| `turkanime_api/sources/anizle.py` | Çok kademeli CF bypass kullanımı |

## Destek

Sorular ve öneriler için
[GitHub Issues](https://github.com/barkeser2002/turkanime-gui/issues).

> Bu depo **CC BY-NC-ND 4.0** ile lisanslıdır; katkı göndermeden önce
> [LICENSE](LICENSE) dosyasını okuyun.
