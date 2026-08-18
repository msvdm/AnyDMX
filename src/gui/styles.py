"""Color palette and application stylesheet."""

COLORS = {
    "bg": "#15171c",
    "panel": "#1e2128",
    "border": "#2c313c",
    "text": "#d6dae3",
    "text_dim": "#7d8493",
    "accent": "#3d9bff",
    "ok": "#39c26d",
    "err": "#e5534b",
    "warn": "#d4a72c",
}

APP_QSS = f"""
QWidget {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
    font-size: 13px;
}}
QFrame#panel {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
}}
QLabel#dim {{
    color: {COLORS['text_dim']};
}}
QComboBox, QSpinBox {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 22px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['accent']};
}}
QPushButton {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    padding: 6px 16px;
    min-height: 22px;
}}
QPushButton:hover {{
    border-color: {COLORS['accent']};
}}
QPushButton#start {{
    background-color: {COLORS['accent']};
    color: #ffffff;
    font-weight: bold;
}}
QPushButton#start:checked {{
    background-color: {COLORS['err']};
}}
QToolTip {{
    background-color: {COLORS['panel']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
}}
"""
