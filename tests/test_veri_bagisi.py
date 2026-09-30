"""Veri bağışı — rıza, gövde, kuyruk, gönderici, kancalar ve ayar sayfası.

Ağırlık merkezi kimlik bağışındakiyle aynı cümle: **açık onay olmadan hiçbir
şey toplanmaz ve gönderilmez.** Kapılar tek tek sınanıyor (varsayılan kapalı,
Esc/×/dış tık/Enter = hayır, "Kaydet" formu açamaz, kapatınca kuyruk silinir,
sunucu yapılandırılmamışsa kanca anlık görüntü bile almaz).

İkinci ağırlık gövdenin BEYAZ LİSTE olması: akış sözlüklerine bilerek çerez,
user-agent, referer ve jeton konuyor; hiçbirinin gövdede görünmediği
denetleniyor. Süreli/imzalı bağlantılar (okcdn, anizm HLS, Tranimaci) gerçek
biçimleriyle (fikstürlerden) ayıklanıyor.

Ağa çıkılmıyor: gönderici sahte, gerçek HTTP yolu 127.0.0.1'deki küçük bir
sunucuya gidiyor (conftest'in ağ mandalı dışarıyı zaten kesiyor).
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.gui.web import veri_bagisi as vb

ONAY = {"onay": True, "okudum": True}

ACIK_AYAR = {"veri bagisi": True, "veri bagisi onayi": vb.ONAY_SURUMU,
             "sunucu adresi": "https://sunucu.test", "sunucu api anahtari": "anahtar"}

# Gerçek biçimli akışlar. Sırlar (referer, user_agent, jeton) bilerek içeride:
# gövdede hiçbiri görünmemeli.
GIZLI_REFERER = "https://gizli-referer.test/izle?oturum=GIZLI-OTURUM"
GIZLI_UA = "GIZLI-UA/1.0 (oturum-bagli)"
SIBNET = "https://video.sibnet.ru/shell.php?videoid=4512290"
MAILRU = "https://my.mail.ru/video/embed/9173325596158070507"
OKCDN = ("https://vd346.okcdn.ru/?expires=1790371318112&srcIp=203.0.113.130&pr=10"
         "&srcAg=CHROME&ms=185.180.203.99&type=4&sig=6gDhmXhHHGM&id=16901295901215")
ANIZM = ("https://anizmplayer.com/cdn/hls/02ab6d029d42490cf24f8ce99de7ecbd/master.m3u8"
         "?md5=jx6W4AfIycIRi4_86Qq8vQ&expires=1790291237")
TRANIMACI = "https://cdn.tranimaci.test/video/1080p.mp4?token=GIZLI-JETON-123"


def akislar():
    return [
        {"url": SIBNET, "label": "Sibnet", "player": "SIBNET", "fansub": "AnimeSue",
         "type": "iframe", "referer": GIZLI_REFERER, "user_agent": GIZLI_UA},
        {"url": OKCDN, "label": "OK.ru 1080p", "player": "ODNOKLASSNIKI",
         "type": "direct", "user_agent": GIZLI_UA},
        {"url": ANIZM, "label": "Anizm (HLS)", "type": "hls", "referer": GIZLI_REFERER},
        {"url": MAILRU, "label": "Mail.ru", "player": "MAIL", "fansub": "AnimeSue",
         "cookies": "sid=GIZLI-CEREZ", "headers": {"Cookie": "sid=GIZLI-CEREZ"}},
        {"url": TRANIMACI, "label": "1080p", "type": "direct", "referer": GIZLI_REFERER},
    ]


class Anime:
    def __init__(self, slug="one-piece", title="One Piece"):
        self.slug = slug
        self.title = title


class Bolum:
    """`AdapterBolum` kılığında: kaynak kimliği ve son `best_video`'nun akışları."""

    def __init__(self, kimlik="one-piece/bolum-1", akis=None, title="One Piece 1. Bölüm"):
        self.kimlik = kimlik
        self.slug = "one-piece-bolum-1"
        self.anime = Anime()
        self.title = title
        self.son_akislar = akislar() if akis is None else list(akis)


class Video:
    def __init__(self, url=SIBNET, player="SIBNET", label="Sibnet"):
        self.url = url
        self.player = player
        self.label = label
        self.referer = GIZLI_REFERER
        self.user_agent = GIZLI_UA


def kayit(bolum=None, **ek):
    """Detay sayfasının `bolumler` ucunun kurduğu bölüm kaydı."""
    e = {"title": "One Piece 1. Bölüm", "obj": bolum if bolum is not None else Bolum(),
         "kaynak": "AnimeTR", "kimlik": "one-piece", "seri_adi": "One Piece",
         "kapak": "https://img.test/one-piece.jpg",
         "bolum_listesi": [("one-piece/bolum-1", "1. Bölüm"),
                           ("one-piece/bolum-2", "2. Bölüm")],
         # Kayıtta dururlar ama gövdeye GİRMEMELİLER:
         "yerel_dosya": "/home/kullanici/Downloads/one-piece/one-piece-bolum-1.mp4"}
    e.update(ek)
    return e


def govde(**ek):
    return vb.govde_kur(vb.anlik_al(kayit(**ek), Video()))


# ── Test ortamı (thread'siz, sahte saat ve gönderici) ────────────────────────
class Ortam:
    """Servis + ayar + saat + gönderici; ``sonuclar`` sıradaki gönderimlerin
    cevabı (boşsa 200). Thread kurulmaz: adımları test atar."""

    def __init__(self, yol):
        self.yol = str(yol)
        self.ayar = dict(ACIK_AYAR)
        self.saat = 1_800_000_000.0
        self.sonuclar: list = []
        self.gonderilen: list = []
        self.bildirim = 0
        self.servis = self.yeni_servis()

    def yeni_servis(self):
        return vb.VeriBagisi(kuyruk_dosyasi=self.yol, ayar_oku=lambda: dict(self.ayar),
                             gonderici=self._gonder, saat=lambda: self.saat,
                             bildir=self._bildir, otomatik=False)

    def _bildir(self):
        self.bildirim += 1

    def _gonder(self, url, basliklar, gvd):
        self.gonderilen.append((url, dict(basliklar), json.loads(json.dumps(gvd))))
        if self.sonuclar:
            return self.sonuclar.pop(0)
        return vb.GonderimSonucu(vb.TAMAM, katki_id="a" * 32)

    def oynat(self, e=None, video=None, rapor=None):
        return self.servis.oynatildi(e if e is not None else kayit(),
                                     video or Video(), rapor)

    def calistir(self, en_cok=20):
        """Kuyruk boşalana ya da geri çekilmeye takılana kadar adımla."""
        bekle = None
        for _ in range(en_cok):
            bekle = self.servis.adim()
            if bekle is None or bekle > vb.GONDERIM_ARALIGI:
                return bekle
        return bekle

    def durum(self):
        return self.servis.kuyruk.durum()

    def dosya(self):
        with open(self.yol, encoding="utf-8") as fp:
            return json.load(fp)


@pytest.fixture
def ortam(tmp_path):
    o = Ortam(tmp_path / vb.KUYRUK_DOSYASI)
    yield o
    o.servis.durdur()


# ═════════════════════════════════════════════════════════════════════════════
# 1. Rıza: varsayılan kapalı, yalnızca açık onay
# ═════════════════════════════════════════════════════════════════════════════
def test_varsayilan_kapali(izole_ev):
    ayarlar = Dosyalar().ayarlar
    assert ayarlar["veri bagisi"] is False
    assert ayarlar["veri bagisi onayi"] == 0
    assert vb.acik_mi(ayarlar) is False


@pytest.mark.parametrize("ayar, beklenen", [
    ({}, False),
    ({"veri bagisi": True}, False),                           # onaysız açık yok
    ({"veri bagisi": True, "veri bagisi onayi": 0}, False),
    ({"veri bagisi": True, "veri bagisi onayi": True}, False),   # True == 1 tuzağı
    ({"veri bagisi": "true", "veri bagisi onayi": 1}, False),
    ({"veri bagisi": 1, "veri bagisi onayi": 1}, False),
    ({"veri bagisi": True, "veri bagisi onayi": vb.ONAY_SURUMU + 1}, False),
    ({"veri bagisi": False, "veri bagisi onayi": vb.ONAY_SURUMU}, False),
    ({"veri bagisi": True, "veri bagisi onayi": vb.ONAY_SURUMU}, True),
])
def test_acik_sayilmak_ayar_ve_guncel_onay_ister(ayar, beklenen):
    """Metin değişince (ONAY_SURUMU artınca) eski onay geçmemeli."""
    assert vb.acik_mi(ayar) is beklenen


