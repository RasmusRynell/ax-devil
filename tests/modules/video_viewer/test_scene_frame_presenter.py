"""Tests for Scene frame presentation assembly."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from importlib import resources
from typing import Any, cast

import pytest
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
from ax_devil.modules.scene.rendering.catalog import (
    SceneRenderCatalog,
    SceneRenderCatalogLoader,
    get_built_in_scene_render_catalog,
)
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter


class _FailingSceneFilter(SessionFilter):
    """Filter test double that raises during Scene processing."""

    def process_scene(self, scene: Scene) -> Scene:
        """Raise to simulate a filter failure."""
        raise RuntimeError("filter exploded")


class _CountingSceneFilter(SessionFilter):
    """Filter test double that records Scene projection calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def process_scene(self, scene: Scene) -> Scene:
        """Return the source Scene and record the projection call."""
        self.calls += 1
        return scene


pytestmark = pytest.mark.usefixtures("qapp")


def _build_scene(entity_id: str) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId(entity_id))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2, allow_outside=True),
            classification=[Classification(type=KnownClassificationType.Human.value, score=Score(0.9))],
            frame_number=0,
            timestamp=datetime.now(timezone.utc),
        )
    )
    scene.add_entity(entity)
    return scene


def _build_frame(sequence: int) -> FrameData:
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0)
    return FrameData(
        frame_id=FrameIdentifier(sequence_id=sequence, timestamp_monotime_us=float(sequence * 1_000_000)),
        content=image,
        source_id="video",
        metadata={"frame": sequence},
    )


def _build_overlay(sequence: int, scene: Scene, metadata: dict[str, Any] | None = None) -> OverlayData:
    return OverlayData(
        frame_id=FrameIdentifier(sequence_id=sequence, timestamp_monotime_us=float(sequence * 1_000_000)),
        content=scene,
        source_id="overlay",
        metadata=metadata,
    )


def test_presenter_assembles_video_frame_with_scene_overlay_and_merged_metadata() -> None:
    scene = _build_scene("entity-1")
    overlay = _build_overlay(1, scene, metadata={"source": "decoder", "overlay_opacity": 0.2})
    presenter = SceneFramePresenter()

    presentation = presenter.prepare_frame(
        _build_frame(1),
        overlay,
        overlay_metadata={"overlay_opacity": 0.75, "presentation": "selected"},
    )

    assert presentation.display_frame.frame.frame_id == 1
    assert presentation.display_frame.frame.timestamp_monotime_us == 1_000_000.0
    assert presentation.display_frame.frame.metadata == {"frame": 1}
    assert presentation.display_frame.overlays is not None
    assert presentation.display_frame.overlays.overlay_id == 1
    assert presentation.display_frame.overlays.timestamp_monotime_us == 1_000_000.0
    assert presentation.display_frame.overlays.metadata == {
        "source": "decoder",
        "overlay_opacity": 0.75,
        "presentation": "selected",
    }


def test_presenter_applies_overlay_persistence_and_marks_reused_overlay() -> None:
    scene = _build_scene("entity-1")
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=5000, opacity=0.3))
    presenter = SceneFramePresenter()

    first = presenter.prepare_frame(_build_frame(1), _build_overlay(1, scene), overlay_policy=policy)
    second = presenter.prepare_frame(_build_frame(2), None, overlay_policy=policy)

    assert first.selected_overlay is not None
    assert first.overlay_reused is False
    assert second.selected_overlay is first.selected_overlay
    assert second.overlay_reused is True
    assert second.display_frame.overlays is not None
    assert second.display_frame.overlays.metadata == {
        "overlay_reused": True,
        "overlay_opacity": 0.3,
    }


