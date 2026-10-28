"""Scene API for decoder plugins: the Scene aggregate and its entities, observations and events.

The value types it is built from (``EntityId``, ``Geometry``, ``RGB`` ...) are defined in ``model_values`` and are
imported from here.
"""

from __future__ import annotations

import builtins
import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, TypeVar

from ax_devil.core.time_units import datetime_to_us
from ax_devil.modules.scene.model_values import (
    RGB,
    Attribute,
    BoundingBox,
    ClassificationType,
    ColorClassification,
    ColorType,
    Confidence,
    EntityId,
    GeographicCoordinates,
    Geometry,
    Image,
    ImageVelocity,
    KnownClassificationType,
    MotionState,
    NormalizedPoint,
    Polygon,
    Score,
    SphericalCoordinates,
    WorldVelocity,
)

__all__ = [
    "Attribute",
    "BoundingBox",
    "Classification",
    "ClassificationType",
    "ColorClassification",
    "ColorType",
    "Confidence",
    "Delete",
    "Entity",
    "EntityId",
    "EntityRelation",
    "Event",
    "GeographicCoordinates",
    "Geometry",
    "Image",
    "ImageVelocity",
    "KnownClassificationType",
    "Merge",
    "MotionState",
    "NormalizedPoint",
    "Observation",
    "Operation",
    "OperationType",
    "Polygon",
    "RGB",
    "Rename",
    "SCENE_MODEL_VERSION",
    "Scene",
    "Score",
    "SphericalCoordinates",
    "Split",
    "TimeSlice",
    "WorldVelocity",
]

_TAttributeItem = TypeVar("_TAttributeItem")

# (major, minor). Bump major when a field is removed, renamed, or changes meaning; bump minor when a field is added.
# Decoder plugins declare the version they build Scenes for, and derived caches are keyed on it.
SCENE_MODEL_VERSION: tuple[int, int] = (3, 1)


class OperationType(enum.Enum):
    """Supported scene-operation types (Axis & ONVIF)."""

    Delete = "delete"
    Rename = "rename"
    Merge = "merge"
    Split = "split"

    @property
    def title(self) -> str:
        """Return the operation's name for event lists and filters."""
        return self.name


# ---------------------------------------------------------------------------#
# Classification hierarchy                                                   #
# ---------------------------------------------------------------------------#
@dataclass(slots=True)
class Classification:
    """Classification with the decoder's score and/or confidence (each None when the source lacks it) and attributes."""

    type: ClassificationType  # str alias
    score: Score | None = None
    attributes: list[Attribute] = field(default_factory=list)
    confidence: Confidence | None = None

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("Classification type cannot be empty")
        if self.score is not None and not isinstance(self.score, Score):
            raise TypeError("Score must be an instance of Score or None")
        if self.confidence is not None and not isinstance(self.confidence, Confidence):
            raise TypeError("Confidence must be an instance of Confidence or None")
        if not isinstance(self.attributes, list):
            raise TypeError("Attributes must be a list of Attribute instances")

    def attribute_value(self, attr_name: str) -> Any | None:
        """Return a classification attribute value by name."""
        for attr in self.attributes:
            if attr.name == attr_name:
                return attr.value
        return None

    def attribute_items(self, attr_name: str, item_type: builtins.type[_TAttributeItem]) -> list[_TAttributeItem]:
        """Return typed list items from a classification attribute."""
        value = self.attribute_value(attr_name)
        if not isinstance(value, list):
            return []
        items: list[_TAttributeItem] = []
        for item in value:
            if not isinstance(item, item_type):
                return []
            items.append(item)
        return items

    def optional_bool_attribute(self, attr_name: str) -> bool | None:
        """Return a bool attribute value when present."""
        value = self.attribute_value(attr_name)
        if isinstance(value, bool):
            return value
        return None

    def optional_float_attribute(self, attr_name: str) -> float | None:
        """Return a numeric attribute value as float."""
        value = self.attribute_value(attr_name)
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        return None


# ---- Concrete types --------------------------------------------------------#


