"""AnimPow kaynağı (`sources/animpow.py`) — ağsız testler.

Site iki arka uçtan besleniyor ve ikisi de burada taklit ediliyor (`SahteSite`):

- **Eski içerik API'si** (client-api.animpow.com): sahte sunucu gerçeği gibi
  davranır — el sıkışmada gelen AES anahtarını kendi RSA özel anahtarıyla açar,
  sonraki her yanıtı o anahtarla AES-256-GCM ile şifreler, X-Session-Id'si
  bilinmeyen isteği 400 ile geri çevirir. Böylece şifre protokolü uçtan uca
  sınanıyor; modül sahte sunucunun açık anahtarını her oturumda yeniden
  aldığı için çalışıyor (koda gömülü anahtar olsaydı düşerdi).
- **QuadroGG** (quadrogg.best): düz JSON.

Fikstürler (`tests/fixtures/animpow/`) sitenin 2026-09'da verdiği GERÇEK
yanıtlardan kırpıldı (uzun açıklama/görsel değerleri kısaltıldı, yapı aynı).
`client-api-arama-frieren-sifreli.json` gerçek bir şifreli yanıt, o oturumun
anahtarı ve çözülmüş hâli.

curl_cffi kendi (libcurl) soketlerini açtığı için conftest'in ağ mandalı onu
YAKALAMIYOR: bu dosyadaki her test `animpow._session`'ı sahteler; sahtesiz bir
istek testi düşürür (`_yalitim`). Tek canlı test `@pytest.mark.network`.
"""
from __future__ import annotations

import ast
import base64
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import pytest
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA

from turkanime_api.sources import animpow, kayit
from turkanime_api.sources.animpow import AnimPowHatasi, AnimPowHizSiniri

KOK = Path(__file__).resolve().parent.parent
FIKSTUR = Path(__file__).resolve().parent / "fixtures" / "animpow"

JJK = "9072-D03257F18BEC"
NARUTO = "9072-19786DE3C7B2"
FRIEREN = "9072-00004C82E101"
ONE_PIECE = "9072-18D4E79F4FC3"
FILM_RED = "9072-B9A333F02D09"


def fikstur(ad: str) -> Any:
    return json.loads((FIKSTUR / ad).read_text(encoding="utf-8"))


def _b64(veri: bytes) -> str:
    return base64.b64encode(veri).decode("ascii")


def sifrele(veri: Any, anahtar: bytes) -> Dict[str, str]:
    """Sunucunun zarfı: {"iv", "authTag", "data"} (AES-256-GCM)."""
    iv = os.urandom(12)
    sifre = AES.new(anahtar, AES.MODE_GCM, nonce=iv)
    metin, etiket = sifre.encrypt_and_digest(json.dumps(veri).encode("utf-8"))
    return {"iv": _b64(iv), "authTag": _b64(etiket), "data": _b64(metin)}


# ─────────────────────────────────────────────────────────────────────────────
# Sahte site
# ─────────────────────────────────────────────────────────────────────────────
class SahteYanit:
    def __init__(self, status_code: int, govde: Any = None, metin: Optional[str] = None):
        self.status_code = status_code
        self.text = metin if metin is not None else json.dumps(govde, ensure_ascii=False)
        self.headers = {"Content-Type": "application/json"}

    def json(self) -> Any:
        return json.loads(self.text)


