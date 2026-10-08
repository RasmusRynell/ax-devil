from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATH,
    BUILT_IN_CATALOG_PATHS,
)
from ax_devil.modules.scene.rendering.catalog_manager import create_scene_render_catalog_manager
from ax_devil.modules.scene.rendering.catalog_store import SceneRenderCatalogStore
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.settings.paths import DEFAULT_RENDER_CATALOG_CHOICE_FILENAME
from tests.catalog_helpers import CLASSIC_CATALOG_PATH, catalog_document


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
    fallback_recipe = document["recipes"]["fallbacks"][0]
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
    fallback_recipe = document["recipes"]["fallbacks"][0]
    steps = cast(list[dict[str, Any]], fallback_recipe["steps"])
    steps.append({"template": "missing", "inputs": {}})
    base_path = tmp_path / "invalid-base.json"
    base_path.write_text(f"{json.dumps(document, indent=2)}\n", encoding="utf-8")

    with pytest.raises(TemplateRuntimeError, match="Unknown template"):
        store.create_catalog("Copy", base_catalog_path=base_path)

    assert not (tmp_path / "copy.json").exists()


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
