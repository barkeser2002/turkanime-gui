""" TürkAnime Downloader CLI """
from os import environ, name, path
from time import sleep
import sys
import atexit
import concurrent.futures as cf
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from rich.live import Live
from rich.table import Table
from rich import print as rprint
import questionary as qa

from ..common import requirements as gereksinim   # modül olarak: testler sahteleyebilsin
from ..common.cf_qt_solver import SOLVER_FLAG
from ..sources import kayit
from ..sources.adapter import kayittan_bolumler
from .dosyalar import Dosyalar
from .cli_tools import prompt_tema, clear, indirme_task_cli, VidSearchCLI, CliStatus
from .version import guncel_surum, update_type

# Uygulama dizinini sistem PATH'ına ekle
SEP = ";" if name == "nt" else ":"
environ["PATH"] += SEP + Dosyalar().ta_path + SEP

# Aramada kaynak başına gösterilecek azami sonuç (kaynakların çoğunun kendi
# varsayılanı da 20; tek bir listeden seçmek için yeterli).
ARAMA_LIMITI = 20


def log_error(e):
    """ Hata logunu error.log dosyasına yazar. """
    try:
        error_path = path.join(Dosyalar().ta_path, "error.log")
        with open(error_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now()}: {str(e)}\n{traceback.format_exc()}\n\n")
    except Exception:
        pass


def select_download_folder(current_path):
    """İndirme klasörünü seçtir: pencere açılabiliyorsa easygui, yoksa metin istemi.

    easygui modül düzeyinde import EDİLMİYOR. tkinter ister; tkinter'sız
    kurulumlarda (sunucu, minimal Linux, bazı Python derlemeleri) import anında
    patlıyor — easygui'nin kendi yedek import'u da düşüyor
    (`ModuleNotFoundError: global_state`). Modül düzeyindeyken bu, CLI'ın HİÇ
    açılmaması demekti; oysa easygui yalnızca bu tek ayar için kullanılıyor.
    Ekranı olmayan (SSH) oturumda import başarılı olsa bile pencere açılamıyor
    (`TclError`); o durumda da metin istemine düşülüyor.
    """
    if current_path and path.exists(current_path):
        default = current_path
    else:
        default = path.expanduser("~")

    try:
        import easygui  # pylint: disable=import-outside-toplevel
        folder = easygui.diropenbox("İndirme klasörünü seçin", "Klasör Seç", default)
    except Exception:
        yanit = qa.path(
            "İndirme klasörü:", default=default, only_directories=True,
            style=prompt_tema,
        ).ask()
        folder = path.expanduser(yanit.strip()) if yanit and yanit.strip() else None
    return folder if folder else current_path


def eps_to_choices(liste, mark_type):
    """
    Bölüm listesi -> questionary.Choice listesi, geçmiş işaretleriyle.
    """
    assert len(liste) != 0
    slug = getattr(liste[0].anime, 'slug', '')
    recent, choices, gecmis = None, [], []
    gecmis_ = Dosyalar().gecmis
    if slug in gecmis_[mark_type]:
        gecmis = gecmis_[mark_type][slug]
        recent = gecmis[-1]
    for bolum in liste:
        isim = str(bolum.title)
        if bolum.slug in gecmis:
            isim += " ●"
        choice = qa.Choice(isim, bolum)
        if bolum.slug == recent:
            recent = choice
        choices.append(choice)
    return choices, recent


# ── Kaynaklar ────────────────────────────────────────────────────────────────
# Kaynak listesinin tek yeri `sources/kayit.py`. Buradaki ad→başlık tablosu
# eskiden elle yazılıyordu, menü de ondan türüyordu; yeni kaynak eklendiğinde
# (AnimeDepo) unutulunca kaynak seçilemiyordu. Aşağıdaki sabit geriye uyum için
# kayıttan türetilmiş bir anlık görüntü; menü ve seçim her seferinde kaydın
# kendisini okuyor.
#
# "animedepo" artık ayrı bir seçenek değil: aynı arşiv "TürkAnime (arşiv)"
# olarak listeleniyor. Ayarında "animedepo" kalmış kullanıcı TürkAnime'ye düşer.
SOURCE_TITLES = {k.cli_kodu: k.cli_etiketi for k in kayit.cli_kaynaklari()}


