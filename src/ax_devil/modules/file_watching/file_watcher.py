"""Tell an owner which of its files changed on disk, once the writing has stopped."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.source_paths import MAX_FOLDERS, Fingerprint, SourcePaths, SourceSnapshot
from ax_devil.modules.background_jobs import BackgroundJobs, background_jobs
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

QUIET_MS = 1000
"""How long the files must stay quiet before they are checked, so one save that writes many times is one change."""

RETRY_MS = 2000
"""How often paths the system refused to watch, for example at an inotify limit, are tried again and checked."""

_POOL = "file-watching"
_POOL_WORKERS = 2
"""Walking folders may be slow on big trees, so it never shares workers with other background work."""


@dataclass(frozen=True, slots=True)
class _Disk:
    """What a worker found: the fingerprints, the paths to watch, and which changes are still being written."""

    fingerprints: Mapping[Path, Fingerprint]
    watches: frozenset[str]
    unsettled: frozenset[Path]
    truncated: bool


@dataclass(slots=True)
class _Registration:
    """One key's paths, the fingerprints last reported on, and the watches its last check asked for."""

    paths: SourcePaths
    baseline: dict[Path, Fingerprint]
    watches: frozenset[str] = frozenset()  # Empty until the first check arrives.
    cancel: Callable[[], None] | None = field(default=None, repr=False)  # The check under way, if any.


class FileWatcher(QObject):
    """Report the registered files that changed, appeared or disappeared, however they were written.

    Each key registers a ``SourceSnapshot``: a file is watched with the folder holding it, a folder with every
    subfolder. Paths that do not exist yet are watched at their nearest existing parent and reported when they appear.
    Only local disks are noticed. Walking and fingerprinting happen on a background pool, never on the GUI thread.

    A folder is watched for which files it holds: its own and its subfolders' fingerprints change when an entry is
    added, removed or renamed, and a file rewritten in place inside it is not noticed. A file whose contents matter
    must be named in ``SourcePaths.files``.

    The snapshot is the baseline, so a change made after it was taken is reported by the first check, whenever it
    happened. Every file system event restarts a quiet timer. When it runs out, the registered paths are read again
    and the ones whose fingerprint differs from the last report are emitted once through ``changed``. A file still
    changing between two reads is held back until it stops. Writers that replace a file leave the old watch behind,
    so watches are set again after every check.
    """

    changed = Signal(str, object)  # key, frozenset[Path] of its registered paths that changed

    def __init__(
        self, parent: QObject | None = None, *, quiet_ms: int = QUIET_MS, jobs: BackgroundJobs | None = None
    ) -> None:
        super().__init__(parent)
        self._jobs = (jobs or background_jobs(_POOL, max_workers=_POOL_WORKERS)).scope(self)
        self._settle_s = quiet_ms / 5000  # How long a changed file must stay as it is to count as written.
        self._registrations: dict[str, _Registration] = {}
        self._dirty: set[str] = set()  # Keys with events not checked yet.
        self._capped = False
        self._rejected: set[str] = set()  # Watches the system refused; no event will report their changes.
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._touched)
        self._watcher.directoryChanged.connect(self._touched)
        self._quiet = QTimer(self)
        self._quiet.setSingleShot(True)
        self._quiet.setInterval(quiet_ms)
        self._quiet.timeout.connect(self._check_dirty)
        self._retry = QTimer(self)
        self._retry.setInterval(RETRY_MS)
        self._retry.timeout.connect(self._retry_rejected)

    def watch(self, key: str, sources: SourceSnapshot) -> None:
        """Report changes to the paths of *sources* under *key*, replacing what *key* watched before.

        Differences from the fingerprints of *sources* are reported, so take the snapshot before reading the files.
        Watching other paths under a key keeps what it already reported on: a path it covered keeps the fingerprint
        it was last reported on, and only a path new to it starts from *sources*. Empty paths watch nothing.
        """
        registered = self._registrations.get(key)
        if not sources.paths:
            self.unwatch(key)
        elif registered is None:
            registration = self._registrations[key] = _Registration(sources.paths, dict(sources.fingerprints))
            self._start_check(key, registration)
        elif registered.paths != sources.paths:
            registered.baseline = _carried_baseline(registered, sources)
            registered.paths = sources.paths
            self._start_check(key, registered)

    def unwatch(self, key: str) -> None:
        """Stop reporting changes under *key*."""
        self._drop(key)
        self._sync_watches()

    def clear(self) -> None:
        """Stop reporting anything."""
        for key in tuple(self._registrations):
            self._drop(key)
        self._quiet.stop()
        self._sync_watches()

    def _drop(self, key: str) -> None:
        """Forget *key* and the check under way for it; the watches are left to ``_sync_watches``."""
        registration = self._registrations.pop(key, None)
        self._dirty.discard(key)
        if registration is not None and registration.cancel is not None:
            registration.cancel()

    def _touched(self, path: str) -> None:
        """Note that *path* was touched and wait for the files to go quiet."""
        touched = [key for key, registration in self._registrations.items() if path in registration.watches]
        if touched:
            self._dirty.update(touched)
            self._quiet.start()

    def _check_dirty(self) -> None:
        """Read the paths of every key that was touched again."""
        dirty, self._dirty = self._dirty, set()
        for key in sorted(dirty):
            registration = self._registrations.get(key)
            if registration is not None:
                self._start_check(key, registration)

    def _start_check(self, key: str, registration: _Registration) -> None:
        """Read *registration*'s paths in the background; a check still under way is replaced by this one."""
        if registration.cancel is not None:
            registration.cancel()
        paths, baseline = registration.paths, dict(registration.baseline)
        settle_s = self._settle_s
        registration.cancel = self._jobs.submit(
            "file watching",
            lambda job: _read_disk(paths, baseline, settle_s, job.token),
            on_result=partial(self._checked, key, registration, paths),
            on_error=partial(self._check_failed, key, registration),
        )

    def _check_failed(self, key: str, registration: _Registration, error: Exception) -> None:
        """Note that a check failed; the next file system event checks again."""
        if self._registrations.get(key) is registration:
            registration.cancel = None
            logger.warning(f"Could not read watched files: {error}")

    def _checked(self, key: str, registration: _Registration, paths: SourcePaths, disk: _Disk) -> None:
        """Report the paths that differ from the baseline and are done changing, and watch what exists.

        *paths* are the ones *disk* was read for; a result for paths that were replaced since is dropped.
        """
        if self._registrations.get(key) is not registration or registration.paths != paths:
            return
        registration.cancel = None
        reported = frozenset(_differences(registration.baseline, disk.fingerprints) - disk.unsettled)
        for path in reported:
            if path in disk.fingerprints:
                registration.baseline[path] = disk.fingerprints[path]
            else:
                del registration.baseline[path]
        first = not registration.watches
        registration.watches = disk.watches
        if disk.truncated:
            self._warn_capped()
        added = self._sync_watches()
        # Look again after the watches were set, which may have been late for a change, or while a file is changing.
        if first or disk.unsettled or not added.isdisjoint(disk.watches):
            self._dirty.add(key)
            self._quiet.start()
        if reported:
            self.changed.emit(key, reported)

    def _sync_watches(self) -> set[str]:
        """Watch exactly what the registrations ask for, again the paths a replaced file left; return what was added."""
        desired: set[str] = set()
        named: set[str] = set()
        for registration in self._registrations.values():
            desired |= registration.watches
            named.update(os.path.normpath(file) for file in registration.paths.files)
        directories = sorted(desired - named)
        if len(directories) > MAX_FOLDERS:  # Only folders are capped: a named file always keeps its own watch.
            self._warn_capped()
            desired = (desired & named) | set(directories[:MAX_FOLDERS])
        current = set(self._watcher.files()) | set(self._watcher.directories())
        if current - desired:
            self._watcher.removePaths(sorted(current - desired))
        missing = desired - current
        self._rejected = set(self._watcher.addPaths(sorted(missing))) if missing else set()
        if self._rejected and not self._retry.isActive():
            self._retry.start()
        elif not self._rejected:
            self._retry.stop()
        return missing - self._rejected

    def _retry_rejected(self) -> None:
        """Try the refused watches again and check the keys that depend on them, as no event will."""
        rejected = self._rejected
        self._sync_watches()
        self._dirty.update(
            key for key, registration in self._registrations.items() if not rejected.isdisjoint(registration.watches)
        )
        self._quiet.start()

    def _warn_capped(self) -> None:
        """Say once that folders beyond the limit are not watched."""
        if not self._capped:
            self._capped = True
            logger.warning(f"Watching only {MAX_FOLDERS} folders; changes in the others are not noticed")


