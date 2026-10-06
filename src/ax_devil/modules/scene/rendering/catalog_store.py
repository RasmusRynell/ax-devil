"""User-store file lifecycle for Scene Render Catalog JSON files and the choice of default catalog."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, cast

from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATH, BUILT_IN_CATALOG_PATHS
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.paths import DEFAULT_RENDER_CATALOG_CHOICE_FILENAME, RENDER_CATALOGS_DIR

_CATALOG_FILENAME_PATTERN = re.compile(r"[^a-z0-9._-]+")
_BUILT_IN_CHOICE_PREFIX = "built-in:"
"""Marks a stored default choice that names a built-in catalog rather than a file in the catalog store."""

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SceneRenderCatalogFile:
    """One render catalog JSON file: a built-in catalog that comes with the app or a file in the user catalog store."""

    catalog_id: str
    name: str
    path: Path
    is_built_in: bool = False


@dataclass(frozen=True, slots=True)
class SceneRenderCatalogFileError:
    """One render catalog file that could not be loaded."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class SceneRenderCatalogListing:
    """The built-in and user catalog files, non-fatal file loading errors and the default catalog."""

    catalogs: tuple[SceneRenderCatalogFile, ...]
    errors: tuple[SceneRenderCatalogFileError, ...]
    default_path: Path
    chosen_default_path: Path
    """The catalog the user chose as default, even while it does not load; ``default_path`` is then the default
    built-in catalog."""

    def catalog_for(self, path: Path) -> SceneRenderCatalogFile | None:
        """Return the listed catalog at *path*, or None when that file is invalid or missing."""
        return next((catalog for catalog in self.catalogs if catalog.path == path), None)

    def error_for(self, path: Path) -> SceneRenderCatalogFileError | None:
        """Return the listing error for *path*, or None when the file is listed or missing."""
        return next((error for error in self.errors if error.path == path), None)

    def label(self, path: Path) -> str:
        """Return the display label for *path*: the catalog name marked as default, or the file name marked unusable."""
        catalog = self.catalog_for(path)
        if catalog is None:
            return f"{path.name} ({'invalid' if self.error_for(path) is not None else 'missing'})"
        return f"{catalog.name} (default)" if path == self.default_path else catalog.name


class SceneRenderCatalogStore:
    """Manage user-editable render catalog files and the choice of default catalog.

    The built-in catalogs are listed from the installed package and never written. Every JSON file in the catalogs
    directory is a user catalog, whatever its name.
    """

    def __init__(self, catalogs_dir: Path | None = None) -> None:
        self._catalogs_dir = catalogs_dir or _configured_render_catalogs_dir()

    @property
    def catalogs_dir(self) -> Path:
        """Return the directory that stores user render catalogs."""
        return self._catalogs_dir

    @property
    def built_in_catalog_path(self) -> Path:
        """Return the built-in catalog that is the default until the user chooses another."""
        return BUILT_IN_CATALOG_PATH

    @property
    def built_in_catalog_paths(self) -> tuple[Path, ...]:
        """Return every read-only built-in render catalog, in listing order."""
        return BUILT_IN_CATALOG_PATHS

    def set_default_catalog(self, catalog_path: Path) -> None:
        """Make *catalog_path*, a built-in or user catalog, the catalog that new selections start from."""
        path = catalog_path.expanduser()
        if path == self.built_in_catalog_path:
            self._default_choice_path.unlink(missing_ok=True)
        elif path in self.built_in_catalog_paths:
            _write_text(self._default_choice_path, f"{_BUILT_IN_CHOICE_PREFIX}{path.name}\n")
        elif path.parent == self._catalogs_dir and path.is_file():
            _write_text(self._default_choice_path, f"./{path.name}\n")
        else:
            raise ValueError(f"Render catalog is not in the catalog store: {path}")
        logger.info(f"Default Scene Render Catalog set to {path}")

    def list_catalogs_with_errors(self) -> SceneRenderCatalogListing:
        """Return render catalog file metadata, per-file metadata errors and the default catalog path."""
        catalog_files: list[SceneRenderCatalogFile] = []
        errors: list[SceneRenderCatalogFileError] = []
        for path in (*self.built_in_catalog_paths, *sorted(self._catalogs_dir.glob("*.json"))):
            try:
                catalog_files.append(self._catalog_file_for_path(path))
            except (OSError, TemplateRuntimeError, ValueError, KeyError) as exc:
                errors.append(SceneRenderCatalogFileError(path=path, message=str(exc)))
        order = {path: index for index, path in enumerate(self.built_in_catalog_paths)}
        catalogs = tuple(
            sorted(catalog_files, key=lambda catalog: (order.get(catalog.path, len(order)), catalog.name.casefold()))
        )
        chosen_path = self._chosen_default_path()
        chosen_listed = any(catalog.path == chosen_path for catalog in catalogs)
        default_path = chosen_path if chosen_listed else self.built_in_catalog_path
        return SceneRenderCatalogListing(
            catalogs=catalogs, errors=tuple(errors), default_path=default_path, chosen_default_path=chosen_path
        )

    def create_catalog(self, name: str, base_catalog_path: Path | None = None) -> SceneRenderCatalogFile:
        """Create a user catalog file by copying an existing catalog document."""
        catalog_name = name.strip()
        if not catalog_name:
            raise ValueError("Render catalog name is required.")

        self._catalogs_dir.mkdir(parents=True, exist_ok=True)
        base_path = base_catalog_path or self.built_in_catalog_path
        document = _read_catalog_document(base_path)
        slug = self._unique_slug(catalog_name)
        metadata = _catalog_metadata(document, base_path)
        metadata["description"] = f"Based on {metadata.get('name', base_path.stem)}."
        metadata["id"] = f"user.{slug}"
        metadata["name"] = catalog_name

        catalog_path = self._catalogs_dir / f"{slug}.json"
        _write_catalog_document(catalog_path, document)
        logger.info(f"Created Scene Render Catalog '{catalog_name}' at {catalog_path}")
        return self._catalog_file_for_path(catalog_path)

    def delete_catalog(self, catalog_path: Path) -> None:
        """Delete a user catalog file; a deleted default catalog makes the default built-in catalog the default."""
        path = self._user_catalog_path(catalog_path, "Built-in render catalogs cannot be removed.")
        path.unlink()
        if path == self._chosen_default_path():
            self._default_choice_path.unlink(missing_ok=True)
        logger.info(f"Deleted Scene Render Catalog at {path}")

    def is_user_catalog(self, catalog_path: Path) -> bool:
        """Return whether *catalog_path* is a file of the user catalog store, which may be deleted."""
        path = catalog_path.expanduser()
        return path.parent == self._catalogs_dir

    def _user_catalog_path(self, catalog_path: Path, built_in_message: str) -> Path:
        path = catalog_path.expanduser()
        if path in self.built_in_catalog_paths:
            raise ValueError(built_in_message)
        if not self.is_user_catalog(path):
            raise ValueError(f"Render catalog is outside the catalog store: {path}")
        return path

    @property
    def _default_choice_path(self) -> Path:
        return self._catalogs_dir / DEFAULT_RENDER_CATALOG_CHOICE_FILENAME

    def _chosen_default_path(self) -> Path:
        try:
            choice = self._default_choice_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return self.built_in_catalog_path
        except (OSError, ValueError) as exc:
            logger.warning(f"Using the built-in render catalog as default; could not read the default choice: {exc}")
            return self.built_in_catalog_path
        if choice.startswith(_BUILT_IN_CHOICE_PREFIX):
            name = choice.removeprefix(_BUILT_IN_CHOICE_PREFIX)
            return next((path for path in self.built_in_catalog_paths if path.name == name), self.built_in_catalog_path)
        return self._catalogs_dir / choice

    def _catalog_file_for_path(self, path: Path) -> SceneRenderCatalogFile:
        return _built_in_catalog_file(path) if path in self.built_in_catalog_paths else _catalog_file(path)

    def _unique_slug(self, name: str) -> str:
        base_slug = _catalog_filename_slug(name)
        candidate = base_slug
        index = 2
        while (self._catalogs_dir / f"{candidate}.json").exists():
            candidate = f"{base_slug}-{index}"
            index += 1
        return candidate