def test_yalnizca_acik_onay_cevabi_onay_sayiliyor(soru_merkezi):
    for cevap, beklenen in ((None, False), (False, False), ({"onay": False}, False),
                            ({"onay": True}, False),                  # kutusuz onay
                            ({"onay": True, "okudum": False}, False),
                            ({"onay": False, "okudum": True}, False),  # Esc/×/Vazgeç
                            ({"onay": 1, "okudum": 1}, False),
                            ({"onay": "true", "okudum": "true"}, False),
                            ([True], False), (ONAY, True)):
        sonuc: list = []
        soru = vb.onay_al(soru_merkezi, sonuc.append)
        assert soru.tur == "veri_bagisi_onayi"
        soru_merkezi.cevapla(soru.kimlik, cevap)
        assert sonuc == [beklenen], cevap


@pytest.mark.parametrize("bitir", ["kapanis", "sayfa_gitti", "teslim_yok"])
def test_cevapsiz_biten_pencere_onay_degil(soru_merkezi, qtbot, bitir):
    """Uygulama kapandı / sayfa öldü / sayfa hiç bağlanmadı: onay YOK."""
    sonuc: list = []
    if bitir == "teslim_yok":
        soru_merkezi.bagli = False
        soru = vb.onay_al(soru_merkezi, sonuc.append)
        soru._zamanlayici.start(1)
        qtbot.waitUntil(lambda: bool(sonuc), timeout=3000)
    else:
        vb.onay_al(soru_merkezi, sonuc.append)
        getattr(soru_merkezi, "hepsini_bitir" if bitir == "kapanis" else "sayfa_gitti")()
    assert sonuc == [False]


def test_soru_merkezi_yoksa_onay_yok():
    sonuc: list = []
    assert vb.onay_al(None, sonuc.append) is None
    assert sonuc == [False]


def test_onay_metni_durust():
    """Ne gittiği, ne GİTMEDİĞİ ve IP'nin sunucuya görünmesi yazılı olmalı."""
    metin = vb.ONAY_METNI
    for ifade in ("kaynağın adı", "kapak", "oynayan video bağlantısı",
                  "diğer aday bağlantılar", "bölüm listesi", "çerezler", "parolalar",
                  "jetonlar", "dosya yolları", "izleme konumun", "user-agent", "referer",
                  "IP adresinden oynatıldığını", "görebilir", "geri çekilemezler",
                  "kuyruk silinir"):
        assert ifade in metin, ifade
    # IP konusunda tutulamayacak söz verilmiyor.
    assert "IP adresin gönderilmez" not in metin
    for yasak in ("destek ol", "teşekkür ederiz"):
        assert yasak not in metin.lower()
    aciklama = " ".join(m for _b, m in vb.ACIKLAMA)
    for ifade in ("oynayan video bağlantısı", "Çerezler", "izleme konumun", "referer",
                  "IP adresinden", "geri çekilemez"):
        assert ifade in aciklama, ifade


# ── Ayar uçları: onay akışı (sayfasız) ───────────────────────────────────────
@pytest.fixture
def sayfa(ayar_uclari, izole_ev, soru_merkezi, tmp_path):
    """Ayar uçları + sayfasız soru merkezi + thread'siz servis (sahte gönderici)."""
    gonderilen: list = []

    def gonder(url, basliklar, gvd):
        gonderilen.append(gvd)
        return vb.GonderimSonucu(vb.TAMAM)

    servis = vb.VeriBagisi(kuyruk_dosyasi=str(tmp_path / "kuyruk.json"),
                           gonderici=gonder, otomatik=False)
    uclar = ayar_uclari(sorular=soru_merkezi, veri_bagisi=servis)
    uclar.merkez, uclar.servis, uclar.gonderilen = soru_merkezi, servis, gonderilen
    yield uclar
    servis.durdur()


def _soru(sayfa):
    (soru,) = sayfa.merkez.bekleyenler("veri_bagisi_onayi")
    return soru


def test_acmak_yalnizca_pencere_aciyor_hicbir_sey_yazmiyor(sayfa):
    d = sayfa.veri_bagisi_ayarla(True)

    assert d["acik"] is False and d["bekliyor"] is True
    soru = _soru(sayfa)
    assert soru.veri["metin"] == vb.ONAY_METNI
    assert soru.veri["kutu"] == vb.ONAY_KUTUSU
    assert Dosyalar().ayarlar["veri bagisi"] is False, "cevap gelmeden yazıldı"


def test_ikinci_tik_ikinci_pencere_acmiyor(sayfa):
    sayfa.veri_bagisi_ayarla(True)
    sayfa.veri_bagisi_ayarla(True)
    assert len(sayfa.merkez.bekleyenler("veri_bagisi_onayi")) == 1


@pytest.mark.parametrize("cevap", [None, {"onay": False, "okudum": True},
                                   {"onay": True}, {"onay": True, "okudum": 1}])
def test_vazgecmek_acmiyor(sayfa, cevap):
    sayfa.veri_bagisi_ayarla(True)
    sayfa.merkez.cevapla(_soru(sayfa).kimlik, cevap)

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["veri bagisi"] is False and ayarlar["veri bagisi onayi"] == 0
    olay = sayfa.kopru.son("ayar_veri_bagisi")
    assert olay["acik"] is False and olay["bekliyor"] is False
    assert "açılmadı" in olay["mesaj"]


def test_acik_onay_aciyor_ve_surumu_yaziyor(sayfa):
    sayfa.veri_bagisi_ayarla(True)
    sayfa.merkez.cevapla(_soru(sayfa).kimlik, ONAY)

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["veri bagisi"] is True
    assert ayarlar["veri bagisi onayi"] == vb.ONAY_SURUMU
    olay = sayfa.kopru.son("ayar_veri_bagisi")
    assert olay["acik"] is True
    # Sunucu adresi boş: dürüstçe "gönderim yok" deniyor.
    assert "Sunucu adresi" in olay["mesaj"] and "gönderim yok" in olay["mesaj"]
    assert sayfa.veri_bagisi_ayarla(True)["mesaj"] == "Veri bağışı zaten açık."


def test_kaydet_formu_veri_bagisini_acamaz(sayfa):
    """Form her alanı yazıyor; bu ayar formda olsaydı "Kaydet" onaysız açardı."""
    assert "veri_bagisi" not in sayfa.ayarlar()["degerler"]
    sayfa.ayarlari_kaydet({"veri_bagisi": True, "veri bagisi": True,
                           "veri_bagisi_onayi": 1, "paralel": 4})
    ayarlar = Dosyalar().ayarlar
    assert ayarlar["veri bagisi"] is False and ayarlar["veri bagisi onayi"] == 0
    assert ayarlar["paralel indirme sayisi"] == 4


def test_kapatmak_aninda_ve_kuyrugu_siliyor(sayfa):
    Dosyalar().set_ayar(ayar_list=ACIK_AYAR)
    sayfa.servis.kuyruk.ekle(govde(), 1_800_000_000.0)      # gönderilmeyi bekliyor

    d = sayfa.veri_bagisi_ayarla(False)

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["veri bagisi"] is False and ayarlar["veri bagisi onayi"] == 0
    assert d["acik"] is False and d["bekleyen"] == 0
    assert "1 kayıt silindi" in d["mesaj"]
    assert sayfa.servis.adim() is None
    assert sayfa.gonderilen == [], "kapattıktan sonra gönderildi"
    # Kapalıyken kanca anlık görüntü bile almıyor.
    assert sayfa.servis.oynatildi(kayit(), Video()) is False


def test_durum_ucu_sayaclari_ve_aciklamayi_veriyor(sayfa):
    d = sayfa.veri_bagisi_durumu()
    for alan in ("acik", "gonderilen", "bekleyen", "dusurulen", "son_hata", "metin",
                 "aciklama", "bekliyor"):
        assert alan in d, alan
    assert [a["baslik"] for a in d["aciklama"]] == [b for b, _m in vb.ACIKLAMA]
    assert sayfa.ayarlar()["veri_bagisi"]["acik"] is False


