"""Tests for the Linux lighting interface backend.

Every nmcli call is mocked. No test creates, addresses, enables or removes a
real interface — running the suite must never touch the machine's networking,
and a contributor with no NetworkManager, no admin rights and no dongle must
still get a clean green run.

The device fixtures below are real `nmcli -t -f all device show` output from
the machine this backend was written on, trimmed to the fields the parser
reads.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import vnet_linux as vnet
from src.core.vnet_common import VNetError


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


ETHERNET = """\
GENERAL.DEVICE:ens2
GENERAL.TYPE:ethernet
GENERAL.VENDOR:Red Hat, Inc.
GENERAL.PRODUCT:Virtio network device
GENERAL.DRIVER:virtio_net
GENERAL.HWADDR:00:A0:98:0A:78:A2
GENERAL.STATE:100 (connected)
GENERAL.IS-SOFTWARE:no
GENERAL.NM-MANAGED:yes
GENERAL.CONNECTION:Wired connection 1
GENERAL.CON-UUID:16b7676e-40b8-3b51-b414-7d7c32c7969a
CAPABILITIES.SPEED:unknown
INTERFACE-FLAGS.UP:yes
IP4.ADDRESS[1]:192.168.100.126/24
IP4.GATEWAY:192.168.100.1
IP6.ADDRESS[1]:fe80::ab36:36c0:ef4b:9a2/64
"""

ANYDMX = """\
GENERAL.DEVICE:AnyDMX
GENERAL.TYPE:dummy
GENERAL.DRIVER:dummy
GENERAL.HWADDR:6A:31:0C:9E:44:01
GENERAL.STATE:100 (connected)
GENERAL.IS-SOFTWARE:yes
GENERAL.NM-MANAGED:yes
GENERAL.CONNECTION:AnyDMX
GENERAL.CON-UUID:2f0d8a71-1111-2222-3333-abcdefabcdef
CAPABILITIES.SPEED:unknown
IP4.ADDRESS[1]:2.100.100.0/8
IP4.GATEWAY:
"""

LOOPBACK = """\
GENERAL.DEVICE:lo
GENERAL.TYPE:loopback
GENERAL.STATE:100 (connected)
GENERAL.IS-SOFTWARE:yes
GENERAL.NM-MANAGED:yes
IP4.ADDRESS[1]:127.0.0.1/8
"""

UNMANAGED = """\
GENERAL.DEVICE:enp3s0
GENERAL.TYPE:ethernet
GENERAL.HWADDR:AA:BB:CC:DD:EE:FF
GENERAL.STATE:10 (unmanaged)
GENERAL.IS-SOFTWARE:no
GENERAL.NM-MANAGED:no
GENERAL.CONNECTION:
GENERAL.CON-UUID:
IP4.ADDRESS[1]:10.0.0.5/24
"""

IFINDEX = {"ens2": 2, "AnyDMX": 7, "lo": 1, "enp3s0": 3, "docker-shim": 9}


@pytest.fixture
def nmcli(monkeypatch):
    """Drive the backend with canned nmcli output; record every argv.

    Returns the call list. Device blocks default to one ethernet plus the
    AnyDMX dummy plus loopback; a test can swap them with `.devices = ...`.
    """
    class Fake:
        def __init__(self):
            self.calls = []
            self.devices = ETHERNET + "\n" + ANYDMX + "\n" + LOOPBACK
            self.method = "auto"
            self.fail_with = None

        def run(self, cmd, **kwargs):
            self.calls.append(cmd)
            if self.fail_with is not None:
                return FakeProc(stderr=self.fail_with, returncode=4)
            if cmd[1:5] == ["-t", "-f", "all", "device"]:
                return FakeProc(stdout=self.devices)
            if "connection" in cmd and "show" in cmd and "-f" in cmd:
                return FakeProc(stdout=f"ipv4.method:{self.method}\n"
                                       "connection.zone:\n")
            return FakeProc(stdout="")

    fake = Fake()
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(vnet, "_ifindex", lambda dev: IFINDEX.get(dev))
    return fake


def argv_of(calls, *needles):
    """The one recorded call containing every needle, or a clear failure."""
    found = [c for c in calls if all(n in c for n in needles)]
    assert len(found) == 1, f"expected one {needles} call, got {found}"
    return found[0]


# ------------------------------------------------------------- parsing

def test_a_value_keeps_its_own_colons():
    """MAC and IPv6 values are made of colons; splitting on all of them
    shreds every address in the list. This is the Linux twin of the
    ConvertTo-Json -Depth 2 trap on the Windows side."""
    block = vnet._kv_block("GENERAL.HWADDR:00:A0:98:0A:78:A2\n"
                           "IP6.ADDRESS[1]:fe80::ab36:36c0:ef4b:9a2/64\n")
    assert block["GENERAL.HWADDR"] == "00:A0:98:0A:78:A2"
    assert block["IP6.ADDRESS[1]"] == "fe80::ab36:36c0:ef4b:9a2/64"


def test_list_adapters_reads_the_fields_the_editor_shows(nmcli):
    adapters = {a["name"]: a for a in vnet.list_adapters()}
    ens2 = adapters["ens2"]
    assert ens2["index"] == 2
    assert ens2["mac"] == "00:A0:98:0A:78:A2"
    assert ens2["addresses"] == [{"ip": "192.168.100.126", "prefix": 24}]
    assert ens2["gateway"] == "192.168.100.1"
    assert ens2["dhcp"] is True
    assert ens2["status"] == "Up"
    assert ens2["admin_up"] is True
    assert ens2["managed"] is True
    assert ens2["description"] == "Red Hat, Inc. Virtio network device"
    assert ens2["link_speed"] == ""          # "unknown" is not a speed
    assert ens2["instance_id"] == "16b7676e-40b8-3b51-b414-7d7c32c7969a"


def test_a_static_interface_is_not_reported_as_dhcp(nmcli):
    nmcli.method = "manual"
    assert vnet.list_adapters()[0]["dhcp"] is False


def test_the_loopback_device_is_not_offered(nmcli):
    """Nobody points a console at lo, and NM manages it from 1.42 on."""
    assert "lo" not in [a["name"] for a in vnet.list_adapters()]


def test_the_anydmx_dummy_counts_as_physical(nmcli):
    """The deliberate echo of the Windows KM-TEST loopback rule.

    IS-SOFTWARE says yes for a dummy, which would file the AnyDMX interface
    away as scenery. It is a real device with a real address that the user
    must be able to edit beside the real NICs, exactly as on Windows. This
    test exists to stop the "fix".
    """
    anydmx = next(a for a in vnet.list_adapters() if a["name"] == "AnyDMX")
    assert anydmx["physical"] is True
    assert anydmx["type"] == "dummy"


def test_an_unmanaged_device_is_listed_but_flagged(nmcli):
    """List it — the user can see it exists — but do not pretend to own it."""
    nmcli.devices = UNMANAGED
    adapter = vnet.list_adapters()[0]
    assert adapter["name"] == "enp3s0"
    assert adapter["managed"] is False
    assert adapter["status"] == "Unmanaged"


def test_find_adapter_reports_the_lighting_interface(nmcli):
    state = vnet.find_adapter("AnyDMX")
    assert state["ip"] == "2.100.100.0"
    assert state["prefix"] == 8
    assert state["instance_id"] == "2f0d8a71-1111-2222-3333-abcdefabcdef"


def test_find_adapter_returns_none_when_absent(nmcli):
    nmcli.devices = ETHERNET
    assert vnet.find_adapter("AnyDMX") is None


def test_artnet_range_addresses_filters_to_usable_ranges(nmcli):
    """Only what an auto-picking console would accept — 2.x and 10.x."""
    assert vnet.artnet_range_addresses() == ["2.100.100.0"]


# ------------------------------------------------------------- creation

def test_create_builds_a_persistent_dummy_connection(nmcli):
    nmcli.devices = ETHERNET
    vnet.create_adapter("AnyDMX", "2.100.100.0", 8)
    add = argv_of(nmcli.calls, "add", "dummy")
    assert add[:6] == ["nmcli", "connection", "add", "type", "dummy", "ifname"]
    assert "2.100.100.0/8" in add
    # autoconnect is what makes it survive a reboot, which is the whole
    # reason this backend is NetworkManager and not `ip addr add`.
    assert add[add.index("connection.autoconnect") + 1] == "yes"
    assert add[add.index("ipv4.method") + 1] == "manual"
    argv_of(nmcli.calls, "up", "AnyDMX")


def test_create_refuses_when_the_interface_already_exists(nmcli):
    with pytest.raises(VNetError, match="already exists"):
        vnet.create_adapter("AnyDMX", "2.100.100.0", 8)
    assert not any("add" in c for c in nmcli.calls)


@pytest.mark.parametrize("bad", ["2.100.100", "2.100.100.256", "", "not.an.ip.x"])
def test_a_bad_address_never_reaches_nmcli(bad, nmcli):
    nmcli.devices = ETHERNET
    with pytest.raises(VNetError):
        vnet.create_adapter("AnyDMX", bad, 8)
    assert not any("add" in c for c in nmcli.calls)


@pytest.mark.parametrize("bad", [0, 33, -1, "eight"])
def test_a_bad_prefix_never_reaches_nmcli(bad, nmcli):
    nmcli.devices = ETHERNET
    with pytest.raises(VNetError):
        vnet.create_adapter("AnyDMX", "2.100.100.0", bad)
    assert not any("add" in c for c in nmcli.calls)


@pytest.mark.parametrize("bad", ["a name with spaces", "sixteencharacters"])
def test_a_name_the_kernel_would_refuse_is_caught_here(bad, nmcli):
    """The shared name rule allows 64 characters and spaces, because a
    Windows adapter name is a label. Here the name *is* the device."""
    nmcli.devices = ETHERNET
    with pytest.raises(VNetError):
        vnet.create_adapter(bad, "2.100.100.0", 8)
    assert not any("add" in c for c in nmcli.calls)


# -------------------------------------------------------------- removal

def test_remove_reports_absence_rather_than_failing(nmcli):
    nmcli.devices = ETHERNET
    assert vnet.remove_adapter(name="AnyDMX") is False
    assert not any("delete" in c for c in nmcli.calls)


def test_remove_targets_the_stored_uuid(nmcli):
    """Remove can only ever delete the connection AnyDMX created, never
    another that happens to share the name."""
    assert vnet.remove_adapter("2f0d8a71-1111-2222-3333-abcdefabcdef") is True
    assert argv_of(nmcli.calls, "delete") == [
        "nmcli", "connection", "delete", "2f0d8a71-1111-2222-3333-abcdefabcdef"]


# ------------------------------------------------------- interface edits

def test_static_addressing_modifies_then_reactivates(nmcli):
    vnet.set_static_ip(2, "2.0.0.5", 8, gateway="")
    modify = argv_of(nmcli.calls, "modify")
    assert modify[modify.index("ipv4.method") + 1] == "manual"
    assert modify[modify.index("ipv4.addresses") + 1] == "2.0.0.5/8"
    assert modify[modify.index("ipv4.gateway") + 1] == ""
    # The change is inert until the connection is brought back up.
    assert argv_of(nmcli.calls, "up") == [
        "nmcli", "connection", "up", "16b7676e-40b8-3b51-b414-7d7c32c7969a"]


def test_a_gateway_is_offered_on_a_real_nic(nmcli):
    """Without one a user pinning a static address loses every route off
    the subnet, which is the trap this dialog exists to spare them."""
    vnet.set_static_ip(2, "192.168.8.20", 24, gateway="192.168.8.1")
    modify = argv_of(nmcli.calls, "modify")
    assert modify[modify.index("ipv4.gateway") + 1] == "192.168.8.1"


def test_dhcp_clears_the_static_address(nmcli):
    nmcli.method = "manual"
    vnet.set_dhcp(2)
    modify = argv_of(nmcli.calls, "modify")
    assert modify[modify.index("ipv4.method") + 1] == "auto"
    assert modify[modify.index("ipv4.addresses") + 1] == ""


def test_enable_and_disable_target_the_device(nmcli):
    vnet.set_adapter_enabled(2, True)
    assert argv_of(nmcli.calls, "connect") == \
        ["nmcli", "device", "connect", "ens2"]
    nmcli.calls.clear()
    vnet.set_adapter_enabled(2, False)
    assert argv_of(nmcli.calls, "disconnect") == \
        ["nmcli", "device", "disconnect", "ens2"]


def test_an_unmanaged_interface_refuses_clearly(nmcli):
    """Not a traceback, and not a silent no-op: a sentence naming the reason."""
    nmcli.devices = UNMANAGED
    with pytest.raises(VNetError, match="not managing"):
        vnet.set_static_ip(3, "10.0.0.9", 24)
    assert not any("modify" in c for c in nmcli.calls)


# ------------------------------------------------------------- batches

def test_apply_checks_the_interface_is_still_the_one_that_was_selected(nmcli):
    """Indexes are reused and the dialog's list can be seconds old."""
    with pytest.raises(VNetError, match="Refresh"):
        vnet.apply_adapter(2, "enp9s0", [{"op": "dhcp"}])
    assert not any("modify" in c for c in nmcli.calls)


