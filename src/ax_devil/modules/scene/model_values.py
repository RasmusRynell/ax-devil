"""Scene value types: identifiers, classifications, geometry, colors and measurements.

Import them from ``ax_devil.modules.scene.model``, which re-exports them with the Scene aggregate.
"""

from __future__ import annotations

import base64
import enum
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any


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


# ---------------------------------------------------------------------------#
# Primitive value objects                                                    #
# ---------------------------------------------------------------------------#
@dataclass(slots=True, frozen=True)
class Score:
    """A decoder's own score for a classification: any finite number, in whatever range the decoder uses."""

    value: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError(f"Score must be finite, got {self.value}")

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "value", state[0])

    @property
    def text(self) -> str:
        """Return the score as a plain number, e.g. ``0.87``."""
        return f"{self.value:.2f}"

    @property
    def fraction(self) -> float | None:
        """Return the value when it lies within 0..1, else None."""
        return self.value if 0.0 <= self.value <= 1.0 else None


@dataclass(slots=True, frozen=True)
class Confidence:
    """A confidence a source reports for a classification: any finite number, in whatever range the source uses."""

    value: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError(f"Confidence must be finite, got {self.value}")

    def __setstate__(self, state: list[object]) -> None:
        """Restore the dataclass cache format without per-instance field introspection."""
        object.__setattr__(self, "value", state[0])

    @property
    def text(self) -> str:
        """Return the confidence as a plain number, e.g. ``0.87``."""
        return f"{self.value:.2f}"

    @property
    def fraction(self) -> float | None:
        """Return the value when it lies within 0..1, else None."""
        return self.value if 0.0 <= self.value <= 1.0 else None


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
    """Color with dynamic name, RGB values, and score."""

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

    def as_xywh(self) -> tuple[float, float, float, float]:
        """Return ``(x, y, width, height)`` bounds in normalized image coordinates."""
        raise NotImplementedError

    def polygon_points(self) -> list["NormalizedPoint"] | None:
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
    def as_xywh(self) -> tuple[float, float, float, float]:
        """Return (x, y, width, height)."""
        return self.top_left.x, self.top_left.y, self.width, self.height


@dataclass(slots=True, frozen=True)
class Polygon(Geometry):
    """Polygon in normalized image space."""

    points: list[NormalizedPoint]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("Polygon must contain at least 3 points.")

    def as_xywh(self) -> tuple[float, float, float, float]:
        """Return ``(x, y, width, height)`` bounds in normalized image coordinates."""
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        min_x = min(xs)
        min_y = min(ys)
        max_x = max(xs)
        max_y = max(ys)
        return min_x, min_y, max_x - min_x, max_y - min_y

    def polygon_points(self) -> list[NormalizedPoint]:
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
    elevation: float | None = None  # metres


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
