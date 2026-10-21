"""UI coordinator for workspace interactions and on-screen content state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox, QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    ConsiderationItemRef,
    Content,
    ItemResolution,
    WorkspaceItem,
    entry_target,
    find_entry,
)
from ax_devil.modules.workspace.ui.browser_projection import build_browser_sections
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import ask_new_name
from ax_devil.modules.workspace.ui.row_menu import show_row_menu
from ax_devil.modules.workspace.ui.viewer_factory import WorkspaceViewerFactory, default_workspace_viewer_factory
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
    from ax_devil.modules.workspace.ui.section_tree import SectionTree
    from ax_devil.modules.workspace.ui.split_view import SplitView
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget


class WorkspaceController(QObject):
    """Coordinate sidebar actions, projected rows and hosted viewers.

    Views follow ``sections_changed`` and route their SectionTree intent through ``perform_row_action``.
    """

    edit_requested = Signal(str)  # item id; the session owns the shared content forms
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
        self._edited_items: set[str] = set()
        self._shared_actions: Mapping[str, QAction] = {}

        self._sections: tuple[WorkspaceBrowserSection, ...] = ()
        self._row_handlers: dict[RowAction, Callable[[WorkspaceBrowserRow], None]] = {
            RowAction.OPEN: self._open_row,
            RowAction.OPEN_TO_SIDE: partial(self._open_row, to_side=True),
            RowAction.START_STREAM: self._start_stream,
            RowAction.STOP_STREAM: self._stop_stream,
            RowAction.ADD: self._add,
            RowAction.INFORMATION: self._show_information,
            RowAction.EXPORT_VIDEO: self._export_video,
            RowAction.HIDE: partial(self._set_considered, considered=False),
            RowAction.UNHIDE: partial(self._set_considered, considered=True),
            RowAction.EDIT: self._edit,
            RowAction.RENAME: self._rename,
            RowAction.REMOVE: self._remove,
        }

        self._connect_signals()
        self._publish_sections()

    def sections(self) -> tuple[WorkspaceBrowserSection, ...]:
        """Return the rows of every item, grouped in sections."""
        return self._sections

    def open_content(self, content: Content, start_index: int | None = None, *, to_side: bool = False) -> None:
        """Open *content*, or exactly its entry *start_index* when supplied, including hidden entries.

        It targets the active pane, or opens in a new split with *to_side*. Other viewers keep their playback state.
        """
        place = self._center_area.open_to_side if to_side else self._center_area.replace_or_open
        self._open_content(content, start_index=start_index, place=place)

    def show_frame(self, content: Content, entry_index: int, frame: int) -> None:
        """Show *frame* of entry *entry_index* of *content* paused; a viewer already showing that entry seeks there
        and takes focus, otherwise the entry opens as ``open_content`` would.
        """
        target = entry_target(content, entry_index)
        viewer = next(
            (
                widget
                for widget in (self._center_area.get_focused_widget(), *self._widget_item_ids)
                if widget is not None and widget.bookmark_target() == target
            ),
            None,
        )
        if viewer is not None:
            viewer.show_frame(frame)
            self._center_area.focus_widget(viewer)
        else:
            self._open_content(
                content, start_index=entry_index, place=self._center_area.replace_or_open, start_frame=frame
            )

    def connect_tree(self, tree: SectionTree) -> None:
        """Carry out what the user asks of the rows of *tree*, however they asked: see ``perform_row_action``."""
        tree.row_action_requested.connect(self.perform_row_action)

    def perform_row_action(
        self, row: WorkspaceBrowserRow, action: RowAction, viewer: ViewerWidget | None = None
    ) -> None:
        """Carry out an enabled row action; stale items do nothing and exports use the requesting viewer."""
        offered = row.offered(action)
        if offered is None or offered.disabled_reason:
            return
        if viewer is not None and action is RowAction.EXPORT_VIDEO:
            viewer.export_video()
        else:
            self._row_handlers[action](row)

    def set_shared_actions(self, actions: Mapping[str, QAction]) -> None:
        """Give every viewer, open now or opened later, the window's *actions* for its menus."""
        self._shared_actions = actions
        for widget in self._widget_item_ids:
            widget.set_shared_actions(actions)

    def close_item_viewers(self, item_id: str) -> None:
        """Close every viewer showing Content of the item *item_id*."""
        for widget, widget_item_id in tuple(self._widget_item_ids.items()):
            if widget_item_id == item_id:
                self._center_area.remove_viewer_widget(widget)

    def _connect_signals(self) -> None:
        self._workspace_store.items_added.connect(self._publish_sections)
        self._workspace_store.items_resolved.connect(self._on_items_resolved)
        self._workspace_store.sources_changed.connect(self._on_sources_changed)
        self._workspace_store.item_removed.connect(self._close_orphaned_viewers)
        self._workspace_store.workspace_replaced.connect(self._close_all_viewers)
        self._workspace_store.item_edited.connect(self._on_item_edited)
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

    def _show_viewer_menu(self, widget: ViewerWidget, global_pos: QPoint) -> None:
        """Show the current Content row menu, with editing available only from the sidebar."""
        shown = widget.current_on_screen_item()
        row = next(
            (
                row
                for section in self._sections
                for row in section.rows
                if shown is not None and row.item is not None and row.row_id == shown.content_id
            ),
            None,
        )
        if row is not None:
            row = replace(row, actions=tuple(a for a in row.actions if a.action is not RowAction.EDIT))
            show_row_menu(
                row,
                self._window_parent(),
                global_pos,
                lambda action: self.perform_row_action(row, action, viewer=widget),
            )

    def _held_item(self, row: WorkspaceBrowserRow) -> WorkspaceItem | None:
        """Return the item *row* stands for as the Workspace holds it now; None for a row of no item or a gone one."""
        if row.item is None or not self._workspace_store.workspace.has_item(row.item.id):
            return None
        return self._workspace_store.workspace.item(row.item.id)

    def _open_row(self, row: WorkspaceBrowserRow, *, to_side: bool = False) -> None:
        """Open what *row* opens, in a new split with *to_side*; a row that cannot open says why instead."""
        if row.unavailable_reason is not None:
            QMessageBox.warning(self._window_parent(), "Open Content", f"{row.label}: {row.unavailable_reason}")
        elif row.activation_target is not None and self._workspace_store.workspace.has_item(
            row.activation_target[0].item_id
        ):
            self.open_content(*row.activation_target, to_side=to_side)

    def _start_stream(self, row: WorkspaceBrowserRow) -> None:
        """Open the camera beside the open viewers, never in place of one, so one stream never stops another."""
        if (item := self._held_item(row)) is not None:
            for content in self._workspace_store.resolution(item.id).contents:
                self.open_content(content, to_side=True)

    def _stop_stream(self, row: WorkspaceBrowserRow) -> None:
        """Close every viewer of the camera."""
        if row.item is not None:
            self.close_item_viewers(row.item.id)

    def _add(self, row: WorkspaceBrowserRow) -> None:
        """Do what the row's ``add`` does: add what it stands for, or ask how."""
        if row.add is not None:
            row.add()

    def _set_considered(self, row: WorkspaceBrowserRow, *, considered: bool) -> None:
        """Hide the row from playback, or let playback use it again."""
        if row.consideration_ref is not None:
            self._workspace_store.set_item_considered(row.consideration_ref, considered)

    def _show_information(self, row: WorkspaceBrowserRow) -> None:
        """Show a simple modal summary of the row."""
        if row.information_factory is not None:
            with WorkspaceItemInfoDialog(row.information_factory(), self._window_parent()) as dialog:
                dialog.exec()

    def _export_video(self, row: WorkspaceBrowserRow) -> None:
        """Export from the viewer currently showing the row."""
        for widget in self._widget_item_ids:
            if row.export_target is not None and widget.current_on_screen_item() == row.export_target:
                widget.export_video()
                return

    def _edit(self, row: WorkspaceBrowserRow) -> None:
        """Ask the session to edit the current saved item."""
        if (item := self._held_item(row)) is not None and item.editable:
            self.edit_requested.emit(item.id)

    def _rename(self, row: WorkspaceBrowserRow) -> None:
        """Rename the saved item; keeping an unlabeled row's name retains its default-name semantics."""
        if (item := self._held_item(row)) is None:
            return
        name = ask_new_name(
            self._window_parent(), item.label or row.label, item.default_name if item.label else row.label
        )
        if name is not None:
            self._workspace_store.rename_item(item.id, name)

    def _remove(self, row: WorkspaceBrowserRow) -> None:
        """Remove the row's item."""
        if row.item is not None:
            self._workspace_store.remove_item(row.item.id)

    def _on_sources_changed(self, paths: frozenset[Path]) -> None:
        """Offer every open viewer that shows one of the changed *paths* to reload."""
        for widget in tuple(self._widget_item_ids):
            if widget.source_paths().files & paths:
                widget.offer_reload()

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

    def _on_item_edited(self, item: WorkspaceItem) -> None:
        """Refresh this item's open viewers when its new resolution arrives."""
        self._edited_items.add(item.id)
        self._publish_sections()

    def _replace_item_viewers(self, resolution: ItemResolution) -> None:
        """Reopen affected viewers at their surviving entry and frame, in the same pane."""
        for widget, item_id in tuple(self._widget_item_ids.items()):
            if item_id != resolution.item.id:
                continue
            target = widget.bookmark_target()
            shown = widget.current_on_screen_item()
            found = find_entry(resolution.contents, target) if target is not None else None
            if target is None and shown is not None:
                content = next((c for c in resolution.contents if c.content_id == shown.content_id), None)
                found = (content, 0) if content is not None else None
            if found is None:
                self._center_area.remove_viewer_widget(widget)
                continue
            self._open_content(
                found[0],
                start_index=found[1],
                start_frame=widget.playhead_frame() if target is not None else None,
                place=partial(self._center_area.replace_viewer_widget, widget),
            )

    def _on_items_resolved(self, resolutions: list[ItemResolution], open_first: bool) -> None:
        """Refresh edited viewers and rows; only newly added items may open their first Content."""
        for resolution in resolutions:
            if resolution.item.id in self._edited_items:
                self._edited_items.discard(resolution.item.id)
                self._replace_item_viewers(resolution)
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
        """Close all viewers and pending edits when another Workspace becomes current."""
        self._edited_items.clear()
        self._center_area.clear_all_widgets()
        self._publish_sections()

    def _open_content(
        self,
        content: Content,
        *,
        start_index: int | None = None,
        place: Callable[[ViewerWidget], None] | None = None,
        start_frame: int | None = None,
    ) -> None:
        """Open Workspace content using the Workspace viewer factory.

        *place* inserts the widget into the center area; it defaults to targeting the active pane.
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
        widget.set_shared_actions(self._shared_actions)
        widget.on_screen_item_changed.connect(lambda _=None, w=widget: self._on_widget_on_screen_item_changed(w))
        widget.context_menu_requested.connect(partial(self._show_viewer_menu, widget))
        widget.consideration_change_requested.connect(self._workspace_store.set_item_considered)
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
