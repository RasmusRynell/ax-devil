"""Consolidated decoder and data provider for ADF Frame v1 payloads."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.data_sources.file_data_provider.stores import SceneBuildResult
from ax_devil.modules.scene.decoding import (
    PayloadToSceneDecoder,
    parse_bounding_box,
    parse_colors_webcolors,
    parse_timestamp,
)
from ax_devil.modules.scene.model import (
    Attribute,
    Classification,
    Entity,
    EntityId,
    GeographicCoordinates,
    Image,
    ImageVelocity,
    KnownClassificationType,
    Observation,
    Scene,
    Score,
    SphericalCoordinates,
    TimeSlice,
    WorldVelocity,
)
from ax_devil.modules.settings.logging_config import get_logger

from .common import (
    ADF_V1_CLASSIFICATION_MAP,
    build_adf_frame_v1_filter_config,
    decode_scene_payload,
    image_from_snapshot,
)

logger = get_logger(__name__)

__all__ = [
    "ADFFrameV1ConsolidatedDecoder",
    "ADFFrameV1ConsolidatedDataProvider",
    "decode_adf_frame_v1_consolidated_json",
    "decode_adf_frame_v1_consolidated_string",
    "resolve_scenes",
]


def _parse_class_attributes(mapped_type: str, class_payload: Dict[str, Any]) -> List[Attribute]:
    """Translate consolidated classification attributes into world model attributes."""
    attributes: List[Attribute] = []

    if mapped_type == KnownClassificationType.Human.value:
        if upper_colors := class_payload.get("upper_clothing_colors"):
            attributes.append(Attribute(name="upper_clothing_colors", value=parse_colors_webcolors(upper_colors)))
        if lower_colors := class_payload.get("lower_clothing_colors"):
            attributes.append(Attribute(name="lower_clothing_colors", value=parse_colors_webcolors(lower_colors)))
        if "carries_bag" in class_payload:
            attributes.append(Attribute(name="carries_bag", value=bool(class_payload["carries_bag"])))
        if "face_visible" in class_payload:
            attributes.append(Attribute(name="face_visible", value=float(class_payload["face_visible"])))

    elif mapped_type in {
        KnownClassificationType.Vehicle.value,
        KnownClassificationType.VehicleOther.value,
        KnownClassificationType.Car.value,
        KnownClassificationType.Truck.value,
        KnownClassificationType.Bus.value,
        KnownClassificationType.Bike.value,
        KnownClassificationType.Bicycle.value,
    }:
        if colors := class_payload.get("colors"):
            attributes.append(Attribute(name="vehicle_colors", value=parse_colors_webcolors(colors)))

    elif mapped_type == KnownClassificationType.LicensePlate.value:
        if "country_code" in class_payload:
            attributes.append(Attribute(name="country_code", value=str(class_payload["country_code"])))
        if "state_code" in class_payload:
            attributes.append(Attribute(name="state_code", value=str(class_payload["state_code"])))
        if "plate_number" in class_payload:
            attributes.append(Attribute(name="plate_number", value=str(class_payload["plate_number"])))

    return attributes


def _classifications_from_classes(classes_raw: Any) -> List[Classification]:
    """Return consolidated classifications mapped to `ADF_V1_CLASSIFICATION_MAP`."""
    classifications: List[Classification] = []
    for class_payload in classes_raw or []:
        class_type_raw = class_payload.get("type")
        if class_type_raw is None:
            raise ValueError("Consolidated classification missing type")

        score_raw = class_payload.get("score")
        if score_raw is None:
            raise ValueError("Consolidated classification missing score")

        mapped_type = ADF_V1_CLASSIFICATION_MAP.get(str(class_type_raw), str(class_type_raw).lower())
        attrs = _parse_class_attributes(mapped_type, class_payload)
        classifications.append(Classification(type=mapped_type, score=Score(float(score_raw)), attributes=attrs))
    return classifications


def _parse_world_velocity(payload: Dict[str, Any] | None) -> WorldVelocity | None:
    if payload is None:
        return None
    pitch = payload.get("pitch")
    yaw = payload.get("yaw")
    speed = payload.get("speed")
    if pitch is None or yaw is None or speed is None:
        raise ValueError("World velocity payload missing pitch, yaw, or speed")
    return WorldVelocity(pitch=float(pitch), yaw=float(yaw), speed=float(speed))


def _parse_world_coordinates(world_position: Dict[str, Any] | None) -> SphericalCoordinates | None:
    if world_position is None:
        return None
    distance = world_position.get("distance")
    azimuth = world_position.get("azimuth") or world_position.get("azimut")
    elevation = world_position.get("elevation")
    if distance is None or azimuth is None or elevation is None:
        raise ValueError("World position payload missing distance, azimuth, or elevation")
    return SphericalCoordinates(distance=float(distance), azimuth=float(azimuth), elevation=float(elevation))


def _parse_geographical_coordinates(geoposition: Dict[str, Any] | None) -> GeographicCoordinates | None:
    if geoposition is None:
        return None
    latitude = geoposition.get("latitude")
    longitude = geoposition.get("longitude")
    if latitude is None or longitude is None:
        raise ValueError("Geoposition missing latitude or longitude")
    elevation = geoposition.get("elevation")
    return GeographicCoordinates(
        latitude=float(latitude),
        longitude=float(longitude),
        elevation=float(elevation) if elevation is not None else None,
    )


def _image_from_consolidated_snapshot(snapshot: Dict[str, Any] | None) -> Image | None:
    if snapshot is None:
        return None
    payload = dict(snapshot)
    if "bounding_box" not in payload and "crop_box" in payload:
        payload["bounding_box"] = payload["crop_box"]
    return image_from_snapshot(payload)


def _ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _make_consolidated_observation(observation_payload: Dict[str, Any], classes: List[Classification]) -> Observation:
    """Construct an `Observation` from a consolidated track observation payload."""
    ts = parse_timestamp(observation_payload.get("timestamp"))
    if ts is None:
        raise ValueError("Observation missing timestamp")

    bbox = parse_bounding_box(observation_payload.get("bounding_box"))
    if bbox is None:
        raise ValueError("Observation missing bounding box")

    observation = Observation(geometry=bbox, classification=classes, timestamp=ts)

    if velocity := observation_payload.get("velocity"):
        observation.velocity_in_image_space = ImageVelocity(vx=float(velocity["vx"]), vy=float(velocity["vy"]))

    observation.velocity_in_world_space = _parse_world_velocity(observation_payload.get("world_velocity"))
    observation.world_coordinates = _parse_world_coordinates(observation_payload.get("world_position"))
    observation.geographical_coordinates = _parse_geographical_coordinates(observation_payload.get("geoposition"))

    return observation


def _scene_from_track(track: Dict[str, Any]) -> Scene:
    """Convert a single consolidated track payload into a `Scene`."""
    raw_entity_id = track.get("id")
    if raw_entity_id is None:
        raise ValueError("Track missing id")
    entity = Entity(id=EntityId(str(raw_entity_id)))

    classifications = _classifications_from_classes(track.get("classes", []))
    observations = track.get("path") or track.get("observations") or []
    if not observations:
        raise ValueError("Track missing observations/path data")

    last_observation: Observation | None = None
    for obs_payload in observations:
        observation = _make_consolidated_observation(obs_payload, classifications)
        entity.add_observation(observation)
        last_observation = observation

    if image_payload := track.get("image"):
        if last_observation is None or last_observation.timestamp is None:
            raise ValueError("Track image requires at least one timestamped observation")
        image = _image_from_consolidated_snapshot(image_payload)
        if image is not None:
            entity.images.append(image)

    track_start_ts = parse_timestamp(track.get("start_time"))
    track_end_ts = parse_timestamp(track.get("end_time"))
    _ensure(track_start_ts is not None, "Track start_time missing or invalid")
    _ensure(track_end_ts is not None, "Track end_time missing or invalid")

    duration_raw = track.get("duration")
    track_duration_value = float(duration_raw) if duration_raw is not None else None

    entity.end_reason = str(track.get("end_reason")) if track.get("end_reason") else None
    scene = Scene(time_slice=TimeSlice(start=entity.start_time, end=entity.end_time))

    _ensure(track_start_ts == entity.start_time, "Inconsistent track start_time")
    _ensure(track_end_ts == entity.end_time, "Inconsistent track end_time")
    if track_duration_value is not None:
        _ensure(track_duration_value == entity.duration, "Inconsistent track duration")

    scene.add_entity(entity)
    return scene


class ADFFrameV1ConsolidatedDecoder(PayloadToSceneDecoder):
    """Decode consolidated ADF Frame v1 payloads into frame-oriented scenes."""

    def decode(self, payload: Any) -> Scene | None:
        return decode_scene_payload(
            payload,
            decoder_name="ADF Frame v1 consolidated decoder",
            decode_dict=decode_adf_frame_v1_consolidated_json,
            decode_string=decode_adf_frame_v1_consolidated_string,
        )


def _iter_tracks(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Iterate over track payloads from consolidated data.

    The beta implementation accepts both single-track payloads and arrays gated by a
    version field. We keep the same shape here to make porting the final logic
    straightforward.
    """
    version = data.get("version")
    if version is not None:
        tracks = data.get("tracks", [])
    else:
        tracks = [data]

    for raw_track in tracks:
        if raw_track is None:
            continue
        if not isinstance(raw_track, dict):
            raise ValueError(f"Unexpected consolidated track payload: {type(raw_track)!r}")
        yield raw_track


