# AnyDMX — Development Rules

> **If you arrived here from the repo:** this file is the working brief for
> Claude, the AI model that writes this project's code with its author. It is
> checked in on purpose. Most of it is not style preference — it is the
> decisions that cost a real debugging session, written down with the reasoning
> so they don't get "simplified" back into bugs later. Read it as the honest
> account of why the code looks the way it does; the sections marked *do not
> re-litigate* are the ones that were expensive.
>
> AnyDMX is MIT-licensed and open to issues and pull requests — see
> [README.md](README.md). Human contributors are as welcome as the model is,
> and these rules apply to both.

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
| `src/core/engine.py` | Universe buffer + status snapshots + `RateMeter`; wires receiver → output |
| `src/core/ports.py` | COM port enumeration + chip ID (FTDI/CH340/CP210x/Prolific) |
| `src/core/vnet.py` | Virtual "AnyDMX" lighting adapter: create/remove, UAC elevation |
| `src/gui/main_window.py` | Single-window GUI: input panel, output panel, 100 ms status polling |
| `src/gui/frameless.py` | `FramelessWindow`: move/resize/clamp — everything the decorations used to do |
| `src/gui/title_bar.py` | The window's own title bar (the window is frameless) |
| `src/gui/status_text.py` | Snapshot → LED, one-word state, and the bottom sentence (pure, no Qt) |
| `src/gui/universe_bar.py` | The discovered-universe chips, as a column or a row |
| `src/gui/vnet_dialog.py` | Interface setup pop-up: every adapter and its settings, plus create/remove the virtual one |
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
  **Every** rate goes through `engine.RateMeter` for this reason — the packet
  rate, the frame rate, and the per-universe rate on each chip. A rate computed
  straight off the poll interval is the bug this class exists to prevent, and
  it looks like a working feature until someone reads the number.
- **~34 fps is a deliberate choice, not a limitation to fix.** Consoles run
  25-44 Hz; the only way past the ceiling is sending fewer channels per frame,
  which re-creates the stale-tail confusion the GUI now exists to explain — a
  speedup no one can see, paid for in the one thing that already cost a session.
  Reopen this only if a fast pan/tilt visibly steps through AnyDMX but not when
  the console drives the rig directly.

## Window geometry facts — measured, do not re-litigate

- The desktop is smaller than the monitor. The dev machine's 4K screen at 300%
  scaling reports **1280x720, work area 1280x680** to Qt, and every size in the
  GUI is in those logical pixels.
- A window larger than the work area gets shoved around by the window manager
  while it is being placed, so `fit_on_screen()` clamps every programmatic
  resize to it and `_settle_on_screen()` checks where it landed one event-loop
  pass later, once the layout has settled.
- **The window is frameless** (`Qt.FramelessWindowHint`): a window manager's
  title bar is outside the widget tree and no stylesheet can reach it, so the
  bar is drawn by `src/gui/title_bar.py` instead. That means move, resize,
  minimise, maximise and close are the app's job now:
  - drag and resize are handed back to the platform with `startSystemMove()` /
    `startSystemResize()`, never reimplemented with mouse arithmetic — that is
    what keeps snapping and tiling native on X11 and Windows
  - all four edges live in `FramelessWindow`: the border is the margin strip
    around the body, caught in `eventFilter()`, and the title bar routes its
    top-edge press through the same `begin_resize()`. There is one rule about
    when the window may be resized, not one per file
  - there is no frame outside the window any more, so `max_client_size()` is
    simply the work area
  - Muffin's rule about hiding the maximise button on a window that cannot fit
    no longer applies — the maximise button is ours. Keep the minimum modest
    anyway; a window that cannot shrink to the screen is still a bad window
- **The panel row's height is a budget spent against the channel grid.** The
  grid needs ~320 px (16 rows over `MIN_TEXT_H`) before it will label its cells,
  and on a 680 px work area there is nothing else to take it from. That is why
  the universes move to the bottom strip when the drawer opens, why Clear sits
  beside the drawer button rather than under it, and why the paddings in the
  panels are as small as they are. Adding a row to either panel takes the cell
  labels away on that machine — check with the drawer open before adding one.

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
- **`Get-NetAdapter -Physical` filters on `Virtual`, not `HardwareInterface`.**
  Measured: the KM-TEST loopback the AnyDMX adapter is built on reports
  `Virtual=False`, so it counts as *physical* and appears in the editable list
  beside the real NICs — correct, it is a real NDIS miniport with a device
  node. Tailscale and VirtualBox host-only report `Virtual=True`.
  `HardwareInterface` is False for adapters users certainly consider real, so
  it is not a usable substitute; `test_the_loopback_adapter_counts_as_physical`
  exists to stop the "fix".
