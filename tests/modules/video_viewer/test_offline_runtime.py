"""Tests for offline session, timeline, and lane behavior."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer import offline_entry_media, offline_viewer_runtime
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineLane, OfflineSession
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings
from ax_devil.modules.workspace import (
    FileOverlaySourceSpec,
    LiveMQTTOverlaySourceSpec,
    OverlayContent,
    SeekableVideoContent,
)
from tests.helpers.scene_inspector import RecordingSceneInspector


class _Display:
    """Display test double recording display frames."""

    def __init__(self) -> None:
        self.frames: list[VideoFrameWithOverlays] = []

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Record displayed frames."""
        self.frames.append(frame)

    def cleanup(self) -> None:
        """Match FrameDisplay cleanup."""

    def frame_ids(self) -> list[int | None]:
        """Return the ids of the displayed frames in order."""
        return [frame.frame.frame_id for frame in self.frames]


class _DisplayWithInspectorAssertion(_Display):
    """Display test double ensuring inspection is not delivered before display."""

    def __init__(self, inspector: RecordingSceneInspector) -> None:
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

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Record the frame and trigger one relative step during presentation."""
        super().display_frame(frame)
        if frame.frame.frame_id != self.trigger_frame or self.session is None:
            return
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


class _Signal:
    """Small signal test double for non-Qt playback source stubs."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        """Record a callback connection."""
        self._callbacks.append(callback)

    def disconnect(self, callback: Callable[..., None]) -> None:
        """Remove a source callback during session cleanup."""
        self._callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        """Invoke connected callbacks."""
        for callback in tuple(self._callbacks):
            callback(*args)


class _AsyncFrameSource:
    """Secondary frame source test double that lets tests complete requests out of order."""

    def __init__(self) -> None:
        self.frameReady = _Signal()
        self.requests: list[int] = []
        self.callbacks: dict[int, Callable[[FrameData | None], None]] = {}

    def request_frame_async(self, frame_number: int, callback: Callable[[FrameData | None], None]) -> None:
        """Record the callback for explicit test completion."""
        self.requests.append(frame_number)
        self.callbacks[frame_number] = callback

    def complete(self, frame_number: int) -> None:
        """Complete the latest request for *frame_number*."""
        self.callbacks[frame_number](_frame(frame_number))

    def complete_from_worker(self, frame_number: int) -> None:
        """Complete the latest request for *frame_number* from a worker thread, as the decoder does."""
        worker = threading.Thread(target=lambda: self.complete(frame_number))
        worker.start()
        worker.join()

    def set_playback_speed(self, speed: float) -> None:
        """Accept speed changes applied to every session source."""


class _PlaybackSource:
    """Primary video source test double for offline-session frame ordering."""

    def __init__(self, *, total_frames: int = 1000) -> None:
        self.sourceFinished = _Signal()
        self.frameReady = _Signal()
        self.current_frame = 0
        self.total_frames = total_frames
        self.position_generation = 0
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
        return self.position_generation

    def play(self) -> bool:
        """Record playback start."""
        self.play_calls += 1
        return True

    def pause(self) -> None:
        """Record playback pause."""
        self.pause_calls += 1

    def jump_to(self, frame_number: int) -> None:
        """Record a seek."""
        self.position_generation += 1
        self.current_frame = frame_number
        self.jump_calls.append(frame_number)

    def reset_to_start(self) -> None:
        """Reset to first frame."""
        self.position_generation += 1
        self.current_frame = 0

    def set_playback_speed(self, speed: float) -> None:
        """Record playback speed changes."""
        self.speed_updates.append(speed)

    def deliver(self, frame_number: int, *, position_generation: int | None = None) -> None:
        """Emit a decoded frame as playback does and let the GUI thread present it."""
        generation = self.position_generation if position_generation is None else position_generation
        self.frameReady.emit(_frame(frame_number, position_generation=generation))
        QCoreApplication.processEvents()


def _frame(frame_number: int, *, position_generation: int | None = None) -> FrameData:
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_monotime_us=float(frame_number * 1000))
    metadata = {"position_generation": position_generation} if position_generation is not None else None
    return FrameData(
        content=QImage(2, 2, QImage.Format.Format_RGB32),
        frame_id=frame_id,
        source_id="video",
        metadata=metadata,
    )


def _overlay(frame_number: int) -> OverlayData:
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_monotime_us=float(frame_number * 1000))
    return OverlayData(
        content=Scene(time_slice=TimeSlice(start=frame_number, end=frame_number)),
        frame_id=frame_id,
        source_id="overlay",
        metadata={"source": "direct"},
    )


def _lane(
    display: _Display,
    *,
    overlay_source: _OverlaySource | None = None,
    video_source: object = None,
    source_index: int = 0,
    overlay_policy: OverlayPersistencePolicy | None = None,
    tools_panel: MediaToolsPanel | None = None,
) -> OfflineLane:
    return OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="lane",
        display=cast(FrameDisplay, display),
        overlay_source=cast(FileOverlaySource | None, overlay_source),
        video_source=cast(FileFrameSource | None, video_source),
        source_index=source_index,
        overlay_policy=overlay_policy,
        tools_panel=tools_panel,
    )


def _make_session(qtbot: QtBot, lanes: list[OfflineLane], sources: list[object]) -> OfflineSession:
    """Wire a session over test sources the way `OfflineSession.build` wires opened media.

    `build` needs real displays; the source pool and the frame slot are private, so this is the one place tests
    reach them.
    """
    container = QWidget()
    qtbot.addWidget(container)
    pool = [
        offline_viewer_runtime._PooledVideoSource(  # noqa: SLF001
            source=cast(FileFrameSource, source),
            relay=offline_viewer_runtime._FrameDeliveryRelay(index, container),  # noqa: SLF001
            source_index=index,
        )
        for index, source in enumerate(sources)
    ]
    session = OfflineSession(lanes, container, media=EntryMedia(), source_pool=pool)
    for pooled in pool:
        pooled.source.frameReady.connect(pooled.relay.deliver)
        pooled.relay.frameReady.connect(session._on_frame_ready)  # noqa: SLF001
    return session


def _make_primary_session(
    qtbot: QtBot, *, current_frame: int = 0, total_frames: int = 1000
) -> tuple[OfflineSession, _Display, _PlaybackSource]:
    display = _Display()
    source = _PlaybackSource(total_frames=total_frames)
    source.current_frame = current_frame
    session = _make_session(qtbot, [_lane(display, video_source=source)], [source])
    return session, display, source


def test_offline_session_bounds_commands_clamps_speed_and_tracks_playback_state(qtbot: QtBot) -> None:
    """Play at the last frame restarts; seeks and steps stay inside the video; speed is clamped."""
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


def test_lane_shows_each_frame_with_the_overlay_for_that_frame() -> None:
    overlay_source = _OverlaySource(_overlay(7))
    display = _Display()
    lane = _lane(display, overlay_source=overlay_source)

    lane.present_frame(_frame(7))

    assert overlay_source.requested_frame_ids == [FrameIdentifier(sequence_id=7, timestamp_monotime_us=7000.0)]
    assert display.frame_ids() == [7]
    assert display.frames[0].overlays is not None
    assert display.frames[0].overlays.overlay_id == 7


def test_lane_displays_frame_before_scheduling_scene_inspection(qtbot: QtBot) -> None:
    """Inspection runs after the frame is on screen, so a slow inspector never delays playback."""
    inspector = RecordingSceneInspector()
    overlay_source = _OverlaySource(_overlay(7))
    display = _DisplayWithInspectorAssertion(inspector)
    lane = _lane(display, overlay_source=overlay_source, tools_panel=cast(MediaToolsPanel, inspector))

    lane.present_frame(_frame(7))

    assert len(display.frames) == 1
    assert inspector.updates == []
    qtbot.waitUntil(lambda: len(inspector.updates) == 1)
    inspected_scene, inspected_frame_id, inspected_metadata = inspector.updates[0]
    assert overlay_source.overlay is not None
    assert inspected_scene is overlay_source.overlay.content
    assert inspected_frame_id == FrameIdentifier(sequence_id=7, timestamp_monotime_us=7000.0)
    assert inspected_metadata == {"source": "direct"}


def test_secondary_video_shows_only_the_current_primary_frame(qtbot: QtBot) -> None:
    """Lanes on a second video follow the primary frame; superseded or stale completions never reach the screen."""
    primary_display = _Display()
    primary = _PlaybackSource()
    secondary = _AsyncFrameSource()
    displays = [_Display(), _Display()]
    overlays = [_OverlaySource(_overlay(90)), _OverlaySource(_overlay(91))]
    session = _make_session(
        qtbot,
        [
            _lane(primary_display, video_source=primary),
            *(
                _lane(display, overlay_source=overlay, video_source=secondary, source_index=1)
                for display, overlay in zip(displays, overlays, strict=True)
            ),
        ],
        [primary, secondary],
    )

    # Frame 9 arrives while frame 7 is still decoding on the second video: 7 is skipped once it completes.
    primary.deliver(7)
    primary.deliver(9)
    assert secondary.requests == [7]
    secondary.complete_from_worker(7)
    assert displays[0].frames == displays[1].frames == []
    qtbot.waitUntil(lambda: secondary.requests == [7, 9])
    secondary.complete_from_worker(9)
    assert displays[0].frames == displays[1].frames == []
    qtbot.waitUntil(lambda: all(display.frames for display in displays))
    assert primary_display.frame_ids() == [7, 9]
    for index, display in enumerate(displays):
        assert display.frame_ids() == [9]
        assert display.frames[0].overlays is not None
        assert display.frames[0].overlays.overlay_id == 90 + index
        assert [frame_id.sequence_id for frame_id in overlays[index].requested_frame_ids] == [9]

    # Seeking to the frame already being decoded still rejects the completion requested before the seek.
    primary.deliver(10)
    before_seek = secondary.callbacks[10]
    session.jump_to_frame(10)
    primary.deliver(10)
    before_seek(_frame(10))
    assert displays[0].frame_ids() == [9]
    secondary.complete(10)
    assert displays[0].frame_ids() == [9, 10]

    # Pausing drops the pending request; a missing secondary frame keeps the previous one on screen.
    primary.deliver(11)
    session.pause_playback()
    secondary.complete(11)
    session.step_frames(2)
    primary.deliver(13)
    secondary.callbacks[13](None)
    assert displays[0].frame_ids() == [9, 10]
    primary.deliver(14)
    secondary.complete(14)
    assert displays[0].frame_ids() == [9, 10, 14]

    # A decoder finishing after the session is gone must not touch it.
    primary.deliver(15)
    late_completion = secondary.callbacks[15]
    session.cleanup(blocking=True)
    QCoreApplication.sendPostedEvents(session, QEvent.Type.DeferredDelete)
    failures: list[Exception] = []

    def complete_after_destruction() -> None:
        try:
            late_completion(_frame(15))
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=complete_after_destruction)
    worker.start()
    worker.join()
    QCoreApplication.processEvents()
    assert failures == []
    assert displays[0].frame_ids() == [9, 10, 14]


def test_seek_drops_frames_decoded_before_it(qtbot: QtBot) -> None:
    """Frames queued from the old position are skipped until the seek target arrives; frames without a position
    generation are always shown."""
    session, display, source = _make_primary_session(qtbot)
    session.start_playback()
    old_generation = source.position_generation
    source.deliver(500)

    session.jump_to_frame(498)
    source.deliver(501, position_generation=old_generation)
    source.deliver(498)
    source.deliver(501, position_generation=old_generation)
    source.deliver(499)

    assert source.jump_calls[-1] == 498
    assert display.frame_ids() == [500, 498, 499]

    session.jump_to_frame(4)
    source.frameReady.emit(_frame(5))
    QCoreApplication.processEvents()

    assert display.frame_ids() == [500, 498, 499, 5]


class _DeliveringSource(QObject):
    """Frame source test double emitting from a worker thread, like the delivery worker."""

    frameReady = Signal(FrameData)


def test_frame_relay_presents_only_newest_of_queued_deliveries(qtbot: QtBot) -> None:
    """A GUI thread that falls behind presents the newest queued frame and skips superseded ones."""
    container = QWidget()
    qtbot.addWidget(container)
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
    qtbot.waitUntil(lambda: bool(presented))
    QCoreApplication.processEvents()
    assert presented == [(3, 3)]

    deliver_from_worker(4)
    qtbot.waitUntil(lambda: len(presented) == 2)
    assert presented == [(3, 3), (3, 4)]


def test_step_during_display_is_relative_to_the_frame_being_shown(qtbot: QtBot) -> None:
    """A command triggered by displaying frame 800 should seek relative to frame 800, not 799."""
    source = _PlaybackSource(total_frames=1000)
    display = _StepBackDuringDisplay(trigger_frame=800, delta=-30)
    session = _make_session(qtbot, [_lane(display, video_source=source)], [source])
    display.session = session

    source.deliver(799)
    source.deliver(800)

    assert display.current_frame_seen_during_display == 800
    assert source.jump_calls[-1] == 770


class _RecordingProvider:
    """Overlay data provider test double recording how the decoder was configured."""

    def __init__(self, file_path: Path, **kwargs: object) -> None:
        self.file_path = file_path
        self.kwargs = kwargs
        self.policy = TimestampFallbackPolicy()

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        self.policy = policy

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        return self.policy

    def get_total_frames(self) -> int:
        return 0


def test_overlay_source_is_opened_with_the_decoder_options_and_fallback_policy_of_its_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_file_decoder_factory(handler_type: str) -> type[_RecordingProvider]:
        assert handler_type == "MOT_FILE"
        return _RecordingProvider

    monkeypatch.setattr(offline_entry_media, "get_file_decoder_factory", get_file_decoder_factory)
    policy = TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY, tolerance_us=12_000)
    overlay = OverlayContent(
        display_name="overlay.txt",
        source_spec=FileOverlaySourceSpec(
            path=Path("/tmp/overlay.txt"),
            handler_type="MOT_FILE",
            timestamp_fallback_policy=policy,
            decoder_kwargs={"width": 1920, "height": 1080},
        ),
    )

    overlay_source, overlay_policy = offline_entry_media.create_overlay_source(
        overlay, frame_timeline=FrameTimeline.lazy(count=0, timestamp_loader=tuple)
    )

    provider = overlay_source.data_provider
    assert isinstance(provider, _RecordingProvider)
    assert provider.file_path == Path("/tmp/overlay.txt")
    assert provider.kwargs == {"width": 1920, "height": 1080}
    assert overlay_source.handler_type == "MOT_FILE"
    assert overlay_source.get_timestamp_fallback_policy() == policy
    assert overlay_policy.settings.enabled


def test_entry_media_rejects_non_file_overlay_source_spec() -> None:
    overlay = OverlayContent(
        display_name="MQTT",
        source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
    )

    with pytest.raises(RuntimeError, match="requires a file overlay source spec"):
        offline_entry_media.create_overlay_source(
            overlay, frame_timeline=FrameTimeline.lazy(count=0, timestamp_loader=tuple)
        )


def test_lane_retains_a_recent_overlay_at_its_original_sample_time() -> None:
    """Direct display and backward revisits use lookup selection and retained styling."""
    overlay = replace(_overlay(7), metadata={"timestamp_match_type": "retained"})
    display = _Display()
    lane = _lane(
        display,
        overlay_source=_OverlaySource(overlay),
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
