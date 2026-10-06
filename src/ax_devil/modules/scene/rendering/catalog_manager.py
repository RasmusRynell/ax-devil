"""Shared runtime manager for Scene Render Catalog state."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from ax_devil.modules.scene.rendering.catalog import (
    SceneRenderCatalog,
    SceneRenderCatalogLoader,
    SceneRenderCatalogRevision,
)
from ax_devil.modules.scene.rendering.catalog_store import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogStore,
)
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


FILE_CHECK_DELAY_MS = 150
"""How long file activity must settle before catalogs are checked, so one save that writes twice is one change."""


class SceneRenderCatalogManager(QObject):
    """Own discovered render catalogs, the default catalog, file operations, and compiled catalog reuse.

    Catalog files and their directories are watched, because catalogs are edited outside the app. When a catalog file
    changes, is replaced or removed, ``catalogFileChanged(path)`` fires and the listing is refreshed.
    """

    catalogListingChanged = Signal(object)
    catalogStatusChanged = Signal(str)
    catalogAppliedToAll = Signal(object)
    catalogFileChanged = Signal(object)

    def __init__(
        self,
        *,
        catalog_store: SceneRenderCatalogStore | None = None,
        catalog_loader: SceneRenderCatalogLoader | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog_store = catalog_store or SceneRenderCatalogStore()
        self._catalog_loader = catalog_loader or SceneRenderCatalogLoader()
        built_in = self._catalog_store.built_in_catalog_path
        self._listing = SceneRenderCatalogListing(
            catalogs=(), errors=(), default_path=built_in, chosen_default_path=built_in
        )
        self._compiled_catalogs: dict[Path, tuple[tuple[int, int], SceneRenderCatalogRevision]] = {}
        self._status = ""
        self._initialized = False
        self._fingerprints: dict[Path, tuple[int, int] | None] = {}
        self._watcher = QFileSystemWatcher(self)
        self._file_check = QTimer(self)
        self._file_check.setSingleShot(True)
        self._file_check.setInterval(FILE_CHECK_DELAY_MS)
        self._file_check.timeout.connect(self._check_files)
        self._watcher.fileChanged.connect(lambda _path: self._file_check.start())
        self._watcher.directoryChanged.connect(lambda _path: self._file_check.start())

    @property
    def catalog_store(self) -> SceneRenderCatalogStore:
        """Return the catalog store owned by this manager."""
        return self._catalog_store

    def initialize(self) -> None:
        """Discover render catalogs once for the application."""
        if self._initialized:
            return
        self.refresh_catalogs()
        self._initialized = True

    def listing(self) -> SceneRenderCatalogListing:
        """Return the latest known catalog listing."""
        return self._listing

    def status(self) -> str:
        """Return the latest human-readable catalog status."""
        return self._status

    def built_in_catalog_path(self) -> Path:
        """Return the built-in catalog that is the default until the user chooses another, and the fallback."""
        return self._catalog_store.built_in_catalog_path

    def default_catalog_path(self) -> Path:
        """Return the catalog new selections start from, as of the latest listing."""
        return self._listing.default_path

    def set_default_catalog(self, catalog_path: Path) -> None:
        """Make *catalog_path* the catalog new selections start from, remembered across restarts.

        The catalog is compiled first, so a catalog that fails to load keeps the previous default and raises the load
        error to the caller.
        """
        self.load_catalog(catalog_path)
        self._catalog_store.set_default_catalog(catalog_path)
        self.refresh_catalogs()
        self._set_status(f"Default render catalog: {catalog_path.name}")

    def apply_to_all(self, catalog_path: Path) -> None:
        """Make every open selection use *catalog_path*; nothing is remembered.

        The catalog is compiled before any selection changes, so a catalog that fails to load leaves every selection
        as it was and raises the load error to the caller.
        """
        self.load_catalog(catalog_path)
        self.catalogAppliedToAll.emit(catalog_path)

    def can_apply_to_all(self, catalog_path: Path) -> bool:
        """Return whether *catalog_path* is listed and loads, so it can be applied to every selection."""
        if self._listing.catalog_for(catalog_path) is None:
            return False
        try:
            self.load_revision(catalog_path)
        except (OSError, ValueError):
            return False
        return True

    def can_use_as_default(self, catalog_path: Path) -> bool:
        """Return whether *catalog_path* can become the default: it is usable and not the chosen default already.

        While the chosen default does not load, the default built-in catalog stands in for it and can be chosen.
        """
        return catalog_path != self._listing.chosen_default_path and self.can_apply_to_all(catalog_path)

    def create_selection(
        self, *, visibility: OverlayVisibility = OverlayVisibility(), parent: QObject | None = None
    ) -> "SceneRenderCatalogSelection":
        """Create an independent active catalog selection backed by this manager."""
        return SceneRenderCatalogSelection(self, visibility=visibility, parent=parent)

    def load_catalog(self, catalog_path: Path, *, force_reload: bool = False) -> SceneRenderCatalog:
        """Return a compiled catalog for *catalog_path*, reusing cached compiled state when possible."""
        return self.load_revision(catalog_path, force_reload=force_reload).catalog

    def load_revision(self, catalog_path: Path, *, force_reload: bool = False) -> SceneRenderCatalogRevision:
        """Return a matching source document and compiled catalog, sharing the selections' cache."""
        cache_key = catalog_path.resolve()
        fingerprint = self._catalog_fingerprint(catalog_path)
        cached = self._compiled_catalogs.get(cache_key)
        if not force_reload and cached is not None and cached[0] == fingerprint:
            return cached[1]

        revision = self._catalog_loader.load_revision(catalog_path)
        self._compiled_catalogs[cache_key] = (fingerprint, revision)
        return revision

    def refresh_catalogs(self) -> SceneRenderCatalogListing:
        """Refresh catalog file metadata without compiling every catalog."""
        self._listing = self._catalog_store.list_catalogs_with_errors()
        self._watch()
        self.catalogListingChanged.emit(self._listing)
        self._publish_listing_status()
        return self._listing

    def create_catalog(self, name: str, *, base_catalog_path: Path | None = None) -> SceneRenderCatalogFile:
        """Create a user catalog and refresh shared listing state."""
        catalog = self._catalog_store.create_catalog(name, base_catalog_path=base_catalog_path)
        self.refresh_catalogs()
        self._set_status(f"Created catalog: {name}")
        return catalog

    def delete_catalog(self, catalog_path: Path) -> None:
        """Delete a user catalog and refresh shared listing state."""
        self._catalog_store.delete_catalog(catalog_path)
        self._compiled_catalogs.pop(catalog_path.resolve(), None)
        self.refresh_catalogs()
        self._set_status(f"Deleted render catalog: {catalog_path.name}")

    def _publish_listing_status(self) -> None:
        if self._listing.errors:
            self._set_status(f"Some render catalogs could not be loaded: {len(self._listing.errors)}")
            return
        self._set_status(f"Render catalogs available: {len(self._listing.catalogs)}")

    def _set_status(self, status: str) -> None:
        self._status = status
        self.catalogStatusChanged.emit(status)

    def _catalog_fingerprint(self, catalog_path: Path) -> tuple[int, int]:
        stat = catalog_path.stat()
        return (stat.st_mtime_ns, stat.st_size)

    def _watched_files(self) -> list[Path]:
        """Return the default built-in catalog and every listed file, including invalid ones, which may be fixed."""
        listed = (catalog.path for catalog in self._listing.catalogs)
        invalid = (error.path for error in self._listing.errors)
        return list(dict.fromkeys((self.built_in_catalog_path(), *listed, *invalid)))

    def _watch(self) -> None:
        """Watch every listed catalog file and the directories holding them; remember how new files look now."""
        files = self._watched_files()
        directories = {self._catalog_store.catalogs_dir, *(path.parent for path in files)}
        watched = set(self._watcher.files()) | set(self._watcher.directories())
        missing = [str(path) for path in (*files, *directories) if path.exists() and str(path) not in watched]
        if missing:
            self._watcher.addPaths(missing)
        for path in files:
            self._fingerprints.setdefault(path, self._fingerprint_or_none(path))

    def _check_files(self) -> None:
        """Report every known catalog file that changed, appeared again or disappeared, and refresh the listing.

        Editors often save by replacing the file, which ends the watch on it; the listing refresh watches it again.
        """
        changed = []
        for path, previous in list(self._fingerprints.items()):
            current = self._fingerprint_or_none(path)
            if current != previous:
                self._fingerprints[path] = current
                changed.append(path)
        try:
            self.refresh_catalogs()
        except OSError as error:
            self._set_status(f"Could not refresh render catalogs: {error}")
        for path in changed:
            logger.info(f"Render catalog file changed: {path}")
            self.catalogFileChanged.emit(path)

    def _fingerprint_or_none(self, catalog_path: Path) -> tuple[int, int] | None:
        try:
            return self._catalog_fingerprint(catalog_path)
        except OSError:
            return None


