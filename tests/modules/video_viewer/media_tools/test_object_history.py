"""Tests for whole-file objects in the entity list, the object history pane and their panel wiring."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QListView, QTabWidget, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.filtering.filter_config import (
    FilterConfig,
    FilterOption,
    build_default_filter_config,
)
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import (
    MotionState,
    Scene,
    TimeSlice,
)
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.entity_list_widget import EntityListModel, EntityListWidget, EntityScope
from ax_devil.modules.video_viewer.media_tools.event_log_widget import EventLogModel
from ax_devil.modules.video_viewer.media_tools.object_history_widget import (
    ObjectCard,
    ObjectHistoryPane,
    PresenceStrip,
)
from tests.helpers.entities import entity_with_classes


def _frame(index: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=index, timestamp_monotime_us=index * 1_000_000.0)


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
        scene.add_entity(entity_with_classes("human", entity_id=entity_id))
    return scene


def _file_list(qtbot: QtBot) -> tuple[EntityListWidget, SessionFilter]:
    filter_widget = SessionFilter(build_default_filter_config())
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.set_history(_history(), filter_widget)
    widget.show()
    return widget, filter_widget


def _view(widget: QWidget) -> QListView:
    view = widget.findChild(QListView)
    assert view is not None
    return view


def _model(widget: EntityListWidget) -> EntityListModel:
    return cast(EntityListModel, _view(widget).model())


def _rows(widget: EntityListWidget) -> list[tuple[str, bool]]:
    model = _model(widget)
    items = [model.item_at(row) for row in range(model.rowCount())]
    return [(item.entity_id, item.visible) for item in items if item is not None]


def _click(widget: EntityListWidget, row: int) -> None:
    view = _view(widget)
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(view.model().index(row, 0)).center()
    )


def _cards(pane: ObjectHistoryPane) -> list[ObjectCard]:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # Closed cards are deleted later.
    cards: list[ObjectCard] = pane.findChildren(ObjectCard)
    return cards


def _card_ids(pane: ObjectHistoryPane) -> list[str]:
    return [_object_button(card).text().split()[1] for card in _cards(pane)]


def _object_button(card: ObjectCard) -> QToolButton:
    button = card.findChild(QToolButton, "objectButton")
    assert button is not None
    return button


def _details(card: ObjectCard) -> str:
    """Return the card's shown text below its button."""
    return " ".join(label.text() for label in card.findChildren(QLabel) if label.isVisibleTo(card))


def _title(pane: ObjectHistoryPane) -> str:
    title = pane.findChild(QLabel, "", Qt.FindChildOption.FindDirectChildrenOnly)
    assert title is not None
    return title.text()


def _click_strip_at_frame(card: ObjectCard, frame: int) -> None:
    strip = card.findChild(PresenceStrip)
    assert strip is not None
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=QPoint(int(strip.width() * (frame + 0.5) / 10), 8))


def test_file_scope_lists_every_object_and_highlights_those_on_the_displayed_frame(qtbot: QtBot) -> None:
    widget, _filter_widget = _file_list(qtbot)
    widget.update_scene(_scene("a"), _frame(2), {})

    widget.set_scope(EntityScope.FILE)
    assert _rows(widget) == [("a", True), ("b", False)]
    assert any(label.text() == "2 objects in the file" for label in widget.findChildren(QLabel))
    item = _model(widget).item_at(1)
    assert item is not None and item.types == ("car",) and [span.text for span in item.trailing] == ["0:05–0:08"]

    changed: list[object] = []
    _model(widget).dataChanged.connect(lambda *args: changed.append(args))
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

    filter_widget.set_enabled("show_cars", False)
    widget.refilter()
    assert [entity_id for entity_id, _visible in _rows(widget)] == ["a"]

    filter_widget.set_enabled("show_cars", True)
    filter_widget.set_id_query("b")
    widget.refilter()
    assert [entity_id for entity_id, _visible in _rows(widget)] == ["b"]


def test_file_rows_select_without_expanding(qtbot: QtBot) -> None:
    widget, _filter_widget = _file_list(qtbot)
    qtbot.waitExposed(widget)
    widget.update_scene(_scene("b"), _frame(5), {})
    _click(widget, 0)
    assert _model(widget).has_expanded()
    widget.set_scope(EntityScope.FILE)
    assert not _model(widget).has_expanded()

    with qtbot.waitSignal(widget.entitySelected) as selected:
        _click(widget, 1)

    item = _model(widget).item_at(1)
    assert selected.args == ["b"]
    assert item is not None and not item.expanded


def test_object_button_shows_the_object_on_the_displayed_frame(qtbot: QtBot) -> None:
    card = ObjectCard("b", _history())
    qtbot.addWidget(card)
    shown = _scene("b")
    card.show_frame(shown, 5)
    assert _details(card) == ""

    _object_button(card).click()
    assert "human" in _details(card)

    card.show_frame(_scene("a"), 7)
    assert "Not in this frame" in _details(card)
    card.show_frame(_scene("a"), 8)
    assert "not shown" in _details(card)
    card.show_frame(shown, 5)
    assert "human" in _details(card)

    _object_button(card).click()
    assert _details(card) == ""


