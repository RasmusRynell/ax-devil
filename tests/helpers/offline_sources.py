"""Fake frame and overlay sources for offline viewer tests, and the spec-backed content that opens them."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

from PySide6.QtGui import QImage

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.base import SeekableFrameSource
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy
from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    OverlayContent,
)


class TrackedSignal:
    """Minimal signal stub that records connect/disconnect calls."""

    def __init__(self) -> None:
        self._callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback: object | None = None) -> None:
        if callback is None:
            self._callbacks.clear()
            return
        try:
            self._callbacks.remove(callback)
        except ValueError:
            return

    def emit(self, *args: object) -> None:
        """Call connected callbacks with *args*."""
        for callback in tuple(self._callbacks):
            callback(*args)  # type: ignore[operator]


class TrackedFrameSource(SeekableFrameSource):
    """Simple source stub that records shutdown/disposal calls."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.frameReady = cast(Any, TrackedSignal())
        self.sourceFinished = cast(Any, TrackedSignal())
        self.stop_calls = 0
        self.wait_timeouts: list[int] = []
        self.delete_later_calls = 0
        self._current_frame = 0
        self._total_frames = 100
        self._playback_speed = 1.0
        self.speed_updates: list[float] = []
        self.async_frame_requests: list[int] = []
        self.fps = 25.0

    def get_total_frames(self) -> int:
        return self._total_frames

    def get_frame_size(self) -> tuple[int, int] | None:
        return (640, 480)

    def get_cached_ranges(self) -> tuple[tuple[int, int], ...]:
        return ()

    def get_duration_s(self) -> float | None:
        return None

    def peek_frame_seconds(self, frame_number: int) -> float | None:
        return None

    def get_current_frame(self) -> int:
        return self._current_frame

    def get_position_generation(self) -> int:
        return 0

    def play(self) -> bool:
        return True

    def pause(self) -> None:
        return

    def stop(self) -> None:
        self.stop_calls += 1

    def wait(self, timeout_ms: int = 2000) -> bool:
        self.wait_timeouts.append(timeout_ms)
        return True

    def deleteLater(self) -> None:  # noqa: N802
        self.delete_later_calls += 1

    def jump_to(self, frame_number: int) -> None:
        self._current_frame = frame_number

    def step_delta(self, delta: int) -> None:
        self._current_frame += delta

    def reset_to_start(self) -> None:
        self._current_frame = 0

    def request_frame_async(self, frame_number: int, callback: Callable[[FrameData | None], None]) -> None:
        """Record and immediately complete an async frame request."""
        self.async_frame_requests.append(frame_number)
        self._current_frame = frame_number
        callback(frame_data(frame_number))

    def set_playback_speed(self, value: float) -> None:
        self._playback_speed = value
        self.speed_updates.append(value)

    def get_playback_speed(self) -> float:
        return self._playback_speed

    def get_timing_profile(self) -> None:
        return None

    def get_frame_timestamps_us(self) -> tuple[int, ...]:
        return tuple(frame_index * 1000 for frame_index in range(self._total_frames))

    def get_frame_timeline(self) -> FrameTimeline:
        return FrameTimeline.lazy(count=self._total_frames, timestamp_loader=self.get_frame_timestamps_us)


