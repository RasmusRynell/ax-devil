"""Startup request descriptions and resolution for the Workspace module."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.content import Content, LiveOverlayMode, PlaylistContent
from ax_devil.modules.workspace.core.intake import WorkspaceIntake, is_video_file

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VideoFileStartup:
    """Open an offline video file with optional overlay, at startup or from the running app."""

    video_path: Path
    overlay_path: Path | None = None
    handler_type: str | None = None
    display_name: str | None = None

    @property
    def needs_handler(self) -> bool:
        """Return whether an overlay file still waits for a data handler choice."""
        return self.overlay_path is not None and self.handler_type is None

    @property
    def label(self) -> str:
        """Return a short one-line name for lists such as recent videos."""
        name = self.display_name or self.video_path.name
        return f"{name}  +  {self.overlay_path.name}" if self.overlay_path is not None else name

    @property
    def description(self) -> str:
        """Return the full file paths behind this request."""
        lines = [str(self.video_path)]
        if self.overlay_path is not None:
            lines.append(f"Overlay: {self.overlay_path}")
        return "\n".join(lines)

    def resolve(self, intake: WorkspaceIntake) -> tuple[Content, ...]:
        """Return the seekable video this request opens."""
        return (
            intake.create_seekable_video(
                video_path=self.video_path,
                display_name=self.display_name,
                overlay_path=self.overlay_path,
                handler_type=self.handler_type,
            ),
        )


@dataclass(frozen=True, slots=True)
class LiveStreamStartup:
    """Open a live RTSP stream on startup using config defaults and CLI overrides."""

    host: str
    username: str
    password: str
    camera_head: int = 1
    resolution: str = "1280x720"
    display_name: str | None = None
    stream_url: str | None = None
    overlay_mode: LiveOverlayMode = LiveOverlayMode.NONE
    handler_type: str | None = None
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    analytics_data_source_key: str = ""
    device_api_protocol: str = "https"
    websocket_topic: str = ""
    websocket_channel_id: int = 1

    def resolve(self, intake: WorkspaceIntake) -> tuple[Content, ...]:
        """Return the live stream this request opens."""
        return (
            intake.create_live_stream(
                host=self.host,
                username=self.username,
                password=self.password,
                camera_head=self.camera_head,
                resolution=self.resolution,
                display_name=self.display_name,
                stream_url=self.stream_url,
                overlay_mode=self.overlay_mode,
                handler_type=self.handler_type,
                mqtt_host=self.mqtt_host,
                mqtt_port=self.mqtt_port,
                mqtt_username=self.mqtt_username,
                mqtt_password=self.mqtt_password,
                analytics_data_source_key=self.analytics_data_source_key,
                device_api_protocol=self.device_api_protocol,
                websocket_topic=self.websocket_topic,
                websocket_channel_id=self.websocket_channel_id,
            ),
        )


@dataclass(frozen=True, slots=True)
class ResolvedPlaylistStartup:
    """Open already-resolved playlist contents on startup."""

    playlists: tuple[PlaylistContent, ...]

    def resolve(self, intake: WorkspaceIntake) -> tuple[Content, ...]:
        """Return the playlists, which are already resolved."""
        return self.playlists


StartupContent = VideoFileStartup | LiveStreamStartup | ResolvedPlaylistStartup
"""A request to open content, resolved with ``startup.resolve(intake)``."""


def video_file_requests(paths: Sequence[Path], intake: WorkspaceIntake) -> tuple[VideoFileStartup, ...]:
    """Turn dropped or picked files into video open requests.

    Every video file opens on its own. One video with one other file opens that file as its overlay, with the data
    handler chosen when exactly one decoder may read it; otherwise the request still needs a handler choice.
    """
    videos = [path for path in paths if is_video_file(path)]
    others = [path for path in paths if not is_video_file(path)]
    if len(videos) != 1 or len(others) != 1:
        if others:
            logger.info(f"Ignoring {len(others)} non-video file(s) that do not pair with exactly one video")
        return tuple(VideoFileStartup(video_path=video) for video in videos)
    overlay = others[0]
    decoders = intake.file_decoder_options_for(overlay)
    if not decoders:
        logger.info(f"No data handler reads {overlay.name}; opening {videos[0].name} without an overlay")
        return (VideoFileStartup(video_path=videos[0]),)
    handler_type = decoders[0].handler_type if len(decoders) == 1 else None
    return (VideoFileStartup(video_path=videos[0], overlay_path=overlay, handler_type=handler_type),)
