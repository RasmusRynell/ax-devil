"""Small real catalogs for tests of editing, validation and selection behavior, and the Classic built-in catalog."""

from functools import cache
from typing import Any

from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATHS,
    SceneRenderCatalog,
    SceneRenderCatalogLoader,
)
from ax_devil.modules.scene.rendering.visibility import OverlayVisibility

CLASSIC_CATALOG_PATH = next(path for path in BUILT_IN_CATALOG_PATHS if path.stem == "classic")
"""The built-in catalog with badges, swatches and bars, whose drawing details many tests check."""


@cache
def classic_catalog() -> SceneRenderCatalog:
    """Return the Classic built-in catalog, compiled once."""
    return SceneRenderCatalogLoader().load_path(CLASSIC_CATALOG_PATH)


@cache
def classic_variant(visibility: OverlayVisibility) -> SceneRenderCatalog:
    """Return a visibility-specialized Classic catalog for observable drawing tests."""
    return SceneRenderCatalogLoader().load_revision(CLASSIC_CATALOG_PATH).specialize(visibility)


def catalog_document() -> dict[str, Any]:
    """Return independent catalog data with fallbacks, shared selectors and a relation."""

    def recipe(recipe_id: str, kind: str, types: list[str] | None = None) -> dict[str, Any]:
        selector: dict[str, Any] = {"kind": kind}
        if types is not None:
            selector["types"] = types
        return {
            "id": recipe_id,
            "label": recipe_id.title(),
            "selector": selector,
            "bindings": {},
            "values": {},
            "steps": [{"template": "marker", "inputs": {}}],
        }

    return {
        "metadata": {"id": "test", "name": "Test", "schema_version": 3},
        "templates": {
            "marker": {
                "parameters": {"x": {"type": "number", "default": 0.25}},
                "values": {"origin": {"ref": ["parameters", "x"]}, "result": {"ref": ["values", "origin"]}},
                "steps": [
                    {"primitive": "point", "fields": {"position": {"x": {"ref": ["values", "result"]}, "y": 0.5}}},
                    {"primitive": "point", "fields": {"position": {"x": 0.75, "y": 0.5}}},
                ],
            },
        },
        "recipes": {
            "fallbacks": [recipe("motion", "unclassified"), recipe("generic_classified", "classified")],
            "classifications": [
                recipe("vehicle", "classification", ["vehicle", "car"]),
                recipe("human", "classification", ["human"]),
            ],
            "relations": [recipe("has_part", "relation", ["has_part"])],
        },
    }


def ref_expr(*parts: str) -> dict[str, object]:
    """Return a catalog expression that reads the value at the path *parts*."""
    return {"ref": list(parts)}


def call_expr(name: str, **args: object) -> dict[str, object]:
    """Return a catalog expression that calls the function *name* with *args*."""
    return {"call": name, "args": args}
