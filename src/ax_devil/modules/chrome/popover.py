"""A frameless panel that opens under a button and closes on any click outside it or on Escape."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtWidgets import QFrame, QLayout, QWidget

from ax_devil.modules.chrome.tokens import Radius, Space

_STYLE = f"""
QFrame#Popover {{
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: {Radius.POPUP}px;
}}
"""


class Popover(QFrame):
    """A ``Qt.Popup`` window that hosts ordinary widgets.

    Unlike a menu it has a real focus chain, closes on Escape, and stays open for clicks inside it, including on
    child popups such as a combo box's list. A click outside only closes it and is not replayed to the widget
    underneath, so the button that opened it can close it again. Subclasses add their content with a layout.
    """

    def __init__(self, owner: QWidget) -> None:
        """Create a hidden popover owned by *owner*, which keeps it alive and styles it."""
        super().__init__(owner, Qt.WindowType.Popup)
        self.setObjectName("Popover")
        self.setStyleSheet(_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self._fitting = False

    def open_below(self, anchor: QWidget, width: int | None = None) -> None:
        """Show the popover under *anchor*, right-aligned to it, and focus its first focusable child.

        It is *width* wide, or as wide as its content prefers, but never narrower than its content allows, and sits
        inside the screen of *anchor*. While open it follows its content's height with its top right corner fixed.
        """
        # A hidden widget does not pass a size change up to its layout, so content changed while the popover was
        # closed, such as a text-size change, would otherwise be measured at its old size and clip.
        for layout in self.findChildren(QLayout):
            layout.invalidate()
        area = anchor.screen().availableGeometry()
        wide = max(width if width is not None else self.sizeHint().width(), self.minimumSizeHint().width())
        wide = min(wide, area.width())
        high = min(self._height_for(wide), area.height())
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + Space.XS))
        left = max(area.left(), min(corner.x() - wide, area.right() + 1 - wide))
        top = max(area.top(), min(corner.y(), area.bottom() + 1 - high))
        self.setGeometry(QRect(left, top, wide, high))
        self.show()
        previous = self.focusWidget()
        if previous is not None:
            previous.clearFocus()
        self.focusNextChild()

    def event(self, event: QEvent) -> bool:
        """Refit the height to the content whenever its layout changes while the popover is open."""
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and self.isVisible() and not self._fitting:
            self._fitting = True
            try:
                self._refit()
            finally:
                self._fitting = False
        return handled

    def _height_for(self, width: int) -> int:
        height = self.heightForWidth(width)
        return height if height >= 0 else self.sizeHint().height()

    def _refit(self) -> None:
        """Resize to the content, keeping the top right corner where it is."""
        wide = max(self.width(), self.minimumSizeHint().width())
        high = self._height_for(wide)
        if (wide, high) != (self.width(), self.height()):
            self.setGeometry(self.x() + self.width() - wide, self.y(), wide, high)
