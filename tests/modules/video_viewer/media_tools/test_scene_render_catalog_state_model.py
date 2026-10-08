"""Model-based check of render catalog selections and selectors under random file changes and user actions."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogLoader,
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
    SceneRenderCatalogStore,
    create_scene_render_catalog_manager,
)
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATH
from ax_devil.modules.video_viewer.media_tools import SceneRenderCatalogSelector
from tests.catalog_helpers import catalog_document

GOOD_STATES = ("v1", "v2")
LISTED_STATES = ("v1", "v2", "compile_error")
FILE_STATES = ("v1", "v2", "compile_error", "json_error", "deleted")
SUCCESS_STATUSES = ("Selected catalog", "Render catalog reloaded")
ERROR_STATUSES = ("Render catalog error", "Active render catalog")


@dataclass
class _ViewModel:
    """What one selection should hold: the chosen path, whether it loaded, and which content is rendered."""

    path: Path
    loaded: bool
    rendered: tuple[str, str]


@dataclass
class _Model:
    """Expected catalog state, tracking the listing snapshot separately from the files on disk."""

    built_in: Path
    names: dict[Path, str]
    disk: dict[Path, str]
    listed: dict[Path, str]
    choice: Path
    views: list[_ViewModel] = field(default_factory=list)

    def state(self, path: Path) -> str:
        return "v1" if path == self.built_in else self.disk[path]

    def refresh(self) -> None:
        self.listed = dict(self.disk)

    def is_listed(self, path: Path) -> bool:
        return path == self.built_in or self.listed.get(path) in LISTED_STATES

    def default_path(self) -> Path:
        return self.choice if self.is_listed(self.choice) else self.built_in

    def load(self, view: _ViewModel, path: Path) -> None:
        view.path = path
        view.loaded = self.state(path) in GOOD_STATES
        if view.loaded:
            view.rendered = (self.names[path], self.state(path))

    def new_view(self) -> _ViewModel:
        view = _ViewModel(self.default_path(), False, ("built-in", "v1"))
        self.load(view, view.path)
        if not view.loaded:
            self.load(view, self.built_in)
        return view


Step = tuple[str, ...]

SCENARIOS: dict[str, list[Step]] = {
    "failed reload then catalog management": [
        ("select", "0", "a"),
        ("write", "a", "compile_error"),
        ("reload", "0"),
        ("refresh",),
        ("apply", "0"),
        ("default", "0"),
    ],
    "apply to all a catalog broken since it loaded": [
        ("select", "0", "a"),
        ("write", "a", "compile_error"),
        ("apply", "0"),
        ("refresh",),
        ("write", "a", "v1"),
        ("reload", "0"),
        ("apply", "0"),
    ],
    "use as default a catalog broken since it loaded": [
        ("select", "0", "a"),
        ("write", "a", "compile_error"),
        ("default", "0"),
        ("refresh",),
        ("write", "a", "v2"),
        ("reload", "0"),
        ("default", "0"),
    ],
    "new view falls back when the default is broken": [
        ("select", "0", "a"),
        ("default", "0"),
        ("write", "a", "compile_error"),
        ("new_view",),
    ],
    "deleted default and missing active catalog": [
        ("select", "0", "a"),
        ("default", "0"),
        ("write", "a", "deleted"),
        ("refresh",),
        ("new_view",),
        ("reload", "0"),
    ],
    "deleting the default and active catalog": [
        ("select", "0", "a"),
        ("default", "0"),
        ("delete", "a"),
        ("new_view",),
        ("reload", "0"),
        ("select", "0", "b"),
    ],
    "use the built-in catalog as default again": [
        ("select", "0", "a"),
        ("default", "0"),
        ("select", "0", "built-in"),
        ("default", "0"),
        ("new_view",),
    ],
    "invalid JSON cannot become default and recovers after repair": [
        ("select", "0", "a"),
        ("write", "a", "json_error"),
        ("default", "0"),
        ("refresh",),
        ("write", "a", "v2"),
        ("reload", "0"),
        ("default", "0"),
        ("new_view",),
    ],
    "failed selection enables actions only after repair": [
        ("write", "a", "compile_error"),
        ("select", "0", "a"),
        ("write", "a", "v1"),
        ("reload", "0"),
        ("apply", "0"),
    ],
    "apply to all reaches failed views": [
        ("write", "b", "compile_error"),
        ("select", "1", "b"),
        ("select", "0", "a"),
        ("apply", "0"),
    ],
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_catalog_scenarios_follow_model(qtbot: QtBot, tmp_path: Path, name: str) -> None:
    _run_steps(qtbot, tmp_path, SCENARIOS[name])


@pytest.mark.parametrize("seed", range(2))
def test_random_catalog_steps_follow_model(qtbot: QtBot, tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    operations = ("write", "write", "select", "reload", "refresh", "apply", "default", "delete", "new_view")
    steps: list[Step] = []
    for _step in range(60):
        operation = rng.choice(operations)
        view = str(rng.randrange(2))
        if operation == "write":
            steps.append((operation, rng.choice(("a", "b")), rng.choice(FILE_STATES)))
        elif operation == "delete":
            steps.append((operation, rng.choice(("a", "b"))))
        elif operation == "select":
            steps.append((operation, view, rng.choice(("built-in", "a", "b"))))
        elif operation in ("reload", "apply", "default"):
            steps.append((operation, view))
        else:
            steps.append((operation,))
    _run_steps(qtbot, tmp_path, steps)


def _run_steps(qtbot: QtBot, tmp_path: Path, steps: list[Step]) -> None:
    """Apply *steps* to two views and check every selection and selector against the model after each step."""
    manager = create_scene_render_catalog_manager(catalog_store=SceneRenderCatalogStore(tmp_path))
    built_in = manager.built_in_catalog_path()
    paths = {"built-in": built_in, "a": tmp_path / "a.json", "b": tmp_path / "b.json"}
    documents = {paths["a"]: _documents("A"), paths["b"]: _documents("B")}
    for path, texts in documents.items():
        _write(path, texts, "v1")
    manager.refresh_catalogs()
    names = {path: name for name, path in paths.items()}
    model = _Model(built_in, names, dict.fromkeys(documents, "v1"), dict.fromkeys(documents, "v1"), built_in)
    selections: list[SceneRenderCatalogSelection] = []
    selectors: list[SceneRenderCatalogSelector] = []

    def add_view() -> None:
        selections.append(manager.create_selection())
        selectors.append(SceneRenderCatalogSelector(selections[-1]))
        qtbot.addWidget(selectors[-1])
        model.views.append(model.new_view())

    add_view()
    add_view()
    history: list[str] = []
    for step in steps:
        operation = step[0]
        if operation == "write":
            path = paths[step[1]]
            _write(path, documents[path], step[2])
            model.disk[path] = step[2]
        elif operation == "select":
            index, path = int(step[1]), paths[step[2]]
            selections[index].select_catalog(path)
            model.load(model.views[index], path)
        elif operation == "reload":
            index = int(step[1])
            selectors[index].reload_selected_catalog()
            model.refresh()
            model.load(model.views[index], model.views[index].path)
        elif operation == "refresh":
            manager.refresh_catalogs()
            model.refresh()
        elif operation == "delete":
            path = paths[step[1]]
            if model.disk[path] != "deleted":
                manager.delete_catalog(path)
                model.disk[path] = "deleted"
                model.choice = built_in if model.choice == path else model.choice
                model.refresh()
        elif operation == "apply":
            index = int(step[1])
            action = _action(selectors[index], "applyRenderCatalogToAllAction")
            path = model.views[index].path
            if action.isEnabled() and model.state(path) in GOOD_STATES:
                for view in model.views:
                    model.load(view, path)
            elif action.isEnabled():
                model.views[index].loaded = False
            action.trigger()
        elif operation == "default":
            index = int(step[1])
            action = _action(selectors[index], "useRenderCatalogAsDefaultAction")
            path = model.views[index].path
            if action.isEnabled() and model.state(path) in GOOD_STATES:
                model.choice = path
                model.refresh()
            elif action.isEnabled():
                model.views[index].loaded = False
            action.trigger()
        elif len(selections) < 4:
            add_view()
        history.append(" ".join(step))
        _assert_matches_model(manager, model, selections, selectors, history)


def _assert_matches_model(
    manager: SceneRenderCatalogManager,
    model: _Model,
    selections: list[SceneRenderCatalogSelection],
    selectors: list[SceneRenderCatalogSelector],
    history: list[str],
) -> None:
    trace = " -> ".join(history)
    assert manager.default_catalog_path() == model.default_path(), trace
    for selection, selector, view in zip(selections, selectors, model.views):
        catalog = selection.active_catalog()
        listed = model.is_listed(view.path)
        usable = listed and view.loaded
        status = selection.status()
        assert selection.active_catalog_path() == view.path, trace
        assert selection.active_catalog_loaded() == view.loaded, trace
        assert catalog is not None and catalog.content_hash == _content_hashes()[view.rendered], trace
        if not view.loaded:
            assert not status.startswith(SUCCESS_STATUSES), f"{trace}: {status}"
        if view.loaded and listed:
            assert not status.startswith(ERROR_STATUSES), f"{trace}: {status}"
        assert _combo(selector).currentData() == str(view.path), trace
        assert _combo(selector).currentText() == _expected_label(model, view.path), trace
        assert _status(selector).text() == status, trace
        assert _action(selector, "applyRenderCatalogToAllAction").isEnabled() == usable, trace
        assert _action(selector, "useRenderCatalogAsDefaultAction").isEnabled() == (
            usable and view.path != model.choice
        ), trace


def _expected_label(model: _Model, path: Path) -> str:
    if model.is_listed(path):
        name = "Standard" if path == model.built_in else model.names[path].upper()
        return f"{name} (default)" if path == model.default_path() else name
    return f"{path.name} ({'invalid' if model.listed.get(path) == 'json_error' else 'missing'})"


@cache
def _documents(name: str) -> dict[str, str]:
    document = catalog_document()
    document["metadata"].update({"id": f"user.{name.lower()}", "name": name})
    texts = {}
    for state, description in (("v1", "one"), ("v2", "three")):
        document["metadata"]["description"] = description
        document["templates"]["marker"]["parameters"]["x"]["default"] = 0.25 if state == "v1" else 0.75
        texts[state] = json.dumps(document)
    document["recipes"]["fallbacks"][0]["steps"].append({"template": "missing", "inputs": {}})
    texts["compile_error"] = json.dumps(document)
    texts["json_error"] = "{ broken"
    return texts


@cache
def _content_hashes() -> dict[tuple[str, str], str]:
    """Return the compiled content hash of every loadable catalog revision, keyed by catalog name and state."""
    texts = {("built-in", "v1"): BUILT_IN_CATALOG_PATH.read_text(encoding="utf-8")}
    texts.update({(name, state): _documents(name.upper())[state] for name in ("a", "b") for state in GOOD_STATES})
    return {
        key: SceneRenderCatalogLoader().validate_document(json.loads(text)).content_hash for key, text in texts.items()
    }


def _write(path: Path, texts: dict[str, str], state: str) -> None:
    if state == "deleted":
        path.unlink(missing_ok=True)
    else:
        path.write_text(texts[state], encoding="utf-8")


def _combo(selector: SceneRenderCatalogSelector) -> QComboBox:
    combo = selector.findChild(QComboBox, "renderCatalogCombo")
    assert combo is not None
    return combo


def _status(selector: SceneRenderCatalogSelector) -> QLabel:
    status = selector.findChild(QLabel, "renderCatalogStatus")
    assert status is not None
    return status


def _action(selector: SceneRenderCatalogSelector, name: str) -> QAction:
    action = selector.findChild(QAction, name)
    assert action is not None
    return action