- `ConvertTo-Json` defaults to **`-Depth 2`**, which serialises the nested
  address objects in `list_adapters()` as type names instead of values.
  `-Depth 4` is not optional. Nor is the `ContainsKey` guard: `@($h[$i])` on a
  missing key yields `[null]`, not `[]`, and an adapter with no address is
  common.
- **`ctypes.wintypes` is wrong off Windows.** It maps `DWORD`/`BOOL` onto
  `c_ulong`/`c_long`, which are 32-bit only under Windows' LLP64 model — on an
  LP64 host they widen to 64 bits and every SetupAPI struct is laid out wrong
  (`_SP_DEVINFO_DATA` came out 48 bytes instead of 32, and the layout test that
  caught it was simply left failing). `vnet.py` therefore spells the scalars as
  fixed widths (`c_uint32`, `c_uint16`) and the structs are correct on any host.
  Do not "simplify" them back to `wintypes`; handle and string types are
  pointer-sized everywhere and are the only ones that still come from there.

## Invariants — do not break

- There is no Start button: the bridge arms itself on startup and re-arms on
  every selection change. A failed bind is reported in the bottom status line,
  never a modal — the GUI must stay usable so the user can pick another port
- The window is two panels: everything Art-Net on the left, everything DMX on
  the right, one dynamic sentence across the bottom. It opens at the compact
  layout's own floor (`COMPACT_W` x `COMPACT_H`) and nothing in the panels may
  be allowed to stretch it, which is why the status label's width policy is
  Ignored, the discovered-universe list is scrolled, and its chips drop the
  source address (never the LOCAL TEST marker) while the column is narrow
- Each drawer state remembers the size it was last left at, for the session
  only: the arrow toggles between two windows the user has already sized, and a
  restart is back to the compact default
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
  Every operation that does — creating the adapter, removing it, changing an
  existing interface — goes through the short-lived elevated helper and asks
  Windows at the moment it is applied
- **How AnyDMX was launched must never decide what Interface Setup can do.**
  There is no read-only mode. The editor was gated on `is_admin()` once and it
  was the wrong trade twice over: pinning a static address on a real NIC is
  the setup step a lighting network needs most often, and the greyed fields
  were indistinguishable from the ones DHCP greys out, so an elevated user
  reading "read-only" concluded the detection was broken. `request_apply()`
  is the rule now, alongside `request_create()`/`request_remove()`: elevated,
  act directly; unelevated, hand it to the helper. `apply_adapter()` keeps its
  own `is_admin()` guard as the last line, not as the policy
- **Rights are reported where they bite, never announced up front.** Opening
  the window asks for nothing and pops nothing. Elevated, it says nothing
  about rights at all — no prompt is coming, so promising one is a lie. The
  only notice left in the editor's button row is DHCP's, and it names the
  control that fixes it
- Validate a change **before** raising the prompt, and again inside the
  helper. A permission dialog raised over a request that cannot succeed is a
  dialog the user learns to click through
- `vnet` helper input is **untrusted**: the request file is writable by the
  unelevated user, so the elevated helper re-validates the action, the index,
  every op, the address, the prefix, and the adapter name before acting — and
  `apply_adapter` refuses outright if the interface's live name is not the one
  the request claims. The helper knows exactly three actions; adding a fourth
  widens the one boundary in this app that an attacker would aim at

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

## The repo is public

AnyDMX is on GitHub under MIT. Two things follow from that:

- **Other people's hardware is the point.** Only dot2 and Onyx have ever been
  tested here, on one Windows machine. A bug report from a console or dongle
  nobody here owns is the most valuable thing this project can receive — so
  when something fails, the GUI must say *what* it saw, never just "no data".
  That invariant is now load-bearing for bug reports too, not only for users.
- **Nothing in the tests may touch real hardware or the network.** A
  contributor running `python -m pytest` on a machine with no dongle, no
  console and no admin rights must get a clean green run.
