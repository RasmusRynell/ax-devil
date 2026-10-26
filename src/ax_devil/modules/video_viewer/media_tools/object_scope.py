"""The Frame | File scope of the Objects page and the switch that chooses it."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QToolButton,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole

_SWITCH_STYLE = f"""
    #objectScope {{ border: 1px solid palette(mid); border-radius: {Radius.CONTROL}px; }}
    #objectScope QToolButton {{
        border: 0px;
        border-radius: {Radius.CONTROL - 1}px;
        padding: 0px {Space.M}px;
        background: transparent;
    }}
    #objectScope QToolButton:hover {{ background: palette(midlight); }}
    #objectScope QToolButton:checked {{ color: palette(highlight); background: palette(midlight); }}
"""


class ObjectScope(Enum):
    """Which objects the page lists: those in the displayed frame, or every object in the file."""

    FRAME = "Frame"
    FILE = "File"

    @property
    def title(self) -> str:
        """Return the scope switch label."""
        return self.value

    @property
    def tooltip(self) -> str:
        """Return the scope switch help."""
        return {
            ObjectScope.FRAME: "Objects in the displayed frame, searchable by any attribute",
            ObjectScope.FILE: "Every object in the file; objects not in the displayed frame are dimmed",
        }[self]


class ScopeSwitch(QFrame):
    """The Frame | File switch: one framed control whose chosen half is marked like the current tab."""

    scopeSelected = Signal(object)  # noqa: N815  # ObjectScope

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("objectScope")
        self.setStyleSheet(_SWITCH_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        group = QButtonGroup(self)
        self._buttons: dict[ObjectScope, QToolButton] = {}
        for scope in ObjectScope:
            button = QToolButton(self)
            button.setText(scope.title)
            button.setToolTip(scope.tooltip)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            TextRole.SMALL.apply(button)
            button.clicked.connect(lambda _checked=False, chosen=scope: self.scopeSelected.emit(chosen))
            group.addButton(button)
            layout.addWidget(button)
            self._buttons[scope] = button
        follow_appearance(self, lambda: self.setFixedHeight(Height.CONTROL.px))

    def set_scope(self, scope: ObjectScope) -> None:
        """Mark *scope* as the chosen one."""
        self._buttons[scope].setChecked(True)
