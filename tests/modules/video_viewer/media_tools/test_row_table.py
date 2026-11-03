"""Tests for the media tools table: columns that fit their content, hide when narrow, resize and copy."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.copy_cell import copy_column_px
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.chrome.tokens import TextRole, body_px
from ax_devil.modules.video_viewer.media_tools.row_columns import CellKind, Column, Labeled
from ax_devil.modules.video_viewer.media_tools.row_model import RowTableModel
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView

_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
_ROWS = [(_UUID, "human", "0.93", "10:25-12:03"), ("1a8fad5b-d9cb-469f-a165-70867728950f", "car", "0.71", "0.4")]
_DIVIDER_Y = 5


def _columns(*, stretch_class: bool = False) -> list[Column]:
    return [
        Column("Id", CellKind.ID_DOT, lambda row: Labeled(row[0], row[1])),
        Column("Class", CellKind.TEXT, lambda row: row[1], stretch=stretch_class),
        Column("Conf", CellKind.NUMBER, lambda row: row[2], priority=1),
        Column("Motion", CellKind.NUMBER, lambda row: row[3], priority=2),
    ]


def _table(
    qtbot: QtBot, width: int, *, table_key: str | None = None, copy: bool = False, stretch_class: bool = False
) -> RowTableView:
    model = RowTableModel(_columns(stretch_class=stretch_class), copy_text=(lambda row: row[0]) if copy else None)
    model.set_rows(_ROWS, lambda row: row[0])
    view = RowTableView(model, table_key=table_key)
    qtbot.addWidget(view)
    view.resize(width, 200)
    view.show()
    qtbot.waitExposed(view)
    return view


def _resized(view: RowTableView, width: int, section: int) -> bool:
    """Resize *view* to *width* and return whether *section* is shown."""
    view.resize(width, 200)
    return not view.isColumnHidden(section)


def test_narrow_table_hides_columns_by_priority_without_resetting_the_rows(qtbot: QtBot) -> None:
    view = _table(qtbot, 600)
    resets: list[None] = []
    view.rows_model.modelReset.connect(lambda: resets.append(None))
    assert not any(view.isColumnHidden(section) for section in range(4))

    view.resize(view.minimumSizeHint().width() + 60, 200)
    assert view.isColumnHidden(3)
    assert not view.isColumnHidden(0) and not view.isColumnHidden(1)

    view.resize(view.minimumSizeHint().width(), 200)
    assert view.isColumnHidden(2) and view.isColumnHidden(3)

    view.resize(600, 200)
    assert not any(view.isColumnHidden(section) for section in range(4))
    assert not resets


def test_id_is_elided_in_the_middle_only_when_it_does_not_fit_and_the_tooltip_has_all_of_it(qtbot: QtBot) -> None:
    wide, narrow = _table(qtbot, 600), _table(qtbot, 260)
    assert wide.shown_text(0, 0) == _UUID

    shown = narrow.shown_text(0, 0)
    assert shown is not None and "…" in shown
    assert shown.startswith(_UUID[:4]) and shown.endswith(_UUID[-4:])
    model = narrow.rows_model
    assert model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) == _UUID


def _drag_divider(view: RowTableView, section: int, delta: int) -> None:
    header = view.header()
    edge = QPoint(header.sectionViewportPosition(section) + header.sectionSize(section), _DIVIDER_Y)
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=edge)
    QTest.mouseMove(header.viewport(), edge + QPoint(delta, 0))
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=edge + QPoint(delta, 0))


def test_dragged_column_width_is_kept_and_double_click_fits_it_to_content(qtbot: QtBot) -> None:
    view = _table(qtbot, 700, table_key="test-row-table-widths")
    fitted = view.columnWidth(1)

    _drag_divider(view, 1, 40)
    assert view.columnWidth(1) == fitted + 40

    again = _table(qtbot, 700, table_key="test-row-table-widths")
    assert again.columnWidth(1) == fitted + 40
    assert _table(qtbot, 700).columnWidth(1) == fitted

    # A saved width is only a preference: it never pushes the columns out of a narrow table, and returns with room.
    again.resize(260, 200)
    visible = [section for section in range(4) if not again.isColumnHidden(section)]
    assert sum(again.columnWidth(section) for section in visible) <= again.viewport().width()
    again.resize(700, 200)
    assert again.columnWidth(1) == fitted + 40

    # A width dragged at one text size never goes below what the column needs at another.
    _drag_divider(again, 1, -400)
    size = body_px()
    apply_text_size(size + 6)
    try:
        assert again.columnWidth(1) >= QFontMetrics(TextRole.CAPTION.font()).horizontalAdvance("CLASS")
    finally:
        apply_text_size(size)

    header = view.header()
    edge = QPoint(header.sectionViewportPosition(1) + header.sectionSize(1), _DIVIDER_Y)
    QTest.mouseDClick(header.viewport(), Qt.MouseButton.LeftButton, pos=edge)
    assert view.columnWidth(1) == fitted


def test_copy_column_and_ctrl_c_copy_the_full_text_of_the_row(qtbot: QtBot) -> None:
    view = _table(qtbot, 260, copy=True)
    model = view.rows_model
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None
    assert view.columnWidth(model.copy_section) == copy_column_px()

    clipboard.setText("")
    rect = view.visualRect(model.index(0, model.copy_section))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert clipboard.text() == _UUID

    clipboard.setText("")
    view.setCurrentIndex(model.index(1, 0))
    QTest.keySequence(view, QKeySequence(QKeySequence.StandardKey.Copy))
    assert clipboard.text() == _ROWS[1][0]

    assert _table(qtbot, 260).rows_model.copy_section == -1


def test_divider_after_a_stretch_column_can_be_dragged_and_fitted(qtbot: QtBot) -> None:
    view = _table(qtbot, 700, stretch_class=True)
    fitted = _table(qtbot, 700).columnWidth(1)
    leftover = view.columnWidth(1)
    assert leftover > fitted

    _drag_divider(view, 1, -30)
    assert view.columnWidth(1) == leftover - 30

    header = view.header()
    edge = QPoint(header.sectionViewportPosition(1) + header.sectionSize(1), _DIVIDER_Y)
    QTest.mouseDClick(header.viewport(), Qt.MouseButton.LeftButton, pos=edge)
    assert view.columnWidth(1) == fitted


def test_ids_are_cut_only_to_a_floor_while_columns_can_be_hidden_and_the_copy_column_stays_reachable(
    qtbot: QtBot,
) -> None:
    view = _table(qtbot, 700, copy=True)
    copy = view.rows_model.copy_section
    for width in range(view.minimumSizeHint().width(), 700, 9):
        view.resize(width, 200)
        shown = view.shown_text(0, 0)
        assert shown is not None
        if not view.isColumnHidden(3):
            assert len(shown) >= 18
            assert (
                view.shown_text(0, 3) == _ROWS[0][3]
            )  # A column that can be hidden is never shown cut.  # About 20 characters, the ellipsis counting as one.
        header = view.header()
        assert header.sectionViewportPosition(copy) + header.sectionSize(copy) <= view.viewport().width()

    # A column that is empty in every row is not worth the room the ids need, until it has something to show.
    model = RowTableModel([*_columns()[:3], Column("Motion", CellKind.TEXT, lambda row: row[3], priority=1)])
    model.set_rows([(*row[:3], "") for row in _ROWS], lambda row: row[0])
    empty = RowTableView(model)
    qtbot.addWidget(empty)
    empty.show()
    qtbot.waitExposed(empty)
    model.set_rows(list(_ROWS), lambda row: row[0])
    fits = next(width for width in range(empty.minimumSizeHint().width(), 2000) if _resized(empty, width, 3))
    model.set_rows([(*row[:3], "") for row in _ROWS], lambda row: row[0])
    empty.resize(fits, 200)  # The narrowest width that shows the column with a value, whatever the font.
    assert empty.isColumnHidden(3)
    narrowest = empty.minimumSizeHint().width()
    model.set_rows(list(_ROWS), lambda row: row[0])
    assert not empty.isColumnHidden(3)
    assert empty.minimumSizeHint().width() == narrowest

    # The minimum width does not depend on the rows: long values, many rows and a scroll bar leave it unchanged.
    holder = QWidget()
    qtbot.addWidget(holder)
    layout = QVBoxLayout(holder)
    tall = RowTableView(RowTableModel(_columns()))
    layout.addWidget(tall)
    holder.resize(700, 40)
    holder.show()
    before = layout.totalMinimumSize().width()
    tall.rows_model.set_rows(
        [(f"{n}{_UUID}", "bicycle, human, vehicle", "100%", "10:25-12:03") for n in range(50)], lambda row: row[0]
    )
    assert tall.verticalScrollBar().maximum() > 0
    assert layout.totalMinimumSize().width() == before

    # A timestamp is never cut, even in the narrowest table.
    times = RowTableModel([Column("Time", CellKind.MONO, lambda row: "00:00:00.666", width_chars=12), _columns()[1]])
    times.set_rows(_ROWS, lambda row: row[0])
    clock = RowTableView(times)
    qtbot.addWidget(clock)
    clock.show()
    clock.resize(clock.minimumSizeHint().width(), 200)
    assert clock.shown_text(0, 0) == "00:00:00.666"


def test_dragging_a_divider_without_room_does_not_pin_the_width(qtbot: QtBot) -> None:
    view = _table(qtbot, 700)
    natural = view.columnWidth(1)
    view.resize(view.minimumSizeHint().width() + 20, 200)

    _drag_divider(view, 1, 80)
    view.resize(700, 200)
    assert view.columnWidth(1) == natural


def test_column_that_fills_while_the_rows_reorder_is_shown(qtbot: QtBot) -> None:
    columns = [
        Column("Id", CellKind.TEXT, lambda row: row[0]),
        Column("Conf", CellKind.NUMBER, lambda row: row[1], sort_key=lambda row: row[1]),
        Column("Motion", CellKind.NUMBER, lambda row: row[2], priority=1),
    ]
    before = [("a" * 40, "0.1", ""), ("b" * 40, "0.5", "")]
    after = [("a" * 40, "0.9", "10:25-12:03"), ("b" * 40, "0.5", "0.4")]  # The same objects, now in another order

    def table(rows: list[tuple[str, str, str]], width: int) -> RowTableView:
        model = RowTableModel(columns)
        model.sort(1, Qt.SortOrder.AscendingOrder)
        model.set_rows(rows, lambda row: row[0])
        view = RowTableView(model)
        qtbot.addWidget(view)
        view.resize(width, 200)
        view.show()
        return view

    width = next(width for width in range(150, 600) if not table(after, width).isColumnHidden(2))
    view = table(before, width)
    assert view.isColumnHidden(2)  # Nothing to show yet, and no room for it
    view.rows_model.set_rows(after, lambda row: row[0])
    assert not view.isColumnHidden(2)
