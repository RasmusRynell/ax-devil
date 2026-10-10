"""UI coordinator for workspace interactions and on-screen content state."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox, QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import ConsiderationItemRef, Content, ItemResolution, WorkspaceItem
from ax_devil.modules.workspace.ui.browser_rows import build_browser_rows
from ax_devil.modules.workspace.ui.viewer_factory import WorkspaceViewerFactory, default_workspace_viewer_factory
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
    from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
    from ax_devil.modules.workspace.ui.split_view import SplitView
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


class WorkspaceController(QObject):
    """Coordinate workspace UI state across the tree, center area, and viewers."""

    def __init__(
        self,
        workspace_store: WorkspaceStore,
        content_browser: ContentBrowserWidget,
        center_area: SplitView,
        render_catalog_manager: SceneRenderCatalogManager,
        parent: QWidget | None = None,
        viewer_factory: WorkspaceViewerFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace_store = workspace_store
        self._content_browser = content_browser
        self._center_area = center_area
        self._render_catalog_manager = render_catalog_manager
        self._viewer_factory = viewer_factory or default_workspace_viewer_factory(self._render_catalog_manager)
        self._logger = get_logger(__name__)
        # The id of the item whose Content each open viewer shows.
        self._widget_item_ids: dict[ViewerWidget, str] = {}

        self._connect_signals()
        self._sync_content_browser()

    def _connect_signals(self) -> None:
        self._content_browser.content_activated.connect(self._on_content_activated)
        self._content_browser.content_open_to_side_requested.connect(self._on_content_open_to_side_requested)
        self._content_browser.item_consideration_change_requested.connect(self._workspace_store.set_item_considered)
        self._content_browser.item_remove_requested.connect(self._workspace_store.remove_item)
        self._content_browser.item_rename_requested.connect(self._workspace_store.rename_item)
        self._content_browser.export_requested.connect(self._on_export_requested)
        self._workspace_store.items_added.connect(self._sync_content_browser)
        self._workspace_store.items_resolved.connect(self._on_items_resolved)
        self._workspace_store.item_removed.connect(self._close_orphaned_viewers)
        self._workspace_store.workspace_replaced.connect(self._close_all_viewers)
        self._workspace_store.item_renamed.connect(self._on_item_renamed)
        self._workspace_store.item_consideration_changed.connect(self._on_item_consideration_changed)
        self._center_area.widget_removed.connect(self._on_widget_removed)

    def _sync_content_browser(self, *_args: object) -> None:
        """Render current Workspace state into the content browser."""
        open_items = frozenset(
            item for widget in self._widget_item_ids if (item := widget.current_on_screen_item()) is not None
        )
        rows = build_browser_rows(
            self._workspace_store.resolutions(), self._workspace_store.is_item_considered, open_items
        )
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
        place: Callable[[ViewerWidget], None],
    ) -> None:
        """Pause open viewers and open browser content with the requested pane placement."""
        self._pause_all_viewers()
        self._open_content(content, start_index=start_index, place=place)

    def _on_export_requested(self, item: object) -> None:
        """Export from the viewer currently showing the item."""
        for widget in self._widget_item_ids:
            if widget.current_on_screen_item() == item:
                widget.export_video()
                return

    def _on_item_renamed(self, item: WorkspaceItem) -> None:
        """Show the item's new name in the rows and in the header of every viewer showing its Content."""
        names = {
            content.content_id: content.display_name for content in self._workspace_store.resolution(item.id).contents
        }
        for widget, item_id in self._widget_item_ids.items():
            on_screen = widget.current_on_screen_item()
            if item_id == item.id and on_screen is not None and on_screen.content_id in names:
                widget.set_display_name(names[on_screen.content_id])
        self._sync_content_browser()

    def _on_items_resolved(self, resolutions: list[ItemResolution], added: bool) -> None:
        """Show resolved items; for items the user added, tell which could not open and preview the first Content.

        Only adding items opens a viewer; opening, creating, or restoring a Workspace lists its items and opens nothing.
        """
        self._sync_content_browser()
        if not added:
            return
        failures = [
            f"{resolution.item.display_name}: {resolution.error}" for resolution in resolutions if resolution.error
        ]
        if failures:
            QMessageBox.warning(self._window_parent(), "Open Content", "\n".join(failures))
        contents = [content for resolution in resolutions for content in resolution.contents]
        if contents:
            self._logger.debug(f"New content added: {contents[0].display_name}")
            self._open_content(contents[0])

    def _close_orphaned_viewers(self, *_args: object) -> None:
        """Close every viewer whose item is no longer in the Workspace, and refresh the rows."""
        for widget, item_id in tuple(self._widget_item_ids.items()):
            if not self._workspace_store.workspace.has_item(item_id):
                self._center_area.remove_viewer_widget(widget)
        self._sync_content_browser()

    def _close_all_viewers(self) -> None:
        """Close every viewer when another Workspace replaces the current one, and refresh the rows.

        The new Workspace's items are resolved afresh, with exclusions back at their defaults, so no viewer keeps
        Content or names from before; opening a Workspace opens nothing on its own.
        """
        self._center_area.clear_all_widgets()
        self._sync_content_browser()

    def _pause_all_viewers(self) -> None:
        """Pause playback on all open viewers."""
        for widget in self._widget_item_ids:
            widget.pause_playback()

    def _open_content(
        self,
        content: Content,
        *,
        start_index: int = 0,
        place: Callable[[ViewerWidget], None] | None = None,
    ) -> None:
        """Open Workspace content using the Workspace viewer factory.

        *place* inserts the widget into the center area; it defaults to replacing the preview pane.
        """
        try:
            opened = self._viewer_factory.open(
                content,
                start_index=start_index,
                parent=self._window_parent(),
                consideration_query=self._workspace_store,
            )
        except Exception as exc:
            message = str(exc)
            self._logger.error(f"Failed to open content {content.display_name}: {message}")
            QMessageBox.warning(self._window_parent(), "Open Content", message)
            return

        try:
            self._open_widget(
                opened.widget,
                content,
                opened.status_message,
                place or self._center_area.replace_or_open,
            )
        except Exception as exc:
            message = str(exc)
            self._logger.error(f"Failed to open content {content.display_name}: {message}")
            QMessageBox.warning(self._window_parent(), "Open Content", message)

    def _open_widget(
        self,
        widget: ViewerWidget,
        content: Content,
        status_message: str,
        place: Callable[[ViewerWidget], None],
    ) -> None:
        """Display *widget* and track it against the item its content came from."""
        place(widget)
        self._widget_item_ids[widget] = content.item_id
        widget.on_screen_item_changed.connect(lambda _=None, w=widget: self._on_widget_on_screen_item_changed(w))
        self._sync_content_browser()
        self._logger.debug(status_message)

    def _on_widget_on_screen_item_changed(self, widget: ViewerWidget) -> None:
        """Refresh browser rows when a viewer changes its visible content."""
        self._sync_content_browser()

    def _on_item_consideration_changed(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Notify open viewers that consideration state may affect displayed content."""
        for widget in list(self._widget_item_ids):
            widget.refresh_item_consideration(item_ref, considered)
        self._sync_content_browser()

    def _on_widget_removed(self, widget: ViewerWidget) -> None:
        """Drop widget tracking as soon as the center area logically detaches it."""
        self._forget_widget(widget)

    def _forget_widget(self, widget: ViewerWidget) -> None:
        """Remove a detached widget from dependency tracking."""
        if self._widget_item_ids.pop(widget, None) is None:
            return
        self._sync_content_browser()

    def cleanup(self) -> None:
        """Clear tracked state and close all open viewers."""
        self._center_area.clear_all_widgets()
        self._widget_item_ids.clear()

    def _window_parent(self) -> QWidget | None:
        """Return the owning window used as parent for viewers and dialogs."""
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None
