"""The hover card a section tree shows next to the pointer where it enters a row that has a picture."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import cast

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QEnterEvent, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from ax_devil.modules.chrome.hover_card import HoverCard
from ax_devil.modules.thumbnails import Thumbnails, ThumbnailSource
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE, SectionTree

_REFRESH_MS = 1000
INTENT_MS = 80
"""How long the pointer must have rested on a row before its card shows, so sweeping across rows shows none. The
picture is requested at once, so it is usually there by then."""
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
    """Show a ``HoverCard`` with the thumbnail of the row of a watched tree that the pointer enters.

    Entering a row requests its picture at once and shows the card ``INTENT_MS`` later, or when the picture arrives if
    that is later. A row without a thumbnail, or whose picture is still loading or fails, shows nothing. The card is
    placed once, at the pointer where it entered the row, and stays there while the pointer moves within the row;
    entering another row hides it until that row's picture is there, and leaving the rows or scrolling, clicking,
    typing, or leaving the application hides it. A second after it appears, and every second after, it asks for its
    picture again, so the picture of an open stream moves and a stale one is checked again.
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
        """Where the pointer entered the tracked row, in global coordinates."""
        self._intent_over = False
        """Whether the pointer has rested on the tracked row for ``INTENT_MS``."""
        self._intent = QTimer(self)
        self._intent.setSingleShot(True)
        self._intent.setInterval(INTENT_MS)
        self._intent.timeout.connect(self._intent_elapsed)
        self._refresh = QTimer(self)
        self._refresh.setInterval(_REFRESH_MS)
        self._refresh.timeout.connect(self._refresh_card)
        self._picture: QImage | None = None
        """The tracked row's picture while the card is not yet shown."""

    def watch(self, tree: SectionTree) -> None:
        """Show the hover card for the rows of *tree*."""
        viewport = tree.viewport()
        self._trees[viewport] = tree
        tree.destroyed.connect(partial(self._trees.pop, viewport, None))
        if (app := QCoreApplication.instance()) is not None:
            app.installEventFilter(self)  # Installing again is harmless.

    def __del__(self) -> None:
        """Stop filtering the application's events before the garbage collector clears this object's attributes.

        The collector clears a collected object's attributes before Qt deletes it, and the application filter would
        keep calling ``eventFilter`` on the cleared object in between. Finalizers run before any clearing.
        """
        app = QCoreApplication.instance()
        if app is not None and isValid(self):
            app.removeEventFilter(self)

    def hide(self) -> None:
        """Hide the card now and stop tracking the row."""
        if self._card is not None:
            self._card.hide()
        self._shown = self._picture = None
        self._intent.stop()
        self._refresh.stop()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Track the row the pointer enters in a watched tree, and hide the card as the user moves on.

        Nothing is consumed. The application filter is what sees the rows' own widgets, which sit in the tree's
        viewport.
        """
        kind = event.type()
        if kind == QEvent.Type.Enter and watched.isWidgetType():
            self._entered(cast(QWidget, watched), cast(QEnterEvent, event).globalPosition().toPoint())
        elif self._shown is None:
            pass
        elif kind in _HIDING_EVENTS or (kind in (QEvent.Type.Hide, QEvent.Type.Leave) and watched in self._trees):
            self.hide()
        elif kind == QEvent.Type.ApplicationStateChange and not self._app_active():
            self.hide()
        return False

    def _app_active(self) -> bool:
        return QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive

    def _entered(self, widget: QWidget, global_pos: QPoint) -> None:
        """Track the row at *global_pos* when *widget*, which the pointer entered, is in a watched tree."""
        for tree in self._trees.values():
            viewport = tree.viewport()
            if widget is viewport or viewport.isAncestorOf(widget):
                hovered = self._row_at(tree, viewport.mapFromGlobal(global_pos))
                if hovered is None:
                    self.hide()
                elif _identity(hovered.row) != self._shown:
                    self._begin(hovered, global_pos)
                return

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

    def _refresh_card(self) -> None:
        """Ask again for the picture of the row under the pointer, which may be another row than the card shows."""
        hovered = self._row_under(self._pointer)
        if hovered is None:
            self.hide()
        elif _identity(hovered.row) != self._shown:
            self._begin(hovered, self._pointer)
        else:
            self._request(hovered)

    def _begin(self, hovered: _Hovered, global_pos: QPoint) -> None:
        """Track the newly entered row at *global_pos*, hiding the card of the previous one, and request its picture."""
        self.hide()
        self._shown = _identity(hovered.row)
        self._pointer = global_pos
        self._intent_over = False
        self._intent.start()
        self._refresh.start()
        self._request(hovered)

    def _request(self, hovered: _Hovered) -> None:
        row = hovered.row
        if row.thumbnail is not None:
            self._thumbnails.request(row.thumbnail, partial(self._deliver, _identity(row), hovered.tree))

    def _intent_elapsed(self) -> None:
        """Show the card if the picture is already there."""
        self._intent_over = True
        if self._picture is not None and self._card is not None:
            self._card.show_at(self._pointer)

    def _deliver(self, identity: tuple[str, ThumbnailSource | None], tree: SectionTree, image: QImage | None) -> None:
        """Show *image* at the pointer once the intent delay is over, or hide the card when there is none, unless the
        pointer has moved on to another row or another thumbnail source."""
        if identity != self._shown:
            return
        if image is None:
            self.hide()
            return
        if self._card is None:
            self._card = HoverCard(tree.window())
        self._card.set_picture(image)
        self._picture = image
        if self._intent_over:
            self._card.show_at(self._pointer)