def test_kuyrugu_temizle_ucu(sayfa):
    Dosyalar().set_ayar(ayar_list=ACIK_AYAR)
    sayfa.servis.kuyruk.ekle(govde(), 1_800_000_000.0)
    d = sayfa.veri_bagisi_temizle()
    assert d["bekleyen"] == 0 and "1 kayıt silindi" in d["mesaj"]
    assert Dosyalar().ayarlar["veri bagisi"] is True, "temizlemek kapatmak değil"
    assert "yok" in sayfa.veri_bagisi_temizle()["mesaj"]


# ═════════════════════════════════════════════════════════════════════════════
# 2. Gövde: calisti bayrağı, süreli bağlantılar, sırlar
# ═════════════════════════════════════════════════════════════════════════════
def test_oynayan_calisti_true_digerleri_false():
    g = vb.govde_kur(vb.anlik_al(kayit(), Video(MAILRU, "MAIL", "Mail.ru")))

    assert g["videolar"][0] == {"url": MAILRU, "oynatici": "MAIL", "fansub": "AnimeSue",
                                "etiket": "Mail.ru", "calisti": True}
    assert [v["calisti"] for v in g["videolar"]] == [True, False]
    assert g["videolar"][1]["url"] == SIBNET
    assert sum(v["calisti"] for v in g["videolar"]) == 1


def test_govde_alanlari():
    g = govde()
    assert g["kaynak"] == "animetr"
    assert g["anime"] == {"kimlik": "one-piece", "baslik": "One Piece",
                          "kapak": "https://img.test/one-piece.jpg"}
    assert g["bolum"] == {"kimlik": "one-piece/bolum-1", "baslik": "One Piece 1. Bölüm",
                          "sezon": None, "no": 1, "ara": None}
    assert g["bolum_listesi"] == [["one-piece/bolum-1", "1. Bölüm"],
                                  ["one-piece/bolum-2", "2. Bölüm"]]
    assert g["istemci"].startswith("turkanime-gui/")


@pytest.mark.parametrize("baslik, beklenen", [
    ("Frieren 2. Sezon 5. Bölüm", (2, 5, None)),
    ("S03E12", (3, 12, None)),
    ("One Piece 5.5. Bölüm", (None, 5, 5)),
    ("Film", (None, None, None)),
])
def test_numaralar_basliktan_bilinmeyen_null(baslik, beklenen):
    """Sezon yazmıyorsa null: "sezon yoksa 1" varsayımı arşive yanlış yazardı."""
    b = govde(title=baslik)["bolum"]
    assert (b["sezon"], b["no"], b["ara"]) == beklenen


@pytest.mark.parametrize("adres", [OKCDN, ANIZM, TRANIMACI,
    "https://vd329.okcdn.ru/expires/1790371319938/srcIp/203.0.113.131/sig/Qa638k/"
    "ondemand/hls4_18783398660608.m3u8",
    "https://bucket.s3.test/v.mp4?X-Amz-Algorithm=AWS4&X-Amz-Signature=abc",
    "https://cdn.test/v.mp4?Expires=1790000000&Signature=abc&Key-Pair-Id=K",
    "https://storage.test/v.mp4?X-Goog-Signature=abc",
    "https://cdn.test/v.m3u8?vt=abc", "https://cdn.test/v.mp4?hdnts=exp=1~hmac=x",
    "https://cust.stream.test/eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln/manifest/video.m3u8",
    "https://kullanici:parola@cdn.test/v.mp4",
    "ftp://cdn.test/v.mp4", "file:///home/kullanici/v.mp4", "/home/kullanici/v.mp4",
    "https://", "https:///yol", "", None, 42, "https://cdn.test/a b.mp4",
    "https://cdn.test/" + "a" * 2048,
])
def test_sureli_ve_gecersiz_adresler_ayiklaniyor(adres):
    assert vb.adres_temizle(adres) is None


@pytest.mark.parametrize("adres, beklenen", [
    (SIBNET, SIBNET), (MAILRU, MAILRU),
    ("https://ok.ru/videoembed/3218801887832", "https://ok.ru/videoembed/3218801887832"),
    # VK'nın `hash`'i kalıcı gömme anahtarı, süreli değil.
    ("https://vk.com/video_ext.php?oid=201414500&id=167549481&hash=496d2253657d6f16",
     "https://vk.com/video_ext.php?oid=201414500&id=167549481&hash=496d2253657d6f16"),
    # İmzasız HLS (QuadroGG): kalıcı CDN yolu.
    ("https://s3i--cdn-s2.test/213e/S1-E1/e786/1080p_284d.m3u8",
     "https://s3i--cdn-s2.test/213e/S1-E1/e786/1080p_284d.m3u8"),
    # Parça atılır: oynatıcı konumu (`#t=`) gibi şeyler taşıyabilir.
    ("https://drive.google.com/file/d/1mXE/preview#t=120",
     "https://drive.google.com/file/d/1mXE/preview"),
    ("HTTPS://Video.Sibnet.ru/shell.php?videoid=1", "https://Video.Sibnet.ru/shell.php?videoid=1"),
])
def test_kalici_adresler_geciyor(adres, beklenen):
    assert vb.adres_temizle(adres) == beklenen


def test_sureli_oynayan_atlanir_digerleri_gider():
    """Oynayan süreliyse (okcdn) gönderilmez; kalıcı adaylar yine gider."""
    g = vb.govde_kur(vb.anlik_al(kayit(), Video(OKCDN, "ODNOKLASSNIKI", "OK.ru 1080p")))
    adresler = [v["url"] for v in g["videolar"]]
    assert adresler == [SIBNET, MAILRU]
    assert not any(v["calisti"] for v in g["videolar"])


def test_kaynagin_kalici_gomme_sayfasi_tercih_ediliyor():
    """Süreli adresin yanında kaynak `gomme` verdiyse o gider (calisti ile)."""
    gomme = "https://ok.ru/videoembed/14838475655711"
    akis = [{"url": OKCDN, "gomme": gomme, "label": "OK.ru 1080p", "player": "ODNOKLASSNIKI"},
            {"url": SIBNET, "label": "Sibnet", "player": "SIBNET"}]
    g = vb.govde_kur(vb.anlik_al(kayit(obj=Bolum(akis=akis)), Video(OKCDN)))
    assert g["videolar"][0] == {"url": gomme, "oynatici": "ODNOKLASSNIKI", "fansub": None,
                                "etiket": "OK.ru 1080p", "calisti": True}


def test_hic_kalici_baglanti_yoksa_govde_yok():
    akis = [{"url": OKCDN}, {"url": ANIZM}]
    assert vb.govde_kur(vb.anlik_al(kayit(obj=Bolum(akis=akis)), Video(OKCDN))) is None


def test_sirlar_govdeye_girmiyor():
    """Beyaz liste: yalnızca sözleşmenin alanları; referer/UA/çerez/jeton/yol yok."""
    g = govde()
    metin = json.dumps(g, ensure_ascii=False)
    for sir in (GIZLI_REFERER, GIZLI_UA, "GIZLI-CEREZ", "GIZLI-JETON", "GIZLI-OTURUM",
                "sid=", "srcIp", "203.0.113", "expires", "token", "/home/kullanici",
                "Downloads", "user_agent", "referer", "cookie", "konum"):
        assert sir not in metin, sir
    assert set(g) == {"kaynak", "anime", "bolum", "videolar", "bolum_listesi", "istemci"}
    assert set(g["anime"]) == {"kimlik", "baslik", "kapak"}
    assert set(g["bolum"]) == {"kimlik", "baslik", "sezon", "no", "ara"}
    for v in g["videolar"]:
        assert set(v) == {"url", "oynatici", "fansub", "etiket", "calisti"}


def test_kapak_sureliyse_null():
    g = govde(kapak="https://iv.okcdn.ru/videoPreview?id=1&type=37&tkn=VSIXu4ihCk")
    assert g["anime"]["kapak"] is None


def test_yerel_dosya_bagislanmiyor():
    """İndirilmiş dosyadan oynatma kaynağa hiç gitmedi: bağış yok."""
    from turkanime_api.gui.qt.prefs import YerelVideo
    assert vb.anlik_al(kayit(), YerelVideo("/home/kullanici/Downloads/a.mp4")) is None


