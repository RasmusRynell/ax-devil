"""Stand-ins and builders for live stream tests: transports, a test clock, content and synchronized frames."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from PySide6.QtGui import QImage

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.base import FrameSource, OverlaySource
from ax_devil.modules.filtering import FilterConfig, build_default_filter_config
from ax_devil.modules.scene.model import Scene, TimeSlice
from ax_devil.modules.synchronization import SyncResult, TimestampedData
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_viewer.stream_media_controller import StreamMediaController
from ax_devil.modules.workspace.core import (
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlayContent,
)

OverlaySpec: TypeAlias = LiveRTSPOverlaySourceSpec | LiveMQTTOverlaySourceSpec | LiveWebSocketOverlaySourceSpec


RTSP_OVERLAY = LiveRTSPOverlaySourceSpec(handler_type="LIVE")


MQTT_OVERLAY = LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local")


class Transport(FrameSource, OverlaySource):
    """Live transport test double that records how it was opened and its lifecycle."""

    def __init__(self, url: str, options: dict[str, object]) -> None:
        super().__init__()
        self.url = url
        self.options = options
        self.filter_config = build_default_filter_config()
        self.plays = 0
        self.pauses = 0
        self.stops = 0
        self.deletes = 0

    def play(self) -> bool:
        self.plays += 1
        return True

    def pause(self) -> None:
        self.pauses += 1

    def stop(self) -> None:
        self.stops += 1

    def wait(self, timeout: int = 2000) -> bool:
        return True

    def deleteLater(self) -> None:
        self.deletes += 1

    @property
    def handler_type(self) -> str:
        return "LIVE"

    def get_filter_config(self) -> FilterConfig:
        return self.filter_config


LiveTransports = tuple[list[Transport], list[Transport]]


class FakeClock:
    """Monotonic clock that only moves when a test advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


MakeController = Callable[..., StreamMediaController]


def make_live_content(overlay: OverlaySpec | None = None, name: str = "cam-1") -> LiveVideoContent:
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
        overlays=(OverlayContent(display_name="overlay", source_spec=overlay),) if overlay else (),
    )


def make_frame_id(seconds: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=seconds, timestamp_monotime_us=seconds * 1_000_000.0)


def make_frame(seconds: int) -> FrameData:
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0)
    return FrameData(frame_id=make_frame_id(seconds), content=image, source_id="video")


def make_overlay(seconds: int) -> OverlayData:
    return OverlayData(frame_id=make_frame_id(seconds), content=Scene(time_slice=TimeSlice(start=0, end=1)))


def make_sync_result(seconds: int = 7) -> SyncResult[FrameData, OverlayData]:
    return SyncResult(
        frame=TimestampedData(data=make_frame(seconds), capture_time=seconds, arrival_time=seconds),
        overlay=TimestampedData(data=make_overlay(seconds), capture_time=seconds, arrival_time=seconds),
    )


def displayed_ids(displayed: list[VideoFrameWithOverlays]) -> list[int | None]:
    return [item.frame.frame_id for item in displayed]
