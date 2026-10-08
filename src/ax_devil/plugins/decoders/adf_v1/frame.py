"""ADF Frame v1 decoder and JSONL provider."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.scene.decoding import (
    PayloadToSceneDecoder,
    parse_bounding_box,
    parse_colors_webcolors,
    parse_timestamp,
)
from ax_devil.modules.scene.model import (
    Attribute,
    Classification,
    Delete,
    Entity,
    EntityId,
    GeographicCoordinates,
    KnownClassificationType,
    MotionState,
    Observation,
    Rename,
    Scene,
    Score,
    SphericalCoordinates,
    TimeSlice,
    WorldVelocity,
)
from ax_devil.modules.settings.logging_config import get_logger

from .common import build_adf_frame_v1_filter_config, decode_scene_payload

logger = get_logger(__name__)


def _parse_class_attributes(class_type: str, class_data: dict[str, Any]) -> list[Attribute]:
    attributes: list[Attribute] = []
    if class_type in {"human", "person"}:
        if "upper_clothing_colors" in class_data:
            attributes.append(
                Attribute(
                    name="upper_clothing_colors",
                    value=parse_colors_webcolors(class_data["upper_clothing_colors"]),
                )
            )
        if "lower_clothing_colors" in class_data:
            attributes.append(
                Attribute(
                    name="lower_clothing_colors",
                    value=parse_colors_webcolors(class_data["lower_clothing_colors"]),
                )
            )
        if "carries_bag" in class_data:
            attributes.append(Attribute(name="carries_bag", value=bool(class_data["carries_bag"])))
        if "face_visible" in class_data:
            attributes.append(Attribute(name="face_visible", value=float(class_data["face_visible"])))
    elif class_type in {"vehicle", "vehicleother", "car", "truck", "bus", "bike", "bicycle"}:
        if "colors" in class_data:
            attributes.append(Attribute(name="vehicle_colors", value=parse_colors_webcolors(class_data["colors"])))
    elif class_type == "licenseplate":
        if "country_code" in class_data:
            attributes.append(Attribute(name="country_code", value=str(class_data["country_code"])))
        if "state_code" in class_data:
            attributes.append(Attribute(name="state_code", value=str(class_data["state_code"])))
    return attributes


def _create_classification(class_data: dict[str, Any]) -> list[Classification]:
    class_type_raw = class_data.get("type")
    if class_type_raw is None:
        raise ValueError("Classification missing type")
    score_raw = class_data.get("score")
    if score_raw is None:
        raise ValueError("Classification missing score")

    class_type = str(class_type_raw).lower()
    score = Score(float(score_raw))

    type_mapping = {
        "human": KnownClassificationType.Human.value,
        "person": KnownClassificationType.Human.value,
        "vehicle": KnownClassificationType.Vehicle.value,
        "car": KnownClassificationType.Car.value,
        "bus": KnownClassificationType.Bus.value,
        "truck": KnownClassificationType.Truck.value,
        "bike": KnownClassificationType.Bike.value,
        "bicycle": KnownClassificationType.Bicycle.value,
        "vehicleother": KnownClassificationType.VehicleOther.value,
        "animal": KnownClassificationType.Animal.value,
        "head": KnownClassificationType.Head.value,
        "licenseplate": KnownClassificationType.LicensePlate.value,
    }
    mapped_type = type_mapping.get(class_type)
    if mapped_type is None:
        raise ValueError(f"Unknown classification type '{class_type_raw}'")

    attributes = _parse_class_attributes(class_type, class_data)
    return [Classification(type=mapped_type, score=score, attributes=attributes)]


def _create_world_velocity(velocity_data: dict[str, Any] | None) -> WorldVelocity | None:
    if velocity_data is None:
        return None

    pitch = velocity_data.get("pitch")
    speed = velocity_data.get("speed")
    yaw = velocity_data.get("yaw")
    if pitch is None or speed is None or yaw is None:
        raise ValueError("Velocity payload missing pitch, speed, or yaw")

    return WorldVelocity(pitch=float(pitch), speed=float(speed), yaw=float(yaw))


def _parse_geographical_coordinates(geoposition_data: dict[str, Any] | None) -> GeographicCoordinates | None:
    if geoposition_data is None:
        return None
    if geoposition_data.get("latitude") is None or geoposition_data.get("longitude") is None:
        raise ValueError("Geoposition payload missing latitude or longitude")

    elevation = geoposition_data.get("elevation")
    return GeographicCoordinates(
        latitude=float(geoposition_data["latitude"]),
        longitude=float(geoposition_data["longitude"]),
        elevation=float(elevation) if elevation is not None else None,
    )


def _parse_world_coordinates(world_position_data: dict[str, Any] | None) -> SphericalCoordinates | None:
    if world_position_data is None:
        return None

    distance = world_position_data.get("distance")
    azimuth = world_position_data.get("azimuth")
    elevation = world_position_data.get("elevation")
    if distance is None or azimuth is None or elevation is None:
        raise ValueError("World position payload missing distance, azimuth, or elevation")

    return SphericalCoordinates(distance=float(distance), azimuth=float(azimuth), elevation=float(elevation))


def _decode_detection(detection_data: dict[str, Any], frame_timestamp: datetime) -> Entity:
    track_id_raw = detection_data.get("object_track_id")
    if track_id_raw is None:
        raise ValueError("Detection missing object_track_id")
    entity_id = str(track_id_raw).strip()
    if not entity_id:
        raise ValueError("object_track_id is empty")

    bbox = parse_bounding_box(detection_data.get("bounding_box"))
    if bbox is None:
        raise ValueError("Detection missing bounding box")

    class_item = detection_data.get("class")
    classification = []
    if class_item is not None:
        classification = _create_classification(class_item)

    observation = Observation(
        geometry=bbox,
        classification=classification,
        timestamp=frame_timestamp,
        velocity_in_world_space=_create_world_velocity(detection_data.get("velocity")),
        geographical_coordinates=_parse_geographical_coordinates(detection_data.get("geoposition")),
        world_coordinates=_parse_world_coordinates(detection_data.get("world_position")),
    )

    moving = detection_data.get("moving")
    motion_state = None
    if isinstance(moving, bool):
        motion_state = MotionState.Moving if moving else MotionState.Stationary
    entity = Entity(id=EntityId(entity_id), motion_state=motion_state)
    entity.add_observation(observation)
    return entity


def _decode_track_event(track_event: dict[str, Any], frame_timestamp: datetime) -> Delete | Rename:
    event_type = str(track_event.get("type", "")).lower()
    timestamp = TimeSlice(start=frame_timestamp, end=frame_timestamp)

    if event_type == "trackended":
        object_track_id = track_event.get("object_track_id")
        if object_track_id is None:
            raise ValueError("TrackEnded event missing object_track_id")
        return Delete(timestamp=timestamp, entity_id=EntityId(str(object_track_id)))

    if event_type == "rename":
        from_id = track_event.get("from_id")
        to_id = track_event.get("to_id")
        if from_id is None or to_id is None:
            raise ValueError("Rename event missing from_id or to_id")
        return Rename(
            timestamp=timestamp,
            from_entity_id=EntityId(str(from_id)),
            to_entity_id=EntityId(str(to_id)),
        )

    raise ValueError(f"Unsupported track event type '{track_event.get('type')}'")


def decode_adf_frame_v1_data(data: dict[str, Any]) -> Scene:
    """Convert ADF Frame v1 payload dict to a Scene."""
    if "frame" in data:
        data = data["frame"]

    frame_timestamp = parse_timestamp(data.get("timestamp"))
    if frame_timestamp is None:
        raise ValueError("Frame timestamp missing or invalid in ADF Frame v1 data")
    frame_ts: datetime = frame_timestamp

    scene = Scene(time_slice=TimeSlice(start=frame_ts, end=frame_ts))

    detections_data = data.get("detections") or []
    for detection in detections_data:
        scene.add_entity(_decode_detection(detection, frame_ts))

    track_events_data = data.get("track_events") or []
    for track_event in track_events_data:
        scene.add_event(_decode_track_event(track_event, frame_ts))

    logger.debug(f"Decoded ADF Frame v1 scene with {len(scene.entities)} entities")
    return scene


def decode_adf_frame_v1_str(payload: str) -> Scene:
    """Top-level convenience wrapper accepting a JSON/TSV string."""
    json_string = payload
    if "\t" in payload:
        prefix, json_section = payload.split("\t", 1)
        if not json_section:
            raise ValueError("ADF Frame v1 TSV row missing JSON payload")
        json_string = json_section
        logger.debug(f"ADF Frame v1 payload included TSV timestamp column: {prefix}")

    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(f"Invalid ADF Frame v1 JSON: {exc}", exc.doc, exc.pos) from exc

    if isinstance(data, dict) and "frame" in data:
        data = data["frame"]

    if not isinstance(data, dict):
        raise ValueError("ADF Frame v1 data must be a dictionary")

    return decode_adf_frame_v1_data(data)


class ADFFrameV1Decoder(PayloadToSceneDecoder):
    """Shared decoder for ADF Frame v1 payloads."""

    def decode(self, payload: Any) -> Scene | None:
        return decode_scene_payload(
            payload,
            decoder_name="ADF Frame v1 decoder",
            decode_dict=decode_adf_frame_v1_data,
            decode_string=decode_adf_frame_v1_str,
        )


class ADFFrameV1DataProvider(SceneDecoderFileProvider):
    """Data provider for ADF Frame v1 JSONL files."""

    def __init__(self, file_path: str | Path) -> None:
        super().__init__(
            file_path=file_path,
            decoder_factory=ADFFrameV1Decoder,
            decoder_name="adf_v1_frame",
            artifact_version=1,
            filter_config_factory=build_adf_frame_v1_filter_config,
            storage_mode=StorageMode.SOURCE_INDEX,
        )


__all__ = [
    "ADFFrameV1DataProvider",
    "ADFFrameV1Decoder",
    "build_adf_frame_v1_filter_config",
    "decode_adf_frame_v1_data",
    "decode_adf_frame_v1_str",
]
