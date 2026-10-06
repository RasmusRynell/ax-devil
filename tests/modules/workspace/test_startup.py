from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from ax_devil.modules.workspace import (
    FileVideoSourceSpec,
    LiveStreamStartup,
    LiveVideoContent,
    PlaylistContent,
    PlaylistEntry,
    ResolvedPlaylistStartup,
    SeekableVideoContent,
    VideoFileStartup,
    resolve_startup_content,
)


def test_resolve_video_file_startup_returns_seekable_content() -> None:
    [content] = resolve_startup_content(VideoFileStartup(video_path=Path("/tmp/example.mp4")))

    assert isinstance(content, SeekableVideoContent)
    assert content.display_name == "example.mp4"
    assert content.source_spec.path == Path("/tmp/example.mp4")
    assert content.metadata == {}


def test_resolve_live_stream_startup_returns_live_content() -> None:
    [content] = resolve_startup_content(LiveStreamStartup(host="camera.local", username="root", password="pass"))

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

    assert resolve_startup_content(ResolvedPlaylistStartup(playlists=(playlist,))) == (playlist,)
    assert resolve_startup_content(ResolvedPlaylistStartup(playlists=())) == ()


def test_resolve_unknown_startup_type_raises() -> None:
    with pytest.raises(TypeError, match="Unknown startup content type"):
        resolve_startup_content(cast(Any, object()))
