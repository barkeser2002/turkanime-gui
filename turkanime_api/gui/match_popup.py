"""
Fuzzy-match onay popup'ı.

Adapter aramasında tam eşleşme yoksa (skor < threshold) ama belirli bir floor
üzerindeki adaylar varsa, kullanıcıya "kaynakta tam bulamadık, en olası adaylar
bunlar — biri sizin aradığınız mı?" sorusunu sorar.

İki API var:
    - :func:`ask_match` — CustomTkinter (modern GUI) popup
    - :func:`ask_match_cli` — questionary tabanlı CLI prompt

Çağıran kod yalnızca :class:`MatchResult` listesi geçirir; popup seçilen sonucu
ya da iptal için ``None`` döndürür.
"""
from __future__ import annotations

from typing import List, Optional

try:
    import customtkinter as ctk
    _HAS_CTK = True
except ImportError:
    _HAS_CTK = False

from ..common.title_match import MatchResult


def _score_color(score: float) -> str:
    """0..1 skoru için yeşil→sarı→kırmızı gradyan döndür."""
    if score >= 0.85:
        return "#10b981"   # yeşil
    if score >= 0.70:
        return "#f59e0b"   # amber
    return "#ef4444"       # kırmızı


def ask_match(
    query: str,
    candidates: List[MatchResult],
    source_name: str = "Kaynak",
    parent=None,
) -> Optional[MatchResult]:
    """Olası eşleşmeleri gösteren modal popup. Seçilen adayı döndür.

    Args:
        query: Kullanıcının orijinal sorgusu.
        candidates: Skor sıralı :class:`MatchResult` listesi (en iyi başta).
        source_name: Hangi kaynakta arandığı ("Animely" gibi).
        parent: Tk root/master widget — ``None`` ise yeni Tk oluşturulur.

    Returns:
        Seçilen MatchResult, kullanıcı iptal ederse None.
    """
    if not _HAS_CTK:
        return ask_match_cli(query, candidates, source_name)

    if not candidates:
        return None

    selected: List[Optional[MatchResult]] = [None]

    standalone = parent is None
    win = ctk.CTkToplevel(parent) if parent is not None else ctk.CTk()
    win.title(f"{source_name} — Olası Eşleşmeler")
    win.geometry("640x520")
    win.configure(fg_color="#0f0f17")
    try:
        win.transient(parent)
        win.grab_set()
    except Exception:
        pass

    # Başlık
    header = ctk.CTkFrame(win, fg_color="#1a1a26", corner_radius=0, height=80)
    header.pack(fill="x")
    header.pack_propagate(False)
    ctk.CTkLabel(
        header,
        text=f"'{query}' için tam eşleşme bulunamadı",
        font=("Segoe UI", 16, "bold"),
        text_color="#f1f5f9",
    ).pack(anchor="w", padx=20, pady=(15, 0))
    ctk.CTkLabel(
        header,
        text=f"{source_name}'da bulunan en yakın {len(candidates)} aday aşağıda. Birini seçin:",
        font=("Segoe UI", 11),
        text_color="#94a3b8",
    ).pack(anchor="w", padx=20, pady=(2, 10))

    # Adaylar scroll listesi
    scroll = ctk.CTkScrollableFrame(win, fg_color="#0f0f17")
    scroll.pack(fill="both", expand=True, padx=15, pady=(10, 10))

    def _pick(item: MatchResult):
        selected[0] = item
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    for cand in candidates:
        card = ctk.CTkFrame(scroll, fg_color="#1a1a26", corner_radius=10)
        card.pack(fill="x", pady=4, padx=2)
        card.bind("<Enter>", lambda e, c=card: c.configure(fg_color="#252535"))
        card.bind("<Leave>", lambda e, c=card: c.configure(fg_color="#1a1a26"))

        # Sol: bilgi
        left = ctk.CTkFrame(card, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=15, pady=12)
        ctk.CTkLabel(
            left, text=cand.title,
            font=("Segoe UI", 13, "bold"),
            text_color="#f1f5f9",
            anchor="w",
        ).pack(anchor="w", fill="x")
        meta = f"id: {cand.slug}"
        if cand.matched_via and cand.matched_via.lower() != query.lower():
            meta += f"  •  alias: {cand.matched_via}"
        ctk.CTkLabel(
            left, text=meta,
            font=("Segoe UI", 10),
            text_color="#64748b",
            anchor="w",
        ).pack(anchor="w", fill="x")

        # Orta: skor rozeti
        score_pct = int(round(cand.score * 100))
        badge = ctk.CTkLabel(
            card,
            text=f"%{score_pct}",
            font=("Segoe UI", 13, "bold"),
            text_color="white",
            fg_color=_score_color(cand.score),
            corner_radius=6,
            width=55, height=28,
        )
        badge.pack(side="left", padx=10)

        # Sağ: Seç butonu
        ctk.CTkButton(
            card, text="Seç", width=80, height=32,
            fg_color="#6366f1", hover_color="#4f46e5",
            command=lambda c=cand: _pick(c),
        ).pack(side="right", padx=15)

    # Alt bar: iptal
    bottom = ctk.CTkFrame(win, fg_color="#0f0f17", height=60)
    bottom.pack(fill="x", side="bottom")
    bottom.pack_propagate(False)
    ctk.CTkButton(
        bottom, text="İptal", width=120, height=36,
        fg_color="#374151", hover_color="#4b5563",
        command=win.destroy,
    ).pack(side="right", padx=20, pady=12)

    win.protocol("WM_DELETE_WINDOW", win.destroy)
    if standalone:
        win.mainloop()
    else:
        win.wait_window()

    return selected[0]


def ask_match_cli(
    query: str,
    candidates: List[MatchResult],
    source_name: str = "Kaynak",
) -> Optional[MatchResult]:
    """CLI varyantı — questionary varsa onu, yoksa düz input kullanır."""
    if not candidates:
        return None
    try:
        import questionary as qa
    except ImportError:
        qa = None

    print(f"\n⚠ '{query}' için {source_name}'da tam eşleşme yok.")
    print(f"  En yakın {len(candidates)} aday:\n")

    if qa is not None:
        choices = [
            qa.Choice(f"%{int(c.score*100):2d}  │  {c.title}", c)
            for c in candidates
        ] + [qa.Choice("İptal — bu kaynakta eşleşme yok", None)]
        return qa.select("Hangisi sizin aradığınız?", choices=choices).ask()

    # questionary yoksa, düz prompt
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}] %{int(c.score*100):2d}  {c.title}")
    print("  [0] İptal")
    while True:
        try:
            choice = input("Seçim: ").strip()
            if choice == "0" or not choice:
                return None
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]
        except (ValueError, EOFError, KeyboardInterrupt):
            return None


__all__ = ["ask_match", "ask_match_cli"]