class SahteSite:
    """client-api.animpow.com + quadrogg.best taklidi.

    ``eski`` / ``quadro``: yol → gövde, `SahteYanit` ya da ``sorgu -> ...``.
    Yolu tanımlı olmayan istek sitenin gerçek "bulunamadı" yanıtını alır:
    eski API 500 {"ok": false, "error": "...404 Not Found..."}, QuadroGG 404.
    """

    def __init__(self, rsa_anahtari):
        self.rsa = rsa_anahtari
        self.oturumlar: Dict[str, bytes] = {}
        self.istekler: List[Tuple[str, str, Dict[str, str]]] = []
        self.eski: Dict[str, Any] = {}
        self.quadro: Dict[str, Any] = {}
        self.el_sikisma_sayisi = 0
        self.el_sikisma_durumu = 200

    # requests/curl_cffi yüzeyi
    def get(self, url, headers=None, timeout=None, **_k):
        return self._istek("GET", url, headers or {}, timeout, None)

    def post(self, url, headers=None, timeout=None, data=None, **k):
        govde = k.get("json")
        if govde is None and data:
            govde = json.loads(data)
        return self._istek("POST", url, headers or {}, timeout, govde)

    def sayi(self, parca: str) -> int:
        return sum(1 for _, url, _ in self.istekler if parca in url)

    # yönlendirme
    def _istek(self, yontem, url, basliklar, zaman_asimi, govde):
        assert zaman_asimi, f"zaman aşımı verilmeyen istek: {url}"
        self.istekler.append((yontem, url, dict(basliklar)))
        parca = urlsplit(url)
        sorgu = {k: v[0] for k, v in parse_qs(parca.query).items()}
        if parca.hostname == "client-api.animpow.com":
            assert parca.path.startswith("/api/v1/")
            return self._eski_api(yontem, parca.path[len("/api/v1"):], sorgu, basliklar, govde)
        if parca.hostname == "quadrogg.best":
            return self._cevap(self.quadro.get(parca.path), sorgu,
                               SahteYanit(404, {"success": False, "message": "Anime not found"}),
                               lambda v: SahteYanit(200, v))
        raise AssertionError(f"beklenmeyen konak: {url}")

    @staticmethod
    def _cevap(tanim, sorgu, yok, sar):
        if callable(tanim):
            tanim = tanim(sorgu)
        if tanim is None:
            return yok
        if isinstance(tanim, (SahteYanit, BaseException)):
            if isinstance(tanim, BaseException):
                raise tanim
            return tanim
        return sar(tanim)

    def _eski_api(self, yontem, yol, sorgu, basliklar, govde):
        if yol == "/auth/public-key":
            pem = self.rsa.publickey().export_key().decode("ascii")
            return SahteYanit(200, {"publicKey": pem})
        if yol == "/auth/handshake":
            assert yontem == "POST"
            self.el_sikisma_sayisi += 1
            if self.el_sikisma_durumu != 200:
                return SahteYanit(self.el_sikisma_durumu, {"ok": False, "error": "Too many"})
            anahtar = PKCS1_OAEP.new(self.rsa, hashAlgo=SHA256).decrypt(
                base64.b64decode(govde["encryptedKey"]))
            assert len(anahtar) == 32, "AES-256 anahtarı 32 bayt olmalı"
            self.oturumlar[govde["sessionId"]] = anahtar
            return SahteYanit(200, {"ok": True})
        sid = basliklar.get("X-Session-Id")
        if not sid:
            return SahteYanit(400, {"ok": False,
                                    "error": "Encryption is required for this API route."})
        if sid not in self.oturumlar:
            return SahteYanit(400, {"ok": False, "error": "Invalid or expired session key"})
        bulunamadi = SahteYanit(500, {
            "ok": False,
            "error": f"AnimPow API error: 404 Not Found (https://api-v3.animpow.com/api/v1{yol})"})
        return self._cevap(self.eski.get(yol), sorgu, bulunamadi,
                           lambda v: SahteYanit(200, sifrele(v, self.oturumlar[sid])))


@pytest.fixture(scope="module")
def rsa_anahtari():
    return RSA.generate(2048)


@pytest.fixture(autouse=True)
def _yalitim(request, monkeypatch):
    """Her test temiz modül durumuyla (oturum, şifre, önbellek) ve AĞSIZ başlar."""
    animpow.sifirla()
    if "network" not in request.keywords:
        def _ag_yok():
            raise AssertionError("AnimPow testi gerçek ağa çıkmaya çalıştı; "
                                 "`site` fikstürünü kullanın")
        monkeypatch.setattr(animpow, "_session", _ag_yok)
    yield
    animpow.sifirla()


@pytest.fixture
def site(monkeypatch, rsa_anahtari):
    sahte = SahteSite(rsa_anahtari)
    monkeypatch.setattr(animpow, "_session", lambda: sahte)
    return sahte


def _bolum_sorgusu(sezon: int, bolum: int, veri: Any):
    """QuadroGG /episodes: yalnızca istenen (sezon, bölüm) için veri, yoksa boş."""
    def cevap(sorgu):
        if (sorgu.get("season"), sorgu.get("episode")) == (str(sezon), str(bolum)):
            return veri
        return {"success": True, "data": []}
    return cevap


def jjk_yukle(site: SahteSite) -> None:
    site.eski[f"/anime/{JJK}/bolumler"] = fikstur("client-api-bolumler-jjk.json")
    site.quadro[f"/api/animes/get-from-core-id-{JJK}/data-list"] = \
        fikstur("quadrogg-datalist-jjk.json")
    site.quadro[f"/api/animes/get-from-core-id-{JJK}/episodes"] = \
        _bolum_sorgusu(1, 1, fikstur("quadrogg-episodes-jjk-s1e1.json"))


def naruto_aramasi_yukle(site: SahteSite) -> None:
    site.eski["/anime/arama"] = lambda q: fikstur("client-api-arama-naruto.json")
    site.quadro["/api/animes/catalog"] = lambda q: fikstur("quadrogg-catalog-naruto.json")


