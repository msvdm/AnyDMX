"""The dedicated lighting network interface — Linux backend.

Reached through src/core/vnet.py, which picks a backend per platform. See
that module for the contract this one implements.

Why NetworkManager and not `ip addr add`
----------------------------------------
The adapter is infrastructure, not session state: created once, it must still
be there after a reboot, exactly as the Windows adapter is. `ip addr add` is
gone the moment the machine restarts, which would make the Linux adapter a
different and worse thing wearing the same name. NetworkManager persists it,
and it is what Mint, Ubuntu and Ubuntu Studio all ship.

Why there is no elevated helper here
------------------------------------
The Windows backend has to build its own privilege boundary: it writes a
request file, relaunches itself through UAC, and re-validates everything on
the far side because that file is writable by the unelevated user. None of
that exists here. NetworkManager already owns the boundary — polkit raises
the prompt, NM validates the request, and nmcli runs unprivileged on this
side of it. The most security-sensitive machinery in this app therefore does
not get a second implementation to keep correct. Do not add one: if something
here needs root, the answer is to ask NetworkManager for it, not to build a
helper.

What is deliberately missing
----------------------------
Renaming an interface. On Windows it is a supported, reversible operation.
On Linux a persistent rename means a udev rule, which is a system-wide change
of a completely different character from "give this NIC an address". The
editor hides the field rather than offering a control that cannot work — see
SUPPORTED_OPS.
"""

import os
import subprocess

from src.core.vnet_common import (
    ADAPTER_NAME, ARTNET_PREFIXES, DEFAULT_IP, DEFAULT_PREFIX, VNetError,
    validate_index, validate_ipv4, validate_name, validate_ops, validate_prefix,
)

_NMCLI_TIMEOUT = 90  # a polkit prompt sits in the middle of these calls

# Everything the editor can ask for. No "rename" — see the module docstring.
SUPPORTED_OPS = frozenset({"enable", "disable", "static", "dhcp"})

# The loopback device is not something anyone points a console at, and NM
# manages it from 1.42 onward, so it would otherwise appear in the list.
SKIPPED_TYPES = ("loopback",)

# NM device states. 100 is activated; below 50 the device is not up in any
# sense the user would call enabled.
_STATE_NAMES = {10: "Unmanaged", 20: "Unavailable", 30: "Disconnected",
                40: "Connecting", 50: "Connecting", 60: "Connecting",
                70: "Connecting", 80: "Connecting", 90: "Connecting",
                100: "Up", 110: "Deactivating", 120: "Failed"}


# --------------------------------------------------------------- privileges

def is_admin():
    """True when this process could change an interface without asking."""
    return os.geteuid() == 0


def is_remote_session():
    """True when this process is being driven over SSH.

    Same danger as RDP on Windows: disabling or re-addressing the interface
    carrying the connection takes the connection with it, and there is no way
    back without physical access.
    """
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


def permission_notice():
    """The sentence to add when a prompt is coming, or None when it is not.

    Root sees nothing about rights, because nothing is going to be asked of
    it. Promising a prompt that will not appear is the lie this returns None
    to avoid.
    """
    if is_admin():
        return None
    return ("The system will ask for your password for this one step; "
            "AnyDMX does not need to be restarted.")


# -------------------------------------------------------------------- nmcli

