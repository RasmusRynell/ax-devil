"""Tests for hover card update behavior."""

from __future__ import annotations

from PySide6.QtCore import Qt
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


def test_hover_card_wraps_long_content_after_viewer_narrows(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(800, 600)
    qtbot.addWidget(parent)
    card = EntityHoverCard(parent)
    html = f"<span>{'long diagnostic text ' * 12}</span>"
    card.show_for("entity-1", html, 100, 100)

    parent.resize(320, 600)
    card.show_for("entity-1", html, 100, 100)

    assert card.geometry().right() < parent.width()
    assert card.geometry().bottom() < parent.height()
    assert "long diagnostic text" in card._browser.toPlainText()


def test_pinned_inspector_retains_all_fields_scroll_and_full_id(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(320, 300)
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    target_id = "12d872dd-1285-536d-bc40-123456789abc"
    sections = debug_section_html({"recentMotion": {f"metric_{index}": index for index in range(80)}})
    card.show_for(target_id, target_id, 100, 100, sections=sections, interactive=True)
    scroll = card._browser.verticalScrollBar()

    assert parent.rect().contains(card.geometry())
    assert target_id in card._browser.toPlainText()
    assert all(f"metric_{index}" in card._browser.toPlainText() for index in range(80))
    assert not card.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert scroll.maximum() > 0
    scroll.setValue(scroll.maximum() // 2)
    previous_scroll = scroll.value()
    previous_position = card.pos()

    updated = debug_section_html({"recentMotion": {f"metric_{index}": index + 1 for index in range(80)}})
    card.show_for(target_id, target_id, 100, 100, sections=updated, interactive=True)

    assert scroll.value() == previous_scroll
    assert card.pos() == previous_position
    scroll.setValue(scroll.maximum())
    assert "metric_79" in card._browser.toPlainText()

    card.show_for("other", "other", 100, 100, sections=updated, interactive=True)
    assert scroll.value() == 0


def test_inspector_uses_columns_when_wide_and_reflows_without_losing_fields(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(1200, 700)
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    sections = debug_section_html({f"group_{index}": {"value": index} for index in range(6)})
    card.show_for("entity", "entity", 100, 100, sections=sections)

    assert card._column_count == 2
    assert card._browser.verticalScrollBar().maximum() == 0

    parent.resize(320, 300)
    card.show_for("entity", "entity", 100, 100, sections=sections, interactive=True)

    assert card._column_count == 1
    assert parent.rect().contains(card.geometry())
    assert all(f"group_{index}" in card._browser.toPlainText() for index in range(6))


def test_unchanged_inspection_does_not_reparse_document(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(640, 480)
    qtbot.addWidget(parent)
    card = EntityHoverCard(parent)
    sections = debug_section_html({"velocity": {"x": 0.002969, "y": 0.01}})
    card.show_for("entity", "entity", 100, 100, sections=sections)
    changes: list[bool] = []
    card._browser.textChanged.connect(lambda: changes.append(True))

    for index in range(20):
        card.show_for("entity", "entity", 100 + index, 100, sections=sections, interactive=True)

    assert changes == []
    card.show_for("entity", "updated", 100, 100, sections=sections, interactive=True)
    assert changes == [True]


def test_long_ids_keys_and_values_wrap_without_hidden_horizontal_overflow(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(320, 300)
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    target_id = "object_identifier_" * 8
    key = "unbroken_diagnostic_key_" * 8
    value = "unbroken_diagnostic_value_" * 8
    sections = debug_section_html({"group": {key: value}})
    card.show_for(target_id, target_id, 100, 100, sections=sections, interactive=True)

    assert parent.rect().contains(card.geometry())
    assert card._browser.document().size().width() <= card._browser.viewport().width()
    assert target_id in card._browser.toPlainText()
    assert key in card._browser.toPlainText()
    assert value in card._browser.toPlainText()


def test_appearance_update_preserves_pinned_scroll_and_bounds(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(640, 350)
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    sections = debug_section_html({"group": {f"metric_{index}": index for index in range(80)}})
    card.show_for("entity", "entity", 500, 100, sections=sections, interactive=True)
    scroll = card._browser.verticalScrollBar()
    scroll.setValue(100)
    original_font_size = QApplication.font().pixelSize()
    try:
        apply_theme("light")
        QApplication.processEvents()
        assert scroll.value() == 100
        apply_text_size(21)
        QApplication.processEvents()
        assert scroll.value() == 100
        assert parent.rect().contains(card.geometry())
    finally:
        apply_text_size(original_font_size)
