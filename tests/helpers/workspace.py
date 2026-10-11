"""Viewer double shared by workspace factory and session tests."""

from PySide6.QtWidgets import QWidget

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    LiveVideoContent,
    PlaylistContent,
    SeekableVideoContent,
    WorkspaceManager,
)
from ax_devil.modules.workspace.content import OnScreenWorkspaceItem
from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


class DummyViewer(WorkspaceWidget):
    """Record workspace routing and lifecycle without opening media sources."""

    def __init__(
        self,
        payload: object,
        render_catalog_manager: SceneRenderCatalogManager,
        start_index: int = 0,
        parent: QWidget | None = None,
        consideration_query: WorkspaceManager | None = None,
    ) -> None:
        self.payload = payload
        self.start_index = start_index
        self.consideration_query = consideration_query
        self.render_catalog_manager = render_catalog_manager
        self.cleaned_up = False
        self.refresh_calls: list[tuple[object, bool]] = []
        super().__init__(parent)

    def _setup_widget_ui(self) -> None:
        pass

    def get_display_name(self) -> str:
        """Use the content label for workspace titles."""
        return getattr(self.payload, "display_name", "dummy")

    def current_on_screen_item(self) -> OnScreenWorkspaceItem | None:
        """Project the selected entry into the workspace browser."""
        if isinstance(self.payload, (SeekableVideoContent, LiveVideoContent)):
            return OnScreenWorkspaceItem(kind="video", content_id=self.payload.content_id)
        if isinstance(self.payload, PlaylistContent):
            return OnScreenWorkspaceItem(
                kind="playlist_entry",
                content_id=self.payload.content_id,
                entry_index=self.start_index,
            )
        return None

    def cleanup(self) -> None:
        """Record that the workspace disposed this viewer."""
        self.cleaned_up = True

    def refresh_item_consideration(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Record consideration updates forwarded by the workspace."""
        self.refresh_calls.append((item_ref, considered))


class PlainWorkspaceWidget(WorkspaceWidget):
    """Minimal workspace widget with no content, for layout and attachment tests."""

    def _setup_widget_ui(self) -> None:
        pass

    def get_display_name(self) -> str:
        """Return a fixed name."""
        return "Plain"
