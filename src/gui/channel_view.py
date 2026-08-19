"""Live grid of all 512 DMX channel levels (32 columns x 16 rows).

Each cell carries its DMX address in the top-right corner and its current
value in the bottom-left, so a channel can be read without hovering it.

A level on screen is not proof that something is sending it. Two cases have to
look different from live data or they read as bugs:

  * held  — Art-Net stopped, the buffer keeps its last frame on purpose
  * stale — the current source sends a short frame, so channels above its
            length keep whatever the previous console left there

Both draw muted, so the grid says which levels are live and which are history.
"""

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QToolTip, QWidget

from src.gui.styles import COLORS

GRID_COLS = 32
GRID_ROWS = 16
CELL_GAP = 2          # wider than a hairline so cells read as separate blocks
TEXT_PAD = 2
# Below this a cell cannot hold two legible labels, so it draws bars only.
MIN_TEXT_W = 26
MIN_TEXT_H = 18
# Ink for text sitting on a pale bar, and the lightness above which a bar needs
# it. Every bar in the palette clears this — including the muted stale bar,
# which is a mid-tone that dark ink reads on far better than light ink.
DARK_INK = "#0e1116"
PALE_BAR = 90

# Addresses never change — build the strings once instead of 512 times a paint.
ADDRESS_LABELS = [str(i + 1) for i in range(512)]


class ChannelView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels = bytes(512)
        self._live_len = 512
        self._holding = False
        self.setMinimumSize(960, 420)
        self.setMouseTracking(True)
        self._bg = QColor(COLORS["panel"])
        self._cell_bg = QColor(COLORS["cell"])
        self._bar = QColor(COLORS["accent"])
        self._bar_hot = QColor("#7cc1ff")
        self._bar_stale = QColor(COLORS["stale"])
        self._addr_pen = QColor(COLORS["text_mid"])
        self._value_pen = QColor(COLORS["text"])
        self._caption = QColor(COLORS["text_dim"])
        dark_ink = QColor(DARK_INK)
        self._ink_on_bar = {
            bar.rgb(): dark_ink if bar.lightness() > PALE_BAR else self._value_pen
            for bar in (self._bar, self._bar_hot, self._bar_stale)
        }

    def set_channels(self, channels, live_len=512, holding=False):
        if (channels == self._channels and live_len == self._live_len
                and holding == self._holding):
            return
        self._channels = channels
        self._live_len = live_len
        self._holding = holding
        self.update()

    def _is_stale(self, index):
        """True when nothing is currently transmitting this channel."""
        return self._holding or index >= self._live_len

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bg)
        w = self.width() / GRID_COLS
        h = self.height() / GRID_ROWS
        cw = round(w) - CELL_GAP
        ch = round(h) - CELL_GAP
        labelled = cw >= MIN_TEXT_W and ch >= MIN_TEXT_H
        if labelled:
            font = painter.font()
            font.setPixelSize(max(7, min(11, int(ch * 0.34))))
            painter.setFont(font)
        # Glyph boxes run taller than the pixel size, and underestimating
        # here leaves a label in dim ink on top of a pale bar.
        text_h = painter.fontMetrics().height() if labelled else 0
        for i, value in enumerate(self._channels):
            x = round((i % GRID_COLS) * w)
            y = round((i // GRID_COLS) * h)
            painter.fillRect(x, y, cw, ch, self._cell_bg)
            bar_h = 0
            on_bar = self._value_pen
            if value:
                bar_h = max(1, round(ch * value / 255))
                if self._is_stale(i):
                    color = self._bar_stale
                else:
                    color = self._bar_hot if value > 200 else self._bar
                painter.fillRect(x, y + ch - bar_h, cw, bar_h, color)
                on_bar = self._ink_on_bar[color.rgb()]
            if not labelled:
                continue
            cell = QRect(x + TEXT_PAD, y, cw - 2 * TEXT_PAD, ch)
            # A label is only legible if its ink suits whatever is behind it,
            # and the bar height decides that corner by corner.
            painter.setPen(on_bar if bar_h >= ch - text_h else self._addr_pen)
            painter.drawText(cell, Qt.AlignTop | Qt.AlignRight, ADDRESS_LABELS[i])
            if not value:
                painter.setPen(self._addr_pen)   # a resting channel recedes
            else:
                painter.setPen(on_bar if bar_h >= text_h else self._value_pen)
            painter.drawText(cell, Qt.AlignBottom | Qt.AlignLeft, str(value))
        if self._holding:
            painter.setPen(self._caption)
            painter.drawText(self.rect().adjusted(0, 4, -6, 0),
                             Qt.AlignTop | Qt.AlignRight, "HOLDING LAST FRAME")
        painter.end()

    def mouseMoveEvent(self, event):
        col = int(event.position().x() / (self.width() / GRID_COLS))
        row = int(event.position().y() / (self.height() / GRID_ROWS))
        idx = row * GRID_COLS + col
        if 0 <= idx < 512:
            if self._holding:
                note = " — held, nothing is sending"
            elif idx >= self._live_len:
                note = " — not in the current frame"
            else:
                note = ""
            QToolTip.showText(
                event.globalPosition().toPoint() + QPoint(12, 12),
                f"Ch {idx + 1}: {self._channels[idx]}{note}", self)
        super().mouseMoveEvent(event)
