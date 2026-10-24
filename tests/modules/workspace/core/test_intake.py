from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ax_devil.core.device_connection import DeviceProtocol
from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    LiveOverlayMode,
    LiveWebSocketOverlaySourceSpec,
    OverlayFile,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake


class _OptionProvider:
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="FILE", display_name="File Decoder"),)

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="LIVE", display_name="Live Decoder"),)


def test_intake_rejects_unknown_file_overlay_handler_before_creating_content() -> None:
    intake = WorkspaceIntake(_OptionProvider())

    with pytest.raises(ValueError, match="Unknown overlay file handler type: MISSING"):
        intake.create_seekable_video(
            video_path=Path("/tmp/video.mp4"),
            display_name="video.mp4",
            overlays=(OverlayFile(Path("/tmp/overlay.jsonl"), "MISSING"),),
        )


def test_intake_creates_seekable_content_for_valid_file_overlay_selection() -> None:
    intake = WorkspaceIntake(_OptionProvider())

    content = intake.create_seekable_video(
        video_path=Path("/tmp/video.mp4"),
        display_name="video.mp4",
        overlays=(OverlayFile(Path("/tmp/overlay.jsonl"), "FILE"),),
    )

    assert isinstance(content, SeekableVideoContent)
    assert content.source_spec.path == Path("/tmp/video.mp4")
    assert content.overlays[0].source_spec == FileOverlaySourceSpec(
        path=Path("/tmp/overlay.jsonl"),
        handler_type="FILE",
    )
    assert content.overlays[0].display_name == "overlay"


def test_intake_creates_one_overlay_per_file_and_rejects_the_selection_when_any_handler_is_unknown() -> None:
    intake = WorkspaceIntake(_OptionProvider())
    first, second = OverlayFile(Path("/tmp/a.jsonl"), "FILE"), OverlayFile(Path("/tmp/b.jsonl"), "FILE")

    content = intake.create_seekable_video(
        video_path=Path("/tmp/video.mp4"), display_name="video.mp4", overlays=(first, second)
    )

    assert [overlay.source_spec for overlay in content.overlays] == [
        FileOverlaySourceSpec(path=first.path, handler_type="FILE"),
        FileOverlaySourceSpec(path=second.path, handler_type="FILE"),
    ]
    with pytest.raises(ValueError, match="Unknown overlay file handler type: MISSING"):
        intake.create_seekable_video(
            video_path=Path("/tmp/video.mp4"),
            display_name="video.mp4",
            overlays=(first, OverlayFile(second.path, "MISSING")),
        )


def test_intake_rejects_unknown_live_overlay_handler_before_creating_content() -> None:
    intake = WorkspaceIntake(_OptionProvider())

    with pytest.raises(ValueError, match="Unknown live overlay handler type: MISSING"):
        intake.create_live_stream(
            host="camera.local",
            display_name="Camera",
            username="root",
            password="pass",
            overlay_mode=LiveOverlayMode.RTSP,
            handler_type="MISSING",
        )


def test_intake_creates_websocket_overlay_spec() -> None:
    intake = WorkspaceIntake(_OptionProvider())

    content = intake.create_live_stream(
        host="camera.local",
        display_name="Camera",
        username="root",
        password="pass",
        overlay_mode=LiveOverlayMode.WEBSOCKET,
        handler_type="LIVE",
        websocket_topic="com.axis.scene.frame.v1",
        websocket_channel_id=3,
        device_api_protocol=DeviceProtocol.HTTP,
        device_port=8080,
    )

    assert (content.source_spec.device_api_protocol, content.source_spec.device_port) == (DeviceProtocol.HTTP, 8080)
    assert content.overlays[0].source_spec == LiveWebSocketOverlaySourceSpec(
        handler_type="LIVE", topic="com.axis.scene.frame.v1", channel_id=3
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"websocket_topic": ""}, "Topic is required for WebSocket overlay mode"),
        ({"websocket_channel_id": 0}, "Channel ID must be a positive integer"),
        ({"device_port": 99999}, "Port must be between 0 and 65535"),
        ({"rtsp_port": 0}, "RTSP Port must be between 1 and 65535"),
        ({"device_api_protocol": DeviceProtocol.HTTP, "device_port": 443}, "HTTP cannot use port 443"),
        ({"device_api_protocol": DeviceProtocol.HTTPS, "device_port": 80}, "HTTPS cannot use port 80"),
    ],
)
def test_intake_rejects_invalid_live_connection_settings(overrides: dict[str, Any], message: str) -> None:
    intake = WorkspaceIntake(_OptionProvider())
    arguments: dict[str, Any] = {
        "host": "camera.local",
        "display_name": "Camera",
        "username": "root",
        "password": "pass",
        "overlay_mode": LiveOverlayMode.WEBSOCKET,
        "handler_type": "LIVE",
        "websocket_topic": "com.axis.scene.frame.v1",
        "websocket_channel_id": 1,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        intake.create_live_stream(**arguments)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"mqtt_host": ""}, "MQTT Host is required for MQTT overlay mode"),
        ({"analytics_data_source_key": ""}, "Data Source is required for MQTT overlay mode"),
        ({"mqtt_port": 0}, "MQTT Port must be between 1 and 65535"),
        ({"mqtt_port": 65536}, "MQTT Port must be between 1 and 65535"),
    ],
)
def test_intake_rejects_invalid_mqtt_connection_settings(overrides: dict[str, Any], message: str) -> None:
    intake = WorkspaceIntake(_OptionProvider())
    arguments: dict[str, Any] = {
        "host": "camera.local",
        "display_name": "Camera",
        "username": "root",
        "password": "pass",
        "overlay_mode": LiveOverlayMode.MQTT,
        "handler_type": "LIVE",
        "mqtt_host": "broker.local",
        "analytics_data_source_key": "analytics/source",
    }
    arguments.update(overrides)

    with pytest.raises(ValueError, match=message):
        intake.create_live_stream(**arguments)


def test_intake_rejects_non_positive_camera_head() -> None:
    intake = WorkspaceIntake(_OptionProvider())

    with pytest.raises(ValueError, match="Camera Head must be a positive integer"):
        intake.create_live_stream(
            host="camera.local", display_name="Camera", username="root", password="pass", camera_head=0
        )


def test_intake_rejects_an_empty_device_host() -> None:
    intake = WorkspaceIntake(_OptionProvider())

    with pytest.raises(ValueError, match="Enter the device host"):
        intake.create_live_stream(host="", display_name="Camera", username="root", password="pass")
