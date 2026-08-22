"""The dedicated lighting network interface — Windows backend.

Reached through src/core/vnet.py, which picks a backend per platform. See
that module for the contract this one implements.

Consoles that auto-pick their Art-Net interface (dot2 among them) only ever
work on the Art-Net 2.x.x.x range. With no such address on the machine they
pick nothing, show 0.0.0.0, and transmit not a single packet — there is then
nothing for AnyDMX to capture, however well it listens. So AnyDMX creates the
landing spot itself: a virtual NIC named "AnyDMX" holding 2.100.100.0/8.

The adapter is infrastructure, not session state. It is created once and
persists until explicitly removed.

Windows offers no command-line way to create a root-enumerated device:
`pnputil` has add-driver but no add-device, and devcon.exe ships only with the
WDK under an unclear redistribution licence. Both are avoidable — devcon is a
thin wrapper over SetupAPI, which ctypes can call directly. The loopback
driver itself (netloop.inf, hardware ID *MSLOOP) is in-box and WHQL-signed on
every Windows install, so nothing has to be bundled or downloaded.

Creating and removing a device node requires administrator rights. Capturing
Art-Net and driving DMX does not.
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from src.core.vnet_common import (
    ADAPTER_NAME, ARTNET_PREFIXES, DEFAULT_IP, DEFAULT_PREFIX, VNetError,
    validate_index as _validate_index,
    validate_ipv4 as _validate_ipv4,
    validate_name as _validate_name,
    validate_ops as _validate_ops,
)

# Everything the editor can ask for. Windows can do all five; the Linux
# backend cannot rename an interface, which is why this is per-backend.
SUPPORTED_OPS = frozenset({"enable", "disable", "rename", "static", "dhcp"})

HARDWARE_ID = "*MSLOOP"
LOOPBACK_INF = os.path.expandvars(r"%windir%\inf\netloop.inf")

_PS_TIMEOUT = 60
# Keep PowerShell from flashing a console window in front of the GUI.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------- privileges

def is_admin():
    """True when this process can create or remove a device node."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def permission_notice():
    """The sentence to add when a prompt is coming, or None when it is not.

    Elevated, this returns None and the dialog says nothing about rights at
    all — no prompt is coming, so promising one is a lie.
    """
    if is_admin():
        return None
    return ("Windows will ask for permission for this one step; "
            "AnyDMX does not need to be restarted.")


SM_REMOTESESSION = 0x1000


def is_remote_session():
    """True when this process is running inside a remote desktop session.

    Disabling or re-addressing the wrong adapter is annoying at the console
    and unrecoverable over RDP — the connection goes with it and there is no
    way back without physical access. The interface dialog says so before
    touching an adapter that carries the default route.
    """
    try:
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION))
    except (AttributeError, OSError):
        return False


# ------------------------------------------------------------- powershell

