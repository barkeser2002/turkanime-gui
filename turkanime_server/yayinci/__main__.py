"""Yayıncı CLI'ı — ``python -m turkanime_server.yayinci``.

Örnekler:
    # Ne commit'lenecekti? Diske de git'e de dokunmaz
    python -m turkanime_server.yayinci --kuru-calistir

    # Yerelde commit'le, uzağa itme (ilk kurulum denemesi)
    python -m turkanime_server.yayinci --arsiv /veri/arsiv --itme-yok

    # Üretim: cron/systemd her turda çağırır; kimlik ortamdan gelir
    TURKANIME_ARSIV_UZAK=https://gitlab.com/kullanici/arsiv.git \\
    TURKANIME_ARSIV_TOKEN=... \\
    python -m turkanime_server.yayinci --arsiv /veri/arsiv

`--token` diye bir bayrak **yok**: argümanlar `ps` çıktısında ve shell
geçmişinde görünür, ortam değişkenleri görünmez.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

# Depo kökünü sys.path'a ekle — `python -m` repo kökünden çağrılmazsa paket
# bulunamaz (crawler/__main__.py da aynı numarayı yapıyor).
_KOK = Path(__file__).resolve().parents[2]
if str(_KOK) not in sys.path:
    sys.path.insert(0, str(_KOK))

# pylint: disable=wrong-import-position
from turkanime_server.yayinci.ayarlar import (        # noqa: E402
    YayinAyarlari, ortamdan,
)
from turkanime_server.yayinci.depo import GitDepo, GitHatasi, GitYok  # noqa: E402
from turkanime_server.yayinci.yayinci import Yayinci  # noqa: E402

_VERI_KOKU = Path(__file__).resolve().parents[1]


def ayristir(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m turkanime_server.yayinci",
        description="Arşiv ağacını git deposuna işler ve uzağa iter.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Kimlik bilgileri yalnızca ortam değişkenlerinden okunur: "
               "TURKANIME_ARSIV_UZAK / _TOKEN / _KULLANICI / _SSH_ANAHTAR "
               "(bkz. turkanime_server/.env.example).",
    )
    p.add_argument("--arsiv", type=Path, default=_VERI_KOKU / "arsiv",
                   help="Yayınlanacak arşiv ağacının kökü (git çalışma ağacı)")
    p.add_argument("--uzak", default=None,
                   help="Uzak depo adresi (varsayılan: TURKANIME_ARSIV_UZAK). "
                        "Adrese token GÖMME — sızar")
    p.add_argument("--dal", default=None,
                   help="Hedef dal (varsayılan: TURKANIME_ARSIV_DAL ya da master)")
    p.add_argument("--itme-yok", action="store_true",
                   help="Yalnızca yerelde commit'le, uzağa itme")
    p.add_argument("--kuru-calistir", action="store_true",
                   help="Hiçbir şey yazma; yalnızca commit mesajını ve sayıları raporla")
    p.add_argument("--azami-gecmis", type=int, default=None,
                   help="Bu commit sayısı aşılınca geçmiş tek köke indirilir (0 = kapalı)")
    p.add_argument("--bakim-araligi", type=int, default=None,
                   help="Her N commit'te bir tam `git gc` (0 = kapalı)")
    p.add_argument("--log", default="INFO",
                   help="Log seviyesi (DEBUG/INFO/WARNING/ERROR)")
    return p.parse_args(argv)


def ayarlari_kur(ns: argparse.Namespace) -> YayinAyarlari:
    """Ortam → CLI sırasıyla ayarları kur (CLI üstte, sırlar hariç)."""
    ayarlar = ortamdan(temel=YayinAyarlari(arsiv=ns.arsiv))
    from dataclasses import replace

    return replace(
        ayarlar,
        arsiv=ns.arsiv,
        uzak=ns.uzak or ayarlar.uzak,
        dal=ns.dal or ayarlar.dal,
        it=not ns.itme_yok,
        kuru_calistir=ns.kuru_calistir,
        azami_gecmis=(ayarlar.azami_gecmis if ns.azami_gecmis is None
                      else ns.azami_gecmis),
        bakim_araligi=(ayarlar.bakim_araligi if ns.bakim_araligi is None
                       else ns.bakim_araligi),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = ayristir(argv)
    logging.basicConfig(
        level=getattr(logging, str(ns.log).upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
    )
    ayarlar = ayarlari_kur(ns)

    if not ns.kuru_calistir and not GitDepo.git_var_mi():
        print("`git` bulunamadı — yayıncı git ikilisine muhtaç.", file=sys.stderr)
        return 3

    try:
        sonuc = Yayinci(ayarlar).yayinla()
    except (GitHatasi, GitYok) as e:
        # Mesaj `depo.gizle()`den geçmiş hâlde geliyor; token içermez.
        print(f"Yayın başarısız: {e}", file=sys.stderr)
        return 2

    print(json.dumps(sonuc.sozluk(), ensure_ascii=False, indent=2))
    # Değişiklik olmaması hata değil: cron her turda çağırıyor ve turların
    # çoğunda gerçekten değişiklik olmayacak.
    return 0 if sonuc.sebep in ("yayinlandi", "degisiklik-yok", "kuru") else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
