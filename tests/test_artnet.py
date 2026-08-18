"""Tests for Art-Net packet parsing and ArtPollReply building."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.artnet_receiver import (
    ARTNET_HEADER, OP_POLL_REPLY, build_poll_reply, parse_packet,
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
