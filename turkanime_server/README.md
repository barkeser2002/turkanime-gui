# TurkAnime sunucu tarafı

İki bileşen aynı depoda, aynı Docker imajında:

| Bileşen | Dosya | Ne yapar |
|---|---|---|
| **API** | `app.py` | İstemcinin **canlı** sorguladığı Flask REST servisi |
| **Tarayıcı** | `crawler/` | Kaynakları **yavaşça** gezip AnimeDepo şemasında arşiv üretir (Faz 11) |
| **Yayıncı** | `yayinci/` | Arşivi git deposuna sürekli işler; istemci ham URL'den okur (Faz 12) |

İkisi de kaynak kazıma işini `turkanime_api.sources.*` adaptörlerine devreder;
sunucuda ikinci bir kazıyıcı yoktur. (Vardı: `anizle_scraper.py`, Selenium'lu ve
yarım bir `sources/anizle.py` kopyasıydı; hiçbir yerden çağrılmıyordu, Faz 11'de
silindi.)

---

## Arka plan arşiv tarayıcısı

### Neden

İstemci bugün her aramada 7 kaynağa canlı gidiyor: yavaş, kırılgan, sitelere
yük. Tarayıcı bu işi sunucuya taşır ve birleşik bir arşiv üretir. Faz 12 bu
arşivi git'e yayınlar; istemciler statik ham URL'lerden tek istekle okur.

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

---

## Arşiv yayıncısı (Faz 12)

### Neden git

Arşiv statik JSON'dan ibaret; onu bir git deposuna itip istemcileri ham (raw)
URL'lere yöneltmek sunucu maliyetini sıfıra indirir, CDN'i bedavaya getirir ve
arşivi çevrimdışı klonlanabilir yapar. KebabLord'un `gitlab.com/AnimeDepo`
deposu da tam olarak böyle çalışıyor.

### Neden ayrı paket (`yayinci/`, `crawler/yayinci.py` değil)

Yayıncının yaptığı iş tarayıcınınkinden bağımsız: ne SQLite durumu, ne nezaket
bütçesi, ne kaynak adaptörü kullanır. "Bir dizin al, git'e koy" der ve dizini
kimin ürettiğiyle ilgilenmez. Bu ayrılık üç şey kazandırır: tarayıcı git
kurulmamış makinede de koşar, yayın ayrı zamanlanabilir (tarama saatler, yayın
saniyeler sürer) ve elle düzeltilmiş bir arşiv de yayınlanabilir.

**Arşiv dizininin kendisi git çalışma ağacıdır.** İkinci bir kopya tutmak on
binlerce küçük dosyayı diskte ikiye katlar ve iki ağaç arasında senkron hatası
riski doğurur. Tarayıcı atomik yazdığı için, yayın sırasında koşan bir tarama en
fazla "yarım tur"u commit'ler; kalanı bir sonraki tura kalır.

### Boş commit atılmaz

