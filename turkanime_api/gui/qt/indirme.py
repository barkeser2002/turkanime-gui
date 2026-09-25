"""İndirme kuyruğu: `DownloadManager` ve yardımcıları.

Arayüzü web'de (İndirilenler sayfası, `gui/web/uclar_indirme.py`); burada
yalnızca iş mantığı. Eski GUI'deki `DownloadWorker` davranışının karşılığı:
kullanıcı ayarlarına uyan paralellik, başarısızlıkta otomatik tekrar, elle
"Yeniden Dene" ve gerçekten çalışan iptal.

İndirme işi yt-dlp'ye `progress_hooks` ile bağlanır; hook arka plan thread'inde
çalıştığı için ilerleme UI'ya **sinyalle** taşınır (kuyruklu bağlantı sayesinde
slot GUI thread'inde çalışır).
"""
from __future__ import annotations

import os
import re
import sys
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from ...common.dosya_adi import (
    bolum_hedefi, oynatilabilir_dosya, yarim_dosyalari_sil,
)
from ...common.hatalar import insanlastir
from ...sources import kayit as kaynak_kaydi
from . import prefs
from .workers import run_bg, set_long_task_limit

# Kullanıcıya gösterilen durumlar. Metinler tek yerde: satır etiketi, durum
# çubuğu ve testler aynı sözlüğü konuşsun.
DURUM_BEKLIYOR = "bekliyor"
DURUM_INDIRILIYOR = "indiriliyor"
DURUM_TAMAMLANDI = "tamamlandı"
DURUM_HATA = "hata"
DURUM_IPTAL = "iptal edildi"
# Kullanıcı durdurdu (ya da uygulama kapanırken/yeniden açılınca): `.part`
# diskte kalır, "Devam et" yt-dlp'nin kaldığı yerden sürdürmesini sağlar.
# BİTMİŞ SAYILMAZ: aynı bölüm ikinci kez kuyruğa girmesin (bkz. `kuyruktaki_is`).
DURUM_DURAKLATILDI = "duraklatıldı"

# Bitmiş sayılan durumlar: satır temizlenebilir, yeniden denenebilir.
BITMIS_DURUMLAR = (DURUM_TAMAMLANDI, DURUM_HATA, DURUM_IPTAL)
# Gerçekten iş gören (thread tutan ya da tutacak) durumlar.
CALISAN_DURUMLAR = (DURUM_BEKLIYOR, DURUM_INDIRILIYOR)

# Kuyruk dosyası `ayarlar.json`'un yanında (bkz. `kuyruk_yolu`).
KUYRUK_DOSYASI = "indirme_kuyrugu.json"
KUYRUK_SURUMU = 1

# Satırdaki hata metninin üst sınırı; ham metnin tamamı araç ipucunda.
HATA_METNI_SINIRI = 120

# Eski GUI ile aynı: bir otomatik tekrar hakkı. Kaynak sunucuları sık sık
# geçici 5xx/timeout veriyor; ikinci deneme çoğu zaman tutuyor.
MAX_DENEME = 2

def _sade(metin: str) -> str:
    return re.sub(r"[\W_]+", " ", metin.casefold()).strip()


def satir_basligi(entry: Dict[str, Any]) -> str:
    """İndirme satırının etiketi: ``"Seri — Bölüm"``.

    Kaynakların bir kısmı bölüm başlığına seri adını koymuyor (Tranimaci
    "3. Bölüm", OpenAnime "<sezon> - Bölüm N"); iki serinin aynı numaralı
    bölümleri kuyrukta birbirinden ayırt edilemiyordu. Başlık adı zaten
    içeriyorsa (arşiv: "Naruto 3. Bölüm") ikinci kez eklenmez.
    """
    bolum_adi = str((entry or {}).get("title") or "Bölüm")
    seri = (str((entry or {}).get("seri_adi") or "")
            or prefs.seri_adi((entry or {}).get("obj")))
    if not seri or _sade(seri) in _sade(bolum_adi):
        return bolum_adi
    return f"{seri} — {bolum_adi}"


def klasoru_ac(yol: str) -> bool:
    """Klasörü sistemin dosya yöneticisinde aç (Windows/macOS/Linux ortak).

    `QDesktopServices` platform farkını soğuruyor; `os.startfile`/`open`/
    `xdg-open` dallanması burada ikinci kez yazılmasın.
    """
    if not yol:
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(yol))))


