"""The rows the Live view lists: a camera row per camera the workspace streams, with a row per stream under it, and a
row per camera a scan found, with what the scan learned."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial

from ax_devil.core.device_connection import DeviceConnection
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, SignIn
from ax_devil.modules.settings.config_manager import expand_environment_reference
from ax_devil.modules.thumbnails import SNAPSHOT_TIMEOUT_S, CameraThumbnail
from ax_devil.modules.workspace.core import LiveOverlayMode, LiveStreamItem
from ax_devil.modules.workspace.ui.browser_rows import Facet, OfferedAction, RowAction, WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamState, LiveStreamStatus

_REFUSED = "The camera refused the login saved with it."
"""What a stream's row says when its own login does not get in."""
_NOT_SIGNED_IN = "Not signed in: add a login under Try to sign in."
"""What a found camera's row says on hover when it has no Add."""
_PROBLEMS = (LiveStreamState.FAILED, LiveStreamState.RECONNECTING, LiveStreamState.STALLED)
"""The stream states whose text a stream's row shows under its name."""
_TAGS = {LiveOverlayMode.RTSP: "RTSP", LiveOverlayMode.MQTT: "MQTT", LiveOverlayMode.WEBSOCKET: "WS"}
"""The chip a stream's row shows for how its overlay arrives; a stream without an overlay has none."""


def network_row_id(camera: DiscoveredCamera) -> str:
    """Return the id of the row of a camera the scan found."""
    return f"network/{camera.host}"


@dataclass(frozen=True, slots=True)
class CameraGroup:
    """The streams the workspace holds from one camera, as rows, and what a scan or a sign-in learned about it.

    Streams share a camera when they connect to the same host, whatever the case, and device port.
    """

    host: str
    port: int
    streams: tuple[WorkspaceBrowserRow, ...]
    items: tuple[LiveStreamItem, ...]
    status: LiveStreamStatus
    """The most urgent state of the streams; one that is refused or could not resolve has failed."""
    scan: DiscoveredCamera | None

    @property
    def row_id(self) -> str:
        """Return the id of the camera's row."""
        return f"camera/{self.host}:{self.port}"


