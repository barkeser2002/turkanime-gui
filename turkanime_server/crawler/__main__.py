"""Tarayıcı CLI'ı — ``python -m turkanime_server.crawler``.

Örnekler:
    # Ne yapılacağını göster, ağa hiç çıkma
    python -m turkanime_server.crawler --kuru-calistir

    # Tek kaynakta çok küçük bir canlı deneme (geliştirme için)
    python -m turkanime_server.crawler --kaynak anizle --limit 3

    # Docker/cron: her tur kuyruktan hazır olan ne varsa işler, sonra çıkar
    python -m turkanime_server.crawler --cikti /veri/arsiv --durum /veri/tarayici.sqlite3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

# Depo kökünü sys.path'a ekle — `python -m` çağrısı repo kökünden yapılmazsa
# `turkanime_api` bulunamaz (app.py da aynı numarayı yapıyor).
_KOK = Path(__file__).resolve().parents[2]
if str(_KOK) not in sys.path:
    sys.path.insert(0, str(_KOK))

# pylint: disable=wrong-import-position
from turkanime_server.crawler.ayarlar import (      # noqa: E402
    VARSAYILAN_TOHUMLAR, NezaketAyarlari, TarayiciAyarlari,
)
from turkanime_server.crawler.kaynaklar import KAYNAKLAR  # noqa: E402
from turkanime_server.crawler.nezaket import KosuZatenSuruyor  # noqa: E402
from turkanime_server.crawler.tarayici import Tarayici    # noqa: E402

# Varsayılan veri yolları `turkanime_server/` altında: cwd nerede olursa olsun
# aynı yere yazılsın (Docker WORKDIR'ı /app/turkanime_server).
_VERI_KOKU = Path(__file__).resolve().parents[1]


def ayristir(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m turkanime_server.crawler",
        description="Kaynakları nazikçe gezip AnimeDepo şemasında arşiv üretir.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--kaynak", action="append", metavar="AD",
                   help=f"Yalnızca bu kaynağı tara (tekrarlanabilir). "
                        f"Seçenekler: {', '.join(sorted(KAYNAKLAR))}")
    p.add_argument("--limit", type=int, default=None,
                   help="Bu koşuda işlenecek en fazla görev (istek) sayısı")
    p.add_argument("--kuru-calistir", action="store_true",
                   help="Ağa hiç çıkma, diske hiç yazma; yalnızca planı raporla")
    p.add_argument("--cikti", type=Path, default=_VERI_KOKU / "arsiv",
                   help="Arşiv ağacının kökü")
    p.add_argument("--durum", type=Path, default=_VERI_KOKU / "durum/tarayici.sqlite3",
                   help="Kalıcı durum (kuyruk + koşullu istek defteri) dosyası")
    p.add_argument("--tohum", action="append", metavar="SORGU",
                   help="Keşif sorgusu (tekrarlanabilir). Varsayılan: a-z + 0-9")
    p.add_argument("--gecikme", type=float, default=NezaketAyarlari.gecikme,
                   help="Kaynak başına istekler arası taban gecikme (sn)")
    p.add_argument("--jitter", type=float, default=NezaketAyarlari.jitter,
                   help="Gecikmenin ± dalgalanma oranı (0..1)")
    p.add_argument("--gunluk-tavan", type=int, default=NezaketAyarlari.gunluk_tavan,
                   help="Kaynak başına günlük istek tavanı")
    p.add_argument("--tazelik", type=float, default=TarayiciAyarlari.tazelik,
                   help="Bir kaydın yeniden çekilmesi için en az geçmesi gereken süre (sn)")
    p.add_argument("--esik", type=float, default=TarayiciAyarlari.eslesme_esigi,
                   help="Çapraz kaynak başlık eşleştirme eşiği (0..1)")
    p.add_argument("--arsiv-yazma", action="store_true",
                   help="Tarama yap ama arşiv ağacını üretme")
    p.add_argument("--log", default="INFO",
                   help="Log seviyesi (DEBUG/INFO/WARNING/ERROR)")
    return p.parse_args(argv)


def ayarlari_kur(ns: argparse.Namespace) -> TarayiciAyarlari:
    kaynaklar: List[str] = []
    for k in ns.kaynak or []:
        ad = k.strip().lower()
        if ad not in KAYNAKLAR:
            raise SystemExit(
                f"Bilinmeyen kaynak: {k} (seçenekler: {', '.join(sorted(KAYNAKLAR))})")
        kaynaklar.append(ad)

    return TarayiciAyarlari(
        cikti=ns.cikti,
        durum=ns.durum,
        kaynaklar=tuple(kaynaklar),
        tohumlar=tuple(ns.tohum) if ns.tohum else VARSAYILAN_TOHUMLAR,
        limit=ns.limit,
        kuru_calistir=ns.kuru_calistir,
        tazelik=ns.tazelik,
        eslesme_esigi=ns.esik,
        nezaket=NezaketAyarlari(
            gecikme=ns.gecikme,
            jitter=ns.jitter,
            gunluk_tavan=ns.gunluk_tavan,
        ),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = ayristir(argv)
    logging.basicConfig(
        level=getattr(logging, str(ns.log).upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
    )
    ayarlar = ayarlari_kur(ns)

    tarayici = Tarayici(ayarlar)
    try:
        ozet = tarayici.calistir(arsiv_yaz=not ns.arsiv_yazma)
    except KosuZatenSuruyor as e:
        # Sessizce beklemek yanlış olurdu: cron 30 dakikada bir çağırıyor,
        # kuyruk kalıcı ve bir sonraki tur kaldığı yerden devam ediyor.
        print(f"Koşu atlandı: {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        # Kalıcı kuyruk sayesinde yarıda kesmek güvenli: bir sonraki koşu
        # kaldığı yerden devam eder.
        print("Kesildi — durum kaydedildi, sonraki koşu kaldığı yerden devam eder.",
              file=sys.stderr)
        return 130
    finally:
        tarayici.kapat()

    print(json.dumps(ozet.sozluk(), ensure_ascii=False, indent=2))
    if ozet.yarim_kaldi:
        # Yarım kalan tur sessizce 0 döndürmemeli: cron log'unda tek fark
        # bu koddur ve "kotanın %99'u kullanılmadı" bilgisi buradan görünür.
        print("Koşu yarım kaldı: "
              f"{', '.join(sorted(set(ozet.engellenen))) or 'kaynak yok'} bu turda "
              "kapandı, kuyrukta hazır iş kaldı.", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