def _norm_source(val: str) -> str:
    """Ayar değeri / menü başlığı → CLI kaynak kodu (tanınmazsa "turkanime")."""
    return kayit.cli_kaynagi(val).cli_kodu


def _source_title(code: str) -> str:
    return kayit.cli_kaynagi(code).cli_etiketi


def secili_kaynak(dosyalar: Optional[Dosyalar] = None) -> "kayit.Kaynak":
    """`ayarlar.json` → "kaynak" ayarındaki kaynak (kayıttan)."""
    ayarlar = (dosyalar or Dosyalar()).ayarlar
    return kayit.cli_kaynagi(ayarlar.get("kaynak", "turkanime"))


def _kaynak_basliklari() -> Dict[str, "kayit.Kaynak"]:
    """"Kaynak seç" menüsü: başlık → kaynak (kayıt sırasıyla).

    Questionary sürümleri arasında Choice(name, value) ile `default`
    eşleşmesi sorun çıkarabiliyor; bu yüzden menü düz başlık dizgeleriyle
    kuruluyor ve seçilen başlık buradan koda çevriliyor.
    """
    return {k.cli_etiketi: k for k in kayit.cli_kaynaklari()}


def _anime_sec(kaynak) -> Optional[Tuple[str, str]]:
    """Seçili kaynakta ara ve kullanıcıya bir seri seçtir: ``(slug, isim)``.

    Bütün kaynaklar için TEK akış. Eskiden her kaynağın kendi dalı vardı ve
    hata davranışları ayrışmıştı: yalnızca TürkAnime dalı arama hatasını
    yakalıyordu, diğerlerinde bir ağ hatası en dıştaki `sys.exit(1)`'e kadar
    çıkıp CLI'ı kapatıyordu.
    """
    sorgu = qa.text(f"{kaynak.etiket}: aramak için yazın",
                    style=prompt_tema).ask(kbi_msg="")
    if not sorgu:
        return None
    try:
        with CliStatus(f"'{sorgu}' {kaynak.etiket} içinde aranıyor.."):
            bulunan = kaynak.ara(sorgu, limit=ARAMA_LIMITI)
    except Exception as e:
        log_error(e)
        rprint("[red][strong]Arama yapılırken bir hata oluştu.[/strong][/red]")
        sleep(1.5)
        return None
    if not bulunan:
        rprint("[red][strong]Aradığınız anime bulunamadı.[/strong][/red]")
        if kaynak.cerez_gerekir:
            rprint(f"[yellow]{kaynak.etiket} oturum çerezi olmadan sonuç vermiyor; "
                   "çerezi Qt arayüzündeki çerez tarayıcısıyla alabilirsiniz.[/yellow]")
        sleep(1.5)
        return None
    secim = qa.select(
        "Seri seç",
        choices=[qa.Choice(isim, (slug, isim)) for slug, isim in bulunan],
        style=prompt_tema,
        instruction="Yukarı/Aşağı • Enter",
    ).ask()
    if not secim:
        return None
    slug, isim = secim
    return str(slug), str(isim)


def _bolumleri_getir(kaynak, slug: str, isim: str) -> Optional[List[Any]]:
    """Seçilen serinin bölüm nesneleri; hata olursa mesaj basıp None."""
    hata = kaynak.kimlik_denetle(slug)
    if hata:
        rprint(f"[red]{hata}[/red]")
        sleep(2)
        return None
    try:
        with CliStatus("Bölümler getiriliyor.."):
            return kayittan_bolumler(kaynak, slug, isim)
    except Exception as e:
        log_error(e)
        rprint("[red][strong]Bölümler alınamadı.[/strong][/red]")
        sleep(1.5)
        return None