@pytest.mark.parametrize("kaynak", ["AniList", "BilinmeyenKaynak", "", None])
def test_yalnizca_video_kaynaklari(kaynak):
    assert vb.anlik_al(kayit(kaynak=kaynak), Video()) is None


def test_kaynak_kimligi_olmayan_bolum_bagislanmiyor():
    """Sunucu arşivi bölümü kaynağın kimliğiyle anahtarlıyor; tahmin kirletir."""
    bolum = Bolum()
    bolum.kimlik = None
    assert vb.anlik_al(kayit(obj=bolum), Video()) is None
    assert vb.anlik_al(kayit(kimlik=""), Video()) is None


def test_kaynak_adi_kayittaki_modul_adi():
    assert govde(kaynak="TürkAnime")["kaynak"] == "animedepo"
    assert govde(kaynak="AnimeDepo")["kaynak"] == "animedepo"      # eski ad
    assert govde(kaynak="TRAnimeİzle")["kaynak"] == "tranime"


def test_sinirlar_kirpiliyor():
    """Sözleşmeyi aşan tek alan 422 ile bütün kaydı düşürürdü."""
    uzun = "ş" * 1000
    akis = [{"url": f"https://video.sibnet.ru/shell.php?videoid={i}", "label": uzun,
             "player": uzun, "fansub": uzun} for i in range(60)]
    liste = [(f"b-{i}", uzun) for i in range(6000)]
    g = vb.govde_kur(vb.anlik_al(
        kayit(obj=Bolum(kimlik="k" * 400, akis=akis), title=uzun, seri_adi=uzun,
              kimlik="a" * 400, bolum_listesi=liste),
        Video(akis[0]["url"])))
    assert len(g["videolar"]) == vb.EN_COK_VIDEO
    assert g["videolar"][0]["calisti"] is True
    assert len(g["bolum_listesi"]) == vb.EN_COK_BOLUM
    assert all(len(b) <= 300 and len(k) <= 300 for k, b in g["bolum_listesi"])
    assert len(g["anime"]["kimlik"]) == 300 and len(g["bolum"]["kimlik"]) == 300
    assert len(g["anime"]["baslik"]) == 300 and len(g["bolum"]["baslik"]) == 300
    v = g["videolar"][0]
    assert (len(v["etiket"]), len(v["oynatici"]), len(v["fansub"])) == (50, 50, 100)


def test_ayni_adres_iki_kez_girmiyor():
    akis = akislar() + [dict(akislar()[3], label="kopya")]
    g = vb.govde_kur(vb.anlik_al(kayit(obj=Bolum(akis=akis)), Video()))
    adresler = [v["url"] for v in g["videolar"]]
    assert len(adresler) == len(set(adresler))


@pytest.mark.parametrize("rapor, beklenen", [
    (None, True),                                        # Lua'sız mpv: çıkış kodu tek kanıt
    ({"konum": 734.2, "sure": 1420, "sebep": "quit"}, True),
    ({"konum": 1420, "sure": 1420, "sebep": "eof"}, True),
    ({"konum": None, "sure": None, "sebep": "eof"}, True),
    ({"konum": 3.0, "sure": 1420, "sebep": "quit"}, False),   # açıp hemen kapattı
    ({"konum": None, "sure": None, "sebep": "quit"}, False),  # akış hiç başlamadı
])
def test_gercek_oynatma_sinyali(rapor, beklenen):
    assert vb.gercekten_oynadi_mi(rapor) is beklenen


# ═════════════════════════════════════════════════════════════════════════════
# 3. Kuyruk: kalıcılık, tekrar, geri çekilme, 429, 400, kapalı
# ═════════════════════════════════════════════════════════════════════════════
def test_kanca_kapaliyken_hicbir_sey_toplamiyor(ortam):
    ortam.ayar["veri bagisi"] = False
    assert ortam.oynat() is False
    assert ortam.servis.adim() is None
    assert ortam.gonderilen == [] and not os.path.exists(ortam.yol)


@pytest.mark.parametrize("degisiklik", [
    {"sunucu adresi": ""}, {"sunucu api anahtari": ""},
    {"sunucu adresi": "http://sunucu.test"},             # şifresiz, yerel değil
    {"sunucu adresi": "http://localhost.saldirgan.test"},
])
def test_sunucu_yapilandirilmamissa_tamamen_kapali(ortam, degisiklik):
    ortam.ayar.update(degisiklik)
    assert ortam.oynat() is False
    assert ortam.servis.adim() is None
    assert ortam.gonderilen == [] and not os.path.exists(ortam.yol)
    assert ortam.servis.durum()["sebep"]


def test_yerel_sunucu_sifresiz_olabilir(ortam):
    ortam.ayar["sunucu adresi"] = "http://127.0.0.1:16554/"
    assert ortam.oynat() is True
    ortam.calistir()
    url, basliklar, _g = ortam.gonderilen[0]
    assert url == "http://127.0.0.1:16554/katki/veri"
    assert basliklar == {"X-API-Key": "anahtar"}


def test_gonderilen_kuyruktan_duser_sayac_artar(ortam):
    assert ortam.oynat() is True
    assert ortam.calistir() is None
    assert len(ortam.gonderilen) == 1
    assert ortam.gonderilen[0][2] == govde()
    d = ortam.durum()
    assert (d["gonderilen"], d["bekleyen"], d["dusurulen"]) == (1, 0, 0)
    assert ortam.bildirim >= 1, "ayar sayfası tazelenmedi"


def test_kuyruk_diske_yaziliyor_yeniden_okunuyor(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.HATA, "Sunucuya ulaşılamadı (ConnectionError).")]
    ortam.oynat()
    ortam.calistir()
    dosya = ortam.dosya()
    assert [k["anahtar"] for k in dosya["bekleyen"]] == [
        '["animetr", "one-piece", "one-piece/bolum-1"]']
    assert "anahtar" not in json.dumps(dosya["bekleme"]), "API anahtarı diske yazıldı"

    # Uygulama yeniden açıldı: kayıt yerinde, gönderilebilir.
    ortam.servis = ortam.yeni_servis()
    assert ortam.durum()["bekleyen"] == 1
    ortam.saat += vb.GERI_CEKILME_TAVANI
    assert ortam.calistir() is None
    assert ortam.durum()["gonderilen"] == 1 and ortam.durum()["bekleyen"] == 0


def test_ayni_bolum_7_gun_icinde_yeniden_gonderilmiyor(ortam):
    ortam.oynat()
    ortam.calistir()
    ortam.oynat()                          # aynı bölüm yeniden izlendi
    ortam.oynat(kayit(obj=Bolum(akis=akislar()[::-1])))     # başka akış sırasıyla da
    ortam.calistir()
    assert len(ortam.gonderilen) == 1

    ortam.saat += vb.TEKRAR_SURESI - 60
    ortam.oynat()
    ortam.calistir()
    assert len(ortam.gonderilen) == 1

    ortam.saat += 120                      # 7 gün doldu
    ortam.oynat()
    ortam.calistir()
    assert len(ortam.gonderilen) == 2


def test_kuyrukta_bekleyen_ikinci_kez_eklenmiyor(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.HATA, "ağ yok")]
    ortam.oynat()
    ortam.calistir()
    ortam.oynat()
    ortam.calistir()
    assert ortam.durum()["bekleyen"] == 1


def test_farkli_bolumler_ayri_kayit(ortam):
    ortam.oynat()
    ortam.oynat(kayit(obj=Bolum(kimlik="one-piece/bolum-2"), title="One Piece 2. Bölüm"))
    ortam.calistir()
    assert [g["bolum"]["kimlik"] for _u, _b, g in ortam.gonderilen] == [
        "one-piece/bolum-1", "one-piece/bolum-2"]


