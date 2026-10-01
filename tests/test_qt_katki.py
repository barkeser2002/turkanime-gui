"""Oturum kimliği bağışı — ayar, onay ve geri çekme.

Buradaki testlerin ağırlık merkezi tek bir cümle: **onay verilmeden hiçbir şey
gönderilmez.** Bu yüzden gönderim fonksiyonu sahtelenirken "çağrılmadı" kontrolü
her kapı için ayrı ayrı yapılıyor (ayar kapalı, ayar açık ama onay yok, onay
penceresi kaza sonucu onaylanamaz). Kalan testler metnin dürüstlüğünü ve geri
çekmenin numarayı temizlediğini bekliyor.

Onay penceresi eskiden Qt diyaloğuydu (`KimlikBagisDialog`); artık web
arayüzünde (`gui.web.katki.onay_al`, tür ``bagis_onayi``). Diyaloğun
güvenceleri (pasif onay düğmesi, odak Vazgeç'te, Enter bağış yapmaz) burada
gerçek QtWebEngine sayfasında sınanıyor; Python tarafı ayrıca yalnızca
``{"onay": true, "okudum": true}``'yu onay sayıyor.

Ağa çıkılmıyor: `katki.bagis_gonder` / `bagis_geri_cek` sahteleniyor,
gerçek HTTP yolunun test edildiği yerde de yapılandırma boş bırakılıp isteğin
hiç başlamadığı doğrulanıyor.
"""
from __future__ import annotations

import pytest

from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.gui.web import katki
from turkanime_api.gui.web.uclar_ayarlar import bagis_kimlikleri

# Casusun sahtelemediği gerçek fonksiyon (akışın tamamını sınayanlar için).
ONAY_AL = katki.onay_al

CEREZ = (
    "# Netscape HTTP Cookie File\n"
    ".tranimeizle.io\tTRUE\t/\tTRUE\t0\t.AitrWeb.Session\tSAHTE-DEGER\n"
)


@pytest.fixture
def sayfa(ayar_uclari, izole_ev):
    """Geçici bir ev dizinine bağlı ayarlar uçları (gerçek ayarlara dokunmaz)."""
    return ayar_uclari()


def durum(sayfa) -> str:
    """Sayfanın durum satırına giden son mesaj."""
    son = sayfa.kopru.son("ayar_durum")
    return son["mesaj"] if son else ""


def geri_cek_acik(sayfa) -> bool:
    """Sayfa "Bağışımı geri çek"i numara listesi doluysa açıyor."""
    return bool(sayfa.ayarlar()["bagis"]["kimlikler"])


@pytest.fixture
def casus(monkeypatch):
    """`bagis_gonder`/`bagis_geri_cek`/`onay_al` çağrılarını kaydeden sahte.

    Varsayılan olarak onay VERİLMEZ: bir testin gönderim görmesi için onayı
    açıkça açması gerekir, tersi değil.
    """
    kayit = {"gonderim": [], "geri_cekme": [], "onay_sorusu": 0}
    ayar = {"onay": False, "gonderim_hatasi": None, "geri_cekme_hatasi": None}

    def _onay_al(sorular, kaynak, geri):
        kayit["onay_sorusu"] += 1
        geri(ayar["onay"])

    def _gonder(deger, kaynak=katki.KAYNAK_TRANIME, ayarlar=None,
                zaman_asimi=15):
        kayit["gonderim"].append((deger, kaynak))
        if ayar["gonderim_hatasi"]:
            raise katki.KatkiHatasi(ayar["gonderim_hatasi"])
        return "BAGIS-1234"

    def _geri_cek(bagis_id, ayarlar=None, zaman_asimi=15):
        kayit["geri_cekme"].append(bagis_id)
        if ayar["geri_cekme_hatasi"]:
            raise katki.KatkiHatasi(ayar["geri_cekme_hatasi"])
        return True

    monkeypatch.setattr(katki, "onay_al", _onay_al)
    monkeypatch.setattr(katki, "bagis_gonder", _gonder)
    monkeypatch.setattr(katki, "bagis_geri_cek", _geri_cek)
    return kayit, ayar


# ── Ayarın varsayılanı ──────────────────────────────────────────────────────
def test_kimlik_paylasimi_varsayilan_kapali(izole_ev):
    """Yeni kurulumda bağış kapalı olmalı; açmak kullanıcının kararı."""
    ayarlar = Dosyalar().ayarlar
    assert ayarlar["kimlik paylas"] is False
    assert ayarlar["kimlik bagis id"] == []
    # Sunucu adresi/anahtarı koda gömülü değil.
    assert ayarlar["sunucu adresi"] == ""
    assert ayarlar["sunucu api anahtari"] == ""


