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
# The GUI polls every 100 ms, which is far too short a window to measure a
# ~35 Hz stream: 3 or 4 frames land per poll, so ordinary timer jitter alone
# swings the answer by ±5 fps and makes a steady output look unstable.
RATE_WINDOW = 0.5            # seconds of history behind each rate figure
POLL_SEEN_TIMEOUT = 8.0      # seconds a console's ArtPoll counts as "discovered us"


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
        self._pps = 0.0
        self._fps = 0.0

    def start(self, com_port, universe, fps=40, bind_ip=""):
        """Start bridging. Empty com_port = monitor mode (Art-Net in only).
        Empty bind_ip = listen on all interfaces."""
        if self.running:
            self.stop()
        self._receiver = ArtNetReceiver(universe, self._on_dmx,
                                        bind_ip=bind_ip or "0.0.0.0")
        self._output = DmxOutput(com_port, self.get_channels, fps=fps) \
            if com_port else None
        self._reset_rates()
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
        self._reset_rates()
        log.info("Engine stopped")

    def _reset_rates(self):
        """Counters restart with each receiver/output, so the window must too."""
        self._last_poll_time = time.monotonic()
        self._last_packets = 0
        self._last_frames = 0
        self._pps = 0.0
        self._fps = 0.0

    def set_universe(self, universe):
        """Switch the listened-to universe, dropping what the old one left.

        Levels captured for universe A must never masquerade as universe B —
        with nothing sending on the new universe the grid would otherwise show
        the old one's values indefinitely. Switching is a deliberate user act,
        so clearing here does not violate the hold-last-frame invariant.
        """
        if self._receiver and self._receiver.universe != universe:
            self._receiver.universe = universe
            self.blackout()

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
        dt = now - self._last_poll_time
        packets = self._receiver.packets_total if self._receiver else 0
        frames = self._output.frames_total if self._output else 0
        if dt >= RATE_WINDOW:
            self._pps = max(0.0, (packets - self._last_packets) / dt)
            self._fps = max(0.0, (frames - self._last_frames) / dt)
            self._last_poll_time = now
            self._last_packets = packets
            self._last_frames = frames
        pps, fps = self._pps, self._fps

        artnet_active = bool(
            self._receiver
            and self._receiver.last_packet_time
            and (now - self._receiver.last_packet_time) < ARTNET_ACTIVE_TIMEOUT
        )
        # Held = the buffer still carries levels but nothing is sending them any
        # more. The GUI must be able to say so: an unlabelled frozen frame looks
        # exactly like a live one, and that is a mystery every time.
        with self._lock:
            has_levels = any(self._buffer)
        held_since = (now - self._receiver.last_packet_time
                      if self._receiver and self._receiver.last_packet_time
                      else 0.0)
        poller_ip = None
        if (self._receiver and self._receiver.last_poll_time
                and (now - self._receiver.last_poll_time) < POLL_SEEN_TIMEOUT):
            poller_ip = self._receiver.last_poll_ip
        return {
            "running": self.running,
            "artnet_active": artnet_active,
            "holding": bool(self.running and not artnet_active and has_levels),
            "held_since": held_since,
            "frame_len": self._receiver.last_frame_len if self._receiver else 0,
            "artnet_source": self._receiver.last_source_ip if self._receiver else None,
            "universes": self._receiver.get_universes() if self._receiver else {},
            "poller_ip": poller_ip,
            "artnet_pps": pps,
            "dmx_enabled": self._output is not None,
            "dmx_connected": bool(self._output and self._output.connected),
            "dmx_fps": fps,
            "dmx_error": self._output.last_error if self._output else None,
        }
