from __future__ import annotations

from typing import cast

from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QListView, QStyleOptionViewItem
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
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
    EntityListItem,
    EntityListModel,
    EntityListWidget,
    entity_detail_html,
    split_id_and_class,
)


def _view(widget: EntityListWidget) -> QListView:
    view = widget.findChild(QListView)
    assert view is not None
    return view


def _item(widget: EntityListWidget, row: int) -> EntityListItem:
    item = cast(EntityListModel, _view(widget).model()).item_at(row)
    assert item is not None
    return item


def _ids(widget: EntityListWidget) -> list[str]:
    return [_item(widget, row).entity_id for row in range(_view(widget).model().rowCount())]


def _click(widget: EntityListWidget, row: int) -> None:
    view = _view(widget)
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(view.model().index(row, 0)).center()
    )


def _row_height(widget: EntityListWidget, row: int) -> int:
    view = _view(widget)
    return view.visualRect(view.model().index(row, 0)).height()


def _scene_with_entities(*entities: Entity) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity in entities:
        scene.add_entity(entity)
    return scene


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


def _shown_list(qtbot: QtBot) -> EntityListWidget:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_rows_follow_each_delivered_scene(qtbot: QtBot) -> None:
    """Synthetic per-frame ids replace earlier rows, and rows of vanished entities are removed."""
    widget = _shown_list(qtbot)

    widget.update_scene(
        _scene_with_entities(_entity("untracked:0:0"), _entity("untracked:0:1")), FrameIdentifier(0, 0), {}
    )
    widget.update_scene(
        _scene_with_entities(_entity("untracked:1:0"), _entity("untracked:1:1")), FrameIdentifier(1, 1), {}
    )
    assert _ids(widget) == ["untracked:1:0", "untracked:1:1"]

    widget.update_scene(_scene_with_entities(_entity("untracked:2:0")), FrameIdentifier(2, 2), {})
    assert _ids(widget) == ["untracked:2:0"]


def test_row_summary_matches_each_delivered_scene(qtbot: QtBot) -> None:
    """The same entity's row shows its id, class and the confidence of the current frame."""
    widget = _shown_list(qtbot)

    for frame, confidence in enumerate((0.3, 0.55, 0.9)):
        entity = _entity("a", frame_number=frame)
        entity.observations[-1].confidence = Score(confidence)
        widget.update_scene(_scene_with_entities(entity), FrameIdentifier(frame, frame), {})

        item = _item(widget, 0)
        assert (item.entity_id, item.class_name, item.types) == ("a", "person", ("person",))
        assert [span.text for span in item.trailing] == [f"{confidence:.0%}"]


def test_motion_states_get_distinct_indicators(qtbot: QtBot) -> None:
    widget = _shown_list(qtbot)
    entities = []
    for state in (MotionState.Moving, MotionState.Stationary, MotionState.Unknown, None):
        entity = _entity(str(state))
        entity.motion_state = state
        entities.append(entity)

    widget.update_scene(_scene_with_entities(*entities), FrameIdentifier(0, 0), {})

    glyphs = [[span.text for span in _item(widget, row).trailing] for row in range(4)]
    assert all(len(spans) == 1 and spans[0] for spans in glyphs[:3])
    assert len({spans[0] for spans in glyphs[:3]}) == 3
    assert glyphs[3] == []  # Absent motion shows no indicator.


def test_clicking_a_row_toggles_its_detail_and_height(qtbot: QtBot) -> None:
    widget = _shown_list(qtbot)
    second = _entity("b", frame_number=2)
    widget.update_scene(_scene_with_entities(_entity("a", frame_number=1), second), FrameIdentifier(0, 0), {})
    collapsed_height = _row_height(widget, 1)

    with qtbot.waitSignal(widget.entitySelected) as selected:
        _click(widget, 1)

    assert selected.args == ["b"]
    assert _item(widget, 1).expanded
    assert _item(widget, 1).detail_html == entity_detail_html(second)
    assert _row_height(widget, 1) > collapsed_height
    assert _row_height(widget, 0) == collapsed_height

    _click(widget, 1)
    assert not _item(widget, 1).expanded
    assert _row_height(widget, 1) == collapsed_height