# ─────────────────────────────────────────────────────────────────────────────
# 1) Kayıt
# ─────────────────────────────────────────────────────────────────────────────
def test_kayit_kaynagi_aciyor():
    kaynak = kayit.bul("AnimPow")
    assert kaynak is not None
    assert kayit.KAYNAKLAR.index(kaynak) > kayit.KAYNAKLAR.index(kayit.bul("Tranimaci")), "yeni kaynak eski kaynakların arkasına eklenmeli"
    assert (kaynak.ad, kaynak.etiket, kaynak.kisaltma, kaynak.renk, kaynak.oynatici) == \
        ("AnimPow", "AnimPow", "AP", "#fd79a8", "ANIMPOW")
    assert (kaynak.modul, kaynak.cli_kodu) == ("animpow", "animpow")
    assert kaynak.taranabilir and kaynak.oynatilabilir
    for ad in ("animpow", "ANIMPOW", "AnimPow"):
        assert kayit.bul(ad) is kaynak
    assert kaynak in kayit.cli_kaynaklari()
    assert kaynak in kayit.tarayici_kaynaklari()

    uclar = kaynak.uclar()
    assert uclar.ara is animpow.search_animpow
    assert uclar.bolumler is animpow.get_anime_episodes
    assert uclar.akislar is animpow.get_episode_streams


def test_bolum_adresi_sitenin_izleme_sayfasi():
    kaynak = kayit.bul("AnimPow")
    assert kaynak.bolum_adresi(f"{JJK}:1:1") == f"https://animpow.com/watch/{JJK}/s1e1"
    assert kaynak.bolum_adresi("960:2:13") == "https://animpow.com/watch/960/s2e13"
    # Biçime uymayan kimlik çökertmemeli (kayıt testleri sahte kimlik veriyor).
    assert kaynak.bolum_adresi("17/b1") == "https://animpow.com/watch/17/b1"


def test_kayit_modulu_animpowu_tembel_yukluyor():
    """kayit.py modül düzeyinde kaynak import etmez (sunucu imajı hafif kalsın)."""
    agac = ast.parse((KOK / "turkanime_api/sources/kayit.py").read_text("utf-8"))
    ust_duzey = {d.module for d in agac.body if isinstance(d, ast.ImportFrom)}
    assert "animpow" not in ust_duzey


def test_kayit_uzerinden_bolumler_ve_akislar(site):
    """Uygulamanın yolu: kayıt → AdapterBolum → akış sağlayıcı."""
    from turkanime_api.sources.adapter import kayittan_bolumler

    jjk_yukle(site)
    bolumler = kayittan_bolumler(kayit.bul("AnimPow"), JJK, "Jujutsu Kaisen")
    assert len(bolumler) == 59
    ilk = bolumler[0]
    assert ilk.url == f"https://animpow.com/watch/{JJK}/s1e1"
    assert ilk.title == "1. Sezon 1. Bölüm - Ryomen Sukuna"
    assert ilk._player_name == "ANIMPOW"
    akislar = ilk._saglayici()(ilk.url)
    assert akislar[0]["type"] == "hls"
    assert ilk.fansubs[0] == "Adonis Fansub"
    assert "FGL Çeviri" in ilk.fansubs


# ─────────────────────────────────────────────────────────────────────────────
# 2) Şifre protokolü
# ─────────────────────────────────────────────────────────────────────────────
def test_gercek_sifreli_yanit_cozuluyor():
    """Sitenin gerçek bir yanıtı, o oturumun anahtarıyla aynen açılmalı."""
    ornek = fikstur("client-api-arama-frieren-sifreli.json")
    anahtar = base64.b64decode(ornek["aes_key_b64"])
    assert animpow._coz(ornek["response"], anahtar) == ornek["decrypted"]


def test_yanlis_anahtar_ya_da_bozuk_etiket_acik_hata():
    ornek = fikstur("client-api-arama-frieren-sifreli.json")
    with pytest.raises(AnimPowHatasi, match="şifresi çözülemedi"):
        animpow._coz(ornek["response"], b"\x00" * 32)
    bozuk = dict(ornek["response"], authTag=_b64(b"\x01" * 16))
    with pytest.raises(AnimPowHatasi, match="şifresi çözülemedi"):
        animpow._coz(bozuk, base64.b64decode(ornek["aes_key_b64"]))


def test_zarf_olmayan_govde_aynen_doner():
    assert animpow._coz({"ok": True, "veri": []}, b"\x00" * 32) == {"ok": True, "veri": []}


def test_gercek_acik_anahtarla_el_sikisma_istegi(monkeypatch):
    """Sitenin gerçek PEM'i okunuyor; istek sitenin istemcisinin gönderdiğiyle aynı."""
    pem = fikstur("client-api-public-key.json")["publicKey"]
    gonderilen: Dict[str, Any] = {}

    class Oturum:
        def get(self, url, headers=None, timeout=None, **_k):
            assert timeout and url.endswith("/auth/public-key")
            return SahteYanit(200, {"publicKey": pem})

        def post(self, url, headers=None, timeout=None, data=None, **_k):
            assert timeout and url.endswith("/auth/handshake")
            gonderilen.update(json.loads(data), basliklar=headers)
            return SahteYanit(200, {"ok": True})

    monkeypatch.setattr(animpow, "_session", Oturum)
    sid, anahtar = animpow._el_sik()
    assert len(anahtar) == 32
    assert gonderilen["sessionId"] == sid and uuid.UUID(sid).version == 4
    # RSA-2048 → 256 baytlık şifreli anahtar.
    assert len(base64.b64decode(gonderilen["encryptedKey"])) == 256
    assert gonderilen["basliklar"]["Content-Type"] == "application/json"