def kuyruk_yolu() -> str:
    """`indirme_kuyrugu.json`: veri kökü, `ayarlar.json`'un yanı.

    Import fonksiyon içinde: testler `Dosyalar`'ı geçici köke bağlıyor
    (`izole_ev`); modül düzeyinde bağlansaydı o bağ görünmezdi.
    """
    from ...cli.dosyalar import Dosyalar
    return os.path.join(Dosyalar().ta_path, KUYRUK_DOSYASI)


def _bolum_kimligi_coz(kaynak: Any, adres: Optional[str]) -> Optional[str]:
    """Bölüm adresinden kaynağın bölüm kimliğini geri çıkar (yoksa None).

    `kayittan_bolumler` nesneye kimliği değil `kaynak.bolum_adresi(kimlik)`'i
    koyuyor; kimlik yalnızca akış sağlayıcısının kapanışında. Kaynak katmanı
    değiştirilmeden (kaynak işi durduruldu) kimlik, adres şablonu tersine
    çevrilerek bulunuyor: şablon bir işaretle çağrılır, önek/sonek ayrılır ve
    sonuç yeniden adres üretilerek DOĞRULANIR — tutmazsa iş kalıcı yazılmaz.
    """
    if not adres:
        return None
    isaret = "\x00"
    try:
        sablon = kaynak.bolum_adresi(isaret)
    except Exception:
        return None
    if sablon.count(isaret) != 1:
        return None
    on, son = sablon.split(isaret)
    if not (adres.startswith(on) and adres.endswith(son)) or len(adres) <= len(on) + len(son):
        return None
    aday = adres[len(on):len(adres) - len(son)]
    try:
        return aday if kaynak.bolum_adresi(aday) == adres else None
    except Exception:
        return None


def _bolumu_kur(kayit: Dict[str, Any]) -> Any:
    """Kuyruk kaydından `AdapterBolum` kur — AĞA ÇIKMADAN, kaynağı yüklemeden.

    `kayittan_bolumler`'in tek bölümlük karşılığı. Akış uçları iş BAŞLAYINCA
    (arka planda) yüklenir: açılışta 40 işlik kuyruğu geri yüklemek her
    kaynağın modülünü GUI thread'inde import etmemeli.
    """
    from ...sources.adapter import AdapterAnime, AdapterBolum
    kaynak = kaynak_kaydi.bul(kayit.get("kaynak"))
    bolum_id = str(kayit.get("bolum_id") or "")
    if kaynak is None or not kaynak.oynatilabilir or not bolum_id:
        return None

    def akislar(kimlik: str):
        uc = kaynak.uclar().akislar
        return uc(kimlik) if uc is not None else []

    return AdapterBolum(
        url=kaynak.bolum_adresi(bolum_id),
        title=str(kayit.get("bolum_baslik") or ""),
        anime=AdapterAnime(slug=str(kayit.get("anime_slug") or ""),
                           title=str(kayit.get("anime_baslik") or "")),
        stream_provider=kaynak_kaydi.akis_saglayici(
            akislar, bolum_id, etiket=kaynak.etiket,
            bos_mesaji=kaynak.bos_akis_mesaji),
        player_name=kaynak.oynatici,
        slug=str(kayit.get("bolum_slug") or "") or None,
    )


def _fansub_arg(fansub: Optional[str]) -> Dict[str, str]:
    """`best_video`'ya yalnızca SEÇİLDİYSE `by_fansub`: eski/sahte bölüm
    nesnelerinin imzası bu argümanı tanımıyor."""
    return {"by_fansub": fansub} if fansub else {}


class IndirmeIptal(Exception):
    """Hook'tan fırlatılan iptal sinyali.

    yt-dlp'yi durdurmanın tek yolu bu: eski GUI hook'ta `return` ediyordu, o da
    yalnızca ilerleme raporunu susturuyor, indirme arka planda sonuna kadar
    devam ediyordu.
    """


def _fmt_size(num: Optional[float]) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


