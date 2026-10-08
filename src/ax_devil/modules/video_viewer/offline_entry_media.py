"""Open an offline playlist entry's sources on a worker thread and hand them to the GUI thread.

The GUI thread never waits for file work. `EntryOpening` opens an entry's video and overlay sources in the
background and delivers the result as `EntryMedia`; whoever holds that media owns it until `release_media`.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Generic, TypeVar

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from ax_devil.modules.data_sources import FileFrameSource, FileOverlaySource
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.plugin_system import get_file_decoder_factory
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistencePolicy,
    OverlayPersistenceSettings,
)
from ax_devil.modules.workspace import (
    EntryLane,
    FileOverlaySourceSpec,
    LiveVideoContent,
    OverlayContent,
    PlaylistEntry,
    SeekableVideoContent,
)

logger = get_logger(__name__)

_T = TypeVar("_T")


class _BackgroundCall(Generic[_T]):
    """Hand one worker result to GUI-thread callbacks.

    The worker only appends its result and never holds it afterwards, and the GUI thread drops the callbacks when it
    takes the result, so neither side can finalize the other's objects.
    """

    def __init__(self, on_done: Callable[[_T], None], on_progress: Callable[[str], None]) -> None:
        self.results: list[_T] = []
        self._on_done: Callable[[_T], None] | None = on_done
        self._on_progress: Callable[[str], None] | None = on_progress

    def progress(self, message: str) -> None:
        """Forward a progress message unless the call already finished."""
        if self._on_progress is not None:
            self._on_progress(message)

    def finish(self) -> None:
        """Deliver the result, if the work produced one, and drop the callbacks."""
        on_done = self._on_done
        self._on_done = None
        self._on_progress = None
        if on_done is not None and self.results:
            on_done(self.results.pop())


class _GuiDispatcher(QObject):
    """Queue background-call notifications onto the thread that created it, the GUI thread."""

    progressed = Signal(object, str)
    finished = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.progressed.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    @Slot(object, str)
    def _progress(self, call: _BackgroundCall[object], message: str) -> None:
        call.progress(message)

    @Slot(object)
    def _finish(self, call: _BackgroundCall[object]) -> None:
        call.finish()


class _SerialWorker:
    """One daemon thread that runs submitted jobs in order, started on first use."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._jobs: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def submit(self, job: Callable[[], None]) -> None:
        """Queue *job* behind the jobs already submitted."""
        self._jobs.put(job)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            job()
            del job


_dispatcher: _GuiDispatcher | None = None
_workers: dict[str, _SerialWorker] = {}


def run_in_background(
    work: Callable[[Callable[[str], None]], _T],
    on_done: Callable[[_T], None],
    *,
    on_progress: Callable[[str], None] = lambda _message: None,
    name: str,
) -> None:
    """Run *work* on a worker thread and call *on_done* with its result on the GUI thread.

    Work with the same *name* runs on one thread, one at a time and in order. Call from the GUI thread. *work*
    receives a callback that reports progress messages to *on_progress* on the GUI thread. If *work* raises, the error
    is logged and *on_done* is not called, so work that acquires resources returns its failures as part of the
    result. *work* must not keep references to its result after returning it.
    """
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _GuiDispatcher()
    dispatcher = _dispatcher
    worker = _workers.setdefault(name, _SerialWorker(name))
    call = _BackgroundCall(on_done, on_progress)

    def report(message: str) -> None:
        dispatcher.progressed.emit(call, message)

    def run() -> None:
        try:
            call.results.append(work(report))
        except Exception:
            logger.exception(f"Background work '{name}' failed")
        dispatcher.finished.emit(call)

    worker.submit(run)


@dataclass(slots=True)
class OpenedLane:
    """One considered entry lane with its opened overlay source and the file-derived data it shows first."""

    lane: EntryLane
    lane_index: int
    video: SeekableVideoContent
    source_index: int
    overlay_source: FileOverlaySource | None = None
    overlay_policy: OverlayPersistencePolicy | None = None
    scene_history: SceneHistory | None = None
    alignment_report: OverlayAlignmentReport | None = None


