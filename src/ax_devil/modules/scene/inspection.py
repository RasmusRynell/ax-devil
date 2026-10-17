"""World-model inspection: the grouped, queryable view of an entity's fields and its one-line summary."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ax_devil.modules.scene.model import (
    Attribute,
    Classification,
    ColorClassification,
    Entity,
    Geometry,
    Observation,
    Score,
)
from ax_devil.modules.scene.query import FieldValue, QueryRecord


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

    ``fraction`` is set for scores in [0, 1] so a view can draw them as a bar.
    """

    path: tuple[str, ...]
    value: FieldValue
    fraction: float | None = None


@dataclass(frozen=True, slots=True)
class EntitySummary:
    """The few facts shown about an entity in lists and hover chips."""

    entity_id: str
    class_name: str
    conf: float | None
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
            grouped[group].extend(_flatten(value, prefix))
    return tuple((group, tuple(items)) for group, items in grouped.items() if items)


def entity_summary(entity: Entity) -> EntitySummary:
    """Summarize an entity for lists and hover chips; speed is only what the decoder reported."""
    observation = entity.latest_observation
    primary = observation.primary_classification if observation is not None else None
    velocity = observation.velocity_in_world_space if observation is not None else None
    conf = _confidence(observation) if observation is not None else None
    return EntitySummary(
        entity_id=str(entity.id),
        class_name=primary.type if primary is not None else _UNKNOWN_CLASS,
        conf=conf.value if conf is not None else None,
        speed=velocity.speed if velocity is not None else None,
        state=entity.motion_state.value if entity.motion_state is not None else None,
    )


def entity_record(entity: Entity) -> QueryRecord:
    """Return the entity as a query record keyed ``group.path`` (``debug.latency``); free text is id, class, state."""
    values: dict[str, list[FieldValue]] = {}
    for group, items in entity_fields(entity):
        for item in items:
            values.setdefault(".".join((group.name.lower(), *item.path)), []).append(item.value)
    summary = entity_summary(entity)
    text = (summary.entity_id, summary.class_name, *(() if summary.state is None else (summary.state,)))
    return QueryRecord(fields={key: tuple(items) for key, items in values.items()}, text=text)


def _confidence(observation: Observation) -> Score | None:
    if observation.confidence is not None:
        return observation.confidence
    primary = observation.primary_classification
    return primary.score if primary is not None else None


def _fraction(score: Score) -> float | None:
    return score.value if 0.0 <= score.value <= 1.0 else None


def _classification_fields(observation: Observation) -> Iterator[InspectionField]:
    primary = observation.primary_classification
    if primary is not None:
        yield InspectionField(("type",), primary.type)
    conf = _confidence(observation)
    if conf is not None:
        yield InspectionField(("conf",), conf.value, _fraction(conf))
    if primary is not None:
        yield from _flatten(primary.attributes, ("attributes",))
    candidates = sorted(observation.classification, key=lambda item: item.score.value, reverse=True)
    yield from _flatten(candidates, ("candidates",))


def _flatten_items(items: Iterable[tuple[str, Any]]) -> Iterator[InspectionField]:
    for name, value in items:
        yield from _flatten(value, (name,))


def _flatten(value: Any, path: tuple[str, ...]) -> Iterator[InspectionField]:
    """Yield every populated leaf below ``value``; named values (attributes, classifications) extend the path."""
    if _is_empty(value):
        return
    if isinstance(value, Enum):
        yield from _flatten(value.value, path)
    elif isinstance(value, bool | int | float | str):
        yield InspectionField(path, value)
    elif isinstance(value, Score):
        yield InspectionField(path, value.value, _fraction(value))
    elif isinstance(value, Attribute):
        yield from _flatten(value.value, (*path, value.name))
    elif isinstance(value, Classification):
        yield from _flatten(value.score, (*path, value.type))
        yield from _flatten(value.confidence, (*path, value.type, "confidence"))
        yield from _flatten(value.attributes, (*path, value.type, "attributes"))
    elif isinstance(value, ColorClassification):
        yield from _flatten(value.score, (*path, value.name))
        yield from _flatten(value.rgb, (*path, value.name, "rgb"))
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
