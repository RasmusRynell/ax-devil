"""A scrolling column of rows for sidebar views."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLayout, QScrollArea, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import SEE_THROUGH
from ax_devil.modules.chrome.tokens import Height, Space


class RowSection:
    """An ordered group of rows inside a ``RowList``, which can be cleared without touching the rest."""

    def __init__(self, layout: QVBoxLayout) -> None:
        self._layout = layout

    def add(self, widget: QWidget) -> None:
        """Append *widget* as a row, across the full width."""
        self._layout.addWidget(widget)
        # Shown now: a row left to show later is first laid out as an empty list, which loses the scroll position.
        widget.show()

    def add_padded(self, widget: QWidget, *, top: int = Space.M, bottom: int = Space.S) -> QWidget:
        """Append *widget* with the side margins rows have, *top* and *bottom* room, and at least a row's height.

        Return the padding around it, which is what to hide to hide the widget with its room.
        """
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(Space.M, top, Space.M, bottom)
        layout.addWidget(widget, 1)
        follow_appearance(holder, lambda: holder.setMinimumHeight(Height.ROW.px))
        self.add(holder)
        return holder

    def clear(self) -> None:
        """Remove every row of the section."""
        _remove_widgets(self._layout)


class RowList(QScrollArea):
    """A vertical list of rows, kept at the top, that scrolls when it does not fit.

    It is see-through over its view's background, so the view needs no stylesheet of its own. Rows added in several
    ``section()`` groups stay in the order the groups and rows were added.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QScrollArea {{ background: transparent; }} {SEE_THROUGH}")
        self.viewport().setAutoFillBackground(False)
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(0)
        column.addLayout(self._rows)
        column.addStretch(1)
        self.setWidget(content)
        self._main = RowSection(self._rows)

    def add(self, widget: QWidget) -> None:
        """Append *widget* as a row, across the full width."""
        self._main.add(widget)

    def add_padded(self, widget: QWidget, *, top: int = Space.M, bottom: int = Space.S) -> QWidget:
        """Append *widget* with the side margins rows have, *top* and *bottom* room, and at least a row's height.

        Return the padding around it, which is what to hide to hide the widget with its room.
        """
        return self._main.add_padded(widget, top=top, bottom=bottom)

    def section(self) -> RowSection:
        """Append an empty group of rows after what is listed so far; later rows come after the group."""
        layout = QVBoxLayout()
        layout.setSpacing(0)
        self._rows.addLayout(layout)
        return RowSection(layout)

    def clear(self) -> None:
        """Remove every row, in sections too, which stay and can be filled again."""
        _remove_widgets(self._rows)


def _remove_widgets(layout: QLayout) -> None:
    """Remove the widgets in *layout* and in the layouts nested in it, keeping those layouts.

    Removed widgets are hidden and deleted later, as a removed button may be the one being clicked; until then they are
    still children of the list.
    """
    for index in reversed(range(layout.count())):
        if (item := layout.itemAt(index)) is None:
            continue
        if (nested := item.layout()) is not None:
            _remove_widgets(nested)
        elif (widget := item.widget()) is not None:
            layout.takeAt(index)
            widget.hide()
            widget.deleteLater()