def test_ag_hatasinda_kayit_kalir_ustel_geri_cekilme(ortam, capsys):
    hata = vb.GonderimSonucu(vb.HATA, "Sunucuya ulaşılamadı (ConnectionError).")
    ortam.sonuclar = [hata, hata, hata]
    ortam.oynat()
    assert ortam.calistir() == pytest.approx(vb.GERI_CEKILME_TABANI)
    ortam.saat += vb.GERI_CEKILME_TABANI
    assert ortam.calistir() == pytest.approx(2 * vb.GERI_CEKILME_TABANI)
    ortam.saat += 2 * vb.GERI_CEKILME_TABANI
    assert ortam.calistir() == pytest.approx(4 * vb.GERI_CEKILME_TABANI)
    assert len(ortam.gonderilen) == 3
    d = ortam.durum()
    assert d["bekleyen"] == 1 and "ulaşılamadı" in d["son_hata"]["metin"]
    assert "yeniden denenecek" in capsys.readouterr().out

    # Geri çekilme dolmadan istek atılmıyor; dolunca gidiyor ve sıfırlanıyor.
    ortam.saat += 60
    assert ortam.servis.adim() > 0 and len(ortam.gonderilen) == 3
    ortam.saat += 4 * vb.GERI_CEKILME_TABANI
    assert ortam.calistir() is None
    assert ortam.dosya()["bekleme"]["ardisik"] == 0


def test_geri_cekilme_tavani(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.HATA, "5xx")] * 40
    ortam.oynat()
    for _ in range(30):
        bekle = ortam.calistir()
        ortam.saat += bekle
    assert bekle == pytest.approx(vb.GERI_CEKILME_TAVANI)


def test_429_retry_after_kadar_bekleniyor(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.BEKLE, "429", bekleme=120.0)]
    ortam.oynat()
    ortam.oynat(kayit(obj=Bolum(kimlik="one-piece/bolum-2")))
    assert ortam.calistir() == pytest.approx(120.0)
    assert len(ortam.gonderilen) == 1
    ortam.saat += 119
    assert ortam.servis.adim() == pytest.approx(1.0)
    assert len(ortam.gonderilen) == 1, "Retry-After dolmadan istek atıldı"
    ortam.saat += 1
    assert ortam.calistir() is None
    assert len(ortam.gonderilen) == 3
    assert ortam.durum()["bekleyen"] == 0


def test_429_uygulama_yeniden_acilsa_da_gecerli(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.BEKLE, "429", bekleme=600.0)]
    ortam.oynat()
    ortam.calistir()
    ortam.servis = ortam.yeni_servis()
    assert ortam.servis.adim() == pytest.approx(600.0)
    assert len(ortam.gonderilen) == 1


def test_400_kaydi_ve_kaynagi_dusuruyor(ortam, capsys):
    ortam.sonuclar = [vb.GonderimSonucu(vb.KAYNAK_REDDI, "Sunucu bu kaynağı kabul etmiyor (400).")]
    ortam.oynat()
    ortam.oynat(kayit(obj=Bolum(kimlik="one-piece/bolum-2")))
    ortam.calistir()
    assert len(ortam.gonderilen) == 1, "aynı kaynağın ikinci kaydı boşuna gitti"
    d = ortam.durum()
    assert (d["bekleyen"], d["dusurulen"], d["gonderilen"]) == (0, 2, 0)
    assert "kayıt düştü" in capsys.readouterr().out
    # Aynı kaynak 7 gün boyunca sorulmuyor; başka kaynak gidiyor.
    ortam.oynat(kayit(obj=Bolum(kimlik="one-piece/bolum-3")))
    ortam.oynat(kayit(kaynak="Animeler", kimlik="one-piece"))
    ortam.calistir()
    assert [g["kaynak"] for _u, _b, g in ortam.gonderilen] == ["animetr", "animeler"]


@pytest.mark.parametrize("kod", [413, 422])
def test_gecersiz_kayit_dusuyor_ve_tekrar_denenmiyor(ortam, kod, capsys):
    ortam.sonuclar = [vb.GonderimSonucu(vb.DUSUR, f"Sunucu kaydı geçersiz saydı ({kod}).")]
    ortam.oynat()
    ortam.calistir()
    d = ortam.durum()
    assert (d["bekleyen"], d["dusurulen"]) == (0, 1)
    assert str(kod) in d["son_hata"]["metin"]
    assert "kayıt düştü" in capsys.readouterr().out
    ortam.oynat()                          # aynı gövde aynı reddi alırdı
    ortam.calistir()
    assert len(ortam.gonderilen) == 1


def test_401_kayit_kalir_yapilandirma_degisince_hemen_denenir(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.UZUN, "Sunucu API anahtarını reddetti (401).")]
    ortam.oynat()
    assert ortam.calistir() == pytest.approx(vb.UZUN_BEKLEME)
    assert ortam.durum()["bekleyen"] == 1
    ortam.saat += 60
    assert ortam.servis.adim() > 0 and len(ortam.gonderilen) == 1
    ortam.ayar["sunucu api anahtari"] = "yeni-anahtar"       # kullanıcı düzeltti
    assert ortam.calistir() is None
    assert ortam.gonderilen[-1][1] == {"X-API-Key": "yeni-anahtar"}


def test_kapaliyken_bekleyen_kuyruk_siliniyor_gonderilmiyor(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.HATA, "ağ yok")]
    ortam.oynat()
    ortam.calistir()
    ortam.ayar["veri bagisi"] = False
    ortam.saat += vb.GERI_CEKILME_TAVANI
    assert ortam.servis.adim() is None
    assert ortam.durum()["bekleyen"] == 0 and ortam.dosya()["bekleyen"] == []
    assert len(ortam.gonderilen) == 1


def test_gonderimden_hemen_once_ayar_yeniden_okunuyor(ortam):
    """Kuyruğa alındıktan sonra ama istekten ÖNCE kapatıldıysa gitmemeli."""
    okumalar = {"n": 0}
    asil = ortam.servis._ayar_oku

    def oku():
        # 1: kanca, 2: adımın başı (kuyruğa alır), 3: istekten hemen önce.
        okumalar["n"] += 1
        return asil() if okumalar["n"] < 3 else dict(asil(), **{"veri bagisi": False})

    ortam.servis._ayar_oku = oku
    ortam.servis.oynatildi(kayit(), Video())
    ortam.servis.adim()
    assert okumalar["n"] == 3
    assert ortam.gonderilen == []
    assert ortam.servis.adim() is None and ortam.durum()["bekleyen"] == 0


def test_kuyruk_siniri_en_eskiyi_dusuruyor(ortam, monkeypatch, capsys):
    monkeypatch.setattr(vb, "KUYRUK_SINIRI", 3)
    for i in range(5):
        ortam.servis.kuyruk.ekle(govde(obj=Bolum(kimlik=f"one-piece/bolum-{i}")), ortam.saat)
    bekleyen = [json.loads(k["anahtar"])[2] for k in ortam.dosya()["bekleyen"]]
    assert bekleyen == ["one-piece/bolum-2", "one-piece/bolum-3", "one-piece/bolum-4"]
    assert ortam.durum()["dusurulen"] == 2
    assert "kuyruk dolu" in capsys.readouterr().out


def test_bolum_listesi_anime_basina_bir_kez(ortam):
    ortam.oynat()
    ortam.oynat(kayit(obj=Bolum(kimlik="one-piece/bolum-2")))
    ortam.calistir()
    listeler = [g["bolum_listesi"] for _u, _b, g in ortam.gonderilen]
    assert listeler[0] and listeler[1] is None
    ortam.saat += vb.TEKRAR_SURESI + 1
    ortam.oynat(kayit(obj=Bolum(kimlik="one-piece/bolum-3")))
    ortam.calistir()
    assert ortam.gonderilen[-1][2]["bolum_listesi"], "7 gün sonra liste yeniden gitmeli"


def test_bozuk_kuyruk_dosyasi_kenara_ayriliyor(tmp_path):
    yol = tmp_path / vb.KUYRUK_DOSYASI
    yol.write_text("{yarım json", encoding="utf-8")
    o = Ortam(yol)
    assert o.durum()["bekleyen"] == 0
    assert any(".bozuk-" in p.name for p in tmp_path.iterdir())
    o.oynat()
    assert o.calistir() is None and len(o.gonderilen) == 1


def test_elle_bozulmus_alanlar_duzeltiliyor(tmp_path):
    yol = tmp_path / vb.KUYRUK_DOSYASI
    yol.write_text(json.dumps({"bekleyen": [{"anahtar": 5}, "x", {"anahtar": "b", "govde": {}},
                                            {"anahtar": "a", "govde": {"kaynak": "animetr"}}],
                               "gorulen": {"a": "dun"}, "bekleme": {"kadar": "yarın"},
                               "sayac": {"gonderilen": -3}}), encoding="utf-8")
    d = Ortam(yol).durum()
    assert (d["bekleyen"], d["gonderilen"], d["bekleme_kadar"]) == (1, 0, 0.0)


