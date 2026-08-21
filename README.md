# AnyDMX

**Art-Net in → DMX out over a cheap USB-RS485 dongle, plus a live view of what
is actually on the wire. One app, one window. Free, MIT-licensed, no account,
no telemetry.**

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

**It is also useful with no dongle attached at all.** Leave the output on
*Monitor mode* and AnyDMX becomes a diagnostic window: every universe arriving,
who is sending it, at what rate, and all 512 live levels. When a rig is dark
and nobody can say whether the console is even transmitting, that is usually
the question worth answering first.

## Who made this, and why

This is a hobby project, and it is worth being straight about where it came
from.

Marian Raynov is a sound engineer, not a software developer. He had a small,
annoying problem: getting Art-Net out of a lighting console and into a cheap
USB DMX dongle meant running a chain of a loopback adapter plus somebody else's
bridge app, and it was fragile in ways that were hard to see. So AnyDMX exists
to collapse that chain into one window that also *explains itself* when it
isn't working.

The code was written by **Claude** (Anthropic's Opus model) working with him,
session by session. He brings the problem, the console, the dongle, the
fixtures and every real-world test; Claude writes and refactors the Python.
That's not a disclaimer — it's just how it was built, and saying so seems more
useful than letting people wonder about the commit history.

One consequence of that worth knowing: the decisions that cost a real debugging
session are written down in [CLAUDE.md](CLAUDE.md) as facts with their
reasoning, so they don't get "simplified" back into bugs later. If you want to
know *why* something is the way it is — especially the frame timing — that file
is the honest answer, not this one.

## Requirements

- Windows, Python 3.10+
- `pip install -r requirements.txt` (PySide6, pyserial)
- A USB-RS485 / Open DMX-style dongle (FTDI chip recommended, CH340 usually
  works) — or nothing at all, if you only want to monitor

## Run

```
python AnyDMX.py
```

The bridge starts by itself — there is nothing to press. The window draws its
own title bar (**DMX bridge**, with the rescan ⟳ beside the app mark on the
left and the window buttons on the right), so it is dark on every desktop
instead of wearing the system's. The window is the signal path: **INPUT** on
the left, **OUTPUT** on the right, and one line along the bottom saying what is
happening right now.

1. **Input Port** — leave it on "All interfaces", or pick a specific IP on
   multi-NIC machines (e.g. a dedicated lighting LAN). That IP is what the node
   advertises to consoles.
2. **Output Device** — the COM port of your dongle (press ⟳ after plugging it
   in). Leave it on "Monitor mode" to watch Art-Net with no hardware attached.
3. **Universe** — or just click one in the list of universes actually arriving,
   with its source and rate, so you do not have to know the number in advance.
   It sits under the Input Port in the compact window and moves to the bottom
   strip when the channel grid is open.
4. Green indicators = packets arriving + DMX streaming. Changing any selection
   re-arms the bridge immediately.
5. **▼ DMX values** opens the 512-channel grid; closed, the window is small
   enough to leave running beside the console. Each state keeps the size you
   last gave it, so the arrow toggles between your two windows.

The channel grid shows all 512 levels live. A frame that is being *held*
because input stopped is labelled as held, and channels above a short frame's
length are drawn muted rather than cleared — levels nobody is sending must
never look identical to live ones. Traffic from `127.0.0.1` is labelled as a
local test sender, so a simulator can never be mistaken for a real console.

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
**AnyDMX** holding `2.100.100.0/8` (both editable). Press **Create Interface**
in the INPUT panel and set it up in the pop-up; the adapter then appears in the
Input Port list. It replaces the old routine of installing a loopback
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
output path only, and says nothing about your console. A hidden simulator
once made a broken bridge look finished here, which is why it announces
itself and why the GUI labels its traffic.

## Tests

```
python -m pytest
```

The suite is mockable end to end — no real COM port, network or adapter is
ever touched by it. Please keep it that way in any patch.

## About the frame rate

AnyDMX streams at roughly **34 fps**, and that is a deliberate choice rather
than a limitation waiting to be fixed.

A full DMX frame is 513 slots × 11 bits at 250000 baud = 22.6 ms of wire time,
so with break and mark-after-break the physical ceiling is about 42 fps. The
trap is that `flush()` empties the *driver* buffer, not the USB chip's FIFO,
while `break_condition` acts on the UART immediately and out of band. Assert
the break on a fixed schedule and it lands while the previous frame is still
draining, corrupting its tail — the symptom is every fixture on the line
twitching at once, intermittently, while the channel grid sits perfectly
still.

That is why there is no "output rate" dropdown here. AnyDMX waits for the
frame to drain and holds a minimum period instead. If you have used a bridge
with a fixed fps setting where only one value looks stable, this is what you
were fighting.

## Status

Verified on real hardware: the virtual adapter is created on real Windows,
dot2 picks it up and transmits, Art-Net is captured, and DMX drives real
fixtures steadily at 33-35 fps.

Not yet proven: **Onyx as a source** (it transmits, but its own patch and
interface setup has not been worked through) and the **PyInstaller build**, so
for now this runs from source.

Ideas that may or may not happen — treat these as interests, not promises:
sACN (E1.31) input, multiple universes and outputs, Art-Net sending to
ESP32-style nodes, tray mode and autostart.

**No warranty, and this drives real lighting hardware.** Test it on a bench
before you put it in front of an audience. See [LICENSE](LICENSE).

## Ideas, problems, contributions

All welcome, from anyone, at any level of detail.

- Something doesn't work, or the status line said something confusing? Open an
  [issue](https://github.com/msvdm/AnyDMX/issues). "It just sat there saying X
  and I didn't know what to do" is a genuinely useful bug report here — half
  the point of this app is explaining itself.
- Tested it with a console that isn't dot2 or Onyx? Please say so either way.
  Working reports are as valuable as broken ones, and there are only two
  consoles in the notes above because there are only two here.
- Got an idea for a feature? Open an issue and describe the problem you're
  hitting, not just the feature — the problem is the part that's hard to guess.
- Pull requests are fine. Run `python -m pytest` first, and have a look at
  [CLAUDE.md](CLAUDE.md) — it records which decisions were expensive to reach,
  so you can tell the load-bearing parts from the arbitrary ones.

There is no roadmap to be behind on and nothing is owed to anyone. It's a
hobby project that solves one problem well.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it, sell it; just keep the
copyright notice.