class _Is:
    """Tek bir indirme işinin paylaşılan durumu (GUI + arka plan)."""

    def __init__(self, task_id: str, entry: Dict[str, Any], title: str, output: str,
                 fansub: Optional[str] = None):
        self.task_id = task_id
        self.entry = entry
        self.title = title
        self.output = output
        # Kullanıcının seçtiği fansub ("Fansub'u kendim seçeyim"); None =
        # otomatik. Kayda (`entry`) YAZILMIYOR: kayıt bölüm listesindeki
        # satırla paylaşılıyor, oynatma da aynı kaydı kullanıyor.
        self.fansub = fansub
        self.iptal = threading.Event()
        self.durum = DURUM_BEKLIYOR
        # Çözülmüş disk hedefi: aynı bölümün ikinci kez kuyruğa girmesini
        # engelleyen anahtar (bkz. `DownloadManager.kuyruktaki_is`).
        self.hedef: Optional[str] = None
        # Bitişte diskte doğrulanan dosya ("Oynat" onu açar) ve serinin klasörü.
        self.dosya: Optional[str] = None
        self.klasor: str = ""
        # Son hatanın ham metni (satırın araç ipucu); satırda kısa Türkçe sebep.
        self.ayrinti: str = ""
        # Duraklatma: iptal bayrağı yt-dlp'yi durduruyor, bu bayrak işin
        # "iptal edildi" değil "duraklatıldı" diye bitmesini sağlıyor.
        self.duraklat = False
        # `_basla` her çağrıldığında artar; `_run` kendi neslini taşır. Bekleyen
        # iş duraklatılıp sürdürülünce havuzda ESKİ `_run` de sırada kalıyor:
        # nesil tutmazsa o çağrı hiçbir şey yapmadan döner (yoksa aynı iş iki
        # thread'de aynı dosyaya yazardı).
        self.nesil = 0
        # İndirmeye verilen son aday: sürdürmede `best_video` BAŞKA adres
        # döndürürse eski akışın `.part`'ı silinmeli (bkz. `_aday_sabitle`).
        self.secilen_url: Optional[str] = None
        self.secilen_etiket: Optional[str] = None
        # Bitişi iki thread de yazabiliyor (iptal GUI'den, sonuç işçiden);
        # kilit olmadan aynı iş iki kez "bitti" diye raporlanabilir.
        self.kilit = threading.Lock()


