"""Art-Net receiver.

Listens on UDP 6454, parses ArtDMX packets into a channel buffer callback,
and answers ArtPoll so consoles discover AnyDMX as a real Art-Net node.
The node also broadcasts unsolicited ArtPollReply on start and periodically,
as the spec requires, so consoles and node scanners list it without polling.

It binds one socket per local IPv4 as well as the wildcard address. Windows
and Linux both deliver unicast to the single most specific socket — system-wide,
not just within this process — so a wildcard-only bind loses unicast Art-Net to
any other app that bound a specific address first. Binding at both levels means
nothing can be quietly stolen from us. Broadcast is copied to every matching
socket, so the duplicate copies are filtered out again in _is_duplicate().

Enumerating the local addresses is the part that is genuinely per-platform:
the hostname lookup that is right on Windows returns 127.0.1.1 and nothing else
on Debian-family Linux. See list_local_ipv4().

Every universe seen is counted, not just the selected one, so the GUI can say
"the console is sending universe 5" instead of "no data for this universe".
"""

import re
import select
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid

IS_WINDOWS = sys.platform == "win32"

ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
OP_POLL = 0x2000
OP_POLL_REPLY = 0x2100
OP_DMX = 0x5000

SHORT_NAME = b"AnyDMX"
LONG_NAME = b"AnyDMX Art-Net to USB DMX bridge"

ANNOUNCE_INTERVAL = 2.5  # seconds between unsolicited ArtPollReply broadcasts
BROADCAST_ADDR = "255.255.255.255"
LOOPBACK = "127.0.0.1"
WILDCARD = "0.0.0.0"
DEDUP_WINDOW = 0.02  # s — one broadcast reaching several of our sockets

MAC_BYTES = uuid.getnode().to_bytes(6, "big")


def _ipv4_from_hostname():
    """Every IPv4 the hostname resolves to. The right answer on Windows.

    On Linux it is worse than useless: Debian and Ubuntu map the hostname to
    127.0.1.1 in /etc/hosts, so this returns a loopback address and the real
    NIC never appears at all. Hence the iproute2 path below.
    """
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return [info[4][0] for info in infos]


def _ipv4_from_ip_command():
    """Every IPv4 iproute2 reports. `ip` ships with every modern Linux.

    Lines look like:
        2: ens2    inet 192.168.100.126/24 brd 192.168.100.255 scope global ens2
    """
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], timeout=5,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ips = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet":
            ips.append(parts[3].split("/")[0])
    return ips


def list_local_ipv4():
    """IPv4 addresses of this machine's interfaces (for the GUI NIC selector).

    Loopback is excluded on both platforms. _bind_addresses() adds 127.0.0.1
    deliberately and separately, and 127.0.1.1 — which is what the hostname
    lookup yields on Linux — is not an interface anyone can send Art-Net to.
    Offering either one in the interface selector is offering a dead end.

    APIPA link-local addresses (169.254.x.x, from adapters that never got a
    lease) sort last — they route nowhere, so they must not be picked as the
    advertised node IP while a real network is available.
    """
    if IS_WINDOWS:
        found = _ipv4_from_hostname()
    else:
        found = _ipv4_from_ip_command() or _ipv4_from_hostname()
    ips = []
    for ip in found:
        if ip not in ips and not ip.startswith("127."):
            ips.append(ip)
    ips.sort(key=lambda ip: ip.startswith("169.254."))
    return ips


def port_owner(port):
    """Name the process holding a UDP port, or None. Used only in bind errors.

    A contested 6454 is the classic silent failure here — another Art-Net app
    binds it and this one simply never sees traffic. Naming the culprit turns
    a mystery into a one-line fix.
    """
    return _port_owner_windows(port) if IS_WINDOWS else _port_owner_linux(port)


def _port_owner_windows(port):
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "UDP"], timeout=5,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].upper() == "UDP" \
                and parts[1].rsplit(":", 1)[-1] == str(port):
            pid = parts[-1]
            return f"{_process_name(pid) or 'unknown process'} (PID {pid})"
    return None


