"""What the Live view shows and does: the workspace's cameras, connecting them, and scanning the network for more."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from functools import partial

from PySide6.QtCore import QObject, QTimer

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, LatestRequest
from ax_devil.modules.device_discovery.network_scan import (
    DeviceOrder,
    DiscoveredCamera,
    Login,
    SignIn,
    parse_scan_targets,
    scan_network,
)
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import WorkspaceItem, live_stream_item_from_defaults
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    Facet,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.live_view import LiveView, ScanStatus
from ax_devil.modules.workspace.ui.sections_view_controller import Sections, SectionsViewController
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

logger = get_logger(__name__)

_FOUND_REFRESH_MS = 100

Found = Callable[[DiscoveredCamera], None]
Scan = Callable[[tuple[str, ...], tuple[Login, ...], Found, CancelToken], Sequence[DiscoveredCamera]]
"""Search addresses, trying the logins in order on each device found, and report each find; stop when the token is
cancelled."""


def _scan_network(
    targets: tuple[str, ...], logins: tuple[Login, ...], found: Found, token: CancelToken
) -> list[DiscoveredCamera]:
    return scan_network(targets=targets, logins=logins, found=found, token=token)


def _run_scan(
    scan: Scan, targets: tuple[str, ...], logins: tuple[Login, ...], job: JobContext[DiscoveredCamera]
) -> tuple[DiscoveredCamera, ...]:
    return tuple(scan(targets, logins, job.report, job.token))


def _defaults() -> tuple[str, tuple[Login, ...]]:
    """Return the Settings' IP range for scans, and the logins they try."""
    config = ConfigManager()
    return str(config.get("defaults", {}).get("network_scan", {}).get("ip_range", "")), Login.saved(config)


def _workspace_hosts(items: Sequence[WorkspaceItem]) -> frozenset[str]:
    """Return the hosts the workspace's items connect to, as entered and with any ``$VARIABLE`` expanded."""
    return frozenset(host for item in items for host in item.hosts)


def _network_row_id(camera: DiscoveredCamera) -> str:
    return f"network/{camera.host}"


