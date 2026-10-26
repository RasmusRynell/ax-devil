from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ax_devil.modules.filtering import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    KnownClassificationType,
    MotionState,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering.cache import CachedSceneOverlay, ReportedCatalogErrors
from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalog
from ax_devil.modules.scene.rendering.catalog_loader import get_built_in_scene_render_catalog
from ax_devil.modules.video_player.engine.quick.preparation import (
    DrawingBuffer,
    DrawingSettings,
    PreparedDrawing,
)
from ax_devil.modules.video_player.engine.render_context import RenderContext


class _FailingCatalog:
    """Catalog test double that fails during drawing preparation."""

    @property
    def rendering_identity(self) -> tuple[str, str]:
        """Return a stable rendering identity."""
        return ("failing", "catalog")

    def render_scene(
        self, scene: Scene, context: RenderContext, output: object, *, diagnostics: object = None
    ) -> list[object]:
        """Raise to simulate data-dependent recipe failure."""
        raise ValueError("bad recipe")


pytestmark = pytest.mark.usefixtures("qapp")


def test_cached_scene_overlay_raises_when_catalog_rendering_fails() -> None:
    scene = _classified_scene()
    overlay = CachedSceneOverlay(scene=scene, catalog=cast(SceneRenderCatalog, _FailingCatalog()))

    with pytest.raises(ValueError, match="bad recipe"):
        overlay.prepare_drawing(
            RenderContext.create(640, 480), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(640, 480)))
        )


def test_reported_catalog_errors_evict_least_recent_instead_of_silencing_new_errors() -> None:
    reported = ReportedCatalogErrors(limit=2)
    first, second, third = (("catalog", "code", f"$.recipes[{index}]") for index in range(3))

    assert reported.is_new(first)
    assert reported.is_new(second)
    assert not reported.is_new(first)
    assert reported.is_new(third)
    assert not reported.is_new(first)
    assert reported.is_new(second)


@pytest.mark.parametrize(
    "kind,option",
    [
        (KnownClassificationType.Human, "show_humans"),
        (KnownClassificationType.Vehicle, "show_vehicles"),
        (KnownClassificationType.Head, "show_heads"),
    ],
)
def test_cached_scene_overlay_filter_removes_primitives_and_hover(kind: KnownClassificationType, option: str) -> None:
    scene = _classified_scene(kind)
    filter_state = SessionFilter(build_default_filter_config())
    overlay = CachedSceneOverlay(scene=scene, scene_filter=filter_state)
    context = RenderContext.create(640, 480)

    drawing_before = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(drawing_before) > 0
    assert overlay.hit_test(0.2, 0.3) is not None
    assert overlay.get_hit_by_id("object-1") is not None

    filter_state.set_enabled(option, False)

    assert len(overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))) == 0
    assert overlay.hit_test(0.2, 0.3) is None
    assert overlay.get_hit_by_id("object-1") is None


def test_cached_scene_overlay_misses_when_catalog_identity_changes() -> None:
    scene = _motion_scene()
    catalog = get_built_in_scene_render_catalog()
    overlay = CachedSceneOverlay(scene=scene, catalog_provider=lambda: catalog)
    context = RenderContext.create(640, 480)

    first = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    catalog = replace(catalog)
    same_identity = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    catalog = replace(catalog, semantic_hash="test-changed-catalog")
    different_identity = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))

    assert same_identity is first
    assert different_identity is not first


def _motion_scene() -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("motion-1"), motion_state=MotionState.Moving)
    entity.add_observation(Observation(geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4), frame_number=0))
    scene.add_entity(entity)
    return scene


def _classified_scene(kind: KnownClassificationType = KnownClassificationType.Human) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("object-1"))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
            classification=[
                Classification(type=kind.value, score=Score(0.9)),
            ],
            frame_number=0,
        )
    )
    scene.add_entity(entity)
    return scene


def test_hover_hit_summarizes_the_entity_once_and_omits_what_the_decoder_did_not_report() -> None:
    overlay = CachedSceneOverlay(scene=_classified_scene())

    first = overlay.get_hit_by_id("object-1")

    assert first is not None
    assert (first.label, first.detail, first.color_key) == ("object-1 human", "conf 0.90", "human")  # No speed.
    assert overlay.get_hit_by_id("object-1") is first


