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


def _click(label: EditableLabel) -> QLineEdit:
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    field = label.findChild(QLineEdit)
    assert field is not None and field.isVisible()
    return field


def test_a_click_edits_the_text_and_enter_reports_it(qtbot: QtBot) -> None:
    label, edited = _label(qtbot)

    field = _click(label)
    assert field.text() == "Untitled" and field.selectedText() == "Untitled"
    QTest.keyClicks(field, "Parking lot")
    QTest.keyClick(field, Qt.Key.Key_Return)

    assert edited == ["Parking lot"]
    assert not field.isVisible()
    assert label.full_text() == "Untitled"  # The owner decides what is shown.


def test_escape_and_an_unchanged_text_report_nothing(qtbot: QtBot) -> None:
    label, edited = _label(qtbot)

    field = _click(label)
    QTest.keyClicks(field, "Parking lot")
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert not field.isVisible()

    field = _click(label)
    QTest.keyClick(field, Qt.Key.Key_Return)

    assert edited == []


def test_leaving_the_field_keeps_what_was_typed(qtbot: QtBot) -> None:
    label, edited = _label(qtbot)

    field = _click(label)
    qtbot.waitUntil(field.hasFocus)
    QTest.keyClicks(field, "Lot")
    field.clearFocus()

    assert edited == ["Lot"]
    assert not field.isVisible()
