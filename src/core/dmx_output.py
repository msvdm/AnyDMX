"""DMX output over a USB serial (RS-485) dongle.

Uses the Open DMX technique: 250000 baud, 8 data bits, no parity, 2 stop bits.
Each frame = serial break + mark-after-break + start code 0x00 + 512 channels.
A full frame occupies 22.6 ms of wire time, so the cadence is capped near 34 fps
and every frame is confirmed sent before the next break — see the timing block
below. The DMX stream keeps running (holding the last frame) even when Art-Net
input pauses — fixtures need a continuous signal.

Auto-reconnects if the dongle is unplugged and replugged.
"""

import errno
import sys
import threading
import time

import serial

IS_LINUX = sys.platform.startswith("linux")

DMX_BAUD = 250000
DMX_CHANNELS = 512
RECONNECT_DELAY = 1.0

# Frame timing. A break is asserted on the UART immediately (SetCommBreak),
# out of band from any data still queued — so if the previous frame has not
# finished leaving the chip, the break lands inside it. Receivers then lose
# frame alignment for one refresh and every fixture on the line twitches at
# once. All of the following exists to make sure that cannot happen.
DMX_SLOTS = DMX_CHANNELS + 1                  # start code + 512 channels
BITS_PER_SLOT = 11                            # 1 start + 8 data + 2 stop
FRAME_TX_TIME = DMX_SLOTS * BITS_PER_SLOT / DMX_BAUD   # 22.6 ms on the wire
BREAK_TIME = 0.001                            # spec minimum is 92 µs
MAB_TIME = 0.0001                             # mark after break, min 12 µs
# flush() empties the driver's buffer, not the USB chip's transmit FIFO. An
# FT232R holds 128 bytes — 5.6 ms of DMX — after flush() has already returned.
DRAIN_MARGIN = 0.006
# Slowest safe cadence: anything faster and the break overruns the frame.
MIN_FRAME_PERIOD = FRAME_TX_TIME + BREAK_TIME + MAB_TIME + DRAIN_MARGIN
DRAIN_TIMEOUT = 0.5                           # give up waiting on a wedged port


def explain_open_error(port_name, exc):
    """Turn a failure to open the port into a sentence that fixes it.

    On Linux /dev/ttyUSB* belongs to root:dialout, and a user who has never
    needed a serial port before is not in that group — so the very first run
    with a real dongle fails with a bare "[Errno 13] Permission denied" that
    says nothing about groups. This is the most likely first-run failure on
    Linux, and the GUI must say what it saw *and* what to do about it.

    pyserial raises SerialException(errno, message), and SerialException
    subclasses OSError, so the errno survives.
    """
    text = str(exc)
    if IS_LINUX and (getattr(exc, "errno", None) == errno.EACCES
                     or "Permission denied" in text):
        return (f"no permission for {port_name} — run: "
                "sudo usermod -aG dialout $USER, then log out and back in")
    return text


class DmxOutput:
    """Background serial sender. get_frame() must return 512 bytes."""

    def __init__(self, port_name, get_frame, fps=40):
        self._port_name = port_name
        self._get_frame = get_frame
        self._period = max(1.0 / fps, MIN_FRAME_PERIOD)
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

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._close()

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
            self.last_error = explain_open_error(self._port_name, e)
            return False
        self.connected = True
        self.last_error = None
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
        time.sleep(BREAK_TIME)
        self._ser.break_condition = False
        time.sleep(MAB_TIME)  # mark-after-break
        self._ser.write(b"\x00" + bytes(channels))  # start code + 512 channels
        self._ser.flush()
        self._wait_drained()

    def _wait_drained(self):
        """Block until the frame has actually left the dongle.

        Without this the next iteration's break can be asserted while the tail
        of this frame is still being clocked out, corrupting it. out_waiting
        covers the driver buffer; DRAIN_MARGIN covers the chip's own FIFO,
        which no API exposes.
        """
        deadline = time.monotonic() + DRAIN_TIMEOUT
        try:
            while self._ser.out_waiting and time.monotonic() < deadline:
                time.sleep(0.001)
        except (serial.SerialException, OSError, AttributeError):
            # Some drivers cannot report it. Never let that kill the sender
            # thread — the margin below still covers the common case.
            pass
        time.sleep(DRAIN_MARGIN)
