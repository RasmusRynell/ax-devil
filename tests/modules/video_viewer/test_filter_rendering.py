"""Render-time filtering behaviour for overlay drawing preparation.

These tests exercise the draw-filter integration introduced when filtering
moved from controller-owned caches to the renderer. They ensure that:
  * Drawing generators pull the latest filter state at paint time, so changing
    a filter immediately affects existing scenes without new sync results.
  * Frame displays expose a lightweight refresh hook that controllers use to
    trigger repainting instead of rebuilding frames.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.filtering import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    KnownClassificationType,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings
from ax_devil.modules.video_player.engine.render_context import RenderContext
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.media_tools import EntityFilterWidget
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter


def _build_scene(entity_id: str) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId(entity_id))
    observation = Observation(
        geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
        classification=[Classification(type=KnownClassificationType.Human.value, score=Score(0.9))],
        frame_number=0,
        timestamp=datetime.now(timezone.utc),
    )
    entity.add_observation(observation)
    scene.add_entity(entity)
    return scene


def _build_frame_and_overlay(sequence: int, scene: Scene | None) -> tuple[FrameData, OverlayData | None]:
    frame_id = FrameIdentifier(sequence_id=sequence, timestamp_monotime_us=float(sequence * 1_000_000))
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0)

    frame = FrameData(frame_id=frame_id, content=image, source_id="test")
    overlay = OverlayData(frame_id=frame_id, content=scene, source_id="test") if scene is not None else None
    return frame, overlay


def test_drawing_preparation_responds_to_filter(qtbot: QtBot) -> None:
    """The drawing generator should reflect the current FilterState on each invocation."""
    base_config = build_default_filter_config()
    model = SessionFilter(base_config)
    widget = EntityFilterWidget(filter_model=model)
    qtbot.addWidget(widget)

    scene = _build_scene("entity-1")
    frame_and_overlay = _build_frame_and_overlay(1, scene)

    converted = SceneFramePresenter(scene_filter=model).prepare_frame(*frame_and_overlay).display_frame
    assert converted.overlays is not None

    generator = converted.overlays.drawing_generator
    context = RenderContext.create(640, 480)

    drawing_before = generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(drawing_before) > 0

    assert model.is_enabled("show_humans")

    widget._checkboxes["show_humans"].setChecked(False)
    qtbot.waitUntil(lambda: not model.is_enabled("show_humans"))

    filtered_scene = model.process_scene(scene)
    assert not filtered_scene.entities

    drawing_after = generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(drawing_after) == 0


def test_hover_interaction_responds_to_filter(qtbot: QtBot) -> None:
    """Hover provider should track the same filter state used for prepared drawings."""
    base_config = build_default_filter_config()
    model = SessionFilter(base_config)
    widget = EntityFilterWidget(filter_model=model)
    qtbot.addWidget(widget)

    scene = _build_scene("entity-1")
    frame_and_overlay = _build_frame_and_overlay(1, scene)

    converted = SceneFramePresenter(scene_filter=model).prepare_frame(*frame_and_overlay).display_frame
    assert converted.overlays is not None
    assert converted.overlays.interaction_provider is not None

    provider = converted.overlays.interaction_provider
    assert provider.hit_test(0.2, 0.2) is not None

    widget._checkboxes["show_humans"].setChecked(False)
    qtbot.waitUntil(lambda: not model.is_enabled("show_humans"))

    assert provider.hit_test(0.2, 0.2) is None


def test_frame_display_refresh_overlays_calls_viewport(qtbot: QtBot) -> None:
    """`refresh_overlays` must delegate to the underlying video widget repaint."""
    display = FrameDisplay()
    qtbot.addWidget(display)

    viewport = display.viewport

    with patch.object(viewport, "refresh_last_frame", wraps=viewport.refresh_last_frame) as refresh_spy:
        display.refresh_overlays()

    assert refresh_spy.called


def test_hover_interaction_uses_latest_observation_for_multi_observation_entities() -> None:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("entity-1"))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
            classification=[],
            frame_number=0,
            timestamp=datetime.now(timezone.utc),
        )
    )
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.2, 0.2, 0.2, 0.2, allow_outside=True),
            classification=[],
            frame_number=1,
            timestamp=datetime.now(timezone.utc),
        )
    )
    scene.add_entity(entity)

    frame_and_overlay = _build_frame_and_overlay(1, scene)
    converted = SceneFramePresenter().prepare_frame(*frame_and_overlay).display_frame
    assert converted.overlays is not None
    provider = converted.overlays.interaction_provider
    assert provider is not None

    assert provider.hit_test(0.15, 0.15) is None
    latest_hit = provider.hit_test(0.25, 0.25)
    assert latest_hit is not None
    assert latest_hit.bounds == (0.2, 0.2, 0.2, 0.2)