def decode_adf_frame_v1_consolidated_json(data: Dict[str, Any]) -> Scene | None:
    """Decode a consolidated ADF v1 payload represented as a dictionary."""
    scenes: Dict[EntityId, Entity] = {}
    timestamps: List[datetime] = []

    for track in _iter_tracks(data):
        scene = _scene_from_track(track)
        for entity in scene.entities.values():
            scenes[entity.id] = entity
            if entity.start_time:
                timestamps.append(entity.start_time)
            if entity.end_time:
                timestamps.append(entity.end_time)

    if not scenes:
        return None

    start = min(timestamps)
    end = max(timestamps)
    consolidated_scene = Scene(time_slice=TimeSlice(start=start, end=end))
    for entity in scenes.values():
        consolidated_scene.add_entity(entity)
    return consolidated_scene


def decode_adf_frame_v1_consolidated_string(payload: str) -> Scene | None:
    """Decode a consolidated ADF v1 payload represented as a JSON string."""
    json_string = payload
    if "\t" in payload:
        prefix, json_section = payload.split("\t", 1)
        if json_section:
            json_string = json_section
            logger.debug(f"ADF Frame v1 consolidated payload included TSV timestamp column: {prefix}")

    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive branch
        raise json.JSONDecodeError(f"Invalid consolidated ADF v1 JSON: {exc}", exc.doc, exc.pos) from exc

    if not isinstance(data, dict):
        raise ValueError("Consolidated payload must be a JSON object")

    return decode_adf_frame_v1_consolidated_json(data)


