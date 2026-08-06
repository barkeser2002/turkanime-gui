"""Arşiv yayıncısı testleri (Faz 12).

**Hiçbiri ağa çıkmaz.** Uzak depo `tmp_path` içinde `git init --bare` ile
kurulan yerel bir depodur; `git push` onu dosya sistemi üzerinden görür. Gerçek
bir uzağa (GitLab/GitHub) tek bir istek bile gitmez.

En değerli test `test_uctan_uca_crawler_yayinci_klon`: tarayıcı çıktısını
yayınlar, sonucu `git clone` ile geri alır ve istemcinin gerçek
`sources/animedepo.py` modülüne okutur. Zincirin herhangi bir halkası kayarsa
burası kırmızı olur.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from turkanime_server.yayinci.ayarlar import YayinAyarlari, ortamdan
from turkanime_server.yayinci.depo import ZORLA_BAYRAGI, GitDepo, GitHatasi, gizle
from turkanime_server.yayinci.mesaj import Degisim, mesaj_uret
from turkanime_server.yayinci.yayinci import Yayinci

pytestmark = pytest.mark.skipif(not GitDepo.git_var_mi(),
                                reason="`git` kurulu değil")


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────
def git(*argv: str, cwd: Path) -> str:
    """Test tarafında git çalıştır (yayıncının sarmalayıcısından bağımsız)."""
    sonuc = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *argv], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True,
    )
    return sonuc.stdout


def bare_depo(tmp_path: Path, ad: str = "uzak.git") -> Path:
    """Yerel bare depo — testlerdeki 'uzak'."""
    yol = tmp_path / ad
    yol.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "--quiet", str(yol)], check=True)
    return yol


def arsiv_kur(kok: Path, animeler: Dict[str, List[Tuple[str, str]]]) -> Path:
    """AnimeDepo şemasında küçük bir arşiv yaz.

    ``animeler``: {slug: [(bolum_slug, url), ...]}
    """
    kok.mkdir(parents=True, exist_ok=True)
    index: Dict[str, Dict[str, Dict[str, str]]] = {}
    for slug, bolumler in animeler.items():
        baslik = slug.replace("-", " ").title()
        index.setdefault(baslik[0].upper(), {})[slug] = {"title": baslik}
        klasor = kok / "animeler" / slug
        klasor.mkdir(parents=True, exist_ok=True)
        _yaz(klasor / "info.json", {"slug": slug, "title": baslik,
                                    "bolum_sayisi": len(bolumler)})
        _yaz(klasor / "bolumler.json",
             [[ep, f"{i + 1}. Bölüm"] for i, (ep, _) in enumerate(bolumler)])
        for ep, url in bolumler:
            _yaz(klasor / f"{ep}.json",
                 [{"url": url, "player": "ANIZLE", "fansub": "720p", "alive": True}])
    _yaz(kok / "dizin.json", {"surum": 1, "adet": len(animeler), "index": index})
    return kok


def _yaz(yol: Path, veri) -> None:
    yol.write_text(json.dumps(veri, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")


def _ayarlar(arsiv: Path, uzak: Optional[Path] = None, **ek) -> YayinAyarlari:
    varsayilan = dict(arsiv=arsiv, uzak=str(uzak) if uzak else None,
                      dal="master", azami_gecmis=0, bakim_araligi=0)
    varsayilan.update(ek)
    return YayinAyarlari(**varsayilan)


# ─────────────────────────────────────────────────────────────────────────────
# 1) Temel yayın döngüsü
# ─────────────────────────────────────────────────────────────────────────────
def test_ilk_yayin_dosyalari_isliyor(tmp_path):
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    uzak = bare_depo(tmp_path)

    sonuc = Yayinci(_ayarlar(arsiv, uzak)).yayinla()

    assert sonuc.yayinlandi and sonuc.sha
    assert sonuc.itildi
    assert sonuc.mesaj.startswith("arşiv: +1 anime")

    izlenen = git("ls-tree", "-r", "--name-only", "HEAD", cwd=arsiv).split()
    assert "dizin.json" in izlenen
    assert "animeler/naruto/info.json" in izlenen
    assert "animeler/naruto/s01e01.json" in izlenen
    # Uzak da gerçekten aldı
    assert git("rev-parse", "master", cwd=uzak).strip() == sonuc.sha


def test_degisiklik_yoksa_commit_atilmiyor(tmp_path):
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    uzak = bare_depo(tmp_path)
    ayarlar = _ayarlar(arsiv, uzak)

    ilk = Yayinci(ayarlar).yayinla()
    assert ilk.yayinlandi
    onceki_sayi = int(git("rev-list", "--count", "HEAD", cwd=arsiv))

    ikinci = Yayinci(ayarlar).yayinla()

    assert ikinci.yayinlandi is False
    assert ikinci.sebep == "degisiklik-yok"
    assert ikinci.sha is None
    assert int(git("rev-list", "--count", "HEAD", cwd=arsiv)) == onceki_sayi, \
        "değişiklik yokken boş commit atıldı"


def test_kismi_degisiklik_yalnizca_degisen_dosyalari_commitliyor(tmp_path):
    arsiv = arsiv_kur(tmp_path / "arsiv", {
        "naruto": [("s01e01", "https://x.test/1.mp4"),
                   ("s01e02", "https://x.test/2.mp4")],
        "bleach": [("s01e01", "https://x.test/b1.mp4")],
    })
    ayarlar = _ayarlar(arsiv, bare_depo(tmp_path))
    Yayinci(ayarlar).yayinla()

    # Tek bölümün akışı değişti + yepyeni bir anime eklendi
    _yaz(arsiv / "animeler" / "naruto" / "s01e02.json",
         [{"url": "https://x.test/2-yeni.mp4", "player": "ANIZLE", "alive": True}])
    arsiv_kur(arsiv, {
        "naruto": [("s01e01", "https://x.test/1.mp4"),
                   ("s01e02", "https://x.test/2-yeni.mp4")],
        "bleach": [("s01e01", "https://x.test/b1.mp4")],
        "one-piece": [("s01e01", "https://x.test/op1.mp4")],
    })

    sonuc = Yayinci(ayarlar).yayinla()
    assert sonuc.yayinlandi

    dosyalar = set(git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD",
                       cwd=arsiv).split())
    assert dosyalar == {
        "dizin.json",
        "animeler/naruto/s01e02.json",
        "animeler/one-piece/info.json",
        "animeler/one-piece/bolumler.json",
        "animeler/one-piece/s01e01.json",
    }, f"dokunulmamış dosyalar da commit'e girdi: {dosyalar}"

    baslik = sonuc.mesaj.splitlines()[0]
    assert baslik == "arşiv: +1 anime, +1 bölüm, ~1 bölüm güncellendi", baslik
    assert "Toplam: 3 anime" in sonuc.mesaj


def test_gecici_yazim_artiklari_commite_girmiyor(tmp_path):
    """Yazıcının atomik yazım artığı (`.x.json.123.tmp`) arşive işlenmemeli."""
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    ayarlar = _ayarlar(arsiv)
    Yayinci(ayarlar).yayinla()

    (arsiv / "animeler" / "naruto" / ".s01e02.json.4242.tmp").write_text("yarim", "utf-8")
    sonuc = Yayinci(ayarlar).yayinla()

    assert sonuc.yayinlandi is False
    assert "tmp" not in git("ls-files", cwd=arsiv)


# ─────────────────────────────────────────────────────────────────────────────
# 2) Commit mesajı (saf mantık)
# ─────────────────────────────────────────────────────────────────────────────
def test_mesaj_sayilari_dogru_veriyor():
    degisim = Degisim(
        eklenen=tuple(f"animeler/yeni-{i}/info.json" for i in range(12))
        + tuple(f"animeler/yeni-{i}/bolumler.json" for i in range(12)),
        degisen=("dizin.json",)
        + tuple(f"animeler/eski/s01e{i:02d}.json" for i in range(1, 341)),
    )
    baslik = mesaj_uret(degisim, toplam_anime=5123).splitlines()[0]
    assert baslik == "arşiv: +12 anime, ~340 bölüm güncellendi"

    gövde = mesaj_uret(degisim, toplam_anime=5123)
    assert "Anime: 12 yeni, 1 güncellendi" in gövde
    assert "Toplam: 5123 anime" in gövde


def test_mesaj_yalnizca_dizin_degisince():
    assert mesaj_uret(Degisim(degisen=("dizin.json",))).splitlines()[0] \
        == "arşiv: dizin güncellendi"


def test_mesaj_silinen_anime_sayiyor():
    degisim = Degisim(silinen=("animeler/olen/info.json",
                               "animeler/olen/bolumler.json",
                               "animeler/olen/s01e01.json"))
    assert degisim.silinen_animeler == {"olen"}
    assert mesaj_uret(degisim).splitlines()[0] == "arşiv: -1 anime"


# ─────────────────────────────────────────────────────────────────────────────
# 3) Gizli bilgi
# ─────────────────────────────────────────────────────────────────────────────
def test_token_commite_calisma_agacina_ve_loga_sizmiyor(tmp_path, caplog):
    gizli = "glpat-COKGIZLI1234567890"
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    uzak = bare_depo(tmp_path)
    ayarlar = _ayarlar(arsiv, uzak, token=gizli, kullanici="deploy")

    with caplog.at_level("DEBUG"):
        sonuc = Yayinci(ayarlar).yayinla()
    assert sonuc.yayinlandi

    # 1) Depo içeriğinde (commit'lenmiş ağaç + .git/config) yok
    assert gizli not in git("log", "-p", "--all", cwd=arsiv)
    assert gizli not in (arsiv / ".git" / "config").read_text("utf-8")
    for p in arsiv.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            assert gizli not in p.read_text("utf-8", errors="replace")

    # 2) Log'da yok
    assert gizli not in caplog.text

    # 3) Ayar nesnesinin repr'inde yok (istisna izleri ayarları basar)
    assert gizli not in repr(ayarlar)


def test_gizle_url_kimligini_maskeliyor():
    assert gizle("https://oauth2:glpat-abcdef@gitlab.com/x.git") \
        == "https://***@gitlab.com/x.git"
    assert gizle("hata: glpat-uzunbirtoken reddedildi", "glpat-uzunbirtoken") \
        == "hata: *** reddedildi"
    # Kısa değerler sır sayılmaz; maskelenirse çıktı okunmaz hâle gelirdi
    assert gizle("abc reddedildi", "abc") == "abc reddedildi"


def test_ayarlar_sirlari_yalnizca_ortamdan_okuyor():
    ortam = {
        "TURKANIME_ARSIV_UZAK": "https://ornek.test/arsiv.git",
        "TURKANIME_ARSIV_TOKEN": "glpat-ORTAMDAN",
        "TURKANIME_ARSIV_DAL": "yayin",
        "TURKANIME_ARSIV_AZAMI_GECMIS": "7",
        "TURKANIME_ARSIV_EPOSTA": "",          # boş = ayarlanmamış
    }
    ayarlar = ortamdan(ortam)
    assert ayarlar.uzak == "https://ornek.test/arsiv.git"
    assert ayarlar.token == "glpat-ORTAMDAN"
    assert ayarlar.dal == "yayin"
    assert ayarlar.azami_gecmis == 7
    assert ayarlar.eposta == YayinAyarlari().eposta
    assert "glpat-ORTAMDAN" not in repr(ayarlar)


def test_cli_token_bayragi_sunmuyor():
    """`--token` argümanı olmamalı: argümanlar `ps` ve shell geçmişinde görünür."""
    from turkanime_server.yayinci import __main__ as cli

    with pytest.raises(SystemExit):
        cli.ayristir(["--token", "glpat-x"])


# ─────────────────────────────────────────────────────────────────────────────
# 4) Depo şişmesi önlemleri
# ─────────────────────────────────────────────────────────────────────────────
def test_gecmis_azami_asilinca_tek_koke_indiriliyor(tmp_path):
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    uzak = bare_depo(tmp_path)
    ayarlar = _ayarlar(arsiv, uzak, azami_gecmis=3)

    # 1..3 birikir (eşik aşılmaz), 4. tur eşiği aşar ve geçmişi tek köke indirir
    sonuclar = []
    for n in range(1, 5):
        _yaz(arsiv / "animeler" / "naruto" / "s01e01.json",
             [{"url": f"https://x.test/{n}.mp4", "alive": True}])
        sonuc = Yayinci(ayarlar).yayinla()
        assert sonuc.yayinlandi, f"{n}. tur commit atmadı"
        sonuclar.append(sonuc)

    assert [s.budandi for s in sonuclar] == [False, False, False, True]
    assert int(git("rev-list", "--count", "HEAD", cwd=arsiv)) == 1
    # Ağaç bozulmadı ve uzak da zorla güncellendi
    assert (arsiv / "dizin.json").is_file()
    assert git("rev-parse", "master", cwd=uzak).strip() \
        == git("rev-parse", "HEAD", cwd=arsiv).strip()
    icerik = json.loads(git("show", "HEAD:animeler/naruto/s01e01.json", cwd=arsiv))
    assert icerik[0]["url"] == "https://x.test/4.mp4"

    # Budamadan sonra depo çalışmaya devam ediyor
    _yaz(arsiv / "animeler" / "naruto" / "s01e01.json",
         [{"url": "https://x.test/5.mp4", "alive": True}])
    devam = Yayinci(ayarlar).yayinla()
    assert devam.yayinlandi and not devam.budandi
    assert int(git("rev-list", "--count", "HEAD", cwd=arsiv)) == 2


def test_budama_turunda_itme_coktuyse_sonraki_tur_toparliyor(tmp_path):
    """Budama + başarısız itme, uzak arşivi kalıcı olarak dondurmamalı.

    Senaryo: `azami_gecmis=4`, 5. turda geçmiş tek köke indirildi ve tam o
    turda uzak erişilemez oldu. Yerel geçmiş yeniden yazıldığı için 6. turdaki
    düz itme "! [rejected]" alır; bilgi yalnızca bellekte tutulursa yayıncı bir
    daha asla itemez — yerel commit sayısı artarken uzak son yayınlanan hâlde
    donar.
    """
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    uzak = bare_depo(tmp_path)
    ayarlar = _ayarlar(arsiv, uzak, azami_gecmis=4)

    def tur(n: int):
        _yaz(arsiv / "animeler" / "naruto" / "s01e01.json",
             [{"url": f"https://x.test/{n}.mp4", "alive": True}])

    for n in range(1, 5):
        tur(n)
        assert Yayinci(ayarlar).yayinla().yayinlandi, f"{n}. tur commit atmadı"
    assert int(git("rev-list", "--count", "HEAD", cwd=arsiv)) == 4

    # 5. tur: budama tetiklenir, ama uzak tam o anda erişilemez
    tur(5)
    kopuk = _ayarlar(arsiv, tmp_path / "erisilemez.git", azami_gecmis=4)
    with pytest.raises(GitHatasi):
        Yayinci(kopuk).yayinla()
    assert int(git("rev-list", "--count", "HEAD", cwd=arsiv)) == 1, \
        "budama gerçekleşmiş olmalı (senaryo bunu gerektiriyor)"
    assert git("rev-parse", "master", cwd=uzak).strip() \
        != git("rev-parse", "HEAD", cwd=arsiv).strip()

    # 6. tur: uzak geri geldi. Yayıncı geçmişi kendisinin yeniden yazdığını
    # hatırlamalı ve uzağı toparlamalı.
    tur(6)
    sonuc = Yayinci(ayarlar).yayinla()
    assert sonuc.yayinlandi and sonuc.itildi
    assert git("rev-parse", "master", cwd=uzak).strip() \
        == git("rev-parse", "HEAD", cwd=arsiv).strip(), \
        "uzak arşiv donmuş durumda kaldı"
    icerik = json.loads(git("show", "master:animeler/naruto/s01e01.json", cwd=uzak))
    assert icerik[0]["url"] == "https://x.test/6.mp4"

    # 7. tur normale dönmeli: bayrak temizlendiği için zorla itme denenmez
    tur(7)
    yedinci = Yayinci(ayarlar).yayinla()
    assert yedinci.itildi and not yedinci.budandi
    assert not (arsiv / ".git" / ZORLA_BAYRAGI).exists(), \
        "başarılı itmeden sonra zorla itme bayrağı kalmamalı"


def test_uzaktaki_yabanci_commit_zorla_itilip_silinmiyor(tmp_path):
    """Ayrışmanın sebebi biz değilsek zorla itme veri kaybettirir.

    Uzağa başkası (elle düzeltme, ikinci bir sunucu) commit atmışsa yayıncı
    onun üstüne yeniden işlemeli; `--force` o commit'i uçururdu.
    """
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    uzak = bare_depo(tmp_path)
    ayarlar = _ayarlar(arsiv, uzak)                 # azami_gecmis=0 → budama yok
    assert Yayinci(ayarlar).yayinla().itildi

    klon = tmp_path / "klon"
    subprocess.run(["git", "clone", "--quiet", str(uzak), str(klon)], check=True)
    (klon / "NOTLAR.md").write_text("elle eklendi\n", encoding="utf-8")
    git("add", "-A", cwd=klon)
    git("-c", "user.name=biri", "-c", "user.email=biri@ornek.test",
        "commit", "--quiet", "-m", "yabanci commit", cwd=klon)
    yabanci = git("rev-parse", "HEAD", cwd=klon).strip()
    git("push", "--quiet", "origin", "HEAD:refs/heads/master", cwd=klon)

    _yaz(arsiv / "animeler" / "naruto" / "s01e01.json",
         [{"url": "https://x.test/2.mp4", "alive": True}])
    sonuc = Yayinci(ayarlar).yayinla()

    assert sonuc.yayinlandi and sonuc.itildi
    assert yabanci in git("rev-list", "master", cwd=uzak).split(), \
        "uzaktaki yabancı commit zorla itmeyle silindi"
    icerik = json.loads(git("show", "master:animeler/naruto/s01e01.json", cwd=uzak))
    assert icerik[0]["url"] == "https://x.test/2.mp4", "arşiv güncellenmedi"


def test_gitattributes_satir_sonu_donusumunu_kapatiyor(tmp_path):
    """`core.autocrlf=true` olan makinede arşiv sahte değişikliğe boğulmamalı."""
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    Yayinci(_ayarlar(arsiv)).yayinla()

    assert "* -text" in (arsiv / ".gitattributes").read_text("utf-8")
    # autocrlf açıkken bile ağaç temiz kalmalı
    kirli = subprocess.run(
        ["git", "-c", "core.autocrlf=true", "status", "--porcelain"],
        cwd=str(arsiv), capture_output=True, text=True, check=True).stdout
    assert kirli.strip() == "", f"satır sonu dönüşümü sahte fark üretti: {kirli}"


# ─────────────────────────────────────────────────────────────────────────────
# 5) Güvenlik freni + kuru koşu
# ─────────────────────────────────────────────────────────────────────────────
def test_arsiv_bosalirsa_yayin_duruyor(tmp_path):
    """Boş bağlanmış birim yayınlanmış arşivi tek commit'te silmemeli."""
    arsiv = arsiv_kur(tmp_path / "arsiv", {
        f"anime-{i}": [("s01e01", f"https://x.test/{i}.mp4")] for i in range(5)
    })
    ayarlar = _ayarlar(arsiv, bare_depo(tmp_path))
    ilk = Yayinci(ayarlar).yayinla()

    for slug in ("anime-0", "anime-1", "anime-2", "anime-3"):
        for p in (arsiv / "animeler" / slug).iterdir():
            p.unlink()
        (arsiv / "animeler" / slug).rmdir()

    sonuc = Yayinci(ayarlar).yayinla()
    assert sonuc.yayinlandi is False
    assert sonuc.sebep == "toplu-silme"
    assert git("rev-parse", "HEAD", cwd=arsiv).strip() == ilk.sha


