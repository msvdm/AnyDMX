"""Tests for the DMX serial frame format (mocked serial port)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serial

from src.core import dmx_output
from src.core.dmx_output import (
    DmxOutput, DMX_CHANNELS, FRAME_TX_TIME, MIN_FRAME_PERIOD,
    explain_open_error,
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


# ------------------------------------------------------- opening the port

def test_permission_denied_names_the_dialout_group(monkeypatch):
    """The first thing a new Linux user hits, and the raw errno explains none of it.

    /dev/ttyUSB* is root:dialout. A bare "[Errno 13] Permission denied" in the
    status line sends the user hunting; naming the group and the command ends
    it in one step.
    """
    monkeypatch.setattr(dmx_output, "IS_LINUX", True)
    exc = serial.SerialException(
        13, "could not open port /dev/ttyUSB0: [Errno 13] Permission denied")
    message = explain_open_error("/dev/ttyUSB0", exc)
    assert "/dev/ttyUSB0" in message
    assert "dialout" in message


def test_other_serial_errors_are_passed_through_untouched(monkeypatch):
    """Only EACCES gets rewritten. Everything else must reach the user as-is."""
    monkeypatch.setattr(dmx_output, "IS_LINUX", True)
    exc = serial.SerialException("device reports readiness but returned no data")
    assert explain_open_error("/dev/ttyUSB0", exc) == str(exc)


def test_permission_denied_is_not_rewritten_off_linux(monkeypatch):
    """There is no dialout group on Windows; the advice would be nonsense."""
    monkeypatch.setattr(dmx_output, "IS_LINUX", False)
    exc = serial.SerialException(13, "could not open port COM3: Access is denied")
    assert "dialout" not in explain_open_error("COM3", exc)


def test_a_failed_open_reaches_last_error(monkeypatch):
    """The explained message is what the GUI reads, not the raw exception."""
    monkeypatch.setattr(dmx_output, "IS_LINUX", True)

    def refuse(*args, **kwargs):
        raise serial.SerialException(13, "[Errno 13] Permission denied")

    monkeypatch.setattr(dmx_output.serial, "Serial", refuse)
    out = DmxOutput("/dev/ttyUSB0", lambda: bytes(DMX_CHANNELS))
    assert out._open() is False
    assert out.connected is False
    assert "dialout" in out.last_error


# ------------------------------------------------------- port enumeration

class FakePort:
    def __init__(self, device, vid=None, hwid="n/a", description="n/a"):
        self.device = device
        self.vid = vid
        self.hwid = hwid
        self.description = description


def test_linux_phantom_serial_nodes_are_not_offered(monkeypatch):
    """Linux creates /dev/ttyS0..31 whether or not the UARTs exist.

    Left in, they bury the one dongle the user is looking for, and the first
    of them gets picked — so the app reports a permission error about a port
    that was never there. That is a misleading answer to a question nobody
    asked, which is worse than a short list.
    """
    from src.core import ports
    monkeypatch.setattr(ports, "IS_LINUX", True)
    monkeypatch.setattr(ports.list_ports, "comports", lambda: [
        FakePort("/dev/ttyS0"), FakePort("/dev/ttyS1"),
        FakePort("/dev/ttyUSB0", vid=0x0403, hwid="USB VID:PID=0403:6001",
                 description="FT232R USB UART"),
    ])
    offered = ports.list_serial_ports()
    assert [p["device"] for p in offered] == ["/dev/ttyUSB0"]
    assert offered[0]["chip"] == "FTDI"


def test_a_real_on_board_port_survives_the_filter(monkeypatch):
    """Only nodes with no ID at all are phantoms. An on-board RS-485 port
    has a hardware ID even without a USB vendor ID, and must still show."""
    from src.core import ports
    monkeypatch.setattr(ports, "IS_LINUX", True)
    monkeypatch.setattr(ports.list_ports, "comports", lambda: [
        FakePort("/dev/ttyS0"),
        FakePort("/dev/ttyS4", hwid="PNP0501", description="16550A"),
    ])
    assert [p["device"] for p in ports.list_serial_ports()] == ["/dev/ttyS4"]


def test_windows_ports_are_never_filtered(monkeypatch):
    """Windows enumerates a COM port only when a device is behind it, so
    there is nothing to filter — and filtering could hide a real one."""
    from src.core import ports
    monkeypatch.setattr(ports, "IS_LINUX", False)
    monkeypatch.setattr(ports.list_ports, "comports", lambda: [
        FakePort("COM3", hwid="n/a", description="Bluetooth Serial"),
    ])
    assert [p["device"] for p in ports.list_serial_ports()] == ["COM3"]
