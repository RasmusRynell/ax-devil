"""Small real catalogs for tests of editing, validation and selection behavior, and the Classic built-in catalog."""

from functools import cache
from pathlib import Path
from threading import Event
from typing import Any

from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogManager,
    SceneRenderCatalogStore,
)
from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATHS,
    SceneRenderCatalog,
    SceneRenderCatalogLoader,
    SceneRenderCatalogRevision,
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


def wait_for_catalogs(qtbot: QtBot, manager: SceneRenderCatalogManager) -> None:
    """Let the manager finish its first listing and every listing or load it started."""
    qtbot.waitUntil(lambda: manager.listing_ready and not manager.is_loading, timeout=10_000)


class GatedCatalogLoader(SceneRenderCatalogLoader):
    """A loader whose reads of held paths wait for the test, so a load stays in flight until it is released."""

    def __init__(self) -> None:
        self.reads: list[Path] = []
        """The paths whose reads started, in order."""
        self._gates: dict[Path, Event] = {}

    def hold(self, path: Path) -> Event:
        """Make reads of *path* wait until the returned event is set."""
        return self._gates.setdefault(path, Event())

    def release_all(self) -> None:
        """Let every held read go, so no worker outlives the test."""
        for gate in self._gates.values():
            gate.set()

    def load_revision(self, catalog_path: Path) -> SceneRenderCatalogRevision:
        """Read *catalog_path* once its gate, if any, is open."""
        self.reads.append(catalog_path)
        gate = self._gates.get(catalog_path)
        if gate is not None:
            gate.wait(10)
        return super().load_revision(catalog_path)


def create_ready_catalog_manager(
    qtbot: QtBot,
    catalog_store: SceneRenderCatalogStore,
    *,
    catalog_loader: SceneRenderCatalogLoader | None = None,
    jobs: BackgroundJobs | None = None,
) -> SceneRenderCatalogManager:
    """Return a manager that has listed its catalogs and compiled its default one, so new selections start at once.

    With a one-worker *jobs*, queued reads run in request order, so a held read blocks every read after it.
    """
    manager = SceneRenderCatalogManager(catalog_store=catalog_store, catalog_loader=catalog_loader, jobs=jobs)
    manager.initialize()
    qtbot.waitUntil(lambda: manager.listing_ready, timeout=10_000)
    manager.request_revision(manager.default_catalog_path(), on_loaded=lambda _revision: None, on_failed=lambda _: None)
    wait_for_catalogs(qtbot, manager)
    return manager


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


def create_catalog(
    qtbot: QtBot, manager: SceneRenderCatalogManager, name: str, *, base_catalog_path: Path | None = None
) -> SceneRenderCatalogFile:
    """Set up a user catalog through its plain store and refresh the manager's discovery."""
    created = manager.catalog_store.create_catalog(name, base_catalog_path=base_catalog_path)
    manager.refresh_catalogs()
    wait_for_catalogs(qtbot, manager)
    return created
