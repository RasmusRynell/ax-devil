"""Visibility removes selected output and its inputs before frame execution."""

import json
from pathlib import Path
from typing import Any

import pytest

from ax_devil.modules.scene.model import BoundingBox, Entity, EntityId, Observation, Scene, TimeSlice
from ax_devil.modules.scene.rendering import (
    OverlayFeature,
    OverlayVisibility,
    SceneRenderCatalogLoader,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATHS, MAX_VISIBILITY_VARIANTS
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic, TemplateRuntimeError
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.catalog_helpers import catalog_document
from tests.drawing_helpers import PointCall, record_scene


def _scene() -> Scene:
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("object"))
    entity.add_observation(Observation(geometry=BoundingBox.from_xywh(0, 0, 0, 0.1), frame_number=0))
    scene.add_entity(entity)
    return scene


def _document() -> dict[str, Any]:
    document = catalog_document()
    document["templates"]["marker"]["feature"] = "confidence"
    document["templates"]["wrapper"] = {
        "parameters": {"x": {"type": "number", "required": True}},
        "steps": [{"template": "marker", "inputs": {"x": {"ref": ["parameters", "x"]}}}],
    }
    recipe = document["recipes"]["fallbacks"][0]
    recipe["values"] = {"shared": 0.4}
    recipe["steps"] = [
        {"primitive": "point", "fields": {"position": {"x": {"ref": ["values", "shared"]}, "y": 0.5}}},
        {
            "template": "wrapper",
            "inputs": {
                "x": {
                    "call": "div",
                    "args": {"numerator": 1, "denominator": {"ref": ["scene", "observation", "geometry", "w"]}},
                }
            },
        },
    ]
    return document


def test_hidden_nested_invocation_never_evaluates_its_failing_inputs() -> None:
    revision = SceneRenderCatalogLoader().validate_revision(_document())
    scene = _scene()
    context = RenderContext.create(640, 480)
    diagnostics: list[CatalogDiagnostic] = []
    assert record_scene(revision.catalog, scene, context, diagnostics=diagnostics) == []
    assert diagnostics
    variant = revision.specialize(OverlayVisibility(frozenset({OverlayFeature.CONFIDENCE})))
    diagnostics.clear()
    output = record_scene(variant, scene, context, diagnostics=diagnostics)
    assert diagnostics == []
    assert [call.x for call in output if isinstance(call, PointCall)] == [0.4]


def test_partially_hidden_template_drops_inputs_used_only_by_hidden_steps() -> None:
    document = _document()
    marker = document["templates"]["marker"]
    del marker["feature"]
    marker["steps"][0]["feature"] = "confidence"
    revision = SceneRenderCatalogLoader().validate_revision(document)
    diagnostics: list[CatalogDiagnostic] = []
    output = record_scene(
        revision.specialize(OverlayVisibility(frozenset({OverlayFeature.CONFIDENCE}))),
        _scene(),
        RenderContext.create(640, 480),
        diagnostics=diagnostics,
    )
    assert diagnostics == []
    assert [call.x for call in output if isinstance(call, PointCall)] == [0.4, 0.75]


def test_fully_hidden_recipe_does_not_evaluate_its_guard() -> None:
    document = _document()
    recipe = document["recipes"]["fallbacks"][0]
    recipe["steps"] = recipe["steps"][1:]
    recipe["enabled"] = {
        "call": "gt",
        "args": {
            "left": {
                "call": "div",
                "args": {"numerator": 1, "denominator": {"ref": ["scene", "observation", "geometry", "w"]}},
            },
            "right": 0,
        },
    }
    revision = SceneRenderCatalogLoader().validate_revision(document)
    context = RenderContext.create(640, 480)
    diagnostics: list[CatalogDiagnostic] = []
    assert record_scene(revision.catalog, _scene(), context, diagnostics=diagnostics) == []
    assert diagnostics
    diagnostics.clear()
    variant = revision.specialize(OverlayVisibility(frozenset({OverlayFeature.CONFIDENCE})))
    assert record_scene(variant, _scene(), context, diagnostics=diagnostics) == []
    assert diagnostics == []


@pytest.mark.parametrize("invalid", ["feature", "input"])
def test_hidden_components_are_still_validated(invalid: str) -> None:
    document = _document()
    marker = document["templates"]["marker"]
    marker["steps"][0]["visible"] = False
    if invalid == "feature":
        marker["feature"] = "unknown"
    else:
        marker["steps"][0]["fields"]["position"]["x"] = {"ref": ["parameters", "unknown"]}
    with pytest.raises(TemplateRuntimeError):
        SceneRenderCatalogLoader().validate_revision(document)


def test_unused_and_permanently_hidden_templates_do_not_enable_controls() -> None:
    document = catalog_document()
    document["templates"]["unused"] = {"feature": "confidence", "steps": [{"template": "marker"}]}
    document["templates"]["hidden"] = {
        "feature": "movement",
        "steps": [{"template": "marker", "visible": False}],
    }
    document["templates"]["switched_off"] = {"enabled": False, "steps": [{"template": "marker"}]}
    document["templates"]["calls_switched_off"] = {"steps": [{"template": "switched_off"}]}
    recipe = document["recipes"]["fallbacks"][0]
    recipe["steps"].extend(
        [
            {"template": "hidden"},
            {"template": "unused", "visible": False},
            {"template": "marker", "feature": "attributes", "enabled": False},
            {"template": "marker", "feature": "relations", "enabled": {"literal": False}},
            {"template": "switched_off", "feature": "confidence"},
            {"template": "calls_switched_off", "feature": "speed"},
        ]
    )
    document["templates"]["marker"]["steps"][0]["feature"] = "ids"
    catalog = SceneRenderCatalogLoader().validate_document(document)
    assert catalog.supported_features == frozenset({OverlayFeature.IDS})


