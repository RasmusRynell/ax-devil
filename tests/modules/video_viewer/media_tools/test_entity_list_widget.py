from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QStyleOptionViewItem
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.scene.inspection import entity_detail_items
from ax_devil.modules.scene.model import (
    Attribute,
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
from ax_devil.modules.video_viewer.media_tools.entity_list_widget import (
    EntityListWidget,
    _build_trailing,
    _detail_row,
    _movement_span,
    split_id_and_class,
)


def _entity_id(widget: EntityListWidget, row: int) -> str:
    item = widget._model.item_at(row)
    assert item is not None
    return item.entity_id


def _scene_with_entities(*entities: Entity) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity in entities:
        scene.add_entity(entity)
    return scene


def _frame_row(frame_number: int) -> str:
    items = dict(entity_detail_items(_entity("x", frame_number=frame_number)))
    return _detail_row("frame_number", items["frame_number"])


def _entity(entity_id: str, *, frame_number: int = 0) -> Entity:
    entity = Entity(id=EntityId(entity_id))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2),
            classification=[Classification("person", Score(0.9))],
            frame_number=frame_number,
        )
    )
    return entity


def _entity_with_many_details(entity_id: str) -> Entity:
    entity = _entity(entity_id, frame_number=10)
    observation = entity.observations[-1]
    observation.classification[0].attributes = [
        Attribute(f"detail_{index}", f"long detail value {index} that should wrap in a narrow entity list")
        for index in range(12)
    ]
    return entity