# ---------------------------------------------------------------------------#
# Observation                                                                #
# ---------------------------------------------------------------------------#
def _ranking_key(item: Classification) -> tuple[bool, float, bool, float]:
    score, confidence = item.score, item.confidence
    return (
        score is not None,
        score.value if score is not None else 0.0,
        confidence is not None,
        confidence.value if confidence is not None else 0.0,
    )


@dataclass(slots=True)
class Observation:
    """Observation of an entity at a specific time."""

    geometry: Geometry
    classification: list[Classification] = field(default_factory=list)
    timestamp: datetime | None = None
    frame_number: int | None = None
    velocity_in_image_space: ImageVelocity | None = None
    velocity_in_world_space: WorldVelocity | None = None
    geographical_coordinates: GeographicCoordinates | None = None
    world_coordinates: SphericalCoordinates | None = None
    # Decoder-owned, free-form, picklable; ax-devil only displays it.
    debug: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp is None and self.frame_number is None:
            raise ValueError("Observation must have either a timestamp or frame number.")

    @property
    def ranked_classifications(self) -> list[Classification]:
        """Return the classifications best first: scored before unscored, then by score, then by confidence.

        Ties keep the decoder's order.
        """
        return sorted(self.classification, key=_ranking_key, reverse=True)

    @property
    def primary_classification(self) -> Classification | None:
        """Return the best-ranked classification for this observation."""
        ranked = self.ranked_classifications
        return ranked[0] if ranked else None


# ---------------------------------------------------------------------------#
# Entity hierarchy                                                           #
# ---------------------------------------------------------------------------#
@dataclass(slots=True)
class Entity:
    """Track of a real-world entity."""

    id: EntityId
    observations: list[Observation] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    end_reason: str | None = None
    motion_state: MotionState | None = None

    @property
    def latest_observation(self) -> Observation | None:
        """Return this entity's latest observation when present."""
        if not self.observations:
            return None
        return self.observations[-1]

    # Temporal helpers -----------------------------------------------------------
    @property
    def start_time(self) -> datetime:
        if not self.observations:
            return datetime.max.replace(tzinfo=timezone.utc)
        first_ts = self.observations[0].timestamp
        return first_ts if first_ts is not None else datetime.max.replace(tzinfo=timezone.utc)

    @property
    def end_time(self) -> datetime:
        if not self.observations:
            return datetime.min.replace(tzinfo=timezone.utc)
        last_ts = self.observations[-1].timestamp
        return last_ts if last_ts is not None else datetime.min.replace(tzinfo=timezone.utc)

    @property
    def duration(self) -> float:
        return (self.end_time - self.start_time).total_seconds()

    # Observation insertion (keeps list time-sorted) ----------------------------
    def add_observation(self, obs: Observation) -> None:
        idx = len(self.observations)
        if idx:
            has_timestamp = obs.timestamp is not None
            if has_timestamp:
                # Find insertion point using local variables for precise type narrowing
                while idx:
                    prev_obs = self.observations[idx - 1]
                    prev_ts = prev_obs.timestamp
                    new_ts = obs.timestamp
                    if prev_ts is not None and new_ts is not None and prev_ts > new_ts:
                        idx -= 1
                        continue
                    break
            else:
                # If no timestamp, use frame number for sorting
                while idx:
                    prev_obs = self.observations[idx - 1]
                    prev_fn = prev_obs.frame_number
                    new_fn = obs.frame_number
                    if prev_fn is not None and new_fn is not None and prev_fn > new_fn:
                        idx -= 1
                        continue
                    break
        self.observations.insert(idx, obs)


# ---------------------------------------------------------------------------#
# Events & temporal context                                                  #
# ---------------------------------------------------------------------------#
@dataclass(slots=True)
class TimeSlice:
    start: datetime | int  # Can be datetime or frame number
    end: datetime | int  # Can be datetime or frame number

    @property
    def duration(self) -> float:
        if isinstance(self.start, datetime) and isinstance(self.end, datetime):
            return (self.end - self.start).total_seconds()
        if isinstance(self.start, int) and isinstance(self.end, int):
            return float(self.end - self.start)
        # Mixed types are not comparable
        return 0.0

    @property
    def start_timestamp_us(self) -> int | None:
        """Start on the media clock in microseconds, or None for a frame-numbered slice."""
        if isinstance(self.start, datetime):
            return datetime_to_us(self.start)
        return None

    @classmethod
    def from_start_end(cls, start: datetime, end: datetime) -> "TimeSlice":
        return cls(start=start, end=end)

    @property
    def is_instant(self) -> bool:
        """True if the slice represents a single instant."""
        return self.start == self.end


