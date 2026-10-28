"""Tests for the media tools panel's Objects list, the object pages it opens, and following the video."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QTabBar, QToolButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.data_sources.scene_history import SceneHistory
from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.object_inspection.pinned_fields import PinnedFields
from ax_devil.modules.scene.inspection import EntitySummary, entity_summary
from ax_devil.modules.scene.model import (
    Entity,
    MotionState,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.media_tools.object_page import ObjectPage
from ax_devil.modules.video_viewer.media_tools.object_scope import ObjectScope
from ax_devil.modules.video_viewer.media_tools.page_stack import ObjectPageKey
from tests.helpers.media_tools_panel import (
    click_button,
    click_object,
    find_button,
    listed_ids,
    make_entity,
    make_frame,
    make_history,
    make_panel,
    make_scene,
    objects_view,
    record_signal,
    search_objects,
)


def test_rows_follow_the_displayed_frame_and_the_query(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection)
    panel.update_scene(make_scene(make_entity("untracked:0:0"), make_entity("untracked:0:1")), make_frame(0), {})
    panel.update_scene(
        make_scene(make_entity("untracked:1:0", score=0.2), make_entity("untracked:1:1", "car")), make_frame(1), {}
    )
    assert sorted(listed_ids(panel)) == ["untracked:1:0", "untracked:1:1"]

    search_objects(panel, "class:car")
    assert listed_ids(panel) == ["untracked:1:1"]
    panel.update_scene(make_scene(make_entity("untracked:2:0", "car"), make_entity("untracked:2:1")), make_frame(2), {})
    assert listed_ids(panel) == ["untracked:2:0"]  # The query stays while frames change.

    search_objects(panel, "score>0.5")
    assert sorted(listed_ids(panel)) == ["untracked:2:0", "untracked:2:1"]  # Any attribute is searchable in this frame.


def test_score_and_confidence_are_separate_columns_and_query_terms(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection)
    panel.update_scene(
        make_scene(
            make_entity("scored", score=0.87),
            make_entity("sure", score=None, confidence=0.87),
            make_entity("neither", score=None),
        ),
        make_frame(0),
        {},
    )
    view = objects_view(panel)
    assert view is not None
    model = view.rows_model
    titles = [column.title for column in model.columns]

    def cell(title: str, row: int) -> str:
        return "".join(model.column_texts(titles.index(title), range(row, row + 1)))

    cells = {key: (cell("Score", n), cell("Conf", n)) for n, key in enumerate(listed_ids(panel))}
    assert cells == {"scored": ("0.87", ""), "sure": ("", "0.87"), "neither": ("", "")}

    search_objects(panel, "confidence>0.8")  # Compares the fraction although the list shows a percentage.
    assert listed_ids(panel) == ["sure"]
    search_objects(panel, "score>0.8")
    assert listed_ids(panel) == ["scored"]


def test_filter_changes_reach_the_listed_frame_while_paused(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection)
    full, cars = make_scene(make_entity("a"), make_entity("b", "car")), make_scene(make_entity("b", "car"))
    panel.update_scene(full, make_frame(0), {}, lambda: full if panel.filter_model.is_enabled("show_humans") else cars)

    panel.filter_model.set_enabled("show_humans", False)

    assert listed_ids(panel) == ["b"]


def test_file_scope_lists_the_whole_file(qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection) -> None:
    panel = make_panel(
        qtbot, render_catalog_selection, make_history(("a", "human", ((0, 3),)), ("b", "car", ((5, 6),)))
    )
    panel.update_scene(make_scene(make_entity("a")), make_frame(2), {})
    assert listed_ids(panel) == ["a"]

    panel.objects_page.set_scope(ObjectScope.FILE)
    assert listed_ids(panel) == ["a", "b"]  # Includes the object that is not in the displayed frame.
    search_objects(panel, "class:car")
    assert listed_ids(panel) == ["b"]
    note = panel.objects_page.findChild(ElidedLabel)
    assert note is not None and not note.isVisibleTo(panel)
    search_objects(panel, "speed:1")  # File scope cannot filter an attribute, so it says what it can filter.
    assert listed_ids(panel) == [] and note.isVisibleTo(panel) and "File scope" in note.full_text()
    search_objects(panel, "")
    panel.filter_model.set_enabled("show_cars", False)  # The type filter hides objects in the list as on the video.
    assert listed_ids(panel) == ["a"]


def test_going_back_from_an_object_restores_the_list_as_it_was_left(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    count = 60  # Object n is seen at frame count - 1 - n, so ordering by Seen reverses the id order.
    objects = [(f"o{n:02d}", "car" if n % 2 else "human", ((count - 1 - n,) * 2,)) for n in range(count)]
    panel = make_panel(qtbot, render_catalog_selection, make_history(*objects, frames=count))
    selected = record_signal(panel.objectSelected)
    panel.objects_page.set_scope(ObjectScope.FILE)
    search_objects(panel, "class:car")
    view = objects_view(panel)
    assert view is not None
    header = view.header()
    seen_column = QPoint(header.sectionViewportPosition(3) + 4, 4)
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=seen_column)
    listed = listed_ids(panel)
    assert listed[0] == "o59" and len(listed) == count // 2
    bar = view.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum() // 2)
    scrolled = bar.value()
    assert scrolled > 0

    click_object(panel, listed[10])
    assert selected == [listed[10]] and objects_view(panel) is None
    panel.update_scene(make_scene(), make_frame(7), {})  # Video frames keep arriving behind the object page.
    click_button(panel, "Back (Esc)")

    assert selected == [listed[10], None]
    assert objects_view(panel) is view and panel.objects_page.scope is ObjectScope.FILE
    assert listed_ids(panel) == listed
    assert view.verticalScrollBar().value() == scrolled
    assert view.rows_model.highlight() == ObjectPageKey(listed[10])
    edit = panel.objects_page.findChild(QLineEdit)
    assert edit is not None and edit.text() == "class:car"


def test_object_page_steps_follow_the_listed_order(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection)
    panel.update_scene(
        make_scene(make_entity("a", score=0.3), make_entity("b", score=0.9), make_entity("c", score=0.6)),
        make_frame(0),
        {},
    )
    assert listed_ids(panel) == ["b", "c", "a"]  # Strongest first: neither alphabetical nor the Scene's order.
    selected = record_signal(panel.objectSelected)

    click_object(panel, "c")
    for step in ("Previous in list", "Next in list", "Next in list"):
        click_button(panel, step)

    assert selected == ["c", "b", "c", "a"]
    next_button = find_button(panel, "Next in list")
    assert next_button is not None and not next_button.isEnabled()


def test_the_selection_on_the_video_and_the_open_object_page_follow_each_other(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection)
    display = FrameDisplay()
    qtbot.addWidget(display)
    panel.link_selection(display)
    panel.update_scene(make_scene(make_entity("a", score=0.3), make_entity("b", score=0.9)), make_frame(0), {})
    shown = record_signal(panel.objectSelected)
    clicked = record_signal(display.objectSelected)

    display.viewport.selectionChanged.emit("a")  # A click on a on the video opens its page.
    assert shown == ["a"] and objects_view(panel) is None

    click_button(panel, "Previous in list")  # Stepping to b in the panel selects b on the video; no echo comes back.
    assert shown == ["a", "b"] and display.viewport._selected_id == "b" and clicked == ["a"]

    display.viewport.selectionChanged.emit(None)  # Clearing the selection leaves the page open.
    assert shown == ["a", "b"] and objects_view(panel) is None

    click_button(panel, "Back (Esc)")  # Going back from the object clears the selection on the video.
    assert display.viewport._selected_id is None


def test_opening_the_panel_does_not_bring_back_a_selection_cleared_on_the_video_meanwhile(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection, show=False)
    display = FrameDisplay()
    qtbot.addWidget(display)
    panel.link_selection(display)
    shown = record_signal(panel.objectSelected)

    display.viewport._set_selected("a")  # The renderer's own path for a click on an object, then on empty video.
    display.viewport._set_selected(None)
    panel.show()
    qtbot.waitExposed(panel)

    assert shown == [] and display.viewport._selected_id is None


def test_object_page_steps_use_the_list_as_it_is_now(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection)
    panel.update_scene(make_scene(make_entity("a", score=0.9), make_entity("b", score=0.5)), make_frame(0), {})
    selected = record_signal(panel.objectSelected)
    click_object(panel, "a")

    panel.update_scene(make_scene(make_entity("a", score=0.9), make_entity("c", score=0.5)), make_frame(1), {})
    click_button(panel, "Next in list")
    assert selected == ["a", "c"]  # "b" has left the frame.

    panel.update_scene(make_scene(make_entity("c", score=0.5)), make_frame(2), {})
    click_button(panel, "Previous in list")  # The list no longer has "a" before it.
    previous_button = find_button(panel, "Previous in list")
    assert selected == ["a", "c"] and previous_button is not None and not previous_button.isEnabled()


def test_hidden_objects_list_defers_query_work_until_shown(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_devil.modules.video_viewer.media_tools import object_list_rows

    panel = make_panel(qtbot, render_catalog_selection)
    panel.update_scene(make_scene(make_entity("a"), make_entity("b")), make_frame(0), {})
    search_objects(panel, "class:human")
    click_object(panel, "a")
    formatted: list[str] = []

    def summary(entity: Entity) -> EntitySummary:
        formatted.append(str(entity.id))
        return entity_summary(entity)

    monkeypatch.setattr(object_list_rows, "entity_summary", summary)

    for index in range(1, 4):
        panel.update_scene(make_scene(make_entity("a"), make_entity("b")), make_frame(index), {})

    assert not formatted
    click_button(panel, "Back (Esc)")
    assert listed_ids(panel) == ["a", "b"]
    assert formatted == ["a", "b"]

    formatted.clear()
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    panel.update_scene(make_scene(make_entity("old")), make_frame(4), {})
    assert tabs.tabText(0) == "Objects 1"  # The tab label is visible even though its table is not.
    panel.hide()
    panel.update_scene(make_scene(make_entity("latest")), make_frame(5), {})
    panel.show()  # The Events tab is still selected.
    assert not formatted
    tabs.setCurrentIndex(0)
    assert listed_ids(panel) == ["latest"]
    assert formatted == ["latest"]


def test_object_page_outside_the_frame_goes_to_where_the_object_was_and_will_be(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection, make_history(("b", "car", ((5, 6), (8, 8)))))
    requested = record_signal(panel.frameRequested)
    panel.update_scene(make_scene(), make_frame(2), {})
    panel.show_object("b")
    assert find_button(panel, "Go to last seen") is None  # Not seen before frame 2.
    click_button(panel, "Next appearance")

    panel.update_scene(make_scene(), make_frame(7), {})
    click_button(panel, "Go to last seen")
    click_button(panel, "Next appearance")

    panel.update_scene(make_scene(), make_frame(9), {})
    assert find_button(panel, "Next appearance") is None
    click_button(panel, "Go to last seen")
    assert requested == [5, 6, 8, 8]


def test_hidden_panel_keeps_only_the_latest_update_and_opens_the_chosen_object_when_shown(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(qtbot, render_catalog_selection, make_history(("b", "car", ((5, 6),))), show=False)
    selected, requested = record_signal(panel.objectSelected), record_signal(panel.frameRequested)

    panel.update_scene(make_scene(make_entity("old")), make_frame(5), {})
    panel.update_scene(make_scene(make_entity("latest")), make_frame(7), {})
    panel.show_object("b")
    assert selected == []  # Nothing opens while the panel is hidden.
    panel.show()
    qtbot.waitExposed(panel)

    assert selected == ["b"]
    click_button(panel, "Go to last seen")  # The page follows the latest frame, 7, where b is not.
    assert requested == [6]
    click_button(panel, "Back (Esc)")
    assert listed_ids(panel) == ["latest"]


def test_history_replacement_updates_the_list_and_object_page_and_clears_the_out_of_date_notice(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = make_panel(
        qtbot, render_catalog_selection, make_history(("a", "human", ((0, 3),)), ("b", "car", ((5, 6),)))
    )
    requested = record_signal(panel.frameRequested)
    panel.update_scene(make_scene(), make_frame(7), {})
    panel.objects_page.set_scope(ObjectScope.FILE)
    notice = panel.findChild(QLabel, "historyOutOfDateNotice")
    assert notice is not None and not notice.isVisible()

    panel.mark_history_out_of_date("History is out of date: unreadable")
    assert notice.isVisible() and "unreadable" in notice.text()
    assert listed_ids(panel) == ["a", "b"]  # The previous history stays usable.

    click_object(panel, "b")
    panel.set_scene_history(make_history(("b", "car", ((2, 3),))))

    assert not notice.isVisible()
    click_button(panel, "Go to last seen")
    assert requested == [3]  # The open page follows the replacement.
    click_button(panel, "Back (Esc)")
    assert listed_ids(panel) == ["b"]


def test_file_scope_is_unavailable_when_the_decoder_cannot_filter_history(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    moving = FilterOption(id="moving", label="Moving", predicate=lambda e, _s: e.motion_state is MotionState.Moving)
    history = make_history(("a", "human", ((0, 3),)))
    panel = make_panel(qtbot, render_catalog_selection, history, filter_model=SessionFilter(FilterConfig((moving,))))
    file_button = next(b for b in panel.objects_page.findChildren(QToolButton) if b.text() == "File")

    panel.objects_page.set_scope(ObjectScope.FILE)

    assert panel.objects_page.scope is ObjectScope.FRAME
    assert not file_button.isVisible()


def test_hidden_object_page_collects_its_events_only_when_shown(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    lookups: list[str] = []

    def events_involving(self: SceneHistory, entity_id: str) -> tuple[()]:
        lookups.append(entity_id)
        return ()

    monkeypatch.setattr(SceneHistory, "events_involving", events_involving)
    page = ObjectPage("a", {}, PinnedFields())
    qtbot.addWidget(page)

    page.set_history(make_history(("a", "human", ((0, 3),))))
    page.set_history(make_history(("a", "human", ((0, 4),))))
    assert lookups == []

    page.show()
    assert lookups == ["a"]


def test_visible_rows_are_formatted_lazily_and_the_keyboard_cursor_follows_the_object(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long list does not format offscreen rows; sorting still keeps navigation attached to object identity."""
    from ax_devil.modules.video_viewer.media_tools import object_list_rows

    formatted: set[str] = set()
    summarize = entity_summary

    def summary(entity: Entity) -> EntitySummary:
        formatted.add(str(entity.id))
        return summarize(entity)

    monkeypatch.setattr(object_list_rows, "entity_summary", summary)
    panel = make_panel(qtbot, render_catalog_selection)
    panel.update_scene(
        make_scene(*(make_entity(str(index), score=index / 200) for index in range(200))), make_frame(0), {}
    )
    view = objects_view(panel)
    assert view is not None
    view.grab()  # Exercise painting, including the cells that demand display summaries.
    assert 0 < len(formatted) < 200
    model = view.rows_model
    view.setCurrentIndex(model.index(model.row_of(ObjectPageKey("199")), 0))
    view.setFocus()
    panel.update_scene(
        make_scene(*(make_entity(str(index), score=1 - index / 200) for index in range(200))), make_frame(1), {}
    )
    assert model.key_at(view.currentIndex().row()) == ObjectPageKey("199")
    selected = record_signal(panel.objectSelected)
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert selected == ["199"]
