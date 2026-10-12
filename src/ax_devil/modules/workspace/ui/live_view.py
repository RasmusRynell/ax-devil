"""The Live view: the cameras in the workspace and on the network, with their order, then the settings of the network
scan."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from PySide6.QtCore import Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QMenu, QProgressBar, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import muted_label
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import SEE_THROUGH, KindColor
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.device_discovery.network_scan import DeviceOrder, Login
from ax_devil.modules.workspace.ui.logins_editor import LoginsEditor
from ax_devil.modules.workspace.ui.sections_view import SectionsView

LIVE_VIEW_ID = "live"


class ScanStatus(Enum):
    """How the last network scan went, as the text shown under the scan settings."""

    IDLE = ""
    SCANNING = "Scanning…"
    INVALID = "Invalid IP range"
    FAILED = "Scan failed"


class LiveView(SectionsView):
    """The sections it is given, with a Sort beside the search, then Add manually and the scan settings; it decides
    nothing itself.

    ``scan_requested`` carries the IP range as typed, ``add_manually_requested`` that the user wants to enter a
    camera's details, ``logins_edited`` the logins a scan tries once the user edited them, and ``order_chosen`` the
    ``DeviceOrder`` picked.
    """

    scan_requested = Signal(str)
    add_manually_requested = Signal()
    logins_edited = Signal(object)  # tuple[Login, ...]
    order_chosen = Signal(object)  # DeviceOrder

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(LIVE_VIEW_ID, "Live", Icon.LIVE_VIDEO, search_placeholder="Search cameras", parent=parent)
        order_menu = QMenu(self)
        orders = QActionGroup(order_menu)
        for order in DeviceOrder:
            action = order_menu.addAction(order.label)
            action.setCheckable(True)
            action.setChecked(order is DeviceOrder.ADDRESS)
            orders.addAction(action)
            action.triggered.connect(lambda _checked=False, order=order: self.order_chosen.emit(order))
        sort = MenuButton("Sort", order_menu)
        sort.setToolTip("The order of the cameras found on the network")
        self.add_tool(sort)

        add_manually = SmallButton("Add manually", Icon.ADD)
        add_manually.clicked.connect(self.add_manually_requested)
        self.add_padded(add_manually, top=Space.S, bottom=Space.S)

        self._ip_range = QLineEdit(self)
        self._ip_range.setPlaceholderText("IP range, such as 192.168.1.0/24")
        self._ip_range.returnPressed.connect(self._request_scan)
        self._sign_in = QCheckBox("Try to sign in to found cameras", self)
        self._sign_in.setToolTip(
            "Tries these logins in order on each Axis device found, to read more about it and add it with the login "
            "that worked.\nPasswords go only as HTTP Digest answers. Settings edits the same list."
        )
        TextRole.SMALL.apply(self._sign_in)
        self._logins = LoginsEditor()
        self._logins.hide()
        self._logins.edited.connect(lambda: self.logins_edited.emit(self._logins.logins()))
        self._sign_in.toggled.connect(self._logins.setVisible)
        TextRole.SMALL.apply(self._ip_range)
        follow_appearance(self._ip_range, lambda: self._ip_range.setFixedHeight(Height.ROW.px))
        self._scan_button = SmallButton("Rescan", Icon.SCAN)
        self._scan_button.clicked.connect(self._request_scan)
        self._scan_progress = QProgressBar(self)
        self._scan_progress.setRange(0, 0)  # Busy: a scan cannot say how far along it is.
        self._scan_progress.setTextVisible(False)
        self._scan_progress.setFixedHeight(Space.S)
        policy = self._scan_progress.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # So the view does not jump when a scan starts and ends.
        self._scan_progress.setSizePolicy(policy)
        self._scan_progress.hide()
        self._scan_status = muted_label("")
        self._scan_status.hide()

        line = QHBoxLayout()
        line.setSpacing(Space.M)
        line.addWidget(self._ip_range, 1)
        line.addWidget(self._scan_button)
        controls = QWidget(self)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(Space.M, 0, Space.M, 0)
        controls_layout.setSpacing(Space.S)
        controls_layout.addLayout(line)
        controls_layout.addWidget(self._sign_in)
        controls_layout.addWidget(self._logins)
        controls_layout.addWidget(self._scan_progress)
        controls_layout.addWidget(self._scan_status)
        footer = QWidget(self)
        footer.setStyleSheet(SEE_THROUGH)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, Space.S, 0, Space.M)
        footer_layout.setSpacing(Space.XS)
        footer_layout.addWidget(SectionCaption("Scan settings", None, KindColor.MUTED.color))
        footer_layout.addWidget(controls)
        self.add(footer)

    def set_scan_range(self, text: str) -> None:
        """Put *text* in the IP range field."""
        self._ip_range.setText(text)

    def scan_range(self) -> str:
        """Return the IP range as typed."""
        return self._ip_range.text()

    def set_logins(self, logins: Sequence[Login]) -> None:
        """Show *logins* as the ones a scan tries."""
        self._logins.set_logins(logins)

    def sign_in_wanted(self) -> bool:
        """Return whether the user asked the scan to try to sign in."""
        return self._sign_in.isChecked()

    def set_scan_status(self, status: ScanStatus, reason: str = "") -> None:
        """Show *status* under the scan settings, with *reason* on hover; the button waits while a scan runs."""
        scanning = status is ScanStatus.SCANNING
        self._scan_button.setEnabled(not scanning)
        self._scan_progress.setVisible(scanning)
        self._scan_status.setText(status.value)
        self._scan_status.setToolTip(reason)
        self._scan_status.setVisible(bool(status.value))

    def _request_scan(self) -> None:
        if self._scan_button.isEnabled():
            self.scan_requested.emit(self._ip_range.text())
