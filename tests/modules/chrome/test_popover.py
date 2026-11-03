"""A popover that grows while open stays on the screen."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.popover import Popover


def test_popover_that_grows_near_the_screen_bottom_stays_on_the_screen(qtbot: QtBot) -> None:
    owner = QWidget()
    qtbot.addWidget(owner)
    anchor = QPushButton("Open", owner)
    owner.show()
    qtbot.waitExposed(owner)
    area = anchor.screen().availableGeometry()
    owner.move(area.right() - owner.width(), area.bottom() - 120 - anchor.height())
    popover = Popover(owner)
    layout = QVBoxLayout(popover)
    layout.addWidget(QLabel("Catalog"))
    popover.open_below(anchor, 200)
    qtbot.waitExposed(popover)

    layout.addWidget(extra := QLabel("Scope\n" * 12))  # Content shown after another choice, taller than the room left.
    qtbot.waitUntil(lambda: popover.height() >= extra.sizeHint().height())

    assert area.contains(popover.geometry())
