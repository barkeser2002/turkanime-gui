"""
DosyaManager()
    - Config ve izlenenler geÃ§miÅŸi dosyalarÄ±nÄ± yaratÄ±r & dÃ¼zenler
DownloadGereksinimler()
    - Gereksinimlerin indirilmesini ve paketten Ã§Ä±karÄ±lmasÄ±nÄ± saÄŸlar.

Bu modÃ¼ldeki JSON dosyalarÄ± (`ayarlar.json`, `gecmis.json`) Ã¼Ã§ kuralla yazÄ±lÄ±r:

1. **SÃ¼reÃ§-iÃ§i kilit.** Oku-deÄŸiÅŸtir-yaz bÃ¶lÃ¼nÃ¼rse kayÄ±t kaybolur: Ã¼Ã§ indirme
   aynÄ± anda bitince Ã¼Ã§Ã¼ de geÃ§miÅŸin eski hÃ¢lini okuyup kendi tek eklemesiyle
   geri yazÄ±yordu, yalnÄ±zca sonuncusu kalÄ±yordu.
2. **Atomik yazÄ±m.** Yerinde `open(...,"w")` dosyayÄ± Ã¶nce sÄ±fÄ±rlar; yazÄ±mÄ±n
   ortasÄ±nda Ã§Ã¶ken bir sÃ¼reÃ§ geriye yarÄ±m JSON bÄ±rakÄ±r. GeÃ§ici dosya + fsync +
   `os.replace` ile okuyucu ya eski ya yeni dosyayÄ± gÃ¶rÃ¼r, arasÄ± yoktur.
3. **Bozuk dosyadan kurtarma.** Yine de bozuk bir dosyayla karÅŸÄ±laÅŸÄ±lÄ±rsa
   (eski sÃ¼rÃ¼mÃ¼n bÄ±raktÄ±ÄŸÄ±, elle dÃ¼zenlenmiÅŸ, disk hatasÄ±) `JSONDecodeError`
   ile Ã§Ã¶kmek yerine dosya yedeÄŸe alÄ±nÄ±r ve varsayÄ±lana dÃ¶nÃ¼lÃ¼r.

Kilit sÃ¼reÃ§-iÃ§idir: aynÄ± anda aÃ§Ä±k bir CLI ile bir GUI birbirini hÃ¢lÃ¢ ezebilir.
Bu bilinÃ§li â€” taÅŸÄ±nabilir dosya kilidi (msvcrt/fcntl) ayrÄ± bir iÅŸ; atomik yazÄ±m
sayesinde en kÃ¶tÃ¼ senaryo "son yazan kazanÄ±r", "dosya bozuldu" deÄŸil.
"""
from os import path,mkdir,getcwd
import json
import os
import threading
import time
import uuid

# yt-dlp, mpv gibi gereksinimlerin indirme linklerinin bulunduÄŸu dosya.
DL_URL="https://raw.githubusercontent.com/KebabLord/turkanime-indirici/master/gereksinimler.json"

# GeÃ§miÅŸin boÅŸ hÃ¢li â€” bozuk dosyadan dÃ¶nÃ¼lecek nokta.
VARSAYILAN_GECMIS = {"izlendi": {}, "indirildi": {}}

_KILIT_DEFTERI = {}
_DEFTER_KILIDI = threading.Lock()


def _kilit(yol):
    """Dosya yoluna baÄŸlÄ± sÃ¼reÃ§-iÃ§i kilit.

    Kilit Ã¶rnekte DEÄÄ°L modÃ¼lde tutulur: `Dosyalar` her Ã§aÄŸrÄ±da yeniden
    Ã¶rnekleniyor (bkz. `gui.qt.prefs._dosya`), Ã¶rnek baÅŸÄ±na kilit olsaydÄ± Ã¼Ã§
    indirme Ã¼Ã§ ayrÄ± kilit alÄ±r ve hiÃ§biri diÄŸerini beklemezdi.
    """
    anahtar = os.path.normcase(os.path.abspath(yol))
    with _DEFTER_KILIDI:
        kilit = _KILIT_DEFTERI.get(anahtar)
        if kilit is None:
            kilit = _KILIT_DEFTERI[anahtar] = threading.RLock()
    return kilit