def test_tek_el_sikisma_ve_oturum_basligi(site):
    naruto_aramasi_yukle(site)
    jjk_yukle(site)
    animpow.search_animpow("naruto")
    animpow.get_anime_episodes(JJK)
    animpow.get_episode_streams(f"{JJK}:1:1")
    assert site.el_sikisma_sayisi == 1
    assert site.sayi("/auth/public-key") == 1
    sid = next(iter(site.oturumlar))
    eski = [b for _, url, b in site.istekler
            if "client-api" in url and "/auth/" not in url]
    assert eski and all(b.get("X-Session-Id") == sid for b in eski)
    assert all(b.get("Referer") == "https://animpow.com/" for b in eski)


def test_acik_anahtar_her_oturumda_yeniden_aliniyor(monkeypatch, rsa_anahtari):
    """Anahtar koda gömülü değil: site anahtarını değiştirse de çalışır."""
    birinci = SahteSite(rsa_anahtari)
    birinci.eski["/anime/arama"] = lambda q: {"ok": True, "veri": [
        {"animpow_core_id": JJK, "name": "Jujutsu Kaisen", "is_ecchi": False}]}
    monkeypatch.setattr(animpow, "_session", lambda: birinci)
    assert animpow.search_animpow("jujutsu") == [(JJK, "Jujutsu Kaisen")]

    animpow.sifirla()
    ikinci = SahteSite(RSA.generate(1024))
    ikinci.eski = birinci.eski
    monkeypatch.setattr(animpow, "_session", lambda: ikinci)
    assert animpow.search_animpow("jujutsu") == [(JJK, "Jujutsu Kaisen")]
    assert ikinci.el_sikisma_sayisi == 1


def test_sunucu_oturumu_unutunca_yeniden_el_sikisiliyor(site):
    naruto_aramasi_yukle(site)
    ilk = animpow.search_animpow("naruto")
    site.oturumlar.clear()           # sunucu yeniden başladı / anahtarın süresi doldu
    assert animpow.search_animpow("naruto") == ilk
    assert site.el_sikisma_sayisi == 2


def test_el_sikisma_hiz_siniri_acik_hata(site):
    site.el_sikisma_durumu = 429
    with pytest.raises(AnimPowHatasi, match="429") as bilgi:
        animpow.get_anime_episodes(FRIEREN)
    assert isinstance(bilgi.value.__cause__, AnimPowHizSiniri)


def test_crypto_yoksa_yalnizca_quadrogg(site, monkeypatch):
    """Sunucu imajında pycryptodome yok: kaynak QuadroGG ile çalışmaya devam eder."""
    for ad in ("Crypto", "Crypto.Cipher", "Crypto.Hash", "Crypto.PublicKey"):
        monkeypatch.setitem(sys.modules, ad, None)        # import → ImportError
    naruto_aramasi_yukle(site)
    jjk_yukle(site)
    site.quadro[f"/api/animes/get-from-core-id-{FRIEREN}/data-list"] = \
        fikstur("quadrogg-datalist-frieren.json")

    sonuc = animpow.search_animpow("naruto", limit=30)
    assert [k for k, _ in sonuc][:1] == [NARUTO] and len(sonuc) == 17
    assert sonuc[0][1] == "Naruto"                          # QuadroGG'nin adı
    assert len(animpow.get_anime_episodes(JJK)) == 59
    assert [a["type"] for a in animpow.get_episode_streams(f"{JJK}:1:1")] == ["hls"] * 3
    # Hiç sonuç yoksa sebep söylenir, boş liste "bölüm yok" gibi görünmez.
    with pytest.raises(AnimPowHatasi, match="pycryptodome"):
        animpow.get_anime_episodes(FRIEREN)
    assert site.sayi("client-api") == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3) Arama
# ─────────────────────────────────────────────────────────────────────────────
def test_arama_iki_arka_ucu_birlestiriyor(site):
    naruto_aramasi_yukle(site)
    sonuc = animpow.search_animpow("naruto", limit=30)
    kimlikler = [k for k, _ in sonuc]
    assert len(kimlikler) == len(set(kimlikler)) == 17, "core kimliğiyle tekilleşmeli"
    # Önce eski API (14), sonra yalnızca QuadroGG'de olanlar (3).
    eski = [v["animpow_core_id"] for v in fikstur("client-api-arama-naruto.json")["veri"]]
    assert kimlikler[:14] == eski
    assert sonuc[0] == (NARUTO, "NARUTO")                  # name_english
    assert sonuc[1] == ("9072-1431B3C86FAA", "Naruto Shippuden")
    assert ("9072-2DE6AB8D66F2", "Zom 100: Bucket List of the Dead") in sonuc[14:]

    sorgu = next(url for _, url, _ in site.istekler if "/anime/arama" in url)
    assert parse_qs(urlsplit(sorgu).query) == {"q": ["naruto"], "limit": ["30"]}
    katalog = next(url for _, url, _ in site.istekler if "/catalog" in url)
    assert parse_qs(urlsplit(katalog).query)["q"] == ["naruto"]


