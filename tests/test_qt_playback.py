"""Oynatma paritesi: ayarların `oynat()`a gitmesi, izleme geçmişi, ilerleme.

Qt tarafı `oynat()`ı hep argümansız çağırıyordu — "dakika hatirla" ve
"izlerken kaydet" ayarları kaydediliyor ama hiç kullanılmıyordu. Ayrıca
oynatma bitince ne geçmişe yazılıyor ne de ilerleme soruluyordu.

Gerçek mpv çalıştırılmaz: sahte `Video` nesneleri çağrıyı kaydeder.
"""
from __future__ import annotations

import pytest

from turkanime_api.gui.qt import prefs
from turkanime_api.gui.qt.pages.episodes import EpisodePage, EpisodeRow
from turkanime_api.gui.qt.progress_dialog import ProgressDialog


class SahteAnime:
    def __init__(self, slug="naruto-test", title="Naruto Test"):
        self.slug = slug
        self.title = title


class SahteBolum:
    def __init__(self, video=None, slug="naruto-test-1-bolum"):
        self.slug = slug
        self.anime = SahteAnime()
        self.video = video
        self.best_kwargs = None

    def best_video(self, **kwargs):
        self.best_kwargs = kwargs
        return self.video


class TamVideo:
    """`objects.Video.oynat` imzası: dakika_hatirla + izlerken_kaydet + mpv_opts."""

    def __init__(self):
        self.kwargs = None

    def oynat(self, dakika_hatirla=False, izlerken_kaydet=False, mpv_opts=None):
        self.kwargs = {"dakika_hatirla": dakika_hatirla,
                       "izlerken_kaydet": izlerken_kaydet}
        return "proc"


class AdapterTarziVideo:
    """`sources.adapter.AdapterVideo.oynat` imzası: yalnızca dakika_hatirla."""

    def __init__(self):
        self.kwargs = None

    def oynat(self, dakika_hatirla=False):
        self.kwargs = {"dakika_hatirla": dakika_hatirla}
        return "proc"


# ── Ayarlar oynat()a gidiyor mu? ─────────────────────────────────────────────
def test_dakika_hatirla_oynata_gidiyor(ayarla):
    ayarla(**{"dakika hatirla": True, "izlerken kaydet": False})
    video = TamVideo()
    prefs.oynat(video)
    assert video.kwargs == {"dakika_hatirla": True, "izlerken_kaydet": False}

    ayarla(**{"dakika hatirla": False, "izlerken kaydet": True})
    prefs.oynat(video)
    assert video.kwargs == {"dakika_hatirla": False, "izlerken_kaydet": True}


def test_desteklenmeyen_argüman_gecilmiyor(ayarla):
    """`AdapterVideo.oynat` `izlerken_kaydet` almıyor; geçmek TypeError olurdu."""
    ayarla(**{"dakika hatirla": True, "izlerken kaydet": True})
    video = AdapterTarziVideo()
    prefs.oynat(video)
    assert video.kwargs == {"dakika_hatirla": True}


def test_mpv_opts_varsayilani_birikmiyor(monkeypatch):
    """`objects.Video.oynat` eskiden PAYLAŞILAN varsayılan listeye yazıyordu.

    "dakika hatirla" artık gerçekten geçtiği için bu hata görünür hâle geldi:
    ikinci oynatmada mpv aynı bayrağı iki kez alıyordu.
    """
    import turkanime_api.objects as objects_mod
    from turkanime_api.objects import Video

    kaydedilen = []

    class SahteInfo(Video):
        def __init__(self):  # pylint: disable=super-init-not-called
            self.is_supported = True
            self.is_working = True
            self._url = "https://ornek/video.mp4"
            self._info = {}
            self.bolum = SahteBolum()

    monkeypatch.setattr(objects_mod.sp, "run",
                        lambda cmd, **k: kaydedilen.append(list(cmd)))
    vid = SahteInfo()
    vid.oynat(dakika_hatirla=True)
    vid.oynat(dakika_hatirla=True)

    assert kaydedilen[0].count("--save-position-on-quit") == 1
    assert kaydedilen[1].count("--save-position-on-quit") == 1


