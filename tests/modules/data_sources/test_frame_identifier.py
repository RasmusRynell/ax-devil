"""Frame and overlay identifiers through initialized file sources."""

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier
from ax_devil.modules.data_sources.file_frame_delivery import FileFrameDelivery
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.plugins.decoders.mot.provider import MOTChallengeSceneDataProvider


@pytest.fixture
def frame_source(video_file_factory: Callable[[float, int], Path]) -> Iterator[FileFrameSource]:
    """Open a private source over immutable shared media and always stop its worker."""
    source = FileFrameSource(str(video_file_factory(0.2, 30)), source_id="identifiers")
    try:
        yield source
    finally:
        source.stop()
        assert source.wait()


def test_video_reads_preserve_frame_identity_and_decoded_timing(frame_source: FileFrameSource, qtbot: QtBot) -> None:
    """Synchronous and asynchronous reads expose the same frame index, PTS, and cadence."""
    source = frame_source
    decoded = source.read_decoded_frame(2)
    assert decoded is not None
    assert decoded.frame_index == 2
    assert decoded.timestamp_us == pytest.approx(2_000_000 / 30)
    frames: list[FrameData | None] = []
    source.request_frame_async(2, frames.append)
    qtbot.waitUntil(lambda: len(frames) == 1)
    frame = frames[0]
    assert frame is not None
    assert frame.source_id == "identifiers"
    assert frame.frame_id == FrameIdentifier(sequence_id=2, timestamp_monotime_us=decoded.timestamp_us)
    assert (frame.content.width(), frame.content.height()) == (decoded.pixels.shape[1], decoded.pixels.shape[0])
    assert frame.metadata is not None
    assert all(frame.metadata[key] == value for key, value in decoded.source_timing_metadata.items())
    assert frame.metadata["video_timestamp_source"] == "pts_time_base_minus_first_pts"
    assert frame.metadata["video_period_source"] == "pts_delta"
    assert frame.metadata["video_period_after_s"] == pytest.approx(1 / 30)
    assert source.get_frame_period_after_s(2) == pytest.approx(1 / 30)
    assert source.get_frame_timestamps_us() == (0, 33_333, 66_666, 100_000, 133_333, 166_666)


def test_partial_video_timeline_can_recover(frame_source: FileFrameSource, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial diagnostic result must not prevent a later complete timeline from being read."""
    complete = (0, 33_333, 66_666, 100_000, 133_333, 166_666)
    timelines = iter([(0,), complete])
    monkeypatch.setattr(FileFrameDelivery, "get_frame_times_us", lambda self: next(timelines))

    assert frame_source.get_frame_timestamps_us() == (0,)
    assert frame_source.get_frame_timestamps_us() == complete
    assert frame_source.get_frame_timestamps_us() == complete


def test_video_cadence_falls_back_when_indexed_period_is_unavailable(
    frame_source: FileFrameSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indexed periods take precedence over FPS, with FPS as the missing-period fallback."""
    monkeypatch.setattr(FileFrameDelivery, "get_frame_period_after_s", lambda self, frame: 0.08)
    assert frame_source.get_frame_period_after_s(1) == 0.08
    monkeypatch.setattr(FileFrameDelivery, "get_frame_period_after_s", lambda self, frame: None)
    assert frame_source.get_frame_period_after_s(1) == pytest.approx(1 / 30)


def test_overlay_lookup_preserves_matched_identity_and_closes_once(tmp_path: Path) -> None:
    """A real MOT provider returns its matched sequence, handles missing frames, and is closed once."""
    path = tmp_path / "detections.txt"
    path.write_text("1,7,100,100,50,50,0.9,1,0.8\n", encoding="utf-8")
    source = FileOverlaySource(path, MOTChallengeSceneDataProvider, "MOT_FILE", source_id="overlays")
    with patch.object(source.data_provider, "close", wraps=source.data_provider.close) as close:
        try:
            overlay = source.get_overlay_at_frame(FrameIdentifier(sequence_id=0, timestamp_monotime_us=123_456))
            assert overlay is not None
            assert overlay.source_id == "overlays"
            assert overlay.frame_id == FrameIdentifier(sequence_id=0, timestamp_monotime_us=0)
            assert len(overlay.content.entities) == 1
            assert source.get_overlay_at_frame(FrameIdentifier(sequence_id=5, timestamp_monotime_us=166_666)) is None
        finally:
            source.close()
            source.close()
        close.assert_called_once_with()
