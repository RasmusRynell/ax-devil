from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListView
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.scene.model import Delete, EntityId, Rename, Scene, TimeSlice
from ax_devil.modules.video_viewer.media_tools.event_log_widget import (
    _STATE_ROLE,
    LIVE_EVENT_LIMIT,
    EventLogWidget,
    EventRowState,
)
from ax_devil.modules.video_viewer.media_tools.frame_labels import format_frame_time, format_short_frame_time


def _frame_id(frame: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=frame, timestamp_monotime_us=frame * 40_000.0)


def _event(frame: int, label: str = "Delete a") -> FrameEvent:
    return FrameEvent(_frame_id(frame), label.split()[0], label, ("a",))


def _history(*events: FrameEvent) -> SceneHistory:
    return SceneHistory(events=events, objects=(), frame_count=100)


def _deleting_scene(*entity_ids: str) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity_id in entity_ids:
        scene.add_event(Delete(timestamp=scene.time_slice, entity_id=EntityId(entity_id)))
    return scene


def _labels(widget: EventLogWidget) -> list[str]:
    model = widget._model
    return [str(model.data(model.index(row, 0))) for row in range(model.rowCount())]


def _states(widget: EventLogWidget) -> list[EventRowState]:
    model = widget._model
    return [model.data(model.index(row, 0), _STATE_ROLE) for row in range(model.rowCount())]


def _follow(widget: EventLogWidget, frame: int) -> None:
    widget.update_scene(None, _frame_id(frame), {})


def test_history_log_orders_events_by_frame_and_follows_the_displayed_frame(qtbot: QtBot) -> None:
    widget = EventLogWidget(_history(_event(5, "Delete b"), _event(2, "Rename a → b"), _event(5, "Delete c")))
    qtbot.addWidget(widget)
    widget.show()

    assert _labels(widget) == [
        "#2 00:00:00.080 Rename a → b",
        "#5 00:00:00.200 Delete b",
        "#5 00:00:00.200 Delete c",
    ]
    assert widget._count_label.text() == "3 events"
    assert _states(widget) == [EventRowState.PAST] * 3

    _follow(widget, 3)
    assert _states(widget) == [EventRowState.PAST, EventRowState.UPCOMING, EventRowState.UPCOMING]
    assert widget._model.position_row() == 0

    _follow(widget, 5)
    assert _states(widget) == [EventRowState.PAST, EventRowState.CURRENT, EventRowState.CURRENT]
    assert widget._model.position_row() == 2

    _follow(widget, 1)
    assert _states(widget) == [EventRowState.UPCOMING] * 3
    assert widget._model.position_row() is None


def test_history_log_ignores_shown_scenes_and_keeps_its_events_when_cleared(qtbot: QtBot) -> None:
    widget = EventLogWidget(_history(_event(2)))
    qtbot.addWidget(widget)

    widget.update_scene(_deleting_scene("live"), _frame_id(9), {"overlay_reused": False})
    widget.clear()

    assert _labels(widget) == ["#2 00:00:00.080 Delete a"]


def test_rows_are_repainted_when_their_state_changes(qtbot: QtBot) -> None:
    """Moving the displayed frame restates every row that changed, and an unchanged frame restates nothing."""
    widget = EventLogWidget(_history(*(_event(frame) for frame in (1, 4, 7, 9))))
    qtbot.addWidget(widget)
    widget.show()
    _follow(widget, 0)
    restated: list[set[int]] = []
    widget._model.dataChanged.connect(
        lambda first, last, _roles: restated[-1].update(range(first.row(), last.row() + 1))
    )

    for frame in (7, 7, 8):
        restated.append(set())
        _follow(widget, frame)

    assert {0, 1, 2} <= restated[0]
    assert restated[1] == set()
    assert 2 in restated[2]


def test_history_log_scrolls_only_when_the_playback_position_leaves_the_view(qtbot: QtBot) -> None:
    widget = EventLogWidget(_history(*(_event(frame) for frame in range(200))))
    qtbot.addWidget(widget)
    widget.resize(320, 200)
    widget.show()
    qtbot.waitExposed(widget)
    view = widget.findChild(QListView)
    assert view is not None
    scroll_bar = view.verticalScrollBar()

    def fully_visible(row: int) -> bool:
        return view.viewport().rect().contains(view.visualRect(view.model().index(row, 0)))

    _follow(widget, 150)
    assert fully_visible(150)
    scrolled = scroll_bar.value()
    _follow(widget, 151)
    assert fully_visible(151) and scroll_bar.value() == scrolled

    _follow(widget, 10)
    assert fully_visible(10)


def test_clicking_selects_an_event_and_double_clicking_requests_its_frame(qtbot: QtBot) -> None:
    widget = EventLogWidget(_history(_event(2), _event(6, "Delete b")))
    qtbot.addWidget(widget)
    widget.resize(320, 200)
    widget.show()
    qtbot.waitExposed(widget)
    view = widget._list_view
    center = view.visualRect(widget._model.index(1, 0)).center()

    with qtbot.waitSignal(widget.eventSelected) as selected:
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
    with qtbot.waitSignal(widget.frameRequested) as requested:
        QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)

    assert selected.args == [_event(6, "Delete b")]
    assert requested.args == [6]


def test_hidden_log_waits_for_the_displayed_frame_until_shown(qtbot: QtBot) -> None:
    widget = EventLogWidget(_history(_event(2), _event(6)))
    qtbot.addWidget(widget)
    changed: list[object] = []
    widget._model.dataChanged.connect(lambda *args: changed.append(args))

    _follow(widget, 3)
    _follow(widget, 6)
    assert changed == [] and widget._model.position_row() is None

    widget.show()
    assert _states(widget) == [EventRowState.PAST, EventRowState.CURRENT]
    assert widget._model.position_row() == 1


