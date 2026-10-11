from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import (
    FileVideoSourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.ui.viewer_factory import WorkspaceViewerFactory
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.workspace import DummyViewer, FakeResolutionContext, store_with


def _make_seekable(name: str) -> SeekableVideoContent:
    return SeekableVideoContent(display_name=name, source_spec=FileVideoSourceSpec(path=Path(f"/tmp/{name}")))


def test_viewer_factory_selects_offline_viewer_for_seekable_video(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    viewer_factory = WorkspaceViewerFactory(render_catalog_manager)
    store, content = store_with(_make_seekable("clip.mp4"))

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        opened = viewer_factory.open(content, consideration_query=store)

    qtbot.addWidget(opened.widget)
    assert isinstance(opened.widget, DummyViewer)
    assert opened.widget.payload is content
    assert opened.widget.render_catalog_manager is render_catalog_manager
    assert opened.widget.consideration_query is store


def test_viewer_factory_selects_live_viewer_for_live_video(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    viewer_factory = WorkspaceViewerFactory(render_catalog_manager)
    content = LiveVideoContent(
        display_name="camera",
        source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="pass"),
    )

    with patch("ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget", DummyViewer):
        opened = viewer_factory.open(content)

    qtbot.addWidget(opened.widget)
    assert isinstance(opened.widget, DummyViewer)
    assert opened.widget.payload is content
    assert opened.widget.render_catalog_manager is render_catalog_manager


def test_viewer_factory_opens_playlist_at_the_requested_entry(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    viewer_factory = WorkspaceViewerFactory(render_catalog_manager)
    first = _make_seekable("first.mp4")
    second = _make_seekable("second.mp4")
    playlist = PlaylistContent(
        display_name="playlist",
        entries=(
            PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
            PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
            PlaylistEntry(lanes=second.standalone_lanes(), default_considered=True),
        ),
    )
    store = WorkspaceStore(FakeResolutionContext())

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        opened = viewer_factory.open(playlist, start_index=2, consideration_query=store)

    qtbot.addWidget(opened.widget)
    assert isinstance(opened.widget, DummyViewer)
    assert opened.widget.payload is playlist
    assert opened.widget.start_index == 2
    assert opened.widget.consideration_query is store
    assert opened.widget.render_catalog_manager is render_catalog_manager


@pytest.mark.parametrize("start_index", [3, -1])
def test_viewer_factory_clamps_out_of_range_playlist_start_index_to_first_entry(
    qtbot: QtBot,
    render_catalog_manager: SceneRenderCatalogManager,
    start_index: int,
) -> None:
    viewer_factory = WorkspaceViewerFactory(render_catalog_manager)
    content = _make_seekable("clip.mp4")
    playlist = PlaylistContent(
        display_name="playlist",
        entries=(PlaylistEntry(lanes=content.standalone_lanes(), default_considered=True),),
    )

    with patch("ax_devil.modules.video_viewer.offline_video_viewer.OfflineVideoViewerWidget", DummyViewer):
        opened = viewer_factory.open(playlist, start_index=start_index)

    qtbot.addWidget(opened.widget)
    assert isinstance(opened.widget, DummyViewer)
    assert opened.widget.start_index == 0
