from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

import ax_devil.modules.scene.rendering.catalog as catalog_module
from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATH,
    BUILT_IN_CATALOG_PATHS,
    SceneRenderCatalogLoader,
)
from ax_devil.modules.scene.rendering.catalog_manager import create_scene_render_catalog_manager
from ax_devil.modules.scene.rendering.catalog_store import SceneRenderCatalogStore
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.settings.paths import DEFAULT_RENDER_CATALOG_CHOICE_FILENAME
from tests.catalog_helpers import CLASSIC_CATALOG_PATH, catalog_document
from tests.drawing_helpers import record_scene


@pytest.mark.parametrize("path", BUILT_IN_CATALOG_PATHS, ids=lambda path: path.stem)
def test_catalog_loader_compiles_every_built_in_catalog(path: Path) -> None:
    catalog = SceneRenderCatalogLoader().load_path(path)

    assert catalog.catalog_id == f"built-in.{path.stem}"
    assert "unclassified" in catalog.fallback_recipes
    assert "classified" in catalog.fallback_recipes
    assert catalog.fallback_recipes["unclassified"].recipe_id == "motion"
    assert "human" in catalog.classification_recipes
    assert catalog.classification_recipes["human"].recipe_id == "human"
    assert catalog.classification_recipes["vehicle"] is catalog.classification_recipes["car"]
    assert catalog.relation_recipes["has_part"].recipe_id == "has_part"


def test_catalog_loader_accepts_catalog_without_relation_recipes() -> None:
    catalog_json = catalog_document()
    del _recipes(catalog_json)["relations"]

    catalog = SceneRenderCatalogLoader().validate_document(
        catalog_json,
    )

    assert catalog.relation_recipes == {}


