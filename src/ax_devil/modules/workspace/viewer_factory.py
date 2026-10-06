"""Workspace viewer factory for content-to-viewer construction and dependency tracking."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from ax_devil.modules.workspace.content import (
    ConsiderationQuery,
    Content,
    LiveVideoContent,
    PlaylistContent,
    SeekableVideoContent,
)

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
    from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


@dataclass(frozen=True, slots=True)
class WorkspaceViewerFactoryResult:
    """Viewer, content dependencies, and status produced when opening content."""

    widget: WorkspaceWidget
    tracked_contents: tuple[Content, ...]
    status_message: str


class WorkspaceViewerFactory:
    """Construct Workspace viewers and report tracked content dependencies."""

    def __init__(self, render_catalog_manager: SceneRenderCatalogManager) -> None:
        self._render_catalog_manager = render_catalog_manager

    def open(
        self,
        content: Content,
        *,
        start_index: int = 0,
        parent: QWidget | None = None,
        consideration_query: ConsiderationQuery | None = None,
    ) -> WorkspaceViewerFactoryResult:
        """Construct a viewer and return the content it depends on."""
        if isinstance(content, SeekableVideoContent):
            return self._open_seekable_video(content, parent, consideration_query)
        if isinstance(content, LiveVideoContent):
            return self._open_live_video(content, parent)
        if isinstance(content, PlaylistContent):
            return self._open_playlist(content, start_index, parent, consideration_query)
        raise TypeError(f"Unsupported workspace content type: {type(content).__name__}")

    def _open_seekable_video(
        self,
        content: SeekableVideoContent,
        parent: QWidget | None,
        consideration_query: ConsiderationQuery | None,
    ) -> WorkspaceViewerFactoryResult:
        """Construct an offline viewer for seekable video content."""
        from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget

        return WorkspaceViewerFactoryResult(
            widget=OfflineVideoViewerWidget(
                content,
                self._render_catalog_manager,
                start_index=0,
                parent=parent,
                consideration_query=consideration_query,
            ),
            tracked_contents=(content,),
            status_message=f"Opened: {content.display_name}",
        )

    def _open_live_video(
        self,
        content: LiveVideoContent,
        parent: QWidget | None,
    ) -> WorkspaceViewerFactoryResult:
        """Construct a live viewer for live video content."""
        from ax_devil.modules.video_viewer.live_video_viewer import LiveVideoViewerWidget

        return WorkspaceViewerFactoryResult(
            widget=LiveVideoViewerWidget(content, self._render_catalog_manager, parent),
            tracked_contents=(content,),
            status_message=f"Opened: {content.display_name}",
        )

    def _open_playlist(
        self,
        playlist: PlaylistContent,
        start_index: int,
        parent: QWidget | None,
        consideration_query: ConsiderationQuery | None,
    ) -> WorkspaceViewerFactoryResult:
        """Construct an offline viewer for playlist content and expand tracked videos."""
        normalized_start_index = start_index if 0 <= start_index < len(playlist.entries) else 0

        from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget

        return WorkspaceViewerFactoryResult(
            widget=OfflineVideoViewerWidget(
                playlist,
                self._render_catalog_manager,
                normalized_start_index,
                parent,
                consideration_query=consideration_query,
            ),
            tracked_contents=(playlist, *self._tracked_playlist_contents(playlist)),
            status_message=f"Opened: {playlist.display_name}",
        )

    def _tracked_playlist_contents(
        self,
        playlist: PlaylistContent,
    ) -> tuple[SeekableVideoContent | LiveVideoContent, ...]:
        """Return unique video content dependencies for a playlist in entry order."""
        videos: Sequence[SeekableVideoContent | LiveVideoContent] = tuple(
            lane.video for entry in playlist.entries for lane in entry.lanes
        )
        return tuple(dict.fromkeys(videos))


def default_workspace_viewer_factory(render_catalog_manager: SceneRenderCatalogManager) -> WorkspaceViewerFactory:
    """Return the production Workspace viewer factory."""
    return WorkspaceViewerFactory(render_catalog_manager)
