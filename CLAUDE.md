# AnyDMX — Development Rules

## Core Principles

1. **Keep things simple** — minimal complexity, straightforward solutions
2. **Stability first** — the app must run unattended for hours; auto-reconnect, never crash on I/O errors
3. **Stay organized** — no extra code or files

## What this app is for

Capture Art-Net emitted by *any* lighting app on the same PC and re-emit it as
DMX out a USB serial dongle. It replaces a chain of
`console → loopback adapter → Freestyler's Art-Net-to-Open-DMX bridge → dongle`
with `console → AnyDMX → dongle`.

**Be protocol- and console-flexible.** dot2 and Onyx are what we test with;
neither is the target. Never hard-code assumptions about one console.

## Core Architecture Rule: The Universe Buffer is the Seam

Input protocols and output targets NEVER talk to each other directly.
Everything flows through the engine's 512-byte universe buffer:

```
ArtNetReceiver ──┐                       ┌── DmxOutput (USB serial)
(future: sACN) ──┼─→ Engine buffer[512] ─┼── (future: Art-Net send)
                 │      (lock-guarded)    └── (future: more dongles)
```

- New input protocol = new receiver class that calls `engine._on_dmx(channels)`
- New output = new class that polls `engine.get_channels()`
- Receivers and outputs each run their own daemon thread and must survive
  I/O errors by reconnecting, not by raising

## Tech Stack

- **Python + PySide6** — GUI
- **pyserial** — DMX output (Open DMX technique: 250000 baud 8N2, break + start code + 512 bytes @ ~34 fps)
- **stdlib socket** — Art-Net UDP (port 6454), no external protocol library
- **ctypes + PowerShell** — virtual network adapter. Nothing bundled: the
  loopback driver is in-box and SetupAPI is called directly.

## Key Files

| File | Role |
|---|---|
| `AnyDMX.py` | Entry point; also dispatches `--vnet-helper` elevated mode before Qt loads |
| `src/core/artnet_receiver.py` | Multi-socket UDP listener, ArtDMX parser, universe discovery, ArtPollReply |
| `src/core/dmx_output.py` | Serial DMX sender thread, auto-reconnect |
| `src/core/engine.py` | Universe buffer + status snapshots; wires receiver → output |
| `src/core/ports.py` | COM port enumeration + chip ID (FTDI/CH340/CP210x/Prolific) |
| `src/core/vnet.py` | Virtual "AnyDMX" lighting adapter: create/remove, UAC elevation |
| `src/gui/main_window.py` | Single-window GUI, 100 ms status polling |
| `src/gui/channel_view.py` | Live 512-channel grid (32×16) |
| `src/gui/styles.py` | Color palette + QSS |
| `src/utils/paths.py` | Portable paths (source run vs PyInstaller exe) |
| `tools/artnet_sniff.py` | Diagnostic: what is actually on the wire (Art-Net + sACN) |
| `tools/artnet_test_sender.py` | Hardware-free test: animated ArtDMX to localhost |

## Threading

3 threads: Main (GUI, polls engine at 100 ms) | ArtNetReceiver (UDP recv) |
DmxOutput (serial send loop). Cross-thread data goes ONLY through the
lock-guarded buffer or single-writer atomic stat attributes. No Qt signals
from worker threads.

## DMX timing facts — arithmetic, do not re-litigate

- A full frame is 513 slots × 11 bits at 250000 baud = **22.6 ms of wire time**.
  With break + MAB the physical ceiling is ~42 fps, so a 40 fps target leaves
  only 1.3 ms of slack — not enough, and `DmxOutput` clamps to ~34 fps.
- `flush()` empties the *driver* buffer, not the USB chip's FIFO. An FT232R
  holds 128 bytes = 5.6 ms of DMX after `flush()` returns.
- `break_condition` (SetCommBreak) acts on the UART **immediately**, out of band
  from queued data. Assert it while the previous frame is still draining and its
  tail is corrupted — the symptom is every fixture on the line twitching at once,
  intermittently, while the channel grid sits perfectly still. Hence
  `_wait_drained()` and the `MIN_FRAME_PERIOD` floor.
- Rates shown in the GUI are averaged over `RATE_WINDOW`, not the 100 ms poll:
  3-4 frames per poll means timer jitter alone swings the figure by ±5 fps.

## Windows networking facts — verified by experiment, do not re-litigate

