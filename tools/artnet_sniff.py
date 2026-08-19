"""Diagnostic listener — what is actually on the wire?

Answers, without touching AnyDMX itself:
  * is any app on this PC emitting Art-Net (or sACN) at all?
  * which address is it aimed at — broadcast, subnet broadcast, or unicast?
  * which universes, from which source, at what rate?

Run it, then switch on Art-Net output in your lighting app and watch.

    python tools/artnet_sniff.py
    python tools/artnet_sniff.py --seconds 30

Windows has no recvmsg/IP_PKTINFO, so a datagram's destination address cannot
be read directly. It is inferred instead: one socket is bound per local IPv4
plus 127.0.0.1 plus the wildcard, and Windows delivers unicast to exactly one
(most specific) socket while copying broadcast to every matching one. Counting
which sockets saw the same datagram therefore reveals how it was addressed.
"""

import argparse
import hashlib
import select
import socket
import struct
import sys
import time

ARTNET_PORT = 6454
SACN_PORT = 5568
ARTNET_HEADER = b"Art-Net\x00"
OP_NAMES = {0x2000: "ArtPoll", 0x2100: "ArtPollReply", 0x5000: "ArtDMX",
            0x5100: "ArtNzs", 0x5200: "ArtSync"}

WILDCARD = "0.0.0.0"
DEDUP_WINDOW = 0.03  # s — same payload from same source inside this = one datagram
ARTNET_RANGES = ("2.", "10.")  # addresses an auto-picking console will accept


