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
from ax_devil.modules.background_jobs import BackgroundJobs, JobScope
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia, LaneOverlay
from ax_devil.modules.video_viewer.offline_lane import OfflineLane
from ax_devil.modules.video_viewer.offline_playback import OfflinePlayback
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter
from ax_devil.modules.video_viewer.timing_diagnostics_widget import OverlayAlignmentIndicator, TimingDiagnosticsWidget
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

    def __init__(self, *, total_frames: int = 1000) -> None:
        self.total_frames = total_frames
        self.frameReady = FakeSignal()
        self.requests: list[int] = []
        self.callbacks: dict[int, Callable[[FrameData | None], None]] = {}

    def get_total_frames(self) -> int:
        """Return the secondary video length."""
        return self.total_frames

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
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_us=frame_number * 1000)
    metadata = {"position_generation": position_generation} if position_generation is not None else None
    return FrameData(
        content=QImage(2, 2, QImage.Format.Format_RGB32),
        frame_id=frame_id,
        source_id="video",
        metadata=metadata,
    )


def make_overlay(frame_number: int) -> OverlayData:
    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_us=frame_number * 1000)
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
    analysis_jobs: JobScope | None = None,
    timing_controls: TimingDiagnosticsWidget | None = None,
    alignment_indicator: OverlayAlignmentIndicator | None = None,
) -> OfflineLane:
    return OfflineLane(
        content=cast(SeekableVideoContent, object()),
        name="lane",
        display=cast(FrameDisplay, display),
        overlay=LaneOverlay(cast(FileOverlaySource, overlay_source), overlay_policy or OverlayPersistencePolicy())
        if overlay_source is not None
        else None,
        export_action=QAction("Export video"),
        video_source=cast(FileFrameSource, video_source or PlaybackSource()),
        source_index=source_index,
        presenter=SceneFramePresenter(),
        tools_panel=tools_panel or cast(MediaToolsPanel, _InspectionPanel()),
        filter_model=SessionFilter(),
        timing_controls=timing_controls or cast(TimingDiagnosticsWidget, object()),
        alignment_indicator=alignment_indicator or cast(OverlayAlignmentIndicator, object()),
        analysis_jobs=analysis_jobs or BackgroundJobs().scope(display),
    )


class _InspectionPanel:
    """Minimal scene inspection sink for lane presentation tests."""

    def update_scene(self, *args: object, **kwargs: object) -> None:
        """Accept an inspection update."""

    def clear_scene(self) -> None:
        """Accept an empty scene."""

    def cleanup(self) -> None:
        """Release no resources."""


def make_session(
    qtbot: QtBot, lanes: list[OfflineLane], sources: list[object], *, jobs: BackgroundJobs | None = None
) -> OfflineSession:
    """Compose the session with its real playback controller."""
    container = QWidget()
    qtbot.addWidget(container)
    playback = OfflinePlayback([cast(FileFrameSource, source) for source in sources])
    scope = lanes[0].analysis_jobs if lanes else (jobs or BackgroundJobs()).scope(container)
    return OfflineSession(lanes, container, media=EntryMedia(), playback=playback, analysis_jobs=scope)


def make_playback(qtbot: QtBot, sources: list[object]) -> OfflinePlayback:
    """Build a playback controller over fake video sources."""
    owner = QWidget()
    playback = OfflinePlayback([cast(FileFrameSource, source) for source in sources], owner)
    qtbot.addWidget(owner, before_close_func=lambda widget, owner=owner: playback.close())
    return playback


def make_primary_playback(
    qtbot: QtBot, *, current_frame: int = 0, total_frames: int = 1000
) -> tuple[OfflinePlayback, list[int], PlaybackSource]:
    source = PlaybackSource(total_frames=total_frames)
    source.current_frame = current_frame
    playback = make_playback(qtbot, [source])
    frames: list[int] = []
    playback.frameReady.connect(lambda _index, frame: frames.append(frame.frame_id.sequence_id))
    return playback, frames, source
