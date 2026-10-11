"""A compact bordered button for the actions on a list row or at the foot of a sidebar view."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QPushButton, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole


class ButtonKind(Enum):
    """How much a button stands out: a plain action, the main one, or one that discards something."""

    NORMAL = "normal"
    PRIMARY = "primary"
    DANGER = "danger"


class SmallButton(QPushButton):
    """A button one row high, with an optional icon; give an empty *text* for an icon-only button."""

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
        palette = self.palette()
        self.setFixedHeight(Height.ROW.px)
        hover = palette_color_css(palette, QPalette.ColorRole.Text, alpha=0.08)
        border = palette_color_css(palette, QPalette.ColorRole.Mid, alpha=0.7)
        text = palette.color(QPalette.ColorRole.Text)
        fill, line, hover_fill = "transparent", border, hover
        if self._kind is ButtonKind.PRIMARY:
            text = palette.color(QPalette.ColorRole.HighlightedText)
            fill = line = "palette(highlight)"
            hover_fill = palette.color(QPalette.ColorRole.Highlight).lighter(112).name()
        elif self._kind is ButtonKind.DANGER:
            text = StatusColor.ERROR.color(palette)
            line = text.name()
        padding = Space.S if not self.text() else Space.M
        self.setStyleSheet(
            f"""
            QPushButton {{
                border: 1px solid {line};
                border-radius: {Radius.CONTROL}px;
                padding: 0 {padding}px;
                background: {fill};
                color: {text.name()};
            }}
            QPushButton:hover {{ background: {hover_fill}; }}
            """
        )
        if self._icon is not None:
            self.setIcon(self._icon.icon(text))
