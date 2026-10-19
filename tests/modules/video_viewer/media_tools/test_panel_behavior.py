"""Tests for the media tools panel as a whole: width, overflow menu and throttled inspection."""

from __future__ import annotations

from typing import Any, cast

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QTabBar, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.data_sources.scene_history import FrameEvent
from ax_devil.modules.filtering.filter_config import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.object_inspection.fields_view import FieldsView
from ax_devil.modules.scene.model import (
    Delete,
    EntityId,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_player.ui.draggable import DraggablePanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.object_scope import ObjectScope
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView
from tests.helpers.media_tools_panel import (
    listed_ids,
    make_entity,
    make_frame,
    make_history,
    make_panel,
    make_scene,
    search_objects,
)


@pytest.mark.usefixtures("restore_app_appearance")
def test_overflow_submenu_fits_its_content_after_the_text_size_grows(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    apply_text_size(13)
    panel = make_panel(qtbot, render_catalog_selection)
    [filters] = [menu for menu in panel._tab_row.more_menu.findChildren(QMenu) if menu.title() == "Overlay filter"]
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
    event = FrameEvent(make_frame(3), "Merge", f"Merge {uuid} → b", (uuid, "b"))
    many = tuple((f"object-{n}", "car", ((0, 5),)) for n in range(9999))
    history = make_history((uuid, long_class, ((0, 5),)), *many, events=(event,))
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
        panel.update_scene(make_scene(make_entity(entity_id, kind)), make_frame(1), {})
        unchanged()
    for scope in (ObjectScope.FILE, ObjectScope.FRAME):
        panel.objects_page.set_scope(scope)
        unchanged()
    path = "candidates.human.attributes.confidence.detection.source.original_tracker_identifier"
    search_objects(panel, f"{path}:1")  # No object has the field: a note says so, as wide as the path.
    unchanged()
    note = panel.objects_page.findChild(ElidedLabel)
    assert note is not None and note.isVisible() and path in note.full_text()
    assert note.toolTip() == note.full_text()
    assert note.fontMetrics().horizontalAdvance(note.text()) <= note.width()  # Cut in the middle, not clipped.
    search_objects(panel, "")
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
    panel = make_panel(qtbot, render_catalog_selection)
    panel.set_playing(True)
    panel.update_scene(make_scene(make_entity("first")), make_frame(0), {})
    for frame in range(1, 4):
        panel.update_scene(make_scene(make_entity(str(frame))), make_frame(frame), {})
    assert listed_ids(panel) == ["first"]
    panel.set_playing(False)
    assert listed_ids(panel) == ["3"]
    for frame in (2, 3, 1):  # Backward and forward steps while paused have no refresh delay.
        panel.update_scene(make_scene(make_entity(str(frame))), make_frame(frame), {})
        assert listed_ids(panel) == [str(frame)]

    panel.set_playing(True)
    panel.update_scene(make_scene(make_entity("playing")), make_frame(4), {})
    panel.update_scene(make_scene(make_entity("searchable")), make_frame(5), {})
    search_objects(panel, "searchable")
    assert listed_ids(panel) == ["searchable"]
    search_objects(panel, "")
    now[0] = 1.0
    panel.update_scene(make_scene(make_entity("due")), make_frame(6), {})
    assert listed_ids(panel) == ["due"]

    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    panel.update_scene(make_scene(make_entity("new-tab")), make_frame(7), {})
    tabs.setCurrentIndex(0)
    assert listed_ids(panel) == ["new-tab"]
    panel.hide()
    panel.update_scene(make_scene(make_entity("hidden")), make_frame(8), {})
    panel.show()
    assert listed_ids(panel) == ["hidden"]
    panel.update_scene(make_scene(make_entity("tail")), make_frame(9), {})
    panel.update_scene(make_scene(make_entity("latest-tail")), make_frame(10), {})
    now[0] = 1.1
    qtbot.waitUntil(lambda: listed_ids(panel) == ["latest-tail"])  # A quiet source must not leave an old table forever.
    now[0] = 1.11
    panel.update_scene(make_scene(make_entity("discarded")), make_frame(11), {})
    assert listed_ids(panel) == ["latest-tail"]  # Arriving just after the idle flush still observes the playback limit.
    panel.clear()
    assert listed_ids(panel) == []


def test_skipped_visual_updates_keep_all_live_events_and_the_exact_paused_marker(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coalescing is a display policy: an event in an intermediate frame must remain inspectable."""
    monkeypatch.setattr("ax_devil.modules.video_viewer.media_tools.media_tools_panel.monotonic", lambda: 0.0)
    panel = make_panel(qtbot, render_catalog_selection)
    tabs = panel.findChild(QTabBar, "mediaToolsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(1)
    panel.set_playing(True)
    for frame in range(3):
        scene = make_scene()
        scene.add_event(Delete(timestamp=scene.time_slice, entity_id=EntityId(str(frame))))
        panel.update_scene(scene, make_frame(frame), {})
    view = panel.events_page.findChild(RowTableView)
    assert view is not None
    assert view.rows_model.rowCount() == 1
    panel.set_playing(False)
    model = view.rows_model
    events = [cast(EventPageKey, model.key_at(row)).event for row in range(model.rowCount())]
    assert [event.entity_ids for event in events] == [("0",), ("1",), ("2",)]
    assert cast(Any, view).position == (2, 3)
