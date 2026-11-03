"""Tests that whole-file analyses stop traversing as soon as their caller no longer wants the result."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ax_devil.core.cancellation import NEVER, Cancelled, CancelToken
from ax_devil.modules.data_sources.overlay_samples import OverlaySamples
from ax_devil.modules.data_sources.scene_history import SampleTrack, SceneHistory, SceneHistoryRecords
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, TimestampTimeline
from ax_devil.modules.synchronization.timestamp_matching import (
    TimestampFallbackPolicy,
    count_tolerated_past_overlay_timestamps,
)

_FRAMES = 200_000  # Far more frames than the few polls below, so only an early stop avoids walking all of them.
_POLLS_BEFORE_CANCEL = 20


class _CancelAfterPolls(CancelToken):
    """Token that is cancelled by its Nth poll and counts every poll, standing in for an owner that cancels mid-run."""

    def __init__(self, polls_before_cancel: int) -> None:
        super().__init__()
        self.polls = 0
        self._polls_before_cancel = polls_before_cancel

    def raise_if_cancelled(self) -> None:
        self.polls += 1
        if self.polls >= self._polls_before_cancel:
            self.cancel()
        super().raise_if_cancelled()


def _samples() -> OverlaySamples:
    return OverlaySamples(
        sequence_lookup=False,
        timestamps_us=(0, 40_000),
        sequences=(),
        sequence_to_timestamp={},
        timestamp_to_sequence={},
        records=SceneHistoryRecords(events=(), tracks=()),
        policy=TimestampFallbackPolicy(tolerance_us=30_000),
    )


def _long_video() -> FrameTimeline:
    return FrameTimeline.from_timestamps(range(1, _FRAMES + 1))


def test_history_placement_stops_walking_frames_once_cancelled() -> None:
    stop = _CancelAfterPolls(_POLLS_BEFORE_CANCEL)

    with pytest.raises(Cancelled):
        _samples().scene_history(_long_video(), allow_previous=True, max_sample_age_us=None, token=stop)

    assert stop.polls == _POLLS_BEFORE_CANCEL


def test_alignment_analysis_stops_walking_frames_once_cancelled() -> None:
    stop = _CancelAfterPolls(_POLLS_BEFORE_CANCEL)

    with pytest.raises(Cancelled):
        _samples().analyze_alignment("TEST", _long_video(), stop)

    assert stop.polls == _POLLS_BEFORE_CANCEL


def test_history_placement_stops_inside_a_track_with_many_runs() -> None:
    """One track can hold far more runs than there are tracks, so cancelling is noticed between its runs."""
    runs = tuple((frame, frame) for frame in range(_FRAMES))
    records = SceneHistoryRecords(events=(), tracks=(SampleTrack("a", (), runs),))
    stop = _CancelAfterPolls(_POLLS_BEFORE_CANCEL)

    with pytest.raises(Cancelled):
        SceneHistory.place(records, tuple(range(_FRAMES)), lambda key: key, [None] * _FRAMES, stop)

    assert stop.polls == _POLLS_BEFORE_CANCEL


def test_history_placement_stops_inside_a_run_with_many_gaps() -> None:
    """One run can span many frames showing no sample, so cancelling is noticed while splitting it around them."""
    shown_keys = [frame if frame % 2 == 0 else None for frame in range(_FRAMES)]
    records = SceneHistoryRecords(events=(), tracks=(SampleTrack("a", (), ((0, _FRAMES - 2),)),))
    stop = _CancelAfterPolls(_POLLS_BEFORE_CANCEL)

    with pytest.raises(Cancelled):
        SceneHistory.place(records, tuple(range(_FRAMES)), lambda key: key, shown_keys, stop)

    assert stop.polls == _POLLS_BEFORE_CANCEL


def test_sample_period_analysis_stops_once_cancelled() -> None:
    """Cancellation during sample-period calculation stops well before the whole recording is traversed."""
    stop = _CancelAfterPolls(_POLLS_BEFORE_CANCEL)

    with pytest.raises(Cancelled):
        TimestampTimeline(tuple(range(_FRAMES))).delta_modes_us(3, stop)

    assert stop.polls == _POLLS_BEFORE_CANCEL


def test_every_analysis_of_a_cancelled_token_raises_before_producing_a_result() -> None:
    cancelled = CancelToken()
    cancelled.cancel()
    samples = _samples()
    video = FrameTimeline.from_timestamps((0, 40_000, 80_000))
    sequences = replace(samples, sequence_lookup=True, sequences=(0, 1, 2))

    for analysis in (
        lambda: samples.scene_history(video, allow_previous=True, max_sample_age_us=None, token=cancelled),
        lambda: samples.analyze_alignment("TEST", video, cancelled),
        lambda: sequences.analyze_alignment("TEST", video, cancelled),
        lambda: count_tolerated_past_overlay_timestamps(
            video_timestamps_us=(0, 40_000),
            sorted_overlay_timestamps_us=(0, 39_000),
            policy=TimestampFallbackPolicy(tolerance_us=5_000),
            token=cancelled,
        ),
    ):
        with pytest.raises(Cancelled):
            analysis()


def test_analyses_of_a_token_nobody_cancels_run_to_the_end() -> None:
    samples = _samples()
    video = FrameTimeline.from_timestamps((0, 40_000, 80_000))

    history = samples.scene_history(video, allow_previous=True, max_sample_age_us=None, token=NEVER)
    report = samples.analyze_alignment("TEST", video, NEVER)

    assert history.frame_count == 3
    assert (report.total_video_frames, report.total_overlay_frames, report.exact_matches) == (3, 2, 2)
