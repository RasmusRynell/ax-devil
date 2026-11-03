"""A small button keeps its row height whenever its stylesheet is applied."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.tokens import Height


def test_small_button_is_a_row_high_whenever_it_is_polished(qtbot: QtBot) -> None:
    """Polishing late, as the first measurement of a closed popover does, must not shrink it to its text."""
    host = QWidget()
    qtbot.addWidget(host)
    button = SmallButton("Reset", parent=host)

    button.ensurePolished()
    assert (button.minimumHeight(), button.maximumHeight()) == (Height.ROW.px, Height.ROW.px)

    button.setStyleSheet("QPushButton { min-height: 0; padding: 0 8px; }")
    assert (button.minimumHeight(), button.maximumHeight()) == (Height.ROW.px, Height.ROW.px)
