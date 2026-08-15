""" TürkAnime Downloader CLI """
from os import environ, name, path
from time import sleep
import sys
import atexit
import concurrent.futures as cf
import traceback
from datetime import datetime
import easygui

from rich.live import Live
from rich.table import Table
from rich import print as rprint
import questionary as qa

from ..bypass import fetch
from ..common.cf_qt_solver import SOLVER_FLAG
from ..objects import Anime
from ..sources import search_animecix, search_anizle
from ..sources.animecix import CixAnime
from ..sources.anizle import AnizleAnime, get_episode_streams
from ..sources.adapter import AdapterAnime, AdapterBolum
from .dosyalar import Dosyalar
from .cli_tools import prompt_tema, clear, indirme_task_cli, VidSearchCLI, CliStatus
from .version import guncel_surum, update_type

# Uygulama dizinini sistem PATH'ına ekle
SEP = ";" if name == "nt" else ":"
environ["PATH"] += SEP + Dosyalar().ta_path + SEP


def log_error(e):
    """ Hata logunu error.log dosyasına yazar. """
    try:
        error_path = path.join(Dosyalar().ta_path, "error.log")
        with open(error_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now()}: {str(e)}\n{traceback.format_exc()}\n\n")
    except Exception:
        pass


def select_download_folder(current_path):
    """Klasör seçimi için easygui kullan"""
    if current_path and path.exists(current_path):
        default = current_path
    else:
        default = path.expanduser("~")
    
    folder = easygui.diropenbox("İndirme klasörünü seçin", "Klasör Seç", default)
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


SOURCE_TITLES = {
    "turkanime": "TürkAnime",
    "animecix": "AnimeciX (deneysel)",
    "anizle": "Anizle (deneysel)",
    "tranimeizle": "TRAnimeİzle",
    "openanime": "OpenAnime",
    "tranimaci": "Tranimaci",
    "animedepo": "AnimeDepo (arşiv)",
}


def _norm_source(val: str) -> str:
    s = str(val or "").lower()
    # NOT: "animedepo" kontrolü "anime*" ile başlayan diğerlerinden önce gelmeli.
    if "animedepo" in s or "depo" in s:
        return "animedepo"
    if "animecix" in s:
        return "animecix"
    if "anizle" in s:
        return "anizle"
    if "tranimaci" in s or "tranimaci.com" in s:
        return "tranimaci"
    if "tran" in s or "tranime" in s:
        return "tranimeizle"
    if "open" in s or "openanime" in s:
        return "openanime"
    return "turkanime"