def test_sayfa_kapali_ayari_yansitiyor(sayfa):
    assert sayfa.ayarlar()["degerler"]["kimlik_paylas"] is False
    assert geri_cek_acik(sayfa) is False


# ── Onay verilmeden gönderim yok (asıl mesele) ──────────────────────────────
def test_ayar_kapaliyken_onay_bile_sorulmuyor(sayfa, casus):
    """Kapalı ayar: pencere açılmaz, gönderim olmaz."""
    kayit, _ = casus
    sayfa._cerez_geldi(CEREZ)

    assert kayit["onay_sorusu"] == 0
    assert kayit["gonderim"] == []
    assert bagis_kimlikleri(Dosyalar().ayarlar) == []


def test_onay_verilmezse_hicbir_sey_gonderilmiyor(sayfa, casus):
    """Ayar açık ama kullanıcı vazgeçti: çerez yerelde kalır, ağa çıkmaz."""
    kayit, ayar = casus
    ayar["onay"] = False
    Dosyalar().set_ayar("kimlik paylas", True)

    sayfa._cerez_geldi(CEREZ)

    assert kayit["onay_sorusu"] == 1        # soruldu
    assert kayit["gonderim"] == []          # ama gönderilmedi
    assert bagis_kimlikleri(Dosyalar().ayarlar) == []
    # Çerez yine de kaydedilmiş olmalı: bağışın reddi kontrolü boşa çıkarmaz.
    assert ".AitrWeb.Session" in Dosyalar().ayarlar.get("tranime_cookie", "")


def test_onay_verilirse_gonderiliyor_ve_numara_saklaniyor(sayfa, casus):
    kayit, ayar = casus
    ayar["onay"] = True
    Dosyalar().set_ayar("kimlik paylas", True)

    sayfa._cerez_geldi(CEREZ)

    assert [d for d, _ in kayit["gonderim"]] == [CEREZ]
    assert kayit["gonderim"][0][1] == katki.KAYNAK_TRANIME
    assert Dosyalar().ayarlar["kimlik bagis id"] == ["BAGIS-1234"]
    assert geri_cek_acik(sayfa) is True


def test_gonderim_basarisizsa_numara_yazilmiyor(sayfa, casus):
    """Sunucu reddettiyse "bağış var" yalanı ayarlara yazılmamalı."""
    kayit, ayar = casus
    ayar["onay"] = True
    ayar["gonderim_hatasi"] = "Sunucu hatası (503)."
    Dosyalar().set_ayar("kimlik paylas", True)

    sayfa._cerez_geldi(CEREZ)

    assert len(kayit["gonderim"]) == 1
    assert bagis_kimlikleri(Dosyalar().ayarlar) == []
    assert "gönderilemedi" in durum(sayfa)


def test_bos_cerez_teklif_bile_edilmiyor(sayfa, casus):
    kayit, ayar = casus
    ayar["onay"] = True
    Dosyalar().set_ayar("kimlik paylas", True)

    sayfa.kimlik_bagisi_teklif("")

    assert kayit["onay_sorusu"] == 0
    assert kayit["gonderim"] == []


# ── Onay metni ──────────────────────────────────────────────────────────────
def test_onay_metni_uc_kritik_ifadeyi_iceriyor():
    """Metin ne verildiğini, riskini ve geri dönüşü açıkça söylemeli."""
    metin = katki.onay_metni().lower()
    assert "senin adına giriş" in metin
    assert "hesabın kapanabilir" in metin
    assert "geri çekebilirsin" in metin


def test_onay_metni_pazarlama_dili_kullanmiyor():
    metin = katki.onay_metni()
    for yasak in ("destek ol", "bağış yaparak projeye", "teşekkür ederiz"):
        assert yasak not in metin.lower()
    # Site adı somut olmalı: "bir siteye" değil, hangi site olduğu yazılı.
    # `lower()` uygulanmıyor: "İ" küçüldüğünde birleşik noktalı "i̇" olur ve
    # düz "tranimeizle" araması bu yüzden tutmaz.
    assert "TRAnimeİzle" in metin


# ── Onay penceresi (sayfa) ──────────────────────────────────────────────────
PENCERE = "document.querySelector('[data-soru=bagis_onayi]')"
ONAY = PENCERE + ".querySelector('.dugme.tehlike')"
VAZGEC = PENCERE + ".querySelector('.dugme.cerceve')"
KUTU = PENCERE + ".querySelector('input[type=checkbox]')"


