# AnyDMX

**Art-Net in → DMX out over a cheap USB-RS485 dongle. One app, one window.**

AnyDMX presents itself on the network as a real Art-Net node (consoles discover
it via ArtPoll), receives ArtDMX for a selected universe, and streams DMX512
out any FTDI/CH340-based USB serial adapter using the Open DMX technique.

It replaces chains like:

```
Console → Art-Net → bridge app → loopback adapter → Open DMX driver → fixtures
```

with:

```
Console → Art-Net → AnyDMX → USB dongle → fixtures
```

No loopback adapter needed — AnyDMX binds UDP 6454 on all interfaces, so a
console on the same PC (dot2 onPC, etc.) can unicast straight to the PC's own
IP, and consoles on the network can broadcast normally.

## Requirements

- Windows, Python 3.10+
- `pip install -r requirements.txt` (PySide6, pyserial)
- A USB-RS485 / Open DMX-style dongle (FTDI chip recommended, CH340 usually works)

## Run

```
python AnyDMX.py
```

1. Pick the COM port of your dongle (press ⟳ after plugging it in).
2. Leave **Network** on "All interfaces", or pick a specific IP on multi-NIC
   machines (e.g. a dedicated lighting LAN) — that IP is what the node
   advertises to consoles.
3. Press **Start**. Green indicators = packets arriving + DMX streaming.
4. The **"Art-Net seen on this PC"** strip lists every universe actually
   arriving, with its source IP and rate. Click one to listen to it — you do
   not have to know the universe number in advance. The spinner is still there
   for manual override.

The channel grid shows all 512 levels live. Traffic from `127.0.0.1` is
labelled as a local test sender, so a simulator can never be mistaken for a
real console.

## Console notes

- **dot2 / dot2 onPC** never lists third-party Art-Net nodes — its network
  view shows MA hardware only. That's normal: just enable Art-Net output in
  dot2 and it broadcasts to the network; AnyDMX picks it up.
- **Onyx** discovers AnyDMX via ArtPoll and lists it under
  EtherDMX → Devices, then may switch the universe to unicast.
- **Console on the same PC as AnyDMX:** this works — Windows copies
  locally-sent broadcast back to other local apps. AnyDMX binds the wildcard
  address *and* every individual NIC address, so unicast Art-Net cannot be
  captured out from under it by another app that bound a specific address
  first.
- **Output must be aimed at a real adapter.** A lighting app whose Art-Net
  interface is set to `0.0.0.0` transmits nothing at all. Pick an actual NIC
  IP in the console's network setup.

## The lighting interface

Consoles that pick their own Art-Net interface — dot2 among them — only ever
work on the Art-Net `2.x.x.x` range. With no such address on the PC they select
nothing, display `0.0.0.0`, and transmit not one packet. There is then nothing
to capture, however well AnyDMX listens.

So AnyDMX can create the landing spot itself: a virtual network adapter named
**AnyDMX** holding `2.100.100.0/8` (both editable). Press **Create** in the
Lighting interface row. It replaces the old routine of installing a loopback
adapter by hand and running a separate bridge application.

- **No need to run AnyDMX as administrator.** Creating and removing the
  adapter needs admin rights, so the button asks Windows for permission and
  does that one step in a short-lived elevated helper. Approve the prompt and
  it is done; decline it and nothing changes. Capturing Art-Net and sending
  DMX never need elevation.
- The adapter **persists** across runs — it is infrastructure, not session
  state. Press **Remove** to delete it.
- It uses Windows' own in-box loopback driver (`netloop.inf`, hardware ID
  `*MSLOOP`). Nothing is downloaded or bundled.
- It is given **no default gateway**, so the `2.0.0.0/8` route can never become
  a path for ordinary traffic, and its firewall profile is set to Private.
- **Restart your console after creating it** — lighting apps enumerate network
  interfaces at startup and will not notice a new one otherwise.

Onyx lets you select the interface explicitly, so it needs none of this: point
it at any normal address and AnyDMX will hear it.

## When nothing arrives

Run the sniffer — it listens on every local address for both Art-Net (6454)
and sACN (5568), and reports every stream it sees with the universe, source,
rate, and whether it was broadcast or unicast:

```
python tools/artnet_sniff.py
```

Then switch on your lighting app's output and watch. If the sniffer shows
nothing, the app is not transmitting and no bridge setting will help — the
problem is in the console's network/DMX configuration.

## Test without a console or hardware

```
python tools/artnet_test_sender.py
```

sends an animated test pattern to 127.0.0.1 — the channel grid should
ripple. It prints a loud banner: DMX produced while it runs proves the
output path only, and says nothing about your console.

## Tests

```
python -m pytest
```

## Notes

- DMX keeps streaming (holding the last frame) if Art-Net input pauses —
  fixtures need a continuous signal.
- If the dongle is unplugged, AnyDMX reconnects automatically when it returns.
- Roadmap: sACN (E1.31) input, multiple universes/outputs, Art-Net sending
  (e.g. to ESP32 nodes), tray mode + autostart.

## License

Proprietary — see [LICENSE](LICENSE).
