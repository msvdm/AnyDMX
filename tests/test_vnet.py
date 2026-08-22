"""Tests for the dedicated lighting network interface.

Every Windows call is mocked. No test creates, renames, addresses, or removes
a real network adapter — running the suite must never touch the machine's
networking.
"""

import ctypes
import json
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import vnet_windows as vnet
from src.core.vnet_common import VNetError


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def fake_runner(calls, stdout="", returncode=0, stderr=""):
    """Records every subprocess invocation and returns a canned result."""
    def run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc(stdout=stdout, stderr=stderr, returncode=returncode)
    return run


# --------------------------------------------------------- struct layout

def test_windows_scalars_keep_their_true_widths():
    """DWORD/BOOL are 32-bit in the Windows ABI, whatever the host's c_long is.

    ctypes.wintypes maps them onto c_ulong/c_long, which widen to 64 bits on an
    LP64 host — so the structs below have to spell the widths out to stay right
    when the suite runs off Windows.
    """
    assert ctypes.sizeof(vnet.DWORD) == 4
    assert ctypes.sizeof(vnet.WORD) == 2
    assert ctypes.sizeof(vnet.BOOL) == 4
    assert ctypes.sizeof(vnet.ULONG) == 4
    assert ctypes.sizeof(vnet._GUID) == 16


def test_devinfo_struct_matches_the_windows_layout():
    """Reserved is ULONG_PTR; a DWORD there would under-size cbSize on x64.

    Offsets rather than a total: they pin every field independently, and they
    are the thing SetupAPI actually reads.
    """
    fields = vnet._SP_DEVINFO_DATA
    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert fields.cbSize.offset == 0
    assert fields.ClassGuid.offset == 4
    assert fields.DevInst.offset == 20
    assert fields.Reserved.offset == 24
    assert fields.Reserved.size == pointer
    assert ctypes.sizeof(fields) == 24 + pointer


# ------------------------------------------------------- privilege gating

