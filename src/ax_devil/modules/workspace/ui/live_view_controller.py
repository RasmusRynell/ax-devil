"""What the Live view shows and does: the workspace's cameras, connecting them, and scanning the network for more."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QObject

from ax_devil.modules.data_sources.live.network_scan import DiscoveredCamera, parse_scan_targets, scan_network
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import WorkspaceItem, live_stream_item_from_defaults
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.live_view import LiveView, ScanStatus
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

logger = get_logger(__name__)


@dataclass(frozen=True)
class _ScanOutcome:
    """What one scan found, or why it failed."""

    cameras: tuple[DiscoveredCamera, ...] = ()
    error: str = ""


def _scan_outcome(scan: Callable[[], Sequence[DiscoveredCamera]]) -> _ScanOutcome:
    """Run *scan*, on a worker thread, and keep its failure as a reason instead of raising it."""
    try:
        return _ScanOutcome(cameras=tuple(scan()))
    except Exception as exc:
        logger.exception("Network scan failed")
        return _ScanOutcome(error=str(exc) or type(exc).__name__)


def _default_ip_range() -> str:
    """Return the IP range the Settings give scans to start with."""
    return str(ConfigManager().get("defaults", {}).get("network_scan", {}).get("ip_range", ""))


def _workspace_hosts(items: Sequence[WorkspaceItem]) -> frozenset[str]:
    """Return the hosts the workspace's items connect to, as entered and with any ``$VARIABLE`` expanded."""
    return frozenset(host for item in items for host in item.hosts)


def _network_row(camera: DiscoveredCamera, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a *camera* the scan found, named by its model with its address under it, whose Add does
    *add*; its serial number shows on hover."""
    return WorkspaceBrowserRow(
        row_id=f"network/{camera.host}",
        label=camera.model or camera.host,
        icon_kind="live_video",
        detail=camera.host if camera.model else "",
        facts=("Camera on the network", *((f"Serial {camera.serial}",) if camera.serial else ())),
        action=RowAction.ADD,
        add=add,
    )


def _add_camera(store: WorkspaceStore, camera: DiscoveredCamera) -> None:
    """Add *camera* to *store* with the stream defaults; like any added item, it opens once resolved."""
    store.add_items([live_stream_item_from_defaults(host=camera.host, label=camera.model)])


class LiveViewController(QObject):
    """Feed *view* the workspace's Live section and, once a scan has run, the cameras it found that the workspace
    does not hold yet.

    *workspace* carries out what the rows ask for. Nothing touches the network until the user asks for a scan of the
    IP range in the view, which starts as the Settings' default and searches the local network when empty; *scan* runs
    through *resolver* away from the GUI thread, and its result is dropped once the view is gone. A found camera's Add
    puts it in *store* with the user's stream defaults.
    """

    def __init__(
        self,
        view: LiveView,
        workspace: WorkspaceController,
        store: WorkspaceStore,
        resolver: ItemResolver,
        scan: Callable[[tuple[str, ...]], Sequence[DiscoveredCamera]] = lambda targets: scan_network(targets=targets),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._view: LiveView | None = view
        self._workspace = workspace
        self._store = store
        self._resolver = resolver
        self._scan = scan
        self._given_range = _default_ip_range()  # What the view's field was last set to by default.
        view.set_scan_range(self._given_range)
        self._found: tuple[DiscoveredCamera, ...] | None = None  # None until a scan finishes
        self._scanning = False
        workspace.connect_tree(view.tree)
        view.scan_requested.connect(self.scan)
        view.destroyed.connect(self._forget_view)
        workspace.sections_changed.connect(self._show)
        self._show(workspace.sections())

    def refresh_default_range(self) -> None:
        """Give the view the Settings' default IP range, unless the user has typed another in its field."""
        if self._view is not None and self._view.scan_range() == self._given_range:
            self._given_range = _default_ip_range()
            self._view.set_scan_range(self._given_range)

    def scan(self, ip_range: str) -> None:
        """Search *ip_range*, or the local network if it is empty, for cameras, unless a search is already running."""
        if self._scanning or self._view is None:
            return
        try:
            targets = parse_scan_targets(ip_range)
        except ValueError as exc:
            self._view.set_scan_status(ScanStatus.INVALID, str(exc))
            return
        self._scanning = True
        self._view.set_scan_status(ScanStatus.SCANNING)
        scan = self._scan
        self._resolver.run(
            lambda: _scan_outcome(lambda: scan(targets)), self._on_scanned, lambda: self._view is not None
        )

    def _on_scanned(self, outcome: _ScanOutcome) -> None:
        self._scanning = False
        if self._view is None:
            return
        if outcome.error:
            self._view.set_scan_status(ScanStatus.FAILED, outcome.error)
            return
        self._view.set_scan_status(ScanStatus.IDLE)
        self._found = outcome.cameras
        self._show(self._workspace.sections())

    def _show(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """Show the Live section, how many of its cameras are connected, and the cameras found that the workspace
        does not hold."""
        if self._view is None:
            return
        shown = tuple(section for section in sections if section.kind is BrowserSection.LIVE)
        connected = sum(row.action is RowAction.DISCONNECT for section in shown for row in section.rows)
        if self._found is not None:
            held = _workspace_hosts(self._store.workspace.items)
            found = tuple(
                _network_row(camera, partial(_add_camera, self._store, camera))
                for camera in self._found
                if camera.host not in held
            )
            shown += (WorkspaceBrowserSection(BrowserSection.NETWORK, found),)
        self._view.set_sections(shown, f"{connected} connected" if connected else "")

    def _forget_view(self) -> None:
        self._view = None
