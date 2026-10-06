"""Scene aggregate and scene-domain value objects."""

from __future__ import annotations

import base64
import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, TypeVar

_TAttributeItem = TypeVar("_TAttributeItem")

# (major, minor). Bump major when a field is removed, renamed, or changes meaning; bump minor when a field is added.
# Decoder plugins declare the version they build Scenes for, and derived caches are keyed on it.
SCENE_MODEL_VERSION: Tuple[int, int] = (1, 0)


# ---------------------------------------------------------------------------#
# Definitions                                                                #
# ---------------------------------------------------------------------------#
class EntityId(str):
    """A unique identifier for an entity in the world model."""

    def __new__(cls, value: str) -> EntityId:
        if not value:
            raise ValueError("EntityId cannot be empty")
        return super().__new__(cls, value)


# ---------------------------------------------------------------------------#
# Enumerations                                                               #
# ---------------------------------------------------------------------------#
# Dynamic classification type
ClassificationType = str


class KnownClassificationType(enum.Enum):
    """Known classification types for convenience."""

    Human = "human"
    Vehicle = "vehicle"
    VehicleOther = "vehicle_other"
    Car = "car"
    Bus = "bus"
    Truck = "truck"
    Bike = "bike"
    Bicycle = "bicycle"
    Animal = "animal"
    Head = "head"
    LicensePlate = "license_plate"
    Face = "face"
    Bag = "bag"
    Unknown = "unknown"


# Dynamic color type
ColorType = str


class MotionState(enum.Enum):
    """Motion state reported for an entity."""

    Moving = "moving"
    Stationary = "stationary"
    Unknown = "unknown"


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
# Primitive value objects                                                    #
# ---------------------------------------------------------------------------#
@dataclass(slots=True, frozen=True)
class Score:
    """A score of a classification, is not standardized to any specific range."""

    value: float

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "value", state[0])


@dataclass(slots=True, frozen=True)
class NormalizedPoint:
    """A point normalized to the range [0, 1]."""

    x: float
    y: float
    allow_outside: bool = False  # Allow values outside [0, 1]

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "x", state[0])
        object.__setattr__(self, "y", state[1])
        object.__setattr__(self, "allow_outside", state[2])

    def __post_init__(self) -> None:
        if self.allow_outside:
            return

        # Ensure x and y are in the range [0, 1]
        if not (0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0):
            raise ValueError(f"NormalizedPoint must have x and y in [0, 1], got ({self.x}, {self.y})")


@dataclass(slots=True, frozen=True)
class RGB:
    """RGB color values with validation."""

    r: int
    g: int
    b: int

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "r", state[0])
        object.__setattr__(self, "g", state[1])
        object.__setattr__(self, "b", state[2])

    def __post_init__(self) -> None:
        if not 0 <= self.r <= 255:
            raise ValueError(f"Red values must be 0-255, got {self.r}")
        if not 0 <= self.g <= 255:
            raise ValueError(f"Green values must be 0-255, got {self.g}")
        if not 0 <= self.b <= 255:
            raise ValueError(f"Blue values must be 0-255, got {self.b}")


@dataclass(slots=True, frozen=True)
class ColorClassification:
    """Color with dynamic name, RGB values, and confidence score."""

    name: ColorType
    rgb: RGB
    score: Score

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "name", state[0])
        object.__setattr__(self, "rgb", state[1])
        object.__setattr__(self, "score", state[2])

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Color name cannot be empty")
        if not isinstance(self.rgb, RGB):
            raise TypeError("RGB must be an instance of RGB")
        if not isinstance(self.score, Score):
            if isinstance(self.score, (int, float)):
                self.score = Score(float(self.score))
            else:
                raise TypeError("Score must be an instance of Score")


@dataclass(slots=True, frozen=True)
class Attribute:
    """Generic attribute for any entity classification."""

    name: str
    value: Any

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "name", state[0])
        object.__setattr__(self, "value", state[1])