def test_selection_choices_survive_catalog_changes_and_failed_reload(tmp_path: Path) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    path = manager.create_catalog("Copy").path
    selection = manager.create_selection()
    other = manager.create_selection()
    selection.select_catalog(path)
    selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
    hidden = selection.active_catalog()
    assert hidden is not None and other.active_catalog() is not hidden
    path.write_text("broken", encoding="utf-8")
    selection.reload_active_catalog()
    assert not selection.active_catalog_loaded()
    assert selection.active_catalog() is hidden
    selection.set_feature_enabled(OverlayFeature.CONFIDENCE, True)
    restored = selection.active_catalog()
    assert restored is not None and restored.rendering_identity != hidden.rendering_identity
    assert not selection.active_catalog_loaded()
    selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
    assert selection.active_catalog() is hidden
    minimal = next(path for path in BUILT_IN_CATALOG_PATHS if path.stem == "minimal")
    manager.apply_to_all(minimal)
    assert OverlayFeature.CONFIDENCE in selection.visibility.disabled
    assert OverlayFeature.CONFIDENCE not in selection.available_features()
    assert other.visibility == OverlayVisibility()
    manager.apply_to_all(manager.built_in_catalog_path())
    assert selection.active_catalog() is not other.active_catalog()
    other.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
    assert selection.active_catalog() is other.active_catalog()
    selection.reset_visibility()
    assert selection.active_catalog() is manager.load_catalog(manager.built_in_catalog_path())


def test_valid_reload_reapplies_choices_and_can_toggle_without_files(tmp_path: Path) -> None:
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    path = manager.create_catalog("Copy").path
    selection = manager.create_selection()
    selection.select_catalog(path)
    selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
    document = SceneRenderCatalogLoader.read_document(path)
    document["templates"]["id_tab"]["parameters"]["font_size"]["default"]["value"] = 15
    path.write_text(json.dumps(document), encoding="utf-8")
    selection.reload_active_catalog()
    assert selection.active_catalog_loaded()
    assert OverlayFeature.CONFIDENCE in selection.visibility.disabled
    path.unlink()
    selection.reset_visibility()
    assert selection.active_catalog_loaded()  # No file load occurs on a toggle.
    assert selection.active_catalog() is not None


@pytest.mark.parametrize("path", BUILT_IN_CATALOG_PATHS, ids=lambda path: path.stem)
def test_packaged_catalogs_hide_all_tagged_output(path: Path) -> None:
    from ax_devil.modules.catalog_viewer.sheets import sheets

    revision = SceneRenderCatalogLoader().load_revision(path)
    variant = revision.specialize(OverlayVisibility(frozenset(OverlayFeature)))
    for sheet in sheets(revision.document):
        diagnostics: list[CatalogDiagnostic] = []
        assert record_scene(variant, sheet.scene, RenderContext.create(1280, 720), diagnostics=diagnostics) == []
        assert diagnostics == []


def test_class_name_does_not_extract_hidden_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Classification text must not extract the score column when only confidence needed it."""
    from ax_devil.modules.scene.model import Classification, Score
    from tests.catalog_helpers import CLASSIC_CATALOG_PATH
    from tests.drawing_helpers import RecordingTarget, TextCall

    revision = SceneRenderCatalogLoader().load_revision(CLASSIC_CATALOG_PATH)
    variant = revision.specialize(OverlayVisibility(frozenset({OverlayFeature.CONFIDENCE, OverlayFeature.OUTLINES})))
    entity = Entity(id=EntityId("object"))
    classification = Classification("custom", Score(0.8))
    observation = Observation(geometry=BoundingBox.from_xywh(0.1, 0.1, 0.3, 0.5), frame_number=0)
    rows = [(entity, observation, classification)]

    def unavailable_score(_score: Score) -> float:
        raise AssertionError("Score column should not be extracted.")

    monkeypatch.setattr(Score, "value", property(unavailable_score))
    target = RecordingTarget()
    variant.fallback_recipes["classified"].render_rows(rows, RenderContext.create(640, 480), target)
    assert [call.text for call in target.calls if isinstance(call, TextCall)] == ["object", "custom"]
    with pytest.raises(AssertionError, match="Score column"):
        revision.catalog.fallback_recipes["classified"].render_rows(
            rows, RenderContext.create(640, 480), RecordingTarget()
        )


def test_revision_reuses_recent_variants_and_recompiles_evicted_ones() -> None:
    from tests.catalog_helpers import CLASSIC_CATALOG_PATH

    revision = SceneRenderCatalogLoader().load_revision(CLASSIC_CATALOG_PATH)
    features = sorted(revision.catalog.supported_features)
    choices = [OverlayVisibility(frozenset({feature})) for feature in features]
    choices.append(OverlayVisibility(frozenset(features[:2])))
    assert len(choices) > MAX_VISIBILITY_VARIANTS
    first = revision.specialize(choices[0])
    variants = [revision.specialize(choice) for choice in choices[1:]]
    assert revision.specialize(choices[-1]) is variants[-1]
    rebuilt = revision.specialize(choices[0])
    assert rebuilt is not first
    assert rebuilt.rendering_identity == first.rendering_identity
