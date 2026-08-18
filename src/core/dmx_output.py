"""DMX output over a USB serial (RS-485) dongle.

Uses the Open DMX technique: 250000 baud, 8 data bits, no parity, 2 stop bits.
Each frame = serial break + mark-after-break + start code 0x00 + 512 channels,
sent continuously at ~40 fps. The DMX stream keeps running (holding the last
frame) even when Art-Net input pauses — fixtures need a continuous signal.

Auto-reconnects if the dongle is unplugged and replugged.
"""

import threading
import time

import serial

from src.utils.logger import get_logger

log = get_logger(__name__)

DMX_BAUD = 250000
DMX_CHANNELS = 512
RECONNECT_DELAY = 1.0


class DmxOutput:
    """Background serial sender. get_frame() must return 512 bytes."""

    def __init__(self, port_name, get_frame, fps=40):
        self._port_name = port_name
        self._get_frame = get_frame
        self._period = 1.0 / fps
        self._ser = None
        self._thread = None
        self._running = False
        # Stats (read by engine/GUI; single-writer, atomic assignments only)
        self.connected = False
        self.frames_total = 0
        self.last_error = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="DmxOutput",
                                        daemon=True)
        self._thread.start()
        log.info("DMX output starting on %s", self._port_name)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._close()
        log.info("DMX output stopped")

    def _loop(self):
        while self._running:
            if self._ser is None and not self._open():
                time.sleep(RECONNECT_DELAY)
                continue
            frame_start = time.monotonic()
            try:
                self._send_frame(self._get_frame())
                self.frames_total += 1
            except (serial.SerialException, OSError) as e:
                self.connected = False
                self.last_error = str(e)
                log.warning("DMX serial error on %s: %s — reconnecting",
                            self._port_name, e)
                self._close()
                continue
            remaining = self._period - (time.monotonic() - frame_start)
            if remaining > 0:
                time.sleep(remaining)

    def _open(self):
        try:
            self._ser = serial.Serial(
                port=self._port_name,
                baudrate=DMX_BAUD,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_TWO,
                timeout=1,
                write_timeout=1,
            )
        except (serial.SerialException, OSError, ValueError) as e:
            self.connected = False
            self.last_error = str(e)
            return False
        self.connected = True
        self.last_error = None
        log.info("Serial port %s opened at %d baud", self._port_name, DMX_BAUD)
        return True

    def _close(self):
        if self._ser:
            try:
                self._ser.close()
            except (serial.SerialException, OSError):
                pass
            self._ser = None
        self.connected = False

    def _send_frame(self, channels):
        # Break: DMX spec minimum is 88 µs; Windows sleep granularity makes
        # this ~1-15 ms, which receivers accept (there is no practical maximum).
        self._ser.break_condition = True
        time.sleep(0.001)
        self._ser.break_condition = False
        time.sleep(0.0001)  # mark-after-break
        self._ser.write(b"\x00" + bytes(channels))  # start code + 512 channels
        self._ser.flush()
