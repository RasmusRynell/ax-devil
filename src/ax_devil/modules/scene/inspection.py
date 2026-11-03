"""World-model inspection: the grouped, queryable view of an entity's fields and its one-line summary."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any

from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    Classification,
    ColorClassification,
    Confidence,
    Entity,
    Geometry,
    Observation,
    Score,
)
from ax_devil.modules.scene.query import FieldValue, QueryRecord

_MAX_DEBUG_BYTES = 32


class FieldGroup(Enum):
    """Sections of the object inspection view, in display order."""

    CLASSIFICATION = "Classification"
    MOTION = "Motion"
    GEOMETRY = "Geometry"
    DEBUG = "Debug"
    RAW = "Raw"
    IMAGES = "Images"

    @property
    def title(self) -> str:
        """Return the section title."""
        return self.value

    @property
    def folded_by_default(self) -> bool:
        """Return whether the section starts folded because it is rarely the first thing to read."""
        return self in (FieldGroup.DEBUG, FieldGroup.RAW, FieldGroup.IMAGES)


class FieldOrigin(Enum):
    """Who supplied a field's value: the source file or stream verbatim, or the decoder; in display order."""

    SOURCE = "Source"
    DECODER = "Computed"

    @property
    def title(self) -> str:
        """Return the caption over the fields of this origin."""
        return self.value


@dataclass(frozen=True, slots=True)
class InspectionField:
    """One leaf value of an entity, addressed by its path inside a ``FieldGroup``.

    ``fraction`` is set for scores and confidences in [0, 1] so a view can draw them as a bar. ``text`` is what a view
    shows in place of the value (``0.87`` for 0.8712); queries compare the value. ``rgb`` is set on a color entry, whose
    path ends in its name and whose value is its score. ``origin`` says whether the source supplied the value verbatim
    or the decoder produced it.
    """

    path: tuple[str, ...]
    value: FieldValue
    fraction: float | None = None
    rgb: RGB | None = None
    text: str | None = None
    origin: FieldOrigin = FieldOrigin.DECODER

    def record_items(self) -> Iterator[tuple[tuple[str, ...], FieldValue]]:
        """Yield the (path, value) pairs a query searches: the field itself and, for a color, its r, g and b."""
        yield self.path, self.value
        if self.rgb is not None:
            yield (*self.path, "rgb", "r"), self.rgb.r
            yield (*self.path, "rgb", "g"), self.rgb.g
            yield (*self.path, "rgb", "b"), self.rgb.b


@dataclass(frozen=True, slots=True)
class EntitySummary:
    """The few facts shown about an entity in lists and hover chips."""

    entity_id: str
    class_name: str
    score: Score | None
    confidence: Confidence | None
    speed: float | None
    state: str | None


# Observation fields that are not "raw": where they are shown. Classification is built by
# ``_classification_fields``. Geometry and debug paths are not prefixed with their field name.
_OBSERVATION_GROUPS = {
    "classification": FieldGroup.CLASSIFICATION,
    "geometry": FieldGroup.GEOMETRY,
    "geographical_coordinates": FieldGroup.GEOMETRY,
    "world_coordinates": FieldGroup.GEOMETRY,
    "velocity_in_image_space": FieldGroup.MOTION,
    "velocity_in_world_space": FieldGroup.MOTION,
    "debug": FieldGroup.DEBUG,
    "source_data": FieldGroup.DEBUG,
}
_UNPREFIXED_OBSERVATION_FIELDS = frozenset({"geometry", "debug", "source_data"})
_DEBUG_ORIGINS = {"debug": FieldOrigin.DECODER, "source_data": FieldOrigin.SOURCE}
_UNKNOWN_CLASS = "unknown"


def entity_fields(entity: Entity) -> tuple[tuple[FieldGroup, tuple[InspectionField, ...]], ...]:
    """Return the entity's populated fields by group, from its latest observation, omitting empty groups."""
    grouped: dict[FieldGroup, list[InspectionField]] = {group: [] for group in FieldGroup}
    if entity.motion_state is not None:
        grouped[FieldGroup.MOTION].append(InspectionField(("state",), entity.motion_state.value))
    grouped[FieldGroup.RAW].extend(
        _flatten_items(_dataclass_items(entity, skip={"id", "observations", "images", "motion_state"}))
    )
    grouped[FieldGroup.IMAGES].extend(_flatten(entity.images, ()))

    observation = entity.latest_observation
    if observation is not None:
        grouped[FieldGroup.CLASSIFICATION].extend(_classification_fields(observation))
        for name, value in _dataclass_items(observation, skip={"classification"}):
            prefix = () if name in _UNPREFIXED_OBSERVATION_FIELDS else (name,)
            group = _OBSERVATION_GROUPS.get(name, FieldGroup.RAW)
            origin = _DEBUG_ORIGINS.get(name)
            grouped[group].extend(_flatten(value, prefix) if origin is None else _debug_leaves(value, prefix, origin))
    return tuple((group, tuple(items)) for group, items in grouped.items() if items)


def latest_primary_classification(entity: Entity) -> Classification | None:
    """Return the entity's best-ranked classification in its latest observation, which names its class and measures."""
    observation = entity.latest_observation
    return observation.primary_classification if observation is not None else None


