"""RTSP source behavior with an offline session and the real processing worker."""

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest
from ax_devil_rtsp import SceneMetadata, StartCancelledError, StreamConfig, StreamError, VideoSample
from PySide6.QtCore import QTimer
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, OverlayData
from ax_devil.modules.data_sources.live import rtsp_source
from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder, RTSPSource
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Entity, EntityId, Scene, TimeSlice

CAPTURE_NS = 1_752_336_601_033_397_000


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


class _Session:
    """Offline stand-in for StreamSession that records its lifecycle."""

    def __init__(
        self,
        url: str,
        config: StreamConfig,
        *,
        on_video: Callable[[VideoSample[Any]], None],
        on_metadata: Callable[[SceneMetadata], None] | None,
        on_failure: Callable[[BaseException], None],
    ) -> None:
        self.config = config
        self.on_video = on_video
        self.on_metadata = on_metadata
        self.on_failure = on_failure
        self.start_calls = 0
        self.stop_calls = 0
        self.start_error: Exception | None = None
        self.failure_while_starting: Exception | None = None
        self.block_start = False
        self._stopped = threading.Event()
        self.release_start = threading.Event()
        self.release_start.set()

    @property
    def is_running(self) -> bool:
        """Fake sessions have no receive thread to join."""
        return False

    def start(self) -> None:
        """Connect, fail, or wait like a slow camera until stopped."""
        self.start_calls += 1
        if self.block_start:
            self._stopped.wait(5)
            self.release_start.wait(5)
            raise StartCancelledError("stopped while starting")
        if self.start_error is not None:
            raise self.start_error
        if self.failure_while_starting is not None:
            self.on_failure(self.failure_while_starting)

    def stop(self) -> None:
        """Record a non-blocking stop request."""
        self.stop_calls += 1
        self._stopped.set()

    def join(self, timeout: float | None = None) -> None:
        """Match StreamSession's join interface; fake sessions finish synchronously."""


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[_Session]:
    """Replace the network session; each RTSPSource appends the one it opened."""
    opened: list[_Session] = []

    def open_session(*args: Any, **kwargs: Any) -> _Session:
        opened.append(_Session(*args, **kwargs))
        return opened[-1]

    monkeypatch.setattr(rtsp_source, "StreamSession", open_session)
    return opened


def _sample(pixels: np.ndarray[Any, np.dtype[np.uint8]], capture_ns: int | None = CAPTURE_NS) -> VideoSample[Any]:
    return VideoSample(pixels, rtp_timestamp=0, capture_time_ns=capture_ns, keyframe=True)


@pytest.mark.parametrize("embedded", [False, True])
def test_frames_and_optional_overlays_use_one_session(qtbot: QtBot, sessions: list[_Session], embedded: bool) -> None:
    """Both modes retain capture timestamps, image ownership, and independent arrivals."""
    overlay = RTSPOverlayDecoder(_Decoder(), "test") if embedded else None
    source = RTSPSource("rtsp://example", overlay=overlay, timeout_s=10)
    session = sessions[0]
    frames: list[FrameData] = []
    overlays: list[OverlayData] = []
    source.frameReady.connect(frames.append)
    source.overlayReady.connect(overlays.append)
    pixels = np.full((2, 3, 4), 100, dtype=np.uint8)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: session.start_calls == 1)
        session.on_video(_sample(pixels))
        qtbot.waitUntil(lambda: len(frames) == 1)
        pixels.fill(0)
        assert frames[0].content.pixelColor(0, 0).red() == 100
        assert frames[0].frame_id.sequence_id == 1
        assert frames[0].frame_id.timestamp_us == pytest.approx(CAPTURE_NS / 1000, rel=0, abs=1)
        assert overlays == []
        assert session.config.metadata is embedded
        if embedded:
            assert session.on_metadata is not None
            session.on_metadata(SceneMetadata("bad", 0, None))
            session.on_metadata(SceneMetadata("valid", 0, None))
            qtbot.waitUntil(lambda: len(overlays) == 1)
            assert overlays[0].frame_id.timestamp_us == 1752336601000000
            assert len(frames) == 1
        else:
            assert session.on_metadata is None
            assert source.get_filter_config() is None
    finally:
        source.stop()
        source.deleteLater()
    assert source.wait()
    assert session.stop_calls == 1