class DownloadManager(QObject):
    """İndirmeleri sıraya alır, arka planda çalıştırır, ilerlemeyi yayar."""

    added = Signal(str, str)            # task_id, başlık
    progress = Signal(str, int, str)    # task_id, yüzde, ayrıntı
    state = Signal(str, str)            # task_id, durum
    finished = Signal(str, bool, str)   # task_id, başarılı mı, mesaj

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._seq = 0
        self._jobs: Dict[str, _Is] = {}
        # Kalıcı kuyruk: yazımlar iki thread'den gelebiliyor (GUI + işçi).
        self._yazma_kilidi = threading.Lock()
        self._son_yazilan: Optional[List[Dict[str, Any]]] = None
        self._kayit_zamanlandi = False

    # ── Sinyal yayma (alıcı silinmiş olabilir) ──────────────────────────────
    def _yay(self, ad: str, *args) -> bool:
        """Sinyali yay; alıcı yok edildiyse `False` dön.

        Pencere kapanınca bu QObject'in C++ tarafı yıkılır ama arka plandaki
        indirme hâlâ koşuyordur. Korumasız `emit`, işçi thread'inde
        `RuntimeError: Internal C++ object already deleted` fırlatıyor,
        `_Task` bunu yakalayıp konsola traceback basıyor ve indirme daha ilk
        ilerleme bildiriminde sessizce ölüyordu. Alıcı gittiyse raporlanacak
        kimse de yok; işi düzgünce bırakmak için `False` yeterli.
        """
        try:
            getattr(self, ad).emit(*args)
            return True
        except RuntimeError:
            return False

    # ── Kuyruk ──────────────────────────────────────────────────────────────
    @staticmethod
    def _hedef(bolum, output: str) -> Optional[str]:
        """İşin disk hedefi (`bolum_hedefi`); kurulamıyorsa None."""
        try:
            return os.path.normcase(bolum_hedefi(output, bolum))
        except (ValueError, TypeError):
            return None

    def kuyruktaki_is(self, entry: Dict[str, Any], output: str = "") -> Optional[str]:
        """Bu bölüm için BİTMEMİŞ bir iş varsa kimliği, yoksa None.

        Anahtar nesne değil çözülmüş HEDEF YOL: aynı bölüm yeniden getirilmiş
        başka bir `AdapterBolum` nesnesiyle de gelebiliyor, iki kaynağın
        slug'ları da aynı yola düşebiliyor. İki yt-dlp aynı `outtmpl`'e
        yazınca ikisi de "tamamlandı" diyor ama dosya bozuk çıkıyordu
        (ölçüldü: 2.000.000 baytlık akıştan 2.429.184 baytlık dosya).
        """
        bolum = (entry or {}).get("obj")
        if bolum is None:
            return None
        hedef = self._hedef(bolum, output or prefs.indirme_dizini())
        if hedef is None:
            return None
        for task_id, job in self._jobs.items():
            if job.durum not in BITMIS_DURUMLAR and job.hedef == hedef:
                return task_id
        return None

    def enqueue(self, entry: Dict[str, Any], output: str = "",
                fansub: Optional[str] = None) -> Optional[str]:
        """Bölümü kuyruğa al; aynı hedefe bitmemiş iş varsa ONUN kimliği döner.

        ``fansub``: `best_video(by_fansub=)`; None otomatik seçim.
        """
        bolum = (entry or {}).get("obj")
        if bolum is None:
            return None
        tercih = prefs.oku()
        output = output or prefs.indirme_dizini(tercih)
        mevcut = self.kuyruktaki_is(entry, output)
        if mevcut is not None:
            return mevcut
        # Eşzamanlılık "paralel indirme sayisi" ayarından; her kuyruğa girişte
        # tazeleniyor ki kullanıcı ayarı değiştirince yeniden başlatmak gerekmesin.
        set_long_task_limit(tercih.paralel)

        self._seq += 1
        task_id = f"dl{self._seq}"
        title = satir_basligi(entry)
        job = _Is(task_id, entry, title, output, fansub=fansub)
        job.hedef = self._hedef(bolum, output)
        try:
            job.klasor = os.path.dirname(bolum_hedefi(output, bolum))
        except (ValueError, TypeError):
            job.klasor = output
        self._jobs[task_id] = job
        self._yay("added", task_id, title)
        self._basla(job)
        return task_id

    # ── Duraklat / sürdür ───────────────────────────────────────────────────
    def pause(self, task_id: str) -> bool:
        """İşi duraklat: bekliyorsa hemen, iniyorsa bir sonraki ilerlemede.

        yt-dlp'de "duraklat" yok; iptalle durdurulup `.part` diskte bırakılıyor,
        "Devam et" aynı işi yeniden başlatıyor ve yt-dlp Range isteğiyle
        kaldığı yerden sürdürüyor. İnen iş thread'i gerçekten bırakana kadar
        "indiriliyor" kalır: o arada sürdürmek iki thread'i aynı dosyaya
        yazdırırdı.
        """
        job = self._jobs.get(task_id)
        if job is None or job.durum not in CALISAN_DURUMLAR:
            return False
        job.duraklat = True
        job.iptal.set()
        if job.durum == DURUM_BEKLIYOR:
            self._duraklatildi(job)
        else:
            self._yay("progress", task_id, -1, "duraklatılıyor…")
        return True

    def resume(self, task_id: str) -> bool:
        """Duraklatılmış işi yeniden kuyruğa al (aynı satır, aynı iş)."""
        job = self._jobs.get(task_id)
        if job is None or job.durum != DURUM_DURAKLATILDI:
            return False
        set_long_task_limit(prefs.oku().paralel)
        self._basla(job)
        return True

    def pause_all(self) -> int:
        return sum(1 for task_id in list(self._jobs) if self.pause(task_id))

    def resume_all(self) -> int:
        return sum(1 for task_id in list(self._jobs) if self.resume(task_id))

    def retry(self, task_id: str) -> Optional[str]:
        """Başarısız/iptal edilmiş işi aynı satırda yeniden kuyruğa al."""
        job = self._jobs.get(task_id)
        if job is None or job.durum not in (DURUM_HATA, DURUM_IPTAL):
            return None
        # Bu iş bitince aynı bölüm yeniden kuyruğa alınmış olabilir; ikisi
        # birden aynı dosyaya yazmasın.
        if self.kuyruktaki_is(job.entry, job.output) is not None:
            return None
        set_long_task_limit(prefs.oku().paralel)
        self._basla(job)
        return task_id

    def cancel(self, task_id: str) -> bool:
        job = self._jobs.get(task_id)
        if job is None or job.durum in BITMIS_DURUMLAR:
            return False
        job.duraklat = False
        job.iptal.set()
        if job.durum in (DURUM_BEKLIYOR, DURUM_DURAKLATILDI):
            # Havuz doluysa bu iş dakikalarca başlamayabilir; kullanıcıya
            # "iptal edildi"yi o zamana kadar beklettirmenin anlamı yok.
            # Duraklatılmış işin zaten thread'i yok.
            self._bitir(job, False, DURUM_IPTAL)
        return True

    def cancel_all(self) -> int:
        """Bekleyen ve çalışan tüm işleri iptal et; iptal edilen sayısını döndür."""
        return sum(1 for task_id in list(self._jobs) if self.cancel(task_id))

    # ── Sorgular (test ve UI için) ──────────────────────────────────────────
    def durum(self, task_id: str) -> Optional[str]:
        job = self._jobs.get(task_id)
        return job.durum if job else None

    def active_ids(self) -> List[str]:
        """Bekleyen ya da inen işler (duraklatılanlar HARİÇ: thread tutmuyorlar;
        kapanış sorusu ve menüdeki sayaç bunlara bakıyor)."""
        return [tid for tid, job in self._jobs.items()
                if job.durum in CALISAN_DURUMLAR]

    def duraklatilan_ids(self) -> List[str]:
        return [tid for tid, job in self._jobs.items()
                if job.durum == DURUM_DURAKLATILDI]

    def dosya(self, task_id: str) -> Optional[str]:
        """Tamamlanan işin diskteki dosyası (yoksa None)."""
        job = self._jobs.get(task_id)
        return job.dosya if job else None

    def klasor(self, task_id: str) -> str:
        """İşin seri klasörü (`<indirilenler>/<seri>`)."""
        job = self._jobs.get(task_id)
        return job.klasor if job else ""

    def ayrinti(self, task_id: str) -> str:
        """Başarısız işin ham hata metni (satırın araç ipucu)."""
        job = self._jobs.get(task_id)
        return job.ayrinti if job else ""

    def kayit(self, task_id: str) -> Optional[Dict[str, Any]]:
        """İşin bölüm kaydı (`entry`); "Oynat" bununla oynatma yoluna gider."""
        job = self._jobs.get(task_id)
        return job.entry if job else None

    # ── İç işleyiş ──────────────────────────────────────────────────────────
    def _basla(self, job: _Is) -> None:
        job.iptal.clear()
        job.duraklat = False
        with job.kilit:
            job.durum = DURUM_BEKLIYOR
            job.nesil += 1
            nesil = job.nesil
        self._yay("state", job.task_id, DURUM_BEKLIYOR)
        # long_running: indirme, işi bitene kadar thread'i tutar; UI görevlerinin
        # (arama, bölüm listesi) havuzunu tüketmemesi için ayrı havuza gider.
        run_bg(self._run, job.task_id, nesil, long_running=True)
        self._kaydet_sonra()

    def _duraklatildi(self, job: _Is) -> bool:
        """İşi "duraklatıldı"ya geçir (aynı iş için ikinci çağrı yok sayılır)."""
        with job.kilit:
            if job.durum in BITMIS_DURUMLAR or job.durum == DURUM_DURAKLATILDI:
                return False
            job.durum = DURUM_DURAKLATILDI
        self._yay("state", job.task_id, DURUM_DURAKLATILDI)
        self._kaydet()
        return True

    def _kesildi(self, job: _Is) -> None:
        """İptal bayrağıyla duran iş: duraklatma mı, iptal mi?"""
        if job.duraklat:
            self._duraklatildi(job)
        else:
            self._bitir(job, False, DURUM_IPTAL)

    def _bitir(self, job: _Is, ok: bool, durum: str,
               mesaj: Optional[str] = None) -> bool:
        """İşi sonlandır. Aynı iş için ikinci çağrı yok sayılır."""
        with job.kilit:
            if job.durum in BITMIS_DURUMLAR:
                return False
            job.durum = durum
        self._yay("state", job.task_id, durum)
        self._yay("finished", job.task_id, ok, mesaj or durum)
        self._kaydet()                  # biten iş kalıcı kuyruktan düşer
        return True

    def _hook_uret(self, job: _Is):
        """`progress_hooks` için iptal farkındalıklı ilerleme hook'u."""
        task_id = job.task_id

        def yay(pct: int, detail: str) -> None:
            # Alıcı yok edildiyse (pencere kapandı) indirmeyi sürdürmenin
            # anlamı yok: iptal yolunu kullanarak yt-dlp'yi düzgünce durdur.
            if not self._yay("progress", task_id, pct, detail):
                raise IndirmeIptal(task_id)

        def hook(d: Dict[str, Any]) -> None:
            if job.iptal.is_set():
                raise IndirmeIptal(task_id)
            durum = d.get("status")
            if durum == "downloading":
                done = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = int(done * 100 / total) if total else 0
                speed = d.get("speed")
                detail = f"{_fmt_size(done)} / {_fmt_size(total)}"
                if speed:
                    detail += f" · {_fmt_size(speed)}/s"
                yay(pct, detail)
            elif durum == "finished":
                yay(100, "birleştiriliyor…")
            elif durum == "error":
                # aria2c fallback'i bu yolla haber veriyor; iş henüz bitmedi.
                mesaj = d.get("message")
                if mesaj:
                    yay(0, f"hata: {mesaj}")

        return hook

    def _run(self, task_id: str, nesil: Optional[int] = None) -> None:
        job = self._jobs.get(task_id)
        if job is None:
            return
        if nesil is not None and nesil != job.nesil:
            return                      # iş bu arada yeniden başlatıldı
        if job.iptal.is_set():
            self._kesildi(job)
            return

        # Ayarlar iş BAŞLARKEN okunur: kuyruk uzunsa kullanıcı arada ayarı
        # değiştirmiş olabilir, sıradaki işler yenisine uymalı.
        tercih = prefs.oku()
        bolum = (job.entry or {}).get("obj")

        with job.kilit:
            job.durum = DURUM_INDIRILIYOR
        if not self._yay("state", task_id, DURUM_INDIRILIYOR):
            # Pencere yok edilmiş: kimse dinlemiyor, indirmeye hiç başlama.
            return
        self._yay("progress", task_id, 0, "video aranıyor…")

        try:
            video = bolum.best_video(by_res=tercih.max_res,
                                     early_subset=tercih.aday_sayisi,
                                     **_fansub_arg(job.fansub))
        except Exception as exc:
            self._hata_bitir(job, exc)
            return
        if video is None:
            self._bitir(job, False, DURUM_HATA, "çalışan video bulunamadı")
            return

        hook = self._hook_uret(job)
        son_hata: Optional[Exception] = None
        # İndirilemeyen adresler. İkinci deneme eskiden AYNI videoyu yeniden
        # indiriyordu; `best_video` her çağrıda aynı ilk adayı seçtiği için
        # `atla` olmadan çalışan diğer adaylara hiç geçilmiyordu.
        basarisiz: set = set()
        for deneme in range(1, MAX_DENEME + 1):
            if job.iptal.is_set():
                break
            if deneme > 1:
                self._yay("progress", task_id, 0, f"{deneme}. deneme…")
                video = self._siradaki_aday(bolum, video, tercih, basarisiz,
                                            job.output, job.fansub)
            self._aday_sabitle(job, bolum, video)
            try:
                prefs.indir(video, hook, job.output, tercih)
            except IndirmeIptal:
                break
            except Exception as exc:
                son_hata = exc
                adres = getattr(video, "url", None)
                if adres:
                    basarisiz.add(adres)
                continue
            if job.iptal.is_set():
                # aria2c'de hook yalnızca sonda ateşleniyor: iş iptal istendikten
                # sonra bitmiş olabilir. "Tamamlandı" demek kullanıcıyı yanıltır.
                break
            # İndirme geçmişi burada yazılır; eski GUI'de yazılıyordu, Qt'de
            # hiç çağrılmıyordu (bölüm satırındaki ⬇ rozeti bu kayda bakıyor).
            prefs.gecmis_kaydet(bolum, "indirildi")
            # Yol kaydedilir: "Oynat" kullanıcı sonradan indirme klasörünü
            # değiştirse de bu dosyayı bulsun. Kayda da iliştiriliyor; bölüm
            # listesindeki ▶ aynı kayıtla oynatıyor.
            try:
                job.dosya = oynatilabilir_dosya(bolum_hedefi(job.output, bolum))
            except (ValueError, TypeError):
                job.dosya = None
            if job.dosya:
                job.entry["yerel_dosya"] = job.dosya
            self._bitir(job, True, DURUM_TAMAMLANDI)
            return

        if job.iptal.is_set():
            self._kesildi(job)
        elif son_hata is not None:
            self._hata_bitir(job, son_hata)
        else:
            self._bitir(job, False, DURUM_HATA, "indirme tamamlanamadı")

    def _aday_sabitle(self, job: _Is, bolum, video) -> None:
        """İndirilecek adayı işe yaz; ÖNCEKİNDEN farklıysa yarım dosyayı sil.

        yt-dlp mevcut `.part`'ı, yeni çalıştırma BAŞKA bir akışı indirse bile
        Range isteğiyle sürdürüyor: ölçüldü, A'nın ilk 1 MB'ı + B'nin kalanı,
        ikisine de eşit olmayan bir dosya. Duraklat/sürdür (ve iptalden sonra
        "Yeniden Dene", uygulama yeniden açılınca "Devam et") `best_video`'yu
        yeniden çağırıyor; aday değiştiyse eski baytlar gitmeli. Aynı adreste
        `.part` korunur — sürdürmenin bütün amacı bu. Adres bilinmiyorsa
        (ilk çalıştırma) dokunulmaz: bu işin yarım dosyası henüz yok.
        """
        adres = getattr(video, "url", None)
        if job.secilen_url and adres != job.secilen_url:
            try:
                yarim_dosyalari_sil(bolum_hedefi(job.output, bolum))
            except (ValueError, OSError, TypeError):
                pass
        if adres != job.secilen_url:
            job.secilen_url = adres
            job.secilen_etiket = getattr(video, "label", None)
            self._kaydet()

    # ── Kalıcı kuyruk ───────────────────────────────────────────────────────
    def _kayit_uret(self, job: _Is, durum: Optional[str] = None
                    ) -> Optional[Dict[str, Any]]:
        """İşin diske yazılacak hâli; yeniden kurulamayacak iş için None.

        Yalnızca kayıttaki bir kaynağın `AdapterBolum`'u yazılır: nesne yeniden
        kurulurken ağa çıkılmamalı, bu da kaynak adı + bölüm kimliği ister.
        `sources.adapter` hiç yüklenmediyse elimizde `AdapterBolum` da yoktur
        (import etmeye gerek yok, `sys.modules`'a bakmak yeter).
        """
        entry = job.entry or {}
        bolum = entry.get("obj")
        modul = sys.modules.get("turkanime_api.sources.adapter")
        if modul is None or not isinstance(bolum, modul.AdapterBolum):
            return None
        kaynak = kaynak_kaydi.bul(entry.get("kaynak") or "")
        if kaynak is None:
            return None
        bolum_id = _bolum_kimligi_coz(kaynak, bolum.url)
        if bolum_id is None:
            return None
        anime = getattr(bolum, "anime", None)
        return {
            "kaynak": kaynak.ad,
            "anime_kimlik": str(entry.get("kimlik") or ""),
            "anime_slug": str(getattr(anime, "slug", "") or ""),
            "anime_baslik": str(getattr(anime, "title", "") or ""),
            "bolum_id": bolum_id,
            "bolum_baslik": str(bolum.title or ""),
            "bolum_slug": str(bolum.slug or ""),
            "baslik": str(entry.get("title") or ""),
            "seri_adi": str(entry.get("seri_adi") or ""),
            "kapak": str(entry.get("kapak") or ""),
            "output": job.output,
            "fansub": job.fansub,
            "secilen_url": job.secilen_url,
            "secilen_etiket": job.secilen_etiket,
            "durum": durum or job.durum,
        }

    def _kayitlar(self, durum: Optional[str] = None) -> List[Dict[str, Any]]:
        isler = [j for j in list(self._jobs.values()) if j.durum not in BITMIS_DURUMLAR]
        return [k for k in (self._kayit_uret(j, durum) for j in isler) if k]

    def _kaydet(self, durum: Optional[str] = None) -> None:
        """Bitmemiş işleri `indirme_kuyrugu.json`'a atomik yaz (değiştiyse).

        Eskiden kuyruk yalnızca bellekteydi: 40 bölümlük toplu indirme yanlış
        bir kapatmada ya da çökmede tamamen kayboluyordu.
        """
        kayitlar = self._kayitlar(durum)
        with self._yazma_kilidi:
            if kayitlar == self._son_yazilan:
                return
            try:
                yol = kuyruk_yolu()
                if kayitlar or os.path.exists(yol):
                    from ...cli.dosyalar import atomik_json_yaz
                    atomik_json_yaz(yol, {"surum": KUYRUK_SURUMU, "isler": kayitlar})
            except Exception as exc:     # kuyruğu kaydedememek indirmeyi durdurmasın
                print(f"[İndirme] kuyruk kaydedilemedi: {exc}")
                return
            self._son_yazilan = kayitlar

    def _kaydet_sonra(self) -> None:
        """GUI thread'inden: aynı olay döngüsü turundaki yazımları birleştir.

        Toplu indirme 40 bölümü art arda kuyruğa alıyor; her biri için ayrı
        fsync'li yazım arayüzü gözle görülür dondururdu.
        """
        if self._kayit_zamanlandi:
            return
        self._kayit_zamanlandi = True

        def yaz() -> None:
            self._kayit_zamanlandi = False
            self._kaydet()
        QTimer.singleShot(0, self, yaz)

    def kapanista_kaydet(self) -> None:
        """Kapanış: bitmemiş her iş "duraklatıldı" olarak yazılır.

        İnen işlerin thread'i iptal bayrağını bir sonraki ilerlemede görüyor;
        dosya o ana kadar beklemeden, şimdi, son hâliyle yazılmalı.
        """
        self._kaydet(DURUM_DURAKLATILDI)

    def geri_yukle(self) -> int:
        """Önceki oturumun kuyruğunu "duraklatıldı" işler olarak geri yükle.

        Ağa çıkılmaz, iş başlatılmaz: kullanıcı "Devam et"/"Tümünü sürdür" ile
        başlatır (açılışta kendiliğinden 40 indirme başlatmak, kullanıcının
        belki bilerek bıraktığı işleri ölçülü bağlantıda sürdürmek olurdu).
        Bozuk dosya `ayarlar.json` gibi `*.bozuk-*` adıyla kenara ayrılır.
        """
        from ...cli.dosyalar import _json_oku
        try:
            veri = _json_oku(kuyruk_yolu(), {"isler": []})
        except Exception as exc:
            print(f"[İndirme] kuyruk okunamadı: {exc}")
            return 0
        isler = veri.get("isler") if isinstance(veri.get("isler"), list) else []
        adet = 0
        for kayit in isler:
            if not isinstance(kayit, dict):
                continue
            try:
                bolum = _bolumu_kur(kayit)
            except Exception as exc:
                print(f"[İndirme] kuyruk kaydı kurulamadı: {exc}")
                continue
            if bolum is None:
                continue
            entry = {"title": kayit.get("baslik") or bolum.title, "obj": bolum,
                     "kaynak": kayit.get("kaynak"),
                     "kimlik": kayit.get("anime_kimlik") or "",
                     "seri_adi": kayit.get("seri_adi") or "",
                     "kapak": kayit.get("kapak") or ""}
            output = str(kayit.get("output") or "") or prefs.indirme_dizini()
            if self.kuyruktaki_is(entry, output) is not None:
                continue
            self._seq += 1
            task_id = f"dl{self._seq}"
            job = _Is(task_id, entry, satir_basligi(entry), output,
                      fansub=kayit.get("fansub") or None)
            job.hedef = self._hedef(bolum, output)
            try:
                job.klasor = os.path.dirname(bolum_hedefi(output, bolum))
            except (ValueError, TypeError):
                job.klasor = output
            job.secilen_url = kayit.get("secilen_url") or None
            job.secilen_etiket = kayit.get("secilen_etiket") or None
            job.durum = DURUM_DURAKLATILDI
            self._jobs[task_id] = job
            self._yay("added", task_id, job.title)
            self._yay("state", task_id, DURUM_DURAKLATILDI)
            adet += 1
        self._son_yazilan = self._kayitlar()
        return adet

    def _hata_bitir(self, job: _Is, exc: BaseException) -> None:
        """İşi hatayla bitir: satıra kısa Türkçe sebep, ham metin araç ipucuna.

        Eskiden satırda "hata: HTTP Error 403: Forbidden" ya da sayfa dolusu
        "HTTPSConnectionPool(...)" yazıyordu; kaynak hatası (Cloudflare, çerez,
        arşivde kayıt yok) ise "video hatası: …" önekine gömülüyordu.
        """
        kisa, ayrinti = insanlastir(exc)
        if len(kisa) > HATA_METNI_SINIRI:
            kisa = kisa[:HATA_METNI_SINIRI - 1] + "…"
        job.ayrinti = ayrinti
        self._bitir(job, False, DURUM_HATA, kisa)

    @staticmethod
    def _siradaki_aday(bolum, video, tercih, basarisiz: set, output: str,
                       fansub: Optional[str] = None):
        """İkinci deneme için `atla` ile başka bir aday iste.

        Başka aday yoksa (ya da arama patlarsa) AYNI video yeniden denenir:
        kaynak sunucuları sık sık geçici 5xx/timeout veriyor ve `MAX_DENEME`
        tam da bunun için var; `.part` korunur, yt-dlp kaldığı yerden devam
        eder. Aday DEĞİŞİYORSA yarım dosyalar silinir: yt-dlp `.part`'ı
        gördüğünde başka akışın baytlarına ekleme yapıyor ve dosya bozuluyordu.
        """
        if not basarisiz:
            return video
        try:
            yeni = bolum.best_video(by_res=tercih.max_res,
                                    early_subset=tercih.aday_sayisi,
                                    atla=frozenset(basarisiz),
                                    **_fansub_arg(fansub))
        except Exception:
            return video
        if yeni is None or getattr(yeni, "url", None) in basarisiz:
            return video
        try:
            yarim_dosyalari_sil(bolum_hedefi(output, bolum))
        except (ValueError, OSError):
            pass
        return yeni


__all__ = ["DownloadManager", "IndirmeIptal",
           "satir_basligi", "klasoru_ac",
           "DURUM_BEKLIYOR", "DURUM_INDIRILIYOR", "DURUM_TAMAMLANDI",
           "DURUM_HATA", "DURUM_IPTAL", "DURUM_DURAKLATILDI", "BITMIS_DURUMLAR",
           "CALISAN_DURUMLAR", "MAX_DENEME", "kuyruk_yolu", "KUYRUK_DOSYASI"]