@dataclass(slots=True)
class EntryMedia:
    """Every source opened for one playlist entry; the holder owns them until `release_media`.

    Lanes that share a video share one source: `OpenedLane.source_index` indexes `video_sources`. *error* is set when
    opening failed; the sources opened before the failure are still listed so they are released like any others.
    """

    video_sources: list[FileFrameSource] = field(default_factory=list)
    lanes: list[OpenedLane] = field(default_factory=list)
    error: str | None = None

    def overlay_sources(self) -> list[FileOverlaySource]:
        """Return the overlay sources of all lanes."""
        return [lane.overlay_source for lane in self.lanes if lane.overlay_source is not None]

    def close(self) -> None:
        """Stop every source and wait for its worker. Blocks; safe on any thread and more than once.

        A source that fails to stop is logged, and the others still close.
        """
        for source in self.video_sources:
            try:
                source.stop()
                source.wait(2000)
            except Exception:
                logger.exception("Failed to stop an offline video source")
        for overlay_source in self.overlay_sources():
            overlay_source.close()

    def delete_later(self) -> None:
        """Schedule deletion of every source. Call on the GUI thread after `close`."""
        for source in self.video_sources:
            source.deleteLater()
        for overlay_source in self.overlay_sources():
            overlay_source.deleteLater()


def create_frame_source(video: SeekableVideoContent) -> FileFrameSource:
    """Open the seekable video source for *video*. Blocks while the frame index is built or loaded."""
    return FileFrameSource(
        str(video.source_spec.path),
        image_sequence_config=video.source_spec.image_sequence_config,
    )


