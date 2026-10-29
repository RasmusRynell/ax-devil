"""The hover card a section tree shows beside the row the user rests the pointer on."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import cast

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QHelpEvent, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from ax_devil.modules.chrome.hover_card import HoverCard
from ax_devil.modules.thumbnails import Thumbnails
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
    """The row under the pointer and where it is on screen."""

    tree: SectionTree
    row: WorkspaceBrowserRow
    rect: QRect


class RowHover(QObject):
    """Show a ``HoverCard`` for the row of a watched tree that the pointer rests on: its name and facts, and the
    picture its thumbnail source gives, if it has one.

    The tree's own tooltip delay is the hover intent. While the card is shown it follows the pointer from row to row
    and goes away when the pointer leaves the rows or the user scrolls, clicks, types, or leaves the application. A
    second after it appears, and every second after, it asks for its picture again, so the picture of an open stream
    moves and a stale one is checked again.
    """

    def __init__(self, thumbnails: Thumbnails, parent: QObject | None = None) -> None:
        """Create a controller that reads pictures through *thumbnails*, owned by *parent*."""
        super().__init__(parent)
        self._thumbnails = thumbnails
        self._trees: dict[QObject, SectionTree] = {}
        """The watched trees by their viewports."""
        self._card: HoverCard | None = None
        self._shown: str | None = None
        """The id of the row the card shows, None while it is hidden."""
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
        viewport = tree.viewport()
        rect = tree.visualItemRect(item)
        return _Hovered(tree, cast(WorkspaceBrowserRow, row), QRect(viewport.mapToGlobal(rect.topLeft()), rect.size()))

    def _row_under(self, global_pos: QPoint) -> _Hovered | None:
        """Return the row of a watched tree at *global_pos*, or None where the pointer is not over one."""
        widget = QApplication.widgetAt(global_pos)
        for tree in self._trees.values():
            viewport = tree.viewport()
            if widget is not None and (widget is viewport or viewport.isAncestorOf(widget)):
                return self._row_at(tree, viewport.mapFromGlobal(global_pos))
        return None

    def _follow(self, global_pos: QPoint) -> None:
        """Move the card to the row at *global_pos* when that is another row, and hide it off the rows."""
        hovered = self._row_under(global_pos)
        if hovered is None:
            self.hide()
        elif hovered.row.row_id != self._shown:
            self._show(hovered)

    def _refresh_card(self) -> None:
        """Ask again for the picture of the row under the pointer, which may be another row than the card shows."""
        hovered = self._row_under(QCursor.pos())
        if hovered is None:
            self.hide()
        else:
            self._show(hovered)

    def _show(self, hovered: _Hovered) -> None:
        """Show the card for *hovered*, replacing the one shown, and request its picture."""
        row = hovered.row
        card = self._card
        if card is None:
            card = self._card = HoverCard(hovered.tree.window())
        card.set_content(row.label, row.tooltip)
        if row.row_id != self._shown:
            self._shown = row.row_id
            if row.thumbnail is None:
                card.set_picture(None)
            else:
                card.set_picture_pending()  # Before the request, which may answer at once.
            card.show_beside(hovered.rect)
            if (app := QCoreApplication.instance()) is not None:
                app.installEventFilter(self)
            self._refresh.start()
        if row.thumbnail is not None:
            self._thumbnails.request(row.thumbnail, partial(self._deliver, row.row_id))

    def _deliver(self, row_id: str, image: QImage | None) -> None:
        """Show *image* in the card, unless the card has moved on to another row."""
        if self._card is not None and row_id == self._shown:
            self._card.set_picture(image)