def test_dizin_yoksa_yayin_reddediliyor(tmp_path):
    bos = tmp_path / "arsiv"
    bos.mkdir()
    sonuc = Yayinci(_ayarlar(bos)).yayinla()
    assert sonuc.sebep == "arsiv-eksik"
    assert not (bos / ".git").exists(), "reddedilen yayın depo kurmamalı"


def test_kuru_calistir_hicbir_sey_yazmiyor(tmp_path):
    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    sonuc = Yayinci(_ayarlar(arsiv, kuru_calistir=True)).yayinla()

    assert sonuc.yayinlandi is False
    assert sonuc.sebep == "kuru"
    assert sonuc.mesaj.startswith("arşiv: +1 anime")
    assert not (arsiv / ".git").exists(), "kuru koşu `git init` bile yapmamalı"


def test_cli_kuru_calistir(tmp_path, capsys):
    from turkanime_server.yayinci import __main__ as cli

    arsiv = arsiv_kur(tmp_path / "arsiv", {"naruto": [("s01e01", "https://x.test/1.mp4")]})
    kod = cli.main(["--arsiv", str(arsiv), "--kuru-calistir", "--itme-yok"])
    assert kod == 0
    rapor = json.loads(capsys.readouterr().out)
    assert rapor["yayinlandi"] is False
    assert rapor["degisim"]["yeni_anime"] == 1
    assert not (arsiv / ".git").exists()


