"""Video oynatıcılarının (player) öncelik sırası — tek kaynak.

Bu liste eskiden yalnızca `objects.SUPPORTED` olarak yaşıyordu. AnimeDepo
arşivindeki bölüm kayıtları da aynı oynatıcı adlarını taşıyor (turkanime.tv'nin
kendi etiketleri) ve istemci onları aynı sırayla denemeli. Ama
`sources.animedepo` `objects`'i import EDEMEZ: `objects` yt_dlp'yi (71 modül)
yüklüyor ve `tests/test_sunucu_bagimliliklari.py` kaynak modüllerinin bu yığını
sürüklemediğini koruyor. Liste bu yüzden bağımlılıksız bir modüle taşındı;
`objects.SUPPORTED` buradan türetiliyor, iki kopya birbirinden kayamıyor.
"""
from __future__ import annotations

from typing import Dict, Tuple

# Çalıştığı bilinen oynatıcılar, en güvenilirden başlayarak.
# (KebabLord upstream V9.2.2/V10: MP4UPLOAD false-positive'ler yüzünden çıkarıldı,
#  ALUCARD/GDRIVE güvenilirlikleri nedeniyle öne alındı.)
DESTEKLENEN_OYNATICILAR: Tuple[str, ...] = (
    "YADISK",
    "ALUCARD(BETA)",
    "GDRIVE",
    "MAIL",
    "PIXELDRAIN",
    "AMATERASU(BETA)",
    "HDVID",
    "ODNOKLASSNIKI",
    "DAILYMOTION",
    "SIBNET",
    "VK",
    "VIDMOLY",
    "SENDVID",
    "MYVI",
)

# Denenebilir ama en sona bırakılanlar — bilinen false-positive ya da ölü:
# - MP4UPLOAD: upstream'in listeden çıkarma sebebi; yt-dlp "çalışıyor" deyip
#   oynatılamayan bir şey döndürüyordu.
# - CLONE: adresler short.icu kısaltıcısına gidiyor; video barındırıcısı değil,
#   yönlendirmenin sonunda ne olduğu belirsiz.
# - STREAMSB: servis kapandı.
# - YOURUPLOAD, UQLOAD: yt-dlp bu konakları "KnownPiracy" listesinde tutuyor
#   ve baştan reddediyor (yt-dlp 2026.08.19); mpv de yt-dlp'den geçtiği için
#   oynamıyorlar. Eskiden "desteklenenler"deydiler ve ilk 8 adayın yerini
#   yiyorlardı. Adresle yakalama `sources.adapter.ytdlp_reddeder`'de; ad
#   burada, çünkü uqload.io/.to gibi aynalar o düzenli ifadeye takılmıyor.
# Tamamen atılmıyorlar: bazı bölümlerin tek kaydı bunlar; hiç yoktan iyidir.
# Önde durmamaları yeter: `best_video` yalnızca ilk birkaç adayı yokluyor.
SONA_BIRAKILANLAR: Tuple[str, ...] = ("MP4UPLOAD", "CLONE", "STREAMSB",
                                      "YOURUPLOAD", "UQLOAD")

# Arşivde aynı servisin farklı adla geçtiği kayıtlar. Adres konaklarına bakılarak
# eşlendi (arsiv/ taraması): "OK.RU" kayıtları ok.ru/odnoklassniki.ru'ya,
# "XDVID" kayıtları hdvid.tv'ye gidiyor. Ad yalnızca SIRALAMA için eşleniyor;
# akışın taşıdığı "player" değeri değiştirilmiyor (kullanıcı arşivdekini görür).
TAKMA_ADLAR: Dict[str, str] = {
    "OK.RU": "ODNOKLASSNIKI",
    "XDVID": "HDVID",
}

_SIRA = {ad: i for i, ad in enumerate(DESTEKLENEN_OYNATICILAR)}
_SON_SIRA = {ad: i for i, ad in enumerate(SONA_BIRAKILANLAR)}


def oncelik_anahtari(oynatici: str) -> Tuple[int, int]:
    """`sorted(..., key=...)` için oynatıcı önceliği; küçük olan önce denenir.

    Üç kuşak: desteklenenler kendi sıralarıyla → bilinmeyenler → sona
    bırakılanlar. Bilinmeyenlerin hepsi aynı anahtarı alır; Python'un sıralaması
    kararlı olduğu için onlar arasında arşivdeki sıra korunur.
    """
    ad = (oynatici or "").strip().upper()
    ad = TAKMA_ADLAR.get(ad, ad)
    if ad in _SIRA:
        return (0, _SIRA[ad])
    if ad in _SON_SIRA:
        return (2, _SON_SIRA[ad])
    return (1, 0)


__all__ = [
    "DESTEKLENEN_OYNATICILAR",
    "SONA_BIRAKILANLAR",
    "TAKMA_ADLAR",
    "oncelik_anahtari",
]
