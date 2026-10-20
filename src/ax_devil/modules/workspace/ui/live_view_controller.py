"""What the Live view shows and does: the workspace's cameras, streaming them, and scanning the network for more."""

from __future__ import annotations

import ipaddress
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
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.workspace.core import (
    ItemResolution,
    LiveStreamItem,
    WorkspaceItem,
    live_stream_item_from_defaults,
)
from ax_devil.modules.workspace.ui.browser_rows import (
    BY_NAME,
    BrowserSection,
    Facet,
    OfferedAction,
    RowAction,
    RowOrder,
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
Scan = Callable[[tuple[str, ...], tuple[Login, ...], Found, CancelToken, bool], Sequence[DiscoveredCamera]]
"""Search addresses, trying the logins in order on each device found, and report each find; stop when the token is
cancelled."""
Check = Callable[[str, Login, CancelToken], DiscoveredCamera | None]
"""Sign in to the camera at a host with a login, and return what that told, or None when no Axis device answers."""

_REFUSED = "The camera refused the login saved with it."
"""What a held camera's row says when its own login does not get in."""
_NOT_SIGNED_IN = "Not signed in: add a login under Try to sign in."
"""What a found camera's row says on hover when it has no Add."""
_SIGN_IN_PROBLEMS = (SignIn.REFUSED, SignIn.FAILED)


def _scan_network(
    targets: tuple[str, ...], logins: tuple[Login, ...], found: Found, token: CancelToken, low_impact: bool
) -> list[DiscoveredCamera]:
    return scan_network(targets=targets, logins=logins, found=found, token=token, low_impact=low_impact)


def _run_scan(
    scan: Scan, targets: tuple[str, ...], logins: tuple[Login, ...], low_impact: bool, job: JobContext[DiscoveredCamera]
) -> tuple[DiscoveredCamera, ...]:
    return tuple(scan(targets, logins, job.report, job.token, low_impact))


def _check_access(host: str, login: Login, token: CancelToken) -> DiscoveredCamera | None:
    cameras = scan_network(targets=(host,), logins=(login,), token=token)
    return cameras[0] if cameras else None


def _run_check(check: Check, host: str, login: Login, job: JobContext[None]) -> DiscoveredCamera | None:
    return check(host, login, job.token)


def _access_request(item: WorkspaceItem) -> tuple[str, Login] | None:
    """Return the address and the login to check *item*'s access with, or None when it has no IPv4 address or no
    username to try."""
    if not isinstance(item, LiveStreamItem):
        return None
    values = item.expanded()
    try:
        ipaddress.IPv4Address(values.host)
    except ValueError:
        return None
    return (values.host, Login(item.username, item.password)) if values.username else None


def _defaults() -> tuple[str, tuple[Login, ...]]:
    """Return the Settings' IP range for scans, and the logins they try."""
    config = ConfigManager()
    return str(config.get("defaults", {}).get("network_scan", {}).get("ip_range", "")), Login.saved(config)


def _workspace_hosts(items: Sequence[WorkspaceItem]) -> frozenset[str]:
    """Return the hosts the workspace's items connect to, as entered and with any ``$VARIABLE`` expanded."""
    return frozenset(host for item in items for host in item.hosts)


def _network_row_id(camera: DiscoveredCamera) -> str:
    return f"network/{camera.host}"


def _joined(parts: Sequence[str]) -> str:
    """Return *parts* as one detail line that wraps only between them, never inside one."""
    return " · ".join(part.replace(" ", "\u00a0") for part in parts if part)


def _network_row(camera: DiscoveredCamera, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a *camera* the scan found, with its address and all non-secret details visible; it has Add
    only when the scan signed in to it."""
    details = _camera_details(camera, show_sign_in_problem=True)
    can_add = camera.sign_in is SignIn.SIGNED_IN
    return WorkspaceBrowserRow(
        row_id=_network_row_id(camera),
        label=camera.host,
        icon_kind="live_video",
        detail=_joined(details),
        wrap_detail=True,
        facts=("Camera on the network", *details),
        warning=None if can_add else _NOT_SIGNED_IN,
        actions=(OfferedAction(RowAction.ADD),) if can_add else (),
        add=add if can_add else None,
        idle=not can_add,
        search_terms=_camera_search_terms(camera),
        facets=frozenset(Facet(group, value) for group, value in camera.facets),
    )


def _scan_camera(
    row: WorkspaceBrowserRow, found: Mapping[str, DiscoveredCamera], checked: Mapping[str, DiscoveredCamera]
) -> DiscoveredCamera | None:
    """Return what *checked*, the row's own sign-in, or else a scan in *found* learned about the camera of a row the
    workspace holds, or None when nothing did."""
    item = row.item
    if item is None:
        return None
    return checked.get(item.id) or next((found[host] for host in item.hosts if host in found), None)


def _with_scan(
    row: WorkspaceBrowserRow, camera: DiscoveredCamera | None, checked: Mapping[str, DiscoveredCamera]
) -> WorkspaceBrowserRow:
    """Return the row of a camera the workspace holds with what *camera*, which signing in or a scan learned about it,
    shows as a found camera does; one that refuses its own login warns and can not start a stream."""
    item = row.item
    if item is None or camera is None:
        return row
    own = checked.get(item.id)
    details = _camera_details(camera, show_sign_in_problem=False)
    row = replace(
        row,
        detail=_joined((row.detail, *details)),
        facts=(*row.facts, *details),
        search_terms=_camera_search_terms(camera),
        facets=row.facets | frozenset(Facet(group, value) for group, value in camera.facets),
    )
    if own is not None and own.sign_in is SignIn.REFUSED:
        return replace(
            row,
            icon_kind="unavailable",
            warning=_REFUSED,
            actions=tuple(offered for offered in row.actions if offered.action is not RowAction.START_STREAM),
        )
    return row


def _camera_details(camera: DiscoveredCamera, *, show_sign_in_problem: bool) -> tuple[str, ...]:
    """Return the non-secret details the row shows for what a scan learned about a camera; with
    *show_sign_in_problem*, a sign-in that did not work is mentioned."""
    facts = (
        camera.model,
        camera.sign_in.label if show_sign_in_problem and camera.sign_in in _SIGN_IN_PROBLEMS else "",
        f"Serial {camera.serial}" if camera.serial else "",
        f"Firmware {camera.firmware}" if camera.firmware else "",
        f"Type {camera.product_type}" if camera.product_type else "",
        f"Up {camera.uptime}" if camera.uptime else "",
        f"Architecture {camera.architecture}" if camera.architecture else "",
        f"SoC {camera.soc}" if camera.soc else "",
    )
    return tuple(fact for fact in facts if fact)


def _camera_search_terms(camera: DiscoveredCamera) -> str:
    """Return what the search finds a camera's row by of what a scan learned about it."""
    parts = (camera.host, camera.model, camera.serial, camera.firmware, camera.product_type, _platform(camera))
    return " ".join(part for part in parts if part)


def _platform(camera: DiscoveredCamera) -> str:
    return " · ".join(part for part in (camera.architecture, camera.soc) if part)


def _streaming(sections: Sections) -> str:
    """Return how many of the workspace's cameras *sections* list as streaming, or nothing for none."""
    count = sum(
        row.offered(RowAction.STOP_STREAM) is not None
        for section in sections
        if section.kind is BrowserSection.LIVE
        for row in section.rows
    )
    return f"{count} streaming" if count else ""


def _camera_item(camera: DiscoveredCamera) -> LiveStreamItem:
    """Return the item for *camera* with the stream defaults, and with the login that signed in to it if one did."""
    login = camera.login
    return live_stream_item_from_defaults(
        host=camera.host,
        username=login.username if login is not None else None,
        password=login.password if login is not None else None,
    )


class LiveViewController(QObject):
    """Feed *view* the workspace's Live section and, once a scan has started, the cameras it finds that the workspace
    does not hold yet, as they are found.

    *workspace* carries out what the rows ask for, the right-click menu included. A scan uses the IP range in the
    view, which starts as the Settings' default and searches the local
    network when empty, signing in when the view asks to; *scan* runs as one of *jobs*, and what it finds is dropped
    once the view is gone or `cleanup` ran. A camera the workspace holds shows what the scan learned about it as a
    found camera does; the view's Sort orders both by what is known of each camera. A found camera's Add puts it in
    *store* with the user's stream defaults, and with the credentials that signed in to it; it only adds, the stream
    starts when the user starts it. It can be added only once signed in, so Add never puts in a camera that refuses
    its login.

    The workspace's cameras are *check*ed once with their own login as they resolve, unless Settings turn signing in
    off; one that refuses it shows a warning and can not start a stream. Until a check answers, or when it can not
    run or fails otherwise, a camera can start.
    """

    def __init__(
        self,
        view: LiveView,
        workspace: WorkspaceController,
        store: WorkspaceStore,
        jobs: BackgroundJobs,
        scan: Scan = _scan_network,
        parent: QObject | None = None,
        check: Check = _check_access,
    ) -> None:
        super().__init__(parent)
        self._view: LiveView | None = view
        self._store = store
        self._jobs = jobs.scope(view)
        self._scan = scan
        self._check = check
        self._checked: dict[str, DiscoveredCamera] = {}  # By item id, what signing in with the item's login found.
        self._checks: dict[str, LatestRequest] = {}
        self._given_range, self._logins = _defaults()  # The range is what the view's field was last given by default.
        view.set_scan_range(self._given_range)
        view.set_logins(self._logins)
        self._given_sign_in = GlobalSettings().scan_sign_in
        view.set_sign_in(self._given_sign_in)
        self._found: tuple[DiscoveredCamera, ...] | None = None  # None until a scan starts
        self._scan_request = LatestRequest(view)
        self._cameras: dict[str, DiscoveredCamera] = {}  # By row id, what the rows listed last show of their camera.
        self._found_refresh = QTimer(self)  # Finds can arrive in a burst; one refresh covers them all.
        self._found_refresh.setSingleShot(True)
        self._found_refresh.setInterval(_FOUND_REFRESH_MS)
        self._found_refresh.timeout.connect(self._refresh)
        view.scan_requested.connect(self._rescan)
        view.logins_edited.connect(self._save_logins)
        view.destroyed.connect(self._forget_view)
        store.workspace_replaced.connect(self._clear_access)
        store.item_edited.connect(self._invalidate_access)
        store.item_removed.connect(self._invalidate_access)
        store.items_resolved.connect(self._check_access_of)
        orders = [self._device_order(order) for order in DeviceOrder]
        orders.insert(1, BY_NAME)  # After the address, which the device orders start from.
        self._listing = SectionsViewController(
            view, workspace, {BrowserSection.LIVE}, compose=self._with_found, summary=_streaming, orders=orders
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
        sign_in = GlobalSettings().scan_sign_in
        if sign_in != self._given_sign_in:
            self._given_sign_in = sign_in
            self._view.set_sign_in(sign_in)

    def scan(self, ip_range: str, *, low_impact: bool = False) -> None:
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
            partial(_run_scan, self._scan, targets, logins, low_impact),
            on_result=self._on_scanned,
            on_error=self._on_scan_failed,
            on_progress=self._on_found,
        )

    def cleanup(self) -> None:
        """Stop showing the outcome of a scan still running."""
        self._found_refresh.stop()
        self._scan_request.close()
        self._clear_access()
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

    def _refresh(self) -> None:
        if self._view is not None:
            self._listing.refresh()

    def _rescan(self, ip_range: str) -> None:
        """Scan again as the user asked, and sign in again to the workspace's cameras, so a login fixed meanwhile is
        noticed."""
        self._clear_access()
        self._check_access(self._store.workspace.items)
        self.scan(ip_range)

    def _invalidate_access(self, item: WorkspaceItem) -> None:
        self._checked.pop(item.id, None)
        check = self._checks.pop(item.id, None)
        if check is not None:
            check.close()
        self._refresh()

    def _clear_access(self) -> None:
        self._checked.clear()
        for check in self._checks.values():
            check.close()
        self._checks.clear()

    def _check_access_of(self, resolutions: list[ItemResolution], _open_first: bool) -> None:
        self._check_access([resolution.item for resolution in resolutions])

    def _check_access(self, items: Sequence[WorkspaceItem]) -> None:
        """Sign in to each of *items* not yet checked, with its own login, when signing in is wanted."""
        if not GlobalSettings().scan_sign_in:
            return
        for item in items:
            request = _access_request(item)
            if request is None or item.id in self._checked:
                continue
            check = self._checks.get(item.id)
            if check is None:
                check = self._checks[item.id] = LatestRequest(self)
            if check.pending:
                continue
            check.submit(
                self._jobs,
                "camera access check",
                partial(_run_check, self._check, *request),
                on_result=partial(self._on_checked, item.id, request),
                on_error=lambda error: logger.debug(f"Camera access check failed: {error}"),
            )

    def _on_checked(self, item_id: str, request: tuple[str, Login], camera: DiscoveredCamera | None) -> None:
        """Keep what a check found, unless the item was removed or replaced with another address or login meanwhile."""
        item = self._store.workspace.item(item_id) if self._store.workspace.has_item(item_id) else None
        if camera is not None and item is not None and _access_request(item) == request:
            self._checked[item_id] = camera
            self._refresh()

    def _add(self, camera: DiscoveredCamera) -> None:
        """Add *camera* without starting its stream; signing in to it has just shown the access works."""
        item = _camera_item(camera)
        self._checked[item.id] = camera
        self._store.add_items([item], open_first=False)

    def _with_found(self, held: Sections) -> Sections:
        """Return *held* with what its cameras' own sign-ins and the scan learned about them, then the cameras found
        that the workspace does not hold."""
        by_host = {camera.host: camera for camera in self._found or ()}
        self._cameras = {}
        listed: list[WorkspaceBrowserSection] = []
        for section in held:
            rows = []
            for row in section.rows:
                camera = _scan_camera(row, by_host, self._checked)
                if camera is not None:
                    self._cameras[row.row_id] = camera
                rows.append(_with_scan(row, camera, self._checked))
            listed.append(replace(section, rows=tuple(rows)))
        if self._found is None:
            return tuple(listed)
        hosts = _workspace_hosts(self._store.workspace.items)
        new = [camera for camera in self._found if camera.host not in hosts]
        self._cameras.update((_network_row_id(camera), camera) for camera in new)
        found = tuple(_network_row(camera, partial(self._add, camera)) for camera in new)
        return (*listed, WorkspaceBrowserSection(BrowserSection.NETWORK, found))

    def _device_order(self, order: DeviceOrder) -> RowOrder:
        """Return *order* as an order of rows, by what the rows listed last show of their camera."""
        return RowOrder(order.label, lambda row: order.key(self._cameras.get(row.row_id)))

    def _forget_view(self) -> None:
        self._view = None
