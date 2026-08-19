"""Tests for the engine's universe buffer behavior."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import engine as engine_module
from src.core.engine import Engine


def test_buffer_starts_zeroed():
    engine = Engine()
    assert engine.get_channels() == bytes(512)


def test_on_dmx_updates_buffer():
    engine = Engine()
    engine._on_dmx(bytes([100, 200, 255]))
    channels = engine.get_channels()
    assert channels[:3] == bytes([100, 200, 255])
    assert channels[3:] == bytes(509)  # untouched channels stay at 0


def test_partial_frame_holds_previous_values():
    engine = Engine()
    engine._on_dmx(bytes([50] * 512))
    engine._on_dmx(bytes([255] * 10))
    channels = engine.get_channels()
    assert channels[:10] == bytes([255] * 10)
    assert channels[10:] == bytes([50] * 502)


def test_oversized_frame_is_clamped():
    engine = Engine()
    engine._on_dmx(bytes([9] * 600))
    assert len(engine.get_channels()) == 512


def test_blackout():
    engine = Engine()
    engine._on_dmx(bytes([255] * 512))
    engine.blackout()
    assert engine.get_channels() == bytes(512)


def test_monitor_mode_no_com_port():
    engine = Engine()
    engine.start("", universe=0)
    try:
        st = engine.get_status()
        assert st["running"] is True
        assert st["dmx_enabled"] is False
        assert st["dmx_connected"] is False
    finally:
        engine.stop()


def test_status_when_stopped():
    engine = Engine()
    st = engine.get_status()
    assert st["running"] is False
    assert st["artnet_active"] is False
    assert st["dmx_connected"] is False


# ------------------------------------- held / stale levels (no real sockets)

class FakeReceiver:
    """Stands in for ArtNetReceiver so no UDP port is bound."""

    def __init__(self, universe=0):
        self.universe = universe
        self.packets_total = 0
        self.last_frame_len = 0
        self.last_source_ip = None
        self.last_packet_time = 0.0
        self.last_poll_ip = None
        self.last_poll_time = 0.0

    def get_universes(self):
        return {}

    def stop(self):
        pass


def _running_engine(universe=0):
    engine = Engine()
    engine._receiver = FakeReceiver(universe)
    engine.running = True
    return engine


def test_short_frame_leaves_a_stale_tail_and_status_says_so():
    """The dot2 -> Onyx puzzle: a short frame never clears what came before."""
    engine = _running_engine()
    engine._receiver.last_frame_len = 512
    engine._on_dmx(bytes([255] * 512))
    engine._receiver.last_frame_len = 128
    engine._on_dmx(bytes([10] * 128))
    assert engine.get_channels()[128:] == bytes([255] * 384)
    assert engine.get_status()["frame_len"] == 128


def test_holding_is_reported_when_levels_outlive_the_signal():
    engine = _running_engine()
    assert engine.get_status()["holding"] is False   # zeroed buffer, nothing held
    engine._on_dmx(bytes([255] * 512))               # levels, but no packet time
    st = engine.get_status()
    assert st["holding"] is True
    assert st["artnet_active"] is False


def test_holding_is_false_while_art_net_is_live():
    engine = _running_engine()
    engine._on_dmx(bytes([255] * 512))
    engine._receiver.last_packet_time = time.monotonic()
    assert engine.get_status()["holding"] is False


def test_status_when_stopped_reports_no_hold():
    st = Engine().get_status()
    assert st["holding"] is False
    assert st["frame_len"] == 0


def test_changing_universe_drops_the_old_universes_levels():
    engine = _running_engine(universe=0)
    engine._on_dmx(bytes([255] * 512))
    engine.set_universe(5)
    assert engine.get_channels() == bytes(512)
    assert engine._receiver.universe == 5


def test_reselecting_the_same_universe_keeps_the_frame():
    engine = _running_engine(universe=3)
    engine._on_dmx(bytes([255] * 512))
    engine.set_universe(3)
    assert engine.get_channels() == bytes([255] * 512)


# --------------------------------------------------------- rate measurement

class FakeOutput:
    def __init__(self):
        self.frames_total = 0
        self.connected = True
        self.last_error = None

    def stop(self):
        pass


def _wind_back(engine, seconds):
    """Age both rate windows, so the next get_status() closes one."""
    for meter in (engine._packet_rate, engine._frame_rate):
        meter._last_time -= seconds


def test_rates_ignore_windows_shorter_than_the_averaging_period():
    """A 100 ms poll cannot measure a 35 Hz stream: jitter alone swings it."""
    engine = _running_engine()
    engine._output = FakeOutput()
    engine._reset_rates()
    engine._output.frames_total = 4
    assert engine.get_status()["dmx_fps"] == 0.0   # window too short — no guess
    _wind_back(engine, engine_module.RATE_WINDOW)
    engine._output.frames_total = 20
    assert engine.get_status()["dmx_fps"] > 0.0


def test_rate_holds_its_last_value_between_windows():
    engine = _running_engine()
    engine._output = FakeOutput()
    engine._reset_rates()
    _wind_back(engine, 1.0)
    engine._output.frames_total = 35
    first = engine.get_status()["dmx_fps"]
    assert 30 < first < 40
    engine._output.frames_total = 36          # one more frame, tiny window
    assert engine.get_status()["dmx_fps"] == first   # steady, not a spike


def test_rates_reset_when_the_engine_restarts():
    engine = _running_engine()
    engine._output = FakeOutput()
    _wind_back(engine, 1.0)
    engine._output.frames_total = 35
    engine.get_status()
    engine.stop()
    assert engine.get_status()["dmx_fps"] == 0.0


# ------------------------------------------------------------- rate meter

def test_rate_meter_holds_zero_until_a_full_window():
    meter = engine_module.RateMeter(window=0.5)
    assert meter.update(10, now=meter._last_time + 0.4) == 0.0


def test_rate_meter_averages_over_the_elapsed_window():
    meter = engine_module.RateMeter(window=0.5)
    start = meter._last_time
    assert meter.update(20, now=start + 1.0) == 20.0


def test_rate_meter_measures_from_the_last_computation_not_the_last_call():
    """Extra reads inside a window must not shorten it — only close it early."""
    meter = engine_module.RateMeter(window=0.5)
    start = meter._last_time
    for offset in (0.1, 0.2, 0.3, 0.4):
        assert meter.update(10, now=start + offset) == 0.0
    # 20 events over the full 1.0 s since the last computation, not since 0.4 s.
    assert meter.update(20, now=start + 1.0) == 20.0


def test_rate_meter_ignores_a_counter_that_went_backwards():
    """A restarted receiver resets its counter; a negative rate is nonsense."""
    meter = engine_module.RateMeter(window=0.5)
    start = meter._last_time
    meter.update(100, now=start + 1.0)
    assert meter.update(5, now=start + 2.0) == 0.0


def test_rate_meter_reset_clears_the_held_figure():
    meter = engine_module.RateMeter(window=0.5)
    meter.update(20, now=meter._last_time + 1.0)
    assert meter.rate > 0
    meter.reset()
    assert meter.rate == 0.0
