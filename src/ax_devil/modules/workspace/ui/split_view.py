"""Dynamic Split View container for viewer widgets.

This module provides a VSCode-like split layout using nested QSplitters with:
- Drag-and-drop splitting from each widget's header
- Directional overlay feedback
- Clean collapse of empty splitters on removal

It hosts `ViewerWidget` instances directly and preserves their header actions
(pin and close). Unpinned widgets are previews that `replace_or_open` may replace;
`open_to_side` always adds a split. Dragging starts only from the header widget exposed by
`ViewerWidget.get_header_widget()`.
"""

from __future__ import annotations

from itertools import count
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.ui.split_filters import HeaderDragFilter, WidgetActivationFilter
from ax_devil.modules.workspace.ui.split_panes import (
    LeafContainer,
    SplitDirection,
    collapse_empty_ancestors,
    largest_leaf_for_add,
    list_leaves,
    replace_child,
)
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget
from ax_devil.modules.workspace.ui.welcome_widget import WelcomeWidget

if TYPE_CHECKING:
    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager

logger = get_logger(__name__)

_id_counter = count(1)


def _next_id() -> str:
    return f"w-{next(_id_counter)}"


class SplitView(QWidget):
    """Manages a dynamic split layout of viewer widgets using nested splitters."""

    widget_removed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._root_leaf = LeafContainer(self)
        self._root_layout.addWidget(self._root_leaf)

        # id -> leaf
        self._id_to_leaf: dict[str, LeafContainer] = {}
        # widget object -> id
        self._widget_ids: dict[ViewerWidget, str] = {}

        # Overlay tracking
        self._active_overlay_leaf: LeafContainer | None = None
        self._active_overlay_direction: SplitDirection | None = None
        self._focused_widget: ViewerWidget | None = None

        # Welcome screen shown when no widgets are present
        self._welcome_scroll = QScrollArea(self)
        self._welcome_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._welcome_scroll.setWidgetResizable(True)
        self._welcome_widget = WelcomeWidget()
        self._welcome_scroll.setWidget(self._welcome_widget)
        self._welcome_scroll.show()

        split_view_id = id(self)
        self.destroyed.connect(
            lambda _=None, split_view_id=split_view_id: logger.debug(f"SplitView.destroyed id={split_view_id}")
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._welcome_scroll.setGeometry(self.rect())

    # Public API
    def add_viewer_widget(self, widget: ViewerWidget) -> None:
        """Add and start a viewer widget in the best available pane."""
        if self.get_widget_count() == 0:
            leaves = list_leaves(self._root_layout)
            if not leaves:
                leaf = LeafContainer(self)
                self._root_layout.addWidget(leaf)
                self._root_leaf = leaf
                self._attach_and_start_widget(leaf, widget)
            else:
                self._root_leaf = leaves[0]
                target_leaf = largest_leaf_for_add(list_leaves(self._root_layout)) or leaves[0]
                if target_leaf.get_widget() is None:
                    self._attach_and_start_widget(target_leaf, widget)
                else:
                    self._split_target_leaf_and_insert_widget(target_leaf, widget, None, SplitDirection.RIGHT)
            return

        target_leaf = largest_leaf_for_add(list_leaves(self._root_layout)) or self._root_leaf
        self._split_target_leaf_and_insert_widget(target_leaf, widget, None, SplitDirection.RIGHT)

    def remove_viewer_widget(self, widget: ViewerWidget) -> bool:
        """Remove and clean a hosted viewer widget."""
        leaf = self._leaf_for_widget(widget)
        if leaf is None:
            return False
        self._detach_widget(widget, leaf)
        widget.setParent(None)
        widget.deleteLater()
        self._update_welcome_visibility()
        return True

    def clear_all_widgets(self) -> None:
        """Remove and clean every hosted viewer widget."""
        # Remove each widget; removal performs cleanup. Copy keys first to avoid mutation during iteration.
        for widget in list(self._widget_ids.keys()):
            self.remove_viewer_widget(widget)
        # After clearing, ensure we have a single root leaf and refresh pointer
        leaves = list_leaves(self._root_layout)
        if not leaves:
            leaf = LeafContainer(self)
            self._root_layout.addWidget(leaf)
            self._root_leaf = leaf
        else:
            self._root_leaf = leaves[0]
        self._set_focused_widget(None)
        self._update_welcome_visibility()

    def get_widget_count(self) -> int:
        """Return the number of hosted viewer widgets."""
        return len(self._widget_ids)

    def replace_or_open(self, widget: ViewerWidget) -> None:
        """Replace the first unpinned (preview) widget, or open a new split if all are pinned."""
        if self.get_widget_count() == 0:
            self.add_viewer_widget(widget)
            return

        leaf = self._find_unpinned_leaf()
        if leaf is None:
            self.add_viewer_widget(widget)
            return

        old_widget = leaf.get_widget()
        if old_widget is not None:
            old_id = self._untrack_widget(old_widget)
            leaf.set_widget(None)
            self._attach_and_start_widget(leaf, widget, displaced_widget=old_widget, displaced_id=old_id)
            return

        self._attach_and_start_widget(leaf, widget)

    def open_to_side(self, widget: ViewerWidget) -> None:
        """Open *widget* pinned in a new split beside the focused pane, never replacing a pane."""
        widget.set_pinned(True)
        focused = self._focused_widget
        leaf = self._leaf_for_widget(focused) if focused is not None else None
        if leaf is None:
            self.add_viewer_widget(widget)
            return
        self._split_target_leaf_and_insert_widget(leaf, widget, None, SplitDirection.RIGHT)

    # Internal helpers
    def _find_unpinned_leaf(self) -> LeafContainer | None:
        """Return the first leaf containing an unpinned widget, or None."""
        for leaf in list_leaves(self._root_layout):
            w = leaf.get_widget()
            if w is not None and not w.is_pinned():
                return leaf
        return None

    def _update_welcome_visibility(self) -> None:
        """Show the welcome screen when no widgets are present, hide otherwise."""
        self._welcome_scroll.setVisible(self.get_widget_count() == 0)

    def _attach_and_start_widget(
        self,
        leaf: LeafContainer,
        widget: ViewerWidget,
        *,
        displaced_widget: ViewerWidget | None = None,
        displaced_id: str | None = None,
    ) -> None:
        """Attach and start a widget, restoring a displaced pane when startup fails."""
        self._attach_widget(leaf, widget)
        self._update_welcome_visibility()
        try:
            widget.on_workspace_attached()
        except Exception:
            if displaced_widget is None:
                self.remove_viewer_widget(widget)
            else:
                self._discard_failed_replacement(widget, leaf)
                self._restore_widget(leaf, displaced_widget, displaced_id)
                self._update_welcome_visibility()
            raise
        if displaced_widget is not None:
            self._cleanup_replaced_widget(displaced_widget)

    def set_welcome_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Configure the welcome screen to read shortcuts from *manager*."""
        self._welcome_widget.set_shortcut_manager(manager)

    def welcome_widget(self) -> WelcomeWidget:
        """Return the welcome screen shown while no widgets are open."""
        return self._welcome_widget

    def _attach_widget(self, leaf: LeafContainer, widget: ViewerWidget) -> None:
        leaf.set_widget(widget)
        wid = _next_id()
        self._id_to_leaf[wid] = leaf
        self._widget_ids[widget] = wid
        self._install_drag_on_widget_header(widget)
        self._install_activation_tracking(widget)
        widget.close_requested.connect(self._on_widget_close_requested)
        self._set_focused_widget(widget)

    def _detach_widget(self, widget: ViewerWidget, leaf: LeafContainer) -> None:
        """Logically remove a widget from the split view before deferred Qt deletion."""
        self._cleanup_widget(widget)
        leaf.set_widget(None)
        wid = self._widget_ids.pop(widget, None)
        if wid:
            self._id_to_leaf.pop(wid, None)
        collapse_empty_ancestors(leaf)
        if self._focused_widget is widget:
            self._set_focused_widget(self._first_available_widget())
        self.widget_removed.emit(widget)

    def _untrack_widget(self, widget: ViewerWidget) -> str | None:
        """Remove split-view identity tracking without cleaning up the widget."""
        wid = self._widget_ids.pop(widget, None)
        if wid is not None:
            self._id_to_leaf.pop(wid, None)
        if self._focused_widget is widget:
            self._set_focused_widget(None)
        return wid

    def _restore_widget(self, leaf: LeafContainer, widget: ViewerWidget, wid: str | None) -> None:
        """Restore an untracked widget after a failed replacement."""
        leaf.set_widget(widget)
        restored_id = wid or _next_id()
        self._widget_ids[widget] = restored_id
        self._id_to_leaf[restored_id] = leaf
        self._set_focused_widget(widget)

    def _discard_failed_replacement(self, widget: ViewerWidget, leaf: LeafContainer) -> None:
        """Clean up a failed replacement without collapsing the leaf being restored."""
        self._cleanup_widget(widget)
        leaf.set_widget(None)
        wid = self._widget_ids.pop(widget, None)
        if wid is not None:
            self._id_to_leaf.pop(wid, None)
        if self._focused_widget is widget:
            self._set_focused_widget(None)
        widget.setParent(None)
        widget.deleteLater()

    def _cleanup_replaced_widget(self, widget: ViewerWidget) -> None:
        """Clean up a replaced widget after its replacement has started successfully."""
        self._cleanup_widget(widget)
        widget.setParent(None)
        widget.deleteLater()
        self.widget_removed.emit(widget)

    def _leaf_for_widget(self, widget: ViewerWidget) -> LeafContainer | None:
        wid = self._widget_ids.get(widget)
        if not wid:
            return None
        return self._id_to_leaf.get(wid)

    def id_for_widget(self, widget: ViewerWidget) -> str | None:
        """Return id for a widget if known, otherwise None (defensive)."""
        return self._widget_ids.get(widget)

    def _install_drag_on_widget_header(self, widget: ViewerWidget) -> None:
        header = widget.get_header_widget()
        # Parent the filter to the header so it dies with the widget; store only a weak reference to the widget
        header.installEventFilter(HeaderDragFilter(self, widget, parent=header))

    def handle_drop(self, target_leaf: LeafContainer, widget_id: str, direction: SplitDirection) -> None:
        """Move or split the dragged widget *widget_id* onto *target_leaf* from *direction*."""
        source_leaf = self._id_to_leaf.get(widget_id)
        if source_leaf is None:
            return
        widget = source_leaf.get_widget()
        if widget is None:
            return
        if target_leaf is source_leaf and target_leaf.get_widget() is widget:
            return

        if target_leaf.get_widget() is None:
            self._move_widget(widget, source_leaf, target_leaf)
            return

        self._split_target_leaf_and_insert_widget(target_leaf, widget, source_leaf, direction)

    def _move_widget(self, widget: ViewerWidget, source_leaf: LeafContainer | None, target_leaf: LeafContainer) -> None:
        if target_leaf is source_leaf:
            return
        if source_leaf is not None:
            source_leaf.set_widget(None)
            # keep widget id, just remap leaf
            wid = self._widget_ids.get(widget)
            if wid:
                self._id_to_leaf[wid] = target_leaf
            collapse_empty_ancestors(source_leaf)
        target_leaf.set_widget(widget)
        self._set_focused_widget(widget)

    def _cleanup_widget(self, widget: ViewerWidget) -> None:
        """Release one hosted widget without interrupting container teardown."""
        try:
            widget.cleanup()
        except Exception as exc:
            logger.warning(f"Viewer widget cleanup failed for {type(widget).__name__}: {exc}")

    def _on_widget_close_requested(self) -> None:
        """Route widget close requests through the split-view removal path."""
        widget = self.sender()
        if isinstance(widget, ViewerWidget):
            self.remove_viewer_widget(widget)

    def _install_activation_tracking(self, widget: ViewerWidget) -> None:
        """Track user interaction on a widget subtree to determine the focused pane."""
        WidgetActivationFilter(self, widget, parent=widget)

    def _set_focused_widget(self, widget: ViewerWidget | None) -> None:
        """Set the currently focused widget."""
        if self._focused_widget is widget:
            return
        self._focused_widget = widget

    def focus_widget(self, widget: ViewerWidget) -> None:
        """Make *widget* the focused pane."""
        self._set_focused_widget(widget)

    def get_focused_widget(self) -> ViewerWidget | None:
        """Return the currently focused widget."""
        return self._focused_widget

    def _first_available_widget(self) -> ViewerWidget | None:
        """Return the first visible widget if any."""
        for leaf in list_leaves(self._root_layout):
            widget = leaf.get_widget()
            if widget is not None:
                return widget
        return None

    # Overlay coordination
    def set_active_overlay(self, leaf: LeafContainer, direction: SplitDirection) -> None:
        """Show the drop hint for *direction* on *leaf* and clear it from the previous leaf."""
        if self._active_overlay_leaf is leaf and self._active_overlay_direction == direction:
            return
        if self._active_overlay_leaf is not None and self._active_overlay_leaf is not leaf:
            self._active_overlay_leaf.drop_overlay.clear_hint()
            self._active_overlay_leaf.drop_overlay.hide()
            self._active_overlay_leaf.update()
        leaf.drop_overlay.set_hint(direction)
        leaf.drop_overlay.show()
        leaf.drop_overlay.raise_()
        leaf.update()
        self._active_overlay_leaf = leaf
        self._active_overlay_direction = direction

    def clear_active_overlay(self, leaf: LeafContainer | None = None) -> None:
        """Clear the drop hint, when it is on *leaf* or *leaf* is None."""
        if self._active_overlay_leaf is None:
            return
        if leaf is not None and leaf is not self._active_overlay_leaf:
            return
        self._active_overlay_leaf.drop_overlay.clear_hint()
        self._active_overlay_leaf.drop_overlay.hide()
        self._active_overlay_leaf.update()
        self._active_overlay_leaf = None
        self._active_overlay_direction = None

    def _split_target_leaf_and_insert_widget(
        self,
        target_leaf: LeafContainer,
        widget: ViewerWidget,
        source_leaf: LeafContainer | None,
        direction: SplitDirection,
    ) -> None:
        parent = target_leaf.parentWidget()
        if parent is None:
            return

        splitter = (
            QSplitter(Qt.Orientation.Horizontal, self)
            if direction in (SplitDirection.LEFT, SplitDirection.RIGHT)
            else QSplitter(Qt.Orientation.Vertical, self)
        )
        splitter.setChildrenCollapsible(False)
        # Invisible handle, zero gap, but still resizable by hit-testing at seam
        splitter.setHandleWidth(0)
        splitter.setStyleSheet(
            """
            QSplitter { border: none; margin: 0px; padding: 0px; background: palette(Base); }
            QSplitter::handle { background: transparent; border: 0px; margin: 0px; padding: 0px; }
            QSplitter::handle:horizontal { width: 0px; }
            QSplitter::handle:vertical { height: 0px; }
            """
        )

        new_leaf = LeafContainer(self)

        if direction in (SplitDirection.LEFT, SplitDirection.TOP):
            first, second = new_leaf, target_leaf
        else:
            first, second = target_leaf, new_leaf

        replace_child(parent, target_leaf, splitter)
        splitter.addWidget(first)
        splitter.addWidget(second)

        if source_leaf is None:
            self._attach_and_start_widget(new_leaf, widget)
        else:
            self._move_widget(widget, source_leaf, new_leaf)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1, 1])
