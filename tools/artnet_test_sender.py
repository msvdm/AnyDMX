"""Send test ArtDMX packets — verify AnyDMX without a console or hardware.

Usage (from the project root):
    python tools/artnet_test_sender.py                 # animate to 127.0.0.1
    python tools/artnet_test_sender.py --ip 192.168.1.50 --universe 1
"""

import argparse
import math
import socket
import struct
import sys
import time

ARTNET_PORT = 6454


def build_artdmx(universe, channels, sequence):
    packet = bytearray()
    packet += b"Art-Net\x00"
    packet += struct.pack("<H", 0x5000)          # OpDmx
    packet += bytes([0, 14])                     # protocol version
    packet += bytes([sequence & 0xFF or 1])      # sequence (0 = disabled)
    packet += bytes([0])                         # physical
    packet += bytes([universe & 0xFF])           # SubUni
    packet += bytes([(universe >> 8) & 0x7F])    # Net
    packet += struct.pack(">H", len(channels))   # length (big-endian)
    packet += bytes(channels)
    return bytes(packet)


def main():
    parser = argparse.ArgumentParser(description="AnyDMX test sender")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--universe", type=int, default=0)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Loud on purpose: a simulator mistaken for a real console once made a
    # broken bridge look like a working one. It must never read as hardware.
    print("=" * 68)
    print("  SIMULATED ART-NET — THIS IS NOT YOUR LIGHTING CONSOLE")
    print("  Any DMX output produced while this runs proves only that the")
    print("  output path works. It says nothing about your console.")
    print("=" * 68)
    print(f"Sending animated ArtDMX to {args.ip}:{ARTNET_PORT} "
          f"universe {args.universe} at {args.fps} fps — Ctrl+C to stop")
    sequence = 0
    t0 = time.monotonic()
    try:
        while True:
            t = time.monotonic() - t0
            channels = [
                int(127.5 * (1 + math.sin(t * 2.0 + ch * 0.12)))
                for ch in range(512)
            ]
            sequence = (sequence % 255) + 1
            sock.sendto(build_artdmx(args.universe, channels, sequence),
                        (args.ip, ARTNET_PORT))
            time.sleep(1 / args.fps)
    except KeyboardInterrupt:
        print("simulated Art-Net stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