def test_kuyrugu_temizle_ozelligi_kapatmiyor(ortam):
    ortam.sonuclar = [vb.GonderimSonucu(vb.HATA, "ağ yok")]
    ortam.oynat()
    ortam.calistir()
    assert ortam.servis.temizle() == 1
    assert ortam.durum()["bekleyen"] == 0
    ortam.oynat(kayit(obj=Bolum(kimlik="one-piece/bolum-9")))
    ortam.saat += vb.GERI_CEKILME_TAVANI
    ortam.calistir()
    assert ortam.durum()["gonderilen"] == 1


# ── Yanıt yorumu ─────────────────────────────────────────────────────────────
class Yanit:
    def __init__(self, kod, govde=None, basliklar=None):
        self.status_code = kod
        self._govde = govde
        self.headers = basliklar or {}

    def json(self):
        if isinstance(self._govde, Exception):
            raise self._govde
        return self._govde


@pytest.mark.parametrize("yanit, tur", [
    (Yanit(200, {"katki_id": "0123456789abcdef0123456789abcdef", "durum": "alindi"}), vb.TAMAM),
    (Yanit(201, {"durum": "alindi"}), vb.TAMAM),
    (Yanit(200, ValueError("html")), vb.HATA),             # her şeye 200 diyen yanlış adres
    (Yanit(200, {"ok": True}), vb.HATA),
    (Yanit(400, {"detail": "bilinmeyen kaynak"}), vb.KAYNAK_REDDI),
    (Yanit(401, {}), vb.UZUN), (Yanit(403, {}), vb.UZUN),
    (Yanit(404, {}), vb.UZUN), (Yanit(405, {}), vb.UZUN),
    (Yanit(302, None, {"Location": "https://baska.test/"}), vb.UZUN),
    (Yanit(413, {}), vb.DUSUR), (Yanit(422, {"detail": [{"loc": ["body"]}]}), vb.DUSUR),
    (Yanit(429, {}, {"Retry-After": "30"}), vb.BEKLE),
    (Yanit(500, {}), vb.HATA), (Yanit(502, ValueError("x")), vb.HATA),
])
def test_yanit_yorumu(yanit, tur):
    assert vb.yaniti_yorumla(yanit, 1_800_000_000.0).tur == tur


@pytest.mark.parametrize("baslik, beklenen", [
    ({"Retry-After": "30"}, 30.0),
    ({"retry-after": "7"}, 7.0),
    ({"Retry-After": "Fri, 15 Jan 2027 08:00:30 GMT"}, 30.0),
    ({}, vb.RETRY_AFTER_VARSAYILAN),
    ({"Retry-After": "bozuk"}, vb.RETRY_AFTER_VARSAYILAN),
    ({"Retry-After": "0"}, 1.0),
    ({"Retry-After": "99999999"}, vb.RETRY_AFTER_TAVANI),
])
def test_retry_after(baslik, beklenen):
    from email.utils import parsedate_to_datetime
    simdi = parsedate_to_datetime("Fri, 15 Jan 2027 08:00:00 GMT").timestamp()
    assert vb.retry_after(Yanit(429, {}, baslik), simdi) == pytest.approx(beklenen)


# ═════════════════════════════════════════════════════════════════════════════
# 4. Gerçek HTTP: 127.0.0.1'deki sahte sunucu
# ═════════════════════════════════════════════════════════════════════════════
class _Isleyici(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        uzunluk = int(self.headers.get("Content-Length") or 0)
        ham = self.rfile.read(uzunluk)
        self.server.istekler.append({"yol": self.path, "basliklar": dict(self.headers),
                                     "govde": json.loads(ham or b"null")})
        if self.server.cevaplar:
            kod, basliklar, cevap = self.server.cevaplar.pop(0)
        else:
            kod, basliklar, cevap = 200, {}, {"katki_id": "f" * 32, "durum": "alindi"}
        veri = json.dumps(cevap).encode("utf-8")
        self.send_response(kod)
        for ad, deger in basliklar.items():
            self.send_header(ad, deger)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(veri)))
        self.end_headers()
        self.wfile.write(veri)

    def log_message(self, *args):
        pass


@pytest.fixture
def sahte_sunucu():
    """`POST /katki/veri`'yi kaydeden yerel sunucu; ``cevaplar`` sıradaki yanıtlar."""
    srv = HTTPServer(("127.0.0.1", 0), _Isleyici)
    srv.istekler, srv.cevaplar = [], []
    srv.adres = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_gercek_gonderim_govdeyi_ve_anahtari_tasiyor(sahte_sunucu):
    g = govde()
    sonuc = vb.gonder(sahte_sunucu.adres + vb.UC_YOLU, {"X-API-Key": "anahtar"}, g)

    assert sonuc.tur == vb.TAMAM and sonuc.katki_id == "f" * 32
    (istek,) = sahte_sunucu.istekler
    assert istek["yol"] == "/katki/veri"
    assert istek["govde"] == g
    assert istek["basliklar"]["X-API-Key"] == "anahtar"
    assert "Cookie" not in istek["basliklar"] and "Referer" not in istek["basliklar"]


def test_yonlendirme_izlenmiyor_anahtar_tasinmiyor(sahte_sunucu):
    sahte_sunucu.cevaplar = [(307, {"Location": sahte_sunucu.adres + "/baska"}, {})]
    sonuc = vb.gonder(sahte_sunucu.adres + vb.UC_YOLU, {"X-API-Key": "anahtar"}, govde())
    assert sonuc.tur == vb.UZUN
    assert [i["yol"] for i in sahte_sunucu.istekler] == ["/katki/veri"]


def test_sunucu_yoksa_hata_kayit_kalir():
    sonuc = vb.gonder("http://127.0.0.1:1/katki/veri", {"X-API-Key": "k"}, govde(),
                      zaman_asimi=2)
    assert sonuc.tur == vb.HATA and "ulaşılamadı" in sonuc.mesaj


def test_sahte_sunucuyla_429_sonra_basari(tmp_path, sahte_sunucu):
    """Göndericinin gerçek HTTP yolu: 429'un Retry-After'ı kuyruğa işleniyor."""
    sahte_sunucu.cevaplar = [(429, {"Retry-After": "45"}, {"detail": "yavaş"})]
    o = Ortam(tmp_path / vb.KUYRUK_DOSYASI)
    o.ayar["sunucu adresi"] = sahte_sunucu.adres
    o.servis = vb.VeriBagisi(kuyruk_dosyasi=o.yol, ayar_oku=lambda: dict(o.ayar),
                             saat=lambda: o.saat, otomatik=False)
    o.oynat()
    assert o.calistir() == pytest.approx(45.0)
    assert o.durum()["bekleyen"] == 1
    o.saat += 45
    assert o.calistir() is None
    assert len(sahte_sunucu.istekler) == 2 and o.durum()["gonderilen"] == 1


def test_arka_plan_threadi_kuyrugu_bosaltip_kapaniyor(izole_ev, sahte_sunucu):
    """Kanca beklemez; gönderim servisin kendi thread'inde, iş bitince thread biter."""
    Dosyalar().set_ayar(ayar_list=dict(ACIK_AYAR, **{"sunucu adresi": sahte_sunucu.adres}))
    servis = vb.VeriBagisi()
    try:
        assert servis.oynatildi(kayit(), Video(), {"konum": 600, "sure": 1400, "sebep": "quit"})
        isci = servis._thread
        assert isci is not None and isci is not threading.current_thread()
        isci.join(10)
        assert not isci.is_alive()
        assert len(sahte_sunucu.istekler) == 1
        assert servis._thread is None, "iş bitince thread boşta beklememeli"
        assert servis.kuyruk.durum()["gonderilen"] == 1
        assert os.path.samefile(os.path.dirname(servis.kuyruk.yol), izole_ev)
    finally:
        servis.durdur()