# ─────────────────────────────────────────────────────────────────────────────
# 6) İstemci yapılandırması
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def animedepo(monkeypatch):
    """Modül seviyesi cache'leri her testte sıfırlanmış `sources.animedepo`."""
    from turkanime_api.sources import animedepo as mod

    monkeypatch.delenv(mod.ORTAM_ANAHTARI, raising=False)
    mod.taban_url_sifirla()
    yield mod
    mod.taban_url_sifirla()


def test_varsayilan_arsiv_url_korunuyor(animedepo, monkeypatch, tmp_path):
    """Ayar yokken davranış birebir eski: KebabLord arşivi."""
    monkeypatch.chdir(tmp_path)          # ne .git ne ayarlar.json var
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert animedepo.taban_url() == animedepo.BASE_URL
    assert animedepo.BASE_URL == "https://gitlab.com/AnimeDepo/animedepo/-/raw/master"


def test_arsiv_url_ortamdan_yapilandirilabiliyor(animedepo, monkeypatch):
    monkeypatch.setenv(animedepo.ORTAM_ANAHTARI, "https://ornek.test/arsiv/raw/master/")
    animedepo.taban_url_sifirla()
    assert animedepo.taban_url() == "https://ornek.test/arsiv/raw/master"

    istenen = []
    monkeypatch.setattr(animedepo, "_session",
                        lambda: _SahteOturum(istenen, {"index": {}}))
    animedepo.fetch_json("dizin.json")
    assert istenen[0][0] == "https://ornek.test/arsiv/raw/master/dizin.json"