def test_arama_limit_ve_bosluk(site):
    naruto_aramasi_yukle(site)
    sonuc = animpow.search_animpow("  naruto  ", limit=5)
    assert len(sonuc) == 5
    sorgu = next(url for _, url, _ in site.istekler if "/anime/arama" in url)
    assert parse_qs(urlsplit(sorgu).query) == {"q": ["naruto"], "limit": ["20"]}


def test_arama_sonuc_yoksa_bos_liste(site):
    site.eski["/anime/arama"] = lambda q: {"ok": True, "veri": [], "meta": {"toplam": 0}}
    site.quadro["/api/animes/catalog"] = lambda q: {"success": True, "data": []}
    assert animpow.search_animpow("yokboyle") == []


def test_bos_sorgu_aga_cikmaz(site):
    assert animpow.search_animpow("") == []
    assert animpow.search_animpow("   ") == []
    assert site.istekler == []


def test_arama_ecchi_ve_gecersiz_kimlik_atiliyor(site):
    site.eski["/anime/arama"] = lambda q: {"ok": True, "veri": [
        {"animpow_core_id": "9072-AAAAAAAAAAAA", "name": "Ecchi", "is_ecchi": True},
        {"animpow_core_id": "../account", "name": "Kötü kimlik", "is_ecchi": False},
        {"animpow_core_id": "960", "name": "Sousou no Frieren: no Mahou",
         "name_english": None, "is_ecchi": False},
    ]}
    site.quadro["/api/animes/catalog"] = lambda q: {"success": True, "data": [
        {"animpow_core_id": "9072-BBBBBBBBBBBB", "name": "Ecchi 2", "is_ecchi": 1},
        {"animpow_core_id": None, "name": "Kimliksiz"},
    ]}
    assert animpow.search_animpow("x") == [("960", "Sousou no Frieren: no Mahou")]


def test_arama_bir_arka_uc_cokerse_digeri_doner(site, caplog):
    naruto_aramasi_yukle(site)
    site.quadro["/api/animes/catalog"] = SahteYanit(503, metin="<html>Bakım</html>")
    with caplog.at_level(logging.WARNING, logger=animpow.__name__):
        sonuc = animpow.search_animpow("naruto", limit=30)
    assert len(sonuc) == 14
    assert "QuadroGG" in caplog.text and "503" in caplog.text


def test_arama_iki_arka_uc_da_cokerse_hata(site):
    site.eski["/anime/arama"] = SahteYanit(502, {"ok": False, "error": "Bad gateway"})
    site.quadro["/api/animes/catalog"] = SahteYanit(503, metin="<html>Bakım</html>")
    with pytest.raises(AnimPowHatasi, match="AnimPow araması alınamadı") as bilgi:
        animpow.search_animpow("naruto")
    assert "502" in str(bilgi.value) and "503" in str(bilgi.value)


# ─────────────────────────────────────────────────────────────────────────────
# 4) Bölüm listesi
# ─────────────────────────────────────────────────────────────────────────────
def test_bolumler_iki_arka_ucun_birlesimi_izleme_sirasiyla(site):
    jjk_yukle(site)
    bolumler = animpow.get_anime_episodes(JJK)
    assert len(bolumler) == 59                             # S1 24 + S2 23 + S3 12
    anahtarlar = [tuple(int(x) for x in b.rsplit(":", 2)[1:]) for b, _ in bolumler]
    assert anahtarlar == sorted(anahtarlar)
    assert bolumler[0] == (f"{JJK}:1:1", "1. Sezon 1. Bölüm - Ryomen Sukuna")  # eski API adı
    assert bolumler[1] == (f"{JJK}:1:2", "1. Sezon 2. Bölüm - Kendim İçin")
    assert bolumler[2] == (f"{JJK}:1:3", "1. Sezon 3. Bölüm - Çelik Kız")      # QuadroGG adı
    assert bolumler[6] == (f"{JJK}:1:7", "1. Sezon 7. Bölüm")                  # ad yok
    assert bolumler[24][0] == f"{JJK}:2:1"
    assert bolumler[-1][0] == f"{JJK}:3:12"


def test_bolumler_quadrogg_bossa_yalniz_eski_api(site):
    site.eski[f"/anime/{FRIEREN}/bolumler"] = fikstur("client-api-bolumler-frieren.json")
    site.quadro[f"/api/animes/get-from-core-id-{FRIEREN}/data-list"] = \
        fikstur("quadrogg-datalist-frieren.json")
    assert animpow.get_anime_episodes(FRIEREN) == [
        (f"{FRIEREN}:1:1", "1. Sezon 1. Bölüm - Yolculuğun Sonu"),
        (f"{FRIEREN}:1:2", "1. Sezon 2. Bölüm - Büyü olmasına gerek yoktu..."),
        (f"{FRIEREN}:2:1", "2. Sezon 1. Bölüm - O Zaman, Gidelim mi?"),
    ]


