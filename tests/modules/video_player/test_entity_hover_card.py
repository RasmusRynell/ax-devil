"""Tests for hover card update behavior."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.scene.inspection import debug_section_html
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
    assert card._browser.toPlainText() == "first"

    card.show_for("entity-1", "<span>updated</span>", 100, 100)
    assert card._browser.toPlainText() == "updated"


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


def _long_card(prefix: str = "metric") -> str:
    return "".join(debug_section_html({"group": {f"{prefix}_{'x' * 40}_{index}": index for index in range(80)}}))


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("text_size", [14, 21])
def test_card_scrolls_only_when_content_exceeds_viewer(qtbot: QtBot, theme: str, text_size: int) -> None:
    apply_theme(theme)
    apply_text_size(text_size)
    parent = QWidget()
    parent.resize(1000, 1100)
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    html = "".join(debug_section_html({"Object": {f"attribute_{index}": "person" for index in range(30)}}))

    card.show_for("entity", html, 100, 100, interactive=True)
    QApplication.processEvents()
    assert parent.rect().contains(card.geometry())
    assert card._browser.verticalScrollBar().maximum() == 0
    assert not card._browser.verticalScrollBar().isVisible()

    parent.resize(320, 240)
    card.show_for("entity", html, 100, 100, interactive=True)
    QApplication.processEvents()
    assert parent.rect().contains(card.geometry())
    assert card._browser.verticalScrollBar().maximum() > 0
    assert card._browser.verticalScrollBar().isVisible()

    card.hide_card()
    card.show_for("other", "<b>Person</b><br/>ID: 12<br/>Confidence: 0.98", 100, 100, interactive=True)
    QApplication.processEvents()
    assert parent.rect().contains(card.geometry())
    assert card._browser.verticalScrollBar().maximum() == 0
    assert not card._browser.verticalScrollBar().isVisible()


def test_pinned_card_fits_viewer_wraps_and_keeps_scroll_for_same_object(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(320, 300)
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    target_id = "object_identifier_" * 8
    card.show_for(target_id, f"{target_id}<br/>{_long_card()}", 100, 100, interactive=True)
    scroll = card._browser.verticalScrollBar()

    assert parent.rect().contains(card.geometry())
    assert card._browser.document().size().width() <= card._browser.viewport().width()
    assert target_id in card._browser.toPlainText()
    assert scroll.maximum() > 0
    scroll.setValue(scroll.maximum() // 2)
    kept = scroll.value()

    card.show_for(target_id, f"{target_id}<br/>{_long_card('value')}", 100, 100, interactive=True)
    assert scroll.value() == kept

    card.show_for("other", _long_card(), 100, 100, interactive=True)
    assert scroll.value() == 0


@pytest.mark.usefixtures("restore_app_appearance")
def test_pinned_card_refits_after_text_size_change(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(640, 350)
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    card.show_for("entity", _long_card(), 500, 100, interactive=True)
    cursor = card._browser.document().find("metric")
    assert cursor.hasSelection()
    card._browser.setTextCursor(cursor)
    scroll = card._browser.verticalScrollBar()
    scroll.setValue(100)
    original_width = card.width()

    apply_text_size(21)
    QApplication.processEvents()

    assert card.width() > original_width
    assert card._browser.textCursor().selectedText() == "metric"
    assert scroll.value() == 100
    assert parent.rect().contains(card.geometry())
