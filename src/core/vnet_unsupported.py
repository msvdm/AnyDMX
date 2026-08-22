"""Fallback backend for platforms AnyDMX has no interface manager for.

macOS lands here. Nothing about it is a claim that the rest of AnyDMX does or
does not work there — capture and DMX output may well be fine — only that
nobody has written and *verified* an interface manager for it. This module
exists so that running on such a platform fails with a sentence instead of an
ImportError, and so the GUI can hide a button it cannot honour.

Implementing a backend means: pick the platform's own network configuration
service, satisfy the contract in src/core/vnet.py, and prove it on real
hardware. Guessing at it from documentation is how this project would ship its
first feature nobody has ever seen work.
"""

import platform

from src.core.vnet_common import (
    ADAPTER_NAME, ARTNET_PREFIXES, DEFAULT_IP, DEFAULT_PREFIX, VNetError,
)

SUPPORTED_OPS = frozenset()
HELPER_FLAG = "--vnet-helper"


def _refuse(*args, **kwargs):
    raise VNetError(
        f"AnyDMX cannot manage network interfaces on {platform.system() or 'this platform'} "
        "yet. Art-Net capture and DMX output do not need it — set an "
        "Art-Net-range address with your system's own network settings.")


def is_admin():
    return False


def is_remote_session():
    return False


def permission_notice():
    return None


def helper_main(argv):
    print("AnyDMX: --vnet-helper is a Windows-only mode.")
    return 2


def list_adapters():
    return []


def find_adapter(name=ADAPTER_NAME):
    return None


def artnet_range_addresses():
    return []


create_adapter = _refuse
remove_adapter = _refuse
apply_adapter = _refuse
request_create = _refuse
request_remove = _refuse
request_apply = _refuse
