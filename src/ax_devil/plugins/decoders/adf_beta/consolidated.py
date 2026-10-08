"""Consolidated ADF track decoder and JSONL provider."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

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
    ColorClassification,
    Entity,
    EntityId,
    GeographicCoordinates,
    ImageVelocity,
    KnownClassificationType,
    Observation,
    Scene,
    Score,
    SphericalCoordinates,
    TimeSlice,
)
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.plugins.decoders.adf_beta.common import (
    ADF_CLASSIFICATION_MAP,
    build_adf_beta_filter_config,
    decode_scene_payload,
    image_from_snapshot,
)

logger = get_logger(__name__)


def _parse_class_attributes(mapped_type: str, cls_data: dict[str, Any]) -> list[Attribute]:
    attrs: list[Attribute] = []

    if mapped_type in (
        KnownClassificationType.Car.value,
        KnownClassificationType.Truck.value,
        KnownClassificationType.Bus.value,
        KnownClassificationType.Vehicle.value,
        KnownClassificationType.VehicleOther.value,
    ):
        if raw_vehicle_colors := cls_data.get("colors"):
            vehicle_colors = parse_colors_webcolors(raw_vehicle_colors)
            attrs.append(Attribute(name="vehicle_colors", value=vehicle_colors))

    elif mapped_type == KnownClassificationType.Human.value:
        upper_clothing_colors: list[ColorClassification] = []
        if raw_upper_clothing_colors := cls_data.get("upper_clothing_colors"):
            upper_clothing_colors.extend(parse_colors_webcolors(raw_upper_clothing_colors))
        if upper_clothing_colors:
            attrs.append(Attribute(name="upper_clothing_colors", value=upper_clothing_colors))

        lower_clothing_colors: list[ColorClassification] = []
        if raw_lower_clothing_color := cls_data.get("lower_clothing_colors"):
            lower_clothing_colors.extend(parse_colors_webcolors(raw_lower_clothing_color))
        if lower_clothing_colors:
            attrs.append(Attribute(name="lower_clothing_colors", value=lower_clothing_colors))

    elif mapped_type == KnownClassificationType.LicensePlate.value:
        country_code = cls_data.get("country_code")
        if country_code:
            attrs.append(Attribute(name="country_code", value=str(country_code)))

        plate_number = cls_data.get("plate_number")
        if plate_number:
            attrs.append(Attribute(name="plate_number", value=str(plate_number)))

    return attrs


def _ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _classifications_from_classes(classes_raw: Any) -> list[Classification]:
    classifications: list[Classification] = []
    for cls_data in classes_raw or []:
        cls_type = cls_data["type"]
        cls_score = float(cls_data["score"])

        mapped_type = ADF_CLASSIFICATION_MAP.get(cls_type, cls_type.lower())

        attrs = _parse_class_attributes(mapped_type, cls_data)

        classifications.append(
            Classification(
                type=mapped_type,
                score=Score(cls_score),
                attributes=attrs,
            )
        )
    return classifications


def _make_consolidated_observation(obs: dict[str, Any], classes: list[Classification]) -> Observation:
    """Build an observation for consolidated payloads, including optional vectors."""
    ts = parse_timestamp(obs.get("timestamp"))
    if ts is None:
        raise ValueError("Observation missing timestamp")

    if not (bbox := parse_bounding_box(obs.get("bounding_box"))):
        raise ValueError("Observation missing bounding box")

    observation = Observation(
        geometry=bbox,
        classification=classes,
        timestamp=ts,
    )

    if velocity_payload := obs.get("velocity"):
        observation.velocity_in_image_space = ImageVelocity(
            vx=float(velocity_payload["vx"]),
            vy=float(velocity_payload["vy"]),
        )

    if world_pos := obs.get("world_position"):
        observation.world_coordinates = SphericalCoordinates(
            distance=float(world_pos["r"]),
            azimuth=float(world_pos["azimut"]),
            elevation=float(world_pos["elevation"]),
        )

    if geo_pos := obs.get("geoposition"):
        observation.geographical_coordinates = GeographicCoordinates(
            latitude=float(geo_pos["latitude"]),
            longitude=float(geo_pos["longitude"]),
            elevation=float(geo_pos.get("elevation")) if geo_pos.get("elevation") is not None else None,
        )

    if world_velocity := obs.get("world_velocity"):
        logger.warning(
            f"World velocity data {world_velocity} found in consolidated observation, but not currently supported"
        )

    return observation


def _normalize_track_entry(track_entry: Any) -> dict[str, Any] | None:
    if track_entry is None:
        return None
    inner = track_entry.get("object")
    if isinstance(inner, dict):
        return inner
    return cast(dict[str, Any], track_entry)


def _scene_from_track(track: dict[str, Any]) -> Scene:
    entity_id = track.get("id")
    if not entity_id:
        raise ValueError("Track missing id")

    entity = Entity(id=EntityId(str(entity_id)))
    classifications = _classifications_from_classes(track.get("classes", []))

    last_observation: Observation | None = None
    for obs_payload in track.get("observations", []):
        observation = _make_consolidated_observation(obs_payload, classifications)
        entity.add_observation(observation)
        last_observation = observation

    image_payload = track.get("image")
    if image_payload is not None:
        if last_observation is None or last_observation.timestamp is None:
            raise ValueError("Track image requires a timestamped observation")
        entity.images.append(image_from_snapshot(image_payload))

    track_start_raw = track.get("start_time")
    track_end_raw = track.get("end_time")
    track_end_reason = track.get("end_reason")
    track_duration_raw = track.get("duration")

    track_start_ts = parse_timestamp(str(track_start_raw))
    track_end_ts = parse_timestamp(str(track_end_raw))
    _ensure(track_start_ts is not None, "Track start_time could not be parsed")
    _ensure(track_end_ts is not None, "Track end_time could not be parsed")

    entity.end_reason = str(track_end_reason) if track_end_reason else None
    track_duration_value: float = float(cast(str | float | int, track_duration_raw))

    scene = Scene(time_slice=TimeSlice(start=entity.start_time, end=entity.end_time))

    _ensure(track_start_ts == entity.start_time, "Inconsistent track start_time")
    _ensure(track_end_ts == entity.end_time, "Inconsistent track end_time")
    _ensure(track_duration_value == entity.duration, "Inconsistent track duration")

    scene.add_entity(entity)
    return scene


def _iter_tracks(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    version = data.get("version")
    if version is not None:
        if version != "1.0.0-beta1":
            raise ValueError(f"Unsupported consolidated ADF version: {version!r}")
        raw_tracks = data.get("tracks", [])
    else:
        raw_tracks = [data]

    for raw_track in raw_tracks:
        track = _normalize_track_entry(raw_track)
        if track is None:
            continue
        yield track


def decode_consolidated_adf_json(data: dict[str, Any]) -> Scene | None:
    scene_entities: dict[EntityId, Entity] = {}
    timestamps: list[datetime] = []

    for track in _iter_tracks(data):
        scene = _scene_from_track(track)
        for entity in scene.entities.values():
            scene_entities[entity.id] = entity
            timestamps.append(entity.start_time)
            timestamps.append(entity.end_time)

    if not scene_entities:
        return None

    start = min(timestamps)
    end = max(timestamps)
    result = Scene(time_slice=TimeSlice(start=start, end=end))
    for entity in scene_entities.values():
        result.add_entity(entity)
    return result


def decode_consolidated_adf_string(json_string: str) -> Scene | None:
    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(f"Invalid consolidated ADF JSON: {exc}", exc.doc, exc.pos) from exc
    if not isinstance(data, dict):
        raise ValueError("Consolidated payload must be an object")
    return decode_consolidated_adf_json(data)


def resolve_scenes(consolidated_scenes: Sequence[Scene | None]) -> list[Scene]:
    frame_buckets: dict[datetime, OrderedDict[EntityId, Entity]] = OrderedDict()

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

    frames: list[Scene] = []
    for ts in sorted(frame_buckets.keys()):
        bucket = frame_buckets[ts]
        frame_scene = Scene(time_slice=TimeSlice(start=ts, end=ts))
        for entity in bucket.values():
            frame_scene.add_entity(entity)
        frames.append(frame_scene)
    return frames


class ADFBetaConsolidatedDecoder(PayloadToSceneDecoder):
    """Decode consolidated ADF payloads into frame-oriented scenes."""

    def decode(self, payload: Any) -> Scene | None:
        return decode_scene_payload(
            payload,
            decoder_name="ADF beta consolidated decoder",
            decode_dict=decode_consolidated_adf_json,
            decode_string=decode_consolidated_adf_string,
        )


class ADFBetaConsolidatedJSONLDataProvider(SceneDecoderFileProvider):
    """Flatten consolidated tracks into frame payloads before caching."""

    def __init__(self, file_path: str | Path) -> None:
        super().__init__(
            file_path=file_path,
            decoder_factory=ADFBetaConsolidatedDecoder,
            decoder_name="adf_beta_consolidated",
            artifact_version=2,
            supports_sequence_lookup=False,
            filter_config_factory=build_adf_beta_filter_config,
            storage_mode=StorageMode.DERIVED_CACHE,
        )

    def _build_scene_maps(
        self,
        payloads: Iterable[str],
        decoder: PayloadToSceneDecoder,
    ) -> SceneBuildResult:
        intermediate_scenes = [decoder.decode(payload) for payload in payloads]
        logger.debug(
            f"Aggregated {len(intermediate_scenes)} frame payloads from consolidated source {self.file_path.name}",
        )
        resolved_scenes = list(enumerate(resolve_scenes(intermediate_scenes)))
        return self._build_scene_result_from_scenes(resolved_scenes, total_lines=len(intermediate_scenes))


__all__ = [
    "ADFBetaConsolidatedDecoder",
    "ADFBetaConsolidatedJSONLDataProvider",
    "build_adf_beta_filter_config",
    "decode_consolidated_adf_json",
    "decode_consolidated_adf_string",
    "resolve_scenes",
]
