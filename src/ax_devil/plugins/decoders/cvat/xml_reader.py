"""Read CVAT XML annotation files into serialised per-frame payloads."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ax_devil.modules.settings.logging_config import get_logger

from .payloads import (
    CVATBoxPayload,
    CVATDecoderPayload,
    CVATFramePayload,
    CVATPointPayload,
    CVATPolygonPayload,
    CVATShapePayload,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CVATFileMetadata:
    """Summary metadata for a CVAT annotation file."""

    width: int
    height: int
    task_name: str
    start_frame: int
    stop_frame: int
    size: int
    labels: tuple[str, ...]
    all_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CVATParseResult:
    """Result from parsing a CVAT XML document."""

    payloads: list[CVATDecoderPayload]
    metadata: CVATFileMetadata


__all__ = [
    "CVATFileMetadata",
    "CVATParseResult",
    "iter_cvat_frames",
    "parse_cvat_document",
    "serialize_cvat_frame",
]


def parse_cvat_document(xml_source: str | Path) -> CVATParseResult:
    """Parse a CVAT XML document into serialised frame payloads and metadata."""
    xml_path = Path(xml_source)
    tree = ET.parse(xml_path)
    root = tree.getroot()

    meta = root.find("meta")
    if meta is None:
        raise ValueError("No 'meta' tag found in CVAT XML file")

    all_metadata = _extract_all_metadata(meta)
    width, height, task_name, start_frame, stop_frame, size = _extract_essential_fields(all_metadata)

    document = ET.Element(root.tag, root.attrib)
    document.extend(child for child in root if child.tag not in {"meta", "track", "image"})
    all_metadata["@document"] = _extract_all_metadata(document)
    frames, labels, frame_debug = _collect_frame_shapes(root)

    if size <= 0 and frames:
        highest_frame = max(frames)
        size = highest_frame + 1
        stop_frame = highest_frame

    labels_tuple = tuple(sorted(labels))

    payloads = []
    for frame_index, shapes in sorted(frames.items()):
        debug = frame_debug[frame_index]
        image_attributes = debug.get("image", {}).get("@attributes", {})
        payloads.append(
            serialize_cvat_frame(
                frame_index,
                shapes,
                _safe_int(image_attributes.get("width")) or width,
                _safe_int(image_attributes.get("height")) or height,
                debug={"cvat": debug},
            )
        )

    metadata = CVATFileMetadata(
        width=width,
        height=height,
        task_name=task_name,
        start_frame=start_frame,
        stop_frame=stop_frame,
        size=size,
        labels=labels_tuple,
        all_metadata=all_metadata,
    )

    return CVATParseResult(payloads=payloads, metadata=metadata)


def iter_cvat_frames(xml_source: str | Path) -> Iterator[tuple[int, CVATFramePayload]]:
    """Yield frame payloads directly from a CVAT XML document."""
    parse_result = parse_cvat_document(xml_source)
    for frame_payload in parse_result.payloads:
        data = json.loads(frame_payload)
        if not isinstance(data, dict):
            logger.debug(f"Skipping malformed CVAT payload during iteration: {data}")
            continue
        frame_data = cast(CVATFramePayload, data)
        yield int(frame_data["frame_index"]), frame_data


def serialize_cvat_frame(
    frame_index: int,
    shapes: Sequence[CVATShapePayload],
    width: int,
    height: int,
    *,
    debug: dict[str, Any] | None = None,
) -> CVATDecoderPayload:
    """Serialise a frame payload into a JSON string."""
    frame_payload: CVATFramePayload = {
        "frame_index": int(frame_index),
        "width": int(width),
        "height": int(height),
        "shapes": list(shapes),
        "debug": debug or {},
    }
    return json.dumps(frame_payload)


# ---------------------------------------------------------------------------#
# XML helpers                                                                #
# ---------------------------------------------------------------------------#


def _collect_frame_shapes(
    root: ET.Element,
) -> tuple[dict[int, list[CVATShapePayload]], set[str], dict[int, dict[str, Any]]]:
    """Collect CVAT shapes grouped by frame index."""
    frame_shapes: dict[int, list[CVATShapePayload]] = {}
    labels: set[str] = set()
    frame_debug: dict[int, dict[str, Any]] = {}
    shape_counter = 0

    for container in root:
        if container.tag not in {"track", "image"}:
            continue
        track_id = _safe_int(container.get("id")) if container.tag == "track" else None
        image_frame = _safe_int(container.get("id", container.get("frame"))) if container.tag == "image" else None
        context = ET.Element(container.tag, container.attrib)
        context.extend(child for child in container if child.tag == "attribute" or child.get("frame") is None)
        if container.tag == "image":
            context[:] = [child for child in container if child.tag == "attribute"]
        context_data = _extract_all_metadata(context)
        if image_frame is not None:
            frame_shapes.setdefault(image_frame, [])
            frame_debug.setdefault(image_frame, {})["image"] = context_data
        for element in container:
            if element.tag == "attribute":
                continue
            frame_index = _safe_int(element.get("frame")) if element.get("frame") is not None else image_frame
            if frame_index is None:
                continue
            shapes = frame_shapes.setdefault(frame_index, [])
            debug = frame_debug.setdefault(frame_index, {})
            if container.tag == "image":
                debug["image"] = context_data
            raw_shape = {
                container.tag: context_data,
                "shape_type": element.tag,
                "shape": _extract_all_metadata(element),
            }
            label = element.get("label", container.get("label", "")).strip().lower()
            builder = {"box": _build_box_payload, "polygon": _build_polygon_payload}.get(element.tag)
            if element.get("outside") == "1" or not label or builder is None:
                debug.setdefault("unrendered_shapes", []).append(raw_shape)
                continue
            attributes = _extract_attribute_children(container) | _extract_attribute_children(element)
            shape_payload = builder(
                element.attrib,
                entity_id=f"cvat_track_{track_id}" if track_id is not None else f"cvat_shape_{shape_counter}",
                frame_index=frame_index,
                label=label,
                track_id=track_id,
                attributes=attributes,
                occluded=element.get("occluded") == "1",
                keyframe=element.get("keyframe") == "1",
                z_order=_safe_int(element.get("z_order")),
                group=_safe_int(element.get("group_id", container.get("group_id"))),
            )
            if track_id is None:
                shape_counter += 1
            if shape_payload is None:
                debug.setdefault("unrendered_shapes", []).append(raw_shape)
                continue
            shape_payload["debug"] = {"cvat": raw_shape}
            shapes.append(shape_payload)
            labels.add(label)

    return frame_shapes, labels, frame_debug


def _extract_attribute_children(element: ET.Element) -> dict[str, Any]:
    """Extract <attribute> children into a dictionary."""
    attributes: dict[str, Any] = {}
    for child in list(element):
        if child.tag != "attribute":
            continue
        name = child.attrib.get("name")
        if not name:
            continue
        value = (child.text or "").strip()
        attributes[name] = value
    return attributes


def _build_box_payload(
    attrib: dict[str, str],
    *,
    entity_id: str,
    frame_index: int,
    label: str,
    track_id: int | None,
    attributes: dict[str, Any],
    occluded: bool,
    keyframe: bool,
    z_order: int | None,
    group: int | None,
) -> CVATBoxPayload | None:
    try:
        xtl = float(attrib["xtl"])
        ytl = float(attrib["ytl"])
        xbr = float(attrib["xbr"])
        ybr = float(attrib["ybr"])
    except (KeyError, TypeError, ValueError):
        logger.debug(f"Skipping CVAT box with missing coordinates: {attrib}")
        return None

    if math.isclose(xbr, xtl) or math.isclose(ybr, ytl):
        logger.debug(f"Skipping CVAT box with zero area: {attrib}")
        return None

    payload: CVATBoxPayload = {
        "type": "box",
        "entity_id": entity_id,
        "frame_index": frame_index,
        "label": label,
        "track_id": track_id,
        "xtl": xtl,
        "ytl": ytl,
        "xbr": xbr,
        "ybr": ybr,
        "attributes": attributes,
        "occluded": occluded,
        "keyframe": keyframe,
        "z_order": z_order,
        "group": group,
        "source": "track" if track_id is not None else "image",
    }
    return payload


def _build_polygon_payload(
    attrib: dict[str, str],
    *,
    entity_id: str,
    frame_index: int,
    label: str,
    track_id: int | None,
    attributes: dict[str, Any],
    occluded: bool,
    keyframe: bool,
    z_order: int | None,
    group: int | None,
) -> CVATPolygonPayload | None:
    points_attr = attrib.get("points")
    if not points_attr:
        logger.debug("Skipping CVAT polygon without points attribute.")
        return None

    points: list[CVATPointPayload] = []
    for raw_point in points_attr.split(";"):
        pair = raw_point.strip()
        if not pair:
            continue
        try:
            x_str, y_str = pair.split(",", 1)
            points.append({"x": float(x_str), "y": float(y_str)})
        except ValueError:
            logger.debug(f"Skipping malformed CVAT polygon point: {pair}")
            continue

    if len(points) < 3:
        logger.debug(f"Skipping CVAT polygon with insufficient points: {points_attr}")
        return None

    payload: CVATPolygonPayload = {
        "type": "polygon",
        "entity_id": entity_id,
        "frame_index": frame_index,
        "label": label,
        "track_id": track_id,
        "points": points,
        "attributes": attributes,
        "occluded": occluded,
        "keyframe": keyframe,
        "z_order": z_order,
        "group": group,
        "source": "track" if track_id is not None else "image",
    }
    return payload


def _extract_all_metadata(meta: ET.Element) -> dict[str, Any]:
    """Extract all metadata from the meta tag recursively as a nested dict."""

    def extract_element_data(element: ET.Element) -> dict[str, Any] | Any:
        data: dict[str, Any] = {}

        if element.attrib:
            data["@attributes"] = dict(element.attrib)

        children: dict[str, Any] = {}
        for child in list(element):
            child_data = extract_element_data(child)
            tag_name = child.tag
            if tag_name in children:
                existing = children[tag_name]
                if not isinstance(existing, list):
                    children[tag_name] = [existing]
                children[tag_name].append(child_data)
            else:
                children[tag_name] = child_data

        if children:
            data.update(children)

        text = (element.text or "").strip()
        if not data and text:
            return text
        if text:
            data["#text"] = text

        return data

    result = extract_element_data(meta)
    return result if isinstance(result, dict) else {"text": result}


def _extract_essential_fields(all_metadata: dict[str, Any]) -> tuple[int, int, str, int, int, int]:
    """Extract essential fields from complete metadata."""
    task_data = all_metadata.get("task", {})
    original_size = task_data.get("original_size", {})
    segments = task_data.get("segments", {}).get("segment", [])

    if not isinstance(segments, list):
        segments = [segments]

    width = int(original_size.get("width", 0)) if isinstance(original_size, dict) else 0
    height = int(original_size.get("height", 0)) if isinstance(original_size, dict) else 0
    task_name = task_data.get("name", "") if isinstance(task_data, dict) else str(task_data)

    first_segment = segments[0] if segments else {}
    if isinstance(first_segment, dict):
        start_frame = int(first_segment.get("start", 0))
        stop_frame = int(first_segment.get("stop", 0))
    else:
        start_frame = 0
        stop_frame = 0

    size = int(task_data.get("size", 0)) if isinstance(task_data, dict) else 0
    if size == 0:
        size = stop_frame - start_frame + 1 if stop_frame >= start_frame else 0

    return width, height, task_name, start_frame, stop_frame, size


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
