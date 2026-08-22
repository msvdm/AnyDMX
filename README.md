# AnyDMX

**Give your lighting console a network interface to send Art-Net to, and get
DMX512 out of a cheap USB dongle. One app, one window. Free, MIT-licensed, no
account, no telemetry.**

I'm a sound engineer, not a programmer or a lighting designer, so don't judge me too much :).
I kept running into the same annoying problem: I had
a lighting console that speaks Art-Net, and a cheap USB-RS485 dongle that
speaks DMX, and getting from one to the other meant installing a loopback
adapter by hand and then running somebody else's bridge application on top of
it. Two pieces of setup, neither of which explains itself when it goes wrong.

AnyDMX is my attempt to collapse that into one window:

```
Console → Art-Net → AnyDMX → USB dongle → fixtures
```

instead of

```
Console → Art-Net → bridge app → loopback adapter → Open DMX driver → fixtures
```

It also tells you what it is seeing, which turned out to matter more than I
expected. When the rig is dark, the first question is never "is the bridge
configured right" — it is "is the console sending anything at all, and on which
universe". AnyDMX answers that on screen.

## What it actually does

Three things, in order:

1. **Gives the console somewhere to send.** Some consoles pick their own
   Art-Net interface and will only use the Art-Net `2.x.x.x` range. If no such
   address exists on the PC they pick nothing, show `0.0.0.0`, and transmit
   nothing at all. AnyDMX can create that address for them — see
   [Interface setup](#interface-setup).
2. **Captures the Art-Net.** It listens on UDP 6454 on the wildcard address
   *and* on every individual NIC address. Broadcast from a console on the
   network works, and so does unicast from an app on the same PC — Windows
   loops locally-sent broadcast back to other local sockets, so same-PC capture
   needs nothing installed. The per-NIC binds matter too: without them, another
   app that bound a specific address first would take the unicast away from
   AnyDMX.
3. **Streams DMX.** 512 channels out an FTDI or CH340 USB serial adapter using
   the Open DMX technique, at a steady ~34 fps, holding the last frame if the
   console stops.

It also **answers ArtPoll and broadcasts ArtPollReply** as the spec asks, so
node scanners should be able to find it. I have not confirmed that on a real
console: dot2 never lists third-party Art-Net nodes at all, and I have not
finished working through Onyx's side of it. Treat it as written but unproven —
nothing about capturing or sending depends on it.

**It is useful with no dongle attached at all.** Leave the output on *Monitor
mode* and AnyDMX is just a window onto the wire: every universe arriving, who
is sending it, at what rate, and all 512 live levels.

## Who wrote this

Claude — Anthropic's Opus model.

I am not a software developer. I know what the problem is, I have the console
and the dongle and the fixtures, and I test every change on real hardware.
Claude writes and refactors the Python. That is the honest division of labour
and I would rather say it plainly than have people wonder about the commit
history.

One thing worth knowing if you read the source: the decisions that cost us a
real debugging session are written down in [CLAUDE.md](CLAUDE.md), with the
reasoning, so they do not get "simplified" back into bugs later. If you want to
know *why* something is the way it is — the frame timing especially — that file
is the honest answer, not this one.

## Download

Grab the file for your system from the
[latest release](https://github.com/msvdm/AnyDMX/releases/latest). Nothing to
install, no Python needed — it is one file.

| System | File | How to run it |
|---|---|---|
| Windows 10/11 (64-bit) | `AnyDMX-*-windows-x64.exe` | Double-click it |
| Linux (64-bit, glibc 2.35+) | `AnyDMX-*-linux-x64` | `chmod +x` it, then run it |

**Windows will warn you the first time.** The file is not signed — a
code-signing certificate costs more per year than this project costs to run —
so SmartScreen shows *"Windows protected your PC"*. Click **More info**, then
**Run anyway**. Some antivirus tools flag single-file Python apps for the same
reason: they are self-extracting, which looks like what it is. Everything it
does is in this repository, and the binaries are built by GitHub Actions from
the tagged commit, not on my machine — the build is
[`.github/workflows/release.yml`](.github/workflows/release.yml) if you want to
read it.

Or run it from source, which is what I do:

## Requirements

- Windows or Linux, Python 3.10+
- `pip install -r requirements.txt` (PySide6, pyserial)
- A USB-RS485 / Open DMX-style dongle — FTDI recommended, CH340 usually works.
  Or nothing at all, if you only want to watch.

macOS is not supported. Not because it cannot work — most of the app is
portable — but because I have no Apple hardware to test on, and I am not
shipping a build I have never watched drive a light. If you have a Mac and a
dongle, a pull request is genuinely welcome; see `src/core/vnet_unsupported.py`
for what an interface backend has to implement.

## Run

```
python AnyDMX.py
```

There is no Start button. The bridge arms itself and re-arms whenever you
change a setting. The window is laid out as the signal path: **INPUT** on the
left, **OUTPUT** on the right, one line along the bottom saying what is
happening right now.

1. **Input Port** — leave it on "All interfaces", or pick a specific IP if you
   have a dedicated lighting network.
2. **Output Device** — your dongle's COM port (press ⟳ after plugging it in),
   or leave it on "Monitor mode" to watch with no hardware.
3. **Universe** — type it, or just click one in the list of universes actually
   arriving, which saves knowing the number in advance.
4. Green indicators mean packets arriving and DMX streaming.
5. **▼ DMX values** opens the 512-channel grid. Closed, the window is small
   enough to sit beside the console; each state remembers the size you left it
   at.

A frame that is being *held* because input stopped is labelled as held, and
channels above a short frame's length are drawn muted rather than cleared.
Levels nobody is sending must never look identical to live ones — that reads as
a bug every time. Traffic from `127.0.0.1` is labelled as a local test sender
for the same reason.

## On Linux

AnyDMX runs from source on Linux the same way it does on Windows, with two
differences worth knowing before you start.

**Your user needs permission to open the dongle.** On Debian, Ubuntu, Mint and
their relatives, `/dev/ttyUSB0` belongs to the `dialout` group, and a user who
has never needed a serial port is not in it. Once:

```
sudo usermod -aG dialout $USER
```

Then log out and back in — a new terminal is not enough, the group is attached
when you log in. If you skip this, AnyDMX tells you exactly this in its status
line rather than showing you a bare "Permission denied".

**Interface Setup uses NetworkManager.** Creating the AnyDMX interface makes a
NetworkManager `dummy` device, which is the closest Linux equivalent of the
loopback adapter it makes on Windows: a real, named interface holding
`2.100.100.0/8` that is still there after a reboot. Your desktop asks for your
password when you apply a change, and nothing else. One thing the Linux side
deliberately will not do is rename an interface — a permanent rename means a
udev rule, which is a different kind of change from "give this NIC an address",
so the Name field is shown but inert.

**How much of this is proven:** Art-Net capture, universe discovery, the
interface editor and the GUI are verified working on Linux Mint 22.3 (Cinnamon,
X11). **DMX output over a dongle is not** — I have no dongle attached to that
machine, so the serial path on Linux is correct code that nobody has watched
drive a real fixture. If you try it, please tell me what happened either way;
see [Ideas, problems, contributions](#ideas-problems-contributions).

## Interface setup

Press **Interface Setup** in the INPUT panel. It does two jobs: it lists every
network interface on the PC so you can fix the one that is wrong without going
into the system network settings, and it creates the virtual AnyDMX interface,
which no OS has a dialog for at all.

Nobody enjoys the network settings on any OS. That is the whole reason this
exists.

### The adapter list

Every adapter, one row each: name, address, DHCP or static, and a coloured dot
for link state. Tags say the things you would otherwise have to work out —
`LISTENING` is the one AnyDMX is capturing on, `GATEWAY` is the one carrying
your internet connection, `VIRTUAL` is not a real NIC, `ANYDMX` is the one this
app made. Hover a row for the hardware description, MAC and link speed, which
is usually how you tell three identical Ethernet ports apart.

Select one and the editor fills in: **Name**, **Addressing** (automatic or
static), **Address** and prefix, and **Gateway**. Press **Apply** and every
change to that adapter goes in one operation — a rename and a re-address is one
step, not two.

**Art-Net 2.x** is the button worth knowing about. One click sets the adapter
static, `/8`, no gateway, and the lowest free address in the `2.100.100.x`
range — which is exactly what an auto-picking console needs, without having to
remember why.

Two things worth knowing:

- Changing an adapter needs administrator rights, but AnyDMX does not have to
  be *started* as administrator. Press Apply and Windows asks for permission
  for that one step; approve it and the change lands, decline it and nothing
  happens.
- Anything that could take the machine off the network — disabling an adapter,
  re-addressing the one carrying your internet or the one AnyDMX is listening
  on — asks first, with Cancel as the default, and warns louder over a remote
  desktop session. It never refuses outright: disabling one of two NICs is a
  perfectly reasonable thing to want.

### The AnyDMX adapter

This is the part I actually built the app for.

Consoles that pick their own Art-Net interface — dot2 among them — only ever
accept the `2.x.x.x` range. With no such address on the PC they select nothing,
display `0.0.0.0`, and send not one packet. There is nothing to capture,
however well AnyDMX listens.

So AnyDMX creates the landing spot itself: a virtual network adapter named
**AnyDMX** holding `2.100.100.0/8` (both editable). Set the address at the
bottom of the pop-up and press **Create**. It then appears in the Input Port
list, and in the console's interface list.

- **You do not need to run AnyDMX as administrator for this.** Creating and
  removing the adapter does need admin rights, so the button asks Windows for
  permission and does that one step in a short-lived elevated helper. Approve
  it and you are done; decline and nothing changes. Capturing Art-Net and
  sending DMX never need elevation at all.
- The adapter **persists** across runs. It is infrastructure, not session
  state. Press **Remove** to delete it.
- It uses Windows' own in-box loopback driver (`netloop.inf`, hardware ID
  `*MSLOOP`). Nothing is downloaded or bundled.
- It gets **no default gateway**, so the `2.0.0.0/8` route can never become a
  path for ordinary traffic, and its firewall profile is set to Private.
- **Restart your console after creating it** — lighting apps enumerate network
  interfaces at startup and will not notice a new one otherwise.

## Console notes

Only two consoles have ever been tested here, both by me, on one Windows
machine. Take the rest as untested rather than unsupported.

- **dot2 / dot2 onPC** works, and is what most of this was built against. It
  will not list AnyDMX in its network view — dot2 shows MA hardware only, which
  is normal and not a fault. Just enable Art-Net output and it broadcasts;
  AnyDMX picks it up. It does need the `2.x.x.x` interface above.
- **Onyx** lets you choose the Art-Net interface explicitly, so it needs no
  virtual adapter. It transmits, but I have not worked through its patch and
  interface setup properly yet, so I cannot claim the whole path is proven.
- **A console on the same PC** works. No loopback adapter is needed for the
  *capture* — Windows handles that. The adapter is only about giving an
  auto-picking console an address it will accept.
- **Point the console at a real adapter.** A lighting app whose Art-Net
  interface reads `0.0.0.0` is transmitting nothing at all, and no setting in
  AnyDMX can help.

If your console is not on this list, I would genuinely like to know how it
goes — see [below](#ideas-problems-contributions).

## When nothing arrives

Run the sniffer. It listens on every local address for both Art-Net (6454) and
sACN (5568) and reports every stream it sees, with universe, source, rate, and
whether it was broadcast or unicast:

```
python tools/artnet_sniff.py
```

Then turn your lighting app's output on and watch. If the sniffer shows
nothing, the app is not transmitting and no AnyDMX setting will change that —
the problem is in the console's own network or DMX configuration.

## Test without a console or hardware

```
python tools/artnet_test_sender.py
```

sends an animated pattern to 127.0.0.1 and the channel grid should ripple. It
prints a loud banner on purpose: DMX produced while it runs proves the output
path only, and says nothing about your console. A hidden test sender once made
a broken bridge look finished here and cost me a day, which is why it announces
itself and why the GUI labels its traffic.

## About the ~34 fps

That number is a choice, not a limit waiting to be raised.

A full DMX frame is 513 slots × 11 bits at 250000 baud — 22.6 ms of wire time —
so with break and mark-after-break the physical ceiling is around 42 fps. The
trap is that `flush()` empties the *driver* buffer, not the USB chip's FIFO,
while the break acts on the UART immediately and out of band. Assert the break
on a fixed schedule and sooner or later it lands while the previous frame is
still draining, corrupting its tail. The symptom is every fixture on the line
twitching at once, intermittently, while the channel grid sits perfectly still.

So AnyDMX waits for the frame to drain and holds a minimum period, and there is
deliberately no output-rate setting. If you have used a bridge with a fixed fps
dropdown where only one value looks stable, this is what you were fighting.

## Status

**Works on real hardware (Windows):** the virtual adapter is created on real
Windows, dot2 picks it up and transmits, Art-Net is captured, and DMX drives
real fixtures steadily at 33-35 fps.

**Works on Linux, up to the dongle:** Art-Net capture, universe discovery, the
NetworkManager interface editor and the GUI are verified on Linux Mint 22.3.
DMX output over a dongle is untested there — no hardware to hand.

**Not proven yet:** Onyx as a source, ArtPoll discovery on any console, DMX
output on Linux, macOS in any form, and the PyInstaller build — so for now this
runs from source.

Things I am interested in, which are not promises: sACN (E1.31) input, several
universes and outputs at once, sending Art-Net on to ESP32-style nodes, tray
mode and autostart.

**No warranty, and this drives real lighting hardware.** Test it on a bench
before you put it in front of an audience.

## Tests

```
python -m pytest
```

Everything is mocked — no real COM port, network or adapter is touched. If you
send a patch, please keep it that way: someone with no dongle, no console and
no admin rights must still get a clean green run.

The same suite runs on Windows and Linux, on every push and pull request
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). The app is written on
Linux and used on Windows, so neither machine notices on its own when a change
breaks the other. Worth being clear about what a green tick means: the code is
correct and imports cleanly. A CI runner has no dongle, no console and no
lighting network, so it can never tell you that DMX reached a fixture.

## Ideas, problems, contributions

All welcome, from anyone, at any level of detail.

- Something not working, or a status message that confused you? Open an
  [issue](https://github.com/msvdm/AnyDMX/issues). "It just sat there saying X
  and I did not know what to do" is a genuinely useful report here — half the
  point of this app is explaining itself, so that is a real bug.
- Tried it with a console that is not dot2 or Onyx? Please tell me either way.
  A report that it worked is as useful as one that it did not, and there are
  only two consoles in the notes above because there are only two here.
- Got a feature idea? Open an issue and describe the problem you are hitting,
  not just the feature — the problem is the part I cannot guess.
- Pull requests are fine. Run the tests first, and skim
  [CLAUDE.md](CLAUDE.md) so you can tell the load-bearing decisions from the
  arbitrary ones.

There is no roadmap to fall behind on. It is a hobby project that solves one
problem well.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it, sell it; just keep the
copyright notice.
