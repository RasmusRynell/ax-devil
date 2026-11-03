"""Small real catalogs for tests of editing, validation and selection behavior, and the Classic built-in catalog."""

import json
from functools import cache
from pathlib import Path
from threading import Event
from typing import Any

from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.scene.model import (
    Attribute,
    BoundingBox,
    Classification,
    Entity,
    EntityId,
    MotionState,
    Observation,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
    SceneRenderCatalogStore,
)
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATHS, SceneRenderCatalog
from ax_devil.modules.scene.rendering.catalog_loader import SceneRenderCatalogLoader, SceneRenderCatalogRevision
from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
from ax_devil.modules.scene.rendering.template_runtime.program import RenderCatalog
from ax_devil.modules.scene.rendering.visibility import OverlayVisibility
from tests.drawing_helpers import CONTEXT, PointCall, record_template

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


def point_step(x: object = 0, *, enabled: object = True) -> dict[str, object]:
    """Return a point step at horizontal position *x*, drawn only when *enabled*."""
    return {
        "primitive": "point",
        "enabled": enabled,
        "fields": {"position": {"x": x, "y": 0}},
        "style": {"stroke": {"color": [255, 255, 255], "width": {"value": 1, "unit": "px"}, "pattern": "solid"}},
    }


def compile_main_template(program: dict[str, Any], **templates: object) -> RenderCatalog:
    """Compile *program* as the template ``main`` next to the other *templates*."""
    return RenderProgramCompiler().compile_catalog({"main": program, **templates})


def main_point_x(catalog: RenderCatalog, inputs: dict[str, object] | None = None) -> float:
    """Return the horizontal position of the first point ``main`` draws."""
    point = record_template(catalog, "main", inputs or {}, context=CONTEXT)[0]
    assert isinstance(point, PointCall)
    return point.x


def px_length(value: float) -> dict[str, object]:
    """Return a length of *value* pixels."""
    return {"value": value, "unit": "px"}


def observed_scene(*, attributes: list[Attribute] | None = None, motion_state: MotionState | None = None) -> Scene:
    """Return a Scene with one human entity of the given *attributes* and *motion_state*."""
    scene = Scene(time_slice=TimeSlice(start=0, end=1))
    entity = Entity(id=EntityId("entity"), motion_state=motion_state)
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
            frame_number=0,
            classification=[Classification(type="human", score=Score(0.8), attributes=attributes or [])],
        )
    )
    scene.add_entity(entity)
    return scene


def ready_manager(qtbot: QtBot, tmp_path: Path) -> SceneRenderCatalogManager:
    """Return a ready manager over a store in *tmp_path*."""
    return create_ready_catalog_manager(qtbot, SceneRenderCatalogStore(tmp_path))


def new_selection(qtbot: QtBot, manager: SceneRenderCatalogManager) -> SceneRenderCatalogSelection:
    """Create a selection on *manager* and wait until nothing is loading."""
    selection = manager.create_selection()
    wait_for_catalogs(qtbot, manager)
    return selection


def select_catalog(qtbot: QtBot, selection: SceneRenderCatalogSelection, path: Path) -> None:
    """Select *path* and wait until nothing is loading."""
    selection.select_catalog(path)
    wait_for_catalogs(qtbot, selection.manager)


def reload_catalog(qtbot: QtBot, selection: SceneRenderCatalogSelection) -> None:
    """Reload the active catalog of *selection* and wait until nothing is loading."""
    selection.reload_active_catalog()
    wait_for_catalogs(qtbot, selection.manager)


def refreshed_listing(qtbot: QtBot, manager: SceneRenderCatalogManager) -> SceneRenderCatalogListing:
    """Refresh the listing and return the one that arrives."""
    listings: list[SceneRenderCatalogListing] = []
    manager.refresh_catalogs(on_done=listings.append)
    qtbot.waitUntil(lambda: bool(listings), timeout=10_000)
    wait_for_catalogs(qtbot, manager)
    return listings[0]


def loaded_revision(
    qtbot: QtBot, manager: SceneRenderCatalogManager, path: Path, *, force_reload: bool = False
) -> SceneRenderCatalogRevision:
    """Request the revision of *path* and return it, raising the failure if it does not load."""
    loaded: list[SceneRenderCatalogRevision] = []
    failed: list[Exception] = []
    manager.request_revision(path, force_reload=force_reload, on_loaded=loaded.append, on_failed=failed.append)
    qtbot.waitUntil(lambda: bool(loaded or failed), timeout=10_000)
    if failed:
        raise failed[0]
    return loaded[0]


def break_compilation(path: Path) -> None:
    """Edit the catalog file at *path* so that it still parses but no longer compiles."""
    document = json.loads(path.read_text(encoding="utf-8"))
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")


def gated_manager(qtbot: QtBot, tmp_path: Path, loader: GatedCatalogLoader) -> SceneRenderCatalogManager:
    """Return a ready manager with one worker, so a held read blocks every read requested after it."""
    store = SceneRenderCatalogStore(tmp_path)
    return create_ready_catalog_manager(qtbot, store, catalog_loader=loader, jobs=BackgroundJobs(max_workers=1))


def user_catalog(qtbot: QtBot, manager: SceneRenderCatalogManager, name: str, base: Path) -> Path:
    path = create_catalog(qtbot, manager, name, base_catalog_path=base).path
    wait_for_catalogs(qtbot, manager)
    return path