def atomik_json_yaz(yol, veri):
    """JSON'u geÃ§ici dosya + `os.replace` ile yaz.

    GeÃ§ici dosya HEDEFLE AYNI dizinde olmalÄ±: `os.replace` yalnÄ±zca aynÄ± dosya
    sisteminde atomik, `tempfile` varsayÄ±lanÄ± (/tmp) baÅŸka bir mount olabilir â€”
    eski kod oradan `shutil.move` ediyordu, yani aslÄ±nda kopyalÄ±yordu.
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
        # YarÄ±m geÃ§ici dosya bÄ±rakma; hedef dosyaya hiÃ§ dokunulmadÄ±.
        try:
            os.remove(gecici)
        except OSError:
            pass
        raise


def _bozugu_ayir(yol, sebep):
    """Okunamayan dosyayÄ± yedeÄŸe al ve kullanÄ±cÄ±ya bildir.

    Silinmiyor: iÃ§inde kurtarÄ±labilir ayar (AniList jetonu, indirme klasÃ¶rÃ¼)
    olabilir ve kullanÄ±cÄ± ne olduÄŸunu gÃ¶remeden veri kaybetmemeli.
    """
    yedek = f"{yol}.bozuk-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(yol, yedek)
    except OSError:
        yedek = None
    print(f"UYARI: {os.path.basename(yol)} okunamadÄ± ({sebep}). "
          + (f"Bozuk dosya {os.path.basename(yedek)} adÄ±yla saklandÄ±; "
             if yedek else "")
          + "varsayÄ±lan deÄŸerlerle devam ediliyor.")


def _json_oku(yol, varsayilan):
    """SÃ¶zlÃ¼k bekleyen JSON okuma; dosya yoksa/bozuksa varsayÄ±lana dÃ¶ner.

    VarsayÄ±lan yeni bir kopya olarak dÃ¶ner: Ã§aÄŸÄ±ran taraf sonucu deÄŸiÅŸtirip
    geri yazÄ±yor, paylaÅŸÄ±lan sÃ¶zlÃ¼k dÃ¶nseydi modÃ¼l sabiti kirlenirdi.
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
        _bozugu_ayir(yol, "JSON nesnesi deÄŸil")
        return json.loads(json.dumps(varsayilan))
    return veri


