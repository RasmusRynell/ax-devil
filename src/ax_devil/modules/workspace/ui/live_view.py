"""The Live view: search, sort and filter over the cameras in the workspace and on the network, then the settings of
the network scan."""

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
from ax_devil.modules.device_discovery.network_scan import DeviceOrder
from ax_devil.modules.workspace.ui.sections_view import SectionsView

LIVE_VIEW_ID = "live"
Facet = tuple[str, str]
"""A group a found camera can be filtered by, and its value there, such as ``("Type", "Network Camera")``."""


class ScanStatus(Enum):
    """How the last network scan went, as the text shown under the scan settings."""

    IDLE = ""
    SCANNING = "Scanning…"
    INVALID = "Invalid IP range"
    FAILED = "Scan failed"


class LiveView(SectionsView):
    """Search, sort and filter, the sections it is given, then Add manually and the scan settings; it decides nothing
    itself.

    ``scan_requested`` carries the IP range as typed, ``add_manually_requested`` that the user wants to enter a
    camera's details, ``search_changed`` the search text, ``order_chosen`` the ``DeviceOrder`` picked, and
    ``hidden_facets_changed`` the set of facets whose found cameras the user hid.
    """

    scan_requested = Signal(str)
    add_manually_requested = Signal()
    search_changed = Signal(str)
    order_chosen = Signal(object)  # DeviceOrder
    hidden_facets_changed = Signal(object)  # frozenset[Facet]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(LIVE_VIEW_ID, "Live", Icon.LIVE_VIDEO, parent)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search cameras")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(Icon.SEARCH.icon(), QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self.search_changed)
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
        self._filter_menu = QMenu(self)
        self._filter = MenuButton("Filter", self._filter_menu)
        self._filter.setToolTip("Show or hide found cameras by type and sign-in")
        self._filter.setEnabled(False)
        self._filter_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
        self._hidden: set[Facet] = set()
        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(Space.S)
        toolbar_layout.addWidget(self._search, 1)
        toolbar_layout.addWidget(sort)
        toolbar_layout.addWidget(self._filter)
        self.add_padded(toolbar, top=Space.S, bottom=Space.XS, index=0)

        add_manually = SmallButton("Add manually", Icon.ADD)
        add_manually.clicked.connect(self.add_manually_requested)
        self.add_padded(add_manually, top=Space.S, bottom=Space.S)

        self._ip_range = QLineEdit(self)
        self._ip_range.setPlaceholderText("IP range, such as 192.168.1.0/24")
        self._ip_range.returnPressed.connect(self._request_scan)
        self._sign_in = QCheckBox("Sign in to found cameras", self)
        self._sign_in.setToolTip(
            "Read more about each camera, and add it with the credentials that worked.\n"
            "They go only to Axis devices, over HTTPS or as an HTTP Digest answer."
        )
        TextRole.SMALL.apply(self._sign_in)
        self._username = QLineEdit(self)
        self._username.setAccessibleName("Username")
        self._password = QLineEdit(self)
        self._password.setAccessibleName("Password")
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self.set_login_hints("", "")
        for field in (self._ip_range, self._username, self._password):
            TextRole.SMALL.apply(field)
            follow_appearance(field, lambda field=field: field.setFixedHeight(Height.ROW.px))
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
        login = QWidget(self)
        login_layout = QHBoxLayout(login)
        login_layout.setContentsMargins(0, 0, 0, 0)
        login_layout.setSpacing(Space.M)
        login_layout.addWidget(self._username, 1)
        login_layout.addWidget(self._password, 1)
        login.hide()
        self._sign_in.toggled.connect(login.setVisible)
        controls = QWidget(self)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(Space.M, 0, Space.M, 0)
        controls_layout.setSpacing(Space.S)
        controls_layout.addLayout(line)
        controls_layout.addWidget(self._sign_in)
        controls_layout.addWidget(login)
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

    def set_login_hints(self, username: str, password: str) -> None:
        """Show *username* and *password*, the defaults an empty sign-in field stands for, as their placeholders."""
        self._username.setPlaceholderText(username or "Username")
        self._password.setPlaceholderText(password or "Password")

    def scan_login(self) -> tuple[str, str] | None:
        """Return the username and password as typed, empty where left to the defaults, or None without sign-in."""
        return (self._username.text(), self._password.text()) if self._sign_in.isChecked() else None

    def set_filter_choices(self, facets: Sequence[Facet]) -> None:
        """Offer to hide found cameras by *facets*, grouped, in each group that has a choice to make."""
        values: dict[str, list[str]] = {}
        for group, value in facets:
            values.setdefault(group, []).append(value)
        groups = tuple(
            (group, tuple(names))
            for group, names in values.items()
            # A group with one value is no choice, unless the user hid it and needs to show it again.
            if len(names) > 1 or any((group, name) in self._hidden for name in names)
        )
        if groups == self._filter_groups:
            return
        self._filter_groups = groups
        self._filter_menu.clear()
        for group, names in groups:
            self._filter_menu.addSection(group)
            for name in names:
                action = self._filter_menu.addAction(name)
                action.setCheckable(True)
                action.setChecked((group, name) not in self._hidden)
                action.toggled.connect(lambda shown, facet=(group, name): self._show_facet(facet, shown))
        self._filter.setEnabled(bool(groups))

    def _show_facet(self, facet: Facet, shown: bool) -> None:
        if shown:
            self._hidden.discard(facet)
        else:
            self._hidden.add(facet)
        self.hidden_facets_changed.emit(frozenset(self._hidden))

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
