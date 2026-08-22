"""Serial port enumeration with USB-serial chip identification."""

import sys

from serial.tools import list_ports

IS_LINUX = sys.platform.startswith("linux")

# USB vendor IDs of common USB-serial chips found in RS-485/DMX dongles
KNOWN_VIDS = {
    0x0403: "FTDI",
    0x1A86: "CH340",
    0x10C4: "CP210x",
    0x067B: "Prolific",
}


def has_hardware(port):
    """True when something is actually behind this device node.

    Linux creates /dev/ttyS0 through /dev/ttyS31 for legacy 16550 UARTs
    whether or not a single one of them exists. Left in, they are 32 dead
    entries burying the one USB dongle the user is hunting for — and the
    first of them is what gets picked, so the app reports a permission error
    about a port that was never real. pyserial marks them hwid 'n/a'.

    A port worth offering has an ID: every USB-serial chip has a VID, and an
    on-board or PCI RS-485 port carries a hardware ID of its own. Only the
    phantoms have neither.
    """
    if port.vid is not None:
        return True
    return str(port.hwid or "").strip().lower() not in ("", "n/a")


def list_serial_ports():
    """Return [{device, description, chip, label}] for every usable port.

    FTDI-based ports are listed first — they are the most reliable for DMX.

    The empty-node filter is Linux-only on purpose. Windows enumerates a COM
    port only when a device is behind it, so there is nothing to filter, and
    a filter there could hide a legitimate port that simply reports no ID.
    """
    ports = []
    for p in list_ports.comports():
        if IS_LINUX and not has_hardware(p):
            continue
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