class Dosyalar:
    """ YazÄ±lÄ±mÄ±n konfigÃ¼rasyon ve indirilenler klasÃ¶rÃ¼nÃ¼ yÃ¶net
    - Windows'ta varsayÄ±lan dizin: $USER/Turkanime
    - Linux'ta varsayÄ±lan dizin: /home/$USER/Turkanime

    Ã–znitelikler:
        ayar_path: Turkanime config dosyasÄ±nÄ±n dizini
        Dosyalar.gecmis_path: Ä°zlenme ve indirme log'unun dizini
    """
    # Defaults to C:/User/xxx/Turkanime veya ~/Turkanime dizini.

    def __init__(self):
        self.ta_path = path.join(path.expanduser("~"), "Turkanime" )
        if path.isdir(".git"): # Git reposundan Ã§alÄ±ÅŸtÄ±rÄ±lÄ±yorsa.
            self.ta_path = getcwd()
        self.ayar_path = path.join(self.ta_path, "ayarlar.json")
        self.gecmis_path = path.join(self.ta_path, "gecmis.json")
        # Platforma gÃ¶re indirilenler klasÃ¶rÃ¼
        downloads_dir = path.join(path.expanduser("~"), "Downloads")

        # Ayar isimleri ascii karakterlerden oluÅŸmalÄ±.
        default_ayarlar = {
            "manuel fansub" : False,
            "izlerken kaydet" : False,
            "indirilenler" : downloads_dir,
            "izlendi ikonu" : True,
            "paralel indirme sayisi" : 3,
            "max resolution" : True,
            "dakika hatirla" : True,
            "aria2c kullan" : False,
            "kaynak": "turkanime",
            "discord_rich_presence": True,
            "tranime_cookie": "",
            "flaresolverr_url": "http://node-kyb.bariskeser.com:8191",
            "cookie_tutorial_dismissed": False
        }
        # Gerekli dosyalar eÄŸer daha Ã¶nce yaratÄ±lmadÄ±ysa yarat.
        if not path.isdir(".git") and not path.isdir(self.ta_path):
            mkdir(self.ta_path)
        # Yeni ayarlar varsa sistemdekine ekle.
        if path.isfile(self.ayar_path):
            # `.ayarlar` bozuk dosyayÄ± yedeÄŸe alÄ±p {} dÃ¶ndÃ¼rÃ¼r; eksik anahtarlar
            # aÅŸaÄŸÄ±da zaten tamamlandÄ±ÄŸÄ± iÃ§in kurtarma kendiliÄŸinden tamamlanÄ±r.
            ayarlar = self.ayarlar
            eksikler = {a: v for a, v in default_ayarlar.items() if a not in ayarlar}
            if eksikler:
                self.set_ayar(ayar_list=eksikler)
            # User ID kontrolÃ¼ - eÄŸer yoksa oluÅŸtur
            if not ayarlar.get('user_id'):
                user_id = str(uuid.uuid4())
                self.set_ayar('user_id', user_id)
                print(f"Yeni kullanÄ±cÄ± kimliÄŸi oluÅŸturuldu: {user_id}")
        else:
            atomik_json_yaz(self.ayar_path, {})
            self.set_ayar(ayar_list=default_ayarlar)
            # Ä°lk Ã§alÄ±ÅŸtÄ±rmada user_id oluÅŸtur
            user_id = str(uuid.uuid4())
            self.set_ayar('user_id', user_id)
            print(f"Ä°lk Ã§alÄ±ÅŸtÄ±rma - kullanÄ±cÄ± kimliÄŸi oluÅŸturuldu: {user_id}")
        if not path.isfile(self.gecmis_path):
            atomik_json_yaz(self.gecmis_path, dict(VARSAYILAN_GECMIS))

    def _gecmis_yaz(self, gecmis):
        """GeÃ§miÅŸ dosyasÄ±nÄ± atomik yaz (geÃ§ici dosya + `os.replace`)."""
        atomik_json_yaz(self.gecmis_path, gecmis)

    def _gecmis_guncelle(self, degistir):
        """Oku-deÄŸiÅŸtir-yaz'Ä± kilit altÄ±nda tek parÃ§a Ã§alÄ±ÅŸtÄ±r.

        `degistir(gecmis)` False dÃ¶nerse yazÄ±m atlanÄ±r (deÄŸiÅŸiklik yok).
        """
        with _kilit(self.gecmis_path):
            gecmis = _json_oku(self.gecmis_path, VARSAYILAN_GECMIS)
            if degistir(gecmis) is False:
                return
            self._gecmis_yaz(gecmis)

    def set_gecmis(self, seri,bolum,islem):
        def degistir(gecmis):
            # setdefault: eski sÃ¼rÃ¼mlerden kalma gecmis.json'da bÃ¶lÃ¼m anahtarÄ±
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
        """Serinin yerel izleme ilerlemesi: son tamamlanan bÃ¶lÃ¼m numarasÄ±.

        Ä°zlendi listesi bÃ¶lÃ¼m slug'Ä± tutuyor; kullanÄ±cÄ±nÄ±n "kaÃ§Ä±ncÄ± bÃ¶lÃ¼mdeyim"
        beyanÄ± ise bir sayÄ± ve slug'lardan tÃ¼retilemez (kaynaklar bÃ¶lÃ¼mÃ¼ farklÄ±
        adlandÄ±rÄ±yor). Bu yÃ¼zden ayrÄ± bir alanda duruyor.
        """
        def degistir(gecmis):
            gecmis.setdefault("ilerleme",{})[seri] = int(bolum_no)
            return True
        self._gecmis_guncelle(degistir)

    def set_ayar(self, ayar = None, deger = None, ayar_list = None):
        assert (ayar != None and deger != None) or ayar_list != None
        # Ayar yazÄ±mÄ± da kilit altÄ±nda: ayar sayfasÄ± ile arka plan servisleri
        # (AniList jetonu, gereksinim kurulumu) aynÄ± dosyaya yazabiliyor.
        with _kilit(self.ayar_path):
            ayarlar = self.ayarlar
            if ayar_list:
                for n,v in ayar_list.items():
                    ayarlar[n] = v
            else:
                ayarlar[ayar] = deger
            atomik_json_yaz(self.ayar_path, ayarlar)

    @property
    def ayarlar(self):
        return _json_oku(self.ayar_path, {})

    @property
    def gecmis(self):
        return _json_oku(self.gecmis_path, VARSAYILAN_GECMIS)
