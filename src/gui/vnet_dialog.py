"""Interface setup: every network adapter on this PC, and the AnyDMX one.

Two jobs in one window, in the order a user meets them. The top half is a
nicer front end for the Windows network settings — the list of adapters, and
one editor for whichever is selected. The bottom half creates and removes the
virtual AnyDMX adapter, which is the part no OS dialog can do.

There is deliberately no explanatory prose in here. It used to open with a
six-line paragraph that took nearly half the height and left room for one
address field; that text now lives in the README, where it can be read once
rather than stared past every time. The labels are the same nouns Windows
uses, and the layout is the explanation.

Administrator rights are never demanded up front. Opening this window asks
for nothing, and how AnyDMX was launched changes nothing about what can be
done here: every button that changes something raises the Windows permission
prompt at the moment it is pressed, through a short-lived elevated helper,
and does nothing else if it is declined. Started elevated, the prompts do not
appear and the window says nothing about rights at all.

Every adapter operation shells out — to PowerShell for the queries, to the
elevated helper for the changes — so this dialog is only ever
opened on demand. Nothing here runs from the main window's 100 ms poll, and
the receiver and output threads are untouched while it works, so DMX keeps
streaming the whole time.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from src.core import vnet
from src.gui.styles import COLORS
from src.utils.logger import get_logger

log = get_logger(__name__)

# Room for eight rows before it scrolls. The work area on a 300%-scaled 4K
# screen is 680 px tall (see CLAUDE.md) and the dialog has to fit inside it
# with a native title bar on top, so this cap is what the rest is budgeted
# against — not a guess.
LIST_MIN_H = 100
LIST_MAX_H = 200

DIALOG_W, DIALOG_H = 720, 620
DIALOG_MIN_W, DIALOG_MIN_H = 660, 540

_LED_CACHE = {}


def _led(state):
    """A small coloured dot, painted once per state and reused.

    An icon rather than a stylesheet: setting a stylesheet re-polishes the
    widget, which is fine for one indicator changing at 10 Hz but wasteful for
    a list of rows rebuilt in a loop.
    """
    if state not in _LED_CACHE:
        pix = QPixmap(10, 10)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(COLORS[state]))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(1, 1, 8, 8)
        painter.end()
        _LED_CACHE[state] = pix
    return _LED_CACHE[state]


def describe(adapter, bind_ip="", vnet_name=vnet.ADAPTER_NAME):
    """One line for an adapter row, and the LED state that goes with it.

    Kept short on purpose: the description, MAC and link speed go in the
    tooltip and the editor's identity line, because a row that spells
    everything out needs a wider window than the screen has.
    """
    addresses = adapter.get("addresses") or []
    if addresses:
        first = addresses[0]
        where = f"{first['ip']}/{first['prefix']}"
        if len(addresses) > 1:
            where += f" +{len(addresses) - 1}"
    elif not adapter.get("admin_up"):
        where = "disabled"
    else:
        where = "no address"

    bits = [adapter.get("name", "?"), where]
    if addresses:
        bits.append("DHCP" if adapter.get("dhcp") else "static")

    # Uppercase so they read as machine facts rather than as prose.
    tags = []
    if bind_ip and any(a["ip"] == bind_ip for a in addresses):
        tags.append("LISTENING")
    if adapter.get("gateway"):
        tags.append("GATEWAY")
    if adapter.get("name") == vnet_name:
        tags.append("ANYDMX")
    if not adapter.get("physical"):
        tags.append("VIRTUAL")

    if not adapter.get("admin_up"):
        state = "text_dim"
    elif adapter.get("status") == "Up":
        state = "ok"
    else:
        state = "warn"
    return "  ·  ".join(bits + tags), state


def next_free_artnet_ip(adapters, base="2.100.100."):
    """The lowest 2.100.100.x not already in use on this machine.

    Never .0 — that is the AnyDMX adapter's own default, and colliding with it
    is the one mistake this button exists to prevent.
    """
    taken = {a["ip"] for row in adapters for a in (row.get("addresses") or [])}
    for host in range(1, 255):
        candidate = f"{base}{host}"
        if candidate not in taken:
            return candidate
    return f"{base}1"


class _NicRow(QPushButton):
    """One adapter in the list.

    A checkable button in an exclusive group, so it inherits the universe
    chip's selected styling — picking an adapter here is the same gesture as
    picking a universe in the main window.
    """

    def __init__(self, adapter, bind_ip, vnet_name, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.setObjectName("nicRow")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        text, state = describe(adapter, bind_ip, vnet_name)
        self.setText(text)
        self.setIcon(_led(state))
        detail = [adapter.get("description", ""), adapter.get("mac", ""),
                  adapter.get("link_speed", ""), adapter.get("category") or ""]
        extra = (adapter.get("addresses") or [])[1:]
        if extra:
            detail.append("also " + ", ".join(
                f"{a['ip']}/{a['prefix']}" for a in extra))
        self.setToolTip("\n".join(d for d in detail if d))


class InterfaceDialog(QDialog):
    """Set up this PC's network interfaces, and the AnyDMX one.

    Takes the live settings dict: the AnyDMX address and prefix typed here are
    the ones the main window persists on close, and a re-addressed adapter
    that AnyDMX was listening on updates bind_ip so the Input Port selection
    survives.
    """

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AnyDMX — interface setup")
        self.resize(DIALOG_W, DIALOG_H)
        self.setMinimumSize(DIALOG_MIN_W, DIALOG_MIN_H)
        self._adapters = []
        self._selected = None
        self._busy = False
        # Not a permission gate any more, only a question of whether a
        # Windows prompt will appear when a change is applied.
        self._elevated = vnet.is_admin()
        self._build_ui()
        # Paint first, query after. The enumeration takes over a second, and
        # doing it in __init__ means the dialog does not appear until it is
        # done — the same trick the main window uses to start its engine.
        QTimer.singleShot(0, self._reload)

    # --------------------------------------------------------------- layout

    def _build_ui(self):
        body = QWidget(self)
        body.setObjectName("dialogBody")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

        root = QVBoxLayout(body)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        root.addLayout(self._build_header())
        root.addWidget(self._build_list(), 1)
        root.addWidget(self._build_editor())
        root.addWidget(self._build_vnet_panel())
        root.addLayout(self._build_footer())

    def _build_header(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        title = QLabel("NETWORK INTERFACES")
        title.setObjectName("sectionIn")
        row.addWidget(title)
        row.addStretch()
        rescan = QPushButton("⟳")
        rescan.setObjectName("refresh")
        rescan.setFixedSize(28, 24)
        rescan.setToolTip("Scan the interfaces again")
        rescan.clicked.connect(self._reload)
        row.addWidget(rescan)
        return row

    def _build_list(self):
        self.list_body = QWidget()
        self.list_layout = QVBoxLayout(self.list_body)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch()

        self.rows = QButtonGroup(self)
        self.rows.setExclusive(True)
        self.rows.buttonClicked.connect(self._row_picked)

        scroll = QScrollArea()
        scroll.setWidget(self.list_body)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(LIST_MIN_H)
        scroll.setMaximumHeight(LIST_MAX_H)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    def _build_editor(self):
        frame = QFrame()
        frame.setObjectName("panelIn")
        grid = QGridLayout(frame)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        # Without this the grid splits its slack evenly between the columns
        # and the captions end up marooned 200 px from the fields they label.
        # All the give goes to the empty column on the right instead.
        grid.setColumnMinimumWidth(0, 76)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)

        grid.addWidget(self._caption("Name"), 0, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setFixedWidth(210)
        grid.addWidget(self.name_edit, 0, 1)

        grid.addWidget(self._caption("Addressing"), 1, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Automatic (DHCP)", True)
        self.mode_combo.addItem("Static", False)
        self.mode_combo.setFixedWidth(210)
        self.mode_combo.currentIndexChanged.connect(self._addressing_changed)
        grid.addWidget(self.mode_combo, 1, 1)

        grid.addWidget(self._caption("Address"), 2, 0)
        addr = QHBoxLayout()
        addr.setSpacing(6)
        self.ip_edit = QLineEdit()
        self.ip_edit.setFixedWidth(130)
        addr.addWidget(self.ip_edit)
        addr.addWidget(QLabel("/"))
        self.prefix_spin = QSpinBox()
        self.prefix_spin.setRange(1, 32)
        self.prefix_spin.setFixedWidth(64)
        addr.addWidget(self.prefix_spin)
        addr.addSpacing(8)
        self.preset_btn = QPushButton("Art-Net 2.x")
        self.preset_btn.setToolTip(
            "Set a free address in the Art-Net range, which is the only range "
            "a console that picks its own interface will accept")
        self.preset_btn.clicked.connect(self._apply_preset)
        addr.addWidget(self.preset_btn)
        addr.addStretch()
        grid.addLayout(addr, 2, 1, 1, 2)

        grid.addWidget(self._caption("Gateway"), 3, 0)
        self.gw_edit = QLineEdit()
        self.gw_edit.setFixedWidth(130)
        self.gw_edit.setPlaceholderText("none")
        grid.addWidget(self.gw_edit, 3, 1)

        self.ident = QLabel("")
        self.ident.setObjectName("caption")
        self.ident.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        grid.addWidget(self.ident, 4, 0, 1, 3)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setToolTip(self._permission_hint(
            "Change this interface."))
        self.apply_btn.clicked.connect(self._apply)
        buttons.addWidget(self.apply_btn)
        self.toggle_btn = QPushButton("Disable")
        self.toggle_btn.clicked.connect(self._toggle_enabled)
        buttons.addWidget(self.toggle_btn)
        buttons.addSpacing(8)
        # Whatever is limiting the editor gets said here, beside the controls
        # it limits, and only while it is true. A notice in the header reads
        # as a verdict on the whole window instead. Ignored width so the
        # sentence can never widen the dialog.
        self.editor_note = QLabel("")
        self.editor_note.setObjectName("caption")
        self.editor_note.setSizePolicy(QSizePolicy.Ignored,
                                       QSizePolicy.Preferred)
        buttons.addWidget(self.editor_note, 1)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(lambda: self._fill_editor(self._selected))
        buttons.addWidget(self.revert_btn)
        grid.addLayout(buttons, 5, 0, 1, 3)

        self.editor = frame
        return frame

    def _build_vnet_panel(self):
        frame = QFrame()
        frame.setObjectName("panel")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("ANYDMX LIGHTING INTERFACE")
        title.setObjectName("sectionIn")
        head.addWidget(title)
        head.addStretch()
        self.vnet_state = QLabel("checking…")
        self.vnet_state.setObjectName("caption")
        self.vnet_state.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(self.vnet_state)
        box.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.vnet_ip = QLineEdit(self.settings.get("vnet_ip", vnet.DEFAULT_IP))
        self.vnet_ip.setFixedWidth(130)
        self.vnet_ip.setToolTip(
            "Address for the virtual adapter. Consoles that auto-pick an "
            "Art-Net interface only accept the 2.x.x.x range.")
        row.addWidget(self.vnet_ip)
        row.addWidget(QLabel("/"))
        self.vnet_prefix = QSpinBox()
        self.vnet_prefix.setRange(1, 32)
        self.vnet_prefix.setFixedWidth(64)
        self.vnet_prefix.setValue(
            int(self.settings.get("vnet_prefix", vnet.DEFAULT_PREFIX)))
        row.addWidget(self.vnet_prefix)
        row.addSpacing(8)
        self.create_btn = QPushButton("Create")
        self.create_btn.setObjectName("primary")
        self.create_btn.setToolTip(self._permission_hint(
            "Add the virtual AnyDMX adapter."))
        self.create_btn.clicked.connect(self._create)
        row.addWidget(self.create_btn)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.setToolTip(self._permission_hint(
            "Delete the virtual AnyDMX adapter."))
        self.remove_btn.clicked.connect(self._remove)
        row.addWidget(self.remove_btn)
        row.addStretch()
        box.addLayout(row)
        return frame

    def _build_footer(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        self.footer = QLabel("")
        # Ignored width, like the main window's status line: a long PowerShell
        # error must never be able to widen the dialog.
        self.footer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.footer, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        return row

    def _permission_hint(self, what):
        """A button's tooltip, with the Windows prompt named when there is one.

        Elevated, the second sentence would be a lie and is left off — the
        rule for this window is that it says nothing about rights when there
        is nothing to say.
        """
        if self._elevated:
            return what
        return (f"{what}\n"
                "Windows will ask for permission for this one step; "
                "AnyDMX does not need to be restarted.")

    @staticmethod
    def _caption(text):
        label = QLabel(text)
        label.setObjectName("caption")
        return label

    # ---------------------------------------------------------- populating

    def _reload(self):
        """Re-enumerate, and rebuild the list around the current selection."""
        keep = self._selected["index"] if self._selected else None
        self._say("", "scanning…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._adapters = vnet.list_adapters()
        except vnet.VNetError as e:
            self._adapters = []
            self._say("err", str(e))
        else:
            self._say("", "")
        finally:
            QApplication.restoreOverrideCursor()

        # Physical first: the adapter someone came here to fix is almost
        # always one with a socket on the back of the machine.
        self._adapters.sort(key=lambda a: (not a.get("physical"),
                                           a.get("name", "")))
        self._build_rows(keep)
        self._refresh_vnet()

    def _build_rows(self, keep=None):
        for button in list(self.rows.buttons()):
            self.rows.removeButton(button)
            self.list_layout.removeWidget(button)
            button.deleteLater()

        bind_ip = self.settings.get("bind_ip", "")
        vnet_name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        chosen = None
        for adapter in self._adapters:
            row = _NicRow(adapter, bind_ip, vnet_name)
            self.rows.addButton(row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            if adapter["index"] == keep or (keep is None and chosen is None):
                chosen = row
        if chosen:
            chosen.setChecked(True)
            self._select(chosen.adapter)
        else:
            self._select(None)

    def _row_picked(self, button):
        self._select(button.adapter)

    def _select(self, adapter):
        self._selected = adapter
        self._fill_editor(adapter)

    def _fill_editor(self, adapter):
        editable = bool(adapter)
        if not adapter:
            for widget in (self.name_edit, self.ip_edit, self.gw_edit):
                widget.setText("")
            self.ident.setText("")
            self.ident.setToolTip("")
        else:
            addresses = adapter.get("addresses") or []
            first = addresses[0] if addresses else {"ip": "", "prefix": 24}
            self.name_edit.setText(adapter.get("name", ""))
            self.mode_combo.setCurrentIndex(0 if adapter.get("dhcp") else 1)
            self.ip_edit.setText(first["ip"])
            self.prefix_spin.setValue(first["prefix"] or 24)
            self.gw_edit.setText(adapter.get("gateway") or "")
            self.toggle_btn.setText(
                "Disable" if adapter.get("admin_up") else "Enable")
            detail = [adapter.get("description", ""), adapter.get("mac", ""),
                      adapter.get("link_speed", ""),
                      adapter.get("category") or ""]
            if len(addresses) > 1:
                detail.append("also " + ", ".join(
                    f"{a['ip']}/{a['prefix']}" for a in addresses[1:]))
            line = "  ·  ".join(d for d in detail if d)
            self.ident.setText(line)
            self.ident.setToolTip(line)

        for widget in (self.name_edit, self.mode_combo, self.preset_btn,
                       self.apply_btn, self.toggle_btn, self.revert_btn):
            widget.setEnabled(editable)
        self._addressing_changed()

    def _addressing_changed(self):
        """Static exposes the address fields; automatic greys them out."""
        static = self.mode_combo.currentIndex() == 1
        for widget in (self.ip_edit, self.prefix_spin, self.gw_edit):
            widget.setEnabled(static and bool(self._selected))
        self._explain_greyed()

    def _explain_greyed(self):
        """Why the address fields are grey — now the only reason left.

        A greyed field with no reason beside it reads as a broken window.
        Windows greys the same three in its own dialog until you pick Manual;
        the difference is that it says so.
        """
        if self._selected and self.mode_combo.currentIndex() == 0:
            self.editor_note.setText(
                "address comes from DHCP — set Addressing to Static to type one")
            self.editor_note.setToolTip(
                "This interface asks the network for its address, so the "
                "fields below show what it was given.\nSwitching to Static "
                "hands it over to you — Art-Net 2.x does that in one click.")
        else:
            self.editor_note.setText("")
            self.editor_note.setToolTip("")

    def _apply_preset(self):
        self.mode_combo.setCurrentIndex(1)
        self.ip_edit.setText(next_free_artnet_ip(self._adapters))
        self.prefix_spin.setValue(8)
        self.gw_edit.setText("")

    # ------------------------------------------------------------- editing

    def pending_ops(self):
        """What the editor is asking for that the adapter is not already doing."""
        adapter = self._selected
        if not adapter:
            return []
        ops = []
        name = self.name_edit.text().strip()
        if name and name != adapter.get("name"):
            ops.append({"op": "rename", "name": name})

        wants_dhcp = self.mode_combo.currentIndex() == 0
        addresses = adapter.get("addresses") or []
        first = addresses[0] if addresses else {"ip": "", "prefix": 0}
        if wants_dhcp:
            if not adapter.get("dhcp"):
                ops.append({"op": "dhcp"})
        else:
            ip = self.ip_edit.text().strip()
            prefix = self.prefix_spin.value()
            gateway = self.gw_edit.text().strip()
            changed = (adapter.get("dhcp") or ip != first["ip"]
                       or prefix != first["prefix"]
                       or gateway != (adapter.get("gateway") or ""))
            if changed:
                ops.append({"op": "static", "ip": ip, "prefix": prefix,
                            "gateway": gateway})
        return ops

    def _apply(self):
        adapter = self._selected
        ops = self.pending_ops()
        if not adapter or not ops:
            self._say("", "nothing to change")
            return
        if not self._confirm(adapter, ops):
            return
        self._run(lambda: vnet.request_apply(adapter["index"],
                                             adapter["name"], ops),
                  f"applying to {adapter['name']}…",
                  done=lambda: self._applied(adapter, ops))

    def _toggle_enabled(self):
        adapter = self._selected
        if not adapter:
            return
        turning_on = not adapter.get("admin_up")
        ops = [{"op": "enable" if turning_on else "disable"}]
        if not self._confirm(adapter, ops):
            return
        self._run(lambda: vnet.request_apply(adapter["index"],
                                             adapter["name"], ops),
                  f"{'enabling' if turning_on else 'disabling'} "
                  f"{adapter['name']}…",
                  done=lambda: self._applied(adapter, ops))

    def _confirm(self, adapter, ops):
        """Ask before anything that could take the machine off the network.

        Never refuse outright — a user with two NICs legitimately disables
        one, and blocking that would send them straight back to the Windows
        dialog this exists to replace. Cancel is the default button.
        """
        kinds = {o["op"] for o in ops}
        carries_route = bool(adapter.get("gateway"))
        listening = any(a["ip"] == self.settings.get("bind_ip", "")
                        for a in (adapter.get("addresses") or []))
        multi = len(adapter.get("addresses") or []) > 1

        reasons = []
        if carries_route:
            reasons.append("It carries this PC's internet connection.")
        if listening:
            reasons.append("AnyDMX is listening on it.")
        if multi and "static" in kinds:
            extra = ", ".join(f"{a['ip']}/{a['prefix']}"
                              for a in adapter["addresses"][1:])
            reasons.append(f"Its other addresses ({extra}) will be removed.")
        if not reasons and "disable" not in kinds:
            return True
        if carries_route and vnet.is_remote_session():
            reasons.append("This is a remote session — you would lose it and "
                           "could not undo this.")

        verb = "Disable" if "disable" in kinds else "Change"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("AnyDMX")
        box.setText(f"{verb} \"{adapter['name']}\"?")
        box.setInformativeText(" ".join(reasons))
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Ok

    def _applied(self, adapter, ops):
        """Keep the main window's Input Port pointing at the same adapter.

        Re-addressing the NIC AnyDMX is bound to used to look like the app
        resetting the input port by itself: the old IP vanishes from the
        combo, the selection silently falls back to "All interfaces", and
        nothing says why.
        """
        old = {a["ip"] for a in (adapter.get("addresses") or [])}
        if self.settings.get("bind_ip", "") not in old:
            return
        fresh = next((a for a in self._adapters
                      if a["index"] == adapter["index"]), None)
        new = (fresh or {}).get("addresses") or []
        if new:
            self.settings["bind_ip"] = new[0]["ip"]
            self._say("ok", f"{fresh['name']} — now {new[0]['ip']}, and AnyDMX "
                            "is following it")
        else:
            # A DHCP lease has not arrived yet. Leaving a dead address in
            # bind_ip would just fail the next bind silently.
            self.settings["bind_ip"] = ""
            self._say("ok", f"{adapter['name']} — waiting for a lease; input "
                            "set back to all interfaces")

    # --------------------------------------------------- the AnyDMX adapter

    def _refresh_vnet(self):
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        try:
            state = vnet.find_adapter(name)
        except vnet.VNetError as e:
            self.vnet_state.setText(f"could not be checked — {e}")
            self.create_btn.setEnabled(True)
            self.remove_btn.setEnabled(False)
            return
        if state:
            prefix = state.get("prefix")
            address = state.get("ip") or "no address"
            if state.get("ip") and prefix:
                address = f"{state['ip']}/{prefix}"
            self.vnet_state.setText(f"{address} · {state.get('status', '?')}")
            self.settings["vnet_instance_id"] = state.get("instance_id") or ""
            self.create_btn.setEnabled(False)
            self.remove_btn.setEnabled(True)
            return
        self.create_btn.setEnabled(True)
        self.remove_btn.setEnabled(False)
        try:
            usable = vnet.artnet_range_addresses()
        except vnet.VNetError:
            usable = []
        if usable:
            self.vnet_state.setText(
                f"not created — Art-Net range already at {', '.join(usable)}")
        else:
            self.vnet_state.setText("not created — no 2.x/10.x address here")

    def _create(self):
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        ip = self.vnet_ip.text().strip()
        prefix = self.vnet_prefix.value()
        self._run(lambda: vnet.request_create(name, ip, prefix),
                  f"creating {name}…")

    def _remove(self):
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        confirm = QMessageBox.question(
            self, "AnyDMX",
            f"Remove the '{name}' network interface?\n\n"
            "Any console sending Art-Net to it will stop reaching AnyDMX.")
        if confirm != QMessageBox.Yes:
            return
        instance_id = self.settings.get("vnet_instance_id", "") or None
        self._run(lambda: vnet.request_remove(instance_id, name),
                  f"removing {name}…")

    # -------------------------------------------------------------- running

    def _run(self, task, busy_text, done=None):
        """One adapter operation, with the dialog visibly busy while it runs.

        Guarded against re-entry: processEvents() below keeps the UI alive,
        which also means a second click can arrive mid-operation.
        """
        if self._busy:
            return
        if not vnet.is_admin():
            busy_text += "  — approve the Windows permission prompt"
        self._busy = True
        self._say("", busy_text)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        failed = None
        try:
            task()
        except vnet.VNetError as e:
            failed = str(e)
            log.warning("Interface operation failed: %s", e)
        finally:
            QApplication.restoreOverrideCursor()
            self._busy = False
        self.settings["vnet_ip"] = self.vnet_ip.text().strip()
        self.settings["vnet_prefix"] = self.vnet_prefix.value()
        self._reload()
        if failed:
            self._say("err", failed)
        elif done:
            done()
        else:
            self._say("ok", "done")

    def _say(self, level, text):
        """The footer is the whole reporting vocabulary here.

        Deliberately not a QMessageBox: a modal on top of a modal in a dialog
        with no spare room, and the window's own rule is that a failure is
        reported in the status line, never a pop-up. The two message boxes
        that remain both ask a question rather than announce a result.
        """
        colour = {"ok": COLORS["ok"], "err": COLORS["err"]}.get(
            level, COLORS["text_dim"])
        self.footer.setStyleSheet(f"color: {colour};")
        self.footer.setText(text.splitlines()[0] if text else "")
        self.footer.setToolTip(text)

    def done(self, result):
        """Every exit path — Close, Esc, the window's X — keeps what was typed."""
        self.settings["vnet_ip"] = self.vnet_ip.text().strip()
        self.settings["vnet_prefix"] = self.vnet_prefix.value()
        super().done(result)
