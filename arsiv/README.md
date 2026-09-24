# Çevrimdışı arşiv (AnimeDepo aynası)

turkanime.tv kapandı. Sitenin anime/bölüm/video kayıtlarının yaşayan tek
kopyası [AnimeDepo](https://gitlab.com/AnimeDepo/animedepo) adlı statik JSON
arşivi. Bu klasör o arşivin **birebir aynası**: GitLab deposu da bir gün
kaybolursa veri projeyle birlikte yaşamaya devam etsin diye depoya alındı.

Dosyalar değiştirilmeden kopyalandı. Hangi commit'ten çekildiği
[`KAYNAK.json`](KAYNAK.json) içinde yazıyor.

## Yapı

```
dizin.json                           {"last_update": <unix>, "index": {grup: {slug: {"title", "status", "last_change"}}}}
animeler/{slug}/info.json            anime künyesi (tür, özet, stüdyo, puan…)
animeler/{slug}/bolumler.json        [[bolum_slug, başlık], …]
animeler/{slug}/{bolum_slug}.json    [{player, fansub, url | mask, path, alive?}, …]
```

Bir video kaydında `url` varsa oynatıcı adresi doğrudan kullanılabilir
(ok.ru, sibnet, mail.ru, mp4upload…). Yalnızca `mask`/`path` taşıyan kayıtlar
turkanime.tv'nin kendi oynatıcısına bağlıydı. Site kapandığı için bunlar
artık çözülemiyor ve istemci onları atlıyor.

## Uygulama bu klasörü nasıl kullanıyor

`turkanime_api/sources/animedepo.py` arşivi şu sırayla arar; yerel bir klasör
ancak içinde okunabilir bir `dizin.json` varsa sayılır, yoksa sıradakine
geçilir:

1. `TURKANIME_ARSIV_DIZIN` ortam değişkeni, sonra `ayarlar.json` →
   `animedepo_dizin` (kullanıcının gösterdiği yerel klasör)
2. Kullanıcının indirdiği tam arşiv: `<veri kökü>/cevrimdisi_arsiv`
   (`tam_arsiv_indir()`; önce GitLab paketi, olmazsa bu deponun GitHub
   paketinden yalnızca `arsiv/`)
3. Depodan çalıştırılıyorsa bu klasör (`arsiv/`)
4. Uzak aynalar, sırayla: özel adres (`TURKANIME_ARSIV_URL` ortam değişkeni
   veya `ayarlar.json` → `animedepo_url`) → GitLab
   (`gitlab.com/AnimeDepo/animedepo`) → bu deponun GitHub kopyası
   (`raw.githubusercontent.com/barkeser2002/turkanime-gui/main/arsiv`)

Yerel okumada ağa hiç çıkılmaz. Uzaktan başarıyla gelen her dosya
`<veri kökü>/arsiv_onbellek/` altına aynı göreli yolla yazılır; bütün aynalar
düştüğünde oradan okunur (çevrimdışı).

"Veri kökü" `~/Turkanime`; uygulama bir git deposunun içinden çalıştırılıyorsa
depo kökü. Bu yüzden indirilenler `arsiv/` değil `cevrimdisi_arsiv/` adıyla
duruyor ve `.gitignore`'da — uygulama bu klasörün üstüne asla yazmaz
(yalnızca aşağıdaki bakımcı komutu günceller).

Video kayıtlarından yalnızca oynatılabilecekler verilir: `DEAD_` önekli
oynatıcılar, `alive: false` kayıtlar, yalnızca `mask`/`path` taşıyanlar ve
turkanime alan adına giden adresler atlanır. VK kayıtlarındaki
`https:https://href.li/?…` biçimi gerçek `vk.com` adresine çevrilir. Akışlar
oynatıcı önceliğine göre sıralanır (`turkanime_api/common/oynatici_onceligi.py`).

## Güncelleme

```bash
python -m turkanime_api.common.arsiv_senkron --hedef arsiv
# başka kaynak/dal: --kaynak <git adresi> --dal <dal>
```

Komut GitLab'daki son commit'i geçici bir klasöre sığ olarak klonlar, geçerli
bir arşiv olduğunu doğrular ve bu klasörü onunla eşitler: yeni ve değişen
dosyalar kopyalanır, kaynakta artık olmayanlar silinir. Bu klasörün kendi
`README.md`'si ve `KAYNAK.json`'ı korunur; `KAYNAK.json` hangi commit'ten
çekildiği bilgisiyle yeniden yazılır. Eklenen/değişen/silinen sayıları
ekrana basılır.

Arşivin kendi uyarı metni için: [DISCLAIMER.md](DISCLAIMER.md).