@cache
def _built_in_catalog_file(path: Path) -> SceneRenderCatalogFile:
    """Read a built-in catalog's metadata once; installed files do not change while running."""
    return _catalog_file(path, is_built_in=True)


def _catalog_file(path: Path, *, is_built_in: bool = False) -> SceneRenderCatalogFile:
    document = _read_catalog_document(path)
    metadata = _catalog_metadata(document, path)
    catalog_id = metadata.get("id")
    name = metadata.get("name")
    if not isinstance(catalog_id, str) or not catalog_id:
        raise TemplateRuntimeError(f"Scene render catalog file '{path}' must contain metadata.id.")
    if not isinstance(name, str) or not name:
        raise TemplateRuntimeError(f"Scene render catalog file '{path}' must contain metadata.name.")
    return SceneRenderCatalogFile(catalog_id=catalog_id, name=name, path=path, is_built_in=is_built_in)


def _configured_render_catalogs_dir() -> Path:
    from ax_devil.modules.settings.config_manager import ConfigManager

    storage = ConfigManager().get("storage", {}) or {}
    if isinstance(storage, Mapping):
        configured_dir = storage.get("render_catalogs_dir")
        if isinstance(configured_dir, str) and configured_dir:
            return Path(configured_dir).expanduser()
    return RENDER_CATALOGS_DIR


def _write_catalog_document(destination: Path, document: Mapping[str, Any]) -> None:
    _validate_catalog_document(document)
    _write_text(destination, f"{json.dumps(document, indent=2)}\n")


def _write_text(destination: Path, text: str) -> None:
    """Replace *destination* atomically, keeping the previous file if writing fails."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as output:
            temporary_path = Path(output.name)
            output.write(text)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_catalog_document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TemplateRuntimeError(f"Scene render catalog file '{path}' must contain an object.")
    return cast(dict[str, Any], value)


def _validate_catalog_document(document: Mapping[str, Any]) -> None:
    from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalogLoader

    SceneRenderCatalogLoader().validate_document(document)


def _catalog_metadata(document: Mapping[str, Any], path: Path) -> dict[str, Any]:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise TemplateRuntimeError(f"Scene render catalog file '{path}' must contain metadata object.")
    return metadata


def _catalog_filename_slug(name: str) -> str:
    slug = _CATALOG_FILENAME_PATTERN.sub("-", name.casefold()).strip("-._")
    return slug or "catalog"
