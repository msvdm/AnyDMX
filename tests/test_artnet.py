"""Tests for Art-Net packet parsing, ArtPollReply building, and node announce."""

import socket
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import artnet_receiver
from src.core.artnet_receiver import (
    ARTNET_HEADER, ARTNET_PORT, BROADCAST_ADDR, MAC_BYTES, OP_POLL_REPLY,
    ArtNetReceiver, build_poll_reply, list_local_ipv4, parse_packet,
)
from tools.artnet_test_sender import build_artdmx


def test_parse_valid_artdmx():
    channels = list(range(256)) * 2
    packet = build_artdmx(universe=0, channels=channels, sequence=1)
    result = parse_packet(packet)
    assert result is not None
    kind, universe, data = result
    assert kind == "dmx"
    assert universe == 0
    assert len(data) == 512
    assert data == bytes(channels)


def test_parse_universe_encoding():
    # Universe 258 = Net 1 (high byte), SubUni 2 (low byte)
    packet = build_artdmx(universe=258, channels=[10, 20, 30], sequence=1)
    kind, universe, data = parse_packet(packet)
    assert universe == 258
    assert data == bytes([10, 20, 30])


def test_parse_rejects_wrong_header():
    packet = bytearray(build_artdmx(0, [1, 2, 3], 1))
    packet[:8] = b"NotArt\x00\x00"
    assert parse_packet(bytes(packet)) is None


def test_parse_rejects_short_packet():
    assert parse_packet(b"Art-Net\x00") is None
    assert parse_packet(b"") is None


def test_parse_rejects_unknown_opcode():
    packet = bytearray(build_artdmx(0, [1], 1))
    packet[8:10] = struct.pack("<H", 0x9999)
    assert parse_packet(bytes(packet)) is None


def test_parse_respects_length_field():
    channels = [255] * 100
    packet = build_artdmx(universe=0, channels=channels, sequence=1)
    kind, universe, data = parse_packet(packet)
    assert len(data) == 100


def test_parse_poll():
    poll = b"Art-Net\x00" + struct.pack("<H", 0x2000) + bytes([0, 14, 0, 0])
    assert parse_packet(poll) == ("poll",)


def test_poll_reply_structure():
    reply = build_poll_reply("192.168.1.10", universe=0)
    assert len(reply) == 239
    assert reply.startswith(ARTNET_HEADER)
    assert struct.unpack("<H", reply[8:10])[0] == OP_POLL_REPLY
    assert reply[10:14] == bytes([192, 168, 1, 10])
    assert b"AnyDMX" in reply


def test_poll_reply_universe_switches():
    # Port address 0x1234: Net 0x12, Subnet 0x3, Universe 0x4
    reply = build_poll_reply("10.0.0.1", universe=0x1234)
    assert reply[18] == 0x12      # NetSwitch
    assert reply[19] == 0x03      # SubSwitch
    assert reply[190] == 0x04     # SwOut[0]


def test_poll_reply_handles_bad_ip():
    reply = build_poll_reply("not-an-ip", universe=0)
    assert len(reply) == 239
    assert reply[10:14] == bytes(4)


def test_poll_reply_contains_mac():
    reply = build_poll_reply("192.168.1.10", universe=0)
    assert reply[201:207] == MAC_BYTES
    assert reply[201:207] != bytes(6)


def _only_hostname(monkeypatch, ips):
    """Force the hostname path and make it return exactly these addresses."""
    fake = [(None, None, None, None, (ip, 0)) for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: fake)
    monkeypatch.setattr(artnet_receiver, "_ipv4_from_ip_command", lambda: [])


def test_list_local_ipv4():
    ips = list_local_ipv4()
    assert isinstance(ips, list)
    for ip in ips:
        parts = ip.split(".")
        assert len(parts) == 4 and all(p.isdigit() for p in parts)


