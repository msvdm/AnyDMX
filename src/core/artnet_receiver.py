"""Art-Net receiver.

Listens on UDP 6454, parses ArtDMX packets into a channel buffer callback,
and answers ArtPoll so consoles discover AnyDMX as a real Art-Net node.
"""

import socket
import struct
import threading
import time

from src.utils.logger import get_logger

log = get_logger(__name__)

ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
OP_POLL = 0x2000
OP_POLL_REPLY = 0x2100
OP_DMX = 0x5000

SHORT_NAME = b"AnyDMX"
LONG_NAME = b"AnyDMX Art-Net to USB DMX bridge"


def parse_packet(data):
    """Parse a raw UDP payload.

    Returns:
        ("dmx", universe, channel_bytes) for an ArtDMX packet
        ("poll",)                        for an ArtPoll packet
        None                             for anything else or malformed data
    """
    if len(data) < 10 or not data.startswith(ARTNET_HEADER):
        return None
    opcode = struct.unpack("<H", data[8:10])[0]
    if opcode == OP_POLL:
        return ("poll",)
    if opcode != OP_DMX or len(data) < 18:
        return None
    sub_uni = data[14]
    net = data[15]
    universe = (net << 8) | sub_uni
    length = (data[16] << 8) | data[17]
    channels = data[18:18 + length]
    if not channels:
        return None
    return ("dmx", universe, channels)


def build_poll_reply(ip, universe):
    """Build a 239-byte ArtPollReply advertising one DMX output port."""
    try:
        ip_bytes = bytes(int(x) for x in ip.split("."))
    except ValueError:
        ip_bytes = bytes(4)
    r = bytearray()
    r += ARTNET_HEADER                                # ID
    r += struct.pack("<H", OP_POLL_REPLY)             # OpCode
    r += ip_bytes                                     # IP address
    r += struct.pack("<H", ARTNET_PORT)               # Port
    r += bytes([0, 1])                                # VersInfo hi/lo
    r += bytes([(universe >> 8) & 0x7F,               # NetSwitch
                (universe >> 4) & 0x0F])              # SubSwitch
    r += bytes([0x00, 0xFF])                          # Oem (unknown)
    r += bytes([0])                                   # Ubea
    r += bytes([0])                                   # Status1
    r += struct.pack("<H", 0x7FF0)                    # ESTA manufacturer (prototype)
    r += SHORT_NAME.ljust(18, b"\x00")                # ShortName
    r += LONG_NAME.ljust(64, b"\x00")                 # LongName
    r += b"#0001 [ok] AnyDMX online".ljust(64, b"\x00")  # NodeReport
    r += bytes([0, 1])                                # NumPorts hi/lo
    r += bytes([0x80, 0, 0, 0])                       # PortTypes: port 0 can output DMX
    r += bytes([0, 0, 0, 0])                          # GoodInput
    r += bytes([0x80, 0, 0, 0])                       # GoodOutput: transmitting
    r += bytes([0, 0, 0, 0])                          # SwIn
    r += bytes([universe & 0x0F, 0, 0, 0])            # SwOut
    r += bytes(7)                                     # SwVideo/SwMacro/SwRemote/Spare/Style
    r += bytes(6)                                     # MAC
    r += bytes(4)                                     # BindIp
    r += bytes([0])                                   # BindIndex
    r += bytes([0x08])                                # Status2: 15-bit port addresses
    r += bytes(26)                                    # Filler
    return bytes(r)


class ArtNetReceiver:
    """Background UDP listener. Calls on_dmx(channels) for the selected universe."""

    def __init__(self, universe, on_dmx, bind_ip="0.0.0.0"):
        self.universe = universe
        self._on_dmx = on_dmx
        self._bind_ip = bind_ip
        self._sock = None
        self._thread = None
        self._running = False
        # Stats (read by engine/GUI; single-writer, atomic assignments only)
        self.packets_total = 0
        self.last_source_ip = None
        self.last_packet_time = 0.0

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind((self._bind_ip, ARTNET_PORT))
        self._sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="ArtNetReceiver",
                                        daemon=True)
        self._thread.start()
        log.info("Art-Net receiver listening on %s:%d (universe %d)",
                 self._bind_ip, ARTNET_PORT, self.universe)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._sock:
            self._sock.close()
            self._sock = None
        log.info("Art-Net receiver stopped")

    def _loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break  # socket closed during stop()
            parsed = parse_packet(data)
            if parsed is None:
                continue
            if parsed[0] == "poll":
                self._send_poll_reply(addr)
            elif parsed[0] == "dmx":
                _, universe, channels = parsed
                if universe == self.universe:
                    self.packets_total += 1
                    self.last_source_ip = addr[0]
                    self.last_packet_time = time.monotonic()
                    self._on_dmx(channels)

    def _send_poll_reply(self, addr):
        reply = build_poll_reply(self._local_ip_for(addr[0]), self.universe)
        try:
            self._sock.sendto(reply, (addr[0], ARTNET_PORT))
            log.debug("ArtPollReply sent to %s", addr[0])
        except OSError as e:
            log.warning("Failed to send ArtPollReply to %s: %s", addr[0], e)

    @staticmethod
    def _local_ip_for(dest_ip):
        """IP of the local interface that routes to dest_ip."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((dest_ip, ARTNET_PORT))
                return s.getsockname()[0]
            finally:
                s.close()
        except OSError:
            return "0.0.0.0"
