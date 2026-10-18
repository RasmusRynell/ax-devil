"""The panel at the foot of a sidebar view: a caption over a column of controls."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.theme import SEE_THROUGH, KindColor
from ax_devil.modules.chrome.tokens import Space


class FooterPanel(QWidget):
    """A *title* caption over ``controls``, which fill ``controls_layout``; the footer panels share this look."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(SEE_THROUGH)
        self.caption = SectionCaption(title, None, KindColor.MUTED.color)
        self.controls = QWidget(self)
        self.controls_layout = QVBoxLayout(self.controls)
        self.controls_layout.setContentsMargins(Space.M, 0, Space.M, 0)
        self.controls_layout.setSpacing(Space.S)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, Space.S, 0, 0)
        layout.setSpacing(Space.XS)
        layout.addWidget(self.caption)
        layout.addWidget(self.controls)
