"""Main window: port/universe selection, status indicators, live channel grid."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from src.core.engine import Engine
from src.core.ports import list_serial_ports
from src.gui.channel_view import ChannelView
from src.gui.styles import COLORS
from src.utils.logger import get_logger
from src.utils.settings import load_settings, save_settings

log = get_logger(__name__)

POLL_MS = 100  # GUI refresh interval


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AnyDMX — Art-Net to DMX bridge")
        self.engine = Engine()
        self.settings = load_settings()
        self._build_ui()
        self._refresh_ports()
        self._restore_settings()
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
        refresh_btn.setToolTip("Rescan COM ports")
        refresh_btn.clicked.connect(self._refresh_ports)
        controls.addWidget(refresh_btn)
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

        # Channel grid
        self.channel_view = ChannelView()
        root.addWidget(self.channel_view, stretch=1)

        hint = QLabel("512 channels — hover a cell for its value")
        hint.setObjectName("dim")
        hint.setAlignment(Qt.AlignRight)
        root.addWidget(hint)

        self.setCentralWidget(central)
        self.resize(760, 480)

    def _set_led(self, led, state):
        color = {"ok": COLORS["ok"], "err": COLORS["err"],
                 "warn": COLORS["warn"], "off": COLORS["text_dim"]}[state]
        led.setStyleSheet(f"color: {color}; font-size: 16px;")

    # ------------------------------------------------------------ actions

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

    def _restore_settings(self):
        self.universe_spin.setValue(self.settings.get("universe", 0))
        saved_port = self.settings.get("com_port", "")
        if saved_port:
            idx = self.port_combo.findData(saved_port)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)

    def _universe_changed(self, value):
        self.engine.set_universe(value)

    def _toggle_engine(self, checked):
        if checked:
            port = self.port_combo.currentData()
            if not port:
                QMessageBox.warning(self, "AnyDMX",
                                    "No COM port selected.\n"
                                    "Plug in the USB-DMX dongle and press ⟳.")
                self.start_btn.setChecked(False)
                return
            try:
                self.engine.start(port, self.universe_spin.value(),
                                  fps=self.settings.get("fps", 40))
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
        save_settings(self.settings)

    # ------------------------------------------------------------- update

    def _poll(self):
        if not self.engine.running:
            return
        st = self.engine.get_status()
        if st["artnet_active"]:
            self._set_led(self.artnet_led, "ok")
            self.artnet_label.setText(
                f"Art-Net: receiving from {st['artnet_source']} "
                f"({st['artnet_pps']:.0f} pkt/s)")
        else:
            self._set_led(self.artnet_led, "warn")
            self.artnet_label.setText(
                "Art-Net: listening on UDP 6454 — no data for this universe")
        if st["dmx_connected"]:
            self._set_led(self.dmx_led, "ok")
            self.dmx_label.setText(f"DMX out: streaming ({st['dmx_fps']:.0f} fps)")
        else:
            self._set_led(self.dmx_led, "err")
            err = st["dmx_error"] or "port unavailable"
            self.dmx_label.setText(f"DMX out: reconnecting — {err}")
        self.channel_view.set_channels(self.engine.get_channels())

    def closeEvent(self, event):
        self._save_current_settings()
        self.engine.stop()
        event.accept()
