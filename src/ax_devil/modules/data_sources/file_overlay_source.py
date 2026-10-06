"""File-based overlay source implementation.

Reads overlay data from files through direct frame-identifier lookup.
"""

from pathlib import Path

from PySide6.QtCore import QObject

from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.alignment_diagnostics import (
    analyze_overlay_alignment,
    analyze_overlay_sequence_alignment,
)
from ax_devil.modules.data_sources.file_data_provider.base import FileDataProviderFactory, FrameIdentifierDataProvider
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.diagnostics.metrics_store import source_identity
from ax_devil.modules.filtering import FilterConfig
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy

logger = get_logger(__name__)


class FileOverlaySource(QObject):
    """Pull-based file overlay lookup with explicit provider ownership."""

    def __init__(
        self,
        file_path: Path,
        file_data_handler: FileDataProviderFactory,
        handler_type: str,
        source_id: str = "file_overlay",
        timestamp_fallback_policy: TimestampFallbackPolicy | None = None,
        parent: QObject | None = None,
        frame_timeline: FrameTimeline | None = None,
    ) -> None:
        super().__init__(parent)
        self.source_id = source_id
        self._handler_type = handler_type
        self.file_path = file_path
        self._closed = False
        self._frame_timeline = frame_timeline

        self.data_provider: FrameIdentifierDataProvider = file_data_handler(file_path)
        self.data_provider.set_timestamp_fallback_policy(timestamp_fallback_policy or TimestampFallbackPolicy())

        logger.debug(f"[{self.source_id}] FileOverlaySource initialized with {self.get_total_frames()} frames")

    @property
    def diagnostics_id(self) -> str:
        """Identify observations from the owned provider, when it publishes diagnostics."""
        return source_identity(self.data_provider)

    @property
    def handler_type(self) -> str:
        return self._handler_type

    def close(self) -> None:
        """Close the owned data provider once."""
        if self._closed:
            return
        self._closed = True
        try:
            self.data_provider.close()
            logger.debug(f"[{self.source_id}] Data provider closed")
        except Exception as e:
            logger.error(f"[{self.source_id}] Error closing data provider: {e}")

    def get_total_frames(self) -> int:
        """Get total number of overlay frames."""
        return int(self.data_provider.get_total_frames())

    def analyze_alignment(self, video_timeline: FrameTimeline) -> OverlayAlignmentReport:
        """Analyze how this overlay source aligns to decoded video timestamps."""
        policy = self.data_provider.get_timestamp_fallback_policy()
        if self.data_provider.uses_sequence_lookup():
            return analyze_overlay_sequence_alignment(
                handler_type=self._handler_type,
                total_video_frames=video_timeline.count,
                overlay_sequences=self.data_provider.get_available_sequences(),
                policy=policy,
            )
        return analyze_overlay_alignment(
            handler_type=self._handler_type,
            video_timeline=video_timeline,
            overlay_timestamps_us=self.data_provider.get_available_frames(),
            policy=policy,
        )

    def scene_history(
        self, video_timeline: FrameTimeline, *, allow_previous: bool, max_sample_age_us: int | None
    ) -> SceneHistory:
        """Return this overlay's events and entity appearances placed on the video's frames as lookup shows them.

        *allow_previous* and *max_sample_age_us* are the sticky-overlay selection the viewer applies on top of lookup.
        """
        return self.data_provider.scene_history(
            video_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us
        )

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        """Return timestamp fallback policy for this overlay source."""
        return self.data_provider.get_timestamp_fallback_policy()

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        """Update timestamp fallback policy after exact and sequence lookup fail."""
        self.data_provider.set_timestamp_fallback_policy(policy)

    def get_overlay_at_frame(self, frame_id: FrameIdentifier, *, allow_previous: bool = False) -> OverlayData | None:
        """Return a matched or allowed past sample, preserving its original timestamp."""
        lookup_result = self.data_provider.lookup_by_frame_id(
            frame_id, allow_previous=allow_previous, frame_timeline=self._frame_timeline
        )
        if lookup_result.scene is None:
            logger.debug(f"[{self.source_id}] No data available for frame_id {frame_id}")
            return None

        # Return Scene directly - drawing will be done later at display layer
        return OverlayData(
            content=lookup_result.scene,
            frame_id=FrameIdentifier(
                sequence_id=(
                    lookup_result.matched_sequence_id
                    if lookup_result.matched_sequence_id is not None
                    else frame_id.sequence_id
                ),
                timestamp_monotime_us=(
                    lookup_result.matched_timestamp_us
                    if lookup_result.matched_timestamp_us is not None
                    else frame_id.timestamp_monotime_us
                ),
            ),
            source_id=self.source_id,
            metadata=lookup_result.to_overlay_metadata(),
        )

    def get_filter_config(self) -> FilterConfig:
        """Return the filter configuration exposed by the data provider."""
        filter_config = self.data_provider.get_filter_config()
        if filter_config is None:
            raise ValueError("Filter configuration must be implemented and cannot be None")
        return filter_config
