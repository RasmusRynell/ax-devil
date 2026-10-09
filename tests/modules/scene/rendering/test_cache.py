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
    NormalizedPoint,
    Observation,
    Polygon,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering.cache import CachedSceneOverlay, ReportedCatalogErrors
from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalog, get_built_in_scene_render_catalog
from ax_devil.modules.video_player.engine.data_types import DrawingBuildReason
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings
from ax_devil.modules.video_player.engine.render_context import RenderContext


class _EmptySceneFilter(SessionFilter):
    """Filter adapter returning a caller-controlled Scene."""

    def process_scene(self, scene: Scene) -> Scene:
        """Return an empty Scene even though filter state allows the source entity."""
        return Scene(time_slice=scene.time_slice)


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


def test_cached_scene_overlay_reuses_prepared_drawing_for_same_identity() -> None:
    scene = _motion_scene()
    overlay = CachedSceneOverlay(scene=scene)
    context = RenderContext.create(640, 480)

    first = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    second = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))

    assert second is first


def test_cached_scene_overlay_misses_when_context_size_changes() -> None:
    scene = _motion_scene()
    overlay = CachedSceneOverlay(scene=scene)

    first = overlay.prepare_drawing(
        RenderContext.create(640, 480), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(640, 480)))
    )
    second = overlay.prepare_drawing(
        RenderContext.create(1280, 720), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(1280, 720)))
    )

    assert second is not first


def test_cached_scene_overlay_misses_when_filter_state_changes_and_hover_tracks_filter() -> None:
    scene = _human_scene()
    filter_config = build_default_filter_config()
    filter_state = SessionFilter(filter_config)
    overlay = CachedSceneOverlay(scene=scene, scene_filter=filter_state)
    context = RenderContext.create(640, 480)

    drawing_before = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(drawing_before) > 0
    assert overlay.hit_test(0.2, 0.3) is not None

    filter_state.set_enabled("show_humans", False)

    drawing_after = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert drawing_after is not drawing_before
    assert len(drawing_after) == 0
    assert overlay.hit_test(0.2, 0.3) is None


def test_cached_scene_overlay_uses_filter_adapter_process_scene() -> None:
    scene = _human_scene()
    overlay = CachedSceneOverlay(scene=scene, scene_filter=_EmptySceneFilter())

    primitives = overlay.prepare_drawing(
        RenderContext.create(640, 480), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(640, 480)))
    )

    assert not primitives
    assert overlay.latest_drawing_preparation_metrics().filtered_entity_count == 0


def test_cached_scene_overlay_raises_when_catalog_rendering_fails() -> None:
    scene = _human_scene()
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


def test_cached_scene_overlay_vehicle_filter_removes_primitives_and_pinned_hover() -> None:
    scene = _vehicle_scene()
    filter_config = build_default_filter_config()
    filter_state = SessionFilter(filter_config)
    overlay = CachedSceneOverlay(scene=scene, scene_filter=filter_state)
    context = RenderContext.create(640, 480)

    drawing_before = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(drawing_before) > 0
    assert overlay.hit_test(0.2, 0.3) is not None
    assert overlay.get_hit_by_id("vehicle-1") is not None

    filter_state.set_enabled("show_vehicles", False)

    drawing_after = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert drawing_after is not drawing_before
    assert len(drawing_after) == 0
    assert overlay.hit_test(0.2, 0.3) is None
    assert overlay.get_hit_by_id("vehicle-1") is None


def test_cached_scene_overlay_head_filter_removes_primitives_and_pinned_hover() -> None:
    scene = _head_scene()
    filter_config = build_default_filter_config()
    filter_state = SessionFilter(filter_config)
    overlay = CachedSceneOverlay(scene=scene, scene_filter=filter_state)
    context = RenderContext.create(640, 480)

    drawing_before = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(drawing_before) > 0
    assert overlay.hit_test(0.2, 0.3) is not None
    assert overlay.get_hit_by_id("head-1") is not None

    filter_state.set_enabled("show_heads", False)

    drawing_after = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert drawing_after is not drawing_before
    assert len(drawing_after) == 0
    assert overlay.hit_test(0.2, 0.3) is None
    assert overlay.get_hit_by_id("head-1") is None


