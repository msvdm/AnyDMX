"""Tests for the discovered-universe chips.

Runs headless (offscreen platform). Nothing here builds a MainWindow: that
would start the engine and open a real serial port, and the suite must never
touch hardware.
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from src.gui.universe_bar import UniverseBar


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def bar(qt_app):
    widget = UniverseBar()
    yield widget
    widget.deleteLater()


def seen(*universes, packets=1, src="2.0.0.5", age=0.0):
    """{universe: record} as the receiver reports it."""
    now = time.monotonic() - age
    return {u: {"packets": packets, "src": src, "last_seen": now}
            for u in universes}


def test_chips_appear_for_every_universe_seen(bar):
    bar.update_universes(seen(0, 5), selected=0)
    assert sorted(bar._chips) == [0, 5]


def test_chips_are_ordered_by_universe(bar):
    bar.update_universes(seen(5, 0), selected=0)
    order = [bar._layout.itemAt(i).widget() for i in range(bar._layout.count())]
    chips = [w.universe for w in order if hasattr(w, "universe")]
    assert chips == [0, 5]


def test_only_the_selected_chip_is_checked(bar):
    bar.update_universes(seen(0, 5), selected=5)
    assert bar._chips[5].isChecked()
    assert not bar._chips[0].isChecked()


def test_a_universe_that_stops_being_seen_loses_only_its_own_chip(bar):
    both = seen(0, 5)
    bar.update_universes(both, selected=0)
    bar.update_universes({0: both[0]}, selected=0)
    assert sorted(bar._chips) == [0]


def test_a_surviving_chip_keeps_its_rate_meter(bar):
    """A universe must not lose its measured rate because another appeared."""
    bar.update_universes(seen(0), selected=0)
    meter = bar._rates[0]
    bar.update_universes(seen(0, 5), selected=0)
    assert bar._rates[0] is meter


def test_clicking_a_chip_asks_for_that_universe(bar):
    bar.update_universes(seen(7), selected=0)
    picked = []
    bar.universe_picked.connect(picked.append)
    bar._chips[7].click()
    assert picked == [7]


# --------------------------------------------------------- compact vs wide

def test_the_wide_chip_carries_the_source_address(bar):
    bar.set_wide(True)
    bar.update_universes(seen(0, src="2.0.0.5"), selected=0)
    assert "2.0.0.5" in bar._chips[0].text()
    assert "pkt/s" in bar._chips[0].text()


def test_the_compact_chip_drops_the_address_for_width(bar):
    bar.set_wide(False)
    bar.update_universes(seen(0, src="2.0.0.5"), selected=0)
    assert "2.0.0.5" not in bar._chips[0].text()


def test_the_compact_chip_never_drops_the_local_test_marker(bar):
    """A simulator must not be able to pass for a console at any width."""
    for wide in (True, False):
        bar.set_wide(wide)
        bar.update_universes(seen(0, src="127.0.0.1"), selected=0)
        assert "LOCAL TEST" in bar._chips[0].text()
        assert "local test sender" in bar._chips[0].toolTip()


def test_a_universe_gone_quiet_reads_idle(bar):
    bar.update_universes(seen(0, age=30), selected=0)
    assert "idle" in bar._chips[0].text()


def test_switching_width_keeps_the_chips_and_flips_their_styling(bar):
    bar.set_wide(False)
    bar.update_universes(seen(0), selected=0)
    chip = bar._chips[0]
    assert chip.property("compact") == "true"
    bar.set_wide(True)
    assert bar._chips[0] is chip           # moved, not rebuilt
    assert chip.property("compact") == "false"


def test_the_placeholder_shows_only_in_the_empty_compact_column(bar):
    # isHidden() rather than isVisible(): the bar itself is never shown here,
    # which would make every child "not visible" regardless.
    bar.set_wide(False)
    bar.update_universes({}, selected=0)
    assert not bar._empty.isHidden()
    # The bottom strip sits beside a sentence that already says nothing is
    # arriving, so the placeholder would only repeat it.
    bar.set_wide(True)
    assert bar._empty.isHidden()


def test_the_placeholder_goes_away_once_a_universe_appears(bar):
    bar.set_wide(False)
    bar.update_universes({}, selected=0)
    bar.update_universes(seen(0), selected=0)
    assert bar._empty.isHidden()