def test_create_requires_admin(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    called = []
    monkeypatch.setattr(vnet, "create_device_node",
                        lambda: called.append("created"))
    with pytest.raises(VNetError) as excinfo:
        vnet.create_adapter()
    assert "administrator" in str(excinfo.value).lower()
    assert called == []  # nothing was created


def test_remove_requires_admin(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError) as excinfo:
        vnet.remove_adapter(instance_id="ROOT\\NET\\0001")
    assert "administrator" in str(excinfo.value).lower()
    assert calls == []


def test_create_refuses_when_the_adapter_already_exists(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "find_adapter",
                        lambda name=vnet.ADAPTER_NAME: {"name": name})
    created = []
    monkeypatch.setattr(vnet, "create_device_node",
                        lambda: created.append("x"))
    with pytest.raises(VNetError, match="already exists"):
        vnet.create_adapter()
    assert created == []


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("bad", ["", "2.100.100", "2.100.100.256", "hello",
                                 "2.100.100.0.1", "2.100.-1.0"])
def test_invalid_addresses_are_rejected(bad, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError):
        vnet.configure_adapter(ip=bad)
    assert calls == []  # rejected before touching the system


def test_valid_address_is_accepted(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    vnet.configure_adapter(name="AnyDMX", ip="2.100.100.0", prefix=8)
    assert len(calls) == 1


@pytest.mark.parametrize("bad_prefix", [0, 33, -1])
def test_invalid_prefix_is_rejected(bad_prefix, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError, match="Prefix"):
        vnet.configure_adapter(ip="2.100.100.0", prefix=bad_prefix)
    assert calls == []


# ---------------------------------------------------- command construction

def test_configure_sets_the_address_without_a_gateway(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    vnet.configure_adapter(name="AnyDMX", ip="2.100.100.0", prefix=8)
    script = calls[0][-1]
    assert "New-NetIPAddress" in script
    assert "-IPAddress '2.100.100.0'" in script
    assert "-PrefixLength 8" in script
    # A gateway would turn the 2.0.0.0/8 route into a path for real traffic.
    assert "DefaultGateway" not in script
    # Public profile would let the firewall filter lighting traffic.
    assert "-NetworkCategory Private" in script
    assert "-Dhcp Disabled" in script


def test_configure_coerces_prefix_to_int(monkeypatch):
    """A prefix arriving from JSON settings as a string must not inject text."""
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    vnet.configure_adapter(ip="2.100.100.0", prefix="8")
    assert "-PrefixLength 8" in calls[0][-1]


# ----------------------------------------------------------- state parsing

def test_find_adapter_parses_windows_output(monkeypatch):
    payload = json.dumps({"name": "AnyDMX", "status": "Up",
                          "instance_id": "ROOT\\NET\\0001",
                          "ip": "2.100.100.0", "prefix": 8})
    monkeypatch.setattr(subprocess, "run", fake_runner([], stdout=payload))
    state = vnet.find_adapter()
    assert state["name"] == "AnyDMX"
    assert state["ip"] == "2.100.100.0"
    assert state["instance_id"] == "ROOT\\NET\\0001"


def test_find_adapter_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_runner([], stdout="   \n"))
    assert vnet.find_adapter() is None


def test_artnet_range_addresses_filters_to_usable_ranges(monkeypatch):
    payload = json.dumps([
        {"IPAddress": "192.168.31.248", "InterfaceAlias": "BNR Inside"},
        {"IPAddress": "2.100.100.0", "InterfaceAlias": "AnyDMX"},
        {"IPAddress": "10.5.5.5", "InterfaceAlias": "Lighting"},
        {"IPAddress": "169.254.83.107", "InterfaceAlias": "Tailscale"},
    ])
    monkeypatch.setattr(subprocess, "run", fake_runner([], stdout=payload))
    assert vnet.artnet_range_addresses() == ["2.100.100.0", "10.5.5.5"]


def test_powershell_failure_becomes_a_readable_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        fake_runner([], returncode=1,
                                    stderr="Access is denied.\nat line:1"))
    with pytest.raises(VNetError, match="Access is denied"):
        vnet.find_adapter()


# ------------------------------------------------------------- removal

def test_remove_reports_absence_rather_than_failing(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "find_adapter", lambda name=vnet.ADAPTER_NAME: None)
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    assert vnet.remove_adapter() is False
    assert calls == []


def test_remove_targets_the_stored_instance_id(monkeypatch):
    """Remove must delete the device AnyDMX made, never a same-named stranger."""
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    looked_up = []
    monkeypatch.setattr(vnet, "find_adapter",
                        lambda name=vnet.ADAPTER_NAME: looked_up.append(name))
    assert vnet.remove_adapter(instance_id="ROOT\\NET\\0007") is True
    assert calls == [["pnputil", "/remove-device", "ROOT\\NET\\0007"]]
    assert looked_up == []  # the stored ID was trusted, no name lookup


def test_remove_surfaces_pnputil_failure(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run",
                        fake_runner([], returncode=1,
                                    stdout="Failed to remove device."))
    with pytest.raises(VNetError, match="Failed to remove device"):
        vnet.remove_adapter(instance_id="ROOT\\NET\\0007")


# ------------------------------------------------------- device creation

def test_create_device_node_requires_the_inbox_driver(monkeypatch):
    monkeypatch.setattr(vnet.os.path, "exists", lambda p: False)
    with pytest.raises(VNetError, match="loopback driver not found"):
        vnet.create_device_node()


def test_half_built_adapter_names_the_device_for_cleanup(monkeypatch):
    """A device that exists but could not be finished must not go unnoticed."""
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "find_adapter", lambda name=vnet.ADAPTER_NAME: None)
    monkeypatch.setattr(vnet, "create_device_node", lambda: "ROOT\\NET\\0009")

    def boom(instance_id, name):
        raise VNetError("The new adapter did not appear in Windows.")

    monkeypatch.setattr(vnet, "_rename_adapter", boom)
    with pytest.raises(VNetError) as excinfo:
        vnet.create_adapter()
    message = str(excinfo.value)
    assert "ROOT\\NET\\0009" in message
    assert "Remove" in message


# ------------------------------------------------------- enumeration race

def test_rename_retries_until_windows_enumerates_the_adapter(monkeypatch):
    """The device node exists before the NIC does; losing that race is a bug."""
    outputs = ["", "", "ok"]
    seen = []

    def fake_ps(script):
        seen.append(script)
        return outputs.pop(0)

    monkeypatch.setattr(vnet, "_powershell", fake_ps)
    monkeypatch.setattr(vnet.time, "sleep", lambda s: None)
    vnet._rename_adapter("ROOT\\NET\\0001", "AnyDMX", attempts=5, delay=0)
    assert len(seen) == 3
    assert "Rename-NetAdapter" in seen[0]