def _source_title(code: str) -> str:
    return SOURCE_TITLES.get(_norm_source(code), "TürkAnime")


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
            try:
                source = _norm_source(Dosyalar().ayarlar.get("kaynak", "turkanime"))
                anime = None
                cix_anime = None
                anizle_anime = None
                tranime_episodes_data = None
                openani_episodes_data = None
                animedepo_episodes_data = None
                tranimaci_episodes_data = None
                adapter_anime = None
                seri_slug = ""
                seri_ismi = ""

                if source == "animecix":
                    q = qa.text("AnimeciX: aramak için yazın", style=prompt_tema).ask(kbi_msg="")
                    if not q:
                        continue
                    with CliStatus("AnimeciX aranıyor.."):
                        found = search_animecix(q) or []
                    if not found:
                        raise KeyError
                    choices = [qa.Choice(name, (aid, name)) for (aid, name) in found]
                    pick = qa.select(
                        "Seri seç",
                        choices=choices,
                        style=prompt_tema,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask()
                    if not pick:
                        continue
                    seri_slug, seri_ismi = pick
                    seri_slug = str(seri_slug)
                    cix_anime = CixAnime(seri_slug, seri_ismi)
                    adapter_anime = AdapterAnime(slug=str(cix_anime.id), title=cix_anime.title)
                elif source == "anizle":
                    q = qa.text("Anizle: aramak için yazın", style=prompt_tema).ask(kbi_msg="")
                    if not q:
                        continue
                    with CliStatus("Anizle aranıyor.."):
                        found = search_anizle(q) or []
                    if not found:
                        raise KeyError
                    choices = [qa.Choice(title, (slug, title)) for (slug, title) in found]
                    pick = qa.select(
                        "Seri seç",
                        choices=choices,
                        style=prompt_tema,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask()
                    if not pick:
                        continue
                    seri_slug, seri_ismi = pick
                    anizle_anime = AnizleAnime(slug=seri_slug, title=seri_ismi)
                    adapter_anime = AdapterAnime(slug=anizle_anime.slug, title=anizle_anime.title)
                elif source == "tranimeizle":
                    from ..sources.tranime import search_tranime, get_tranime_episodes
                    q = qa.text("TRAnimeİzle: aramak için yazın", style=prompt_tema).ask(kbi_msg="")
                    if not q:
                        continue
                    with CliStatus("TRAnimeİzle aranıyor.."):
                        found = search_tranime(q) or []
                    if not found:
                        raise KeyError
                    choices = [qa.Choice(name, (slug, name)) for (slug, name) in found]
                    pick = qa.select(
                        "Seri seç",
                        choices=choices,
                        style=prompt_tema,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask()
                    if not pick:
                        continue
                    seri_slug, seri_ismi = pick
                    adapter_anime = AdapterAnime(slug=seri_slug, title=seri_ismi)
                    tranime_episodes_data = get_tranime_episodes(seri_slug)
                elif source == "openanime":
                    from ..sources.openani import search_openani, get_anime_episodes as get_openani_episodes
                    q = qa.text("OpenAnime: aramak için yazın", style=prompt_tema).ask(kbi_msg="")
                    if not q:
                        continue
                    with CliStatus("OpenAnime aranıyor.."):
                        found = search_openani(q) or []
                    if not found:
                        raise KeyError
                    choices = [qa.Choice(name, (slug, name)) for (slug, name) in found]
                    pick = qa.select(
                        "Seri seç",
                        choices=choices,
                        style=prompt_tema,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask()
                    if not pick:
                        continue
                    seri_slug, seri_ismi = pick
                    adapter_anime = AdapterAnime(slug=seri_slug, title=seri_ismi)
                    openani_episodes_data = get_openani_episodes(seri_slug)
                elif source == "tranimaci":
                    from ..sources.tranimaci import (
                        search_tranimaci,
                        get_anime_episodes as get_tranimaci_episodes,
                    )
                    q = qa.text("Tranimaci: aramak için yazın", style=prompt_tema).ask(kbi_msg="")
                    if not q:
                        continue
                    with CliStatus("Tranimaci aranıyor.."):
                        found = search_tranimaci(q) or []
                    if not found:
                        raise KeyError
                    choices = [qa.Choice(name, (slug, name)) for (slug, name) in found]
                    pick = qa.select(
                        "Seri seç",
                        choices=choices,
                        style=prompt_tema,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask()
                    if not pick:
                        continue
                    seri_slug, seri_ismi = pick
                    adapter_anime = AdapterAnime(slug=seri_slug, title=seri_ismi)
                    tranimaci_episodes_data = get_tranimaci_episodes(seri_slug)
                elif source == "animedepo":
                    from ..sources.animedepo import (
                        search_animedepo,
                        get_anime_episodes as get_animedepo_episodes,
                    )
                    q = qa.text("AnimeDepo: aramak için yazın", style=prompt_tema).ask(kbi_msg="")
                    if not q:
                        continue
                    with CliStatus("AnimeDepo aranıyor.."):
                        found = search_animedepo(q) or []
                    if not found:
                        raise KeyError
                    choices = [qa.Choice(name, (slug, name)) for (slug, name) in found]
                    pick = qa.select(
                        "Seri seç",
                        choices=choices,
                        style=prompt_tema,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask()
                    if not pick:
                        continue
                    seri_slug, seri_ismi = pick
                    adapter_anime = AdapterAnime(slug=seri_slug, title=seri_ismi)
                    animedepo_episodes_data = get_animedepo_episodes(seri_slug)
                else:
                    arama_metni = qa.text(
                        'Animeyi yazın',
                        style=prompt_tema
                    ).ask()
                    if not arama_metni:
                        continue
                    try:
                        with CliStatus(f"'{arama_metni}' için sitede aranıyor.."):
                            animeler = Anime.arama_yap(arama_metni)
                    except Exception as e:
                        log_error(e)
                        rprint("[red][strong]Arama yapılırken bir hata oluştu.[/strong][/red]")
                        sleep(1.5)
                        continue
                    if not animeler:
                        rprint("[red][strong]Aradığınız anime bulunamadı.[/strong][/red]")
                        sleep(1.5)
                        continue
                    seri_ismi = qa.select(
                        'Bulunan sonuçlardan birini seçin:',
                        choices=[n for s, n in animeler],
                        style=prompt_tema,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask()
                    if seri_ismi is None:
                        continue
                    seri_slug = next(s for s, n in animeler if n == seri_ismi)
                    anime = Anime(seri_slug)
            except (KeyError, IndexError):
                rprint("[red][strong]Aradığınız anime bulunamadı.[/strong][red]")
                sleep(1.5)
                continue

            anizle_stream_provider = (
                (lambda slug, _timeout=10: get_episode_streams(slug, timeout=_timeout))
                if source == "anizle" else None
            )

            tranime_stream_provider = None
            if source == "tranimeizle":
                from ..sources.tranime import get_tranime_episode_details
                def _tranime_provider(ep_slug):
                    def provider(url):
                        try:
                            ep_details = get_tranime_episode_details(ep_slug)
                            if ep_details:
                                sources = ep_details.get_sources()
                                streams = []
                                for s in sources:
                                    iframe = s.get_iframe()
                                    if iframe:
                                        streams.append({"url": iframe, "label": s.name})
                                return streams
                        except Exception:
                            pass
                        return []
                    return provider
                tranime_stream_provider = _tranime_provider

            openani_stream_provider = None
            if source == "openanime":
                from ..sources.openani import get_episode_streams as get_openani_streams
                openani_stream_provider = (lambda slug, _timeout=10: get_openani_streams(slug, timeout=_timeout))

            tranimaci_stream_provider = None
            if source == "tranimaci":
                from ..sources.tranimaci import get_episode_streams as get_tranimaci_streams
                tranimaci_stream_provider = (lambda slug: get_tranimaci_streams(slug))

            animedepo_stream_provider = None
            if source == "animedepo":
                from ..sources.animedepo import get_episode_streams as get_animedepo_streams
                animedepo_stream_provider = (lambda ep_id: get_animedepo_streams(ep_id))

            while True:
                dosya = Dosyalar()
                if "izle" in islem:
                    with CliStatus("Bölümler getiriliyor.."):
                        if source == "animecix" and cix_anime is not None:
                            adapter = adapter_anime or AdapterAnime(slug=str(cix_anime.id), title=cix_anime.title)
                            bolumler = [
                                AdapterBolum(e.url, e.title, adapter)
                                for e in cix_anime.episodes
                            ]
                        elif source == "anizle" and anizle_anime is not None and anizle_stream_provider:
                            adapter = adapter_anime or AdapterAnime(slug=anizle_anime.slug, title=anizle_anime.title)
                            bolumler = [
                                AdapterBolum(
                                    e.url,
                                    e.title,
                                    adapter,
                                    stream_provider=anizle_stream_provider,
                                    player_name="ANIZLE"
                                )
                                for e in anizle_anime.episodes
                            ]
                        elif source == "tranimeizle" and tranime_episodes_data is not None and tranime_stream_provider:
                            adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                            bolumler = [
                                AdapterBolum(
                                    e.slug,
                                    e.title,
                                    adapter,
                                    stream_provider=tranime_stream_provider(e.slug),
                                    player_name="TRANIME"
                                )
                                for e in tranime_episodes_data
                            ]
                        elif source == "openanime" and openani_episodes_data is not None and openani_stream_provider:
                            adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                            bolumler = [
                                AdapterBolum(
                                    f"https://openani.me/anime/{ep_slug}",
                                    ep_title,
                                    adapter,
                                    stream_provider=lambda url, _es=ep_slug: openani_stream_provider(_es),
                                    player_name="OPENANI"
                                )
                                for ep_slug, ep_title in openani_episodes_data
                            ]
                        elif source == "tranimaci" and tranimaci_episodes_data is not None and tranimaci_stream_provider:
                            adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                            bolumler = [
                                AdapterBolum(
                                    f"https://tranimaci.com/video/{ep_slug}",
                                    ep_title,
                                    adapter,
                                    stream_provider=lambda url, _es=ep_slug: tranimaci_stream_provider(_es),
                                    player_name="TRANIMACI"
                                )
                                for ep_slug, ep_title in tranimaci_episodes_data
                            ]
                        elif source == "animedepo" and animedepo_episodes_data is not None and animedepo_stream_provider:
                            adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                            bolumler = [
                                AdapterBolum(
                                    ep_id,   # "anime_slug/bolum_slug" bileşik kimlik
                                    ep_title,
                                    adapter,
                                    stream_provider=lambda url, _es=ep_id: animedepo_stream_provider(_es),
                                    player_name="ANIMEDEPO"
                                )
                                for ep_id, ep_title in animedepo_episodes_data
                            ]
                        elif anime is not None:
                            bolumler = anime.bolumler
                        else:
                            bolumler = []
                        if not bolumler:
                            rprint("[red]Bölüm bulunamadı.[/red]")
                            break
                        choices, recent = eps_to_choices(bolumler, mark_type="izlendi")
                    bolum = qa.select(
                        message='Bölüm seç', choices=choices, style=prompt_tema, default=recent,
                        instruction="Yukarı/Aşağı • Enter"
                    ).ask(kbi_msg="")
                    if not bolum:
                        break
                    fansubs, sub = getattr(bolum, 'fansubs', []), None
                    if dosya.ayarlar["manuel fansub"] and len(fansubs) > 1:
                        sub = qa.select(
                            message='Fansub seç', choices=fansubs, style=prompt_tema,
                            instruction="Yukarı/Aşağı • Enter"
                        ).ask(kbi_msg="")
                        if not sub:
                            break
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
                else:
                    if source == "animecix" and cix_anime is not None:
                        adapter = adapter_anime or AdapterAnime(slug=str(cix_anime.id), title=cix_anime.title)
                        bolum_kayitlari = [AdapterBolum(e.url, e.title, adapter) for e in cix_anime.episodes]
                        choices, recent = eps_to_choices(bolum_kayitlari, mark_type="indirildi")
                    elif source == "anizle" and anizle_anime is not None and anizle_stream_provider:
                        adapter = adapter_anime or AdapterAnime(slug=anizle_anime.slug, title=anizle_anime.title)
                        bolum_kayitlari = [
                            AdapterBolum(
                                e.url,
                                e.title,
                                adapter,
                                stream_provider=anizle_stream_provider,
                                player_name="ANIZLE"
                            )
                            for e in anizle_anime.episodes
                        ]
                        choices, recent = eps_to_choices(bolum_kayitlari, mark_type="indirildi")
                    elif source == "tranimeizle" and tranime_episodes_data is not None and tranime_stream_provider:
                        adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                        bolum_kayitlari = [
                            AdapterBolum(
                                e.slug,
                                e.title,
                                adapter,
                                stream_provider=tranime_stream_provider(e.slug),
                                player_name="TRANIME"
                            )
                            for e in tranime_episodes_data
                        ]
                        choices, recent = eps_to_choices(bolum_kayitlari, mark_type="indirildi")
                    elif source == "openanime" and openani_episodes_data is not None and openani_stream_provider:
                        adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                        bolum_kayitlari = [
                            AdapterBolum(
                                f"https://openani.me/anime/{ep_slug}",
                                ep_title,
                                adapter,
                                stream_provider=lambda url, _es=ep_slug: openani_stream_provider(_es),
                                player_name="OPENANI"
                            )
                            for ep_slug, ep_title in openani_episodes_data
                        ]
                        choices, recent = eps_to_choices(bolum_kayitlari, mark_type="indirildi")
                    elif source == "tranimaci" and tranimaci_episodes_data is not None and tranimaci_stream_provider:
                        adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                        bolum_kayitlari = [
                            AdapterBolum(
                                f"https://tranimaci.com/video/{ep_slug}",
                                ep_title,
                                adapter,
                                stream_provider=lambda url, _es=ep_slug: tranimaci_stream_provider(_es),
                                player_name="TRANIMACI"
                            )
                            for ep_slug, ep_title in tranimaci_episodes_data
                        ]
                        choices, recent = eps_to_choices(bolum_kayitlari, mark_type="indirildi")
                    elif source == "animedepo" and animedepo_episodes_data is not None and animedepo_stream_provider:
                        adapter = adapter_anime or AdapterAnime(slug=seri_slug, title=seri_ismi)
                        bolum_kayitlari = [
                            AdapterBolum(
                                ep_id,   # "anime_slug/bolum_slug" bileşik kimlik
                                ep_title,
                                adapter,
                                stream_provider=lambda url, _es=ep_id: animedepo_stream_provider(_es),
                                player_name="ANIMEDEPO"
                            )
                            for ep_id, ep_title in animedepo_episodes_data
                        ]
                        choices, recent = eps_to_choices(bolum_kayitlari, mark_type="indirildi")
                    elif anime is not None:
                        choices, recent = eps_to_choices(anime.bolumler, mark_type="indirildi")
                    else:
                        choices, recent = ([], None)

                    if not choices:
                        rprint("[red]Bölüm bulunamadı.[/red]")
                        break

                    if len(choices) > 10:
                        filt = qa.text("Bölüm ara/filtre (boş geçilebilir)", style=prompt_tema).ask(kbi_msg="")
                        if filt:
                            choices = [c for c in choices if filt.lower() in str(c.title).lower()]

                    bolumler = qa.checkbox(
                        message="Bölüm seç",
                        choices=choices,
                        style=prompt_tema,
                        initial_choice=recent,
                        instruction="Boşluk: seç • a: tümünü değiştir • i: tersine çevir • Enter: onayla"
                    ).ask(kbi_msg="")
                    if not bolumler:
                        break
                    table = Table.grid(expand=False)
                    with Live(table, refresh_per_second=10, vertical_overflow="visible"):
                        futures = []
                        paralel = dosya.ayarlar.get("paralel indirme sayisi")
                        with cf.ThreadPoolExecutor(max_workers=paralel) as executor:
                            for bolum in bolumler:
                                futures.append(executor.submit(
                                    indirme_task_cli, bolum, table, dosya
                                ))
                            cf.wait(futures)

        elif islem == "Kaynak seç":
            ds = Dosyalar()
            kay = _norm_source(ds.ayarlar.get("kaynak", "turkanime"))
            # Questionary sürümleri arasında Choice(name,value) ile default eşleşmesi sorun çıkarabiliyor.
            # Bu yüzden düz string seçenekler kullanıp başlıktan koda map ediyoruz.
            # Liste SOURCE_TITLES'tan türetiliyor: elle yazılan kopya ayrışıyordu
            # (AnimeDepo eklendiğinde burada görünmediği için seçilemiyordu).
            secenekler = list(SOURCE_TITLES.values())
            varsayilan = _source_title(kay)
            sec_title = qa.select(
                "Kaynak seç",
                choices=secenekler,
                default=varsayilan,
                style=prompt_tema,
                instruction="Yukarı/Aşağı • Enter",
            ).ask()
            if sec_title:
                sec = _norm_source(sec_title)
                ds.set_ayar("kaynak", sec)

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


def main():
    # Donmuş (PyInstaller) CLI exe'si de CF çözücüsünün giriş noktasıdır:
    # `cf_bypass._get_qt_solver`, donmuş modda `sys.executable --cf-qt-solver`
    # ile UYGULAMANIN KENDİSİNİ çağırıyor. CLI bu bayrağı tanımadığı sürece o
    # çağrı etkileşimli menüyü açıyor, çözücü el sıkışması bozuk çıkıyor ve
    # açılan süreç yetim kalıyordu — her CF challenge'ında bir tane.
    if SOLVER_FLAG in sys.argv:
        from ..common.cf_qt_solver import main as solver_main
        return solver_main()

    # Güncelleme kontrolü
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
        rprint("[red][strong]Güncelleme kontrol edilemedi.[/strong][red]")
        sleep(3)

    # Gereksinimleri kontrol et (embed edilmiş araçlar kullanılıyor)
    # gereksinim_kontrol_cli()

    # Script kapanışında
    def kapat():
        with CliStatus("Kapatılıyor.."):
            sleep(1.5)
    atexit.register(kapat)

    # Türkanime'ye bağlan
    try:
        with CliStatus("Türkanime'ye bağlanılıyor.."):
            _ = fetch("/")  # Create Session
    except (ConnectionError, AssertionError) as e:
        log_error(e)
        rprint("[red][strong]TürkAnime'ye ulaşılamıyor.[/strong][red]")
        sys.exit(1)

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
