"""What the Live view shows and does: the workspace's cameras, connecting them, and scanning the network for more."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.device_discovery.network_scan import (
    DeviceOrder,
    DiscoveredCamera,
    SignIn,
    parse_scan_targets,
    scan_network,
)
from ax_devil.modules.settings.config_manager import ConfigManager, default_hint, expand_environment_reference
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import WorkspaceItem, live_stream_item_from_defaults
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.live_view import Facet, LiveView, ScanStatus
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

logger = get_logger(__name__)

Found = Callable[[DiscoveredCamera], None]
Scan = Callable[[tuple[str, ...], str, str, Found], Sequence[DiscoveredCamera]]
"""Search addresses, signing in with a username and password unless the username is empty, and report each find."""


def _scan_network(targets: tuple[str, ...], username: str, password: str, found: Found) -> list[DiscoveredCamera]:
    return scan_network(targets=targets, username=username, password=password, found=found)


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


class _Finds(QObject):
    """Carries each camera a running scan finds to the GUI thread, where it was made; the scan holds it to the end."""

    found = Signal(object)  # DiscoveredCamera


def _defaults() -> tuple[str, str, str]:
    """Return the Settings' IP range for scans, and the stream defaults' username and password as written."""
    config = ConfigManager()
    device = (config.get_raw("defaults", {}) or {}).get("device", {}) or {}
    ip_range = str(config.get("defaults", {}).get("network_scan", {}).get("ip_range", ""))
    return ip_range, str(device.get("username") or ""), str(device.get("password") or "")


def _workspace_hosts(items: Sequence[WorkspaceItem]) -> frozenset[str]:
    """Return the hosts the workspace's items connect to, as entered and with any ``$VARIABLE`` expanded."""
    return frozenset(host for item in items for host in item.hosts)


def _network_row_id(camera: DiscoveredCamera) -> str:
    return f"network/{camera.host}"