def _bolum_izle(bolumler: List[Any], dosya: Dosyalar) -> bool:
    """Bir bölüm seçtirip oynat. Kullanıcı vazgeçerse False (menüye dön)."""
    choices, recent = eps_to_choices(bolumler, mark_type="izlendi")
    bolum = qa.select(
        message='Bölüm seç', choices=choices, style=prompt_tema, default=recent,
        instruction="Yukarı/Aşağı • Enter"
    ).ask(kbi_msg="")
    if not bolum:
        return False
    sub = None
    # `fansubs` akışları getiriyor (arşivde yerel okuma, diğer kaynaklarda
    # ağ); yalnızca kullanıcı fansub seçmek istiyorsa sorulur.
    if dosya.ayarlar["manuel fansub"]:
        fansubs = getattr(bolum, 'fansubs', [])
        if len(fansubs) > 1:
            sub = qa.select(
                message='Fansub seç', choices=fansubs, style=prompt_tema,
                instruction="Yukarı/Aşağı • Enter"
            ).ask(kbi_msg="")
            if not sub:
                return False
    success = False
    for _ in range(3):
        vid_cli = VidSearchCLI()
        with vid_cli.progress:
            best_video = bolum.best_video(
                by_res=dosya.ayarlar["max resolution"],
                by_fansub=sub,
                callback=vid_cli.callback
            )
        if not best_video:
            print("  (!) Hiçbir çalışan video bulunamadı.")
            break
        print("  Video başlatılacak..")
        proc = best_video.oynat(dakika_hatirla=dosya.ayarlar["dakika hatirla"])
        if proc is None:
            print("  Video oynatıcı başlatılamadı!")
            best_video.is_working = False
            continue
        if proc.returncode == 0:
            success = True
            break
        best_video.is_working = False
        print("  Video çalışmadı, başka bir video denenecek..")
    if success and getattr(bolum, 'anime', None):
        dosya.set_gecmis(bolum.anime.slug, bolum.slug, "izlendi")
    return True


def _bolum_indir(bolumler: List[Any], dosya: Dosyalar) -> bool:
    """Bölümleri seçtirip paralel indir. Kullanıcı vazgeçerse False."""
    choices, recent = eps_to_choices(bolumler, mark_type="indirildi")
    if len(choices) > 10:
        filt = qa.text("Bölüm ara/filtre (boş geçilebilir)", style=prompt_tema).ask(kbi_msg="")
        if filt:
            choices = [c for c in choices if filt.lower() in str(c.title).lower()]

    secilenler = qa.checkbox(
        message="Bölüm seç",
        choices=choices,
        style=prompt_tema,
        initial_choice=recent,
        instruction="Boşluk: seç • a: tümünü değiştir • i: tersine çevir • Enter: onayla"
    ).ask(kbi_msg="")
    if not secilenler:
        return False
    table = Table.grid(expand=False)
    with Live(table, refresh_per_second=10, vertical_overflow="visible"):
        futures = []
        paralel = dosya.ayarlar.get("paralel indirme sayisi")
        with cf.ThreadPoolExecutor(max_workers=paralel) as executor:
            for bolum in secilenler:
                futures.append(executor.submit(
                    indirme_task_cli, bolum, table, dosya
                ))
            cf.wait(futures)
    return True


