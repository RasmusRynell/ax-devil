"""Decoder and helpers for CVAT XML annotations."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, TypedDict, cast

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
    Score,
    TimeSlice,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

CVATDecoderPayload = str


class CVATPointPayload(TypedDict):
    x: float
    y: float


class CVATBaseShapePayload(TypedDict, total=False):
    entity_id: str
    frame_index: int
    label: str
    track_id: Optional[int]
    attributes: Dict[str, Any]
    occluded: bool
    keyframe: bool
    z_order: Optional[int]
    group: Optional[int]
    source: str


class CVATBoxPayload(CVATBaseShapePayload):
    type: str
    xtl: float
    ytl: float
    xbr: float
    ybr: float


class CVATPolygonPayload(CVATBaseShapePayload):
    type: str
    points: List[CVATPointPayload]


CVATShapePayload = CVATBoxPayload | CVATPolygonPayload


class CVATFramePayload(TypedDict):
    frame_index: int
    width: int
    height: int
    shapes: List[CVATShapePayload]


@dataclass(frozen=True, slots=True)
class CVATFileMetadata:
    """Summary metadata for a CVAT annotation file."""

    width: int
    height: int
    task_name: str
    start_frame: int
    stop_frame: int
    size: int
    labels: Tuple[str, ...]
    all_metadata: Dict[str, Any]


@dataclass(frozen=True, slots=True)
class CVATParseResult:
    """Result from parsing a CVAT XML document."""

    payloads: List[CVATDecoderPayload]
    metadata: CVATFileMetadata


__all__ = [
    "CVATDecoderPayload",
    "CVATFileMetadata",
    "CVATFramePayload",
    "CVATParseResult",
    "CVATShapePayload",
    "decode_cvat_frame",
    "iter_cvat_frames",
    "parse_cvat_document",
    "serialize_cvat_frame",
    "build_cvat_filter_config",
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

    frames, labels = _collect_frame_shapes(xml_path)

    if size <= 0 and frames:
        highest_frame = max(frames)
        size = highest_frame + 1
        stop_frame = highest_frame

    labels_tuple = tuple(sorted(labels))

    payloads = [
        serialize_cvat_frame(frame_index, shapes, width, height) for frame_index, shapes in sorted(frames.items())
    ]

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


def iter_cvat_frames(xml_source: str | Path) -> Iterator[Tuple[int, CVATFramePayload]]:
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
) -> CVATDecoderPayload:
    """Serialise a frame payload into a JSON string."""
    frame_payload: CVATFramePayload = {
        "frame_index": int(frame_index),
        "width": int(width),
        "height": int(height),
        "shapes": list(shapes),
    }
    return json.dumps(frame_payload)


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

    entities: Dict[EntityId, Entity] = {}

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

        geometry = _decode_geometry(shape_payload, width=width, height=height)
        if geometry is None:
            logger.debug(f"Skipping CVAT shape with unsupported geometry: {shape_payload}")
            continue

        observation = Observation(
            frame_number=frame_index,
            geometry=geometry,
            classification=[Classification(label.lower(), Score(1.0), attributes)],
            confidence=Score(1.0),
        )

        entity = entities.get(entity_id)
        if entity is None:
            entity = Entity(id=entity_id, observations=[])
            entities[entity_id] = entity
        entity.add_observation(observation)

    return Scene(time_slice=TimeSlice(frame_index, frame_index), entities=entities)


def build_cvat_filter_config(labels: Iterable[str]) -> FilterConfig:
    """Construct a FilterConfig for CVAT annotations from the supplied labels."""
    unique_labels = sorted({label for label in labels if label})
    options: List[FilterOption] = []

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
            make_classification_predicate((), include_empty=True, include_unknown_prefix=False).build_option(
                id="show_unlabeled",
                label="Unlabeled",
                default_enabled=True,
                sort_key=0,
            )
        )

    return FilterConfig(options=tuple(options), name="cvat")


# ---------------------------------------------------------------------------#
# XML helpers                                                                #
# ---------------------------------------------------------------------------#


def _collect_frame_shapes(xml_path: Path) -> Tuple[Dict[int, List[CVATShapePayload]], set[str]]:
    """Collect CVAT shapes grouped by frame index."""
    frame_shapes: Dict[int, List[CVATShapePayload]] = {}
    labels: set[str] = set()
    current_track_id: Optional[int] = None
    current_track_label: Optional[str] = None
    shape_counter = 0
    current_image_frame: Optional[int] = None

    for event, element in ET.iterparse(xml_path, events=("start", "end")):
        tag = element.tag

        if event == "start":
            if tag == "track":
                try:
                    current_track_id = int(element.attrib.get("id", -1))
                except (TypeError, ValueError):
                    current_track_id = None
                current_track_label = element.attrib.get("label")
            elif tag == "image":
                current_track_id = None
                current_track_label = None
                current_image_frame = _safe_int(element.attrib.get("id") or element.attrib.get("frame"))
        elif event == "end":
            if tag in {"box", "polygon"}:
                frame_str = element.attrib.get("frame")
                frame_candidate: Optional[int]
                if frame_str is None and current_image_frame is not None:
                    frame_candidate = current_image_frame
                else:
                    frame_candidate = _safe_int(frame_str)
                if frame_candidate is None:
                    logger.debug(f"Skipping CVAT shape without frame attribute: {element.attrib}")
                    element.clear()
                    continue
                frame_index = frame_candidate

                outside = element.attrib.get("outside") == "1"
                if outside:
                    element.clear()
                    continue

                label = element.attrib.get("label", current_track_label or "")
                label = (label or "").strip()
                if not label:
                    logger.debug("Skipping CVAT shape without label.")
                    element.clear()
                    continue

                labels.add(label.lower())

                track_id_value: Optional[int]
                if current_track_id is not None:
                    track_id_value = current_track_id
                    entity_id = f"cvat_track_{current_track_id}"
                else:
                    track_id_value = None
                    entity_id = f"cvat_shape_{shape_counter}"
                    shape_counter += 1

                attributes_payload = _extract_attribute_children(element)
                occluded = element.attrib.get("occluded") == "1"
                keyframe = element.attrib.get("keyframe") == "1"
                z_order = _safe_int(element.attrib.get("z_order"))
                group = _safe_int(element.attrib.get("group_id"))

                shape_payload: Optional[CVATShapePayload]
                if tag == "box":
                    shape_payload = _build_box_payload(
                        element.attrib,
                        entity_id=entity_id,
                        frame_index=frame_index,
                        label=label.lower(),
                        track_id=track_id_value,
                        attributes=attributes_payload,
                        occluded=occluded,
                        keyframe=keyframe,
                        z_order=z_order,
                        group=group,
                    )
                elif tag == "polygon":
                    shape_payload = _build_polygon_payload(
                        element.attrib,
                        entity_id=entity_id,
                        frame_index=frame_index,
                        label=label.lower(),
                        track_id=track_id_value,
                        attributes=attributes_payload,
                        occluded=occluded,
                        keyframe=keyframe,
                        z_order=z_order,
                        group=group,
                    )
                else:
                    shape_payload = None

                if shape_payload is None:
                    element.clear()
                    continue

                frame_shapes.setdefault(frame_index, []).append(shape_payload)

                element.clear()
            elif tag == "track":
                current_track_id = None
                current_track_label = None
                element.clear()
            elif tag == "image":
                current_image_frame = None
                element.clear()
            elif tag == "attribute":
                continue
            else:
                element.clear()

    return frame_shapes, labels


def _extract_attribute_children(element: ET.Element) -> Dict[str, Any]:
    """Extract <attribute> children into a dictionary."""
    attributes: Dict[str, Any] = {}
    for child in list(element):
        if child.tag != "attribute":
            continue
        name = child.attrib.get("name")
        if not name:
            continue
        value = (child.text or "").strip()
        attributes[name] = value
        child.clear()
    return attributes


def _build_box_payload(
    attrib: Dict[str, str],
    *,
    entity_id: str,
    frame_index: int,
    label: str,
    track_id: Optional[int],
    attributes: Dict[str, Any],
    occluded: bool,
    keyframe: bool,
    z_order: Optional[int],
    group: Optional[int],
) -> Optional[CVATBoxPayload]:
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
    attrib: Dict[str, str],
    *,
    entity_id: str,
    frame_index: int,
    label: str,
    track_id: Optional[int],
    attributes: Dict[str, Any],
    occluded: bool,
    keyframe: bool,
    z_order: Optional[int],
    group: Optional[int],
) -> Optional[CVATPolygonPayload]:
    points_attr = attrib.get("points")
    if not points_attr:
        logger.debug("Skipping CVAT polygon without points attribute.")
        return None

    points: List[CVATPointPayload] = []
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
        points: List[NormalizedPoint] = []
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


def _extract_all_metadata(meta: ET.Element) -> Dict[str, Any]:
    """Extract all metadata from the meta tag recursively as a nested dict."""

    def extract_element_data(element: ET.Element) -> Dict[str, Any] | Any:
        data: Dict[str, Any] = {}

        if element.attrib:
            data["@attributes"] = dict(element.attrib)

        children: Dict[str, Any] = {}
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

        return data

    result = extract_element_data(meta)
    return result if isinstance(result, dict) else {"text": result}


def _extract_essential_fields(all_metadata: Dict[str, Any]) -> Tuple[int, int, str, int, int, int]:
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


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_label(label: str) -> str:
    cleaned = label.replace("_", " ").strip()
    return cleaned.title() if cleaned else "Unlabeled"


def _option_id(label: str) -> str:
    cleaned = label.replace(" ", "_").replace("/", "_").strip("_")
    return f"show_{cleaned or 'unlabeled'}"