def test_cached_scene_overlay_pinned_hover_lookup_tracks_filter_state() -> None:
    scene = _human_scene()
    filter_config = build_default_filter_config()
    filter_state = SessionFilter(filter_config)
    overlay = CachedSceneOverlay(scene=scene, scene_filter=filter_state)

    visible_hit = overlay.get_hit_by_id("human-1")
    assert visible_hit is not None

    filter_state.set_enabled("show_humans", False)

    assert overlay.get_hit_by_id("human-1") is None


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


def test_cached_scene_overlay_reports_drawing_preparation_metrics() -> None:
    scene = _polygon_scene()
    overlay = CachedSceneOverlay(scene=scene)

    primitives = overlay.prepare_drawing(
        RenderContext.create(640, 480), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(640, 480)))
    )
    metrics = overlay.latest_drawing_preparation_metrics()

    assert sum(count for _, count in metrics.primitive_counts) == len(primitives)
    assert dict(metrics.primitive_counts).get("text", 0) == len(primitives.texts)
    assert dict(metrics.primitive_counts).get("polygon", 0) == 1
    assert metrics.filtered_entity_count == 1
    assert metrics.filter_time_ms is not None and metrics.filter_time_ms >= 0.0
    assert metrics.generation_time_ms is not None and metrics.generation_time_ms >= 0.0


def _motion_scene(*, motion_state: MotionState = MotionState.Moving) -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("motion-1"), motion_state=motion_state)
    entity.add_observation(Observation(geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4), frame_number=0))
    scene.add_entity(entity)
    return scene


def _polygon_scene() -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("poly-1"))
    entity.add_observation(
        Observation(
            geometry=Polygon(
                points=[
                    NormalizedPoint(0.1, 0.1),
                    NormalizedPoint(0.2, 0.1),
                    NormalizedPoint(0.2, 0.2),
                ]
            ),
            frame_number=0,
        )
    )
    scene.add_entity(entity)
    return scene


def _human_scene() -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("human-1"))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
            classification=[
                Classification(type=KnownClassificationType.Human.value, score=Score(0.9)),
            ],
            frame_number=0,
        )
    )
    scene.add_entity(entity)
    return scene


def _vehicle_scene() -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("vehicle-1"))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
            classification=[
                Classification(type=KnownClassificationType.Vehicle.value, score=Score(0.9)),
            ],
            frame_number=0,
        )
    )
    scene.add_entity(entity)
    return scene


def _head_scene() -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("head-1"))
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
            classification=[
                Classification(type=KnownClassificationType.Head.value, score=Score(0.9)),
            ],
            frame_number=0,
        )
    )
    scene.add_entity(entity)
    return scene


def test_filter_work_before_paint_is_reported_once_and_cache_hits_have_no_duration() -> None:
    scene = _human_scene()
    overlay = CachedSceneOverlay(scene=scene, scene_filter=_EmptySceneFilter())
    context = RenderContext.create(640, 480)
    overlay.filtered_scene()  # Presenter/inspector does this before the paint handler.
    overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    first = overlay.latest_drawing_preparation_metrics()
    assert first.filter_time_ms is not None
    assert first.generation_time_ms is not None
    assert not first.filter_cache_hit
    assert not first.drawing_cache_hit
    assert first.input_entity_count == 1
    assert first.filtered_entity_count == 0
    overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    second = overlay.latest_drawing_preparation_metrics()
    assert second.filter_time_ms is None
    assert second.generation_time_ms is None
    assert second.filter_cache_hit
    assert second.drawing_cache_hit
    overlay.prepare_drawing(
        RenderContext.create(1280, 720), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(1280, 720)))
    )
    resized = overlay.latest_drawing_preparation_metrics()
    assert resized.filter_cache_hit
    assert resized.generation_time_ms is not None
    assert not resized.drawing_cache_hit


