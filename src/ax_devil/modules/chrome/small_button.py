"""A compact button for the actions on a list row or at the foot of a sidebar view."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Height, Space, TextRole


class SmallButton(QPushButton):
    """A row-height action using the shared button appearance, with optional text and icon."""

    def __init__(self, text: str, icon: Icon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        TextRole.SMALL.apply(self)
        if icon is not None:
            self.setIcon(icon.icon())
        padding = Space.S if not text else Space.M
        self.setStyleSheet(f"QPushButton {{ min-height: 0; padding: 0 {padding}px; }}")
        follow_appearance(self, lambda: self.setFixedHeight(Height.ROW.px))