# ── Ana pencere akışı ────────────────────────────────────────────────────────
def test_oynatma_ayarlari_ve_gecmis(main_window, qtbot, monkeypatch, ayarla,
                                    preserved_gecmis):
    """Oynatma bitince geçmişe yazılmalı ve ilerleme diyaloğu açılmalı."""
    from turkanime_api.cli.dosyalar import Dosyalar

    ayarla(**{"max resolution": False, "1080p aday sayısı": 4,
              "dakika hatirla": True, "izlerken kaydet": False})

    acilan = []
    monkeypatch.setattr(ProgressDialog, "exec",
                        lambda self: acilan.append(self) or 0)

    video = TamVideo()
    bolum = SahteBolum(video=video, slug="naruto-test-3-bolum")
    main_window._on_play({"title": "Naruto Test 3. Bölüm", "obj": bolum})

    qtbot.waitUntil(lambda: bool(acilan), timeout=10000)

    assert bolum.best_kwargs == {"by_res": False, "early_subset": 4}
    assert video.kwargs == {"dakika_hatirla": True, "izlerken_kaydet": False}
    izlendi = Dosyalar().gecmis["izlendi"]
    assert "naruto-test-3-bolum" in izlendi.get("naruto-test", [])
    assert acilan[0].spnEpisode.value() == 3, "bölüm numarası başlıktan gelmeli"


def test_oynatilamayan_bolum_dialog_acmiyor(main_window, qtbot, monkeypatch, ayarla):
    """Video bulunamadıysa ilerleme sorulmamalı."""
    ayarla(**{"dakika hatirla": True})
    acilan = []
    monkeypatch.setattr(ProgressDialog, "exec",
                        lambda self: acilan.append(self) or 0)

    bolum = SahteBolum(video=None)
    main_window._on_play({"title": "Naruto Test 4. Bölüm", "obj": bolum})

    qtbot.waitUntil(lambda: main_window._playing is False, timeout=10000)
    qtbot.wait(150)
    assert acilan == []


# ── İlerleme diyaloğu ────────────────────────────────────────────────────────
def test_ilerleme_dialogu_yerel_ilerlemeye_yaziyor(qtbot, preserved_gecmis):
    from turkanime_api.cli.dosyalar import Dosyalar

    dialog = ProgressDialog(SahteBolum(), "Naruto Test 12. Bölüm")
    qtbot.addWidget(dialog)
    assert dialog.spnEpisode.value() == 12

    yayilan = []
    dialog.progress_saved.connect(lambda seri, no: yayilan.append((seri, no)))
    dialog.spnEpisode.setValue(13)
    dialog.save()

    assert yayilan == [("naruto-test", 13)]
    assert Dosyalar().gecmis["ilerleme"]["naruto-test"] == 13


@pytest.mark.parametrize("anime_baslik, anime_slug, bolum_adi, beklenen", [
    ("86 2nd Season", "86-2nd-season", "86 2nd Season 5. Bölüm", 5),
    ("3x3 Eyes", "3x3-eyes", "3x3 Eyes 1. Bölüm", 1),
    ("5-toubun no Hanayome", "5-toubun-no-hanayome", "5-toubun no Hanayome 3. Bölüm", 3),
])
def test_ilerleme_dialogu_addaki_rakami_bolum_sanmiyor(
        qtbot, anime_baslik, anime_slug, bolum_adi, beklenen):
    """Başlık anime adını da taşıyor; addaki rakam AniList'e ilerleme yazılıyordu.

    12 bölümlük "86 2nd Season"da diyalog 86 öneriyordu; kullanıcı "Kaydet"e
    basınca hesaba o numara işleniyordu.
    """
    bolum = SahteBolum(slug=f"{anime_slug}-bolum")
    bolum.anime = SahteAnime(slug=anime_slug, title=anime_baslik)

    dialog = ProgressDialog(bolum, bolum_adi)
    qtbot.addWidget(dialog)
    assert dialog.spnEpisode.value() == beklenen