def create_overlay_source(
    overlay: OverlayContent,
    *,
    frame_timeline: FrameTimeline,
) -> tuple[FileOverlaySource, OverlayPersistencePolicy]:
    """Open the file overlay source for *overlay*. Blocks while the decoder reads the file."""
    if not isinstance(overlay.source_spec, FileOverlaySourceSpec):
        raise RuntimeError(f"Offline overlay '{overlay.display_name}' requires a file overlay source spec.")

    try:
        source_spec = overlay.source_spec
        decoder_factory = get_file_decoder_factory(source_spec.handler_type)
        if source_spec.decoder_kwargs:
            decoder_factory = partial(decoder_factory, **source_spec.decoder_kwargs)
        overlay_source = FileOverlaySource(
            source_spec.path,
            decoder_factory,
            source_spec.handler_type,
            timestamp_fallback_policy=source_spec.timestamp_fallback_policy,
            frame_timeline=frame_timeline,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to create overlay source for {overlay.display_name}: {exc}") from exc

    return overlay_source, OverlayPersistencePolicy(OverlayPersistenceSettings.default_enabled())


def place_scene_history(
    overlay_source: FileOverlaySource | None,
    overlay_policy: OverlayPersistencePolicy | None,
    frame_timeline: FrameTimeline,
) -> SceneHistory | None:
    """Place an overlay's history on the video as the lane's lookup and sticky selection show it."""
    if overlay_source is None or overlay_policy is None:
        return None
    allow_previous, max_sample_age_us = overlay_policy.sample_selection()
    return overlay_source.scene_history(
        frame_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us
    )


def _require_seekable_video(video: SeekableVideoContent | LiveVideoContent) -> SeekableVideoContent:
    if isinstance(video, SeekableVideoContent):
        return video
    raise TypeError("Offline viewer requires seekable video content.")


def _open_lanes(
    media: EntryMedia,
    entry: PlaylistEntry,
    lane_indices: tuple[int, ...],
    *,
    report: Callable[[str], None],
    abandoned: threading.Event,
) -> None:
    """Open sources into *media* lane by lane, so a failure or abandonment leaves only owned sources behind.

    Abandonment is checked after every blocking open, so a discarded entry never starts its derived analysis.
    """
    source_index_by_video: dict[str, int] = {}
    for position, lane_index in enumerate(lane_indices):
        if abandoned.is_set():
            return
        lane = entry.lanes[lane_index]
        video = _require_seekable_video(lane.video)
        source_index = source_index_by_video.get(video.content_id)
        if source_index is None:
            report(f"Building frame index: {video.display_name}")
            source_index = len(media.video_sources)
            media.video_sources.append(create_frame_source(video))
            source_index_by_video[video.content_id] = source_index
            if abandoned.is_set():
                return
            # Timing analysis reads the whole index; do it here so the lane's diagnostics never wait for it.
            media.video_sources[source_index].get_timing_profile()

        opened = OpenedLane(lane=lane, lane_index=lane_index, video=video, source_index=source_index)
        media.lanes.append(opened)
        if lane.overlay is None:
            continue
        if abandoned.is_set():
            return
        if len(lane_indices) == 1:
            report(f"Parsing overlay data: {lane.overlay.display_name}")
        else:
            report(f"Parsing overlay data ({position + 1}/{len(lane_indices)}): {lane.overlay.display_name}")
        frame_timeline = media.video_sources[source_index].get_frame_timeline()
        opened.overlay_source, opened.overlay_policy = create_overlay_source(
            lane.overlay, frame_timeline=frame_timeline
        )
        if abandoned.is_set():
            return
        opened.scene_history = place_scene_history(opened.overlay_source, opened.overlay_policy, frame_timeline)
        opened.alignment_report = opened.overlay_source.analyze_alignment(frame_timeline)


def open_entry_media(
    entry: PlaylistEntry,
    lane_indices: tuple[int, ...],
    report: Callable[[str], None],
    *,
    gui_thread: QThread,
    abandoned: threading.Event,
) -> EntryMedia:
    """Open the sources of *entry*'s lanes at *lane_indices* and move them to *gui_thread*.

    Blocks; run it on a worker thread. *report* receives progress messages. Never raises: a failure is recorded in
    `EntryMedia.error`. Once *abandoned* is set it opens nothing more and returns what it already opened.
    """
    media = EntryMedia()
    try:
        _open_lanes(media, entry, lane_indices, report=report, abandoned=abandoned)
    except Exception as exc:
        logger.error(f"Failed to open playlist entry: {exc}", exc_info=True)
        media.error = str(exc)
    for source in media.video_sources:
        source.moveToThread(gui_thread)
    for overlay_source in media.overlay_sources():
        overlay_source.moveToThread(gui_thread)
    return media


def _close_media(media_box: list[EntryMedia], _report: Callable[[str], None]) -> EntryMedia:
    media = media_box.pop()
    media.close()
    return media


def release_media(media: EntryMedia) -> None:
    """Close *media*'s sources on a worker thread, then delete them on the GUI thread. Call on the GUI thread."""
    # The worker takes the media out of the box so the GUI thread holds the last reference again.
    run_in_background(partial(_close_media, [media]), EntryMedia.delete_later, name="offline-entry-release")


class EntryOpening:
    """One entry opening in the background for a viewer that may stop wanting it."""

    def __init__(self, *, on_status: Callable[[str], None], on_opened: Callable[[EntryMedia], None]) -> None:
        self._abandoned = threading.Event()
        self._on_status: Callable[[str], None] | None = on_status
        self._on_opened: Callable[[EntryMedia], None] | None = on_opened

    def start(self, entry: PlaylistEntry, lane_indices: tuple[int, ...]) -> None:
        """Start opening the sources of *entry*'s lanes at *lane_indices*. Call on the GUI thread."""
        run_in_background(
            partial(
                open_entry_media,
                entry,
                lane_indices,
                gui_thread=QThread.currentThread(),
                abandoned=self._abandoned,
            ),
            self._finish,
            on_progress=self._report_status,
            name="offline-entry-open",
        )

    def abandon(self) -> None:
        """Stop wanting the result: open nothing more and release whatever was opened instead of delivering it."""
        self._abandoned.set()
        self._on_status = None
        self._on_opened = None

    def _report_status(self, message: str) -> None:
        if self._on_status is not None:
            self._on_status(message)

    def _finish(self, media: EntryMedia) -> None:
        on_opened = self._on_opened
        self._on_status = None
        self._on_opened = None
        if on_opened is None:
            release_media(media)
            return
        on_opened(media)