# ═════════════════════════════════════════════════════════════════════════════
# 5. Kaynak katmanı: bölüm kimliği ve aday listesi
# ═════════════════════════════════════════════════════════════════════════════
def test_kayittan_bolumler_kaynak_kimligini_tasiyor():
    from turkanime_api.sources import kayit as kaynak_kaydi
    from turkanime_api.sources.adapter import kayittan_bolumler

    kaynak = kaynak_kaydi.Kaynak(
        "SahteVeri", "Sahte Veri", "SV", "#000", "SAHTEV",
        lambda: kaynak_kaydi.KaynakUclari(lambda q, limit=10: [],
                                          lambda _s: [("core:1:2", "2. Bölüm")],
                                          lambda _b: []),
        bolum_adresi=lambda b: "https://sahte.test/watch/" + b.replace(":", "/"))
    (bolum,) = kayittan_bolumler(kaynak, "core", "Sahte")
    assert bolum.kimlik == "core:1:2"          # adresten geri çevrilemeyen biçim


def test_best_video_aday_listesini_sakliyor(monkeypatch):
    import turkanime_api.sources.adapter as adapter_mod

    monkeypatch.setattr(adapter_mod, "extract_video_info",
                        lambda url, _o: {"url": url, "ext": "mp4"} if url == MAILRU else {})
    kaynagin = akislar()
    b = adapter_mod.AdapterBolum("https://s.test/1", "1. Bölüm",
                                 adapter_mod.AdapterAnime("s", "S"),
                                 stream_provider=lambda _u: kaynagin, kimlik="s/1")
    assert b.son_akislar == []
    vid = b.best_video(by_res=False, early_subset=8)
    assert vid.url == MAILRU
    # Elenmeden önceki TAM liste (oynayan dahil), kaynağın sırasıyla.
    assert [a["url"] for a in b.son_akislar] == [a["url"] for a in akislar()]
    b.son_akislar[0]["url"] = "degisti"
    assert kaynagin[0]["url"] == SIBNET, "kopya değil: kaynağın listesi bozuldu"


# ═════════════════════════════════════════════════════════════════════════════
# 6. Kancalar: gerçek oynatma ve indirme akışı
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture
def mpv(monkeypatch):
    """Sahte mpv: `raporlar`dan sıradakini konum betiğinin dosyasına yazar."""
    from turkanime_api.common import mpv_oynatici
    anahtar = "--script-opts-append=" + mpv_oynatici.KONUM_ANAHTARI + "="
    durum = {"argv": [], "raporlar": [], "kod": 0}

    class Proc:
        def __init__(self, argv):
            self.returncode = durum["kod"]
            durum["argv"].append(list(argv))
            rapor = durum["raporlar"].pop(0) if durum["raporlar"] else None
            yol = next((a[len(anahtar):] for a in argv if a.startswith(anahtar)), None)
            if rapor is not None and yol:
                with open(yol, "w", encoding="utf-8") as fp:
                    json.dump(rapor, fp)

        def wait(self):
            return self.returncode

    monkeypatch.setattr(mpv_oynatici, "mpv_bul", lambda: "/opt/sahte/mpv")
    monkeypatch.setattr(mpv_oynatici.sp, "Popen", Proc)
    return durum


def _gercek_bolum(monkeypatch):
    """Gerçek `AdapterBolum` (akışlar sahte, yt-dlp yoklaması sahte)."""
    import turkanime_api.sources.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "extract_video_info",
                        lambda url, _o: {"url": url, "ext": "mp4"} if url == MAILRU else {})
    return adapter_mod.AdapterBolum("https://animetr.test/izle/one-piece/bolum-1",
                                    "One Piece 1. Bölüm",
                                    adapter_mod.AdapterAnime("one-piece", "One Piece"),
                                    stream_provider=lambda _u: akislar(),
                                    player_name="ANIMETR", kimlik="one-piece/bolum-1")


def _oynat(main_window, qtbot, entry):
    main_window._on_play(entry)
    qtbot.waitUntil(lambda: main_window._playing is False, timeout=10000)


@pytest.fixture
def acik_sunucu(izole_ev, sahte_sunucu):
    Dosyalar().set_ayar(ayar_list=dict(ACIK_AYAR, **{"sunucu adresi": sahte_sunucu.adres}))
    return sahte_sunucu


def test_oynatma_kancasi_oynayani_ve_adaylari_gonderiyor(acik_sunucu, main_window, qtbot,
                                                         mpv, monkeypatch):
    mpv["raporlar"] = [{"konum": 734.2, "sure": 1420, "sebep": "quit"}]
    bolum = _gercek_bolum(monkeypatch)
    _oynat(main_window, qtbot, kayit(obj=bolum))

    qtbot.waitUntil(lambda: len(acik_sunucu.istekler) == 1, timeout=10000)
    g = acik_sunucu.istekler[0]["govde"]
    assert g["kaynak"] == "animetr" and g["bolum"]["kimlik"] == "one-piece/bolum-1"
    assert g["videolar"][0] == {"url": MAILRU, "oynatici": "MAIL", "fansub": "AnimeSue",
                                "etiket": "Mail.ru", "calisti": True}
    assert [v["url"] for v in g["videolar"][1:]] == [SIBNET]
    assert acik_sunucu.istekler[0]["basliklar"]["X-API-Key"] == "anahtar"
    metin = json.dumps(acik_sunucu.istekler[0])
    assert GIZLI_UA not in metin and "GIZLI" not in metin


@pytest.mark.parametrize("rapor, kod", [
    ({"konum": 3.0, "sure": 1420, "sebep": "quit"}, 0),     # açıp hemen kapattı
    ({"konum": 734.2, "sure": 1420, "sebep": "quit"}, 2),   # mpv oynatamadı
])
def test_gercek_oynatma_yoksa_gonderilmiyor(acik_sunucu, main_window, qtbot, mpv,
                                             monkeypatch, rapor, kod):
    mpv["raporlar"], mpv["kod"] = [rapor] * 3, kod
    _oynat(main_window, qtbot, kayit(obj=_gercek_bolum(monkeypatch)))
    qtbot.wait(300)
    assert acik_sunucu.istekler == []
    assert main_window.veri_bagisi.kuyruk.durum()["bekleyen"] == 0


def test_indirilmis_dosyadan_oynatma_gonderilmiyor(acik_sunucu, main_window, qtbot, mpv,
                                                   monkeypatch, tmp_path):
    mpv["raporlar"] = [{"konum": 734.2, "sure": 1420, "sebep": "eof"}]
    dosya = tmp_path / "one-piece-bolum-1.mp4"
    dosya.write_bytes(b"\x00" * 128)
    _oynat(main_window, qtbot, kayit(obj=_gercek_bolum(monkeypatch), yerel_dosya=str(dosya)))
    assert mpv["argv"][0][1] == os.path.abspath(dosya), "yerel dosya oynatılmadı"
    qtbot.wait(300)
    assert acik_sunucu.istekler == []


def test_kapaliyken_oynatma_hicbir_sey_gondermiyor(izole_ev, sahte_sunucu, main_window,
                                                    qtbot, mpv, monkeypatch):
    Dosyalar().set_ayar(ayar_list={"sunucu adresi": sahte_sunucu.adres,
                                   "sunucu api anahtari": "anahtar"})
    mpv["raporlar"] = [{"konum": 734.2, "sure": 1420, "sebep": "eof"}]
    _oynat(main_window, qtbot, kayit(obj=_gercek_bolum(monkeypatch)))
    qtbot.wait(300)
    assert sahte_sunucu.istekler == []
    assert not os.path.exists(main_window.veri_bagisi.kuyruk.yol)


def test_indirme_kancasi_indirileni_gonderiyor(acik_sunucu, izole_ev, main_window, qtbot,
                                               monkeypatch):
    """`DownloadManager` dosyası doğrulanan indirmeyi `indirildi` ile duyuruyor."""
    from turkanime_api.gui.qt import indirme
    from turkanime_api.gui.qt.indirme import BITMIS_DURUMLAR, DURUM_TAMAMLANDI

    bolum = Bolum()

    class IndenVideo(Video):
        def indir(self, callback=None, output=""):
            callback({"status": "finished"})

    bolum.best_video = lambda **_k: IndenVideo(MAILRU, "MAIL", "Mail.ru")
    monkeypatch.setattr(indirme, "run_bg",
                        lambda fn, *a, **k: fn(*a))              # işçi senkron
    tid = main_window.downloads.enqueue(kayit(obj=bolum), output=str(izole_ev / "indir"))
    qtbot.waitUntil(lambda: main_window.downloads.durum(tid) in BITMIS_DURUMLAR, timeout=5000)
    assert main_window.downloads.durum(tid) == DURUM_TAMAMLANDI

    qtbot.waitUntil(lambda: len(acik_sunucu.istekler) == 1, timeout=10000)
    g = acik_sunucu.istekler[0]["govde"]
    assert g["videolar"][0]["url"] == MAILRU and g["videolar"][0]["calisti"] is True


