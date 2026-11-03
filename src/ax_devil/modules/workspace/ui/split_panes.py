"""Panes of the split view: a leaf holding one viewer, its drop feedback, and edits to the splitter tree of leaves."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
)
from PySide6.QtWidgets import QBoxLayout, QFrame, QSplitter, QVBoxLayout, QWidget

from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget

if TYPE_CHECKING:
    from ax_devil.modules.workspace.ui.split_view import SplitView

PANEL_MIME_TYPE = "application/x-ax-devil-widget-id"


class SplitDirection(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


class LeafContainer(QFrame):
    """Leaf that hosts one viewer widget and accepts drops to split."""

    def __init__(self, manager: SplitView, widget: ViewerWidget | None = None) -> None:
        super().__init__(manager)
        self._manager = manager
        self._widget: ViewerWidget | None = None
        self.setObjectName("LeafContainer")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWidth(0)
        self.setContentsMargins(0, 0, 0, 0)
        self.setAcceptDrops(True)

        # Overlay for drag feedback
        self.drop_overlay = DropOverlay(self)
        self.drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_overlay.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if widget:
            self.set_widget(widget)

    def get_widget(self) -> ViewerWidget | None:
        """Return the viewer widget hosted by this leaf, if any."""
        return self._widget

    def set_widget(self, widget: ViewerWidget | None) -> None:
        """Replace the viewer widget hosted by this leaf."""
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            existing_widget = item.widget()
            if existing_widget is not None:
                # Keep extracted viewers owned: a queued child-show must never open a top-level window.
                existing_widget.setParent(self._manager)
                existing_widget.hide()
        self._widget = widget
        if widget is not None:
            layout.addWidget(widget)
            widget.show()

    # Drag-and-drop events
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.acceptProposedAction()
            pos = event.position().toPoint()
            direction = self._infer_drop_direction_from_position(pos)
            src_ba = event.mimeData().data(PANEL_MIME_TYPE)
            src_id = bytes(src_ba.data()).decode("utf-8")
            current = self.get_widget()
            current_id = self._manager.id_for_widget(current) if current is not None else None
            if current is not None and current_id is not None and current_id == src_id:
                self._manager.clear_active_overlay(self)
            else:
                self._manager.set_active_overlay(self, direction)
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.acceptProposedAction()
            pos = event.position().toPoint()
            direction = self._infer_drop_direction_from_position(pos)
            src_ba = event.mimeData().data(PANEL_MIME_TYPE)
            src_id = bytes(src_ba.data()).decode("utf-8")
            current = self.get_widget()
            current_id = self._manager.id_for_widget(current) if current is not None else None
            if current is not None and current_id is not None and current_id == src_id:
                self._manager.clear_active_overlay(self)
            else:
                self._manager.set_active_overlay(self, direction)
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._manager.clear_active_overlay(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.ignore()
            self._manager.clear_active_overlay(self)
            return
        ba = event.mimeData().data(PANEL_MIME_TYPE)
        widget_id = bytes(ba.data()).decode("utf-8")
        pos = event.position().toPoint()
        direction = self._infer_drop_direction_from_position(pos)
        self._manager.handle_drop(self, widget_id, direction)
        event.acceptProposedAction()
        self._manager.clear_active_overlay(self)

    def leaveEvent(self, _event: QEvent) -> None:
        self.drop_overlay.clear_hint()
        self.drop_overlay.hide()
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.drop_overlay.setGeometry(self.rect())

    def _infer_drop_direction_from_position(self, pos: QPoint) -> SplitDirection:
        rect = self.rect()
        w, h = rect.width(), rect.height()
        x, y = pos.x(), pos.y()

        top_bottom_margin = max(24, int(h * 0.25))
        if y < top_bottom_margin:
            return SplitDirection.TOP
        if y > h - top_bottom_margin:
            return SplitDirection.BOTTOM

        return SplitDirection.LEFT if x < (w // 2) else SplitDirection.RIGHT


class DropOverlay(QWidget):
    """Transparent overlay to draw directional split feedback above content."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._direction: SplitDirection | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_hint(self, direction: SplitDirection | None) -> None:
        self._direction = direction
        self.update()

    def clear_hint(self) -> None:
        self._direction = None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.isVisible() or self._direction is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.rect().adjusted(1, 1, -1, -1)
        if not full.isValid():
            return
        if self._direction == SplitDirection.LEFT:
            target = full.adjusted(0, 0, -full.width() // 2, 0)
        elif self._direction == SplitDirection.RIGHT:
            target = full.adjusted(full.width() // 2, 0, 0, 0)
        elif self._direction == SplitDirection.TOP:
            target = full.adjusted(0, 0, 0, -full.height() // 2)
        else:  # BOTTOM
            target = full.adjusted(0, full.height() // 2, 0, 0)

        palette = self.palette()
        highlight = palette.color(QPalette.ColorRole.Highlight)
        fill = highlight
        fill.setAlpha(72)
        border = highlight.darker(130)
        border.setAlpha(160)
        painter.setBrush(fill)
        painter.setPen(border)
        painter.drawRect(target)


def replace_child(parent: QWidget, old_child: QWidget, new_child: QWidget) -> None:
    """Put *new_child* where *old_child* sits in *parent*, a splitter or a box layout, and detach *old_child*."""
    if isinstance(parent, QSplitter):
        index = parent.indexOf(old_child)
        prev_sizes = parent.sizes()
        # Not QSplitter.replaceWidget: PySide does not record the new parent, so freeing the old splitter
        # below invalidates the Qt-created wrappers (such as QML items) inside the moved subtree.
        parent.insertWidget(index, new_child)
        old_child.setParent(None)
        if prev_sizes and len(prev_sizes) == parent.count():
            parent.setSizes(prev_sizes)
    else:
        layout = parent.layout()
        assert isinstance(layout, QBoxLayout)
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            if item.widget() is old_child:
                layout.takeAt(i)
                old_child.setParent(None)
                layout.insertWidget(i, new_child)
                break


def collapse_empty_ancestors(leaf: LeafContainer) -> None:
    """Replace the splitter around an empty *leaf* by its sibling."""
    widget = leaf.get_widget()
    if widget is not None:
        return
    parent = leaf.parentWidget()
    if not isinstance(parent, QSplitter):
        return
    if parent.count() != 2:
        return
    idx = parent.indexOf(leaf)
    sibling_index = 1 - idx
    sibling = parent.widget(sibling_index)
    if sibling is None:
        return
    grandparent = parent.parentWidget()
    if grandparent is None:
        return
    prev_gp_sizes = grandparent.sizes() if isinstance(grandparent, QSplitter) else None
    replace_child(grandparent, parent, sibling)
    if isinstance(grandparent, QSplitter) and prev_gp_sizes and len(prev_gp_sizes) == grandparent.count():
        grandparent.setSizes(prev_gp_sizes)
    parent.setParent(None)
    leaf.setParent(None)


def largest_leaf_for_add(leaves: list[LeafContainer]) -> LeafContainer | None:
    """Return the largest leaf already holding a widget, or else the largest leaf; None without leaves."""
    if not leaves:
        return None

    def area(leaf: LeafContainer) -> int:
        s = leaf.size()
        return int(max(1, s.width() * s.height()))

    occupied = [leaf for leaf in leaves if leaf.get_widget() is not None]
    candidates = occupied or leaves
    return max(candidates, key=area)


def list_leaves(root_layout: QBoxLayout) -> list[LeafContainer]:
    """Return the leaves under *root_layout* in layout order."""

    def walk(widget: QWidget) -> list[LeafContainer]:
        if isinstance(widget, LeafContainer):
            return [widget]
        if isinstance(widget, QSplitter):
            acc_list: list[LeafContainer] = []
            for i in range(widget.count()):
                child = widget.widget(i)
                if child is not None:
                    acc_list.extend(walk(child))
            return acc_list
        layout = widget.layout()
        if layout is not None:
            acc_list2: list[LeafContainer] = []
            for i in range(layout.count()):
                item_i = layout.itemAt(i)
                if item_i is None:
                    continue
                child = item_i.widget()
                if child is not None:
                    acc_list2.extend(walk(child))
            return acc_list2
        return []

    if root_layout.count() == 0:
        return []
    root_item = root_layout.itemAt(0)
    if root_item is None:
        return []
    root_child = root_item.widget()
    if root_child is None:
        return []
    return walk(root_child)
