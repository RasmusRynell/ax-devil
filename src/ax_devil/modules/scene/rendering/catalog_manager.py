"""Shared runtime manager for Scene Render Catalog state."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Generic, TypeVar

from PySide6.QtCore import QObject, QTimer, Signal, SignalInstance

from ax_devil.core.source_paths import Fingerprint, SourcePaths, SourceSnapshot
from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, LatestRequest, background_jobs
from ax_devil.modules.file_watching import FileWatcher
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

LISTING_RETRY_DELAY_MS = 5000
"""How long to wait before listing again after the first listing failed, until one succeeds."""

_WATCH_KEY = "catalogs"

_POOL = "render-catalogs"
_POOL_WORKERS = 2
"""Catalog files may sit on slow storage, so listing and loading never share workers with other background work."""

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _Scan:
    """What a worker found on disk: the listing, and the catalog files and their folder as they were."""

    listing: SceneRenderCatalogListing
    snapshot: SourceSnapshot


@dataclass(frozen=True, slots=True)
class _Loaded:
    """A compiled revision with the fingerprint the file had just before it was read."""

    fingerprint: Fingerprint
    revision: SceneRenderCatalogRevision


def _scan(store: SceneRenderCatalogStore, known: tuple[Path, ...], context: JobContext[None]) -> _Scan:
    """List the catalogs and fingerprint them, the chosen default and every path in *known*; runs on a worker."""
    token = context.token
    listing = store.list_catalogs_with_errors(token)
    listed = (catalog.path for catalog in listing.catalogs)
    invalid = (error.path for error in listing.errors)
    files = dict.fromkeys((store.built_in_catalog_path, *listed, *invalid, *known, listing.chosen_default_path))
    paths = SourcePaths(files=frozenset(files), folders=frozenset({store.catalogs_dir}))
    return _Scan(listing, paths.snapshot(token))


def _load_revision(loader: SceneRenderCatalogLoader, path: Path, _context: JobContext[None]) -> _Loaded:
    """Read and compile *path*; runs on a worker. The fingerprint comes first, so a later save reads as newer."""
    fingerprint = Fingerprint.of_stat(path.stat())
    return _Loaded(fingerprint, loader.load_revision(path))


def _create_catalog(
    store: SceneRenderCatalogStore, name: str, base_path: Path | None, _context: JobContext[None]
) -> SceneRenderCatalogFile:
    """Finish an accepted catalog copy, including its write; only its consumer callbacks may detach."""
    return store.create_catalog(name, base_catalog_path=base_path)


def _nothing(_result: None = None) -> None:
    """Accept completion or cancellation of a request whose work is already done."""


@dataclass(eq=False)
class _Waiter(Generic[T]):
    """One consumer's interest in the outcome of a job; cancelling it ends the delivery."""

    on_result: Callable[[T], None] | None
    on_failed: Callable[[Exception], None] | None = None

    def cancel(self) -> None:
        self.on_result = self.on_failed = None

    def succeed(self, result: T) -> None:
        if self.on_result is not None:
            self.on_result(result)

    def fail(self, error: Exception) -> None:
        if self.on_failed is not None:
            self.on_failed(error)


