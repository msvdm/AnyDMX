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


class RateMeter:
    """Events per second off a monotonic cumulative counter.

    Every rate in this app is read far more often than it can be measured: a
    100 ms sample of a ~35 Hz stream holds three or four events, so ordinary
    timer jitter alone swings the answer by ±5. The figure is therefore
    averaged over RATE_WINDOW and *held* between windows rather than
    recomputed per read — which is only correct because the elapsed time is
    measured from the last computation, not from the last call.
    """

    def __init__(self, window=RATE_WINDOW):
        self._window = window
        self.rate = 0.0
        self._last_time = time.monotonic()
        self._last_count = 0

    def reset(self, now=None):
        """Counters restart with each receiver/output, so the window must too."""
        self._last_time = time.monotonic() if now is None else now
        self._last_count = 0
        self.rate = 0.0

    def update(self, count, now=None):
        """Feed the cumulative total; returns the current rate."""
        now = time.monotonic() if now is None else now
        dt = now - self._last_time
        if dt >= self._window:
            self.rate = max(0.0, (count - self._last_count) / dt)
            self._last_time = now
            self._last_count = count
        return self.rate



class Engine:
    def __init__(self):
        self._buffer = bytearray(DMX_CHANNELS)
        self._lock = threading.Lock()
        self._receiver = None
        self._output = None
        self.running = False
        self._packet_rate = RateMeter()
        self._frame_rate = RateMeter()

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
        self._packet_rate.reset()
        self._frame_rate.reset()

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
        """Snapshot for the GUI.

        Not a pure read: this is the sampling point for both rate meters, so
        it expects one caller on one cadence — the GUI's poll timer. A second
        caller would not corrupt the figures (each window is measured from the
        last computation) but would close the windows early on both.
        """
        now = time.monotonic()
        pps = self._packet_rate.update(
            self._receiver.packets_total if self._receiver else 0, now)
        fps = self._frame_rate.update(
            self._output.frames_total if self._output else 0, now)

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
