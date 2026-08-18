"""COM port enumeration with USB-serial chip identification."""

from serial.tools import list_ports

# USB vendor IDs of common USB-serial chips found in RS-485/DMX dongles
KNOWN_VIDS = {
    0x0403: "FTDI",
    0x1A86: "CH340",
    0x10C4: "CP210x",
    0x067B: "Prolific",
}


def list_serial_ports():
    """Return [{device, description, chip, label}] for all COM ports.

    FTDI-based ports are listed first — they are the most reliable for DMX.
    """
    ports = []
    for p in list_ports.comports():
        chip = KNOWN_VIDS.get(p.vid, "Unknown")
        label = f"{p.device} — {chip}" if chip != "Unknown" else \
                f"{p.device} — {p.description}"
        ports.append({
            "device": p.device,
            "description": p.description,
            "chip": chip,
            "label": label,
        })
    ports.sort(key=lambda x: (x["chip"] != "FTDI", x["device"]))
    return ports
