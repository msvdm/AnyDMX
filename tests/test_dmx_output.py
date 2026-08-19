"""Tests for the DMX serial frame format (mocked serial port)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.dmx_output import (
    DmxOutput, DMX_CHANNELS, FRAME_TX_TIME, MIN_FRAME_PERIOD,
)


class FakeSerial:
    """Records the break/write sequence of one DMX frame."""

    def __init__(self, busy_polls=0):
        self.events = []
        self._break = False
        self._busy_polls = busy_polls  # polls before the buffer reports empty

    @property
    def out_waiting(self):
        self.events.append(("out_waiting", self._busy_polls))
        if self._busy_polls:
            self._busy_polls -= 1
            return 1
        return 0

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


# ------------------------------------------------------- break/frame overrun

def test_frame_is_confirmed_sent_before_returning():
    """A break asserted into the tail of the previous frame corrupts it.

    That is the whole cause of every fixture twitching at once, so the drain
    check must come after the flush and before the caller can break again.
    """
    out = DmxOutput("COM_FAKE", lambda: bytes(DMX_CHANNELS))
    out._ser = FakeSerial()
    out._send_frame(bytes(DMX_CHANNELS))
    kinds = [k for k, _ in out._ser.events]
    assert kinds.index("flush") < kinds.index("out_waiting")


def test_drain_waits_while_the_buffer_still_holds_data():
    out = DmxOutput("COM_FAKE", lambda: bytes(DMX_CHANNELS))
    out._ser = FakeSerial(busy_polls=3)
    out._send_frame(bytes(DMX_CHANNELS))
    polls = [k for k, _ in out._ser.events if k == "out_waiting"]
    assert len(polls) == 4          # three busy, then empty


def test_drain_survives_a_port_that_cannot_report_its_buffer():
    class NoOutWaiting(FakeSerial):
        @property
        def out_waiting(self):
            raise OSError("not supported by this driver")

    out = DmxOutput("COM_FAKE", lambda: bytes(DMX_CHANNELS))
    out._ser = NoOutWaiting()
    out._send_frame(bytes(DMX_CHANNELS))   # must not raise
    assert ("flush", None) in out._ser.events


def test_requested_fps_is_clamped_to_what_the_wire_allows():
    """512 channels take 22.6 ms at 250 kbaud — 40 fps does not fit."""
    assert FRAME_TX_TIME > 0.022
    fast = DmxOutput("COM_FAKE", lambda: bytes(DMX_CHANNELS), fps=40)
    assert fast._period == MIN_FRAME_PERIOD
    assert 1.0 / fast._period < 40


def test_a_slower_requested_fps_is_left_alone():
    slow = DmxOutput("COM_FAKE", lambda: bytes(DMX_CHANNELS), fps=25)
    assert slow._period == 1.0 / 25
