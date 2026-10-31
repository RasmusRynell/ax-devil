"""The Live view: the cameras in the workspace and on the network, with a button to add one manually and the settings
of the network scan under them."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from PySide6.QtCore import Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QProgressBar, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.device_discovery.network_scan import Login
from ax_devil.modules.workspace.ui.footer_panel import FooterPanel
from ax_devil.modules.workspace.ui.logins_editor import LoginsEditor
from ax_devil.modules.workspace.ui.sections_view import SectionsView

LIVE_VIEW_ID = "live"


class ScanStatus(Enum):
    """How the last network scan went, as the text shown in the header of the scan settings."""

    IDLE = ""
    SCANNING = "Scanning…"
    INVALID = "Invalid IP range"
    FAILED = "Scan failed"

    @property
    def color(self) -> StatusColor | None:
        """Return the status color the text takes, or None for the muted look."""
        return StatusColor.ERROR if self in (ScanStatus.INVALID, ScanStatus.FAILED) else None


class ScanSettingsPanel(FooterPanel):
    """The settings of the network scan: the IP range with Rescan, whether to sign in and the logins to try, and, in
    its header, how the last scan went.

    ``scan_requested`` carries the IP range as typed, and ``logins_edited`` the logins a scan tries once the user
    edited them.
    """

    scan_requested = Signal(str)
    logins_edited = Signal(object)  # tuple[Login, ...]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Scan settings", parent)
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
        # A thin line over the panel's top edge, outside its layout, so showing it never moves anything.
        self._scan_progress = QProgressBar(self)
        self._scan_progress.setRange(0, 0)  # Busy: a scan cannot say how far along it is.
        self._scan_progress.setTextVisible(False)
        self._scan_progress.setFixedHeight(Space.XS)
        # The theme's 1px border and rounded chunk leave no room for a chunk in a line this thin.
        self._scan_progress.setStyleSheet(
            "QProgressBar { border: none; background: transparent; }"
            "QProgressBar::chunk { background: palette(highlight); border-radius: 0px; }"
        )
        self._scan_progress.hide()

        line = QHBoxLayout()
        line.setSpacing(Space.M)
        line.addWidget(self._ip_range, 1)
        line.addWidget(self._scan_button)
        self.controls_layout.addLayout(line)
        self.controls_layout.addWidget(self._sign_in)
        self.controls_layout.addWidget(self._logins)

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

    def set_sign_in(self, enabled: bool) -> None:
        """Set whether scans try the saved logins, showing their editor when enabled."""
        self._sign_in.setChecked(enabled)

    def set_scan_status(self, status: ScanStatus, reason: str = "") -> None:
        """Show *status* in the header of the scan settings, with *reason* on hover; the button waits while a scan
        runs."""
        scanning = status is ScanStatus.SCANNING
        self._scan_button.setEnabled(not scanning)
        self._scan_progress.setVisible(scanning)
        self.caption.set_aside(status.value, status.color)
        self.caption.setToolTip(reason)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Keep the progress line across the top edge."""
        super().resizeEvent(event)
        self._scan_progress.setGeometry(0, 0, self.width(), self._scan_progress.height())

    def _request_scan(self) -> None:
        if self._scan_button.isEnabled():
            self.scan_requested.emit(self._ip_range.text())


class LiveView(SectionsView):
    """The sections it is given, with an Add manually button under them and the scan settings below it; it decides
    nothing itself.

    ``scan_requested`` carries the IP range as typed, ``add_manually_requested`` that the user wants to enter a
    camera's details, and ``logins_edited`` the logins a scan tries once the user edited them.
    """

    scan_requested = Signal(str)
    add_manually_requested = Signal()
    logins_edited = Signal(object)  # tuple[Login, ...]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            LIVE_VIEW_ID, "Live", Icon.LIVE_VIDEO, search_placeholder="Search", empty_text="No cameras", parent=parent
        )
        self.add_footer_button("Add manually", Icon.ADD).clicked.connect(self.add_manually_requested)
        self._scan = ScanSettingsPanel(self)
        self._scan.scan_requested.connect(self.scan_requested)
        self._scan.logins_edited.connect(self.logins_edited)
        self.set_footer_panel(self._scan)

    def set_scan_range(self, text: str) -> None:
        """Put *text* in the IP range field."""
        self._scan.set_scan_range(text)

    def scan_range(self) -> str:
        """Return the IP range as typed."""
        return self._scan.scan_range()

    def set_logins(self, logins: Sequence[Login]) -> None:
        """Show *logins* as the ones a scan tries."""
        self._scan.set_logins(logins)

    def sign_in_wanted(self) -> bool:
        """Return whether the user asked the scan to try to sign in."""
        return self._scan.sign_in_wanted()

    def set_sign_in(self, enabled: bool) -> None:
        """Set whether scans try the saved logins, showing their editor when enabled."""
        self._scan.set_sign_in(enabled)

    def set_scan_status(self, status: ScanStatus, reason: str = "") -> None:
        """Show *status* in the header of the scan settings, with *reason* on hover; the button waits while a scan
        runs."""
        self._scan.set_scan_status(status, reason)
