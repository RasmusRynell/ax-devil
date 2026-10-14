"""What an overlay file's samples show on a video, as an immutable value that any thread may analyze."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from dataclasses import dataclass

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.data_sources.alignment_diagnostics import (
    analyze_overlay_alignment,
    analyze_overlay_sequence_alignment,
)
from ax_devil.modules.data_sources.scene_history import SceneHistory, SceneHistoryRecords
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.synchronization.timestamp_matching import (
    TimestampFallbackPolicy,
    find_matching_overlay_timestamp,
)


def sequence_sample(
    sorted_sequences: tuple[int, ...],
    frame_index: int,
    frame_time_us: int,
    timeline: FrameTimeline,
    *,
    allow_previous: bool,
) -> tuple[int, int] | None:
    """Return the sequence and video time of the frame-keyed sample a frame shows, if any."""
    index = bisect_right(sorted_sequences, frame_index) - 1
    if index < 0:
        return None
    sequence = sorted_sequences[index]
    if not allow_previous and sequence != frame_index:
        return None
    timestamps = timeline.timestamps_us
    if not 0 <= sequence < len(timestamps) or timestamps[sequence] > frame_time_us:
        return None
    return sequence, timestamps[sequence]


@dataclass(frozen=True, slots=True)
class OverlaySamples:
    """Sample keys, history records and timestamp policy of one overlay file at one moment.

    The mappings are never mutated after the provider builds them, so a worker thread may call the analyses while the
    GUI thread keeps looking samples up; a policy change makes a new value instead of changing this one.
    """

    sequence_lookup: bool
    timestamps_us: tuple[int, ...]  # Sorted sample timestamps.
    sequences: tuple[int, ...]  # Sorted sample sequence ids.
    sequence_to_timestamp: Mapping[int, int]
    timestamp_to_sequence: Mapping[int, int]
    records: SceneHistoryRecords
    policy: TimestampFallbackPolicy

    def analyze_alignment(
        self, handler_type: str, video_timeline: FrameTimeline, token: CancelToken
    ) -> OverlayAlignmentReport:
        """Analyze how these samples align to decoded video timestamps; polls *token*."""
        if self.sequence_lookup:
            return analyze_overlay_sequence_alignment(
                handler_type=handler_type,
                total_video_frames=video_timeline.count,
                overlay_sequences=self.sequences,
                policy=self.policy,
                token=token,
            )
        return analyze_overlay_alignment(
            handler_type=handler_type,
            video_timeline=video_timeline,
            overlay_timestamps_us=self.timestamps_us,
            policy=self.policy,
            token=token,
        )

    def scene_history(
        self,
        video_timeline: FrameTimeline,
        *,
        allow_previous: bool,
        max_sample_age_us: int | None,
        token: CancelToken,
    ) -> SceneHistory:
        """Return the events and entity appearances placed on the video's frames as lookup shows them.

        Objects appear on exactly the frames whose looked-up sample contains them, under the same matching as
        ``lookup_by_frame_id`` with *allow_previous*, and only while the sample is at most *max_sample_age_us* old.
        Events take effect on the first frame at or after their sample; frame-keyed samples belong to the frame
        their sequence names. Polls *token*.
        """
        frame_times = video_timeline.timestamps_us
        shown_keys: list[int | None] = []
        for frame_index, frame_time in token.polled(enumerate(frame_times)):
            sample = self._shown_sample(frame_index, frame_time, video_timeline, allow_previous=allow_previous)
            too_old = (
                sample is not None and max_sample_age_us is not None and frame_time - sample[1] > max_sample_age_us
            )
            shown_keys.append(sample[0] if sample is not None and not too_old else None)

        def frame_position(timestamp_key: int) -> int:
            if self.sequence_lookup:
                return self.timestamp_to_sequence[timestamp_key]
            if not frame_times or timestamp_key < frame_times[0]:
                return -1
            return bisect_left(frame_times, timestamp_key)

        return SceneHistory.place(self.records, frame_times, frame_position, shown_keys, token)

    def _shown_sample(
        self, frame_index: int, frame_time_us: int, timeline: FrameTimeline, *, allow_previous: bool
    ) -> tuple[int, int] | None:
        """Return the timestamp key and time of the sample lookup selects for a video frame, if any."""
        if self.sequence_lookup:
            sample = sequence_sample(
                self.sequences, frame_index, frame_time_us, timeline, allow_previous=allow_previous
            )
            return (self.sequence_to_timestamp[sample[0]], sample[1]) if sample is not None else None
        match = find_matching_overlay_timestamp(
            sorted_overlay_timestamps_us=self.timestamps_us,
            video_timestamp_us=frame_time_us,
            policy=self.policy,
            allow_previous=allow_previous,
        )
        key = match.overlay_timestamp_us
        return (key, key) if key is not None else None
