"""Tests for offline session, timeline, and lane behavior."""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_viewer import offline_entry_media, offline_viewer_runtime
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineLane, OfflineSession
from ax_devil.modules.video_viewer.scene_inspection import SceneRefilter
from ax_devil.modules.workspace import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveMQTTOverlaySourceSpec,
    OverlayContent,
    SeekableVideoContent,
)


class _Display:
    """Display test double recording display frames."""

    def __init__(self) -> None:
        self.frames: list[VideoFrameWithOverlays] = []
        self.refresh_count = 0

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Record displayed frames."""
        self.frames.append(frame)

    def refresh_overlays(self) -> None:
        """Record overlay refresh requests."""
        self.refresh_count += 1

    def cleanup(self) -> None:
        """Match FrameDisplay cleanup."""


class _RecordingSceneInspector:
    """Scene inspector test double recording updates."""

    def __init__(self) -> None:
        self.updates: list[tuple[Scene | None, FrameIdentifier | None, dict[str, object] | None]] = []

    def clear(self) -> None:
        """Clear recorded updates."""
        self.updates.clear()

    def update_scene(
        self,
        scene: Scene | None,
        frame_id: FrameIdentifier | None,
        metadata: dict[str, object] | None,
        refilter: SceneRefilter | None = None,
    ) -> None:
        """Record an inspector update."""
        self.updates.append((scene, frame_id, metadata))


class _DisplayWithInspectorAssertion(_Display):
    """Display test double ensuring inspection is not delivered before display."""

    def __init__(self, inspector: _RecordingSceneInspector) -> None:
        super().__init__()
        self._inspector = inspector

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Record the displayed frame and assert inspection has not run yet."""
        assert self._inspector.updates == []
        super().display_frame(frame)


