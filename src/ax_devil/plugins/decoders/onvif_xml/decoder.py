from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set

from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption
from ax_devil.modules.filtering.predicate_utils import ClassFilterSpec, make_other_classification_predicate
from ax_devil.modules.scene.decoding import parse_timestamp
from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ClassificationType,
    ColorClassification,
    Delete,
    Entity,
    EntityId,
    Geometry,
    KnownClassificationType,
    NormalizedPoint,
    Observation,
    Polygon,
    Rename,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


###############################################################################
# XML CONSTANTS
###############################################################################

NS: Dict[str, str] = {
    "tt": "http://www.onvif.org/ver10/schema",
    "bd": "http://www.onvif.org/ver20/analytics/humanbody",
}

###############################################################################
# GENERIC HELPERS
###############################################################################


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(elem: Optional[ET.Element]) -> Optional[str]:
    return elem.text.strip() if elem is not None and elem.text else None


###############################################################################
# COORDINATE UTILS
###############################################################################

_HALF = 0.5


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _onvif_to_norm(x: float, y: float) -> NormalizedPoint:
    return NormalizedPoint(_clip01((x + 1.0) * _HALF), _clip01((1.0 - y) * _HALF))


def _bbox_from_attrs(attrs: Dict[str, str]) -> BoundingBox:
    left, top, right, bottom = map(float, (attrs["left"], attrs["top"], attrs["right"], attrs["bottom"]))
    p1, p2 = _onvif_to_norm(left, top), _onvif_to_norm(right, bottom)
    x_min, y_min = min(p1.x, p2.x), min(p1.y, p2.y)
    width, height = abs(p2.x - p1.x), abs(p2.y - p1.y)
    return BoundingBox.from_xywh(x_min, y_min, width, height)


###############################################################################
# COLOR PARSING
###############################################################################


def _parse_colors(obj: ET.Element, xpath: str) -> List[ColorClassification]:
    clusters = obj.findall(xpath, NS)
    colors: List[ColorClassification] = []

    for c in clusters:
        colorspace = c.get("Colorspace")
        if colorspace != "RGB":
            raise ValueError(f"Only RGB colorspace is supported, got: {colorspace}")

        x = int(c.get("X") or "0")
        y = int(c.get("Y") or "0")
        z = int(c.get("Z") or "0")
        likelihood_str = c.get("Likelihood")
        if likelihood_str is None:
            raise ValueError("Likelihood missing in color cluster")
        likelihood = float(likelihood_str)

        # Validate RGB values are in valid range
        if not (0 <= x <= 255 and 0 <= y <= 255 and 0 <= z <= 255):
            raise ValueError(f"RGB values must be 0-255, got R={x}, G={y}, B={z}")

        colors.append(ColorClassification(name="RGB", rgb=RGB(x, y, z), score=Score(likelihood)))
    return colors


###############################################################################
# CLASSIFICATION MAPPING
###############################################################################

_CLASS_MAP = {
    "human": KnownClassificationType.Human.value,
    "person": KnownClassificationType.Human.value,
    "vehicle": KnownClassificationType.Vehicle.value,
    "vehicleother": KnownClassificationType.VehicleOther.value,
    "car": KnownClassificationType.Car.value,
    "bus": KnownClassificationType.Bus.value,
    "truck": KnownClassificationType.Truck.value,
    "bicycle": KnownClassificationType.Bicycle.value,
    "bike": KnownClassificationType.Bike.value,
    "animal": KnownClassificationType.Animal.value,
    "head": KnownClassificationType.Head.value,
    "licenseplate": KnownClassificationType.LicensePlate.value,
    "license_plate": KnownClassificationType.LicensePlate.value,
    "humanface": "face",
    "human_face": "face",
    "face": "face",
}


def _map_class(name: str) -> ClassificationType:
    return _CLASS_MAP.get(name.lower(), name.lower())


###############################################################################
# OBJECT → OBSERVATION HELPERS
###############################################################################


