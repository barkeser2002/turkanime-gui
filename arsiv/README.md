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

`turkanime_api/sources/animedepo.py` arşivi şu sırayla arar:

1. `TURKANIME_ARSIV_DIZIN` ortam değişkeni veya `ayarlar.json` →
   `animedepo_dizin` (yerel bir klasör)
2. Kullanıcının indirdiği tam arşiv (Ayarlar → Çevrimdışı arşiv)
3. Depodan çalıştırılıyorsa bu klasör
4. Uzak aynalar: önce bu deponun GitHub kopyası, sonra GitLab

## Güncelleme

```bash
python -m turkanime_api.common.arsiv_senkron --hedef arsiv
```

Komut GitLab'daki son commit'i sığ olarak klonlar ve bu klasörü onunla
eşitler: silinenler silinir, `KAYNAK.json` yeniden yazılır.

Arşivin kendi uyarı metni için: [DISCLAIMER.md](DISCLAIMER.md).
