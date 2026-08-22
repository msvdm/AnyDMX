"""The two things AnyDMX.py answers before Qt is ever loaded.

Both matter to the packaged build: a released binary has to be able to say
what it is without opening a window, and the elevated helper has to reach the
right backend on a machine with no display.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AnyDMX
from src import __version__


def test_version_flag_prints_and_exits_cleanly(monkeypatch, capsys):
    """The release workflow's smoke test greps this exact line.

    It is also the answer to the first question on every bug report, and a
    downloaded binary is the one copy whose version nobody else can look up.
    """
    monkeypatch.setattr(sys, "argv", ["AnyDMX.py", "--version"])
    assert AnyDMX.main() == 0
    assert capsys.readouterr().out.strip() == f"AnyDMX {__version__}"


def test_short_version_flag_works_too(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["AnyDMX.py", "-V"])
    assert AnyDMX.main() == 0
    assert capsys.readouterr().out.strip() == f"AnyDMX {__version__}"


def test_the_version_is_shaped_like_a_tag():
    """release.yml parses this out of the source and compares it to the tag.

    A version it cannot parse fails the release *after* the tag is pushed,
    which means deleting a tag to fix it.
    """
    parts = __version__.split(".")
    assert len(parts) == 3, __version__
    assert all(p.isdigit() for p in parts), __version__


def test_the_helper_flag_reaches_this_platform_s_backend(monkeypatch):
    """Checked before Qt loads, so the elevated child stays small and quick."""
    from src.core import vnet
    seen = []
    monkeypatch.setattr(vnet, "helper_main", lambda argv: seen.append(argv) or 7)
    monkeypatch.setattr(sys, "argv", ["AnyDMX.py", vnet.HELPER_FLAG, "/tmp/req"])
    assert AnyDMX.main() == 7
    assert seen and vnet.HELPER_FLAG in seen[0]


def test_the_helper_flag_wins_over_the_version_flag(monkeypatch):
    """An elevated helper must never be diverted into printing a banner."""
    from src.core import vnet
    monkeypatch.setattr(vnet, "helper_main", lambda argv: 0)
    monkeypatch.setattr(sys, "argv",
                        ["AnyDMX.py", "--version", vnet.HELPER_FLAG, "/tmp/req"])
    assert AnyDMX.main() == 0
