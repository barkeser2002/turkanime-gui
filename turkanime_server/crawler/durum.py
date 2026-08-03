"""Kalıcı tarama durumu — SQLite.

**Neden SQLite, JSON değil.** Tarama günlere yayılıyor ve süreç her an
öldürülebilir (docker restart, OOM, deploy). JSON durum dosyasında her görev
sonrası tüm dosyayı yeniden yazmak gerekir: on binlerce bölümde bu her adımda
megabaytlarca I/O demek ve yazımın ortasında ölürsek dosya çöpe döner. SQLite
tek görevi tek işlemde (transaction) kalıcılaştırır, çökme sonrası dosya
tutarlıdır, `(kaynak, tur, anahtar)` üzerinde indeks aramasıdır ve stdlib'de
gelir — yeni bağımlılık yok. Kuyruk, koşullu-istek imzaları, günlük sayaçlar ve
biriken bulgular tek dosyada durduğu için yedeklemesi de tek dosya kopyası.

Şemadaki tablolar:
    kuyruk  — yapılacak işler (kaynak, tür, anahtar) + tekrar zamanı
    kayit   — koşullu istek defteri: içerik imzası / ETag / tazelik aralığı
    sayac   — kaynak × gün istek sayacı (günlük tavan için)
    kume    — çapraz kaynak anime kümeleri (arşiv slug'ı burada doğar)
    bulgu   — kaynakta bulunan anime → küme bağı
    bolum   — küme × kaynak bölüm listesi
    akis    — bölüm × kaynak stream listesi (JSON)
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

SEMA = """
CREATE TABLE IF NOT EXISTS kuyruk (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kaynak   TEXT NOT NULL,
    tur      TEXT NOT NULL,
    anahtar  TEXT NOT NULL,
    ek       TEXT,
    oncelik  INTEGER NOT NULL DEFAULT 0,
    durum    TEXT NOT NULL DEFAULT 'bekliyor',
    deneme   INTEGER NOT NULL DEFAULT 0,
    hazir_ts REAL NOT NULL DEFAULT 0,
    UNIQUE(kaynak, tur, anahtar)
);
CREATE INDEX IF NOT EXISTS ix_kuyruk_sira ON kuyruk(durum, hazir_ts, oncelik, id);

CREATE TABLE IF NOT EXISTS kayit (
    kaynak        TEXT NOT NULL,
    tur           TEXT NOT NULL,
    anahtar       TEXT NOT NULL,
    imza          TEXT,
    etag          TEXT,
    last_modified TEXT,
    tazelik       REAL NOT NULL DEFAULT 0,
    gecerli_ts    REAL NOT NULL DEFAULT 0,
    guncel_ts     REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(kaynak, tur, anahtar)
);

CREATE TABLE IF NOT EXISTS sayac (
    kaynak TEXT NOT NULL,
    gun    TEXT NOT NULL,
    adet   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(kaynak, gun)
);

