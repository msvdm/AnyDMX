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
2. Set the Art-Net universe your console sends on (0 is the usual default).
3. Press **Start**. Green indicators = packets arriving + DMX streaming.
   The channel grid shows all 512 levels live.

## Test without a console or hardware

```
python tools/artnet_test_sender.py
```

sends an animated test pattern to 127.0.0.1 — the channel grid should ripple.

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
