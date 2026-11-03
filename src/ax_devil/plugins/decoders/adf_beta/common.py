"""Shared helpers for ADF decoders."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from ax_devil.modules.filtering.filter_config import FilterConfig
from ax_devil.modules.filtering.predicate_utils import ClassFilterSpec, make_other_classification_predicate
from ax_devil.modules.scene.decoding import parse_bounding_box, parse_timestamp
from ax_devil.modules.scene.model import Image, KnownClassificationType

SceneType = TypeVar("SceneType")

ADF_CLASSIFICATION_MAP = {
    "Car": KnownClassificationType.Car.value,
    "Human": KnownClassificationType.Human.value,
    "Vehicle": KnownClassificationType.Vehicle.value,
    "VehicleOther": KnownClassificationType.VehicleOther.value,
    "Bus": KnownClassificationType.Bus.value,
    "Truck": KnownClassificationType.Truck.value,
    "Bike": KnownClassificationType.Bike.value,
    "Bicycle": KnownClassificationType.Bicycle.value,
    "Animal": KnownClassificationType.Animal.value,
    "Head": KnownClassificationType.Head.value,
    "LicensePlate": KnownClassificationType.LicensePlate.value,
    "Bag": KnownClassificationType.Bag.value,
    "Face": KnownClassificationType.Face.value,
}

_ADF_FILTER_SPECS: tuple[ClassFilterSpec, ...] = (
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
    ClassFilterSpec("show_faces", "Faces", (KnownClassificationType.Face.value,)),
    ClassFilterSpec("show_bags", "Bags", (KnownClassificationType.Bag.value,)),
)

_ADF_KNOWN_TYPES = {type_name for spec in _ADF_FILTER_SPECS for type_name in spec.types}


def decode_scene_payload(
    payload: Any,
    *,
    decoder_name: str,
    decode_dict: Callable[[dict[str, Any]], SceneType],
    decode_string: Callable[[str], SceneType],
) -> SceneType | None:
    """Normalize payload variants (bytes/str/dict) for Scene decoders."""
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return None
        return decode_string(stripped)
    if isinstance(payload, dict):
        return decode_dict(payload)
    raise TypeError(f"Unsupported payload type for {decoder_name}: {type(payload)!r}")


def image_from_snapshot(snapshot: dict[str, Any] | None) -> Image:
    """Convert an ADF snapshot payload to an Image, requiring all fields to be present."""
    if snapshot is None:
        raise ValueError("Image payload missing")

    data = snapshot.get("data")
    if not data:
        raise ValueError("Image payload missing data field")

    ts = parse_timestamp(snapshot.get("timestamp"))
    if ts is None:
        raise ValueError("Image timestamp could not be parsed")

    bbox = parse_bounding_box(snapshot.get("bounding_box"))
    if bbox is None:
        raise ValueError("Image payload missing bounding box")

    return Image.from_base64(timestamp=ts, bounding_box=bbox, base64_data=data)


def build_adf_beta_filter_config() -> FilterConfig:
    options = [spec.build_option(index) for index, spec in enumerate(_ADF_FILTER_SPECS)]

    excluded_types = set(_ADF_KNOWN_TYPES)
    excluded_types.add(KnownClassificationType.Unknown.value)

    options.append(
        make_other_classification_predicate(excluded_types).build_option(
            id="show_other",
            label="Other",
            default_enabled=True,
            sort_key=len(options),
        )
    )

    return FilterConfig(options=tuple(options), name="adf_beta")


__all__ = ["ADF_CLASSIFICATION_MAP", "decode_scene_payload", "image_from_snapshot", "build_adf_beta_filter_config"]
