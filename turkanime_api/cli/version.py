"""CLI sürüm bilgisi ve güncelleme denetimi.

Sürüm numarası burada TUTULMUYOR: `turkanime_api/version.py` tek kaynak.
Eskiden iki dosyada iki literal vardı ve biri güncellenip diğeri unutulunca
CLI kendini yanlış sürümle tanıtıyordu (release CI'ı ikisini ayrı ayrı
sed'lemek zorundaydı).

Güncelleme KANALI da artık burada tutulmuyor: `common/updater.py` tek kaynak.
Bu modül eskiden `__build__`e göre üç ayrı uca çıkıyordu ve üçü de yanlıştı:

- ``source``: ".../turkanime-gui/master/pyproject.toml". Depoda `master` diye
  bir dal yok — `git ls-remote` yalnızca `main`i görüyor. GitHub, yeniden
  adlandırılmış varsayılan dal için eski adı hâlâ yönlendirdiğinden istek
  bugün 200 dönüyor; yani ölçülen belirti "404" değildi. Ama sürüm bilgisini
  bir uyumluluk yönlendirmesine bağlamak ve `pyproject.toml`u ağdan regex'le
  ayrıştırmak, Qt tarafının okuduğu `docs/version.json`dan ayrışmak demekti.
- ``pip``: PyPI'da "turkanime-cli" sorgulanıyordu. Bu paketin adı
  "turkanime-gui" (pyproject.toml:2); "turkanime-cli" BAŞKA birinin paketi
  (upstream çatalı, yayıncısı "Junicchi") ve sürümü bizimkinden ileride —
  yani pip kurulumunda CLI her açılışta yabancı bir paketin sürümünü
  "güncelleme mevcut" diye gösterecekti.
- ``exe``: GitHub Releases ucu tek başına doğruydu ama Qt'nin kullandığı
  kanaldan farklıydı; iki arayüz aynı kurulumda farklı sürüm görebiliyordu.

`common/updater.py` zaten "iki arayüzde iki farklı güncelleme mantığı
oluşmasın" diye yazılmıştı; CLI o taşınmanın dışında kalmış son yerdi.
Denetimi silmek yerine oraya bağlamak seçildi: kullanıcıya yeni sürümü
duyurmanın CLI'da başka yolu yok ve `updater` tarafı testli.
"""
from ..common import updater
from ..version import __version__   # noqa: F401  (yeniden dışa veriliyor)

__author__ = "https://github.com/barkeser2002/turkanime-gui"
# Yalnızca bilgi amaçlı. Güncelleme ucu artık buna BAKMIYOR, dolayısıyla
# pip/exe paketlerken bu değeri elle düzenlemek gerekmiyor.
__build__ = "source"  # source,exe,pip

# Güncellemenin cinsi, ilk farklı sürüm bileşenine göre adlandırılır.
# 3'ten uzun sürümlerde (ör. "9.4.9.3") kuyruk farkı yamadır.
_CINSLER = ("Radikal", "Özellik", "Onarım")


def guncel_surum(timeout: int = updater.ZAMAN_ASIMI):
    """Yayındaki en güncel sürüm ("X.Y.Z"); bilinemiyorsa ``None``.

    Ağ/biçim hatasında `updater.surum_bilgisi_getir` istisna yükseltir; onu
    burada yutmuyoruz ki çağıran "kontrol edilemedi" ile "güncel" arasındaki
    farkı görebilsin.
    """
    veri = updater.surum_bilgisi_getir(timeout=timeout)
    surum = str((veri or {}).get("version") or "").strip()
    return surum or None


def update_type(surum):
    """`surum` yerelden yeniyse güncellemenin cinsi, değilse ``None``.

    `surum` ``None`` olabilir: `guncel_surum()` sürüm alanı eksik bir
    `version.json` için ``None`` döndürüyor ve eski kod bunu doğrudan
    ``surum.split(".")`` ediyordu — her açılışta AttributeError. Eski gövde
    ayrıca ``int()`` kullandığı için "10.1.0-beta" gibi etiketli sürümlerde de
    patlıyordu; ayrıştırma artık `updater.surum_parcala`ya bırakılıyor.
    """
    uzak = updater.surum_parcala(surum)
    yerel = updater.surum_parcala(__version__)
    if uzak is None or yerel is None:
        return None
    if not updater.yeni_mi(surum, __version__):
        return None
    # "9.5" ile "9.5.0" aynı sürüm; kısa olanı sıfırla doldur ki ilk fark
    # gerçek fark olsun.
    uzunluk = max(len(uzak), len(yerel))
    uzak += (0,) * (uzunluk - len(uzak))
    yerel += (0,) * (uzunluk - len(yerel))
    for sira, (u, y) in enumerate(zip(uzak, yerel)):
        if u != y:
            return _CINSLER[sira] if sira < len(_CINSLER) else _CINSLER[-1]
    return None


__all__ = ["__version__", "__author__", "__build__", "guncel_surum", "update_type"]