def _network_row(camera: DiscoveredCamera, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a *camera* the scan found, named by its model with its address and sign-in under it, whose
    Add does *add*; what else the scan learned shows on hover, and the search finds it by."""
    signed = (camera.sign_in.label,) if camera.sign_in is not SignIn.NOT_TRIED else ()
    platform = " · ".join(part for part in (camera.architecture, camera.soc) if part)
    facts = (
        f"Serial {camera.serial}" if camera.serial else "",
        f"Firmware {camera.firmware}" if camera.firmware else "",
        camera.product_type,
        f"Up {camera.uptime}" if camera.uptime else "",
        platform,
    )
    return WorkspaceBrowserRow(
        row_id=_network_row_id(camera),
        label=camera.model or camera.host,
        icon_kind="live_video",
        detail=" · ".join((camera.host, *signed)) if camera.model else " · ".join(signed),
        facts=("Camera on the network", *(fact for fact in facts if fact)),
        action=RowAction.ADD,
        add=add,
        search_terms=" ".join((camera.host, camera.serial, camera.firmware, camera.product_type, platform)),
    )


def _add_camera(store: WorkspaceStore, camera: DiscoveredCamera, login: tuple[str, str]) -> None:
    """Add *camera* to *store* with the stream defaults, and with *login* when the scan signed in with it; like any
    added item, it opens once resolved."""
    username, password = login if camera.sign_in is SignIn.SIGNED_IN else ("", "")
    store.add_items(
        [
            live_stream_item_from_defaults(
                host=camera.host, label=camera.model, username=username or None, password=password or None
            )
        ]
    )


class LiveViewController(QObject):
    """Feed *view* the workspace's Live section and, once a scan has started, the cameras it finds that the workspace
    does not hold yet, as they are found.

    *workspace* carries out what the rows ask for, the right-click menu included. Nothing touches the network until the
    user asks for a scan of the IP range in the view, which starts as the Settings' default and searches the local
    network when empty, signing in when the view asks to; *scan* runs through *resolver* away from the GUI thread, and
    what it finds is dropped once the view is gone. The search matches every row; the order and the filter apply to
    the found cameras. A found camera's Add puts it in *store* with the user's stream defaults, and with the
    credentials that signed in to it.
    """

    def __init__(
        self,
        view: LiveView,
        workspace: WorkspaceController,
        store: WorkspaceStore,
        resolver: ItemResolver,
        scan: Scan = _scan_network,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._view: LiveView | None = view
        self._workspace = workspace
        self._store = store
        self._resolver = resolver
        self._scan = scan
        self._given = _defaults()  # What the view's fields were last given by default.
        view.set_scan_range(self._given[0])
        view.set_login_hints(default_hint(self._given[1]), default_hint(self._given[2], mask=True))
        self._found: tuple[DiscoveredCamera, ...] | None = None  # None until a scan starts
        self._login = ("", "")  # The credentials of the last scan, as written.
        self._finds: _Finds | None = None
        self._scanning = False
        self._order = DeviceOrder.ADDRESS
        self._hidden: frozenset[Facet] = frozenset()
        self._filtered_out: frozenset[str] = frozenset()  # Row ids of found cameras the filter hides.
        self._query = ""
        workspace.connect_tree(view.tree)
        view.scan_requested.connect(self.scan)
        view.search_changed.connect(self._search)
        view.order_chosen.connect(self._sort)
        view.hidden_facets_changed.connect(self._filter)
        view.destroyed.connect(self._forget_view)
        workspace.sections_changed.connect(self._show)
        self._apply_filter()
        self._show(workspace.sections())

    def refresh_defaults(self) -> None:
        """Give the view the Settings' default IP range, unless the user has typed another in its field, and the
        stream defaults' credentials as what empty sign-in fields stand for."""
        if self._view is None:
            return
        ip_range, username, password = _defaults()
        if self._view.scan_range() == self._given[0]:
            self._view.set_scan_range(ip_range)
        else:
            ip_range = self._given[0]  # The user's range stays theirs, even once a default equals it.
        self._given = (ip_range, username, password)
        self._view.set_login_hints(default_hint(username), default_hint(password, mask=True))

    def scan(self, ip_range: str) -> None:
        """Search *ip_range*, or the local network if it is empty, for cameras, unless a search is already running."""
        if self._scanning or self._view is None:
            return
        try:
            targets = parse_scan_targets(ip_range)
        except ValueError as exc:
            self._view.set_scan_status(ScanStatus.INVALID, str(exc))
            return
        typed = self._view.scan_login()
        self._login = (typed[0] or self._given[1], typed[1] or self._given[2]) if typed is not None else ("", "")
        username, password = (expand_environment_reference(value) for value in self._login)
        self._scanning = True
        self._found = ()
        self._view.set_scan_status(ScanStatus.SCANNING)
        self._show(self._workspace.sections())
        finds = self._finds = _Finds()
        finds.found.connect(self._on_found)
        scan = self._scan
        self._resolver.run(
            lambda: _scan_outcome(lambda: scan(targets, username, password, finds.found.emit)),
            self._on_scanned,
            lambda: self._view is not None,
        )

    def _on_found(self, camera: DiscoveredCamera) -> None:
        if self._found is not None and self._scanning:
            self._found += (camera,)
            self._show(self._workspace.sections())

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

    def _search(self, text: str) -> None:
        self._query = text.strip().casefold()
        self._apply_filter()

    def _sort(self, order: DeviceOrder) -> None:
        self._order = order
        self._show(self._workspace.sections())

    def _filter(self, hidden: frozenset[Facet]) -> None:
        self._hidden = hidden
        self._show(self._workspace.sections())

    def _show(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """Show the Live section, how many of its cameras are connected, and the cameras found that the workspace
        does not hold, in the chosen order, filtered and searched."""
        if self._view is None:
            return
        shown = tuple(section for section in sections if section.kind is BrowserSection.LIVE)
        connected = sum(row.action is RowAction.DISCONNECT for section in shown for row in section.rows)
        if self._found is not None:
            held = _workspace_hosts(self._store.workspace.items)
            cameras = [camera for camera in self._order.sorted(self._found) if camera.host not in held]
            found = tuple(
                _network_row(camera, partial(_add_camera, self._store, camera, self._login)) for camera in cameras
            )
            shown += (WorkspaceBrowserSection(BrowserSection.NETWORK, found),)
            self._view.set_filter_choices(sorted({facet for camera in self._found for facet in camera.facets}))
            self._filtered_out = frozenset(
                _network_row_id(camera) for camera in cameras if self._hidden.intersection(camera.facets)
            )
        self._view.set_sections(shown, f"{connected} connected" if connected else "")

    def _apply_filter(self) -> None:
        """Have the tree hide the found cameras the filter leaves out, now and whenever it rebuilds, and while
        searching, the rows that do not match."""
        if self._view is None:
            return
        query = self._query
        self._view.tree.set_filter(
            lambda row: row.row_id not in self._filtered_out,
            (lambda row: query in row.search_text.casefold()) if query else None,
        )

    def _forget_view(self) -> None:
        self._view = None