def test_frames_without_capture_time_are_dropped(qtbot: QtBot, sessions: list[_Session]) -> None:
    """Frames that cannot be synchronized never reach the display."""
    source = RTSPSource("rtsp://example", timeout_s=10)
    frames: list[FrameData] = []
    source.frameReady.connect(frames.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: sessions[0].start_calls == 1)
        sessions[0].on_video(_sample(np.zeros((2, 2, 4), dtype=np.uint8), capture_ns=None))
        sessions[0].on_video(_sample(np.zeros((2, 2, 4), dtype=np.uint8)))
        qtbot.waitUntil(lambda: len(frames) == 1)
        assert frames[0].frame_id.timestamp_us == pytest.approx(CAPTURE_NS / 1000, rel=0, abs=1)
    finally:
        source.stop()
        source.deleteLater()


def test_pause_resume_keeps_session_and_stop_is_terminal(qtbot: QtBot, sessions: list[_Session]) -> None:
    """Pausing retains the connection; repeated shutdown never restarts production."""
    source = RTSPSource("rtsp://example", timeout_s=10)
    session = sessions[0]
    try:
        assert source.play()
        qtbot.waitUntil(lambda: session.start_calls == 1)
        source.pause()
        assert not source._worker.is_playing()
        assert session.stop_calls == 0
        assert source.play()
        assert source._worker.is_playing()
        source.stop()
        source.stop()
        assert not source.play()
        assert source.wait()
        assert session.start_calls == 1
        assert session.stop_calls == 1
    finally:
        source.stop()
        source.deleteLater()


