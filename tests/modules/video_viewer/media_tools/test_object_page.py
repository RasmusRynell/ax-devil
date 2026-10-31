"""The object page's copy icons: they copy the full value, not what the narrow page shows."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

import pytest
from PySide6.QtCore import QModelIndex, QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.copy_cell import CopiedMark, copy_column_px
from ax_devil.modules.object_inspection import fields_model
from ax_devil.modules.object_inspection.fields_model import FieldsModel, Section, field_sections
from ax_devil.modules.object_inspection.fields_view import FieldsView
from ax_devil.modules.object_inspection.pinned_fields import PinnedFields
from ax_devil.modules.object_inspection.rows import FieldRow, RowFonts, id_row
from ax_devil.modules.scene.inspection import InspectionField
from ax_devil.modules.scene.model import (
    RGB,
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.video_viewer.media_tools.object_page import ObjectPage

_BOX = BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True)
_ID = "9f1c2a7e-3b4d-4e5f-8a6b-0c1d2e3f4a5b"


def test_copy_icons_copy_the_full_id_and_the_raw_section_as_json(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    entity = Entity(EntityId(_ID))
    entity.add_observation(
        Observation(
            frame_number=3,
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
            classification=[Classification("human", Score(0.9))],
        )
    )
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    scene.add_entity(entity)
    made: list[None] = []
    to_json = fields_model._json_of

    def counting_json(items: Sequence[InspectionField]) -> str:
        made.append(None)
        return to_json(items)

    monkeypatch.setattr(fields_model, "_json_of", counting_json)
    page = ObjectPage(_ID, {"Raw": False}, PinnedFields())
    qtbot.addWidget(page)
    page.resize(200, 400)  # Too narrow for the whole id
    page.show()
    page.update_scene(scene, 0)
    fields = page.findChild(FieldsView, "objectFields")
    assert fields is not None
    model = fields.model()
    fields.grab()
    assert made == []  # Painting the Raw caption does not build the JSON it would copy
    copy_x = fields.viewport().width() - 5

    def copy_at(index: QModelIndex) -> str:
        rect = fields.visualRect(index)
        QTest.mouseClick(fields.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(copy_x, rect.center().y()))
        return QGuiApplication.clipboard().text()

    assert copy_at(model.index(0, 0)) == _ID
    raw = next(row for row in range(model.rowCount()) if model.index(row, 0).data() == "Raw")
    assert "frame_number" in json.loads(copy_at(model.index(raw, 0)))

    first = model.index(0, 0)
    QTest.mouseClick(
        fields.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(20, fields.visualRect(first).center().y())
    )
    copy_at(model.index(raw, 0))  # The copy icon of another row moves the cursor there as well
    QGuiApplication.clipboard().clear()
    QTest.keyClick(fields, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert "frame_number" in json.loads(QGuiApplication.clipboard().text())

    measured: list[str] = []
    label_px = FieldRow.label_px

    def counting(row: FieldRow, fonts: RowFonts) -> int:
        measured.append(row.label)
        return label_px(row, fonts)

    monkeypatch.setattr(FieldRow, "label_px", counting)
    page.update_scene(scene, 1)  # A frame that only brings new values measures no label again
    assert measured == []


def _page_of(qtbot: QtBot, pins: PinnedFields, entity_id: str, debug: dict[str, Any]) -> tuple[ObjectPage, Scene]:
    entity = Entity(EntityId(entity_id))
    entity.add_observation(Observation(frame_number=0, geometry=_BOX, debug=debug))
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    scene.add_entity(entity)
    page = ObjectPage(entity_id, {"Debug": False}, pins)
    qtbot.addWidget(page)
    page.resize(300, 500)
    page.show()
    page.update_scene(scene, 0)
    return page, scene


def _click_pin(view: FieldsView, label: str) -> None:
    """Click the pin icon of the row labelled *label*."""
    model = cast(FieldsModel, view.model())
    for top in range(model.rowCount()):
        for row in range(model.rowCount(model.index(top, 0))):
            index = model.index(row, 0, model.index(top, 0))
            if (found := model.row_at(index)) is not None and found.label == label:
                x = view.viewport().width() - copy_column_px() - copy_column_px() // 2
                y = view.visualRect(index).center().y()
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x, y))
                return
    raise AssertionError(f"no row {label}")


def _pinned(page: ObjectPage) -> dict[str, str]:
    view = page.findChild(FieldsView, "pinnedFields")
    assert view is not None
    model = cast(FieldsModel, view.model())
    return {row.label: row.copy_text() for row in model.rows()} if view.isVisible() else {}


def test_clothing_colors_are_listed_under_a_heading_per_attribute() -> None:
    def color(name: str, rgb: RGB, score: float) -> ColorClassification:
        return ColorClassification(name, rgb, Score(score))

    blue, white = RGB(0, 0, 255), RGB(255, 255, 255)
    attributes = [
        Attribute("upper_clothing_colors", [color("blue", blue, 0.62), color("white", white, 0.21)]),
        Attribute("lower_clothing_colors", [color("blue", blue, 0.71)]),
    ]
    human = Classification("human", Score(0.9), attributes=attributes)
    entity = Entity(EntityId("a"))
    entity.add_observation(Observation(frame_number=0, geometry=_BOX, classification=[human]))
    rows = field_sections(entity)[0].rows
    labels = "type, score, upper clothing colors, blue, white, lower clothing colors, blue, candidates.human.score"
    assert [row.label for row in rows] == labels.split(", ")
    assert rows[3].copy_text() == "0.62 #0000ff"
    assert rows[3].field_id != rows[6].field_id


def test_debug_lists_fields_the_source_supplied_first_under_captions() -> None:
    def debug_rows(debug: dict[str, Any], source_data: dict[str, Any]) -> Section:
        entity = Entity(EntityId("a"))
        entity.add_observation(Observation(frame_number=0, geometry=_BOX, debug=debug, source_data=source_data))
        return {section.name: section for section in field_sections(entity)}["Debug"]

    mixed = debug_rows({"latency": 12}, {"vendor": {"track_id": 9}})
    assert [row.label for row in mixed.rows] == ["Source", "vendor.track_id", "Computed", "latency"]
    assert mixed.count == 2
    assert [row.label for row in debug_rows({}, {"uvg_vcm": {"track_id": 9}}).rows] == [
        "Source",
        "uvg_vcm.track_id",
    ]
    assert [row.label for row in debug_rows({"latency": 12}, {}).rows] == ["latency"]


def test_a_pinned_field_shows_the_same_field_of_whichever_object_is_shown(qtbot: QtBot) -> None:
    pins = PinnedFields()
    first, scene = _page_of(qtbot, pins, "a", {"moving": True, "idle_s": 3.5})
    fields = first.findChild(FieldsView, "objectFields")
    assert fields is not None
    assert _pinned(first) == {}
    _click_pin(fields, "moving")
    assert _pinned(first) == {"debug.moving": "true"}

    second, _ = _page_of(qtbot, pins, "b", {"moving": False})
    assert _pinned(second) == {"debug.moving": "false"}
    third, _ = _page_of(qtbot, pins, "c", {"idle_s": 1.0})
    assert _pinned(third) == {"debug.moving": "unavailable"}  # Not the last value, and not false

    entity = scene.entities[EntityId("a")]
    entity.add_observation(Observation(frame_number=1, geometry=_BOX, debug={"moving": False}))
    first.update_scene(scene, 1)
    assert _pinned(first) == {"debug.moving": "false"}
    first.update_scene(Scene(time_slice=TimeSlice(start=1, end=1)), 2)  # The object left the frame
    assert _pinned(first) == {"debug.moving": "unavailable"}

    pinned = first.findChild(FieldsView, "pinnedFields")
    assert pinned is not None
    _click_pin(pinned, "debug.moving")
    assert _pinned(first) == {} and not pinned.isVisible()


def test_fields_view_minimum_width_does_not_depend_on_its_rows(qtbot: QtBot) -> None:
    view = FieldsView({})
    qtbot.addWidget(view)
    view.show()
    long_label = "candidates.human.attributes.confidence"
    widths = set()
    for sections, lead in (
        ((), ()),
        ((Section("A", (FieldRow("x", "1"),)),), (id_row(_ID),)),
        ((Section("A", (FieldRow(long_label, "1"),)),), ()),
        ((Section("A", tuple(FieldRow(f"k{n}", "1") for n in range(300))),), ()),  # Enough rows for a scroll bar
    ):
        view.set_sections(sections, lead)
        widths.add(view.minimumSizeHint().width())
    assert len(widths) == 1  # The panel does not change width from one object to the next
    assert FieldRow(long_label, "1").cut_text(QRect(0, 0, 400, 20), RowFonts.current(), 60) is not None  # and the label


def _fields(qtbot: QtBot, width: int = 260) -> FieldsView:
    view = FieldsView({})
    qtbot.addWidget(view)
    view.resize(width, 200)
    view.show()
    return view


def test_label_column_follows_the_values_of_the_object_shown(qtbot: QtBot) -> None:
    label = "candidates.human.attributes.confidence"
    view, fresh = _fields(qtbot), _fields(qtbot)
    view.set_sections((Section("A", (FieldRow(label, "7"),)),))
    view.set_sections((Section("A", (FieldRow(label, _ID),)),))  # Another object with the same fields
    fresh.set_sections((Section("A", (FieldRow(label, _ID),)),))
    delegate: Any = fresh.itemDelegate()
    assert delegate.label_width < FieldRow(label, "7").label_px(RowFonts.current())  # The id takes room from the label
    assert cast(Any, view.itemDelegate()).label_width == delegate.label_width


def test_copy_check_ends_when_the_copied_value_changes(qtbot: QtBot) -> None:
    view = _fields(qtbot)
    view.set_sections((Section("A", (FieldRow("speed", "1.0"),)),))
    model = cast(FieldsModel, view.model())
    row = model.index(0, 0, model.index(0, 0))
    view.expand(model.index(0, 0))
    rect = view.visualRect(row)
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(view.viewport().width() - 5, rect.center().y())
    )
    mark = view.findChild(CopiedMark)
    assert mark is not None and mark.key == model.copy_key_at(row)
    view.set_sections((Section("A", (FieldRow("speed", "1.0"),)),))  # The same value again keeps the check
    assert mark.key == model.copy_key_at(row)
    view.set_sections((Section("A", (FieldRow("speed", "2.0"),)),))  # The clipboard has the previous value
    assert mark.key != model.copy_key_at(row)