def _powershell(script):
    """Run a PowerShell snippet, returning stdout. Raises VNetError on failure."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=_PS_TIMEOUT,
            creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise VNetError(f"Could not run PowerShell: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise VNetError(detail[0] if detail else "PowerShell reported an error")
    return proc.stdout


def _q(value):
    """Escape a value for a single-quoted PowerShell string."""
    return str(value).replace("'", "''")


def _powershell_json(script):
    """Run a snippet ending in ConvertTo-Json; return list of dicts."""
    out = _powershell(script).strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError as e:
        raise VNetError(f"Unexpected PowerShell output: {out[:120]}") from e
    return data if isinstance(data, list) else [data]


# ------------------------------------------------------------- inspection

def find_adapter(name=ADAPTER_NAME):
    """Current state of the lighting adapter, or None if it does not exist.

    Returns {name, status, instance_id, ip, prefix}. ip is None when the
    adapter exists but carries no IPv4 address yet.
    """
    rows = _powershell_json(
        f"$a = Get-NetAdapter -Name '{_q(name)}' -ErrorAction SilentlyContinue; "
        "if (-not $a) { '' } else { "
        "$ip = Get-NetIPAddress -InterfaceAlias $a.Name -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "[pscustomobject]@{ name = $a.Name; status = $a.Status; "
        "instance_id = $a.PnPDeviceID; ip = $ip.IPAddress; "
        "prefix = $ip.PrefixLength } | ConvertTo-Json -Compress }")
    return rows[0] if rows else None


def artnet_range_addresses():
    """Local IPv4 addresses a console like dot2 would consider usable."""
    rows = _powershell_json(
        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Select-Object IPAddress, InterfaceAlias | ConvertTo-Json -Compress")
    return [r["IPAddress"] for r in rows
            if str(r.get("IPAddress", "")).startswith(ARTNET_PREFIXES)]


# Every adapter Windows shows the user, joined to its addressing, in one
# round trip. Written as a plain string rather than an f-string: PowerShell
# hashtable and scriptblock syntax is dense with braces, and doubling every
# one of them to survive .format() would make it unreadable.
#
# Two details are load-bearing:
#   - ConvertTo-Json defaults to -Depth 2, which serialises the nested address
#     objects as type names instead of values. -Depth 4 is not optional.
#   - the ContainsKey guard is what makes an adapter with no address come back
#     as [] rather than [null]. @($ipv4[$i]) looks equivalent and is not.
#
# No -IncludeHidden: it adds eight WAN Miniport rows (SSTP, kernel debugger,
# Network Monitor) that no lighting user has any use for.
_LIST_SCRIPT = """
$ipv4 = @{}
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {
  $i = [int]$_.ifIndex
  if (-not $ipv4.ContainsKey($i)) { $ipv4[$i] = New-Object System.Collections.ArrayList }
  [void]$ipv4[$i].Add([pscustomobject]@{ ip = [string]$_.IPAddress; prefix = [int]$_.PrefixLength })
}
$dhcp = @{}
Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object {
  $dhcp[[int]$_.ifIndex] = [string]$_.Dhcp }
$gw = @{}
Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | ForEach-Object {
  if (-not $gw.ContainsKey([int]$_.ifIndex)) { $gw[[int]$_.ifIndex] = [string]$_.NextHop } }
