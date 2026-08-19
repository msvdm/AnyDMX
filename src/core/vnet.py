"""The dedicated lighting network interface.

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

from src.utils.logger import get_logger

log = get_logger(__name__)

ADAPTER_NAME = "AnyDMX"
DEFAULT_IP = "2.100.100.0"
DEFAULT_PREFIX = 8
HARDWARE_ID = "*MSLOOP"
LOOPBACK_INF = os.path.expandvars(r"%windir%\inf\netloop.inf")

# Addresses an auto-picking console will accept: the Art-Net spec's own ranges.
ARTNET_PREFIXES = ("2.", "10.")

_PS_TIMEOUT = 60
# Keep PowerShell from flashing a console window in front of the GUI.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class VNetError(Exception):
    """Something went wrong managing the lighting interface."""


# --------------------------------------------------------------- privileges

def is_admin():
    """True when this process can create or remove a device node."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
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


# --------------------------------------------------------------- SetupAPI

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    # Reserved is ULONG_PTR: a pointer field keeps cbSize correct on both
    # 32- and 64-bit, where a DWORD would silently under-size the struct.
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", _GUID),
                ("DevInst", wintypes.DWORD),
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
        wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
        ctypes.POINTER(_SP_DEVINFO_DATA)]
    api.SetupDiCreateDeviceInfoW.restype = wintypes.BOOL
    api.SetupDiSetDeviceRegistryPropertyW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.DWORD,
        ctypes.c_char_p, wintypes.DWORD]
    api.SetupDiSetDeviceRegistryPropertyW.restype = wintypes.BOOL
    api.SetupDiCallClassInstaller.argtypes = [
        wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA)]
    api.SetupDiCallClassInstaller.restype = wintypes.BOOL
    api.SetupDiGetDeviceInstanceIdW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.LPWSTR,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
    api.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    api.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    return api


def _newdev():
    api = ctypes.WinDLL("newdev", use_last_error=True)
    api.UpdateDriverForPlugAndPlayDevicesW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.BOOL)]
    api.UpdateDriverForPlugAndPlayDevicesW.restype = wintypes.BOOL
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
        needed = wintypes.DWORD()
        if not api.SetupDiGetDeviceInstanceIdW(
                dev_info, ctypes.byref(data), buf, MAX_DEVICE_ID_LEN,
                ctypes.byref(needed)):
            _fail("Reading the new device ID")
        instance_id = buf.value

        reboot = wintypes.BOOL(False)
        if not _newdev().UpdateDriverForPlugAndPlayDevicesW(
                None, HARDWARE_ID, LOOPBACK_INF, INSTALLFLAG_FORCE,
                ctypes.byref(reboot)):
            _fail("Binding the loopback driver")
        if reboot.value:
            log.warning("Windows reports a reboot is required for %s", instance_id)
        log.info("Created loopback device %s", instance_id)
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
    log.info("Lighting interface ready: %s at %s/%s", name, ip, prefix)
    return state


def _rename_adapter(instance_id, name, attempts=10, delay=1.0):
    """Rename the freshly created adapter, which Windows called 'Ethernet N'.

    Windows registers the device node before the network stack has finished
    enumerating it as an adapter, so this retries instead of losing the race
    and leaving a correctly-created adapter under the wrong name.
    """
    device, alias = _q(instance_id), _q(name)
    for attempt in range(attempts):
        out = _powershell(
            "$a = Get-NetAdapter -IncludeHidden | "
            f"Where-Object {{ $_.PnPDeviceID -eq '{device}' }}; "
            "if ($a) { "
            f"if ($a.Name -ne '{alias}') {{ Rename-NetAdapter -Name $a.Name "
            f"-NewName '{alias}' }}; 'ok' }}")
        if "ok" in out:
            if attempt:
                log.info("Adapter %s appeared after %.0f s", instance_id,
                         attempt * delay)
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
    log.info("Lighting interface %s addressed %s/%s", name, ip, prefix)


def remove_adapter(instance_id=None, name=ADAPTER_NAME):
    """Remove the lighting interface. Reports plainly when there is none.

    Targets the stored instance ID so Remove can only ever delete the device
    AnyDMX created, never another adapter that happens to share the name.
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
    log.info("Removed lighting interface %s", instance_id)
    return True


def _validate_ipv4(ip):
    parts = str(ip).split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255
                                  for p in parts):
        raise VNetError(f"'{ip}' is not a valid IPv4 address")


def _validate_name(name):
    """Adapter names go into elevated PowerShell, so keep them boring.

    Quotes are escaped everywhere already; this is defence in depth for a
    string that reaches an administrator context.
    """
    name = str(name)
    if not name or len(name) > 64:
        raise VNetError("Adapter name must be 1-64 characters")
    if not all(c.isalnum() or c in " -_" for c in name):
        raise VNetError("Adapter name may only contain letters, digits, "
                        "spaces, hyphens, and underscores")
    return name


# ------------------------------------------------------------- elevation

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
ERROR_CANCELLED = 1223
_ELEVATED_TIMEOUT_MS = 180_000
HELPER_FLAG = "--vnet-helper"


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
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
    shell32.ShellExecuteExW.restype = wintypes.BOOL

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
        else:
            raise VNetError(f"Unknown action {action!r}")
        result = {"ok": True, "state": state}
    except VNetError as e:
        result = {"ok": False, "error": str(e)}
    except Exception as e:  # never let the helper die without reporting
        log.exception("Elevated helper failed")
        result = {"ok": False, "error": f"Unexpected failure: {e}"}
    try:
        Path(path).write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        return 1
    return 0 if result["ok"] else 1
