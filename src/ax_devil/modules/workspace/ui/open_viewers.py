"""The viewers open in the center area, and which item each one shows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox, QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import BookmarkTarget, Content, ItemResolution, find_entry
from ax_devil.modules.workspace.ui.viewer_factory import WorkspaceViewerFactory
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore

if TYPE_CHECKING:
    from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamStatus
    from ax_devil.modules.workspace.ui.split_view import SplitView
    from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget

logger = get_logger(__name__)


class OpenViewers:
    """Open, place and track the viewers of the center area, each against the item whose Content it shows.

    *on_changed* is called whenever the set of viewers or what one shows changes; *show_menu* is asked for a viewer's
    context menu. Viewers the center area detaches are forgotten at once.
    """

    def __init__(
        self,
        center_area: SplitView,
        workspace_store: WorkspaceStore,
        viewer_factory: WorkspaceViewerFactory,
        window: QWidget | None,
        on_changed: Callable[[], None],
        show_menu: Callable[[ViewerWidget, QPoint], None],
    ) -> None:
        self._center_area = center_area
        self._workspace_store = workspace_store
        self._viewer_factory = viewer_factory
        self._window = window
        self._on_changed = on_changed
        self._show_menu = show_menu
        # The id of the item whose Content each open viewer shows.
        self._widget_item_ids: dict[ViewerWidget, str] = {}
        self._shared_actions: Mapping[str, QAction] = {}
        center_area.widget_removed.connect(self._forget_widget)

    def widgets(self) -> tuple[ViewerWidget, ...]:
        """Return every open viewer."""
        return tuple(self._widget_item_ids)

    def stream_statuses(self) -> dict[str, tuple[LiveStreamStatus, ...]]:
        """Return the stream status of every open viewer that has one, by the id of the content it shows."""
        statuses: dict[str, tuple[LiveStreamStatus, ...]] = {}
        for widget in self._widget_item_ids:
            item, status = widget.current_on_screen_item(), widget.stream_status()
            if item is not None and status is not None:
                statuses[item.content_id] = (*statuses.get(item.content_id, ()), status)
        return statuses

    def of_item(self, item_id: str) -> tuple[ViewerWidget, ...]:
        """Return the viewers showing Content of the item *item_id*."""
        return tuple(widget for widget, widget_item_id in self._widget_item_ids.items() if widget_item_id == item_id)

    def showing(self, target: BookmarkTarget) -> ViewerWidget | None:
        """Return the viewer showing *target*, the focused one first, or None."""
        return next(
            (
                widget
                for widget in (self._center_area.get_focused_widget(), *self._widget_item_ids)
                if widget is not None and widget.bookmark_target() == target
            ),
            None,
        )

    def set_shared_actions(self, actions: Mapping[str, QAction]) -> None:
        """Give every viewer, open now or opened later, the window's *actions* for its menus."""
        self._shared_actions = actions
        for widget in self._widget_item_ids:
            widget.set_shared_actions(actions)

    def close_item(self, item_id: str) -> None:
        """Close every viewer showing Content of the item *item_id*."""
        for widget in self.of_item(item_id):
            self._center_area.remove_viewer_widget(widget)

    def close_orphans(self) -> None:
        """Close every viewer whose item is no longer in the Workspace."""
        for widget, item_id in tuple(self._widget_item_ids.items()):
            if not self._workspace_store.workspace.has_item(item_id):
                self._center_area.remove_viewer_widget(widget)

    def close_all(self) -> None:
        """Close every viewer."""
        self._center_area.clear_all_widgets()

    def replace_item(self, resolution: ItemResolution) -> None:
        """Reopen affected viewers at their surviving entry and frame, in the same pane."""
        for widget in self.of_item(resolution.item.id):
            target = widget.bookmark_target()
            shown = widget.current_on_screen_item()
            found = find_entry(resolution.contents, target) if target is not None else None
            if target is None and shown is not None:
                content = next((c for c in resolution.contents if c.content_id == shown.content_id), None)
                found = (content, 0) if content is not None else None
            if found is None:
                self._center_area.remove_viewer_widget(widget)
                continue
            self.open(
                found[0],
                start_index=found[1],
                start_frame=widget.playhead_frame() if target is not None else None,
                place=partial(self._center_area.replace_viewer_widget, widget),
            )

    def open(
        self,
        content: Content,
        *,
        start_index: int | None = None,
        place: Callable[[ViewerWidget], None] | None = None,
        start_frame: int | None = None,
        lane_index: int | None = None,
    ) -> None:
        """Open Workspace content using the Workspace viewer factory.

        *place* inserts the widget into the center area; it defaults to targeting the active pane.
        """
        try:
            opened = self._viewer_factory.open(
                content,
                start_index=start_index,
                parent=self._window,
                consideration_query=self._workspace_store,
                start_frame=start_frame,
            )
        except Exception as exc:
            message = str(exc)
            logger.error(f"Failed to open content {content.display_name}: {message}")
            QMessageBox.warning(self._window, "Open Content", message)
            return

        try:
            if lane_index is not None:
                opened.widget.focus_lane(lane_index)
            self._place(
                opened.widget,
                content,
                opened.status_message,
                place or self._center_area.replace_or_open,
            )
        except Exception as exc:
            message = str(exc)
            logger.error(f"Failed to open content {content.display_name}: {message}")
            QMessageBox.warning(self._window, "Open Content", message)

    def _place(
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
        widget.on_screen_item_changed.connect(lambda _=None: self._on_changed())
        widget.stream_status_changed.connect(lambda: self._on_changed())
        widget.context_menu_requested.connect(partial(self._show_menu, widget))
        widget.consideration_change_requested.connect(self._workspace_store.set_item_considered)
        self._on_changed()
        logger.debug(status_message)

    def _forget_widget(self, widget: ViewerWidget) -> None:
        """Drop a widget the center area detached from tracking."""
        if self._widget_item_ids.pop(widget, None) is None:
            return
        self._on_changed()