def test_apply_refuses_an_index_that_has_gone(nmcli):
    with pytest.raises(VNetError, match="no longer present"):
        vnet.apply_adapter(99, "ens2", [{"op": "dhcp"}])


def test_rename_is_refused_with_a_reason(nmcli):
    """A persistent rename on Linux means a udev rule — a different kind of
    change entirely, so the backend does not claim it."""
    assert "rename" not in vnet.SUPPORTED_OPS
    with pytest.raises(VNetError, match="udev"):
        vnet.apply_adapter(2, "ens2", [{"op": "rename", "name": "lights"}])
    assert not any("modify" in c for c in nmcli.calls)


def test_a_batch_runs_in_order(nmcli):
    """Enable first — a disconnected interface cannot be addressed."""
    vnet.apply_adapter(2, "ens2",
                       [{"op": "static", "ip": "2.0.0.5", "prefix": 8},
                        {"op": "enable"}])
    verbs = [c[1:3] for c in nmcli.calls if c[1] == "device"]
    assert verbs[0] == ["device", "connect"]
    connect_at = nmcli.calls.index(["nmcli", "device", "connect", "ens2"])
    modify_at = next(i for i, c in enumerate(nmcli.calls) if "modify" in c)
    assert connect_at < modify_at


def test_a_contradictory_batch_is_refused_before_anything_runs(nmcli):
    with pytest.raises(VNetError, match="contradicts"):
        vnet.apply_adapter(2, "ens2", [{"op": "enable"}, {"op": "disable"}])
    assert not any("device" in c for c in nmcli.calls)