Fark git'in kendi `status`'undan okunur; sahnede bir şey yoksa commit
çağrılmaz. Faz 11'in yazıcısı içeriği değişmeyen dosyaya zaten dokunmadığı için
(`dizin.json`'ın `guncelleme` damgası dahil) iki mekanizma birbirini tamamlar:
yazıcı diski, yayıncı geçmişi temiz tutar.

Commit mesajı dosya değil **anime/bölüm** sayar:

```
arşiv: +12 anime, ~340 bölüm güncellendi

Dosya: 36 eklendi, 341 güncellendi
Anime: 12 yeni, 1 güncellendi
Bölüm: 340 güncellendi
Toplam: 5123 anime
```

### Depo şişmesi

| Önlem | Varsayılan | Neden |
|---|---|---|
| `git gc --auto` her turda, tam `gc` her N commit'te | N = 25 | On binlerce küçük dosya gevşek (loose) nesne patlaması demek; paketlenmeden klon boyutu dosya sayısıyla doğru orantılı büyür |
| Geçmiş budama (`--orphan` tazeleme + zorla itme) | 200 commit | Arşiv bir kod deposu değil, bir **ayna**: kimse `git bisect` yapmıyor, herkes son hâli okuyor. Eski sürümlerin klon boyutuna bedeli faydasından büyük |
| `.gitattributes` içinde `* -text` | her zaman | Geliştirme makinelerinde `core.autocrlf=true` yaygın; satır sonu dönüşümü tüm JSON'ları "değişmiş" gösterir ve her turda dev bir sahte commit üretir |
| Oynak alanın ayrı dosyada durması | şema gereği | En sık değişen veri (stream URL'leri) zaten bölüm başına ayrı dosyada; `dizin.json` ancak indeks gerçekten değişince tazelenir |

Budama ve tam `gc` `--azami-gecmis 0` / `--bakim-araligi 0` ile kapatılabilir.

### Güvenlik freni

`dizin.json` yoksa yayın hiç başlamaz; takip edilen dosyaların yarısından
fazlası silinecekse commit atılmaz. Yanlış bağlanmış boş bir birim (volume) ya
da tarayıcının yarım çıktısı, aksi hâlde tüm istemcilerin okuduğu arşivi tek
commit'te boşaltırdı.

### Kimlik ve gizlilik

Sırlar **yalnızca ortam değişkeninden** okunur; `--token` diye bir bayrak
bilinçli olarak **yok** (argümanlar `ps` çıktısında ve shell geçmişinde görünür,
ortam değişkenleri görünmez). Örnek için `.env.example`, `.gitignore`'da olan
`.env`'e kopyalanır.

- Token argümana yazılmaz: `git push https://oauth2:TOKEN@host/...` çalışır ama
  `ps` ve CI log'larında görünür. Onun yerine token'ı *ortamdan okuyan* bir
  kimlik yardımcısı geçilir; yardımcı metninde sırrın kendisi değil, adı vardır.
- Token `.git/config`'e yazılmaz: arşiv dizini elden ele geçse bile içinden
  token çıkmaz.
- Dışarı verilen her git hata metni `gizle()`den geçer (sır → `***`, URL'ye
  gömülü kimlik → `scheme://***@host`).
- SSH tercih edilirse `TURKANIME_ARSIV_SSH_ANAHTAR` yeter; `GIT_SSH_COMMAND`
  `IdentitiesOnly=yes` ile kurulur.

| Değişken | Ne |
|---|---|
| `TURKANIME_ARSIV_UZAK` | Hedef depo adresi (kimlik gömmeyin) |
| `TURKANIME_ARSIV_DAL` | Hedef dal (varsayılan `master`) |
| `TURKANIME_ARSIV_TOKEN` / `_KULLANICI` | HTTPS deploy token |
| `TURKANIME_ARSIV_SSH_ANAHTAR` | SSH özel anahtar yolu |
| `TURKANIME_ARSIV_AD` / `_EPOSTA` | Commit kimliği |
| `TURKANIME_ARSIV_AZAMI_GECMIS` / `_BAKIM_ARALIGI` | Depo şişmesi eşikleri |
| `TURKANIME_ARSIV_URL` | **İstemci** tarafı: arşivin ham kök adresi |

### Çalıştırma

```bash
# Ne commit'lenecekti? Git'e de diske de dokunmaz (`git init` bile yapmaz)
python -m turkanime_server.yayinci --kuru-calistir

# Yerelde commit'le, uzağa itme (ilk kurulum denemesi)
python -m turkanime_server.yayinci --arsiv /veri/arsiv --itme-yok

# Üretim: kimlik ortamdan gelir
python -m turkanime_server.yayinci --arsiv /veri/arsiv
```

Çıkış kodları: `0` yayınlandı / değişiklik yok / kuru koşu, `1` güvenlik freni,
`2` git hatası, `3` git kurulu değil.

### Zamanlama — "tara + yayınla"

Tek tur = tarayıcı kuyrukta hazır olanı işler, sonra yayıncı sonucu iter. İkisi
de sonsuz döngüde koşmaz; turu zamanlayıcı başlatır (bkz. Faz 11 nezaket notu).

Docker Compose:

```bash
docker compose --profile crawler run --rm turkanime-crawler \
  && docker compose --profile yayin run --rm turkanime-yayinci
```

systemd timer (`/etc/systemd/system/turkanime-arsiv.service` + `.timer`):

```ini
[Service]
Type=oneshot
WorkingDirectory=/opt/turkanime/turkanime_server
EnvironmentFile=/opt/turkanime/turkanime_server/.env
ExecStart=/usr/bin/docker compose --profile crawler run --rm turkanime-crawler
ExecStart=/usr/bin/docker compose --profile yayin  run --rm turkanime-yayinci

[Timer]
OnCalendar=*:0/30
Persistent=true
```

`Persistent=true` önemli: makine kapalıyken kaçan tur açılışta telafi edilir.
Cron karşılığı (`crontab -e`):

```cron
*/30 * * * * cd /opt/turkanime/turkanime_server && docker compose --profile crawler run --rm turkanime-crawler && docker compose --profile yayin run --rm turkanime-yayinci
```

Yarım saatlik tur agresif değil: nezaket tavanları zaten kaynak başına 400
istek/gün; turların çoğu "yapacak taze iş yok" deyip saniyeler içinde çıkar ve
yayıncı da commit atmaz.

### İstemci tarafı

`turkanime_api/sources/animedepo.py` artık adresi şu sırayla çözer:

1. `TURKANIME_ARSIV_URL` ortam değişkeni
2. `ayarlar.json` içindeki `animedepo_url`
3. `BASE_URL` — KebabLord arşivi

Hiçbiri yoksa davranış **birebir eskisi gibidir**. Ayar dosyası salt okunur
açılır (`Dosyalar()` örneklenmez: yapıcısı dosya yaratıp `user_id` üretiyor, bir
URL okumak için kullanıcının ayarlarına yazmak yanlış olurdu).

`dizin(tazele=True)` cache'i `If-None-Match` ile doğrular: sunucu 304 döndürürse
(arşiv commit'i değişmemişse) megabaytlık dizin yeniden indirilmez. ETag'ler ilk
indirmede not edilir; bilinen ETag yoksa düz indirmeye düşülür.

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
python -m pytest tests/test_crawler.py tests/test_publisher.py
```

Tarayıcı testleri sahte adaptörlerle koşar, ağa çıkmaz. Yayıncı testlerindeki
"uzak", `tmp_path` içinde `git init --bare` ile kurulan yerel bir depodur;
gerçek bir uzağa tek istek bile gitmez. Gerçek ağ gerektiren testler
`@pytest.mark.network` ile işaretlenir ve `--network` olmadan atlanır.
