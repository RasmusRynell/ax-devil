"""UI coordinator for workspace interactions and on-screen content state."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import ConsiderationItemRef, Content, ItemResolution, WorkspaceItem
from ax_devil.modules.workspace.ui.browser_rows import (
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
    build_browser_sections,
)
from ax_devil.modules.workspace.ui.viewer_factory import WorkspaceViewerFactory, default_workspace_viewer_factory
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
    from ax_devil.modules.workspace.ui.section_tree import SectionTree
    from ax_devil.modules.workspace.ui.split_view import SplitView
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


class WorkspaceController(QObject):
    """Coordinate workspace UI state across the sidebar views, center area, and viewers.

    The sidebar views read ``sections`` and follow ``sections_changed``, which reports every change to the rows,
    including which Content is open in a viewer, and hand their ``SectionTree`` to ``connect_tree``, so a row does the
    same in every view.
    """

    sections_changed = Signal(object)  # tuple[WorkspaceBrowserSection, ...]

    def __init__(
        self,
        workspace_store: WorkspaceStore,
        center_area: SplitView,
        render_catalog_manager: SceneRenderCatalogManager,
        parent: QWidget | None = None,
        viewer_factory: WorkspaceViewerFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace_store = workspace_store
        self._center_area = center_area
        self._render_catalog_manager = render_catalog_manager
        self._viewer_factory = viewer_factory or default_workspace_viewer_factory(self._render_catalog_manager)
        self._logger = get_logger(__name__)
        # The id of the item whose Content each open viewer shows.
        self._widget_item_ids: dict[ViewerWidget, str] = {}

        self._sections: tuple[WorkspaceBrowserSection, ...] = ()

        self._connect_signals()
        self._publish_sections()

    def sections(self) -> tuple[WorkspaceBrowserSection, ...]:
        """Return the rows of every item, grouped in sections."""
        return self._sections

    def open_content(
        self, content: Content, start_index: int = 0, *, to_side: bool = False, start_frame: int | None = None
    ) -> None:
        """Open *content*, from its playlist entry *start_index*, as a double click in the browser does.

        It replaces the preview pane, or opens in a new split with *to_side*; open viewers pause. With *start_frame*,
        the entry opens paused at that frame instead of playing from the start.
        """
        place = self._center_area.open_to_side if to_side else self._center_area.replace_or_open
        self._pause_all_viewers()
        self._open_content(content, start_index=start_index, place=place, start_frame=start_frame)

    def connect_tree(self, tree: SectionTree) -> None:
        """Carry out what the user does with the rows of *tree*: double-clicks open, actions run, eyes include or
        exclude, and its menu opens, exports, renames and removes."""
        tree.row_activated.connect(self.open_row)
        tree.row_action_requested.connect(self.perform_row_action)
        tree.row_consideration_changed.connect(self._workspace_store.set_item_considered)
        tree.content_activated.connect(self._on_content_activated)
        tree.content_open_to_side_requested.connect(self._on_content_open_to_side_requested)
        tree.item_remove_requested.connect(self._workspace_store.remove_item)
        tree.item_rename_requested.connect(self._workspace_store.rename_item)
        tree.export_requested.connect(self._on_export_requested)

    def open_row(self, row: WorkspaceBrowserRow, to_side: bool = False) -> None:
        """Open what *row* opens, in a new split with *to_side*; a row that cannot open says why instead."""
        if row.unavailable_reason is not None:
            QMessageBox.warning(self._window_parent(), "Open Content", f"{row.label}: {row.unavailable_reason}")
        elif row.activation_target is not None:
            self.open_content(*row.activation_target, to_side=to_side)

    def perform_row_action(self, row: WorkspaceBrowserRow) -> None:
        """Carry out the action *row* offers, whichever view showed it.

        Start stream opens the camera beside the open viewers, never in place of one, so starting one stream never
        stops another; Stop stream closes every viewer of the camera; Rescan resolves the row's item again; Add does
        what the row's ``add`` does.
        """
        if row.action is RowAction.START_STREAM and row.item is not None:
            for content in self._workspace_store.resolution(row.item.id).contents:
                self.open_content(content, to_side=True)
        elif row.action is RowAction.STOP_STREAM and row.item is not None:
            self.close_item_viewers(row.item.id)
        elif row.action is RowAction.RESCAN and row.item is not None:
            self._workspace_store.rescan_item(row.item.id)
        elif row.action is RowAction.ADD and row.add is not None:
            row.add()

    def close_item_viewers(self, item_id: str) -> None:
        """Close every viewer showing Content of the item *item_id*."""
        for widget, widget_item_id in tuple(self._widget_item_ids.items()):
            if widget_item_id == item_id:
                self._center_area.remove_viewer_widget(widget)

    def _connect_signals(self) -> None:
        self._workspace_store.items_added.connect(self._publish_sections)
        self._workspace_store.items_resolved.connect(self._on_items_resolved)
        self._workspace_store.item_rescanned.connect(self._publish_sections)
        self._workspace_store.item_removed.connect(self._close_orphaned_viewers)
        self._workspace_store.workspace_replaced.connect(self._close_all_viewers)
        self._workspace_store.item_renamed.connect(self._on_item_renamed)
        self._workspace_store.item_consideration_changed.connect(self._on_item_consideration_changed)
        self._center_area.widget_removed.connect(self._on_widget_removed)

    def _publish_sections(self, *_args: object) -> None:
        """Build the rows of the Workspace as it is now and report them."""
        open_items = frozenset(
            item for widget in self._widget_item_ids if (item := widget.current_on_screen_item()) is not None
        )
        self._sections = build_browser_sections(
            self._workspace_store.resolutions(), self._workspace_store.is_item_considered, open_items
        )
        self.sections_changed.emit(self._sections)

    def _on_content_activated(self, content: Content, start_index: int = 0) -> None:
        """Open a double-clicked browser item in the preview pane."""
        self.open_content(content, start_index)

    def _on_content_open_to_side_requested(self, content: Content, start_index: int = 0) -> None:
        """Open a browser item in a new split without replacing any pane."""
        self.open_content(content, start_index, to_side=True)

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
        self._publish_sections()

    def _on_items_resolved(self, resolutions: list[ItemResolution], open_first: bool) -> None:
        """Show resolved items; with *open_first*, tell which could not open and preview the first Content.

        Only adding items opens a viewer, and a camera never opens one by being added to the Live view; opening,
        creating, or restoring a Workspace lists its items and opens nothing.
        """
        self._publish_sections()
        if not open_first:
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
        self._publish_sections()

    def _close_all_viewers(self) -> None:
        """Close every viewer when another Workspace replaces the current one, and refresh the rows.

        The new Workspace's items are resolved afresh, with exclusions back at their defaults, so no viewer keeps
        Content or names from before; opening a Workspace opens nothing on its own.
        """
        self._center_area.clear_all_widgets()
        self._publish_sections()

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
        start_frame: int | None = None,
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
                start_frame=start_frame,
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
        self._publish_sections()
        self._logger.debug(status_message)

    def _on_widget_on_screen_item_changed(self, widget: ViewerWidget) -> None:
        """Refresh browser rows when a viewer changes its visible content."""
        self._publish_sections()

    def _on_item_consideration_changed(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Notify open viewers that consideration state may affect displayed content."""
        for widget in list(self._widget_item_ids):
            widget.refresh_item_consideration(item_ref, considered)
        self._publish_sections()

    def _on_widget_removed(self, widget: ViewerWidget) -> None:
        """Drop widget tracking as soon as the center area logically detaches it."""
        self._forget_widget(widget)

    def _forget_widget(self, widget: ViewerWidget) -> None:
        """Remove a detached widget from dependency tracking."""
        if self._widget_item_ids.pop(widget, None) is None:
            return
        self._publish_sections()

    def cleanup(self) -> None:
        """Clear tracked state and close all open viewers."""
        self._center_area.clear_all_widgets()
        self._widget_item_ids.clear()

    def _window_parent(self) -> QWidget | None:
        """Return the owning window used as parent for viewers and dialogs."""
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None