@pytest.fixture
def pencere(main_window, web):
    """Sayfada açılmış bağış onayı penceresi; ``sonuc`` `onay_al`'ın cevapları."""
    sonuc: list = []
    katki.onay_al(main_window.sorular, katki.KAYNAK_TRANIME, sonuc.append)
    web.bekle("!!" + PENCERE)
    web.sonuc = sonuc
    return web


def _bitti(web, beklenen):
    web.qtbot.waitUntil(lambda: bool(web.sonuc), timeout=5000)
    assert web.sonuc == [beklenen]
    web.bekle("!" + PENCERE)


def test_pencere_metni_duz_metin_olarak_gosteriyor(pencere):
    assert pencere.js(PENCERE + ".querySelector('.bagis-metni').textContent") == \
        katki.onay_metni()
    # HTML olarak yorumlanıp bir kısmı görünmez olmasın: düz metin, alt öğe yok.
    assert pencere.js(PENCERE + ".querySelector('.bagis-metni').children.length") == 0
    assert katki.ONAY_KUTUSU in pencere.js(PENCERE + ".innerText")
    assert pencere.js(PENCERE + ".getAttribute('aria-label')") == katki.ONAY_BASLIK


def test_onay_tek_tikla_verilemiyor(pencere):
    """Onay düğmesi, risk kutusu işaretlenmeden etkin olmamalı."""
    assert pencere.js(KUTU + ".checked") is False
    assert pencere.js(ONAY + ".disabled") is True
    # Pasif düğmeye tıklama (programla bile) hiçbir şey göndermez.
    pencere.js(ONAY + ".click()")
    pencere.qtbot.wait(150)
    assert pencere.sonuc == []

    pencere.js(KUTU + ".click()")
    assert pencere.js(ONAY + ".disabled") is False
    pencere.js(KUTU + ".click()")
    assert pencere.js(ONAY + ".disabled") is True
    # Kutu işaretsizken düğme bir şekilde etkinleşse de gönderilmez.
    pencere.js(ONAY + ".disabled = false; " + ONAY + ".click()")
    pencere.qtbot.wait(150)
    assert pencere.sonuc == []


def test_varsayilan_dugme_vazgec(pencere):
    """Odak Vazgeç'te; Enter — odak onay düğmesindeyken ve kutu işaretliyken
    bile — bağış yapmaz (Qt'de Enter varsayılan düğmeye, Vazgeç'e gidiyordu)."""
    assert pencere.js("document.activeElement.textContent") == "Vazgeç"
    pencere.js(KUTU + ".click(); " + ONAY + ".focus()")
    assert pencere.js(ONAY + ".disabled") is False
    assert pencere.js("document.activeElement.textContent") == katki.ONAY_DUGMESI
    pencere.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', "
               "{key: 'Enter', bubbles: true}))")
    _bitti(pencere, False)


@pytest.mark.parametrize("vazgec", [
    VAZGEC + ".click()",
    "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))",
    PENCERE + ".querySelector('.modal-baslik .ikon-dugme').click()",
    PENCERE + ".parentElement.click()",                     # dış tık
], ids=["vazgec", "esc", "kapat", "dis-tik"])
def test_vazgecmenin_her_yolu_onay_degil(pencere, vazgec):
    pencere.js(KUTU + ".click()")          # kutu işaretli olsa da
    pencere.js(vazgec)
    _bitti(pencere, False)


def test_bilerek_onay_veriliyor(pencere):
    pencere.js(KUTU + ".click()")
    pencere.js(ONAY + ".click()")
    _bitti(pencere, True)


def test_yalnizca_acik_onay_cevabi_onay_sayiliyor(soru_merkezi):
    """`onay_al` yalnızca {onay: true, okudum: true}'yu onay sayar (Accepted'ın karşılığı)."""
    for cevap, beklenen in ((None, False), (False, False), ({"onay": False}, False),
                            ({"onay": True}, False),                 # kutusuz onay
                            ({"onay": True, "okudum": False}, False),
                            ({"onay": 1, "okudum": 1}, False),
                            ({"onay": "true", "okudum": "true"}, False),
                            ([True], False),
                            ({"onay": True, "okudum": True}, True)):
        sonuc: list = []
        soru = katki.onay_al(soru_merkezi, katki.KAYNAK_TRANIME, sonuc.append)
        soru_merkezi.cevapla(soru.kimlik, cevap)
        assert sonuc == [beklenen], cevap


