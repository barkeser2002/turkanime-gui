"""Yerel kitaplık (`common.kutuphane`): geçmiş sırası, favoriler, dosya kuralları.

ESKİ EKSİK: `gecmis.json` yalnızca slug tutuyordu (kaynak, ad, zaman yok);
izlenen bir seri oradan yeniden açılamıyor, "son izlenen" de son İLK izleme
oluyordu. Kitaplık ayrı dosyada; eski geçmişin biçimine dokunmuyor.
"""
from __future__ import annotations

import json
import os

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.common import kutuphane


def test_dosya_ayarlarin_yaninda_ve_gecmisten_ayri(izole_ev):
    kutuphane.izleme_kaydet("TürkAnime", "07-ghost", "07-Ghost", "07-ghost-1-bolum")
    yol = kutuphane.kutuphane_yolu()
    assert os.path.dirname(yol) == Dosyalar().ta_path
    assert os.path.isfile(yol)
    # gecmis.json'un biçimi değişmedi: yeni anahtar yok.
    assert set(Dosyalar().gecmis) == {"izlendi", "indirildi"}


def test_gecmis_en_yeni_basta_tekrar_izleme_one_tasiyor(izole_ev):
    kutuphane.izleme_kaydet("TürkAnime", "a", "A", "a-1", "1. Bölüm", zaman=100)
    kutuphane.izleme_kaydet("AnimeciX", "1234", "B", "b-1", "1. Bölüm", zaman=200)
    assert [k["kimlik"] for k in kutuphane.gecmis_listesi()] == ["1234", "a"]

    kutuphane.izleme_kaydet("TürkAnime", "a", "A", "a-1", "1. Bölüm", zaman=300)
    gecmis = kutuphane.gecmis_listesi()
    assert [k["kimlik"] for k in gecmis] == ["a", "1234"], "kopya olmamalı"
    assert gecmis[0]["zaman"] == 300
    assert [s["kimlik"] for s in kutuphane.devam_listesi()] == ["a", "1234"]


def test_gecmis_sinirli(izole_ev, monkeypatch):
    monkeypatch.setattr(kutuphane, "GECMIS_SINIRI", 3)
    for no in range(5):
        kutuphane.izleme_kaydet("TürkAnime", "a", "A", f"a-{no}", zaman=no)
    assert [k["bolum_slug"] for k in kutuphane.gecmis_listesi()] == ["a-4", "a-3", "a-2"]


def test_kaynaksiz_ya_da_kimliksiz_kayit_yazilmiyor(izole_ev):
    assert kutuphane.izleme_kaydet("", "a", "A", "a-1") is False
    assert kutuphane.izleme_kaydet("TürkAnime", "", "A", "a-1") is False
    assert not os.path.exists(kutuphane.kutuphane_yolu())


def test_eski_gecmis_bozulmadan_kaliyor(izole_ev):
    d = Dosyalar()
    d.set_gecmis("naruto", "naruto-1", "izlendi")
    d.set_gecmis("naruto", "naruto-1", "indirildi")
    d.set_ilerleme("naruto", 1)
    kutuphane.izleme_kaydet("TürkAnime", "naruto", "Naruto", "naruto-2")
    kutuphane.favori_ayarla("TürkAnime", "naruto", True, "Naruto")

    gecmis = Dosyalar().gecmis
    assert gecmis["izlendi"] == {"naruto": ["naruto-1"]}
    assert gecmis["indirildi"] == {"naruto": ["naruto-1"]}
    assert gecmis["ilerleme"] == {"naruto": 1}
    Dosyalar().set_gecmis("naruto", "naruto-2", "izlendi")     # hâlâ yazılabiliyor
    assert Dosyalar().gecmis["izlendi"]["naruto"] == ["naruto-1", "naruto-2"]


def test_bozuk_dosya_yedeklenip_bos_kitapliga_donuluyor(izole_ev, capsys):
    yol = kutuphane.kutuphane_yolu()
    with open(yol, "w", encoding="utf-8") as fp:
        fp.write("{yarım")
    assert kutuphane.devam_listesi() == []
    assert any(ad.startswith("kutuphane.json.bozuk-")
               for ad in os.listdir(os.path.dirname(yol)))
    assert kutuphane.izleme_kaydet("TürkAnime", "a", "A", "a-1")
    assert len(kutuphane.devam_listesi()) == 1
    capsys.readouterr()


def test_yanlis_tipli_bolumler_yazimi_dusurmuyor(izole_ev):
    with open(kutuphane.kutuphane_yolu(), "w", encoding="utf-8") as fp:
        json.dump({"seriler": [], "gecmis": {"x": 1}, "konum": "?"}, fp)
    assert kutuphane.izleme_kaydet("TürkAnime", "a", "A", "a-1")
    assert kutuphane.favori_ayarla("TürkAnime", "a", True)
    assert kutuphane.favori_mi("TürkAnime", "a")


def test_favori_ekle_cikar(izole_ev):
    assert kutuphane.favori_ayarla("TürkAnime", "a", True, "A", "http://k/a.jpg", zaman=1)
    assert kutuphane.favori_ayarla("AnimeciX", "9", True, "B", zaman=2)
    assert [s["kimlik"] for s in kutuphane.favoriler()] == ["9", "a"]
    assert kutuphane.favoriler()[1]["kapak"] == "http://k/a.jpg"

    # Hiç izlenmemiş seri favoriden çıkınca kitaplıktan da çıkar.
    kutuphane.favori_ayarla("AnimeciX", "9", False)
    assert kutuphane.anahtar("AnimeciX", "9") not in kutuphane.oku()["seriler"]

    # İzlenmiş seri favoriden çıkınca "izlemeye devam et"te kalır.
    kutuphane.izleme_kaydet("TürkAnime", "a", "A", "a-1")
    kutuphane.favori_ayarla("TürkAnime", "a", False)
    assert not kutuphane.favori_mi("TürkAnime", "a")
    assert [s["kimlik"] for s in kutuphane.devam_listesi()] == ["a"]


def test_sonraki_bolum():
    bolumler = ["b1", "b2", "b3", "b4"]
    assert kutuphane.sonraki_bolum(bolumler, []) == 0
    assert kutuphane.sonraki_bolum(bolumler, ["b1"]) == 1
    assert kutuphane.sonraki_bolum(bolumler, ["b4"]) is None
    # Sırasız izleme: en ilerideki izlenenin ardı.
    assert kutuphane.sonraki_bolum(bolumler, ["b3", "b1"]) == 3
    assert kutuphane.sonraki_bolum([], ["b1"]) is None


def test_sure_metni_ve_bitti_mi():
    assert kutuphane.sure_metni(734.2) == "12:14"
    assert kutuphane.sure_metni(3723) == "1:02:03"
    assert kutuphane.sure_metni(None) == "0:00"
    assert kutuphane.bitti_mi(10, 1400, "eof")
    assert kutuphane.bitti_mi(1300, 1400, "quit")
    assert not kutuphane.bitti_mi(734, 1420, "quit")
    assert not kutuphane.bitti_mi(734, None, "quit")
