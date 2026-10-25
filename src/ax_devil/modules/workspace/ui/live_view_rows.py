"""The rows the Live view lists for cameras: the workspace's own and those a scan found, with what the scan learned."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, SignIn
from ax_devil.modules.workspace.ui.browser_rows import Facet, OfferedAction, RowAction, WorkspaceBrowserRow

_REFUSED = "The camera refused the login saved with it."
"""What a held camera's row says when its own login does not get in."""
_NOT_SIGNED_IN = "Not signed in: add a login under Try to sign in."
"""What a found camera's row says on hover when it has no Add."""
_SIGN_IN_PROBLEMS = (SignIn.REFUSED, SignIn.FAILED)


def network_row_id(camera: DiscoveredCamera) -> str:
    """Return the id of the row of a camera the scan found."""
    return f"network/{camera.host}"


def _joined(parts: Sequence[str]) -> str:
    """Return *parts* as one detail line that wraps only between them, never inside one."""
    return " · ".join(part.replace(" ", "\u00a0") for part in parts if part)


def network_row(camera: DiscoveredCamera, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a *camera* the scan found, with its address and all non-secret details visible; it has Add
    only when the scan signed in to it."""
    details = _camera_details(camera, show_sign_in_problem=True)
    can_add = camera.sign_in is SignIn.SIGNED_IN
    return WorkspaceBrowserRow(
        row_id=network_row_id(camera),
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


def scan_camera(
    row: WorkspaceBrowserRow, found: Mapping[str, DiscoveredCamera], checked: Mapping[str, DiscoveredCamera]
) -> DiscoveredCamera | None:
    """Return what *checked*, the row's own sign-in, or else a scan in *found* learned about the camera of a row the
    workspace holds, or None when nothing did."""
    item = row.item
    if item is None:
        return None
    return checked.get(item.id) or next((found[host] for host in item.hosts if host in found), None)


def with_scan(
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
            stream=None,
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
