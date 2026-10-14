"""Open an offline playlist entry's sources on a worker thread and hand them to the GUI thread.

The GUI thread never waits for file work. `open_entry_media` opens an entry's video and overlay sources in the
background and returns `EntryMedia`; whoever holds that media owns it until `release_media`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

from PySide6.QtCore import QObject, QThread, QTimer

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.modules.background_jobs import JobContext, JobScope, background_jobs
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.plugin_system import get_file_decoder_factory
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistencePolicy,
    OverlayPersistenceSettings,
)
from ax_devil.modules.workspace.core import (
    EntryLane,
    FileOverlaySourceSpec,
    LiveVideoContent,
    OverlayContent,
    PlaylistEntry,
    SeekableVideoContent,
)

logger = get_logger(__name__)


def entry_opening_scope(owner: QObject) -> JobScope:
    """Return *owner*'s scope on the one queue every offline viewer opens entries on, one at a time."""
    return background_jobs("offline-entry-open", max_workers=1).scope(owner)


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
        """Request shutdown of every source. Blocks for bounded decoder joins; run on a worker.

        This does not guarantee worker exit. The release owner retains the media until every source's wait succeeds.
        A source that fails to stop is logged, and the others still close.
        """
        for source in self.video_sources:
            try:
                source.stop()
            except Exception:
                logger.exception("Failed to stop an offline video source")
        for overlay_source in self.overlay_sources():
            try:
                overlay_source.close()
            except Exception:
                logger.exception("Failed to close an offline overlay source")

    def delete_later(self) -> None:
        """Schedule deletion of every source. Call on the GUI thread only after every source has exited."""
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
    token: CancelToken,
) -> SceneHistory | None:
    """Place an overlay's history on the video as the lane's lookup and sticky selection show it; polls *token*."""
    if overlay_source is None or overlay_policy is None:
        return None
    allow_previous, max_sample_age_us = overlay_policy.sample_selection()
    return overlay_source.scene_history(
        frame_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us, token=token
    )


def _require_seekable_video(video: SeekableVideoContent | LiveVideoContent) -> SeekableVideoContent:
    if isinstance(video, SeekableVideoContent):
        return video
    raise TypeError("Offline viewer requires seekable video content.")


def _open_lanes(
    media: EntryMedia,
    entry: PlaylistEntry,
    lane_indices: tuple[int, ...],
    job: JobContext[str],
) -> None:
    """Open sources into *media* lane by lane, so a failure or cancellation leaves only owned sources behind.

    The job's token is polled after every blocking open, so a discarded entry never starts its derived analysis.
    """
    token = job.token
    report = job.report
    # Lanes showing the same video share one source; image sequence timing is not part of video equality.
    source_index_by_video: dict[tuple[SeekableVideoContent, ImageSequenceConfig | None], int] = {}
    for position, lane_index in token.polled(enumerate(lane_indices)):
        lane = entry.lanes[lane_index]
        video = _require_seekable_video(lane.video)
        video_key = (video, video.source_spec.image_sequence_config)
        source_index = source_index_by_video.get(video_key)
        if source_index is None:
            report(f"Building frame index: {video.display_name}")
            source_index = len(media.video_sources)
            media.video_sources.append(create_frame_source(video))
            source_index_by_video[video_key] = source_index
            token.raise_if_cancelled()
            # Timing analysis reads the whole index; do it here so the lane's diagnostics never wait for it.
            media.video_sources[source_index].get_timing_profile()

        opened = OpenedLane(lane=lane, lane_index=lane_index, video=video, source_index=source_index)
        media.lanes.append(opened)
        if lane.overlay is None:
            continue
        if len(lane_indices) == 1:
            report(f"Parsing overlay data: {lane.overlay.display_name}")
        else:
            report(f"Parsing overlay data ({position + 1}/{len(lane_indices)}): {lane.overlay.display_name}")
        frame_timeline = media.video_sources[source_index].get_frame_timeline()
        opened.overlay_source, opened.overlay_policy = create_overlay_source(
            lane.overlay, frame_timeline=frame_timeline
        )
        token.raise_if_cancelled()
        opened.scene_history = place_scene_history(opened.overlay_source, opened.overlay_policy, frame_timeline, token)
        token.raise_if_cancelled()
        opened.alignment_report = opened.overlay_source.analyze_alignment(frame_timeline, token)