$cat = @{}
Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {
  $cat[[int]$_.InterfaceIndex] = [string]$_.NetworkCategory }
Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object {
  $i = [int]$_.ifIndex
  [pscustomobject]@{
    name        = [string]$_.Name
    description = [string]$_.InterfaceDescription
    index       = $i
    status      = [string]$_.Status
    admin_up    = ($_.AdminStatus -eq 'Up')
    physical    = (-not $_.Virtual)
    link_speed  = [string]$_.LinkSpeed
    mac         = [string]$_.MacAddress
    instance_id = [string]$_.PnPDeviceID
    addresses   = @(if ($ipv4.ContainsKey($i)) { $ipv4[$i] } else { @() })
    dhcp        = ($dhcp[$i] -eq 'Enabled')
    gateway     = $gw[$i]
    category    = $cat[$i]
  }
} | ConvertTo-Json -Compress -Depth 4
"""


def list_adapters():
    """Every adapter Windows shows the user, in one PowerShell round trip.

    Returns a list of dicts:
      name, description, link_speed, mac, instance_id, status : str
      index      : int   InterfaceIndex — the handle every mutation targets
      admin_up   : bool  False when administratively disabled
      physical   : bool  not Virtual — the same rule Get-NetAdapter -Physical
                         uses. The KM-TEST loopback the AnyDMX adapter is
                         built on reports Virtual=False and so counts as
                         physical here. That is correct and intended: it is a
                         real NDIS miniport with a device node, and it must be
                         editable alongside the real NICs. Do not "fix" this
                         to HardwareInterface, which is False for adapters
                         users certainly think of as real.
      addresses  : [{"ip": str, "prefix": int}, ...] — may be empty, and may
                   hold more than one entry
      dhcp       : bool
      gateway    : str | None   next hop of the IPv4 default route, if any
      category   : str | None   Private / Public / DomainAuthenticated
    """
    rows = _powershell_json(_LIST_SCRIPT)
    for row in rows:
        row["index"] = int(row.get("index") or 0)
        row["addresses"] = [
            {"ip": str(a.get("ip", "")), "prefix": int(a.get("prefix") or 0)}
            for a in (row.get("addresses") or []) if a]
    return rows


def _adapter_at(index):
    """The one adapter at this interface index, or None."""
    index = _validate_index(index)
    return next((a for a in list_adapters() if a["index"] == index), None)


# --------------------------------------------------------------- SetupAPI

# Windows scalar types at their true widths. ctypes.wintypes maps DWORD and
# BOOL onto c_ulong/c_long, which are 32-bit only under Windows' LLP64 model —
# on an LP64 host they silently become 64-bit and every structure below is laid
# out wrong. Fixed-width spellings are correct on any host, which is what lets
# the layout tests still mean something when the suite runs off Windows.
# Handle and string types are pointer-sized everywhere, so those stay wintypes.
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
BOOL = ctypes.c_int32
ULONG = ctypes.c_uint32


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", WORD),
                ("Data3", WORD), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    # Reserved is ULONG_PTR: a pointer field keeps cbSize correct on both
    # 32- and 64-bit, where a DWORD would silently under-size the struct.
    _fields_ = [("cbSize", DWORD), ("ClassGuid", _GUID),
                ("DevInst", DWORD),
                ("Reserved", ctypes.POINTER(ctypes.c_ulong))]


GUID_DEVCLASS_NET = _GUID(0x4D36E972, 0xE325, 0x11CE,
                          (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00,
                                               0x2B, 0xE1, 0x03, 0x18))

DICD_GENERATE_ID = 0x00000001
SPDRP_HARDWAREID = 0x00000001
DIF_REGISTERDEVICE = 0x00000019
INSTALLFLAG_FORCE = 0x00000001
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_DEVICE_ID_LEN = 200


def _setupapi():
    api = ctypes.WinDLL("setupapi", use_last_error=True)
    api.SetupDiCreateDeviceInfoList.argtypes = [ctypes.POINTER(_GUID), wintypes.HWND]
    api.SetupDiCreateDeviceInfoList.restype = wintypes.HANDLE
    api.SetupDiCreateDeviceInfoW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, ctypes.POINTER(_GUID),
        wintypes.LPCWSTR, wintypes.HWND, DWORD,
        ctypes.POINTER(_SP_DEVINFO_DATA)]
    api.SetupDiCreateDeviceInfoW.restype = BOOL
    api.SetupDiSetDeviceRegistryPropertyW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA), DWORD,
        ctypes.c_char_p, DWORD]
    api.SetupDiSetDeviceRegistryPropertyW.restype = BOOL
    api.SetupDiCallClassInstaller.argtypes = [
        DWORD, wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA)]
    api.SetupDiCallClassInstaller.restype = BOOL
    api.SetupDiGetDeviceInstanceIdW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.LPWSTR,
        DWORD, ctypes.POINTER(DWORD)]
    api.SetupDiGetDeviceInstanceIdW.restype = BOOL
    api.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    api.SetupDiDestroyDeviceInfoList.restype = BOOL
    return api


def _newdev():
    api = ctypes.WinDLL("newdev", use_last_error=True)
    api.UpdateDriverForPlugAndPlayDevicesW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, DWORD,
        ctypes.POINTER(BOOL)]
    api.UpdateDriverForPlugAndPlayDevicesW.restype = BOOL
    return api


def _fail(action):
    err = ctypes.get_last_error()
    raise VNetError(f"{action} failed: {ctypes.FormatError(err).strip()} "
                    f"(error {err})")


def create_device_node():
    """Create the loopback device node. Returns its PnP instance ID.

    This is exactly what `devcon install netloop.inf *MSLOOP` does, minus the
    redistributable binary.
    """
    if not os.path.exists(LOOPBACK_INF):
        raise VNetError(f"Windows loopback driver not found at {LOOPBACK_INF}")
    api = _setupapi()
    dev_info = api.SetupDiCreateDeviceInfoList(ctypes.byref(GUID_DEVCLASS_NET), None)
    if dev_info == INVALID_HANDLE_VALUE:
        _fail("Creating the device list")
    try:
        data = _SP_DEVINFO_DATA()
        data.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
        if not api.SetupDiCreateDeviceInfoW(
                dev_info, "NET", ctypes.byref(GUID_DEVCLASS_NET), None, None,
                DICD_GENERATE_ID, ctypes.byref(data)):
            _fail("Registering a new network device")

        # SPDRP_HARDWAREID is REG_MULTI_SZ: doubly NUL-terminated UTF-16.
        hwid = (HARDWARE_ID + "\0\0").encode("utf-16-le")
        if not api.SetupDiSetDeviceRegistryPropertyW(
                dev_info, ctypes.byref(data), SPDRP_HARDWAREID, hwid, len(hwid)):
            _fail("Setting the hardware ID")

        if not api.SetupDiCallClassInstaller(
                DIF_REGISTERDEVICE, dev_info, ctypes.byref(data)):
            _fail("Installing the device")

        buf = ctypes.create_unicode_buffer(MAX_DEVICE_ID_LEN)
        needed = DWORD()
        if not api.SetupDiGetDeviceInstanceIdW(
                dev_info, ctypes.byref(data), buf, MAX_DEVICE_ID_LEN,
                ctypes.byref(needed)):
            _fail("Reading the new device ID")
        instance_id = buf.value

        reboot = BOOL(False)  # required out-param; the flag is not acted on
        if not _newdev().UpdateDriverForPlugAndPlayDevicesW(
                None, HARDWARE_ID, LOOPBACK_INF, INSTALLFLAG_FORCE,
                ctypes.byref(reboot)):
            _fail("Binding the loopback driver")
        return instance_id
    finally:
        api.SetupDiDestroyDeviceInfoList(dev_info)


# ------------------------------------------------------------- lifecycle

def create_adapter(name=ADAPTER_NAME, ip=DEFAULT_IP, prefix=DEFAULT_PREFIX):
    """Create, name, and address the lighting interface. Returns its state.

    Requires administrator rights. If naming or addressing fails after the
    device exists, the error says so and names the device, so a half-built
    adapter can be removed rather than lingering unnoticed.
    """
    name = _validate_name(name)
    if not is_admin():
        raise VNetError("Creating a network interface requires administrator "
                        "rights.")
    existing = find_adapter(name)
    if existing:
        raise VNetError(f"An adapter named '{name}' already exists.")

    instance_id = create_device_node()
    try:
        _rename_adapter(instance_id, name)
        configure_adapter(name, ip, prefix)
    except VNetError as e:
        raise VNetError(
            f"{e} The device was created as {instance_id} but could not be "
            f"finished — use Remove to clear it up.") from e
    state = find_adapter(name) or {"name": name, "instance_id": instance_id}
    return state


def _rename_adapter(instance_id, name, attempts=10, delay=1.0):
    """Rename the freshly created adapter, which Windows called 'Ethernet N'.

    Windows registers the device node before the network stack has finished
    enumerating it as an adapter, so this retries instead of losing the race
    and leaving a correctly-created adapter under the wrong name.
    """
    device, alias = _q(instance_id), _q(name)
    for _ in range(attempts):
        out = _powershell(
            "$a = Get-NetAdapter -IncludeHidden | "
            f"Where-Object {{ $_.PnPDeviceID -eq '{device}' }}; "
            "if ($a) { "
            f"if ($a.Name -ne '{alias}') {{ Rename-NetAdapter -Name $a.Name "
            f"-NewName '{alias}' }}; 'ok' }}")
        if "ok" in out:
            return
        time.sleep(delay)
    raise VNetError("The new adapter did not appear in Windows in time.")


def configure_adapter(name=ADAPTER_NAME, ip=DEFAULT_IP, prefix=DEFAULT_PREFIX):
    """Give the adapter a static address, and deliberately no gateway.

    Without a default gateway the 2.0.0.0/8 on-link route can never become a
    path for ordinary traffic. The connection profile is forced to Private so
    the Windows firewall does not treat lighting traffic as a public network.
    """
    _validate_ipv4(ip)
    if not 1 <= int(prefix) <= 32:
        raise VNetError(f"Prefix length must be 1-32, not {prefix}")
    alias = _q(name)
    _powershell(
        f"Remove-NetIPAddress -InterfaceAlias '{alias}' -AddressFamily IPv4 "
        "-Confirm:$false -ErrorAction SilentlyContinue; "
        f"Remove-NetRoute -InterfaceAlias '{alias}' -AddressFamily IPv4 "
        "-Confirm:$false -ErrorAction SilentlyContinue; "
        f"Set-NetIPInterface -InterfaceAlias '{alias}' -Dhcp Disabled "
        "-ErrorAction SilentlyContinue; "
        f"New-NetIPAddress -InterfaceAlias '{alias}' -IPAddress '{_q(ip)}' "
        f"-PrefixLength {int(prefix)} -ErrorAction Stop | Out-Null; "
        f"Set-NetConnectionProfile -InterfaceAlias '{alias}' "
        "-NetworkCategory Private -ErrorAction SilentlyContinue")


def remove_adapter(instance_id=None, name=ADAPTER_NAME):
    """Remove the lighting interface. Reports plainly when there is none.

    Targets the stored instance ID so Remove can only ever delete the device
    AnyDMX created, never another adapter that happens to share the name.

    instance_id is deliberately not pattern-validated: it goes to pnputil as
    a list argv with no shell, so there is nothing to inject into. Keep it
    that way — do not refactor this to a shell string.
    """
    name = _validate_name(name)
    if not is_admin():
        raise VNetError("Removing a network interface requires administrator "
                        "rights.")
    if not instance_id:
        current = find_adapter(name)
        if not current:
            return False
        instance_id = current.get("instance_id")
    if not instance_id:
        return False
    try:
        proc = subprocess.run(["pnputil", "/remove-device", instance_id],
                              capture_output=True, text=True, timeout=_PS_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise VNetError(f"Could not run pnputil: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "").strip().splitlines()
        raise VNetError(f"Could not remove {instance_id}: "
                        f"{detail[-1] if detail else 'pnputil failed'}")
    return True


# ----------------------------------------------------- interface settings

# Everything below targets -InterfaceIndex rather than the adapter's name: a
# batch that renames and re-addresses in one go would otherwise lose its own
# target halfway through. Each function validates before it checks privilege
# and before it shells out, so a bad value never reaches an elevated context.


def set_static_ip(index, ip, prefix, gateway=""):
    """Replace an adapter's IPv4 configuration with one static address.

    Unlike configure_adapter(), a gateway is offered here. That difference is
    deliberate: the AnyDMX adapter must never have one, but a user pinning a
    static address on their real NIC loses every route off the subnet without
    it, and this dialog exists so they do not have to go to Windows to fix
    that.
    """
    _validate_ipv4(ip)
    if not 1 <= int(prefix) <= 32:
        raise VNetError(f"Prefix length must be 1-32, not {prefix}")
    if gateway:
        _validate_ipv4(gateway)
    index = _validate_index(index)
    if not is_admin():
        raise VNetError("Changing an address requires administrator rights.")
    route = f" -DefaultGateway '{_q(gateway)}'" if gateway else ""
    _powershell(
        f"Set-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4 "
        "-Dhcp Disabled -ErrorAction SilentlyContinue; "
        f"Remove-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 "
        "-Confirm:$false -ErrorAction SilentlyContinue; "
        f"Remove-NetRoute -InterfaceIndex {index} -AddressFamily IPv4 "
        "-Confirm:$false -ErrorAction SilentlyContinue; "
        f"New-NetIPAddress -InterfaceIndex {index} -IPAddress '{_q(ip)}' "
        f"-PrefixLength {int(prefix)}{route} -ErrorAction Stop | Out-Null")


def set_dhcp(index):
    """Hand an adapter back to DHCP, including its DNS servers.

    -ResetServerAddresses is not offered as a setting and is not optional:
    leaving hand-pinned DNS behind a switch back to automatic produces a
    half-restored adapter that works until it doesn't.
    """
    index = _validate_index(index)
    if not is_admin():
        raise VNetError("Changing an address requires administrator rights.")
    _powershell(
        f"Remove-NetIPAddress -InterfaceIndex {index} -AddressFamily IPv4 "
        "-Confirm:$false -ErrorAction SilentlyContinue; "
        f"Remove-NetRoute -InterfaceIndex {index} -AddressFamily IPv4 "
        "-Confirm:$false -ErrorAction SilentlyContinue; "
        f"Set-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4 "
        "-Dhcp Enabled -ErrorAction Stop; "
        f"Set-DnsClientServerAddress -InterfaceIndex {index} "
        "-ResetServerAddresses -ErrorAction SilentlyContinue")


def set_adapter_name(index, new_name):
    """Rename an adapter by index.

    Not _rename_adapter(), which is the private, PnPDeviceID-targeted,
    retrying one used while a freshly created adapter is still enumerating.
    """
    new_name = _validate_name(new_name)
    index = _validate_index(index)
    if not is_admin():
        raise VNetError("Renaming an interface requires administrator rights.")
    _powershell(
        f"Get-NetAdapter -InterfaceIndex {index} -ErrorAction Stop | "
        f"Rename-NetAdapter -NewName '{_q(new_name)}' -Confirm:$false "
        "-ErrorAction Stop")


def set_adapter_enabled(index, enabled):
    """Enable or disable an adapter.

    The verb comes from a literal table — no caller-supplied text ever
    reaches the command line here.
    """
    index = _validate_index(index)
    if not is_admin():
        raise VNetError("Enabling or disabling an interface requires "
                        "administrator rights.")
    verb = "Enable-NetAdapter" if enabled else "Disable-NetAdapter"
    _powershell(
        f"Get-NetAdapter -InterfaceIndex {index} -ErrorAction Stop | "
        f"{verb} -Confirm:$false -ErrorAction Stop")


# Enable before addressing (a disabled adapter cannot be given an address),
# and disable last, so a batch that does both still ends up where it meant to.
_RUNNERS = {
    "enable": lambda i, c: set_adapter_enabled(i, True),
    "disable": lambda i, c: set_adapter_enabled(i, False),
    "rename": lambda i, c: set_adapter_name(i, c["name"]),
    "dhcp": lambda i, c: set_dhcp(i),
    "static": lambda i, c: set_static_ip(i, c["ip"], c["prefix"], c["gateway"]),
}


def apply_adapter(index, expect_name, ops):
    """Apply a batch of changes to one adapter. Requires administrator rights.

    expect_name is an identity check, and it is the guard that makes the rest
    safe. Interface indexes are stable while an adapter exists but Windows
    reuses them, and the dialog's list can be seconds old by the time someone
    presses Apply — so confirm the adapter is still the one they were looking
    at before touching it.
    """
    ops = _validate_ops(ops)
    index = _validate_index(index)
    expect_name = _validate_name(expect_name)
    if not is_admin():
        raise VNetError("Changing a network interface requires administrator "
                        "rights.")
    live = _adapter_at(index)
    if live is None:
        raise VNetError(f"Interface {index} is no longer present — nothing "
                        "was changed.")
    if live["name"] != expect_name:
        raise VNetError(
            f"Interface {index} is now '{live['name']}', not '{expect_name}' "
            "— nothing was changed. Refresh and try again.")
    for change in ops:
        _RUNNERS[change["op"]](index, change)
    return _adapter_at(index)


# ------------------------------------------------------------- elevation

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
ERROR_CANCELLED = 1223
_ELEVATED_TIMEOUT_MS = 180_000
HELPER_FLAG = "--vnet-helper"


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("fMask", ULONG),
                ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", DWORD), ("hIcon", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE)]


def request_create(name=ADAPTER_NAME, ip=DEFAULT_IP, prefix=DEFAULT_PREFIX):
    """Create the adapter, asking Windows for elevation only if needed."""
    if is_admin():
        return create_adapter(name, ip, prefix)
    return _run_elevated({"action": "create", "name": _validate_name(name),
                          "ip": ip, "prefix": prefix})


def request_remove(instance_id=None, name=ADAPTER_NAME):
    """Remove the adapter, asking Windows for elevation only if needed."""
    if is_admin():
        return remove_adapter(instance_id, name)
    return _run_elevated({"action": "remove", "name": _validate_name(name),
                          "instance_id": instance_id or ""})


def request_apply(index, expect_name, ops):
    """Change one interface, asking Windows for elevation only if needed.

    This used to be the one thing the app could not do without being started
    as administrator, and the editor simply went read-only. That was the
    wrong trade: pinning a static address on a real NIC is the setup step a
    lighting network needs most often, and sending the user away to relaunch
    the app — or to the Windows dialog this window exists to replace — to do
    it is the workflow this project set out to remove.

    The ops are validated here, in the unelevated process, so a typo is
    refused before a permission prompt is raised over it. They are validated
    again on the other side, because the request file between the two is
    writable by the unelevated user and is never trusted.
    """
    ops = _validate_ops(ops)
    index = _validate_index(index)
    expect_name = _validate_name(expect_name)
    if is_admin():
        return apply_adapter(index, expect_name, ops)
    return _run_elevated({"action": "apply", "name": expect_name,
                          "index": index, "ops": ops})


def _helper_target(request_path):
    """(executable, parameters) to relaunch this app in helper mode."""
    quoted = f'"{request_path}"'
    if getattr(sys, "frozen", False):
        return sys.executable, f"{HELPER_FLAG} {quoted}"
    # pythonw avoids a console window flashing over the GUI
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    if windowless.exists():
        exe = windowless
    script = Path(__file__).resolve().parents[2] / "AnyDMX.py"
    return str(exe), f'"{script}" {HELPER_FLAG} {quoted}'


def _run_elevated(request):
    """Run one adapter operation in an elevated copy of this app.

    The request and its result travel through a temp file. The helper
    re-validates everything it reads: the file is writable by the unelevated
    user, so it is treated as input, not as instructions.
    """
    fd, path = tempfile.mkstemp(prefix="anydmx-vnet-", suffix=".json")
    os.close(fd)
    try:
        Path(path).write_text(json.dumps(request), encoding="utf-8")
        exe, params = _helper_target(path)
        _shell_execute_runas(exe, params)
        try:
            result = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise VNetError("The elevated helper did not report a result.")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not result.get("ok"):
        raise VNetError(result.get("error") or "The elevated helper failed.")
    return result.get("state")


def _shell_execute_runas(exe, params):
    """ShellExecuteEx with the runas verb; waits for the helper to finish."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = BOOL

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.nShow = SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            raise VNetError("Administrator permission was declined, so nothing "
                            "was changed.")
        raise VNetError("Could not request administrator rights: "
                        f"{ctypes.FormatError(err).strip()} (error {err})")
    try:
        if kernel32.WaitForSingleObject(info.hProcess, _ELEVATED_TIMEOUT_MS) != 0:
            raise VNetError("The elevated helper did not finish in time.")
    finally:
        kernel32.CloseHandle(info.hProcess)