def test_arsiv_url_ayarlar_json_ile_yapilandirilabiliyor(animedepo, monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()          # `Dosyalar` kuralı: repo kökü = cwd
    (tmp_path / "ayarlar.json").write_text(
        json.dumps({animedepo.AYAR_ANAHTARI: "https://ayardan.test/raw/master"}),
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    animedepo.taban_url_sifirla()
    assert animedepo.taban_url() == "https://ayardan.test/raw/master"


def test_bozuk_ayar_varsayilani_bozmuyor(animedepo, monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "ayarlar.json").write_text("{bozuk json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    animedepo.taban_url_sifirla()
    assert animedepo.taban_url() == animedepo.BASE_URL


class _SahteYanit:
    def __init__(self, durum, etag, veri):
        self.status_code = durum
        self.headers = {"ETag": etag}
        self._veri = veri

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._veri


class _SahteOturum:
    """`_session()` yerine geçen, ağa çıkmayan sahte (ETag'i doğru uygular)."""

    def __init__(self, kayit, veri, etag='W/"abc"'):
        self.kayit, self.veri, self.etag = kayit, veri, etag

    def get(self, url, timeout=None, headers=None):     # noqa: D102
        baslik = dict(headers or {})
        self.kayit.append((url, baslik))
        if baslik.get("If-None-Match") == self.etag:
            return _SahteYanit(304, self.etag, None)
        return _SahteYanit(200, self.etag, self.veri)


def test_dizin_degismediyse_yeniden_indirilmiyor(animedepo, monkeypatch):
    istenen: List[Tuple[str, dict]] = []
    monkeypatch.setattr(animedepo, "_session",
                        lambda: _SahteOturum(istenen, {"index": {"N": {"naruto": {}}}}))

    ilk = animedepo.dizin()
    assert ilk["index"]
    assert len(istenen) == 1 and not istenen[0][1], "ilk istek koşulsuz olmalı"

    # Cache'i koşullu istekle doğrula: sunucu 304 döndürür, veri geri gelmez
    ikinci = animedepo.dizin(tazele=True)
    assert ikinci is ilk, "304 sonrası cache korunmalı"
    assert istenen[1][1].get("If-None-Match") == 'W/"abc"', \
        "saklanan ETag koşullu istekte geri gönderilmeli"


# ─────────────────────────────────────────────────────────────────────────────
# 7) Uçtan uca — tarayıcı → yayıncı → klon → istemci
# ─────────────────────────────────────────────────────────────────────────────
def test_uctan_uca_crawler_yayinci_klon(tmp_path, monkeypatch):
    from tests.test_crawler import SahteKaynak, _defter, _katalog
    from turkanime_server.crawler.ayarlar import NezaketAyarlari, TarayiciAyarlari
    from turkanime_server.crawler.tarayici import Tarayici
    from turkanime_api.sources import animedepo

    # 1) Tarayıcı arşivi üretir (ağa çıkmaz — sahte kaynak)
    arsiv = tmp_path / "arsiv"
    tarayici = Tarayici(
        TarayiciAyarlari(cikti=arsiv, durum=tmp_path / "durum.sqlite3",
                         tohumlar=("naruto",),
                         nezaket=NezaketAyarlari(gecikme=0.0, jitter=0.0)),
        defter=_defter(anizle=SahteKaynak(_katalog("Naruto", "n1", bolum_sayisi=3))),
    )
    tarayici.calistir()
    tarayici.kapat()

    # 2) Yayıncı git'e işler ve "uzağa" iter
    uzak = bare_depo(tmp_path)
    sonuc = Yayinci(_ayarlar(arsiv, uzak)).yayinla()
    assert sonuc.yayinlandi and sonuc.itildi

    # 3) Uzaktan klonla
    klon = tmp_path / "klon"
    subprocess.run(["git", "clone", "--quiet", str(uzak), str(klon)], check=True)
    assert (klon / "dizin.json").is_file()

    # 4) İstemci modülü klonu okuyabiliyor mu?
    monkeypatch.setattr(
        animedepo, "fetch_json",
        lambda path: json.loads((klon / path.lstrip("/")).read_text("utf-8")))
    monkeypatch.setattr(animedepo, "_dizin_cache", None)

    sonuclar = animedepo.search_animedepo("Naruto")
    assert sonuclar, "istemci klonlanan arşivde anime bulamadı"
    slug = sonuclar[0][0]
    bolumler = animedepo.get_anime_episodes(slug)
    assert len(bolumler) == 3
    akislar = animedepo.get_episode_streams(bolumler[0][0])
    assert akislar and akislar[0]["url"].endswith(".mp4")
