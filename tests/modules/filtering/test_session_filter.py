"""Session filtering behavior shared by display and export."""

from ax_devil.modules.filtering import FilterConfig, FilterOption
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Entity, EntityId, Scene, TimeSlice


def test_detached_snapshot_keeps_decoder_predicates_and_search_after_session_edits() -> None:
    config = FilterConfig(
        options=(
            FilterOption(
                id="tracks", label="Tracks", predicate=lambda entity, state: str(entity.id).startswith("Track")
            ),
            FilterOption(id="other", label="Other", predicate=lambda entity, state: str(entity.id).startswith("other")),
        )
    )
    model = SessionFilter(config)
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    scene.add_entity(Entity(id=EntityId("Track-A")))
    scene.add_entity(Entity(id=EntityId("Track-B")))
    scene.add_entity(Entity(id=EntityId("other-A")))
    model.set_enabled("tracks", False)
    model.set_id_query(" -a ")
    snapshot = model.snapshot()

    model.set_enabled("tracks", True)
    model.set_id_query("B")
    assert list(model.process_scene(scene).entities) == ["Track-B"]
    assert list(snapshot.process_scene(scene).entities) == ["other-A"]
    assert not snapshot.supports_history_filtering

    model.toggle_all()
    assert not model.process_scene(scene).entities
    assert list(snapshot.process_scene(scene).entities) == ["other-A"]