def network_row(camera: DiscoveredCamera, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a *camera* the scan found: its model, or else whether the scan signed in, beside the address;
    it has Add only when the scan signed in to it."""
    can_add = camera.sign_in is SignIn.SIGNED_IN
    login = camera.login
    thumbnail = (
        CameraThumbnail(
            network_row_id(camera),
            DeviceConnection(
                camera.host,
                camera.protocol,
                username=expand_environment_reference(login.username),
                password=expand_environment_reference(login.password),
                timeout_s=SNAPSHOT_TIMEOUT_S,
            ),
            camera_head=1,
        )
        if can_add and login is not None
        else None
    )
    return WorkspaceBrowserRow(
        row_id=network_row_id(camera),
        label=camera.host,
        icon_kind="live_video",
        aside=camera.model or camera.sign_in.label,
        info=_camera_info(camera.host, camera),
        facts=("Camera on the network", *_camera_details(camera, show_sign_in_problem=True)),
        warning=None if can_add else _NOT_SIGNED_IN,
        actions=(OfferedAction(RowAction.ADD),) if can_add else (),
        add=add if can_add else None,
        idle=not can_add,
        search_terms=_camera_search_terms(camera.host, camera),
        facets=_facets(camera),
        thumbnail=thumbnail,
    )


def group_cameras(
    rows: Sequence[WorkspaceBrowserRow], found: Mapping[str, DiscoveredCamera], checked: Mapping[str, DiscoveredCamera]
) -> tuple[CameraGroup, ...]:
    """Return the stream *rows* the workspace holds grouped by camera, in the order their cameras first appear.

    Each stream row is shown as a stream of its camera. *checked*, by item id, is what signing in with an item's own
    login found, and *found*, by host, what a scan did; a camera takes the first of these that learned something.
    """
    members: dict[tuple[str, int], list[tuple[WorkspaceBrowserRow, LiveStreamItem]]] = {}
    for row in rows:
        item = row.item
        assert isinstance(item, LiveStreamItem)  # The Live section holds live streams only.
        members.setdefault((item.expanded().host.casefold(), item.device_port), []).append((row, item))
    return tuple(_group(host, port, streams, found, checked) for (host, port), streams in members.items())


def _group(
    host: str,
    port: int,
    members: Sequence[tuple[WorkspaceBrowserRow, LiveStreamItem]],
    found: Mapping[str, DiscoveredCamera],
    checked: Mapping[str, DiscoveredCamera],
) -> CameraGroup:
    """Return the camera at *host* and *port* streamed by *members*, the rows of its streams with their items."""
    statuses = []
    learned = []
    for row, item in members:
        own = checked.get(item.id)
        failure = _failure(row, own)
        statuses.append(LiveStreamStatus(LiveStreamState.FAILED, failure) if failure else row.stream)
        learned.append(own or next((found[name] for name in item.hosts if name in found), None))
    return CameraGroup(
        host=host,
        port=port,
        streams=tuple(_stream_row(row, item, checked.get(item.id)) for row, item in members),
        items=tuple(item for _row, item in members),
        status=LiveStreamStatus.of_viewers(status for status in statuses if status is not None),
        scan=next((camera for camera in learned if camera is not None), None),
    )


def camera_row(group: CameraGroup, add_stream: Callable[[LiveStreamItem | None], None]) -> WorkspaceBrowserRow:
    """Return the row of the camera *group* stands for, with its streams under it.

    The row is marked by its worst stream and says the camera's model, or else how its streams are doing; its only
    action adds another stream, which *add_stream* is asked to do from the camera's first stream.
    """
    scan = group.scan
    status = group.status
    label = next((item.label for item in group.items if item.label), group.items[0].default_name)
    return WorkspaceBrowserRow(
        row_id=group.row_id,
        label=label,
        icon_kind="live_video",
        children=group.streams,
        aside=scan.model if scan is not None and scan.model else status.text or "Stopped",
        info=_camera_info(group.items[0].default_name, scan),
        facts=("Camera", *(_camera_details(scan, show_sign_in_problem=False) if scan is not None else ())),
        actions=(OfferedAction(RowAction.ADD_STREAM),),
        add=partial(add_stream, group.items[0]),
        idle=status.state is LiveStreamState.IDLE and not any(stream.is_open for stream in group.streams),
        stream=status,
        search_terms=_camera_search_terms(group.items[0].default_name, scan),
        facets=_facets(scan) if scan is not None else frozenset(),
        thumbnail=next((stream.thumbnail for stream in group.streams if stream.thumbnail is not None), None),
        expanded_at_first=True,
    )


def _failure(row: WorkspaceBrowserRow, own: DiscoveredCamera | None) -> str:
    """Return why the stream of *row* can not run, or nothing when it can: its camera refuses the login saved with it,
    or it could not resolve."""
    return _REFUSED if _refuses(own) else row.unavailable_reason or ""


def _refuses(own: DiscoveredCamera | None) -> bool:
    """Return whether *own*, what signing in with an item's own login found, shows the camera refused the login."""
    return own is not None and own.sign_in is SignIn.REFUSED


def refused_streams(checked: Mapping[str, DiscoveredCamera]) -> frozenset[str]:
    """Return the ids of the items whose camera refused the login saved with them, from *checked*, what signing in
    with each item's own login found."""
    return frozenset(item_id for item_id, camera in checked.items() if _refuses(camera))


def _stream_row(row: WorkspaceBrowserRow, item: LiveStreamItem, own: DiscoveredCamera | None) -> WorkspaceBrowserRow:
    """Return *row*, the row of a stream of *item*, as a stream under its camera: how its overlay arrives, what it
    draws, a problem under it, and a glyph for whether it has an overlay; a stream whose login the camera refuses can
    not start."""
    status = row.stream
    tag = _TAGS.get(item.overlay_mode, "")
    problem = _failure(row, own) or (status.text if status is not None and status.state in _PROBLEMS else "")
    row = replace(
        row,
        label=_stream_label(item),
        tag=tag,
        glyph=Icon.OVERLAY if tag else Icon.VIDEO_ONLY,
        detail=problem,
        detail_is_warning=bool(problem),
        info=_stream_info(item, row.location),
        children=(),
    )
    if not _refuses(own):
        return row
    return replace(
        row,
        icon_kind="unavailable",
        stream=None,
        idle=False,
        warning=_REFUSED,
        actions=tuple(offered for offered in row.actions if offered.action is not RowAction.START_STREAM),
    )


def _stream_label(item: LiveStreamItem) -> str:
    """Return what a stream's row calls it: the topic or data source its overlay draws, else the decoder, else that it
    has no overlay."""
    mode = item.overlay_mode
    if not mode.requires_handler:
        return "Video only"
    source = {LiveOverlayMode.WEBSOCKET: item.websocket_topic, LiveOverlayMode.MQTT: item.analytics_data_source_key}
    return source.get(mode) or item.handler_type or "Video only"


def _stream_info(item: LiveStreamItem, location: str | None) -> tuple[str, ...]:
    """Return the lines a stream's row opens: the MQTT broker, else where the stream comes from."""
    broker = item.expanded().mqtt_host if item.overlay_mode is LiveOverlayMode.MQTT else ""
    text = f"{broker}:{item.mqtt_port}" if broker else location
    return (text,) if text else ()


def _camera_info(host: str, camera: DiscoveredCamera | None) -> tuple[str, ...]:
    """Return the two lines a camera's row opens: where it is and what it is, then its serial number and uptime; a scan
    that learned nothing leaves the address alone."""
    if camera is None:
        return (host,)
    what = (host, camera.product_type, f"Firmware {camera.firmware}" if camera.firmware else "")
    since = (f"Serial {camera.serial}" if camera.serial else "", f"Up {camera.uptime}" if camera.uptime else "")
    return tuple(line for line in (_dotted(what), _dotted(since)) if line)


def _dotted(parts: Sequence[str]) -> str:
    return " · ".join(part for part in parts if part)


def _camera_details(camera: DiscoveredCamera, *, show_sign_in_problem: bool) -> tuple[str, ...]:
    """Return the non-secret details the hover text lists for what a scan learned about a camera; with
    *show_sign_in_problem*, a sign-in that did not work is mentioned."""
    facts = (
        camera.model,
        camera.sign_in.label if show_sign_in_problem and camera.sign_in in (SignIn.REFUSED, SignIn.FAILED) else "",
        f"Serial {camera.serial}" if camera.serial else "",
        f"Firmware {camera.firmware}" if camera.firmware else "",
        f"Type {camera.product_type}" if camera.product_type else "",
        f"Up {camera.uptime}" if camera.uptime else "",
        f"Architecture {camera.architecture}" if camera.architecture else "",
        f"SoC {camera.soc}" if camera.soc else "",
    )
    return tuple(fact for fact in facts if fact)


def _camera_search_terms(host: str, camera: DiscoveredCamera | None) -> str:
    """Return what the search finds a camera's row by: its address, and what a scan learned about it."""
    if camera is None:
        return host
    platform = _dotted((camera.architecture, camera.soc))
    return _dotted((host, camera.model, camera.serial, camera.firmware, camera.product_type, platform))


def _facets(camera: DiscoveredCamera) -> frozenset[Facet]:
    return frozenset(Facet(group, value) for group, value in camera.facets)
