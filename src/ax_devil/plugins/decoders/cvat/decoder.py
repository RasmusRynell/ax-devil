"""Decode serialised CVAT frame payloads into scenes and build their filter config."""

from __future__ import annotations

import json
from collections.abc import Iterable, MutableMapping
from typing import Any, cast

from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption
from ax_devil.modules.filtering.predicate_utils import make_classification_predicate
from ax_devil.modules.scene.model import (
    Attribute,
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    NormalizedPoint,
    Observation,
    Polygon,
    Scene,
    TimeSlice,
)
from ax_devil.modules.settings.logging_config import get_logger

from .payloads import CVATDecoderPayload, CVATFramePayload

logger = get_logger(__name__)

__all__ = ["build_cvat_filter_config", "decode_cvat_frame"]


def decode_cvat_frame(payload: CVATDecoderPayload) -> Scene:
    """Decode a serialised CVAT frame payload into a :class:`Scene`."""
    try:
        frame_payload: CVATFramePayload = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid CVAT payload: {exc}") from exc

    if not isinstance(frame_payload, dict):
        raise ValueError("Expected CVAT payload to be a JSON object.")

    try:
        frame_index = int(frame_payload["frame_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("CVAT payload missing 'frame_index'.") from exc

    width = max(int(frame_payload.get("width", 0)), 1)
    height = max(int(frame_payload.get("height", 0)), 1)

    raw_shapes = frame_payload.get("shapes", [])
    if not isinstance(raw_shapes, list):
        raise ValueError("CVAT payload 'shapes' must be a list.")

    entities: dict[EntityId, Entity] = {}

    for raw_shape in raw_shapes:
        if not isinstance(raw_shape, MutableMapping):
            logger.debug(f"Skipping malformed CVAT shape payload: {raw_shape}")
            continue

        shape_payload = cast(MutableMapping[str, Any], raw_shape)
        label = str(shape_payload.get("label", "")).strip()
        if not label:
            logger.debug(f"Skipping CVAT shape without label: {shape_payload}")
            continue

        entity_id_value = str(shape_payload.get("entity_id") or f"cvat_shape_{frame_index}")
        entity_id = EntityId(entity_id_value)

        attributes_dict = shape_payload.get("attributes", {})
        if not isinstance(attributes_dict, dict):
            attributes_dict = {}
        attributes = [Attribute(name=str(key), value=value) for key, value in attributes_dict.items()]

        if bool(shape_payload.get("occluded")):
            attributes.append(Attribute(name="occluded", value=True))
        if bool(shape_payload.get("keyframe")):
            attributes.append(Attribute(name="keyframe", value=True))

        z_order_value = shape_payload.get("z_order")
        if z_order_value is not None:
            try:
                attributes.append(Attribute(name="z_order", value=int(z_order_value)))
            except (TypeError, ValueError):
                logger.debug(f"Skipping z_order attribute conversion for shape: {shape_payload}")

        group = shape_payload.get("group")
        if group is not None:
            attributes.append(Attribute(name="group_id", value=group))

        geometry = _decode_geometry(shape_payload, width=width, height=height)
        if geometry is None:
            logger.debug(f"Skipping CVAT shape with unsupported geometry: {shape_payload}")
            continue

        observation = Observation(
            frame_number=frame_index,
            geometry=geometry,
            classification=[Classification(label.lower(), attributes=attributes)],
            debug=shape_payload.get("debug", {}),
        )

        entity = entities.get(entity_id)
        if entity is None:
            entity = Entity(id=entity_id, observations=[])
            entities[entity_id] = entity
        entity.add_observation(observation)

    return Scene(
        time_slice=TimeSlice(frame_index, frame_index), entities=entities, debug=frame_payload.get("debug", {})
    )


def build_cvat_filter_config(labels: Iterable[str]) -> FilterConfig:
    """Construct a FilterConfig for CVAT annotations from the supplied labels."""
    unique_labels = sorted({label for label in labels if label})
    options: list[FilterOption] = []

    for index, label in enumerate(unique_labels):
        options.append(
            make_classification_predicate((label,)).build_option(
                id=_option_id(label),
                label=_normalise_label(label),
                default_enabled=True,
                sort_key=index,
            )
        )

    if not options:
        options.append(
            make_classification_predicate((), include_empty=True).build_option(
                id="show_unlabeled",
                label="Unlabeled",
                default_enabled=True,
                sort_key=0,
            )
        )

    return FilterConfig(options=tuple(options), name="cvat")


def _decode_geometry(
    raw_shape: MutableMapping[str, Any],
    *,
    width: int,
    height: int,
) -> BoundingBox | Polygon | None:
    """Decode geometry from a shape payload."""
    shape_type = raw_shape.get("type")
    if shape_type == "box":
        try:
            xtl = float(raw_shape["xtl"])
            ytl = float(raw_shape["ytl"])
            xbr = float(raw_shape["xbr"])
            ybr = float(raw_shape["ybr"])
        except (KeyError, TypeError, ValueError):
            return None

        box_width = max(xbr - xtl, 0.0)
        box_height = max(ybr - ytl, 0.0)

        if box_width == 0 or box_height == 0:
            return None

        return BoundingBox.from_xywh(
            xtl / width,
            ytl / height,
            box_width / width,
            box_height / height,
            allow_outside=True,
        )

    if shape_type == "polygon":
        points_payload = raw_shape.get("points", [])
        if not isinstance(points_payload, list):
            return None
        points: list[NormalizedPoint] = []
        for raw_point in points_payload:
            if not isinstance(raw_point, MutableMapping):
                continue
            try:
                x = float(raw_point["x"])
                y = float(raw_point["y"])
            except (KeyError, TypeError, ValueError):
                continue
            points.append(NormalizedPoint(x=x / width, y=y / height, allow_outside=True))

        if len(points) < 3:
            return None
        return Polygon(points=points)

    return None


def _normalise_label(label: str) -> str:
    cleaned = label.replace("_", " ").strip()
    return cleaned.title() if cleaned else "Unlabeled"


def _option_id(label: str) -> str:
    cleaned = label.replace(" ", "_").replace("/", "_").strip("_")
    return f"show_{cleaned or 'unlabeled'}"
