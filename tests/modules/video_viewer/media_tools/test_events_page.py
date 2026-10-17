"""Tests for the Events page: the history list following the video, live recording and event queries."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.object_inspection.frame_labels import format_frame_time, format_short_frame_time
from ax_devil.modules.scene.model import Delete, EntityId, Scene, TimeSlice
from ax_devil.modules.video_viewer.media_tools.events_page import LIVE_EVENT_LIMIT, EventsPage
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView


def _frame_id(frame: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=frame, timestamp_monotime_us=frame * 40_000.0)


def _event(frame: int, label: str, *entity_ids: str) -> FrameEvent:
    return FrameEvent(_frame_id(frame), label.split()[0], label, entity_ids)


def _history(*events: FrameEvent) -> SceneHistory:
    return SceneHistory(events=events, objects=(), frame_count=100)


def _deleting_scene(*entity_ids: str) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity_id in entity_ids:
        scene.add_event(Delete(timestamp=scene.time_slice, entity_id=EntityId(entity_id)))
    return scene


def _page(qtbot: QtBot, history: SceneHistory | None, *, show: bool = True) -> EventsPage:
    page = EventsPage(history)
    qtbot.addWidget(page)
    page.resize(360, 300)
    if show:
        page.show()
        qtbot.waitExposed(page)
    return page


def _view(page: EventsPage) -> RowTableView:
    view = page.findChild(RowTableView)
    assert view is not None
    return view


def _position(page: EventsPage) -> tuple[int, int] | None:
    """Return the rows of the table that are of the displayed frame, as (first, past the last)."""
    position: tuple[int, int] | None = cast(Any, _view(page)).position
    return position


def _labels(page: EventsPage) -> list[str]:
    model = _view(page).rows_model
    return [cast(Any, model.row_at(row)).event.label for row in range(model.rowCount())]


def _search(page: EventsPage, text: str) -> None:
    edit = page.findChild(QLineEdit)
    assert edit is not None
    edit.setText(text)
    QTest.keyClick(edit, Qt.Key.Key_Return)


def test_history_lists_events_in_frame_order_and_follows_the_displayed_frame_once_shown(qtbot: QtBot) -> None:
    page = _page(qtbot, _history(_event(5, "Delete b"), _event(2, "Rename a → b"), _event(5, "Delete c")), show=False)
    page.update_scene(None, _frame_id(3), {})
    page.update_scene(None, _frame_id(5), {})  # A hidden page keeps only the latest frame.
    page.show()

    assert _labels(page) == ["Rename a → b", "Delete b", "Delete c"]
    assert _position(page) == (1, 3)  # The rows of frame 5 are the displayed frame's.
    page.update_scene(None, _frame_id(3), {})
    assert _position(page) == (1, 1)
    page.update_scene(None, _frame_id(1), {})
    assert _position(page) == (0, 0)


def test_list_keeps_the_scroll_position_the_user_left_it_at_when_shown_again(qtbot: QtBot) -> None:
    page = _page(qtbot, _history(*(_event(frame, f"Delete {frame}") for frame in range(100))))
    page.update_scene(None, _frame_id(80), {})
    bar = _view(page).verticalScrollBar()
    assert bar.value() > 0  # Following playback.

    bar.setValue(0)  # The user scrolls back to the start, then opens an event page and returns.
    page.hide()
    page.show()
    assert bar.value() == 0


def test_live_page_records_events_of_newly_shown_overlays_only_even_while_hidden(qtbot: QtBot) -> None:
    page = _page(qtbot, None, show=False)

    page.update_scene(_deleting_scene("a"), _frame_id(7), {"overlay_reused": False})
    page.update_scene(_deleting_scene("a"), _frame_id(8), {"overlay_reused": True})
    page.update_scene(None, _frame_id(9), {})
    page.update_scene(_deleting_scene("b"), _frame_id(10), {"overlay_reused": False})
    assert page.count == 2
    page.show()

    assert _labels(page) == ["Delete a", "Delete b"]
    page.clear()
    assert page.count == 0 and _labels(page) == []


def test_live_page_keeps_only_the_newest_events(qtbot: QtBot) -> None:
    page = _page(qtbot, None)

    page.update_scene(_deleting_scene(*(str(index) for index in range(LIVE_EVENT_LIMIT))), _frame_id(1), {})
    page.update_scene(_deleting_scene("newest", "last"), _frame_id(2), {})

    labels = _labels(page)
    assert page.count == len(labels) == LIVE_EVENT_LIMIT
    assert labels[0] == "Delete 2" and labels[-1] == "Delete last"


def test_queries_search_event_kind_involved_objects_and_label_text(qtbot: QtBot) -> None:
    events = (_event(1, "Rename 7 → 9", "7", "9"), _event(3, "Delete 9", "9"), _event(5, "Delete 12", "12"))
    page = _page(qtbot, _history(*events))

    _search(page, "kind:rename")
    assert _labels(page) == ["Rename 7 → 9"]
    _search(page, "object:9")
    assert _labels(page) == ["Rename 7 → 9", "Delete 9"]  # Any involved object matches, not just the first.
    _search(page, "12")
    assert _labels(page) == ["Delete 12"]
    _search(page, "")
    assert len(_labels(page)) == 3


def test_frame_times_use_clock_parts() -> None:
    assert format_frame_time(3_723_456_789.0) == "01:02:03.456"
    assert format_frame_time(1_700_000_000_123_000.0) == "22:13:20.123"  # Live times are UTC clock time.
    assert format_short_frame_time(83_000_000.0) == "1:23"
