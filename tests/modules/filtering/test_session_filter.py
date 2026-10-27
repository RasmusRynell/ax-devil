"""Session filtering behavior shared by display and export."""

from ax_devil.modules.filtering import FilterConfig, FilterOption
from ax_devil.modules.filtering.measure_range import Measure, MeasureRange
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import (
    BoundingBox,
    Classification,
    Confidence,
    Entity,
    EntityId,
    Observation,
    Scene,
    Score,
    TimeSlice,
)


def test_detached_snapshot_keeps_decoder_predicates_after_session_edits() -> None:
    config = FilterConfig(
        options=(
            FilterOption(
                id="tracks", label="Tracks", predicate=lambda entity, _state: str(entity.id).startswith("Track")
            ),
            FilterOption(
                id="other", label="Other", predicate=lambda entity, _state: str(entity.id).startswith("other")
            ),
        )
    )
    model = SessionFilter(config)
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    scene.add_entity(Entity(id=EntityId("Track-A")))
    scene.add_entity(Entity(id=EntityId("Track-B")))
    scene.add_entity(Entity(id=EntityId("other-A")))
    model.set_enabled("tracks", False)
    snapshot = model.snapshot()

    model.set_enabled("tracks", True)
    assert list(model.process_scene(scene).entities) == ["Track-A", "Track-B", "other-A"]
    assert list(snapshot.process_scene(scene).entities) == ["other-A"]
    assert not snapshot.supports_history_filtering

    model.toggle_all()
    assert not model.process_scene(scene).entities
    assert list(snapshot.process_scene(scene).entities) == ["other-A"]


def test_ranges_judge_only_reported_values_and_change_what_is_cached() -> None:
    def entity(name: str, score: float | None, confidence: float | None) -> Entity:
        classification = Classification(
            "human",
            Score(score) if score is not None else None,
            confidence=Confidence(confidence) if confidence is not None else None,
        )
        geometry = BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2)
        result = Entity(EntityId(name))
        result.add_observation(Observation(frame_number=0, geometry=geometry, classification=[classification]))
        return result

    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    for item in (entity("low", 0.3, 0.3), entity("high", 0.9, 0.9), entity("silent", None, None)):
        scene.add_entity(item)
    model = SessionFilter()
    unfiltered = model.cache_identity

    model.set_range(Measure.SCORE, MeasureRange(minimum=0.5))
    assert list(model.process_scene(scene).entities) == ["high", "silent"]  # A missing value is not read as 0.
    assert model.cache_identity != unfiltered
    model.set_range(Measure.CONFIDENCE, MeasureRange(maximum=0.5))
    assert list(model.process_scene(scene).entities) == ["silent"]
    assert list(model.snapshot().process_scene(scene).entities) == ["silent"]

    model.set_range(Measure.SCORE, MeasureRange())
    model.set_range(Measure.CONFIDENCE, MeasureRange())
    assert model.cache_identity == unfiltered