def menu_loop():
    """ Ana menü interaktif navigasyonu """
    while True:
        clear()
        islem = qa.select(
            "İşlemi seç",
            choices=['Anime izle', 'Anime indir', 'Kaynak seç', 'Ayarlar', 'Kapat'],
            style=prompt_tema,
            instruction="Yukarı/Aşağı ile gezin • Enter ile onayla"
        ).ask()
        if not islem:
            break

        if "Anime" in islem:
            kaynak = secili_kaynak()
            secim = _anime_sec(kaynak)
            if secim is None:
                continue
            seri_slug, seri_ismi = secim
            bolumler = _bolumleri_getir(kaynak, seri_slug, seri_ismi)
            if bolumler is None:
                continue
            if not bolumler:
                rprint("[red]Bölüm bulunamadı.[/red]")
                sleep(1.5)
                continue
            # Liste seri başına BİR kez çekiliyor; kullanıcı aynı seriden art
            # arda bölüm izleyip/indirip vazgeçene kadar burada kalıyor.
            while True:
                dosya = Dosyalar()
                devam = (_bolum_izle(bolumler, dosya) if "izle" in islem
                         else _bolum_indir(bolumler, dosya))
                if not devam:
                    break

        elif islem == "Kaynak seç":
            ds = Dosyalar()
            basliklar = _kaynak_basliklari()
            sec_title = qa.select(
                "Kaynak seç",
                choices=list(basliklar),
                default=secili_kaynak(ds).cli_etiketi,
                style=prompt_tema,
                instruction="Yukarı/Aşağı • Enter",
            ).ask()
            if sec_title in basliklar:
                ds.set_ayar("kaynak", basliklar[sec_title].cli_kodu)

        elif islem == "Ayarlar":
            while True:
                clear()
                dosyalar = Dosyalar()
                ayarlar = dosyalar.ayarlar
                tr = lambda opt: "AÇIK" if opt else "KAPALI"
                ayarlar_options = [
                    'İndirilenler klasörünü seç',
                    'İzlerken kaydet: ' + tr(ayarlar['izlerken kaydet']),
                    'Manuel fansub seç: ' + tr(ayarlar['manuel fansub']),
                    'İzlendi/İndirildi ikonu: ' + tr(ayarlar["izlendi ikonu"]),
                    'Paralel indirme sayisi: ' + str(ayarlar["paralel indirme sayisi"]),
                    'Maksimum çözünürlüğe ulaş: ' + tr(ayarlar["max resolution"]),
                    'Kaldığın dakikayı hatirla: ' + tr(ayarlar["dakika hatirla"]),
                    'Aria2c ile hızlandır (deneysel): ' + tr(ayarlar["aria2c kullan"]),
                    'Geri dön'
                ]
                ayar_islem = qa.select(
                    'İşlemi seç', ayarlar_options, style=prompt_tema,
                    instruction="Yukarı/Aşağı • Enter"
                ).ask()

                if ayar_islem == ayarlar_options[0]:
                    indirilenler_dizin = select_download_folder(ayarlar.get("indirilenler"))
                    if indirilenler_dizin:
                        dosyalar.set_ayar("indirilenler", indirilenler_dizin)
                elif ayar_islem == ayarlar_options[1]:
                    dosyalar.set_ayar("izlerken kaydet", not ayarlar['izlerken kaydet'])
                elif ayar_islem == ayarlar_options[2]:
                    dosyalar.set_ayar('manuel fansub', not ayarlar['manuel fansub'])
                elif ayar_islem == ayarlar_options[3]:
                    dosyalar.set_ayar('izlendi ikonu', not ayarlar['izlendi ikonu'])
                elif ayar_islem == ayarlar_options[4]:
                    max_dl = qa.text(
                        message='Maksimum eş zamanlı kaç bölüm indirilsin?',
                        default=str(ayarlar["paralel indirme sayisi"]),
                        style=prompt_tema
                    ).ask(kbi_msg="")
                    if isinstance(max_dl, str) and max_dl.isdigit():
                        dosyalar.set_ayar("paralel indirme sayisi", int(max_dl))
                elif ayar_islem == ayarlar_options[5]:
                    dosyalar.set_ayar('max resolution', not ayarlar['max resolution'])
                elif ayar_islem == ayarlar_options[6]:
                    dosyalar.set_ayar('dakika hatirla', not ayarlar['dakika hatirla'])
                elif ayar_islem == ayarlar_options[7]:
                    dosyalar.set_ayar('aria2c kullan', not ayarlar['aria2c kullan'])
                else:
                    break

        elif islem == "Kapat":
            break


def kaynagi_hazirla(kaynak=None) -> Any:
    """Seçili kaynağın açılış hazırlığını çalıştır (kayıttaki `hazirlik`).

    TürkAnime için arşiv dizinini yükler: ilk aramanın beklemesi durum
    göstergesinin altına taşınır ve arşiv hiçbir yerde yoksa kullanıcı bunu
    menüye girmeden öğrenir. Hazırlığı olmayan kaynakta hiçbir şey yapmaz.
    """
    kaynak = kaynak or secili_kaynak()
    return kaynak.hazirlik() if kaynak.hazirlik else None


# Eski ad. Açılış denetimi eskiden `bypass.fetch("/")` ile turkanime.tv'de
# oturum açıyordu; site kapandı, artık kaynağı (arşivi) hazırlıyor. Ad, açılış
# denetimini sahteleyen testler (tests/test_cli.py) ve `__main__.fetch`'e
# dokunan betikler bozulmasın diye korunuyor — `main()` bu adı çağırıyor.
fetch = kaynagi_hazirla


