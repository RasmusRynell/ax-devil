"""Shared runtime manager for Scene Render Catalog state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ax_devil.modules.background_jobs import BackgroundJobs, LatestRequest, background_jobs
from ax_devil.modules.file_watching import FileWatcher
from ax_devil.modules.scene.rendering.catalog_jobs import (
    CatalogRevisions,
    Scan,
    Waiter,
    create_catalog_file,
    nothing,
    scan_catalogs,
)
from ax_devil.modules.scene.rendering.catalog_loader import SceneRenderCatalogLoader, SceneRenderCatalogRevision
from ax_devil.modules.scene.rendering.catalog_selection import SceneRenderCatalogSelection
from ax_devil.modules.scene.rendering.catalog_store import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogStore,
)
from ax_devil.modules.scene.rendering.catalogs_in_use import CatalogsInUse
from ax_devil.modules.scene.rendering.visibility import OverlayVisibility
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


FILE_CHECK_DELAY_MS = 150
"""How long file activity must settle before catalogs are checked, so one save that writes twice is one change."""

LISTING_RETRY_DELAY_MS = 5000
"""How long to wait before listing again after the first listing failed, until one succeeds."""

_WATCH_KEY = "catalogs"

_POOL = "render-catalogs"
_POOL_WORKERS = 2
"""Catalog files may sit on slow storage, so listing and loading never share workers with other background work."""


class SceneRenderCatalogManager(QObject):
    """Own discovered render catalogs, the default catalog, file operations, and compiled catalog reuse.

    Listing, fingerprinting and loading catalog files run on a background pool and reach this GUI-thread object as
    plain results; callers ask with callbacks, and a newer request replaces an older one whose result is no longer
    wanted. The compiled-revision cache is current until a listing finds the file changed.

    Catalog files and their directories are watched, because catalogs are edited outside the app. When a catalog file
    changes, is replaced or removed, ``catalogFileChanged(path)`` fires and the listing is refreshed.
    """

    catalogListingChanged = Signal(object)
    catalogStatusChanged = Signal(str)
    catalogAppliedToAll = Signal(object)
    catalogFileChanged = Signal(object)
    catalogUsabilityChanged = Signal()
    """Whether a catalog can be applied to all or made default, or which catalogs open views use, may have changed
    without a listing change."""

    def __init__(
        self,
        *,
        catalog_store: SceneRenderCatalogStore | None = None,
        catalog_loader: SceneRenderCatalogLoader | None = None,
        jobs: BackgroundJobs | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog_store = catalog_store or SceneRenderCatalogStore()
        self._jobs = (jobs or background_jobs(_POOL, max_workers=_POOL_WORKERS)).scope(self)
        self.catalogs_in_use = CatalogsInUse(self.catalogUsabilityChanged.emit)
        self._revisions = CatalogRevisions(
            self._jobs, catalog_loader or SceneRenderCatalogLoader(), self.catalogUsabilityChanged.emit
        )
        # Copies allocate a unique filename and write it as one ordered operation across all managers.
        self._mutations = background_jobs("render-catalog-mutations", max_workers=1).scope(self)
        built_in = self._catalog_store.built_in_catalog_path
        self._listing = SceneRenderCatalogListing(
            catalogs=(), errors=(), default_path=built_in, chosen_default_path=built_in
        )
        self._listing_ready = False
        self._listing_discovered = False
        self._clock = 0
        self._newest_apply = 0
        self._apply_request = LatestRequest(self)
        self._default_request = LatestRequest(self)
        self._scan_request = LatestRequest(self)
        self._scan_waiters: list[Waiter[SceneRenderCatalogListing]] = []
        self._status = ""
        self._initialized = False
        self._catalog_files: frozenset[Path] = frozenset()
        self._watcher = FileWatcher(self, quiet_ms=FILE_CHECK_DELAY_MS)
        self._watcher.changed.connect(self._files_changed)
        self._listing_retry = QTimer(self)
        self._listing_retry.setSingleShot(True)
        self._listing_retry.setInterval(LISTING_RETRY_DELAY_MS)
        self._listing_retry.timeout.connect(self.refresh_catalogs)

    @property
    def catalog_store(self) -> SceneRenderCatalogStore:
        """Return the catalog store owned by this manager."""
        return self._catalog_store

    @property
    def listing_ready(self) -> bool:
        """Return whether the first listing has arrived, so the default catalog is known."""
        return self._listing_ready

    @property
    def listing_discovered(self) -> bool:
        """Return whether a listing was read from disk; until then the default is a stand-in and is listed again."""
        return self._listing_discovered

    @property
    def is_loading(self) -> bool:
        """Return whether a listing or catalog load is still running."""
        return self._scan_request.pending or self._revisions.loading

    def initialize(self) -> None:
        """Start discovering render catalogs once for the application; ``catalogListingChanged`` reports them."""
        if self._initialized:
            return
        self._initialized = True
        self.refresh_catalogs()

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

        This does not compile the catalog; ask ``request_revision(..., force_reload=True)`` first, so a catalog that
        fails to load never becomes the default. A catalog that cannot be stored keeps the previous default and raises
        the error to the caller. The new default counts at once, so a view opened before the listing refresh returns
        starts on it.
        """
        self._catalog_store.set_default_catalog(catalog_path)
        self._listing = replace(self._listing, default_path=catalog_path, chosen_default_path=catalog_path)
        self.refresh_catalogs(on_done=lambda _listing: self._set_status(f"Default render catalog: {catalog_path.name}"))

    def use_as_default(
        self,
        catalog_path: Path,
        *,
        on_failed: Callable[[Exception], None],
        on_not_saved: Callable[[Exception], None],
        on_done: Callable[[], None] = nothing,
    ) -> Callable[[], None]:
        """Make *catalog_path* the default for new selections once it has been read again and compiled.

        A catalog that fails to load goes to *on_failed* and keeps the previous default; one that cannot be stored
        goes to *on_not_saved*. The newest request wins: asking cancels the previous one. Return a function that
        cancels the request. Successful persistence calls *on_done*.
        """

        def loaded(_revision: SceneRenderCatalogRevision) -> None:
            try:
                self.set_default_catalog(catalog_path)
            except (OSError, ValueError) as error:
                on_not_saved(error)
            else:
                on_done()

        return self._default_request.start(
            lambda result, error: self.request_revision(
                catalog_path, force_reload=True, on_loaded=result, on_failed=error
            ),
            on_result=loaded,
            on_error=on_failed,
        )

    def apply_to_all(
        self,
        catalog_path: Path,
        *,
        on_failed: Callable[[Exception], None],
        on_done: Callable[[], None] = nothing,
    ) -> Callable[[], None]:
        """Make every open selection use *catalog_path*; nothing is remembered.

        The catalog is read again and compiled before any selection changes. A catalog that fails to load leaves every
        selection as it was and goes to *on_failed*. The newest request wins: asking cancels the previous one.
        A selection its user chose a catalog for after the request was made keeps that choice. Return a function that
        cancels the request. Successful application calls *on_done*.
        """

        def loaded(_revision: SceneRenderCatalogRevision) -> None:
            self.catalogAppliedToAll.emit(catalog_path)
            on_done()

        self._newest_apply = self.stamp()
        return self._apply_request.start(
            lambda result, error: self.request_revision(
                catalog_path, force_reload=True, on_loaded=result, on_failed=error
            ),
            on_result=loaded,
            on_error=on_failed,
        )

    def stamp(self) -> int:
        """Return a number above every one returned before, to order a user's choice against an apply to all."""
        self._clock += 1
        return self._clock

    @property
    def newest_apply_stamp(self) -> int:
        """Return the stamp of the newest apply-to-all request; ``catalogAppliedToAll`` announces only that one."""
        return self._newest_apply

    def can_apply_to_all(self, catalog_path: Path) -> bool:
        """Return whether *catalog_path* is listed and its current file compiled, so it can go to every selection.

        Only known results are consulted, so a catalog nobody has loaded yet is not eligible and asking never reads
        a file.
        """
        return self._listing.catalog_for(catalog_path) is not None and self._revisions.is_compiled(catalog_path)

    def can_use_as_default(self, catalog_path: Path) -> bool:
        """Return whether *catalog_path* can become the default: it is usable and not the chosen default already.

        While the chosen default does not load, the default built-in catalog stands in for it and can be chosen.
        """
        return catalog_path != self._listing.chosen_default_path and self.can_apply_to_all(catalog_path)

    def create_selection(
        self, *, visibility: OverlayVisibility = OverlayVisibility(), parent: QObject | None = None
    ) -> SceneRenderCatalogSelection:
        """Create an independent active catalog selection backed by this manager."""
        return SceneRenderCatalogSelection(self, visibility=visibility, parent=parent)

    def request_revision(
        self,
        catalog_path: Path,
        *,
        on_loaded: Callable[[SceneRenderCatalogRevision], None],
        on_failed: Callable[[Exception], None],
        force_reload: bool = False,
    ) -> Callable[[], None]:
        """Get the source document and compiled catalog of *catalog_path*, sharing the selections' cache.

        Call exactly one of *on_loaded* or *on_failed* on the GUI thread: before returning when the cached revision is
        current, otherwise after a worker read and compiled the file. Consumers asking for the same file share one
        read; *force_reload* reads the file again, and every consumer then waiting receives that newer revision.
        Return a function that cancels this request; a read nobody waits for any more stops.
        """
        return self._revisions.request(
            catalog_path, on_loaded=on_loaded, on_failed=on_failed, force_reload=force_reload
        )

    def refresh_catalogs(
        self,
        *,
        on_done: Callable[[SceneRenderCatalogListing], None] | None = None,
        on_failed: Callable[[Exception], None] | None = None,
    ) -> Callable[[], None]:
        """Refresh catalog file metadata without compiling every catalog.

        The listing arrives through ``catalogListingChanged``, then *on_done* is called with it; a listing that cannot
        be read goes to *on_failed* and the status. Asking again replaces a refresh still running, so *on_done* sees
        the newest listing. Return a function that cancels *on_done* and *on_failed*.
        """
        waiter = Waiter(on_done, on_failed)
        self._scan_waiters.append(waiter)
        known = self._revisions.known()
        self._scan_request.submit(
            self._jobs,
            "render catalog listing",
            partial(scan_catalogs, self._catalog_store, known),
            on_result=self._scanned,
            on_error=self._scan_failed,
        )
        return waiter.cancel

    def create_catalog(
        self,
        name: str,
        *,
        base_catalog_path: Path | None = None,
        on_created: Callable[[SceneRenderCatalogFile], None],
        on_failed: Callable[[Exception], None],
    ) -> Callable[[], None]:
        """Copy a catalog on the serial mutation pool, then refresh the shared listing.

        Accepted copies finish even if their consumer loses interest. Return a function that detaches the callbacks;
        it does not cancel the write or the manager's listing refresh. Results and errors reach the GUI thread.
        """
        waiter = Waiter(on_created, on_failed)
        self._mutations.submit(
            "create render catalog",
            partial(create_catalog_file, self._catalog_store, name, base_catalog_path),
            on_result=partial(self._created, waiter),
            on_error=partial(self._create_failed, waiter),
        )
        return waiter.cancel

    def _created(self, waiter: Waiter[SceneRenderCatalogFile], catalog: SceneRenderCatalogFile) -> None:
        self.refresh_catalogs(on_done=lambda _listing: self._set_status(f"Created catalog: {catalog.name}"))
        waiter.succeed(catalog)

    def _create_failed(self, waiter: Waiter[SceneRenderCatalogFile], error: Exception) -> None:
        self._set_status(f"Could not create render catalog: {error}")
        waiter.fail(error)

    def delete_catalog(self, catalog_path: Path) -> None:
        """Delete a user catalog and refresh shared listing state; requests still reading it fail."""
        self._catalog_store.delete_catalog(catalog_path)
        if self._listing.chosen_default_path == catalog_path:
            built_in = self.built_in_catalog_path()
            self._listing = replace(self._listing, default_path=built_in, chosen_default_path=built_in)
        self._revisions.forget(catalog_path)
        self.refresh_catalogs(on_done=lambda _listing: self._set_status(f"Deleted render catalog: {catalog_path.name}"))

    def _scanned(self, scan: Scan) -> None:
        """Take a worker's findings: follow what changed on disk, then tell listeners and the watcher."""
        waiters, self._scan_waiters = self._scan_waiters, []
        self._listing = scan.listing
        fingerprints = scan.snapshot.fingerprints
        stale = self._revisions.drop_changed(fingerprints)
        self._catalog_files = scan.snapshot.paths.files
        self._watcher.watch(_WATCH_KEY, scan.snapshot)
        self._listing_ready = self._listing_discovered = True
        self.catalogListingChanged.emit(self._listing)
        self._publish_listing_status()
        for waiter in waiters:
            waiter.succeed(self._listing)
        for path in stale:
            if not self._revisions.is_reading(path) and path in fingerprints:
                self.request_revision(path, on_loaded=self._usability_changed, on_failed=self._usability_changed)

    def _scan_failed(self, error: Exception) -> None:
        waiters, self._scan_waiters = self._scan_waiters, []
        self._set_status(f"Could not refresh render catalogs: {error}")
        if not self._listing_ready:
            self._listing_ready = True
            self.catalogListingChanged.emit(self._listing)
        if not self._listing_discovered:
            self._listing_retry.start()
        for waiter in waiters:
            waiter.fail(error)

    def _usability_changed(self, _result: object) -> None:
        self.catalogUsabilityChanged.emit()

    def _publish_listing_status(self) -> None:
        if self._listing.errors:
            self._set_status(f"Some render catalogs could not be loaded: {len(self._listing.errors)}")
            return
        self._set_status(f"Render catalogs available: {len(self._listing.catalogs)}")

    def _set_status(self, status: str) -> None:
        self._status = status
        self.catalogStatusChanged.emit(status)

    def _files_changed(self, _key: str, paths: frozenset[Path]) -> None:
        """Restart the reads of changed catalog files, then list again and report each once the listing is current.

        The listing drops the compiled catalogs that changed and compiles them again in the background.
        """
        changed = sorted(paths & self._catalog_files)
        for path in changed:
            self._revisions.restart(path)
        self.refresh_catalogs(on_done=lambda _listing: self._report_changed(changed))

    def _report_changed(self, changed: list[Path]) -> None:
        for path in changed:
            logger.info(f"Render catalog file changed: {path}")
            self.catalogFileChanged.emit(path)


def create_scene_render_catalog_manager(
    *,
    catalog_store: SceneRenderCatalogStore | None = None,
    parent: QObject | None = None,
) -> SceneRenderCatalogManager:
    """Create a Scene Render Catalog manager and start discovering catalogs; selections wait for the first listing."""
    manager = SceneRenderCatalogManager(catalog_store=catalog_store, parent=parent)
    manager.initialize()
    return manager