def test_rename_gives_up_with_a_clear_message(monkeypatch):
    monkeypatch.setattr(vnet, "_powershell", lambda script: "")
    monkeypatch.setattr(vnet.time, "sleep", lambda s: None)
    with pytest.raises(VNetError, match="did not appear"):
        vnet._rename_adapter("ROOT\\NET\\0001", "AnyDMX", attempts=3, delay=0)


def test_powershell_strings_are_escaped():
    assert vnet._q("O'Brien") == "O''Brien"


def test_adapter_name_with_a_quote_cannot_break_out(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_runner(calls, stdout=""))
    vnet.find_adapter("Any'DMX")
    assert "Any''DMX" in calls[0][-1]


# ------------------------------------------------------------- elevation

def test_request_create_runs_directly_when_already_admin(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "create_adapter",
                        lambda n, i, p: {"name": n, "ip": i, "prefix": p})
    monkeypatch.setattr(vnet, "_run_elevated",
                        lambda req: pytest.fail("should not elevate"))
    state = vnet.request_create("AnyDMX", "2.100.100.0", 8)
    assert state["ip"] == "2.100.100.0"


def test_request_create_elevates_when_not_admin(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    monkeypatch.setattr(vnet, "create_adapter",
                        lambda *a: pytest.fail("must not run unelevated"))
    seen = {}
    monkeypatch.setattr(vnet, "_run_elevated", lambda req: seen.update(req))
    vnet.request_create("AnyDMX", "2.100.100.0", 8)
    assert seen["action"] == "create"
    assert seen["ip"] == "2.100.100.0"


def test_declining_the_uac_prompt_is_reported_plainly(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: False)

    def decline(exe, params):
        raise VNetError("Administrator permission was declined, so nothing "
                        "was changed.")

    monkeypatch.setattr(vnet, "_shell_execute_runas", decline)
    with pytest.raises(VNetError, match="declined"):
        vnet.request_create("AnyDMX", "2.100.100.0", 8)


def test_elevated_failure_travels_back_to_the_caller(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: False)

    def helper(exe, params):
        # stand in for the elevated child writing its result
        path = params.split('"')[-2]
        pathlib.Path(path).write_text(
            json.dumps({"ok": False, "error": "Windows said no"}),
            encoding="utf-8")

    monkeypatch.setattr(vnet, "_shell_execute_runas", helper)
    with pytest.raises(VNetError, match="Windows said no"):
        vnet.request_create("AnyDMX", "2.100.100.0", 8)


def test_request_cleans_up_its_temp_file(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    captured = {}

    def helper(exe, params):
        path = params.split('"')[-2]
        captured["path"] = path
        pathlib.Path(path).write_text(json.dumps({"ok": True, "state": None}),
                                      encoding="utf-8")

    monkeypatch.setattr(vnet, "_shell_execute_runas", helper)
    vnet.request_create("AnyDMX", "2.100.100.0", 8)
    assert not pathlib.Path(captured["path"]).exists()


# ------------------------------------------------- helper input validation

def test_helper_rejects_an_unknown_action(tmp_path):
    """The request file is writable unelevated, so it is input, not orders."""
    path = tmp_path / "req.json"
    path.write_text(json.dumps({"action": "format-c", "name": "AnyDMX"}),
                    encoding="utf-8")
    code = vnet.helper_main([vnet.HELPER_FLAG, str(path)])
    assert code == 1
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "Unknown action" in result["error"]


def test_helper_rejects_a_dangerous_adapter_name(tmp_path, monkeypatch):
    monkeypatch.setattr(vnet, "create_adapter",
                        lambda *a: pytest.fail("must not reach creation"))
    path = tmp_path / "req.json"
    path.write_text(json.dumps({"action": "create", "ip": "2.100.100.0",
                                "prefix": 8,
                                "name": "A'; Remove-Item -Recurse #"}),
                    encoding="utf-8")
    assert vnet.helper_main([vnet.HELPER_FLAG, str(path)]) == 1
    assert "may only contain" in json.loads(path.read_text())["error"]


def test_helper_reports_success(tmp_path, monkeypatch):
    monkeypatch.setattr(vnet, "create_adapter",
                        lambda n, i, p: {"name": n, "ip": i})
    path = tmp_path / "req.json"
    path.write_text(json.dumps({"action": "create", "name": "AnyDMX",
                                "ip": "2.100.100.0", "prefix": 8}),
                    encoding="utf-8")
    assert vnet.helper_main([vnet.HELPER_FLAG, str(path)]) == 0
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["state"]["name"] == "AnyDMX"


def test_helper_without_a_request_path_exits_nonzero():
    assert vnet.helper_main(["AnyDMX.py"]) == 2


@pytest.mark.parametrize("bad", ["", "a" * 65, "Any/DMX", "Any\\DMX",
                                 "Any'DMX", "Any;DMX"])
def test_adapter_names_are_restricted(bad):
    with pytest.raises(VNetError):
        vnet._validate_name(bad)


def test_ordinary_adapter_names_pass():
    for good in ("AnyDMX", "Any DMX", "Any-DMX_2"):
        assert vnet._validate_name(good) == good


# ------------------------------------------------------- adapter listing

# Trimmed from a real machine: two addressed physical NICs (both carrying a
# default route), a VirtualBox adapter that is present but has no address, the
# AnyDMX loopback, and a Tailscale tunnel.
LIST_PAYLOAD = json.dumps([
    {"name": "BNR Inside", "description": "Realtek PCIe GbE Family Controller",
     "index": 9, "status": "Up", "admin_up": True, "physical": True,
     "link_speed": "1 Gbps", "mac": "00-E0-4C-68-24-3C",
     "instance_id": "PCI\\VEN_10EC",
     "addresses": [{"ip": "192.168.31.248", "prefix": 24}],
     "dhcp": True, "gateway": "192.168.31.3", "category": "Private"},
    {"name": "Ethernet", "description": "VirtualBox Host-Only Ethernet Adapter",
     "index": 8, "status": "Not Present", "admin_up": False, "physical": False,
     "link_speed": "0 bps", "mac": "0A-00-27-00-00-0A",
     "instance_id": "ROOT\\NET\\0000", "addresses": [],
     "dhcp": False, "gateway": None, "category": None},
    {"name": "AnyDMX", "description": "Microsoft KM-TEST Loopback Adapter",
     "index": 33, "status": "Up", "admin_up": True, "physical": True,
     "link_speed": "1.2 Gbps", "mac": "02-00-4C-4F-4F-50",
     "instance_id": "ROOT\\NET\\0007",
     "addresses": [{"ip": "2.100.100.0", "prefix": 8}],
     "dhcp": False, "gateway": None, "category": "Private"},
    {"name": "Tailscale", "description": "Tailscale Tunnel",
     "index": 32, "status": "Up", "admin_up": True, "physical": False,
     "link_speed": "100 Gbps", "mac": "", "instance_id": "ROOT\\NET\\0001",
     "addresses": [{"ip": "169.254.83.107", "prefix": 16}],
     "dhcp": False, "gateway": None, "category": "Private"},
])


def test_list_adapters_parses_every_field(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        fake_runner(calls, stdout=LIST_PAYLOAD))
    rows = vnet.list_adapters()
    assert [r["name"] for r in rows] == ["BNR Inside", "Ethernet", "AnyDMX",
                                         "Tailscale"]
    inside = rows[0]
    assert inside["index"] == 9 and isinstance(inside["index"], int)
    assert inside["dhcp"] is True
    assert inside["gateway"] == "192.168.31.3"
    assert inside["category"] == "Private"
    assert inside["link_speed"] == "1 Gbps"
    assert inside["addresses"] == [{"ip": "192.168.31.248", "prefix": 24}]
    # -Depth 4 is what keeps the nested addresses from serialising as type
    # names; if it ever regresses this assertion is the one that fails.
    assert "-Depth 4" in calls[0][-1]


def test_list_adapters_handles_an_adapter_with_no_address(monkeypatch):
    """An adapter with no IPv4 comes back as [], never [None].

    The ContainsKey guard in the script is what makes that true: @($h[$i]) on
    a missing key yields a one-element array holding null.
    """
    monkeypatch.setattr(subprocess, "run",
                        fake_runner([], stdout=LIST_PAYLOAD))
    empty = next(r for r in vnet.list_adapters() if r["name"] == "Ethernet")
    assert empty["addresses"] == []


def test_list_adapters_keeps_every_address_in_order(monkeypatch):
    payload = json.dumps([{
        "name": "Multi", "description": "d", "index": 4, "status": "Up",
        "admin_up": True, "physical": True, "link_speed": "1 Gbps",
        "mac": "", "instance_id": "x", "dhcp": False,
        "gateway": None, "category": None,
        "addresses": [{"ip": "10.0.0.5", "prefix": 8},
                      {"ip": "2.0.0.5", "prefix": 8}]}])
    monkeypatch.setattr(subprocess, "run", fake_runner([], stdout=payload))
    rows = vnet.list_adapters()
    assert [a["ip"] for a in rows[0]["addresses"]] == ["10.0.0.5", "2.0.0.5"]


def test_the_loopback_adapter_counts_as_physical(monkeypatch):
    """The AnyDMX adapter is physical; Tailscale is not.

    Get-NetAdapter -Physical filters on Virtual, not HardwareInterface, and
    the KM-TEST loopback reports Virtual=False. That is deliberate here: it is
    a real NDIS miniport with a device node and belongs in the editable list
    beside the real NICs. HardwareInterface is False for adapters users
    certainly consider real, so it is not a usable substitute — this test
    exists to stop the "fix".
    """
    monkeypatch.setattr(subprocess, "run",
                        fake_runner([], stdout=LIST_PAYLOAD))
    physical = {r["name"]: r["physical"] for r in vnet.list_adapters()}
    assert physical["AnyDMX"] is True
    assert physical["Tailscale"] is False


# --------------------------------------------- interface settings: gating

@pytest.mark.parametrize("call", [
    lambda: vnet.set_static_ip(9, "2.0.0.1", 8),
    lambda: vnet.set_dhcp(9),
    lambda: vnet.set_adapter_name(9, "Lighting"),
    lambda: vnet.set_adapter_enabled(9, False),
])
def test_changing_an_interface_requires_admin(monkeypatch, call):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError, match="administrator"):
        call()
    assert calls == []


@pytest.mark.parametrize("bad", ["", "2.100.100", "2.100.100.256", "hello"])
def test_a_bad_address_never_reaches_the_system(monkeypatch, bad):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError):
        vnet.set_static_ip(9, bad, 8)
    assert calls == []


