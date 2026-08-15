"""`Dosyalar` yazım güvenliği — yarış koşulu ve bozuk JSON.

İki denetim bulgusu tek dosyada, çünkü ikisi de aynı yazma yolunu ilgilendiriyor:

1. **Yarış (veri kaybı).** Kullanıcı 3 bölümü kuyruğa atıyor (varsayılan
   paralellik 3), üçü de yaklaşık aynı anda bitiyor. Üç iş parçacığı da
   `gecmis.json`'u eski hâliyle okuyup kendi tek eklemesiyle geri yazıyordu →
   yalnızca sonuncusu kalıyordu.
2. **Bozuk/yarım JSON.** Yazım yerinde truncate ile yapılıyordu; çökme yarım
   dosya bırakıyor, sonraki açılış `JSONDecodeError` ile import anında
   çöküyordu ve kullanıcının tek çaresi dosyayı elle silmekti.

Testler `Barrier` kullanıyor: üç iş parçacığının okumadan ÖNCE buluşmasını
garantiler, yani yarış "bazen" değil her koşuda tetiklenir. Zamanlamaya bağlı
(flaky) bir test, düzeltmeyi geri alan bir değişikliği yakalayamazdı.

Hiçbir test gerçek kullanıcı dosyasına dokunmaz — hepsi `tmp_path` altında.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from turkanime_api.cli.dosyalar import Dosyalar


PARALEL = 3          # "paralel indirme sayisi" varsayılanı


def _yaz(yol, veri):
    with open(yol, "w", encoding="utf-8") as fp:
        json.dump(veri, fp)


@pytest.fixture
def dosyalar(tmp_path):
    """Gerçek dosyalara dokunmayan `Dosyalar` örneği.

    `__init__` kullanıcının ev dizinine yazıyor; testin ilgilendiği tek şey
    okuma/yazma yolları, bu yüzden yapıcı atlanıp yollar elle veriliyor.
    """
    d = Dosyalar.__new__(Dosyalar)
    d.ta_path = str(tmp_path)
    d.ayar_path = str(tmp_path / "ayarlar.json")
    d.gecmis_path = str(tmp_path / "gecmis.json")
    _yaz(d.gecmis_path, {"izlendi": {}, "indirildi": {}})
    _yaz(d.ayar_path, {})
    return d


def _es_zamanli(hedef, adet=PARALEL):
    """`adet` iş parçacığını aynı anda salıver ve bitmelerini bekle."""
    kapi = threading.Barrier(adet)
    hatalar = []

    def sar(i):
        kapi.wait()
        try:
            hedef(i)
        except Exception as hata:            # sessiz yutulmasın
            hatalar.append(hata)

    isler = [threading.Thread(target=sar, args=(i,)) for i in range(adet)]
    for t in isler:
        t.start()
    for t in isler:
        t.join()
    assert not hatalar, hatalar


# ─────────────────────────────────────────────────────────────────────────────
# 1) Yarış koşulu
# ─────────────────────────────────────────────────────────────────────────────
def test_es_zamanli_set_gecmis_kayit_kaybetmiyor(dosyalar):
    """Denetim senaryosu: 3 indirme aynı anda bitiyor, 3 kayıt da kalmalı."""
    _es_zamanli(lambda i: dosyalar.set_gecmis("naruto", f"bolum-{i}", "indirildi"))

    kalan = dosyalar.gecmis["indirildi"]["naruto"]
    assert sorted(kalan) == [f"bolum-{i}" for i in range(PARALEL)], \
        f"kayıt kayboldu: {kalan}"


def test_es_zamanli_farkli_seriler_birbirini_ezmiyor(dosyalar):
    _es_zamanli(lambda i: dosyalar.set_gecmis(f"seri-{i}", "bolum-1", "izlendi"))

    izlendi = dosyalar.gecmis["izlendi"]
    assert sorted(izlendi) == [f"seri-{i}" for i in range(PARALEL)]


def test_es_zamanli_ilerleme_ve_gecmis_ayni_dosyada_carpismiyor(dosyalar):
    """`set_ilerleme` ve `set_gecmis` aynı dosyayı yazıyor: ikisi de kalmalı."""
    def is_yap(i):
        if i == 0:
            dosyalar.set_ilerleme("naruto", 12)
        else:
            dosyalar.set_gecmis("naruto", f"bolum-{i}", "indirildi")

    _es_zamanli(is_yap)

    gecmis = dosyalar.gecmis
    assert gecmis["ilerleme"]["naruto"] == 12
    assert sorted(gecmis["indirildi"]["naruto"]) == ["bolum-1", "bolum-2"]


def test_es_zamanli_set_ayar_kayip_ayar_birakmiyor(dosyalar):
    _es_zamanli(lambda i: dosyalar.set_ayar(f"ayar_{i}", i))

    ayarlar = dosyalar.ayarlar
    assert [ayarlar.get(f"ayar_{i}") for i in range(PARALEL)] == list(range(PARALEL))


def test_ayni_bolum_iki_kez_eklenmiyor(dosyalar):
    """Kilit eklenirken mükerrer kaydı eleyen erken çıkış kaybolmasın."""
    _es_zamanli(lambda _i: dosyalar.set_gecmis("naruto", "bolum-1", "indirildi"))
    assert dosyalar.gecmis["indirildi"]["naruto"] == ["bolum-1"]


# ─────────────────────────────────────────────────────────────────────────────
# 2) Atomik yazım
# ─────────────────────────────────────────────────────────────────────────────
def test_yazim_hatasinda_eski_dosya_korunuyor(dosyalar, monkeypatch):
    """Yazım ortasında çökme: dosya ya eski ya yeni; yarım asla."""
    dosyalar.set_gecmis("naruto", "bolum-1", "indirildi")
    onceki = dosyalar.gecmis

    def patla(*_a, **_k):
        raise OSError("disk doldu")

    monkeypatch.setattr(json, "dump", patla)
    with pytest.raises(OSError):
        dosyalar.set_gecmis("naruto", "bolum-2", "indirildi")

    monkeypatch.undo()
    assert dosyalar.gecmis == onceki, "yarım yazım eski içeriği bozdu"


def test_yazim_hatasinda_gecici_dosya_kalmiyor(tmp_path, monkeypatch):
    from turkanime_api.cli.dosyalar import atomik_json_yaz

    hedef = tmp_path / "x.json"
    atomik_json_yaz(hedef, {"a": 1})

    monkeypatch.setattr(json, "dump",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("kes")))
    with pytest.raises(OSError):
        atomik_json_yaz(hedef, {"a": 2})
    monkeypatch.undo()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.json"]
    assert json.loads(hedef.read_text("utf-8")) == {"a": 1}


def test_gecici_dosya_hedefle_ayni_dizinde(tmp_path, monkeypatch):
    """`os.replace` yalnızca aynı dosya sisteminde atomik; /tmp başka mount olabilir."""
    from turkanime_api.cli.dosyalar import atomik_json_yaz

    gorulen = []
    gercek_replace = os.replace
    monkeypatch.setattr(os, "replace",
                        lambda a, b: (gorulen.append(a), gercek_replace(a, b))[1])

    hedef = tmp_path / "alt" / "y.json"
    atomik_json_yaz(hedef, {"a": 1})

    assert gorulen, "os.replace hiç çağrılmadı — yazım atomik değil"
    assert os.path.dirname(gorulen[0]) == os.path.dirname(str(hedef))


# ─────────────────────────────────────────────────────────────────────────────
# 3) Bozuk / yarım JSON'dan kurtarma
# ─────────────────────────────────────────────────────────────────────────────
YARIM_GECMIS = '{"izlendi": {}, "indiril'
YARIM_AYAR = '{\n  "indirilenler": "C:/Users/x/Downl'


def test_bozuk_gecmis_okunurken_cokmuyor(dosyalar, capsys):
    dosyalar_yolu = dosyalar.gecmis_path
    with open(dosyalar_yolu, "w", encoding="utf-8") as fp:
        fp.write(YARIM_GECMIS)

    assert dosyalar.gecmis == {"izlendi": {}, "indirildi": {}}
    assert "okunamadı" in capsys.readouterr().out


def test_bozuk_ayar_okunurken_cokmuyor(dosyalar, capsys):
    with open(dosyalar.ayar_path, "w", encoding="utf-8") as fp:
        fp.write(YARIM_AYAR)

    assert dosyalar.ayarlar == {}
    assert "okunamadı" in capsys.readouterr().out


def test_bozuk_dosya_silinmeden_yedege_aliniyor(dosyalar, tmp_path):
    """İçinde kurtarılabilir ayar olabilir; sessizce silmek veri kaybıdır."""
    with open(dosyalar.ayar_path, "w", encoding="utf-8") as fp:
        fp.write(YARIM_AYAR)
    dosyalar.ayarlar

    yedekler = [p for p in tmp_path.iterdir() if ".bozuk-" in p.name]
    assert len(yedekler) == 1, [p.name for p in tmp_path.iterdir()]
    assert yedekler[0].read_text("utf-8") == YARIM_AYAR


def test_bozuk_gecmis_sonrasi_yazim_calisiyor(dosyalar):
    """Kurtarma tek seferlik olmamalı: bozuk dosyadan sonra kayıt tutulabilmeli."""
    with open(dosyalar.gecmis_path, "w", encoding="utf-8") as fp:
        fp.write(YARIM_GECMIS)

    dosyalar.set_gecmis("naruto", "bolum-1", "indirildi")
    assert dosyalar.gecmis["indirildi"] == {"naruto": ["bolum-1"]}


def test_json_nesnesi_olmayan_dosya_da_kurtariliyor(dosyalar):
    """Liste ya da düz metin: `gecmis["izlendi"]` sonrasında TypeError verirdi."""
    with open(dosyalar.gecmis_path, "w", encoding="utf-8") as fp:
        fp.write("[1, 2, 3]")
    assert dosyalar.gecmis == {"izlendi": {}, "indirildi": {}}


def test_varsayilan_gecmis_paylasilmiyor(dosyalar):
    """Kurtarma modül sabitini döndürseydi ikinci okuma kirlenmiş olurdu."""
    with open(dosyalar.gecmis_path, "w", encoding="utf-8") as fp:
        fp.write("bozuk")
    ilk = dosyalar.gecmis
    ilk["indirildi"]["naruto"] = ["x"]

    with open(dosyalar.gecmis_path, "w", encoding="utf-8") as fp:
        fp.write("yine bozuk")
    assert dosyalar.gecmis == {"izlendi": {}, "indirildi": {}}


def test_ilk_kurulum_bozuk_ayarla_cokmuyor(tmp_path, monkeypatch, capsys):
    """`Dosyalar()` yapıcısı: eskiden bozuk ayarla import anında çöküyordu."""
    ev = tmp_path / "ev"
    (ev / "Turkanime").mkdir(parents=True)
    with open(ev / "Turkanime" / "ayarlar.json", "w", encoding="utf-8") as fp:
        fp.write(YARIM_AYAR)

    monkeypatch.setattr(os.path, "expanduser", lambda _p: str(ev))
    monkeypatch.chdir(tmp_path)          # `.git` yok → ~/Turkanime kullanılır

    d = Dosyalar()                       # eskiden JSONDecodeError
    assert d.ayarlar.get("paralel indirme sayisi") == 3
    assert d.ayarlar.get("user_id")
    assert "okunamadı" in capsys.readouterr().out
