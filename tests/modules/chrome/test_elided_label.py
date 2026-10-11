"""An elided label never draws text past the room its contents have."""

from __future__ import annotations

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.elided_label import ElidedLabel


def test_text_that_fills_the_label_but_not_its_padded_contents_is_elided(qtbot: QtBot) -> None:
    text = "Requires plugin API 1, host provides 2"
    label = ElidedLabel(text, Qt.TextElideMode.ElideRight)
    label.setStyleSheet("padding: 0 4px;")
    qtbot.addWidget(label)
    label.show()
    label.resize(label.fontMetrics().horizontalAdvance(text), label.height())

    assert label.fontMetrics().horizontalAdvance(label.text()) <= label.contentsRect().width()
    assert label.text() != text and label.full_text() == text


def test_markup_in_a_name_is_shown_as_text(qtbot: QtBot) -> None:
    """Names come from files and devices; a camera calling itself bold must not change how it is drawn."""
    label = ElidedLabel("<b>AXIS</b> Q1656", Qt.TextElideMode.ElideRight)
    qtbot.addWidget(label)
    label.resize(400, 20)
    label.show()

    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == "<b>AXIS</b> Q1656"
