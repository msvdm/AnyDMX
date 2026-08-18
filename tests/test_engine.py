"""Tests for the engine's universe buffer behavior."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_status_when_stopped():
    engine = Engine()
    st = engine.get_status()
    assert st["running"] is False
    assert st["artnet_active"] is False
    assert st["dmx_connected"] is False