def test_expanded_detail_follows_reordered_entity(qtbot: QtBot) -> None:
    widget = _shown_list(qtbot)
    widget.update_scene(
        _scene_with_entities(_entity("a", frame_number=1), _entity("b", frame_number=2)), FrameIdentifier(0, 0), {}
    )
    _click(widget, 0)

    moved = _entity("a", frame_number=4)
    widget.update_scene(_scene_with_entities(_entity("b", frame_number=3), moved), FrameIdentifier(1, 1), {})

    assert not _item(widget, 0).expanded
    assert _item(widget, 0).detail_html == ""
    assert _item(widget, 1).expanded
    assert _item(widget, 1).detail_html == entity_detail_html(moved)


def test_expanded_row_height_fits_wrapped_detail(qtbot: QtBot) -> None:
    """A narrower list wraps detail text onto more lines, so the expanded row must grow to show it."""
    widget = _shown_list(qtbot)
    widget.update_scene(_scene_with_entities(_entity_with_many_details("a")), FrameIdentifier(0, 0), {})
    view = _view(widget)
    index = view.model().index(0, 0)
    delegate = view.itemDelegate()

    def height_at(width: int) -> int:
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, width, 0)
        return delegate.sizeHint(option, index).height()

    collapsed_height = height_at(160)
    _click(widget, 0)

    assert height_at(160) > height_at(600) > collapsed_height


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
    _click(widget, 0)
    view = _view(widget)
    index = view.model().index(0, 0)
    delegate = view.itemDelegate()
    painted = QStyleOptionViewItem()
    painted.rect = QRect(0, 0, view.viewport().width(), 0)

    assert view.verticalScrollBar().isVisible()
    assert delegate.sizeHint(QStyleOptionViewItem(), index).height() == delegate.sizeHint(painted, index).height()


def test_entity_list_refilters_shown_frame_when_filters_change(qtbot: QtBot) -> None:
    widget = _shown_list(qtbot)
    full = _scene_with_entities(_entity("a"), _entity("b"))

    widget.update_scene(_scene_with_entities(_entity("a")), FrameIdentifier(0, 0), {}, lambda: full)
    assert _ids(widget) == ["a"]

    widget.refilter()
    assert _ids(widget) == ["a", "b"]


def test_entity_details_show_object_fields_then_debug() -> None:
    entity = _entity("a", frame_number=3)
    entity.motion_state = MotionState.Moving
    entity.end_reason = "lost"
    entity.observations[-1].confidence = Score(0.5)
    entity.observations[-1].debug = {"cost": 1}

    html = entity_detail_html(entity)

    for field_name in ("end_reason", "motion_state", "classification", "frame_number", "confidence"):
        assert field_name in html
    assert html.index("confidence") < html.index("cost")


def test_hidden_inspector_defers_rows_and_shows_only_latest_scene(qtbot: QtBot) -> None:
    widget = EntityListWidget(show_title=False)
    qtbot.addWidget(widget)
    widget.update_scene(_scene_with_entities(_entity("old")), FrameIdentifier(1, 1), {})
    latest = _scene_with_entities(_entity("latest"))
    widget.update_scene(latest, FrameIdentifier(2, 2), {})
    assert _ids(widget) == []
    widget.show()
    item = _item(widget, 0)
    assert item.entity_id == "latest"
    assert item.detail_html == ""
    widget.update_scene(latest, FrameIdentifier(3, 3), {})
    assert _item(widget, 0) is item
    assert any("frame 3" in label.text() for label in widget.findChildren(QLabel))
    widget.hide()
    widget.update_scene(None, None, None)
    widget.show()
    assert _ids(widget) == []
    widget.hide()
    widget.update_scene(latest, FrameIdentifier(4, 4), {})
    widget.clear()
    widget.show()
    assert _ids(widget) == []


def test_long_ids_leave_room_for_the_class() -> None:
    """Short ids leave spare width for long classes; long ids still reserve room for a visible class."""
    assert split_id_and_class(300, 40, 50, 8) == (40, 50)
    assert split_id_and_class(150, 20, 180, 8) == (20, 122)
    id_room, class_room = split_id_and_class(90, 260, 120, 8)
    assert 0 < class_room <= 30 and id_room + 8 + class_room == 90
    assert split_id_and_class(150, 260, 0, 8) == (150, 0)
