"""Event filters that start header drags and track which pane the user interacts with."""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

from PySide6.QtCore import QChildEvent, QEvent, QMimeData, QObject, QPoint, Qt
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import QAbstractButton, QApplication

from ax_devil.modules.workspace.ui.split_panes import PANEL_MIME_TYPE
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget

if TYPE_CHECKING:
    from ax_devil.modules.workspace.ui.split_view import SplitView


class HeaderDragFilter(QObject):
    """Event filter installed on a widget header to initiate drag operations."""

    def __init__(self, manager: SplitView, widget: ViewerWidget, parent: QObject | None = None) -> None:
        # Parent to the header (or widget) so the filter is destroyed with it
        super().__init__(parent if parent is not None else manager)
        self._manager = manager
        # Store only a weak reference to avoid strong reference cycles retaining the widget
        self._widget_ref: weakref.ReferenceType[ViewerWidget] = weakref.ref(widget)
        self._drag_start_pos: QPoint | None = None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                # Do not start drag when clicking header buttons
                child = obj.childAt(event.position().toPoint()) if hasattr(obj, "childAt") else None
                if isinstance(child, QAbstractButton):
                    return False
                self._drag_start_pos = event.position().toPoint()
                return False
        elif event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if self._drag_start_pos is not None and (
                (event.position().toPoint() - self._drag_start_pos).manhattanLength()
                >= QApplication.startDragDistance()
            ):
                self._start_drag()
                self._drag_start_pos = None
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            self._drag_start_pos = None
            return False
        return False

    def _start_drag(self) -> None:
        widget = self._widget_ref()
        if widget is None:
            return
        drag = QDrag(widget)
        mime = QMimeData()
        widget_id = self._manager.id_for_widget(widget)
        if widget_id is None:
            return
        mime.setData(PANEL_MIME_TYPE, widget_id.encode("utf-8"))
        drag.setMimeData(mime)
        # Use header snapshot as pixmap if possible
        header = widget.get_header_widget()
        pixmap = header.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(pixmap.rect().center())
        drag.exec(Qt.DropAction.MoveAction)


class WidgetActivationFilter(QObject):
    """Event filter that reports user interaction for focused-pane tracking."""

    def __init__(self, manager: SplitView, widget: ViewerWidget, parent: QObject | None = None) -> None:
        super().__init__(parent if parent is not None else manager)
        self._manager = manager
        self._widget_ref: weakref.ReferenceType[ViewerWidget] = weakref.ref(widget)
        self._install_on(widget)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        widget = self._widget_ref()
        if widget is None:
            return False
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            self._manager.focus_widget(widget)
        elif event.type() == QEvent.Type.ChildAdded:
            child = event.child() if isinstance(event, QChildEvent) else None
            if child is not None:
                self._install_on(child)
        return False

    def _install_on(self, obj: QObject) -> None:
        if obj.property("_ax_split_activation_tracking"):
            return
        obj.setProperty("_ax_split_activation_tracking", True)
        obj.installEventFilter(self)
        for child in obj.children():
            self._install_on(child)
