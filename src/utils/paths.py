"""Portable path resolution: works from source and from a PyInstaller exe."""

import sys
from pathlib import Path


def app_dir():
    """Directory where settings/ and logs/ live (project root or exe folder)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def settings_dir():
    d = app_dir() / "settings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir():
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
