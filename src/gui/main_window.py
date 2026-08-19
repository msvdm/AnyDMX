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

What this file is *not* responsible for: being a frameless window (see
frameless.py), phrasing the status (status_text.py), or drawing the discovered
universes (universe_bar.py). What is left is the panels and the wiring.
"""

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from src.core import vnet
from src.core.artnet_receiver import list_local_ipv4
from src.core.engine import Engine
from src.core.ports import list_serial_ports
from src.gui.channel_view import ChannelView
from src.gui.frameless import FramelessWindow
from src.gui.status_text import artnet_status, bottom_line, dmx_status
from src.gui.styles import COLORS
from src.gui.title_bar import TitleBar, TitleRule
from src.gui.universe_bar import UniverseBar
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

LED_COLORS = {"ok": "ok", "err": "err", "warn": "warn", "off": "text_dim"}
STATUS_COLORS = {"ok": "text", "warn": "warn", "err": "err", "off": "text_dim"}


class MainWindow(FramelessWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AnyDMX — Art-Net to DMX bridge")
        self.engine = Engine()
        self.settings = load_settings()
        # Each drawer state remembers the size it was last left at, so the
        # arrow toggles between two windows the user has already sized. Session
        # only: a restart opens at the compact default again.
        self._compact_size = None
        self._expanded_size = None
        self._start_error = ""
        self._led_state = {}
        self._status_level = None
        # Set only here: the constructor populates combos and restores saved
        # values, and neither must re-arm the engine before it is wired up.
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
        # window's resize border.
        body = QWidget()
        body.setObjectName("body")
        shell.addWidget(body, 1)
        self.set_resize_body(body)

        self._root = QVBoxLayout(body)
        self._root.setContentsMargins(10, 8, 10, 8)
        self._root.setSpacing(6)

        # One set of chips, shown in the panel or the bottom strip.
        self.universe_bar = UniverseBar()
        self.universe_bar.universe_picked.connect(self._pick_universe)

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
        self.setCentralWidget(central)
        self.fit_on_screen(COMPACT_W, COMPACT_H)

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
        self.uni_scroll = QScrollArea()
        self.uni_scroll.setWidgetResizable(True)
        self.uni_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # No minimum: it scrolls, so it is the one thing here that can give way
        # when the window is squeezed onto a small screen.
        self.uni_scroll.setMinimumHeight(0)
        self.uni_scroll.setWidget(self.universe_bar)
        column.addWidget(self.uni_scroll, 1)
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
        self.uni_strip_row = QHBoxLayout(self.uni_strip)
        self.uni_strip_row.setContentsMargins(0, 0, 0, 0)
        self.uni_strip_row.setSpacing(6)
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
        led.setStyleSheet(
            f"color: {COLORS[LED_COLORS[state]]}; font-size: 15px;")

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
        free = not self.is_fixed()
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
        self._move_universe_bar(wide=expanded)
        # Wide window: the sentence fits on one line, so it stops reserving
        # room for the three it needs when compact.
        self.status_label.setMinimumHeight(20 if expanded else 34)
        self._sync_channels_toggle()
        if free and expanded:
            self.fit_on_screen(*(self._expanded_size or (EXPANDED_W, EXPANDED_H)))
        elif free:
            # The layout only reports its shrunken size once it has settled,
            # so the resize waits for the next event-loop pass.
            QTimer.singleShot(0, self._shrink_to_compact)
        self._save_current_settings()

    def _move_universe_bar(self, wide):
        """Carry the one set of chips between its two homes.

        takeWidget()/setWidget() and add/removeWidget only move the widget —
        the chips, their rate meters and the selection all survive the trip.
        """
        if wide:
            self.uni_scroll.takeWidget()
            self.uni_strip_row.addWidget(self.universe_bar)
        else:
            self.uni_strip_row.removeWidget(self.universe_bar)
            self.uni_scroll.setWidget(self.universe_bar)
        self.universe_bar.set_wide(wide)
        self.universe_bar.setVisible(True)
        self.uni_column.setVisible(not wide)
        self.uni_strip.setVisible(wide)

    def _shrink_to_compact(self):
        if not self.is_fixed():
            self.fit_on_screen(*(self._compact_size or (COMPACT_W, COMPACT_H)))

    # ------------------------------------------------------------ actions

    def _rescan(self):
        """The refresh button: COM ports, NICs, and a fresh bind."""
        self._refresh_ports()
        self._apply_engine()

    @staticmethod
    def _repopulate(combo, default_label, entries):
        """Refill a combo, keeping the current selection if it survives.

        Signals stay blocked throughout: clear() emits currentIndexChanged once
        per item it drops, which would otherwise re-arm the engine several
        times per rescan.
        """
        with QSignalBlocker(combo):
            current = combo.currentData()
            combo.clear()
            combo.addItem(default_label, "")
            for label, data in entries:
                combo.addItem(label, data)
            if current:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)

    def _refresh_ports(self):
        self._repopulate(
            self.port_combo, "Monitor mode — no DMX output",
            [(p["label"], p["device"]) for p in list_serial_ports()])
        self._refresh_nics()

    def _refresh_nics(self):
        self._repopulate(self.nic_combo, "All interfaces",
                         [(ip, ip) for ip in list_local_ipv4()])

    def _restore_settings(self):
        self.universe_spin.setValue(self.settings.get("universe", 0))
        for combo, key in ((self.port_combo, "com_port"),
                           (self.nic_combo, "bind_ip")):
            saved = self.settings.get(key, "")
            index = combo.findData(saved) if saved else -1
            if index >= 0:
                combo.setCurrentIndex(index)
        expanded = bool(self.settings.get("channels_expanded", True))
        self.channels_toggle.setChecked(expanded)
        self._toggle_channels(expanded)

    def _pick_universe(self, universe):
        """A universe chip was clicked — the spin box is the single source."""
        self.universe_spin.setValue(universe)

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
        selected = self.universe_spin.value()
        self.universe_bar.update_universes(st["universes"], selected)

        others = sorted(u for u in st["universes"] if u != selected)
        artnet = artnet_status(st, selected, others)
        dmx = dmx_status(st)
        self._set_led(self.artnet_led, artnet.led)
        self.artnet_state.setText(artnet.short)
        self._set_led(self.dmx_led, dmx.led)
        self.dmx_state.setText(dmx.short)
        self._set_status(*bottom_line(artnet, dmx))

        self.channel_view.set_channels(
            self.engine.get_channels(),
            live_len=st["frame_len"] if st["artnet_active"] else 0,
            holding=st["holding"])

    def _set_status(self, level, text):
        if level != self._status_level:
            self._status_level = level
            self.status_label.setStyleSheet(
                f"color: {COLORS[STATUS_COLORS[level]]};")
        self.status_label.setText(text)

    def closeEvent(self, event):
        self._save_current_settings()
        self.engine.stop()
        event.accept()