def _build_reasons(overlay: CachedSceneOverlay) -> tuple[DrawingBuildReason, ...]:
    # A call stops mypy narrowing the attribute after one assertion and flagging the next as unreachable.
    return overlay.latest_metrics.build_reasons


def test_primitive_build_reasons_follow_changed_inputs_and_clear_on_reuse() -> None:
    """Explain rebuilds even when several render inputs change between retrievals."""

    scene = _human_scene()
    filter_config = build_default_filter_config()
    filter_state = SessionFilter(filter_config)
    overlay = CachedSceneOverlay(scene=scene, scene_filter=filter_state)
    context = RenderContext.create(640, 480)
    first = overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert _build_reasons(overlay) == (DrawingBuildReason.INITIAL,)
    assert overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context))) is first
    assert _build_reasons(overlay) == ()
    assert overlay.latest_metrics.generation_time_ms is None
    overlay.prepare_drawing(
        RenderContext.create(1280, 720), DrawingBuffer(DrawingSettings.for_context(RenderContext.create(1280, 720)))
    )
    assert _build_reasons(overlay) == (DrawingBuildReason.TARGET,)
    filter_state.set_enabled("show_humans", False)
    overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert _build_reasons(overlay) == (DrawingBuildReason.FILTER, DrawingBuildReason.TARGET)
    overlay.prepare_drawing(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert overlay.latest_metrics.drawing_cache_hit
    assert _build_reasons(overlay) == ()


def test_hover_formats_only_selected_entity_and_reuses_its_card(monkeypatch: pytest.MonkeyPatch) -> None:
    from ax_devil.modules.scene.rendering import cache as module

    calls: list[EntityId] = []

    def format_card(entity: Entity) -> str:
        calls.append(entity.id)
        return f"card:{entity.id}"

    monkeypatch.setattr(module, "build_entity_hover_html", format_card)
    scene = _motion_scene()
    overlay = CachedSceneOverlay(scene=scene)
    assert overlay.hit_test(-1, -1) is None
    assert calls == []
    entity_id = next(iter(scene.entities))
    first = overlay.get_hit_by_id(str(entity_id))
    assert first is not None
    assert first.card_html == f"card:{entity_id}"
    assert overlay.get_hit_by_id(str(entity_id)) is first
    assert calls == [entity_id]


@pytest.mark.parametrize(
    "changed",
    [
        DrawingSettings(640.5, 480, 480),
        DrawingSettings(640, 480, 479.5),
        DrawingSettings(640, 480, 480, dpi=120),
        DrawingSettings(640, 480, 480, dpr=1.5),
        DrawingSettings(640, 480, 480, hardware=True),
        DrawingSettings(640, 480, 480, viewport=(-200.0, 0.0, 640.0, 480.0)),
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


def test_cached_scene_overlay_tracks_search_and_toggle_all_without_new_scene() -> None:
    scene = _human_scene()
    model = SessionFilter(build_default_filter_config())
    overlay = CachedSceneOverlay(scene=scene, scene_filter=model)
    context = RenderContext.create(640, 480)
    settings = DrawingSettings.for_context(context)
    first = overlay.prepare_drawing(context, DrawingBuffer(settings))
    assert len(first) > 0

    model.set_id_query("missing")
    assert len(overlay.prepare_drawing(context, DrawingBuffer(settings))) == 0
    assert overlay.hit_test(0.2, 0.3) is None

    model.set_id_query("")
    assert len(overlay.prepare_drawing(context, DrawingBuffer(settings))) > 0
    assert overlay.hit_test(0.2, 0.3) is not None

    model.toggle_all()
    assert len(overlay.prepare_drawing(context, DrawingBuffer(settings))) == 0
    assert overlay.hit_test(0.2, 0.3) is None