# ---------------------------------------------------------------------------#
# Geometry                                                                   #
# ---------------------------------------------------------------------------#
@dataclass(slots=True, frozen=True)
class Geometry:
    """Base for geometric primitives in normalized image space."""

    def as_xywh(self) -> Tuple[float, float, float, float]:
        """Return ``(x, y, width, height)`` bounds in normalized image coordinates."""
        raise NotImplementedError

    def polygon_points(self) -> List["NormalizedPoint"] | None:
        """Return polygon points when this geometry has an explicit polygon shape."""
        return None


@dataclass(slots=True, frozen=True)
class BoundingBox(Geometry):
    """Axis-aligned bounding box in normalized image space."""

    top_left: NormalizedPoint
    bottom_right: NormalizedPoint

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "top_left", state[0])
        object.__setattr__(self, "bottom_right", state[1])

    # Convenience ------------------------------------------------------------------
    @property
    def width(self) -> float:
        return self.bottom_right.x - self.top_left.x

    @property
    def height(self) -> float:
        return self.bottom_right.y - self.top_left.y

    @property
    def area(self) -> float:
        return self.width * self.height

    # Constructors -----------------------------------------------------------------
    @classmethod
    def from_xywh(cls, x: float, y: float, w: float, h: float, allow_outside: bool = False) -> "BoundingBox":
        return cls(
            top_left=NormalizedPoint(x, y, allow_outside=allow_outside),
            bottom_right=NormalizedPoint(x + w, y + h, allow_outside=allow_outside),
        )

    # Helpers ----------------------------------------------------------------------
    def as_xywh(self) -> Tuple[float, float, float, float]:
        """Return (x, y, width, height)."""
        return self.top_left.x, self.top_left.y, self.width, self.height


@dataclass(slots=True, frozen=True)
class Polygon(Geometry):
    """Polygon in normalized image space."""

    points: List[NormalizedPoint]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("Polygon must contain at least 3 points.")

    def as_xywh(self) -> Tuple[float, float, float, float]:
        """Return ``(x, y, width, height)`` bounds in normalized image coordinates."""
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        min_x = min(xs)
        min_y = min(ys)
        max_x = max(xs)
        max_y = max(ys)
        return min_x, min_y, max_x - min_x, max_y - min_y

    def polygon_points(self) -> List[NormalizedPoint]:
        """Return this polygon's normalized points."""
        return self.points


# ---------------------------------------------------------------------------#
# Image & motion                                                             #
# ---------------------------------------------------------------------------#
@dataclass(slots=True)
class Image:
    timestamp: datetime
    bounding_box: BoundingBox
    data: bytes

    @classmethod
    def from_base64(
        cls,
        timestamp: datetime,
        bounding_box: BoundingBox,
        base64_data: str,
    ) -> "Image":
        return cls(
            timestamp=timestamp,
            bounding_box=bounding_box,
            data=base64.b64decode(base64_data),
        )


@dataclass(slots=True)
class ImageVelocity:
    vx: float
    vy: float


# ---------------------------------------------------------------------------#
# Coordinate frames                                                          #
# ---------------------------------------------------------------------------#
@dataclass(slots=True)
class GeographicCoordinates:
    latitude: float
    longitude: float
    elevation: Optional[float] = None  # metres


@dataclass(slots=True)
class SphericalCoordinates:
    distance: float
    azimuth: float
    elevation: float


@dataclass(slots=True)
class WorldVelocity:
    pitch: float
    speed: float
    yaw: float