def test_entity_list_replaces_row_data_by_position_for_synthetic_detection_ids(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(
        _scene_with_entities(_entity("untracked:0:0"), _entity("untracked:0:1")),
        FrameIdentifier(0, 0),
        {},
    )

    widget.update_scene(
        _scene_with_entities(_entity("untracked:1:0"), _entity("untracked:1:1")),
        FrameIdentifier(1, 1),
        {},
    )

    assert widget._model.rowCount() == 2
    assert [_entity_id(widget, row) for row in range(2)] == ["untracked:1:0", "untracked:1:1"]


def test_entity_list_removes_unused_rows(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(
        _scene_with_entities(_entity("untracked:0:0"), _entity("untracked:0:1")),
        FrameIdentifier(0, 0),
        {},
    )

    widget.update_scene(_scene_with_entities(_entity("untracked:1:0")), FrameIdentifier(1, 1), {})

    assert widget._model.rowCount() == 1
    assert _entity_id(widget, 0) == "untracked:1:0"


def test_entity_list_expanded_detail_follows_reordered_entity(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(
        _scene_with_entities(_entity("a", frame_number=1), _entity("b", frame_number=2)),
        FrameIdentifier(0, 0),
        {},
    )

    widget._on_index_clicked(widget._model.index(0, 0))

    widget.update_scene(
        _scene_with_entities(_entity("b", frame_number=3), _entity("a", frame_number=4)),
        FrameIdentifier(1, 1),
        {},
    )

    first_item = widget._model.item_at(0)
    second_item = widget._model.item_at(1)
    assert first_item is not None
    assert second_item is not None
    assert not first_item.expanded
    assert first_item.detail_html == ""
    assert second_item.expanded
    assert _frame_row(4) in second_item.detail_html


def test_entity_list_expanded_row_requests_detail_height(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(
        _scene_with_entities(_entity("a", frame_number=1), _entity("b", frame_number=2)),
        FrameIdentifier(0, 0),
        {},
    )

    delegate = widget._list_view.itemDelegate()
    second_index = widget._model.index(1, 0)
    option = QStyleOptionViewItem()
    collapsed_height = delegate.sizeHint(option, second_index).height()

    widget._on_index_clicked(second_index)
    expanded_item = widget._model.item_at(1)

    assert expanded_item is not None
    assert expanded_item.expanded
    assert _frame_row(2) in expanded_item.detail_html
    assert delegate.sizeHint(option, second_index).height() > collapsed_height


def test_entity_list_expanded_row_height_fits_rendered_detail(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(
        _scene_with_entities(_entity_with_many_details("a")),
        FrameIdentifier(0, 0),
        {},
    )

    index = widget._model.index(0, 0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 160, 0)

    collapsed_height = widget._list_view.itemDelegate().sizeHint(option, index).height()
    widget._on_index_clicked(index)
    expanded_height = widget._list_view.itemDelegate().sizeHint(option, index).height()

    assert expanded_height > collapsed_height
    assert expanded_height > 180


def test_entity_list_measures_rows_at_painted_viewport_width(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.resize(220, 120)
    widget.show()
    widget.update_scene(
        _scene_with_entities(*(_entity_with_many_details(str(index)) for index in range(3))),
        FrameIdentifier(0, 0),
        {},
    )
    index = widget._model.index(0, 0)
    widget._on_index_clicked(index)
    delegate = widget._list_view.itemDelegate()
    painted = QStyleOptionViewItem()
    painted.rect = QRect(0, 0, widget._list_view.viewport().width(), 0)

    assert widget._list_view.verticalScrollBar().isVisible()
    assert delegate.sizeHint(QStyleOptionViewItem(), index).height() == delegate.sizeHint(painted, index).height()


def test_entity_list_clicking_nonfirst_row_populates_detail_immediately(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(
        _scene_with_entities(_entity("a", frame_number=1), _entity("b", frame_number=2)),
        FrameIdentifier(0, 0),
        {},
    )

    widget._on_index_clicked(widget._model.index(1, 0))
    second_item = widget._model.item_at(1)

    assert second_item is not None
    assert second_item.expanded
    assert _frame_row(2) in second_item.detail_html


def test_entity_list_shows_scene_and_observation_debug(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()
    entity = _entity("a")
    entity.observations[-1].debug = {"tracker": {"cost": 0.25}}
    scene = _scene_with_entities(entity)
    scene.debug = {"decoder": {"latency_ms": 12}}

    widget.update_scene(scene, FrameIdentifier(0, 0), {})
    widget._on_index_clicked(widget._model.index(0, 0))

    item = widget._model.item_at(0)
    assert item is not None
    assert "tracker" in item.detail_html
    assert "0.25" in item.detail_html
    assert not widget._scene_debug_label.isHidden()
    assert "latency_ms" in widget._scene_debug_label.text()

    widget.update_scene(_scene_with_entities(), FrameIdentifier(1, 1), {})

    assert widget._scene_debug_label.isHidden()


def test_entity_list_summary_matches_each_delivered_scene(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    for frame, confidence in enumerate((0.3, 0.55, 0.9)):
        entity = _entity("a", frame_number=frame)
        entity.observations[-1].confidence = Score(confidence)
        widget.update_scene(_scene_with_entities(entity), FrameIdentifier(frame, frame), {})

        item = widget._model.item_at(0)
        assert item is not None
        assert item.types == ("person",)
        assert item.entity_id == "a"
        assert [span.text for span in item.trailing] == [f"{confidence:.0%}"]


def test_entity_list_builds_rows_lazily_and_uses_uniform_sizes_only_when_collapsed(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(_scene_with_entities(_entity("a"), _entity("b")), FrameIdentifier(0, 0), {})

    assert widget._model._items == {}
    assert widget._list_view.uniformItemSizes()

    widget._on_index_clicked(widget._model.index(1, 0))
    assert not widget._list_view.uniformItemSizes()

    widget._on_index_clicked(widget._model.index(1, 0))
    assert widget._list_view.uniformItemSizes()


def test_entity_list_refilters_shown_frame_when_filters_change(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()
    full = _scene_with_entities(_entity("a"), _entity("b"))

    widget.update_scene(_scene_with_entities(_entity("a")), FrameIdentifier(0, 0), {}, lambda: full)
    assert widget._model.rowCount() == 1

    widget.refilter()
    assert widget._model.rowCount() == 2


def test_movement_span_shows_unknown_movement_indicator() -> None:
    assert _movement_span(MotionState.Unknown).text == "?"
    entity = _entity("unknown")
    entity.motion_state = MotionState.Unknown
    assert _build_trailing(entity)[0].text == "?"


def test_entity_summary_omits_absent_motion() -> None:
    assert not _build_trailing(_entity("absent"))


def test_entity_rows_show_the_class_after_the_id(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()

    widget.update_scene(_scene_with_entities(_entity("a")), FrameIdentifier(0, 0), {})

    item = widget._model.item_at(0)
    assert item is not None and (item.entity_id, item.class_name) == ("a", "person")


def test_entity_detail_items_include_every_populated_field() -> None:
    entity = _entity("a", frame_number=3)
    entity.motion_state = MotionState.Moving
    entity.end_reason = "lost"
    entity.observations[-1].confidence = Score(0.5)
    entity.observations[-1].debug = {"cost": 1}

    names = [name for name, _ in entity_detail_items(entity)]

    assert names == [
        "end_reason",
        "motion_state",
        "observations",
        "geometry",
        "classification",
        "frame_number",
        "confidence",
        "debug",
    ]


def test_movement_span_shows_moving_indicator() -> None:
    assert _movement_span(MotionState.Moving).text == "●"


def test_movement_span_shows_stationary_indicator() -> None:
    assert _movement_span(MotionState.Stationary).text == "○"


def test_hidden_inspector_defers_rows_and_shows_only_latest_scene(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.update_scene(_scene_with_entities(_entity("old")), FrameIdentifier(1, 1), {})
    latest = _scene_with_entities(_entity("latest"))
    widget.update_scene(latest, FrameIdentifier(2, 2), {})
    assert widget._model.rowCount() == 0
    widget.show()
    item = widget._model.item_at(0)
    assert item is not None and item.entity_id == "latest"
    assert item.detail_html == ""
    widget.update_scene(latest, FrameIdentifier(3, 3), {})
    assert widget._model.item_at(0) is item
    assert "frame 3" in widget._count_label.text()
    widget.hide()
    widget.update_scene(None, None, None)
    widget.show()
    assert widget._model.rowCount() == 0
    widget.hide()
    widget.update_scene(latest, FrameIdentifier(4, 4), {})
    widget.clear()
    widget.show()
    assert widget._model.rowCount() == 0


def test_long_ids_leave_room_for_the_class() -> None:
    """Both fit when there is room; otherwise a long id is shortened so the class stays visible."""
    assert split_id_and_class(300, 40, 50, 8) == (40, 50)
    id_room, class_room = split_id_and_class(150, 260, 50, 8)
    assert class_room == 50 and id_room == 150 - 8 - 50
    assert split_id_and_class(150, 260, 0, 8) == (150, 0)
    id_room, class_room = split_id_and_class(90, 260, 120, 8)
    assert 0 < class_room <= 30 and id_room + 8 + class_room == 90
