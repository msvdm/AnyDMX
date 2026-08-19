"""Main window: input on the left, output on the right, levels in the drawer.

The layout is the mental model. Everything Art-Net comes in through the blue
panel on the left; everything DMX leaves through the orange panel on the right;
the one dynamic sentence at the bottom says what is actually happening right
now. It opens at the smallest size the compact layout can be drawn at — the
shape you leave running next to a console — and each drawer state remembers
the size it was last left at.

There is no Start button: the bridge runs from the moment the window opens and
re-arms itself whenever a selection changes. A bridge that has to be started is
a bridge someone forgets to start.
"""

import time

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from src.core import vnet
from src.core.artnet_receiver import LOOPBACK, list_local_ipv4
from src.core.engine import ARTNET_ACTIVE_TIMEOUT, Engine
from src.core.ports import list_serial_ports
from src.gui.channel_view import ChannelView
from src.gui.styles import COLORS
from src.gui.title_bar import RESIZE_MARGIN, TitleBar, TitleRule
from src.gui.vnet_dialog import InterfaceDialog
from src.utils.logger import get_logger
from src.utils.settings import load_settings, save_settings

log = get_logger(__name__)

POLL_MS = 100        # GUI refresh interval
# Startup size: the compact layout's own floor, so the window opens as small as
# it can be drawn. Qt raises either figure to the layout minimum if a platform's
# fonts need more, which is exactly the intent.
COMPACT_W = 490
COMPACT_H = 390      # the old 352 plus the title bar the window now draws itself
EXPANDED_W = 1520    # the grid wants width far more than it wants height
EXPANDED_H = 860


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AnyDMX — Art-Net to DMX bridge")
        # No window-manager decorations: the bar is ours, drawn by TitleBar in
        # the app's own colors. Everything the decorations used to provide —
        # move, resize, minimise, maximise, close — is reimplemented, so read
        # TitleBar and _edges_at() together before changing either.
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.engine = Engine()
        self.settings = load_settings()
        self._uni_buttons = {}
        self._discovered_keys = ()
        self._last_uni_packets = {}
        self._last_uni_time = time.monotonic()
        # Each drawer state remembers the size it was last left at, so the
        # arrow toggles between two windows the user has already sized. Session
        # only: a restart opens at the compact default again.
        self._compact_size = None
        self._expanded_size = None
        self._start_error = ""
        self._led_state = {}
        self._status_level = None
        # Repopulating a combo fires currentIndexChanged for every item it
        # drops. Without this the engine would restart several times per rescan.
        self._loading = True
        self._build_ui()
        self._refresh_ports()
        self._restore_settings()
        self._loading = False
        # Deferred so the window paints before the first bind and before the
        # PowerShell round trip that checks the lighting interface.
        QTimer.singleShot(0, self._apply_engine)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_MS)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        shell = QVBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        refresh_btn = QPushButton("⟳")
        refresh_btn.setObjectName("refresh")
        refresh_btn.setFixedSize(28, 24)
        refresh_btn.setToolTip(
            "Rescan COM ports and network interfaces, and re-arm the bridge")
        refresh_btn.clicked.connect(self._rescan)
        self.title_bar = TitleBar(self, "DMX bridge", actions=[refresh_btn])
        shell.addWidget(self.title_bar)
        shell.addWidget(TitleRule())

        # Everything below the bar. The strip of margin around it is the
        # window's resize border now, so it must stay at least RESIZE_MARGIN.
        self._body = QWidget()
        self._body.setObjectName("body")
        self._body.setMouseTracking(True)
        self._body.installEventFilter(self)
        shell.addWidget(self._body, 1)

        self._root = QVBoxLayout(self._body)
        self._root.setContentsMargins(10, 8, 10, 8)
        self._root.setSpacing(6)

        self.columns = QWidget()
        columns = QHBoxLayout(self.columns)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(10)
        columns.addWidget(self._build_input_panel(), 1)
        flow = QLabel("→")
        flow.setObjectName("flow")
        flow.setToolTip("Everything crosses the engine's 512-channel buffer")
        columns.addWidget(flow, 0, Qt.AlignVCenter)
        columns.addWidget(self._build_output_panel(), 1)
        self._root.addWidget(self.columns)

        # Channel grid — collapsible, since it is by far the tallest thing
        # in the window and most of the time nobody is reading levels.
        self.channel_view = ChannelView()
        self._root.addWidget(self.channel_view, 1)

        self._root.addWidget(self._build_status_bar())
        self._rebuild_discovered(())
        self.setCentralWidget(central)
        self._fit_on_screen(COMPACT_W, COMPACT_H)

    def _build_input_panel(self):
        frame = QFrame()
        frame.setObjectName("panelIn")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 8, 14, 8)
        box.setSpacing(4)

        heading = QLabel("INPUT  ·  ART-NET")
        heading.setObjectName("sectionIn")
        box.addWidget(heading)

        box.addWidget(self._field_label("Input Port"))
        self.nic_combo = QComboBox()
        self.nic_combo.setToolTip(
            "Interface to listen on and advertise as an Art-Net node.\n"
            "All interfaces works for most setups; pick a specific IP on\n"
            "multi-NIC machines (e.g. a dedicated lighting network).")
        self.nic_combo.currentIndexChanged.connect(self._selection_changed)
        box.addWidget(self.nic_combo)
        box.addSpacing(2)

        self.vnet_btn = QPushButton("Create Interface")
        self.vnet_btn.setObjectName("primary")
        self.vnet_btn.setToolTip(
            "Add a virtual 2.x.x.x adapter for consoles that pick their own\n"
            "Art-Net interface (dot2). Needs administrator rights.")
        self.vnet_btn.clicked.connect(self._open_interface_dialog)
        box.addWidget(self.vnet_btn)
        box.addSpacing(4)

        self.artnet_led, self.artnet_state = self._indicator(box)
        box.addSpacing(2)

        # The universes live here only while the window is compact. With the
        # drawer open the whole column moves to the wide strip at the bottom —
        # every pixel it gives back is a pixel the channel grid needs to stay
        # tall enough to label its cells.
        self.uni_column = QWidget()
        column = QVBoxLayout(self.uni_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        caption = QLabel("Universes seen on this PC")
        caption.setObjectName("caption")
        column.addWidget(caption)

        # Scrolled, so a console announcing a dozen universes cannot stretch
        # the window past the compact size.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # No minimum: it scrolls, so it is the one thing here that can give way
        # when the window is squeezed onto a small screen.
        scroll.setMinimumHeight(0)
        holder = QWidget()
        self.discovered_col = QVBoxLayout(holder)
        self.discovered_col.setContentsMargins(0, 0, 4, 0)
        self.discovered_col.setSpacing(4)
        scroll.setWidget(holder)
        column.addWidget(scroll, 1)
        box.addWidget(self.uni_column, 1)
        return frame

    def _build_output_panel(self):
        frame = QFrame()
        frame.setObjectName("panelOut")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 8, 14, 8)
        box.setSpacing(4)

        heading = QLabel("OUTPUT  ·  DMX512")
        heading.setObjectName("sectionOut")
        box.addWidget(heading)

        box.addWidget(self._field_label("Output Device"))
        self.port_combo = QComboBox()
        self.port_combo.setToolTip(
            "USB-RS485 dongle to stream DMX out of.\n"
            "Leave on monitor mode to watch Art-Net without any hardware.")
        self.port_combo.currentIndexChanged.connect(self._selection_changed)
        box.addWidget(self.port_combo)
        box.addSpacing(2)

        box.addWidget(self._field_label("Universe"))
        self.universe_spin = QSpinBox()
        self.universe_spin.setRange(0, 32767)
        self.universe_spin.setToolTip(
            "Art-Net port address (0-based) captured and sent to this device.\n"
            "Universe 0 = Net 0, Subnet 0, Universe 0 — the default on most "
            "consoles.")
        self.universe_spin.valueChanged.connect(self._universe_changed)
        box.addWidget(self.universe_spin)
        box.addSpacing(4)

        self.dmx_led, self.dmx_state = self._indicator(box)
        box.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.channels_toggle = QPushButton()
        self.channels_toggle.setObjectName("drawer")
        self.channels_toggle.setCheckable(True)
        self.channels_toggle.setChecked(True)
        self.channels_toggle.clicked.connect(self._toggle_channels)
        buttons.addWidget(self.channels_toggle, 1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("danger")
        self.clear_btn.setToolTip("Zero the universe buffer — sends all-zero DMX")
        self.clear_btn.clicked.connect(self._clear_buffer)
        buttons.addWidget(self.clear_btn)
        box.addLayout(buttons)
        self._sync_channels_toggle()
        return frame

    def _build_status_bar(self):
        frame = QFrame()
        frame.setObjectName("panel")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(10)
        self.status_label = QLabel("Starting…")
        self.status_label.setWordWrap(True)
        # Ignored width: the sentence changes constantly and must never be
        # allowed to push the window wider than the size the user chose.
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setMinimumHeight(34)
        row.addWidget(self.status_label, 1)
        # Where the universes go while the drawer is open: the window is wide
        # then, and this row is the only thing that spans it.
        self.uni_strip = QWidget()
        self.discovered_row = QHBoxLayout(self.uni_strip)
        self.discovered_row.setContentsMargins(0, 0, 0, 0)
        self.discovered_row.setSpacing(6)
        self.uni_strip.setVisible(False)
        row.addWidget(self.uni_strip, 0)
        return frame

    @staticmethod
    def _field_label(text):
        label = QLabel(text)
        label.setObjectName("caption")
        return label

    def _indicator(self, box):
        """LED + one-word state, the at-a-glance half of the status."""
        row = QHBoxLayout()
        row.setSpacing(8)
        led = QLabel("●")
        state = QLabel("stopped")
        state.setObjectName("dim")
        row.addWidget(led)
        row.addWidget(state, 1)
        box.addLayout(row)
        self._set_led(led, "off")
        return led, state

    def _set_led(self, led, state):
        # Setting a stylesheet re-polishes the widget, and this runs ten times
        # a second — so only touch it when the state has actually changed.
        if self._led_state.get(led) == state:
            return
        self._led_state[led] = state
        color = {"ok": COLORS["ok"], "err": COLORS["err"],
                 "warn": COLORS["warn"], "off": COLORS["text_dim"]}[state]
        led.setStyleSheet(f"color: {color}; font-size: 15px;")

    # ------------------------------------------------------------- drawer

    def _sync_channels_toggle(self):
        """Arrow points the way the drawer is about to move."""
        expanded = self.channels_toggle.isChecked()
        self.channels_toggle.setText(
            "▲  DMX values" if expanded else "▼  DMX values")
        self.channels_toggle.setToolTip(
            "Hide the 512-channel grid and run compact" if expanded
            else "Show the 512-channel grid")

    def _toggle_channels(self, expanded):
        free = not (self.isMaximized() or self.isFullScreen())
        # Remember the size being left behind, so the arrow always returns to
        # the window the user last sized in that state.
        if free:
            if expanded and not self.channel_view.isVisible():
                self._compact_size = (self.width(), self.height())
            elif not expanded and self.channel_view.isVisible():
                self._expanded_size = (self.width(), self.height())
        self.channel_view.setVisible(expanded)
        # With the grid gone the two panels take the slack, so the compact
        # window stays filled instead of leaving a hole at the bottom.
        self._root.setStretchFactor(self.columns, 0 if expanded else 1)
        self.uni_column.setVisible(not expanded)
        self.uni_strip.setVisible(expanded)
        # Wide window: the sentence fits on one line, so it stops reserving
        # room for the three it needs when compact.
        self.status_label.setMinimumHeight(20 if expanded else 34)
        self._rebuild_discovered(self._discovered_keys)
        self._sync_channels_toggle()
        if free and expanded:
            self._fit_on_screen(*(self._expanded_size or (EXPANDED_W, EXPANDED_H)))
        elif free:
            # The layout only reports its shrunken size once it has settled,
            # so the resize waits for the next event-loop pass.
            QTimer.singleShot(0, self._shrink_to_compact)
        self._save_current_settings()

    def _shrink_to_compact(self):
        if not (self.isMaximized() or self.isFullScreen()):
            self._fit_on_screen(*(self._compact_size or (COMPACT_W, COMPACT_H)))

    # ------------------------------------------------------------ geometry
    #
    # A window bigger than the desktop costs both of the things that make this
    # one usable: Muffin drops the maximise button from a window that cannot
    # fit the work area, and window managers shove an oversized window around
    # while the user tries to place it. The drawer asks for a wide window and
    # the desktop is not always wide — a 4K screen at 300% scaling reports
    # 1280x680 of usable space — so every resize here is clamped to what fits.
    # Sizes the user set by hand are already on screen; only the two remembered
    # sizes and the two defaults ever come through here.

    def _max_client_size(self):
        """Largest window that still fits the work area, or None.

        The window is frameless, so what it asks for is all there is — there is
        no title bar or border outside it to leave room for.
        """
        screen = self.screen()
        if screen is None:
            return None
        avail = screen.availableGeometry()
        return (avail.width(), avail.height())

    def _fit_on_screen(self, width, height):
        """Resize to at most what the desktop can show."""
        limit = self._max_client_size()
        if limit:
            width, height = min(width, limit[0]), min(height, limit[1])
        self.resize(width, height)
        # The size only settles once the layout has been through an event
        # loop pass, and only then is it worth checking where it landed.
        QTimer.singleShot(0, self._settle_on_screen)

    def _settle_on_screen(self):
        """Re-clamp once the layout has settled, then pull the window into view."""
        if self.isMaximized() or self.isFullScreen():
            return
        screen = self.screen()
        limit = self._max_client_size()
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

    # --------------------------------------------------- frameless plumbing

    def _edges_at(self, pos):
        """Which window edges a point in the body is close enough to grab."""
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edges()
        edges = Qt.Edges()
        if pos.x() <= RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        elif pos.x() >= self._body.width() - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        # The body starts under the title bar, which handles the top edge.
        if pos.y() >= self._body.height() - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _edge_cursor(edges):
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
        """The window's resize border: the margin strip around the body.

        Nothing else is over those few pixels, so the body sees the events and
        hands them to the platform's own resize loop.
        """
        if obj is self._body:
            kind = event.type()
            if kind == QEvent.MouseMove and not event.buttons():
                self._body.setCursor(
                    self._edge_cursor(self._edges_at(event.position())))
            elif kind == QEvent.Leave:
                self._body.unsetCursor()
            elif (kind == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton):
                edges = self._edges_at(event.position())
                handle = self.windowHandle()
                if edges and handle is not None:
                    handle.startSystemResize(edges)
                    return True
        return super().eventFilter(obj, event)

    def changeEvent(self, event):
        """Keep the maximise glyph honest when the state changes elsewhere."""
        super().changeEvent(event)
        # setWindowFlags() in the constructor can raise this before there is a
        # bar to sync.
        bar = getattr(self, "title_bar", None)
        if bar is not None and event.type() == QEvent.WindowStateChange:
            bar.sync()

    # ------------------------------------------------------------ actions

    def _rescan(self):
        """The refresh button: COM ports, NICs, and a fresh bind."""
        self._refresh_ports()
        self._apply_engine()

    def _refresh_ports(self):
        was_loading = self._loading
        self._loading = True
        current = self.port_combo.currentData()
        self.port_combo.clear()
        self.port_combo.addItem("Monitor mode — no DMX output", "")
        for p in list_serial_ports():
            self.port_combo.addItem(p["label"], p["device"])
        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        self._refresh_nics()
        self._loading = was_loading

    def _refresh_nics(self):
        was_loading = self._loading
        self._loading = True
        current = self.nic_combo.currentData()
        self.nic_combo.clear()
        self.nic_combo.addItem("All interfaces", "")
        for ip in list_local_ipv4():
            self.nic_combo.addItem(ip, ip)
        if current:
            idx = self.nic_combo.findData(current)
            if idx >= 0:
                self.nic_combo.setCurrentIndex(idx)
        self._loading = was_loading

    def _restore_settings(self):
        self.universe_spin.setValue(self.settings.get("universe", 0))
        saved_port = self.settings.get("com_port", "")
        if saved_port:
            idx = self.port_combo.findData(saved_port)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        saved_ip = self.settings.get("bind_ip", "")
        if saved_ip:
            idx = self.nic_combo.findData(saved_ip)
            if idx >= 0:
                self.nic_combo.setCurrentIndex(idx)
        expanded = bool(self.settings.get("channels_expanded", True))
        self.channels_toggle.setChecked(expanded)
        self._toggle_channels(expanded)

    # --------------------------------------------------- lighting interface

    def _open_interface_dialog(self):
        """Create Interface: check for admin rights, then show the pop-up."""
        if not vnet.is_admin() and not self._confirm_unelevated():
            return
        dialog = InterfaceDialog(self.settings, self)
        dialog.exec()
        self._refresh_nics()
        self._save_current_settings()
        # Rebind so the receiver picks up (or lets go of) the new address.
        self._apply_engine()

    def _confirm_unelevated(self):
        box = QMessageBox(self)
        box.setWindowTitle("AnyDMX")
        box.setIcon(QMessageBox.Information)
        box.setText("Creating a network interface needs administrator rights.")
        box.setInformativeText(
            "AnyDMX is not running elevated — capturing Art-Net and driving "
            "DMX never need it. Windows will ask for permission at the moment "
            "the interface is created, and only for that one step.")
        proceed = box.addButton("Continue", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is proceed

    # ------------------------------------------------------------- engine

    def _selection_changed(self, _index=0):
        """Input port or output device changed — re-arm on the new selection."""
        if self._loading:
            return
        self._apply_engine()

    def _apply_engine(self):
        """(Re)start the bridge on the current selections. No Start button."""
        if self._loading:
            return
        port = self.port_combo.currentData() or ""
        bind_ip = self.nic_combo.currentData() or ""
        try:
            self.engine.start(port, self.universe_spin.value(),
                              fps=self.settings.get("fps", 40),
                              bind_ip=bind_ip)
            self._start_error = ""
        except OSError as e:
            # Not fatal and not worth a modal on every combo change: the bottom
            # line says what happened and ⟳ tries again.
            self._start_error = str(e)
            log.warning("Could not start the bridge: %s", e)
            self._set_led(self.artnet_led, "err")
            self._set_led(self.dmx_led, "off")
            self.artnet_state.setText("blocked")
            self.dmx_state.setText("stopped")
            return
        self._save_current_settings()

    def _universe_changed(self, value):
        self.engine.set_universe(value)
        if not self._loading:
            self._save_current_settings()

    def _clear_buffer(self):
        """Manual escape hatch for stale levels a short frame never overwrites."""
        answer = QMessageBox.question(
            self, "Clear the universe buffer",
            "This sets all 512 channels to zero and sends that out.\n"
            "If a rig is connected, it goes dark until the console sends again.\n\n"
            "Clear now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.engine.blackout()

    def _save_current_settings(self):
        self.settings["com_port"] = self.port_combo.currentData() or ""
        self.settings["universe"] = self.universe_spin.value()
        self.settings["bind_ip"] = self.nic_combo.currentData() or ""
        self.settings["channels_expanded"] = self.channels_toggle.isChecked()
        save_settings(self.settings)

    # --------------------------------------------------- discovered universes

    def _rebuild_discovered(self, keys):
        """Replace the universe buttons. On set change, or a drawer toggle.

        They live in the input panel while the window is compact and in the
        bottom strip while the drawer is open, so the rebuild always empties
        both and fills whichever one is currently on screen.
        """
        for layout in (self.discovered_col, self.discovered_row):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._uni_buttons = {}
        compact = not self.channels_toggle.isChecked()
        target = self.discovered_col if compact else self.discovered_row
        if not keys:
            if compact:
                # Short enough to read in the compact column; the rest of the
                # advice is a hover away rather than three clipped lines.
                empty = QLabel("nothing yet — is Art-Net output switched on?")
                empty.setToolTip(
                    "Check that your lighting app's Art-Net output is enabled "
                    "and bound to a real adapter, not 0.0.0.0.")
                empty.setObjectName("caption")
                empty.setWordWrap(True)
                target.addWidget(empty)
                target.addStretch()
            return
        for universe in keys:
            btn = QPushButton()
            btn.setObjectName("uni")
            btn.setProperty("compact", "true" if compact else "false")
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _checked, u=universe: self.universe_spin.setValue(u))
            target.addWidget(btn)
            self._uni_buttons[universe] = btn
        if compact:
            target.addStretch()

    def _update_discovered(self, universes):
        now = time.monotonic()
        dt = max(now - self._last_uni_time, 1e-6)
        keys = tuple(sorted(universes))
        if keys != self._discovered_keys:
            self._rebuild_discovered(keys)
            self._discovered_keys = keys
        selected = self.universe_spin.value()
        for universe, btn in self._uni_buttons.items():
            rec = universes[universe]
            previous = self._last_uni_packets.get(universe, rec["packets"])
            rate = max(0.0, (rec["packets"] - previous) / dt)
            live = (now - rec["last_seen"]) < ARTNET_ACTIVE_TIMEOUT
            src = rec["src"]
            wide = self.channels_toggle.isChecked()
            state = ("idle" if not live else
                     f"{rate:.0f} pkt/s" if wide else f"{rate:.0f}/s")
            # The compact column is only as wide as a combo box, so the source
            # address gives way to the tooltip there. The LOCAL TEST marker
            # never does: a simulator must not be able to pass for a console.
            local = src == LOOPBACK
            sep = "  ·  " if wide else " · "
            origin = f"{sep}{src}" if wide else ""
            marker = f"{sep}LOCAL TEST" if local else ""
            btn.setText(f"U{universe}{origin}{marker}{sep}{state}")
            btn.setToolTip(
                f"Listen to universe {universe}, sent from {src} at "
                f"{rate:.0f} packets/s"
                + (" — this is the local test sender, not your console."
                   if local else "."))
            btn.setChecked(universe == selected)
        self._last_uni_packets = {u: r["packets"] for u, r in universes.items()}
        self._last_uni_time = now

    # ------------------------------------------------------------- update

    def _poll(self):
        if not self.engine.running:
            if self._start_error:
                self._set_status(
                    "err", f"Not listening — {self._start_error}  "
                           "Close whatever holds UDP 6454, or pick a specific "
                           "input port, then press ⟳.")
            return
        st = self.engine.get_status()
        universes = st["universes"]
        self._update_discovered(universes)
        others = sorted(u for u in universes if u != self.universe_spin.value())
        artnet = self._artnet_status(st, others)
        dmx = self._dmx_status(st)
        # The short state is on the panel; the bottom line carries the sentence
        # behind it, and the output's only when it is not simply streaming.
        detail = artnet[2] if dmx[0] == "ok" else f"{artnet[2]}    ·    {dmx[2]}"
        self._set_led(self.artnet_led, artnet[0])
        self.artnet_state.setText(artnet[1])
        self._set_led(self.dmx_led, dmx[0])
        self.dmx_state.setText(dmx[1])
        self._set_status(artnet[0] if dmx[0] != "err" else "err", detail)
        self.channel_view.set_channels(
            self.engine.get_channels(),
            live_len=st["frame_len"] if st["artnet_active"] else 0,
            holding=st["holding"])

    def _artnet_status(self, st, others):
        """(led, short state, sentence) for the Art-Net side."""
        if st["artnet_active"]:
            source = st["artnet_source"]
            origin = (f"{source} — LOCAL TEST SENDER, not your console"
                      if source == LOOPBACK else source)
            return ("ok", f"receiving · {st['artnet_pps']:.0f} pkt/s",
                    f"Art-Net: receiving universe {self.universe_spin.value()} "
                    f"from {origin} ({st['artnet_pps']:.0f} pkt/s, "
                    f"{st['frame_len']} ch)")
        if st["holding"]:
            # Not a fault — the last frame is held on purpose so the rig stays
            # lit. Saying so is the whole point: silence looks identical to it.
            source = st["artnet_source"] or "the last source"
            return ("warn", "holding last frame",
                    f"Art-Net: nothing arriving — holding last frame from "
                    f"{source} ({st['held_since']:.0f}s ago)")
        if others:
            # The console is talking, just not on the universe we listen to.
            listed = ", ".join(str(u) for u in others)
            return ("warn", "wrong universe",
                    f"Art-Net: nothing on universe "
                    f"{self.universe_spin.value()} — but universe {listed} is "
                    f"arriving. Click it to listen to it.")
        if st["poller_ip"]:
            return ("warn", "discovered, no DMX",
                    f"Art-Net: node visible to console at {st['poller_ip']} "
                    "— waiting for DMX on this universe")
        return ("warn", "listening",
                "Art-Net: listening on UDP 6454 — nothing is being sent")

    def _dmx_status(self, st):
        """(led, short state, sentence) for the DMX side."""
        if not st["dmx_enabled"]:
            return ("warn", "monitor mode",
                    "DMX out: monitor mode — no output device selected")
        if st["dmx_connected"]:
            return ("ok", f"streaming · {st['dmx_fps']:.0f} fps",
                    f"DMX out: streaming ({st['dmx_fps']:.0f} fps)")
        err = st["dmx_error"] or "port unavailable"
        return ("err", "reconnecting", f"DMX out: reconnecting — {err}")

    def _set_status(self, level, text):
        if level != self._status_level:
            self._status_level = level
            color = {"ok": COLORS["text"], "warn": COLORS["warn"],
                     "err": COLORS["err"], "off": COLORS["text_dim"]}[level]
            self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setText(text)

    def closeEvent(self, event):
        self._save_current_settings()
        self.engine.stop()
        event.accept()
