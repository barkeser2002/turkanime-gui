"""Qt tema — eski CustomTkinter koyu paletinin QSS karşılığı.

Renkler mevcut GUI'de sabit kodlanmış değerlerden alındı, böylece Qt'ye geçişte
görsel kimlik korunur.
"""
from __future__ import annotations

# ── Palet ───────────────────────────────────────────────────────────────────
BG = "#0f0f0f"          # ana arka plan
BG_ELEV = "#1a1a1a"     # yükseltilmiş yüzey (kart, panel)
BG_ELEV_2 = "#242424"   # hover / girdi alanı
BORDER = "#2f2f2f"
TEXT = "#f5f5f5"
TEXT_MUTED = "#9a9a9a"
ACCENT = "#00b894"      # birincil vurgu (yeşil)
ACCENT_HOVER = "#00d1a7"
DANGER = "#d63031"
DANGER_HOVER = "#e84118"

RADIUS = 8


def score_color(score: float) -> str:
    """0..1 skoru için yeşil→amber→kırmızı (eski simple/theme.py ile aynı anlam)."""
    if score >= 0.75:
        return ACCENT
    if score >= 0.45:
        return "#fbc531"
    return DANGER


QSS = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{ background-color: {BG}; }}

/* ── Yüzeyler ───────────────────────────────────────────────────────────── */
QFrame#Card, QFrame#Panel {{
    background-color: {BG_ELEV};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
QFrame#Card:hover {{ border-color: {ACCENT}; }}

/* Poster üstündeki köşe rozeti: metin rengini sayfalar kendi belirler
   (puan rengi / izleme durumu), buradan yalnızca hap zemini gelir. İç
   QLabel'ın zemini şeffaf olmalı, yoksa genel `QWidget` kuralının koyu
   dikdörtgeni hapın içinde görünür. */
QFrame#CardBadge {{
    background-color: rgba(8, 8, 8, 200);
    border: none;
    border-radius: 5px;
}}
QFrame#CardBadge QLabel {{ background: transparent; }}
QFrame#CardBadgeFlat, QFrame#CardBadgeFlat QLabel {{
    background: transparent;
    border: none;
}}

QFrame#Header {{
    background-color: {BG_ELEV};
    border: none;
    border-bottom: 1px solid {BORDER};
}}

QFrame#Sidebar {{
    background-color: {BG_ELEV};
    border: none;
    border-right: 1px solid {BORDER};
}}

/* ── Yazı ───────────────────────────────────────────────────────────────── */
QLabel#Title {{ font-size: 20px; font-weight: 600; }}
QLabel#Subtitle {{ font-size: 15px; font-weight: 600; }}
QLabel#Muted {{ color: {TEXT_MUTED}; }}

/* ── Butonlar ───────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {BG_ELEV_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background-color: {BORDER}; }}
QPushButton:pressed {{ background-color: {BG_ELEV}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; background-color: {BG_ELEV}; }}

QPushButton#Primary {{
    background-color: {ACCENT};
    border: none;
    color: #06231d;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background-color: {ACCENT_HOVER}; }}

QPushButton#Danger {{ background-color: {DANGER}; border: none; color: white; }}
QPushButton#Danger:hover {{ background-color: {DANGER_HOVER}; }}

QPushButton#Nav {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS}px;
    padding: 9px 14px;
    text-align: left;
}}
QPushButton#Nav:hover {{ background-color: {BG_ELEV_2}; }}
QPushButton#Nav:checked {{ background-color: {BG_ELEV_2}; color: {ACCENT}; font-weight: 600; }}

/* ── Girdiler ───────────────────────────────────────────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    background-color: {BG_ELEV_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: #06231d;
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {BG_ELEV_2};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: #06231d;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background-color: {BG_ELEV_2};
}}
QCheckBox::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}

/* ── İlerleme ───────────────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {BG_ELEV_2};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 6px; }}

/* ── Kaydırma ───────────────────────────────────────────────────────────── */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}

/* ── Sekme / ayraç ──────────────────────────────────────────────────────── */
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}
QTabBar::tab {{
    background: transparent; padding: 8px 14px; border: none;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}

QToolTip {{
    background-color: {BG_ELEV_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}
"""


def apply_theme(app) -> None:
    """QApplication'a koyu temayı uygula."""
    app.setStyleSheet(QSS)


__all__ = ["QSS", "apply_theme", "score_color", "BG", "BG_ELEV", "ACCENT", "DANGER", "TEXT_MUTED"]
