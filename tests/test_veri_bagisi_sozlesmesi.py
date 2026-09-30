"""Gönderilen veri bağışı gövdesi, sunucunun yayımladığı sözleşmeye uyuyor mu?

Kimlik bağışında iki taraf ayrı ayrı "doğru" ama birbirine göre yanlıştı (`tur`
alanı hiç gönderilmiyordu, kaynak adı uyuşmuyordu; bkz.
`test_kimlik_bagisi_sozlesmesi.py`). Çare tek belge: sunucu gövde şemasını
`sozlesme/*.json` olarak yayımlıyor, istemci gönderdiği gövdeyi ona doğruluyor.

`sozlesme/katki_veri.json` ŞİMDİLİK YER TUTUCU: istemci deposunda, uzlaşılan
sözleşmeden Pydantic v2'nin `model_json_schema()` biçiminde yazıldı; birleştirmede
sunucunun ürettiği dosyayla değişecek. Bu yüzden testler belgenin AYRINTISINA
değil, sözleşmede yazılı sınırlara dayanıyor; doğrulayıcı da tanımadığı bir
kısıtlama anahtarı görürse susmuyor, hata veriyor (yeni dosya doğrulayıcının
bilmediği bir kural getirirse test onu sessizce geçirmesin).

Doğrulayıcı elle yazıldı: istemci ortamında `jsonschema`/`pydantic` yok ve
yalnızca test için bağımlılık eklemek paketlemeyi etkilerdi.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

import pytest

from turkanime_api.gui.web import veri_bagisi as vb
from turkanime_api.sources import kayit as kaynak_kaydi

KOK = Path(__file__).resolve().parents[1]
SOZLESME_YOLU = KOK / "sozlesme" / "katki_veri.json"

# Değeri kısıtlamayan (yalnızca açıklayan) anahtarlar.
_ACIKLAYICI = frozenset({"title", "description", "default", "examples", "deprecated",
                         "readOnly", "writeOnly", "$defs", "definitions", "$schema",
                         "$id", "$comment"})
_KISITLAYICI = frozenset({"$ref", "anyOf", "oneOf", "allOf", "type", "enum", "const",
                          "minLength", "maxLength", "pattern", "format", "minimum",
                          "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems",
                          "maxItems", "items", "prefixItems", "uniqueItems", "properties",
                          "required", "additionalProperties"})
_TURLER = {
    "string": lambda d: isinstance(d, str),
    "integer": lambda d: isinstance(d, int) and not isinstance(d, bool),
    "number": lambda d: isinstance(d, (int, float)) and not isinstance(d, bool)
    and math.isfinite(d),
    "boolean": lambda d: isinstance(d, bool),
    "null": lambda d: d is None,
    "array": lambda d: isinstance(d, list),
    "object": lambda d: isinstance(d, dict),
}


def dogrula(deger: Any, sema: Dict[str, Any], kok: Dict[str, Any], yol: str = "$") -> List[str]:
    """JSON Schema'nın (2020-12) Pydantic'in ürettiği alt kümesi; hata listesi."""
    bilinmeyen = set(sema) - _ACIKLAYICI - _KISITLAYICI
    if bilinmeyen:
        return [f"{yol}: doğrulayıcının tanımadığı anahtar {sorted(bilinmeyen)} — "
                "yeni sözleşme bir kural getirdi, doğrulayıcıyı genişletin"]
    hatalar: List[str] = []
    if "$ref" in sema:
        ref = sema["$ref"]
        assert ref.startswith("#/"), ref
        hedef: Any = kok
        for parca in ref[2:].split("/"):
            hedef = hedef[parca]
        hatalar += dogrula(deger, hedef, kok, yol)
    if "anyOf" in sema and all(dogrula(deger, alt, kok, yol) for alt in sema["anyOf"]):
        hatalar.append(f"{yol}: anyOf seçeneklerinin hiçbiri tutmadı ({deger!r:.60})")
    if "oneOf" in sema:
        tutan = sum(not dogrula(deger, alt, kok, yol) for alt in sema["oneOf"])
        if tutan != 1:
            hatalar.append(f"{yol}: oneOf {tutan} seçenekle tuttu")
    for alt in sema.get("allOf", []):
        hatalar += dogrula(deger, alt, kok, yol)
    tur = sema.get("type")
    if tur is not None:
        turler = tur if isinstance(tur, list) else [tur]
        if not any(_TURLER[t](deger) for t in turler):
            return hatalar + [f"{yol}: tür {tur} bekleniyordu, {type(deger).__name__} geldi"]
    if "enum" in sema and deger not in sema["enum"]:
        hatalar.append(f"{yol}: {deger!r} izinli değerlerden değil")
    if "const" in sema and deger != sema["const"]:
        hatalar.append(f"{yol}: {deger!r} != {sema['const']!r}")
    if isinstance(deger, str):
        if len(deger) < sema.get("minLength", 0):
            hatalar.append(f"{yol}: {len(deger)} < minLength {sema['minLength']}")
        if "maxLength" in sema and len(deger) > sema["maxLength"]:
            hatalar.append(f"{yol}: {len(deger)} > maxLength {sema['maxLength']}")
        if "pattern" in sema and not re.search(sema["pattern"], deger):
            hatalar.append(f"{yol}: {deger!r:.60} kalıba uymuyor ({sema['pattern']})")
        if sema.get("format") in ("uri", "url"):
            parca = urlsplit(deger)
            if not (parca.scheme and parca.netloc):
                hatalar.append(f"{yol}: {deger!r:.60} geçerli bir adres değil")
    if isinstance(deger, (int, float)) and not isinstance(deger, bool):
        if "minimum" in sema and deger < sema["minimum"]:
            hatalar.append(f"{yol}: {deger} < minimum {sema['minimum']}")
        if "maximum" in sema and deger > sema["maximum"]:
            hatalar.append(f"{yol}: {deger} > maximum {sema['maximum']}")
        if "exclusiveMinimum" in sema and deger <= sema["exclusiveMinimum"]:
            hatalar.append(f"{yol}: {deger} <= exclusiveMinimum")
        if "exclusiveMaximum" in sema and deger >= sema["exclusiveMaximum"]:
            hatalar.append(f"{yol}: {deger} >= exclusiveMaximum")
    if isinstance(deger, list):
        if len(deger) < sema.get("minItems", 0):
            hatalar.append(f"{yol}: {len(deger)} öğe < minItems {sema['minItems']}")
        if "maxItems" in sema and len(deger) > sema["maxItems"]:
            hatalar.append(f"{yol}: {len(deger)} öğe > maxItems {sema['maxItems']}")
        onek = sema.get("prefixItems", [])
        for i, (oge, alt) in enumerate(zip(deger, onek)):
            hatalar += dogrula(oge, alt, kok, f"{yol}[{i}]")
        if isinstance(sema.get("items"), dict):
            for i, oge in enumerate(deger[len(onek):], start=len(onek)):
                hatalar += dogrula(oge, sema["items"], kok, f"{yol}[{i}]")
        elif sema.get("items") is False and len(deger) > len(onek):
            hatalar.append(f"{yol}: prefixItems'tan fazla öğe")
        if sema.get("uniqueItems") and len({json.dumps(o, sort_keys=True) for o in deger}) \
                != len(deger):
            hatalar.append(f"{yol}: öğeler tekil değil")
    if isinstance(deger, dict):
        ozellikler = sema.get("properties", {})
        for ad in sema.get("required", []):
            if ad not in deger:
                hatalar.append(f"{yol}: zorunlu alan yok: {ad}")
        for ad, alt_deger in deger.items():
            if ad in ozellikler:
                hatalar += dogrula(alt_deger, ozellikler[ad], kok, f"{yol}.{ad}")
            elif sema.get("additionalProperties") is False:
                hatalar.append(f"{yol}: sözleşmede olmayan alan: {ad}")
            elif isinstance(sema.get("additionalProperties"), dict):
                hatalar += dogrula(alt_deger, sema["additionalProperties"], kok, f"{yol}.{ad}")
    return hatalar


@pytest.fixture(scope="module")
def sozlesme() -> Dict[str, Any]:
    assert SOZLESME_YOLU.is_file(), (
        "sözleşme belgesi yok; istemcinin doğrulayacak bir şeyi kalmaz")
    return json.loads(SOZLESME_YOLU.read_text(encoding="utf-8"))


def gecerli_mi(govde: Dict[str, Any], sozlesme: Dict[str, Any]) -> List[str]:
    sema = sozlesme["govde_semasi"]
    return dogrula(json.loads(json.dumps(govde)), sema, sema)


# ── Belge ────────────────────────────────────────────────────────────────────
def test_belge_kimlik_bagisiyla_ayni_bicimde(sozlesme):
    assert {"govde_semasi", "surum", "uc"} <= set(sozlesme)
    assert isinstance(sozlesme["surum"], int)
    assert sozlesme["govde_semasi"].get("type") == "object"


def test_uc_istemcinin_gonderdigi_yol(sozlesme):
    yontem, yol = sozlesme["uc"].split(" ", 1)
    assert (yontem, yol) == ("POST", vb.UC_YOLU)


# ── Gövde şemaya uyuyor ─────────────────────────────────────────────────────
SIBNET = "https://video.sibnet.ru/shell.php?videoid=4512290"
OKCDN = "https://vd346.okcdn.ru/?expires=1790371318112&srcIp=203.0.113.130&sig=6gD"


class _Bolum:
    def __init__(self, kimlik, akislar, title="One Piece 1. Bölüm"):
        self.kimlik = kimlik
        self.son_akislar = akislar
        self.title = title
        self.anime = None


class _Video:
    def __init__(self, url, player="SIBNET", label="Sibnet"):
        self.url, self.player, self.label = url, player, label


def _govde(kaynak="AnimeTR", kimlik="one-piece", bolum_kimlik="one-piece/bolum-1",
           akislar=None, oynayan=SIBNET, **ek):
    akislar = akislar if akislar is not None else [
        {"url": SIBNET, "label": "Sibnet", "player": "SIBNET", "fansub": "AnimeSue",
         "referer": "https://gizli.test/", "user_agent": "UA"},
        {"url": OKCDN, "label": "OK.ru", "player": "ODNOKLASSNIKI"},
        {"url": "https://my.mail.ru/video/embed/9173325596158070507", "label": "Mail.ru"},
    ]
    kayit = {"title": "One Piece 1. Bölüm", "obj": _Bolum(bolum_kimlik, akislar),
             "kaynak": kaynak, "kimlik": kimlik, "seri_adi": "One Piece",
             "kapak": "https://img.test/one-piece.jpg",
             "bolum_listesi": [("one-piece/bolum-1", "1. Bölüm")]}
    kayit.update(ek)
    govde = vb.govde_kur(vb.anlik_al(kayit, _Video(oynayan)))
    assert govde is not None
    return govde


def _uc_uca():
    """Sınırların hepsi aynı anda zorlanıyor."""
    uzun = "ğ" * 2000
    akis = [{"url": f"https://video.sibnet.ru/shell.php?videoid={i}" + "x" * 1990,
             "label": uzun, "player": uzun, "fansub": uzun} for i in range(70)]
    return _govde(kimlik="a" * 999, bolum_kimlik="b" * 999, akislar=akis,
                  oynayan=akis[5]["url"], title="2. Sezon 7.5. Bölüm " + uzun,
                  seri_adi=uzun, kapak="https://img.test/" + "k" * 5000,
                  bolum_listesi=[(f"b{i}" + "x" * 400, uzun) for i in range(7000)])


SENARYOLAR = {
    "tam": lambda: _govde(),
    "listesiz_kapaksiz": lambda: _govde(bolum_listesi=None, kapak=""),
    "numarasiz": lambda: _govde(title="Film"),
    "sezonlu_ara_bolum": lambda: _govde(title="Naruto 2. Sezon 5.5. Bölüm"),
    "sureli_oynayan": lambda: _govde(oynayan=OKCDN),
    "arsiv_kaynagi": lambda: _govde(kaynak="TürkAnime", kimlik="07-ghost",
                                    bolum_kimlik="07-ghost/07-ghost-1-bolum"),
    "sinirlar": _uc_uca,
}


@pytest.mark.parametrize("senaryo", sorted(SENARYOLAR))
def test_gonderilen_govde_sozlesmeye_uyuyor(sozlesme, senaryo):
    govde = SENARYOLAR[senaryo]()
    hatalar = gecerli_mi(govde, sozlesme)
    assert not hatalar, "\n".join(hatalar[:10])


def test_govde_yalnizca_sozlesmenin_alanlarini_tasiyor():
    """Sunucu fazla alanı sessizce yok sayabilir; bu yüzden beyaz liste burada da.

    Yeni dosya `additionalProperties: false` taşımasa bile, gövdeye referer,
    user-agent ya da yerel yol gibi bir alanın sızması bu testte yakalanır.
    """
    govde = _uc_uca()
    assert set(govde) == {"kaynak", "anime", "bolum", "videolar", "bolum_listesi",
                          "istemci"}
    assert set(govde["anime"]) == {"kimlik", "baslik", "kapak"}
    assert set(govde["bolum"]) == {"kimlik", "baslik", "sezon", "no", "ara"}
    assert all(set(v) == {"url", "oynatici", "fansub", "etiket", "calisti"}
               for v in govde["videolar"])


@pytest.mark.parametrize("kaynak", [k.ad for k in kaynak_kaydi.kaynaklar(metadata=False)])
def test_her_oynatilabilir_kaynagin_adi_sozlesmede_gecerli(sozlesme, kaynak):
    """Kaynak adı kayıttaki modül adı (küçük harf); sunucu tanımıyorsa 400 verir
    ve kayıt düşer — ama biçim yüzünden 422 hiçbir kaynakta olmamalı."""
    hatalar = gecerli_mi(_govde(kaynak=kaynak), sozlesme)
    assert not hatalar, "\n".join(hatalar[:5])


# ── Doğrulayıcı boş değil: sözleşmede yazılı sınırları gerçekten uyguluyor ──
def _boz(govde, yol, deger=...):
    govde = json.loads(json.dumps(govde))
    hedef = govde
    for parca in yol[:-1]:
        hedef = hedef[parca]
    if deger is ...:
        del hedef[yol[-1]]
    else:
        hedef[yol[-1]] = deger
    return govde


@pytest.mark.parametrize("yol, deger", [
    (("videolar",), []),                                   # 1..40
    (("videolar",), "×41"),
    (("kaynak",), ""),
    (("kaynak",), "k" * 51),
    (("anime", "kimlik"), ""),
    (("anime", "baslik"), "b" * 301),
    (("anime",), ...),                                     # zorunlu
    (("bolum",), ...),
    (("bolum", "kimlik"), "x" * 301),
    (("bolum", "sezon"), 1000),
    (("bolum", "no"), -1),
    (("bolum", "ara"), 100),
    (("videolar", 0, "url"), "http://"),                   # 8..2048
    (("videolar", 0, "url"), "https://x.test/" + "a" * 2048),
    (("videolar", 0, "fansub"), "f" * 101),
    (("bolum_listesi",), "×5001"),
    (("istemci",), "i" * 31),
])
def test_dogrulayici_sozlesme_sinirlarini_uyguluyor(sozlesme, yol, deger):
    govde = _govde()
    if deger == "×41":
        deger = govde["videolar"] * 41
    elif deger == "×5001":
        deger = [["b", "t"]] * 5001
    assert gecerli_mi(_boz(govde, yol, deger), sozlesme), f"{yol} bozuldu ama geçti"


def test_dogrulayici_fazla_alani_yakaliyor(sozlesme):
    """Yalnızca şema fazla alanı yasaklıyorsa anlamlı (yer tutucu yasaklıyor)."""
    if sozlesme["govde_semasi"].get("additionalProperties") is not False:
        pytest.skip("sözleşme fazla alanı yasaklamıyor")
    govde = _govde()
    govde["videolar"][0]["referer"] = "https://gizli.test/"
    assert gecerli_mi(govde, sozlesme)


def test_dogrulayici_tanimadigi_kurali_sessizce_gecmiyor():
    assert dogrula("x", {"type": "string", "if": {"const": "x"}}, {})


# ── Yanıt ────────────────────────────────────────────────────────────────────
class _Yanit:
    status_code = 200
    headers: Dict[str, str] = {}

    def json(self):
        return {"katki_id": "0123456789abcdef0123456789abcdef", "durum": "alindi"}


def test_sozlesmedeki_basari_yaniti_basari_sayiliyor():
    sonuc = vb.yaniti_yorumla(_Yanit(), 0.0)
    assert sonuc.tur == vb.TAMAM and sonuc.katki_id == "0123456789abcdef0123456789abcdef"
