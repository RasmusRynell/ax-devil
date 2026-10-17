"""The copy icon at the right edge of a painted row, and the check that confirms a copy.

Lists and detail pages that paint their own rows share this: the column is ``COPY_PX`` wide, the icon is dim until the
row is hovered, and a copy turns it into a check for a moment.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable

from PySide6.QtCore import QObject, QRect, QTimer
from PySide6.QtGui import QGuiApplication, QPainter, QPalette

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import TextRole

COPY_PX = 22
COPY_TOOLTIP = "Copy"
_COPIED_MS = 1500


def paint_copy_icon(painter: QPainter, rect: QRect, palette: QPalette, *, bright: bool, copied: bool) -> None:
    """Draw the copy icon centered in *rect*, or the check if *copied*; dim unless *bright*. It is a line high, so it
    grows with the text size."""
    color = palette.color(QPalette.ColorRole.Text if bright or copied else QPalette.ColorRole.PlaceholderText)
    icon = (Icon.CHECK if copied else Icon.COPY).icon(color)
    size = TextRole.BODY.px + 1
    icon.paint(
        painter,
        QRect(rect.left() + (rect.width() - size) // 2, rect.top() + (rect.height() - size) // 2, size, size),
    )


class CopiedMark(QObject):
    """Puts text on the clipboard and remembers for a moment which row's key it came from, so that row shows a check.

    *changed* is called when the mark appears and when it ends, to repaint.
    """

    def __init__(self, parent: QObject, changed: Callable[[], None]) -> None:
        super().__init__(parent)
        self.key: Hashable | None = None
        self._changed = changed
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_COPIED_MS)
        self._timer.timeout.connect(self._clear)

    def copy(self, text: str, key: Hashable) -> None:
        """Copy *text* and mark the row with *key* as copied."""
        QGuiApplication.clipboard().setText(text)
        self.key = key
        self._timer.start()
        self._changed()

    def _clear(self) -> None:
        self.key = None
        self._changed()
