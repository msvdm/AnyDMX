"""A window without decorations, owning everything the decorations used to do.

Switching the window manager's title bar off (it sits outside the widget tree,
so no stylesheet can reach it) hands the application four jobs it never used to
have: moving, resizing, keeping the window on screen, and reporting its own
maximised state. All four live here rather than in the window's contents.

Move and resize are handed straight back to the platform through
`startSystemMove()` / `startSystemResize()` rather than reimplemented with
mouse arithmetic, so snapping, tiling and multi-monitor behaviour stay native
on both X11 and Windows.

The resize border is the strip of margin around the body widget, caught in
`eventFilter()`. The top edge is the one exception: the title bar covers it, so
the bar calls `begin_resize(Qt.TopEdge)` from its own press handler. Both paths
end up in the same two methods here, so there is one rule about when a window
may be resized, not two that can drift apart.

A window bigger than the desktop costs both of the things that make one usable:
window managers shove an oversized window around while the user tries to place
it, and some drop the maximise button from a window that cannot fit the work
area. The desktop is also routinely smaller than the monitor — a 4K screen at
300% scaling reports 1280x680 of usable space — so every programmatic resize
goes through `fit_on_screen()`.
"""

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QMainWindow

RESIZE_MARGIN = 6     # grabbable strip along each edge


class FramelessWindow(QMainWindow):
    """QMainWindow with no decorations. Subclasses supply the contents.

    A subclass builds its widgets, then calls `set_resize_body()` with the
    widget whose margin strip should act as the resize border, and sets
    `title_bar` to the bar that should be told about state changes.
    """

    def __init__(self):
        super().__init__()
        # Both set before setWindowFlags(): that call raises a
        # WindowStateChange straight back into changeEvent() below, while the
        # subclass has not built anything yet.
        self.title_bar = None
        self._resize_body = None
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)

    def set_resize_body(self, body):
        body.setMouseTracking(True)
        body.installEventFilter(self)
        self._resize_body = body

    # ------------------------------------------------------------- state

    def is_fixed(self):
        """True when the window's size is not the user's to drag."""
        return self.isMaximized() or self.isFullScreen()

    def begin_move(self):
        handle = self.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def begin_resize(self, edges):
        """Hand a drag to the platform. False when this window cannot resize."""
        if not edges or self.is_fixed():
            return False
        handle = self.windowHandle()
        if handle is None:
            return False
        handle.startSystemResize(edges)
        return True

    def changeEvent(self, event):
        """Keep the maximise glyph honest when the state changes elsewhere."""
        super().changeEvent(event)
        # setWindowFlags() in the constructor can raise this before there is a
        # bar to sync.
        if self.title_bar is not None and event.type() == QEvent.WindowStateChange:
            self.title_bar.sync()

    # ---------------------------------------------------------- geometry

    def max_client_size(self):
        """Largest window that still fits the work area, or None.

        The window is frameless, so what it asks for is all there is — there is
        no title bar or border outside it to leave room for.
        """
        screen = self.screen()
        if screen is None:
            return None
        avail = screen.availableGeometry()
        return (avail.width(), avail.height())

    def fit_on_screen(self, width, height):
        """Resize to at most what the desktop can show."""
        limit = self.max_client_size()
        if limit:
            width, height = min(width, limit[0]), min(height, limit[1])
        self.resize(width, height)
        # The size only settles once the layout has been through an event
        # loop pass, and only then is it worth checking where it landed.
        QTimer.singleShot(0, self._settle_on_screen)

    def _settle_on_screen(self):
        """Re-clamp once the layout has settled, then pull the window into view."""
        if self.is_fixed():
            return
        screen = self.screen()
        limit = self.max_client_size()
        if screen is None or limit is None:
            return
        width, height = min(self.width(), limit[0]), min(self.height(), limit[1])
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)
        avail = screen.availableGeometry()
        pos = self.frameGeometry().topLeft()
        x = min(max(pos.x(), avail.x()),
                max(avail.x(), avail.right() + 1 - width))
        y = min(max(pos.y(), avail.y()),
                max(avail.y(), avail.bottom() + 1 - height))
        if (x, y) != (pos.x(), pos.y()):
            self.move(x, y)

    # ------------------------------------------------------ resize border

    def edges_at(self, pos):
        """Which window edges a point in the body is close enough to grab."""
        if self.is_fixed() or self._resize_body is None:
            return Qt.Edges()
        edges = Qt.Edges()
        if pos.x() <= RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        elif pos.x() >= self._resize_body.width() - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        # The body starts under the title bar, which handles the top edge.
        if pos.y() >= self._resize_body.height() - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def edge_cursor(edges):
        sideways = bool(edges & (Qt.LeftEdge | Qt.RightEdge))
        upright = bool(edges & (Qt.TopEdge | Qt.BottomEdge))
        if sideways and upright:
            falling = bool(edges & Qt.LeftEdge) == bool(edges & Qt.TopEdge)
            return Qt.SizeFDiagCursor if falling else Qt.SizeBDiagCursor
        if sideways:
            return Qt.SizeHorCursor
        if upright:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def eventFilter(self, obj, event):
        """The resize border: the margin strip around the body.

        Nothing else is over those few pixels, so the body sees the events and
        hands them to the platform's own resize loop.
        """
        if obj is self._resize_body:
            kind = event.type()
            if kind == QEvent.MouseMove and not event.buttons():
                self._resize_body.setCursor(
                    self.edge_cursor(self.edges_at(event.position())))
            elif kind == QEvent.Leave:
                self._resize_body.unsetCursor()
            elif (kind == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton):
                if self.begin_resize(self.edges_at(event.position())):
                    return True
        return super().eventFilter(obj, event)