def test_bolumler_cop_adlar_atiliyor_tek_sezon_bicimi(site):
    """Naruto'nun eski kayıtlarında ad "1. Bölüm"; QuadroGG'nin gerçek adı kullanılır."""
    site.eski[f"/anime/{NARUTO}/bolumler"] = fikstur("client-api-bolumler-naruto.json")
    site.quadro[f"/api/animes/get-from-core-id-{NARUTO}/data-list"] = \
        fikstur("quadrogg-datalist-naruto.json")
    bolumler = animpow.get_anime_episodes(NARUTO)
    assert len(bolumler) == 12
    assert bolumler[0] == (f"{NARUTO}:1:1", "1. Bölüm - Giriş! Naruto Uzumaki!")
    assert all(" Sezon " not in b for _, b in bolumler), "tek sezonda sezon yazılmaz"


def test_bolumler_one_piece_arka_uclar_arasi_sira(site):
    """Eski API ilk bölümleri, QuadroGG en yenileri (1173+) veriyor."""
    site.eski[f"/anime/{ONE_PIECE}/bolumler"] = fikstur("client-api-bolumler-onepiece.json")
    site.quadro[f"/api/animes/get-from-core-id-{ONE_PIECE}/data-list"] = \
        fikstur("quadrogg-datalist-onepiece.json")
    bolumler = animpow.get_anime_episodes(ONE_PIECE)
    assert [int(b.rsplit(":", 1)[1]) for b, _ in bolumler] == [1, 2, *range(1173, 1180)]
    assert bolumler[0][1] == "1. Bölüm - Ben Luffy! Korsanlar Kralı Olacak Adam!"
    assert bolumler[2] == (f"{ONE_PIECE}:1:1173", "1173. Bölüm")


def test_film_tek_bolum(site):
    site.eski[f"/anime/{FILM_RED}/bolumler"] = fikstur("client-api-bolumler-film-red.json")
    assert animpow.get_anime_episodes(FILM_RED) == [(f"{FILM_RED}:1:1", "1. Bölüm")]


def test_sayisal_eski_kimlik_quadroggye_sorulmuyor(site):
    site.eski["/anime/960/bolumler"] = fikstur("client-api-bolumler-film-red.json")
    assert animpow.get_anime_episodes("960") == [("960:1:1", "1. Bölüm")]
    assert site.sayi("quadrogg") == 0


def test_bilinmeyen_anime_bos_liste(site):
    assert animpow.get_anime_episodes("9072-FFFFFFFFFFFF") == []


def test_bolum_listesi_onbellekte(site):
    jjk_yukle(site)
    animpow.get_anime_episodes(JJK)
    animpow.get_episode_streams(f"{JJK}:1:1")
    animpow.get_episode_streams(f"{JJK}:1:2")
    assert site.sayi("/bolumler") == 1, "büyük liste her akış sorgusunda yeniden çekilmemeli"


@pytest.mark.parametrize("ad,beklenen", [
    ("Ryomen Sukuna", "Ryomen Sukuna"),
    ("  Lanet Rahmi   Ölmeli 2. Kısım ", "Lanet Rahmi Ölmeli 2. Kısım"),
    ("1. Bölüm", ""),
    ("12 bolum", ""),
    ("S1-E1", ""),
    ("Vudeo", ""),
    ("Doodstream 2", ""),
    ("3. Bölüm - 12. Bölüm", ""),     # başlık ayrıştırıcısı yanlış numara okurdu
    ("", ""),
    (None, ""),
])
def test_bolum_adi_ayiklama(ad, beklenen):
    assert animpow._anlamli_ad(ad) == beklenen


def test_anime_adiyla_ayni_bolum_adi_atiliyor():
    assert animpow._anlamli_ad("JUJUTSU KAISEN", "Jujutsu Kaisen") == ""


