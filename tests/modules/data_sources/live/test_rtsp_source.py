"""RTSP source behavior with an offline transport and the real processing worker."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, OverlayData
from ax_devil.modules.data_sources.live import rtsp_source
from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder, RTSPSource
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Entity, EntityId, Scene, TimeSlice


class _Decoder(PayloadToSceneDecoder):
    """Return a scene with a known device timestamp."""

    def decode(self, payload: Any) -> Scene | None:
        """Decode the test payload, including a recoverable malformed packet."""
        if payload == "bad":
            raise ValueError("Malformed metadata")
        return Scene(
            time_slice=TimeSlice(
                start=datetime(2025, 7, 12, 16, 10, 1, tzinfo=timezone.utc),
                end=datetime(2025, 7, 12, 16, 10, 1, tzinfo=timezone.utc),
            ),
            entities={EntityId("1"): Entity(id=EntityId("1"))},
        )


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace network transport while retaining its registered callbacks."""
    factory = MagicMock()
    monkeypatch.setattr(rtsp_source, "RtspDataRetriever", factory)
    return factory


@pytest.mark.parametrize("embedded", [False, True])
def test_frames_and_optional_overlays_use_one_transport(qtbot: QtBot, transport: MagicMock, embedded: bool) -> None:
    """Both modes retain capture timestamps, image ownership, and independent arrivals."""
    source = RTSPSource("rtsp://example", overlay=RTSPOverlayDecoder(_Decoder(), "test") if embedded else None)
    frames: list[FrameData] = []
    overlays: list[OverlayData] = []
    source.frameReady.connect(frames.append)
    source.overlayReady.connect(overlays.append)
    callbacks = transport.call_args.kwargs
    pixels = np.full((2, 3, 3), 100, dtype=np.uint8)
    try:
        assert source.play()
        callbacks["on_video_data"](
            {
                "data": pixels,
                "latest_rtp_data": {"human_time": "2025-07-12 16:10:01.033397 UTC"},
                "diagnostics": {"video_sample_count": 1},
            }
        )
        qtbot.waitUntil(lambda: len(frames) == 1)
        pixels.fill(0)
        assert frames[0].content.pixelColor(0, 0).red() == 100
        assert frames[0].frame_id.sequence_id == 1
        assert frames[0].frame_id.timestamp_monotime_us == pytest.approx(1752336601033397, rel=0, abs=1)
        assert overlays == []
        if embedded:
            callbacks["on_application_data"]({"data": "bad"})
            callbacks["on_application_data"]({"data": "valid"})
            qtbot.waitUntil(lambda: len(overlays) == 1)
            assert overlays[0].frame_id.timestamp_monotime_us == 1752336601000000
            assert len(frames) == 1
        else:
            assert callbacks["on_application_data"] is None
            assert source.get_filter_config() is None
    finally:
        source.stop()
        source.deleteLater()
    assert source.wait()
    transport.return_value.start.assert_called_once()
    transport.return_value.stop.assert_called_once()


def test_pause_resume_keeps_transport_and_stop_is_terminal(qtbot: QtBot, transport: MagicMock) -> None:
    """Pausing retains the connection; repeated shutdown never restarts production."""
    source = RTSPSource("rtsp://example")
    try:
        assert source.play()
        source.pause()
        assert not source._worker.is_playing()
        transport.return_value.stop.assert_not_called()
        assert source.play()
        assert source._worker.is_playing()
        transport.return_value.start.assert_called_once()
        source.stop()
        source.stop()
        assert not source.play()
        assert source.wait()
        transport.return_value.stop.assert_called_once()
    finally:
        source.stop()
        source.deleteLater()


def test_start_failure_emits_error_without_starting_worker(qtbot: QtBot, transport: MagicMock) -> None:
    """Connection failures leave the processing worker stopped."""
    transport.return_value.start.side_effect = RuntimeError("Connection failed")
    source = RTSPSource("rtsp://example")
    errors: list[str] = []
    source.sourceError.connect(errors.append)
    try:
        assert not source.play()
        assert not source._worker.isRunning()
        assert errors == ["Failed to start RTSP: Connection failed"]
    finally:
        source.stop()
        source.deleteLater()


def test_callbacks_keep_bounded_buffers(qtbot: QtBot, transport: MagicMock) -> None:
    """Paused producers retain only the configured number of recent packets."""
    source = RTSPSource("rtsp://example", buffer_size=2, overlay=RTSPOverlayDecoder(_Decoder(), "test"))
    callbacks = transport.call_args.kwargs
    try:
        for index in range(4):
            callbacks["on_video_data"]({"data": index})
            callbacks["on_application_data"]({"data": index})
        assert [packet["data"] for packet in source.frame_buffer] == [2, 3]
        assert [packet["data"] for packet in source.overlay_buffer] == [2, 3]
    finally:
        source.stop()
        source.deleteLater()
    assert not source.frame_buffer
    assert not source.overlay_buffer


def test_session_start_reports_connection_and_logs_source_without_payload(
    qtbot: QtBot, transport: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Session start reports the connection; logging identifies the source without its authenticated URL."""
    url = "rtsp://synthetic-user:synthetic-password@camera.local/stream?token=synthetic-token"
    source = RTSPSource(url)
    connected: list[bool] = []
    source.sourceConnected.connect(lambda: connected.append(True))
    try:
        with caplog.at_level("DEBUG"):
            transport.call_args.kwargs["on_session_start"]({"url": url})
        assert connected == [True]
        assert "RTSP session started for rtsp" in caplog.text
        assert "synthetic-" not in caplog.text
    finally:
        source.stop()
        source.deleteLater()
