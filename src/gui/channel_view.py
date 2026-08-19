"""Live grid of all 512 DMX channel levels (32 columns x 16 rows).

A level on screen is not proof that something is sending it. Two cases have to
look different from live data or they read as bugs:

  * held  — Art-Net stopped, the buffer keeps its last frame on purpose
  * stale — the current source sends a short frame, so channels above its
            length keep whatever the previous console left there

Both draw muted, so the grid says which levels are live and which are history.
"""

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QToolTip, QWidget

from src.gui.styles import COLORS

GRID_COLS = 32
GRID_ROWS = 16


class ChannelView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels = bytes(512)
        self._live_len = 512
        self._holding = False
        self.setMinimumSize(480, 240)
        self.setMouseTracking(True)
        self._bg = QColor(COLORS["panel"])
        self._cell_bg = QColor(COLORS["bg"])
        self._bar = QColor(COLORS["accent"])
        self._bar_hot = QColor("#7cc1ff")
        self._bar_stale = QColor(COLORS["stale"])
        self._caption = QColor(COLORS["text_dim"])

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
        gap = 1
        for i, value in enumerate(self._channels):
            col = i % GRID_COLS
            row = i // GRID_COLS
            x = round(col * w)
            y = round(row * h)
            cw = round(w) - gap
            ch = round(h) - gap
            painter.fillRect(x, y, cw, ch, self._cell_bg)
            if value:
                bar_h = max(1, round(ch * value / 255))
                if self._is_stale(i):
                    color = self._bar_stale
                else:
                    color = self._bar_hot if value > 200 else self._bar
                painter.fillRect(x, y + ch - bar_h, cw, bar_h, color)
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