@pytest.mark.parametrize("bad", [0, 33, -1])
def test_a_bad_prefix_never_reaches_the_system(monkeypatch, bad):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError, match="Prefix"):
        vnet.set_static_ip(9, "2.0.0.1", bad)
    assert calls == []


def test_a_bad_gateway_never_reaches_the_system(monkeypatch):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError):
        vnet.set_static_ip(9, "2.0.0.1", 8, gateway="not-an-ip")
    assert calls == []


@pytest.mark.parametrize("bad", ["33; Remove-Item", None, 0, "", 0x1000000])
def test_a_bad_interface_index_never_reaches_the_system(monkeypatch, bad):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError):
        vnet.set_dhcp(bad)
    assert calls == []


# ---------------------------------------- interface settings: the commands

def test_settings_target_the_interface_index(monkeypatch):
    """A batch may rename and re-address at once, so the name is not a handle."""
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    vnet.set_static_ip("9", "2.100.100.5", "8")
    script = calls[0][-1]
    assert "-InterfaceIndex 9" in script
    assert "-IPAddress '2.100.100.5'" in script
    assert "-PrefixLength 8" in script


def test_a_gateway_is_set_only_when_one_is_given(monkeypatch):
    """The AnyDMX no-gateway rule must NOT be copied wholesale here.

    configure_adapter() deliberately never sets a gateway, because the
    2.0.0.0/8 on-link route must not become a path for ordinary traffic. A
    user pinning a static address on their real NIC has the opposite problem:
    without a gateway they lose every route off the subnet.
    """
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    vnet.set_static_ip(9, "192.168.1.50", 24)
    assert "DefaultGateway" not in calls[0][-1]
    vnet.set_static_ip(9, "192.168.1.50", 24, gateway="192.168.1.1")
    assert "-DefaultGateway '192.168.1.1'" in calls[1][-1]