def test_ilerleme_dialogu_seri_kimligi_yoksa_kaydetmiyor(qtbot):
    class KimliksizBolum:
        slug = "bilinmeyen-1-bolum"
        anime = None

    dialog = ProgressDialog(KimliksizBolum(), "Bilinmeyen 1. Bölüm")
    qtbot.addWidget(dialog)
    assert not dialog.btnSave.isEnabled()
    assert dialog.lblNote.text()


def test_ilerleme_kaydet_bos_seriyi_reddediyor(preserved_gecmis):
    assert prefs.ilerleme_kaydet("", 5) is False


# ── İzlendi / indirildi rozetleri ────────────────────────────────────────────
class SahteGecmis:
    def __init__(self, izlendi=(), indirildi=()):
        self._izlendi = set(izlendi)
        self._indirildi = set(indirildi)

    def durum(self, bolum):
        slug = getattr(bolum, "slug", "")
        return (slug in self._izlendi, slug in self._indirildi)


def _episode(bolum, title="1. Bölüm", source="TürkAnime"):
    return {"season": 1, "number": 1, "title": title,
            "sources": {source: {"title": title, "obj": bolum}}}


def test_satirda_izlendi_ve_indirildi_rozeti(qtbot):
    bolum = SahteBolum()
    row = EpisodeRow(_episode(bolum),
                     gecmis=SahteGecmis(izlendi=[bolum.slug], indirildi=[bolum.slug]))
    qtbot.addWidget(row)

    assert row.izlendi and row.indirildi
    assert row.lblHistory.isVisibleTo(row)
    assert "izlendi" in row.lblHistory.toolTip()


def test_satirda_gecmis_yoksa_rozet_gizli(qtbot):
    row = EpisodeRow(_episode(SahteBolum()), gecmis=SahteGecmis())
    qtbot.addWidget(row)
    assert not row.izlendi and not row.indirildi
    assert not row.lblHistory.isVisibleTo(row)


def test_ikon_ayari_kapaliyken_gecmis_okunmuyor(qtbot, ayarla):
    ayarla(**{"izlendi ikonu": False})
    page = EpisodePage()
    qtbot.addWidget(page)
    page.load("TürkAnime", "naruto-test", "Naruto Test",
              episodes=[{"title": "1. Bölüm", "obj": SahteBolum()}])
    assert page._gecmis is None
    assert not page._rows[0].lblHistory.isVisibleTo(page._rows[0])


def test_liste_gercek_gecmisi_okuyor(qtbot, izole_ev):
    """`gecmis.json`'daki kayıt satır rozetine yansımalı.

    `izole_ev` şart: aşağıdaki "indirildi False" iddiası dosyanın BOŞ
    başlamasına dayanıyor, kullanıcının gerçek geçmişinde aynı bölüm varsa
    test yalancı kırmızı veriyordu.
    """
    from turkanime_api.cli.dosyalar import Dosyalar

    Dosyalar().set_ayar("izlendi ikonu", True)
    Dosyalar().set_gecmis("naruto-test", "naruto-test-1-bolum", "izlendi")

    page = EpisodePage()
    qtbot.addWidget(page)
    page.load("TürkAnime", "naruto-test", "Naruto Test",
              episodes=[{"title": "1. Bölüm", "obj": SahteBolum()}])

    assert page._rows[0].izlendi is True
    assert page._rows[0].indirildi is False

    Dosyalar().set_gecmis("naruto-test", "naruto-test-1-bolum", "indirildi")
    page.refresh_history()
    assert page._rows[0].indirildi is True