CREATE TABLE IF NOT EXISTS kume (
    slug      TEXT PRIMARY KEY,
    baslik    TEXT NOT NULL,
    guncel_ts REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bulgu (
    kaynak    TEXT NOT NULL,
    kaynak_id TEXT NOT NULL,
    baslik    TEXT NOT NULL,
    kume      TEXT NOT NULL,
    PRIMARY KEY(kaynak, kaynak_id)
);
CREATE INDEX IF NOT EXISTS ix_bulgu_kume ON bulgu(kume);

CREATE TABLE IF NOT EXISTS bolum (
    kume      TEXT NOT NULL,
    kaynak    TEXT NOT NULL,
    kaynak_id TEXT NOT NULL,
    ep_id     TEXT NOT NULL,
    baslik    TEXT NOT NULL,
    sira      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(kume, kaynak, ep_id)
);
CREATE INDEX IF NOT EXISTS ix_bolum_kume ON bolum(kume);

CREATE TABLE IF NOT EXISTS akis (
    kume   TEXT NOT NULL,
    kaynak TEXT NOT NULL,
    ep_id  TEXT NOT NULL,
    veri   TEXT NOT NULL,
    PRIMARY KEY(kume, kaynak, ep_id)
);
CREATE INDEX IF NOT EXISTS ix_akis_kume ON akis(kume);
"""


@dataclass(frozen=True)
class Gorev:
    """Kuyruktan çekilmiş tek iş."""

    id: int
    kaynak: str
    tur: str
    anahtar: str
    ek: Dict[str, Any]
    deneme: int


@dataclass(frozen=True)
class Kayit:
    """Koşullu istek defteri satırı."""

    imza: Optional[str]
    etag: Optional[str]
    last_modified: Optional[str]
    tazelik: float
    gecerli_ts: float
    guncel_ts: float


class DurumDeposu:
    """Tarayıcının tüm kalıcı durumu.

    Thread-güvenli: tek bağlantı + `RLock`. Tarayıcı kaynak başına eşzamanlılığı
    1'de tuttuğu için çekişme düşük; bağlantı havuzuna gerek yok.
    """

    def __init__(self, yol: Path | str, simdi: Callable[[], float] = time.time):
        self.yol = Path(yol)
        self._simdi = simdi
        self._kilit = RLock()
        if str(self.yol) != ":memory:":
            self.yol.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.yol), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SEMA)
        # Çökme kurtarma: önceki koşuda "işlemde" kalan görevler sahipsizdir,
        # kuyruğa geri konur. Bu satır olmadan süreç her öldüğünde birkaç görev
        # sonsuza dek kilitli kalırdı.
        self._db.execute("UPDATE kuyruk SET durum='bekliyor' WHERE durum='islemde'")
        self._db.commit()

    # ── Yaşam döngüsü ───────────────────────────────────────────────────────
    def kapat(self) -> None:
        with self._kilit:
            try:
                self._db.commit()
            finally:
                self._db.close()

    def __enter__(self) -> "DurumDeposu":
        return self

    def __exit__(self, *_) -> None:
        self.kapat()

    # ── Kuyruk ──────────────────────────────────────────────────────────────
    def kuyruga_ekle(
        self,
        kaynak: str,
        tur: str,
        anahtar: str,
        ek: Optional[Dict[str, Any]] = None,
        oncelik: int = 0,
        hazir_ts: float = 0.0,
    ) -> bool:
        """Görevi kuyruğa koy. Zaten varsa dokunma; ``True`` = yeni eklendi.

        Var olanı güncellememek bilinçli: aynı anime birden çok arama sonucunda
        bulunur ve her seferinde ``deneme`` sıfırlansaydı sürekli hata veren bir
        kayıt geri çekilme cezasından sıyrılırdı.
        """
        with self._kilit:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO kuyruk (kaynak, tur, anahtar, ek, oncelik, hazir_ts) "
                "VALUES (?,?,?,?,?,?)",
                (kaynak, tur, anahtar, json.dumps(ek or {}, ensure_ascii=False),
                 oncelik, hazir_ts),
            )
            self._db.commit()
            return cur.rowcount > 0

    def sirada_bekleyen(
        self,
        kaynaklar: Optional[Sequence[str]] = None,
        simdi: Optional[float] = None,
    ) -> Optional[Gorev]:
        """Hazır bir görevi al ve 'işlemde' işaretle (yoksa ``None``)."""
        ts = self._simdi() if simdi is None else simdi
        with self._kilit:
            sorgu = ("SELECT * FROM kuyruk WHERE durum='bekliyor' AND hazir_ts<=?")
            par: List[Any] = [ts]
            if kaynaklar:
                sorgu += " AND kaynak IN (%s)" % ",".join("?" * len(kaynaklar))
                par.extend(kaynaklar)
            sorgu += " ORDER BY oncelik ASC, id ASC LIMIT 1"
            satir = self._db.execute(sorgu, par).fetchone()
            if satir is None:
                return None
            self._db.execute("UPDATE kuyruk SET durum='islemde' WHERE id=?", (satir["id"],))
            self._db.commit()
            return Gorev(
                id=satir["id"], kaynak=satir["kaynak"], tur=satir["tur"],
                anahtar=satir["anahtar"], ek=json.loads(satir["ek"] or "{}"),
                deneme=satir["deneme"],
            )

    def gorev_bitti(self, gorev: Gorev) -> None:
        with self._kilit:
            self._db.execute("UPDATE kuyruk SET durum='bitti' WHERE id=?", (gorev.id,))
            self._db.commit()

    def gorev_ertele(self, gorev: Gorev, hazir_ts: float,
                     deneme_artir: bool = True) -> None:
        """Görevi kuyruğa geri koy — ``hazir_ts``den önce tekrar verilmez.

        ``deneme_artir=False`` başarılı ama "tekrar ziyaret edilecek" görevler
        için: deneme sayacı hata ölçüsüdür, normal tazeleme onu kirletmemeli.
        """
        with self._kilit:
            self._db.execute(
                "UPDATE kuyruk SET durum='bekliyor', deneme=deneme+?, hazir_ts=? WHERE id=?",
                (1 if deneme_artir else 0, hazir_ts, gorev.id),
            )
            self._db.commit()

    def gorev_birak(self, gorev: Gorev) -> None:
        """Görevi denemeyi artırmadan kuyruğa iade et (ör. günlük tavan doldu)."""
        with self._kilit:
            self._db.execute("UPDATE kuyruk SET durum='bekliyor' WHERE id=?", (gorev.id,))
            self._db.commit()

    def kuyruk_sayimi(self) -> Dict[str, int]:
        with self._kilit:
            return {
                r["durum"]: r["adet"]
                for r in self._db.execute(
                    "SELECT durum, COUNT(*) AS adet FROM kuyruk GROUP BY durum")
            }

    # ── Koşullu istek defteri ───────────────────────────────────────────────
    def kayit_oku(self, kaynak: str, tur: str, anahtar: str) -> Optional[Kayit]:
        with self._kilit:
            r = self._db.execute(
                "SELECT * FROM kayit WHERE kaynak=? AND tur=? AND anahtar=?",
                (kaynak, tur, anahtar),
            ).fetchone()
        if r is None:
            return None
        return Kayit(
            imza=r["imza"], etag=r["etag"], last_modified=r["last_modified"],
            tazelik=r["tazelik"], gecerli_ts=r["gecerli_ts"], guncel_ts=r["guncel_ts"],
        )

    def kayit_yaz(
        self, kaynak: str, tur: str, anahtar: str, *,
        imza: Optional[str], tazelik: float, gecerli_ts: float,
        guncel_ts: float, etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> None:
        with self._kilit:
            self._db.execute(
                "INSERT INTO kayit (kaynak,tur,anahtar,imza,etag,last_modified,"
                "tazelik,gecerli_ts,guncel_ts) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(kaynak,tur,anahtar) DO UPDATE SET "
                "imza=excluded.imza, etag=excluded.etag, "
                "last_modified=excluded.last_modified, tazelik=excluded.tazelik, "
                "gecerli_ts=excluded.gecerli_ts, guncel_ts=excluded.guncel_ts",
                (kaynak, tur, anahtar, imza, etag, last_modified,
                 tazelik, gecerli_ts, guncel_ts),
            )
            self._db.commit()

    # ── Günlük istek sayacı ─────────────────────────────────────────────────
    def sayac_oku(self, kaynak: str, gun: str) -> int:
        with self._kilit:
            r = self._db.execute(
                "SELECT adet FROM sayac WHERE kaynak=? AND gun=?", (kaynak, gun)
            ).fetchone()
        return int(r["adet"]) if r else 0

    def sayac_artir(self, kaynak: str, gun: str, adet: int = 1) -> int:
        with self._kilit:
            self._db.execute(
                "INSERT INTO sayac (kaynak,gun,adet) VALUES (?,?,?) "
                "ON CONFLICT(kaynak,gun) DO UPDATE SET adet=adet+excluded.adet",
                (kaynak, gun, adet),
            )
            self._db.commit()
            return self.sayac_oku(kaynak, gun)

    # ── Kümeler / bulgular ──────────────────────────────────────────────────
    def kume_yaz(self, slug: str, baslik: str) -> None:
        with self._kilit:
            self._db.execute(
                "INSERT INTO kume (slug,baslik,guncel_ts) VALUES (?,?,?) "
                "ON CONFLICT(slug) DO UPDATE SET baslik=excluded.baslik, "
                "guncel_ts=excluded.guncel_ts",
                (slug, baslik, self._simdi()),
            )
            self._db.commit()

    def kumeler(self) -> List[Tuple[str, str]]:
        with self._kilit:
            return [(r["slug"], r["baslik"])
                    for r in self._db.execute("SELECT slug, baslik FROM kume ORDER BY slug")]

    def bulgu_yaz(self, kaynak: str, kaynak_id: str, baslik: str, kume: str) -> None:
        with self._kilit:
            self._db.execute(
                "INSERT INTO bulgu (kaynak,kaynak_id,baslik,kume) VALUES (?,?,?,?) "
                "ON CONFLICT(kaynak,kaynak_id) DO UPDATE SET "
                "baslik=excluded.baslik, kume=excluded.kume",
                (kaynak, kaynak_id, baslik, kume),
            )
            self._db.commit()

    def bulgular(self, kume: Optional[str] = None) -> List[Dict[str, str]]:
        with self._kilit:
            if kume is None:
                sat = self._db.execute("SELECT * FROM bulgu ORDER BY kume, kaynak")
            else:
                sat = self._db.execute(
                    "SELECT * FROM bulgu WHERE kume=? ORDER BY kaynak", (kume,))
            return [dict(r) for r in sat]

    def bolum_yaz(self, kume: str, kaynak: str, kaynak_id: str,
                  ep_id: str, baslik: str, sira: int) -> None:
        with self._kilit:
            self._db.execute(
                "INSERT INTO bolum (kume,kaynak,kaynak_id,ep_id,baslik,sira) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(kume,kaynak,ep_id) DO UPDATE SET "
                "baslik=excluded.baslik, sira=excluded.sira, kaynak_id=excluded.kaynak_id",
                (kume, kaynak, kaynak_id, ep_id, baslik, sira),
            )
            self._db.commit()

    def bolumler(self, kume: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._kilit:
            if kume is None:
                sat = self._db.execute("SELECT * FROM bolum ORDER BY kume, kaynak, sira")
            else:
                sat = self._db.execute(
                    "SELECT * FROM bolum WHERE kume=? ORDER BY kaynak, sira", (kume,))
            return [dict(r) for r in sat]

    def akis_yaz(self, kume: str, kaynak: str, ep_id: str,
                 veri: Iterable[Dict[str, Any]]) -> None:
        with self._kilit:
            self._db.execute(
                "INSERT INTO akis (kume,kaynak,ep_id,veri) VALUES (?,?,?,?) "
                "ON CONFLICT(kume,kaynak,ep_id) DO UPDATE SET veri=excluded.veri",
                (kume, kaynak, ep_id, json.dumps(list(veri), ensure_ascii=False)),
            )
            self._db.commit()

    def akislar(self, kume: str) -> List[Dict[str, Any]]:
        with self._kilit:
            sat = self._db.execute(
                "SELECT kaynak, ep_id, veri FROM akis WHERE kume=?", (kume,))
            out: List[Dict[str, Any]] = []
            for r in sat:
                try:
                    veri = json.loads(r["veri"])
                except json.JSONDecodeError:
                    veri = []
                out.append({"kaynak": r["kaynak"], "ep_id": r["ep_id"], "veri": veri})
            return out


__all__ = ["DurumDeposu", "Gorev", "Kayit", "SEMA"]
