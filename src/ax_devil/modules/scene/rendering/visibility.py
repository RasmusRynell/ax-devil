"""Per-view choices specialized into catalog programs before frame rendering."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum


class OverlayFeature(str, Enum):
    """Stable semantic groups that catalogs may expose to viewers."""

    OUTLINES = "outlines"
    IDS = "ids"
    CLASS_NAMES = "class_names"
    CONFIDENCE = "confidence"
    SPEED = "speed"
    MOVEMENT = "movement"
    ATTRIBUTES = "attributes"
    RELATIONS = "relations"

    @property
    def label(self) -> str:
        """Return the user-facing control label."""
        return {
            self.OUTLINES: "Object outlines",
            self.IDS: "Object IDs",
            self.CLASS_NAMES: "Class names",
            self.CONFIDENCE: "Confidence",
            self.SPEED: "Speed arrows",
            self.MOVEMENT: "Movement badges",
            self.ATTRIBUTES: "Attributes",
            self.RELATIONS: "Relations",
        }[self]

    @property
    def description(self) -> str:
        """Describe the information controlled by this group."""
        return {
            self.OUTLINES: "Bounding boxes, polygons and their associated fills.",
            self.IDS: "Displayed object identifiers.",
            self.CLASS_NAMES: "Classification text.",
            self.CONFIDENCE: "Confidence numbers, bars and their backgrounds.",
            self.SPEED: "Velocity arrows and their heads.",
            self.MOVEMENT: "Motion-state symbols.",
            self.ATTRIBUTES: "Attribute badges, color swatches and related indicators.",
            self.RELATIONS: "Relationship lines and endpoint markers.",
        }[self]


@dataclass(frozen=True, slots=True)
class OverlayVisibility:
    """Immutable user choices; an empty disabled set permits every catalog feature."""

    disabled: frozenset[OverlayFeature] = frozenset()

    def with_feature(self, feature: OverlayFeature, enabled: bool) -> "OverlayVisibility":
        """Return choices with one feature enabled or disabled."""
        return replace(self, disabled=self.disabled - {feature} if enabled else self.disabled | {feature})

    def with_choices(self, choices: Mapping[OverlayFeature, bool]) -> "OverlayVisibility":
        """Return these choices with *choices*, features the user turned on or off, applied on top."""
        disabled = (self.disabled - {f for f, on in choices.items() if on}) | {f for f, on in choices.items() if not on}
        return replace(self, disabled=frozenset(disabled))

    def choices_since(
        self, start: "OverlayVisibility", earlier: Mapping[OverlayFeature, bool]
    ) -> dict[OverlayFeature, bool]:
        """Return what the user has chosen, given that a view began at *start* with *earlier* choices applied.

        Features that differ from *start* are choices; earlier choices that still hold are kept, so a choice survives a
        view that happened to start the same way.
        """
        current = {feature: feature not in self.disabled for feature in OverlayFeature}
        kept = {feature: on for feature, on in earlier.items() if current[feature] == on}
        changed = {feature: on for feature, on in current.items() if (feature not in start.disabled) != on}
        return {**kept, **changed}
