"""Tests for shared timestamp matching policy."""

import pytest

from ax_devil.core.cancellation import NEVER
from ax_devil.modules.synchronization.timestamp_matching import (
    TimestampFallbackMode,
    TimestampFallbackPolicy,
    count_tolerated_past_overlay_timestamps,
    find_matching_overlay_timestamp,
)


def test_find_matching_overlay_timestamp_returns_exact_match() -> None:
    """Exact overlay timestamps should match before tolerance is considered."""
    result = find_matching_overlay_timestamp(
        sorted_overlay_timestamps_us=(1_000, 2_000),
        video_timestamp_us=1_000,
        policy=TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY),
    )

    assert result.overlay_index == 0
    assert result.overlay_timestamp_us == 1_000
    assert result.offset_us == 0
    assert result.match_type == "exact"


def test_find_matching_overlay_timestamp_rejects_future_within_tolerance() -> None:
    """Future overlay timestamps must not match, even within tolerance."""
    result = find_matching_overlay_timestamp(
        sorted_overlay_timestamps_us=(1_280_000,),
        video_timestamp_us=1_279_000,
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert result.match_type == "missing"
    assert result.overlay_timestamp_us is None


def test_find_matching_overlay_timestamp_accepts_past_overlay_timestamp() -> None:
    """Overlay timestamps at or before the video timestamp can match by tolerance."""
    result = find_matching_overlay_timestamp(
        sorted_overlay_timestamps_us=(1_279_000,),
        video_timestamp_us=1_280_000,
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert result.overlay_index == 0
    assert result.overlay_timestamp_us == 1_279_000
    assert result.offset_us == -1_000
    assert result.match_type == "tolerated_past"


def test_find_matching_overlay_timestamp_uses_latest_previous_overlay_timestamp() -> None:
    """The policy should use the latest overlay at or before the video timestamp."""
    result = find_matching_overlay_timestamp(
        sorted_overlay_timestamps_us=(1_278_000, 1_279_000, 1_281_000),
        video_timestamp_us=1_280_000,
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert result.overlay_index == 1
    assert result.overlay_timestamp_us == 1_279_000
    assert result.match_type == "tolerated_past"


def test_find_matching_overlay_timestamp_rejects_empty_overlay_timeline() -> None:
    """Empty overlay timelines should produce a missing match."""
    result = find_matching_overlay_timestamp(
        sorted_overlay_timestamps_us=(),
        video_timestamp_us=1_280_000,
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert result.match_type == "missing"


@pytest.mark.parametrize(
    ("video", "overlays", "tolerance_us", "expected"),
    [
        pytest.param((1_000, 2_000, 3_000), (1_000, 1_999, 2_300, 4_000), 500, 1, id="excludes_exact_matches"),
        pytest.param((1_000,), (998, 999), 5_000, 1, id="counts_only_latest_previous_overlay"),
        pytest.param((1_000, 1_001), (1_000,), 5_000, 0, id="exact_overlay_is_not_counted_for_later_frame"),
    ],
)
def test_count_tolerated_past_overlay_timestamps_counts_only_runtime_selectable_non_exact_matches(
    video: tuple[int, ...], overlays: tuple[int, ...], tolerance_us: int, expected: int
) -> None:
    """Only the past overlay a frame would actually select, and only when it is not an exact match, is counted."""
    count = count_tolerated_past_overlay_timestamps(
        video_timestamps_us=video,
        sorted_overlay_timestamps_us=overlays,
        policy=TimestampFallbackPolicy(tolerance_us=tolerance_us),
        token=NEVER,
    )

    assert count == expected