# ─────────────────────────────────────────────────────────────────────────────
# 5) Akışlar
# ─────────────────────────────────────────────────────────────────────────────
def test_akislar_sira_tur_ve_referer(site):
    jjk_yukle(site)
    akislar = animpow.get_episode_streams(f"{JJK}:1:1")
    turler = [a["type"] for a in akislar]
    assert turler == ["hls"] * 3 + ["direct"] * 25 + ["iframe"] * 6
    assert len({a["url"] for a in akislar}) == len(akislar)

    hls = akislar[:3]
    assert [a["label"] for a in hls] == ["1080p HLS - Adonis Fansub",
                                         "720p HLS - Adonis Fansub",
                                         "480p HLS - Adonis Fansub"]
    assert all("referer" not in a for a in hls), "QuadroGG HLS referer istemiyor"
    assert all(a["url"].endswith(".m3u8") and a["player"] == "ANIMPOW" for a in hls)

    mp4 = akislar[3:28]
    assert mp4[0]["label"] == "1080p MP4 - FGL Çeviri"
    assert mp4[0]["fansub"] == "FGL Çeviri"
    # Cdn1 referer'sız 403 dönüyor: her doğrudan akış sitenin adresini taşımalı.
    assert all(a["referer"] == "https://animpow.com/" for a in mp4)
    assert all(a["url"].startswith("https://benstreamsunucusuyum.xyz/stream/?token=")
               for a in mp4)

    gomulu = akislar[28:]
    assert all(a["player"] == "SIBNET" and "referer" not in a for a in gomulu)
    # Sitenin sibnet vekili açılmış: asıl adres yt-dlp'ye gidiyor.
    assert gomulu[0]["url"] == "https://video.sibnet.ru/shell.php?videoid=5583670"
    assert not any("doodstream" in a["url"] or "animpow.com" in a["url"] for a in akislar)


def test_oldu_bilinen_gomululer_atiliyor(site):
    """doodstream ve uqload (yt-dlp: Cloudflare 403) listeye girmez."""
    site.eski[f"/anime/{NARUTO}/bolumler"] = fikstur("client-api-bolumler-naruto.json")
    akislar = animpow.get_episode_streams(f"{NARUTO}:1:2")
    assert [a["label"] for a in akislar] == [
        "1080p MP4 - YuushaSubs", "720p MP4 - YuushaSubs", "480p MP4 - YuushaSubs",
        "720p MP4 - AniSekai", "480p MP4 - AniSekai"]


def test_cdn_m3u8_ve_pro_cdn(site):
    """Canlı örneklerde görülmedi ama sitenin şeması taşıyor: referer'lı verilir."""
    film = fikstur("client-api-bolumler-film-red.json")
    kayit_ = film["episodes"][0]
    kayit_.update({
        "cdn_m3u8": "https://benstreamsunucusuyum.xyz/hls/master.m3u8",
        "pro_cdn_active": True,
        "pro_cdn_data": [
            {"quality": "1080", "format": "m3u8", "link": "https://pro.example/1080.m3u8"},
            {"quality": "720p", "format": "mp4", "link": "https://pro.example/720.mp4"},
            {"quality": "480", "format": "mp4", "link": "javascript:alert(1)"},
        ],
    })
    site.eski[f"/anime/{FILM_RED}/bolumler"] = film
    akislar = animpow.get_episode_streams(f"{FILM_RED}:1:1")
    ozet = [(a["label"], a["type"], a.get("referer")) for a in akislar]
    assert ozet == [
        ("1080p MP4 - eleber", "direct", "https://animpow.com/"),
        ("720p MP4 - eleber", "direct", "https://animpow.com/"),
        ("480p MP4 - eleber", "direct", "https://animpow.com/"),
        ("HLS - eleber", "hls", "https://animpow.com/"),
        ("1080p Pro - eleber", "hls", "https://animpow.com/"),
        ("720p Pro - eleber", "direct", "https://animpow.com/"),
    ]


@pytest.mark.parametrize("girdi,beklenen", [
    ("https://sibnet-api-server-v1.animpow.com/api/sibnet?url="
     "https%3A%2F%2Fvideo.sibnet.ru%2Fshell.php%3Fvideoid%3D5264095",
     "https://video.sibnet.ru/shell.php?videoid=5264095"),
    ("//ok.ru/videoembed/5735638764285", "https://ok.ru/videoembed/5735638764285"),
    ("https://www.dailymotion.com/embed/video/x7y6c3j",
     "https://www.dailymotion.com/embed/video/x7y6c3j"),
    ("https://sibnet-api-server-v1.animpow.com/api/sibnet", ""),   # iç adres yok
    ("javascript:alert(1)", ""),
])
def test_gomulu_adres_aciliyor(girdi, beklenen):
    assert animpow._gomuluyu_ac(girdi) == beklenen


@pytest.mark.parametrize("url,oynatici", [
    ("https://video.sibnet.ru/shell.php?videoid=1", "SIBNET"),
    ("https://m.ok.ru/videoembed/1", "ODNOKLASSNIKI"),
    ("https://odnoklassniki.ru/videoembed/1", "ODNOKLASSNIKI"),
    ("https://my.mail.ru/video/embed/1", "MAIL"),
    ("https://book.ru/videoembed/1", None),          # yalnızca adında "ok.ru" geçiyor
    ("https://doodstream.com/e/x", None),
    ("https://uqload.io/embed-x.html", None),
])
def test_gomulu_konak_eslesmesi(url, oynatici):
    assert animpow._gomulu_oynatici(url) == oynatici


