"""Tests for the media tools panel: the Objects list, drilling into an object page, and following the video."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, cast

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMenu, QTabBar, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption, build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.object_inspection.fields_view import FieldsView
from ax_devil.modules.scene.inspection import EntitySummary, entity_summary
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
from ax_devil.modules.video_player.ui.draggable import DraggablePanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.event_page import EventPage
from ax_devil.modules.video_viewer.media_tools.events_page import LIVE_EVENT_LIMIT, EventsPage
from ax_devil.modules.video_viewer.media_tools.object_page import ObjectPage
from ax_devil.modules.video_viewer.media_tools.objects_page import ObjectScope
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey, ObjectPageKey, PageKey
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView


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
    buttons = [
        b for b in panel.findChildren(SmallButton) if (label in b.text() or label in b.toolTip()) and b.isVisible()
    ]
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


def test_file_scope_lists_the_whole_file(qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection) -> None:
    panel = _panel(qtbot, render_catalog_selection, _history(("a", "human", ((0, 3),)), ("b", "car", ((5, 6),))))
    panel.update_scene(_scene(_entity("a")), _frame(2), {})
    assert _ids(panel) == ["a"]

    panel.objects_page.set_scope(ObjectScope.FILE)
    assert _ids(panel) == ["a", "b"]  # Includes the object that is not in the displayed frame.
    _search(panel, "class:car")
    assert _ids(panel) == ["b"]
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


def test_the_selection_on_the_video_and_the_open_object_page_follow_each_other(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection)
    display = FrameDisplay()
    qtbot.addWidget(display)
    panel.link_selection(display)
    panel.update_scene(_scene(_entity("a", conf=0.3), _entity("b", conf=0.9)), _frame(0), {})
    shown = _record(panel.objectSelected)
    clicked = _record(display.objectSelected)

    display.viewport.selectionChanged.emit("a")  # A click on a on the video opens its page.
    assert shown == ["a"] and _view(panel) is None

    _click(panel, "Previous in list")  # Stepping to b in the panel selects b on the video; no echo comes back.
    assert shown == ["a", "b"] and display.viewport._selected_id == "b" and clicked == ["a"]

    display.viewport.selectionChanged.emit(None)  # Clearing the selection leaves the page open.
    assert shown == ["a", "b"] and _view(panel) is None

    _click(panel, "Back (Esc)")  # Going back from the object clears the selection on the video.
    assert display.viewport._selected_id is None


def test_opening_the_panel_does_not_bring_back_a_selection_cleared_on_the_video_meanwhile(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = _panel(qtbot, render_catalog_selection, show=False)
    display = FrameDisplay()
    qtbot.addWidget(display)
    panel.link_selection(display)
    shown = _record(panel.objectSelected)

    display.viewport._set_selected("a")  # The renderer's own path for a click on an object, then on empty video.
    display.viewport._set_selected(None)
    panel.show()
    qtbot.waitExposed(panel)

    assert shown == [] and display.viewport._selected_id is None


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


def test_hidden_objects_list_defers_query_work_until_shown(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_devil.modules.video_viewer.media_tools import objects_page

    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(_scene(_entity("a"), _entity("b")), _frame(0), {})
    _search(panel, "class:human")
    _open(panel, "a")
    formatted: list[str] = []

    def summary(entity: Entity) -> EntitySummary:
        formatted.append(str(entity.id))
        return entity_summary(entity)

    monkeypatch.setattr(objects_page, "entity_summary", summary)

    for index in range(1, 4):
        panel.update_scene(_scene(_entity("a"), _entity("b")), _frame(index), {})

    assert not formatted
    _click(panel, "Back (Esc)")
    assert _ids(panel) == ["a", "b"]
    assert formatted == ["a", "b"]

    formatted.clear()
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    panel.update_scene(_scene(_entity("old")), _frame(4), {})
    assert tabs.tabText(0) == "Objects 1"  # The tab label is visible even though its table is not.
    panel.hide()
    panel.update_scene(_scene(_entity("latest")), _frame(5), {})
    panel.show()  # The Events tab is still selected.
    assert not formatted
    tabs.setCurrentIndex(0)
    assert _ids(panel) == ["latest"]
    assert formatted == ["latest"]


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


def test_identical_events_are_stepped_through_one_at_a_time(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    twin = FrameEvent(_frame(3), "Delete", "Delete a", ("a",))
    panel = _panel(qtbot, render_catalog_selection, _history(events=(twin, twin)))
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None
    model = events_view.rows_model

    def marked() -> list[bool]:
        return [model.is_active(row) for row in range(model.rowCount())]

    _click_row(events_view, model.key_at(0))
    assert marked() == [True, False]
    _click(panel, "Next in list")
    assert marked() == [False, True]
    _click(panel, "Previous in list")
    assert marked() == [True, False]


def _deleting_scene(frame: int, *entity_ids: str) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=frame, end=frame))
    for entity_id in entity_ids:
        scene.add_event(Delete(scene.time_slice, EntityId(entity_id)))
    return scene


def _open_last_live_event(panel: MediaToolsPanel) -> SmallButton | None:
    """Open the newest event of the Events list and return its Next button."""
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None
    _click_row(events_view, events_view.rows_model.key_at(events_view.rows_model.rowCount() - 1))
    return _button(panel, "Next in list")


@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("held", [1, LIVE_EVENT_LIMIT])
def test_live_event_page_steps_to_events_that_arrive_while_it_is_open(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
    monkeypatch: pytest.MonkeyPatch,
    held: int,
    hidden: bool,
) -> None:
    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(_deleting_scene(1, *(f"o{n}" for n in range(held))), _frame(1), {})
    next_button = _open_last_live_event(panel)  # At the limit, the next event drops the oldest and keeps the count.
    assert next_button is not None and not next_button.isEnabled()
    events_table = panel.events_page.findChild(RowTableView)
    assert events_table is not None
    resets: list[None] = []
    events_table.rows_model.modelReset.connect(lambda: resets.append(None))

    lookups: list[None] = []
    neighbor = EventsPage.neighbor

    def counting_neighbor(page: EventsPage, key: PageKey, delta: int) -> PageKey | None:
        lookups.append(None)
        return neighbor(page, key, delta)

    monkeypatch.setattr(EventsPage, "neighbor", counting_neighbor)
    if hidden:
        panel.hide()

    panel.update_scene(_deleting_scene(2, "new"), _frame(2), {})
    if hidden:
        assert lookups == []  # A collapsed panel does no list work per event.
        panel.show()
    assert next_button.isEnabled()
    assert resets == []  # The hidden list is not rebuilt per event.
    next_button.click()

    (page,) = [p for p in panel.findChildren(EventPage) if p.isVisible()]
    assert page.frame_event.entity_ids == ("new",)


def test_event_page_lists_the_equal_event_reported_twice_among_its_same_moment(qtbot: QtBot) -> None:
    twice = FrameEvent(_frame(3), "Delete", "Delete a", ("a",))
    other = FrameEvent(_frame(3), "Merge", "Merge a, b", ("a", "b"))
    page = EventPage(EventPageKey(twice, 0))
    qtbot.addWidget(page)
    page.set_history(_history(events=(twice, other, twice)))
    page.show()
    moment = next(view for view in page.findChildren(RowTableView) if view.rows_model.columns[0].title == "Kind")

    assert moment.rows_model.rowCount() == 2
    assert {moment.rows_model.row_of(EventPageKey(twice, 1)), moment.rows_model.row_of(EventPageKey(other))} == {0, 1}


def test_live_event_page_sorts_the_latest_classes_and_dims_objects_without_an_overlay(qtbot: QtBot) -> None:
    page = EventPage(EventPageKey(FrameEvent(_frame(3), "Merge", "Merge a, b", ("a", "b"))))
    qtbot.addWidget(page)
    page.show()
    table = page.findChild(RowTableView)
    assert table is not None

    page.update_scene(_scene(_entity("a", "human"), _entity("b", "car")), 3)
    assert not table.rows_model.is_dimmed(0)
    page.update_scene(_scene(_entity("a", "car"), _entity("b", "human")), 4, defer=True)
    header = table.header()
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(header.sectionViewportPosition(1) + 4, 4))
    assert [table.rows_model.key_at(row) for row in range(2)] == ["a", "b"]
    page.update_scene(None, 4)
    assert table.rows_model.is_dimmed(0)


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


@pytest.mark.usefixtures("restore_app_appearance")
def test_overflow_submenu_fits_its_content_after_the_text_size_grows(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    apply_text_size(13)
    panel = _panel(qtbot, render_catalog_selection)
    [filters] = [menu for menu in panel._more_menu.findChildren(QMenu) if menu.title() == "Overlay filter"]
    filters.popup(panel.mapToGlobal(QPoint(0, 0)))
    filters.hide()

    apply_text_size(21)
    filters.popup(panel.mapToGlobal(QPoint(0, 0)))

    content = panel.filter_widget.parentWidget()
    assert content is not None
    assert filters.width() >= content.sizeHint().width()


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("text_size", [13, 15, 21], ids=["small", "medium", "larger"])
def test_panel_keeps_its_width_whatever_it_shows(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, text_size: int
) -> None:
    """Pages, data, folds and scroll bars never move the panel's minimum or its width in the video view."""
    apply_text_size(text_size)
    uuid = "3f2b8c1e-9a4d-4e7b-8c55-1d2e3f4a5b6c"
    long_class = "a-very-long-class-name-for-one-object"
    event = FrameEvent(_frame(3), "Merge", f"Merge {uuid} → b", (uuid, "b"))
    many = tuple((f"object-{n}", "car", ((0, 5),)) for n in range(9999))
    history = _history((uuid, long_class, ((0, 5),)), *many, events=(event,))
    filters = SessionFilter(build_default_filter_config())
    panel = MediaToolsPanel(render_catalog_selection, filter_model=filters, scene_history=history)
    window = QWidget()
    qtbot.addWidget(window)
    QHBoxLayout(window).addWidget(display := FrameDisplay())
    display.enable_side_panel()
    display.set_side_panel_widget(panel)
    window.resize(1400, 700)
    window.show()
    display.set_side_panel_open(True)
    side_panel = display.findChild(DraggablePanel)
    assert side_panel is not None
    qtbot.waitUntil(lambda: side_panel.width() == side_panel.expanded_width)
    minimum, width = panel.minimumSizeHint().width(), side_panel.width()

    def unchanged() -> None:
        qtbot.wait(20)
        assert panel.minimumSizeHint().width() == minimum
        assert panel._tab_row.minimumSizeHint().width() <= minimum
        assert panel._stack.minimumSizeHint().width() <= minimum  # The header, and the widest page held.
        assert side_panel.width() == width

    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    unchanged()
    for kind, entity_id in (("car", "a"), (long_class, "b"), ("human", uuid)):
        panel.update_scene(_scene(_entity(entity_id, kind)), _frame(1), {})
        unchanged()
    for scope in (ObjectScope.FILE, ObjectScope.FRAME):
        panel.objects_page.set_scope(scope)
        unchanged()
    path = "candidates.human.attributes.confidence.detection.source.original_tracker_identifier"
    _search(panel, f"{path}:1")  # No object has the field: a note says so, as wide as the path.
    unchanged()
    note = panel.objects_page.findChild(ElidedLabel)
    assert note is not None and note.isVisible() and path in note.full_text()
    assert note.toolTip() == note.full_text()
    assert note.fontMetrics().horizontalAdvance(note.text()) <= note.width()  # Cut in the middle, not clipped.
    _search(panel, "")
    long_path = "/home/someone/projects/a-long-folder-name/another-long-folder-name/scene_history_cache_file.json"
    panel.mark_history_out_of_date(f"History is out of date: {long_path}")
    unchanged()
    notice = panel.findChild(QLabel, "historyOutOfDateNotice")
    assert notice is not None
    words = notice.text().replace("\u200b", " ").split()  # A label breaks lines at spaces and these invisible marks.
    assert max(notice.fontMetrics().horizontalAdvance(word) for word in words) <= notice.width()
    tabs.setCurrentIndex(1)
    unchanged()
    tabs.setCurrentIndex(0)
    panel.show_object(uuid)
    unchanged()
    fields = panel.findChild(FieldsView)
    assert fields is not None
    fields.collapseAll()
    unchanged()
    fields.expandAll()
    unchanged()
    panel._open_event(event)  # An event page whose back button is named after the object's id.
    unchanged()
    window.resize(1400, 160)  # Short enough for every list and page to need its scroll bar.
    unchanged()
    window.resize(1400, 700)
    panel._stack.show_root(panel._tab_roots[0])
    unchanged()


