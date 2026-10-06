"""Tests for whole-file objects in the entity list, the object history pane and their panel wiring."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QTabWidget, QToolButton
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.filtering.filter_config import (
    FilterConfig,
    FilterOption,
    FilterState,
    build_default_filter_config,
)
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    MotionState,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import EntityFilterWidget, MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.entity_list_widget import EntityListWidget, EntityScope
from ax_devil.modules.video_viewer.media_tools.object_history_widget import (
    ObjectCard,
    ObjectHistoryPane,
    PresenceStrip,
)


def _frame(index: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=index, timestamp_monotime_us=index * 1_000_000.0)


def _entity(entity_id: str, object_type: str = "human") -> Entity:
    observation = Observation(
        geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2),
        classification=[Classification(object_type, Score(0.9))],
        frame_number=0,
    )
    return Entity(id=EntityId(entity_id), observations=[observation])


_FRAME_TIMES = tuple(index * 1_000_000 for index in range(10))
_RENAME = FrameEvent(_frame(4), "Rename", "Rename a → b", ("a", "b"))
_DELETE = FrameEvent(_frame(9), "Delete", "Delete b", ("b",))


def _history() -> SceneHistory:
    return SceneHistory(
        events=(_RENAME, _DELETE),
        objects=(
            ObjectHistory("a", ("human",), ((0, 3),), _FRAME_TIMES),
            ObjectHistory("b", ("car",), ((5, 6), (8, 8)), _FRAME_TIMES),
        ),
        frame_count=10,
    )


def _scene(*entity_ids: str) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity_id in entity_ids:
        scene.add_entity(_entity(entity_id))
    return scene


def _file_list(qtbot: QtBot) -> tuple[EntityListWidget, EntityFilterWidget]:
    filter_widget = EntityFilterWidget(filter_config=build_default_filter_config())
    qtbot.addWidget(filter_widget)
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.set_history(_history(), filter_widget)
    widget.show()
    return widget, filter_widget


def _rows(widget: EntityListWidget) -> list[tuple[str, bool]]:
    model = widget._model
    items = [model.item_at(row) for row in range(model.rowCount())]
    return [(item.entity_id, item.visible) for item in items if item is not None]


def test_file_scope_lists_every_object_and_highlights_those_on_the_displayed_frame(qtbot: QtBot) -> None:
    widget, _filter_widget = _file_list(qtbot)
    widget.update_scene(_scene("a"), _frame(2), {})

    widget.set_scope(EntityScope.FILE)
    assert _rows(widget) == [("a", True), ("b", False)]
    assert widget._count_label.text() == "2 objects in the file"
    item = widget._model.item_at(1)
    assert item is not None and item.types == ("car",) and [span.text for span in item.trailing] == ["0:05–0:08"]

    changed: list[object] = []
    widget._model.dataChanged.connect(lambda *args: changed.append(args))
    widget.update_scene(None, _frame(7), {})
    assert changed == []  # Highlights only repaint the visible rows.
    assert _rows(widget) == [("a", False), ("b", False)]
    widget.update_scene(None, _frame(8), {})
    assert _rows(widget) == [("a", False), ("b", True)]

    widget.set_scope(EntityScope.FRAME)
    widget.update_scene(_scene("a"), _frame(2), {})
    assert _rows(widget) == [("a", False)]


def test_file_scope_applies_type_filters_and_id_search(qtbot: QtBot) -> None:
    widget, filter_widget = _file_list(qtbot)
    widget.set_scope(EntityScope.FILE)

    filter_widget.filter_state.set_enabled("show_cars", False)
    widget.refilter()
    assert [entity_id for entity_id, _visible in _rows(widget)] == ["a"]

    filter_widget.filter_state.set_enabled("show_cars", True)
    filter_widget.set_id_query("b")
    widget.refilter()
    assert [entity_id for entity_id, _visible in _rows(widget)] == ["b"]


def test_file_rows_select_without_expanding(qtbot: QtBot) -> None:
    widget, _filter_widget = _file_list(qtbot)
    widget.update_scene(_scene("b"), _frame(5), {})
    widget._on_index_clicked(widget._model.index(0, 0))
    assert widget._model.has_expanded()
    widget.set_scope(EntityScope.FILE)
    assert not widget._model.has_expanded()

    with qtbot.waitSignal(widget.entitySelected) as selected:
        widget._on_index_clicked(widget._model.index(1, 0))

    item = widget._model.item_at(1)
    assert selected.args == ["b"]
    assert item is not None and not item.expanded
    assert widget._list_view.uniformItemSizes()


def test_object_button_shows_the_object_on_the_displayed_frame(qtbot: QtBot) -> None:
    card = ObjectCard("b", _history())
    qtbot.addWidget(card)
    shown = _scene("b")
    card.show_frame(shown, 5)
    assert card._details.isHidden() and card._details.text() == ""

    card._button.setChecked(True)
    assert not card._details.isHidden()
    assert "human" in card._details.text() and card._button.text().startswith("▾ b")

    card.show_frame(_scene("a"), 7)
    assert "Not in this frame" in card._details.text()
    card.show_frame(_scene("a"), 8)
    assert "not shown" in card._details.text()
    card.show_frame(shown, 5)
    assert "human" in card._details.text()

    card._button.setChecked(False)
    assert card._details.isHidden() and card._button.text().startswith("▸ b")


def test_object_card_explains_objects_not_shown_in_the_video(qtbot: QtBot) -> None:
    card = ObjectCard("gone", _history())
    qtbot.addWidget(card)

    assert card.findChild(PresenceStrip) is None
    assert any("Not shown in this video" in label.text() for label in card.findChildren(QLabel))


def test_presence_strip_requests_the_frame_under_the_pointer(qtbot: QtBot) -> None:
    history = _history()
    object_history = history.object("b")
    assert object_history is not None
    strip = PresenceStrip(object_history, history.events_involving("b"), history.frame_count)
    qtbot.addWidget(strip)
    strip.resize(100, 16)
    strip.show()
    qtbot.waitExposed(strip)

    with qtbot.waitSignal(strip.frameRequested) as requested:
        QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=QPoint(55, 8))

    assert requested.args == [5]


def test_pane_shows_every_object_of_an_event_and_follows_the_displayed_frame(qtbot: QtBot) -> None:
    pane = ObjectHistoryPane(_history())
    qtbot.addWidget(pane)
    pane.show_frame(_scene("a"), 3)

    pane.show_event(_RENAME)
    assert [card._entity_id for card in pane._cards] == ["a", "b"]
    assert not pane.isHidden()
    assert "Rename a → b" in pane._title.text() and 'href="4"' in pane._title.text()
    assert all(card._strip is not None and card._strip._current_frame == 3 for card in pane._cards)

    pane.show_object("b")
    assert [card._entity_id for card in pane._cards] == ["b"]

    pane.clear()
    assert pane._cards == [] and pane.isHidden()


def test_panel_routes_selection_frames_and_scene_updates(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(render_catalog_selection, scene_history=_history())
    qtbot.addWidget(panel)
    panel.show()
    panel.findChild(QTabWidget, "mediaToolsTabs").setCurrentIndex(1)  # type: ignore[union-attr]
    pane = panel.findChild(ObjectHistoryPane)
    assert pane is not None
    requested: list[int] = []
    panel.frameRequested.connect(requested.append)

    panel.update_scene(_scene("a"), _frame(5), {})
    panel.event_log.eventSelected.emit(_DELETE)
    assert [card._entity_id for card in pane._cards] == ["b"]
    panel.entity_list.entitySelected.emit("a")
    assert [card._entity_id for card in pane._cards] == ["a"]
    pane._cards[0].frameRequested.emit(3)
    panel.event_log.frameRequested.emit(9)

    assert requested == [3, 9]
    assert panel.event_log._model.position_row() == 0
    assert pane._cards[0]._strip is not None and pane._cards[0]._strip._current_frame == 5


def test_panel_drops_repeated_frames_and_hidden_panes_catch_up_when_shown(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(render_catalog_selection, scene_history=_history())
    qtbot.addWidget(panel)
    pane = panel.findChild(ObjectHistoryPane)
    assert pane is not None
    pane.show_object("b")
    card = pane._cards[0]
    scene = _scene("b")
    forwarded: list[object] = []
    panel.entity_list.update_scene = lambda *args: forwarded.append(args)  # type: ignore[method-assign]

    panel.update_scene(scene, _frame(5), {})
    panel.update_scene(scene, _frame(5), {"overlay_reused": True})
    assert len(forwarded) == 1
    assert card._strip is not None and card._strip._current_frame is None  # Hidden: nothing is updated.

    panel.show()
    assert card._strip._current_frame == 5


def test_panel_updates_open_cards_when_filters_change_while_paused(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(
        render_catalog_selection, filter_config=build_default_filter_config(), scene_history=_history()
    )
    qtbot.addWidget(panel)
    panel.show()
    shown = _scene("b")
    filtered = _scene()
    panel.update_scene(
        shown,
        _frame(5),
        {},
        lambda: filtered if not panel.filter_widget.filter_state.is_enabled("show_humans") else shown,
    )
    panel.entity_list.entitySelected.emit("b")
    pane = panel.findChild(ObjectHistoryPane)
    assert pane is not None
    card = pane._cards[0]
    card._button.setChecked(True)
    assert "human" in card._details.text()

    panel.filter_widget.filter_state.set_enabled("show_humans", False)
    panel.filter_widget.filterChanged.emit()

    assert "not shown" in card._details.text()


def test_panel_replaces_history_in_every_tool(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(render_catalog_selection, scene_history=_history())
    qtbot.addWidget(panel)
    panel.show()
    panel.update_scene(_scene("b"), _frame(5), {})
    panel.event_log.eventSelected.emit(_RENAME)
    pane = panel.findChild(ObjectHistoryPane)
    assert pane is not None
    replacement = SceneHistory(
        events=(_DELETE,),
        objects=(ObjectHistory("b", ("car",), ((2, 9),), _FRAME_TIMES),),
        frame_count=10,
    )

    panel.set_scene_history(replacement)

    assert panel.event_log._model.rowCount() == 1
    assert [card._entity_id for card in pane._cards] == ["a", "b"]
    assert pane._cards[0]._strip is None  # "a" is no longer shown in the replacement history.
    assert pane._cards[1]._strip is not None and pane._cards[1]._strip._current_frame == 5
    assert "Rename a → b" in pane._title.text()


def test_live_panel_has_no_history_tools(qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection) -> None:
    panel = MediaToolsPanel(render_catalog_selection)
    qtbot.addWidget(panel)

    assert panel.findChild(ObjectHistoryPane) is None
    assert panel.findChild(QToolButton, "entityScope_file") is None


def test_file_scope_delegates_recorded_types_once_per_distinct_set(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget, filter_widget = _file_list(qtbot)
    history = SceneHistory(
        events=(),
        objects=(
            ObjectHistory("Object-A", ("human", "car"), ((0, 3),), _FRAME_TIMES),
            ObjectHistory("Object-B", ("human", "car"), ((0, 3),), _FRAME_TIMES),
            ObjectHistory("unmatched", ("unknown",), ((0, 3),), _FRAME_TIMES),
        ),
        frame_count=10,
    )
    widget.set_history(history, filter_widget)
    filter_widget.set_id_query("OBJECT")
    recorded: list[tuple[str, ...]] = []

    def keep_types(types: tuple[str, ...], config: FilterConfig, state: FilterState) -> bool:
        assert config is filter_widget.filter_config and state is filter_widget.filter_state
        recorded.append(types)
        return True

    monkeypatch.setattr(
        "ax_devil.modules.video_viewer.media_tools.entity_list_widget.history_type_filter_keeps", keep_types
    )
    widget.set_scope(EntityScope.FILE)

    assert [entity_id for entity_id, _visible in _rows(widget)] == ["Object-A", "Object-B"]
    assert recorded == [("human", "car")]


def test_entity_only_decoder_filters_disable_file_scope(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    config = FilterConfig(
        options=(
            FilterOption(
                id="moving", label="Moving", predicate=lambda entity, _state: entity.motion_state is MotionState.Moving
            ),
        )
    )
    panel = MediaToolsPanel(render_catalog_selection, filter_config=config, scene_history=_history())
    qtbot.addWidget(panel)
    file_button = panel.findChild(QToolButton, "entityScope_file")
    frame_button = panel.findChild(QToolButton, "entityScope_frame")

    assert file_button is not None and not file_button.isEnabled()
    assert "classification filters" in file_button.toolTip()
    assert frame_button is not None and frame_button.isEnabled() and frame_button.isChecked()
