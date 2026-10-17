"""Tests for the media tools panel: the Objects list, drilling into an object page, and following the video."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, cast

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QTabBar, QToolButton
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption, build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Delete,
    Entity,
    EntityId,
    MotionState,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.event_page import EventPage
from ax_devil.modules.video_viewer.media_tools.object_page import ObjectPage
from ax_devil.modules.video_viewer.media_tools.objects_page import ObjectScope
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey, ObjectPageKey
from ax_devil.modules.video_viewer.media_tools.row_table import (
    _DIMMED_ROLE,
    CellKind,
    Column,
    RowTableModel,
    RowTableView,
)


def _frame(index: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=index, timestamp_monotime_us=index * 1_000_000.0)


def _entity(entity_id: str, kind: str = "human", conf: float = 0.9) -> Entity:
    entity = Entity(EntityId(entity_id))
    geometry = BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True)
    classification = [Classification(kind, Score(conf))]
    entity.add_observation(
        Observation(frame_number=0, geometry=geometry, confidence=Score(conf), classification=classification)
    )
    return entity


def _scene(*entities: Entity) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity in entities:
        scene.add_entity(entity)
    return scene


def _history(
    *objects: tuple[str, str, tuple[tuple[int, int], ...]], frames: int = 10, events: tuple[FrameEvent, ...] = ()
) -> SceneHistory:
    times = tuple(index * 1_000_000 for index in range(frames))
    return SceneHistory(
        events=events,
        objects=tuple(ObjectHistory(entity_id, (kind,), spans, times) for entity_id, kind, spans in objects),
        frame_count=frames,
    )


def _panel(
    qtbot: QtBot,
    selection: SceneRenderCatalogSelection,
    history: SceneHistory | None = None,
    *,
    filter_model: SessionFilter | None = None,
    show: bool = True,
) -> MediaToolsPanel:
    filters = filter_model or SessionFilter(build_default_filter_config())
    panel = MediaToolsPanel(selection, filter_model=filters, scene_history=history)
    qtbot.addWidget(panel)
    panel.resize(360, 400)
    if show:
        panel.show()
        qtbot.waitExposed(panel)
    return panel


def _view(panel: MediaToolsPanel) -> RowTableView | None:
    """Return the table the Objects page is showing (it keeps one per scope), or None when it shows a message."""
    return next((view for view in panel.objects_page.findChildren(RowTableView) if view.isVisible()), None)


def _ids(panel: MediaToolsPanel) -> list[str]:
    view = _view(panel)
    model = view.rows_model if view is not None else None
    return [] if model is None else [cast(ObjectPageKey, model.key_at(n)).entity_id for n in range(model.rowCount())]


def _search(panel: MediaToolsPanel, text: str) -> None:
    edit = panel.objects_page.findChild(QLineEdit)
    assert edit is not None
    edit.setText(text)
    QTest.keyClick(edit, Qt.Key.Key_Return)


def _open(panel: MediaToolsPanel, entity_id: str) -> None:
    """Click the row of *entity_id* in the Objects list."""
    view = _view(panel)
    assert view is not None
    _click_row(view, ObjectPageKey(entity_id))


def _click_row(view: RowTableView, key: Hashable) -> None:
    view.scroll_to_key(key)
    rect = view.visualRect(view.rows_model.index(view.rows_model.row_of(key), 0))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def _button(panel: MediaToolsPanel, label: str) -> SmallButton | None:
    """Return the visible button of the panel with this text or tooltip."""
    buttons = [b for b in panel.findChildren(SmallButton) if label in (b.text(), b.toolTip()) and b.isVisible()]
    return buttons[0] if buttons else None


def _click(panel: MediaToolsPanel, label: str) -> None:
    button = _button(panel, label)
    assert button is not None, label
    button.click()


def _record(signal: Any) -> list[Any]:
    """Return a list that collects what *signal* emits from now on."""
    seen: list[Any] = []
    signal.connect(seen.append)
    return seen


def test_rows_follow_the_displayed_frame_and_the_query(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(_scene(_entity("untracked:0:0"), _entity("untracked:0:1")), _frame(0), {})
    panel.update_scene(_scene(_entity("untracked:1:0", conf=0.2), _entity("untracked:1:1", "car")), _frame(1), {})
    assert sorted(_ids(panel)) == ["untracked:1:0", "untracked:1:1"]

    _search(panel, "class:car")
    assert _ids(panel) == ["untracked:1:1"]
    panel.update_scene(_scene(_entity("untracked:2:0", "car"), _entity("untracked:2:1")), _frame(2), {})
    assert _ids(panel) == ["untracked:2:0"]  # The query stays while frames change.

    _search(panel, "conf>0.5")
    assert sorted(_ids(panel)) == ["untracked:2:0", "untracked:2:1"]  # Any attribute is searchable in this frame.


def test_filter_changes_reach_the_listed_frame_while_paused(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection)
    full, cars = _scene(_entity("a"), _entity("b", "car")), _scene(_entity("b", "car"))
    panel.update_scene(full, _frame(0), {}, lambda: full if panel.filter_model.is_enabled("show_humans") else cars)

    panel.filter_model.set_enabled("show_humans", False)

    assert _ids(panel) == ["b"]


def test_file_scope_lists_the_whole_file_and_explains_what_a_query_covers(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection, _history(("a", "human", ((0, 3),)), ("b", "car", ((5, 6),))))
    panel.update_scene(_scene(_entity("a")), _frame(2), {})
    assert _ids(panel) == ["a"]

    panel.objects_page.set_scope(ObjectScope.FILE)
    assert _ids(panel) == ["a", "b"]  # Includes the object that is not in the displayed frame.
    _search(panel, "class:car")
    assert _ids(panel) == ["b"]
    notes = [label.text() for label in panel.objects_page.findChildren(QLabel) if label.isVisibleTo(panel)]
    assert any("Attribute filters apply in Frame scope" in note for note in notes)
    _search(panel, "")
    panel.filter_model.set_enabled("show_cars", False)  # The type filter hides objects in the list as on the video.
    assert _ids(panel) == ["a"]


def test_going_back_from_an_object_restores_the_list_as_it_was_left(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    count = 60  # Object n is seen at frame count - 1 - n, so ordering by Seen reverses the id order.
    objects = [(f"o{n:02d}", "car" if n % 2 else "human", ((count - 1 - n,) * 2,)) for n in range(count)]
    panel = _panel(qtbot, render_catalog_selection, _history(*objects, frames=count))
    selected = _record(panel.objectSelected)
    panel.objects_page.set_scope(ObjectScope.FILE)
    _search(panel, "class:car")
    view = _view(panel)
    assert view is not None
    header = view.header()
    seen_column = QPoint(header.sectionViewportPosition(3) + 4, 4)
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=seen_column)
    listed = _ids(panel)
    assert listed[0] == "o59" and len(listed) == count // 2
    bar = view.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum() // 2)
    scrolled = bar.value()
    assert scrolled > 0

    _open(panel, listed[10])
    assert selected == [listed[10]] and _view(panel) is None
    panel.update_scene(_scene(), _frame(7), {})  # Video frames keep arriving behind the object page.
    _click(panel, "Back (Esc)")

    assert selected == [listed[10], None]
    assert _view(panel) is view and panel.objects_page.scope is ObjectScope.FILE
    assert _ids(panel) == listed
    assert view.verticalScrollBar().value() == scrolled
    assert view.rows_model.highlight() == ObjectPageKey(listed[10])
    edit = panel.objects_page.findChild(QLineEdit)
    assert edit is not None and edit.text() == "class:car"


def test_object_page_steps_follow_the_listed_order(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(_scene(_entity("a", conf=0.3), _entity("b", conf=0.9), _entity("c", conf=0.6)), _frame(0), {})
    assert _ids(panel) == ["b", "c", "a"]  # Strongest first: neither alphabetical nor the Scene's order.
    selected = _record(panel.objectSelected)

    _open(panel, "c")
    for step in ("Previous in list", "Next in list", "Next in list"):
        _click(panel, step)

    assert selected == ["c", "b", "c", "a"]
    next_button = _button(panel, "Next in list")
    assert next_button is not None and not next_button.isEnabled()


def test_object_page_steps_use_the_list_as_it_is_now(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(_scene(_entity("a", conf=0.9), _entity("b", conf=0.5)), _frame(0), {})
    selected = _record(panel.objectSelected)
    _open(panel, "a")

    panel.update_scene(_scene(_entity("a", conf=0.9), _entity("c", conf=0.5)), _frame(1), {})
    _click(panel, "Next in list")
    assert selected == ["a", "c"]  # "b" has left the frame.

    panel.update_scene(_scene(_entity("c", conf=0.5)), _frame(2), {})
    _click(panel, "Previous in list")  # The list no longer has "a" before it.
    previous_button = _button(panel, "Previous in list")
    assert selected == ["a", "c"] and previous_button is not None and not previous_button.isEnabled()


def test_scene_updates_behind_an_open_object_page_do_not_rebuild_the_hidden_list(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(_scene(_entity("a"), _entity("b")), _frame(0), {})
    _open(panel, "a")
    rebuilt: list[int] = []
    set_rows = RowTableModel.set_rows

    def counted(self: RowTableModel, *args: Any) -> None:
        rebuilt.append(1)
        set_rows(self, *args)

    monkeypatch.setattr(RowTableModel, "set_rows", counted)

    for index in range(1, 4):
        panel.update_scene(_scene(_entity("a"), _entity("b")), _frame(index), {})

    assert not rebuilt
    _click(panel, "Back (Esc)")
    assert _ids(panel) == ["a", "b"]


def test_unchanged_rows_are_not_sorted_again(qtbot: QtBot) -> None:
    sorts: list[str] = []

    def sort_key(row: object) -> str:
        sorts.append(str(row))
        return str(row)

    model = RowTableModel([Column("Name", CellKind.TEXT, str, sort_key)])
    model.sort(0)
    rows = ["b", "a", "c"]
    model.set_rows(rows, str)
    sorts.clear()

    model.set_rows(list(rows), str)
    assert not sorts
    model.set_rows(["a", "c"], str)
    assert sorts and [model.key_at(n) for n in range(model.rowCount())] == ["a", "c"]


def test_object_page_outside_the_frame_goes_to_where_the_object_was_and_will_be(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection, _history(("b", "car", ((5, 6), (8, 8)))))
    requested = _record(panel.frameRequested)
    panel.update_scene(_scene(), _frame(2), {})
    panel.show_object("b")
    assert _button(panel, "Go to last seen") is None  # Not seen before frame 2.
    _click(panel, "Next appearance")

    panel.update_scene(_scene(), _frame(7), {})
    _click(panel, "Go to last seen")
    _click(panel, "Next appearance")

    panel.update_scene(_scene(), _frame(9), {})
    assert _button(panel, "Next appearance") is None
    _click(panel, "Go to last seen")
    assert requested == [5, 6, 8, 8]


def test_hidden_panel_keeps_only_the_latest_update_and_opens_the_chosen_object_when_shown(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection, _history(("b", "car", ((5, 6),))), show=False)
    selected, requested = _record(panel.objectSelected), _record(panel.frameRequested)

    panel.update_scene(_scene(_entity("old")), _frame(5), {})
    panel.update_scene(_scene(_entity("latest")), _frame(7), {})
    panel.show_object("b")
    assert selected == []  # Nothing opens while the panel is hidden.
    panel.show()
    qtbot.waitExposed(panel)

    assert selected == ["b"]
    _click(panel, "Go to last seen")  # The page follows the latest frame, 7, where b is not.
    assert requested == [6]
    _click(panel, "Back (Esc)")
    assert _ids(panel) == ["latest"]


def test_history_replacement_updates_the_list_and_object_page_and_clears_the_out_of_date_notice(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection, _history(("a", "human", ((0, 3),)), ("b", "car", ((5, 6),))))
    requested = _record(panel.frameRequested)
    panel.update_scene(_scene(), _frame(7), {})
    panel.objects_page.set_scope(ObjectScope.FILE)
    notice = panel.findChild(QLabel, "historyOutOfDateNotice")
    assert notice is not None and not notice.isVisible()

    panel.mark_history_out_of_date("History is out of date: unreadable")
    assert notice.isVisible() and "unreadable" in notice.text()
    assert _ids(panel) == ["a", "b"]  # The previous history stays usable.

    _open(panel, "b")
    panel.set_scene_history(_history(("b", "car", ((2, 3),))))

    assert not notice.isVisible()
    _click(panel, "Go to last seen")
    assert requested == [3]  # The open page follows the replacement.
    _click(panel, "Back (Esc)")
    assert _ids(panel) == ["b"]


def test_file_scope_is_unavailable_when_the_decoder_cannot_filter_history(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    moving = FilterOption(id="moving", label="Moving", predicate=lambda e, _s: e.motion_state is MotionState.Moving)
    history = _history(("a", "human", ((0, 3),)))
    panel = _panel(qtbot, render_catalog_selection, history, filter_model=SessionFilter(FilterConfig((moving,))))
    file_button = next(b for b in panel.objects_page.findChildren(QToolButton) if b.text() == "File")

    panel.objects_page.set_scope(ObjectScope.FILE)

    assert panel.objects_page.scope is ObjectScope.FRAME
    assert not file_button.isVisible()


def test_event_page_opens_from_the_events_list_and_leads_to_an_involved_object_and_back(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    event = FrameEvent(_frame(3), "Merge", "Merge a → b", ("a", "b"))
    panel = _panel(qtbot, render_catalog_selection, _history(("a", "human", ((0, 5),)), events=(event,)))
    requested, selected = _record(panel.frameRequested), _record(panel.objectSelected)
    panel.update_scene(_scene(_entity("a")), _frame(1), {})
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None

    _click_row(events_view, EventPageKey(event))
    _click(panel, "Go to frame")
    assert requested == [3]

    (page,) = [v for v in panel.findChildren(RowTableView) if v.isVisible() and v is not events_view]
    _click_row(page, "a")  # Its object page is a step further from the events list.
    assert selected[-1] == "a"
    _click(panel, "Back (Esc)")
    assert _button(panel, "Go to frame") is not None  # The event page again, and the list is still beneath it.
    _click(panel, "Back (Esc)")
    assert events_view.isVisible() and events_view.rows_model.highlight() == EventPageKey(event)


def test_event_page_follows_its_event_through_a_history_replacement_and_closes_when_it_is_gone(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    def merge(frame: int) -> FrameEvent:
        return FrameEvent(_frame(frame), "Merge", "Merge a → b", ("a", "b"))

    delete = FrameEvent(_frame(6), "Delete", "Delete a", ("a",))
    panel = _panel(qtbot, render_catalog_selection, _history(events=(merge(3), delete)))
    requested = _record(panel.frameRequested)
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None
    _click_row(events_view, EventPageKey(merge(3)))

    panel.set_scene_history(_history(events=(merge(5), delete)))
    _click(panel, "Go to frame")
    _click(panel, "Next in list")  # The list it came from has the new order too.
    _click(panel, "Go to frame")
    assert requested == [5, 6]

    panel.set_scene_history(_history(events=(merge(5),)))
    assert events_view.isVisible() and _button(panel, "Go to frame") is None


def test_live_event_page_steps_to_events_that_arrive_while_it_is_open(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    def deleted(frame: int) -> Scene:
        scene = Scene(time_slice=TimeSlice(start=frame, end=frame))
        scene.add_event(Delete(scene.time_slice, EntityId(f"o{frame}")))
        return scene

    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(deleted(1), _frame(1), {})
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None
    _click_row(events_view, EventPageKey(FrameEvent.of(_frame(1), deleted(1).events[0])))
    next_button = _button(panel, "Next in list")
    assert next_button is not None and not next_button.isEnabled()

    panel.update_scene(deleted(2), _frame(2), {})
    assert next_button.isEnabled()
    next_button.click()

    (page,) = [p for p in panel.findChildren(EventPage) if p.isVisible()]
    assert page.frame_event.entity_ids == ("o2",)


def test_live_event_page_dims_an_object_when_the_frame_has_no_overlay(qtbot: QtBot) -> None:
    page = EventPage(FrameEvent(_frame(3), "Delete", "Delete a", ("a",)))
    qtbot.addWidget(page)
    page.show()
    table = page.findChild(RowTableView)
    assert table is not None

    page.update_scene(_scene(_entity("a")), 3)
    assert not table.rows_model.data(table.rows_model.index(0, 0), _DIMMED_ROLE)
    page.update_scene(None, 4)
    assert table.rows_model.data(table.rows_model.index(0, 0), _DIMMED_ROLE)


def test_hidden_object_page_collects_its_events_only_when_shown(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    lookups: list[str] = []

    def events_involving(self: SceneHistory, entity_id: str) -> tuple[()]:
        lookups.append(entity_id)
        return ()

    monkeypatch.setattr(SceneHistory, "events_involving", events_involving)
    page = ObjectPage("a", {})
    qtbot.addWidget(page)

    page.set_history(_history(("a", "human", ((0, 3),))))
    page.set_history(_history(("a", "human", ((0, 4),))))
    assert lookups == []

    page.show()
    assert lookups == ["a"]
