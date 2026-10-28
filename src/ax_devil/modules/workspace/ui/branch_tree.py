"""A tree whose branch arrows sit beside the first line of a row."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QTreeWidget

from ax_devil.modules.chrome.tokens import Height


class FirstLineBranchTree(QTreeWidget):
    """A tree that draws a row's branch arrow level with its first line, however tall the row's widget grows when it
    shows info lines or a problem under that line."""

    def drawBranches(  # noqa: N802
        self, painter: QPainter, rect: QRect, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        """Draw the branch arrow in the height of a row's first line."""
        super().drawBranches(painter, QRect(rect.left(), rect.top(), rect.width(), Height.ROW.px), index)
