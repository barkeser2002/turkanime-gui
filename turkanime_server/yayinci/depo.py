"""git komut satırı sarmalayıcısı.

**Neden `git` ikilisi, GitPython değil.** Yayıncının ihtiyacı on kadar komut;
bunun için yeni bir bağımlılık (ve onun sürüm/uyum yükü) taşımak pahalı. Ayrıca
`push` sırasında kimlik yönetimi zaten git'in kendi mekanizmasıyla yapılıyor.

**Sır sızdırmama.** İki kural burada zorlanır:

1. Token argümana yazılmaz. `git push https://oauth2:TOKEN@host/...` çalışır
   ama komut satırı `ps` çıktısında ve çoğu CI log'unda görünür. Onun yerine
   token'ı *ortamdan* okuyan bir kimlik yardımcısı geçiriyoruz: yardımcı
   metninde sırrın kendisi değil, adı var.
2. Token `.git/config`'e yazılmaz. Uzak adres kimlik bilgisiz saklanır; yani
   arşiv dizini elden ele geçse bile içinden token çıkmaz.

Ek olarak `gizle()`, dışarı verilen her hata metnini maskeler.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .ayarlar import KULLANICI_DEGISKENI, TOKEN_DEGISKENI

log = logging.getLogger("turkanime.yayinci")


class GitYok(RuntimeError):
    """`git` ikilisi bulunamadı."""


class GitHatasi(RuntimeError):
    """git komutu sıfırdan farklı döndü (mesaj maskelenmiştir)."""


class ItmeReddedildi(GitHatasi):
    """Uzak, itmeyi ileri-sarım olmadığı için reddetti.

    Ağ/kimlik hatasından ayrı bir tip: ikisinin toparlanma yolu farklı. Ağ
    hatasında beklenir, reddedilmede geçmişin neden ayrıştığına bakılır.
    """


# `git push` reddedildiğinde stderr'de görünen izler.
_REDDETME_IZLERI = ("[rejected]", "non-fast-forward", "fetch first",
                    "updates were rejected")

# Geçmişi yeniden yazan budama sonrası uzak ancak zorla itmeyle güncellenebilir.
# Bu bilgi *diske* yazılır: itme o turda başarısız olursa (uzak erişilemez,
# token süresi dolmuş...) bilgi bellekte kalırsa sonraki turlar sonsuza dek
# "! [rejected]" alır ve uzak arşiv kalıcı olarak donar. `.git/` altında durur:
# çalışma ağacının parçası değil, yani yayınlanan arşive sızmaz.
ZORLA_BAYRAGI = "turkanime-yayin-zorla-it"


# Kimlik yardımcısı: sırrı değeriyle değil, *ortam değişkeni adıyla* taşır.
# git bunu `sh -c` ile çalıştırır (Windows'ta git'in kendi sh'ı ile).
KIMLIK_YARDIMCISI = (
    "!f() { if [ \"$1\" = get ]; then "
    "printf 'username=%s\\npassword=%s\\n' "
    f"\"${{{KULLANICI_DEGISKENI}:-oauth2}}\" \"${TOKEN_DEGISKENI}\"; "
    "fi; }; f"
)

# Arşiv baytı baytına korunmalı. Geliştirme makinelerinde `core.autocrlf=true`
# yaygın; bu ayar on binlerce JSON'u satır sonu farkıyla "değişmiş" gösterir ve
# her turda dev bir sahte commit üretirdi.
GITATTRIBUTES = (
    "# Arşiv baytı baytına korunur — satır sonu dönüşümü tüm JSON'ları\n"
    "# sahte değişiklik olarak gösterir ve depoyu şişirir.\n"
    "* -text\n"
)

# Yazıcının atomik yazım artığı (`.dizin.json.1234.tmp`). Tarayıcı yayınla aynı
# anda koşuyorsa bu dosyalar `git add -A` ile commit'e girerdi.
GITIGNORE = (
    "# Atomik yazım geçici dosyaları (crawler/yazici.py)\n"
    ".*.tmp\n"
)


def gizle(metin: str, *sirlar: Optional[str]) -> str:
    """Sırları ve URL'ye gömülü kimlik bilgilerini maskele."""
    out = metin or ""
    for sir in sirlar:
        # Çok kısa değerler ("", "x") metnin her yerine uyar; maskelemek
        # çıktıyı okunmaz yapar ve zaten sır sayılmazlar.
        if sir and len(sir) >= 6:
            out = out.replace(sir, "***")
    return re.sub(r"(\w+://)[^/\s@]+@", r"\1***@", out)


