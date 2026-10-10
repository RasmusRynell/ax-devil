"""Shared Workspace content builders for tests."""

from __future__ import annotations

from pathlib import Path

from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    OverlayContent,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)


def make_overlay(name: str) -> OverlayContent:
    """Build a file overlay."""
    return OverlayContent(
        display_name=name,
        source_spec=FileOverlaySourceSpec(path=Path(f"/tmp/{name}.txt"), handler_type="AXIS_JSON"),
        metadata={"note": "test overlay"},
    )


def make_video(name: str = "test.mp4", overlay_count: int = 0) -> SeekableVideoContent:
    """Build a seekable video, optionally with numbered overlays."""
    return SeekableVideoContent(
        display_name=name,
        source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")),
        overlays=tuple(make_overlay(f"Overlay {index + 1}") for index in range(overlay_count)),
    )


def make_live_video(
    name: str = "live",
    overlay_source: OverlaySourceKind = OverlaySourceKind.NO_SOURCE,
) -> LiveVideoContent:
    """Build a live video, optionally with one live overlay source."""
    overlays: tuple[OverlayContent, ...] = ()
    if overlay_source is OverlaySourceKind.MQTT_SOURCE:
        overlays = (
            OverlayContent(
                display_name=OverlaySourceKind.MQTT_SOURCE.display_name,
                source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
            ),
        )
    elif overlay_source is OverlaySourceKind.RTSP_SOURCE:
        overlays = (
            OverlayContent(
                display_name=OverlaySourceKind.RTSP_SOURCE.display_name,
                source_spec=LiveRTSPOverlaySourceSpec(handler_type="LIVE"),
                metadata={"note": "test overlay"},
            ),
        )
    return LiveVideoContent(
        display_name=name,
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
        overlays=overlays,
        metadata={"note": "test camera"},
    )


def make_playlist(name: str = "Suite") -> PlaylistContent:
    """Build a two-entry playlist."""
    return PlaylistContent(
        display_name=name,
        entries=(
            PlaylistEntry(lanes=make_video("a.mp4").standalone_lanes(), default_considered=True),
            PlaylistEntry(lanes=make_video("b.mp4").standalone_lanes(), default_considered=True),
        ),
    )