def test_presenter_keeps_retained_drawings_and_hover_in_sync_with_filter_changes() -> None:
    """Changing the filter updates a retained overlay without requiring another video frame."""
    model = SessionFilter(build_default_filter_config())
    model.set_enabled("show_humans", False)
    scene = _build_scene("entity-1")
    presenter = SceneFramePresenter(scene_filter=model)
    presentation = presenter.prepare_frame(_build_frame(1), _build_overlay(1, scene))

    inspected_scene = presentation.inspection.scene
    assert inspected_scene is not None
    assert not inspected_scene.entities
    assert presentation.inspection.frame_id == FrameIdentifier(sequence_id=1, timestamp_monotime_us=1_000_000.0)
    assert presentation.inspection.metadata == {}
    overlay = presentation.display_frame.overlays
    assert overlay is not None
    provider = overlay.interaction_provider
    assert provider is not None
    context = RenderContext.create(640, 480)
    for enabled in (False, True, False):
        model.set_enabled("show_humans", enabled)
        drawing = overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
        assert bool(drawing) == enabled
        assert (provider.hit_test(0.2, 0.2) is not None) == enabled


def test_presenter_reuses_one_filtered_scene_for_inspection_rendering_and_hover() -> None:
    scene = _build_scene("entity-1")
    scene_filter = _CountingSceneFilter()
    presenter = SceneFramePresenter(scene_filter=scene_filter)

    presentation = presenter.prepare_frame(_build_frame(1), _build_overlay(1, scene))

    assert presentation.inspection.scene is scene
    assert scene_filter.calls == 1
    assert presentation.display_frame.overlays is not None
    presentation.display_frame.overlays.drawing_generator(
        RenderContext.create(640, 480), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(640, 480)))
    )
    assert presentation.display_frame.overlays.interaction_provider is not None
    presentation.display_frame.overlays.interaction_provider.hit_test(0.2, 0.2)
    assert scene_filter.calls == 1


def test_presenter_passes_scene_render_catalog_to_overlay() -> None:
    scene = _build_scene("entity-1")
    catalog = replace(get_built_in_scene_render_catalog(), catalog_id="test.catalog", semantic_hash="test-catalog")
    presenter = SceneFramePresenter(scene_render_catalog=catalog)

    presentation = presenter.prepare_frame(_build_frame(1), _build_overlay(1, scene))

    assert presentation.display_frame.overlays is not None
    cached_overlay = getattr(presentation.display_frame.overlays.drawing_generator, "__self__")
    assert cached_overlay._catalog is catalog


def test_presenter_updates_existing_overlay_catalog_before_next_frame() -> None:
    scene = _build_scene("entity-1")
    initial_catalog = get_built_in_scene_render_catalog()
    changed_catalog = _catalog_with_disabled_human_recipe()
    presenter = SceneFramePresenter(scene_render_catalog=initial_catalog)

    presentation = presenter.prepare_frame(_build_frame(1), _build_overlay(1, scene))

    assert presentation.display_frame.overlays is not None
    context = RenderContext.create(640, 480)
    initial_drawing = presentation.display_frame.overlays.drawing_generator(
        context, DrawingBuffer(DrawingSettings.for_context(context))
    )
    presenter.attach_scene_render_catalog(changed_catalog)
    updated_drawing = presentation.display_frame.overlays.drawing_generator(
        context, DrawingBuffer(DrawingSettings.for_context(context))
    )

    assert initial_drawing
    assert updated_drawing == PreparedDrawing()


def test_presenter_falls_back_to_unfiltered_inspector_scene_when_filtering_fails() -> None:
    scene = _build_scene("entity-1")
    presenter = SceneFramePresenter(scene_filter=_FailingSceneFilter())

    presentation = presenter.prepare_frame(_build_frame(1), _build_overlay(1, scene))

    assert presentation.display_frame.frame.frame_id == 1
    assert presentation.display_frame.overlays is not None
    assert presentation.inspection.scene is scene
    assert presentation.inspection.frame_id == FrameIdentifier(sequence_id=1, timestamp_monotime_us=1_000_000.0)
    assert presentation.inspection.metadata == {}


def _catalog_with_disabled_human_recipe() -> SceneRenderCatalog:
    document = deepcopy(_load_catalog_json("classic.json"))
    metadata = cast(dict[str, Any], document["metadata"])
    metadata["id"] = "test.disabled-human"
    metadata["name"] = "Disabled Human"
    recipes = cast(dict[str, Any], document["recipes"])
    classification_recipes = cast(list[dict[str, Any]], recipes["classifications"])
    for recipe in classification_recipes:
        if recipe["id"] == "human":
            recipe["enabled"] = False
            break
    else:
        raise AssertionError("Built-in catalog must contain a human recipe.")
    return SceneRenderCatalogLoader().validate_document(document)