def helper_main(argv):
    """Entry point for the elevated helper. Returns a process exit code."""
    try:
        path = argv[argv.index(HELPER_FLAG) + 1]
    except (ValueError, IndexError):
        return 2
    result = {"ok": False, "error": "The helper received no valid request."}
    try:
        request = json.loads(Path(path).read_text(encoding="utf-8"))
        action = request.get("action")
        name = _validate_name(request.get("name", ADAPTER_NAME))
        if action == "create":
            state = create_adapter(name, request.get("ip"),
                                   request.get("prefix", DEFAULT_PREFIX))
        elif action == "remove":
            state = remove_adapter(request.get("instance_id") or None, name)
        elif action == "apply":
            # apply_adapter re-validates the index and every op, and refuses
            # to touch an interface whose live name is not the one the
            # request claims. Nothing here is taken on trust.
            state = apply_adapter(request.get("index"), name,
                                  request.get("ops"))
        else:
            raise VNetError(f"Unknown action {action!r}")
        result = {"ok": True, "state": state}
    except VNetError as e:
        result = {"ok": False, "error": str(e)}
    except Exception as e:  # never let the helper die without reporting
        # No log to fall back on, so the exception's class travels with the
        # message — it is all a bug report will have.
        result = {"ok": False,
                  "error": f"Unexpected failure: {type(e).__name__}: {e}"}
    try:
        Path(path).write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        return 1
    return 0 if result["ok"] else 1