class GitDepo:
    """Tek bir git çalışma ağacı.

    Arşiv dizininin **kendisi** çalışma ağacıdır: ikinci bir kopya tutmak on
    binlerce küçük dosyayı diskte ikiye katlar ve iki ağaç arasında senkron
    hatası riski doğurur. Tarayıcı atomik yazdığı için, yayın sırasında koşan
    bir tarama en fazla "yarım tur"u commit'ler; kalanı sonraki tura kalır.
    """

    def __init__(
        self,
        yol: Path | str,
        *,
        dal: str = "master",
        ad: str = "turkanime-arsiv",
        eposta: str = "turkanime-arsiv@users.noreply.localhost",
        token: Optional[str] = None,
        kullanici: str = "oauth2",
        ssh_anahtar: Optional[Path] = None,
    ):
        self.yol = Path(yol)
        self.dal = dal
        self.ad = ad
        self.eposta = eposta
        self.token = token
        self.kullanici = kullanici
        self.ssh_anahtar = Path(ssh_anahtar) if ssh_anahtar else None

    # ── Alt seviye ──────────────────────────────────────────────────────────
    @staticmethod
    def git_var_mi() -> bool:
        return shutil.which("git") is not None

    def depo_mu(self) -> bool:
        return (self.yol / ".git").exists()

    def _ortam(self) -> Dict[str, str]:
        ort = dict(os.environ)
        # Kimlik istemi = üretimde sonsuza dek asılı kalan bir süreç.
        ort["GIT_TERMINAL_PROMPT"] = "0"
        if self.token:
            ort[TOKEN_DEGISKENI] = self.token
            ort[KULLANICI_DEGISKENI] = self.kullanici
        if self.ssh_anahtar:
            # `IdentitiesOnly`: ajanda başka anahtar varsa git onu denemesin,
            # sunucu birkaç yanlış denemeden sonra bağlantıyı keser.
            ort["GIT_SSH_COMMAND"] = (
                f'ssh -i "{self.ssh_anahtar}" -o IdentitiesOnly=yes '
                "-o StrictHostKeyChecking=accept-new"
            )
        return ort

    def _temel(self) -> List[str]:
        # Makine yapılandırması yayını etkilememeli: `core.autocrlf` arşivi
        # baştan sona sahte değişikliğe boğar, `commit.gpgsign` ise başsız
        # sunucuda commit'i kilitler. İkisi de geliştirici makinelerinde açık.
        return [
            "git", "-C", str(self.yol),
            "-c", "core.autocrlf=false",
            "-c", "core.safecrlf=false",
            "-c", "commit.gpgsign=false",
            "-c", "i18n.commitEncoding=UTF-8",
            "-c", f"user.name={self.ad}",
            "-c", f"user.email={self.eposta}",
        ]

    def calistir(self, *argv: str, kimlik: bool = False, kontrol: bool = True,
                 girdi: Optional[str] = None) -> subprocess.CompletedProcess:
        """git komutunu çalıştır; hata metni maskelenir."""
        if not self.git_var_mi():
            raise GitYok("`git` bulunamadı — yayıncı git ikilisine muhtaç")
        komut = self._temel()
        if kimlik and self.token:
            # Boş `credential.helper` miras alınan yardımcıları sıfırlar;
            # aksi hâlde makinedeki bir keychain araya girip istem açabilir.
            komut += ["-c", "credential.helper=", "-c",
                      f"credential.helper={KIMLIK_YARDIMCISI}"]
        komut += list(argv)
        sonuc = subprocess.run(                       # noqa: S603
            komut, cwd=str(self.yol), env=self._ortam(),
            input=girdi, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        if kontrol and sonuc.returncode != 0:
            raise GitHatasi(
                f"git {' '.join(argv[:2])} başarısız ({sonuc.returncode}): "
                + gizle((sonuc.stderr or sonuc.stdout or "").strip(), self.token)
            )
        return sonuc

    # ── Kurulum ─────────────────────────────────────────────────────────────
    def hazirla(self) -> bool:
        """Depo yoksa kur. ``True`` = yeni kuruldu."""
        self.yol.mkdir(parents=True, exist_ok=True)
        yeni = not self.depo_mu()
        if yeni:
            # `git init -b` git 2.28+ ister; `symbolic-ref` her sürümde çalışır
            # ve Docker imajındaki git sürümüne bağımlılık bırakmaz.
            self.calistir("init", "--quiet")
            self.calistir("symbolic-ref", "HEAD", f"refs/heads/{self.dal}")
        for ad, icerik in ((".gitattributes", GITATTRIBUTES), (".gitignore", GITIGNORE)):
            hedef = self.yol / ad
            if not hedef.exists():
                hedef.write_text(icerik, encoding="utf-8")
        return yeni

    # ── Sorgu ───────────────────────────────────────────────────────────────
    def durum(self) -> List[Tuple[str, str]]:
        """``[(kod, yol), ...]`` — sahnelenmemiş hâliyle çalışma ağacı farkı.

        `-uall`: takip edilmeyen dosyalar tek tek listelenir. Varsayılan mod
        yeni bir anime klasörünü tek satırda ("animeler/x/") özetler ve dosya
        sayıları yanlış çıkardı.
        """
        ham = self.calistir("status", "--porcelain", "-z", "-uall").stdout
        parcalar = [p for p in ham.split("\0") if p]
        cikti: List[Tuple[str, str]] = []
        i = 0
        while i < len(parcalar):
            girdi = parcalar[i]
            i += 1
            if len(girdi) < 4:
                continue
            kod, yol = girdi[:2], girdi[3:]
            if "R" in kod or "C" in kod:
                i += 1                    # yeniden adlandırmada kaynak yol ekte
            cikti.append((kod, yol))
        return cikti

    def takip_edilen_sayisi(self) -> int:
        ham = self.calistir("ls-files", "-z").stdout
        return sum(1 for p in ham.split("\0") if p)

    def commit_sayisi(self) -> int:
        sonuc = self.calistir("rev-list", "--count", "HEAD", kontrol=False)
        if sonuc.returncode != 0:
            return 0                      # henüz commit yok (unborn HEAD)
        try:
            return int(sonuc.stdout.strip())
        except ValueError:
            return 0

    def son_sha(self) -> Optional[str]:
        sonuc = self.calistir("rev-parse", "HEAD", kontrol=False)
        return sonuc.stdout.strip() or None if sonuc.returncode == 0 else None

    def commit_dosyalari(self, sha: str = "HEAD") -> List[str]:
        """Commit'in gerçekten değiştirdiği dosyalar."""
        ham = self.calistir("diff-tree", "--no-commit-id", "--name-only",
                            "-r", "-z", sha).stdout
        return [p for p in ham.split("\0") if p]

    # ── Yazma ───────────────────────────────────────────────────────────────
    def sahnele(self) -> None:
        self.calistir("add", "-A")

    def commit(self, mesaj: str) -> Optional[str]:
        """Sahnelenmişi commit'le; sahnede bir şey yoksa ``None``.

        Mesaj argüman değil stdin ile veriliyor: çok satırlı ve Türkçe mesajlar
        Windows'ta argüman kodlamasına takılıyor, ayrıca uzun mesaj komut satırı
        sınırına dayanabiliyor.
        """
        sonuc = self.calistir("commit", "--no-verify", "-F", "-",
                              girdi=mesaj, kontrol=False)
        if sonuc.returncode != 0:
            cikti = (sonuc.stdout or "") + (sonuc.stderr or "")
            if "nothing to commit" in cikti or "working tree clean" in cikti:
                return None
            raise GitHatasi("git commit başarısız: " + gizle(cikti.strip(), self.token))
        return self.son_sha()

    def it(self, uzak: str, *, zorla: bool = False) -> None:
        """Uzağa it. Uzak adres `.git/config`'e yazılmaz.

        Raises:
            ItmeReddedildi — ileri-sarım değil (geçmişler ayrışmış).
            GitHatasi      — diğer her şey (ağ, kimlik, uzak yok...).
        """
        argv: Sequence[str] = ("push", "--quiet")
        if zorla:
            argv = argv + ("--force",)
        # `HEAD:refs/heads/<dal>`: yerel dal adı ne olursa olsun hedef sabit.
        sonuc = self.calistir(*argv, uzak, f"HEAD:refs/heads/{self.dal}",
                              kimlik=True, kontrol=False)
        if sonuc.returncode == 0:
            return
        ham = ((sonuc.stderr or "") + (sonuc.stdout or "")).strip()
        mesaj = gizle(ham, self.token)
        if any(iz in ham.lower() for iz in _REDDETME_IZLERI):
            raise ItmeReddedildi("git push reddedildi: " + mesaj)
        raise GitHatasi("git push başarısız: " + mesaj)

    def uzakla_esitle(self, uzak: str, mesaj: str) -> bool:
        """Yerel commit'i uzağın ucunun **üstüne** taşı. ``False`` = yapılamadı.

        Zorla itmenin güvenli alternatifi: uzaktaki commit'ler silinmez, yalnızca
        bizim commit'imizin atası değiştirilir. Ağaç (yani arşiv içeriği) aynen
        korunur; `reset --soft` indeksi ve çalışma ağacını ellemez.
        """
        getir = self.calistir("fetch", "--quiet", uzak, f"refs/heads/{self.dal}",
                              kimlik=True, kontrol=False)
        if getir.returncode != 0:
            return False
        self.calistir("reset", "--soft", "FETCH_HEAD")
        self.calistir("add", "-A")
        self.commit(mesaj + "\n\nUzak depo ayrışmıştı; arşiv uzağın ucunun\n"
                            "üstüne yeniden işlendi (zorla itme yapılmadı).\n")
        return True

    # ── Zorla itme bayrağı ──────────────────────────────────────────────────
    def _bayrak_yolu(self) -> Path:
        return self.yol / ".git" / ZORLA_BAYRAGI

    def zorla_gerekiyor(self) -> bool:
        """Önceki turda geçmiş yeniden yazıldı ve uzak hâlâ güncellenmedi mi?"""
        return self._bayrak_yolu().is_file()

    def zorla_isaretle(self, sebep: str = "") -> None:
        try:
            self._bayrak_yolu().write_text(sebep or "gecmis-budandi\n",
                                           encoding="utf-8")
        except OSError as e:
            log.warning("zorla itme bayrağı yazılamadı: %s", e)

    def zorla_temizle(self) -> None:
        try:
            self._bayrak_yolu().unlink(missing_ok=True)
        except OSError as e:
            log.warning("zorla itme bayrağı silinemedi: %s", e)

    # ── Bakım ───────────────────────────────────────────────────────────────
    def gc(self, tam: bool = False) -> None:
        """Nesne deposunu topla. ``tam=True`` erişilemez nesneleri de atar."""
        if tam:
            self.calistir("gc", "--prune=now", "--quiet", kontrol=False)
        else:
            self.calistir("gc", "--auto", "--quiet", kontrol=False)

    def gecmisi_buda(self, mesaj: str) -> Optional[str]:
        """Geçmişi tek bir kök commit'e indir (``--orphan`` tazeleme).

        Çalışma ağacına dokunulmaz: yalnızca yeni bir köksüz dal üzerinde
        mevcut ağaç commit'lenir ve hedef dal onunla değiştirilir. Eski
        nesneler erişilemez hâle gelir; reflog süresi doldurulup `gc` ile
        gerçekten silinirler.
        """
        gecici = "yayin-tazeleme"
        self.calistir("checkout", "--orphan", gecici, "--quiet")
        self.calistir("add", "-A")
        sha = self.commit(mesaj)
        self.calistir("branch", "-M", self.dal)
        self.calistir("reflog", "expire", "--expire=now", "--all", kontrol=False)
        self.gc(tam=True)
        return sha


__all__ = ["GitDepo", "GitHatasi", "GitYok", "ItmeReddedildi", "gizle",
           "GITATTRIBUTES", "GITIGNORE", "KIMLIK_YARDIMCISI", "ZORLA_BAYRAGI"]
