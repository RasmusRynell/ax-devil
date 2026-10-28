"""A row of independent on/off options drawn as chips that wrap when the width runs out."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QPushButton, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.flow_layout import FlowLayout
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole

_CHECKED_TINT = 0.2


class ToggleChips(QWidget):
    """Checkable chips, one per ``(key, label)`` item, that wrap onto further rows when the width is short.

    Every chip is a checkable ``QPushButton``, so focus, Space, a disabled state and a tooltip work as on any button.
    Checked chips take the selection color; ``toggled(key, checked)`` follows the user's clicks only.
    """

    toggled = Signal(str, bool)

    def __init__(self, items: Sequence[tuple[str, str]], parent: QWidget | None = None) -> None:
        """Create one unchecked chip for each ``(key, label)`` in *items*, in order."""
        super().__init__(parent)
        self._chips: dict[str, QPushButton] = {}
        layout = FlowLayout(self)
        for key, label in items:
            chip = QPushButton(label, self)
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.toggled.connect(partial(self.toggled.emit, key))
            self._chips[key] = chip
            layout.addWidget(chip)
        follow_appearance(self, self._apply_appearance)

    def chip(self, key: str) -> QPushButton:
        """Return the chip for *key*, for a tooltip, an object name or a disabled state."""
        return self._chips[key]

    def set_checked(self, key: str, checked: bool) -> None:
        """Check or uncheck the chip for *key* without emitting ``toggled``."""
        chip = self._chips[key]
        with QSignalBlocker(chip):
            chip.setChecked(checked)

    def _apply_appearance(self) -> None:
        palette = self.palette()
        tint = palette_color_css(palette, QPalette.ColorRole.Highlight, alpha=_CHECKED_TINT)
        self.setStyleSheet(
            f"""
            QPushButton {{
                min-height: 0; padding: 0 {Space.M}px; background: transparent;
                border: 1px solid palette(mid); border-radius: {Radius.CONTROL}px;
            }}
            QPushButton:checked {{ background-color: {tint}; color: palette(highlight); }}
            QPushButton:disabled {{
                background: transparent; border: 1px dashed palette(mid); color: palette(placeholder-text);
            }}
            QPushButton:focus {{ border-color: palette(highlight); }}
            """
        )
        for chip in self._chips.values():
            chip.setFont(TextRole.SMALL.font())
            chip.setFixedHeight(Height.ROW.px)
