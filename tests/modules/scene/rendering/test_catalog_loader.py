from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATHS,
    SceneRenderCatalogLoader,
    get_built_in_scene_render_catalog,
)
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from tests.catalog_helpers import catalog_document


@pytest.mark.parametrize("path", BUILT_IN_CATALOG_PATHS, ids=lambda path: path.stem)
def test_catalog_loader_compiles_every_built_in_catalog(path: Path) -> None:
    catalog = SceneRenderCatalogLoader().load_path(path)

    assert catalog.catalog_id == f"built-in.{path.stem}"
    assert {"unclassified", "classified"} <= set(catalog.fallback_recipes)
    assert {"human", "car", "vehicle", "head"} <= set(catalog.classification_recipes)
    assert "has_part" in catalog.relation_recipes


def test_catalog_loader_accepts_catalog_without_relation_recipes() -> None:
    catalog_json = catalog_document()
    del _recipes(catalog_json)["relations"]

    catalog = SceneRenderCatalogLoader().validate_document(
        catalog_json,
    )

    assert catalog.relation_recipes == {}


@pytest.mark.parametrize("field,value", [("id", 123), ("schema_version", True)])
def test_catalog_loader_load_path_validates_structure(tmp_path: Path, field: str, value: object) -> None:
    catalog_path = tmp_path / "catalog.json"
    document = catalog_document()
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


@pytest.mark.parametrize("version", [2, 99])
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


@pytest.mark.parametrize("group", ["fallbacks", "classifications"])
def test_catalog_loader_rejects_duplicate_selectors(group: str) -> None:
    catalog_json = catalog_document()
    recipes = cast(list[dict[str, Any]], _recipes(catalog_json)[group])
    duplicate_selector_recipe = deepcopy(recipes[0])
    duplicate_selector_recipe["id"] = "duplicate-selector"
    recipes.append(duplicate_selector_recipe)

    with pytest.raises(TemplateRuntimeError, match=f"Duplicate {group} selector"):
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


def test_built_in_catalog_is_compiled_once_per_process() -> None:
    cached_catalog = get_built_in_scene_render_catalog()

    assert get_built_in_scene_render_catalog() is cached_catalog
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
