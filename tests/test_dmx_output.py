"""Tests for the DMX serial frame format (mocked serial port)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.dmx_output import DmxOutput, DMX_CHANNELS


class FakeSerial:
    """Records the break/write sequence of one DMX frame."""

    def __init__(self):
        self.events = []
        self._break = False

    @property
    def break_condition(self):
        return self._break

    @break_condition.setter
    def break_condition(self, value):
        self._break = value
        self.events.append(("break", value))

    def write(self, data):
        self.events.append(("write", bytes(data)))

    def flush(self):
        self.events.append(("flush", None))

    def close(self):
        pass


def test_frame_sequence_break_then_data():
    out = DmxOutput("COM_FAKE", lambda: bytes(DMX_CHANNELS))
    out._ser = FakeSerial()
    out._send_frame(bytes(range(256)) + bytes(256))

    events = out._ser.events
    assert events[0] == ("break", True)
    assert events[1] == ("break", False)
    kind, payload = events[2]
    assert kind == "write"
    assert payload[0] == 0x00                 # DMX start code
    assert len(payload) == 1 + DMX_CHANNELS   # start code + 512 channels
    assert payload[1:257] == bytes(range(256))
    assert events[3] == ("flush", None)


def test_open_failure_sets_error():
    out = DmxOutput("COM_DOES_NOT_EXIST_99", lambda: bytes(DMX_CHANNELS))
    assert out._open() is False
    assert out.connected is False
    assert out.last_error
