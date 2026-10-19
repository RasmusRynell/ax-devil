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

from ax_devil.modules.chrome.copy_cell import CopiedMark
from ax_devil.modules.object_inspection import fields_model
from ax_devil.modules.object_inspection.fields_model import FieldsModel, Section
from ax_devil.modules.object_inspection.fields_view import FieldsView
from ax_devil.modules.object_inspection.rows import FieldRow, RowFonts, id_row
from ax_devil.modules.scene.inspection import InspectionField
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.video_viewer.media_tools.object_page import ObjectPage

_ID = "9f1c2a7e-3b4d-4e5f-8a6b-0c1d2e3f4a5b"


def test_copy_icons_copy_the_full_id_and_the_raw_section_as_json(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    entity = Entity(EntityId(_ID))
    entity.add_observation(
        Observation(
            frame_number=3,
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
            confidence=Score(0.9),
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
    page = ObjectPage(_ID, {"Raw": False})
    qtbot.addWidget(page)
    page.resize(200, 400)  # Too narrow for the whole id
    page.show()
    page.update_scene(scene, 0)
    fields = page.findChild(FieldsView)
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
