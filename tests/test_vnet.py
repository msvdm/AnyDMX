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

from src.core import vnet
from src.core.vnet import VNetError


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

def test_devinfo_struct_is_sized_for_this_architecture():
    """Reserved is ULONG_PTR; a DWORD there would under-size cbSize on x64."""
    expected = 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24
    assert ctypes.sizeof(vnet._SP_DEVINFO_DATA) == expected


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
