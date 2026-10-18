"""Worker jobs and request bookkeeping of the catalog manager: scanning, loading and waiting consumers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Generic, TypeVar

from ax_devil.core.source_paths import Fingerprint, SourcePaths, SourceSnapshot
from ax_devil.modules.background_jobs import JobContext, JobScope
from ax_devil.modules.scene.rendering.catalog_loader import SceneRenderCatalogLoader, SceneRenderCatalogRevision
from ax_devil.modules.scene.rendering.catalog_store import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogStore,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Scan:
    """What a worker found on disk: the listing, and the catalog files and their folder as they were."""

    listing: SceneRenderCatalogListing
    snapshot: SourceSnapshot


@dataclass(frozen=True, slots=True)
class Loaded:
    """A compiled revision with the fingerprint the file had just before it was read."""

    fingerprint: Fingerprint
    revision: SceneRenderCatalogRevision


def scan_catalogs(store: SceneRenderCatalogStore, known: tuple[Path, ...], context: JobContext[None]) -> Scan:
    """List the catalogs and fingerprint them, the chosen default and every path in *known*; runs on a worker.

    The folder and the files it holds are fingerprinted before the listing reads them, so a catalog created or saved
    meanwhile differs from the baseline and is found by the first check.
    """
    token = context.token
    folders = frozenset({store.catalogs_dir})
    before = SourcePaths(
        files=frozenset((*store.built_in_catalog_paths, *sorted(store.catalogs_dir.glob("*.json")), *known)),
        folders=folders,
    ).snapshot(token)
    listing = store.list_catalogs_with_errors(token)
    listed = (catalog.path for catalog in listing.catalogs)
    invalid = (error.path for error in listing.errors)
    files = dict.fromkeys((store.built_in_catalog_path, *listed, *invalid, *known, listing.chosen_default_path))
    return Scan(listing, before | SourcePaths(files=frozenset(files), folders=folders).snapshot(token))


def load_catalog_revision(loader: SceneRenderCatalogLoader, path: Path, _context: JobContext[None]) -> Loaded:
    """Read and compile *path*; runs on a worker. The fingerprint comes first, so a later save reads as newer."""
    fingerprint = Fingerprint.of_stat(path.stat())
    return Loaded(fingerprint, loader.load_revision(path))


def create_catalog_file(
    store: SceneRenderCatalogStore, name: str, base_path: Path | None, _context: JobContext[None]
) -> SceneRenderCatalogFile:
    """Finish an accepted catalog copy, including its write; only its consumer callbacks may detach."""
    return store.create_catalog(name, base_catalog_path=base_path)


def nothing(_result: None = None) -> None:
    """Accept completion or cancellation of a request whose work is already done."""


@dataclass(eq=False)
class Waiter(Generic[T]):
    """One consumer's interest in the outcome of a job; cancelling it ends the delivery."""

    on_result: Callable[[T], None] | None
    on_failed: Callable[[Exception], None] | None = None

    def cancel(self) -> None:
        """Stop delivering to this consumer."""
        self.on_result = self.on_failed = None

    def succeed(self, result: T) -> None:
        """Deliver a result unless cancelled."""
        if self.on_result is not None:
            self.on_result(result)

    def fail(self, error: Exception) -> None:
        """Deliver a failure unless cancelled."""
        if self.on_failed is not None:
            self.on_failed(error)


@dataclass(eq=False)
class Load:
    """The reading of one catalog file, shared by every consumer that wants it; a restart replaces only its job."""

    waiters: list[Waiter[SceneRenderCatalogRevision]] = field(default_factory=list)
    cancel_job: Callable[[], None] = nothing


