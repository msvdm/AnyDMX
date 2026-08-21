"""Color palette and application stylesheet.

Two accents carry the whole layout: blue is everything coming *in*, orange is
everything going *out*. Every panel, label and button picks its color from
that one rule, so the window can be read at a glance without labels.
"""

COLORS = {
    "bg": "#14161d",
    "panel": "#1c2029",
    "titlebar": "#0e1015",  # darker than the window, like every CSD bar
    "panel_hi": "#242936",
    "border": "#2f3542",
    "text": "#e3e7f0",
    "text_dim": "#838c9e",
    "text_mid": "#c3cad8",  # labels on the grid: legible on the lighter cells
    "accent": "#3d9bff",    # INPUT — Art-Net side
    "accent_dim": "#1d4a80",
    "output": "#ff9040",    # OUTPUT — DMX side
    "output_dim": "#8a4c1c",
    "ok": "#3ddc84",
    "err": "#ff5f57",
    "warn": "#ffc247",
    "cell": "#2e3442",   # grid cell ground, lighter than the panel
    "stale": "#5b6478",  # levels nothing is transmitting any more
}

APP_QSS = f"""
QMainWindow, QDialog, QWidget#central, QWidget#body, QWidget#dialogBody {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
}}
QWidget {{
    font-size: 13px;
}}
QLabel {{
    background: transparent;
    color: {COLORS['text']};
}}
QLabel#dim {{
    color: {COLORS['text_dim']};
}}
QLabel#title {{
    font-size: 19px;
    font-weight: bold;
}}
QLabel#caption {{
    color: {COLORS['text_dim']};
    font-size: 11px;
}}
QLabel#sectionIn, QLabel#sectionOut {{
    font-size: 12px;
    font-weight: bold;
}}
QLabel#flow {{
    color: {COLORS['text_dim']};
    font-size: 17px;
}}
QLabel#sectionIn {{ color: {COLORS['accent']}; }}
QLabel#sectionOut {{ color: {COLORS['output']}; }}

QFrame#titlebar {{
    background-color: {COLORS['titlebar']};
    border: none;
}}
QFrame#titlerule {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {COLORS['accent']}, stop:1 {COLORS['output']});
    border: none;
}}
QLabel#windowtitle {{
    color: {COLORS['text']};
    font-size: 13px;
    font-weight: bold;
}}
QLabel#mark {{
    font-size: 14px;
    font-weight: bold;
}}
QPushButton#winbtn, QPushButton#winclose {{
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    min-height: 0;
    color: {COLORS['text_dim']};
    font-size: 14px;
}}
QPushButton#winbtn:hover {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text']};
}}
QPushButton#winclose:hover {{
    background-color: {COLORS['err']};
    color: #ffffff;
}}

QFrame#panel {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
}}
QFrame#panelIn {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-top: 3px solid {COLORS['accent']};
    border-radius: 8px;
}}
QFrame#panelOut {{
    background-color: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-top: 3px solid {COLORS['output']};
    border-radius: 8px;
}}

QComboBox, QSpinBox, QLineEdit {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    padding: 5px 8px;
    min-height: 20px;
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{
    border-color: {COLORS['accent_dim']};
}}
/* Not cosmetic. The interface editor greys out the address fields when an
   adapter is set to DHCP — without this rule a disabled field is
   pixel-identical to an editable one, and the whole affordance disappears. */
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{
    background-color: {COLORS['bg']};
    color: {COLORS['text_dim']};
    border-color: {COLORS['border']};
}}
/* The drop-down and spin buttons are left to the platform style: a stylesheet
   that takes them over has to supply the arrow images too, and a hand-rolled
   arrow renders as a solid block on some styles. */
QComboBox QAbstractItemView {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['accent']};
    selection-color: #ffffff;
}}

QPushButton {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    padding: 6px 14px;
    min-height: 20px;
}}
QPushButton:hover {{
    border-color: {COLORS['accent']};
}}
QPushButton:disabled {{
    color: {COLORS['text_dim']};
    border-color: {COLORS['border']};
}}
QPushButton#primary {{
    background-color: {COLORS['accent']};
    color: #ffffff;
    font-weight: bold;
    border: 1px solid {COLORS['accent']};
}}
QPushButton#primary:hover {{
    background-color: #58aaff;
}}
QPushButton#primary:disabled {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text_dim']};
    border-color: {COLORS['border']};
}}
QPushButton#danger {{
    color: {COLORS['err']};
    border-color: {COLORS['border']};
}}
QPushButton#danger:hover {{
    background-color: {COLORS['err']};
    color: #ffffff;
    border-color: {COLORS['err']};
}}
QPushButton#drawer {{
    background-color: transparent;
    color: {COLORS['output']};
    border-color: {COLORS['output_dim']};
    font-size: 12px;
}}
QPushButton#drawer:hover {{
    background-color: {COLORS['output']};
    color: #12141a;
    border-color: {COLORS['output']};
}}
QPushButton#refresh {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0;
    min-height: 0;
    color: {COLORS['text_dim']};
    font-size: 15px;
}}
QPushButton#refresh:hover {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['accent']};
}}
QPushButton#uni {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text_dim']};
    text-align: left;
    padding: 4px 10px;
    font-size: 12px;
}}
/* In the compact column a chip has about 150 px to say universe, source
   marker and rate in — a size down buys the last few. */
QPushButton#uni[compact="true"] {{
    font-size: 11px;
    padding: 4px 7px;
}}
QPushButton#uni:checked {{
    background-color: {COLORS['accent_dim']};
    color: #ffffff;
    border-color: {COLORS['accent']};
}}

/* One adapter in the interface dialog: the universe chip, full width. The
   selected row reads the same as a selected universe chip on purpose — it is
   the same gesture. */
QPushButton#nicRow {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text_mid']};
    text-align: left;
    padding: 5px 10px;
    font-size: 12px;
}}
QPushButton#nicRow:checked {{
    background-color: {COLORS['accent_dim']};
    color: #ffffff;
    border-color: {COLORS['accent']};
}}

QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border']};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QToolTip {{
    background-color: {COLORS['panel_hi']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['accent_dim']};
    padding: 4px;
}}
"""
