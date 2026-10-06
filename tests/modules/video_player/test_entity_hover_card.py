"""Tests for hover card update behavior."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.ui.entity_hover_card import EntityHoverCard


def _rects_intersect(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    return not (
        first_x + first_width <= second_x
        or second_x + second_width <= first_x
        or first_y + first_height <= second_y
        or second_y + second_height <= first_y
    )


def test_hover_card_updates_html_for_same_target_id(qtbot: QtBot) -> None:
    """When target is unchanged but payload changes, card content must refresh."""
    parent = QWidget()
    parent.resize(640, 480)
    qtbot.addWidget(parent)

    card = EntityHoverCard(parent)
    card.show_for("entity-1", "<span>first</span>", 100, 100)
    assert card._label.text() == "<span>first</span>"

    card.show_for("entity-1", "<span>updated</span>", 100, 100)
    assert card._label.text() == "<span>updated</span>"


def test_hover_card_prefers_left_when_more_room_on_left(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(320, 240)
    qtbot.addWidget(parent)

    card = EntityHoverCard(parent)
    avoid_rect = (260, 100, 40, 40)
    card.show_for("entity-1", "<span>wide hover card content</span>", 300, 100, avoid_rect=avoid_rect)

    assert card.x() + card.width() <= avoid_rect[0]
    assert not _rects_intersect((card.x(), card.y(), card.width(), card.height()), avoid_rect)


def test_hover_card_prefers_above_when_more_room_above(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(320, 240)
    qtbot.addWidget(parent)

    card = EntityHoverCard(parent)
    avoid_rect = (120, 200, 40, 20)
    card.show_for("entity-1", "<span>tall hover card content</span>", 160, 200, avoid_rect=avoid_rect)

    assert card.y() + card.height() <= avoid_rect[1]
    assert not _rects_intersect((card.x(), card.y(), card.width(), card.height()), avoid_rect)


def test_hover_card_avoids_object_rect_when_space_exists(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(640, 480)
    qtbot.addWidget(parent)

    card = EntityHoverCard(parent)
    avoid_rect = (220, 160, 80, 80)
    card.show_for("entity-1", "<span>hover content</span>", 300, 160, avoid_rect=avoid_rect)

    assert not _rects_intersect((card.x(), card.y(), card.width(), card.height()), avoid_rect)
