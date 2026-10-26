"""One consumer's active catalog and visibility choices over the shared catalog manager."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, SignalInstance

from ax_devil.modules.background_jobs import LatestRequest
from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalog
from ax_devil.modules.scene.rendering.catalog_jobs import nothing
from ax_devil.modules.scene.rendering.catalog_loader import SceneRenderCatalogRevision
from ax_devil.modules.scene.rendering.catalog_store import (
    SceneRenderCatalogListing,
)
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.settings.logging_config import get_logger

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering.catalog_manager import SceneRenderCatalogManager

logger = get_logger(__name__)


class SceneRenderCatalogSelection(QObject):
    """Own one consumer's active render catalog selection, starting from the manager's default catalog.

    The active path is the catalog the user chose, from the moment they choose it. The active catalog is the last one
    that compiled; until the chosen path loads, and after a failed load, it is kept, and may be an older revision or
    another file. Every load attempt of the active path, including the validation done by ``apply_to_all()`` and
    ``use_as_default()``, records its failure, and only a successful compile clears it, so ``active_catalog_loaded()``
    is true exactly when the active catalog compiled from the active path and the latest attempt succeeded.
    ``selectionChanged`` fires after every change to the path, catalog, load state or status, including every listing
    change; ``activeCatalogChanged`` fires only when the catalog in use changes.

    Loading is asynchronous: a catalog that is not cached becomes the active one when its worker finishes. What the
    view shows has one request at a time: a newer select, reload, file change or apply to all replaces one still
    running, so only the latest is delivered. The user's apply or default is a separate command that only a newer
    command or select replaces; file changes and other views' applies never cancel it. A catalog the user selects
    here after an apply to all was requested stays, so a slow apply does not overwrite the newer choice. Until the
    first catalog loads
    there is no active catalog. The owner calls ``cleanup()`` when it ends.
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
        self._chosen_at = 0
        self._load_error: str | None = None
        self._status = ""
        self._showing = LatestRequest(self)
        self._command = LatestRequest(self)
        self._manager_slots: list[tuple[SignalInstance, Callable[..., None]]] = [
            (manager.catalogListingChanged, self._publish_listing_status),
            (manager.catalogListingChanged, self._restart_when_discovered),
            (manager.catalogAppliedToAll, self._on_applied_to_all),
            (manager.catalogFileChanged, self._on_catalog_file_changed),
            (manager.catalogUsabilityChanged, self._republish),
        ]
        self._started = False
        self._started_before_discovery = False
        if not manager.listing_ready:
            self._manager_slots.append((manager.catalogListingChanged, self._start_when_listed))
        for signal, slot in self._manager_slots:
            signal.connect(slot)
        if manager.listing_ready:
            self._start()

    def cleanup(self) -> None:
        """Stop listening to the manager and drop every request still waiting; the owner calls it when it ends.

        Requests keep their callbacks alive, so a selection whose owner merely stops using it would still apply or
        make default a catalog afterwards.
        """
        self._showing.close()
        self._command.close()
        for signal, slot in self._manager_slots:
            with suppress(RuntimeError, TypeError):
                signal.disconnect(slot)
        self._manager_slots.clear()

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
        """Return whether the catalog in use compiled from the active path and the latest attempt on it succeeded."""
        return (
            self._active_catalog is not None
            and self._load_error is None
            and self._loaded_catalog_path == self._active_catalog_path
        )

    def status(self) -> str:
        """Return this selection's latest human-readable status."""
        return self._status

    def apply_to_all(self) -> None:
        """Make every open selection use the active catalog; a failure to load it is recorded on this selection."""
        path = self._active_catalog_path
        self._command.start(
            lambda result, error: self._manager.apply_to_all(path, on_done=partial(result, None), on_failed=error),
            on_result=nothing,
            on_error=partial(self._fail_load, path),
        )

    def use_as_default(self) -> None:
        """Make the active catalog the default for new selections; a failure to load it is recorded here."""
        path = self._active_catalog_path
        self._command.start(
            lambda result, error: self._manager.use_as_default(
                path, on_done=partial(result, None), on_failed=error, on_not_saved=result
            ),
            on_result=self._default_finished,
            on_error=partial(self._fail_load, path),
        )

    def select_catalog(self, catalog_path: Path, *, force_emit: bool = False) -> None:
        """Choose *catalog_path* for this selection now and activate it once it has loaded."""
        self._command.cancel()
        self._chosen_at = self._manager.stamp()
        self._show(catalog_path, on_loaded=partial(self._adopt, catalog_path, force_emit=force_emit))

    def reload_active_catalog(self) -> None:
        """Refresh the shared catalog listing and recompile this selection's active catalog path."""
        self._started = True
        self._showing.start(
            lambda result, error: self._manager.refresh_catalogs(on_done=result, on_failed=error),
            on_result=self._reload,
            on_error=self._refresh_failed,
        )

    def refresh_catalogs(self) -> None:
        """Refresh shared catalog metadata; the listing signal updates this selection's status."""
        self._manager.refresh_catalogs()

    def _show(
        self,
        catalog_path: Path,
        *,
        on_loaded: Callable[[SceneRenderCatalogRevision], None],
        force_reload: bool = False,
        on_failed: Callable[[Exception], None] | None = None,
    ) -> None:
        """Choose *catalog_path* and ask for it, replacing the load this selection still waits for.

        The path counts as chosen at once; the catalog in use stays until the new one arrives.
        """
        self._started = True
        if catalog_path != self._active_catalog_path:
            self._active_catalog_path = catalog_path
            self._load_error = None
        if catalog_path != self._loaded_catalog_path:
            self._publish(f"Loading {catalog_path.name}…")
        failed = on_failed or partial(self._fail_load, catalog_path)
        self._showing.start(
            lambda result, error: self._manager.request_revision(
                catalog_path, force_reload=force_reload, on_loaded=result, on_failed=error
            ),
            on_result=on_loaded,
            on_error=failed,
        )

    def _follow(self, catalog_path: Path) -> None:
        """Show *catalog_path* after its file changed or another view applied it; a pending command stays."""
        self._show(catalog_path, on_loaded=partial(self._adopt, catalog_path))

    def _on_applied_to_all(self, catalog_path: Path) -> None:
        """Follow an apply to all, unless the user chose a catalog here after it was requested."""
        if self._chosen_at < self._manager.newest_apply_stamp:
            self._follow(catalog_path)

    def _start_when_listed(self, _listing: object) -> None:
        if not self._started:
            self._start()

    def _start(self) -> None:
        """Load the default catalog; one that does not compile gives way to the default built-in catalog."""
        self._started_before_discovery = not self._manager.listing_discovered
        path = self._manager.default_catalog_path()
        self._show(
            path,
            on_loaded=partial(self._adopt, path, force_emit=True),
            on_failed=partial(self._start_failed, path),
        )

    def _restart_when_discovered(self, _listing: object) -> None:
        """Start again from the real default when the first listing failed and this selection took the stand-in.

        A selection the user or an apply to all has since moved to another catalog keeps it.
        """
        if not (self._started_before_discovery and self._manager.listing_discovered):
            return
        self._started_before_discovery = False
        if self._chosen_at == 0 and self._active_catalog_path == self._manager.built_in_catalog_path():
            if self._manager.default_catalog_path() != self._active_catalog_path:
                self._start()

    def _start_failed(self, catalog_path: Path, error: Exception) -> None:
        self._fail_load(catalog_path, error)
        built_in = self._manager.built_in_catalog_path()
        if catalog_path == built_in:
            return
        failure = self._status
        self._show(built_in, on_loaded=partial(self._adopt_built_in, built_in, failure))

    def _adopt_built_in(self, catalog_path: Path, failure: str, revision: SceneRenderCatalogRevision) -> None:
        self._adopt(catalog_path, revision, force_emit=True)
        self._publish(f"Using the default built-in catalog because the chosen default could not be used. {failure}")

    def _adopt(self, catalog_path: Path, revision: SceneRenderCatalogRevision, *, force_emit: bool = False) -> None:
        try:
            catalog = revision.specialize(self._visibility)
        except ValueError as exc:
            self._fail_load(catalog_path, exc)
            return
        self._revision = revision
        self._set_active_catalog(
            catalog, catalog_path, status=f"Selected catalog: {catalog.catalog_id}", force_emit=force_emit
        )

    def _reload(self, _listing: object) -> None:
        path = self._active_catalog_path
        self._show(path, force_reload=True, on_loaded=partial(self._adopt_reloaded, path))

    def _adopt_reloaded(self, catalog_path: Path, revision: SceneRenderCatalogRevision) -> None:
        try:
            catalog = revision.specialize(self._visibility)
        except ValueError as exc:
            self._fail_load(catalog_path, exc)
            return
        self._revision = revision
        if (
            self.active_catalog_loaded()
            and self._active_catalog is not None
            and catalog.content_hash == self._active_catalog.content_hash
        ):
            self._publish("Selected catalog is current.")
            return
        self._set_active_catalog(catalog, catalog_path, status="Render catalog reloaded.", force_emit=True)

    def _refresh_failed(self, error: Exception) -> None:
        self._publish(f"Could not refresh render catalogs: {error}")

    def _default_finished(self, error: Exception | None) -> None:
        """Finish persistence separately from load failure: a valid catalog remains usable if saving fails."""
        if error is not None:
            self._publish(f"Could not set the default render catalog: {error}")

    def _republish(self) -> None:
        self._publish(self._status)

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
            self._follow(catalog_path)

    def _fail_load(self, catalog_path: Path, error: Exception) -> None:
        """Report a failed load; one of the active path is recorded, keeping the last compiled catalog in use."""
        if catalog_path == self._active_catalog_path:
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
        elif self._active_catalog is not None and self._loaded_catalog_path == path:
            status = f"Selected catalog: {self._active_catalog.catalog_id}"
        else:
            status = self._status
        self._publish(status)

    def _publish(self, status: str) -> None:
        """Record *status* and tell observers that the selection changed; every selection change ends here."""
        self._status = status
        self.selectionChanged.emit()
