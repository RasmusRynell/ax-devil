"""Base interface for data providers that work with FrameIdentifier objects."""

from collections.abc import Callable
from typing import Protocol

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.file_data_provider.lookup_metadata import OverlayLookupResult
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.filtering.filter_config import FilterConfig
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy


class FrameIdentifierDataProvider(Protocol):
    """Data provider that works with FrameIdentifier objects.

    This interface allows data providers to choose which part of the FrameIdentifier
    to use for lookups (sequence_id, timestamp_monotime_us, or both).
    """

    def load_by_frame_id(self, frame_identifier: FrameIdentifier) -> Scene | None:
        """Load scene data using FrameIdentifier.

        Args:
            frame_identifier: FrameIdentifier containing sequence_id and timestamp_monotime_us

        Returns:
            Scene data if found, None if not found
        """
        ...

    def lookup_by_frame_id(
        self,
        frame_identifier: FrameIdentifier,
        *,
        allow_previous: bool = False,
        frame_timeline: FrameTimeline | None = None,
    ) -> OverlayLookupResult:
        """Resolve a sample, optionally allowing the latest past timestamp beyond matching tolerance."""
        ...

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        """Return the timestamp fallback policy used after exact and sequence lookup fail."""
        ...

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        """Update timestamp fallback policy used after exact and sequence lookup fail."""
        ...

    def get_total_frames(self) -> int:
        """Get total number of frames/scenes available."""
        ...

    def get_available_frames(self) -> set[int]:
        """Get set of available frame indices."""
        ...

    def scene_history(
        self, video_timeline: FrameTimeline, *, allow_previous: bool, max_sample_age_us: int | None
    ) -> SceneHistory:
        """Return the source's events and entity appearances placed on the video's frames as lookup shows them."""
        ...

    def uses_sequence_lookup(self) -> bool:
        """Return whether provider-owned sequence lookup is enabled."""
        ...

    def get_available_sequences(self) -> set[int]:
        """Get set of available sequence identifiers."""
        ...

    def close(self) -> None:
        """Close the data provider and clean up resources."""
        ...

    def get_filter_config(self) -> FilterConfig | None:
        """Return filter configuration for this overlay source or None if no filtering is supported."""
        ...


FileDataProviderFactory = Callable[..., FrameIdentifierDataProvider]
