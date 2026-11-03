"""Numeric ranges over what a decoder reports about a classification: its score and its confidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from ax_devil.modules.scene.inspection import latest_primary_classification
from ax_devil.modules.scene.model import Classification, Entity


class Measure(str, Enum):
    """A number a classification can carry, filterable by range."""

    SCORE = "Score"
    CONFIDENCE = "Confidence"

    def of(self, classification: Classification) -> float | None:
        """Return the classification's raw value for this measure, or None when the decoder gave none."""
        reported = classification.score if self is Measure.SCORE else classification.confidence
        return reported.value if reported is not None else None


@dataclass(frozen=True, slots=True)
class MeasureRange:
    """An inclusive range; an open end is None. A range judges only values that were reported."""

    minimum: float | None = None
    maximum: float | None = None

    def keeps(self, value: float | None) -> bool:
        """Return whether *value* lies in the range; a missing value is kept, not treated as 0."""
        if value is None:
            return True
        return (self.minimum is None or value >= self.minimum) and (self.maximum is None or value <= self.maximum)


def entity_in_ranges(entity: Entity, ranges: Mapping[Measure, MeasureRange]) -> bool:
    """Return whether the entity's latest primary classification is within every range."""
    primary = latest_primary_classification(entity)
    if primary is None:
        return True
    return all(measure_range.keeps(measure.of(primary)) for measure, measure_range in ranges.items())
