"""The dedicated lighting network interface — one contract, one backend each.

Consoles that auto-pick their Art-Net interface (dot2 among them) only ever
work on the Art-Net 2.x.x.x range. With no such address on the machine they
pick nothing, show 0.0.0.0, and transmit not a single packet — there is then
nothing for AnyDMX to capture, however well it listens. So AnyDMX creates the
landing spot itself: a virtual interface named "AnyDMX" holding 2.100.100.0/8.

The interface is infrastructure, not session state. It is created once and
persists until explicitly removed — across reboots, on every platform. That
requirement is why the Linux backend uses NetworkManager rather than the much
simpler `ip addr add`.

This module is the seam, and the same one the rest of the app already lives
by: callers name a capability, never a platform. src/gui/vnet_dialog.py talks
only to the names below and does not know which backend answered.

  vnet_windows.py   SetupAPI + PowerShell, elevated through a UAC helper
  vnet_linux.py     NetworkManager via nmcli, elevated through polkit
  vnet_unsupported.py   a clear refusal, so an unknown platform still runs

The two working backends are shaped differently on purpose. Windows has to
build its own privilege boundary and defend it; Linux borrows NetworkManager's.
Read each module's docstring before changing either — the reasoning is there,
and the Windows one was expensive.

The contract every backend implements
-------------------------------------
  Constants   ADAPTER_NAME  DEFAULT_IP  DEFAULT_PREFIX  ARTNET_PREFIXES
              HELPER_FLAG  SUPPORTED_OPS
  Errors      VNetError
  Privilege   is_admin()  is_remote_session()  permission_notice()
              helper_main(argv)
  Inspection  list_adapters()  find_adapter(name)  artnet_range_addresses()
  Mutation    create_adapter(...)  remove_adapter(...)
              apply_adapter(index, expect_name, ops)
  Requests    request_create(...)  request_remove(...)
              request_apply(index, expect_name, ops)

The request_* trio is what the GUI calls. Each one validates first, so a bad
value is refused before any permission prompt is raised over it, then either
acts directly or asks the platform for the rights it needs.
"""

import sys

from src.core.vnet_common import (
    ADAPTER_NAME, ARTNET_PREFIXES, DEFAULT_IP, DEFAULT_PREFIX, OP_ORDER,
    VNetError, validate_index, validate_ipv4, validate_name, validate_ops,
    validate_prefix,
)

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
SUPPORTED = IS_WINDOWS or IS_LINUX

if IS_WINDOWS:
    from src.core import vnet_windows as _backend
elif IS_LINUX:
    from src.core import vnet_linux as _backend
else:
    from src.core import vnet_unsupported as _backend

BACKEND = _backend.__name__.rsplit(".", 1)[-1]

# Bound explicitly rather than star-imported: this list *is* the contract, and
# a backend that grows a name the others lack should be visible here first.
SUPPORTED_OPS = _backend.SUPPORTED_OPS
HELPER_FLAG = _backend.HELPER_FLAG

is_admin = _backend.is_admin
is_remote_session = _backend.is_remote_session
permission_notice = _backend.permission_notice
helper_main = _backend.helper_main

list_adapters = _backend.list_adapters
find_adapter = _backend.find_adapter
artnet_range_addresses = _backend.artnet_range_addresses

create_adapter = _backend.create_adapter
remove_adapter = _backend.remove_adapter
apply_adapter = _backend.apply_adapter

request_create = _backend.request_create
request_remove = _backend.request_remove
request_apply = _backend.request_apply
