"""Main window: port/universe selection, status indicators, live channel grid."""

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout,
    QWidget,
)

from src.core import vnet
from src.core.artnet_receiver import LOOPBACK, list_local_ipv4
from src.core.engine import ARTNET_ACTIVE_TIMEOUT, Engine
from src.core.ports import list_serial_ports
from src.gui.channel_view import ChannelView
from src.gui.styles import COLORS
from src.utils.logger import get_logger
from src.utils.settings import load_settings, save_settings

log = get_logger(__name__)

POLL_MS = 100  # GUI refresh interval
DEFAULT_HEIGHT = 820  # window height with the channel drawer open


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AnyDMX — Art-Net to DMX bridge")
        # Spelled out so every window manager offers maximise. Muffin hides it
        # on a window whose minimum size cannot fit the screen, which is why
        # the channel grid's minimum has to stay modest.
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.engine = Engine()
        self.settings = load_settings()
        self._uni_buttons = {}
        self._discovered_keys = None
        self._last_uni_packets = {}
        self._last_uni_time = time.monotonic()
        self._expanded_height = None
        self._build_ui()
        self._refresh_ports()
        self._restore_settings()
        # Deferred: querying the adapter costs a PowerShell round trip, and the
        # window should paint before we spend a second on it.
        QTimer.singleShot(0, self._refresh_vnet)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_MS)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Controls row
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("DMX output:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(220)
        controls.addWidget(self.port_combo)
        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedWidth(34)
        refresh_btn.setToolTip(
            "Rescan COM ports, network interfaces, and the lighting interface")
        refresh_btn.clicked.connect(self._rescan)
        controls.addWidget(refresh_btn)
        controls.addSpacing(16)
        controls.addWidget(QLabel("Network:"))
        self.nic_combo = QComboBox()
        self.nic_combo.setMinimumWidth(150)
        self.nic_combo.setToolTip(
            "Interface to listen on and advertise as an Art-Net node.\n"
            "All interfaces works for most setups; pick a specific IP on\n"
            "multi-NIC machines (e.g. a dedicated lighting network).")
        controls.addWidget(self.nic_combo)
        controls.addSpacing(16)
        controls.addWidget(QLabel("Universe:"))
        self.universe_spin = QSpinBox()
        self.universe_spin.setRange(0, 32767)
        self.universe_spin.setToolTip(
            "Art-Net port address (0-based).\n"
            "Universe 0 = Net 0, Subnet 0, Universe 0 — the default on most consoles.")
        self.universe_spin.valueChanged.connect(self._universe_changed)
        controls.addWidget(self.universe_spin)
        controls.addStretch()
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("start")
        self.start_btn.setCheckable(True)
        self.start_btn.setMinimumWidth(110)
        self.start_btn.clicked.connect(self._toggle_engine)
        controls.addWidget(self.start_btn)
        root.addLayout(controls)

        # Status row
        status_frame = QFrame()
        status_frame.setObjectName("panel")
        status = QHBoxLayout(status_frame)
        status.setContentsMargins(12, 8, 12, 8)
        self.artnet_led = QLabel("●")
        self.artnet_label = QLabel("Art-Net: stopped")
        self.artnet_label.setObjectName("dim")
        self.dmx_led = QLabel("●")
        self.dmx_label = QLabel("DMX out: stopped")
        self.dmx_label.setObjectName("dim")
        status.addWidget(self.artnet_led)
        status.addWidget(self.artnet_label)
        status.addSpacing(24)
        status.addWidget(self.dmx_led)
        status.addWidget(self.dmx_label)
        status.addStretch()
        root.addWidget(status_frame)
        self._set_led(self.artnet_led, "off")
        self._set_led(self.dmx_led, "off")

        # The lighting interface: what gives an auto-picking console a
        # 2.x address to send to. Without one, dot2 emits nothing at all.
        vnet_frame = QFrame()
        vnet_frame.setObjectName("panel")
        vnet_row = QHBoxLayout(vnet_frame)
        vnet_row.setContentsMargins(12, 8, 12, 8)
        vnet_row.setSpacing(8)
        vnet_row.addWidget(QLabel("Lighting interface:"))
        self.vnet_status = QLabel("checking…")
        self.vnet_status.setObjectName("dim")
        vnet_row.addWidget(self.vnet_status, stretch=1)
        self.vnet_ip_edit = QLineEdit()
        self.vnet_ip_edit.setFixedWidth(120)
        self.vnet_ip_edit.setToolTip(
            "Address for the virtual adapter. Consoles that auto-pick an "
            "Art-Net interface only accept the 2.x.x.x range.")
        vnet_row.addWidget(self.vnet_ip_edit)
        vnet_row.addWidget(QLabel("/"))
        self.vnet_prefix_spin = QSpinBox()
        self.vnet_prefix_spin.setRange(1, 32)
        self.vnet_prefix_spin.setFixedWidth(56)
        vnet_row.addWidget(self.vnet_prefix_spin)
        self.vnet_create_btn = QPushButton("Create")
        self.vnet_create_btn.clicked.connect(self._create_vnet)
        vnet_row.addWidget(self.vnet_create_btn)
        self.vnet_remove_btn = QPushButton("Remove")
        self.vnet_remove_btn.clicked.connect(self._remove_vnet)
        vnet_row.addWidget(self.vnet_remove_btn)
        root.addWidget(vnet_frame)

        # Everything actually on the wire — so nobody has to guess a universe
        discovered_frame = QFrame()
        discovered_frame.setObjectName("panel")
        discovered = QVBoxLayout(discovered_frame)
        discovered.setContentsMargins(12, 8, 12, 8)
        caption = QLabel("Art-Net seen on this PC:")
        caption.setObjectName("dim")
        discovered.addWidget(caption)
        self.discovered_row = QHBoxLayout()
        self.discovered_row.setSpacing(6)
        discovered.addLayout(self.discovered_row)
        root.addWidget(discovered_frame)
        self._rebuild_discovered(())

        # Channel grid — collapsible, since it is by far the tallest thing
        # in the window and most of the time nobody is reading levels.
        self.channel_view = ChannelView()
        root.addWidget(self.channel_view, stretch=1)

        # Takes up the slack while the grid is hidden, so the panels above keep
        # their natural height instead of stretching to fill the window.
        self._collapse_spacer = QWidget()
        self._collapse_spacer.setSizePolicy(QSizePolicy.Expanding,
                                            QSizePolicy.Expanding)
        self._collapse_spacer.setVisible(False)
        root.addWidget(self._collapse_spacer, stretch=1)

        legend = QHBoxLayout()
        legend.addStretch()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Zero the universe buffer — sends all-zero DMX")
        self.clear_btn.clicked.connect(self._clear_buffer)
        legend.addWidget(self.clear_btn)
        self.channels_toggle = QPushButton()
        self.channels_toggle.setObjectName("drawer")
        self.channels_toggle.setCheckable(True)
        self.channels_toggle.setChecked(True)
        self.channels_toggle.clicked.connect(self._toggle_channels)
        legend.addWidget(self.channels_toggle)
        root.addLayout(legend)
        self._sync_channels_toggle()

        self.setCentralWidget(central)
        self.resize(1520, DEFAULT_HEIGHT)

    def _set_led(self, led, state):
        color = {"ok": COLORS["ok"], "err": COLORS["err"],
                 "warn": COLORS["warn"], "off": COLORS["text_dim"]}[state]
        led.setStyleSheet(f"color: {color}; font-size: 16px;")

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
        if expanded:
            self.channel_view.setVisible(True)
            self._collapse_spacer.setVisible(False)
            self._sync_channels_toggle()
            if not (self.isMaximized() or self.isFullScreen()):
                self.resize(self.width(), self._expanded_height or DEFAULT_HEIGHT)
        else:
            # Remember the open height so reopening lands where it was left.
            if self.channel_view.isVisible():
                self._expanded_height = self.height()
            self.channel_view.setVisible(False)
            self._collapse_spacer.setVisible(True)
            self._sync_channels_toggle()
            # The layout only reports its shrunken height once it has settled,
            # so the resize has to wait for the next event-loop pass.
            QTimer.singleShot(0, self._shrink_to_fit)
        self._save_current_settings()

    def _shrink_to_fit(self):
        if self.isMaximized() or self.isFullScreen():
            return
        self.resize(self.width(), self.sizeHint().height())

    # ------------------------------------------------------------ actions

    def _rescan(self):
        """The refresh button: COM ports, NICs, and the lighting interface."""
        self._refresh_ports()
        self._refresh_vnet()

    def _refresh_ports(self):
        current = self.port_combo.currentData()
        self.port_combo.clear()
        for p in list_serial_ports():
            self.port_combo.addItem(p["label"], p["device"])
        if self.port_combo.count() == 0:
            self.port_combo.addItem("No COM ports found", "")
        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        self._refresh_nics()

    def _refresh_nics(self):
        current = self.nic_combo.currentData()
        self.nic_combo.clear()
        self.nic_combo.addItem("All interfaces", "")
        for ip in list_local_ipv4():
            self.nic_combo.addItem(ip, ip)
        if current:
            idx = self.nic_combo.findData(current)
            if idx >= 0:
                self.nic_combo.setCurrentIndex(idx)

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
        self.vnet_ip_edit.setText(self.settings.get("vnet_ip", vnet.DEFAULT_IP))
        self.vnet_prefix_spin.setValue(
            int(self.settings.get("vnet_prefix", vnet.DEFAULT_PREFIX)))
        expanded = bool(self.settings.get("channels_expanded", True))
        self.channels_toggle.setChecked(expanded)
        self._toggle_channels(expanded)

    # --------------------------------------------------- lighting interface

    def _refresh_vnet(self):
        """Update the interface row.

        Deliberately NOT driven from the 100 ms poll: it shells out to
        PowerShell, which would stall the GUI ten times a second.
        """
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        try:
            state = vnet.find_adapter(name)
        except vnet.VNetError as e:
            self.vnet_status.setText(f"could not be checked — {e}")
            self.vnet_create_btn.setEnabled(True)
            self.vnet_remove_btn.setEnabled(False)
            return
        if state:
            prefix = state.get("prefix")
            address = state.get("ip") or "no address"
            if state.get("ip") and prefix:
                address = f"{state['ip']}/{prefix}"
            self.vnet_status.setText(
                f"{state.get('name', name)} · {address} · {state.get('status', '?')}")
            self.settings["vnet_instance_id"] = state.get("instance_id") or ""
            self.vnet_create_btn.setEnabled(False)
            self.vnet_remove_btn.setEnabled(True)
            return
        self.vnet_create_btn.setEnabled(True)
        self.vnet_remove_btn.setEnabled(False)
        try:
            usable = vnet.artnet_range_addresses()
        except vnet.VNetError:
            usable = []
        if usable:
            self.vnet_status.setText(
                f"not created — Art-Net range already present at {', '.join(usable)}")
        else:
            self.vnet_status.setText(
                "not created — no 2.x/10.x address on this PC, so a console that "
                "picks its own interface (dot2) will transmit nothing")

    def _create_vnet(self):
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        ip = self.vnet_ip_edit.text().strip()
        prefix = self.vnet_prefix_spin.value()
        self._run_vnet_task(lambda: vnet.request_create(name, ip, prefix),
                            f"creating {name}…")

    def _remove_vnet(self):
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        confirm = QMessageBox.question(
            self, "AnyDMX",
            f"Remove the '{name}' network interface?\n\n"
            "Any console sending Art-Net to it will stop reaching AnyDMX.")
        if confirm != QMessageBox.Yes:
            return
        instance_id = self.settings.get("vnet_instance_id", "") or None
        self._run_vnet_task(lambda: vnet.request_remove(instance_id, name),
                            f"removing {name}…")

    def _run_vnet_task(self, task, busy_text):
        """One-shot adapter operation, with the GUI visibly busy while it runs."""
        was_running = self.engine.running
        if not vnet.is_admin():
            busy_text += "  — approve the Windows permission prompt"
        self.vnet_status.setText(busy_text)
        self.vnet_create_btn.setEnabled(False)
        self.vnet_remove_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            task()
        except vnet.VNetError as e:
            QMessageBox.critical(self, "AnyDMX", str(e))
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_vnet()
        self._refresh_nics()
        self._save_current_settings()
        if was_running:
            # Rebind so the receiver picks up (or lets go of) the new address.
            self._toggle_engine(False)
            self.start_btn.setChecked(True)
            self._toggle_engine(True)

    def _universe_changed(self, value):
        self.engine.set_universe(value)

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

    def _toggle_engine(self, checked):
        if checked:
            port = self.port_combo.currentData()
            try:
                self.engine.start(port, self.universe_spin.value(),
                                  fps=self.settings.get("fps", 40),
                                  bind_ip=self.nic_combo.currentData() or "")
            except OSError as e:
                QMessageBox.critical(
                    self, "AnyDMX",
                    f"Could not start:\n{e}\n\n"
                    "Is another Art-Net application already using UDP port 6454?")
                self.start_btn.setChecked(False)
                return
            self.start_btn.setText("Stop")
            self._save_current_settings()
        else:
            self.engine.stop()
            self.start_btn.setText("Start")
            self._set_led(self.artnet_led, "off")
            self._set_led(self.dmx_led, "off")
            self.artnet_label.setText("Art-Net: stopped")
            self.dmx_label.setText("DMX out: stopped")

    def _save_current_settings(self):
        self.settings["com_port"] = self.port_combo.currentData() or ""
        self.settings["universe"] = self.universe_spin.value()
        self.settings["bind_ip"] = self.nic_combo.currentData() or ""
        self.settings["vnet_ip"] = self.vnet_ip_edit.text().strip()
        self.settings["vnet_prefix"] = self.vnet_prefix_spin.value()
        self.settings["channels_expanded"] = self.channels_toggle.isChecked()
        save_settings(self.settings)

    # --------------------------------------------------- discovered universes

    def _rebuild_discovered(self, keys):
        """Replace the strip of universe buttons. Only on set change."""
        while self.discovered_row.count():
            item = self.discovered_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._uni_buttons = {}
        if not keys:
            empty = QLabel("nothing yet — check that your lighting app's Art-Net output is enabled and bound to a real adapter, not 0.0.0.0")
            empty.setObjectName("dim")
            self.discovered_row.addWidget(empty)
            self.discovered_row.addStretch()
            return
        for universe in keys:
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setToolTip("Listen to this universe")
            btn.clicked.connect(
                lambda _checked, u=universe: self.universe_spin.setValue(u))
            self.discovered_row.addWidget(btn)
            self._uni_buttons[universe] = btn
        self.discovered_row.addStretch()

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
            origin = f"{src} (local test sender)" if src == LOOPBACK else src
            state = f"{rate:.0f} pkt/s" if live else "idle"
            btn.setText(f"Universe {universe}  ·  {origin}  ·  {state}")
            btn.setChecked(universe == selected)
        self._last_uni_packets = {u: r["packets"] for u, r in universes.items()}
        self._last_uni_time = now

    # ------------------------------------------------------------- update

    def _poll(self):
        if not self.engine.running:
            return
        st = self.engine.get_status()
        universes = st["universes"]
        self._update_discovered(universes)
        others = sorted(u for u in universes if u != self.universe_spin.value())
        if st["artnet_active"]:
            self._set_led(self.artnet_led, "ok")
            source = st["artnet_source"]
            origin = (f"{source} — LOCAL TEST SENDER, not your console"
                      if source == LOOPBACK else source)
            self.artnet_label.setText(
                f"Art-Net: receiving from {origin} "
                f"({st['artnet_pps']:.0f} pkt/s, {st['frame_len']} ch)")
        elif st["holding"]:
            # Not a fault — the last frame is held on purpose so the rig stays
            # lit. Saying so is the whole point: silence looks identical to it.
            self._set_led(self.artnet_led, "warn")
            source = st["artnet_source"] or "the last source"
            self.artnet_label.setText(
                f"Art-Net: nothing arriving — holding last frame from {source} "
                f"({st['held_since']:.0f}s ago)")
        elif others:
            # The console is talking, just not on the universe we are listening to.
            self._set_led(self.artnet_led, "warn")
            listed = ", ".join(str(u) for u in others)
            self.artnet_label.setText(
                f"Art-Net: nothing on universe {self.universe_spin.value()} — "
                f"but universe {listed} is arriving. Click it below.")
        elif st["poller_ip"]:
            self._set_led(self.artnet_led, "warn")
            self.artnet_label.setText(
                f"Art-Net: node visible to console at {st['poller_ip']} "
                "— waiting for DMX on this universe")
        else:
            self._set_led(self.artnet_led, "warn")
            self.artnet_label.setText(
                "Art-Net: listening on UDP 6454 — nothing is being sent")
        if not st["dmx_enabled"]:
            self._set_led(self.dmx_led, "warn")
            self.dmx_label.setText(
                "DMX out: monitor mode — no COM port selected")
        elif st["dmx_connected"]:
            self._set_led(self.dmx_led, "ok")
            self.dmx_label.setText(f"DMX out: streaming ({st['dmx_fps']:.0f} fps)")
        else:
            self._set_led(self.dmx_led, "err")
            err = st["dmx_error"] or "port unavailable"
            self.dmx_label.setText(f"DMX out: reconnecting — {err}")
        self.channel_view.set_channels(
            self.engine.get_channels(),
            live_len=st["frame_len"] if st["artnet_active"] else 0,
            holding=st["holding"])

    def closeEvent(self, event):
        self._save_current_settings()
        self.engine.stop()
        event.accept()