def _process_name(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                             timeout=5, capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip().split('","')[0].lstrip('"') or None


# ss prints the holder as: users:(("python3",pid=1234,fd=3))
_SS_PROCESS = re.compile(r'\(\("([^"]+)",pid=(\d+)')


def _port_owner_linux(port):
    """Linux: `ss` from iproute2. netstat is not installed everywhere.

    ss only names processes belonging to this user. A port held by another
    user still answers the question that matters — why the bind failed — so
    say it is held rather than returning None and leaving the failure
    unexplained. Silence here is the bug this function exists to prevent.
    """
    try:
        out = subprocess.run(["ss", "-lunp"], timeout=5,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        # State Recv-Q Send-Q Local:Port Peer:Port [Process]
        if len(parts) < 4 or parts[0].upper() != "UNCONN":
            continue
        if parts[3].rsplit(":", 1)[-1] != str(port):
            continue
        found = _SS_PROCESS.search(line)
        if found:
            return f"{found.group(1)} (PID {found.group(2)})"
        return "a process owned by another user (sudo ss -lunp names it)"
    return None


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
    r += MAC_BYTES                                    # MAC
    r += bytes(4)                                     # BindIp
    r += bytes([0])                                   # BindIndex
    r += bytes([0x08])                                # Status2: 15-bit port addresses
    r += bytes(26)                                    # Filler
    return bytes(r)


class ArtNetReceiver:
    """Background UDP listener. Calls on_dmx(channels) for the selected universe."""

    def __init__(self, universe, on_dmx, bind_ip=WILDCARD):
        self.universe = universe
        self._on_dmx = on_dmx
        self._bind_ip = bind_ip
        self._socks = []
        self._labels = {}  # fileno -> bound address
        self._announce_sock = None
        self._thread = None
        self._running = False
        self._advertise_ip = WILDCARD
        self._last_announce = 0.0
        self._recent = {}  # (src, payload hash) -> (timestamp, {socket labels})
        self._universes = {}
        self._uni_lock = threading.Lock()
        # Stats (read by engine/GUI; single-writer, atomic assignments only)
        self.packets_total = 0
        self.last_frame_len = 0  # channels in the last frame — short frames
                                 # leave everything above this untouched
        self.last_source_ip = None
        self.last_packet_time = 0.0
        self.last_poll_ip = None
        self.last_poll_time = 0.0

    # ------------------------------------------------------------ lifecycle

    def start(self):
        self._socks = self._bind_sockets()
        self._announce_sock = self._pick_announce_sock()
        self._advertise_ip = self._pick_advertise_ip()
        self._last_announce = 0.0  # loop announces immediately on first pass
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="ArtNetReceiver",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        for sock in self._socks:
            try:
                sock.close()
            except OSError:
                pass
        self._socks = []
        self._labels = {}  # fileno -> bound address
        self._announce_sock = None

    def _bind_sockets(self):
        """One socket per address we want to be reachable at.

        Raises OSError naming the offending process if nothing can be bound.
        """
        socks = []
        labels = {}
        errors = []
        for addr in self._bind_addresses():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.bind((addr, ARTNET_PORT))
            except OSError as e:
                sock.close()
                errors.append(f"{addr}: {e}")
                continue
            sock.settimeout(0.5)
            labels[sock.fileno()] = addr
            socks.append(sock)
        if not socks:
            owner = port_owner(ARTNET_PORT)
            detail = f" UDP {ARTNET_PORT} is held by {owner}." if owner else ""
            raise OSError(f"Could not listen for Art-Net.{detail} "
                          f"Tried: {'; '.join(errors)}")
        self._labels = labels
        return socks

    def _bind_addresses(self):
        """Wildcard + every local IPv4, or just the one interface if pinned."""
        if self._bind_ip and self._bind_ip != WILDCARD:
            return [self._bind_ip]
        addrs = [WILDCARD]
        for ip in list_local_ipv4() + [LOOPBACK]:
            if ip not in addrs:
                addrs.append(ip)
        return addrs

    def _pick_announce_sock(self):
        """Socket used to send replies. Prefer the wildcard one for broadcast."""
        for sock in self._socks:
            if self._labels[sock.fileno()] == WILDCARD:
                return sock
        return self._socks[0]

    # ----------------------------------------------------------------- loop

    def _loop(self):
        while self._running:
            now = time.monotonic()
            if now - self._last_announce >= ANNOUNCE_INTERVAL:
                self._announce()
                self._last_announce = now
            for sock in self._wait_readable(0.5):
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    return  # socket closed during stop()
                self._handle(data, addr, self._labels[sock.fileno()])

    def _wait_readable(self, timeout):
        """Sockets with data waiting. Isolated so tests can drive the loop."""
        try:
            ready, _, _ = select.select(self._socks, [], [], timeout)
            return ready
        except (OSError, ValueError):
            return []

    def _handle(self, data, addr, label):
        parsed = parse_packet(data)
        if parsed is None:
            return
        # Every kind of packet is deduplicated the same way, so this is asked
        # once — before deciding what the packet is.
        if self._is_duplicate(data, addr[0], label):
            return
        if parsed[0] == "poll":
            self.last_poll_ip = addr[0]
            self.last_poll_time = time.monotonic()
            self._send_poll_reply(addr)
            return
        _, universe, channels = parsed
        self._note_universe(universe, addr[0])
        if universe != self.universe:
            return
        self.packets_total += 1
        self.last_frame_len = len(channels)
        self.last_source_ip = addr[0]
        self.last_packet_time = time.monotonic()
        self._on_dmx(channels)

    def _is_duplicate(self, data, src, label):
        """True when this is another copy of a datagram already handled.

        A broadcast is delivered to every socket of ours that matches, so the
        same datagram arrives once per socket. The same *socket* seeing an
        identical payload again means a genuinely new frame, not a copy — which
        is what keeps a console holding a static look at 44 fps from being
        thinned out to half rate.
        """
        if len(self._socks) < 2:
            return False
        now = time.monotonic()
        key = (src, hash(data))
        seen_at, labels = self._recent.get(key, (0.0, None))
        if labels is not None and now - seen_at < DEDUP_WINDOW \
                and label not in labels:
            labels.add(label)
            return True
        self._recent[key] = (now, {label})
        if len(self._recent) > 64:
            self._recent = {k: v for k, v in self._recent.items()
                            if now - v[0] < DEDUP_WINDOW}
        return False

    def _note_universe(self, universe, src):
        """Record every universe on the wire, selected or not."""
        with self._uni_lock:
            rec = self._universes.get(universe)
            if rec is None:
                rec = self._universes[universe] = {"packets": 0, "src": src,
                                                   "last_seen": 0.0}
            rec["packets"] += 1
            rec["src"] = src
            rec["last_seen"] = time.monotonic()

    def get_universes(self):
        """Snapshot of {universe: {packets, src, last_seen}} for the GUI."""
        with self._uni_lock:
            return {u: dict(rec) for u, rec in self._universes.items()}

    # ------------------------------------------------------------- announce

    def _send_poll_reply(self, addr):
        reply = build_poll_reply(self._local_ip_for(addr[0]), self.universe)
        try:
            self._announce_sock.sendto(reply, (addr[0], ARTNET_PORT))
        except OSError:
            pass  # best-effort: capture never depends on the reply

    def _announce(self):
        """Broadcast an unsolicited ArtPollReply (spec: on start + periodically)."""
        reply = build_poll_reply(self._advertise_ip, self.universe)
        try:
            self._announce_sock.sendto(reply, (BROADCAST_ADDR, ARTNET_PORT))
        except OSError:
            pass  # best-effort: capture never depends on announcing

    def _pick_advertise_ip(self):
        """IP to put in announce packets: the bound NIC, else the outbound one.

        With no explicit bind, ask the routing table which interface reaches the
        network — that beats guessing on a multi-NIC machine. Only fall back to
        the enumerated list if that lookup gives nothing usable.
        """
        if self._bind_ip and self._bind_ip != WILDCARD:
            return self._bind_ip
        routed = self._local_ip_for(BROADCAST_ADDR)
        if routed and routed != WILDCARD and not routed.startswith("169.254."):
            return routed
        local = list_local_ipv4()
        if local:
            return local[0]
        return routed or WILDCARD

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
            return WILDCARD