def test_playback_coalesces_inspection_but_pause_navigation_and_showing_catch_up(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fast table may skip frames, but inspection actions must see the currently displayed snapshot."""
    now = [0.0]
    monkeypatch.setattr("ax_devil.modules.video_viewer.media_tools.media_tools_panel.monotonic", lambda: now[0])
    panel = _panel(qtbot, render_catalog_selection)
    panel.set_playing(True)
    panel.update_scene(_scene(_entity("first")), _frame(0), {})
    for frame in range(1, 4):
        panel.update_scene(_scene(_entity(str(frame))), _frame(frame), {})
    assert _ids(panel) == ["first"]
    panel.set_playing(False)
    assert _ids(panel) == ["3"]
    for frame in (2, 3, 1):  # Backward and forward steps while paused have no refresh delay.
        panel.update_scene(_scene(_entity(str(frame))), _frame(frame), {})
        assert _ids(panel) == [str(frame)]

    panel.set_playing(True)
    panel.update_scene(_scene(_entity("playing")), _frame(4), {})
    panel.update_scene(_scene(_entity("searchable")), _frame(5), {})
    _search(panel, "searchable")
    assert _ids(panel) == ["searchable"]
    _search(panel, "")
    now[0] = 1.0
    panel.update_scene(_scene(_entity("due")), _frame(6), {})
    assert _ids(panel) == ["due"]

    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    panel.update_scene(_scene(_entity("new-tab")), _frame(7), {})
    tabs.setCurrentIndex(0)
    assert _ids(panel) == ["new-tab"]
    panel.hide()
    panel.update_scene(_scene(_entity("hidden")), _frame(8), {})
    panel.show()
    assert _ids(panel) == ["hidden"]
    panel.update_scene(_scene(_entity("tail")), _frame(9), {})
    panel.update_scene(_scene(_entity("latest-tail")), _frame(10), {})
    now[0] = 1.1
    qtbot.waitUntil(lambda: _ids(panel) == ["latest-tail"])  # A quiet source must not leave an old table forever.
    now[0] = 1.11
    panel.update_scene(_scene(_entity("discarded")), _frame(11), {})
    assert _ids(panel) == ["latest-tail"]  # Arriving just after the idle flush still observes the playback limit.
    panel.clear()
    assert _ids(panel) == []


def test_skipped_visual_updates_keep_all_live_events_and_the_exact_paused_marker(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coalescing is a display policy: an event in an intermediate frame must remain inspectable."""
    monkeypatch.setattr("ax_devil.modules.video_viewer.media_tools.media_tools_panel.monotonic", lambda: 0.0)
    panel = _panel(qtbot, render_catalog_selection)
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    panel.set_playing(True)
    for frame in range(3):
        scene = _scene()
        scene.add_event(Delete(timestamp=scene.time_slice, entity_id=EntityId(str(frame))))
        panel.update_scene(scene, _frame(frame), {})
    view = panel.events_page.findChild(RowTableView)
    assert view is not None
    assert view.rows_model.rowCount() == 1
    panel.set_playing(False)
    model = view.rows_model
    events = [cast(EventPageKey, model.key_at(row)).event for row in range(model.rowCount())]
    assert [event.entity_ids for event in events] == [("0",), ("1",), ("2",)]
    assert cast(Any, view).position == (2, 3)


def test_visible_rows_are_formatted_lazily_and_the_keyboard_cursor_follows_the_object(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long list does not format offscreen rows; sorting still keeps navigation attached to object identity."""
    from ax_devil.modules.video_viewer.media_tools import objects_page

    formatted: set[str] = set()
    summarize = entity_summary

    def summary(entity: Entity) -> EntitySummary:
        formatted.add(str(entity.id))
        return summarize(entity)

    monkeypatch.setattr(objects_page, "entity_summary", summary)
    panel = _panel(qtbot, render_catalog_selection)
    panel.update_scene(_scene(*(_entity(str(index), conf=index / 200) for index in range(200))), _frame(0), {})
    view = _view(panel)
    assert view is not None
    view.grab()  # Exercise painting, including the cells that demand display summaries.
    assert 0 < len(formatted) < 200
    model = view.rows_model
    view.setCurrentIndex(model.index(model.row_of(ObjectPageKey("199")), 0))
    view.setFocus()
    panel.update_scene(_scene(*(_entity(str(index), conf=1 - index / 200) for index in range(200))), _frame(1), {})
    assert model.key_at(view.currentIndex().row()) == ObjectPageKey("199")
    selected = _record(panel.objectSelected)
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert selected == ["199"]
