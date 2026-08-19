"""Pop-up for the dedicated lighting interface.

Lives in its own window because it is a one-off setup step, not something the
running bridge needs on screen: the adapter is infrastructure, created once and
kept until explicitly removed.

Every adapter operation shells out — to PowerShell for the query, to a
short-lived elevated helper for create/remove — so this dialog is only ever
opened on demand. Nothing here runs from the main window's 100 ms poll.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from src.core import vnet
from src.utils.logger import get_logger

log = get_logger(__name__)

WHY = (
    "Consoles that pick their own Art-Net interface — dot2 among them — only "
    "work on the Art-Net 2.x.x.x range. With no such address on this PC they "
    "select nothing, display 0.0.0.0, and transmit not one packet.\n\n"
    "This creates the landing spot: a virtual network adapter that shows up in "
    "the Input Port list once it exists. Restart your console afterwards — "
    "lighting apps enumerate interfaces at startup."
)


class InterfaceDialog(QDialog):
    """Create or remove the AnyDMX lighting interface.

    Takes the live settings dict: the address and prefix the user types here
    are the ones the main window persists on close.
    """

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AnyDMX — lighting interface")
        self.setMinimumWidth(460)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        body = QWidget(self)
        body.setObjectName("dialogBody")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

        root = QVBoxLayout(body)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("Lighting interface")
        title.setObjectName("sectionIn")
        root.addWidget(title)

        why = QLabel(WHY)
        why.setObjectName("dim")
        why.setWordWrap(True)
        root.addWidget(why)

        self.status = QLabel("checking…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        addr = QHBoxLayout()
        addr.setSpacing(8)
        addr.addWidget(QLabel("Address:"))
        self.ip_edit = QLineEdit(self.settings.get("vnet_ip", vnet.DEFAULT_IP))
        self.ip_edit.setFixedWidth(130)
        self.ip_edit.setToolTip(
            "Address for the virtual adapter. Consoles that auto-pick an "
            "Art-Net interface only accept the 2.x.x.x range.")
        addr.addWidget(self.ip_edit)
        addr.addWidget(QLabel("/"))
        self.prefix_spin = QSpinBox()
        self.prefix_spin.setRange(1, 32)
        self.prefix_spin.setFixedWidth(64)
        self.prefix_spin.setValue(
            int(self.settings.get("vnet_prefix", vnet.DEFAULT_PREFIX)))
        addr.addWidget(self.prefix_spin)
        addr.addStretch()
        root.addLayout(addr)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.create_btn = QPushButton("Create")
        self.create_btn.setObjectName("primary")
        self.create_btn.clicked.connect(self._create)
        buttons.addWidget(self.create_btn)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self._remove)
        buttons.addWidget(self.remove_btn)
        buttons.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    # ------------------------------------------------------------- actions

    def _refresh(self):
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        try:
            state = vnet.find_adapter(name)
        except vnet.VNetError as e:
            self.status.setText(f"Could not be checked — {e}")
            self.create_btn.setEnabled(True)
            self.remove_btn.setEnabled(False)
            return
        if state:
            prefix = state.get("prefix")
            address = state.get("ip") or "no address"
            if state.get("ip") and prefix:
                address = f"{state['ip']}/{prefix}"
            self.status.setText(
                f"{state.get('name', name)} · {address} · "
                f"{state.get('status', '?')}")
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
            self.status.setText(
                "Not created — the Art-Net range is already present at "
                f"{', '.join(usable)}, so a console may work without this.")
        else:
            self.status.setText(
                "Not created — there is no 2.x/10.x address on this PC.")

    def _create(self):
        name = self.settings.get("vnet_name", vnet.ADAPTER_NAME)
        ip = self.ip_edit.text().strip()
        prefix = self.prefix_spin.value()
        self._run_task(lambda: vnet.request_create(name, ip, prefix),
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
        self._run_task(lambda: vnet.request_remove(instance_id, name),
                       f"removing {name}…")

    def _run_task(self, task, busy_text):
        """One-shot adapter operation, with the dialog visibly busy while it runs."""
        if not vnet.is_admin():
            busy_text += "  — approve the Windows permission prompt"
        self.status.setText(busy_text)
        self.create_btn.setEnabled(False)
        self.remove_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            task()
        except vnet.VNetError as e:
            QMessageBox.critical(self, "AnyDMX", str(e))
        finally:
            QApplication.restoreOverrideCursor()
        self.settings["vnet_ip"] = self.ip_edit.text().strip()
        self.settings["vnet_prefix"] = self.prefix_spin.value()
        self._refresh()

    def done(self, result):
        """Every exit path — Close, Esc, the window's X — keeps what was typed."""
        self.settings["vnet_ip"] = self.ip_edit.text().strip()
        self.settings["vnet_prefix"] = self.prefix_spin.value()
        super().done(result)