def test_dhcp_also_resets_the_dns_servers(monkeypatch):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    vnet.set_dhcp(9)
    script = calls[0][-1]
    assert "-Dhcp Enabled" in script
    assert "-ResetServerAddresses" in script


def test_enable_and_disable_use_literal_verbs(monkeypatch):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    vnet.set_adapter_enabled(9, True)
    vnet.set_adapter_enabled(9, False)
    assert "Enable-NetAdapter" in calls[0][-1]
    assert "Disable-NetAdapter" in calls[1][-1]


def test_a_rename_cannot_break_out_of_its_quotes(monkeypatch):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError, match="may only contain"):
        vnet.set_adapter_name(9, "Any'; Remove-Item #")
    assert calls == []


# ------------------------------------------------------------- batching

def _stub_live(monkeypatch, name="BNR Inside", index=9):
    """Make _adapter_at report one adapter without touching PowerShell."""
    monkeypatch.setattr(vnet, "list_adapters",
                        lambda: [{"name": name, "index": index}])


def test_a_batch_is_ordered_so_it_cannot_undo_itself():
    """Enable first, disable last — addressing a disabled adapter fails."""
    ops = vnet._validate_ops([{"op": "disable"},
                              {"op": "rename", "name": "Lighting"}])
    assert [o["op"] for o in ops] == ["rename", "disable"]
    ops = vnet._validate_ops([{"op": "static", "ip": "2.0.0.1", "prefix": 8},
                              {"op": "enable"}])
    assert [o["op"] for o in ops] == ["enable", "static"]