# ----------------------------------------------------------- environment

def test_a_missing_nmcli_says_what_is_missing(monkeypatch):
    def absent(cmd, **kwargs):
        raise FileNotFoundError("nmcli")
    monkeypatch.setattr(subprocess, "run", absent)
    with pytest.raises(VNetError, match="NetworkManager"):
        vnet.list_adapters()


def test_a_declined_prompt_says_so(nmcli):
    """polkit refusing is the common case, and "Error: 4" explains nothing."""
    nmcli.fail_with = "Error: Not authorized to control networking."
    with pytest.raises(VNetError, match="declined"):
        vnet.list_adapters()


def test_root_is_promised_no_prompt(monkeypatch):
    monkeypatch.setattr(vnet.os, "geteuid", lambda: 0)
    assert vnet.is_admin() is True
    assert vnet.permission_notice() is None


def test_an_unprivileged_run_is_told_a_prompt_is_coming(monkeypatch):
    monkeypatch.setattr(vnet.os, "geteuid", lambda: 1000)
    assert vnet.permission_notice()


def test_an_ssh_session_is_recognised(monkeypatch):
    """Disabling the NIC carrying the connection is unrecoverable over SSH,
    exactly as over RDP on Windows."""
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51000 10.0.0.9 22")
    assert vnet.is_remote_session() is True
    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.delenv("SSH_TTY", raising=False)
    assert vnet.is_remote_session() is False