def main():
    # Donmuş (PyInstaller) CLI exe'si de CF çözücüsünün giriş noktasıdır:
    # `cf_bypass._get_qt_solver`, donmuş modda `sys.executable --cf-qt-solver`
    # ile UYGULAMANIN KENDİSİNİ çağırıyor. CLI bu bayrağı tanımadığı sürece o
    # çağrı etkileşimli menüyü açıyor, çözücü el sıkışması bozuk çıkıyor ve
    # açılan süreç yetim kalıyordu — her CF challenge'ında bir tane.
    if SOLVER_FLAG in sys.argv:
        from ..common.cf_qt_solver import main as solver_main
        return solver_main()

    # Güncelleme kontrolü. Uç artık `common/updater.py` — gerekçesi
    # `cli/version.py` başlığında (eski üç ucun üçü de yanlış hedefi
    # gösteriyordu).
    try:
        with CliStatus("Güncelleme kontrol ediliyor.."):
            surum = guncel_surum()
        tip = update_type(surum)
        if tip:
            rprint(f"[yellow]{tip} Güncellemesi mevcut!! v{surum}[/yellow]")
            rprint("[yellow]Yeni özellikler için uygulamayı güncelleyebilirsiniz! [/yellow]")
            sleep(5)
    except Exception as e:
        log_error(e)
        # sleep(3) kaldırıldı: mesaj zaten ekranda kalıyor, ağı olmayan
        # kullanıcıyı her açılışta üç saniye bekletmenin karşılığı yoktu.
        rprint("[red][strong]Güncelleme kontrol edilemedi.[/strong][red]")

    # Gereksinim denetimi. Eskiden yorum satırındaydı ve gerekçe "embed
    # edilmiş araçlar kullanılıyor" idi; oysa gömülü kopya mekanizması
    # (common/requirements.py: `sys._MEIPASS/bin`) YALNIZCA PyInstaller
    # paketinde geçerli. pip ile kurulan CLI'da mpv/yt-dlp/aria2c/ffmpeg hiç
    # denetlenmiyor, eksiklik ancak oynatma/indirme anında sessiz bir hataya
    # dönüşüyordu. Denetim Qt ile AYNI çekirdekten yapılıyor; CLI'ın kendi
    # kopyası (cli/gereksinimler.py) bu yüzden silindi.
    # `path_hazirla` şart: sihirbazın uygulama dizinine kurduğu araçlar
    # PATH'te değilse `arac_var_mi` onları göremez.
    gereksinim.path_hazirla()
    try:
        with CliStatus("Gereksinimler denetleniyor.."):
            eksikler = gereksinim.eksik_araclar()
    except Exception as e:
        log_error(e)
        eksikler = []
    if eksikler:
        # Uyarı yeterli, çıkış değil: aria2c ve ffmpeg olmadan da izlenebiliyor
        # ve CLI bugün hiç denetlemiyordu — çıkmak düpedüz gerileme olurdu.
        rprint(f"[yellow]!) Şu araçlar bulunamadı: {', '.join(eksikler)}[/yellow]")
        rprint("[yellow]   Kurulum için: https://github.com/barkeser2002/"
               "turkanime-gui/wiki[/yellow]")

    # Script kapanışında
    def kapat():
        with CliStatus("Kapatılıyor.."):
            sleep(1.5)
    atexit.register(kapat)

    # Seçili kaynağı hazırla — YALNIZCA kaydında hazırlığı olan kaynakta
    # (TürkAnime: arşiv dizini). Burası eskiden turkanime.tv'de oturum
    # açıyordu (`bypass.fetch("/")`); site kapandı, o istek artık yalnızca
    # zaman aşımı biriktiriyordu. Diğer kaynaklarda denetim koşmuyor.
    kaynak = secili_kaynak()
    if kaynak.hazirlik is not None:
        try:
            with CliStatus(f"{kaynak.etiket} hazırlanıyor.."):
                _ = fetch(kaynak)
        except Exception as e:
            # sys.exit(1) YOK: çıkmak kullanıcıyı "Kaynak seç" menüsünden de
            # mahrum bırakırdı, yani ayarını düzeltmesinin yolu kalmazdı.
            # Yakalama geniş: yerel okuma (OSError/ValueError) ve uzak ayna
            # (Timeout, SSLError…) hataları ayrı ayrı sayılmaya değmez.
            log_error(e)
            rprint(f"[red][strong]{kaynak.etiket} okunamıyor.[/strong][/red]")
            rprint("[yellow]Menüden 'Kaynak seç' ile başka bir kaynağa "
                   "geçebilirsiniz.[/yellow]")
            sleep(2)

    # Navigasyon
    clear()
    rprint("[green]!)[/green] Üst menülere dönmek için Ctrl+C kullanabilirsiniz.\n")
    sleep(1.7)
    menu_loop()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log_error(e)
        rprint("[red][strong]Beklenmeyen bir hata oluştu. Detaylar error.log dosyasında.[/strong][/red]")
        sys.exit(1)