def open_entry_media(
    entry: PlaylistEntry,
    lane_indices: tuple[int, ...],
    gui_thread: QThread,
    job: JobContext[str],
) -> EntryMedia:
    """Open the sources of *entry*'s lanes at *lane_indices* and move them to *gui_thread*.

    Blocks; run it as a background job. *job* receives progress messages. Never raises: a failure or cancellation is
    recorded in `EntryMedia.error`, and every source opened before it is still in the returned media. The sources can
    only be freed on the GUI thread, so they go back to the job as a result: delivered to the viewer, or, for a
    cancelled job, to `on_discard`, which releases them off the opening queue.
    """
    media = EntryMedia()
    try:
        _open_lanes(media, entry, lane_indices, job)
    except Cancelled:
        media.error = "Opening was cancelled"  # Delivered only if Cancelled came from elsewhere than the job's owner.
    except Exception as exc:
        logger.error(f"Failed to open playlist entry: {exc}", exc_info=True)
        media.error = str(exc)
    for source in media.video_sources:
        source.moveToThread(gui_thread)
    for overlay_source in media.overlay_sources():
        overlay_source.moveToThread(gui_thread)
    return media


def _close_media(media: EntryMedia, _job: JobContext[None]) -> EntryMedia:
    media.close()
    return media


def _finished_media(media: tuple[EntryMedia, ...], _job: JobContext[None]) -> list[EntryMedia]:
    """Check decoder exit without waiting or issuing another stop request."""
    finished: list[EntryMedia] = []
    for entry in media:
        try:
            exited = [source.wait(0) for source in entry.video_sources]
        except Exception:
            logger.exception("Failed to check offline video shutdown")
            continue
        if all(exited):
            finished.append(entry)
    return finished


def _already_logged(_error: Exception) -> None:
    """Drop a failure the background jobs already logged."""


class _MediaReleases(QObject):
    """Own mandatory source shutdown independently of the viewers that surrender their media.

    The process owns this service: unlike viewer scopes, its scope never closes while the application runs.
    At process exit the background job daemon policy applies; shutdown never joins decoder workers on the GUI.
    """

    def __init__(self) -> None:
        super().__init__()
        self._jobs = background_jobs("offline-entry-release", max_workers=1).scope(self)
        self._pending: list[EntryMedia] = []
        self._sweeping = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._sweep)

    def release(self, media: EntryMedia) -> None:
        """Retain *media* until worker shutdown finishes and GUI-thread deletion is scheduled."""
        self._jobs.submit(
            "release offline entry",
            partial(_close_media, media),
            on_result=self._closed,
            on_error=_already_logged,
        )

    def _closed(self, media: EntryMedia) -> None:
        self._pending.append(media)
        if not self._sweeping and not self._timer.isActive():
            self._sweep()

    def _sweep(self) -> None:
        if self._sweeping or not self._pending:
            return
        self._sweeping = True
        self._jobs.submit(
            "check offline entry shutdown",
            partial(_finished_media, tuple(self._pending)),
            on_result=self._swept,
            on_error=self._sweep_failed,
        )

    def _swept(self, finished: list[EntryMedia]) -> None:
        finished_ids = {id(media) for media in finished}
        self._pending = [media for media in self._pending if id(media) not in finished_ids]
        for media in finished:
            media.delete_later()
        self._sweeping = False
        if self._pending:
            self._timer.start()

    def _sweep_failed(self, _error: Exception) -> None:
        self._sweeping = False
        self._timer.start()


_media_releases: _MediaReleases | None = None


def release_media(media: EntryMedia) -> None:
    """Transfer *media* to the process's release owner; call on the GUI thread, without waiting."""
    global _media_releases
    if _media_releases is None:
        _media_releases = _MediaReleases()
    _media_releases.release(media)
