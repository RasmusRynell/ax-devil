"""Tests for shared scene decoding helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ax_devil.modules.scene.decoding import (
    parse_bounding_box,
    parse_colors_webcolors,
    parse_timestamp,
    rgb_from_map,
)
from ax_devil.modules.scene.model import RGB, Score


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        ("2025-01-02T03:04:05Z", datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
        ("2025-01-02T03:04:05+02:00", datetime.fromisoformat("2025-01-02T03:04:05+02:00")),
        ("2025-01-02T03:04:05", datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
    ],
)
def test_parse_timestamp_preserves_instant_and_timezone(timestamp: str, expected: datetime) -> None:
    parsed = parse_timestamp(timestamp)
    assert parsed == expected
    assert parsed.tzinfo == expected.tzinfo


@pytest.mark.parametrize("timestamp", [None, ""])
def test_parse_timestamp_rejects_empty_values(timestamp: str | None) -> None:
    with pytest.raises(ValueError, match="Timestamp string is empty or None"):
        parse_timestamp(timestamp)


def test_parse_timestamp_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="Invalid timestamp format"):
        parse_timestamp("not-a-timestamp")


def test_parse_bounding_box_accepts_lowercase_keys() -> None:
    bbox = parse_bounding_box({"left": 0.1, "top": 0.2, "right": 0.7, "bottom": 0.8})

    assert bbox is not None
    assert bbox.top_left.x == 0.1
    assert bbox.top_left.y == 0.2
    assert bbox.bottom_right.x == 0.7
    assert bbox.bottom_right.y == 0.8


def test_parse_bounding_box_accepts_uppercase_keys() -> None:
    bbox = parse_bounding_box({"Left": "0.1", "Top": "0.2", "Right": "0.7", "Bottom": "0.8"})

    assert bbox is not None
    assert bbox.top_left.x == 0.1
    assert bbox.top_left.y == 0.2
    assert bbox.bottom_right.x == 0.7
    assert bbox.bottom_right.y == 0.8


def test_parse_bounding_box_allows_missing_box() -> None:
    assert parse_bounding_box(None) is None


def test_parse_bounding_box_rejects_missing_coordinate() -> None:
    with pytest.raises(ValueError, match="Bounding box missing right value"):
        parse_bounding_box({"left": 0.1, "top": 0.2, "bottom": 0.8})


def test_rgb_from_map_returns_known_color() -> None:
    assert rgb_from_map("RED") == RGB(255, 0, 0)


def test_rgb_from_map_rejects_unknown_color() -> None:
    with pytest.raises(ValueError, match="Color 'chartreuse-ish' not found in color map"):
        rgb_from_map("chartreuse-ish")


def test_parse_colors_webcolors_converts_payloads() -> None:
    colors = parse_colors_webcolors(
        [
            {"name": "red", "score": 0.75},
            {"name": "blue", "score": 0.25},
        ]
    )

    assert [color.name for color in colors] == ["red", "blue"]
    assert [color.rgb for color in colors] == [RGB(255, 0, 0), RGB(0, 0, 255)]
    assert [color.score for color in colors] == [Score(0.75), Score(0.25)]


def test_parse_colors_webcolors_rejects_missing_payload() -> None:
    with pytest.raises(ValueError, match="Color data is None"):
        parse_colors_webcolors(None)