def _network_row(camera: DiscoveredCamera, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a *camera* the scan found, named by its model with its address and sign-in under it, whose
    Add does *add*; what else the scan learned shows on hover, the search finds it by, and the Filter can hide it by."""
    signed = (camera.sign_in.label,) if camera.sign_in is not SignIn.NOT_TRIED else ()
    return WorkspaceBrowserRow(
        row_id=_network_row_id(camera),
        label=camera.model or camera.host,
        icon_kind="live_video",
        detail=" · ".join((camera.host, *signed)) if camera.model else " · ".join(signed),
        facts=("Camera on the network", *_camera_facts(camera)),
        action=RowAction.ADD,
        add=add,
        search_terms=_camera_search_terms(camera),
        facets=frozenset(Facet(group, value) for group, value in camera.facets),
    )


def _with_scan(row: WorkspaceBrowserRow, found: Mapping[str, DiscoveredCamera]) -> WorkspaceBrowserRow:
    """Return the row of a camera the workspace holds with what a scan learned about it, as a found camera shows it."""
    camera = next((found[host] for host in row.item.hosts if host in found), None) if row.item is not None else None
    if camera is None:
        return row
    facts = (camera.model, *_camera_facts(camera)) if camera.model else _camera_facts(camera)
    return replace(
        row,
        facts=(*row.facts, *facts),
        search_terms=_camera_search_terms(camera),
        facets=row.facets | frozenset(Facet(group, value) for group, value in camera.facets),
    )


def _camera_facts(camera: DiscoveredCamera) -> tuple[str, ...]:
    """Return the lines a camera's row shows on hover of what a scan learned about it."""
    facts = (
        f"Serial {camera.serial}" if camera.serial else "",
        f"Firmware {camera.firmware}" if camera.firmware else "",
        camera.product_type,
        f"Up {camera.uptime}" if camera.uptime else "",
        _platform(camera),
    )
    return tuple(fact for fact in facts if fact)


def _camera_search_terms(camera: DiscoveredCamera) -> str:
    """Return what the search finds a camera's row by of what a scan learned about it."""
    parts = (camera.host, camera.model, camera.serial, camera.firmware, camera.product_type, _platform(camera))
    return " ".join(part for part in parts if part)


def _platform(camera: DiscoveredCamera) -> str:
    return " · ".join(part for part in (camera.architecture, camera.soc) if part)


def _connected(sections: Sections) -> str:
    """Return how many of the workspace's cameras *sections* list as connected, or nothing for none."""
    count = sum(
        row.action is RowAction.DISCONNECT
        for section in sections
        if section.kind is BrowserSection.LIVE
        for row in section.rows
    )
    return f"{count} connected" if count else ""


def _add_camera(store: WorkspaceStore, camera: DiscoveredCamera) -> None:
    """Add *camera* to *store* with the stream defaults, and with the login that signed in to it if one did; like any
    added item, it opens once resolved."""
    login = camera.login
    store.add_items(
        [
            live_stream_item_from_defaults(
                host=camera.host,
                label=camera.model,
                username=login.username if login is not None else None,
                password=login.password if login is not None else None,
            )
        ]
    )


class LiveViewController(QObject):
    """Feed *view* the workspace's Live section and, once a scan has started, the cameras it finds that the workspace
    does not hold yet, as they are found.

    *workspace* carries out what the rows ask for, the right-click menu included. Nothing touches the network until the
    user asks for a scan of the IP range in the view, which starts as the Settings' default and searches the local
    network when empty, signing in when the view asks to; *scan* runs as one of *jobs*, and what it finds is dropped
    once the view is gone or `cleanup` ran. A camera the workspace holds shows what the scan learned about it as a
    found camera does; the order applies to the found cameras. A found camera's Add puts it in *store* with the
    user's stream defaults, and with the credentials that signed in to it.
    """

    def __init__(
        self,
        view: LiveView,
        workspace: WorkspaceController,
        store: WorkspaceStore,
        jobs: BackgroundJobs,
        scan: Scan = _scan_network,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._view: LiveView | None = view
        self._store = store
        self._jobs = jobs.scope(view)
        self._scan = scan
        self._given_range, self._logins = _defaults()  # The range is what the view's field was last given by default.
        view.set_scan_range(self._given_range)
        view.set_logins(self._logins)
        self._found: tuple[DiscoveredCamera, ...] | None = None  # None until a scan starts
        self._scan_request = LatestRequest(view)
        self._order = DeviceOrder.ADDRESS
        self._found_refresh = QTimer(self)  # Finds can arrive in a burst; one refresh covers them all.
        self._found_refresh.setSingleShot(True)
        self._found_refresh.setInterval(_FOUND_REFRESH_MS)
        self._found_refresh.timeout.connect(self._refresh)
        view.scan_requested.connect(self.scan)
        view.logins_edited.connect(self._save_logins)
        view.order_chosen.connect(self._sort)
        view.destroyed.connect(self._forget_view)
        self._listing = SectionsViewController(
            view, workspace, {BrowserSection.LIVE}, compose=self._with_found, summary=_connected
        )

    def refresh_defaults(self) -> None:
        """Give the view the Settings' default IP range, unless the user has typed another in its field, and the
        logins a scan tries."""
        if self._view is None:
            return
        ip_range, self._logins = _defaults()
        if self._view.scan_range() == self._given_range:
            self._given_range = ip_range
            self._view.set_scan_range(ip_range)
        self._view.set_logins(self._logins)

    def scan(self, ip_range: str) -> None:
        """Search *ip_range*, or the local network if it is empty, for cameras, unless a search is already running."""
        if self._scan_request.pending or self._view is None:
            return
        try:
            targets = parse_scan_targets(ip_range)
        except ValueError as exc:
            self._view.set_scan_status(ScanStatus.INVALID, str(exc))
            return
        logins = self._logins if self._view.sign_in_wanted() else ()
        self._found = ()
        self._view.set_scan_status(ScanStatus.SCANNING)
        self._refresh()
        self._scan_request.submit(
            self._jobs,
            "network scan",
            partial(_run_scan, self._scan, targets, logins),
            on_result=self._on_scanned,
            on_error=self._on_scan_failed,
            on_progress=self._on_found,
        )

    def cleanup(self) -> None:
        """Stop showing the outcome of a scan still running."""
        self._found_refresh.stop()
        self._scan_request.close()
        self._jobs.close()

    def _save_logins(self, logins: tuple[Login, ...]) -> None:
        """Keep the logins the user edited in the view as the ones scans try, shared with Settings."""
        if logins == self._logins:
            return
        try:
            Login.save(ConfigManager(), logins)
        except Exception as exc:
            logger.warning(f"Could not save the network scan logins: {exc}")
            if self._view is not None:
                self._view.set_logins(self._logins)
            return
        self._logins = logins

    def _on_found(self, camera: DiscoveredCamera) -> None:
        if self._found is not None:
            self._found += (camera,)
            if not self._found_refresh.isActive():
                self._found_refresh.start()

    def _on_scanned(self, cameras: tuple[DiscoveredCamera, ...]) -> None:
        self._found_refresh.stop()
        if self._view is None:
            return
        self._view.set_scan_status(ScanStatus.IDLE)
        self._found = cameras
        self._refresh()

    def _on_scan_failed(self, error: Exception) -> None:
        if self._found_refresh.isActive():
            self._found_refresh.stop()
            self._refresh()
        if self._view is not None:
            self._view.set_scan_status(ScanStatus.FAILED, str(error) or type(error).__name__)

    def _sort(self, order: DeviceOrder) -> None:
        self._order = order
        self._refresh()

    def _refresh(self) -> None:
        if self._view is not None:
            self._listing.refresh()

    def _with_found(self, held: Sections) -> Sections:
        """Return *held* with what the scan learned about its cameras, then the cameras found that the workspace does
        not hold, in the chosen order; only *held* before any scan."""
        if self._found is None:
            return held
        by_host = {camera.host: camera for camera in self._found}
        held = tuple(replace(section, rows=tuple(_with_scan(row, by_host) for row in section.rows)) for section in held)
        hosts = _workspace_hosts(self._store.workspace.items)
        found = tuple(
            _network_row(camera, partial(_add_camera, self._store, camera))
            for camera in self._order.sorted(self._found)
            if camera.host not in hosts
        )
        return (*held, WorkspaceBrowserSection(BrowserSection.NETWORK, found))

    def _forget_view(self) -> None:
        self._view = None