def test_object_card_explains_objects_not_shown_in_the_video(qtbot: QtBot) -> None:
    card = ObjectCard("gone", _history())
    qtbot.addWidget(card)

    assert card.findChild(PresenceStrip) is None
    assert "Not shown in this video" in _details(card)


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
    assert _card_ids(pane) == ["a", "b"]
    assert not pane.isHidden()
    assert "Rename a → b" in _title(pane) and 'href="4"' in _title(pane)
    for card in _cards(pane):
        _object_button(card).click()
    assert ["human" in _details(card) for card in _cards(pane)] == [True, False]
    pane.show_frame(_scene("b"), 5)
    assert ["human" in _details(card) for card in _cards(pane)] == [False, True]

    pane.show_object("b")
    assert _card_ids(pane) == ["b"]

    pane.clear()
    assert _cards(pane) == [] and pane.isHidden()


def test_panel_routes_selection_frames_and_scene_updates(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(render_catalog_selection, scene_history=_history(), filter_model=SessionFilter())
    qtbot.addWidget(panel)
    panel.show()
    panel.findChild(QTabWidget, "mediaToolsTabs").setCurrentIndex(1)  # type: ignore[union-attr]
    pane = panel.findChild(ObjectHistoryPane)
    assert pane is not None
    requested: list[int] = []
    panel.frameRequested.connect(requested.append)

    panel.update_scene(_scene("a"), _frame(5), {})
    panel.event_log.eventSelected.emit(_DELETE)
    assert _card_ids(pane) == ["b"]
    panel.entity_list.entitySelected.emit("a")
    (card,) = _cards(pane)
    assert _card_ids(pane) == ["a"]
    qtbot.waitExposed(card)
    _click_strip_at_frame(card, 3)
    panel.event_log.frameRequested.emit(9)

    assert requested == [3, 9]
    assert cast(EventLogModel, _view(panel.event_log).model()).position_row() == 0
    _object_button(card).click()
    assert "human" in _details(card)  # The new card already follows the displayed frame.


def test_panel_drops_repeated_frames_and_hidden_panes_catch_up_when_shown(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(render_catalog_selection, scene_history=_history(), filter_model=SessionFilter())
    qtbot.addWidget(panel)
    pane = panel.findChild(ObjectHistoryPane)
    assert pane is not None
    pane.show_object("b")
    (card,) = _cards(pane)
    _object_button(card).click()
    scene = _scene("b")
    forwarded: list[object] = []
    panel.entity_list.update_scene = lambda *args: forwarded.append(args)  # type: ignore[method-assign]

    panel.update_scene(scene, _frame(5), {})
    panel.update_scene(scene, _frame(5), {"overlay_reused": True})
    assert len(forwarded) == 1
    assert "human" not in _details(card)  # Hidden: nothing is updated.

    panel.show()
    assert "human" in _details(card)


def test_panel_updates_open_cards_when_filters_change_while_paused(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(
        render_catalog_selection, filter_model=SessionFilter(build_default_filter_config()), scene_history=_history()
    )
    qtbot.addWidget(panel)
    panel.show()
    shown = _scene("b")
    filtered = _scene()
    panel.update_scene(
        shown,
        _frame(5),
        {},
        lambda: filtered if not panel.filter_model.is_enabled("show_humans") else shown,
    )
    panel.entity_list.entitySelected.emit("b")
    pane = panel.findChild(ObjectHistoryPane)
    assert pane is not None
    (card,) = _cards(pane)
    _object_button(card).click()
    assert "human" in _details(card)

    panel.filter_model.set_enabled("show_humans", False)

    assert "not shown" in _details(card)


def test_panel_replaces_history_in_every_tool(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection
) -> None:
    panel = MediaToolsPanel(render_catalog_selection, scene_history=_history(), filter_model=SessionFilter())
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

    assert _view(panel.event_log).model().rowCount() == 1
    assert _card_ids(pane) == ["a", "b"]
    first, second = _cards(pane)
    assert first.findChild(PresenceStrip) is None  # "a" is no longer shown in the replacement history.
    assert second.findChild(PresenceStrip) is not None
    _object_button(second).click()
    assert "human" in _details(second)  # The rebuilt card follows the displayed frame.
    assert "Rename a → b" in _title(pane)


def test_live_panel_has_no_history_tools(qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection) -> None:
    panel = MediaToolsPanel(render_catalog_selection, filter_model=SessionFilter())
    qtbot.addWidget(panel)

    assert panel.findChild(ObjectHistoryPane) is None
    assert panel.findChild(QToolButton, "entityScope_file") is None


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
    panel = MediaToolsPanel(render_catalog_selection, filter_model=SessionFilter(config), scene_history=_history())
    qtbot.addWidget(panel)
    file_button = panel.findChild(QToolButton, "entityScope_file")
    frame_button = panel.findChild(QToolButton, "entityScope_frame")

    assert file_button is not None and not file_button.isEnabled()
    assert "classification filters" in file_button.toolTip()
    assert frame_button is not None and frame_button.isEnabled() and frame_button.isChecked()
