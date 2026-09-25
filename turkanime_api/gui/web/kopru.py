"""JS ↔ Python köprüsü (QWebChannel üzerinden).

Tek bir nesne (`Kopru`) sayfaya ``kopru`` adıyla açılıyor. İki yön:

* **Çağrı**: JS ``kopru.cagir(istek, yontem, argumanlar_json)`` der; Python
  ``yontem`` adlı ucu çalıştırıp ``yanit(istek, basarili, sonuc_json)`` yayar.
  JS tarafı (``statik/js/cekirdek.js``) bunu Promise'e çeviriyor:
  ``await TA.cagir("kesif", {mod: "trending"})``.
* **Olay**: Python ``kopru.yay(ad, veri)`` der; sayfa ``TA.dinle(ad, fn)`` ile
  dinler (indirme ilerlemesi, AniList girişi, ...).

Uçlar alan başına sınıflarda (`uclar_kesif`, ...) ``@uc`` ile işaretlenir ve
`bagla` ile kaydedilir. ``arka=True`` uç arka plan havuzunda koşar (ağ, disk):
GUI thread'i hiç beklemez. Sinyal yayımı thread güvenli (alıcı GUI
thread'inde, bağlantı kuyruklu).

Hata sözleşmesi: uç fırlatırsa JS'e ``{"mesaj": ...}`` gider — metin
`common.hatalar.insanlastir`'dan, yani kullanıcıya gösterilecek Türkçe cümle.
Ham metin konsolda kalır.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, Signal, Slot


def uc(ad: Optional[str] = None, *, arka: bool = False):
    """Metodu köprü ucu olarak işaretle (ad verilmezse metodun adı)."""
    def isaretle(fn):
        fn._web_uc = (ad or fn.__name__, arka)
        return fn
    return isaretle


@dataclass(frozen=True)
class _Uc:
    ad: str
    fn: Callable[..., Any]
    arka: bool


def _jsonla(deger: Any) -> Any:
    """`json.dumps`'ın tanımadığı değerler: küme/demet liste, gerisi metin."""
    if isinstance(deger, (set, frozenset, tuple)):
        return list(deger)
    if hasattr(deger, "__fspath__"):
        return str(deger)
    return str(deger)


def json_metni(veri: Any) -> str:
    return json.dumps(veri, ensure_ascii=False, default=_jsonla)


class Kopru(QObject):
    """Sayfaya açılan tek nesne. Uçları `bagla` ile alan sınıflarından alır.

    EBEVEYNSİZ kurun: arka plan uçları bu nesneden sinyal yayıyor; Qt
    ebeveyni (pencere) onu iş sürerken yıkarsa yayım yarışa girip süreci
    düşürür. Ebeveynsiz nesneyi süren işin kendi referansı yaşatıyor.
    """

    # (istek kimliği, başarılı mı, sonuç JSON'u)
    yanit = Signal(str, bool, str)
    # (olay adı, veri JSON'u)
    olay = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._uclar: Dict[str, _Uc] = {}

    # ── Kayıt ───────────────────────────────────────────────────────────────
    def kaydet(self, ad: str, fn: Callable[..., Any], *, arka: bool = False) -> None:
        if ad in self._uclar:
            raise ValueError(f"köprü ucu iki kez kaydedildi: {ad!r}")
        self._uclar[ad] = _Uc(ad, fn, arka)

    def bagla(self, nesne: Any) -> Any:
        """``@uc`` ile işaretli metotları kaydet; nesneyi geri döndür."""
        for isim in dir(type(nesne)):
            isaret = getattr(getattr(type(nesne), isim, None), "_web_uc", None)
            if isaret:
                ad, arka = isaret
                self.kaydet(ad, getattr(nesne, isim), arka=arka)
        return nesne

    def uc_adlari(self):
        return sorted(self._uclar)

    # ── JS → Python ─────────────────────────────────────────────────────────
    @Slot(str, str, str)
    def cagir(self, istek: str, yontem: str, argumanlar: str) -> None:
        kayit = self._uclar.get(yontem)
        if kayit is None:
            self._yanitla(istek, False, {"mesaj": f"bilinmeyen uç: {yontem}"})
            return
        try:
            args = json.loads(argumanlar or "{}")
        except ValueError:
            args = None
        if not isinstance(args, dict):
            self._yanitla(istek, False, {"mesaj": "argümanlar sözlük olmalı"})
            return
        if kayit.arka:
            from ..qt.workers import run_bg
            run_bg(self._calistir, istek, kayit, args)
        else:
            self._calistir(istek, kayit, args)

    def _calistir(self, istek: str, kayit: _Uc, args: Dict[str, Any]) -> None:
        try:
            sonuc = kayit.fn(**args)
        except Exception as exc:  # uç hatası sayfaya mesaj olarak gider
            if getattr(exc, "sessiz", False):
                # Beklenen durum (ör. sayfa başka animeye geçti): sayfa
                # yanıtı zaten atacak, konsolu kirletmeye gerek yok.
                self._yanitla(istek, False, {"mesaj": str(exc), "eski": True})
                return
            from ...common.hatalar import insanlastir
            kisa, ayrinti = insanlastir(exc)
            print(f"[Web] {kayit.ad}: {ayrinti}")
            if isinstance(exc, (TypeError, AttributeError, KeyError)):
                traceback.print_exc()     # büyük ihtimalle hata bizde
            self._yanitla(istek, False, {"mesaj": kisa})
            return
        self._yanitla(istek, True, sonuc)

    def _yanitla(self, istek: str, basarili: bool, veri: Any) -> None:
        try:
            metin = json_metni(veri)
        except (TypeError, ValueError) as exc:
            basarili, metin = False, json_metni({"mesaj": f"yanıt kodlanamadı: {exc}"})
        try:
            self.yanit.emit(istek, basarili, metin)
        except RuntimeError:
            pass                  # köprü silindi (pencere kapandı)

    # ── Python → JS ─────────────────────────────────────────────────────────
    def yay(self, ad: str, veri: Any = None) -> None:
        """Sayfaya olay gönder (her thread'den güvenli)."""
        try:
            self.olay.emit(ad, json_metni(veri))
        except RuntimeError:
            pass


__all__ = ["Kopru", "uc", "json_metni"]