def test_the_lighting_interface_is_not_described_as_a_dummy(nmcli):
    """"dummy" is the kernel's word for the mechanism, not a name.

    A dummy device has no vendor and no product, so the description falls
    through to GENERAL.DRIVER — which is the literal string "dummy". Shown as
    the identity of the AnyDMX interface it reads like an unfinished
    placeholder, and says nothing about what the user is looking at.
    """
    anydmx = next(a for a in vnet.list_adapters() if a["name"] == "AnyDMX")
    assert anydmx["description"] == "AnyDMX lighting interface"
    assert "dummy" not in anydmx["description"].lower()


def test_a_dummy_that_is_not_ours_is_still_described_honestly(nmcli):
    """Someone else's dummy device is virtual, but it is not the lighting
    interface and must not claim to be."""
    nmcli.devices = ANYDMX.replace("GENERAL.DEVICE:AnyDMX",
                                   "GENERAL.DEVICE:docker-shim")
    adapter = vnet.list_adapters()[0]
    assert adapter["description"] == "Virtual network interface"


def test_real_hardware_still_describes_itself(nmcli):
    """The vendor/product path must not be lost to the dummy special case."""
    ens2 = next(a for a in vnet.list_adapters() if a["name"] == "ens2")
    assert ens2["description"] == "Red Hat, Inc. Virtio network device"