@pytest.mark.parametrize("ops", [
    [{"op": "enable"}, {"op": "disable"}],
    [{"op": "dhcp"}, {"op": "static", "ip": "2.0.0.1", "prefix": 8}],
    [{"op": "dhcp"}, {"op": "dhcp"}],
    [{"op": "format-c"}],
    [],
    "not-a-list",
])
def test_a_nonsense_batch_is_rejected(ops):
    with pytest.raises(VNetError):
        vnet._validate_ops(ops)


def test_a_batch_is_validated_before_any_of_it_runs(monkeypatch):
    """Half a reconfigured adapter is worse than none: the user cannot see
    which half."""
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    _stub_live(monkeypatch)
    with pytest.raises(VNetError):
        vnet.apply_adapter(9, "BNR Inside",
                           [{"op": "rename", "name": "Fine"},
                            {"op": "static", "ip": "nope", "prefix": 8}])
    assert calls == []


def test_apply_requires_admin(monkeypatch):
    calls = []
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    monkeypatch.setattr(subprocess, "run", fake_runner(calls))
    with pytest.raises(VNetError, match="administrator"):
        vnet.apply_adapter(9, "BNR Inside", [{"op": "dhcp"}])
    assert calls == []


def test_apply_refuses_when_the_adapter_changed_underneath(monkeypatch):
    """Windows recycles interface indexes, and the dialog's list can be
    seconds old. This is what stops a stale row re-addressing the wrong NIC."""
    ran = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "set_dhcp", lambda i: ran.append(i))
    _stub_live(monkeypatch, name="Something Else")
    with pytest.raises(VNetError) as e:
        vnet.apply_adapter(9, "BNR Inside", [{"op": "dhcp"}])
    assert "Something Else" in str(e.value) and "BNR Inside" in str(e.value)
    assert ran == []


def test_apply_reports_an_adapter_that_vanished(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "list_adapters", lambda: [])
    with pytest.raises(VNetError, match="no longer present"):
        vnet.apply_adapter(9, "BNR Inside", [{"op": "dhcp"}])


def test_apply_runs_the_whole_batch_in_order(monkeypatch):
    ran = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "set_adapter_enabled",
                        lambda i, on: ran.append(("enable", i, on)))
    monkeypatch.setattr(vnet, "set_adapter_name",
                        lambda i, n: ran.append(("rename", i, n)))
    monkeypatch.setattr(vnet, "set_static_ip",
                        lambda i, ip, p, g: ran.append(("static", i, ip, p, g)))
    _stub_live(monkeypatch)
    vnet.apply_adapter(9, "BNR Inside", [
        {"op": "static", "ip": "2.100.100.5", "prefix": 8, "gateway": ""},
        {"op": "rename", "name": "Lighting"},
        {"op": "enable"}])
    assert [r[0] for r in ran] == ["enable", "rename", "static"]
    assert ran[2] == ("static", 9, "2.100.100.5", 8, "")


