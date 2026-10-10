"""Workspace viewer factory for content-to-viewer construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from ax_devil.modules.workspace.core.content import ConsiderationQuery, Content, LiveVideoContent

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


@dataclass(frozen=True, slots=True)
class WorkspaceViewerFactoryResult:
    """Viewer and status produced when opening content."""

    widget: ViewerWidget
    status_message: str


class WorkspaceViewerFactory:
    """Construct Workspace viewers for content."""

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
        """Construct the viewer for *content*."""
        if isinstance(content, LiveVideoContent):
            from ax_devil.modules.video_viewer.live_video_viewer import LiveVideoViewerWidget

            widget: ViewerWidget = LiveVideoViewerWidget(content, self._render_catalog_manager, parent)
        else:
            from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget

            widget = OfflineVideoViewerWidget(
                content,
                self._render_catalog_manager,
                start_index if 0 <= start_index < len(content.entries) else 0,
                parent,
                consideration_query=consideration_query,
            )
        return WorkspaceViewerFactoryResult(
            widget=widget,
            status_message=f"Opened: {content.display_name}",
        )


def default_workspace_viewer_factory(render_catalog_manager: SceneRenderCatalogManager) -> WorkspaceViewerFactory:
    """Return the production Workspace viewer factory."""
    return WorkspaceViewerFactory(render_catalog_manager)