- Windows **loops locally-sent broadcast back to other local sockets**, so an
  app broadcasting Art-Net on this PC can be captured by AnyDMX on the same PC.
  No loopback adapter is needed for *capture*.
- **Unicast goes to the single most specific socket, system-wide.** A
  wildcard-only listener loses unicast to any other app that bound a specific
  address first. Hence `ArtNetReceiver` binds `0.0.0.0` **and** every local
  IPv4 **and** `127.0.0.1`.
- **Broadcast is copied to every matching socket**, so the multi-socket bind
  produces duplicates. `_is_duplicate()` filters them. Its rule matters: the
  same *socket* seeing an identical payload again is a genuinely new frame, not
  a copy — otherwise a console holding a static look at 44 fps gets halved.
- Windows Firewall does not block this loopback path, even on a Public profile.
- `socket.recvmsg`/`IP_PKTINFO` **do not exist on Windows**, so a datagram's
  destination address cannot be read. `tools/artnet_sniff.py` infers it from
  which sockets received the same datagram.
- Consoles that auto-pick their Art-Net interface (dot2 does) only accept the
  Art-Net `2.x.x.x` range. With no such address they pick nothing, display
  `0.0.0.0`, and **transmit nothing at all** — that is what `src/core/vnet.py`
  exists to fix. Onyx lets you choose the interface and needs none of it.
- Windows has **no built-in CLI to create a root-enumerated device**: `pnputil`
  has no `/add-device` and `devcon.exe` is not shipped. Creation goes through
  SetupAPI via ctypes (`netloop.inf`, hardware ID `*MSLOOP`). Removal can use
  `pnputil /remove-device`.

## Invariants — do not break

- DMX output keeps streaming the last frame when Art-Net input stops
  (fixtures need continuous DMX; a paused console must not black out the rig)
- A held frame must always be *labelled* as held. Levels nobody is sending look
  identical to live ones otherwise, and that reads as a bug every time. Same for
  a short ArtDMX frame: channels above its length keep the previous console's
  values (spec-correct), so the GUI draws them muted rather than clearing them
- The buffer is cleared only by deliberate user action — the Clear button or a
  universe change. Never automatically on source change or frame length
- `DmxOutput` reconnect loop never gives up while running
- GUI never blocks: no serial/socket calls on the main thread except via engine
  snapshots. `vnet` shells out to PowerShell, so it is called **only** on
  demand (startup, refresh button, Create/Remove) — never from the 100 ms poll
- Art-Net universe = 15-bit port address: `(Net << 8) | SubUni`; ArtDMX length
  field is big-endian, opcodes little-endian
- The receiver counts **every** universe seen, not only the selected one — the
  GUI must be able to say "the console is sending universe 5", never just
  "no data"
- Capturing Art-Net and driving DMX **never** require administrator rights.
  Only creating/removing the adapter does, via a short-lived elevated helper
- `vnet` helper input is **untrusted**: the request file is writable by the
  unelevated user, so the elevated helper re-validates the action, the address,
  the prefix, and the adapter name before acting

## Never hide a simulator

`tools/artnet_test_sender.py` once ran unannounced in the background and made a
broken bridge look finished — it cost a full day. It now prints a banner, and
the GUI labels `127.0.0.1` traffic as a local test sender. **When any part of a
demonstration is simulated, say so in the same breath as the result.** Before
declaring integration work done, check for stray processes from earlier runs.

## Test Suite — Mandatory

```
python -m pytest
```

After any code change in `src/`, run the full suite before considering the task
done. Hardware tests use mocks — no real COM port, network, or adapter is ever
touched. **Keep it that way.**

## Run / Build

```
python AnyDMX.py                        # run from source
python tools/artnet_sniff.py            # what is actually on the wire
python tools/artnet_test_sender.py      # feed test pattern to localhost
```

PyInstaller build: deferred until after POC hardware verification.

## Verified on real hardware

The whole chain works end to end: `vnet.py` creates the adapter on real
Windows, dot2 picks it up and transmits, Art-Net is captured, and DMX drives
real fixtures. The frame-timing fix above was confirmed by the symptom it was
built for disappearing — fixtures no longer twitch in unison, and the cadence
holds steady at 33-35 fps.

Still unproven: Onyx as a source (it transmits, but its own patch/interface
setup has not been worked through), and the PyInstaller build.
