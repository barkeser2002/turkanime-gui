"""
DosyaManager()
    - Config ve izlenenler geçmişi dosyalarını yaratır & düzenler
DownloadGereksinimler()
    - Gereksinimlerin indirilmesini ve paketten çıkarılmasını sağlar.

Bu modüldeki JSON dosyaları (`ayarlar.json`, `gecmis.json`) üç kuralla yazılır:

1. **Süreç-içi kilit.** Oku-değiştir-yaz bölünürse kayıt kaybolur: üç indirme
   aynı anda bitince üçü de geçmişin eski hâlini okuyup kendi tek eklemesiyle
   geri yazıyordu, yalnızca sonuncusu kalıyordu.
2. **Atomik yazım.** Yerinde `open(...,"w")` dosyayı önce sıfırlar; yazımın
   ortasında çöken bir süreç geriye yarım JSON bırakır. Geçici dosya + fsync +
   `os.replace` ile okuyucu ya eski ya yeni dosyayı görür, arası yoktur.
3. **Bozuk dosyadan kurtarma.** Yine de bozuk bir dosyayla karşılaşılırsa
   (eski sürümün bıraktığı, elle düzenlenmiş, disk hatası) `JSONDecodeError`
   ile çökmek yerine dosya yedeğe alınır ve varsayılana dönülür.

Kilit süreç-içidir: aynı anda açık bir CLI ile bir GUI birbirini hâlâ ezebilir.
Bu bilinçli — taşınabilir dosya kilidi (msvcrt/fcntl) ayrı bir iş; atomik yazım
sayesinde en kötü senaryo "son yazan kazanır", "dosya bozuldu" değil.
"""
from os import path,mkdir,getcwd
import json
import os
import threading
import time
import uuid

# yt-dlp, mpv gibi gereksinimlerin indirme linklerinin bulunduğu dosya.
DL_URL="https://raw.githubusercontent.com/KebabLord/turkanime-indirici/master/gereksinimler.json"

# Geçmişin boş hâli — bozuk dosyadan dönülecek nokta.
VARSAYILAN_GECMIS = {"izlendi": {}, "indirildi": {}}

_KILIT_DEFTERI = {}
_DEFTER_KILIDI = threading.Lock()

# Eski (Türkçe karakterli) ayar adı -> ASCII karşılığı.
#
# Aşağıdaki "ayar isimleri ascii karakterlerden oluşmalı" kuralına rağmen Qt
# tarafı bir süre "1080p aday sayısı" adını OKUDU; anahtar `default_ayarlar`da
# hiç yoktu, yani dosyada ancak elle ya da eski bir sürümle oluşabiliyordu.
# Kural ile gerçek ayrışınca tek bir ayar iki adla birden yaşar ve hangisinin
# kazandığı okuyucunun sırasına kalır. Bu yüzden eski ad açılışta ASCII adına
# taşınıp SİLİNİR: dosyada her zaman tek doğru anahtar kalır.
ESKI_AYAR_ADLARI = {"1080p aday sayısı": "1080p aday sayisi"}


def _kilit(yol):
    """Dosya yoluna bağlı süreç-içi kilit.

    Kilit örnekte DEĞİL modülde tutulur: `Dosyalar` her çağrıda yeniden
    örnekleniyor (bkz. `gui.qt.prefs._dosya`), örnek başına kilit olsaydı üç
    indirme üç ayrı kilit alır ve hiçbiri diğerini beklemezdi.
    """
    anahtar = os.path.normcase(os.path.abspath(yol))
    with _DEFTER_KILIDI:
        kilit = _KILIT_DEFTERI.get(anahtar)
        if kilit is None:
            kilit = _KILIT_DEFTERI[anahtar] = threading.RLock()
    return kilit