@dataclass(slots=True)
class Event:
    """Base class for all events."""

    timestamp: TimeSlice

    @property
    def kind(self) -> str:
        """Return the kind of event, shared by all events of one type, for event filters."""
        raise NotImplementedError

    @property
    def label(self) -> str:
        """Return a one-line description for event lists."""
        raise NotImplementedError

    @property
    def involved_entity_ids(self) -> tuple[EntityId, ...]:
        """Return the entities this event involves."""
        raise NotImplementedError


@dataclass(slots=True)
class Operation(Event):
    """Base class for scene operations."""

    operation_type: ClassVar[OperationType]

    @property
    def kind(self) -> str:
        """Return the operation's name."""
        return self.operation_type.title


@dataclass(slots=True)
class Delete(Operation):
    """Delete an entity."""

    entity_id: EntityId
    operation_type: ClassVar[OperationType] = OperationType.Delete

    @property
    def label(self) -> str:
        """Return a one-line description for event lists."""
        return f"{self.kind} {self.entity_id}"

    @property
    def involved_entity_ids(self) -> tuple[EntityId, ...]:
        """Return the entities this event involves."""
        return (self.entity_id,)


@dataclass(slots=True)
class Rename(Operation):
    """Rename an entity."""

    from_entity_id: EntityId
    to_entity_id: EntityId
    operation_type: ClassVar[OperationType] = OperationType.Rename

    @property
    def label(self) -> str:
        """Return a one-line description for event lists."""
        return f"{self.kind} {self.from_entity_id} → {self.to_entity_id}"

    @property
    def involved_entity_ids(self) -> tuple[EntityId, ...]:
        """Return the entities this event involves."""
        return (self.from_entity_id, self.to_entity_id)


@dataclass(slots=True)
class Merge(Operation):
    """Merge multiple entities into one."""

    entity_ids: list[EntityId]
    target_entity_id: EntityId
    operation_type: ClassVar[OperationType] = OperationType.Merge

    @property
    def label(self) -> str:
        """Return a one-line description for event lists."""
        return f"{self.kind} {', '.join(self.entity_ids)} → {self.target_entity_id}"

    @property
    def involved_entity_ids(self) -> tuple[EntityId, ...]:
        """Return the entities this event involves."""
        return (*self.entity_ids, self.target_entity_id)


@dataclass(slots=True)
class Split(Operation):
    """Split an entity into multiple entities."""

    source_entity_id: EntityId
    new_entity_ids: list[EntityId]
    operation_type: ClassVar[OperationType] = OperationType.Split

    @property
    def label(self) -> str:
        """Return a one-line description for event lists."""
        return f"{self.kind} {self.source_entity_id} → {', '.join(self.new_entity_ids)}"

    @property
    def involved_entity_ids(self) -> tuple[EntityId, ...]:
        """Return the entities this event involves."""
        return (self.source_entity_id, *self.new_entity_ids)


# ---------------------------------------------------------------------------#
# Entity relations                                                           #
# ---------------------------------------------------------------------------#
@dataclass(slots=True, frozen=True)
class EntityRelation:
    """Directed semantic relationship between two entities in a Scene."""

    type: str
    source_entity_id: EntityId
    target_entity_id: EntityId

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("EntityRelation type cannot be empty")


@dataclass(slots=True)
class Scene:
    time_slice: TimeSlice
    entities: dict[EntityId, Entity] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    relations: set[EntityRelation] = field(default_factory=set)
    # Decoder-owned, free-form, picklable; retained for decoder/provider diagnostics, not displayed in the UI.
    debug: dict[str, Any] = field(default_factory=dict)

    def add_entity(self, entity: Entity) -> None:
        """Register a fully-constructed entity."""
        self.entities[entity.id] = entity

    def add_event(self, event: Event) -> None:
        self.events.append(event)

    def add_relation(self, relation: EntityRelation) -> None:
        """Register a relationship between two Scene entities."""
        self.relations.add(relation)
