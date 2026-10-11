"""A compact button for the actions on a list row or at the foot of a sidebar view."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QPushButton, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css, qcolor_to_css
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole

_FILL_ALPHA = 0.08
_HOVER_ALPHA = 0.16
_DANGER_FILL_ALPHA = 0.12
_DANGER_HOVER_ALPHA = 0.24


class ButtonKind(Enum):
    """How much a button stands out: a plain action, the main one, or one that discards something."""

    NORMAL = "normal"
    PRIMARY = "primary"
    DANGER = "danger"


class SmallButton(QPushButton):
    """A button one row high, with an optional icon; give an empty *text* for an icon-only button.

    Its outline is always the color of its fill, hovered or not, so the button reads as one shape.
    """

    def __init__(
        self, text: str, icon: Icon | None = None, kind: ButtonKind = ButtonKind.NORMAL, parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent)
        self._icon = icon
        self._kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        TextRole.SMALL.apply(self)
        follow_appearance(self, self._apply_appearance)

    def _apply_appearance(self) -> None:
        # The application's palette: once the theme stylesheet applies, a widget's own palette has other highlights.
        palette = QGuiApplication.palette()
        self.setFixedHeight(Height.ROW.px)
        text = palette.color(QPalette.ColorRole.Text)
        fill = palette_color_css(palette, QPalette.ColorRole.Text, alpha=_FILL_ALPHA)
        hover_fill = palette_color_css(palette, QPalette.ColorRole.Text, alpha=_HOVER_ALPHA)
        if self._kind is ButtonKind.PRIMARY:
            text = palette.color(QPalette.ColorRole.HighlightedText)
            fill = palette_color_css(palette, QPalette.ColorRole.Highlight)
            hover_fill = qcolor_to_css(palette.color(QPalette.ColorRole.Highlight).lighter(112))
        elif self._kind is ButtonKind.DANGER:
            text = StatusColor.ERROR.color(palette)
            fill = qcolor_to_css(text, alpha=_DANGER_FILL_ALPHA)
            hover_fill = qcolor_to_css(text, alpha=_DANGER_HOVER_ALPHA)
        padding = Space.S if not self.text() else Space.M
        self.setStyleSheet(
            f"""
            QPushButton {{
                border: 1px solid {fill};
                border-radius: {Radius.CONTROL}px;
                padding: 0 {padding}px;
                background: {fill};
                color: {text.name()};
            }}
            QPushButton:hover {{ background: {hover_fill}; border-color: {hover_fill}; }}
            """
        )
        if self._icon is not None:
            self.setIcon(self._icon.icon(text))