def local_ipv4s():
    """Every IPv4 this machine holds, loopback included."""
    ips = ["127.0.0.1"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def open_sockets(ips, sacn_universes):
    """One UDP socket per (address, port). Returns [(label, port, sock)]."""
    socks = []
    for port in (ARTNET_PORT, SACN_PORT):
        for ip in [WILDCARD] + ips:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                s.bind((ip, port))
            except OSError as e:
                print(f"  ! cannot bind {ip}:{port} - {e}")
                s.close()
                continue
            if port == SACN_PORT and ip not in (WILDCARD, "127.0.0.1"):
                _join_sacn(s, ip, sacn_universes)
            socks.append((ip, port, s))
    return socks


def _join_sacn(sock, iface_ip, universes):
    """sACN data is multicast to 239.255.<hi>.<lo>; join a workable range."""
    for uni in universes:
        group = f"239.255.{(uni >> 8) & 0xFF}.{uni & 0xFF}"
        mreq = socket.inet_aton(group) + socket.inet_aton(iface_ip)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            pass  # interface may not support multicast; unicast sACN still lands


def describe(data):
    """(protocol, opcode_name, universe, channel_count) or None if unrecognised."""
    if data.startswith(ARTNET_HEADER) and len(data) >= 10:
        op = struct.unpack("<H", data[8:10])[0]
        name = OP_NAMES.get(op, f"op 0x{op:04X}")
        if op == 0x5000 and len(data) >= 18:
            universe = (data[15] << 8) | data[14]
            length = (data[16] << 8) | data[17]
            return ("Art-Net", name, universe, length)
        return ("Art-Net", name, None, None)
    # sACN E1.31: ACN packet identifier sits at offset 4 of the root layer
    if len(data) >= 126 and data[4:16] == b"ASC-E1.17\x00\x00\x00":
        universe = struct.unpack(">H", data[113:115])[0]
        length = max(struct.unpack(">H", data[123:125])[0] - 1, 0)
        return ("sACN", "E1.31 Data", universe, length)
    return None


def classify(labels, ip_count):
    """Turn the set of receiving sockets into how the datagram was addressed."""
    ifaces = sorted(labels - {WILDCARD})
    if not ifaces:
        return "unicast to an address no interface socket covers"
    if len(ifaces) >= ip_count:
        return "BROADCAST 255.255.255.255 (all interfaces)"
    if WILDCARD in labels:
        return f"broadcast on subnet of {', '.join(ifaces)}"
    return f"unicast to {', '.join(ifaces)}"


class Stream:
    """Running totals for one (source, protocol, opcode, universe) combination."""

    __slots__ = ("packets", "first", "last", "addressing", "length")

    def __init__(self, ts):
        self.packets = 0
        self.first = self.last = ts
        self.addressing = "?"
        self.length = None


def main():
    ap = argparse.ArgumentParser(description="Art-Net / sACN diagnostic listener")
    ap.add_argument("--seconds", type=float, default=0,
                    help="stop after N seconds (default: run until Ctrl+C)")
    ap.add_argument("--sacn-universes", type=int, default=16,
                    help="how many sACN multicast groups to join (default 16)")
    args = ap.parse_args()

    ips = local_ipv4s()
    print("AnyDMX packet sniffer")
    print(f"  local IPv4: {', '.join(ips)}")
    if not [ip for ip in ips if ip.startswith(ARTNET_RANGES)]:
        print("  ! No 2.x or 10.x address on this PC. A console that picks its")
        print("    own Art-Net interface (dot2 does) has nothing to bind to and")
        print("    will transmit nothing at all. Create the lighting interface")
        print("    in AnyDMX, then restart the console.")
    socks = open_sockets(ips, range(1, args.sacn_universes + 1))
    if not socks:
        print("  ! no sockets could be bound - is another app holding these ports?")
        return 1
    print(f"  listening on {len(socks)} sockets "
          f"(UDP {ARTNET_PORT} Art-Net, UDP {SACN_PORT} sACN)")
    print("\n  Now enable Art-Net (or sACN) output in your lighting app.")
    print("  Ctrl+C to stop.\n")

    by_sock = {s.fileno(): ip for ip, _, s in socks}
    port_of = {s.fileno(): port for _, port, s in socks}
    pending = {}   # (src, port, digest) -> [ts, {labels}, payload]
    streams = {}   # (src, proto, opname, universe) -> Stream
    ip_count = len(ips)
    deadline = time.monotonic() + args.seconds if args.seconds else None
    last_report = time.monotonic()
    all_socks = [s for _, _, s in socks]

    try:
        while True:
            if deadline and time.monotonic() >= deadline:
                break
            ready, _, _ = select.select(all_socks, [], [], 0.2)
            for sock in ready:
                fd = sock.fileno()
                ip, port = by_sock[fd], port_of[fd]
                try:
                    data, addr = sock.recvfrom(2048)
                except OSError:
                    continue
                key = (addr[0], port, hashlib.blake2b(data, digest_size=8).digest())
                now = time.monotonic()
                entry = pending.get(key)
                # the same socket seeing it twice means a genuinely new datagram
                if entry and (now - entry[0] > DEDUP_WINDOW or ip in entry[1]):
                    _commit_keyed(key, pending.pop(key), streams, ip_count)
                    entry = None
                if entry is None:
                    entry = pending[key] = [now, set(), data]
                entry[1].add(ip)

            now = time.monotonic()
            for key in [k for k, v in pending.items() if now - v[0] > DEDUP_WINDOW]:
                _commit_keyed(key, pending.pop(key), streams, ip_count)

            if now - last_report >= 1.0:
                _report(streams)
                last_report = now
    except KeyboardInterrupt:
        pass
    finally:
        for s in all_socks:
            s.close()

    for key, entry in list(pending.items()):
        _commit_keyed(key, entry, streams, ip_count)
    print("\n=== final ===")
    _report(streams, final=True)
    return 0


def _commit_keyed(key, entry, streams, ip_count):
    ts, labels, data = entry
    info = describe(data)
    if info is None:
        return
    src = key[0]
    proto, opname, universe, length = info
    stream_key = (src, proto, opname, universe)
    st = streams.get(stream_key)
    if st is None:
        st = streams[stream_key] = Stream(ts)
    st.packets += 1
    st.last = ts
    st.length = length
    st.addressing = classify(labels, ip_count)


def _report(streams, final=False):
    if not streams:
        if not final:
            return
        print("  NOTHING RECEIVED.")
        print("  No Art-Net or sACN reached this PC at all. The lighting app is")
        print("  not transmitting - check that its DMX output is enabled and")
        print("  bound to a real network adapter, not 0.0.0.0.")
        return
    now = time.monotonic()
    print(f"--- {len(streams)} stream(s) ---")
    for (src, proto, opname, universe), st in sorted(
            streams.items(), key=lambda kv: str(kv[0])):
        span = max(st.last - st.first, 1e-6)
        rate = st.packets / span if st.packets > 1 else 0.0
        stale = "" if now - st.last < 2.0 else "  (stopped)"
        uni = "-" if universe is None else str(universe)
        chans = "" if st.length is None else f"len={st.length}"
        tag = "  <- LOCAL TEST SENDER" if src == "127.0.0.1" else ""
        print(f"  {proto:<8} {opname:<13} universe={uni:<6} {chans:<10}"
              f" src={str(src):<15} {rate:6.1f} pkt/s"
              f"  [{st.addressing}]{tag}{stale}")


if __name__ == "__main__":
    sys.exit(main())
