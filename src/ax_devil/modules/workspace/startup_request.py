"""Startup request descriptions and resolution for the Workspace module."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.content import Content, LiveOverlayMode, PlaylistContent
from ax_devil.modules.workspace.intake import WorkspaceIntake, default_workspace_intake, is_video_file

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

    def __post_init__(self) -> None:
        """Normalize legacy string overlay modes at the startup boundary."""
        if isinstance(self.overlay_mode, str) and not isinstance(self.overlay_mode, LiveOverlayMode):
            object.__setattr__(self, "overlay_mode", LiveOverlayMode.from_value(self.overlay_mode))


@dataclass(frozen=True, slots=True)
class ResolvedPlaylistStartup:
    """Open already-resolved playlist contents on startup."""

    playlists: tuple[PlaylistContent, ...]


StartupContent = VideoFileStartup | LiveStreamStartup | ResolvedPlaylistStartup


def resolve_startup_content(
    startup: StartupContent,
    intake: WorkspaceIntake | None = None,
) -> tuple[Content, ...]:
    """Return Workspace content described by a startup request."""
    resolved_intake = intake or default_workspace_intake()
    if isinstance(startup, VideoFileStartup):
        return (
            resolved_intake.create_seekable_video(
                video_path=startup.video_path,
                display_name=startup.display_name,
                overlay_path=startup.overlay_path,
                handler_type=startup.handler_type,
            ),
        )
    if isinstance(startup, LiveStreamStartup):
        return (
            resolved_intake.create_live_stream(
                host=startup.host,
                username=startup.username,
                password=startup.password,
                camera_head=startup.camera_head,
                resolution=startup.resolution,
                display_name=startup.display_name,
                stream_url=startup.stream_url,
                overlay_mode=startup.overlay_mode,
                handler_type=startup.handler_type,
                mqtt_host=startup.mqtt_host,
                mqtt_port=startup.mqtt_port,
                mqtt_username=startup.mqtt_username,
                mqtt_password=startup.mqtt_password,
                analytics_data_source_key=startup.analytics_data_source_key,
                device_api_protocol=startup.device_api_protocol,
                websocket_topic=startup.websocket_topic,
                websocket_channel_id=startup.websocket_channel_id,
            ),
        )
    if isinstance(startup, ResolvedPlaylistStartup):
        return tuple(startup.playlists)
    raise TypeError(f"Unknown startup content type: {type(startup).__name__}")


def video_file_requests(paths: Sequence[Path], intake: WorkspaceIntake | None = None) -> tuple[VideoFileStartup, ...]:
    """Turn dropped or picked files into video open requests.

    Every video file opens on its own. One video with one other file opens that file as its overlay, with the data
    handler chosen when exactly one decoder may read it; otherwise the request still needs a handler choice.
    """
    resolved_intake = intake or default_workspace_intake()
    videos = [path for path in paths if is_video_file(path)]
    others = [path for path in paths if not is_video_file(path)]
    if len(videos) != 1 or len(others) != 1:
        if others:
            logger.info(f"Ignoring {len(others)} non-video file(s) that do not pair with exactly one video")
        return tuple(VideoFileStartup(video_path=video) for video in videos)
    overlay = others[0]
    decoders = resolved_intake.file_decoder_options_for(overlay)
    if not decoders:
        logger.info(f"No data handler reads {overlay.name}; opening {videos[0].name} without an overlay")
        return (VideoFileStartup(video_path=videos[0]),)
    handler_type = decoders[0].handler_type if len(decoders) == 1 else None
    return (VideoFileStartup(video_path=videos[0], overlay_path=overlay, handler_type=handler_type),)
