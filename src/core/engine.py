"""Engine: owns the 512-channel universe buffer and wires receiver → output.

The receiver and the output never talk to each other directly — the buffer is
the seam. Future input protocols (sACN, ...) and output targets (Art-Net send,
more dongles, ...) plug into this same buffer without touching each other.
"""

import threading
import time

from src.core.artnet_receiver import ArtNetReceiver
from src.core.dmx_output import DmxOutput, DMX_CHANNELS
from src.utils.logger import get_logger

log = get_logger(__name__)

ARTNET_ACTIVE_TIMEOUT = 2.0  # seconds without a packet before "no signal"


class Engine:
    def __init__(self):
        self._buffer = bytearray(DMX_CHANNELS)
        self._lock = threading.Lock()
        self._receiver = None
        self._output = None
        self.running = False
        # For rate calculations between get_status() polls
        self._last_poll_time = time.monotonic()
        self._last_packets = 0
        self._last_frames = 0

    def start(self, com_port, universe, fps=40):
        """Start bridging. Empty com_port = monitor mode (Art-Net in only)."""
        if self.running:
            self.stop()
        self._receiver = ArtNetReceiver(universe, self._on_dmx)
        self._output = DmxOutput(com_port, self.get_channels, fps=fps) \
            if com_port else None
        self._receiver.start()  # raises OSError if port 6454 is taken
        if self._output:
            self._output.start()
        self.running = True
        log.info("Engine started: universe %d -> %s",
                 universe, com_port or "(monitor mode, no DMX output)")

    def stop(self):
        if self._receiver:
            self._receiver.stop()
            self._receiver = None
        if self._output:
            self._output.stop()
            self._output = None
        self.running = False
        log.info("Engine stopped")

    def set_universe(self, universe):
        if self._receiver:
            self._receiver.universe = universe

    def _on_dmx(self, channels):
        n = min(len(channels), DMX_CHANNELS)
        with self._lock:
            self._buffer[:n] = channels[:n]

    def get_channels(self):
        with self._lock:
            return bytes(self._buffer)

    def blackout(self):
        with self._lock:
            self._buffer[:] = bytes(DMX_CHANNELS)

    def get_status(self):
        """Snapshot for the GUI. Rates are computed between successive calls."""
        now = time.monotonic()
        dt = max(now - self._last_poll_time, 1e-6)
        packets = self._receiver.packets_total if self._receiver else 0
        frames = self._output.frames_total if self._output else 0
        pps = max(0.0, (packets - self._last_packets) / dt)
        fps = max(0.0, (frames - self._last_frames) / dt)
        self._last_poll_time = now
        self._last_packets = packets
        self._last_frames = frames

        artnet_active = bool(
            self._receiver
            and self._receiver.last_packet_time
            and (now - self._receiver.last_packet_time) < ARTNET_ACTIVE_TIMEOUT
        )
        return {
            "running": self.running,
            "artnet_active": artnet_active,
            "artnet_source": self._receiver.last_source_ip if self._receiver else None,
            "artnet_pps": pps,
            "dmx_enabled": self._output is not None,
            "dmx_connected": bool(self._output and self._output.connected),
            "dmx_fps": fps,
            "dmx_error": self._output.last_error if self._output else None,
        }
