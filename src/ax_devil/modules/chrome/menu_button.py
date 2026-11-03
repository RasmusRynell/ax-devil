"""Tool button that opens a menu, marked by a chevron right after its text."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from ax_devil.modules.chrome.icons import Icon


class MenuButton(QToolButton):
    """Flat button showing ``text ⌄`` that opens ``menu`` on click.

    The chevron is the button's icon laid out right-to-left, which centers it on the text instead of the theme's
    low-hanging menu indicator. The menu itself keeps left-to-right layout.
    """

    def __init__(self, text: str, menu: QMenu, parent: QWidget | None = None) -> None:
        """Create the button with ``menu`` as its popup."""
        super().__init__(parent)
        self.setText(text)
        self.setAutoRaise(True)
        self.setIcon(Icon.MENU.icon())
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setMenu(menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Drop the two spaces QToolButton pads its text with; right-to-left they would gap the chevron."""
        hint = super().sizeHint()
        return QSize(hint.width() - 2 * self.fontMetrics().horizontalAdvance(" "), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Match the size hint, as QToolButton does."""
        return self.sizeHint()
