"""Live grid of all 512 DMX channel levels (32 columns x 16 rows)."""

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
        self.setMinimumSize(480, 240)
        self.setMouseTracking(True)
        self._bg = QColor(COLORS["panel"])
        self._cell_bg = QColor(COLORS["bg"])
        self._bar = QColor(COLORS["accent"])
        self._bar_hot = QColor("#7cc1ff")

    def set_channels(self, channels):
        if channels != self._channels:
            self._channels = channels
            self.update()

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
                color = self._bar_hot if value > 200 else self._bar
                painter.fillRect(x, y + ch - bar_h, cw, bar_h, color)
        painter.end()

    def mouseMoveEvent(self, event):
        col = int(event.position().x() / (self.width() / GRID_COLS))
        row = int(event.position().y() / (self.height() / GRID_ROWS))
        idx = row * GRID_COLS + col
        if 0 <= idx < 512:
            QToolTip.showText(
                event.globalPosition().toPoint() + QPoint(12, 12),
                f"Ch {idx + 1}: {self._channels[idx]}", self)
        super().mouseMoveEvent(event)
