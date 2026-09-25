"""Kaynak kimlikleri (TRAnimeİzle çerezi, OpenAnime jetonları) — Qt'siz.

Çerez ve jetonlar `ayarlar.json`'da duruyor ama kaynak modüllerine süreç
içindeki global'ler üzerinden giriyor (`tranime.SESSION_COOKIE`,
`openani.OPENANI_TOKEN`); her süreç bunları None ile başlatıyor. Uygulama
açılışta bu aktarımı `gui.qt.prefs` üzerinden yapıyordu, CLI ise hiç
yapmıyordu: çerez kayıtlıyken bile `search_tranime` hiç istek atmadan boş
dönüyor, harf dizini yedeği de bot denetimine takılıyordu ve CLI kullanıcıya
"çerezi Qt uygulamasından alın" diyordu. CLI `gui.qt`'yi import edemez
(`gui/qt/__init__.py` PySide6 çeken `app`'i yüklüyor); bu yüzden gövde buraya
taşındı ve iki taraf da onu çağırıyor.
"""
from __future__ import annotations

from typing import Any, Mapping


def _metin(ayarlar: Mapping[str, Any], ad: str) -> str:
    return str(ayarlar.get(ad) or "")


def kaynak_kimliklerini_uygula(ayarlar: Mapping[str, Any]) -> bool:
    """Ayarlardaki çerez ve jetonları kaynak modüllerinin global'lerine bas.

    Boş değer de bilerek gönderiliyor: "Temizle" dendiğinde süreç içindeki eski
    çerezin de düşmesi gerekir, yoksa yalnızca disk temizlenirdi.

    Hata yutulur ve `False` döner: kimlik yükleyememek açılışı engellememeli.
    Kaynak modülleri fonksiyon içinde import ediliyor; biri kırıksa (eksik
    bağımlılık) diğerinin kimliği yine yüklenir.
    """
    ayarlar = ayarlar or {}
    tamam = True
    try:
        from ..sources.tranime import set_session_cookie
        set_session_cookie(_metin(ayarlar, "tranime_cookie"))
    except Exception:
        tamam = False
    try:
        from ..sources.openani import set_openani_tokens
        set_openani_tokens(_metin(ayarlar, "openani_token"),
                           _metin(ayarlar, "openani_refresh_token"))
    except Exception:
        tamam = False
    return tamam


__all__ = ["kaynak_kimliklerini_uygula"]
