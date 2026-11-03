"""A row list keeps its rows at the top in the order added, and scrolls when they do not fit."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.row_list import RowList
from ax_devil.modules.chrome.tokens import Height


def _shown(qtbot: QtBot, height: int) -> RowList:
    rows = RowList()
    qtbot.addWidget(rows)
    rows.resize(300, height)
    rows.show()
    return rows


def _listed(rows: RowList) -> list[QLabel]:
    """Return the rows still listed; removed ones are hidden until they are deleted."""
    return [label for label in rows.findChildren(QLabel) if not label.isHidden()]


def _top(row: QLabel, rows: RowList) -> int:
    """Return the row's top edge in the list's visible area."""
    return row.mapTo(rows.viewport(), row.rect().topLeft()).y()


def test_rows_stay_at_the_top_in_the_order_added(qtbot: QtBot) -> None:
    rows = _shown(qtbot, 600)
    labels = [QLabel(f"row {index}") for index in range(3)]
    for label in labels:
        rows.add(label)
    qtbot.waitUntil(lambda: all(label.isVisible() for label in labels))

    tops = [_top(label, rows) for label in labels]
    assert tops[0] == 0
    assert tops == sorted(tops)
    assert len(set(tops)) == len(tops)
    assert labels[-1].geometry().bottom() < rows.viewport().height() // 2
    assert all(label.width() == rows.viewport().width() for label in labels)


def test_cleared_rows_go_and_new_rows_start_at_the_top(qtbot: QtBot) -> None:
    rows = _shown(qtbot, 600)
    old = [QLabel(f"old {index}") for index in range(3)]
    for label in old:
        rows.add(label)
    rows.clear()
    fresh = QLabel("fresh")
    rows.add(fresh)

    qtbot.waitUntil(fresh.isVisible)
    assert _listed(rows) == [fresh]
    assert _top(fresh, rows) == 0


def test_a_list_taller_than_its_area_scrolls_to_the_last_row(qtbot: QtBot) -> None:
    rows = _shown(qtbot, 120)
    labels = [QLabel(f"row {index}") for index in range(40)]
    for label in labels:
        rows.add(label)
    bar = rows.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    assert _top(labels[-1], rows) >= rows.viewport().height()
    assert rows.horizontalScrollBar().maximum() == 0

    bar.setValue(bar.maximum())

    last = labels[-1]
    assert 0 <= _top(last, rows)
    assert _top(last, rows) + last.height() <= rows.viewport().height()


def test_a_padded_widget_has_a_rows_height_and_the_side_margins_of_rows(qtbot: QtBot) -> None:
    rows = _shown(qtbot, 600)
    label = QLabel("hint")
    label.setFixedHeight(1)
    holder = rows.add_padded(label, top=0, bottom=0)
    qtbot.waitUntil(label.isVisible)

    assert holder.height() >= Height.ROW.px
    assert label.mapTo(rows.viewport(), label.rect().topLeft()).x() > 0
    assert label.width() < rows.viewport().width()


def test_a_section_clears_without_touching_the_rest(qtbot: QtBot) -> None:
    rows = _shown(qtbot, 600)
    first, second = rows.section(), rows.section()
    kept = QLabel("kept")
    first.add(QLabel("gone"))
    second.add(kept)
    footer = QLabel("footer")
    rows.add(footer)

    first.clear()
    again = QLabel("again")
    first.add(again)

    qtbot.waitUntil(lambda: again.isVisible() and kept.isVisible())
    assert _top(again, rows) < _top(kept, rows) < _top(footer, rows)

    rows.clear()
    assert _listed(rows) == []
