# Arşiv sunucusu ayrı depoya taşındı

Bu klasörün belgeleri artık burada tutulmuyor.

**→ [github.com/barkeser2002/turkanime-server](https://github.com/barkeser2002/turkanime-server)** *(private)*

---

## Neden ayrıldı

Sunucu tarafı üç parçadan oluşuyor ve hiçbiri masaüstü uygulamasının çalışması
için gerekli değil:

| Parça | Ne yapar |
|-------|----------|
| `app.py` | Flask API — istemcinin veri çektiği uç |
| `crawler/` | Kaynakları yavaş ve nazik gezip birleşik arşiv üretir |
| `yayinci/` | Üretilen arşivi bir git deposuna sürekli işler (isteğe bağlı) |

Ayrılma gerekçeleri:

- **Gizlilik.** Sunucu yapılandırması, veritabanı bağlantısı ve dağıtım
  ayrıntıları herkese açık bir depoda durmamalı.
- **Bağımsız yaşam döngüsü.** Sunucu, istemciden ayrı sürümleniyor ve ayrı
  dağıtılıyor; ikisini aynı etikete bağlamak ikisini de yavaşlatıyordu.
- **İmaj boyutu.** Konteyner masaüstü bağımlılıklarını (PySide6/QtWebEngine,
  yt-dlp) taşımak zorunda değil.

## İstemciyle ilişkisi

Sunucu, **bu depodaki kaynak adaptörlerini yeniden kullanıyor** —
`turkanime_api.sources.*` ve `turkanime_api.common.*`. İkinci bir kazıyıcı
yazılmadı; kaynak siteler değiştiğinde düzeltme tek yerde, burada yapılıyor.

Sunucu deposu istemciyi bir git bağımlılığı olarak kuruyor:

```bash
pip install --no-deps git+https://github.com/barkeser2002/turkanime-gui.git@main
```

`--no-deps` bilinçli: istemci PySide6/QtWebEngine ve yt-dlp beyan ediyor,
sunucunun hiçbirine ihtiyacı yok.

## Bu klasördeki kod

`turkanime_server/` altındaki Python dosyaları geçiş tamamlanana kadar burada
duruyor ve bu depodaki testler tarafından hâlâ kapsanıyor. **Değişiklikler
artık ayrı depoda yapılmalı**; buradaki kopya bir sonraki sürümde kaldırılacak.

## Sorun bildirimi

Sunucuyla ilgili hatalar için bu deponun Issues sekmesini değil,
[turkanime-server](https://github.com/barkeser2002/turkanime-server) deposunu
kullanın. Depo private olduğu için erişiminiz yoksa
[info@bariskeser.com](mailto:info@bariskeser.com) adresine yazın.