def test_list_local_ipv4_excludes_loopback(monkeypatch):
    """The selector must never offer an address nothing can be sent to.

    On Debian and Ubuntu the hostname resolves to 127.0.1.1, which is how this
    returned nothing but loopback on Linux. _bind_addresses() adds 127.0.0.1
    itself, so loopback has no business in this list on any platform.
    """
    _only_hostname(monkeypatch, ["127.0.1.1", "192.168.8.76", "127.0.0.1"])
    assert list_local_ipv4() == ["192.168.8.76"]


def test_list_local_ipv4_sorts_link_local_last(monkeypatch):
    _only_hostname(monkeypatch,
                   ["169.254.83.107", "192.168.8.76", "192.168.31.248"])
    assert list_local_ipv4() == ["192.168.8.76", "192.168.31.248", "169.254.83.107"]


def test_list_local_ipv4_reads_iproute2_off_windows(monkeypatch):
    """The Linux path parses `ip -4 -o addr show`, not the hostname."""
    monkeypatch.setattr(artnet_receiver, "IS_WINDOWS", False)
    monkeypatch.setattr(artnet_receiver.subprocess, "run",
                        lambda *a, **k: _Completed(IP_ADDR_OUTPUT))
    assert list_local_ipv4() == ["192.168.100.126", "2.100.100.0"]


