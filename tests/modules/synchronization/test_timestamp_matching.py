"""Tests for shared timestamp matching policy."""

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


def test_find_matching_overlay_timestamp_rejects_future_outside_tolerance() -> None:
    """Future overlay timestamps outside tolerance should not match."""
    result = find_matching_overlay_timestamp(
        sorted_overlay_timestamps_us=(1_286_000,),
        video_timestamp_us=1_280_000,
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert result.match_type == "missing"
    assert result.overlay_timestamp_us is None


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


def test_count_tolerated_past_overlay_timestamps_excludes_exact_matches() -> None:
    """Tolerance count should only include non-exact past matches."""
    count = count_tolerated_past_overlay_timestamps(
        video_timestamps_us=(1_000, 2_000, 3_000),
        sorted_overlay_timestamps_us=(1_000, 1_999, 2_300, 4_000),
        policy=TimestampFallbackPolicy(tolerance_us=500),
    )

    assert count == 1


def test_count_tolerated_past_overlay_timestamps_counts_only_runtime_selectable_overlays() -> None:
    """Tolerance count should not include older overlays hidden behind the latest previous overlay."""
    count = count_tolerated_past_overlay_timestamps(
        video_timestamps_us=(1_000,),
        sorted_overlay_timestamps_us=(998, 999),
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert count == 1


def test_count_tolerated_past_overlay_timestamps_does_not_double_count_exact_overlay() -> None:
    """An exact overlay selected by a later frame should still count only as exact elsewhere."""
    count = count_tolerated_past_overlay_timestamps(
        video_timestamps_us=(1_000, 1_001),
        sorted_overlay_timestamps_us=(1_000,),
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert count == 0


def test_count_tolerated_past_overlay_timestamps_rejects_overlay_after_video() -> None:
    """Overlay timestamps after a video timestamp should not count as tolerated."""
    count = count_tolerated_past_overlay_timestamps(
        video_timestamps_us=(1_000,),
        sorted_overlay_timestamps_us=(1_001,),
        policy=TimestampFallbackPolicy(tolerance_us=5_000),
    )

    assert count == 0
