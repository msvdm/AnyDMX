"""Tests for the interface setup dialog.

Nothing here shells out. The autouse fixture below replaces every vnet
function the dialog can reach, so running the suite cannot enumerate, address,
rename, enable or remove a real network adapter — and it is autouse precisely
so a new test cannot forget one and quietly run PowerShell in CI.

Nothing here builds a MainWindow either: that would start the engine and open
a real serial port.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from src.core import vnet
from src.gui import vnet_dialog
from src.gui.vnet_dialog import InterfaceDialog, describe, next_free_artnet_ip


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def adapter(name, index, ip=None, prefix=24, dhcp=False, up=True,
            physical=True, gateway=None, extra=()):
    addresses = []
    if ip:
        addresses.append({"ip": ip, "prefix": prefix})
    addresses += [{"ip": a, "prefix": 8} for a in extra]
    return {"name": name, "index": index, "description": f"{name} controller",
            "status": "Up" if up else "Disabled", "admin_up": up,
            "physical": physical, "link_speed": "1 Gbps", "mac": "00-11-22",
            "instance_id": f"ROOT\\NET\\{index:04d}", "addresses": addresses,
            "dhcp": dhcp, "gateway": gateway, "category": "Private"}


ADAPTERS = [
    adapter("BNR Outside", 6, "192.168.8.76", 24, dhcp=True,
            gateway="192.168.8.1"),
    adapter("BNR Inside", 9, "192.168.31.248", 24, dhcp=True),
    adapter("AnyDMX", 33, "2.100.100.0", 8),
    adapter("Spare", 8, None, up=False),
    adapter("Tailscale", 32, "169.254.83.107", 16, physical=False),
]


@pytest.fixture(autouse=True)
def no_powershell(monkeypatch):
    """Every door out of the dialog into Windows, stopped."""
    monkeypatch.setattr(vnet, "list_adapters", lambda: [dict(a) for a in ADAPTERS])
    monkeypatch.setattr(vnet, "is_admin", lambda: True)
    monkeypatch.setattr(vnet, "is_remote_session", lambda: False)
    monkeypatch.setattr(vnet, "find_adapter", lambda name=None: None)
    monkeypatch.setattr(vnet, "artnet_range_addresses", lambda: [])
    monkeypatch.setattr(vnet, "apply_adapter",
                        lambda i, n, ops: pytest.fail("apply_adapter escaped"))
    monkeypatch.setattr(vnet, "request_create",
                        lambda *a: pytest.fail("request_create escaped"))
    monkeypatch.setattr(vnet, "request_remove",
                        lambda *a: pytest.fail("request_remove escaped"))


def build(qt_app, settings=None):
    dlg = InterfaceDialog(dict(settings or {"bind_ip": ""}))
    dlg._reload()          # the real one runs off a QTimer once shown
    return dlg


# ------------------------------------------------------------- geometry

def test_the_dialog_fits_the_work_area(qt_app, monkeypatch):
    """The dev machine reports a 1280x680 work area (see CLAUDE.md).

    With a native title bar of roughly 32 px the dialog has to stay under
    ~648, and eight adapters here put the list at its scroll cap so this is
    the tallest it can ask to be. Nothing else guards that rule.
    """
    many = [adapter(f"NIC {i}", i, f"10.0.0.{i}") for i in range(1, 9)]
    monkeypatch.setattr(vnet, "list_adapters", lambda: many)
    dlg = InterfaceDialog({"bind_ip": ""})
    dlg._reload()
    hint = dlg.sizeHint()
    assert hint.height() <= 648, f"dialog wants {hint.height()} px"
    assert hint.width() <= 1280
    assert dlg.minimumSizeHint().height() <= 648


# ------------------------------------------------------------- the list

def test_every_adapter_gets_a_row(qt_app):
    dlg = build(qt_app)
    assert len(dlg.rows.buttons()) == len(ADAPTERS)


def test_physical_adapters_come_first(qt_app):
    """The adapter someone came here to fix usually has a socket on the back."""
    dlg = build(qt_app)
    names = [b.adapter["name"] for b in dlg.rows.buttons()]
    assert names[-1] == "Tailscale"


def test_the_bound_adapter_is_tagged(qt_app):
    text, _ = describe(ADAPTERS[1], bind_ip="192.168.31.248")
    assert "LISTENING" in text
    text, _ = describe(ADAPTERS[1], bind_ip="10.0.0.1")
    assert "LISTENING" not in text


def test_a_disabled_adapter_says_so(qt_app):
    text, state = describe(ADAPTERS[3])
    assert "disabled" in text
    assert state == "text_dim"


def test_the_gateway_adapter_is_tagged(qt_app):
    text, _ = describe(ADAPTERS[0])
    assert "GATEWAY" in text


# ------------------------------------------------------------- the editor

def test_selecting_a_row_fills_the_editor(qt_app):
    dlg = build(qt_app)
    target = next(b for b in dlg.rows.buttons()
                  if b.adapter["name"] == "AnyDMX")
    dlg._row_picked(target)
    assert dlg.name_edit.text() == "AnyDMX"
    assert dlg.ip_edit.text() == "2.100.100.0"
    assert dlg.prefix_spin.value() == 8
    assert dlg.mode_combo.currentIndex() == 1        # static


def test_dhcp_greys_out_the_address_fields(qt_app):
    dlg = build(qt_app)
    target = next(b for b in dlg.rows.buttons()
                  if b.adapter["name"] == "AnyDMX")
    dlg._row_picked(target)
    assert dlg.ip_edit.isEnabled()
    dlg.mode_combo.setCurrentIndex(0)                # automatic
    assert not dlg.ip_edit.isEnabled()
    assert not dlg.prefix_spin.isEnabled()
    assert not dlg.gw_edit.isEnabled()


def test_read_only_disables_the_editor_but_not_the_anydmx_adapter(
        qt_app, monkeypatch):
    """Create/Remove has always worked unelevated. It must keep working."""
    monkeypatch.setattr(vnet, "is_admin", lambda: False)
    dlg = build(qt_app)
    assert not dlg.name_edit.isEnabled()
    assert not dlg.apply_btn.isEnabled()
    assert not dlg.ip_edit.isEnabled()
    assert dlg.create_btn.isEnabled()
    assert "read-only" in dlg.mode_label.text()


def test_the_artnet_preset_picks_a_free_address(qt_app):
    dlg = build(qt_app)
    dlg._row_picked(dlg.rows.buttons()[0])
    dlg._apply_preset()
    assert dlg.ip_edit.text() == "2.100.100.1"       # .0 is taken by AnyDMX
    assert dlg.prefix_spin.value() == 8
    assert dlg.mode_combo.currentIndex() == 1
    assert dlg.gw_edit.text() == ""


def test_the_preset_never_offers_the_anydmx_default():
    """2.100.100.0 is the AnyDMX adapter's own address; colliding with it is
    the one mistake this button exists to prevent."""
    assert next_free_artnet_ip([]) == "2.100.100.1"


# -------------------------------------------------------------- applying

def test_an_unchanged_adapter_applies_nothing(qt_app):
    dlg = build(qt_app)
    dlg._row_picked(dlg.rows.buttons()[0])
    assert dlg.pending_ops() == []


def test_apply_builds_only_what_changed(qt_app):
    dlg = build(qt_app)
    target = next(b for b in dlg.rows.buttons()
                  if b.adapter["name"] == "AnyDMX")
    dlg._row_picked(target)
    dlg.name_edit.setText("Lighting")
    dlg.ip_edit.setText("2.100.100.7")
    ops = dlg.pending_ops()
    assert [o["op"] for o in ops] == ["rename", "static"]
    assert ops[0]["name"] == "Lighting"
    assert ops[1]["ip"] == "2.100.100.7"
    assert ops[1]["prefix"] == 8


def test_switching_to_dhcp_is_one_op(qt_app):
    dlg = build(qt_app)
    target = next(b for b in dlg.rows.buttons()
                  if b.adapter["name"] == "AnyDMX")
    dlg._row_picked(target)
    dlg.mode_combo.setCurrentIndex(0)
    assert dlg.pending_ops() == [{"op": "dhcp"}]


def test_the_internet_adapter_asks_before_it_is_changed(qt_app, monkeypatch):
    dlg = build(qt_app)
    target = next(b for b in dlg.rows.buttons()
                  if b.adapter["name"] == "BNR Outside")
    dlg._row_picked(target)
    dlg.ip_edit.setText("2.0.0.9")
    monkeypatch.setattr(dlg, "_confirm", lambda a, o: False)
    dlg._apply()                    # apply_adapter would pytest.fail if reached


def test_an_ordinary_adapter_is_not_nagged(qt_app):
    dlg = build(qt_app)
    spare = next(a for a in ADAPTERS if a["name"] == "Spare")
    assert dlg._confirm(spare, [{"op": "rename", "name": "x"}]) is True


# ------------------------------------------------------------- reporting

def test_failures_go_to_the_footer_not_a_modal(qt_app, monkeypatch):
    """The window's own rule: a failure is reported in the status line, never
    a pop-up. A modal on top of a modal is worse here, not better."""
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        lambda *a, **k: pytest.fail("used a modal"))
    dlg = build(qt_app)

    def boom():
        raise vnet.VNetError("Windows said no")

    dlg._run(boom, "trying…")
    assert "Windows said no" in dlg.footer.text()


def test_a_long_error_cannot_widen_the_dialog(qt_app):
    dlg = build(qt_app)
    before = dlg.sizeHint().width()
    dlg._say("err", "x" * 4000)
    assert dlg.sizeHint().width() == before


def test_rebinding_follows_a_re_addressed_adapter(qt_app, monkeypatch):
    """Re-addressing the NIC AnyDMX listens on used to look like the app
    resetting the input port by itself."""
    settings = {"bind_ip": "192.168.31.248"}
    dlg = InterfaceDialog(settings)
    monkeypatch.setattr(vnet, "list_adapters",
                        lambda: [adapter("BNR Inside", 9, "2.100.100.4", 8)])
    dlg._reload()
    old = adapter("BNR Inside", 9, "192.168.31.248", 24)
    dlg._applied(old, [{"op": "static"}])
    assert settings["bind_ip"] == "2.100.100.4"


def test_a_lease_that_has_not_arrived_clears_the_binding(qt_app, monkeypatch):
    settings = {"bind_ip": "192.168.31.248"}
    dlg = InterfaceDialog(settings)
    monkeypatch.setattr(vnet, "list_adapters",
                        lambda: [adapter("BNR Inside", 9, None)])
    dlg._reload()
    old = adapter("BNR Inside", 9, "192.168.31.248", 24)
    dlg._applied(old, [{"op": "dhcp"}])
    assert settings["bind_ip"] == ""
    assert "lease" in dlg.footer.text()


def test_close_still_persists_the_anydmx_address(qt_app):
    settings = {"bind_ip": "", "vnet_ip": "2.100.100.0", "vnet_prefix": 8}
    dlg = InterfaceDialog(settings)
    dlg.vnet_ip.setText("2.50.50.50")
    dlg.vnet_prefix.setValue(16)
    dlg.done(0)
    assert settings["vnet_ip"] == "2.50.50.50"
    assert settings["vnet_prefix"] == 16
