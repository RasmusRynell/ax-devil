"""Folder-icon button beside a path field that opens a file or folder chooser."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QToolButton, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Height


class BrowseButton(QToolButton):
    """A square folder button, as tall as the field beside it, that calls *browse* when clicked."""

    def __init__(self, what: str, browse: Callable[[], object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIcon(Icon.BROWSE.icon())
        self.setToolTip(f"Choose {what}")
        self.setAccessibleName(f"Choose {what}")
        self.setAutoRaise(True)
        self.clicked.connect(browse)
        follow_appearance(self, lambda: self.setFixedSize(Height.CONTROL.px, Height.CONTROL.px))
