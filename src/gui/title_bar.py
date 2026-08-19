"""The window's own title bar, drawn in the app's colors.

A window manager's title bar sits outside the widget tree, so no stylesheet can
reach it — the only way to a bar that matches the window is to switch the
decorations off and draw one here. That hands this widget the jobs the window
manager used to do: dragging, double-click to maximise, the top-edge resize and
the minimise/maximise/close buttons.

Move and resize are handed straight back to the platform through
`startSystemMove()` / `startSystemResize()` rather than reimplemented with
mouse arithmetic, so snapping, tiling and multi-monitor behaviour stay native
on both X11 and Windows.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from src.gui.styles import COLORS

BAR_HEIGHT = 34
RULE_HEIGHT = 2
RESIZE_MARGIN = 6     # top strip of the bar that resizes instead of moving
BUTTON_W = 34

MAXIMISE = "☐"   # ballot box: an empty window outline
RESTORE = "❐"    # overlapping squares: back to the smaller window


class TitleBar(QFrame):
    """Bar with the app mark on the left, a centred title, buttons on the right.

    `actions` are app buttons (the rescan) placed with the mark, deliberately
    far from the close button.
    """

    def __init__(self, window, title, actions=()):
        super().__init__(window)
        self._window = window
        self.setObjectName("titlebar")
        self.setFixedHeight(BAR_HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 0, 0)
        row.setSpacing(6)

        left = QWidget()
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(6)
        mark = QLabel(f"Any<span style='color:{COLORS['output']}'>DMX</span>")
        mark.setObjectName("mark")
        left_row.addWidget(mark)
        for button in actions:
            left_row.addWidget(button)
        left_row.addStretch()
        row.addWidget(left)

        caption = QLabel(title)
        caption.setObjectName("windowtitle")
        caption.setAlignment(Qt.AlignCenter)
        row.addWidget(caption, 1)

        right = QWidget()
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.setSpacing(0)
        right_row.addStretch()
        self._max_btn = None
        for glyph, name, slot, tip in (
                ("−", "winbtn", window.showMinimized, "Minimise"),
                (MAXIMISE, "winbtn", self._toggle_max, "Maximise"),
                ("✕", "winclose", window.close, "Close")):
            button = QPushButton(glyph)
            button.setObjectName(name)
            button.setFixedSize(BUTTON_W, BAR_HEIGHT)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(slot)
            right_row.addWidget(button)
            if glyph == MAXIMISE:
                self._max_btn = button
        row.addWidget(right)

        # The title is only truly centred in the window if what flanks it is
        # the same width on both sides.
        flank = max(left.sizeHint().width(), right.sizeHint().width())
        left.setFixedWidth(flank)
        right.setFixedWidth(flank)

    # ------------------------------------------------------------ actions

    def _toggle_max(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync()

    def sync(self):
        """Match the glyph to the window state, however the state changed."""
        maximised = self._window.isMaximized()
        self._max_btn.setText(RESTORE if maximised else MAXIMISE)
        self._max_btn.setToolTip("Restore" if maximised else "Maximise")

    # ------------------------------------------------------------- mouse

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        handle = self._window.windowHandle()
        if handle is None:
            return
        # The bar is flush with the top edge, so its first few pixels have to
        # keep working as the window's resize border.
        if event.position().y() <= RESIZE_MARGIN and not self._window.isMaximized():
            handle.startSystemResize(Qt.TopEdge)
        else:
            handle.startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_max()


class TitleRule(QFrame):
    """The blue-to-orange hairline under the bar: input on the left, output on
    the right, the same story the two panels tell."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titlerule")
        self.setFixedHeight(RULE_HEIGHT)