@pytest.mark.parametrize(
    "failure",
    [
        "PLAY failed with 503 Service Unavailable",
        "the stream has no scene metadata; Axis URLs need analytics=polygon",
    ],
)
def test_start_failure_retries_until_session_connects(
    qtbot: QtBot, sessions: list[_Session], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Temporary RTSP or metadata startup failures retry with a fresh session."""
    monkeypatch.setattr(rtsp_source, "RETRY_INITIAL_DELAY_S", 0.01)
    source = RTSPSource("rtsp://example", timeout_s=10)
    sessions[0].start_error = StreamError(failure)
    errors: list[str] = []
    events: list[str] = []
    source.sourceError.connect(errors.append)
    source.sourceReconnecting.connect(events.append)
    source.sourceConnected.connect(lambda: events.append("connected"))
    try:
        assert source.play()
        qtbot.waitUntil(lambda: len(sessions) == 2 and sessions[1].start_calls == 1)
        qtbot.waitUntil(lambda: events == [f"Failed to start RTSP: {failure}", "connected"])
        assert errors == []
    finally:
        source.stop()
        assert source.wait()
        source.deleteLater()


def test_authentication_failure_is_terminal(qtbot: QtBot, sessions: list[_Session]) -> None:
    """Bad credentials do not cause an endless automatic reconnect loop."""
    source = RTSPSource("rtsp://example", timeout_s=10)
    sessions[0].start_error = StreamError("DESCRIBE was refused with 401 Unauthorized: check credentials")
    errors: list[str] = []
    reconnecting: list[str] = []
    source.sourceError.connect(errors.append)
    source.sourceReconnecting.connect(reconnecting.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: bool(errors))
        assert errors == ["Failed to start RTSP: DESCRIBE was refused with 401 Unauthorized: check credentials"]
        assert reconnecting == []
        assert source.wait()
        assert len(sessions) == 1
    finally:
        source.stop()
        source.deleteLater()


def test_stop_cancels_a_slow_connection_promptly_without_error(qtbot: QtBot, sessions: list[_Session]) -> None:
    """Stopping while the camera has not answered returns at once and ends the worker silently."""
    source = RTSPSource("rtsp://example", timeout_s=10)
    sessions[0].block_start = True
    sessions[0].release_start.clear()
    errors: list[str] = []
    callbacks: list[bool] = []
    destroyed: list[bool] = []
    source.destroyed.connect(lambda: destroyed.append(True))
    source.sourceError.connect(errors.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: sessions[0].start_calls == 1)
        source.retire()
        QTimer.singleShot(0, lambda: callbacks.append(True))
        qtbot.waitUntil(lambda: bool(callbacks))
        assert not destroyed
        assert errors == []
    finally:
        sessions[0].release_start.set()
        assert source.wait()
    qtbot.waitUntil(lambda: bool(destroyed))


def test_failure_after_start_reconnects_embedded_metadata(
    qtbot: QtBot, sessions: list[_Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A metadata-enabled session that ends is replaced and returns to live without manual retry."""
    monkeypatch.setattr(rtsp_source, "RETRY_INITIAL_DELAY_S", 0.01)
    source = RTSPSource("rtsp://example", overlay=RTSPOverlayDecoder(_Decoder(), "test"), timeout_s=10)
    errors: list[str] = []
    events: list[str] = []
    source.sourceError.connect(errors.append)
    source.sourceReconnecting.connect(events.append)
    source.sourceConnected.connect(lambda: events.append("connected"))
    try:
        assert source.play()
        qtbot.waitUntil(lambda: events == ["connected"])
        sessions[0].on_failure(StreamError("the camera closed the connection"))
        qtbot.waitUntil(lambda: len(sessions) == 2 and sessions[1].start_calls == 1)
        qtbot.waitUntil(
            lambda: events == ["connected", "RTSP stream ended: the camera closed the connection", "connected"]
        )
        assert sessions[1].config.metadata
        assert errors == []
    finally:
        source.stop()
        source.deleteLater()


def test_video_silence_reconnects_even_while_metadata_arrives(
    qtbot: QtBot, sessions: list[_Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata activity must not hide a stalled video track from the RTSP source."""
    monkeypatch.setattr(rtsp_source, "VIDEO_SILENCE_S", 0.05)
    monkeypatch.setattr(rtsp_source, "RETRY_INITIAL_DELAY_S", 0.01)
    source = RTSPSource("rtsp://example", overlay=RTSPOverlayDecoder(_Decoder(), "test"), timeout_s=10)
    reconnecting: list[str] = []
    connected: list[bool] = []
    errors: list[str] = []
    source.sourceReconnecting.connect(reconnecting.append)
    source.sourceConnected.connect(lambda: connected.append(True))
    source.sourceError.connect(errors.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: sessions[0].start_calls == 1)
        assert sessions[0].on_metadata is not None
        sessions[0].on_metadata(SceneMetadata("valid", 0, None))
        qtbot.waitUntil(lambda: len(sessions) >= 2 and sessions[1].start_calls == 1)
        qtbot.waitUntil(lambda: len(connected) == 2)
        assert connected == [True, True]
        assert len(reconnecting) == 1
        assert "No RTSP video frames received" in reconnecting[0]
        assert errors == []
    finally:
        source.stop()
        source.deleteLater()


def test_failure_right_after_connecting_retries_after_reporting_connection(
    qtbot: QtBot, sessions: list[_Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream that ends as PLAY returns reports reconnecting after its initial connected state."""
    monkeypatch.setattr(rtsp_source, "RETRY_INITIAL_DELAY_S", 0.01)
    source = RTSPSource("rtsp://example", timeout_s=10)
    sessions[0].failure_while_starting = StreamError("the camera closed the connection")
    events: list[str] = []
    errors: list[str] = []
    source.sourceConnected.connect(lambda: events.append("connected"))
    source.sourceReconnecting.connect(events.append)
    source.sourceError.connect(errors.append)
    try:
        assert source.play()
        qtbot.waitUntil(lambda: len(sessions) == 2 and sessions[1].start_calls == 1)
        qtbot.waitUntil(lambda: len(events) >= 3 or bool(errors))
        assert errors == []
        assert events == ["connected", "RTSP stream ended: the camera closed the connection", "connected"]
    finally:
        source.stop()
        source.deleteLater()


def test_callbacks_keep_bounded_buffers(qtbot: QtBot, sessions: list[_Session]) -> None:
    """Paused producers retain only the configured number of recent packets."""
    source = RTSPSource("rtsp://example", buffer_size=2, overlay=RTSPOverlayDecoder(_Decoder(), "test"), timeout_s=10)
    session = sessions[0]
    assert session.on_metadata is not None
    try:
        for index in range(4):
            session.on_video(VideoSample(index, rtp_timestamp=index, capture_time_ns=None, keyframe=False))
            session.on_metadata(SceneMetadata(str(index), index, None))
        assert [sample.data for sample in source.frame_buffer] == [2, 3]
        assert [document.xml for document in source.overlay_buffer] == ["2", "3"]
    finally:
        source.stop()
        source.deleteLater()
    assert not source.frame_buffer
    assert not source.overlay_buffer