def atomik_json_yaz(yol, veri):
    """JSON'u geçici dosya + `os.replace` ile yaz.

    Geçici dosya HEDEFLE AYNI dizinde olmalı: `os.replace` yalnızca aynı dosya
    sisteminde atomik, `tempfile` varsayılanı (/tmp) başka bir mount olabilir —
    eski kod oradan `shutil.move` ediyordu, yani aslında kopyalıyordu.
    """
    dizin = os.path.dirname(os.path.abspath(yol)) or "."
    os.makedirs(dizin, exist_ok=True)
    gecici = os.path.join(
        dizin, f".{os.path.basename(yol)}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(gecici, "w", encoding="utf-8") as fp:
            json.dump(veri, fp, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(gecici, yol)
    except BaseException:
        # Yarım geçici dosya bırakma; hedef dosyaya hiç dokunulmadı.
        try:
            os.remove(gecici)
        except OSError:
            pass
        raise


def _bozugu_ayir(yol, sebep):
    """Okunamayan dosyayı yedeğe al ve kullanıcıya bildir.

    Silinmiyor: içinde kurtarılabilir ayar (AniList jetonu, indirme klasörü)
    olabilir ve kullanıcı ne olduğunu göremeden veri kaybetmemeli.
    """
    yedek = f"{yol}.bozuk-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(yol, yedek)
    except OSError:
        yedek = None
    print(f"UYARI: {os.path.basename(yol)} okunamadı ({sebep}). "
          + (f"Bozuk dosya {os.path.basename(yedek)} adıyla saklandı; "
             if yedek else "")
          + "varsayılan değerlerle devam ediliyor.")


def _json_oku(yol, varsayilan):
    """Sözlük bekleyen JSON okuma; dosya yoksa/bozuksa varsayılana döner.

    Varsayılan yeni bir kopya olarak döner: çağıran taraf sonucu değiştirip
    geri yazıyor, paylaşılan sözlük dönseydi modül sabiti kirlenirdi.
    """
    try:
        with open(yol, encoding="utf-8") as fp:
            veri = json.load(fp)
    except FileNotFoundError:
        return json.loads(json.dumps(varsayilan))
    except (ValueError, UnicodeDecodeError, OSError) as hata:
        _bozugu_ayir(yol, hata)
        return json.loads(json.dumps(varsayilan))
    if not isinstance(veri, dict):
        _bozugu_ayir(yol, "JSON nesnesi değil")
        return json.loads(json.dumps(varsayilan))
    return veri


class Dosyalar:
    """ Yazılımın konfigürasyon ve indirilenler klasörünü yönet
    - Windows'ta varsayılan dizin: $USER/Turkanime
    - Linux'ta varsayılan dizin: /home/$USER/Turkanime

    Öznitelikler:
        ayar_path: Turkanime config dosyasının dizini
        Dosyalar.gecmis_path: İzlenme ve indirme log'unun dizini
    """
    # Defaults to C:/User/xxx/Turkanime veya ~/Turkanime dizini.

    def __init__(self):
        self.ta_path = path.join(path.expanduser("~"), "Turkanime" )
        if path.isdir(".git"): # Git reposundan çalıştırılıyorsa.
            self.ta_path = getcwd()
        self.ayar_path = path.join(self.ta_path, "ayarlar.json")
        self.gecmis_path = path.join(self.ta_path, "gecmis.json")
        # Platforma göre indirilenler klasörü
        downloads_dir = path.join(path.expanduser("~"), "Downloads")

        # Ayar isimleri ascii karakterlerden oluşmalı.
        default_ayarlar = {
            "manuel fansub" : False,
            "izlerken kaydet" : False,
            "indirilenler" : downloads_dir,
            "izlendi ikonu" : True,
            "paralel indirme sayisi" : 3,
            # Kaç 1080p adayının erkenden yoklanacağı. Qt tarafı bunu okuyordu
            # ama anahtar burada yoktu; varsayılansız ayar "kimse yazmadıysa
            # ne olacak?" sorusunu her okuyucuya ayrı ayrı sordurur.
            "1080p aday sayisi" : 8,
            "max resolution" : True,
            "dakika hatirla" : True,
            "aria2c kullan" : False,
            "kaynak": "turkanime",
            "discord_rich_presence": True,
            "tranime_cookie": "",
            # OpenAnime giriş çerezleri: kaynak, ölü CDN uçlarında kullanıcıya
            # "Ayarlar'dan token'ı girin" diyor (bkz. sources/openani.py).
            "openani_token": "",
            "openani_refresh_token": "",
            "flaresolverr_url": "http://node-kyb.bariskeser.com:8191",
            "cookie_tutorial_dismissed": False,
            # Oturum kimliği bağışı — VARSAYILAN KAPALI ve öyle kalmalı.
            # Açıkken bile tek başına hiçbir şey göndermez: çerez alındığında
            # yalnızca onay diyaloğunun GÖSTERİLMESİNE izin verir, gönderim
            # kullanıcının o diyaloğu onaylamasına bağlıdır
            # (bkz. `gui.qt.katki_dialog`).
            "kimlik paylas": False,
            # Sunucunun verdiği bağış numarası; geri çekmenin tek anahtarı.
            # Bağışçıyı sunucuda tanımlayan tek şey bu olduğu için boşalması
            # "bağışı artık silemiyorum" demektir — geri çekme başarılı olmadan
            # temizlenmez.
            "kimlik bagis id": "",
            # Sunucu adresi/anahtarı koda gömülü DEĞİL: gömülü olsaydı
            # istemcinin her kopyası aynı sunucuya kimlik göndermeye hazır
            # gelirdi. Boş bırakılırsa bağış ucu istemci tarafında da kapalıdır.
            "sunucu adresi": "",
            "sunucu api anahtari": ""
        }
        # Gerekli dosyalar eğer daha önce yaratılmadıysa yarat.
        if not path.isdir(".git") and not path.isdir(self.ta_path):
            mkdir(self.ta_path)
        # Yeni ayarlar varsa sistemdekine ekle.
        if path.isfile(self.ayar_path):
            # `.ayarlar` bozuk dosyayı yedeğe alıp {} döndürür; eksik anahtarlar
            # aşağıda zaten tamamlandığı için kurtarma kendiliğinden tamamlanır.
            ayarlar = self.ayarlar
            eksikler = {a: v for a, v in default_ayarlar.items() if a not in ayarlar}
            # Eski adla yazılmış değer varsayılanı EZMELİ: kullanıcının seçtiği
            # sayı, göç yüzünden sessizce 8'e dönmemeli. Bu yüzden `goc`
            # sözlüğü `eksikler`in üstüne biniyor.
            goc = {yeni: ayarlar[eski]
                   for eski, yeni in ESKI_AYAR_ADLARI.items() if eski in ayarlar}
            if eksikler or goc:
                self.set_ayar(ayar_list={**eksikler, **goc})
            if goc:
                self.ayar_sil(*ESKI_AYAR_ADLARI)
            # User ID kontrolü - eğer yoksa oluştur
            if not ayarlar.get('user_id'):
                user_id = str(uuid.uuid4())
                self.set_ayar('user_id', user_id)
                print(f"Yeni kullanıcı kimliği oluşturuldu: {user_id}")
        else:
            atomik_json_yaz(self.ayar_path, {})
            self.set_ayar(ayar_list=default_ayarlar)
            # İlk çalıştırmada user_id oluştur
            user_id = str(uuid.uuid4())
            self.set_ayar('user_id', user_id)
            print(f"İlk çalıştırma - kullanıcı kimliği oluşturuldu: {user_id}")
        if not path.isfile(self.gecmis_path):
            atomik_json_yaz(self.gecmis_path, dict(VARSAYILAN_GECMIS))

    def _gecmis_yaz(self, gecmis):
        """Geçmiş dosyasını atomik yaz (geçici dosya + `os.replace`)."""
        atomik_json_yaz(self.gecmis_path, gecmis)

    def _gecmis_guncelle(self, degistir):
        """Oku-değiştir-yaz'ı kilit altında tek parça çalıştır.

        `degistir(gecmis)` False dönerse yazım atlanır (değişiklik yok).
        """
        with _kilit(self.gecmis_path):
            gecmis = _json_oku(self.gecmis_path, VARSAYILAN_GECMIS)
            if degistir(gecmis) is False:
                return
            self._gecmis_yaz(gecmis)

    def set_gecmis(self, seri,bolum,islem):
        def degistir(gecmis):
            # setdefault: eski sürümlerden kalma gecmis.json'da bölüm anahtarı
            # eksik olabiliyor; KeyError yerine sessizce tamamla.
            bolumler = gecmis.setdefault(islem,{})
            if seri not in bolumler:
                bolumler[seri] = []
            if bolum in bolumler[seri]:
                return False
            bolumler[seri].append(bolum)
            return True
        self._gecmis_guncelle(degistir)

    def set_ilerleme(self, seri, bolum_no):
        """Serinin yerel izleme ilerlemesi: son tamamlanan bölüm numarası.

        İzlendi listesi bölüm slug'ı tutuyor; kullanıcının "kaçıncı bölümdeyim"
        beyanı ise bir sayı ve slug'lardan türetilemez (kaynaklar bölümü farklı
        adlandırıyor). Bu yüzden ayrı bir alanda duruyor.
        """
        def degistir(gecmis):
            gecmis.setdefault("ilerleme",{})[seri] = int(bolum_no)
            return True
        self._gecmis_guncelle(degistir)

    def set_ayar(self, ayar = None, deger = None, ayar_list = None):
        assert (ayar != None and deger != None) or ayar_list != None
        # Ayar yazımı da kilit altında: ayar sayfası ile arka plan servisleri
        # (AniList jetonu, gereksinim kurulumu) aynı dosyaya yazabiliyor.
        with _kilit(self.ayar_path):
            ayarlar = self.ayarlar
            if ayar_list:
                for n,v in ayar_list.items():
                    ayarlar[n] = v
            else:
                ayarlar[ayar] = deger
            atomik_json_yaz(self.ayar_path, ayarlar)

    def ayar_sil(self, *adlar):
        """Verilen ayar anahtarlarını dosyadan kaldır; bir şey silindiyse True.

        Göç sonrası eski adın dosyada kalması "iki adlı tek ayar" demek olurdu:
        kullanıcı yeni adı değiştirir, eski adı okuyan bir yol hâlâ eski değeri
        görürdü. Silme de yazma gibi kilit altında (bkz. `set_ayar`).
        """
        with _kilit(self.ayar_path):
            ayarlar = self.ayarlar
            silinecek = [a for a in adlar if a in ayarlar]
            if not silinecek:
                return False
            for a in silinecek:
                del ayarlar[a]
            atomik_json_yaz(self.ayar_path, ayarlar)
            return True

    @property
    def ayarlar(self):
        return _json_oku(self.ayar_path, {})

    @property
    def gecmis(self):
        return _json_oku(self.gecmis_path, VARSAYILAN_GECMIS)
