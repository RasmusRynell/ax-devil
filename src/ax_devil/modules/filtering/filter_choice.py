"""A filter's choices in a form that can be saved and carried between decoders."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .measure_range import Measure, MeasureRange


@dataclass(frozen=True)
class FilterChoice:
    """What a user chose in a filter, independent of the decoder it was chosen for.

    A filter carries the classes switched away from their decoder's default, matched by option id, and the Score and
    Confidence ranges as plain numbers; Clear returns to the decoder's defaults. *ranges* holds only ranges with a
    bound.
    """

    classes: frozenset[tuple[str, bool]] = frozenset()
    ranges: frozenset[tuple[Measure, MeasureRange]] = frozenset()

    @classmethod
    def from_config(cls, value: object) -> FilterChoice:
        """Return the choice saved as *value*; raise ValueError when it is malformed."""
        if not isinstance(value, dict) or set(value) != {"classes", "ranges"}:
            raise ValueError(f"Invalid object filter {value!r}")
        classes, ranges = value["classes"], value["ranges"]
        if not isinstance(classes, dict) or not isinstance(ranges, dict):
            raise ValueError(f"Invalid object filter {value!r}")
        for option_id, enabled in classes.items():
            if not isinstance(option_id, str) or not isinstance(enabled, bool):
                raise ValueError(f"Invalid object filter class {option_id!r}: {enabled!r}")
        parsed = {Measure(name): _range_from_config(bounds) for name, bounds in ranges.items()}
        return cls(
            classes=frozenset(classes.items()),
            ranges=frozenset((measure, bounds) for measure, bounds in parsed.items() if bounds != MeasureRange()),
        )

    def config_value(self) -> dict[str, object]:
        """Return the choice as the JSON object saved under ``settings.view_defaults.object_filter``."""
        return {
            "classes": dict(sorted(self.classes)),
            "ranges": {
                measure.value: {"minimum": bounds.minimum, "maximum": bounds.maximum}
                for measure, bounds in sorted(self.ranges, key=lambda item: item[0].value)
            },
        }

    def range(self, measure: Measure) -> MeasureRange:
        """Return the range chosen for *measure*; unbounded when none."""
        return next((bounds for kept, bounds in self.ranges if kept is measure), MeasureRange())


def _range_from_config(value: object) -> MeasureRange:
    if not isinstance(value, dict) or set(value) != {"minimum", "maximum"}:
        raise ValueError(f"Invalid range {value!r}")
    minimum, maximum = _bound_from_config(value["minimum"]), _bound_from_config(value["maximum"])
    return MeasureRange(minimum, maximum)


def _bound_from_config(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid range bound {value!r}")
    return float(value)