def entity_summary(entity: Entity) -> EntitySummary:
    """Summarize an entity for lists and hover chips; speed is only what the decoder reported."""
    observation = entity.latest_observation
    primary = latest_primary_classification(entity)
    velocity = observation.velocity_in_world_space if observation is not None else None
    return EntitySummary(
        entity_id=str(entity.id),
        class_name=primary.type if primary is not None else _UNKNOWN_CLASS,
        score=primary.score if primary is not None else None,
        confidence=primary.confidence if primary is not None else None,
        speed=velocity.speed if velocity is not None else None,
        state=entity.motion_state.value if entity.motion_state is not None else None,
    )


def entity_record(entity: Entity) -> QueryRecord:
    """Return the entity as a query record keyed ``group.path`` (``debug.latency``); free text is id, class, state."""
    values: dict[str, list[FieldValue]] = {}
    for group, items in entity_fields(entity):
        for item in items:
            for path, value in item.record_items():
                values.setdefault(".".join((group.name.lower(), *path)), []).append(value)
    summary = entity_summary(entity)
    text = (summary.entity_id, summary.class_name, *(() if summary.state is None else (summary.state,)))
    return QueryRecord(fields={key: tuple(items) for key, items in values.items()}, text=text)


def _classification_fields(observation: Observation) -> Iterator[InspectionField]:
    primary = observation.primary_classification
    if primary is not None:
        yield InspectionField(("type",), primary.type)
        yield from _flatten(primary.score, ("score",))
        yield from _flatten(primary.confidence, ("confidence",))
        yield from _flatten(primary.attributes, ("attributes",))
    for candidate in observation.ranked_classifications:
        if candidate is primary:  # Its attributes are already listed under ``attributes``.
            yield from _flatten(candidate.score, ("candidates", candidate.type, "score"))
            yield from _flatten(candidate.confidence, ("candidates", candidate.type, "confidence"))
        else:
            yield from _flatten(candidate, ("candidates",))


def _flatten_items(items: Iterable[tuple[str, Any]]) -> Iterator[InspectionField]:
    for name, value in items:
        yield from _flatten(value, (name,))


def _debug_leaves(value: Any, path: tuple[str, ...], origin: FieldOrigin) -> Iterator[InspectionField]:
    """Yield every debug leaf; reported empty values stay as ``null``, ``""``, ``[]`` or ``{}``, bytes as hex."""
    if value is None:
        yield InspectionField(path, "null", origin=origin)
    elif isinstance(value, str):
        yield InspectionField(path, value, origin=origin)
    elif isinstance(value, bytes):
        shown = value[:_MAX_DEBUG_BYTES].hex()
        text = shown if len(value) <= _MAX_DEBUG_BYTES else f"{shown}... ({len(value)} bytes)"
        yield InspectionField(path, text, origin=origin)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _debug_leaves(item, (*path, str(key)), origin)
        if not value:
            yield InspectionField(path, "{}", origin=origin)
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            yield from _debug_leaves(item, (*path, str(index)), origin)
        if not value:
            yield InspectionField(path, "[]", origin=origin)
    else:
        yield from (replace(item, origin=origin) for item in _flatten(value, path))


def _flatten(value: Any, path: tuple[str, ...]) -> Iterator[InspectionField]:
    """Yield every populated leaf below ``value``; named values (attributes, classifications) extend the path."""
    if _is_empty(value):
        return
    if isinstance(value, Enum):
        yield from _flatten(value.value, path)
    elif isinstance(value, bool | int | float | str):
        yield InspectionField(path, value)
    elif isinstance(value, Score | Confidence):
        yield InspectionField(path, value.value, value.fraction, text=value.text)
    elif isinstance(value, Attribute):
        yield from _flatten(value.value, (*path, value.name))
    elif isinstance(value, Classification):
        yield from _flatten(value.score, (*path, value.type, "score"))
        yield from _flatten(value.confidence, (*path, value.type, "confidence"))
        yield from _flatten(value.attributes, (*path, value.type, "attributes"))
    elif isinstance(value, ColorClassification):
        yield InspectionField((*path, value.name), value.score.value, value.score.fraction, value.rgb)
    elif isinstance(value, Geometry):
        x, y, width, height = value.as_xywh()
        for name, number in (("x", x), ("y", y), ("width", width), ("height", height)):
            yield InspectionField((*path, name), number)
        points = value.polygon_points()
        if points:
            yield InspectionField((*path, "points"), len(points))
    elif isinstance(value, datetime):
        yield InspectionField(path, value.isoformat())
    elif isinstance(value, bytes):
        yield InspectionField(path, f"{len(value)} bytes")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _flatten(item, (*path, str(key)))
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            named = isinstance(item, Attribute | Classification | ColorClassification)
            yield from _flatten(item, path if named else (*path, str(index)))
    elif is_dataclass(value):
        for name, item in _dataclass_items(value):
            yield from _flatten(item, (*path, name))
    else:
        yield InspectionField(path, str(value))


def _dataclass_items(value: object, *, skip: set[str] | None = None) -> Iterable[tuple[str, Any]]:
    if not is_dataclass(value):
        return

    skipped = skip or set()
    for field in fields(value):
        if field.name in skipped:
            continue
        item = getattr(value, field.name)
        if _is_empty(item):
            continue
        yield field.name, item


def _is_empty(value: Any) -> bool:
    """Return True for None and empty text, bytes and containers; other objects (such as arrays) count as populated."""
    if value is None:
        return True
    return isinstance(value, str | bytes | list | tuple | Mapping) and len(value) == 0