class CatalogRevisions:
    """The compiled revision of each catalog file read so far, and the reads in progress.

    A revision stays current until a listing finds its file changed. Consumers asking for the same file share one read.
    """

    def __init__(self, jobs: JobScope, loader: SceneRenderCatalogLoader, on_dropped: Callable[[], None]) -> None:
        self._jobs = jobs
        self._loader = loader
        self._on_dropped = on_dropped  # a failed read dropped a revision that had been usable
        self._compiled: dict[Path, tuple[Fingerprint, SceneRenderCatalogRevision]] = {}
        self._loads: dict[Path, Load] = {}

    @property
    def loading(self) -> bool:
        """Return whether any catalog file is being read."""
        return bool(self._loads)

    def known(self) -> tuple[Path, ...]:
        """Return the files with a compiled revision."""
        return tuple(self._compiled)

    def is_compiled(self, path: Path) -> bool:
        """Return whether *path* has a compiled revision."""
        return path in self._compiled

    def is_reading(self, path: Path) -> bool:
        """Return whether *path* is being read."""
        return path in self._loads

    def request(
        self,
        path: Path,
        *,
        on_loaded: Callable[[SceneRenderCatalogRevision], None],
        on_failed: Callable[[Exception], None],
        force_reload: bool,
    ) -> Callable[[], None]:
        """Deliver the compiled revision of *path*: at once when current, else after a worker read it.

        *force_reload* reads the file again, and every consumer then waiting receives that newer revision. Return a
        function that cancels this request; a read nobody waits for any more stops.
        """
        cached = self._compiled.get(path)
        if cached is not None and not force_reload:
            on_loaded(cached[1])
            return nothing
        waiter = Waiter(on_loaded, on_failed)
        load = self._loads.get(path)
        if load is None or force_reload:
            load = self._start(path, load)
        load.waiters.append(waiter)
        return partial(self._cancel, path, load, waiter)

    def restart(self, path: Path) -> None:
        """Read *path* again if it is being read, so the read does not deliver a revision of an older file."""
        if (load := self._loads.get(path)) is not None:
            self._start(path, load)

    def forget(self, path: Path) -> None:
        """Drop the revision of a deleted file and fail the consumers still waiting for it."""
        self._compiled.pop(path, None)
        load = self._loads.pop(path, None)
        if load is not None:
            load.cancel_job()
            error = FileNotFoundError(f"{path.name} was deleted")
            for waiter in list(load.waiters):
                waiter.fail(error)

    def drop_changed(self, fingerprints: Mapping[Path, Fingerprint]) -> list[Path]:
        """Drop the revisions whose file no longer has the fingerprint it was read with; return their paths."""
        stale = [path for path, (read, _revision) in self._compiled.items() if fingerprints.get(path) != read]
        for path in stale:
            del self._compiled[path]
        return stale

    def _start(self, path: Path, load: Load | None) -> Load:
        """Read *path* on a worker for the consumers of *load*; a read of it still running is stopped."""
        if load is None:
            load = self._loads[path] = Load()
        else:
            load.cancel_job()
        load.cancel_job = self._jobs.submit(
            f"render catalog {path.name}",
            partial(load_catalog_revision, self._loader, path),
            on_result=partial(self._loaded, path, load),
            on_error=partial(self._failed, path, load),
        )
        return load

    def _cancel(self, path: Path, load: Load, waiter: Waiter[SceneRenderCatalogRevision]) -> None:
        waiter.cancel()
        if waiter in load.waiters:
            load.waiters.remove(waiter)
        if not load.waiters and self._loads.get(path) is load:
            load.cancel_job()
            del self._loads[path]

    def _loaded(self, path: Path, load: Load, loaded: Loaded) -> None:
        del self._loads[path]
        self._compiled[path] = (loaded.fingerprint, loaded.revision)
        for waiter in list(load.waiters):
            waiter.succeed(loaded.revision)

    def _failed(self, path: Path, load: Load, error: Exception) -> None:
        del self._loads[path]
        if self._compiled.pop(path, None) is not None:
            self._on_dropped()
        for waiter in list(load.waiters):
            waiter.fail(error)