# ------------------------------------ the elevated helper's three actions

def _helper(tmp_path, request):
    """Run helper_main over one request file and give back (code, result)."""
    path = tmp_path / "req.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    code = vnet.helper_main([vnet.HELPER_FLAG, str(path)])
    return code, json.loads(path.read_text(encoding="utf-8"))


def test_the_elevated_helper_knows_only_create_remove_and_apply(tmp_path,
                                                                monkeypatch):
    """The request file is writable by the unelevated user, so the set of
    things it can ask for is the security boundary. Three, and no more."""
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    code, result = _helper(tmp_path, {"action": "configure", "name": "AnyDMX"})
    assert code == 1 and result["ok"] is False
    assert "Unknown action" in result["error"]


def test_the_helper_applies_a_valid_change(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "set_static_ip",
                        lambda i, ip, p, g: ran.append((i, ip, p, g)))
    _stub_live(monkeypatch)
    code, result = _helper(tmp_path, {
        "action": "apply", "name": "BNR Inside", "index": 9,
        "ops": [{"op": "static", "ip": "2.100.100.5", "prefix": 8,
                 "gateway": "2.0.0.1"}]})
    assert code == 0 and result["ok"] is True
    assert ran == [(9, "2.100.100.5", 8, "2.0.0.1")]


def test_the_helper_revalidates_an_apply_it_is_handed(tmp_path, monkeypatch):
    """Whatever the unelevated side checked, this side checks again.

    The file can be rewritten between the two, so every one of these has to
    be refused by the elevated process on its own account.
    """
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "set_static_ip",
                        lambda *a: pytest.fail("a bad request was applied"))
    monkeypatch.setattr(vnet, "set_adapter_enabled",
                        lambda *a: pytest.fail("a bad request was applied"))
    _stub_live(monkeypatch)
    bad = [
        # An adapter that is not the one the request claims it is.
        {"action": "apply", "name": "Lighting", "index": 9,
         "ops": [{"op": "disable"}]},
        {"action": "apply", "name": "BNR Inside", "index": "9; shutdown",
         "ops": [{"op": "disable"}]},
        {"action": "apply", "name": "BNR Inside", "index": 9,
         "ops": [{"op": "static", "ip": "not-an-ip", "prefix": 8}]},
        {"action": "apply", "name": "BNR Inside", "index": 9,
         "ops": [{"op": "static", "ip": "2.0.0.1", "prefix": 99}]},
        {"action": "apply", "name": "BNR Inside", "index": 9,
         "ops": [{"op": "rename", "name": "x'; Remove-NetAdapter"}]},
        {"action": "apply", "name": "BNR Inside", "index": 9,
         "ops": [{"op": "reboot"}]},
        {"action": "apply", "name": "BNR Inside", "index": 9, "ops": []},
        {"action": "apply", "name": "BNR Inside", "index": 9,
         "ops": [{"op": "enable"}, {"op": "disable"}]},
    ]
    for request in bad:
        code, result = _helper(tmp_path, request)
        assert code == 1 and result["ok"] is False, request
        assert result["error"]


def test_request_apply_elevates_only_when_it_has_to(monkeypatch):
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "_run_elevated",
                        lambda r: pytest.fail("elevated as administrator"))
    monkeypatch.setattr(vnet, "set_dhcp", lambda i: None)
    _stub_live(monkeypatch)
    vnet.request_apply(9, "BNR Inside", [{"op": "dhcp"}])

    sent = []
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    monkeypatch.setattr(vnet, "_run_elevated", lambda r: sent.append(r))
    vnet.request_apply(9, "BNR Inside", [{"op": "dhcp"}])
    assert sent == [{"action": "apply", "name": "BNR Inside", "index": 9,
                     "ops": [{"op": "dhcp"}]}]


def test_request_apply_refuses_a_typo_before_prompting(monkeypatch):
    """A permission prompt raised over a request that cannot succeed is a
    prompt the user learns to click through."""
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    monkeypatch.setattr(vnet, "_run_elevated",
                        lambda r: pytest.fail("prompted over a bad request"))
    with pytest.raises(VNetError, match="not a valid IPv4"):
        vnet.request_apply(9, "BNR Inside",
                           [{"op": "static", "ip": "2.100.100", "prefix": 8}])
