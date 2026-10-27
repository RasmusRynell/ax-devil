"""World-model inspection: the grouped, queryable view of an entity's fields and its one-line summary."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    Classification,
    ColorClassification,
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


@dataclass(frozen=True, slots=True)
class InspectionField:
    """One leaf value of an entity, addressed by its path inside a ``FieldGroup``.

    ``fraction`` is set for a score or confidence whose value lies in [0, 1], only so a view can draw a bar. ``rgb``
    is set on a color entry, whose path ends in its name and whose value is its score.
    """

    path: tuple[str, ...]
    value: FieldValue
    fraction: float | None = None
    rgb: RGB | None = None

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
    score: float | None  # The primary classification's score.
    confidence: float | None  # Only what the observation reports as a confidence.
    speed: float | None
    state: str | None


# Observation fields that are not "raw": where they are shown. Classification and confidence are built by
# ``_classification_fields``. Geometry and debug paths are not prefixed with their field name.
_OBSERVATION_GROUPS = {
    "classification": FieldGroup.CLASSIFICATION,
    "confidence": FieldGroup.CLASSIFICATION,
    "geometry": FieldGroup.GEOMETRY,
    "geographical_coordinates": FieldGroup.GEOMETRY,
    "world_coordinates": FieldGroup.GEOMETRY,
    "velocity_in_image_space": FieldGroup.MOTION,
    "velocity_in_world_space": FieldGroup.MOTION,
    "debug": FieldGroup.DEBUG,
}
_UNPREFIXED_OBSERVATION_FIELDS = frozenset({"geometry", "debug"})
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
        for name, value in _dataclass_items(observation, skip={"classification", "confidence"}):
            prefix = () if name in _UNPREFIXED_OBSERVATION_FIELDS else (name,)
            group = _OBSERVATION_GROUPS.get(name, FieldGroup.RAW)
            grouped[group].extend((_debug_leaves if name == "debug" else _flatten)(value, prefix))
    return tuple((group, tuple(items)) for group, items in grouped.items() if items)


def entity_score(entity: Entity) -> float | None:
    """Return the score of an entity's primary classification, as its summary shows it."""
    observation = entity.latest_observation
    primary = observation.primary_classification if observation is not None else None
    return primary.score.value if primary is not None else None


def entity_summary(entity: Entity) -> EntitySummary:
    """Summarize an entity for lists and hover chips; speed is only what the decoder reported."""
    observation = entity.latest_observation
    primary = observation.primary_classification if observation is not None else None
    velocity = observation.velocity_in_world_space if observation is not None else None
    confidence = observation.confidence if observation is not None else None
    return EntitySummary(
        entity_id=str(entity.id),
        class_name=primary.type if primary is not None else _UNKNOWN_CLASS,
        score=primary.score.value if primary is not None else None,
        confidence=confidence.value if confidence is not None else None,
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


def _fraction(value: float) -> float | None:
    return value if 0.0 <= value <= 1.0 else None


def _classification_fields(observation: Observation) -> Iterator[InspectionField]:
    primary = observation.primary_classification
    if primary is not None:
        yield InspectionField(("type",), primary.type)
        yield InspectionField(("score",), primary.score.value, _fraction(primary.score.value))
    if observation.confidence is not None:
        yield InspectionField(("confidence",), observation.confidence.value, _fraction(observation.confidence.value))
    if primary is not None:
        yield from _flatten(primary.attributes, ("attributes",))
    candidates = sorted(observation.classification, key=lambda item: item.score.value, reverse=True)
    for candidate in candidates:
        if candidate is primary:  # Its attributes are already listed under ``attributes``.
            yield from _flatten(candidate.score, ("candidates", candidate.type))
        else:
            yield from _flatten(candidate, ("candidates",))


def _flatten_items(items: Iterable[tuple[str, Any]]) -> Iterator[InspectionField]:
    for name, value in items:
        yield from _flatten(value, (name,))


def _debug_leaves(value: Any, path: tuple[str, ...]) -> Iterator[InspectionField]:
    """Yield every debug leaf; reported empty values stay as ``null``, ``""``, ``[]`` or ``{}``, bytes as hex."""
    if value is None:
        yield InspectionField(path, "null")
    elif isinstance(value, str):
        yield InspectionField(path, value)
    elif isinstance(value, bytes):
        shown = value[:_MAX_DEBUG_BYTES].hex()
        yield InspectionField(path, shown if len(value) <= _MAX_DEBUG_BYTES else f"{shown}... ({len(value)} bytes)")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _debug_leaves(item, (*path, str(key)))
        if not value:
            yield InspectionField(path, "{}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            yield from _debug_leaves(item, (*path, str(index)))
        if not value:
            yield InspectionField(path, "[]")
    else:
        yield from _flatten(value, path)


def _flatten(value: Any, path: tuple[str, ...]) -> Iterator[InspectionField]:
    """Yield every populated leaf below ``value``; named values (attributes, classifications) extend the path."""
    if _is_empty(value):
        return
    if isinstance(value, Enum):
        yield from _flatten(value.value, path)
    elif isinstance(value, bool | int | float | str):
        yield InspectionField(path, value)
    elif isinstance(value, Score):
        yield InspectionField(path, value.value, _fraction(value.value))
    elif isinstance(value, Attribute):
        yield from _flatten(value.value, (*path, value.name))
    elif isinstance(value, Classification):
        yield from _flatten(value.score, (*path, value.type))
        yield from _flatten(value.attributes, (*path, value.type, "attributes"))
    elif isinstance(value, ColorClassification):
        yield InspectionField((*path, value.name), value.score.value, _fraction(value.score.value), value.rgb)
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
