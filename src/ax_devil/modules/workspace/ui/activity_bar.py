"""Activity bar: the strip of icon buttons along the window's left edge."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Height, Radius, Space


class ActivityBar(QWidget):
    """A vertical strip of icon buttons: the sidebar view at the top, and app-wide actions at the bottom.

    A view button is checked while its view is on screen; the owner reports that through ``set_view_shown``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilActivityBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._buttons: list[QToolButton] = []
        self._view_button: QToolButton | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(Space.XS, Space.S, Space.XS, Space.S)
        self._layout.setSpacing(Space.XS)
        self._layout.addStretch(1)
        follow_appearance(self, self._apply_appearance)

    def add_view(self, icon: Icon, tooltip: str, activate: Callable[[], None]) -> QToolButton:
        """Add the sidebar view button at the top; clicking it runs *activate*."""
        button = self._add_button(icon, tooltip, activate, at=0)
        button.setCheckable(True)
        self._view_button = button
        return button

    def add_action(self, icon: Icon, tooltip: str, activate: Callable[[], None]) -> QToolButton:
        """Add an action button below the earlier ones at the bottom."""
        return self._add_button(icon, tooltip, activate, at=self._layout.count())

    def set_view_shown(self, shown: bool) -> None:
        """Check the view button while the sidebar view is on screen."""
        if self._view_button is not None:
            self._view_button.setChecked(shown)

    def _add_button(self, icon: Icon, tooltip: str, activate: Callable[[], None], *, at: int) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setIcon(icon.icon())
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False: activate())
        self._layout.insertWidget(at, button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._buttons.append(button)
        self._size_button(button)
        return button

    def _size_button(self, button: QToolButton) -> None:
        side = Height.TITLE_BAR.px
        button.setFixedSize(side, side)
        icon_side = round(side * 0.55)
        button.setIconSize(QSize(icon_side, icon_side))

    def _apply_appearance(self) -> None:
        for button in self._buttons:
            self._size_button(button)
        palette = self.palette()
        border = palette_color_css(palette, palette.ColorRole.Mid, alpha=0.5)
        accent = palette_color_css(palette, palette.ColorRole.Highlight)
        checked = palette_color_css(palette, palette.ColorRole.Highlight, alpha=0.16)
        self.setStyleSheet(
            f"""
            #AxDevilActivityBar {{
                background: palette(window);
                border-right: 1px solid {border};
            }}
            #AxDevilActivityBar QToolButton {{
                border: none;
                border-left: 2px solid transparent;
                border-radius: {Radius.CONTROL}px;
                background: transparent;
            }}
            #AxDevilActivityBar QToolButton:hover {{ background: palette(midlight); }}
            #AxDevilActivityBar QToolButton:checked {{
                background: {checked};
                border-left: 2px solid {accent};
            }}
            """
        )
