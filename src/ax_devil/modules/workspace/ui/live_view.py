"""The Live view: the cameras in the workspace, with connect and disconnect, and cameras found on the network."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow, muted_label
from ax_devil.modules.chrome.row_list import RowList
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.small_button import ButtonKind, SmallButton
from ax_devil.modules.chrome.theme import KindColor, StatusColor
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.data_sources.live.network_scan import DiscoveredCamera
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow

LIVE_VIEW_ID = "live"


class CameraState(Enum):
    """Where a camera in the workspace stands: the action its row offers, and its icon and the icon's color."""

    CONNECTED = ("Disconnect", Icon.LIVE_VIDEO, KindColor.LIVE)
    DISCONNECTED = ("Connect", Icon.LIVE_VIDEO, KindColor.MUTED)
    PENDING = ("", Icon.LIVE_VIDEO, KindColor.MUTED)
    """Still resolving: nothing to connect yet."""
    UNAVAILABLE = ("", Icon.WARNING, StatusColor.WARNING)
    """Cannot open; its row shows why."""

    def __init__(self, action: str, icon: Icon, color: KindColor | StatusColor) -> None:
        self.action = action
        self.icon = icon
        self.color = color

    @classmethod
    def of(cls, row: WorkspaceBrowserRow) -> CameraState:
        """Return the state of the camera *row* stands for; connected means a viewer shows it."""
        if row.unavailable_reason is not None:
            return cls.UNAVAILABLE
        if row.activation_target is None:
            return cls.PENDING
        return cls.CONNECTED if row.is_open else cls.DISCONNECTED


class ScanStatus(Enum):
    """How the last network scan went, as the text shown beside the scan button."""

    IDLE = ""
    SCANNING = "Scanning…"
    FAILED = "Scan failed"


class LiveView(ViewPanel):
    """List the cameras it is given, and report what the user asks for; it decides nothing itself.

    ``camera_action_requested`` carries the row whose Connect or Disconnect was pressed, ``add_requested`` the network
    camera to add.
    """

    camera_action_requested = Signal(object)  # WorkspaceBrowserRow
    add_requested = Signal(object)  # DiscoveredCamera
    scan_requested = Signal()
    add_by_address_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._connected = muted_label("")
        super().__init__("Live", header_widget=self._connected, parent=parent)
        self._rows: tuple[WorkspaceBrowserRow, ...] = ()
        self._network: tuple[DiscoveredCamera, ...] | None = None

        rows = RowList(self)
        self._workspace_section = rows.section()
        self._network_section = rows.section()
        add_by_address = SmallButton("Add by address", Icon.ADD)
        add_by_address.clicked.connect(self.add_by_address_requested)
        rows.add_padded(add_by_address, top=Space.M, bottom=Space.S)
        self.add(rows, 1)

        self._scan_button = SmallButton("Scan network", Icon.SCAN)
        self._scan_button.clicked.connect(self.scan_requested)
        self._scan_status = muted_label("")
        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(Space.M)
        footer_layout.addWidget(self._scan_button)
        footer_layout.addWidget(self._scan_status, 1)
        self.add_padded(footer, top=Space.S, bottom=Space.M)
        follow_appearance(self, self._render)

    def sidebar_view(self) -> SidebarView:
        """Return this view as the sidebar's Live view."""
        return SidebarView(LIVE_VIEW_ID, "Live", Icon.LIVE_VIDEO, ViewGroup.SOURCES, self)

    def set_cameras(self, rows: Sequence[WorkspaceBrowserRow]) -> None:
        """List *rows*, the workspace's live stream rows, under "In workspace"."""
        self._rows = tuple(rows)
        connected = sum(CameraState.of(row) is CameraState.CONNECTED for row in self._rows)
        self._connected.setText(f"{connected} connected")
        self._connected.setVisible(connected > 0)
        self._render()

    def set_network(self, cameras: Sequence[DiscoveredCamera]) -> None:
        """List *cameras* under "On the network"; that section is hidden until the first call, after the first scan."""
        self._network = tuple(cameras)
        self._render()

    def set_scan_status(self, status: ScanStatus, reason: str = "") -> None:
        """Show *status* beside the scan button, with *reason* on hover; the button waits while a scan runs."""
        self._scan_button.setEnabled(status is not ScanStatus.SCANNING)
        self._scan_status.setText(status.value)
        self._scan_status.setToolTip(reason)

    def _render(self) -> None:
        """Rebuild both sections; icons in kind colors are drawn for the current palette."""
        palette = self.palette()
        self._workspace_section.clear()
        if self._rows:
            self._workspace_section.add(SectionCaption("In workspace", len(self._rows), KindColor.LIVE.color))
        for row in self._rows:
            state = CameraState.of(row)
            trailing: list[QWidget] = []
            if state.action:
                button = SmallButton(state.action)
                button.clicked.connect(lambda _=False, row=row: self.camera_action_requested.emit(row))
                trailing.append(button)
            item = ListRow(
                row.label,
                icon=state.icon.icon(state.color.color(palette)),
                subtitle=row.unavailable_reason or row.detail,
                trailing=trailing,
                dim=state is not CameraState.CONNECTED,
            )
            item.setToolTip(row.tooltip)
            self._workspace_section.add(item)

        self._network_section.clear()
        if self._network is None:
            return
        count = len(self._network) or None
        self._network_section.add(SectionCaption("On the network", count, KindColor.MUTED.color))
        if not self._network:
            self._network_section.add_padded(muted_label("No new cameras"), top=Space.XS, bottom=Space.XS)
        for camera in self._network:
            add = SmallButton("Add", Icon.ADD, ButtonKind.PRIMARY)
            add.clicked.connect(lambda _=False, camera=camera: self.add_requested.emit(camera))
            details = (camera.host, camera.serial) if camera.serial else (camera.host,)
            self._network_section.add(
                ListRow(
                    camera.model or camera.host,
                    icon=Icon.LIVE_VIDEO.icon(KindColor.MUTED.color(palette)),
                    subtitle=" · ".join(details),
                    trailing=[add],
                )
            )