def _nmcli(args):
    """Run one nmcli command. Returns stdout, raises VNetError on failure.

    Always an argument list, never a shell string — these carry user-typed
    names and addresses, and polkit may be about to run the result as root.
    """
    try:
        proc = subprocess.run(["nmcli"] + list(args), capture_output=True,
                              text=True, timeout=_NMCLI_TIMEOUT)
    except FileNotFoundError as e:
        raise VNetError(
            "NetworkManager's nmcli was not found — AnyDMX manages interfaces "
            "through NetworkManager on Linux.") from e
    except (OSError, subprocess.SubprocessError) as e:
        raise VNetError(f"Could not run nmcli: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else "nmcli reported an error"
        if "not authorized" in message.lower() or "insufficient" in message.lower():
            raise VNetError(f"{message} — the permission request was declined "
                            "or no authentication agent answered it.")
        raise VNetError(message)
    return proc.stdout


def _kv_block(text):
    """Parse one `nmcli -t ... show` block into a dict.

    `device show` emits KEY:VALUE, and the value keeps its raw colons — MAC
    addresses and IPv6 addresses are full of them, so this splits on the
    first colon only. (nmcli's *multi-column* terse mode is the one that
    escapes ':' as '\\:'; this format does not, and treating them the same
    shreds every MAC in the list.)
    """
    fields = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def _device_blocks():
    """Every device NM can see, one dict each, in a single round trip."""
    out = _nmcli(["-t", "-f", "all", "device", "show"])
    return [_kv_block(chunk) for chunk in out.split("\n\n") if chunk.strip()]


# ------------------------------------------------------------- inspection

def _ifindex(device):
    """The kernel's interface index — the Linux answer to Windows' ifIndex."""
    try:
        with open(f"/sys/class/net/{device}/ifindex", "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _state_code(block):
    # "100 (connected)" -> 100
    raw = block.get("GENERAL.STATE", "")
    head = raw.split(" ", 1)[0]
    return int(head) if head.isdigit() else 0


def _addresses(block):
    """Every IP4.ADDRESS[n] in the block, as {"ip", "prefix"}."""
    found = []
    for key, value in block.items():
        if not key.startswith("IP4.ADDRESS[") or "/" not in value:
            continue
        ip, _, prefix = value.partition("/")
        found.append({"ip": ip, "prefix": int(prefix) if prefix.isdigit() else 0})
    return found


def _description(block):
    """A human description of the device, never a kernel driver name.

    Real hardware has a vendor and a product and describes itself. A dummy
    device has neither — it is not hardware — so the fallback would reach
    GENERAL.DRIVER, which is literally the string "dummy". That is the
    kernel's word for the mechanism, and showing it as the identity of the
    lighting interface tells the user nothing about what they are looking at.
    The interface is called AnyDMX everywhere else; it says so here too.
    """
    parts = [block.get("GENERAL.VENDOR", ""), block.get("GENERAL.PRODUCT", "")]
    text = " ".join(p for p in parts if p).strip()
    if text:
        return text
    if block.get("GENERAL.TYPE") == "dummy":
        if block.get("GENERAL.DEVICE", "") == ADAPTER_NAME:
            return f"{ADAPTER_NAME} lighting interface"
        return "Virtual network interface"
    return block.get("GENERAL.DRIVER", "") or block.get("GENERAL.TYPE", "")


def _connection_settings(uuid):
    """ipv4.method and the firewall zone for one connection, in one call."""
    if not uuid:
        return {}
    try:
        out = _nmcli(["-t", "-f", "ipv4.method,connection.zone",
                      "connection", "show", uuid])
    except VNetError:
        return {}
    return _kv_block(out)


def list_adapters():
    """Every interface NetworkManager shows, joined to its addressing.

    Returns the same dict shape the Windows backend does; see its list_adapters
    for the field meanings. Two Linux-specific notes:

      physical : IS-SOFTWARE says no, *or* the device is a dummy. Including
                 dummy is deliberate and is the exact echo of the Windows rule
                 that the KM-TEST loopback counts as physical — the AnyDMX
                 interface is a real device with a real address and must be
                 editable beside the real NICs, not filed away as scenery.
      managed  : NM is not managing this device, so nothing here can change
                 it. The editor says so rather than offering dead controls.

    One nmcli call for every device, plus one per device that has a
    connection, to read ipv4.method. That is on-demand only — the ⟳ button and
    opening the dialog — and never anywhere near the 100 ms status poll.
    """
    adapters = []
    for block in _device_blocks():
        device = block.get("GENERAL.DEVICE", "")
        ntype = block.get("GENERAL.TYPE", "")
        if not device or ntype in SKIPPED_TYPES:
            continue
        index = _ifindex(device)
        if index is None:
            continue  # it went away between the listing and now
        uuid = block.get("GENERAL.CON-UUID", "")
        settings = _connection_settings(uuid)
        code = _state_code(block)
        speed = block.get("CAPABILITIES.SPEED", "")
        adapters.append({
            "name": device,
            "description": _description(block),
            "index": index,
            "status": _STATE_NAMES.get(code, "Unknown"),
            "admin_up": code >= 50,
            "physical": block.get("GENERAL.IS-SOFTWARE") == "no" or ntype == "dummy",
            "link_speed": "" if speed in ("", "unknown") else speed,
            "mac": block.get("GENERAL.HWADDR", ""),
            "instance_id": uuid,
            "addresses": _addresses(block),
            "dhcp": settings.get("ipv4.method", "") == "auto",
            "gateway": block.get("IP4.GATEWAY") or None,
            "category": settings.get("connection.zone") or None,
            "managed": block.get("GENERAL.NM-MANAGED") == "yes",
            "connection": block.get("GENERAL.CONNECTION") or "",
            "type": ntype,
        })
    return adapters


def _adapter_at(index):
    """The one adapter at this interface index, or None."""
    index = validate_index(index)
    return next((a for a in list_adapters() if a["index"] == index), None)


def find_adapter(name=ADAPTER_NAME):
    """Current state of the lighting interface, or None if it does not exist.

    Returns {name, status, instance_id, ip, prefix} — ip is None when the
    interface exists but carries no IPv4 address yet.
    """
    name = validate_name(name)
    match = next((a for a in list_adapters() if a["name"] == name), None)
    if match is None:
        return None
    first = (match["addresses"] or [{}])[0]
    return {"name": match["name"], "status": match["status"],
            "instance_id": match["instance_id"],
            "ip": first.get("ip"), "prefix": first.get("prefix")}


def artnet_range_addresses():
    """Local IPv4 addresses a console like dot2 would consider usable."""
    return [a["ip"] for adapter in list_adapters()
            for a in adapter["addresses"]
            if a["ip"].startswith(ARTNET_PREFIXES)]


# --------------------------------------------------------------- mutation

def _validate_ifname(name):
    """Linux interface names are stricter than the shared name rule.

    The shared validator allows spaces and 64 characters because a Windows
    adapter name is a label. Here the name *is* the device, and the kernel
    caps it at 15 bytes with no spaces.
    """
    name = validate_name(name)
    if len(name) > 15 or " " in name:
        raise VNetError("A Linux interface name must be 15 characters or "
                        "fewer, with no spaces.")
    return name


def _require_connection(adapter):
    """The connection UUID to target, or a clear refusal."""
    if not adapter.get("managed"):
        raise VNetError(
            f"NetworkManager is not managing {adapter['name']}, so AnyDMX "
            "cannot change it. Its address is set somewhere else on this "
            "system.")
    uuid = adapter.get("instance_id")
    if not uuid:
        raise VNetError(
            f"{adapter['name']} has no NetworkManager connection to change. "
            "Connect it once from the system network settings, then retry.")
    return uuid


def create_adapter(name=ADAPTER_NAME, ip=DEFAULT_IP, prefix=DEFAULT_PREFIX):
    """Create and address the lighting interface. Returns its state.

    A NetworkManager dummy device: a real, named, persistent interface that
    holds an address and survives a reboot — the closest true analogue to the
    KM-TEST loopback the Windows backend creates.
    """
    name = _validate_ifname(name)
    validate_ipv4(ip)
    prefix = validate_prefix(prefix)
    if find_adapter(name):
        raise VNetError(f"An interface named '{name}' already exists.")
    _nmcli(["connection", "add", "type", "dummy",
            "ifname", name, "con-name", name,
            "ipv4.method", "manual", "ipv4.addresses", f"{ip}/{prefix}",
            "ipv6.method", "disabled",
            "connection.autoconnect", "yes"])
    try:
        _nmcli(["connection", "up", name])
    except VNetError as e:
        raise VNetError(
            f"{e} The connection was created as '{name}' but could not be "
            "brought up — use Remove to clear it up.") from e
    return find_adapter(name) or {"name": name, "instance_id": name}


def remove_adapter(instance_id=None, name=ADAPTER_NAME):
    """Remove the lighting interface. Reports plainly when there is none.

    Targets the stored connection UUID so Remove can only ever delete the
    connection AnyDMX created, never another that happens to share the name.
    Deleting the connection takes the dummy device with it.
    """
    name = validate_name(name)
    if not instance_id:
        current = find_adapter(name)
        if not current:
            return False
        instance_id = current.get("instance_id")
    if not instance_id:
        return False
    _nmcli(["connection", "delete", instance_id])
    return True


def set_static_ip(index, ip, prefix, gateway=""):
    """Replace an interface's IPv4 configuration with one static address.

    A gateway is offered here and never on the AnyDMX interface itself: a user
    pinning a static address on their real NIC loses every route off the
    subnet without one, and this dialog exists so they do not have to go to
    the system settings to fix that.
    """
    validate_ipv4(ip)
    prefix = validate_prefix(prefix)
    if gateway:
        validate_ipv4(gateway)
    adapter = _live_adapter(index)
    uuid = _require_connection(adapter)
    _nmcli(["connection", "modify", uuid,
            "ipv4.method", "manual",
            "ipv4.addresses", f"{ip}/{prefix}",
            "ipv4.gateway", gateway or ""])
    _nmcli(["connection", "up", uuid])
    return True


def set_dhcp(index):
    """Hand an interface's addressing back to DHCP."""
    adapter = _live_adapter(index)
    uuid = _require_connection(adapter)
    _nmcli(["connection", "modify", uuid, "ipv4.method", "auto",
            "ipv4.addresses", "", "ipv4.gateway", ""])
    _nmcli(["connection", "up", uuid])
    return True


def set_adapter_enabled(index, enabled):
    """Connect or disconnect an interface."""
    adapter = _live_adapter(index)
    if not adapter.get("managed"):
        raise VNetError(
            f"NetworkManager is not managing {adapter['name']}, so AnyDMX "
            "cannot enable or disable it.")
    _nmcli(["device", "connect" if enabled else "disconnect", adapter["name"]])
    return True


def _live_adapter(index):
    """The adapter at this index right now, or a refusal naming the gap."""
    adapter = _adapter_at(index)
    if adapter is None:
        raise VNetError(f"Interface {index} is no longer present — nothing "
                        "was changed.")
    return adapter


_RUNNERS = {
    "enable": lambda i, c: set_adapter_enabled(i, True),
    "disable": lambda i, c: set_adapter_enabled(i, False),
    "dhcp": lambda i, c: set_dhcp(i),
    "static": lambda i, c: set_static_ip(i, c["ip"], c["prefix"], c["gateway"]),
}


def _reject_unsupported(ops):
    for change in ops:
        if change["op"] not in SUPPORTED_OPS:
            raise VNetError(
                f"AnyDMX cannot {change['op']} an interface on Linux. "
                "A persistent rename here needs a udev rule, which is a "
                "system-wide change of a different kind entirely.")
    return ops


def apply_adapter(index, expect_name, ops):
    """Apply a batch of changes to one interface.

    expect_name is an identity check, and it is the guard that makes the rest
    safe: interface indexes are reused, and the dialog's list can be seconds
    old by the time someone presses Apply, so confirm the interface is still
    the one they were looking at before touching it.
    """
    ops = _reject_unsupported(validate_ops(ops))
    index = validate_index(index)
    expect_name = validate_name(expect_name)
    live = _live_adapter(index)
    if live["name"] != expect_name:
        raise VNetError(
            f"Interface {index} is now '{live['name']}', not '{expect_name}' "
            "— nothing was changed. Refresh and try again.")
    for change in ops:
        _RUNNERS[change["op"]](index, change)
    return _adapter_at(index)


# ---------------------------------------------------------------- requests

# The Windows backend's request_* functions decide whether to act directly or
# hand the work to an elevated helper. Here there is nothing to decide:
# nmcli is safe to run unprivileged, and NetworkManager asks polkit for
# whatever it needs. Validation still happens before the call, so a typo is
# refused without a password prompt appearing over it.

HELPER_FLAG = "--vnet-helper"


def request_create(name=ADAPTER_NAME, ip=DEFAULT_IP, prefix=DEFAULT_PREFIX):
    return create_adapter(name, ip, prefix)


def request_remove(instance_id=None, name=ADAPTER_NAME):
    return remove_adapter(instance_id, name)


def request_apply(index, expect_name, ops):
    return apply_adapter(index, expect_name, ops)


def helper_main(argv):
    """There is no elevated helper on Linux; see the module docstring."""
    print("AnyDMX: --vnet-helper is a Windows-only mode. On Linux, interface "
          "changes go through NetworkManager and polkit.")
    return 2
