"""Tests for FrameIdentifier and monotime calculations."""

from typing import cast
from unittest.mock import Mock, patch

import numpy as np

from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.file_data_provider.lookup_metadata import OverlayLookupResult
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder import DecodedFrame
from ax_devil.modules.data_sources.file_frame_delivery import FileFrameDelivery
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy


def _lookup_result(scene: Scene | None) -> OverlayLookupResult:
    return OverlayLookupResult(
        scene=scene,
        requested_timestamp_us=0,
        matched_timestamp_us=0 if scene is not None else None,
        match_type="exact" if scene is not None else "missing",
        timestamp_fallback_policy=TimestampFallbackPolicy(),
    )


class TestFileFrameSourceFrameData:
    """Test frame identifiers built by FileFrameSource."""

    def test_read_decoded_frame_delegates_to_source_delivery(self) -> None:
        """Non-playback consumers read through the source-owned delivery path."""
        source = FileFrameSource.__new__(FileFrameSource)
        source.total_frames = 5
        decoded_frame = DecodedFrame(
            frame_index=4,
            pixels=np.zeros((2, 3, 3), dtype=np.uint8),
            timestamp_us=160_000.0,
        )

        class _Delivery:
            def __init__(self) -> None:
                self.requested_frames: list[int] = []

            def read_decoded_frame(self, frame_number: int) -> DecodedFrame:
                self.requested_frames.append(frame_number)
                return decoded_frame

        delivery = _Delivery()
        source._frame_delivery = cast(FileFrameDelivery, delivery)

        assert source.read_decoded_frame(4) is decoded_frame
        assert delivery.requested_frames == [4]

    def test_build_frame_data_uses_decoded_frame_timestamp(self) -> None:
        """Frame source should preserve the timestamp carried by the decoded frame."""
        source = FileFrameSource.__new__(FileFrameSource)
        source.source_id = "test-video"
        decoded_frame = DecodedFrame(
            frame_index=2,
            pixels=np.zeros((2, 3, 3), dtype=np.uint8),
            timestamp_us=120_000.0,
            period_after_s=0.04,
            source_timing_metadata={
                "video_timestamp_source": "pts_time_base_minus_first_pts",
                "video_pts": 12,
                "video_first_pts": 0,
                "video_time_base": "1/100",
            },
        )

        frame_data = source._build_frame_data(decoded_frame)

        assert frame_data.frame_id == FrameIdentifier(sequence_id=2, timestamp_monotime_us=120_000.0)
        assert frame_data.metadata == {
            "video_timestamp_source": "pts_time_base_minus_first_pts",
            "video_pts": 12,
            "video_first_pts": 0,
            "video_time_base": "1/100",
            "video_period_after_s": 0.04,
            "video_period_source": "pts_delta",
        }

    def test_get_frame_period_after_s_prefers_reader_periods(self) -> None:
        """Playback period should come from indexed PTS timing when available."""
        source = FileFrameSource.__new__(FileFrameSource)
        source.fps = 25.0
        source._frame_delivery = Mock()
        source._frame_delivery.get_frame_period_after_s.return_value = 0.08

        assert source.get_frame_period_after_s(1) == 0.08

    def test_get_frame_period_after_s_falls_back_to_fps(self) -> None:
        """Invalid or absent PTS timing should fall back to FPS-derived period."""
        source = FileFrameSource.__new__(FileFrameSource)
        source.fps = 25.0
        source._frame_delivery = Mock()
        source._frame_delivery.get_frame_period_after_s.return_value = None

        assert source.get_frame_period_after_s(1) == 0.04

    def test_get_frame_timestamps_us_uses_runtime_lookup_normalization(self) -> None:
        """Diagnostics should normalize fractional timestamps like provider lookup does."""
        source = FileFrameSource.__new__(FileFrameSource)
        source.total_frames = 2
        source.source_id = "test_video"
        source._frame_timestamps_us = None
        source._frame_delivery = Mock()
        source._frame_delivery.get_frame_times_us.return_value = (33_366, 66_733)

        assert source.get_frame_timestamps_us() == (33_366, 66_733)

    def test_get_frame_timestamps_us_does_not_cache_partial_timeline(self) -> None:
        """A partial timestamp timeline should not poison later diagnostics reads."""
        source = FileFrameSource.__new__(FileFrameSource)
        source.total_frames = 3
        source.source_id = "test_video"
        source._frame_timestamps_us = None
        source._frame_delivery = Mock()
        source._frame_delivery.get_frame_times_us.side_effect = [(1_000,), (1_000, 2_000, 3_000)]

        assert source.get_frame_timestamps_us() == (1_000,)
        assert source._frame_timestamps_us is None
        assert source.get_frame_timestamps_us() == (1_000, 2_000, 3_000)


class TestFileOverlaySourceIntegration:
    """Test FileOverlaySource with real FrameIdentifier."""

    def test_close_is_idempotent(self) -> None:
        """Closing a file overlay source closes its provider exactly once."""
        overlay_source = FileOverlaySource.__new__(FileOverlaySource)
        overlay_source.source_id = "test_overlay"
        overlay_source.data_provider = Mock()
        overlay_source._frame_timeline = None
        overlay_source._closed = False

        overlay_source.close()
        overlay_source.close()

        overlay_source.data_provider.close.assert_called_once_with()

    def test_get_overlay_at_frame_with_frame_identifier(self) -> None:
        """Test that FileOverlaySource correctly uses FrameIdentifier."""
        # Create a mock FileOverlaySource
        overlay_source = FileOverlaySource.__new__(FileOverlaySource)
        overlay_source.source_id = "test_overlay"
        overlay_source.data_provider = Mock()
        overlay_source._frame_timeline = None

        with patch.object(overlay_source, "get_total_frames", return_value=100):
            # Mock a scene
            mock_scene = Scene(time_slice=TimeSlice(0, 0))
            overlay_source.data_provider.lookup_by_frame_id.return_value = _lookup_result(mock_scene)

            # Create FrameIdentifier with real monotime calculation
            frame_id = FrameIdentifier(
                sequence_id=5,
                timestamp_monotime_us=166666.66666666666,  # 5/30fps * 1000000
            )

            # Test the actual function
            result = overlay_source.get_overlay_at_frame(frame_id)

            assert result is not None
            # Verify it's a real OverlayData object
            assert isinstance(result, OverlayData)
            assert result.frame_id.timestamp_monotime_us == 0
            assert result.frame_id.sequence_id == 5
            assert result.frame_id.timestamp_monotime_us == 0
            assert result.content == mock_scene
            assert result.source_id == "test_overlay"

            # Verify the data provider was called with the FrameIdentifier
            overlay_source.data_provider.lookup_by_frame_id.assert_called_once_with(
                frame_id, allow_previous=False, frame_timeline=None
            )

    def test_get_overlay_at_frame_no_data(self) -> None:
        """Test FileOverlaySource handles when data provider returns None."""
        overlay_source = FileOverlaySource.__new__(FileOverlaySource)
        overlay_source.source_id = "test_overlay"
        overlay_source.data_provider = Mock()
        overlay_source._frame_timeline = None

        with patch.object(overlay_source, "get_total_frames", return_value=100):
            # Mock data provider returning None
            overlay_source.data_provider.lookup_by_frame_id.return_value = _lookup_result(None)

            frame_id = FrameIdentifier(sequence_id=5, timestamp_monotime_us=166666.66666666666)

            result = overlay_source.get_overlay_at_frame(frame_id)

            assert result is None