@dataclass(eq=False)
class _Load:
    """The reading of one catalog file, shared by every consumer that wants it; a restart replaces only its job."""

    waiters: list[_Waiter[SceneRenderCatalogRevision]] = field(default_factory=list)
    cancel_job: Callable[[], None] = _nothing


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
    """Whether a catalog can be applied to all or made default may have changed without a listing change."""

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
        self._catalog_loader = catalog_loader or SceneRenderCatalogLoader()
        self._jobs = (jobs or background_jobs(_POOL, max_workers=_POOL_WORKERS)).scope(self)
        # Copies allocate a unique filename and write it as one ordered operation across all managers.
        self._mutations = background_jobs("render-catalog-mutations", max_workers=1).scope(self)
        built_in = self._catalog_store.built_in_catalog_path
        self._listing = SceneRenderCatalogListing(
            catalogs=(), errors=(), default_path=built_in, chosen_default_path=built_in
        )
        self._listing_ready = False
        self._listing_discovered = False
        self._compiled_catalogs: dict[Path, tuple[Fingerprint, SceneRenderCatalogRevision]] = {}
        self._loads: dict[Path, _Load] = {}
        self._clock = 0
        self._newest_apply = 0
        self._apply_request = LatestRequest(self)
        self._default_request = LatestRequest(self)
        self._scan_request = LatestRequest(self)
        self._scan_waiters: list[_Waiter[SceneRenderCatalogListing]] = []
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
        return self._scan_request.pending or bool(self._loads)

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
        on_done: Callable[[], None] = _nothing,
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
        on_done: Callable[[], None] = _nothing,
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
        return self._listing.catalog_for(catalog_path) is not None and catalog_path in self._compiled_catalogs

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
        cached = self._compiled_catalogs.get(catalog_path)
        if cached is not None and not force_reload:
            on_loaded(cached[1])
            return _nothing
        waiter = _Waiter(on_loaded, on_failed)
        load = self._loads.get(catalog_path)
        if load is None or force_reload:
            load = self._start_load(catalog_path, load)
        load.waiters.append(waiter)
        return partial(self._cancel_load, catalog_path, load, waiter)

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
        waiter = _Waiter(on_done, on_failed)
        self._scan_waiters.append(waiter)
        known = tuple(self._compiled_catalogs)
        self._scan_request.submit(
            self._jobs,
            "render catalog listing",
            partial(_scan, self._catalog_store, known),
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
        waiter = _Waiter(on_created, on_failed)
        self._mutations.submit(
            "create render catalog",
            partial(_create_catalog, self._catalog_store, name, base_catalog_path),
            on_result=partial(self._created, waiter),
            on_error=partial(self._create_failed, waiter),
        )
        return waiter.cancel

    def _created(self, waiter: _Waiter[SceneRenderCatalogFile], catalog: SceneRenderCatalogFile) -> None:
        self.refresh_catalogs(on_done=lambda _listing: self._set_status(f"Created catalog: {catalog.name}"))
        waiter.succeed(catalog)

    def _create_failed(self, waiter: _Waiter[SceneRenderCatalogFile], error: Exception) -> None:
        self._set_status(f"Could not create render catalog: {error}")
        waiter.fail(error)

    def delete_catalog(self, catalog_path: Path) -> None:
        """Delete a user catalog and refresh shared listing state; requests still reading it fail."""
        self._catalog_store.delete_catalog(catalog_path)
        if self._listing.chosen_default_path == catalog_path:
            built_in = self.built_in_catalog_path()
            self._listing = replace(self._listing, default_path=built_in, chosen_default_path=built_in)
        self._compiled_catalogs.pop(catalog_path, None)
        load = self._loads.pop(catalog_path, None)
        if load is not None:
            load.cancel_job()
            error = FileNotFoundError(f"{catalog_path.name} was deleted")
            for waiter in list(load.waiters):
                waiter.fail(error)
        self.refresh_catalogs(on_done=lambda _listing: self._set_status(f"Deleted render catalog: {catalog_path.name}"))

    def _start_load(self, catalog_path: Path, load: _Load | None) -> _Load:
        """Read *catalog_path* on a worker for the consumers of *load*; a read of it still running is stopped."""
        if load is None:
            load = self._loads[catalog_path] = _Load()
        else:
            load.cancel_job()
        load.cancel_job = self._jobs.submit(
            f"render catalog {catalog_path.name}",
            partial(_load_revision, self._catalog_loader, catalog_path),
            on_result=partial(self._loaded, catalog_path, load),
            on_error=partial(self._load_failed, catalog_path, load),
        )
        return load

    def _cancel_load(self, catalog_path: Path, load: _Load, waiter: _Waiter[SceneRenderCatalogRevision]) -> None:
        waiter.cancel()
        if waiter in load.waiters:
            load.waiters.remove(waiter)
        if not load.waiters and self._loads.get(catalog_path) is load:
            load.cancel_job()
            del self._loads[catalog_path]

    def _loaded(self, catalog_path: Path, load: _Load, loaded: _Loaded) -> None:
        del self._loads[catalog_path]
        self._compiled_catalogs[catalog_path] = (loaded.fingerprint, loaded.revision)
        for waiter in list(load.waiters):
            waiter.succeed(loaded.revision)

    def _load_failed(self, catalog_path: Path, load: _Load, error: Exception) -> None:
        del self._loads[catalog_path]
        if self._compiled_catalogs.pop(catalog_path, None) is not None:
            self.catalogUsabilityChanged.emit()
        for waiter in list(load.waiters):
            waiter.fail(error)

    def _scanned(self, scan: _Scan) -> None:
        """Take a worker's findings: follow what changed on disk, then tell listeners and the watcher."""
        waiters, self._scan_waiters = self._scan_waiters, []
        self._listing = scan.listing
        fingerprints = scan.snapshot.fingerprints
        stale = [path for path, (read, _revision) in self._compiled_catalogs.items() if fingerprints.get(path) != read]
        for path in stale:
            del self._compiled_catalogs[path]
        self._catalog_files = scan.snapshot.paths.files
        self._watcher.watch(_WATCH_KEY, scan.snapshot)
        self._listing_ready = self._listing_discovered = True
        self.catalogListingChanged.emit(self._listing)
        self._publish_listing_status()
        for waiter in waiters:
            waiter.succeed(self._listing)
        for path in stale:
            if path not in self._loads and path in fingerprints:
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
            if (load := self._loads.get(path)) is not None:
                self._start_load(path, load)
        self.refresh_catalogs(on_done=lambda _listing: self._report_changed(changed))

    def _report_changed(self, changed: list[Path]) -> None:
        for path in changed:
            logger.info(f"Render catalog file changed: {path}")
            self.catalogFileChanged.emit(path)


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
            on_result=_nothing,
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


def create_scene_render_catalog_manager(
    *,
    catalog_store: SceneRenderCatalogStore | None = None,
    parent: QObject | None = None,
) -> SceneRenderCatalogManager:
    """Create a Scene Render Catalog manager and start discovering catalogs; selections wait for the first listing."""
    manager = SceneRenderCatalogManager(catalog_store=catalog_store, parent=parent)
    manager.initialize()
    return manager
