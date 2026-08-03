# TurkAnime sunucu tarafı

İki bileşen aynı depoda, aynı Docker imajında:

| Bileşen | Dosya | Ne yapar |
|---|---|---|
| **API** | `app.py` | İstemcinin **canlı** sorguladığı Flask REST servisi |
| **Tarayıcı** | `crawler/` | Kaynakları **yavaşça** gezip AnimeDepo şemasında arşiv üretir (Faz 11) |

İkisi de kaynak kazıma işini `turkanime_api.sources.*` adaptörlerine devreder;
sunucuda ikinci bir kazıyıcı yoktur. (Vardı: `anizle_scraper.py`, Selenium'lu ve
yarım bir `sources/anizle.py` kopyasıydı; hiçbir yerden çağrılmıyordu, Faz 11'de
silindi.)

---

## Arka plan arşiv tarayıcısı

### Neden

İstemci bugün her aramada 7 kaynağa canlı gidiyor: yavaş, kırılgan, sitelere
yük. Tarayıcı bu işi sunucuya taşır ve birleşik bir arşiv üretir. Faz 12 arşivi
git'e yayınlayacak; istemciler statik ham URL'lerden tek istekle okuyacak.

### Çıktı şeması

`turkanime_api/sources/animedepo.py`'nin okuduğu şemanın **birebir** aynısı —
istemcide tek satır değişiklik gerekmez:

```
dizin.json                          {"index": {grup: {slug: {"title": ...}}}}
animeler/{slug}/info.json           {slug, title, aliases, sources, bolum_sayisi}
animeler/{slug}/bolumler.json       [[bolum_slug, başlık], ...]
animeler/{slug}/{bolum_slug}.json   [{url, player, fansub, alive}, ...]
```

Bölüm slug'ları `common/episode_parser.py` ile normalize edilir (`s01e05`),
anime slug'ları çapraz kaynak eşleştirmesinden doğar.

### Nezaket kuralları

| Kural | Varsayılan | Bayrak |
|---|---|---|
| Kaynak başına eşzamanlılık | **1** (kilitle zorlanır) | — |
| İstekler arası gecikme | 8 sn | `--gecikme` |
| Jitter (± dalgalanma) | %35 | `--jitter` |
| Üstel geri çekilme | 30 sn → 30 dk | — |
| Günlük istek tavanı | 400 / kaynak | `--gunluk-tavan` |
| Kayıt tazeleme aralığı | 6 saat → 7 gün | `--tazelik` |

Tam bir tarama bilerek **günlere yayılır**. Amaç arşiv, hız değil.

### Koşullu istek

Üç kademe, en ucuzdan pahalıya:

1. **Kuyruk seviyesi** — başarılı görev, tazelik süresi kadar ileriye ertelenir;
   süre dolmadan kuyruktan bile çıkmaz.
2. **ETag / Last-Modified** — ucu destekleyen adaptörler `KosulluSonuc` döndürür;
   304 gelirse veri hiç işlenmez.
3. **İçerik hash'i** — düz liste döndüren adaptörler için sha256 karşılaştırması.

İçerik değişmediyse ziyaret aralığı 2× büyür (7 güne kadar): bitmiş seriler
seyrek, süregelen sezonlar sık ziyaret alır.

### Devam edebilirlik

Durum SQLite'ta (`durum/tarayici.sqlite3`): kuyruk, koşullu istek defteri,
günlük sayaçlar, biriken bulgular. Süreç öldürülüp yeniden başlatılınca kaldığı
yerden devam eder; açılışta "işlemde" kalmış görevler kuyruğa iade edilir.

JSON değil SQLite: her görev tek işlemde kalıcılaşır (JSON'da tüm dosyayı
yeniden yazmak gerekirdi ve yazımın ortasında ölmek dosyayı çöpe çevirirdi),
`(kaynak, tür, anahtar)` indeksli aramadır ve stdlib'de gelir.

### Çalıştırma

```bash
# Ne yapılacağını göster — ağa hiç çıkmaz, diske hiç yazmaz
python -m turkanime_server.crawler --kuru-calistir

# Tek kaynakta çok küçük canlı deneme (geliştirme için)
python -m turkanime_server.crawler --kaynak anizle --limit 3

# Üretim: her tur kuyrukta hazır ne varsa işler ve çıkar
python -m turkanime_server.crawler --cikti /veri/arsiv --durum /veri/durum/tarayici.sqlite3
```

Bayraklar: `--kaynak` (tekrarlanabilir), `--limit` (bu koşudaki en fazla istek),
`--kuru-calistir`, `--cikti`, `--durum`, `--tohum`, `--gecikme`, `--jitter`,
`--gunluk-tavan`, `--tazelik`, `--esik`, `--arsiv-yazma`, `--log`.

Docker (ayrı profil — API ile birlikte ayağa kalkmaz):

```bash
docker compose --profile crawler run --rm turkanime-crawler
```

Sonsuz döngüde çalışmaz; bir sonraki turu cron/systemd başlatır. Bu bilinçli:
sürekli koşan bir tarayıcıda günlük tavanların anlamı kalmaz.

### Kaynaklar

`anizle`, `openani`, `tranimaci`, `tranime`, `animecix`. `animedepo` yok:
ürettiğimiz arşivin şeması zaten o, taramak kendi çıktımızı geri okumak olurdu.

Kaynaklarda "hepsini listele" ucu olmadığı için keşif, alfabe + rakam
tohumlarıyla (`a`..`z`, `0`..`9`) arama yapılarak ilerler; `--tohum` ile
değiştirilebilir.

### Faz 12 notu

Arşiv `sources/animedepo.py` ile bayt uyumlu olduğu için istemci tarafında
yapılacak iş, o modülün `BASE_URL`'ini yayınlanan depoya çevirmekten ibaret.

---

## API sunucusu

### Docker ile

```bash
docker compose up -d
docker compose logs -f
```

### Yerel

```bash
pip install -r requirements.txt
python app.py
```

### Çevre değişkenleri

- `PORT` — varsayılan **34665**
- `DEBUG` — `true` ise debug modu
- `DB_HOST` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` — MySQL bağlantısı

### Uçlar

Genel:
- `GET /health` — durum + yüklenen kaynaklar
- `GET /sources` — desteklenen kaynaklar
- `GET /search?q=&source=&fuzzy=` — çok dilli arama

Kaynak başına (`animecix`, `anizle`, `tranime`, `openani`, `tranimaci`):
- `GET /{source}/search?q=`
- `GET /{source}/episodes/{slug}`
- `GET /{source}/streams/{episode_slug}`

Veritabanı:
- `GET|POST /anime-matches`, `GET /anime-matches/search?q=`
- `POST /user/episode-status`, `GET /user/{user_id}/episode-status`

### Veri biçimleri

```json
// anime match
{"source": "TürkAnime", "anime_id": "12345", "anime_title": "Anime Adı"}

// episode status
{"user_id": "uuid", "episode_id": "TürkAnime_bolum", "watched": true, "downloaded": false}
```

---

## Testler

```bash
python -m pytest tests/test_crawler.py
```

Tarayıcı testleri sahte adaptörlerle koşar, ağa çıkmaz. Gerçek ağ gerektiren
testler `@pytest.mark.network` ile işaretlenir ve `--network` olmadan atlanır.
