"""Typed payload shapes shared by the CVAT XML reader and the scene decoder."""

from __future__ import annotations

from typing import Any, TypedDict

CVATDecoderPayload = str


class CVATPointPayload(TypedDict):
    x: float
    y: float


class CVATBaseShapePayload(TypedDict, total=False):
    entity_id: str
    frame_index: int
    label: str
    track_id: int | None
    attributes: dict[str, Any]
    occluded: bool
    keyframe: bool
    z_order: int | None
    group: int | None
    source: str


class CVATBoxPayload(CVATBaseShapePayload):
    type: str
    xtl: float
    ytl: float
    xbr: float
    ybr: float


class CVATPolygonPayload(CVATBaseShapePayload):
    type: str
    points: list[CVATPointPayload]


CVATShapePayload = CVATBoxPayload | CVATPolygonPayload


class CVATFramePayload(TypedDict):
    frame_index: int
    width: int
    height: int
    shapes: list[CVATShapePayload]