def resolve_scenes(consolidated_scenes: Sequence[Scene | None]) -> List[Scene]:
    """Flatten consolidated tracks into per-frame scenes.

    This helper matches the beta decoder behaviour, grouping observations by timestamp
    so the resulting stream can be cached in JSONL form.
    """
    frame_buckets: Dict[datetime, OrderedDict[EntityId, Entity]] = OrderedDict()

    for scene in consolidated_scenes:
        if scene is None:
            continue
        for entity in scene.entities.values():
            for observation in entity.observations:
                ts = observation.timestamp
                if ts is None:
                    continue
                bucket = frame_buckets.setdefault(ts, OrderedDict())
                frame_entity = bucket.get(entity.id)
                if frame_entity is None:
                    frame_entity = Entity(id=EntityId(str(entity.id)))
                    bucket[entity.id] = frame_entity
                frame_entity.add_observation(observation)

    frames: List[Scene] = []
    for ts in sorted(frame_buckets.keys()):
        bucket = frame_buckets[ts]
        frame_scene = Scene(time_slice=TimeSlice(start=ts, end=ts))
        for entity in bucket.values():
            frame_scene.add_entity(entity)
        frames.append(frame_scene)
    return frames


class ADFFrameV1ConsolidatedDataProvider(SceneDecoderFileProvider):
    """Data provider adapter for consolidated ADF Frame v1 JSONL tracks."""

    def __init__(self, file_path: str | Path) -> None:
        super().__init__(
            file_path=file_path,
            decoder_factory=ADFFrameV1ConsolidatedDecoder,
            decoder_name="adf_v1_consolidated",
            artifact_version=2,
            supports_sequence_lookup=False,
            filter_config_factory=build_adf_frame_v1_filter_config,
            storage_mode=StorageMode.DERIVED_CACHE,
        )

    def _build_scene_maps(
        self,
        payloads: Iterable[str],
        decoder: PayloadToSceneDecoder,
    ) -> SceneBuildResult:
        logger.debug(f"Building consolidated scene map for {self.file_path.name}")

        intermediate_scenes = [decoder.decode(payload) for payload in payloads]
        logger.debug(f"Aggregated {len(intermediate_scenes)} consolidated payloads from {self.file_path.name}")
        resolved_scenes = list(enumerate(resolve_scenes(intermediate_scenes)))
        return self._build_scene_result_from_scenes(resolved_scenes, total_lines=len(intermediate_scenes))