@pytest.mark.parametrize("bitir", ["kapanis", "sayfa_gitti", "teslim_yok"])
def test_cevapsiz_biten_pencere_onay_degil(soru_merkezi, qtbot, bitir):
    """Uygulama kapandı / sayfa öldü / sayfa hiç bağlanmadı: onay YOK."""
    sonuc: list = []
    if bitir == "teslim_yok":
        soru_merkezi.bagli = False
        soru = katki.onay_al(soru_merkezi, katki.KAYNAK_TRANIME, sonuc.append)
        soru._zamanlayici.start(1)
        qtbot.waitUntil(lambda: bool(sonuc), timeout=3000)
    elif bitir == "kapanis":
        katki.onay_al(soru_merkezi, katki.KAYNAK_TRANIME, sonuc.append)
        soru_merkezi.hepsini_bitir()
    else:
        katki.onay_al(soru_merkezi, katki.KAYNAK_TRANIME, sonuc.append)
        soru_merkezi.sayfa_gitti()
    assert sonuc == [False]


def test_soru_merkezi_yoksa_onay_yok():
    sonuc: list = []
    assert katki.onay_al(None, katki.KAYNAK_TRANIME, sonuc.append) is None
    assert sonuc == [False]


# ── Akışın tamamı: çerez → pencere → gönderim ───────────────────────────────
@pytest.fixture
def pencereli_sayfa(ayar_uclari, izole_ev, soru_merkezi, casus, monkeypatch):
    """Gerçek `onay_al` (casusun sahtesi geri alınmış) + sayfasız soru merkezi;
    gönderim yine casusta."""
    monkeypatch.setattr(katki, "onay_al", ONAY_AL)
    uclar = ayar_uclari(sorular=soru_merkezi)
    return uclar, soru_merkezi


def test_pencere_acikken_hicbir_sey_gonderilmiyor(pencereli_sayfa, casus):
    kayit, _ = casus
    sayfa, merkez = pencereli_sayfa
    Dosyalar().set_ayar("kimlik paylas", True)

    sayfa._cerez_geldi(CEREZ)

    (soru,) = merkez.bekleyenler("bagis_onayi")
    assert soru.veri["metin"] == katki.onay_metni()
    assert kayit["gonderim"] == [], "cevap gelmeden gönderim olmamalı"
    # Çerez yine de kaydedildi: bağış ayrı karar.
    assert ".AitrWeb.Session" in Dosyalar().ayarlar.get("tranime_cookie", "")

    merkez.cevapla(soru.kimlik, {"onay": True, "okudum": True})
    assert [d for d, _ in kayit["gonderim"]] == [CEREZ]
    assert Dosyalar().ayarlar["kimlik bagis id"] == ["BAGIS-1234"]


def test_vazgecilen_pencere_gondermiyor(pencereli_sayfa, casus):
    kayit, _ = casus
    sayfa, merkez = pencereli_sayfa
    Dosyalar().set_ayar("kimlik paylas", True)
    sayfa._cerez_geldi(CEREZ)
    (soru,) = merkez.bekleyenler("bagis_onayi")
    merkez.cevapla(soru.kimlik, {"onay": False})
    assert kayit["gonderim"] == []
    assert "bağışlanmadı" in durum(sayfa)


def test_pencere_acikken_ayar_kapatilirsa_gonderilmiyor(pencereli_sayfa, casus):
    """Kapı 1 gönderim anında yeniden denetleniyor: son söz "gönderme"."""
    kayit, _ = casus
    sayfa, merkez = pencereli_sayfa
    Dosyalar().set_ayar("kimlik paylas", True)
    sayfa._cerez_geldi(CEREZ)
    (soru,) = merkez.bekleyenler("bagis_onayi")

    Dosyalar().set_ayar("kimlik paylas", False)
    merkez.cevapla(soru.kimlik, {"onay": True, "okudum": True})

    assert kayit["gonderim"] == []
    assert bagis_kimlikleri(Dosyalar().ayarlar) == []
    assert "bağışlanmadı" in durum(sayfa)


def test_ayar_kapaliyken_pencere_hic_acilmiyor(pencereli_sayfa, casus):
    kayit, _ = casus
    sayfa, merkez = pencereli_sayfa
    sayfa._cerez_geldi(CEREZ)
    assert merkez.bekleyenler() == []
    assert kayit["gonderim"] == []


# ── Geri çekme ──────────────────────────────────────────────────────────────
def test_geri_cekme_numarayi_temizliyor(sayfa, casus):
    kayit, _ = casus
    Dosyalar().set_ayar("kimlik bagis id", ["BAGIS-1234"])
    assert geri_cek_acik(sayfa) is True

    sonuc = sayfa.bagis_geri_cek()

    assert kayit["geri_cekme"] == ["BAGIS-1234"]
    assert Dosyalar().ayarlar["kimlik bagis id"] == []
    assert geri_cek_acik(sayfa) is False
    assert "geri çekildi" in sonuc["mesaj"]


