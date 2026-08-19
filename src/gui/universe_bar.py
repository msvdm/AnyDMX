"""The chips listing every Art-Net universe seen on this PC.

The receiver counts *every* universe on the wire, not only the selected one, so
the window can say "the console is sending universe 5" instead of "no data".
Clicking a chip switches to that universe.

The same chips are shown in two places: down the input panel while the window
is compact, and along the bottom strip while the channel drawer is open — the
drawer needs every pixel of height it can get, and the bottom strip is the only
thing that spans the wide window. That is a change of *direction*, not a change
of content, so this is one widget with one layout that flips between column and
row rather than two sets of buttons rebuilt on every toggle.

Width is the only thing the two really disagree about: the compact column is no
wider than a combo box, so a chip there drops the source address and shortens
its rate. The LOCAL TEST marker never drops — a simulator must not be able to
pass for a console.
"""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QLabel, QPushButton, QWidget

from src.core.artnet_receiver import LOOPBACK
from src.core.engine import ARTNET_ACTIVE_TIMEOUT, RateMeter

EMPTY_TEXT = "nothing yet — is Art-Net output switched on?"
EMPTY_TIP = ("Check that your lighting app's Art-Net output is enabled "
             "and bound to a real adapter, not 0.0.0.0.")


class _UniverseChip(QPushButton):
    """One universe. Knows how to describe itself at either width."""

    def __init__(self, universe):
        super().__init__()
        self.universe = universe
        self.setObjectName("uni")
        self.setCheckable(True)

    def render(self, src, rate, live, wide):
        sep = "  ·  " if wide else " · "
        state = "idle" if not live else (
            f"{rate:.0f} pkt/s" if wide else f"{rate:.0f}/s")
        local = src == LOOPBACK
        origin = f"{sep}{src}" if wide else ""
        marker = f"{sep}LOCAL TEST" if local else ""
        self.setText(f"U{self.universe}{origin}{marker}{sep}{state}")
        self.setToolTip(
            f"Listen to universe {self.universe}, sent from {src} at "
            f"{rate:.0f} packets/s"
            + (" — this is the local test sender, not your console."
               if local else "."))

    def set_wide(self, wide):
        # A dynamic property only reaches the stylesheet after a re-polish,
        # which is the one thing rebuilding the chips used to do for free.
        self.setProperty("compact", "false" if wide else "true")
        self.style().unpolish(self)
        self.style().polish(self)


class UniverseBar(QWidget):
    """Chips for every discovered universe, as a column or a row."""

    universe_picked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QBoxLayout(QBoxLayout.TopToBottom, self)
        self._layout.setContentsMargins(0, 0, 4, 0)
        self._layout.setSpacing(4)  # matches the column; see set_wide()
        self._chips = {}
        self._rates = {}
        self._wide = False
        self._empty = QLabel(EMPTY_TEXT)
        self._empty.setObjectName("caption")
        self._empty.setToolTip(EMPTY_TIP)
        self._empty.setWordWrap(True)
        self._layout.addWidget(self._empty)
        self._layout.addStretch(1)

    # ------------------------------------------------------------ layout

    def set_wide(self, wide):
        """Column in the input panel, row along the bottom strip."""
        if wide == self._wide:
            return
        self._wide = wide
        self._layout.setDirection(QBoxLayout.LeftToRight if wide
                                  else QBoxLayout.TopToBottom)
        # The column is scrolled, so it keeps a gutter clear of the scrollbar.
        self._layout.setContentsMargins(0, 0, 0 if wide else 4, 0)
        for chip in self._chips.values():
            chip.set_wide(wide)
        self._sync_empty()

    def _sync_empty(self):
        # The bottom strip sits beside a sentence that already says nothing is
        # arriving, so the placeholder would only repeat it.
        self._empty.setVisible(not self._chips and not self._wide)

    # ------------------------------------------------------------ content

    def update_universes(self, universes, selected):
        """Refresh from the receiver's {universe: {packets, src, last_seen}}."""
        now = time.monotonic()
        if universes.keys() != self._chips.keys():
            self._sync_chips(universes.keys())
        for universe, chip in self._chips.items():
            rec = universes[universe]
            rate = self._rates[universe].update(rec["packets"], now)
            live = (now - rec["last_seen"]) < ARTNET_ACTIVE_TIMEOUT
            chip.render(rec["src"], rate, live, self._wide)
            chip.setChecked(universe == selected)

    def _sync_chips(self, keys):
        """Add and remove chips so the set matches, keeping the rest in place.

        Chips that survive keep their rate meter — a universe must not lose its
        measured rate because a different one appeared beside it.
        """
        for universe in list(self._chips):
            if universe not in keys:
                chip = self._chips.pop(universe)
                self._rates.pop(universe, None)
                self._layout.removeWidget(chip)
                chip.deleteLater()
        for universe in sorted(keys):
            if universe in self._chips:
                continue
            chip = _UniverseChip(universe)
            chip.set_wide(self._wide)
            chip.clicked.connect(
                lambda _checked, u=universe: self.universe_picked.emit(u))
            self._chips[universe] = chip
            self._rates[universe] = RateMeter()
        # Re-seat every chip in universe order. removeWidget() leaves the
        # widget alive and parented, so this is ordering, not a rebuild — the
        # placeholder stays at index 0 and the stretch stays last.
        for chip in self._chips.values():
            self._layout.removeWidget(chip)
        for slot, universe in enumerate(sorted(self._chips)):
            self._layout.insertWidget(slot + 1, self._chips[universe])
        self._sync_empty()
