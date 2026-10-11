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
