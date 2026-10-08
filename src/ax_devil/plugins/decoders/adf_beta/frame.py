"""Axis Analytics Data Format (ADF) frame decoder and JSONL provider."""

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
    Image,
    ImageVelocity,
    Observation,
    Rename,
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


def _parse_class_attributes(cls_type: str, c_raw: dict[str, Any], obs: dict[str, Any]) -> list[Attribute]:
    attrs: list[Attribute] = []
    if cls_type in {"Car", "Bus", "Truck", "Vehicle"}:
        if color := parse_colors_webcolors(c_raw.get("colors")):
            attrs.append(Attribute(name="vehicle_colors", value=color))
    elif cls_type == "Human":
        if upper_clothing_color := parse_colors_webcolors(c_raw.get("upper_clothing_colors")):
            attrs.append(Attribute(name="upper_clothing_colors", value=upper_clothing_color))
        if lower_clothing_color := parse_colors_webcolors(c_raw.get("lower_clothing_colors")):
            attrs.append(Attribute(name="lower_clothing_colors", value=lower_clothing_color))
    elif cls_type == "LicensePlate":
        if cc := c_raw.get("country_code", None):
            attrs.append(Attribute(name="country_code", value=str(cc)))
        if pn := c_raw.get("plate_number", None):
            attrs.append(Attribute(name="plate_number", value=str(pn)))
    return attrs


def _make_observation(obs: dict[str, Any]) -> tuple[Observation, Image | None]:
    """Create an Observation (and optional Image) from a single ADF observation."""
    geom = parse_bounding_box(obs.get("bounding_box"))
    if geom is None:
        raise ValueError("Observation missing bounding box")
    velocity_payload = obs.get("velocity")

    c_raw = obs.get("class")
    if c_raw is None:
        classes = obs.get("classes") or []
        logger.debug(
            f"Top-N is supported but we only use the first class; found {classes}, \
                using {classes[0] if classes else None}"
        )
        c_raw = classes[0] if classes else None

    cls: list[Classification] = []
    if c_raw is not None:
        c_type = c_raw["type"]
        c_score: Score | None = c_raw.get("score")
        if c_score is None:
            raise ValueError(f"Classification score missing for type {c_type} in observation")
        attrs = _parse_class_attributes(c_type, c_raw, obs)
        if c_type:
            cls.append(
                Classification(
                    type=ADF_CLASSIFICATION_MAP.get(c_type, c_type.lower()),
                    score=c_score,
                    attributes=attrs,
                )
            )

    ts = parse_timestamp(obs.get("timestamp"))
    if ts is None:
        raise ValueError("Observation missing timestamp")
    ob = Observation(geometry=geom, classification=cls, timestamp=ts)
    if velocity_payload:
        ob.velocity_in_image_space = ImageVelocity(
            vx=float(velocity_payload["vx"]),
            vy=float(velocity_payload["vy"]),
        )

    world_pos = obs.get("world_position")
    if world_pos:
        ob.world_coordinates = SphericalCoordinates(
            distance=float(world_pos["r"]),
            azimuth=float(world_pos["azimut"]),
            elevation=float(world_pos["elevation"]),
        )

    geo_pos = obs.get("geoposition")
    if geo_pos:
        ob.geographical_coordinates = GeographicCoordinates(
            latitude=float(geo_pos["latitude"]),
            longitude=float(geo_pos["longitude"]),
            elevation=float(geo_pos.get("elevation")) if geo_pos.get("elevation") is not None else None,
        )

    if world_velocity_payload := obs.get("world_velocity"):
        logger.warning(
            f"World velocity data {world_velocity_payload} found in ADF observation, \
                but not currently supported in this decoder"
        )

    image_payload = obs.get("image")
    img = image_from_snapshot(image_payload) if image_payload is not None else None
    return ob, img


def decode_adf_json(data: dict[str, Any]) -> Scene:  # noqa: C901 - complexity OK
    """Convert ADF JSON *dict* to a `Scene`."""
    frames = data.get("frames") or [data.get("frame") or data]

    frames_with_ts: list[tuple[dict[str, Any], datetime]] = []
    for frame in frames:
        ts = parse_timestamp(frame.get("timestamp"))
        if ts is None:
            raise ValueError("Frame timestamp missing or invalid in ADF data")
        frames_with_ts.append((frame, ts))

    timestamps = [ts for _, ts in frames_with_ts]
    t0 = min(timestamps)
    t1 = max(timestamps)

    if t0 != t1:
        logger.warning(
            f"ADF data contains frames with differing timestamps ({t0.isoformat()} → {t1.isoformat()}); "
            "using full range for scene time slice"
        )

    scene = Scene(time_slice=TimeSlice(start=t0, end=t1))
    entities: dict[str, Entity] = {}

    for frame, frame_ts in frames_with_ts:
        for obs in frame.get("observations", []):
            tid = str(obs["track_id"]).strip()
            if not tid:
                raise ValueError("track_id missing in observation")

            if tid not in entities:
                entities[tid] = Entity(id=EntityId(tid))

            observation, image = _make_observation(obs)
            entities[tid].add_observation(observation)
            if image is not None:
                entities[tid].images.append(image)

        for op in frame.get("operations", []):
            if op.get("type") == "DeleteOperation" and (eid := op.get("id")):
                ts_slice = TimeSlice(start=frame_ts, end=frame_ts)
                scene.add_event(Delete(timestamp=ts_slice, entity_id=EntityId(str(eid))))
            elif op.get("type") == "RenameOperation":
                ts_slice = TimeSlice(start=frame_ts, end=frame_ts)
                old_id = EntityId(str(op["from"]))
                new_id = EntityId(str(op["to"]))
                scene.add_event(Rename(timestamp=ts_slice, from_entity_id=old_id, to_entity_id=new_id))

    for e in entities.values():
        scene.add_entity(e)

    return scene


def decode_adf_string(json_string: str) -> Scene:
    """Top-level convenience wrapper accepting a JSON *string*."""
    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(f"Invalid ADF JSON: {exc}", exc.doc, exc.pos) from exc
    return decode_adf_json(data)


class ADFBetaFrameDecoder(PayloadToSceneDecoder):
    """Shared decoder for ADF JSON payloads (strings, bytes, or dicts)."""

    def decode(self, payload: Any) -> Scene | None:
        return decode_scene_payload(
            payload,
            decoder_name="ADF beta frame decoder",
            decode_dict=decode_adf_json,
            decode_string=decode_adf_string,
        )


class ADFBetaFrameJSONLDataProvider(SceneDecoderFileProvider):
    """Data provider for ADF JSONL files."""

    def __init__(self, file_path: str | Path) -> None:
        """Initialize the ADF JSONL data provider."""
        super().__init__(
            file_path=file_path,
            decoder_factory=ADFBetaFrameDecoder,
            decoder_name="adf_beta_frame",
            artifact_version=1,
            filter_config_factory=build_adf_beta_filter_config,
            storage_mode=StorageMode.SOURCE_INDEX,
        )


__all__ = [
    "ADFBetaFrameDecoder",
    "ADFBetaFrameJSONLDataProvider",
    "build_adf_beta_filter_config",
    "decode_adf_json",
    "decode_adf_string",
]