def test_geri_cekme_basarisizsa_numara_kaliyor(sayfa, casus):
    """Sunucudan silinemediyse yerel numara durmalı: tek silme anahtarı o."""
    kayit, ayar = casus
    ayar["geri_cekme_hatasi"] = "Sunucuya ulaşılamadı."
    Dosyalar().set_ayar("kimlik bagis id", ["BAGIS-1234"])

    sonuc = sayfa.bagis_geri_cek()

    assert kayit["geri_cekme"] == ["BAGIS-1234"]
    assert Dosyalar().ayarlar["kimlik bagis id"] == ["BAGIS-1234"]
    assert "geri çekilemedi" in sonuc["mesaj"]


def test_bagis_yokken_geri_cekme_aga_cikmiyor(sayfa, casus):
    kayit, _ = casus
    sayfa.bagis_geri_cek()
    assert kayit["geri_cekme"] == []


# ── Yapılandırma: boş ayar = projenin sunucusu ──────────────────────────────
def test_bos_ayar_projenin_sunucusu_ve_yerlesik_anahtar(yerlesik_anahtar):
    assert katki.sunucu_yapilandirmasi({}) == (
        katki.VARSAYILAN_SUNUCU_ADRESI, yerlesik_anahtar)
    assert katki.VARSAYILAN_SUNUCU_ADRESI.startswith("https://")


def test_kullanici_anahtari_projenin_sunucusunda_kullaniliyor():
    assert katki.sunucu_yapilandirmasi({"sunucu api anahtari": " k "}) == (
        katki.VARSAYILAN_SUNUCU_ADRESI, "k")


@pytest.mark.parametrize("adres", ["https://turkanimeapi.bariskeser.com/",
                                   "HTTPS://TurkAnimeApi.bariskeser.com"])
def test_varsayilan_adres_elle_yazilinca_da_yerlesik_anahtar(adres, yerlesik_anahtar):
    assert katki.sunucu_yapilandirmasi({"sunucu adresi": adres})[1] == yerlesik_anahtar


@pytest.mark.parametrize("adres", [
    "https://baska.test",
    "http://turkanimeapi.bariskeser.com",                    # şifresiz
    "https://turkanimeapi.bariskeser.com.saldirgan.test",
])
def test_yerlesik_anahtar_baska_sunucuya_gitmiyor(adres, monkeypatch, yerlesik_anahtar):
    """Projenin anahtarı, ayarlara yazılmış üçüncü bir sunucuya sızmamalı.

    `bagis_gonder` ağ hatasını da `KatkiHatasi`ya çeviriyor; bu yüzden yalnızca
    hatayı değil, isteğin hiç başlamadığını da ayrıca denetliyoruz.
    """
    import requests
    istekler = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: istekler.append((a, k)))
    assert katki.sunucu_yapilandirmasi({"sunucu adresi": adres})[1] == ""
    with pytest.raises(katki.KatkiHatasi):
        katki.bagis_gonder(CEREZ, ayarlar={"sunucu adresi": adres})
    assert istekler == []


def test_api_anahtari_yoksa_gonderim_baslamiyor():
    with pytest.raises(katki.KatkiHatasi):
        katki.bagis_gonder(
            CEREZ, ayarlar={"sunucu adresi": "http://127.0.0.1:1"})


def test_bos_cerez_gonderilmiyor():
    with pytest.raises(katki.KatkiHatasi):
        katki.bagis_gonder("", ayarlar={"sunucu adresi": "http://127.0.0.1:1",
                                               "sunucu api anahtari": "k"})


def test_yapilandirma_ayarlardan_okunuyor(izole_ev):
    Dosyalar().set_ayar(ayar_list={"sunucu adresi": "https://ornek.test/",
                                   "sunucu api anahtari": " gizli "})
    assert katki.sunucu_yapilandirmasi() == ("https://ornek.test", "gizli")


def test_ayarlar_sayfasi_sunucu_bilgisini_kaydediyor(sayfa):
    sayfa.ayarlari_kaydet({"kimlik_paylas": True, "sunucu_adresi": "https://ornek.test",
                           "sunucu_anahtari": " gizli "})

    ayarlar = Dosyalar().ayarlar
    assert ayarlar["kimlik paylas"] is True
    assert ayarlar["sunucu adresi"] == "https://ornek.test"
    assert ayarlar["sunucu api anahtari"] == "gizli"
