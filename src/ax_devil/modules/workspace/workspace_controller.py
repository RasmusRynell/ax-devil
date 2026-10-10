"""UI coordinator for workspace interactions and on-screen content state."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox, QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.content import ConsiderationItemRef, Content
from ax_devil.modules.workspace.viewer_factory import WorkspaceViewerFactory, default_workspace_viewer_factory
from ax_devil.modules.workspace.workspace_manager import WorkspaceManager

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
    from ax_devil.modules.workspace.content_browser import ContentBrowserWidget
    from ax_devil.modules.workspace.split_view import SplitView
    from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


class WorkspaceController(QObject):
    """Coordinate workspace UI state across the tree, center area, and viewers."""

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        content_browser: ContentBrowserWidget,
        center_area: SplitView,
        render_catalog_manager: SceneRenderCatalogManager,
        parent: QWidget | None = None,
        viewer_factory: WorkspaceViewerFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace_manager = workspace_manager
        self._content_browser = content_browser
        self._center_area = center_area
        self._render_catalog_manager = render_catalog_manager
        self._viewer_factory = viewer_factory or default_workspace_viewer_factory(self._render_catalog_manager)
        self._logger = get_logger(__name__)
        self._widget_content_ids: dict[WorkspaceWidget, frozenset[str]] = {}

        self._connect_signals()
        self._sync_content_browser()

    def _connect_signals(self) -> None:
        self._content_browser.content_activated.connect(self._on_content_activated)
        self._content_browser.content_open_to_side_requested.connect(self._on_content_open_to_side_requested)
        self._content_browser.item_consideration_change_requested.connect(self._workspace_manager.set_item_considered)
        self._content_browser.content_remove_requested.connect(self._workspace_manager.remove_content)
        self._content_browser.export_requested.connect(self._on_export_requested)
        self._workspace_manager.contents_added.connect(self._on_contents_added)
        self._workspace_manager.content_removed.connect(self._on_content_removed)
        self._workspace_manager.item_consideration_changed.connect(self._on_item_consideration_changed)
        self._center_area.widget_removed.connect(self._on_widget_removed)

    def _sync_content_browser(self, *_args: object) -> None:
        """Render current Workspace state into the content browser."""
        open_items = frozenset(
            item for widget in self._widget_content_ids if (item := widget.current_on_screen_item()) is not None
        )
        rows = self._workspace_manager.get_browser_rows(open_items)
        self._content_browser.set_browser_rows(rows)

    def _on_content_activated(self, content: Content, start_index: int = 0) -> None:
        """Open a double-clicked browser item in the preview pane."""
        self._open_browser_content(content, start_index, self._center_area.replace_or_open)

    def _on_content_open_to_side_requested(self, content: Content, start_index: int = 0) -> None:
        """Open a browser item in a new split without replacing any pane."""
        self._open_browser_content(content, start_index, self._center_area.open_to_side)

    def _open_browser_content(
        self,
        content: Content,
        start_index: int,
        place: Callable[[WorkspaceWidget], None],
    ) -> None:
        """Pause open viewers and open browser content with the requested pane placement."""
        self._pause_all_viewers()
        self._open_content(content, start_index=start_index, place=place)

    def _on_export_requested(self, item: object) -> None:
        """Export from the viewer currently showing the item."""
        for widget in self._widget_content_ids:
            if widget.current_on_screen_item() == item:
                widget.export_video()
                return

    def _on_contents_added(self, contents: list[Content]) -> None:
        """Preview the first newly added content item in the current workspace."""
        if not contents:
            return

        self._sync_content_browser()
        self._logger.debug(f"New content added: {contents[0].display_name}")
        self._open_content(contents[0])

    def _on_content_removed(self, content: Content) -> None:
        """Close any viewer showing or tracking the removed content."""
        for widget, tracked_content_ids in tuple(self._widget_content_ids.items()):
            if content.content_id in tracked_content_ids:
                self._center_area.remove_workspace_widget(widget)
        self._sync_content_browser()

    def _pause_all_viewers(self) -> None:
        """Pause playback on all open viewers."""
        for widget in self._widget_content_ids:
            widget.pause_playback()

    def _open_content(
        self,
        content: Content,
        *,
        start_index: int = 0,
        place: Callable[[WorkspaceWidget], None] | None = None,
    ) -> None:
        """Open Workspace content using the Workspace viewer factory.

        *place* inserts the widget into the center area; it defaults to replacing the preview pane.
        """
        try:
            opened = self._viewer_factory.open(
                content,
                start_index=start_index,
                parent=self._window_parent(),
                consideration_query=self._workspace_manager,
            )
        except Exception as exc:
            message = str(exc)
            self._logger.error(f"Failed to open content {content.display_name}: {message}")
            QMessageBox.warning(self._window_parent(), "Open Content", message)
            return

        try:
            self._open_widget(
                opened.widget,
                opened.tracked_contents,
                opened.status_message,
                place or self._center_area.replace_or_open,
            )
        except Exception as exc:
            message = str(exc)
            self._logger.error(f"Failed to open content {content.display_name}: {message}")
            QMessageBox.warning(self._window_parent(), "Open Content", message)

    def _open_widget(
        self,
        widget: WorkspaceWidget,
        contents: Iterable[Content],
        status_message: str,
        place: Callable[[WorkspaceWidget], None],
    ) -> None:
        """Track and display a widget for the given content dependencies."""
        place(widget)
        self._register_widget(widget, contents)
        self._logger.debug(status_message)

    def _register_widget(
        self,
        widget: WorkspaceWidget,
        contents: Iterable[Content],
    ) -> None:
        """Register a widget against every content item it depends on."""
        self._widget_content_ids[widget] = frozenset(content.content_id for content in contents)
        widget.on_screen_item_changed.connect(lambda _=None, w=widget: self._on_widget_on_screen_item_changed(w))
        self._sync_content_browser()

    def _on_widget_on_screen_item_changed(self, widget: WorkspaceWidget) -> None:
        """Refresh browser rows when a viewer changes its visible content."""
        self._sync_content_browser()

    def _on_item_consideration_changed(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Notify open viewers that consideration state may affect displayed content."""
        for widget in list(self._widget_content_ids):
            widget.refresh_item_consideration(item_ref, considered)
        self._sync_content_browser()

    def _on_widget_removed(self, widget: WorkspaceWidget) -> None:
        """Drop widget tracking as soon as the center area logically detaches it."""
        self._forget_widget(widget)

    def _forget_widget(self, widget: WorkspaceWidget) -> None:
        """Remove a detached widget from dependency tracking."""
        if self._widget_content_ids.pop(widget, None) is None:
            return
        self._sync_content_browser()

    def cleanup(self) -> None:
        """Clear tracked state and close all open viewers."""
        self._center_area.clear_all_widgets()
        self._widget_content_ids.clear()

    def _window_parent(self) -> QWidget | None:
        """Return the owning window used as parent for viewers and dialogs."""
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None
