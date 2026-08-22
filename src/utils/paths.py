"""Portable path resolution: from source, from a one-file build, or installed.

Three different situations, and they want different answers:

  run from source      settings beside the project, assets beside the project
  one-file binary      settings beside the exe, assets in the unpacked bundle
  installed (.deb)     settings in the user's config dir — /usr/lib is not
                       theirs to write to — assets in the unpacked bundle

The portable case is the one worth protecting: AnyDMX on a USB stick should
carry its settings on the stick with it. So "beside the executable" stays the
first choice, and the per-user directory is the fallback for when that is not
writable, rather than the other way round.
"""

import os
import sys
from pathlib import Path

APP_NAME = "AnyDMX"


def _executable_dir():
    """Where the running program lives: the exe's folder, or the project."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def user_config_dir():
    """The per-user settings directory this platform expects."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) / APP_NAME if base else Path.home() / f".{APP_NAME.lower()}"
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / APP_NAME if base else Path.home() / ".config" / APP_NAME


def app_dir():
    """Directory settings.json sits in.

    Beside the executable when that is writable, so a portable copy keeps its
    settings with it. An installed copy cannot write there — that is the point
    of installing it — so those settings go where the user's own files go.

    os.access() is only advisory on Windows, where it reads the read-only
    attribute rather than the ACL. That costs nothing here: save_settings()
    already treats a failed write as losing persistence and nothing else.
    """
    beside = _executable_dir()
    try:
        if os.access(beside, os.W_OK):
            return beside
    except OSError:
        pass
    return user_config_dir()


def resource_dir():
    """Directory the bundled read-only assets live in.

    Deliberately not app_dir(). A one-file build unpacks its bundle into a
    temporary directory that is deleted when the app exits, so the icon comes
    from there — while settings must go somewhere that survives.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return Path(__file__).resolve().parents[2]