class SceneRenderCatalogSelection(QObject):
    """Own one consumer's active render catalog selection, starting from the manager's default catalog.

    The active path is the catalog the user chose. The active catalog is the last one that compiled; after a failed
    load it is kept, and may be an older revision or another file. Every load attempt of the active path, including
    the validation done by ``apply_to_all()`` and ``use_as_default()``, records its failure, and only a successful
    compile clears it, so ``active_catalog_loaded()`` is true exactly when the latest attempt compiled.
    ``selectionChanged`` fires after every change to the path, catalog, load state or status, including every listing
    change; ``activeCatalogChanged`` fires only when the catalog in use changes.
    """

    activeCatalogChanged = Signal(object)
    selectionChanged = Signal()

    def __init__(
        self,
        manager: SceneRenderCatalogManager,
        *,
        visibility: OverlayVisibility = OverlayVisibility(),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._active_catalog_path = manager.default_catalog_path()
        self._active_catalog: SceneRenderCatalog | None = None
        self._revision: SceneRenderCatalogRevision | None = None
        self._visibility = visibility
        self._loaded_catalog_path: Path | None = None
        self._load_error: str | None = None
        self._status = ""
        self._manager.catalogListingChanged.connect(self._publish_listing_status)
        self._manager.catalogAppliedToAll.connect(self.select_catalog)
        self._manager.catalogFileChanged.connect(self._on_catalog_file_changed)
        self.select_catalog(self._active_catalog_path, force_emit=True)
        if self._active_catalog is None and self._active_catalog_path != manager.built_in_catalog_path():
            failure = self._status
            self.select_catalog(manager.built_in_catalog_path(), force_emit=True)
            self._publish(f"Using the default built-in catalog because the chosen default could not be used. {failure}")

    @property
    def manager(self) -> SceneRenderCatalogManager:
        """Return the shared catalog manager backing this selection."""
        return self._manager

    def active_catalog_path(self) -> Path:
        """Return this selection's active catalog path, even when that catalog failed to load."""
        return self._active_catalog_path

    def active_catalog(self) -> SceneRenderCatalog | None:
        """Return this selection's compiled active catalog, if one loaded successfully."""
        return self._active_catalog

    @property
    def visibility(self) -> OverlayVisibility:
        """Return this view's choices, including features absent from its current catalog."""
        return self._visibility

    def available_features(self) -> frozenset[OverlayFeature]:
        """Return semantic groups declared by the last successfully loaded catalog."""
        return frozenset() if self._revision is None else self._revision.catalog.supported_features

    def set_feature_enabled(self, feature: OverlayFeature, enabled: bool) -> None:
        """Specialize one visibility change and refresh the current overlay."""
        self._set_visibility(self._visibility.with_feature(feature, enabled))

    def reset_visibility(self) -> None:
        """Enable all features, including choices retained from other catalogs."""
        self._set_visibility(OverlayVisibility())

    def _set_visibility(self, visibility: OverlayVisibility) -> None:
        """Atomically apply a complete view preference, retaining last-good output on failure."""
        if visibility == self._visibility:
            return
        if self._revision is None:
            self._visibility = visibility
            self.selectionChanged.emit()
            return
        try:
            catalog = self._revision.specialize(visibility)
        except ValueError as exc:
            self._publish(f"Could not change overlay details: {exc}")
            return
        previous = self._active_catalog
        self._visibility = visibility
        self._active_catalog = catalog
        self.selectionChanged.emit()
        if previous is None or previous.rendering_identity != catalog.rendering_identity:
            self.activeCatalogChanged.emit(catalog)

    def active_catalog_loaded(self) -> bool:
        """Return whether the latest load attempt of the active path compiled the catalog now in use."""
        return self._active_catalog is not None and self._load_error is None

    def status(self) -> str:
        """Return this selection's latest human-readable status."""
        return self._status

    def apply_to_all(self) -> None:
        """Make every open selection use the active catalog; a failure to load it is recorded on this selection."""
        try:
            self._manager.apply_to_all(self._active_catalog_path)
        except (OSError, ValueError) as exc:
            self._fail_load(self._active_catalog_path, exc)

    def use_as_default(self) -> None:
        """Make the active catalog the default for new selections; a failure to load it is recorded here."""
        try:
            self._manager.load_catalog(self._active_catalog_path)
        except (OSError, ValueError) as exc:
            self._fail_load(self._active_catalog_path, exc)
            return
        try:
            self._manager.set_default_catalog(self._active_catalog_path)
        except (OSError, ValueError) as exc:
            self._publish(f"Could not set the default render catalog: {exc}")

    def select_catalog(self, catalog_path: Path, *, force_emit: bool = False) -> None:
        """Compile and activate *catalog_path* for this selection."""
        try:
            revision = self._manager.load_revision(catalog_path)
            catalog = revision.specialize(self._visibility)
        except (OSError, ValueError) as exc:
            self._fail_load(catalog_path, exc)
            return
        self._revision = revision
        self._set_active_catalog(
            catalog, catalog_path, status=f"Selected catalog: {catalog.catalog_id}", force_emit=force_emit
        )

    def reload_active_catalog(self) -> None:
        """Refresh the shared catalog listing and recompile this selection's active catalog path."""
        try:
            self._manager.refresh_catalogs()
        except OSError as exc:
            self._publish(f"Could not refresh render catalogs: {exc}")
            return
        try:
            revision = self._manager.load_revision(self._active_catalog_path, force_reload=True)
            catalog = revision.specialize(self._visibility)
        except (OSError, ValueError) as exc:
            self._fail_load(self._active_catalog_path, exc)
            return
        self._revision = revision
        if (
            self.active_catalog_loaded()
            and self._active_catalog is not None
            and catalog.content_hash == self._active_catalog.content_hash
        ):
            self._publish("Selected catalog is current.")
            return
        self._set_active_catalog(catalog, self._active_catalog_path, status="Render catalog reloaded.", force_emit=True)

    def refresh_catalogs(self) -> SceneRenderCatalogListing:
        """Refresh shared catalog metadata; the listing signal updates this selection's status."""
        return self._manager.refresh_catalogs()

    def _set_active_catalog(
        self,
        catalog: SceneRenderCatalog,
        catalog_path: Path,
        *,
        status: str,
        force_emit: bool,
    ) -> None:
        previous_catalog = self._active_catalog
        previous_path = self._loaded_catalog_path
        self._active_catalog = catalog
        self._active_catalog_path = catalog_path
        self._loaded_catalog_path = catalog_path
        self._load_error = None
        self._publish(status)
        if (
            force_emit
            or previous_catalog is None
            or previous_catalog.rendering_identity != catalog.rendering_identity
            or previous_path != catalog_path
        ):
            logger.debug(f"Scene Render Catalog selection set to {catalog.catalog_id} from {catalog_path}")
            self.activeCatalogChanged.emit(catalog)

    def _on_catalog_file_changed(self, catalog_path: Path) -> None:
        """Load the new version of the active catalog's file; one that no longer loads keeps the last version in use.

        Selections showing other catalogs are untouched.
        """
        if catalog_path == self._active_catalog_path:
            self.select_catalog(catalog_path)

    def _fail_load(self, catalog_path: Path, error: Exception) -> None:
        """Make *catalog_path* active with a recorded load failure, keeping the last compiled catalog in use."""
        self._active_catalog_path = catalog_path
        self._load_error = str(error)
        self._publish(f"Render catalog error: {error}")

    def _publish_listing_status(self, listing: SceneRenderCatalogListing) -> None:
        """Derive the status from the listing and load state; it never hides a recorded load failure."""
        path = self._active_catalog_path
        listing_error = listing.error_for(path)
        if listing_error is not None:
            status = f"Active render catalog is invalid: {listing_error.message}"
        elif listing.catalog_for(path) is None:
            status = f"Active render catalog is missing: {path}"
        elif self._load_error is not None:
            status = f"Render catalog error: {self._load_error}"
        elif self._active_catalog is not None:
            status = f"Selected catalog: {self._active_catalog.catalog_id}"
        else:
            status = self._status
        self._publish(status)

    def _publish(self, status: str) -> None:
        """Record *status* and tell observers that the selection changed; every selection change ends here."""
        self._status = status
        self.selectionChanged.emit()


def create_scene_render_catalog_manager(
    *,
    catalog_store: SceneRenderCatalogStore | None = None,
    parent: QObject | None = None,
) -> SceneRenderCatalogManager:
    """Create and initialize a Scene Render Catalog manager."""
    manager = SceneRenderCatalogManager(catalog_store=catalog_store, parent=parent)
    manager.initialize()
    return manager
