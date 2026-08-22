"""Portable path resolution: works from source and from a PyInstaller exe."""

import sys
from pathlib import Path


def app_dir():
    """Directory settings.json sits in (project root or exe folder)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]



def resource_dir():
    """Directory the bundled read-only assets live in.

    Deliberately not app_dir(). A one-file build unpacks its bundle into a
    temporary directory that is deleted when the app exits, so the icon comes
    from there — while settings.json must be written beside the executable,
    where it survives. Two different questions, two different answers.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return Path(__file__).resolve().parents[2]
