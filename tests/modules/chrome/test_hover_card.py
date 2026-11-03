"""A hover card sits beside the pointer and stays on the screen."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication, QImage
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.hover_card import HoverCard


def test_the_card_sits_beside_the_pointer_and_flips_to_its_other_side_at_the_screen_edge(qtbot: QtBot) -> None:
    card = HoverCard()
    qtbot.addWidget(card)
    card.set_picture(QImage(64, 48, QImage.Format.Format_RGB32))
    area = QGuiApplication.primaryScreen().availableGeometry()

    near_corner = area.topLeft() + QPoint(20, 20)
    far_corner = area.bottomRight() - QPoint(20, 20)

    assert card.placement(near_corner).x() > near_corner.x()
    assert card.placement(near_corner).y() > near_corner.y()
    for pointer in (near_corner, far_corner):
        placed = QRect(card.placement(pointer), card.size())
        assert area.contains(placed)
        assert not placed.contains(pointer)
