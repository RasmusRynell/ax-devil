"""Workspace intake seam for decoder options and content construction."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ax_devil.modules.workspace.core.content import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveMQTTOverlaySourceSpec,
    LiveOverlayMode,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlayContent,
    OverlaySourceKind,
    SeekableVideoContent,
)

VIDEO_FILE_SUFFIXES = (".mp4", ".avi", ".mkv", ".mov")


def is_video_file(path: Path) -> bool:
    """Return whether *path* names a video file the Workspace can open."""
    return path.suffix.lower() in VIDEO_FILE_SUFFIXES


@dataclass(frozen=True, slots=True)
class WorkspaceDecoderOption:
    """One decoder option exposed in Workspace-owned terms."""

    handler_type: str
    display_name: str
    description: str = ""
    file_extensions: tuple[str, ...] = ()

    def may_read(self, path: Path) -> bool:
        """Return whether this decoder may read *path*, judged by its declared file extensions."""
        return not self.file_extensions or path.suffix.lower() in self.file_extensions


class WorkspaceDecoderOptionProvider(Protocol):
    """Provider of decoder options normalized for Workspace intake callers."""

    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        """Return decoders that can read overlay files for seekable video."""

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        """Return decoders that can read live overlay payloads."""


class WorkspaceIntake:
    """Validate Workspace selections and construct Workspace content records."""

    def __init__(self, option_provider: WorkspaceDecoderOptionProvider) -> None:
        self._option_provider = option_provider

    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        """Return file decoder options available to add-content flows."""
        return self._option_provider.file_decoder_options()

    def file_decoder_options_for(self, overlay_path: Path) -> tuple[WorkspaceDecoderOption, ...]:
        """Return file decoder options that may read *overlay_path*."""
        return tuple(option for option in self.file_decoder_options() if option.may_read(overlay_path))

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        """Return live overlay decoder options available to add-content flows."""
        return self._option_provider.live_overlay_decoder_options()

    def create_seekable_video(
        self,
        *,
        video_path: Path,
        display_name: str | None = None,
        overlay_path: Path | None = None,
        handler_type: str | None = None,
    ) -> SeekableVideoContent:
        """Create seekable Workspace video content after validating overlay selection."""
        if overlay_path is None:
            if handler_type is not None:
                raise ValueError("Overlay handler was selected without an overlay file.")
        else:
            self._require_known_handler(handler_type, self.file_decoder_options(), "overlay file")

        overlays: tuple[OverlayContent, ...] = ()
        if overlay_path is not None and handler_type is not None:
            overlays = (
                OverlayContent(
                    display_name=overlay_path.stem,
                    source_spec=FileOverlaySourceSpec(path=overlay_path, handler_type=handler_type),
                ),
            )

        return SeekableVideoContent(
            display_name=display_name or video_path.name,
            source_spec=FileVideoSourceSpec(path=video_path),
            overlays=overlays,
        )

    def create_live_stream(
        self,
        *,
        host: str,
        username: str,
        password: str,
        camera_head: int = 1,
        resolution: str = "1280x720",
        display_name: str | None = None,
        stream_url: str | None = None,
        overlay_mode: LiveOverlayMode = LiveOverlayMode.NONE,
        handler_type: str | None = None,
        mqtt_host: str = "",
        mqtt_port: int = 1883,
        mqtt_username: str = "",
        mqtt_password: str = "",
        analytics_data_source_key: str = "",
        device_api_protocol: str = "https",
        websocket_topic: str = "",
        websocket_channel_id: int = 1,
    ) -> LiveVideoContent:
        """Create live Workspace video content after validating overlay selection."""
        if camera_head < 1:
            raise ValueError("Camera Head must be a positive integer.")
        if overlay_mode is LiveOverlayMode.MQTT:
            if not mqtt_host:
                raise ValueError("MQTT Host is required for MQTT overlay mode.")
            if not analytics_data_source_key:
                raise ValueError("Data Source is required for MQTT overlay mode.")
            if not 1 <= mqtt_port <= 65535:
                raise ValueError("MQTT Port must be between 1 and 65535.")
            if device_api_protocol not in {"https", "http"}:
                raise ValueError("Device API protocol must be http or https.")
        if overlay_mode is LiveOverlayMode.WEBSOCKET:
            if not websocket_topic:
                raise ValueError("Topic is required for WebSocket overlay mode.")
            if websocket_channel_id < 1:
                raise ValueError("Channel ID must be a positive integer.")
            if device_api_protocol not in {"https", "http"}:
                raise ValueError("Device API protocol must be http or https.")
        if overlay_mode.requires_handler:
            self._require_known_handler(handler_type, self.live_overlay_decoder_options(), "live overlay")
        elif handler_type is not None:
            raise ValueError("Overlay handler was selected without an overlay mode.")

        video_spec = LiveRTSPStreamSpec(
            host=host,
            username=username,
            password=password,
            camera_head=camera_head,
            resolution=resolution,
            stream_url=stream_url,
        )
        overlays: tuple[OverlayContent, ...] = ()

        if overlay_mode is LiveOverlayMode.RTSP:
            assert handler_type is not None
            rtsp_overlay_spec = LiveRTSPOverlaySourceSpec(handler_type=handler_type)
            overlays = (
                OverlayContent(
                    display_name=rtsp_overlay_spec.source_kind.display_name,
                    source_spec=rtsp_overlay_spec,
                ),
            )
        elif overlay_mode is LiveOverlayMode.MQTT:
            assert handler_type is not None
            mqtt_overlay_spec = LiveMQTTOverlaySourceSpec(
                handler_type=handler_type,
                broker_host=mqtt_host,
                broker_port=mqtt_port,
                broker_username=mqtt_username,
                broker_password=mqtt_password,
                analytics_data_source_key=analytics_data_source_key,
                device_api_protocol=device_api_protocol,
            )
            overlays = (
                OverlayContent(
                    display_name=OverlaySourceKind.MQTT_SOURCE.display_name,
                    source_spec=mqtt_overlay_spec,
                ),
            )
        elif overlay_mode is LiveOverlayMode.WEBSOCKET:
            assert handler_type is not None
            websocket_overlay_spec = LiveWebSocketOverlaySourceSpec(
                handler_type=handler_type,
                topic=websocket_topic,
                channel_id=websocket_channel_id,
                device_api_protocol=device_api_protocol,
            )
            overlays = (
                OverlayContent(
                    display_name=OverlaySourceKind.WEBSOCKET_SOURCE.display_name,
                    source_spec=websocket_overlay_spec,
                ),
            )

        return LiveVideoContent(
            display_name=display_name or f"Live: {host}",
            source_spec=video_spec,
            overlays=overlays,
        )

    def _require_known_handler(
        self,
        handler_type: str | None,
        options: Iterable[WorkspaceDecoderOption],
        selection_name: str,
    ) -> None:
        """Reject missing or unknown decoder selections before content enters Workspace."""
        if handler_type is None:
            raise ValueError(f"Select a handler type for the {selection_name}.")
        known_handlers = {option.handler_type for option in options}
        if handler_type not in known_handlers:
            raise ValueError(f"Unknown {selection_name} handler type: {handler_type}")