# ---------------------------------------------------------------------------#
# Classification hierarchy                                                   #
# ---------------------------------------------------------------------------#
@dataclass(slots=True)
class Classification:
    """Classification with dynamic type and flexible attributes."""

    type: ClassificationType  # str alias
    score: Score
    attributes: List[Attribute] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("Classification type cannot be empty")
        if not isinstance(self.score, Score):
            if isinstance(self.score, (int, float)):
                self.score = Score(float(self.score))
            else:
                raise TypeError("Score must be an instance of Score")
        if not isinstance(self.attributes, list):
            raise TypeError("Attributes must be a list of Attribute instances")

    def attribute_value(self, attr_name: str) -> Any | None:
        """Return a classification attribute value by name."""
        for attr in self.attributes:
            if attr.name == attr_name:
                return attr.value
        return None

    def attribute_items(self, attr_name: str, item_type: Type[_TAttributeItem]) -> List[_TAttributeItem]:
        """Return typed list items from a classification attribute."""
        value = self.attribute_value(attr_name)
        if not isinstance(value, list):
            return []
        items: List[_TAttributeItem] = []
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
@dataclass(slots=True)
class Observation:
    """Observation of an entity at a specific time."""

    geometry: Geometry
    classification: List[Classification] = field(default_factory=list)
    timestamp: Optional[datetime] = None
    frame_number: Optional[int] = None
    confidence: Optional[Score] = None
    velocity_in_image_space: Optional[ImageVelocity] = None
    velocity_in_world_space: Optional[WorldVelocity] = None
    geographical_coordinates: Optional[GeographicCoordinates] = None
    world_coordinates: Optional[SphericalCoordinates] = None
    # Decoder-owned, free-form, picklable; ax-devil only displays it.
    debug: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp is None and self.frame_number is None:
            raise ValueError("Observation must have either a timestamp or frame number.")

    @property
    def primary_classification(self) -> Classification | None:
        """Return the highest-score classification for this observation."""
        return max(self.classification, key=lambda item: item.score.value, default=None)


# ---------------------------------------------------------------------------#
# Entity hierarchy                                                           #
# ---------------------------------------------------------------------------#
@dataclass(slots=True)
class Entity:
    """Track of a real-world entity."""

    id: EntityId
    observations: List[Observation] = field(default_factory=list)
    images: List[Image] = field(default_factory=list)
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
    def involved_entity_ids(self) -> Tuple[EntityId, ...]:
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
    def involved_entity_ids(self) -> Tuple[EntityId, ...]:
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
    def involved_entity_ids(self) -> Tuple[EntityId, ...]:
        """Return the entities this event involves."""
        return (self.from_entity_id, self.to_entity_id)


@dataclass(slots=True)
class Merge(Operation):
    """Merge multiple entities into one."""

    entity_ids: List[EntityId]
    target_entity_id: EntityId
    operation_type: ClassVar[OperationType] = OperationType.Merge

    @property
    def label(self) -> str:
        """Return a one-line description for event lists."""
        return f"{self.kind} {', '.join(self.entity_ids)} → {self.target_entity_id}"

    @property
    def involved_entity_ids(self) -> Tuple[EntityId, ...]:
        """Return the entities this event involves."""
        return (*self.entity_ids, self.target_entity_id)


@dataclass(slots=True)
class Split(Operation):
    """Split an entity into multiple entities."""

    source_entity_id: EntityId
    new_entity_ids: List[EntityId]
    operation_type: ClassVar[OperationType] = OperationType.Split

    @property
    def label(self) -> str:
        """Return a one-line description for event lists."""
        return f"{self.kind} {self.source_entity_id} → {', '.join(self.new_entity_ids)}"

    @property
    def involved_entity_ids(self) -> Tuple[EntityId, ...]:
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
    entities: Dict[EntityId, Entity] = field(default_factory=dict)
    events: List[Event] = field(default_factory=list)
    relations: set[EntityRelation] = field(default_factory=set)
    # Decoder-owned, free-form, picklable; ax-devil only displays it.
    debug: Dict[str, Any] = field(default_factory=dict)

    def add_entity(self, entity: Entity) -> None:
        """Register a fully-constructed entity."""
        self.entities[entity.id] = entity

    def add_event(self, event: Event) -> None:
        self.events.append(event)

    def add_relation(self, relation: EntityRelation) -> None:
        """Register a relationship between two Scene entities."""
        self.relations.add(relation)