def test_basarisiz_indirme_duyurulmuyor(izole_ev, qtbot, monkeypatch):
    from turkanime_api.gui.qt import indirme

    mgr = indirme.DownloadManager()
    duyurulan: list = []
    mgr.indirildi.connect(lambda e, v: duyurulan.append(v))

    class Patlayan(Video):
        def indir(self, callback=None, output=""):
            raise RuntimeError("403")

    bolum = Bolum()
    bolum.best_video = lambda **_k: Patlayan()
    monkeypatch.setattr(indirme, "run_bg", lambda fn, *a, **k: fn(*a))
    tid = mgr.enqueue(kayit(obj=bolum), output=str(izole_ev))
    qtbot.waitUntil(lambda: mgr.durum(tid) in indirme.BITMIS_DURUMLAR, timeout=5000)
    qtbot.wait(50)
    assert duyurulan == []


def test_detay_bolum_listesi_kayitlara_ekleniyor(izole_ev, main_window, sahte_bolumler):
    """Bölüm listesi (kaynak kimliği + ad) kayda, ağ isteği olmadan ekleniyor."""
    b1, b2 = Bolum(kimlik="s/1"), Bolum(kimlik="s/2")
    sahte_bolumler({"AnimeTR": [{"title": "1. Bölüm", "obj": b1},
                                {"title": "2. Bölüm", "obj": b2},
                                {"title": "kimliksiz", "obj": object()}]})
    detay = main_window.detay
    rid = detay.ac_sonuc("AnimeTR", "seri", "Seri")
    detay.bolumler(rid, "AnimeTR")
    kayitlar = detay.oturum.bolumler["AnimeTR"]
    assert kayitlar[0]["bolum_listesi"] == [("s/1", "1. Bölüm"), ("s/2", "2. Bölüm")]
    assert kayitlar[0]["bolum_listesi"] is kayitlar[1]["bolum_listesi"]


# ═════════════════════════════════════════════════════════════════════════════
# 7. Sayfa: onay penceresi ve Ayarlar kartı (gerçek QtWebEngine)
# ═════════════════════════════════════════════════════════════════════════════
PENCERE = "document.querySelector('[data-soru=veri_bagisi_onayi]')"
ONAYLA = PENCERE + ".querySelector('.dugme.birincil')"
VAZGEC = PENCERE + ".querySelector('.dugme.cerceve')"
KUTU = PENCERE + ".querySelector('input[type=checkbox]')"


@pytest.fixture
def pencere(main_window, web):
    sonuc: list = []
    vb.onay_al(main_window.sorular, sonuc.append)
    web.bekle("!!" + PENCERE)
    web.sonuc = sonuc
    return web


def _bitti(web, beklenen):
    web.qtbot.waitUntil(lambda: bool(web.sonuc), timeout=5000)
    assert web.sonuc == [beklenen]
    web.bekle("!" + PENCERE)


def test_pencere_metni_duz_metin(pencere):
    assert pencere.js(PENCERE + ".querySelector('.bagis-metni').textContent") == vb.ONAY_METNI
    assert pencere.js(PENCERE + ".querySelector('.bagis-metni').children.length") == 0
    assert vb.ONAY_KUTUSU in pencere.js(PENCERE + ".innerText")
    assert pencere.js(ONAYLA + ".textContent") == vb.ONAY_DUGMESI


def test_onay_kutusuz_verilemiyor(pencere):
    assert pencere.js(ONAYLA + ".disabled") is True
    pencere.js(ONAYLA + ".click()")
    pencere.js(ONAYLA + ".disabled = false; " + ONAYLA + ".click()")
    pencere.qtbot.wait(150)
    assert pencere.sonuc == []


def test_enter_her_zaman_vazgeciyor(pencere):
    assert pencere.js("document.activeElement.textContent") == "Vazgeç"
    pencere.js(KUTU + ".click(); " + ONAYLA + ".focus()")
    pencere.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', "
               "{key: 'Enter', bubbles: true}))")
    _bitti(pencere, False)


@pytest.mark.parametrize("vazgec", [
    VAZGEC + ".click()",
    "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))",
    PENCERE + ".querySelector('.modal-baslik .ikon-dugme').click()",
    PENCERE + ".parentElement.click()",
], ids=["vazgec", "esc", "kapat", "dis-tik"])
def test_vazgecmenin_her_yolu_onay_degil(pencere, vazgec):
    pencere.js(KUTU + ".click()")
    pencere.js(vazgec)
    _bitti(pencere, False)


def test_bilerek_onay(pencere):
    pencere.js(KUTU + ".click()")
    pencere.js(ONAYLA + ".click()")
    _bitti(pencere, True)


KART = "document.querySelector('.ayar-karti[data-bolum=veri]')"
ANAHTAR = KART + ".querySelector('input[type=checkbox]')"


def _ayarlar_sayfasi(main_window, web):
    main_window.show_page("settings")
    web.bekle("!!" + KART)
    return web


def test_ayarlar_karti_durust_aciklama_ve_sayaclar(izole_ev, main_window, web):
    _ayarlar_sayfasi(main_window, web)
    metin = web.js(KART + ".innerText")
    for ifade in ("Ne gönderilir", "Ne gönderilmez", "Sunucu ne görür",
                  "oynayan video bağlantısı", "IP adresinden", "referer",
                  "Gönderilen", "Bekleyen", "Son hata", "Kapalı"):
        assert ifade in metin, ifade
    assert web.js(ANAHTAR + ".checked") is False
    assert web.js(KART + ".querySelector('[data-sayac=gonderilen]').textContent") == "0"
    assert web.js(KART + ".querySelector('.dugme-satiri .dugme').disabled") is True
    assert web.js("!!document.querySelector('.ayar-menu-ogesi[data-bolum=veri]')") is True


def test_anahtar_onay_ister_esc_geri_alir(izole_ev, main_window, web):
    _ayarlar_sayfasi(main_window, web)
    web.js(ANAHTAR + ".click()")
    web.bekle("!!" + PENCERE)
    # Pencere açıkken anahtar kilitli: ikinci tık ikinci pencere açmasın.
    web.bekle(ANAHTAR + ".disabled === true")
    assert Dosyalar().ayarlar["veri bagisi"] is False
    web.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    web.bekle("!" + PENCERE)
    web.bekle(ANAHTAR + ".checked === false && " + ANAHTAR + ".disabled === false")
    assert Dosyalar().ayarlar["veri bagisi"] is False
    assert web.js("document.querySelector('.kaydet-cubugu').hidden") is True, \
        "anahtar formu kirletmemeli ('Kaydet' onu yazamaz)"


def test_anahtar_onayla_acilir_kapatinca_kapanir(izole_ev, main_window, web):
    _ayarlar_sayfasi(main_window, web)
    web.js(ANAHTAR + ".click()")
    web.bekle("!!" + PENCERE)
    web.js(KUTU + ".click()")
    web.js(ONAYLA + ".click()")
    web.bekle("!" + PENCERE)
    web.bekle(ANAHTAR + ".checked === true && " + ANAHTAR + ".disabled === false")
    ayarlar = Dosyalar().ayarlar
    assert ayarlar["veri bagisi"] is True and ayarlar["veri bagisi onayi"] == vb.ONAY_SURUMU
    # Sunucu adresi boş: kart dürüstçe "gönderim yok" diyor.
    web.bekle(KART + ".querySelector('.veri-durumu').textContent.includes('gönderim yok')")

    web.js(ANAHTAR + ".click()")
    web.bekle(KART + ".querySelector('.veri-durumu').textContent.includes('Kapalı')")
    assert Dosyalar().ayarlar["veri bagisi"] is False
    assert web.js(ANAHTAR + ".checked") is False
