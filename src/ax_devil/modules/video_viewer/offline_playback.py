"""Playback and accepted frame delivery over borrowed offline video sources."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack

from PySide6.QtCore import QObject, Signal

from ax_devil.core.data_types import FrameData
from ax_devil.core.playback_speed import DEFAULT_PLAYBACK_SPEED, clamp_playback_speed
from ax_devil.core.time_units import us_to_seconds
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_viewer.offline_frame_pool import (
    FrameDeliveryRelay,
    PooledVideoSource,
    PresentedFrame,
    SecondaryFrameRelay,
)


class OfflinePlayback(QObject):
    """Own playback, coalescing and generation acceptance, without owning the borrowed sources."""

    frameReady = Signal(int, FrameData)
    playingChanged = Signal(bool)
    seekStarted = Signal()

    def __init__(self, sources: Sequence[FileFrameSource], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._closed = False
        self._controls: SeekableVideoControlPanel | None = None
        self._speed = DEFAULT_PLAYBACK_SPEED
        self._source_pool = [
            PooledVideoSource(source, FrameDeliveryRelay(index, self), index) for index, source in enumerate(sources)
        ]
        self._is_playing = False
        self._presented_frame: PresentedFrame | None = None
        self._min_valid_primary_generation: int | None = None
        self._secondary_frame_relay = SecondaryFrameRelay(self)
        self._secondary_frame_relay.frameReady.connect(self._display_secondary_frame)
        primary = self._primary_source()
        self._current_frame = self._bounded_frame(primary.get_current_frame()) if primary is not None else 0
        with ExitStack() as rollback:
            rollback.callback(self._secondary_frame_relay.deleteLater)
            for pooled in self._source_pool:
                rollback.callback(pooled.relay.deleteLater)
            if primary is not None:
                primary.sourceFinished.connect(self._on_video_finished)
                rollback.callback(primary.sourceFinished.disconnect, self._on_video_finished)
            for pooled in self._source_pool:
                pooled.relay.frameReady.connect(self._on_frame_ready)
                pooled.source.frameReady.connect(pooled.relay.deliver)
                rollback.callback(pooled.source.frameReady.disconnect, pooled.relay.deliver)
            rollback.pop_all()

    @property
    def total_frames(self) -> int:
        """Return the primary video's length."""
        source = self._primary_source()
        return source.get_total_frames() if source is not None else 0

    @property
    def current_frame(self) -> int:
        """Return the requested position, which may still be decoding."""
        return self._current_frame

    @property
    def presented_frame(self) -> PresentedFrame | None:
        """Return the last frame accepted for presentation."""
        return self._presented_frame

    @property
    def is_playing(self) -> bool:
        """Return whether playback is running."""
        return self._is_playing

    def follow(self, controls: SeekableVideoControlPanel) -> None:
        """Push the timeline and playback state to one transport panel."""
        if self._closed:
            return
        self._controls = controls
        source = self._primary_source()
        if source is None:
            controls.show_video(total_frames=0, frame_rate=0.0)
        else:
            controls.show_video(
                source.get_total_frames(),
                float(source.fps),
                source.get_cached_ranges,
                source.get_duration_s(),
                source.peek_frame_seconds,
            )
        controls.set_playback_speed(self._speed, emit_signals=False)
        controls.sync_playback_state(self.is_playing)
        presented = self.presented_frame
        controls.set_current_frame(self.current_frame, presented.seconds if presented is not None else None)

    def toggle(self) -> None:
        """Toggle playback."""
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def _show_playing(self, playing: bool) -> None:
        if self._controls is not None:
            self._controls.sync_playback_state(playing)

    def close(self) -> None:
        """Disconnect delivery and drop controls; never release the borrowed media."""
        if self._closed:
            return
        self._closed = True
        self._controls = None
        self._is_playing = False
        primary = self._primary_source()
        if primary is not None:
            primary.sourceFinished.disconnect(self._on_video_finished)
        self._secondary_frame_relay.frameReady.disconnect(self._display_secondary_frame)
        self._secondary_frame_relay.deleteLater()
        for pooled in self._source_pool:
            pooled.source.frameReady.disconnect(pooled.relay.deliver)
            pooled.relay.frameReady.disconnect(self._on_frame_ready)
            pooled.relay.deleteLater()
        self._source_pool.clear()

    def play(self) -> None:
        """Start playback for the active runtime."""
        primary_source = self._primary_source()
        if primary_source is None:
            return

        total_frames = self.total_frames
        at_end_of_media = total_frames > 0 and primary_source.get_current_frame() >= total_frames - 1

        self._reset_secondaries()

        if at_end_of_media:
            primary_source.reset_to_start()
            self._current_frame = 0
        playing = primary_source.play()
        self._is_playing = playing
        self._update_min_valid_primary_generation()
        self.playingChanged.emit(playing)
        self._show_playing(playing)

    def pause(self) -> None:
        """Pause playback for the active runtime."""
        primary_source = self._primary_source()
        if primary_source is None:
            return

        primary_source.pause()
        self._is_playing = False
        self.playingChanged.emit(False)
        self._reset_secondaries()
        self._show_playing(False)

    def seek(self, frame_num: int) -> None:
        """Jump the primary source to a specific frame."""
        primary_source = self._primary_source()
        if primary_source is None:
            return

        self._reset_secondaries()

        target = self._bounded_frame(frame_num)
        self._current_frame = target
        self.seekStarted.emit()
        primary_source.jump_to(target)
        self._update_min_valid_primary_generation()

    def step(self, delta: int) -> None:
        """Step the primary source by *delta* frames."""
        self.seek(self.current_frame + delta)

    def _primary_source(self) -> FileFrameSource | None:
        """Return the primary video source for the active runtime."""
        if not self._source_pool:
            return None
        return self._source_pool[0].source

    def set_speed(self, speed: float) -> None:
        """Apply playback speed to all active video sources."""
        playback_speed = clamp_playback_speed(speed)
        self._speed = playback_speed
        if self._controls is not None:
            self._controls.set_playback_speed(playback_speed, emit_signals=False)
        for pooled in self._source_pool:
            pooled.source.set_playback_speed(playback_speed)

    def _on_frame_ready(self, source_index: int, frame_data: FrameData) -> None:
        if self._closed:
            return
        if source_index == 0 and self._is_stale_primary_frame(frame_data):
            return

        if source_index == 0:
            self._current_frame = self._bounded_frame(frame_data.frame_id.sequence_id)
            self._presented_frame = PresentedFrame(self._current_frame, us_to_seconds(frame_data.frame_id.timestamp_us))
            if self._controls is not None:
                self._controls.set_current_frame(self.current_frame, self._presented_frame.seconds)
        self.frameReady.emit(source_index, frame_data)
        if source_index == 0:
            self._sync_secondary_video_positions(self.current_frame)

    def _display_secondary_frame(
        self, pooled_source: PooledVideoSource, generation: int, requested_frame: int, frame_data: FrameData | None
    ) -> None:
        if self._closed or pooled_source not in self._source_pool:
            return
        if not pooled_source.complete_frame_request(generation, requested_frame, self._secondary_frame_relay):
            return
        if frame_data is not None:
            self.frameReady.emit(pooled_source.source_index, frame_data)

    def _reset_secondaries(self) -> None:
        for pooled_source in self._source_pool[1:]:
            pooled_source.reset_frame_requests()

    def _sync_secondary_video_positions(self, target_frame: int) -> None:
        for pooled_source in self._source_pool[1:]:
            if pooled_source.source.get_total_frames() <= 0:
                continue
            pooled_source.request_frame(
                max(0, min(target_frame, pooled_source.source.get_total_frames() - 1)), self._secondary_frame_relay
            )

    def _on_video_finished(self) -> None:
        if self._closed:
            return
        self._is_playing = False
        self.playingChanged.emit(False)
        self._show_playing(False)

    def _update_min_valid_primary_generation(self) -> None:
        primary_source = self._primary_source()
        if primary_source is None:
            return
        self._min_valid_primary_generation = primary_source.get_position_generation()

    def _is_stale_primary_frame(self, frame_data: FrameData) -> bool:
        if self._min_valid_primary_generation is None or frame_data.metadata is None:
            return False
        generation = frame_data.metadata.get("position_generation")
        return isinstance(generation, int) and generation < self._min_valid_primary_generation

    def _bounded_frame(self, frame_number: int) -> int:
        if self.total_frames <= 0:
            return 0
        return max(0, min(frame_number, self.total_frames - 1))