def _load_catalog_json(filename: str) -> dict[str, Any]:
    path = (
        resources.files("ax_devil")
        .joinpath("modules")
        .joinpath("scene")
        .joinpath("rendering")
        .joinpath("catalog_definitions")
        .joinpath(filename)
    )
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    return cast(dict[str, Any], value)


def test_presenter_retains_preparation_only_for_the_same_source_sample() -> None:
    scene = _build_scene("entity-1")
    scene_filter = _CountingSceneFilter()
    presenter = SceneFramePresenter(scene_filter=scene_filter)
    context = RenderContext.create(640, 480)
    first = presenter.prepare_frame(_build_frame(1), _build_overlay(1, scene)).display_frame.overlays
    second = presenter.prepare_frame(_build_frame(2), _build_overlay(1, scene)).display_frame.overlays
    assert first is not None and second is not None
    primitives = first.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert second.interaction_provider is first.interaction_provider
    assert second.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context))) is primitives
    assert scene_filter.calls == 1

    resized = second.drawing_generator(
        RenderContext.create(320, 240), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(320, 240)))
    )
    assert resized is not primitives
    presenter.attach_scene_render_catalog(_catalog_with_disabled_human_recipe())
    assert second.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context))) == PreparedDrawing()

    presenter.attach_scene_filter(_CountingSceneFilter())
    third = presenter.prepare_frame(_build_frame(3), _build_overlay(1, scene)).display_frame.overlays
    assert third is not None and third.interaction_provider is not second.interaction_provider
    # Even the same Scene in a different source sample starts a fresh cache.
    fourth = presenter.prepare_frame(_build_frame(4), _build_overlay(2, scene)).display_frame.overlays
    assert fourth is not None and fourth.interaction_provider is not third.interaction_provider
    presenter.prepare_frame(_build_frame(5), None)
    assert presenter._cached_source is None and presenter._cached_overlay is None


def test_retained_overlay_does_not_keep_its_presenter_alive() -> None:
    from weakref import ref

    presenter = SceneFramePresenter()
    presentation = presenter.prepare_frame(_build_frame(1), _build_overlay(1, _build_scene("a")))
    owner = ref(presenter)
    del presenter
    assert owner() is None
    overlay = presentation.display_frame.overlays
    assert overlay is not None
    assert overlay.drawing_generator(
        RenderContext.create(640, 480), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(640, 480)))
    )


def test_visibility_change_reuses_filtered_scene_and_hover_cards(qtbot: QtBot) -> None:
    """Visibility replaces preparation for the retained sample without redoing inspection work."""
    from ax_devil.modules.scene.rendering import OverlayFeature, SceneRenderCatalogManager

    selection = SceneRenderCatalogManager().create_selection()
    scene_filter = _CountingSceneFilter()
    presenter = SceneFramePresenter(scene_filter=scene_filter, scene_render_catalog=selection.active_catalog())
    selection.activeCatalogChanged.connect(presenter.attach_scene_render_catalog)
    presentation = presenter.prepare_frame(_build_frame(1), _build_overlay(1, _build_scene("entity-1")))
    overlay = presentation.display_frame.overlays
    assert overlay is not None and overlay.interaction_provider is not None
    context = RenderContext.create(640, 480)
    initial = overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
    hit = overlay.interaction_provider.get_hit_by_id("entity-1")
    assert hit is not None
    selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
    hidden = overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context)))
    current_hit = overlay.interaction_provider.get_hit_by_id("entity-1")
    assert current_hit is not None and current_hit.card_html is hit.card_html
    assert len(hidden) < len(initial)
    assert scene_filter.calls == 1
    selection.reset_visibility()
    assert overlay.drawing_generator(context, DrawingBuffer(DrawingSettings.for_context(context))) == initial


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

    converted = SceneFramePresenter().prepare_frame(_build_frame(1), _build_overlay(1, scene)).display_frame
    assert converted.overlays is not None
    provider = converted.overlays.interaction_provider
    assert provider is not None

    assert provider.hit_test(0.15, 0.15) is None
    latest_hit = provider.hit_test(0.25, 0.25)
    assert latest_hit is not None
    assert latest_hit.bounds == (0.2, 0.2, 0.2, 0.2)