class TrackedOverlaySource:
    """Pull overlay stub that records closing and disposal calls."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.close_calls = 0
        self.delete_later_calls = 0
        self.requested_frame_ids: list[FrameIdentifier] = []

    @property
    def handler_type(self) -> str:
        return "tracked"

    def get_filter_config(self) -> None:
        return None

    def close(self) -> None:
        self.close_calls += 1

    def deleteLater(self) -> None:  # noqa: N802
        self.delete_later_calls += 1

    def moveToThread(self, _thread: object) -> bool:  # noqa: N802
        return True

    def get_total_frames(self) -> int:
        return 100

    def get_overlay_at_frame(self, frame_id: FrameIdentifier, *, allow_previous: bool = False) -> OverlayData | None:
        self.requested_frame_ids.append(frame_id)
        return None


class OverlayDecoderRequest:
    """Decoder-factory payload used by the test FileOverlaySource seam."""

    def __init__(self, name: str, registry: list[TrackedOverlaySource] | None) -> None:
        self.name = name
        self.registry = registry


class SourceSamples:
    """Analysis snapshot of a test overlay source: the fallback policy it had when taken, answered by that source."""

    def __init__(self, source: SpecFileOverlaySource, policy: TimestampFallbackPolicy) -> None:
        self._source = source
        self._policy = policy

    def scene_history(
        self,
        video_timeline: FrameTimeline,
        *,
        allow_previous: bool,
        max_sample_age_us: int | None,
        token: CancelToken,
    ) -> SceneHistory:
        return self._source.history_under(
            self._policy, video_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us
        )

    def analyze_alignment(
        self, _handler_type: str, video_timeline: FrameTimeline, token: CancelToken
    ) -> OverlayAlignmentReport:
        return self._source.report_under(self._policy, video_timeline)


class SpecFileOverlaySource(TrackedOverlaySource):
    """FileOverlaySource test double created from source specs and decoder lookup.

    Its history names the settings it was placed under, and its analyses fail with *failure* while that is set, so
    tests can tell which setting the displayed results follow.
    """

    def __init__(
        self,
        path: Path,
        decoder_factory: Callable[[], object],
        handler_type: str,
        **_kwargs: object,
    ) -> None:
        request = decoder_factory()
        name = path.stem
        registry: list[TrackedOverlaySource] | None = None
        if isinstance(request, OverlayDecoderRequest):
            name = request.name
            registry = request.registry
        super().__init__(name)
        self.path = path
        self._handler_type = handler_type
        self._timestamp_fallback_policy = TimestampFallbackPolicy()
        self.history_selections: list[tuple[bool, int | None]] = []
        self.failure: str | None = None
        if registry is not None:
            registry.append(self)

    @property
    def handler_type(self) -> str:
        return self._handler_type

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        return self._timestamp_fallback_policy

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        self._timestamp_fallback_policy = policy

    def samples(self) -> SourceSamples:
        return SourceSamples(self, self._timestamp_fallback_policy)

    def scene_history(
        self, video_timeline: FrameTimeline, *, allow_previous: bool, max_sample_age_us: int | None, token: CancelToken
    ) -> SceneHistory:
        return self.history_under(
            self._timestamp_fallback_policy,
            video_timeline,
            allow_previous=allow_previous,
            max_sample_age_us=max_sample_age_us,
        )

    def history_under(
        self,
        policy: TimestampFallbackPolicy,
        video_timeline: FrameTimeline,
        *,
        allow_previous: bool,
        max_sample_age_us: int | None,
    ) -> SceneHistory:
        self.history_selections.append((allow_previous, max_sample_age_us))
        if self.failure is not None:
            raise RuntimeError(self.failure)

        def frame_id(index: int) -> FrameIdentifier:
            return FrameIdentifier(sequence_id=index, timestamp_us=video_timeline.timestamps_us[index])

        settings = ["no fallback"] if policy.effective_tolerance_us == 0 else []
        settings += [] if allow_previous else ["no sticky"]
        suffix = f" ({', '.join(settings)})" if settings else ""
        label = f"Delete {self.name}{suffix}"
        return SceneHistory(
            events=(FrameEvent(frame_id(20), "Delete", label, (self.name,)),),
            objects=(ObjectHistory(self.name, ("human",), ((10, 19),), video_timeline.timestamps_us),),
            frame_count=video_timeline.count,
        )

    def analyze_alignment(self, video_timeline: FrameTimeline, token: CancelToken) -> OverlayAlignmentReport:
        return self.report_under(self._timestamp_fallback_policy, video_timeline)

    def report_under(self, policy: TimestampFallbackPolicy, video_timeline: FrameTimeline) -> OverlayAlignmentReport:
        if self.failure is not None:
            raise RuntimeError(self.failure)
        return OverlayAlignmentReport(
            handler_type=self.handler_type,
            total_video_frames=video_timeline.count,
            total_overlay_frames=0,
            exact_matches=0,
            max_abs_nearest_offset_us=None,
            sample_period_modes_us=(),
            tolerance_us=policy.effective_tolerance_us,
            tolerated_past_matches=0,
        )


def make_tracked_frame_source_opener(
    name: str,
    registry: list[TrackedFrameSource],
) -> Callable[[], TrackedFrameSource]:
    """Build a test source opener that appends each created instance to *registry*."""

    def create() -> TrackedFrameSource:
        source = TrackedFrameSource(name)
        registry.append(source)
        return source

    return create


TEST_FRAME_SOURCE_OPENERS: dict[Path, Callable[[], object]] = {}


def make_overlay_content(
    name: str,
    registry: list[TrackedOverlaySource] | None = None,
) -> OverlayContent:
    """Build spec-backed overlay content for offline viewer tests."""
    return OverlayContent(
        display_name=name,
        source_spec=FileOverlaySourceSpec(
            path=Path(f"/tmp/{name}.txt"),
            handler_type="TEST_FILE",
            decoder_kwargs={"name": name, "registry": registry},
        ),
        metadata={"path": f"/tmp/{name}.txt", "handler_type": "TEST_FILE"},
    )


def assert_frame_source_disposed(source: TrackedFrameSource) -> None:
    """Assert the frame source was stopped, waited for, and scheduled for deletion exactly once."""

    assert source.stop_calls == 1
    assert len(source.wait_timeouts) == 1
    assert source.delete_later_calls == 1


def assert_overlay_source_disposed(source: TrackedOverlaySource) -> None:
    """Assert the overlay source was closed and scheduled for deletion exactly once."""

    assert source.close_calls == 1
    assert source.delete_later_calls == 1


def frame_data(frame_number: int) -> FrameData:
    """Build a minimal frame payload for viewer runtime tests."""

    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_us=frame_number * 1000)
    return FrameData(content=QImage(2, 2, QImage.Format.Format_RGB32), frame_id=frame_id, source_id="video")


def stub_frame_source() -> MagicMock:
    source = MagicMock()
    source.get_total_frames.return_value = 100
    source.fps = 25.0
    source.get_frame_size.return_value = (640, 480)
    source.get_cached_ranges.return_value = ((0, 9),)
    source.get_duration_s.return_value = None
    source.peek_frame_seconds.return_value = None
    source.get_current_frame.return_value = 0
    source.get_playback_speed.return_value = 1.0
    source.get_position_generation.return_value = 0
    source.get_timing_profile.return_value = None
    source.get_frame_timestamps_us.return_value = tuple(frame_index * 1000 for frame_index in range(100))
    source.get_frame_timeline.return_value = FrameTimeline.lazy(
        count=100,
        timestamp_loader=source.get_frame_timestamps_us,
    )
    return source
