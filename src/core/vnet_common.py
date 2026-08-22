"""Vocabulary and validation shared by every vnet backend.

The two backends are shaped very differently — Windows drives SetupAPI and
PowerShell through an elevated helper, Linux drives NetworkManager and lets
polkit raise the prompt — but they take the same values from the same dialog,
so they must reject the same input in the same way. One rule, one place.

Every one of these runs *before* a permission prompt is raised, so a typo is
refused without a password dialog appearing over it, and again on the far side
of any privilege boundary, because what crosses it is never trusted.
"""

ADAPTER_NAME = "AnyDMX"
DEFAULT_IP = "2.100.100.0"
DEFAULT_PREFIX = 8

# Addresses an auto-picking console will accept: the Art-Net spec's own ranges.
ARTNET_PREFIXES = ("2.", "10.")

# Order a batch is applied in. Enable first (a disabled adapter cannot be
# addressed), disable last (it is the change that takes the adapter away).
OP_ORDER = {"enable": 0, "rename": 1, "static": 2, "dhcp": 2, "disable": 3}


class VNetError(Exception):
    """Something went wrong managing the lighting interface."""


def validate_ipv4(ip):
    parts = str(ip).split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255
                                  for p in parts):
        raise VNetError(f"'{ip}' is not a valid IPv4 address")


def validate_prefix(prefix):
    try:
        prefix = int(prefix)
    except (TypeError, ValueError):
        raise VNetError(f"Prefix length must be 1-32, not {prefix}") from None
    if not 1 <= prefix <= 32:
        raise VNetError(f"Prefix length must be 1-32, not {prefix}")
    return prefix


def validate_name(name):
    """Interface names reach a privileged context, so keep them boring.

    Quoting and argument lists handle this everywhere already; this is defence
    in depth for a string that ends up in an administrator's or root's hands.
    """
    name = str(name)
    if not name or len(name) > 64:
        raise VNetError("Adapter name must be 1-64 characters")
    if not all(c.isalnum() or c in " -_" for c in name):
        raise VNetError("Adapter name may only contain letters, digits, "
                        "spaces, hyphens, and underscores")
    return name


def validate_index(index):
    """Interface indexes are interpolated bare, so they must be real ints."""
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise VNetError(f"'{index}' is not a network interface index") from None
    if not 1 <= index <= 0xFFFFFF:
        raise VNetError(f"Interface index {index} is out of range")
    return index


def validate_ops(ops):
    """Validate a whole batch before any of it runs.

    A typo in the third change must not leave the first two applied — half a
    reconfigured adapter is worse than none, because the user cannot see
    which half.
    """
    if not isinstance(ops, list) or not 1 <= len(ops) <= 6:
        raise VNetError("A change request must carry 1-6 changes.")
    clean = []
    for raw in ops:
        if not isinstance(raw, dict):
            raise VNetError("Malformed change request.")
        op = raw.get("op")
        if op not in OP_ORDER:
            raise VNetError(f"Unknown change {op!r}")
        if op == "rename":
            clean.append({"op": op, "name": validate_name(raw.get("name"))})
        elif op == "static":
            ip = str(raw.get("ip", ""))
            validate_ipv4(ip)
            prefix = validate_prefix(raw.get("prefix", 0))
            gateway = str(raw.get("gateway", "") or "")
            if gateway:
                validate_ipv4(gateway)
            clean.append({"op": op, "ip": ip, "prefix": prefix,
                          "gateway": gateway})
        else:
            clean.append({"op": op})
    kinds = {c["op"] for c in clean}
    if len(clean) != len(kinds):
        raise VNetError("A change request may not repeat a change.")
    if {"enable", "disable"} <= kinds or {"static", "dhcp"} <= kinds:
        raise VNetError("A change request contradicts itself.")
    clean.sort(key=lambda c: OP_ORDER[c["op"]])
    return clean
