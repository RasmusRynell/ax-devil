"""The hover card a section tree shows next to the pointer while it rests on a row that has a picture."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import cast

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QHelpEvent, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from ax_devil.modules.chrome.hover_card import HoverCard
from ax_devil.modules.thumbnails import Thumbnails, ThumbnailSource
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE, SectionTree

_REFRESH_MS = 1000
_HIDING_EVENTS = frozenset(
    {
        QEvent.Type.Wheel,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.KeyPress,
    }
)


@dataclass(frozen=True, slots=True)
class _Hovered:
    """The row under the pointer and the tree it is in."""

    tree: SectionTree
    row: WorkspaceBrowserRow


def _identity(row: WorkspaceBrowserRow) -> tuple[str, ThumbnailSource | None]:
    return row.row_id, row.thumbnail


class RowHover(QObject):
    """Show a ``HoverCard`` with the thumbnail of the row of a watched tree that the pointer rests on.

    The tree's own tooltip delay is the hover intent. The card appears only once the picture is there: a row without a
    thumbnail, or whose picture is still loading or fails, shows nothing. While it is shown the card follows the
    pointer; moving to another row hides it until that row's picture is there, and leaving the rows or scrolling,
    clicking, typing, or leaving the application hides it. A second after it appears, and every second after, it asks
    for its picture again, so the picture of an open stream moves and a stale one is checked again.
    """

    def __init__(self, thumbnails: Thumbnails, parent: QObject | None = None) -> None:
        """Create a controller that reads pictures through *thumbnails*, owned by *parent*."""
        super().__init__(parent)
        self._thumbnails = thumbnails
        self._trees: dict[QObject, SectionTree] = {}
        """The watched trees by their viewports."""
        self._card: HoverCard | None = None
        self._shown: tuple[str, ThumbnailSource | None] | None = None
        """The id and thumbnail source of the row under the pointer, None while nothing is tracked. A row whose source
        changes is requested afresh, and answers to the earlier source are ignored."""
        self._pointer = QPoint()
        """Where the pointer last was, in global coordinates."""
        self._refresh = QTimer(self)
        self._refresh.setInterval(_REFRESH_MS)
        self._refresh.timeout.connect(self._refresh_card)

    def watch(self, tree: SectionTree) -> None:
        """Show the hover card for the rows of *tree*."""
        viewport = tree.viewport()
        self._trees[viewport] = tree
        viewport.installEventFilter(self)

    def hide(self) -> None:
        """Hide the card now."""
        if self._card is not None:
            self._card.hide()
        self._shown = None
        self._refresh.stop()
        if (app := QCoreApplication.instance()) is not None:
            app.removeEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Show the card for a tooltip request on a watched tree, and keep it or hide it as the user moves on.

        Only the tooltip request is consumed.
        """
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            tree = self._trees.get(watched)
            hovered = self._row_at(tree, cast(QHelpEvent, event).pos()) if tree is not None else None
            if hovered is None:
                return False
            self._pointer = cast(QHelpEvent, event).globalPos()
            self._show(hovered)
            return True
        if self._shown is None:
            return False
        if kind == QEvent.Type.MouseMove:
            self._follow(cast(QMouseEvent, event).globalPosition().toPoint())
        elif kind in _HIDING_EVENTS or (kind in (QEvent.Type.Hide, QEvent.Type.Leave) and watched in self._trees):
            self.hide()
        elif kind == QEvent.Type.ApplicationStateChange and not self._app_active():
            self.hide()
        return False

    def _app_active(self) -> bool:
        return QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive

    def _row_at(self, tree: SectionTree, position: QPoint) -> _Hovered | None:
        """Return the row at *position* in the viewport of *tree*, or None where there is no row."""
        item = tree.itemAt(position)
        row = item.data(0, ROW_ROLE) if item is not None else None
        if item is None or row is None:
            return None
        return _Hovered(tree, cast(WorkspaceBrowserRow, row))

    def _row_under(self, global_pos: QPoint) -> _Hovered | None:
        """Return the row of a watched tree at *global_pos*, or None where the pointer is not over one."""
        widget = QApplication.widgetAt(global_pos)
        for tree in self._trees.values():
            viewport = tree.viewport()
            if widget is not None and (widget is viewport or viewport.isAncestorOf(widget)):
                return self._row_at(tree, viewport.mapFromGlobal(global_pos))
        return None

    def _follow(self, global_pos: QPoint) -> None:
        """Move the card with the pointer to *global_pos*; on another row hide it and ask for that row's picture."""
        self._pointer = global_pos
        hovered = self._row_under(global_pos)
        if hovered is None:
            self.hide()
        elif _identity(hovered.row) != self._shown:
            self._show(hovered)
        elif self._card is not None and self._card.isVisible():
            self._card.show_at(global_pos)

    def _refresh_card(self) -> None:
        """Ask again for the picture of the row under the pointer, which may be another row than the card shows."""
        hovered = self._row_under(self._pointer)
        if hovered is None:
            self.hide()
        else:
            self._show(hovered)

    def _show(self, hovered: _Hovered) -> None:
        """Track *hovered*, hiding the card when it is a new row, and request its picture."""
        row = hovered.row
        identity = _identity(row)
        if identity != self._shown:
            if self._card is not None:
                self._card.hide()
            self._shown = identity
            if (app := QCoreApplication.instance()) is not None:
                app.installEventFilter(self)
            self._refresh.start()
        if row.thumbnail is not None:
            self._thumbnails.request(row.thumbnail, partial(self._deliver, identity, hovered.tree))

    def _deliver(self, identity: tuple[str, ThumbnailSource | None], tree: SectionTree, image: QImage | None) -> None:
        """Show *image* at the pointer, or hide the card when there is none, unless the pointer has moved on to
        another row or another thumbnail source."""
        if identity != self._shown:
            return
        if image is None:
            self.hide()
            return
        if self._card is None:
            self._card = HoverCard(tree.window())
        self._card.set_picture(image)
        self._card.show_at(self._pointer)