class _StepBackDuringDisplay(_Display):
    """Display test double that triggers a playback step while presenting a frame."""

    def __init__(self, *, trigger_frame: int, delta: int) -> None:
        super().__init__()
        self.trigger_frame = trigger_frame
        self.delta = delta
        self.session: OfflineSession | None = None
        self.current_frame_seen_during_display: int | None = None
        self.triggered = False

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Record the frame and trigger one relative step during presentation."""
        super().display_frame(frame)
        if self.triggered or frame.frame.frame_id != self.trigger_frame or self.session is None:
            return
        self.triggered = True
        self.current_frame_seen_during_display = self.session.current_frame
        self.session.step_frames(self.delta)


class _OverlaySource:
    """Seekable overlay test double recording direct frame lookup."""

    def __init__(self, overlay: OverlayData | None) -> None:
        self.overlay = overlay
        self.requested_frame_ids: list[FrameIdentifier] = []

    def get_overlay_at_frame(self, frame_id: FrameIdentifier, *, allow_previous: bool = False) -> OverlayData | None:
        """Return the configured overlay for a direct frame lookup."""
        self.requested_frame_ids.append(frame_id)
        return self.overlay


class _AsyncFrameSource:
    """Async frame source test double that lets tests complete requests out of order."""

    def __init__(self) -> None:
        self.requests: list[int] = []
        self.callbacks: dict[int, Callable[[FrameData | None], None]] = {}

    def request_frame_async(self, frame_number: int, callback: Callable[[FrameData | None], None]) -> None:
        """Record the callback for explicit test completion."""
        self.requests.append(frame_number)
        self.callbacks[frame_number] = callback

    def complete(self, frame_number: int) -> None:
        """Complete a previously requested frame."""
        callback = self.callbacks[frame_number]
        callback(_frame(frame_number))


class _Signal:
    """Small signal test double for non-Qt playback source stubs."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        """Record a callback connection."""
        self._callbacks.append(callback)

    def emit(self, *args: object) -> None:
        """Invoke connected callbacks."""
        for callback in tuple(self._callbacks):
            callback(*args)


class _PlaybackSource:
    """Primary video source test double for offline-session frame ordering."""

    def __init__(self, *, total_frames: int = 1000) -> None:
        self.sourceFinished = _Signal()
        self.current_frame = 0
        self.total_frames = total_frames
        self._position_generation = 0
        self.play_calls = 0
        self.pause_calls = 0
        self.jump_calls: list[int] = []
        self.speed_updates: list[float] = []

    def get_total_frames(self) -> int:
        """Return total frames."""
        return self.total_frames

    def get_current_frame(self) -> int:
        """Return current frame."""
        return self.current_frame

    def get_position_generation(self) -> int:
        """Return current explicit-position generation."""
        return self._position_generation

    def play(self) -> bool:
        """Record playback start."""
        self.play_calls += 1
        return True

    def pause(self) -> None:
        """Record playback pause."""
        self.pause_calls += 1

    def jump_to(self, frame_number: int) -> None:
        """Record a seek."""
        self._position_generation += 1
        self.current_frame = frame_number
        self.jump_calls.append(frame_number)

    def reset_to_start(self) -> None:
        """Reset to first frame."""
        self._position_generation += 1
        self.current_frame = 0

    def set_playback_speed(self, speed: float) -> None:
        """Record playback speed changes."""
        self.speed_updates.append(speed)


def _frame(frame_number: int, *, position_generation: int | None = None) -> FrameData:
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_monotime_us=float(frame_number * 1000))
    metadata = {"position_generation": position_generation} if position_generation is not None else None
    return FrameData(
        content=QImage(2, 2, QImage.Format.Format_RGB32),
        frame_id=frame_id,
        source_id="video",
        metadata=metadata,
    )


def _source_frame(source: _PlaybackSource, frame_number: int) -> FrameData:
    return _frame(frame_number, position_generation=source._position_generation)


def _overlay(frame_number: int) -> OverlayData:
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_monotime_us=float(frame_number * 1000))
    return OverlayData(
        content=Scene(time_slice=TimeSlice(start=frame_number, end=frame_number)),
        frame_id=frame_id,
        source_id="overlay",
        metadata={"source": "direct"},
    )


def _make_primary_session(
    qtbot: object,
    *,
    current_frame: int = 0,
    total_frames: int = 1000,
) -> tuple[OfflineSession, _Display, _PlaybackSource]:
    display = _Display()
    source = _PlaybackSource(total_frames=total_frames)
    source.current_frame = current_frame
    lane = OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="primary",
        display=display,  # type: ignore[arg-type]
        overlay_source=None,
        video_source=source,  # type: ignore[arg-type]
    )
    container = QWidget()
    qtbot.addWidget(container)  # type: ignore[attr-defined]
    pooled_source = offline_viewer_runtime._PooledVideoSource(  # noqa: SLF001
        source=source,  # type: ignore[arg-type]
        relay=offline_viewer_runtime._FrameDeliveryRelay(0, container),  # noqa: SLF001
        source_index=0,
    )
    session = OfflineSession([lane], container, media=EntryMedia(), source_pool=[pooled_source])
    return session, display, source


def test_offline_session_bounds_commands_clamps_speed_and_tracks_playback_state(qtbot: object) -> None:
    session, _display, source = _make_primary_session(qtbot, current_frame=9, total_frames=10)

    assert session.total_frames == 10
    assert session.current_frame == 9
    assert not session.is_playing

    session.start_playback()

    assert source.play_calls == 1
    assert source.current_frame == 0
    assert session.current_frame == 0
    assert session.is_playing

    session.pause_playback()

    assert source.pause_calls == 1
    assert not session.is_playing

    session.jump_to_frame(500)

    assert source.jump_calls[-1] == 9
    assert session.current_frame == 9

    session.step_frames(-20)

    assert source.jump_calls[-1] == 0
    assert session.current_frame == 0

    session.set_playback_speed(99.0)

    assert source.speed_updates[-1] == 10.0


def test_offline_lane_uses_direct_overlay_lookup_and_presenter_without_offline_sync() -> None:
    overlay = _overlay(7)
    overlay_source = _OverlaySource(overlay)
    display = _Display()
    lane = OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="lane",
        display=display,  # type: ignore[arg-type]
        overlay_source=overlay_source,  # type: ignore[arg-type]
    )

    lane.present_frame(_frame(7))

    assert overlay_source.requested_frame_ids == [FrameIdentifier(sequence_id=7, timestamp_monotime_us=7000.0)]
    assert len(display.frames) == 1
    assert display.frames[0].overlays is not None
    assert display.frames[0].overlays.overlay_id == 7


def test_offline_lane_displays_frame_before_scheduling_scene_inspection(qtbot: object) -> None:
    inspector = _RecordingSceneInspector()
    overlay_source = _OverlaySource(_overlay(7))
    display = _DisplayWithInspectorAssertion(inspector)
    lane = OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="lane",
        display=display,  # type: ignore[arg-type]
        overlay_source=overlay_source,  # type: ignore[arg-type]
        tools_panel=cast(MediaToolsPanel, inspector),
    )

    lane.present_frame(_frame(7))

    assert len(display.frames) == 1
    assert inspector.updates == []
    qtbot.waitUntil(lambda: len(inspector.updates) == 1)  # type: ignore[attr-defined]
    inspected_scene, inspected_frame_id, inspected_metadata = inspector.updates[0]
    assert overlay_source.overlay is not None
    assert inspected_scene is overlay_source.overlay.content
    assert inspected_frame_id == FrameIdentifier(sequence_id=7, timestamp_monotime_us=7000.0)
    assert inspected_metadata == {"source": "direct"}


def test_offline_lane_async_frame_requests_present_latest_desired_frame() -> None:
    frame_source = _AsyncFrameSource()
    overlay_source = _OverlaySource(_overlay(9))
    display = _Display()
    lane = OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="lane",
        display=display,  # type: ignore[arg-type]
        overlay_source=overlay_source,  # type: ignore[arg-type]
        video_source=frame_source,  # type: ignore[arg-type]
    )

    def _complete(requested_frame: int, frame_data: FrameData | None) -> None:
        lane.complete_frame_request(requested_frame, frame_data, _complete)

    lane.request_frame(7, _complete)
    lane.request_frame(9, _complete)
    frame_source.complete(7)

    assert frame_source.requests == [7, 9]
    assert display.frames == []

    frame_source.complete(9)

    assert [frame_id.sequence_id for frame_id in overlay_source.requested_frame_ids] == [9]
    assert len(display.frames) == 1


def test_offline_session_drops_queued_old_primary_frame_until_seek_target_arrives(
    qtbot: object,
) -> None:
    session, display, source = _make_primary_session(qtbot)
    session.start_playback()
    current_frame = source.total_frames // 2
    target_frame = current_frame - 2
    queued_old_frame = current_frame + 1
    playback_after_target = target_frame + 1
    old_generation = source._position_generation

    session._on_frame_ready(0, _source_frame(source, current_frame))  # noqa: SLF001
    source.current_frame = current_frame
    session.jump_to_frame(target_frame)
    session._on_frame_ready(0, _frame(queued_old_frame, position_generation=old_generation))  # noqa: SLF001
    session._on_frame_ready(0, _source_frame(source, target_frame))  # noqa: SLF001
    session._on_frame_ready(0, _frame(queued_old_frame, position_generation=old_generation))  # noqa: SLF001
    session._on_frame_ready(0, _source_frame(source, playback_after_target))  # noqa: SLF001

    assert source.jump_calls[-1] == target_frame
    displayed_ids = [frame.frame.frame_id for frame in display.frames]
    assert displayed_ids == [current_frame, target_frame, playback_after_target]


def test_offline_session_accepts_primary_frames_without_position_generation(
    qtbot: object,
) -> None:
    session, display, source = _make_primary_session(qtbot)
    session._on_frame_ready(0, _source_frame(source, 10))  # noqa: SLF001
    source.current_frame = 10

    session.jump_to_frame(4)
    session._on_frame_ready(0, _frame(5))  # noqa: SLF001

    displayed_ids = [frame.frame.frame_id for frame in display.frames]
    assert displayed_ids == [10, 5]


class _DeliveringSource(QObject):
    """Frame source test double emitting from a worker thread, like the delivery worker."""

    frameReady = Signal(FrameData)


def test_frame_relay_presents_only_newest_of_queued_deliveries(qtbot: object) -> None:
    """A GUI thread that falls behind presents the newest queued frame and skips superseded ones."""
    container = QWidget()
    qtbot.addWidget(container)  # type: ignore[attr-defined]
    relay = offline_viewer_runtime._FrameDeliveryRelay(3, container)  # noqa: SLF001
    source = _DeliveringSource()
    source.frameReady.connect(relay.deliver)
    presented: list[tuple[int, int]] = []
    relay.frameReady.connect(lambda index, frame_data: presented.append((index, frame_data.frame_id.sequence_id)))

    def deliver_from_worker(*frame_numbers: int) -> None:
        def emit_frames() -> None:
            for frame_number in frame_numbers:
                source.frameReady.emit(_frame(frame_number))

        worker = threading.Thread(target=emit_frames)
        worker.start()
        worker.join()

    deliver_from_worker(1, 2, 3)
    assert presented == []
    qtbot.waitUntil(lambda: bool(presented))  # type: ignore[attr-defined]
    QCoreApplication.processEvents()
    assert presented == [(3, 3)]

    deliver_from_worker(4)
    qtbot.waitUntil(lambda: len(presented) == 2)  # type: ignore[attr-defined]
    assert presented == [(3, 3), (3, 4)]


def test_offline_session_updates_current_frame_before_display_callbacks(
    qtbot: object,
) -> None:
    """A command triggered by displaying frame 800 should seek relative to frame 800, not 799."""
    source = _PlaybackSource(total_frames=1000)
    display = _StepBackDuringDisplay(trigger_frame=800, delta=-30)
    lane = OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="primary",
        display=display,  # type: ignore[arg-type]
        overlay_source=None,
        video_source=source,  # type: ignore[arg-type]
    )
    container = QWidget()
    qtbot.addWidget(container)  # type: ignore[attr-defined]
    pooled_source = offline_viewer_runtime._PooledVideoSource(  # noqa: SLF001
        source=source,  # type: ignore[arg-type]
        relay=offline_viewer_runtime._FrameDeliveryRelay(0, container),  # noqa: SLF001
        source_index=0,
    )
    session = OfflineSession([lane], container, media=EntryMedia(), source_pool=[pooled_source])
    display.session = session

    source.current_frame = 799
    session._on_frame_ready(0, _source_frame(source, 799))  # noqa: SLF001
    source.current_frame = 800
    session._on_frame_ready(0, _source_frame(source, 800))  # noqa: SLF001

    assert (
        display.current_frame_seen_during_display,
        source.jump_calls[-1],
    ) == (800, 770)


def test_entry_media_opens_file_video_source_from_seekable_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[str, object]] = []

    class _FileFrameSource:
        def __init__(self, file_path: str, **kwargs: object) -> None:
            opened.append((file_path, kwargs["image_sequence_config"]))

    monkeypatch.setattr(offline_entry_media, "FileFrameSource", _FileFrameSource)
    video = SeekableVideoContent(
        display_name="clip.mp4",
        source_spec=FileVideoSourceSpec(path=Path("/tmp/clip.mp4")),
    )

    source = offline_entry_media.create_frame_source(video)

    assert isinstance(source, _FileFrameSource)
    assert opened == [("/tmp/clip.mp4", None)]


def test_entry_media_opens_file_overlay_source_from_decoder_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DecoderFactory:
        def __call__(self, **_kwargs: object) -> object:
            return object()

    decoder_factory = _DecoderFactory()
    lookups: list[str] = []
    opened: list[tuple[Path, object, str, dict[str, object]]] = []

    class _FileOverlaySource:
        def __init__(self, path: Path, factory: object, handler_type: str, **kwargs: object) -> None:
            opened.append((path, factory, handler_type, kwargs))

    def _get_file_decoder_factory(handler_type: str) -> object:
        lookups.append(handler_type)
        return decoder_factory

    monkeypatch.setattr(offline_entry_media, "FileOverlaySource", _FileOverlaySource)
    monkeypatch.setattr(offline_entry_media, "get_file_decoder_factory", _get_file_decoder_factory)
    overlay = OverlayContent(
        display_name="overlay.txt",
        source_spec=FileOverlaySourceSpec(
            path=Path("/tmp/overlay.txt"),
            handler_type="MOT_FILE",
            timestamp_fallback_policy=TimestampFallbackPolicy(
                mode=TimestampFallbackMode.EXACT_ONLY,
                tolerance_us=12_000,
            ),
            decoder_kwargs={"width": 1920, "height": 1080},
        ),
        metadata={"path": "/tmp/overlay.txt", "handler_type": "MOT_FILE"},
    )

    frame_timeline = FrameTimeline.lazy(count=0, timestamp_loader=tuple)
    overlay_source, overlay_policy = offline_entry_media.create_overlay_source(overlay, frame_timeline=frame_timeline)

    assert overlay.source_spec is not None
    assert isinstance(overlay.source_spec, FileOverlaySourceSpec)
    assert overlay.source_spec.decoder_kwargs == {"width": 1920, "height": 1080}
    assert isinstance(overlay_source, _FileOverlaySource)
    assert overlay_policy is not None
    assert lookups == ["MOT_FILE"]
    opened_path, opened_factory, opened_handler, opened_kwargs = opened[0]
    assert opened_path == Path("/tmp/overlay.txt")
    assert opened_handler == "MOT_FILE"
    assert opened_kwargs == {
        "frame_timeline": frame_timeline,
        "timestamp_fallback_policy": TimestampFallbackPolicy(
            mode=TimestampFallbackMode.EXACT_ONLY,
            tolerance_us=12_000,
        ),
    }
    assert isinstance(opened_factory, partial)
    assert opened_factory.func is decoder_factory
    assert opened_factory.keywords == {"width": 1920, "height": 1080}


def test_entry_media_rejects_non_file_overlay_source_spec() -> None:
    overlay = OverlayContent(
        display_name="MQTT",
        source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
    )

    with pytest.raises(RuntimeError, match="requires a file overlay source spec"):
        offline_entry_media.create_overlay_source(
            overlay, frame_timeline=FrameTimeline.lazy(count=0, timestamp_loader=tuple)
        )


def test_offline_lane_uses_stateless_retention_and_original_sample_time() -> None:
    """Direct display and backward revisits use lookup selection and retained styling."""
    from dataclasses import replace

    from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings

    overlay = replace(_overlay(7), metadata={"timestamp_match_type": "retained"})
    overlay_source = _OverlaySource(overlay)
    display = _Display()
    lane = OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="lane",
        display=display,  # type: ignore[arg-type]
        overlay_source=overlay_source,  # type: ignore[arg-type]
        overlay_policy=OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=2, opacity=0.4)),
    )
    for frame in [8, 10, 8, 6]:
        lane.present_frame(_frame(frame))
    for index in [0, 2]:
        shown = display.frames[index].overlays
        assert shown is not None
        assert shown.timestamp_monotime_us == 7000
        assert shown.metadata is not None
        assert shown.metadata["overlay_reused"] is True
        assert shown.metadata["overlay_opacity"] == 0.4
    assert display.frames[1].overlays is None
    assert display.frames[3].overlays is None
