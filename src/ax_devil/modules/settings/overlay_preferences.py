"""Preferences for what viewers show over the video, on by default."""

from __future__ import annotations

from enum import Enum


class OverlayPreference(str, Enum):
    """A closed set of on/off overlay preferences; the member value is its config key."""

    LANE_NAMES = "lane_names_enabled"
    HOVER = "hover_enabled"

    @property
    def label(self) -> str:
        """Return the display label shared by the View menu and Settings."""
        labels = {
            OverlayPreference.LANE_NAMES: "Show Lane Names",
            OverlayPreference.HOVER: "Object Hover and Click Details",
        }
        return labels[self]

    @property
    def description(self) -> str:
        """Describe what the preference shows."""
        descriptions = {
            OverlayPreference.LANE_NAMES: "Name each video lane; names hide while zoomed in or showing frame info.",
            OverlayPreference.HOVER: "Highlight the object under the pointer and show its details; click to pin them.",
        }
        return descriptions[self]

    @classmethod
    def from_config(cls, value: object) -> dict[OverlayPreference, bool]:
        """Read preferences from ``{key: bool}``; a missing key leaves its preference on."""
        saved = value if isinstance(value, dict) else {}
        return {preference: bool(saved.get(preference.value, True)) for preference in cls}

    @classmethod
    def config_value(cls, enabled: dict[OverlayPreference, bool]) -> dict[str, bool]:
        """Return the persisted form, listing every preference explicitly."""
        return {preference.value: enabled[preference] for preference in cls}
