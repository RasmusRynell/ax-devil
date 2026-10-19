"""Tests for the media tools panel's event pages and the live events they follow."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabBar
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.data_sources.scene_history import FrameEvent
from ax_devil.modules.scene.model import (
    Delete,
    EntityId,
    Scene,
    TimeSlice,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.event_page import EventPage
from ax_devil.modules.video_viewer.media_tools.events_page import LIVE_EVENT_LIMIT, EventsPage
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey, PageKey
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView
from tests.helpers.media_tools_panel import (
    click_button,
    click_row,
    find_button,
    make_entity,
    make_frame,
    make_history,
    make_panel,
    make_scene,
    record_signal,
)


def test_event_page_opens_from_the_events_list_and_leads_to_an_involved_object_and_back(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    event = FrameEvent(make_frame(3), "Merge", "Merge a → b", ("a", "b"))
    panel = make_panel(qtbot, render_catalog_selection, make_history(("a", "human", ((0, 5),)), events=(event,)))
    requested, selected = record_signal(panel.frameRequested), record_signal(panel.objectSelected)
    panel.update_scene(make_scene(make_entity("a")), make_frame(1), {})
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None

    click_row(events_view, EventPageKey(event))
    click_button(panel, "Go to frame")
    assert requested == [3]

    (page,) = [v for v in panel.findChildren(RowTableView) if v.isVisible() and v is not events_view]
    click_row(page, "a")  # Its object page is a step further from the events list.
    assert selected[-1] == "a"
    click_button(panel, "Back (Esc)")
    assert find_button(panel, "Go to frame") is not None  # The event page again, and the list is still beneath it.
    click_button(panel, "Back (Esc)")
    assert events_view.isVisible() and events_view.rows_model.highlight() == EventPageKey(event)


def test_event_page_follows_its_event_through_a_history_replacement_and_closes_when_it_is_gone(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    def merge(frame: int) -> FrameEvent:
        return FrameEvent(make_frame(frame), "Merge", "Merge a → b", ("a", "b"))

    delete = FrameEvent(make_frame(6), "Delete", "Delete a", ("a",))
    panel = make_panel(qtbot, render_catalog_selection, make_history(events=(merge(3), delete)))
    requested = record_signal(panel.frameRequested)
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None
    click_row(events_view, EventPageKey(merge(3)))

    panel.set_scene_history(make_history(events=(merge(5), delete)))
    click_button(panel, "Go to frame")
    click_button(panel, "Next in list")  # The list it came from has the new order too.
    click_button(panel, "Go to frame")
    assert requested == [5, 6]

    panel.set_scene_history(make_history(events=(merge(5),)))
    assert events_view.isVisible() and find_button(panel, "Go to frame") is None


def test_identical_events_are_stepped_through_one_at_a_time(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    twin = FrameEvent(make_frame(3), "Delete", "Delete a", ("a",))
    panel = make_panel(qtbot, render_catalog_selection, make_history(events=(twin, twin)))
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    events_view = panel.events_page.findChild(RowTableView)
    assert events_view is not None
    model = events_view.rows_model

    def marked() -> list[bool]:
        return [model.is_active(row) for row in range(model.rowCount())]

    click_row(events_view, model.key_at(0))
    assert marked() == [True, False]
    click_button(panel, "Next in list")
    assert marked() == [False, True]
    click_button(panel, "Previous in list")
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
    click_row(events_view, events_view.rows_model.key_at(events_view.rows_model.rowCount() - 1))
    return find_button(panel, "Next in list")


@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("held", [1, LIVE_EVENT_LIMIT])
def test_live_event_page_steps_to_events_that_arrive_while_it_is_open(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
    monkeypatch: pytest.MonkeyPatch,
    held: int,
    hidden: bool,
) -> None:
    panel = make_panel(qtbot, render_catalog_selection)
    panel.update_scene(_deleting_scene(1, *(f"o{n}" for n in range(held))), make_frame(1), {})
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

    panel.update_scene(_deleting_scene(2, "new"), make_frame(2), {})
    if hidden:
        assert lookups == []  # A collapsed panel does no list work per event.
        panel.show()
    assert next_button.isEnabled()
    assert resets == []  # The hidden list is not rebuilt per event.
    next_button.click()

    (page,) = [p for p in panel.findChildren(EventPage) if p.isVisible()]
    assert page.frame_event.entity_ids == ("new",)


def test_event_page_lists_the_equal_event_reported_twice_among_its_same_moment(qtbot: QtBot) -> None:
    twice = FrameEvent(make_frame(3), "Delete", "Delete a", ("a",))
    other = FrameEvent(make_frame(3), "Merge", "Merge a, b", ("a", "b"))
    page = EventPage(EventPageKey(twice, 0))
    qtbot.addWidget(page)
    page.set_history(make_history(events=(twice, other, twice)))
    page.show()
    moment = next(view for view in page.findChildren(RowTableView) if view.rows_model.columns[0].title == "Kind")

    assert moment.rows_model.rowCount() == 2
    assert {moment.rows_model.row_of(EventPageKey(twice, 1)), moment.rows_model.row_of(EventPageKey(other))} == {0, 1}


def test_live_event_page_sorts_the_latest_classes_and_dims_objects_without_an_overlay(qtbot: QtBot) -> None:
    page = EventPage(EventPageKey(FrameEvent(make_frame(3), "Merge", "Merge a, b", ("a", "b"))))
    qtbot.addWidget(page)
    page.show()
    table = page.findChild(RowTableView)
    assert table is not None

    page.update_scene(make_scene(make_entity("a", "human"), make_entity("b", "car")), 3)
    assert not table.rows_model.is_dimmed(0)
    page.update_scene(make_scene(make_entity("a", "car"), make_entity("b", "human")), 4, defer=True)
    header = table.header()
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(header.sectionViewportPosition(1) + 4, 4))
    assert [table.rows_model.key_at(row) for row in range(2)] == ["a", "b"]
    page.update_scene(None, 4)
    assert table.rows_model.is_dimmed(0)
