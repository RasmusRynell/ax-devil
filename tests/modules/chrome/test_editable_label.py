"""Editable label: a click edits the text in place."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.editable_label import EditableLabel


def _label(qtbot: QtBot) -> tuple[EditableLabel, list[str]]:
    label = EditableLabel("Untitled", Qt.TextElideMode.ElideRight)
    qtbot.addWidget(label)
    label.resize(200, 24)
    label.show()
    qtbot.waitExposed(label)
    label.activateWindow()
    edited: list[str] = []
    label.edited.connect(edited.append)
    return label, edited


def _click(qtbot: QtBot, label: EditableLabel) -> QLineEdit:
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    field = label.findChild(QLineEdit)
    assert field is not None and field.isVisible()
    qtbot.waitUntil(field.hasFocus)
    return field


def test_a_click_edits_the_text_in_place_and_reports_it_as_it_is_typed(qtbot: QtBot) -> None:
    label, edited = _label(qtbot)

    field = _click(qtbot, label)
    assert field.text() == "Untitled" and field.selectedText() == "Untitled"
    QTest.keyClicks(field, "Lot")
    assert edited == ["L", "Lo", "Lot"]  # A save at any moment sees the name typed so far.
    QTest.keyClick(field, Qt.Key.Key_Return)

    assert edited[-1] == "Lot"
    assert not field.isVisible()
    assert label.full_text() == "Untitled"  # The owner decides what is shown.


def test_escape_puts_back_the_text_the_label_had(qtbot: QtBot) -> None:
    label, edited = _label(qtbot)

    field = _click(qtbot, label)
    QTest.keyClicks(field, "Lot")
    QTest.keyClick(field, Qt.Key.Key_Escape)

    assert edited[-1] == "Untitled"
    assert not field.isVisible()


def test_ending_an_edit_without_typing_reports_nothing(qtbot: QtBot) -> None:
    label, edited = _label(qtbot)

    QTest.keyClick(_click(qtbot, label), Qt.Key.Key_Return)
    QTest.keyClick(_click(qtbot, label), Qt.Key.Key_Escape)

    assert edited == []


def test_leaving_the_field_ends_the_edit_and_keeps_what_was_typed(qtbot: QtBot) -> None:
    label, edited = _label(qtbot)

    field = _click(qtbot, label)
    QTest.keyClicks(field, "Lot")
    field.clearFocus()

    assert edited[-1] == "Lot"
    assert not field.isVisible()