def test_gomululer_bu_sitede_calisanlar_once(site):
    film = fikstur("client-api-bolumler-film-red.json")
    sablon = film["episodes"][0]
    film["episodes"] = [
        dict(sablon, cdn_mp4_480=None, cdn_mp4_720=None, cdn_mp4_1080=None, url=url)
        for url in ("https://ok.ru/videoembed/1", "https://drive.google.com/file/d/x/preview",
                    "https://video.sibnet.ru/shell.php?videoid=2",
                    "https://www.dailymotion.com/embed/video/x3",
                    "https://vidmoly.to/embed-y.html", "https://mega.nz/embed/z")]
    site.eski[f"/anime/{FILM_RED}/bolumler"] = film
    oynaticilar = [a["player"] for a in animpow.get_episode_streams(f"{FILM_RED}:1:1")]
    assert oynaticilar == ["SIBNET", "GDRIVE", "DAILYMOTION", "ODNOKLASSNIKI"]


# ─────────────────────────────────────────────────────────────────────────────
# 6) Hata yolları
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kimlik", ["../account", "", "naruto", "9072-XYZ", None])
def test_gecersiz_anime_kimligi(site, kimlik):
    with pytest.raises(ValueError, match="Geçersiz AnimPow kimliği"):
        animpow.get_anime_episodes(kimlik)
    assert site.istekler == []


@pytest.mark.parametrize("kimlik", ["naruto-1", f"{JJK}:1", f"{JJK}:1:0", f"{JJK}:a:b",
                                    "../x:1:1", None])
def test_gecersiz_bolum_kimligi(site, kimlik):
    with pytest.raises(ValueError, match="Geçersiz AnimPow bölüm kimliği"):
        animpow.get_episode_streams(kimlik)
    assert site.istekler == []


def test_ag_hatasi_bolum_listesinde_yukseliyor(site):
    site.eski[f"/anime/{JJK}/bolumler"] = OSError("bağlantı sıfırlandı")
    site.quadro[f"/api/animes/get-from-core-id-{JJK}/data-list"] = \
        OSError("bağlantı sıfırlandı")
    with pytest.raises(AnimPowHatasi, match="AnimPow bölüm listesi alınamadı") as bilgi:
        animpow.get_anime_episodes(JJK)
    assert isinstance(bilgi.value.__cause__, OSError)


def test_ag_hatasi_akislarda_yukseliyor(site):
    site.eski[f"/anime/{JJK}/bolumler"] = SahteYanit(500, {"ok": False,
                                                           "error": "Internal error"})
    site.quadro[f"/api/animes/get-from-core-id-{JJK}/episodes"] = OSError("zaman aşımı")
    with pytest.raises(AnimPowHatasi, match="AnimPow akışları alınamadı") as bilgi:
        animpow.get_episode_streams(f"{JJK}:1:1")
    assert "HTTP 500" in str(bilgi.value) and "zaman aşımı" in str(bilgi.value)


def test_json_yerine_html_acik_hata(site):
    site.quadro[f"/api/animes/get-from-core-id-{JJK}/data-list"] = \
        SahteYanit(200, metin="<!DOCTYPE html><title>Bakım</title>")
    with pytest.raises(AnimPowHatasi, match="JSON yerine"):
        animpow.get_anime_episodes(JJK)


def test_engel_sayfasi_bos_sonuc_sayilmiyor(site):
    site.eski["/anime/arama"] = SahteYanit(403, metin="<html>Just a moment...</html>")
    site.quadro["/api/animes/catalog"] = SahteYanit(403, metin="<html>Just a moment...</html>")
    with pytest.raises(AnimPowHatasi, match="engellendi"):
        animpow.search_animpow("naruto")


def test_bir_arka_uc_cokse_de_akislar_doner(site, caplog):
    jjk_yukle(site)
    site.quadro[f"/api/animes/get-from-core-id-{JJK}/episodes"] = OSError("zaman aşımı")
    with caplog.at_level(logging.WARNING, logger=animpow.__name__):
        akislar = animpow.get_episode_streams(f"{JJK}:1:1")
    assert len(akislar) == 31 and akislar[0]["type"] == "direct"
    assert "zaman aşımı" in caplog.text


# ─────────────────────────────────────────────────────────────────────────────
# 7) Canlı duman testi (varsayılan olarak atlanır: --network)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.network
def test_canli_arama_bolum_akis():
    sonuc = animpow.search_animpow("jujutsu kaisen", limit=5)
    assert sonuc, "canlı arama sonuç vermedi"
    core = next((k for k, b in sonuc if b.casefold() == "jujutsu kaisen"), sonuc[0][0])
    bolumler = animpow.get_anime_episodes(core)
    assert len(bolumler) >= 24
    assert re.fullmatch(r"9072-[0-9A-F]{12}:1:1", bolumler[0][0])
    akislar = animpow.get_episode_streams(bolumler[0][0])
    assert akislar, "ilk bölümün akışı yok"
    assert all(a["url"].startswith("https://") for a in akislar)
    assert all(a.get("referer") == animpow.REFERER
               for a in akislar if a["type"] == "direct")