def _geometry_from_obj(obj: ET.Element) -> Optional[Geometry]:
    bbox = obj.find("./tt:Appearance/tt:Shape/tt:BoundingBox", NS)
    if bbox is not None:
        return _bbox_from_attrs(bbox.attrib)

    poly = obj.find("./tt:Appearance/tt:Shape/tt:Polygon", NS)
    if poly is not None:
        pts: List[NormalizedPoint] = []
        for p in poly.findall("./tt:Point", NS):
            x_str = p.get("x")
            y_str = p.get("y")
            if x_str is None or y_str is None:
                continue
            pts.append(_onvif_to_norm(float(x_str), float(y_str)))
        if len(pts) >= 3:
            return Polygon(points=pts)

    cog = obj.find("./tt:Appearance/tt:Shape/tt:CenterOfGravity", NS)
    if cog is not None:
        x_str = cog.get("x")
        y_str = cog.get("y")
        if x_str is not None and y_str is not None:
            center = _onvif_to_norm(float(x_str), float(y_str))
            size = 0.02
            logger.warning(f"Functionality not tested found: CenterOfGravity: {center.x}, {center.y}")
            return BoundingBox.from_xywh(center.x - size / 2, center.y - size / 2, size, size)
    return None


def _classifications_from_obj(obj: ET.Element) -> List[Classification]:
    out: List[Classification] = []

    t = obj.find("./tt:Appearance/tt:Class/tt:Type", NS)
    if t is not None and (name := _text(t)):
        likelihood_str = t.get("Likelihood")
        if likelihood_str is None:
            raise ValueError(f"Likelihood missing in classification type {name}")
        likelihood = float(likelihood_str)
        out.append(Classification(type=_map_class(name), score=Score(likelihood)))

    veh = obj.find("./tt:Appearance/tt:VehicleInfo/tt:Type", NS)
    if veh is not None and out:
        subtype = (s := _text(veh)) and s.lower()
        if subtype:
            mapping = {"bus": "bus", "car": "car", "truck": "truck", "bike": "bike", "bicycle": "bicycle"}
            out[0].type = mapping.get(subtype, subtype)
            veh_lik_str = veh.get("Likelihood")
            if veh_lik_str is None:
                raise ValueError(f"Likelihood missing in vehicle type {subtype}")
            out[0].score = Score(float(veh_lik_str))

    # clothing colours
    top = _parse_colors(obj, "./tt:Appearance/tt:HumanBody/bd:Clothing/bd:Tops/bd:Color/tt:ColorCluster/tt:Color")
    bot = _parse_colors(obj, "./tt:Appearance/tt:HumanBody/bd:Clothing/bd:Bottoms/bd:Color/tt:ColorCluster/tt:Color")
    if top and out:
        out[0].attributes.append(Attribute(name="upper_clothing_colors", value=top))
    if bot and out:
        out[0].attributes.append(Attribute(name="lower_clothing_colors", value=bot))

    # vehicle colors
    vehicle_colors = _parse_colors(obj, "./tt:Appearance/tt:Color/tt:ColorCluster/tt:Color")
    if vehicle_colors and out:
        out[0].attributes.append(Attribute(name="vehicle_colors", value=vehicle_colors))

    return out


###############################################################################
# MAIN DECODER (single-frame)
###############################################################################


def extract_frame_xml(xml: str) -> str:
    """Extract frame XML from ONVIF metadata structure.

    Handles both direct frame XML and nested structures like:
    <MetadataStream><VideoAnalytics><Frame>...</Frame></VideoAnalytics></MetadataStream>
    """
    root = ET.fromstring(xml)

    # Check if root is already a Frame/SampleFrame
    local = _strip_ns(root.tag)
    if local in {"Frame", "SampleFrame"}:
        return xml

    # Look for nested Frame/SampleFrame with namespace
    frame = None

    # Try with namespace prefix
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in {"Frame", "SampleFrame"}:
            frame = elem
            break

    if frame is None:
        raise ValueError("No <tt:Frame>/<tt:SampleFrame> elements found")

    # Convert the found frame element back to string
    return ET.tostring(frame, encoding="unicode")