def test_list_local_ipv4_falls_back_when_ip_is_missing(monkeypatch):
    """No iproute2 (or it failed): the hostname lookup is better than nothing."""
    monkeypatch.setattr(artnet_receiver, "IS_WINDOWS", False)
    monkeypatch.setattr(artnet_receiver.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no ip")))
    fake = [(None, None, None, None, ("192.168.8.76", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: fake)
    assert list_local_ipv4() == ["192.168.8.76"]


IP_ADDR_OUTPUT = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
    "2: ens2    inet 192.168.100.126/24 brd 192.168.100.255 scope global "
    "dynamic noprefixroute ens2\\       valid_lft 3502sec\n"
    "3: AnyDMX    inet 2.100.100.0/8 brd 2.255.255.255 scope global "
    "noprefixroute AnyDMX\\       valid_lft forever\n"
)


SS_OUTPUT = (
    "State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process\n"
    "UNCONN 0      0            0.0.0.0:6454       0.0.0.0:*    "
    'users:(("Freestyler",pid=4312,fd=9))\n'
    "UNCONN 0      0          127.0.0.53%lo:53    0.0.0.0:*     \n"
)


class _Completed:
    """Just enough of subprocess.CompletedProcess for the parsers."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_port_owner_names_the_holder_on_linux(monkeypatch):
    monkeypatch.setattr(artnet_receiver, "IS_WINDOWS", False)
    monkeypatch.setattr(artnet_receiver.subprocess, "run",
                        lambda *a, **k: _Completed(SS_OUTPUT))
    assert artnet_receiver.port_owner(6454) == "Freestyler (PID 4312)"


def test_port_owner_reports_a_port_it_cannot_attribute(monkeypatch):
    """ss only names this user's processes. Held-but-unnamed still beats None.

    Returning None here would leave the bind failure unexplained, which is
    the exact silence this function exists to break.
    """
    held = ("UNCONN 0      0            0.0.0.0:6454       0.0.0.0:*     \n")
    monkeypatch.setattr(artnet_receiver, "IS_WINDOWS", False)
    monkeypatch.setattr(artnet_receiver.subprocess, "run",
                        lambda *a, **k: _Completed(held))
    owner = artnet_receiver.port_owner(6454)
    assert owner is not None and "another user" in owner


def test_port_owner_is_none_when_the_port_is_free(monkeypatch):
    monkeypatch.setattr(artnet_receiver, "IS_WINDOWS", False)
    monkeypatch.setattr(artnet_receiver.subprocess, "run",
                        lambda *a, **k: _Completed(SS_OUTPUT))
    assert artnet_receiver.port_owner(9999) is None


def test_advertise_ip_skips_link_local(monkeypatch):
    r = ArtNetReceiver(0, lambda c: None)
    monkeypatch.setattr(ArtNetReceiver, "_local_ip_for",
                        staticmethod(lambda dest: "169.254.83.107"))
    monkeypatch.setattr(artnet_receiver, "list_local_ipv4",
                        lambda: ["192.168.8.76", "169.254.83.107"])
    assert r._pick_advertise_ip() == "192.168.8.76"


def test_advertise_ip_uses_bound_interface():
    r = ArtNetReceiver(0, lambda c: None, bind_ip="192.168.31.248")
    assert r._pick_advertise_ip() == "192.168.31.248"


class FakeSock:
    """Stands in for a bound UDP socket; records what the receiver sends."""

    def __init__(self, fd=1):
        self.sent = []
        self._fd = fd

    def fileno(self):
        return self._fd

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, _size):
        raise socket.timeout

    def close(self):
        pass


def _make_receiver(sock_count=1, universe=0, on_dmx=None):
    r = ArtNetReceiver(universe, on_dmx or (lambda c: None))
    socks = [FakeSock(fd) for fd in range(1, sock_count + 1)]
    r._socks = socks
    r._labels = {s.fileno(): f"10.0.0.{s.fileno()}" for s in socks}
    r._announce_sock = socks[0]
    r._advertise_ip = "192.168.1.10"
    return r, socks


def _run_loop(passes):
    """Drive _loop for N passes with no readable sockets."""
    r, socks = _make_receiver()
    r._running = True
    remaining = {"n": passes}

    def wait(_timeout):
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            r._running = False
        return []

    r._wait_readable = wait
    r._loop()
    return socks[0]


def test_announces_on_start():
    sock = _run_loop(passes=3)  # default interval: only the start announce fires
    assert len(sock.sent) == 1
    data, addr = sock.sent[0]
    assert addr == (BROADCAST_ADDR, ARTNET_PORT)
    assert struct.unpack("<H", data[8:10])[0] == OP_POLL_REPLY
    assert data[10:14] == bytes([192, 168, 1, 10])


def test_announces_periodically(monkeypatch):
    monkeypatch.setattr(artnet_receiver, "ANNOUNCE_INTERVAL", 0.0)
    sock = _run_loop(passes=3)  # zero interval: one announce per loop pass
    assert len(sock.sent) == 3
    assert all(addr == (BROADCAST_ADDR, ARTNET_PORT) for _, addr in sock.sent)


# ----------------------------------------------------------- socket binding

def test_bind_addresses_cover_wildcard_loopback_and_every_nic(monkeypatch):
    monkeypatch.setattr(artnet_receiver, "list_local_ipv4",
                        lambda: ["192.168.8.76", "192.168.31.248"])
    r = ArtNetReceiver(0, lambda c: None)
    assert r._bind_addresses() == ["0.0.0.0", "192.168.8.76",
                                   "192.168.31.248", "127.0.0.1"]


def test_bind_addresses_respect_a_pinned_interface():
    r = ArtNetReceiver(0, lambda c: None, bind_ip="192.168.31.248")
    assert r._bind_addresses() == ["192.168.31.248"]


def test_bind_failure_names_the_process_holding_the_port(monkeypatch):
    # 203.0.113.1 (TEST-NET-3) is not a local address, so bind genuinely fails.
    monkeypatch.setattr(ArtNetReceiver, "_bind_addresses",
                        lambda self: ["203.0.113.1"])
    monkeypatch.setattr(artnet_receiver, "port_owner",
                        lambda port: "somelightingapp.exe (PID 4242)")
    r = ArtNetReceiver(0, lambda c: None)
    with pytest.raises(OSError) as excinfo:
        r._bind_sockets()
    assert "somelightingapp.exe (PID 4242)" in str(excinfo.value)


# ------------------------------------------------- broadcast de-duplication

def test_one_broadcast_reaching_several_sockets_counts_once():
    received = []
    r, socks = _make_receiver(sock_count=3, on_dmx=received.append)
    packet = build_artdmx(universe=0, channels=[7] * 512, sequence=1)
    for sock in socks:  # same datagram, copied to every matching socket
        r._handle(packet, ("192.168.31.5", 6454), r._labels[sock.fileno()])
    assert len(received) == 1
    assert r.packets_total == 1


def test_repeat_on_the_same_socket_is_a_new_frame():
    """A console holding a static look sends identical payloads — keep them all."""
    received = []
    r, socks = _make_receiver(sock_count=2, on_dmx=received.append)
    packet = build_artdmx(universe=0, channels=[7] * 512, sequence=1)
    label = r._labels[socks[0].fileno()]
    r._handle(packet, ("192.168.31.5", 6454), label)
    r._handle(packet, ("192.168.31.5", 6454), label)
    assert len(received) == 2
    assert r.packets_total == 2


def test_same_payload_from_different_sources_is_not_a_duplicate():
    received = []
    r, socks = _make_receiver(sock_count=2, on_dmx=received.append)
    packet = build_artdmx(universe=0, channels=[7] * 512, sequence=1)
    label = r._labels[socks[0].fileno()]
    r._handle(packet, ("192.168.31.5", 6454), label)
    r._handle(packet, ("192.168.8.9", 6454), label)
    assert len(received) == 2


# ------------------------------------------------------- universe discovery

def test_unselected_universes_are_recorded_but_not_forwarded():
    received = []
    r, socks = _make_receiver(universe=0, on_dmx=received.append)
    label = r._labels[socks[0].fileno()]
    r._handle(build_artdmx(universe=5, channels=[1] * 512, sequence=1),
              ("192.168.31.5", 6454), label)
    assert received == []            # not our universe — nothing forwarded
    seen = r.get_universes()
    assert set(seen) == {5}          # ...but we know the console is sending it
    assert seen[5]["packets"] == 1
    assert seen[5]["src"] == "192.168.31.5"


def test_universe_map_tracks_several_universes_and_counts():
    r, socks = _make_receiver(universe=1)
    label = r._labels[socks[0].fileno()]
    for universe in (1, 1, 4):
        r._handle(build_artdmx(universe, [2] * 512, sequence=1),
                  ("10.1.1.1", 6454), label)
    seen = r.get_universes()
    assert seen[1]["packets"] == 2
    assert seen[4]["packets"] == 1


def test_get_universes_returns_a_snapshot_not_live_state():
    r, socks = _make_receiver(universe=0)
    label = r._labels[socks[0].fileno()]
    r._handle(build_artdmx(0, [3] * 512, sequence=1), ("10.1.1.1", 6454), label)
    snapshot = r.get_universes()
    snapshot[0]["packets"] = 999
    assert r.get_universes()[0]["packets"] == 1


# ------------------------------------------------------------ frame length

def test_frame_length_follows_the_artdmx_length_field():
    r, socks = _make_receiver(universe=0)
    label = r._labels[socks[0].fileno()]
    r._handle(build_artdmx(0, [7] * 512, sequence=1), ("10.1.1.1", 6454), label)
    assert r.last_frame_len == 512
    r._handle(build_artdmx(0, [7] * 128, sequence=2), ("10.1.1.1", 6454), label)
    assert r.last_frame_len == 128   # the GUI can now say "128 ch", not "512"


def test_frame_length_ignores_other_universes():
    r, socks = _make_receiver(universe=0)
    label = r._labels[socks[0].fileno()]
    r._handle(build_artdmx(0, [7] * 512, sequence=1), ("10.1.1.1", 6454), label)
    r._handle(build_artdmx(5, [7] * 24, sequence=2), ("10.1.1.1", 6454), label)
    assert r.last_frame_len == 512
