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


def test_list_local_ipv4():
    ips = list_local_ipv4()
    assert isinstance(ips, list)
    for ip in ips:
        parts = ip.split(".")
        assert len(parts) == 4 and all(p.isdigit() for p in parts)


def test_list_local_ipv4_sorts_link_local_last(monkeypatch):
    fake = [(None, None, None, None, (ip, 0))
            for ip in ("169.254.83.107", "192.168.8.76", "192.168.31.248")]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: fake)
    assert list_local_ipv4() == ["192.168.8.76", "192.168.31.248", "169.254.83.107"]


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