def _differences(before: Mapping[Path, Fingerprint], after: Mapping[Path, Fingerprint]) -> set[Path]:
    """Return the paths that were added, removed or changed from *before* to *after*."""
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def _covers(paths: SourcePaths, path: Path) -> bool:
    """Return whether *path* is one of the files of *paths*, or a folder of it or inside one."""
    return path in paths.files or any(path.is_relative_to(folder) for folder in paths.folders)


def _carried_baseline(registered: _Registration, sources: SourceSnapshot) -> dict[Path, Fingerprint]:
    """Return the baseline for *sources*' paths: what *registered* last reported on, else what *sources* found.

    A file or folder *registered* named keeps its baseline, absence included, so one that appeared meanwhile is a
    change. Any other path, even inside a folder it covered, starts from *sources*: being added to the key is not one.
    """
    baseline = {path: fp for path, fp in registered.baseline.items() if _covers(sources.paths, path)}
    named = registered.paths.files | registered.paths.folders
    for path, fingerprint in sources.fingerprints.items():
        if path not in named:
            baseline.setdefault(path, fingerprint)
    return baseline


def _nearest_existing(path: Path) -> str:
    """Return *path*, or its nearest parent that exists, as a clean string."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return os.path.normpath(path)


def _watches(paths: SourcePaths, fingerprints: Mapping[Path, Fingerprint]) -> frozenset[str]:
    """Return what to watch for *paths*: what was fingerprinted, the folder holding each file, and for a path that
    does not exist its nearest existing parent."""
    watches = {os.path.normpath(path) for path in fingerprints}
    watches.update(_nearest_existing(file.parent) for file in paths.files)
    watches.update(_nearest_existing(folder.parent) for folder in paths.folders if folder not in fingerprints)
    return frozenset(watches)


def _read_disk(paths: SourcePaths, baseline: Mapping[Path, Fingerprint], settle_s: float, token: CancelToken) -> _Disk:
    """Take a snapshot of *paths* and say what to watch.

    The paths that differ from *baseline* are read again after *settle_s*; those that changed meanwhile are still
    being written.
    """
    snapshot = paths.snapshot(token)
    unsettled: set[Path] = set()
    if differing := _differences(baseline, snapshot.fingerprints):
        token.wait(settle_s)
        unsettled = {path for path in differing if Fingerprint.of(path) != snapshot.fingerprints.get(path)}
    return _Disk(
        snapshot.fingerprints, _watches(paths, snapshot.fingerprints), frozenset(unsettled), snapshot.truncated
    )
