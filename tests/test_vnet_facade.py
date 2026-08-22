"""The platform seam: one contract, one backend per platform.

src/gui/vnet_dialog.py talks only to src/core/vnet.py and never learns which
backend answered. That only stays true if every backend answers to the same
names — so this file is the thing that notices when one drifts.

Nothing here touches the network: importing a backend is not running one.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import vnet, vnet_linux, vnet_unsupported, vnet_windows

BACKENDS = (vnet_windows, vnet_linux, vnet_unsupported)

# Exactly what src/core/vnet.py documents, and exactly what the dialog uses.
CONTRACT = (
    "ADAPTER_NAME", "DEFAULT_IP", "DEFAULT_PREFIX", "ARTNET_PREFIXES",
    "HELPER_FLAG", "SUPPORTED_OPS",
    "is_admin", "is_remote_session", "permission_notice", "helper_main",
    "list_adapters", "find_adapter", "artnet_range_addresses",
    "create_adapter", "remove_adapter", "apply_adapter",
    "request_create", "request_remove", "request_apply",
)


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.__name__)
@pytest.mark.parametrize("name", CONTRACT)
def test_every_backend_answers_to_the_whole_contract(backend, name):
    assert hasattr(backend, name), f"{backend.__name__} is missing {name}"


@pytest.mark.parametrize("name", CONTRACT)
def test_the_facade_re_exports_the_whole_contract(name):
    assert hasattr(vnet, name), f"src/core/vnet.py is missing {name}"


def test_the_facade_picks_a_backend_for_this_platform():
    expected = {"win32": "vnet_windows"}.get(
        sys.platform, "vnet_linux" if sys.platform.startswith("linux")
        else "vnet_unsupported")
    assert vnet.BACKEND == expected
    assert vnet.SUPPORTED is (expected != "vnet_unsupported")


def test_the_entry_point_can_still_find_the_helper_flag():
    """AnyDMX.py dispatches on vnet.HELPER_FLAG before Qt loads. Every
    backend has to carry it, even the ones with no helper behind it."""
    assert vnet.HELPER_FLAG == "--vnet-helper"
    for backend in BACKENDS:
        assert backend.HELPER_FLAG == vnet.HELPER_FLAG


def test_only_windows_claims_rename():
    """The one real capability difference, stated in one place.

    A backend growing or losing an op should show up here first, not as a
    dead control in the dialog.
    """
    assert "rename" in vnet_windows.SUPPORTED_OPS
    assert "rename" not in vnet_linux.SUPPORTED_OPS
    assert vnet_linux.SUPPORTED_OPS < vnet_windows.SUPPORTED_OPS


def test_an_unsupported_platform_refuses_with_a_sentence():
    """macOS lands here. It must not crash on import, and it must not
    pretend the interface manager works."""
    assert vnet_unsupported.list_adapters() == []
    assert vnet_unsupported.find_adapter() is None
    assert vnet_unsupported.permission_notice() is None
    with pytest.raises(vnet.VNetError, match="cannot manage network"):
        vnet_unsupported.request_create()
    with pytest.raises(vnet.VNetError):
        vnet_unsupported.request_apply(1, "eth0", [{"op": "dhcp"}])


def test_every_backend_raises_the_one_shared_error():
    """The dialog catches vnet.VNetError. Three classes named VNetError
    would let a backend's failure escape as an unhandled exception."""
    from src.core.vnet_common import VNetError
    for backend in BACKENDS:
        assert getattr(backend, "VNetError", VNetError) is VNetError
    assert vnet.VNetError is VNetError
