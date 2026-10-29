"""A layout that places widgets left to right and wraps to a new row where the width runs out."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget

from ax_devil.modules.chrome.tokens import Space


class FlowLayout(QLayout):
    """Wrap items into rows of their size hints, with ``Space.S`` between them.

    The height depends on the width, so the layout reports ``heightForWidth``; its minimum size is its widest item.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty layout, installed on *parent* when given."""
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(Space.S)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        """Append *item* after the last one."""
        self._items.append(item)

    def count(self) -> int:
        """Return the number of items."""
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        """Return the item at *index*, or None when out of range."""
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        """Remove and return the item at *index*, or None when out of range."""
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        """Return no direction: the layout takes the space it is given and no more."""
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        """Return True: the number of rows follows the width."""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        """Return the height of the rows that fit in *width*, margins included."""
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        """Place the items in rows inside *rect*."""
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the minimum size."""
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        """Return the size of the widest and the tallest item, margins included."""
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _place(self, rect: QRect, *, apply: bool) -> int:
        """Lay the items out inside *rect*, move them when *apply*, and return the height used including margins."""
        margins = self.contentsMargins()
        inner = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        gap = self.spacing()
        x, y, row_height = inner.x(), inner.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > inner.x() and x + hint.width() > inner.right() + 1:
                x, y, row_height = inner.x(), y + row_height + gap, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            row_height = max(row_height, hint.height())
        return y + row_height - inner.y() + margins.top() + margins.bottom()
