from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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
    assert content.metadata == {}
    assert content.overlays[0].source_spec == FileOverlaySourceSpec(
        path=Path("/tmp/overlay.jsonl"),
        handler_type="FILE",
    )
    assert content.overlays[0].metadata == {}
    assert content.overlays[0].display_name == "overlay"


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


def test_intake_rejects_mqtt_overlay_without_broker_host() -> None:
    intake = WorkspaceIntake(_OptionProvider())

    with pytest.raises(ValueError, match="MQTT Host is required for MQTT overlay mode"):
        intake.create_live_stream(
            host="camera.local",
            display_name="Camera",
            username="root",
            password="pass",
            overlay_mode=LiveOverlayMode.MQTT,
            handler_type="LIVE",
            analytics_data_source_key="analytics/source",
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
        device_api_protocol="http",
    )

    assert content.overlays[0].source_spec == LiveWebSocketOverlaySourceSpec(
        handler_type="LIVE",
        topic="com.axis.scene.frame.v1",
        channel_id=3,
        device_api_protocol="http",
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"websocket_topic": ""}, "Topic is required for WebSocket overlay mode"),
        ({"websocket_channel_id": 0}, "Channel ID must be a positive integer"),
        ({"device_api_protocol": "ftp"}, "Device API protocol must be http or https"),
    ],
)
def test_intake_rejects_invalid_websocket_connection_settings(overrides: dict[str, Any], message: str) -> None:
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
        ({"analytics_data_source_key": ""}, "Data Source is required for MQTT overlay mode"),
        ({"mqtt_port": 0}, "MQTT Port must be between 1 and 65535"),
        ({"mqtt_port": 65536}, "MQTT Port must be between 1 and 65535"),
        ({"device_api_protocol": "ftp"}, "Device API protocol must be http or https"),
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