def decode_onvif_xml(xml: str) -> Scene:
    """Convert ONVIF metadata → `Scene` (assumes one frame)."""
    frame_xml = extract_frame_xml(xml)
    root = ET.fromstring(frame_xml)
    frame = root

    utc_attr = frame.get("UtcTime")
    if utc_attr is None:
        raise ValueError("Frame lacks a valid UtcTime attribute")
    ts = parse_timestamp(utc_attr.strip())
    if ts is None:
        raise ValueError("Frame lacks a valid UtcTime attribute")

    scene = Scene(time_slice=TimeSlice(start=ts, end=ts))
    entities: Dict[str, Entity] = {}

    # Objects → observations
    for obj in frame.findall(".//tt:Object", NS):
        oid = obj.get("ObjectId") or "unknown"
        geom = _geometry_from_obj(obj)
        if geom is None:
            continue
        obs = Observation(geometry=geom, classification=_classifications_from_obj(obj), timestamp=ts)
        entities.setdefault(oid, Entity(id=EntityId(oid))).add_observation(obs)

    # Operations → events
    for ren in frame.findall(".//tt:ObjectTree/tt:Rename", NS):
        from_el = ren.find("./tt:from", NS)
        to_el = ren.find("./tt:to", NS)
        from_id = from_el.get("ObjectId") if from_el is not None else None
        to_id = to_el.get("ObjectId") if to_el is not None else None
        if from_id and to_id:
            scene.add_event(
                Rename(
                    timestamp=TimeSlice(start=ts, end=ts),
                    from_entity_id=EntityId(from_id),
                    to_entity_id=EntityId(to_id),
                )
            )
    for dele in frame.findall(".//tt:ObjectTree/tt:Delete", NS):
        del_id = dele.get("ObjectId")
        if del_id:
            scene.add_event(
                Delete(
                    timestamp=TimeSlice(start=ts, end=ts),
                    entity_id=EntityId(del_id),
                )
            )

    for e in entities.values():
        scene.add_entity(e)
    return scene


###############################################################################
# EXPORTS
###############################################################################

_ONVIF_FILTER_SPECS: tuple[ClassFilterSpec, ...] = (
    ClassFilterSpec("show_unknown", "Unknown", (KnownClassificationType.Unknown.value,), include_empty=True),
    ClassFilterSpec("show_humans", "Humans", (KnownClassificationType.Human.value,)),
    ClassFilterSpec("show_heads", "Heads", (KnownClassificationType.Head.value,)),
    ClassFilterSpec("show_vehicles", "Vehicles", (KnownClassificationType.Vehicle.value,)),
    ClassFilterSpec("show_vehicles_other", "Vehicles (Other)", (KnownClassificationType.VehicleOther.value,)),
    ClassFilterSpec("show_cars", "Cars", (KnownClassificationType.Car.value,)),
    ClassFilterSpec("show_buses", "Buses", (KnownClassificationType.Bus.value,)),
    ClassFilterSpec("show_trucks", "Trucks", (KnownClassificationType.Truck.value,)),
    ClassFilterSpec("show_bikes", "Bikes", (KnownClassificationType.Bike.value,)),
    ClassFilterSpec("show_bicycles", "Bicycles", (KnownClassificationType.Bicycle.value,)),
    ClassFilterSpec("show_animals", "Animals", (KnownClassificationType.Animal.value,)),
    ClassFilterSpec("show_license_plates", "License Plates", (KnownClassificationType.LicensePlate.value,)),
    ClassFilterSpec("show_faces", "Faces", ("face",)),
)

_ONVIF_KNOWN_TYPES: Set[str] = {type_name for spec in _ONVIF_FILTER_SPECS for type_name in spec.types}


def build_onvif_filter_config() -> FilterConfig:
    """Return the filter configuration for ONVIF XML decoders."""
    options: List[FilterOption] = []
    for index, spec in enumerate(_ONVIF_FILTER_SPECS):
        options.append(spec.build_option(index))

    excluded_types = set(_ONVIF_KNOWN_TYPES)
    excluded_types.add(KnownClassificationType.Unknown.value)

    options.append(
        make_other_classification_predicate(excluded_types).build_option(
            id="show_other",
            label="Other",
            default_enabled=True,
            sort_key=len(options),
        )
    )

    return FilterConfig(options=tuple(options), name="onvif_xml")


__all__ = ["decode_onvif_xml", "build_onvif_filter_config"]