@pytest.mark.parametrize("field,value", [("id", 123), ("schema_version", True)])
def test_catalog_loader_load_path_validates_structure(
    tmp_path: Path, field: str, value: object, small_catalog_path: Path
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    catalog_path = store.create_catalog("Copy", base_catalog_path=small_catalog_path).path
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    document["metadata"][field] = value
    catalog_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(TemplateRuntimeError, match=rf"metadata\.{field}"):
        SceneRenderCatalogLoader().load_path(catalog_path)


def test_catalog_loader_compile_reports_structural_errors() -> None:
    with pytest.raises(TemplateRuntimeError, match="(Unknown fields|Expected an object|not of type)"):
        SceneRenderCatalogLoader().validate_document(
            {"metadata": {"id": "broken", "name": "Broken", "schema_version": 3}},
        )

    document = catalog_document()
    _recipes(document)["fallbacks"][0]["bindings"] = []
    with pytest.raises(TemplateRuntimeError, match="(Unknown fields|Expected an object|not of type)"):
        SceneRenderCatalogLoader().validate_document(document)


@pytest.mark.parametrize("version", [1, 2, 99])
def test_catalog_loader_rejects_unsupported_versions(version: int) -> None:
    document = catalog_document()
    document["metadata"]["schema_version"] = version

    with pytest.raises(TemplateRuntimeError) as error:
        SceneRenderCatalogLoader().validate_document(document)

    assert error.value.diagnostic.code == "unsupported_version"
    assert error.value.diagnostic.location == "$.metadata.schema_version"


def test_catalog_loader_returns_read_only_compiled_mappings() -> None:
    catalog = SceneRenderCatalogLoader().validate_document(
        catalog_document(),
    )

    with pytest.raises(TypeError):
        cast(Any, catalog.fallback_recipes)["custom"] = catalog.fallback_recipes["unclassified"]
    with pytest.raises(TypeError):
        cast(Any, catalog.classification_recipes)["custom"] = catalog.classification_recipes["human"]
    with pytest.raises(TypeError):
        cast(Any, catalog.relation_recipes)["custom"] = catalog.relation_recipes["has_part"]
    with pytest.raises(TypeError):
        cast(Any, catalog.templates.programs)["custom"] = catalog.templates.programs["marker"]


def test_catalog_loader_rejects_duplicate_fallback_selectors() -> None:
    catalog_json = catalog_document()
    fallback_recipes = _fallback_recipes(catalog_json)
    duplicate_selector_recipe = deepcopy(fallback_recipes[0])
    duplicate_selector_recipe["id"] = "duplicate-fallback-selector"
    fallback_recipes.append(duplicate_selector_recipe)

    with pytest.raises(TemplateRuntimeError, match="Duplicate fallbacks selector"):
        SceneRenderCatalogLoader().validate_document(catalog_json)


def test_catalog_loader_rejects_duplicate_classification_selectors() -> None:
    catalog_json = catalog_document()
    classification_recipes = _classification_recipes(catalog_json)
    duplicate_selector_recipe = deepcopy(classification_recipes[0])
    duplicate_selector_recipe["id"] = "duplicate-classification-selector"
    classification_recipes.append(duplicate_selector_recipe)

    with pytest.raises(TemplateRuntimeError, match="Duplicate classifications selector"):
        SceneRenderCatalogLoader().validate_document(catalog_json)


def test_catalog_loader_rejects_duplicate_recipe_ids() -> None:
    catalog_json = catalog_document()
    classification_recipe = _classification_recipes(catalog_json)[0]
    classification_recipe["id"] = _fallback_recipes(catalog_json)[0]["id"]

    with pytest.raises(TemplateRuntimeError, match="duplicate recipe id"):
        SceneRenderCatalogLoader().validate_document(catalog_json)


def test_catalog_loader_rejects_missing_required_fallbacks() -> None:
    catalog_json = catalog_document()
    recipes = _recipes(catalog_json)
    recipes["fallbacks"] = [
        fallback for fallback in _fallback_recipes(catalog_json) if _selector_kind(fallback) != "classified"
    ]

    with pytest.raises(TemplateRuntimeError, match="Missing required"):
        SceneRenderCatalogLoader().validate_document(catalog_json)


def test_catalog_loader_rejects_unknown_recipe_template_reference() -> None:
    catalog_json = catalog_document()
    fallback_recipe = _fallback_recipes(catalog_json)[0]
    steps = cast(list[dict[str, Any]], fallback_recipe["steps"])
    steps.append({"template": "missing", "inputs": {}})

    with pytest.raises(TemplateRuntimeError, match="Unknown template"):
        SceneRenderCatalogLoader().validate_document(catalog_json)


def test_catalog_loader_rejects_unknown_recipe_binding_reference() -> None:
    catalog_json = catalog_document()
    human_recipe = next(recipe for recipe in _classification_recipes(catalog_json) if recipe["id"] == "human")
    values = cast(dict[str, Any], human_recipe["values"])
    values["bad_binding_reference"] = {"ref": ["bindings", "hood_color", "score"]}

    with pytest.raises(TemplateRuntimeError, match="Unknown field"):
        SceneRenderCatalogLoader().validate_document(catalog_json)


def test_failed_catalog_store_write_preserves_original_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, small_catalog_path: Path
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    first_catalog = store.create_catalog("First", base_catalog_path=small_catalog_path)
    second_catalog = store.create_catalog("Second", base_catalog_path=small_catalog_path)
    store.set_default_catalog(first_catalog.path)
    files_before = sorted(tmp_path.iterdir())

    def fail_replace(source: Path, target: Path) -> Path:
        raise OSError("Replacement failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="Replacement failed"):
        store.set_default_catalog(second_catalog.path)

    assert store.list_catalogs_with_errors().default_path == first_catalog.path
    assert sorted(tmp_path.iterdir()) == files_before


def test_catalog_store_creates_user_catalog_file_from_default(tmp_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)

    catalog_file = store.create_catalog("Vehicle Review")
    document = json.loads(catalog_file.path.read_text(encoding="utf-8"))

    assert catalog_file.path == tmp_path / "vehicle-review.json"
    assert catalog_file.catalog_id == "user.vehicle-review"
    assert catalog_file.name == "Vehicle Review"
    assert document["metadata"]["id"] == "user.vehicle-review"
    assert document["metadata"]["name"] == "Vehicle Review"
    assert "templates" in document


@pytest.mark.parametrize("filename", ["built-in.json", "default.json", "catalog.json"])
def test_catalog_store_never_overwrites_user_files_named_like_the_built_in_catalog(
    tmp_path: Path, filename: str
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    user_path = tmp_path / filename
    document = json.loads(store.built_in_catalog_path.read_text(encoding="utf-8"))
    document["metadata"].update({"id": "user.customized", "name": "Customized"})
    document["templates"]["object_box"]["description"] = "Edited by the user."
    user_path.write_text(json.dumps(document), encoding="utf-8")
    user_bytes = user_path.read_bytes()

    listing = store.list_catalogs_with_errors()
    create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path)).create_selection()

    assert user_path.read_bytes() == user_bytes
    assert [(catalog.path, catalog.is_built_in) for catalog in listing.catalogs] == [
        *((path, True) for path in BUILT_IN_CATALOG_PATHS),
        (user_path, False),
    ]


def test_catalog_store_creates_user_catalog_named_built_in(tmp_path: Path, small_catalog_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)

    catalog_file = store.create_catalog("Built-in", base_catalog_path=small_catalog_path)

    assert catalog_file.path == tmp_path / "built-in.json"
    assert not catalog_file.is_built_in
    assert store.list_catalogs_with_errors().catalogs[len(BUILT_IN_CATALOG_PATHS)] == catalog_file


def test_catalog_store_lists_built_in_catalogs_from_package_with_standard_as_default(tmp_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path / "not-created-yet")

    listing = store.list_catalogs_with_errors()

    assert store.built_in_catalog_path == BUILT_IN_CATALOG_PATH
    assert [catalog.name for catalog in listing.catalogs] == [
        "Standard",
        "Minimal",
        "Chunky",
        "Glass",
        "Tracking",
        "Classic",
    ]
    assert all(catalog.is_built_in for catalog in listing.catalogs)
    assert listing.default_path == BUILT_IN_CATALOG_PATH
    assert not store.catalogs_dir.exists()


def test_catalog_store_sets_built_in_catalog_as_default(tmp_path: Path, small_catalog_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    catalog_file = store.create_catalog("Vehicle review", base_catalog_path=small_catalog_path)
    store.set_default_catalog(catalog_file.path)

    store.set_default_catalog(store.built_in_catalog_path)

    assert store.list_catalogs_with_errors().default_path == store.built_in_catalog_path
    assert sorted(tmp_path.iterdir()) == [catalog_file.path]


def test_catalog_store_remembers_another_built_in_catalog_as_default(tmp_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)

    store.set_default_catalog(CLASSIC_CATALOG_PATH)

    listing = SceneRenderCatalogStore(tmp_path).list_catalogs_with_errors()
    assert listing.default_path == CLASSIC_CATALOG_PATH
    assert listing.label(CLASSIC_CATALOG_PATH) == "Classic (default)"


@pytest.mark.parametrize("name", ["", "../minimal.json", "/minimal.json", "missing.json", "catalog.schema.json"])
def test_catalog_store_recovers_from_invalid_built_in_choices(tmp_path: Path, name: str) -> None:
    """A damaged saved choice must not stop catalog listing or app startup."""
    (tmp_path / DEFAULT_RENDER_CATALOG_CHOICE_FILENAME).write_text(f"built-in:{name}\n", encoding="utf-8")
    listing = SceneRenderCatalogStore(tmp_path).list_catalogs_with_errors()
    assert listing.default_path == listing.chosen_default_path == BUILT_IN_CATALOG_PATH
    assert len(listing.catalogs) == len(BUILT_IN_CATALOG_PATHS)


def test_catalog_store_distinguishes_user_filenames_from_built_in_choices(
    tmp_path: Path, small_catalog_path: Path
) -> None:
    """A valid user filename may start with the marker used for a packaged catalog."""
    path = tmp_path / f"built-in:{CLASSIC_CATALOG_PATH.name}"
    path.write_bytes(small_catalog_path.read_bytes())
    store = SceneRenderCatalogStore(tmp_path)
    for selected in (path, CLASSIC_CATALOG_PATH, path):
        store.set_default_catalog(selected)
        assert SceneRenderCatalogStore(tmp_path).list_catalogs_with_errors().default_path == selected


def test_catalog_store_remembers_default_catalog(tmp_path: Path, small_catalog_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    catalog_file = store.create_catalog("Vehicle review", base_catalog_path=small_catalog_path)

    store.set_default_catalog(catalog_file.path)

    listing = SceneRenderCatalogStore(tmp_path).list_catalogs_with_errors()
    assert listing.default_path == catalog_file.path
    assert [listing.label(catalog.path) for catalog in listing.catalogs][-2:] == ["Classic", "Vehicle review (default)"]


def test_catalog_listing_labels_invalid_and_missing_paths_by_file_name(tmp_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    invalid_path = tmp_path / "broken.json"
    invalid_path.write_text("{ broken", encoding="utf-8")

    listing = store.list_catalogs_with_errors()

    assert listing.catalog_for(invalid_path) is None
    assert listing.error_for(invalid_path) is not None
    assert listing.label(invalid_path) == "broken.json (invalid)"
    assert listing.label(tmp_path / "gone.json") == "gone.json (missing)"
    assert listing.label(store.built_in_catalog_path) == "Standard (default)"


def test_catalog_store_rejects_default_outside_store(tmp_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path / "catalogs")
    outside_path = tmp_path / "outside.json"
    outside_path.write_text(store.built_in_catalog_path.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="not in the catalog store"):
        store.set_default_catalog(outside_path)

    assert store.list_catalogs_with_errors().default_path == store.built_in_catalog_path


def test_catalog_store_ignores_default_choice_for_missing_catalog(tmp_path: Path, small_catalog_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    catalog_file = store.create_catalog("Vehicle review", base_catalog_path=small_catalog_path)
    store.set_default_catalog(catalog_file.path)

    catalog_file.path.unlink()

    assert store.list_catalogs_with_errors().default_path == store.built_in_catalog_path


def test_catalog_store_lists_valid_catalogs_when_another_catalog_is_invalid(
    tmp_path: Path, small_catalog_path: Path
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    valid_catalog = store.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    invalid_path = tmp_path / "broken.json"
    invalid_path.write_text("not valid catalog data\n", encoding="utf-8")

    listing = store.list_catalogs_with_errors()

    assert [catalog.path for catalog in listing.catalogs] == [*store.built_in_catalog_paths, valid_catalog.path]
    assert len(listing.errors) == 1
    assert listing.errors[0].path == invalid_path


def test_catalog_store_listing_reads_structurally_invalid_catalog_metadata(
    tmp_path: Path, small_catalog_path: Path
) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    valid_catalog = store.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)
    document = catalog_document()
    fallback_recipe = _fallback_recipes(document)[0]
    steps = cast(list[dict[str, Any]], fallback_recipe["steps"])
    steps.append({"template": "missing", "inputs": {}})
    invalid_path = tmp_path / "broken-structure.json"
    invalid_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    listing = store.list_catalogs_with_errors()

    assert [catalog.path for catalog in listing.catalogs] == [
        *store.built_in_catalog_paths,
        invalid_path,
        valid_catalog.path,
    ]
    assert listing.errors == ()


def test_catalog_store_validates_created_catalog_before_writing(tmp_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    document = catalog_document()
    fallback_recipe = _fallback_recipes(document)[0]
    steps = cast(list[dict[str, Any]], fallback_recipe["steps"])
    steps.append({"template": "missing", "inputs": {}})
    base_path = tmp_path / "invalid-base.json"
    base_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    with pytest.raises(TemplateRuntimeError, match="Unknown template"):
        store.create_catalog("Copy", base_catalog_path=base_path)

    assert not (tmp_path / "copy.json").exists()


def test_get_built_in_scene_render_catalog_uses_process_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cached_catalog = catalog_module.get_built_in_scene_render_catalog()

    class _UnexpectedLoader:
        def __init__(self) -> None:
            raise AssertionError("Built-in catalog getter must not load the catalog again.")

    monkeypatch.setattr(catalog_module, "SceneRenderCatalogLoader", _UnexpectedLoader)

    assert catalog_module.get_built_in_scene_render_catalog() is cached_catalog
    assert cached_catalog.catalog_id == "built-in.standard"


def _fallback_recipes(catalog_json: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _recipes(catalog_json)["fallbacks"])


def _classification_recipes(catalog_json: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _recipes(catalog_json)["classifications"])


def _recipes(catalog_json: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], catalog_json["recipes"])


def _selector_kind(recipe: dict[str, Any]) -> str:
    selector = cast(dict[str, Any], recipe["selector"])
    return cast(str, selector["kind"])


def test_render_identity_ignores_metadata_and_calculation_declaration_order() -> None:
    document = catalog_document()
    loader = SceneRenderCatalogLoader()
    before = loader.validate_document(document)
    document["metadata"]["id"] = "renamed"
    document["metadata"]["description"] = "Different presentation metadata"
    values = document["templates"]["marker"]["values"]
    document["templates"]["marker"]["values"] = dict(reversed(list(values.items())))
    after = loader.validate_document(document)
    assert before.content_hash != after.content_hash
    assert before.rendering_identity == after.rendering_identity
    document["templates"]["marker"]["steps"].reverse()
    assert loader.validate_document(document).rendering_identity != before.rendering_identity


def test_file_parser_rejects_duplicate_definitions(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"metadata": {}, "metadata": {}}')
    with pytest.raises(TemplateRuntimeError) as error:
        SceneRenderCatalogLoader().load_path(path)
    assert error.value.diagnostic.code == "duplicate_definition"


@pytest.mark.parametrize("relation_recipe", [False, True])
def test_runtime_failure_discards_only_the_affected_recipe_output(relation_recipe: bool, tmp_path: Path) -> None:
    from ax_devil.modules.scene.model import (
        BoundingBox,
        Entity,
        EntityId,
        EntityRelation,
        Observation,
        Scene,
        TimeSlice,
    )
    from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
    from ax_devil.modules.video_player.engine.render_context import RenderContext
    from tests.drawing_helpers import PointCall

    document = catalog_document()
    recipe = document["recipes"]["relations" if relation_recipe else "fallbacks"][0]
    geometry_path = (
        ["scene", "source", "observation", "geometry", "w"]
        if relation_recipe
        else ["scene", "observation", "geometry", "w"]
    )
    recipe["steps"] = [
        {"primitive": "point", "fields": {"position": {"x": 0.5, "y": 0}}},
        {
            "primitive": "point",
            "fields": {
                "position": {
                    "x": {
                        "call": "div",
                        "args": {"numerator": 1, "denominator": {"ref": geometry_path}},
                    },
                    "y": 0,
                }
            },
        },
    ]
    if relation_recipe:
        document["recipes"]["fallbacks"][0]["steps"] = []
    catalog = SceneRenderCatalogLoader().validate_document(document)
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    for name, width in (("bad", 0.0), ("good", 0.5)):
        entity = Entity(id=EntityId(name))
        entity.add_observation(Observation(geometry=BoundingBox.from_xywh(0, 0, width, 0.2), frame_number=0))
        scene.add_entity(entity)
        if relation_recipe:
            scene.add_relation(EntityRelation(type="has_part", source_entity_id=entity.id, target_entity_id=entity.id))
    diagnostics: list[CatalogDiagnostic] = []
    primitives = record_scene(catalog, scene, RenderContext.create(800, 400), diagnostics=diagnostics)
    assert len(primitives) == 2
    assert all(isinstance(item, PointCall) for item in primitives)
    assert len(diagnostics) == 1
    assert "steps[1]" in diagnostics[0].location
    assert "zero" in diagnostics[0].message
    assert diagnostics[0].recipe_id == recipe["id"]
    assert [item.x for item in primitives if isinstance(item, PointCall)] == [0.5, 2.0]

    # File loading and reordered JSON must preserve both rollback and successful output.
    path = tmp_path / "roundtrip.json"
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    restored = SceneRenderCatalogLoader().load_path(path)
    restored_diagnostics: list[CatalogDiagnostic] = []
    assert restored.rendering_identity == catalog.rendering_identity
    restored_output = record_scene(restored, scene, RenderContext.create(800, 400), diagnostics=restored_diagnostics)
    assert len(restored_output) == len(primitives)
    for actual, expected in zip(restored_output, primitives):
        assert isinstance(actual, PointCall)
        assert isinstance(expected, PointCall)
        assert (actual.x, actual.y, actual.style) == (expected.x, expected.y, expected.style)
    assert restored_diagnostics == diagnostics


def test_catalog_store_deletes_user_catalog_file(tmp_path: Path, small_catalog_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    catalog_file = store.create_catalog("Vehicle Review", base_catalog_path=small_catalog_path)

    store.delete_catalog(catalog_file.path)

    assert not catalog_file.path.exists()


@pytest.mark.parametrize("path", BUILT_IN_CATALOG_PATHS, ids=lambda path: path.stem)
def test_catalog_store_rejects_deleting_built_in_catalogs(tmp_path: Path, path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)

    with pytest.raises(ValueError, match="Built-in render catalogs cannot be removed"):
        store.delete_catalog(path)

    assert path.is_file()
    assert not store.is_user_catalog(path)


def test_catalog_store_deleting_default_restores_built_in_default(tmp_path: Path, small_catalog_path: Path) -> None:
    store = SceneRenderCatalogStore(tmp_path)
    catalog_file = store.create_catalog("Vehicle review", base_catalog_path=small_catalog_path)
    store.set_default_catalog(catalog_file.path)

    store.delete_catalog(catalog_file.path)
    recreated_file = store.create_catalog("Vehicle review", base_catalog_path=small_catalog_path)

    assert recreated_file.path == catalog_file.path
    assert store.list_catalogs_with_errors().default_path == store.built_in_catalog_path