@pytest.mark.parametrize(
    "changed",
    [
        DrawingSettings(640.5, 480, 480),
        DrawingSettings(640, 480, 479.5),
        DrawingSettings(640, 480, 480, dpi=120),
        DrawingSettings(640, 480, 480, dpr=1.5),
    ],
)
def test_final_drawing_cache_includes_every_surface_input(changed: DrawingSettings) -> None:
    """Native context dimensions alone cannot identify prepared output."""
    context = RenderContext.create(640, 480)
    settings = DrawingSettings.for_context(context)
    overlay = CachedSceneOverlay(scene=_motion_scene())
    first = overlay.prepare_drawing(context, DrawingBuffer(settings))
    second = overlay.prepare_drawing(context, DrawingBuffer(changed))
    assert second is not first
    assert overlay.prepare_drawing(context, DrawingBuffer(changed)) is second


def test_a_context_of_other_dimensions_is_not_served_the_drawing_prepared_for_another() -> None:
    """Catalog lengths resolve against the context, so a native export cannot reuse a scaled viewer drawing."""
    settings = DrawingSettings(500, 250, 250, dpr=2.0)
    overlay = CachedSceneOverlay(scene=_motion_scene())
    viewer, export = RenderContext.create(500, 250), RenderContext.create(1000, 500)

    first = overlay.prepare_drawing(viewer, DrawingBuffer(settings))

    assert overlay.prepare_drawing(export, DrawingBuffer(settings)) is not first


def test_panning_within_the_culling_margin_reuses_the_drawing() -> None:
    """Pan keeps the prepared drawing until the viewport leaves the margin prepared around it."""
    context = RenderContext.create(1000, 400)
    overlay = CachedSceneOverlay(scene=_motion_scene())

    def prepare(left: float) -> PreparedDrawing:
        return overlay.prepare_drawing(
            context, DrawingBuffer(DrawingSettings(1000, 400, 400, viewport=(left, 0.0, 200.0, 400.0)))
        )

    first = prepare(400.0)
    assert prepare(320.0) is first and prepare(500.0) is first
    moved = prepare(150.0)
    assert moved is not first


def test_cached_scene_overlay_tracks_toggle_all_without_new_scene() -> None:
    scene = _classified_scene()
    model = SessionFilter(build_default_filter_config())
    overlay = CachedSceneOverlay(scene=scene, scene_filter=model)
    context = RenderContext.create(640, 480)
    settings = DrawingSettings.for_context(context)
    first = overlay.prepare_drawing(context, DrawingBuffer(settings))
    assert len(first) > 0
    assert overlay.hit_test(0.2, 0.3) is not None

    model.toggle_all()
    assert len(overlay.prepare_drawing(context, DrawingBuffer(settings))) == 0
    assert overlay.hit_test(0.2, 0.3) is None


@pytest.mark.parametrize("initially_ready", [False, True])
def test_hover_targets_follow_catalog_readiness_even_with_a_cached_hit_index(initially_ready: bool) -> None:
    """An overlay waiting for its catalog offers neither invisible hover targets nor a pinned entity card."""
    ready_catalog = get_built_in_scene_render_catalog()
    catalog: SceneRenderCatalog | None = ready_catalog if initially_ready else None
    overlay = CachedSceneOverlay(scene=_classified_scene(), catalog_provider=lambda: catalog)
    context = RenderContext.create(640, 480)
    settings = DrawingSettings.for_context(context)
    if initially_ready:
        assert overlay.hit_test(0.2, 0.3) is not None
        assert overlay.get_hit_by_id("object-1") is not None
        catalog = None

    assert len(overlay.prepare_drawing(context, DrawingBuffer(settings))) == 0
    assert overlay.hit_test(0.2, 0.3) is None
    assert overlay.get_hit_by_id("object-1") is None

    catalog = ready_catalog

    assert len(overlay.prepare_drawing(context, DrawingBuffer(settings))) > 0
    assert overlay.hit_test(0.2, 0.3) is not None
    assert overlay.get_hit_by_id("object-1") is not None