def test_hidden_live_log_still_records_events(qtbot: QtBot) -> None:
    widget = EventLogWidget(None)
    qtbot.addWidget(widget)

    widget.update_scene(_deleting_scene("a"), _frame_id(7), {"overlay_reused": False})
    assert _labels(widget) == ["#7 00:00:00.280 Delete a"]

    widget.show()
    assert _states(widget) == [EventRowState.CURRENT]


def test_live_log_adds_events_of_newly_shown_overlays_only(qtbot: QtBot) -> None:
    widget = EventLogWidget(None)
    qtbot.addWidget(widget)
    widget.show()
    requested: list[int] = []
    widget.frameRequested.connect(requested.append)

    widget.update_scene(_deleting_scene("a"), _frame_id(7), {"overlay_reused": False})
    widget.update_scene(_deleting_scene("a"), _frame_id(8), {"overlay_reused": True})
    widget.update_scene(None, _frame_id(9), {})
    widget.update_scene(_deleting_scene("b"), _frame_id(10), {"overlay_reused": False})

    assert _labels(widget) == ["#7 00:00:00.280 Delete a", "#10 00:00:00.400 Delete b"]
    assert _states(widget) == [EventRowState.PAST, EventRowState.CURRENT]
    view = widget.findChild(QListView)
    assert view is not None
    QTest.mouseDClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(view.model().index(0, 0)).center()
    )
    assert requested == []  # Live events cannot seek.

    widget.clear()
    assert widget._model.rowCount() == 0
    assert widget._count_label.text() == "No events"


def test_live_log_keeps_only_the_newest_events(qtbot: QtBot) -> None:
    widget = EventLogWidget(None)
    qtbot.addWidget(widget)

    widget.update_scene(_deleting_scene(*(str(index) for index in range(LIVE_EVENT_LIMIT))), _frame_id(1), {})
    widget.update_scene(_deleting_scene("newest", "last"), _frame_id(2), {})

    assert widget._model.rowCount() == LIVE_EVENT_LIMIT
    assert _labels(widget)[0].endswith("Delete 2")
    assert _labels(widget)[-1].endswith("Delete last")


def test_live_log_follows_new_events_only_while_scrolled_to_the_bottom(qtbot: QtBot) -> None:
    widget = EventLogWidget(None)
    qtbot.addWidget(widget)
    widget.resize(320, 120)
    widget.show()
    qtbot.waitExposed(widget)
    scroll_bar = widget._list_view.verticalScrollBar()

    widget.update_scene(_deleting_scene(*(str(index) for index in range(50))), _frame_id(1), {})
    assert scroll_bar.value() == scroll_bar.maximum() > 0

    scroll_bar.setValue(0)
    widget.update_scene(_deleting_scene("more"), _frame_id(2), {})
    assert scroll_bar.value() == 0


def test_frame_times_use_clock_parts() -> None:
    assert format_frame_time(0) == "00:00:00.000"
    assert format_frame_time(3_723_456_789.0) == "01:02:03.456"
    assert format_frame_time(1_700_000_000_123_000.0) == "22:13:20.123"
    assert format_short_frame_time(83_000_000.0) == "1:23"
    assert format_short_frame_time(3_723_456_789.0) == "1:02:03"


def test_search_and_kind_filter_narrow_the_history_log(qtbot: QtBot) -> None:
    widget = EventLogWidget(_history(_event(1, "Rename 7 → 9"), _event(3, "Delete 9"), _event(5, "Delete 12")))
    qtbot.addWidget(widget)
    widget.show()
    _follow(widget, 4)

    assert list(widget._kind_boxes) == ["Delete", "Rename"]
    widget._search.setText("9")
    assert _labels(widget) == ["#1 00:00:00.040 Rename 7 → 9", "#3 00:00:00.120 Delete 9"]
    assert widget._count_label.text() == "2 of 3 events"
    assert widget._model.position_row() == 1

    widget._kind_boxes["Rename"].setChecked(False)
    assert _labels(widget) == ["#3 00:00:00.120 Delete 9"]
    assert widget._filter_button.text() == "Filter 1/2"

    widget._search.clear()
    widget._kind_boxes["Rename"].setChecked(True)
    assert widget._model.rowCount() == 3 and widget._count_label.text() == "3 events"


def test_live_log_offers_new_kinds_and_filters_while_trimming(qtbot: QtBot) -> None:
    widget = EventLogWidget(None)
    qtbot.addWidget(widget)
    widget.update_scene(_deleting_scene("a"), _frame_id(1), {})
    assert list(widget._kind_boxes) == ["Delete"]

    widget._kind_boxes["Delete"].setChecked(False)
    rename = Scene(time_slice=TimeSlice(start=0, end=0))
    rename.add_event(Rename(timestamp=rename.time_slice, from_entity_id=EntityId("a"), to_entity_id=EntityId("b")))
    widget.update_scene(rename, _frame_id(2), {})

    assert list(widget._kind_boxes) == ["Delete", "Rename"]
    assert not widget._kind_boxes["Delete"].isChecked() and widget._kind_boxes["Rename"].isChecked()
    assert _labels(widget) == ["#2 00:00:00.080 Rename a → b"]

    widget.update_scene(_deleting_scene(*(str(index) for index in range(LIVE_EVENT_LIMIT))), _frame_id(3), {})
    assert widget._model.total_count == LIVE_EVENT_LIMIT
    assert widget._model.rowCount() == 0  # The rename was trimmed; the remaining deletes are hidden.
