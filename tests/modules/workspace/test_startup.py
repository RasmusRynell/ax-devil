from __future__ import annotations

from pathlib import Path

from ax_devil.modules.workspace import (
    FileVideoSourceSpec,
    LiveStreamStartup,
    LiveVideoContent,
    PlaylistContent,
    PlaylistEntry,
    ResolvedPlaylistStartup,
    SeekableVideoContent,
    VideoFileStartup,
    default_workspace_intake,
)


def test_resolve_video_file_startup_returns_seekable_content() -> None:
    [content] = VideoFileStartup(video_path=Path("/tmp/example.mp4")).resolve(default_workspace_intake())

    assert isinstance(content, SeekableVideoContent)
    assert content.display_name == "example.mp4"
    assert content.source_spec.path == Path("/tmp/example.mp4")
    assert content.metadata == {}


def test_resolve_live_stream_startup_returns_live_content() -> None:
    [content] = LiveStreamStartup(host="camera.local", username="root", password="pass").resolve(
        default_workspace_intake()
    )

    assert isinstance(content, LiveVideoContent)
    assert content.display_name == "Live: camera.local"
    assert content.source_spec.host == "camera.local"
    assert content.metadata == {}


def test_resolve_resolved_playlist_startup_returns_playlists() -> None:
    video = SeekableVideoContent(display_name="video", source_spec=FileVideoSourceSpec(path=Path("/tmp/video.mp4")))
    playlist = PlaylistContent(
        display_name="Resolved",
        entries=(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),),
    )

    intake = default_workspace_intake()

    assert ResolvedPlaylistStartup(playlists=(playlist,)).resolve(intake) == (playlist,)
    assert ResolvedPlaylistStartup(playlists=()).resolve(intake) == ()
