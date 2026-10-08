"""Workspace viewer factory for content-to-viewer construction and dependency tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from ax_devil.modules.workspace.content import ConsiderationQuery, Content, LiveVideoContent

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
        if isinstance(content, LiveVideoContent):
            from ax_devil.modules.video_viewer.live_video_viewer import LiveVideoViewerWidget

            widget: WorkspaceWidget = LiveVideoViewerWidget(content, self._render_catalog_manager, parent)
            tracked_contents: tuple[Content, ...] = (content,)
        else:
            from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget

            widget = OfflineVideoViewerWidget(
                content,
                self._render_catalog_manager,
                start_index if 0 <= start_index < len(content.entries) else 0,
                parent,
                consideration_query=consideration_query,
            )
            lane_videos = (lane.video for entry in content.entries for lane in entry.lanes)
            tracked_contents = tuple(dict.fromkeys((content, *lane_videos)))
        return WorkspaceViewerFactoryResult(
            widget=widget,
            tracked_contents=tracked_contents,
            status_message=f"Opened: {content.display_name}",
        )


def default_workspace_viewer_factory(render_catalog_manager: SceneRenderCatalogManager) -> WorkspaceViewerFactory:
    """Return the production Workspace viewer factory."""
    return WorkspaceViewerFactory(render_catalog_manager)
