"""Shared helper functions for scene/annotation decoders."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ax_devil.modules.scene.model import (
    RGB,
    BoundingBox,
    ColorClassification,
    NormalizedPoint,
    Score,
)

__all__: list[str] = [
    "parse_timestamp",
    "rgb_from_map",
    "parse_colors_webcolors",
    "parse_bounding_box",
]

_COLOR_RGB_MAP = {
    "red": RGB(255, 0, 0),
    "green": RGB(0, 255, 0),
    "blue": RGB(0, 0, 255),
    "white": RGB(255, 255, 255),
    "black": RGB(0, 0, 0),
    "silver": RGB(192, 192, 192),
    "yellow": RGB(255, 255, 0),
    "orange": RGB(255, 165, 0),
    "gray": RGB(128, 128, 128),
    "grey": RGB(128, 128, 128),
    "brown": RGB(165, 42, 42),
    "pink": RGB(255, 192, 203),
    "purple": RGB(128, 0, 128),
    "cyan": RGB(0, 255, 255),
    "magenta": RGB(255, 0, 255),
    "beige": RGB(245, 245, 220),
}


def parse_timestamp(timestamp_str: str | None) -> datetime:
    """Return a timezone-aware ``datetime`` parsed from ISO-8601 text."""
    if not timestamp_str:
        raise ValueError("Timestamp string is empty or None")

    try:
        if timestamp_str.endswith("Z"):
            timestamp_str = timestamp_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(timestamp_str)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("Invalid timestamp format")


def rgb_from_map(name: str) -> RGB:
    """Return a curated colour -> ``RGB`` mapping with a neutral fallback."""
    if rgb := _COLOR_RGB_MAP.get(name.lower()):
        return rgb
    raise ValueError(f"Color '{name}' not found in color map")


def parse_colors_webcolors(raw: list[dict[str, Any]] | None) -> list[ColorClassification]:
    """Convert colour payload dictionaries into ``ColorClassification`` objects."""
    if raw is None:
        raise ValueError("Color data is None")
    colors = []
    for item in raw:
        colors.append(
            ColorClassification(name=item["name"], rgb=rgb_from_map(item["name"]), score=Score(item["score"]))
        )
    return colors


def parse_bounding_box(bbox_data: Mapping[str, Any] | None) -> BoundingBox | None:
    """Return a normalised ``BoundingBox`` from left/top/right/bottom payloads."""
    if bbox_data is None:
        return None

    left = _extract_coordinate(bbox_data, "left", "Left")
    top = _extract_coordinate(bbox_data, "top", "Top")
    right = _extract_coordinate(bbox_data, "right", "Right")
    bottom = _extract_coordinate(bbox_data, "bottom", "Bottom")

    return BoundingBox(
        top_left=NormalizedPoint(left, top),
        bottom_right=NormalizedPoint(right, bottom),
    )


def _extract_coordinate(bbox_data: Mapping[str, Any], lowercase: str, uppercase: str) -> float:
    """Pick a numeric value from the bounding box payload."""
    value = bbox_data.get(lowercase)
    if value is None:
        value = bbox_data.get(uppercase)
    if value is None:
        raise ValueError(f"Bounding box missing {lowercase} value")
    return float(value)
