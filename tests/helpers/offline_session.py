"""Stand-ins for the displays, sources and sessions that drive an offline session in tests."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import cast

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtGui import QAction, QImage
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia
from ax_devil.modules.video_viewer.offline_frame_pool import FrameDeliveryRelay, PooledVideoSource
from ax_devil.modules.video_viewer.offline_lane import OfflineLane
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy
from ax_devil.modules.workspace.core import (
    SeekableVideoContent,
)


class FakeDisplay(QObject):
    """Display test double recording display frames."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[VideoFrameWithOverlays] = []

    def display_frame(self, frame: VideoFrameWithOverlays) -> None:
        """Record displayed frames."""
        self.frames.append(frame)

    def cleanup(self) -> None:
        """Match FrameDisplay cleanup."""

    def frame_ids(self) -> list[int | None]:
        """Return the ids of the displayed frames in order."""
        return [frame.frame.frame_id for frame in self.frames]


class StepBackDuringDisplay(FakeDisplay):
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


class FakeOverlaySource:
    """Seekable overlay test double recording direct frame lookup."""

    def __init__(self, overlay: OverlayData | None) -> None:
        self.overlay = overlay
        self.requested_frame_ids: list[FrameIdentifier] = []

    def get_overlay_at_frame(self, frame_id: FrameIdentifier, *, allow_previous: bool = False) -> OverlayData | None:
        """Return the configured overlay for a direct frame lookup."""
        self.requested_frame_ids.append(frame_id)
        return self.overlay


class FakeSignal:
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


class AsyncFrameSource:
    """Secondary frame source test double that lets tests complete requests out of order."""

    def __init__(self) -> None:
        self.frameReady = FakeSignal()
        self.requests: list[int] = []
        self.callbacks: dict[int, Callable[[FrameData | None], None]] = {}

    def request_frame_async(self, frame_number: int, callback: Callable[[FrameData | None], None]) -> None:
        """Record the callback for explicit test completion."""
        self.requests.append(frame_number)
        self.callbacks[frame_number] = callback

    def complete(self, frame_number: int) -> None:
        """Complete the latest request for *frame_number*."""
        self.callbacks[frame_number](make_frame(frame_number))

    def complete_from_worker(self, frame_number: int) -> None:
        """Complete the latest request for *frame_number* from a worker thread, as the decoder does."""
        worker = threading.Thread(target=lambda: self.complete(frame_number))
        worker.start()
        worker.join()

    def set_playback_speed(self, speed: float) -> None:
        """Accept speed changes applied to every session source."""


class PlaybackSource:
    """Primary video source test double for offline-session frame ordering."""

    def __init__(self, *, total_frames: int = 1000) -> None:
        self.sourceFinished = FakeSignal()
        self.frameReady = FakeSignal()
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

    def get_frame_timeline(self) -> FrameTimeline:
        """Return a timeline of four frames 40 ms apart."""
        return FrameTimeline.from_timestamps(frame * 40_000 for frame in range(4))

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
        self.frameReady.emit(make_frame(frame_number, position_generation=generation))
        QCoreApplication.processEvents()


def make_frame(frame_number: int, *, position_generation: int | None = None) -> FrameData:
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_monotime_us=float(frame_number * 1000))
    metadata = {"position_generation": position_generation} if position_generation is not None else None
    return FrameData(
        content=QImage(2, 2, QImage.Format.Format_RGB32),
        frame_id=frame_id,
        source_id="video",
        metadata=metadata,
    )


def make_overlay(frame_number: int) -> OverlayData:
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_monotime_us=float(frame_number * 1000))
    return OverlayData(
        content=Scene(time_slice=TimeSlice(start=frame_number, end=frame_number)),
        frame_id=frame_id,
        source_id="overlay",
        metadata={"source": "direct"},
    )


def make_lane(
    display: FakeDisplay,
    *,
    overlay_source: FakeOverlaySource | None = None,
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
        export_action=QAction("Export video"),
        video_source=cast(FileFrameSource | None, video_source),
        source_index=source_index,
        overlay_policy=overlay_policy,
        tools_panel=tools_panel,
    )


def make_session(
    qtbot: QtBot, lanes: list[OfflineLane], sources: list[object], *, jobs: BackgroundJobs | None = None
) -> OfflineSession:
    """Wire a session over test sources the way `OfflineSession.build` wires opened media.

    `build` needs real displays; the source pool and the frame slot are private, so this is the one place tests
    reach them.
    """
    container = QWidget()
    qtbot.addWidget(container)
    pool = [
        PooledVideoSource(
            source=cast(FileFrameSource, source),
            relay=FrameDeliveryRelay(index, container),
            source_index=index,
        )
        for index, source in enumerate(sources)
    ]
    session = OfflineSession(lanes, container, media=EntryMedia(), source_pool=pool, jobs=jobs)
    for pooled in pool:
        pooled.source.frameReady.connect(pooled.relay.deliver)
        pooled.relay.frameReady.connect(session._on_frame_ready)  # noqa: SLF001
    return session


def make_primary_session(
    qtbot: QtBot, *, current_frame: int = 0, total_frames: int = 1000
) -> tuple[OfflineSession, FakeDisplay, PlaybackSource]:
    display = FakeDisplay()
    source = PlaybackSource(total_frames=total_frames)
    source.current_frame = current_frame
    session = make_session(qtbot, [make_lane(display, video_source=source)], [source])
    return session, display, source
