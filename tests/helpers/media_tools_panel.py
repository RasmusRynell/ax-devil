"""Builders and lookups for media tools panel tests: scenes, histories, the panel and its Objects list."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.core.time_units import seconds_to_us
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.filtering.filter_config import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
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
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.page_stack import ObjectPageKey
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView


def make_frame(index: int) -> FrameIdentifier:
    return FrameIdentifier(sequence_id=index, timestamp_us=seconds_to_us(index))


def make_entity(entity_id: str, kind: str = "human", score: float = 0.9) -> Entity:
    entity = Entity(EntityId(entity_id))
    geometry = BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True)
    classification = [Classification(kind, Score(score))]
    entity.add_observation(Observation(frame_number=0, geometry=geometry, classification=classification))
    return entity


def make_scene(*entities: Entity) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity in entities:
        scene.add_entity(entity)
    return scene


def make_history(
    *objects: tuple[str, str, tuple[tuple[int, int], ...]], frames: int = 10, events: tuple[FrameEvent, ...] = ()
) -> SceneHistory:
    times = tuple(index * 1_000_000 for index in range(frames))
    return SceneHistory(
        events=events,
        objects=tuple(ObjectHistory(entity_id, (kind,), spans, times) for entity_id, kind, spans in objects),
        frame_count=frames,
    )


def make_panel(
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


def objects_view(panel: MediaToolsPanel) -> RowTableView | None:
    """Return the table the Objects page is showing (it keeps one per scope), or None when it shows a message."""
    return next((view for view in panel.objects_page.findChildren(RowTableView) if view.isVisible()), None)


def listed_ids(panel: MediaToolsPanel) -> list[str]:
    view = objects_view(panel)
    model = view.rows_model if view is not None else None
    return [] if model is None else [cast(ObjectPageKey, model.key_at(n)).entity_id for n in range(model.rowCount())]


def search_objects(panel: MediaToolsPanel, text: str) -> None:
    edit = panel.objects_page.findChild(QLineEdit)
    assert edit is not None
    edit.setText(text)
    QTest.keyClick(edit, Qt.Key.Key_Return)


def click_object(panel: MediaToolsPanel, entity_id: str) -> None:
    """Click the row of *entity_id* in the Objects list."""
    view = objects_view(panel)
    assert view is not None
    click_row(view, ObjectPageKey(entity_id))


def click_row(view: RowTableView, key: Hashable) -> None:
    view.scroll_to_key(key)
    rect = view.visualRect(view.rows_model.index(view.rows_model.row_of(key), 0))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def find_button(panel: MediaToolsPanel, label: str) -> SmallButton | None:
    """Return the visible button of the panel with this text or tooltip."""
    buttons = [
        b for b in panel.findChildren(SmallButton) if (label in b.text() or label in b.toolTip()) and b.isVisible()
    ]
    return buttons[0] if buttons else None


def click_button(panel: MediaToolsPanel, label: str) -> None:
    button = find_button(panel, label)
    assert button is not None, label
    button.click()


def record_signal(signal: Any) -> list[Any]:
    """Return a list that collects what *signal* emits from now on."""
    seen: list[Any] = []
    signal.connect(seen.append)
    return seen
