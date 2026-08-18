# AnyDMX — Development Rules

## Core Principles

1. **Keep things simple** — minimal complexity, straightforward solutions
2. **Stability first** — the app must run unattended for hours; auto-reconnect, never crash on I/O errors
3. **Stay organized** — no extra code or files

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
- **pyserial** — DMX output (Open DMX technique: 250000 baud 8N2, break + start code + 512 bytes @ ~40 fps)
- **stdlib socket** — Art-Net UDP (port 6454), no external protocol library

## Key Files

| File | Role |
|---|---|
| `AnyDMX.py` | Entry point |
| `src/core/artnet_receiver.py` | UDP listener, ArtDMX parser, ArtPollReply (node discovery) |
| `src/core/dmx_output.py` | Serial DMX sender thread, auto-reconnect |
| `src/core/engine.py` | Universe buffer + status snapshots; wires receiver → output |
| `src/core/ports.py` | COM port enumeration + chip ID (FTDI/CH340/CP210x/Prolific) |
| `src/gui/main_window.py` | Single-window GUI, 100 ms status polling |
| `src/gui/channel_view.py` | Live 512-channel grid (32×16) |
| `src/gui/styles.py` | Color palette + QSS |
| `src/utils/paths.py` | Portable paths (source run vs PyInstaller exe) |
| `tools/artnet_test_sender.py` | Hardware-free test: animated ArtDMX to localhost |

## Threading

3 threads: Main (GUI, polls engine at 100 ms) | ArtNetReceiver (UDP recv) |
DmxOutput (serial send loop). Cross-thread data goes ONLY through the
lock-guarded buffer or single-writer atomic stat attributes. No Qt signals
from worker threads.

## Invariants — do not break

- DMX output keeps streaming the last frame when Art-Net input stops
  (fixtures need continuous DMX; a paused console must not black out the rig)
- `DmxOutput` reconnect loop never gives up while running
- GUI never blocks: no serial/socket calls on the main thread except via engine snapshots
- Art-Net universe = 15-bit port address: `(Net << 8) | SubUni`; ArtDMX length
  field is big-endian, opcodes little-endian

## Test Suite — Mandatory

```
python -m pytest
```

After any code change in `src/`, run the full suite before considering the
task done. Hardware tests use mocks — no real COM port or network is needed.
Keep it that way.

## Run / Build

```
python AnyDMX.py                        # run from source
python tools/artnet_test_sender.py      # feed test pattern to localhost
```

PyInstaller build: deferred until after POC hardware verification.
